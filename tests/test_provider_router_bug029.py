"""Tests for BUG-029: thundering-herd chain-drain detection + backoff.

Part A: QuotaTracker.all_api_providers_in_cooldown + get_status exposure.
Part B (router chain-drain backoff onto the host's local model) was deleted
under Hard Rule 15: there is no platform chain to drain and ``ollama-local``
never serves a universe.
"""
from __future__ import annotations

from tinyassets.providers.quota import QuotaTracker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Part A — QuotaTracker.all_api_providers_in_cooldown
# ---------------------------------------------------------------------------


class TestAllApiProvidersInCooldown:





    def test_cooldown_remaining_dict_includes_all_providers(self):
        qt = QuotaTracker()
        qt.cooldown("claude-code", 60)
        result = qt.cooldown_remaining_dict(["claude-code", "codex"])
        assert "claude-code" in result
        assert result["claude-code"] > 0
        assert result["codex"] == 0


# ---------------------------------------------------------------------------
# Part B — ProviderRouter raises AllProvidersExhaustedError on chain-drain
# ---------------------------------------------------------------------------

