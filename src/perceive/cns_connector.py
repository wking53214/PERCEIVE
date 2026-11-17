"""Optional CNS connector: one PERCEIVE gate, usable at either end of a decision.

A CNS gate declares which end it belongs to. ALPHA runs before the work and
judges the proposed event against the state it would change. OMEGA runs on
the result and judges the state the work would produce. This connector wraps
an existing PERCEIVE gate, unchanged, and lets the caller choose the end when
the wrapper is built. The judgment itself stays in the gate.

Verdict mapping:
  - the gate allows          -> PASS
  - the gate refuses         -> TERMINAL_BREACH (PERCEIVE gates are vetoes and
                                carry no retry, so a refusal is final)
  - the gate raises, returns
    a non-boolean, or the
    content cannot be bound  -> TERMINAL_BREACH, recorded as a fault

Every verdict is bound to the content it judged through cns.gate.subject_digest,
so a verdict cannot be lifted onto a different event or state.

A gate is only wrapped at an end it declares (its ``positions``). The wrapper
does not change what the gate looks at, and a gate that compares the event with
the state before it sees, at OMEGA, a state that already contains the event.
Such a gate compares the event with itself and passes what ALPHA refuses. So a
gate that has not declared OMEGA is refused there with GatePositionNotSupported
when the wrapper is built, instead of being judged and waved through. A gate
that declares nothing is ALPHA only.

A gate that is declared for both ends and needs to know which one it is judging
defines ``check_at(position, event, state, manifest)``. The connector tells it,
so the gate never has to guess its end from the state. Other gates are judged
with ``check``, as before.

CNS is imported only when a connector is built. Without it, building one raises
CnsNotInstalled with the install command, and nothing else in PERCEIVE changes.
"""

from __future__ import annotations

import copy
import importlib
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Dict, Optional

from .gates import ALPHA as _ALPHA
from .gates import OMEGA as _OMEGA
from .gates import BaseGate, positions_of
from .models import Event, Manifest, State

__all__ = [
    "CnsNotInstalled",
    "GatePositionNotSupported",
    "Judged",
    "PerceiveCnsGate",
    "cns_available",
]

INSTALL_HINT = "pip install 'perceive[cns]'"


class CnsNotInstalled(ImportError):
    """Raised when cns.gate cannot be imported, or is too old to use."""


class GatePositionNotSupported(ValueError):
    """Raised when a gate is wrapped at an end it does not declare.

    A ValueError, so code that already catches a bad position keeps working.
    """

    def __init__(self, gate_name: str, position: str, supported: Any) -> None:
        self.gate_name = gate_name
        self.position = position
        self.supported = frozenset(supported)
        ends = ", ".join(sorted(self.supported)) or "none"
        if position == _OMEGA:
            why = (
                "At omega the state already contains the event, so a gate that compares the "
                "event with the state before it compares the event with itself and can pass "
                "what alpha refuses."
            )
        else:
            why = (
                "At alpha the state is the one before the event, so a gate written to read "
                "the state the event produces has nothing to read yet."
            )
        super().__init__(
            f"gate {gate_name!r} cannot be judged at the {position} end: it declares {ends}. "
            f"{why} Declare an end (the gate's `positions`) only after checking that the gate "
            "gives the right verdict there."
        )


def _cns_gate() -> ModuleType:
    """Import cns.gate on demand, or say exactly what is missing."""
    try:
        module = importlib.import_module("cns.gate")
    except ImportError as exc:
        raise CnsNotInstalled(
            "PERCEIVE's CNS connector needs the CNS package (cns.gate), which is "
            f"not installed. Install it with: {INSTALL_HINT}."
        ) from exc
    needed = ("Gate", "GateOutcome", "GatePosition", "GateResult", "subject_digest")
    missing = [name for name in needed if not hasattr(module, name)]
    if missing:
        raise CnsNotInstalled(
            "the installed cns.gate lacks " + ", ".join(missing) + ". The connector "
            f"needs the pinned CNS release. Install it with: {INSTALL_HINT}."
        )
    return module


def cns_available() -> bool:
    """Whether the CNS gate contract can be imported in this environment."""
    try:
        _cns_gate()
    except CnsNotInstalled:
        return False
    return True


