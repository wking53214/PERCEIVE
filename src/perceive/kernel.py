"""The governance kernel: gates, invariants, transitions, and the ledger.

Recovered substantially from the archival v1.0.0 monolith, whose design was
sound. Invariants are checked both before and after a transition, the
transition works on a deep copy so a rejected event cannot leave partial
state behind, and every decision -- accepted or refused -- is appended to a
hash-linked ledger.

Three things the archive lacked are added here, each documented in
PROVENANCE.md:

* ``verify_chain``, which makes the ledger's tamper-evidence checkable. A hash
  chain nobody verifies provides no integrity guarantee at all.
* Re-governance after micropatching. The archive's own review noted that "the
  MicroPatch engine executes after the governance controls and there is no
  subsequent governance-control pass before model execution", which means
  patched output reached the model ungoverned.
* Rejection of unsealed or expired manifests, so decisions are never recorded
  under lapsed authority.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .gates import BaseGate, evaluate_gates
from .models import (
    AuditEntry,
    CheckOutcome,
    DecisionStatus,
    Event,
    ExecutionMetrics,
    Manifest,
    State,
)


class ChainIntegrityError(Exception):
    """The audit ledger failed verification."""

    def __init__(self, sequence_number: int, detail: str) -> None:
        super().__init__(f"audit entry {sequence_number}: {detail}")
        self.sequence_number = sequence_number
        self.detail = detail


class GovernanceKernel:
    """Enforces a manifest over a state, recording every decision."""

    def __init__(self) -> None:
        self.audit_log: List[AuditEntry] = []
        self.state_store: Dict[str, State] = {}
        self.sequence_counter = 0
        self.metrics = ExecutionMetrics()
        self.audit_chain_tip: Optional[str] = None

    # -- invariants --------------------------------------------------------

    def check_invariants(
        self, state: State, manifest: Manifest, event: Optional[Event]
    ) -> Tuple[bool, List[CheckOutcome]]:
        """Evaluate every invariant. An exception is a violation, not a pass."""
        outcomes: List[CheckOutcome] = []
        for phi, name in zip(manifest.invariants, manifest.invariant_names):
            try:
                result = phi(state, event)
                if not isinstance(result, bool):
                    outcomes.append(
                        CheckOutcome(name, False, f"non-bool verdict {result!r}", fault=True)
                    )
                    return False, outcomes
                outcomes.append(CheckOutcome(name, result))
                if not result:
                    return False, outcomes
            except Exception as exc:
                outcomes.append(
                    CheckOutcome(name, False, f"{type(exc).__name__}: {exc}", fault=True)
                )
                return False, outcomes
        return True, outcomes

    # -- transition --------------------------------------------------------

    def transition(
        self,
        state: State,
        event: Event,
        manifest: Manifest,
        gates: Sequence[BaseGate],
    ) -> Tuple[State, DecisionStatus, str, List[CheckOutcome], List[CheckOutcome]]:
        """Attempt one governed transition.

        Returns the unchanged state on any refusal, so a caller that ignores
        the status still cannot accidentally commit a rejected event.
        """
        allowed, gate_results = evaluate_gates(gates, event, state, manifest)
        if not allowed:
            status = (
                DecisionStatus.FAULT_DETECTED
                if any(g.fault for g in gate_results)
                else DecisionStatus.GATE_REJECTED
            )
            failed = next((g for g in gate_results if not g.passed), None)
            reason = f"{failed.name}: {failed.reason}" if failed else "gate rejection"
            return state, status, reason, gate_results, []

        before_ok, before_invariants = self.check_invariants(state, manifest, event)
        if not before_ok:
            failed = next((i for i in before_invariants if not i.passed), None)
            return (
                state,
                DecisionStatus.INVARIANT_VIOLATED,
                f"pre-transition: {failed.name}: {failed.reason}" if failed else "pre-transition",
                gate_results,
                before_invariants,
            )

        candidate = copy.deepcopy(state)
        candidate.history.append(event)
        candidate.context.update(event.delta)

        after_ok, after_invariants = self.check_invariants(candidate, manifest, None)
        invariant_results = before_invariants + after_invariants
        if not after_ok:
            failed = next((i for i in after_invariants if not i.passed), None)
            return (
                state,
                DecisionStatus.INVARIANT_VIOLATED,
                f"post-transition: {failed.name}: {failed.reason}" if failed else "post-transition",
                gate_results,
                invariant_results,
            )

        return candidate, DecisionStatus.ACCEPTED, "OK", gate_results, invariant_results

    # -- state addressing --------------------------------------------------

    def dial(self, state: State, manifest: Manifest) -> Optional[str]:
        """Content address of a state, or None if the state is not valid.

        Refusing to address an invalid state is the point: an unaddressable
        state cannot be stored, cloned, or resumed from.
        """
        valid, _ = self.check_invariants(state, manifest, None)
        if not valid:
            return None
        return hashlib.sha256(
            json.dumps(state.to_dict(), sort_keys=True, default=str).encode()
        ).hexdigest()

    def clone(self, state_hash: str) -> Optional[State]:
        """Recover a previously accepted state by its content address."""
        stored = self.state_store.get(state_hash)
        return copy.deepcopy(stored) if stored is not None else None

    # -- ledger ------------------------------------------------------------

    def _append_audit(self, entry: AuditEntry) -> AuditEntry:
        entry.previous_hash = self.audit_chain_tip
        entry.audit_hash = entry.compute_hash()
        self.audit_chain_tip = entry.audit_hash
        self.audit_log.append(entry)
        return entry

    def verify_chain(self) -> bool:
        """Verify the ledger end to end.

        Recomputes every entry's hash and checks every link. A hash chain that
        is never verified is decoration, so this is the function that turns the
        ledger into evidence.

        Raises ChainIntegrityError naming the first bad entry.
        """
        previous: Optional[str] = None
        for position, entry in enumerate(self.audit_log):
            if entry.previous_hash != previous:
                raise ChainIntegrityError(
                    entry.sequence_number,
                    f"previous_hash {entry.previous_hash!r} does not match the "
                    f"prior entry's hash {previous!r}",
                )
            recomputed = entry.compute_hash()
            if recomputed != entry.audit_hash:
                raise ChainIntegrityError(
                    entry.sequence_number,
                    "contents do not match the recorded hash; the entry was altered",
                )
            if entry.sequence_number != position + 1:
                raise ChainIntegrityError(
                    entry.sequence_number,
                    f"out of order: expected sequence {position + 1}",
                )
            previous = entry.audit_hash

        if previous != self.audit_chain_tip:
            raise ChainIntegrityError(
                len(self.audit_log), "chain tip does not match the final entry"
            )
        return True

    # -- metrics -----------------------------------------------------------

    def _record(self, decision: DecisionStatus, gate_results, invariant_results) -> None:
        self.metrics.total_events += 1
        if decision is DecisionStatus.ACCEPTED:
            self.metrics.accepted += 1
        elif decision is DecisionStatus.GATE_REJECTED:
            self.metrics.gate_rejections += 1
        elif decision is DecisionStatus.INVARIANT_VIOLATED:
            self.metrics.invariant_violations += 1
        elif decision is DecisionStatus.FAULT_DETECTED:
            self.metrics.faults += 1
        elif decision is DecisionStatus.PATCH_REJECTED:
            self.metrics.patch_rejections += 1

        for outcome in gate_results:
            if not outcome.passed:
                self.metrics.gate_fault_counts[outcome.name] = (
                    self.metrics.gate_fault_counts.get(outcome.name, 0) + 1
                )
        for outcome in invariant_results:
            if not outcome.passed:
                self.metrics.invariant_fault_counts[outcome.name] = (
                    self.metrics.invariant_fault_counts.get(outcome.name, 0) + 1
                )

    # -- public entry point ------------------------------------------------

    def run_event(
        self,
        state: State,
        event: Event,
        manifest: Manifest,
        gates: Sequence[BaseGate],
        patches_applied: Optional[List[str]] = None,
    ) -> Tuple[State, Optional[str], AuditEntry]:
        """Govern one event and append the decision to the ledger.

        Returns ``(state, state_hash, audit_entry)``. On refusal the state is
        the original and the hash is None.
        """
        self.sequence_counter += 1
        before = copy.deepcopy(state)

        new_state, decision, reason, gate_results, invariant_results = self.transition(
            state, event, manifest, gates
        )

        self._record(decision, gate_results, invariant_results)

        state_hash: Optional[str] = None
        if decision is DecisionStatus.ACCEPTED:
            state_hash = self.dial(new_state, manifest)
            if state_hash:
                self.state_store[state_hash] = copy.deepcopy(new_state)

        entry = self._append_audit(
            AuditEntry(
                timestamp=datetime.now().isoformat(),
                before=before,
                event=event,
                manifest_version=manifest.version,
                manifest_hash=manifest.hash,
                # Deep-copied so that a caller mutating the returned state
                # afterwards cannot alter the ledger and break the chain.
                after=copy.deepcopy(new_state)
                if decision is DecisionStatus.ACCEPTED
                else before,
                decision=decision,
                reason=reason,
                gate_results=gate_results,
                invariant_results=invariant_results,
                sequence_number=self.sequence_counter,
                patches_applied=list(patches_applied or []),
            )
        )

        returned = new_state if decision is DecisionStatus.ACCEPTED else state
        return returned, state_hash, entry

    # -- reporting ---------------------------------------------------------

    def ledger(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self.audit_log]

    def decisions_for(self, context_id: str) -> List[AuditEntry]:
        return [
            e for e in self.audit_log if e.event and e.event.context_id == context_id
        ]
