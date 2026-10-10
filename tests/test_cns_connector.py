"""PERCEIVE's CNS connector: one gate, either end, fail-closed, bound to content."""

import importlib
import sys

import pytest

from perceive.cns_connector import CnsNotInstalled, Judged, PerceiveCnsGate, cns_available
from perceive.gates import BaseGate
from perceive.models import Event, Manifest, State

cns_gate = pytest.importorskip("cns.gate", reason="needs the pinned CNS package")


def manifest():
    return Manifest("M", "v1", [], []).seal()


def refuse_marked(name="no_marked"):
    """A gate that refuses any event whose delta carries a 'bad' key."""
    return BaseGate(rules=[(name, lambda e, s, m: "bad" not in e.delta)])


class Fixed:
    """A stub gate returning whatever its function returns, for odd verdicts."""

    def __init__(self, name, fn):
        self.name = name
        self.fn = fn

    def check(self, event, state, manifest):
        return self.fn(event, state, manifest)


class Named(BaseGate):
    name = "sentinel"


def judged(delta, state=None):
    return Judged(Event("observation", delta, "p1"), state or State(), manifest())


def test_alpha_passes_an_allowed_event():
    gate = PerceiveCnsGate(refuse_marked(), position="alpha")
    result = gate.check(judged({"n": 1}))
    assert result.outcome is cns_gate.GateOutcome.PASS
    assert result.position is cns_gate.GatePosition.ALPHA


def test_alpha_refuses_a_blocked_event_with_the_gate_reason():
    gate = PerceiveCnsGate(refuse_marked(), position="alpha")
    result = gate.check(judged({"bad": True}))
    assert result.outcome is cns_gate.GateOutcome.TERMINAL_BREACH
    assert result.blocking()
    assert "no_marked" in result.reason or result.reason


def test_omega_judges_the_state_the_work_would_produce():
    no_bad_history = BaseGate(
        rules=[("no_bad_history", lambda e, s, m: not any("bad" in ev.delta for ev in s.history))]
    )
    gate = PerceiveCnsGate(no_bad_history, position="omega")
    result_state = State(history=[Event("observation", {"bad": 1}, "p1")])
    refused = gate.check(judged({"n": 1}, state=result_state))
    assert refused.position is cns_gate.GatePosition.OMEGA
    assert refused.outcome is cns_gate.GateOutcome.TERMINAL_BREACH
    assert gate.check(judged({"n": 1})).outcome is cns_gate.GateOutcome.PASS


def test_gate_name_carries_the_perceive_prefix():
    assert PerceiveCnsGate(Named(), position="alpha").name == "perceive.sentinel"


def test_verdict_binds_to_the_content_it_judged():
    gate = PerceiveCnsGate(refuse_marked(), position="alpha")
    result = gate.check(judged({"n": 1}))
    assert result.bound()
    assert result.binds(result.subject, result.subject_digest)
    other = gate.check(judged({"n": 2}))
    assert not result.binds(other.subject, other.subject_digest)


def test_a_raising_gate_is_a_fault_not_a_pass():
    def explode(e, s, m):
        raise RuntimeError("boom")

    gate = PerceiveCnsGate(BaseGate(rules=[("explode", explode)]), position="alpha")
    result = gate.check(judged({"n": 1}))
    assert result.outcome is cns_gate.GateOutcome.TERMINAL_BREACH
    assert "fault" in result.reason and "boom" in result.reason


def test_a_non_boolean_verdict_is_a_fault_not_a_pass():
    gate = PerceiveCnsGate(Fixed("odd", lambda e, s, m: ("yes", "r")), position="omega")
    result = gate.check(judged({"n": 1}))
    assert result.outcome is cns_gate.GateOutcome.TERMINAL_BREACH
    assert "non-bool" in result.reason


def test_unbindable_content_is_refused_not_passed():
    bad = Judged(Event("observation", {"n": float("nan")}, "p1"), State(), manifest())
    gate = PerceiveCnsGate(refuse_marked(), position="alpha")
    result = gate.check(bad)
    assert result.outcome is cns_gate.GateOutcome.TERMINAL_BREACH
    assert "cannot be bound" in result.reason


def test_unknown_position_is_rejected():
    with pytest.raises(ValueError):
        PerceiveCnsGate(refuse_marked(), position="middle")


def test_missing_cns_raises_with_the_install_command(monkeypatch):
    real_import = importlib.import_module

    def no_cns(name, *args, **kwargs):
        if name == "cns.gate":
            raise ModuleNotFoundError("No module named 'cns'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", no_cns)
    assert cns_available() is False
    with pytest.raises(CnsNotInstalled, match="pip install 'perceive\\[cns\\]'"):
        PerceiveCnsGate(refuse_marked(), position="alpha")
