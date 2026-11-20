"""Items 36 to 40: empty gates, approved and ledgered clearances, snapshots, patch records."""

from types import MappingProxyType

import pytest

from perceive.consensus import GovernanceNode
from perceive.engine import PERCEIVEEngine
from perceive.micropatch import MicroPatchEngine
from perceive.models import DecisionStatus, Event, StateSnapshot, state_fingerprint

VITALS = {"heart_rate": 120, "respiratory_rate": 30, "temperature_c": 37.0,
          "oxygen_saturation": 97, "systolic_bp": 95}


def _bad(subject):
    # A well-formed admission whose authority label carries a listed word, so the word-list gate refuses
    # it (the event-shape gate does not).
    delta = {"patient_id": subject, "age_months": 12, "admission_time": "2026-11-20T10:00:00", "vitals": {}}
    return Event("admission", delta, subject, metadata={"authority": "classified"})


def _good(subject, vitals=None):
    return Event("observation", {"vitals": vitals or VITALS}, subject, metadata={"authority": "attending"})


# -- 36: an empty gate list is refused at build time ---------------------------

def test_an_engine_with_no_gates_is_refused():
    with pytest.raises(ValueError, match="at least one gate"):
        PERCEIVEEngine(gates=[])


def test_a_node_with_no_gates_is_refused():
    engine = PERCEIVEEngine()
    with pytest.raises(ValueError, match="at least one gate"):
        GovernanceNode("n1", [], engine.manifest)


# -- 37: clearances need an approver and are written to the ledger --------------

def test_an_engine_with_no_approvers_cannot_clear_a_refusal():
    engine = PERCEIVEEngine()
    engine.submit(_bad("P1"))
    with pytest.raises(PermissionError):
        engine.resolve_refusal("P1", resolved_by="dr_a", reason="reviewed")
    assert engine.unresolved_refusal("P1")


def test_someone_not_on_the_list_cannot_clear_a_refusal():
    engine = PERCEIVEEngine(approvers=["dr_a"])
    engine.submit(_bad("P1"))
    with pytest.raises(PermissionError):
        engine.resolve_refusal("P1", resolved_by="dr_b", reason="reviewed")
    assert engine.unresolved_refusal("P1")


def test_a_clearance_is_written_to_the_ledger_and_the_chain_still_verifies():
    engine = PERCEIVEEngine(approvers=["dr_a"])
    engine.submit(_bad("P1"))
    before = len(engine.kernel.audit_log)
    assert engine.resolve_refusal("P1", resolved_by="dr_a", reason="marker was a typo")
    assert len(engine.kernel.audit_log) == before + 1
    entry = engine.kernel.audit_log[-1]
    assert entry.decision is DecisionStatus.REFUSAL_RESOLVED
    assert entry.event.delta["resolved_by"] == "dr_a" and "classified" in entry.event.delta["cleared"]
    assert engine.verify()
    entry.reason = "tampered"
    assert engine.verify() is False


def test_a_clearance_with_nothing_pending_writes_nothing():
    engine = PERCEIVEEngine(approvers=["dr_a"])
    assert engine.resolve_refusal("P1", resolved_by="dr_a", reason="nothing pending") is False
    assert engine.kernel.audit_log == []


# -- 38: the state calls return read-only snapshots ----------------------------

def test_the_submitted_state_is_a_read_only_snapshot_that_cannot_reach_the_engine():
    engine = PERCEIVEEngine()
    state, response = engine.submit(_good("P2"))
    assert response.accepted and isinstance(state, StateSnapshot)
    with pytest.raises(AttributeError):
        state.history = ()
    with pytest.raises(TypeError):
        state.context["vitals"] = {}
    assert isinstance(state.context, MappingProxyType)
    assert isinstance(state.history, tuple)
    state.history[0].delta["vitals"] = "poisoned"
    assert engine.state_of("P2").history[0].delta["vitals"] == VITALS


def test_state_of_returns_a_fresh_snapshot_and_none_when_unknown():
    engine = PERCEIVEEngine()
    assert engine.state_of("nobody") is None
    engine.submit(_good("P2"))
    a, b = engine.state_of("P2"), engine.state_of("P2")
    assert a is not b and a.to_dict() == b.to_dict()


def test_a_snapshot_fingerprint_equals_the_live_state_fingerprint():
    engine = PERCEIVEEngine()
    snapshot, response = engine.submit(_good("P2"))
    assert state_fingerprint(snapshot) == response.state_hash


# -- 39: every patch attempt, rejected ones included, is in the ledger ----------

def test_a_rejected_patch_is_recorded_in_the_ledger_and_the_response():
    patches = MicroPatchEngine()
    patches.register("poison", lambda ev, st: Event(ev.event_type, {"note": "classified"}, ev.context_id,
                                                    ev.timestamp, ev.metadata))
    engine = PERCEIVEEngine(patches=patches)
    _, response = engine.submit(_good("P3"))
    assert response.patches_applied == []
    assert response.patches_rejected and "poison" in response.patches_rejected[0]
    entry = engine.kernel.audit_log[-1]
    assert entry.patches_rejected == response.patches_rejected
    assert entry.payload()["patches_rejected"] == response.patches_rejected
    assert engine.verify()
    entry.patches_rejected = ["something else"]
    assert engine.verify() is False


def test_an_entry_with_no_rejected_patch_hashes_as_before():
    engine = PERCEIVEEngine()
    engine.submit(_good("P3"))
    assert "patches_rejected" not in engine.kernel.audit_log[-1].payload()
