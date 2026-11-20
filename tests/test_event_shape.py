"""Step 2 of the screening plan: check what a clinical event is, not what words it carries."""

import pytest

from perceive.engine import PERCEIVEEngine
from perceive.models import Event

HEALTHY = {"heart_rate": 140.0, "respiratory_rate": 45.0, "oxygen_saturation": 97.0, "temperature": 37.0}
WHEN = "2026-11-20T10:00:00"
META = {"authority": "attending"}


def observe(delta, event_type="observation"):
    event = Event(event_type, delta, "p1", timestamp=WHEN, metadata=META)
    _snapshot, response = PERCEIVEEngine().submit(event)
    return response


def admit(**overrides):
    delta = {"patient_id": "p1", "age_months": 12, "admission_time": WHEN, "vitals": HEALTHY}
    delta.update(overrides)
    return observe(delta, event_type="admission")


def test_a_well_formed_observation_is_accepted():
    assert observe({"vitals": HEALTHY}).accepted


def test_fewer_vitals_and_an_extra_measurement_are_allowed():
    assert observe({"vitals": {}}).accepted
    assert observe({"vitals": {"heart_rate": 140.0}}).accepted
    assert observe({"vitals": {**HEALTHY, "systolic_bp": 95}}).accepted


@pytest.mark.parametrize(
    "delta",
    [
        {"vitals": {**HEALTHY, "heart_rate": "140"}},  # text that reads as a number
        {"vitals": {**HEALTHY, "heart_rate": None}},  # a missing value that used to pass as "no data"
        {"vitals": {**HEALTHY, "heart_rate": True}},
        {"vitals": {**HEALTHY, "heart_rate": float("nan")}},
        {"vitals": {**HEALTHY, "heart_rate": float("inf")}},
        {"vitals": "fine"},
        {"vitals": HEALTHY, "note": "x"},  # a field no rule reads
        {},  # an observation with no vitals field
    ],
)
def test_a_malformed_observation_is_refused_by_the_shape_gate(delta):
    response = observe(delta)
    assert not response.accepted
    assert response.reason.startswith("event_shape:")


def test_an_event_type_the_manifest_does_not_describe_is_refused():
    response = observe({"vitals": HEALTHY}, event_type="teleport")
    assert not response.accepted
    assert response.reason.startswith("event_shape:")


@pytest.mark.parametrize(
    "overrides",
    [
        {"patient_id": "someone_else"},
        {"age_months": "12"},
        {"age_months": -1},
        {"age_months": float("nan")},
        {"admission_time": "last tuesday"},
        {"admission_time": 20261120},
        {"extra": 1},
    ],
)
def test_a_malformed_admission_is_refused(overrides):
    response = admit(**overrides)
    assert not response.accepted
    assert response.reason.startswith("event_shape:")


def test_a_well_formed_admission_is_accepted():
    assert admit().accepted


def test_shape_is_judged_first_so_the_reason_names_the_structure_problem():
    engine = PERCEIVEEngine()
    assert engine.gates[0].name == "event_shape"
