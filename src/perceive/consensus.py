"""Consensus: unanimous locally, quorum across nodes.

Two distinct mechanisms in the corpus, easily conflated:

* **Unanimous gate consensus** within one kernel. Every gate is a veto, so
  adding a gate can only make the system more conservative. This is enforced
  in ``gates.evaluate_gates``.

* **DGK multi-node quorum** across kernels -- the Distributed Governance
  Kernel, described as being for "critical decisions" and "emergency
  overrides only". Several kernels evaluate the same event independently and
  a decision requires agreement from a quorum of them.

Quorum exists to tolerate a *faulty* node, not to overrule a correct one.
The asymmetry below is the whole design: allowing an event requires a quorum
of allows, while a single node's refusal is enough to deny. A quorum that
could vote away another node's veto would let a compromised majority approve
what an honest node refused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .gates import BaseGate, evaluate_gates
from .models import CheckOutcome, Event, Manifest, State


@dataclass
class NodeVote:
    node_id: str
    allowed: bool
    reason: str = ""
    fault: bool = False
    outcomes: List[CheckOutcome] = field(default_factory=list)


@dataclass
class ConsensusResult:
    allowed: bool
    votes: List[NodeVote]
    quorum_required: int
    reason: str = ""

    @property
    def allow_votes(self) -> int:
        return sum(1 for v in self.votes if v.allowed)

    @property
    def deny_votes(self) -> int:
        return sum(1 for v in self.votes if not v.allowed and not v.fault)

    @property
    def faulted_nodes(self) -> List[str]:
        return [v.node_id for v in self.votes if v.fault]

    def to_dict(self) -> Dict:
        return {
            "allowed": self.allowed,
            "quorum_required": self.quorum_required,
            "allow_votes": self.allow_votes,
            "deny_votes": self.deny_votes,
            "faulted_nodes": self.faulted_nodes,
            "reason": self.reason,
            "votes": [
                {"node_id": v.node_id, "allowed": v.allowed, "reason": v.reason, "fault": v.fault}
                for v in self.votes
            ],
        }


@dataclass
class GovernanceNode:
    """One independent evaluator: its own gates, and its own manifest.

    Nodes hold their own manifest so that a divergence between sites -- one
    hospital running a stale ruleset, say -- surfaces as disagreement rather
    than passing unnoticed.
    """

    node_id: str
    gates: Sequence[BaseGate]
    manifest: Manifest

    def vote(self, event: Event, state: State) -> NodeVote:
        try:
            allowed, outcomes = evaluate_gates(self.gates, event, state, self.manifest)
        except Exception as exc:
            return NodeVote(
                self.node_id, False, f"{type(exc).__name__}: {exc}", fault=True
            )
        failed = next((o for o in outcomes if not o.passed), None)
        return NodeVote(
            node_id=self.node_id,
            allowed=allowed,
            reason=f"{failed.name}: {failed.reason}" if failed else "",
            fault=any(o.fault for o in outcomes),
            outcomes=outcomes,
        )


def majority_quorum(node_count: int) -> int:
    """Strict majority. The default for a DGK cluster."""
    if node_count < 1:
        raise ValueError("a cluster needs at least one node")
    return node_count // 2 + 1


class DistributedGovernanceKernel:
    """A cluster of independent governance nodes."""

    def __init__(
        self, nodes: Sequence[GovernanceNode], quorum: Optional[int] = None
    ) -> None:
        if not nodes:
            raise ValueError("a cluster needs at least one node")
        self.nodes = list(nodes)
        self.quorum = quorum if quorum is not None else majority_quorum(len(self.nodes))
        if not 1 <= self.quorum <= len(self.nodes):
            raise ValueError(
                f"quorum {self.quorum} is outside 1..{len(self.nodes)}"
            )

    def _collect(self, event: Event, state: State) -> List[NodeVote]:
        """Poll every node, tolerating one that cannot answer at all.

        A node that is unreachable or crashes outright must register as a
        fault vote. Letting it propagate would mean one bad node takes down
        the cluster whose whole purpose is to survive one bad node.
        """
        votes: List[NodeVote] = []
        for node in self.nodes:
            try:
                votes.append(node.vote(event, state))
            except Exception as exc:
                votes.append(
                    NodeVote(
                        node.node_id,
                        False,
                        f"node unreachable: {type(exc).__name__}: {exc}",
                        fault=True,
                    )
                )
        return votes

    def decide(self, event: Event, state: State) -> ConsensusResult:
        """Collect every node's vote and apply the quorum rule."""
        votes = self._collect(event, state)

        # A node that refuses is honoured outright. Quorum tolerates faults;
        # it does not overrule a healthy node's veto.
        deniers = [v for v in votes if not v.allowed and not v.fault]
        if deniers:
            return ConsensusResult(
                allowed=False,
                votes=votes,
                quorum_required=self.quorum,
                reason=(
                    f"node {deniers[0].node_id} denied ({deniers[0].reason}); "
                    f"a node veto is not subject to quorum"
                ),
            )

        allows = sum(1 for v in votes if v.allowed)
        if allows < self.quorum:
            return ConsensusResult(
                allowed=False,
                votes=votes,
                quorum_required=self.quorum,
                reason=(
                    f"{allows} allow vote(s) below quorum {self.quorum}; "
                    f"faulted nodes: {[v.node_id for v in votes if v.fault]}"
                ),
            )

        return ConsensusResult(
            allowed=True,
            votes=votes,
            quorum_required=self.quorum,
            reason=f"{allows}/{len(votes)} nodes allowed, quorum {self.quorum} met",
        )

    def manifest_divergence(self) -> Dict[str, List[str]]:
        """Group node ids by manifest hash.

        More than one group means the cluster is running inconsistent policy,
        which is a governance incident whether or not any decision has failed
        yet.
        """
        groups: Dict[str, List[str]] = {}
        for node in self.nodes:
            groups.setdefault(node.manifest.hash or "<unsealed>", []).append(node.node_id)
        return groups
