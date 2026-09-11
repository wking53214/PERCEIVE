"""Pediatric early-warning: invariants, risk model, and synthetic data.

Recovered from the archival v1.0.0 monolith, which expands PERCEIVE as the
"Pediatric Early-warning Research & Clinical Evidence Intelligence Validation
Engine". The vital-sign bounds, the four outcome classes, the z-score
formulation, the "Lyapunov energy" aggregate and the synthetic cohort
distribution are all the archive's.

Two corrections are marked below and explained in PROVENANCE.md. Both were
found by testing the recovered model, not by reading it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .gates import BaseGate
from .models import Event, Manifest, State

# --------------------------------------------------------------------------
# Invariants -- physiological bounds. Recovered verbatim.
# --------------------------------------------------------------------------

#: Bounds outside which a reading is not a patient state but a bad sensor.
VITAL_BOUNDS: Dict[str, Tuple[float, float]] = {
    "heart_rate": (80.0, 220.0),
    "respiratory_rate": (15.0, 90.0),
    "oxygen_saturation": (82.0, 100.0),
    "temperature": (35.0, 41.0),
}

#: Population reference (mean, sd) for the z-scores. Recovered verbatim.
VITAL_REFERENCE: Dict[str, Tuple[float, float]] = {
    "heart_rate": (140.0, 20.0),
    "respiratory_rate": (45.0, 10.0),
    "oxygen_saturation": (97.0, 3.0),
    "temperature": (37.0, 0.5),
}


def _vitals(state: State) -> Dict[str, float]:
    return (state.context or {}).get("vitals", {}) or {}


def _bounded(name: str):
    """Build an invariant enforcing one vital's bounds.

    A missing reading passes: absence of data is not evidence of a violation,
    and the gates are responsible for rejecting incomplete events.
    """
    low, high = VITAL_BOUNDS[name]

    def invariant(state: State, event: Optional[Event]) -> bool:
        value = _vitals(state).get(name)
        if value is None:
            return True
        return low <= float(value) <= high

    invariant.__name__ = f"invariant_{name}_range"
    return invariant


def invariant_history_append_only(state: State, event: Optional[Event]) -> bool:
    """History must only ever grow.

    The archival version was ``return True`` -- a stub that asserted the
    property without checking it, so the manifest advertised a guarantee it
    did not provide. The real check compares against the length recorded in
    context; if it is absent, there is nothing yet to contradict.
    """
    recorded = (state.context or {}).get("_history_length")
    if recorded is None:
        return True
    return len(state.history) >= int(recorded)


def pediatric_manifest(
    author: str = "PERCEIVE-System",
    approval_authority: str = "Clinical-Governance",
    expiration_date: str = "",
) -> Manifest:
    """The recovered PERCEIVE-PED-V1 manifest, sealed."""
    invariants = [_bounded(n) for n in ("heart_rate", "respiratory_rate", "oxygen_saturation", "temperature")]
    invariants.append(invariant_history_append_only)
    names = [
        "heart_rate_range",
        "respiratory_rate_range",
        "oxygen_saturation_floor",
        "temperature_range",
        "history_append_only",
    ]
    return Manifest(
        manifest_id="PERCEIVE-PED-V1",
        version="v1.0",
        invariants=invariants,
        invariant_names=names,
        author=author,
        approval_authority=approval_authority,
        effective_date=datetime.now().date().isoformat(),
        expiration_date=expiration_date,
        risk_level="HIGH",
    ).seal()


# --------------------------------------------------------------------------
# Clinical gates
# --------------------------------------------------------------------------

#: A reading that moves more than this fraction between observations is a
#: sensor artefact rather than physiology. Recovered verbatim.
MAX_VITAL_STEP = 0.60


class VitalsPlausibilityGate(BaseGate):
    """Reject implausible jumps between consecutive readings."""

    name = "vitals_plausible"

    def check(self, event: Event, state: State, manifest: Manifest) -> Tuple[bool, str]:
        if "vitals" not in event.delta:
            return True, ""
        previous = _vitals(state)
        incoming = event.delta.get("vitals") or {}
        if not previous:
            return True, ""

        for key in VITAL_BOUNDS:
            if key in previous and key in incoming:
                old = float(previous[key])
                new = float(incoming[key])
                if old > 0:
                    change = abs(new - old) / old
                    if change > MAX_VITAL_STEP:
                        return False, (
                            f"{key} moved {change:.0%} between readings "
                            f"({old} -> {new}), above the {MAX_VITAL_STEP:.0%} limit"
                        )
        return True, ""


class MonotonicTimeGate(BaseGate):
    """Observations must not travel backwards in time."""

    name = "monotonic_time"

    def check(self, event: Event, state: State, manifest: Manifest) -> Tuple[bool, str]:
        if not state.history:
            return True, ""
        last = state.history[-1].timestamp
        if event.timestamp < last:
            return False, f"timestamp {event.timestamp} precedes {last}"
        return True, ""


# --------------------------------------------------------------------------
# Risk model
# --------------------------------------------------------------------------


@dataclass
class RiskComponents:
    hr_z: float
    rr_z: float
    o2_z: float
    temp_z: float
    energy: float
    outcome: str

    def to_dict(self) -> Dict[str, float]:
        return {
            "hr_z": round(self.hr_z, 4),
            "rr_z": round(self.rr_z, 4),
            "o2_z": round(self.o2_z, 4),
            "temp_z": round(self.temp_z, 4),
            "energy": round(self.energy, 4),
        }


def compute_z(value: float, mean: float, std: float) -> float:
    if std <= 0:
        return 0.0
    return (value - mean) / std


#: Energy thresholds separating the four outcome classes. Recovered verbatim.
OUTCOME_THRESHOLDS: Sequence[Tuple[float, str]] = (
    (4.0, "normal"),
    (9.0, "early_warning"),
    (16.0, "deterioration"),
)
CRITICAL = "critical"


def classify_outcome(energy: float) -> str:
    for threshold, label in OUTCOME_THRESHOLDS:
        if energy < threshold:
            return label
    return CRITICAL


def evaluate_risk(vitals: Dict[str, float]) -> RiskComponents:
    """Sum of squared z-scores across four vitals.

    The archival code defaulted a *missing* heart rate and respiratory rate to
    0.0, which produces a large z-score and therefore a critical reading from
    an absent sensor -- the model's loudest alarm fires when it has the least
    information. Missing vitals now contribute their population mean, so an
    absent sensor contributes nothing rather than a false alarm. Whether a
    reading may be absent at all is a question for the gates.
    """
    z: Dict[str, float] = {}
    for name, (mean, std) in VITAL_REFERENCE.items():
        value = vitals.get(name)
        z[name] = 0.0 if value is None else compute_z(float(value), mean, std)

    energy = sum(v ** 2 for v in z.values())
    return RiskComponents(
        hr_z=z["heart_rate"],
        rr_z=z["respiratory_rate"],
        o2_z=z["oxygen_saturation"],
        temp_z=z["temperature"],
        energy=energy,
        outcome=classify_outcome(energy),
    )


# --------------------------------------------------------------------------
# Synthetic cohort
# --------------------------------------------------------------------------

#: Cohort mix and per-class vital distributions. Recovered verbatim.
OUTCOME_MIX: Dict[str, float] = {
    "normal": 0.70,
    "early_warning": 0.15,
    "deterioration": 0.10,
    "critical": 0.05,
}

_CLASS_PARAMS: Dict[str, Dict[str, Tuple[float, float]]] = {
    "normal": {"heart_rate": (130, 15), "respiratory_rate": (40, 8), "oxygen_saturation": (97, 1), "temperature": (37.0, 0.3)},
    "early_warning": {"heart_rate": (155, 12), "respiratory_rate": (52, 10), "oxygen_saturation": (94, 1.5), "temperature": (37.8, 0.4)},
    "deterioration": {"heart_rate": (170, 15), "respiratory_rate": (60, 12), "oxygen_saturation": (91, 2), "temperature": (38.5, 0.5)},
    "critical": {"heart_rate": (190, 20), "respiratory_rate": (70, 15), "oxygen_saturation": (85, 3), "temperature": (39.5, 0.8)},
}


class SyntheticPatientDataset:
    """Reproducible synthetic cohort for demos and tests.

    Sampled values are clamped into ``VITAL_BOUNDS``. The archival generator
    sampled unclamped Gaussians, so a critical patient could be handed an
    oxygen saturation below the manifest's own floor of 82 -- the dataset
    produced patients its own invariants rejected, and the demo reported them
    as governance violations rather than as the generator bug they were.
    """

    def __init__(self, num_patients: int = 290, seed: int = 42) -> None:
        self.num_patients = num_patients
        self.seed = seed
        self._rng = random.Random(seed)
        self.patients: List[Dict[str, Any]] = self._generate()

    def _sample(self, outcome: str) -> Dict[str, float]:
        vitals: Dict[str, float] = {}
        for name, (mean, sd) in _CLASS_PARAMS[outcome].items():
            low, high = VITAL_BOUNDS[name]
            value = self._rng.gauss(mean, sd)
            vitals[name] = round(min(max(value, low), high), 1)
        return vitals

    def _generate(self) -> List[Dict[str, Any]]:
        outcomes = self._rng.choices(
            list(OUTCOME_MIX), weights=list(OUTCOME_MIX.values()), k=self.num_patients
        )
        patients = []
        for i, outcome in enumerate(outcomes):
            patients.append(
                {
                    "patient_id": f"PT_{i + 1:04d}",
                    "age_months": self._rng.randint(0, 48),
                    "outcome": outcome,
                    "vitals": self._sample(outcome),
                    "admission_timestamp": (
                        datetime(2026, 1, 1) - timedelta(hours=self._rng.randint(1, 72))
                    ).isoformat(),
                }
            )
        return patients
