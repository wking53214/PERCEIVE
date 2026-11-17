"""A gate declares the ends of a decision it can be judged at.

ALPHA judges an event against the state before it. OMEGA judges the state the
event produced, which already contains the event. A gate that compares the event
with the state before it compares the event with itself at OMEGA, and passes what
ALPHA refuses. These tests check that every declaration is true, in both
directions, and that nothing is left to a silent default.

None of this needs the CNS package. The connector's own refusal is tested in
test_cns_connector.py.
"""

import copy
import inspect

import pytest

import perceive.clinical as clinical_module
import perceive.gates as gates_module
from perceive.clinical import MonotonicTimeGate, VitalsPlausibilityGate
from perceive.gates import (
    ALPHA,
    ALPHA_ONLY,
    BOTH,
    OMEGA,
    OMEGA_ONLY,
    BaseGate,
    BoundaryGate,
    CitadelGate,
    FortressGate,
    InvariantValidatorGate,
    MicroPatchGate,
    SentinelGate,
    positions_of,
)
from perceive.models import Event, Manifest, State

OK = {"heart_rate": 130.0, "respiratory_rate": 40.0, "oxygen_saturation": 97.0, "temperature": 37.0}


def produced(event, prior):
    """The state the kernel would produce: the prior state plus the event."""
    post = copy.deepcopy(prior)
    post.history.append(event)
    post.context.update(event.delta)
    return post


def manifests():
    return {
        "sealed": Manifest("M", "v1", [], []).seal(),
        "high-risk": Manifest("H", "v1", [], [], risk_level="HIGH").seal(),
        "expired": Manifest("E", "v1", [], [], expiration_date="2000-01-01T00:00:00").seal(),
        "unsealed": Manifest("U", "v1", [], []),
    }


def priors():
    return {
        "empty": State(),
        "one-event": State(
            history=[Event("observation", {"vitals": OK}, "p1", timestamp="2026-01-01T00:05:00")],
            context={"vitals": OK},
        ),
    }


def events():
    t = "2026-01-01T00:10:00"
    return {
        "clean": Event("observation", {"n": 1}, "p1", timestamp=t),
        "harm": Event("observation", {"note": "harm"}, "p1", timestamp=t),
        "forbidden": Event("observation", {"note": "forbidden"}, "p1", timestamp=t),
        "unsafe": Event("observation", {"note": "unsafe"}, "p1", timestamp=t),
        "drift": Event("observation", {"note": "drift"}, "p1", timestamp=t),
        "oversize": Event("observation", {"blob": "x" * 600}, "p1", timestamp=t),
        "no-type": Event("", {"n": 1}, "p1", timestamp=t),
        "no-context": Event("observation", {"n": 1}, "", timestamp=t),
        "with-authority": Event("observation", {"n": 1}, "p1", timestamp=t, metadata={"authority": "attending"}),
        "backdated": Event("observation", {"n": 1}, "p1", timestamp="2025-12-31T00:00:00"),
        "vitals-jump": Event("observation", {"vitals": {**OK, "heart_rate": 220.0}}, "p1", timestamp=t),
    }


# --- the declarations are true ----------------------------------------------


@pytest.mark.parametrize(
    "make",
    [BoundaryGate, InvariantValidatorGate, FortressGate, CitadelGate, MicroPatchGate, MonotonicTimeGate, SentinelGate],
    ids=lambda c: c.__name__,
)
def test_a_gate_declared_for_both_ends_gives_the_same_verdict_at_both(make):
    """The claim behind `positions = BOTH`: the verdict does not depend on whether
    the state is the one before the event or the one after it. Each end is judged
    the way the connector judges it, with the end told to the gate."""
    gate = make()
    for m_name, manifest in manifests().items():
        for p_name, prior in priors().items():
            for e_name, event in events().items():
                before = gate.check_at(ALPHA, event, prior, manifest)
                after = gate.check_at(OMEGA, event, produced(event, prior), manifest)
                assert before == after, f"{gate.name}: {m_name} / {p_name} / {e_name}: {before} != {after}"


