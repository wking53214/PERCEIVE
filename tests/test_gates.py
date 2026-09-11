"""The six gates, unanimous consensus, and DGK quorum."""

import pytest

from perceive.consensus import (
    DistributedGovernanceKernel,
    GovernanceNode,
    majority_quorum,
)
from perceive.gates import (
    CANONICAL_ORDER,
    BoundaryGate,
    CitadelGate,
    FortressGate,
    InvariantValidatorGate,
    MicroPatchGate,
    SentinelGate,
    default_gates,
    evaluate_gates,
)
from perceive.models import Event, Manifest, State


@pytest.fixture
def manifest():
    return Manifest("M", "v1", [], []).seal()


def ev(**delta):
    return Event("observation", delta or {"n": 1}, "p1")


class TestGateRegistry:
    def test_six_gates_in_canonical_order(self):
        gates = default_gates()
        assert len(gates) == 6
        assert tuple(g.name for g in gates) == CANONICAL_ORDER

    def test_clean_event_passes_every_gate(self, manifest):
        allowed, outcomes = evaluate_gates(default_gates(), ev(), State(), manifest)
        assert allowed
        assert len(outcomes) == 6
        assert all(o.passed for o in outcomes)


class TestBoundary:
    def test_empty_event_type_rejected(self, manifest):
        g = BoundaryGate()
        assert not g.check(Event("", {"n": 1}, "p1"), State(), manifest)[0]

    def test_empty_context_id_rejected(self, manifest):
        g = BoundaryGate()
        assert not g.check(Event("obs", {"n": 1}, ""), State(), manifest)[0]

    def test_oversize_request_rejected(self, manifest):
        ok, reason = BoundaryGate().check(ev(blob="x" * 600), State(), manifest)
        assert not ok and "limit" in reason

    @pytest.mark.parametrize("word", ["harm", "injure", "damage", "destroy"])
    def test_harm_markers_rejected(self, manifest, word):
        assert not BoundaryGate().check(ev(note=word), State(), manifest)[0]


class TestFortressAndCitadel:
    @pytest.mark.parametrize("word", ["forbidden", "prohibited", "restricted", "classified"])
    def test_fortress_rejects_forbidden_content(self, manifest, word):
        assert not FortressGate().check(ev(note=word), State(), manifest)[0]

    @pytest.mark.parametrize("word", ["unsafe", "dangerous", "hazardous", "toxic"])
    def test_citadel_rejects_unsafe_content(self, manifest, word):
        assert not CitadelGate().check(ev(note=word), State(), manifest)[0]

    def test_high_risk_manifest_requires_a_declared_authority(self):
        high = Manifest("M", "v1", [], [], risk_level="HIGH").seal()
        gate = CitadelGate()
        assert not gate.check(ev(), State(), high)[0]
        authorised = Event("obs", {"n": 1}, "p1", metadata={"authority": "attending"})
        assert gate.check(authorised, State(), high)[0]


class TestSentinel:
    @pytest.mark.parametrize("word", ["drift", "deviation", "divergence", "anomaly"])
    def test_drift_markers_rejected(self, manifest, word):
        assert not SentinelGate().check(ev(note=word), State(), manifest)[0]

    def test_backwards_timestamp_rejected(self, manifest):
        state = State()
        state.history.append(Event("obs", {}, "p1", timestamp="2026-06-01T12:00:00"))
        late = Event("obs", {"n": 1}, "p1", timestamp="2026-05-01T12:00:00")
        assert not SentinelGate().check(late, state, manifest)[0]

    def test_unbounded_history_rejected(self, manifest):
        state = State()
        state.history = [Event("obs", {}, "p1")] * 10
        assert not SentinelGate(max_history=10).check(ev(), state, manifest)[0]


class TestInvariantValidator:
    def test_unsealed_after_edit_is_rejected(self):
        m = Manifest("M", "v1", [lambda s, e: True], ["a"]).seal()
        m.invariant_names = ["renamed"]
        ok, reason = InvariantValidatorGate().check(ev(), State(), m)
        assert not ok and "changed after sealing" in reason


