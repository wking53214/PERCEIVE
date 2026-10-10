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

CNS is imported only when a connector is built. Without it, building one raises
CnsNotInstalled with the install command, and nothing else in PERCEIVE changes.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Dict

from .gates import BaseGate
from .models import Event, Manifest, State

__all__ = [
    "CnsNotInstalled",
    "Judged",
    "PerceiveCnsGate",
    "cns_available",
]

INSTALL_HINT = "pip install 'perceive[cns]'"

_ALPHA = "alpha"
_OMEGA = "omega"


class CnsNotInstalled(ImportError):
    """Raised when cns.gate cannot be imported, or is too old to use."""


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
    """

    event: Event
    state: State
    manifest: Manifest


def _content(judged: Judged) -> Dict[str, Any]:
    """The judged content as plain values, so every consumer digests it alike."""
    event = judged.event
    return {
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


class PerceiveCnsGate:
    """A PERCEIVE gate exposed as a CNS gate at the position the caller picks."""

    def __init__(self, gate: BaseGate, position: str = _ALPHA) -> None:
        cns = _cns_gate()
        if position not in (_ALPHA, _OMEGA):
            raise ValueError(f"position must be 'alpha' or 'omega', not {position!r}")
        self._cns = cns
        self._gate = gate
        self.name = f"perceive.{gate.name}"
        self.position = cns.GatePosition.ALPHA if position == _ALPHA else cns.GatePosition.OMEGA

    def check(self, candidate: Judged) -> Any:
        """Judge one candidate and return a CNS GateResult bound to its content."""
        cns = self._cns
        subject = f"perceive:{self.position.value}:{candidate.event.event_type}:{candidate.event.context_id}"
        try:
            digest = cns.subject_digest(_content(candidate))
        except TypeError as exc:
            return cns.GateResult(
                gate=self.name, position=self.position, outcome=cns.GateOutcome.TERMINAL_BREACH,
                reason=f"fault: content cannot be bound ({exc})",
            )
        try:
            ok, reason = self._gate.check(candidate.event, candidate.state, candidate.manifest)
        except Exception as exc:  # a gate that raises is a fault, never a pass
            return cns.GateResult(
                gate=self.name, position=self.position, outcome=cns.GateOutcome.TERMINAL_BREACH,
                reason=f"fault: gate raised {type(exc).__name__}: {exc}",
                subject=subject, subject_digest=digest,
            )
        if not isinstance(ok, bool):
            return cns.GateResult(
                gate=self.name, position=self.position, outcome=cns.GateOutcome.TERMINAL_BREACH,
                reason=f"fault: non-bool verdict {ok!r}", subject=subject, subject_digest=digest,
            )
        outcome = cns.GateOutcome.PASS if ok else cns.GateOutcome.TERMINAL_BREACH
        return cns.GateResult(
            gate=self.name, position=self.position, outcome=outcome,
            reason=reason or "", subject=subject, subject_digest=digest,
        )
