"""Runtime patch injection, re-governed.

The archival pipeline ran

    ... -> Sentinel -> OBSERVE Layer -> MicroPatch Engine -> Model Adapter

and a review in the corpus identifies the consequence precisely: "the
MicroPatch engine executes after the governance controls and there is no
subsequent governance-control pass before model execution."

That is a hole in the governance envelope. Everything upstream is gated, and
then a patch rewrites the result and hands it straight to the model. A patch
is exactly the kind of hastily written emergency code that most needs
checking, and it was the one stage exempt from it.

This module closes the hole: every patch declares what it touches, patched
output is re-governed, and a patch whose output the gates refuse is discarded
with the original restored.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .gates import BaseGate, evaluate_gates
from .models import CheckOutcome, Event, Manifest, State

#: A patch takes an event and returns a replacement, or None to decline.
PatchFn = Callable[[Event, State], Optional[Event]]


@dataclass
class MicroPatch:
    """One emergency rule.

    ``expires_after`` is a count of applications, not a date, because a patch
    is a response to an incident and an incident ends. A patch with no
    expiry becomes permanent policy that never went through review.
    """

    name: str
    fn: PatchFn
    reason: str = ""
    expires_after: Optional[int] = None
    applications: int = 0

    @property
    def expired(self) -> bool:
        return (
            self.expires_after is not None
            and self.applications >= self.expires_after
        )


@dataclass
class PatchResult:
    event: Event
    applied: List[str] = field(default_factory=list)
    rejected: List[str] = field(default_factory=list)
    regoverned: bool = False
    gate_results: List[CheckOutcome] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.applied)


class MicroPatchEngine:
    """Applies emergency patches and re-governs the result."""

    def __init__(self, patches: Optional[Sequence[MicroPatch]] = None) -> None:
        self.patches: List[MicroPatch] = list(patches or [])

    def register(
        self,
        name: str,
        fn: PatchFn,
        reason: str = "",
        expires_after: Optional[int] = None,
    ) -> "MicroPatchEngine":
        if any(p.name == name for p in self.patches):
            raise ValueError(f"a patch named {name!r} is already registered")
        self.patches.append(
            MicroPatch(name=name, fn=fn, reason=reason, expires_after=expires_after)
        )
        return self

    def retire(self, name: str) -> bool:
        before = len(self.patches)
        self.patches = [p for p in self.patches if p.name != name]
        return len(self.patches) < before

    def prune_expired(self) -> List[str]:
        """Drop spent patches. Returns the names removed."""
        expired = [p.name for p in self.patches if p.expired]
        if expired:
            self.patches = [p for p in self.patches if not p.expired]
        return expired

    @property
    def active(self) -> List[str]:
        return [p.name for p in self.patches if not p.expired]

    def apply(
        self,
        event: Event,
        state: State,
        manifest: Manifest,
        gates: Sequence[BaseGate],
    ) -> PatchResult:
        """Apply every eligible patch, then re-govern the result.

        If the patched event fails the gates, the patch is discarded and the
        original event is returned unchanged. A patch may make the system more
        conservative; it may never smuggle an event past the gates.
        """
        result = PatchResult(event=event)
        current = event

        for patch in self.patches:
            if patch.expired:
                continue
            try:
                candidate = patch.fn(copy.deepcopy(current), state)
            except Exception as exc:
                result.rejected.append(f"{patch.name} (fault: {type(exc).__name__}: {exc})")
                continue

            if candidate is None:
                continue
            if not isinstance(candidate, Event):
                result.rejected.append(f"{patch.name} (returned {type(candidate).__name__}, not Event)")
                continue

            current = candidate
            patch.applications += 1
            result.applied.append(patch.name)

        if not result.applied:
            return result

        # The closed hole: patched output goes back through the gates.
        allowed, gate_results = evaluate_gates(gates, current, state, manifest)
        result.regoverned = True
        result.gate_results = gate_results

        if not allowed:
            failed = next((g for g in gate_results if not g.passed), None)
            detail = f"{failed.name}: {failed.reason}" if failed else "gate rejection"
            result.rejected.extend(f"{name} (re-governance: {detail})" for name in result.applied)
            result.applied = []
            result.event = event  # discard the patch, restore the original
            return result

        result.event = current
        return result
