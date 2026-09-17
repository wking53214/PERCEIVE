"""PERCEIVE -- a governance kernel with a verifiable audit ledger.

Six gates under unanimous consensus, manifest-declared invariants checked
before and after every transition, and a hash-linked ledger that records every
decision, accepted or refused.

See PROVENANCE.md for what was recovered from the archives and what was
reconstructed.
"""

from .clinical import (
    SyntheticPatientDataset,
    evaluate_risk,
    pediatric_manifest,
)
from .consensus import (
    DistributedGovernanceKernel,
    GovernanceNode,
    majority_quorum,
)
from .engine import PERCEIVEEngine, PerceiveResponse, run_synthetic
from .gates import CANONICAL_ORDER, default_gates
from .kernel import ChainIntegrityError, GovernanceKernel
from .micropatch import MicroPatch, MicroPatchEngine
from .models import (
    AuditEntry,
    CheckOutcome,
    DecisionStatus,
    Event,
    ExecutionMetrics,
    Manifest,
    State,
)

__version__ = "2.0.0"

__all__ = [
    "PERCEIVEEngine",
    "PerceiveResponse",
    "GovernanceKernel",
    "ChainIntegrityError",
    "DistributedGovernanceKernel",
    "GovernanceNode",
    "majority_quorum",
    "MicroPatch",
    "MicroPatchEngine",
    "Event",
    "State",
    "Manifest",
    "AuditEntry",
    "CheckOutcome",
    "DecisionStatus",
    "ExecutionMetrics",
    "default_gates",
    "CANONICAL_ORDER",
    "pediatric_manifest",
    "evaluate_risk",
    "SyntheticPatientDataset",
    "run_synthetic",
    "__version__",
]
