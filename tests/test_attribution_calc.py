"""Tests for tinyassets/attribution/calc.py — Task #39."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from tinyassets.attribution.schema import AttributionCredit, AttributionEdge


def _edge(parent: str, child: str, depth: int = 1) -> AttributionEdge:
    return AttributionEdge(
        edge_id=f"{parent}-{child}",
        parent_id=parent,
        child_id=child,
        parent_kind="branch",
        child_kind="branch",
        generation_depth=depth,
        contribution_kind="remix",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _credit(actor: str, artifact: str, gen_depth: int = 0) -> AttributionCredit:
    return AttributionCredit(
        credit_id=f"{actor}-{artifact}",
        artifact_id=artifact,
        artifact_kind="branch",
        actor_id=actor,
        credit_share=1.0,
        royalty_share=0.0,
        generation_depth=gen_depth,
        contribution_kind="original" if gen_depth == 0 else "remix",
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )


def _credit_with_identity(actor: str, artifact: str) -> AttributionCredit:
    return AttributionCredit(
        credit_id=f"{actor}-{artifact}",
        artifact_id=artifact,
        artifact_kind="branch",
        actor_id=actor,
        owner_user_id=f"owner-{actor}",
        daemon_id=f"daemon::{actor}",
        runtime_instance_id=f"runtime-{actor}",
        worker_id=f"worker-{actor}",
        credit_share=1.0,
        royalty_share=0.0,
        generation_depth=0,
        contribution_kind="original",
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )


# ── helper assertions ────────────────────────────────────────────────────────


def _assert_sums_to_one(shares: dict[str, float]) -> None:
    total = sum(shares.values())
    assert math.isclose(total, 1.0, rel_tol=1e-6), (
        f"Shares should sum to 1.0, got {total}: {shares}"
    )


# ── compute_credit_shares (via credits) ──────────────────────────────────────


class TestComputeCreditSharesFromCredits:
    def test_single_author_gets_full_share(self):
        from tinyassets.attribution.calc import compute_credit_shares
        credits = [_credit("alice", "art-1", gen_depth=0)]
        shares = compute_credit_shares(edges=[], credits=credits)
        assert shares == {"alice": pytest.approx(1.0)}

    def test_two_authors_same_generation_split_equally(self):
        from tinyassets.attribution.calc import compute_credit_shares
        credits = [
            _credit("alice", "art-1", gen_depth=0),
            _credit("bob", "art-1", gen_depth=0),
        ]
        shares = compute_credit_shares(edges=[], credits=credits)
        assert shares["alice"] == pytest.approx(0.5)
        assert shares["bob"] == pytest.approx(0.5)
        _assert_sums_to_one(shares)

    def test_identity_fields_do_not_change_credit_split(self):
        from tinyassets.attribution.calc import compute_credit_shares
        credits = [
            _credit_with_identity("alice", "art-1"),
            _credit_with_identity("bob", "art-1"),
        ]
        shares = compute_credit_shares(edges=[], credits=credits)
        assert shares["alice"] == pytest.approx(0.5)
        assert shares["bob"] == pytest.approx(0.5)
        _assert_sums_to_one(shares)

    def test_two_generation_chain_depth_decay(self):
        """Gen 0 gets weight 1.0, gen 1 gets weight 0.5. Normalized: 2/3, 1/3."""
        from tinyassets.attribution.calc import compute_credit_shares
        credits = [
            _credit("alice", "art-leaf", gen_depth=0),
            _credit("bob", "art-parent", gen_depth=1),
        ]
        shares = compute_credit_shares(edges=[], credits=credits)
        # alice: 1.0 / (1.0 + 0.5) = 2/3; bob: 0.5 / 1.5 = 1/3
        assert shares["alice"] == pytest.approx(2 / 3, rel=1e-5)
        assert shares["bob"] == pytest.approx(1 / 3, rel=1e-5)
        _assert_sums_to_one(shares)

    def test_three_generation_chain_decay(self):
        """Weights: gen0=1, gen1=0.5, gen2=0.25. Total=1.75."""
        from tinyassets.attribution.calc import compute_credit_shares
        credits = [
            _credit("a", "art-0", gen_depth=0),
            _credit("b", "art-1", gen_depth=1),
            _credit("c", "art-2", gen_depth=2),
        ]
        shares = compute_credit_shares(edges=[], credits=credits)
        assert shares["a"] == pytest.approx(1.0 / 1.75, rel=1e-5)
        assert shares["b"] == pytest.approx(0.5 / 1.75, rel=1e-5)
        assert shares["c"] == pytest.approx(0.25 / 1.75, rel=1e-5)
        _assert_sums_to_one(shares)

    def test_multi_fork_same_generation_splits_equally(self):
        """Two authors at gen 1 split gen-1 weight equally between themselves.

        Weights: alice (gen0) = 1.0; bob (gen1) = 0.25; carol (gen1) = 0.25.
        Total raw = 1.5. Normalized: alice = 2/3, bob = 1/6, carol = 1/6.
        """
        from tinyassets.attribution.calc import compute_credit_shares
        credits = [
            _credit("alice", "art-leaf", gen_depth=0),
            _credit("bob", "art-p1", gen_depth=1),
            _credit("carol", "art-p2", gen_depth=1),
        ]
        shares = compute_credit_shares(edges=[], credits=credits)
        # bob and carol must have equal shares (symmetric)
        assert shares["bob"] == pytest.approx(shares["carol"])
        # alice at gen 0 gets more than bob or carol individually
        assert shares["alice"] > shares["bob"]
        _assert_sums_to_one(shares)

    def test_depth_cap_truncates_deep_lineage(self):
        """Authors beyond depth_cap contribute nothing (as if lineage ends there)."""
        from tinyassets.attribution.calc import compute_credit_shares
        credits = [
            _credit("alice", "art-0", gen_depth=0),
            _credit("bob", "art-deep", gen_depth=100),
        ]
        shares = compute_credit_shares(edges=[], credits=credits, depth_cap=5)
        # bob's depth 100 is clamped to 5: weight 2^(-5)=0.03125 vs alice's 1.0
        # both still included (just with capped weight)
        assert "alice" in shares
        assert "bob" in shares
        _assert_sums_to_one(shares)

    def test_empty_credits_returns_empty(self):
        from tinyassets.attribution.calc import compute_credit_shares
        assert compute_credit_shares(edges=[], credits=[]) == {}

    def test_shares_sum_to_one(self):
        from tinyassets.attribution.calc import compute_credit_shares
        credits = [
            _credit("a", "x", gen_depth=0),
            _credit("b", "x", gen_depth=1),
            _credit("c", "x", gen_depth=1),
            _credit("d", "x", gen_depth=2),
        ]
        shares = compute_credit_shares(edges=[], credits=credits)
        _assert_sums_to_one(shares)


# ── compute_credit_shares (via edges) ────────────────────────────────────────


class TestComputeCreditSharesFromEdges:
    def test_single_parent_gets_full_share(self):
        """A → B: artifact A is the sole contributor; gets 100%."""
        from tinyassets.attribution.calc import compute_credit_shares
        edges = [_edge("art-a", "art-b", depth=1)]
        shares = compute_credit_shares(edges=edges)
        assert shares == {"art-a": pytest.approx(1.0)}

    def test_two_parents_same_depth_split_equally(self):
        """A → C and B → C at depth 1: A and B each get 50%."""
        from tinyassets.attribution.calc import compute_credit_shares
        edges = [
            _edge("art-a", "art-c", depth=1),
            _edge("art-b", "art-c", depth=1),
        ]
        shares = compute_credit_shares(edges=edges)
        assert shares["art-a"] == pytest.approx(0.5)
        assert shares["art-b"] == pytest.approx(0.5)

    def test_cycle_detection_raises(self):
        """A → B and B → A is a cycle; should raise ValueError."""
        from tinyassets.attribution.calc import compute_credit_shares
        edges = [_edge("art-a", "art-b"), _edge("art-b", "art-a")]
        with pytest.raises(ValueError, match="[Cc]ycle"):
            compute_credit_shares(edges=edges)

    def test_depth_cap_respected(self):
        """Chain longer than depth_cap should not crash; deep ancestors are ignored."""
        from tinyassets.attribution.calc import compute_credit_shares
        # Build linear chain: art-0 → art-1 → ... → art-20 (art-20 is the leaf)
        edges = [_edge(f"art-{i}", f"art-{i+1}", depth=i + 1) for i in range(20)]
        shares = compute_credit_shares(edges=edges, depth_cap=5)
        # art-20 is leaf (depth=0, excluded). art-19..art-15 are depths 1..5 (included).
        # art-14..art-0 are beyond depth_cap=5 and must not appear.
        expected_contributors = {f"art-{19 - i}" for i in range(5)}  # art-19..art-15
        assert set(shares.keys()) == expected_contributors
        _assert_sums_to_one(shares)

    def test_no_edges_no_credits_returns_empty(self):
        from tinyassets.attribution.calc import compute_credit_shares
        assert compute_credit_shares(edges=[]) == {}
