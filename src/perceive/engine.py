"""PERCEIVEEngine -- the orchestration layer.

Recovered from the archival v1.0.0 monolith: ingest, govern, evaluate,
validate. The pipeline order is the archive's, with the micropatch stage
moved inside the governance envelope rather than after it.

    event
      |
      v
    micropatch  ->  re-govern patched output  (added; see micropatch.py)
      |
      v
    six gates (unanimous)  ->  invariants (pre)  ->  apply  ->  invariants (post)
      |
      v
    audit ledger (hash-linked)
      |
      v
    risk evaluation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .clinical import (
    MonotonicTimeGate,
    RiskComponents,
    VitalsPlausibilityGate,
    evaluate_risk,
    pediatric_manifest,
)
from .gates import BaseGate, default_gates
from .kernel import GovernanceKernel
from .micropatch import MicroPatchEngine
from .models import AuditEntry, DecisionStatus, Event, Manifest, State


@dataclass
class PerceiveResponse:
    """The outcome of evaluating one subject, and the proof behind it."""

    subject_id: str
    accepted: bool
    decision: str
    reason: str
    risk: Optional[RiskComponents] = None
    state_hash: Optional[str] = None
    manifest_version: str = ""
    manifest_hash: str = ""
    audit_hash: Optional[str] = None
    chain_verified: bool = False
    patches_applied: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "accepted": self.accepted,
            "decision": self.decision,
            "reason": self.reason,
            "risk": self.risk.to_dict() if self.risk else None,
            "outcome": self.risk.outcome if self.risk else None,
            "state_hash": self.state_hash,
            "manifest": {"version": self.manifest_version, "hash": self.manifest_hash},
            "audit_hash": self.audit_hash,
            "chain_verified": self.chain_verified,
            "patches_applied": self.patches_applied,
            "timestamp": self.timestamp,
        }


class PERCEIVEEngine:
    """Governance kernel, gates, manifest and patch engine, wired together."""

    def __init__(
        self,
        manifest: Optional[Manifest] = None,
        gates: Optional[Sequence[BaseGate]] = None,
        patches: Optional[MicroPatchEngine] = None,
    ) -> None:
        self.kernel = GovernanceKernel()
        self.manifest = manifest or pediatric_manifest()
        self.gates: List[BaseGate] = list(gates) if gates is not None else (
            default_gates() + [VitalsPlausibilityGate(), MonotonicTimeGate()]
        )
        self.patches = patches or MicroPatchEngine()
        self._states: Dict[str, State] = {}

    # -- governed submission ----------------------------------------------

    def submit(self, event: Event) -> Tuple[State, PerceiveResponse]:
        """Govern one event against its subject's accumulated state."""
        state = self._states.get(event.context_id, State())

        patch_result = self.patches.apply(event, state, self.manifest, self.gates)
        effective = patch_result.event

        new_state, state_hash, entry = self.kernel.run_event(
            state, effective, self.manifest, self.gates, patch_result.applied
        )

        if entry.decision is DecisionStatus.ACCEPTED:
            new_state.context["_history_length"] = len(new_state.history)
            self._states[event.context_id] = new_state

        response = PerceiveResponse(
            subject_id=event.context_id,
            accepted=entry.decision.accepted,
            decision=entry.decision.value,
            reason=entry.reason,
            state_hash=state_hash,
            manifest_version=self.manifest.version,
            manifest_hash=self.manifest.hash,
            audit_hash=entry.audit_hash,
            patches_applied=patch_result.applied,
        )
        return new_state, response

    # -- clinical convenience ---------------------------------------------

    def ingest_patient(self, patient: Dict[str, Any]) -> Tuple[State, List[PerceiveResponse]]:
        """Admission followed by a first observation, as in the archive."""
        patient_id = patient["patient_id"]
        admission = Event(
            event_type="admission",
            delta={
                "patient_id": patient_id,
                "age_months": patient["age_months"],
                "admission_time": patient["admission_timestamp"],
                "vitals": patient["vitals"],
            },
            context_id=patient_id,
            timestamp=patient["admission_timestamp"],
            metadata={"authority": patient.get("authority", "attending")},
        )
        state, first = self.submit(admission)

        observed_at = (
            datetime.fromisoformat(patient["admission_timestamp"]) + timedelta(minutes=5)
        ).isoformat()
        observation = Event(
            event_type="observation",
            delta={"vitals": patient["vitals"]},
            context_id=patient_id,
            timestamp=observed_at,
            metadata={"authority": patient.get("authority", "attending")},
        )
        state, second = self.submit(observation)
        return state, [first, second]

    def evaluate_patient(self, patient: Dict[str, Any]) -> PerceiveResponse:
        """Ingest a patient and, if governance allowed it, score the risk.

        Risk is computed only for accepted states. The archival engine scored
        the risk regardless of the governance outcome, so a rejected event
        still produced a clinical number -- the governance envelope reported a
        refusal while the system emitted the answer anyway.
        """
        state, responses = self.ingest_patient(patient)
        final = responses[-1]

        if final.accepted:
            final.risk = evaluate_risk(state.context.get("vitals", {}))

        final.chain_verified = self.verify()
        return final

    # -- reporting ---------------------------------------------------------

    def verify(self) -> bool:
        """Whether the audit ledger still verifies end to end."""
        from .kernel import ChainIntegrityError

        try:
            return self.kernel.verify_chain()
        except ChainIntegrityError:
            return False

    @property
    def metrics(self):
        return self.kernel.metrics

    def state_of(self, subject_id: str) -> Optional[State]:
        return self._states.get(subject_id)


def run_synthetic(num_patients: int = 10, seed: int = 42):
    """Demo: govern and score a synthetic cohort."""
    from .clinical import SyntheticPatientDataset

    dataset = SyntheticPatientDataset(num_patients=num_patients, seed=seed)
    engine = PERCEIVEEngine()
    results = [engine.evaluate_patient(p) for p in dataset.patients]
    return results, engine