class TestMicroPatchGate:
    def test_too_many_active_patches_is_a_rejection(self, manifest):
        g = MicroPatchGate(max_active=2)
        for i in range(5):
            g.add_rule(f"p{i}", lambda e, s, m: True)
        ok, reason = g.check(ev(), State(), manifest)
        assert not ok and "become policy" in reason


class TestUnanimity:
    def test_one_veto_denies_the_whole_chain(self, manifest):
        class Denier:
            name = "denier"

            def check(self, event, state, manifest):
                return False, "always denies"

        allowed, _ = evaluate_gates(default_gates() + [Denier()], ev(), State(), manifest)
        assert not allowed

    def test_adding_a_permissive_gate_cannot_unlock_a_denial(self, manifest):
        class Permissive:
            name = "permissive"

            def check(self, event, state, manifest):
                return True, ""

        base, _ = evaluate_gates(default_gates(), ev(note="forbidden"), State(), manifest)
        widened, _ = evaluate_gates(
            default_gates() + [Permissive()], ev(note="forbidden"), State(), manifest
        )
        assert base is False and widened is False

    def test_short_circuit_stops_at_the_first_denial(self, manifest):
        allowed, outcomes = evaluate_gates(
            default_gates(), ev(note="forbidden"), State(), manifest, short_circuit=True
        )
        assert not allowed
        assert outcomes[-1].name == "fortress"

    def test_full_evaluation_collects_every_opinion(self, manifest):
        _, outcomes = evaluate_gates(
            default_gates(), ev(note="forbidden"), State(), manifest, short_circuit=False
        )
        assert len(outcomes) == 6


class TestDistributedConsensus:
    def make_cluster(self, n, manifest, quorum=None):
        nodes = [GovernanceNode(f"n{i}", default_gates(), manifest) for i in range(n)]
        return DistributedGovernanceKernel(nodes, quorum=quorum)

    def test_majority_quorum_sizes(self):
        assert majority_quorum(1) == 1
        assert majority_quorum(3) == 2
        assert majority_quorum(5) == 3
        assert majority_quorum(4) == 3

    def test_unanimous_allow_reaches_quorum(self, manifest):
        result = self.make_cluster(3, manifest).decide(ev(), State())
        assert result.allowed
        assert result.allow_votes == 3

    def test_a_single_node_veto_denies_regardless_of_quorum(self, manifest):
        """Quorum tolerates faults; it must not overrule a healthy veto."""

        class Strict(GovernanceNode):
            def vote(self, event, state):
                from perceive.consensus import NodeVote

                return NodeVote(self.node_id, False, "site policy forbids this")

        nodes = [
            GovernanceNode("n0", default_gates(), manifest),
            GovernanceNode("n1", default_gates(), manifest),
            Strict("n2", default_gates(), manifest),
        ]
        result = DistributedGovernanceKernel(nodes).decide(ev(), State())
        assert not result.allowed
        assert "not subject to quorum" in result.reason

    def test_faulted_nodes_below_quorum_deny(self, manifest):
        class Broken(GovernanceNode):
            def vote(self, event, state):
                raise RuntimeError("node down")

        nodes = [
            GovernanceNode("n0", default_gates(), manifest),
            Broken("n1", default_gates(), manifest),
            Broken("n2", default_gates(), manifest),
        ]
        cluster = DistributedGovernanceKernel(nodes)
        result = cluster.decide(ev(), State())
        assert not result.allowed
        assert set(result.faulted_nodes) == {"n1", "n2"}

    def test_empty_cluster_rejected(self):
        with pytest.raises(ValueError):
            DistributedGovernanceKernel([])

    def test_out_of_range_quorum_rejected(self, manifest):
        with pytest.raises(ValueError):
            self.make_cluster(3, manifest, quorum=9)

    def test_manifest_divergence_is_visible(self, manifest):
        other = Manifest("M", "v2", [], []).seal()
        nodes = [
            GovernanceNode("n0", default_gates(), manifest),
            GovernanceNode("n1", default_gates(), other),
        ]
        groups = DistributedGovernanceKernel(nodes).manifest_divergence()
        assert len(groups) == 2, "two rulesets in one cluster must be visible"
