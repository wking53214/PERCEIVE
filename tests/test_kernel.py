"""Kernel: transitions, invariants, and audit-chain integrity."""

import copy

import pytest

from perceive.gates import default_gates
from perceive.kernel import ChainIntegrityError, GovernanceKernel
from perceive.models import DecisionStatus, Event, Manifest, State


def always(state, event):
    return True


def never(state, event):
    return False


def explodes(state, event):
    raise RuntimeError("invariant blew up")


@pytest.fixture
def manifest():
    return Manifest("M", "v1", [always], ["always"]).seal()


@pytest.fixture
def kernel():
    return GovernanceKernel()


@pytest.fixture
def gates():
    return default_gates()


def ev(n=0, ctx="p1", **kw):
    return Event("observation", {"n": n}, ctx, **kw)


class TestTransition:
    def test_accepted_event_advances_state(self, kernel, manifest, gates):
        state, hash_, entry = kernel.run_event(State(), ev(1), manifest, gates)
        assert entry.decision is DecisionStatus.ACCEPTED
        assert len(state.history) == 1
        assert state.context["n"] == 1
        assert hash_

    def test_rejected_event_leaves_state_untouched(self, kernel, manifest, gates):
        original = State()
        event = Event("observation", {"note": "this is forbidden"}, "p1")
        state, hash_, entry = kernel.run_event(original, event, manifest, gates)
        assert entry.decision is DecisionStatus.GATE_REJECTED
        assert state.history == []
        assert hash_ is None

    def test_invariant_violation_is_distinguished_from_gate_rejection(self, kernel, gates):
        m = Manifest("M", "v1", [never], ["never"]).seal()
        _, _, entry = kernel.run_event(State(), ev(1), m, gates)
        assert entry.decision is DecisionStatus.INVARIANT_VIOLATED

    def test_raising_invariant_is_a_violation_not_a_pass(self, kernel, gates):
        """An exception must never be read as permission."""
        m = Manifest("M", "v1", [explodes], ["explodes"]).seal()
        _, _, entry = kernel.run_event(State(), ev(1), m, gates)
        assert entry.decision is DecisionStatus.INVARIANT_VIOLATED
        assert any(i.fault for i in entry.invariant_results)

    def test_raising_gate_is_a_fault_not_a_pass(self, kernel, manifest):
        class Exploding:
            name = "exploding"

            def check(self, event, state, manifest):
                raise RuntimeError("gate blew up")

        _, _, entry = kernel.run_event(State(), ev(1), manifest, [Exploding()])
        assert entry.decision is DecisionStatus.FAULT_DETECTED

    def test_post_transition_invariant_catches_bad_result(self, kernel, gates):
        def n_below_10(state, event):
            return state.context.get("n", 0) < 10

        m = Manifest("M", "v1", [n_below_10], ["n_below_10"]).seal()
        state, _, entry = kernel.run_event(State(), ev(99), m, gates)
        assert entry.decision is DecisionStatus.INVARIANT_VIOLATED
        assert "post-transition" in entry.reason
        assert state.history == []