@dataclass(frozen=True)
class Judged:
    """Everything a PERCEIVE gate needs to judge, as one candidate.

    At the ALPHA end, `state` is the state before the event is applied. At the
    OMEGA end, `state` is the state the event would produce.

    `before` is the state the event was applied to. At OMEGA, when it is given,
    the post-state must be exactly `before`'s history plus `event`: a state that
    merely ends with the event, but dropped or rewrote earlier history, is
    refused. Without `before` only the weaker check is possible (the history
    ends with the event). Build the wrapper with `require_before=True` to make
    `before` mandatory at OMEGA.
    """

    event: Event
    state: State
    manifest: Manifest
    before: Optional[State] = None


def _content(judged: Judged) -> Dict[str, Any]:
    """The judged content as plain values, so every consumer digests it alike."""
    event = judged.event
    content = {
        "event": {
            "event_type": event.event_type,
            "delta": event.delta,
            "context_id": event.context_id,
            "timestamp": event.timestamp,
            "metadata": event.metadata,
        },
        "state": judged.state.to_dict(),
        "manifest": {
            "version": judged.manifest.version,
            "hash": judged.manifest.hash,
        },
    }
    if judged.before is not None:  # absent when not given, so older digests are unchanged
        content["before"] = judged.before.to_dict()
    return content


class PerceiveCnsGate:
    """A PERCEIVE gate exposed as a CNS gate at the position the caller picks."""

    def __init__(self, gate: BaseGate, position: str = _ALPHA, require_before: bool = False) -> None:
        cns = _cns_gate()
        if position not in (_ALPHA, _OMEGA):
            raise ValueError(f"position must be 'alpha' or 'omega', not {position!r}")
        supported = positions_of(gate)
        if position not in supported:
            raise GatePositionNotSupported(
                getattr(gate, "name", type(gate).__name__), position, supported
            )
        self._cns = cns
        self._gate = gate
        self._end = position
        self._require_before = require_before
        self.name = f"perceive.{gate.name}"
        self.position = cns.GatePosition.ALPHA if position == _ALPHA else cns.GatePosition.OMEGA

    def check(self, candidate: Judged) -> Any:
        """Judge one candidate and return a CNS GateResult bound to its content.

        The gate judges private copies, so it cannot change what the caller holds.
        The caller's objects are digested before and again after the judgment; if
        they differ (something changed them while the gate ran), the verdict is a
        fault, because it would otherwise be bound to content that no longer exists.
        """
        cns = self._cns
        subject = f"perceive:{self.position.value}:{candidate.event.event_type}:{candidate.event.context_id}"

        def breach(reason: str, **bound: Any) -> Any:
            return cns.GateResult(
                gate=self.name, position=self.position,
                outcome=cns.GateOutcome.TERMINAL_BREACH, reason=reason, **bound,
            )

        try:
            digest = cns.subject_digest(_content(candidate))
            seen = copy.deepcopy((candidate.event, candidate.state, candidate.manifest))
        except Exception as exc:
            return breach(f"fault: content cannot be bound ({exc})")
        bound = {"subject": subject, "subject_digest": digest}

        if self.position is cns.GatePosition.OMEGA:
            problem = self._continuity_problem(candidate)
            if problem:
                return breach(problem, **bound)

        event, state, manifest = seen
        try:
            judge = getattr(self._gate, "check_at", None)
            if callable(judge):
                ok, reason = judge(self._end, event, state, manifest)
            else:
                ok, reason = self._gate.check(event, state, manifest)
        except Exception as exc:  # a gate that raises is a fault, never a pass
            return breach(f"fault: gate raised {type(exc).__name__}: {exc}", **bound)
        if not isinstance(ok, bool):
            return breach(f"fault: non-bool verdict {ok!r}", **bound)

        try:
            unchanged = cns.subject_digest(_content(candidate)) == digest
        except Exception:
            unchanged = False
        if not unchanged:
            return breach("fault: the judged content changed while the gate ran", **bound)

        outcome = cns.GateOutcome.PASS if ok else cns.GateOutcome.TERMINAL_BREACH
        return cns.GateResult(
            gate=self.name, position=self.position, outcome=outcome,
            reason=reason or "", **bound,
        )

    def _continuity_problem(self, candidate: Judged) -> str:
        """Why the OMEGA state is not the state this event produced, or ''."""
        history = candidate.state.history
        if not history or history[-1] != candidate.event:
            return ("post-state does not end with the judged event; it is not the "
                    "state this event produced")
        before = candidate.before
        if before is None:
            if self._require_before:
                return "no prior state given; the post-state cannot be checked for continuity"
            return ""
        if history[:-1] != before.history:
            return ("post-state is not the prior state plus this event; earlier history "
                    "was dropped or rewritten")
        return ""