def test_the_vitals_gate_refuses_a_jump_before_the_work_and_passes_it_after():
    """Why VitalsPlausibilityGate is ALPHA only. If this starts failing, the gate
    was made position-safe: declare it BOTH and move the case into the test above."""
    prior = priors()["one-event"]
    jump = events()["vitals-jump"]
    gate = VitalsPlausibilityGate()
    manifest = manifests()["sealed"]
    assert gate.check(jump, prior, manifest)[0] is False
    assert gate.check(jump, produced(jump, prior), manifest)[0] is True


def test_the_end_must_be_told_to_the_sentinel_gate_not_left_to_the_state():
    """Why SentinelGate has check_at. The plain check() on the state after the work
    compares the event with itself and passes a backdated event. Told its end, the
    gate refuses it, exactly as it does before the work."""
    prior = priors()["one-event"]
    late = events()["backdated"]
    gate = SentinelGate()
    manifest = manifests()["sealed"]
    post = produced(late, prior)
    assert gate.check(late, prior, manifest)[0] is False
    assert gate.check(late, post, manifest)[0] is True, "the naive call is still blind"
    assert gate.check_at(OMEGA, late, post, manifest)[0] is False


@pytest.mark.parametrize("prior_len", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("replay", [False, True], ids=["new-event", "exact-replay-of-last"])
def test_the_history_limit_is_the_same_at_both_ends(prior_len, replay):
    """The case that rules out guessing the end from the state: an event that is an
    exact replay of the last recorded one looks the same before and after the
    work. Inferring the end would let it through at the limit."""
    gate = SentinelGate(max_history=3)
    history = [
        Event("observation", {"i": i}, "p1", timestamp=f"2026-01-01T00:0{i + 1}:00")
        for i in range(prior_len)
    ]
    if replay and history:
        event = copy.deepcopy(history[-1])
    else:
        event = Event("observation", {"i": 99}, "p1", timestamp="2026-01-01T00:09:00")
    prior = State(history=history)
    manifest = manifests()["sealed"]
    expected = prior_len < 3
    assert gate.check_at(ALPHA, event, prior, manifest)[0] is expected
    assert gate.check_at(OMEGA, event, produced(event, prior), manifest)[0] is expected


def test_the_sentinel_gate_refuses_a_state_that_does_not_end_with_the_event_at_omega():
    gate = SentinelGate()
    event = events()["clean"]
    manifest = manifests()["sealed"]
    assert gate.check_at(OMEGA, event, State(), manifest)[0] is False
    other = State(history=[Event("observation", {"other": 1}, "p1", timestamp="2026-01-01T00:05:00")])
    assert gate.check_at(OMEGA, event, other, manifest)[0] is False


def test_a_gate_that_does_not_care_about_its_end_is_judged_with_check():
    gate = FortressGate()
    event = events()["forbidden"]
    prior = priors()["empty"]
    manifest = manifests()["sealed"]
    assert gate.check_at(ALPHA, event, prior, manifest) == gate.check(event, prior, manifest)


# --- nothing is left to a silent default -------------------------------------


def _built_in_gate_classes():
    for module in (gates_module, clinical_module):
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, BaseGate) and cls is not BaseGate and cls.__module__ == module.__name__:
                yield cls


def test_every_built_in_gate_states_its_positions_itself():
    """A new gate must be classified on purpose. Inheriting the default is not a
    declaration, so this fails until the author has decided."""
    found = list(_built_in_gate_classes())
    assert len(found) >= 8, "the scan should find the six gates and the two clinical gates"
    undeclared = [cls.__name__ for cls in found if "positions" not in vars(cls)]
    assert undeclared == [], f"declare `positions` on: {undeclared}"


