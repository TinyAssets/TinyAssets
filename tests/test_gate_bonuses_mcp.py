"""Tests for gate bonus MCP actions: stake_bonus, unstake_bonus, release_bonus.

Spec: docs/vetted-specs.md §Gate bonuses — staked payouts attached to gate milestones.
"""

from __future__ import annotations

# ── helpers ───────────────────────────────────────────────────────────────────


# ── stake_bonus ───────────────────────────────────────────────────────────────


# ── unstake_bonus ─────────────────────────────────────────────────────────────


# ── release_bonus ─────────────────────────────────────────────────────────────


# ── Pure business logic unit tests ────────────────────────────────────────────

class TestGatesActionsUnit:
    """Unit tests for tinyassets/gates/actions.py business logic (no MCP layer)."""

    def test_validate_stake_amount_valid(self):
        from tinyassets.gates.actions import validate_stake_amount
        stake, err = validate_stake_amount(500)
        assert stake == 500
        assert err is None

    def test_validate_stake_amount_zero(self):
        from tinyassets.gates.actions import validate_stake_amount
        stake, err = validate_stake_amount(0)
        assert stake == 0
        assert err is None

    def test_validate_stake_amount_negative_rejected(self):
        from tinyassets.gates.actions import validate_stake_amount
        stake, err = validate_stake_amount(-1)
        assert err is not None

    def test_validate_stake_amount_non_numeric_rejected(self):
        from tinyassets.gates.actions import validate_stake_amount
        _, err = validate_stake_amount("abc")
        assert err is not None

    def test_compute_bonus_payout_invariant(self):
        from tinyassets.gates.actions import compute_bonus_payout
        for stake in range(0, 10_001, 100):
            net, treasury = compute_bonus_payout(stake)
            assert net + treasury == stake, f"invariant broken at stake={stake}"

    def test_compute_bonus_payout_1pct_take(self):
        from tinyassets.gates.actions import compute_bonus_payout
        net, treasury = compute_bonus_payout(10_000)
        assert treasury == 100
        assert net == 9_900
