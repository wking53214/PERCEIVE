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
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Protocol, Sequence, Tuple, Union

from .models import CheckOutcome, Event, Manifest, State

#: Recovered control constants.
HARM_MARKERS = ("harm", "injure", "damage", "destroy")
FORBIDDEN_MARKERS = ("forbidden", "prohibited", "restricted", "classified")
UNSAFE_MARKERS = ("unsafe", "dangerous", "hazardous", "toxic")
DRIFT_MARKERS = ("drift", "deviation", "divergence", "anomaly")

#: Recovered threshold.
MAX_REQUEST_CHARS = 500

#: The two ends of a decision at which a gate can be judged outside the kernel.
#: ALPHA judges the proposed event against the state it would change. OMEGA
#: judges the state the work would produce, so that state already contains the
#: event. The kernel itself always judges gates at ALPHA.
ALPHA = "alpha"
OMEGA = "omega"
ALPHA_ONLY: FrozenSet[str] = frozenset({ALPHA})
OMEGA_ONLY: FrozenSet[str] = frozenset({OMEGA})
BOTH: FrozenSet[str] = frozenset({ALPHA, OMEGA})

Positions = Union[str, Iterable[str]]


def _normalise_positions(value: Positions, owner: str) -> FrozenSet[str]:
    """Turn a declaration into a set of known ends, or fail loudly.

    A typo must not quietly become "supports nothing" or "supports everything",
    so an unknown end or an empty declaration is an error, not a default.
    """
    items = {value} if isinstance(value, str) else set(value)
    unknown = items - BOTH
    if unknown:
        raise ValueError(
            f"{owner} declares unknown position(s) {sorted(map(repr, unknown))}; "
            f"the ends are {ALPHA!r} and {OMEGA!r}"
        )
    if not items:
        raise ValueError(f"{owner} declares no position, so it could be judged nowhere")
    return frozenset(items)


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

    #: The ends at which this gate's own logic gives a trustworthy verdict.
    #:
    #: The default is ALPHA only, on purpose. The kernel judges every gate
    #: before the work, against the state before the event, so that is what a
    #: gate is normally written for. At OMEGA the state already contains the
    #: event, so a gate that compares the event with the state before it
    #: (a plausibility step, a clock check, a size limit) compares the event
    #: with itself and passes what ALPHA refuses. A gate must not be used at an
    #: end nobody has checked, so a gate declares an end only after showing it
    #: gives the same verdict on the state before and after the event.
    positions: FrozenSet[str] = ALPHA_ONLY

    def __init__(
        self,
        rules: Optional[Sequence[Tuple[str, Callable[[Event, State, Manifest], bool]]]] = None,
        positions: Optional[Positions] = None,
    ) -> None:
        self.rules: List[Tuple[str, Callable[[Event, State, Manifest], bool]]] = list(
            rules or []
        )
        # Keyed by the identity of each rule entry, so two rules with the same
        # name cannot share a declaration. Rules handed in at construction are
        # not in here, and a rule with no entry here is ALPHA only.
        self._rule_positions: Dict[int, FrozenSet[str]] = {}
        if positions is not None:
            self.positions = _normalise_positions(positions, f"gate {self.name!r}")

    def add_rule(
        self,
        name: str,
        predicate: Callable[[Event, State, Manifest], bool],
        positions: Optional[Positions] = None,
    ) -> "BaseGate":
        """Add a named rule. A rule is ALPHA only unless it declares otherwise."""
        entry = (name, predicate)
        self.rules.append(entry)
        if positions is not None:
            self._rule_positions[id(entry)] = _normalise_positions(
                positions, f"rule {name!r} of gate {self.name!r}"
            )
        return self

    def supported_positions(self) -> FrozenSet[str]:
        """The ends this gate instance may be judged at.

        The gate's own declaration, narrowed by every rule it holds: a gate is
        only as position-safe as its least position-safe rule. An empty result
        means the gate and its rules share no end.
        """
        supported = _normalise_positions(self.positions, f"gate {self.name!r}")
        declared = getattr(self, "_rule_positions", {})
        for entry in getattr(self, "rules", ()):
            supported = supported & declared.get(id(entry), ALPHA_ONLY)
        return supported

    def check_at(
        self, position: str, event: Event, state: State, manifest: Manifest
    ) -> Tuple[bool, str]:
        """Judge at an explicit end of the decision.

        The default is ``check``, which is right for a gate whose verdict does
        not depend on the end. A gate that does depend on it overrides this, so
        it is told its end instead of guessing it from the state: an event that
        replays the last recorded one looks the same at both ends.
        """
        return self.check(event, state, manifest)

    def check(self, event: Event, state: State, manifest: Manifest) -> Tuple[bool, str]:
        for rule_name, predicate in self.rules:
            if not predicate(event, state, manifest):
                return False, f"rule {rule_name!r} denied"
        return True, ""


