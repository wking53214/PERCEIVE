"""Clinical layer: invariants, risk model, and the synthetic cohort.

Several tests here pin corrections to the recovered archival code. Each is
labelled with what the archive did and why it was wrong.
"""

import pytest

from perceive.clinical import (
    MAX_VITAL_STEP,
    OUTCOME_MIX,
    VITAL_BOUNDS,
    MonotonicTimeGate,
    SyntheticPatientDataset,
    VitalsPlausibilityGate,
    classify_outcome,
    evaluate_risk,
    invariant_history_append_only,
    pediatric_manifest,
)
from perceive.models import Event, State

HEALTHY = {
    "heart_rate": 130.0,
    "respiratory_rate": 40.0,
    "oxygen_saturation": 97.0,
    "temperature": 37.0,
}


class TestRiskModel:
    def test_population_mean_scores_as_normal(self):
        result = evaluate_risk(
            {"heart_rate": 140, "respiratory_rate": 45, "oxygen_saturation": 97, "temperature": 37.0}
        )
        assert result.energy == pytest.approx(0.0)
        assert result.outcome == "normal"

    def test_severe_derangement_scores_as_critical(self):
        result = evaluate_risk(
            {"heart_rate": 200, "respiratory_rate": 80, "oxygen_saturation": 84, "temperature": 40.0}
        )
        assert result.outcome == "critical"

    @pytest.mark.parametrize(
        "energy,expected",
        [(0.0, "normal"), (3.9, "normal"), (4.0, "early_warning"),
         (8.9, "early_warning"), (9.0, "deterioration"), (15.9, "deterioration"),
         (16.0, "critical"), (100.0, "critical")],
    )
    def test_outcome_thresholds(self, energy, expected):
        assert classify_outcome(energy) == expected

    def test_missing_vitals_do_not_manufacture_an_alarm(self):
        """Archival correction.

        The recovered code defaulted a missing heart rate and respiratory rate
        to 0.0, giving z-scores of -7.0 and -4.5 and an energy of 69 -- so an
        absent sensor produced the model's loudest alarm. Missing readings now
        contribute their population mean, and therefore nothing.
        """
        assert evaluate_risk({}).outcome == "normal"
        assert evaluate_risk({}).energy == pytest.approx(0.0)

    def test_a_single_missing_vital_does_not_swamp_the_others(self):
        partial = dict(HEALTHY)
        del partial["heart_rate"]
        assert evaluate_risk(partial).outcome == "normal"


class TestInvariants:
    def test_manifest_seals_with_five_named_invariants(self):
        m = pediatric_manifest()
        assert len(m.invariants) == len(m.invariant_names) == 5
        assert m.is_sealed()
        assert m.risk_level == "HIGH"

    @pytest.mark.parametrize("vital,bad", [
        ("heart_rate", 40.0), ("heart_rate", 300.0),
        ("respiratory_rate", 5.0), ("respiratory_rate", 120.0),
        ("oxygen_saturation", 50.0), ("temperature", 30.0), ("temperature", 45.0),
    ])
    def test_out_of_range_vitals_violate_their_invariant(self, vital, bad):
        m = pediatric_manifest()
        state = State(context={"vitals": {**HEALTHY, vital: bad}})
        assert not all(inv(state, None) for inv in m.invariants)

    def test_in_range_vitals_satisfy_every_invariant(self):
        m = pediatric_manifest()
        state = State(context={"vitals": HEALTHY})
        assert all(inv(state, None) for inv in m.invariants)

    def test_missing_vitals_do_not_violate_bounds(self):
        m = pediatric_manifest()
        assert all(inv(State(), None) for inv in m.invariants)

    def test_history_append_only_actually_checks(self):
        """Archival correction: the recovered version was ``return True``.

        It advertised a guarantee the manifest never enforced.
        """
        state = State(context={"_history_length": 3})
        state.history = [Event("obs", {}, "p1")] * 3
        assert invariant_history_append_only(state, None)

        truncated = State(context={"_history_length": 3})
        truncated.history = [Event("obs", {}, "p1")]
        assert not invariant_history_append_only(truncated, None)


class TestClinicalGates:
    def test_implausible_jump_rejected(self):
        state = State(context={"vitals": HEALTHY})
        event = Event("observation", {"vitals": {**HEALTHY, "heart_rate": 220.0}}, "p1")
        ok, reason = VitalsPlausibilityGate().check(event, state, pediatric_manifest())
        assert not ok and "heart_rate" in reason

    def test_gradual_change_allowed(self):
        state = State(context={"vitals": HEALTHY})
        event = Event("observation", {"vitals": {**HEALTHY, "heart_rate": 150.0}}, "p1")
        assert VitalsPlausibilityGate().check(event, state, pediatric_manifest())[0]

    def test_first_observation_has_nothing_to_compare_against(self):
        event = Event("observation", {"vitals": HEALTHY}, "p1")
        assert VitalsPlausibilityGate().check(event, State(), pediatric_manifest())[0]

    def test_backwards_observation_rejected(self):
        state = State()
        state.history.append(Event("obs", {}, "p1", timestamp="2026-06-01T12:00:00"))
        late = Event("obs", {}, "p1", timestamp="2026-01-01T00:00:00")
        assert not MonotonicTimeGate().check(late, state, pediatric_manifest())[0]


class TestSyntheticCohort:
    def test_cohort_is_reproducible(self):
        a = SyntheticPatientDataset(num_patients=50, seed=7).patients
        b = SyntheticPatientDataset(num_patients=50, seed=7).patients
        assert a == b

    def test_every_generated_patient_satisfies_the_manifest(self):
        """Archival correction.

        The recovered generator sampled unclamped Gaussians, so 4 of its 290
        patients fell outside the manifest's own bounds -- the dataset
        produced patients its own invariants rejected, and the demo reported
        them as governance violations rather than as a generator bug.
        """
        dataset = SyntheticPatientDataset(num_patients=290, seed=42)
        manifest = pediatric_manifest()
        for patient in dataset.patients:
            state = State(context={"vitals": patient["vitals"]})
            assert all(inv(state, None) for inv in manifest.invariants), patient

    def test_vitals_stay_inside_declared_bounds(self):
        for patient in SyntheticPatientDataset(num_patients=200, seed=3).patients:
            for name, (low, high) in VITAL_BOUNDS.items():
                assert low <= patient["vitals"][name] <= high

    def test_cohort_mix_is_roughly_as_declared(self):
        from collections import Counter

        patients = SyntheticPatientDataset(num_patients=2000, seed=11).patients
        mix = Counter(p["outcome"] for p in patients)
        for label, expected in OUTCOME_MIX.items():
            assert abs(mix[label] / 2000 - expected) < 0.04, label
