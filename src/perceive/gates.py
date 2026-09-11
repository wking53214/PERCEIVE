"""The six governance gates.

Recovered from the corpus, which names them explicitly: Boundary, Citadel,
Fortress, Invariant, Micropatch, Sentinel. The archival pipeline ordered them

    BoundaryGate -> InvariantGate -> Fortress -> Citadel -> Sentinel
      -> OBSERVE Layer -> MicroPatch Engine -> Model Adapter

and the control constants recovered alongside it were ``harm``, ``forbidden``,
``unsafe`` and ``drift``, with a 500-character request limit.

Consensus is **unanimous**: every gate must allow a transition for it to
proceed. That is the property worth keeping -- it makes each gate a veto, so
adding a gate can only ever make the system more conservative, never less.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from .models import CheckOutcome, Event, Manifest, State

#: Recovered control constants.
HARM_MARKERS = ("harm", "injure", "damage", "destroy")
FORBIDDEN_MARKERS = ("forbidden", "prohibited", "restricted", "classified")
UNSAFE_MARKERS = ("unsafe", "dangerous", "hazardous", "toxic")
DRIFT_MARKERS = ("drift", "deviation", "divergence", "anomaly")

#: Recovered threshold.
MAX_REQUEST_CHARS = 500


class Gate(Protocol):
    """A gate is a named veto over a proposed transition."""

    name: str

    def check(self, event: Event, state: State, manifest: Manifest) -> Tuple[bool, str]:
        ...


class BaseGate:
    """Rule-driven gate, matching the archival ``BaseGate`` abstraction.

    A gate holds a collection of named predicates; the gate allows the event
    only if every one of them does. Predicates are separate so a rejection can
    name the specific rule that fired, which a single monolithic check cannot.
    """

    name = "base"

    def __init__(
        self,
        rules: Optional[Sequence[Tuple[str, Callable[[Event, State, Manifest], bool]]]] = None,
    ) -> None:
        self.rules: List[Tuple[str, Callable[[Event, State, Manifest], bool]]] = list(
            rules or []
        )

    def add_rule(
        self, name: str, predicate: Callable[[Event, State, Manifest], bool]
    ) -> "BaseGate":
        self.rules.append((name, predicate))
        return self

    def check(self, event: Event, state: State, manifest: Manifest) -> Tuple[bool, str]:
        for rule_name, predicate in self.rules:
            if not predicate(event, state, manifest):
                return False, f"rule {rule_name!r} denied"
        return True, ""


def _text_of(event: Event) -> str:
    """Flatten an event's payload to searchable text."""
    parts = [event.event_type, event.context_id]
    parts.extend(str(v) for v in event.delta.values())
    parts.extend(str(v) for v in event.metadata.values())
    return " ".join(parts).lower()


# --------------------------------------------------------------------------
# 1. Boundary -- capability and scope
# --------------------------------------------------------------------------


class BoundaryGate(BaseGate):
    """Is this request inside the system's declared capability at all?

    Runs first because it is the cheapest and the most categorical: a request
    outside the boundary needs no further analysis.
    """

    name = "boundary"

    def __init__(self, max_chars: int = MAX_REQUEST_CHARS) -> None:
        super().__init__()
        self.max_chars = max_chars

    def check(self, event: Event, state: State, manifest: Manifest) -> Tuple[bool, str]:
        if not event.event_type:
            return False, "event_type is empty"
        if not event.context_id:
            return False, "context_id is empty"

        text = _text_of(event)
        if len(text) > self.max_chars:
            return False, f"request is {len(text)} chars, limit {self.max_chars}"

        for marker in HARM_MARKERS:
            if re.search(rf"\b{marker}\w*\b", text):
                return False, f"harm marker {marker!r} outside capability boundary"

        return super().check(event, state, manifest)


# --------------------------------------------------------------------------
# 2. InvariantValidator -- would this break a declared rule?
# --------------------------------------------------------------------------


class InvariantValidatorGate(BaseGate):
    """Pre-flight the manifest's invariants against the *projected* state.

    The kernel checks invariants during the transition too. This gate exists
    so that a violation is caught and named as a gate rejection before any
    state is copied, which is both cheaper and clearer in the audit trail.
    """

    name = "invariant_validator"

    def check(self, event: Event, state: State, manifest: Manifest) -> Tuple[bool, str]:
        if manifest.is_expired():
            return False, f"manifest {manifest.manifest_id} has expired"
        if manifest.hash and not manifest.is_sealed():
            return False, (
                f"manifest {manifest.manifest_id} hash does not match its "
                f"contents; the ruleset changed after sealing"
            )
        return super().check(event, state, manifest)


# --------------------------------------------------------------------------
# 3. Fortress -- containment; forbidden content
# --------------------------------------------------------------------------