class TestAuditChain:
    def test_every_decision_is_recorded(self, kernel, manifest, gates):
        state = State()
        state, _, _ = kernel.run_event(state, ev(1), manifest, gates)
        kernel.run_event(state, Event("obs", {"x": "forbidden"}, "p1"), manifest, gates)
        assert len(kernel.audit_log) == 2
        assert [e.decision for e in kernel.audit_log] == [
            DecisionStatus.ACCEPTED,
            DecisionStatus.GATE_REJECTED,
        ]

    def test_chain_verifies_when_untouched(self, kernel, manifest, gates):
        state = State()
        for i in range(5):
            state, _, _ = kernel.run_event(state, ev(i), manifest, gates)
        assert kernel.verify_chain() is True

    def test_altering_a_reason_breaks_the_chain(self, kernel, manifest, gates):
        state = State()
        for i in range(3):
            state, _, _ = kernel.run_event(state, ev(i), manifest, gates)
        kernel.audit_log[1].reason = "tampered"
        with pytest.raises(ChainIntegrityError) as exc:
            kernel.verify_chain()
        assert exc.value.sequence_number == 2

    def test_altering_a_decision_breaks_the_chain(self, kernel, manifest, gates):
        state, _, _ = kernel.run_event(State(), ev(1), manifest, gates)
        kernel.run_event(state, Event("obs", {"x": "forbidden"}, "p1"), manifest, gates)
        kernel.audit_log[1].decision = DecisionStatus.ACCEPTED
        with pytest.raises(ChainIntegrityError):
            kernel.verify_chain()

    def test_deleting_an_entry_breaks_the_chain(self, kernel, manifest, gates):
        state = State()
        for i in range(4):
            state, _, _ = kernel.run_event(state, ev(i), manifest, gates)
        del kernel.audit_log[1]
        with pytest.raises(ChainIntegrityError):
            kernel.verify_chain()

    def test_reordering_entries_breaks_the_chain(self, kernel, manifest, gates):
        state = State()
        for i in range(4):
            state, _, _ = kernel.run_event(state, ev(i), manifest, gates)
        kernel.audit_log[1], kernel.audit_log[2] = kernel.audit_log[2], kernel.audit_log[1]
        with pytest.raises(ChainIntegrityError):
            kernel.verify_chain()

    def test_ledger_is_immune_to_later_mutation_of_returned_state(
        self, kernel, manifest, gates
    ):
        """A caller mutating the state it got back must not alter the ledger."""
        state, _, _ = kernel.run_event(State(), ev(1), manifest, gates)
        state.context["injected"] = "after the fact"
        state.history.append(ev(99))
        assert kernel.verify_chain() is True

    def test_empty_chain_verifies(self, kernel):
        assert kernel.verify_chain() is True


class TestStateAddressing:
    def test_accepted_state_is_addressable_and_clonable(self, kernel, manifest, gates):
        state, hash_, _ = kernel.run_event(State(), ev(7), manifest, gates)
        restored = kernel.clone(hash_)
        assert restored is not None
        assert restored.context["n"] == 7

    def test_clone_of_unknown_hash_is_none(self, kernel):
        assert kernel.clone("0" * 64) is None

    def test_invalid_state_has_no_address(self, kernel, gates):
        m = Manifest("M", "v1", [never], ["never"]).seal()
        assert kernel.dial(State(), m) is None

    def test_identical_states_share_an_address(self, kernel, manifest, gates):
        a, ha, _ = kernel.run_event(State(), ev(1, timestamp="2026-01-01T00:00:00"), manifest, gates)
        k2 = GovernanceKernel()
        b, hb, _ = k2.run_event(State(), ev(1, timestamp="2026-01-01T00:00:00"), manifest, gates)
        assert ha == hb


class TestMetrics:
    def test_metrics_count_each_outcome(self, kernel, manifest, gates):
        state = State()
        state, _, _ = kernel.run_event(state, ev(1), manifest, gates)
        kernel.run_event(state, Event("obs", {"x": "forbidden"}, "p1"), manifest, gates)
        m = kernel.metrics
        assert m.total_events == 2
        assert m.accepted == 1
        assert m.gate_rejections == 1
        assert m.acceptance_rate == 0.5

    def test_failing_gate_is_attributed_by_name(self, kernel, manifest, gates):
        kernel.run_event(State(), Event("obs", {"x": "forbidden"}, "p1"), manifest, gates)
        assert kernel.metrics.gate_fault_counts.get("fortress") == 1


class TestManifest:
    def test_mismatched_names_are_rejected(self):
        with pytest.raises(ValueError):
            Manifest("M", "v1", [always, never], ["only_one"])

    def test_sealed_manifest_detects_rule_substitution(self):
        m = Manifest("M", "v1", [always], ["always"]).seal()
        assert m.is_sealed()
        m.invariants = [never]
        assert not m.is_sealed()

    def test_unsealable_invariant_still_hashes(self):
        """An invariant with no retrievable source must not break the manifest."""
        ns = {}
        exec("def dynamic(state, event):\n    return True", ns)
        m = Manifest("M", "v1", [ns["dynamic"]], ["dynamic"])
        assert len(m.compute_hash()) == 64

    def test_expired_manifest_is_reported(self):
        m = Manifest("M", "v1", [always], ["always"], expiration_date="2000-01-01T00:00:00")
        assert m.is_expired()

    def test_expired_manifest_is_refused_by_the_gates(self, kernel, gates):
        m = Manifest("M", "v1", [always], ["always"], expiration_date="2000-01-01T00:00:00").seal()
        _, _, entry = kernel.run_event(State(), ev(1), m, gates)
        assert entry.decision is DecisionStatus.GATE_REJECTED
        assert "expired" in entry.reason