def positions_of(gate: Any) -> FrozenSet[str]:
    """The ends a gate may be judged at, for any gate-shaped object.

    A gate that says nothing is ALPHA only, because that is the only end the
    kernel has ever judged it at.
    """
    method = getattr(gate, "supported_positions", None)
    if callable(method):
        return frozenset(method())
    declared = getattr(gate, "positions", ALPHA_ONLY)
    return _normalise_positions(declared, f"gate {getattr(gate, 'name', type(gate).__name__)!r}")


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
    #: Reads only the event. Nothing here depends on the state.
    positions = BOTH

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
    #: Reads only the manifest. Nothing here depends on the state.
    positions = BOTH

    def check(self, event: Event, state: State, manifest: Manifest) -> Tuple[bool, str]:
        if manifest.is_expired():
            return False, f"manifest {manifest.manifest_id} has expired"
        if not manifest.hash:
            # An unsealed manifest carries no evidence its rules are the
            # approved ones, so it is refused rather than trusted.
            return False, f"manifest {manifest.manifest_id} is not sealed"
        if not manifest.is_sealed():
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
    #: Reads only the event.
    positions = BOTH

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
    #: Reads only the event and the manifest.
    positions = BOTH

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
    #: Reads only the history the event met. Judged at OMEGA through check_at,
    #: the event is dropped from the end of the state's history first, so it is
    #: never compared with itself and the count is the same as at ALPHA. The end
    #: is told to the gate, not guessed: a replay of the last event looks the
    #: same at both ends.
    positions = BOTH

    def __init__(self, max_history: int = 10_000) -> None:
        super().__init__()
        self.max_history = max_history

    def check(self, event: Event, state: State, manifest: Manifest) -> Tuple[bool, str]:
        """Judge before the work: the state's history is the history the event meets."""
        return self._judge(event, state.history, state, manifest)

    def check_at(
        self, position: str, event: Event, state: State, manifest: Manifest
    ) -> Tuple[bool, str]:
        if position != OMEGA:
            return self.check(event, state, manifest)
        # Judged on the result, the history already ends with the event. The
        # history the event met is everything before it. A state that does not
        # end with the event is not the state this event produced.
        if not state.history or state.history[-1] != event:
            return False, "state does not end with the judged event"
        return self._judge(event, state.history[:-1], state, manifest)

    def _judge(
        self, event: Event, met: Sequence[Event], state: State, manifest: Manifest
    ) -> Tuple[bool, str]:
        """Judge the event against ``met``, the history it arrived at."""
        text = _text_of(event)
        for marker in DRIFT_MARKERS:
            if re.search(rf"\b{marker}\w*\b", text):
                return False, f"drift marker {marker!r} detected"

        if len(met) >= self.max_history:
            return False, (
                f"history has reached {len(met)} events; refusing to "
                f"grow an unbounded audit surface"
            )

        # Monotonic time. Out-of-order events make the ledger unorderable and
        # therefore unusable as evidence.
        if met and event.timestamp < met[-1].timestamp:
            return False, (
                f"event timestamp {event.timestamp} precedes the last recorded "
                f"event {met[-1].timestamp}"
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
    #: Counts its own rules. Each rule it holds still narrows this per rule.
    positions = BOTH

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