def test_the_declarations_are_what_the_code_supports():
    expected = {
        BoundaryGate: BOTH,
        InvariantValidatorGate: BOTH,
        FortressGate: BOTH,
        CitadelGate: BOTH,
        MicroPatchGate: BOTH,
        SentinelGate: BOTH,
        VitalsPlausibilityGate: ALPHA_ONLY,
        MonotonicTimeGate: BOTH,
    }
    assert {cls: cls().supported_positions() for cls in expected} == expected


def test_a_plain_gate_defaults_to_the_end_the_kernel_uses():
    assert BaseGate().supported_positions() == ALPHA_ONLY


def test_the_default_gate_set_is_unchanged_in_order_and_names():
    assert [g.name for g in gates_module.default_gates()] == list(gates_module.CANONICAL_ORDER)


# --- rules narrow a gate -----------------------------------------------------


def test_a_rule_is_alpha_only_unless_it_declares_otherwise():
    gate = BaseGate(positions=BOTH)
    gate.add_rule("reads_state", lambda e, s, m: len(s.history) < 5)
    assert gate.supported_positions() == ALPHA_ONLY


def test_a_rule_that_declares_both_ends_leaves_a_both_gate_alone():
    gate = BaseGate(positions=BOTH)
    gate.add_rule("reads_event", lambda e, s, m: "bad" not in e.delta, positions=BOTH)
    assert gate.supported_positions() == BOTH


def test_rules_handed_in_at_construction_are_alpha_only():
    gate = BaseGate(rules=[("r", lambda e, s, m: True)], positions=BOTH)
    assert gate.supported_positions() == ALPHA_ONLY


def test_a_rule_appended_straight_to_the_list_is_alpha_only():
    gate = BaseGate(positions=BOTH)
    gate.rules.append(("sneaked_in", lambda e, s, m: True))
    assert gate.supported_positions() == ALPHA_ONLY


def test_two_rules_with_one_name_do_not_share_a_declaration():
    gate = BaseGate(positions=BOTH)
    gate.add_rule("same", lambda e, s, m: True, positions=BOTH)
    gate.add_rule("same", lambda e, s, m: True)
    assert gate.supported_positions() == ALPHA_ONLY


def test_a_gate_and_its_rules_can_share_no_end():
    gate = BaseGate(positions=OMEGA_ONLY)
    gate.add_rule("reads_event", lambda e, s, m: True, positions=ALPHA_ONLY)
    assert gate.supported_positions() == frozenset()


def test_the_micropatch_gate_is_narrowed_by_the_patches_it_holds():
    gate = MicroPatchGate()
    assert gate.supported_positions() == BOTH
    gate.add_rule("emergency_patch", lambda e, s, m: True)
    assert gate.supported_positions() == ALPHA_ONLY


# --- declarations are validated ----------------------------------------------


def test_a_single_string_is_a_valid_declaration():
    class Late(BaseGate):
        name = "late"
        positions = "omega"

    assert Late().supported_positions() == OMEGA_ONLY


@pytest.mark.parametrize("bad", [{"midle"}, {"alpha", "gamma"}, set(), []])
def test_an_unknown_or_empty_declaration_is_an_error_not_a_default(bad):
    class Broken(BaseGate):
        name = "broken"
        positions = bad

    with pytest.raises(ValueError):
        Broken().supported_positions()
    with pytest.raises(ValueError):
        positions_of(Broken())


def test_the_constructor_declaration_is_validated_too():
    with pytest.raises(ValueError):
        BaseGate(positions={"nowhere"})


# --- any gate-shaped object ---------------------------------------------------


def test_a_gate_that_says_nothing_is_alpha_only():
    class Silent:
        name = "silent"

        def check(self, event, state, manifest):
            return True, ""

    assert positions_of(Silent()) == ALPHA_ONLY


def test_a_gate_shaped_object_can_declare_its_ends():
    class Declared:
        name = "declared"
        positions = BOTH

        def check(self, event, state, manifest):
            return True, ""

    assert positions_of(Declared()) == BOTH
