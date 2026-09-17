"""Compliance exporters.

The corpus names four target regimes -- HIPAA, FDA 510(k), SOX and GDPR -- and
describes exporters that render the audit ledger into each.

What an exporter can and cannot do is worth stating plainly, because the
archives are not careful about it. These functions render evidence that
already exists in the ledger into the shape each regime expects. They do not
establish compliance, and producing one is not an audit. The corpus contains
claims that these components were "the sole prerequisite tool for EU AI Act
compliance"; the same corpus rates that claim "Evidence Level: Low".

What the ledger genuinely supports is narrow and real: every decision is
recorded with its inputs, the rule that decided it, the manifest version in
force, and a hash link to the decision before it. That is a defensible
decision record. Whether the rules themselves satisfy a regulation is a
question for a person with standing to answer it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from .kernel import ChainIntegrityError, GovernanceKernel
from .models import AuditEntry, DecisionStatus, Manifest


@dataclass
class ExportEnvelope:
    """A rendered export plus the integrity claim it rests on."""

    regime: str
    generated_at: str
    manifest_id: str
    manifest_version: str
    manifest_hash: str
    chain_verified: bool
    chain_error: Optional[str]
    record_count: int
    records: List[Dict[str, Any]]
    caveats: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime,
            "generated_at": self.generated_at,
            "manifest": {
                "id": self.manifest_id,
                "version": self.manifest_version,
                "hash": self.manifest_hash,
            },
            "integrity": {
                "chain_verified": self.chain_verified,
                "chain_error": self.chain_error,
            },
            "record_count": self.record_count,
            "records": self.records,
            "caveats": self.caveats,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, default=str)


#: Applies to every export. An export whose chain does not verify is not
#: evidence of anything, so the caveat travels with the document.
BASE_CAVEATS = (
    "This export renders decisions already recorded in the audit ledger. It "
    "does not establish compliance with any regime.",
    "Integrity rests on the hash chain. If chain_verified is false, treat "
    "every record below as unverified.",
    "Whether the governing rules satisfy the regime is a determination for a "
    "qualified reviewer, not for this exporter.",
)


def _envelope(
    regime: str,
    kernel: GovernanceKernel,
    manifest: Manifest,
    records: List[Dict[str, Any]],
    caveats: Sequence[str] = (),
) -> ExportEnvelope:
    verified = False
    error: Optional[str] = None
    try:
        verified = kernel.verify_chain()
    except ChainIntegrityError as exc:
        error = str(exc)

    return ExportEnvelope(
        regime=regime,
        generated_at=datetime.now().isoformat(),
        manifest_id=manifest.manifest_id,
        manifest_version=manifest.version,
        manifest_hash=manifest.hash,
        chain_verified=verified,
        chain_error=error,
        record_count=len(records),
        records=records,
        caveats=list(BASE_CAVEATS) + list(caveats),
    )


def _decision_row(entry: AuditEntry) -> Dict[str, Any]:
    failed_gate = next((g for g in entry.gate_results if not g.passed), None)
    failed_invariant = next((i for i in entry.invariant_results if not i.passed), None)
    return {
        "sequence": entry.sequence_number,
        "timestamp": entry.timestamp,
        "decision": entry.decision.value,
        "reason": entry.reason,
        "subject": entry.event.context_id if entry.event else None,
        "event_type": entry.event.event_type if entry.event else None,
        "deciding_rule": (
            failed_gate.name if failed_gate else
            failed_invariant.name if failed_invariant else None
        ),
        "manifest_version": entry.manifest_version,
        "audit_hash": entry.audit_hash,
        "previous_hash": entry.previous_hash,
        "patches_applied": entry.patches_applied,
    }


def export_hipaa(kernel: GovernanceKernel, manifest: Manifest) -> ExportEnvelope:
    """Access-and-disclosure oriented view, per subject.

    Carries no vitals or context payloads: a compliance export that reproduces
    the protected data it is accounting for defeats its own purpose.
    """
    records = []
    for entry in kernel.audit_log:
        row = _decision_row(entry)
        row["phi_included"] = False
        records.append(row)
    return _envelope(
        "HIPAA",
        kernel,
        manifest,
        records,
        caveats=(
            "Protected health information is deliberately excluded; records "
            "reference subjects by context_id only.",
        ),
    )


def export_fda_510k(kernel: GovernanceKernel, manifest: Manifest) -> ExportEnvelope:
    """Device-history view: what the system decided, and under which rules."""
    records = []
    for entry in kernel.audit_log:
        row = _decision_row(entry)
        row["rules_in_force"] = [i.name for i in entry.invariant_results]
        row["gates_evaluated"] = [g.name for g in entry.gate_results]
        records.append(row)
    return _envelope(
        "FDA-510(k)",
        kernel,
        manifest,
        records,
        caveats=(
            "A decision record is one element of a submission. It is not a "
            "predicate comparison, a clinical evaluation, or a risk file.",
        ),
    )


def export_sox(kernel: GovernanceKernel, manifest: Manifest) -> ExportEnvelope:
    """Controls view: the change record for the rules themselves."""
    records = []
    for entry in kernel.audit_log:
        row = _decision_row(entry)
        row["manifest_hash"] = entry.manifest_hash
        row["control_outcome"] = "effective" if entry.decision.accepted else "exception"
        records.append(row)

    versions = sorted({e.manifest_hash for e in kernel.audit_log if e.manifest_hash})
    return _envelope(
        "SOX",
        kernel,
        manifest,
        records,
        caveats=(
            f"{len(versions)} distinct manifest hash(es) appear in this period; "
            f"more than one means the control set changed mid-period.",
        ),
    )


def export_gdpr(kernel: GovernanceKernel, manifest: Manifest, subject_id: str) -> ExportEnvelope:
    """Per-subject view, for access and explanation requests.

    Scoped to one data subject, since that is what the right of access covers.
    """
    entries = kernel.decisions_for(subject_id)
    records = []
    for entry in entries:
        row = _decision_row(entry)
        row["automated_decision"] = True
        row["logic_applied"] = entry.reason
        records.append(row)
    return _envelope(
        "GDPR",
        kernel,
        manifest,
        records,
        caveats=(
            f"Scoped to data subject {subject_id!r}.",
            "The 'logic applied' field names the rule that decided; it is not "
            "a full statement of the system's logic.",
        ),
    )


EXPORTERS = {
    "hipaa": export_hipaa,
    "fda": export_fda_510k,
    "sox": export_sox,
}
