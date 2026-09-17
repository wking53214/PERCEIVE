"""Core data models for the PERCEIVE governance kernel.

Recovered substantially intact from the archival v1.0.0 monolith. The shapes
below -- Event, State, Manifest, AuditEntry -- are the archive's, and they
were sound: an append-only history, a content-addressed manifest, and an audit
entry that records both sides of every transition.

Additions are marked in PROVENANCE.md. The significant ones are gate outcome
records carrying a reason (the archive stored bare booleans in a tuple) and
``DecisionStatus.PATCH_REJECTED``.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


class DecisionStatus(Enum):
    """Terminal status of one governed transition."""

    ACCEPTED = "ACCEPTED"
    GATE_REJECTED = "GATE_REJECTED"
    INVARIANT_VIOLATED = "INVARIANT_VIOLATED"
    FAULT_DETECTED = "FAULT_DETECTED"
    #: Added: a micropatch produced a state the gates then refused. The
    #: archival pipeline had no way to express this, because it never
    #: re-governed patched output.
    PATCH_REJECTED = "PATCH_REJECTED"

    @property
    def accepted(self) -> bool:
        return self is DecisionStatus.ACCEPTED


@dataclass
class Event:
    """A proposed change. Never applied until the gates and invariants allow."""

    event_type: str
    delta: Dict[str, Any]
    context_id: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class State:
    """Governed state: an append-only history plus a derived context."""

    history: List[Event] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "history": [asdict(e) for e in self.history],
            "context": self.context,
        }


@dataclass
class Manifest:
    """A signed, content-addressed set of invariants.

    The hash covers the *source* of every invariant, not just its name, so
    silently swapping a rule's body changes the manifest hash and every audit
    entry written under it becomes attributable to a different manifest.
    """

    manifest_id: str
    version: str
    invariants: List[Callable[[State, Optional[Event]], bool]]
    invariant_names: List[str]
    author: str = ""
    approval_authority: str = ""
    effective_date: str = ""
    expiration_date: str = ""
    risk_level: str = ""
    signature: str = ""
    parent_manifest: Optional[str] = None
    change_log: List[str] = field(default_factory=list)
    hash: str = ""

    def __post_init__(self) -> None:
        if len(self.invariants) != len(self.invariant_names):
            raise ValueError(
                f"{len(self.invariants)} invariants but "
                f"{len(self.invariant_names)} names; every rule must be named "
                f"for the audit trail to identify it"
            )

    def compute_hash(self) -> str:
        """Content address covering invariant source.

        The archival implementation called ``inspect.getsource`` unguarded,
        which raises OSError for any invariant defined in a REPL, in exec'd
        code, or in a C extension -- taking the whole manifest down with it.
        Such an invariant is recorded by qualified name instead, so the
        manifest still hashes and the degradation is visible in the digest.
        """
        code_hashes: List[str] = []
        for inv in self.invariants:
            try:
                material = inspect.getsource(inv).encode()
            except (OSError, TypeError):
                material = f"<unavailable:{getattr(inv, '__qualname__', repr(inv))}>".encode()
            code_hashes.append(hashlib.sha256(material).hexdigest())

        schema = json.dumps(
            {
                "manifest_id": self.manifest_id,
                "version": self.version,
                "invariant_names": self.invariant_names,
                "author": self.author,
                "approval_authority": self.approval_authority,
                "effective_date": self.effective_date,
                "expiration_date": self.expiration_date,
                "risk_level": self.risk_level,
                "parent_manifest": self.parent_manifest,
                "bytecode_signatures": code_hashes,
            },
            sort_keys=True,
        )
        return hashlib.sha256(schema.encode()).hexdigest()

    def seal(self) -> "Manifest":
        """Fix the manifest's hash. Call once, after the rules are final."""
        self.hash = self.compute_hash()
        return self

    def is_sealed(self) -> bool:
        return bool(self.hash) and self.hash == self.compute_hash()

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Whether the manifest's authority has lapsed.

        An expired manifest is a governance failure rather than a formality:
        decisions made under lapsed authority are not defensible.
        """
        if not self.expiration_date:
            return False
        now = now or datetime.now()
        try:
            expiry = datetime.fromisoformat(self.expiration_date)
        except ValueError:
            return False
        return now > expiry


@dataclass(frozen=True)
class CheckOutcome:
    """One gate's or one invariant's verdict, with its reason.

    The archive stored ``(bool, Optional[str])`` tuples. Naming the fields is
    what lets a compliance export explain *why* a decision was made.
    """

    name: str
    passed: bool
    reason: str = ""
    fault: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AuditEntry:
    """One immutable record in the hash-linked ledger."""

    timestamp: str
    before: State
    event: Optional[Event]
    manifest_version: str
    manifest_hash: str
    after: State
    decision: DecisionStatus
    reason: str = ""
    gate_results: List[CheckOutcome] = field(default_factory=list)
    invariant_results: List[CheckOutcome] = field(default_factory=list)
    sequence_number: int = 0
    previous_hash: Optional[str] = None
    audit_hash: Optional[str] = None
    patches_applied: List[str] = field(default_factory=list)

    def payload(self) -> Dict[str, Any]:
        """Exactly the bytes the chain hash covers.

        Everything decision-relevant is inside, so altering any of it after
        the fact breaks the chain. ``audit_hash`` itself is excluded because
        it is the output.
        """
        return {
            "previous_hash": self.previous_hash,
            "timestamp": self.timestamp,
            "event": asdict(self.event) if self.event else None,
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "decision": self.decision.value,
            "manifest_version": self.manifest_version,
            "manifest_hash": self.manifest_hash,
            "sequence_number": self.sequence_number,
            "reason": self.reason,
            "gate_results": [g.to_dict() for g in self.gate_results],
            "invariant_results": [i.to_dict() for i in self.invariant_results],
            "patches_applied": self.patches_applied,
        }

    def compute_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.payload(), sort_keys=True, default=str).encode()
        ).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        d = self.payload()
        d["audit_hash"] = self.audit_hash
        return d


@dataclass
class ExecutionMetrics:
    total_events: int = 0
    accepted: int = 0
    gate_rejections: int = 0
    invariant_violations: int = 0
    faults: int = 0
    patch_rejections: int = 0
    gate_fault_counts: Dict[str, int] = field(default_factory=dict)
    invariant_fault_counts: Dict[str, int] = field(default_factory=dict)

    @property
    def acceptance_rate(self) -> float:
        return self.accepted / self.total_events if self.total_events else 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["acceptance_rate"] = round(self.acceptance_rate, 4)
        return d