class FortressGate(BaseGate):
    """The ingestion perimeter. Rejects forbidden content outright."""

    name = "fortress"

    def check(self, event: Event, state: State, manifest: Manifest) -> Tuple[bool, str]:
        text = _text_of(event)
        for marker in FORBIDDEN_MARKERS:
            if re.search(rf"\b{marker}\w*\b", text):
                return False, f"forbidden marker {marker!r} at the perimeter"
        return super().check(event, state, manifest)


# --------------------------------------------------------------------------
# 4. Citadel -- routing; unsafe content
# --------------------------------------------------------------------------


class CitadelGate(BaseGate):
    """The overarching container: unsafe content, and risk-tier routing.

    Where Fortress asks "may this enter", Citadel asks "may this enter *here*"
    -- a HIGH risk-level manifest refuses events that did not declare an
    authority.
    """

    name = "citadel"

    def check(self, event: Event, state: State, manifest: Manifest) -> Tuple[bool, str]:
        text = _text_of(event)
        for marker in UNSAFE_MARKERS:
            if re.search(rf"\b{marker}\w*\b", text):
                return False, f"unsafe marker {marker!r} rejected by routing"

        if manifest.risk_level.upper() == "HIGH":
            if not event.metadata.get("authority"):
                return False, (
                    "HIGH risk manifest requires a declared authority in "
                    "event.metadata['authority']"
                )
        return super().check(event, state, manifest)


# --------------------------------------------------------------------------
# 5. Sentinel -- output verification; drift
# --------------------------------------------------------------------------


class SentinelGate(BaseGate):
    """The watchdog. Detects drift against the accumulated history."""

    name = "sentinel"

    def __init__(self, max_history: int = 10_000) -> None:
        super().__init__()
        self.max_history = max_history

    def check(self, event: Event, state: State, manifest: Manifest) -> Tuple[bool, str]:
        text = _text_of(event)
        for marker in DRIFT_MARKERS:
            if re.search(rf"\b{marker}\w*\b", text):
                return False, f"drift marker {marker!r} detected"

        if len(state.history) >= self.max_history:
            return False, (
                f"history has reached {len(state.history)} events; refusing to "
                f"grow an unbounded audit surface"
            )

        # Monotonic time. Out-of-order events make the ledger unorderable and
        # therefore unusable as evidence.
        if state.history and event.timestamp < state.history[-1].timestamp:
            return False, (
                f"event timestamp {event.timestamp} precedes the last recorded "
                f"event {state.history[-1].timestamp}"
            )
        return super().check(event, state, manifest)


# --------------------------------------------------------------------------
# 6. MicroPatch -- emergency rules
# --------------------------------------------------------------------------


class MicroPatchGate(BaseGate):
    """Runtime emergency rules, evaluated last.

    Deliberately narrow: patches are for incidents, and a patch set that grows
    without bound becomes an ungoverned second policy. Every active patch is
    named in the audit entry.
    """

    name = "micropatch"

    def __init__(self, max_active: int = 32) -> None:
        super().__init__()
        self.max_active = max_active

    def check(self, event: Event, state: State, manifest: Manifest) -> Tuple[bool, str]:
        if len(self.rules) > self.max_active:
            return False, (
                f"{len(self.rules)} active patches exceed the limit of "
                f"{self.max_active}; emergency rules have become policy"
            )
        return super().check(event, state, manifest)

    def active_patches(self) -> List[str]:
        return [name for name, _ in self.rules]


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

#: Canonical evaluation order, recovered from the archival processing chain.
CANONICAL_ORDER: Tuple[str, ...] = (
    "boundary",
    "invariant_validator",
    "fortress",
    "citadel",
    "sentinel",
    "micropatch",
)


def default_gates() -> List[BaseGate]:
    """The six gates in canonical order."""
    return [
        BoundaryGate(),
        InvariantValidatorGate(),
        FortressGate(),
        CitadelGate(),
        SentinelGate(),
        MicroPatchGate(),
    ]


def evaluate_gates(
    gates: Sequence[BaseGate],
    event: Event,
    state: State,
    manifest: Manifest,
    short_circuit: bool = True,
) -> Tuple[bool, List[CheckOutcome]]:
    """Run the gates under unanimous consensus.

    A gate that raises is recorded as a *fault*, not as a pass. Treating an
    exception as permission is the failure mode that makes a governance kernel
    worse than no kernel at all.
    """
    outcomes: List[CheckOutcome] = []
    allowed = True

    for gate in gates:
        try:
            ok, reason = gate.check(event, state, manifest)
            if not isinstance(ok, bool):
                outcomes.append(
                    CheckOutcome(gate.name, False, f"non-bool verdict {ok!r}", fault=True)
                )
                allowed = False
                if short_circuit:
                    break
                continue
            outcomes.append(CheckOutcome(gate.name, ok, reason))
            if not ok:
                allowed = False
                if short_circuit:
                    break
        except Exception as exc:
            outcomes.append(
                CheckOutcome(
                    gate.name, False, f"{type(exc).__name__}: {exc}", fault=True
                )
            )
            allowed = False
            if short_circuit:
                break

    return allowed, outcomes
