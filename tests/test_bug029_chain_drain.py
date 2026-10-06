"""Tests for BUG-029 Part A — chain-drain detection + observability.

Covers:
- QuotaTracker.all_api_providers_in_cooldown(): True when all API providers
  cooled, False when at least one API provider is available.
- QuotaTracker.cooldown_remaining_dict(): returns per-provider remaining seconds.
- get_status exposes per_provider_cooldown_remaining as a dict.
- CHAIN_DRAINED warning is emitted when chain drains to local-only.
"""

from __future__ import annotations

import json

import pytest


class TestAllApiProvidersInCooldown:
    def _tracker(self):
        from tinyassets.providers.quota import QuotaTracker
        return QuotaTracker()

    def test_returns_false_when_no_providers_cooled(self):
        qt = self._tracker()
        chain = ["claude-code", "codex", "ollama-local"]
        assert qt.all_api_providers_in_cooldown(chain) is False

    def test_returns_true_when_all_api_providers_cooled(self):
        qt = self._tracker()
        qt.cooldown("claude-code", 120)
        qt.cooldown("codex", 120)
        chain = ["claude-code", "codex", "ollama-local"]
        assert qt.all_api_providers_in_cooldown(chain) is True

    def test_returns_false_when_one_api_provider_available(self):
        qt = self._tracker()
        qt.cooldown("claude-code", 120)
        chain = ["claude-code", "codex", "ollama-local"]
        assert qt.all_api_providers_in_cooldown(chain) is False

    def test_returns_false_when_chain_is_only_local(self):
        qt = self._tracker()
        chain = ["ollama-local"]
        assert qt.all_api_providers_in_cooldown(chain) is False

    def test_custom_local_providers_set(self):
        qt = self._tracker()
        qt.cooldown("claude-code", 120)
        qt.cooldown("codex", 120)
        chain = ["claude-code", "codex", "my-local"]
        # When my-local is declared local, all API providers (claude-code, codex) are cooled.
        assert qt.all_api_providers_in_cooldown(chain, local_providers={"my-local"}) is True

    def test_returns_false_when_chain_is_empty(self):
        qt = self._tracker()
        assert qt.all_api_providers_in_cooldown([]) is False


class TestCooldownRemainingDict:
    def _tracker(self):
        from tinyassets.providers.quota import QuotaTracker
        return QuotaTracker()

    def test_returns_zero_for_uncooled_providers(self):
        qt = self._tracker()
        result = qt.cooldown_remaining_dict(["claude-code", "ollama-local"])
        assert result["claude-code"] == 0
        assert result["ollama-local"] == 0

    def test_returns_positive_for_cooled_providers(self):
        qt = self._tracker()
        qt.cooldown("claude-code", 120)
        result = qt.cooldown_remaining_dict(["claude-code", "codex"])
        assert result["claude-code"] > 0
        assert result["codex"] == 0

    def test_all_providers_covered(self):
        qt = self._tracker()
        providers = ["claude-code", "codex", "gemini-free", "groq-free"]
        result = qt.cooldown_remaining_dict(providers)
        assert set(result.keys()) == set(providers)


class TestGetStatusCooldownField:
    """These read the FULL status shape, which needs a universe to report on.

    They used to get it by being nobody: an unauthenticated caller fell through
    to the single-tenant default universe. A signed-in founder with no home
    gets the short "no universe bound to this account yet" shape instead, which
    is correct and carries no provider state -- so the tests bind a home.
    """

    def test_get_status_has_per_provider_cooldown_remaining(self, founder_home):
        """get_status must include per_provider_cooldown_remaining dict."""
        from tinyassets.universe_server import get_status
        payload = json.loads(get_status())
        assert "per_provider_cooldown_remaining" in payload, payload
        assert isinstance(payload["per_provider_cooldown_remaining"], dict)

    def test_per_provider_cooldown_remaining_values_are_ints(self, founder_home):
        """All values in per_provider_cooldown_remaining must be non-negative ints."""
        from tinyassets.universe_server import get_status
        remaining = json.loads(get_status())["per_provider_cooldown_remaining"]
        for provider, seconds in remaining.items():
            assert isinstance(seconds, int), f"{provider}: expected int, got {type(seconds)}"
            assert seconds >= 0, f"{provider}: negative cooldown seconds"

    def test_schema_contract_includes_per_provider_field(self, founder_home):
        """Contract test: schema_version=1 contract must include the new field."""
        from tinyassets.universe_server import get_status
        payload = json.loads(get_status())
        assert "per_provider_cooldown_remaining" in payload, (
            "per_provider_cooldown_remaining not in get_status schema_version=1 response"
        )

    @pytest.mark.parametrize("own_cooldown", [False, True])
    def test_status_reads_only_the_callers_cooldown(self, founder_home, monkeypatch, own_cooldown):
        from tinyassets import graph_compiler
        from tinyassets.api import permissions
        from tinyassets.providers.router import ProviderRouter
        from tinyassets.universe_server import get_status

        router = ProviderRouter()
        owner = permissions.current_actor_id()
        router.cool_source("claude-code", owner="another-owner", retry_after_s=600)
        if own_cooldown:
            router.cool_source("claude-code", owner=owner, retry_after_s=120)
        monkeypatch.setattr(graph_compiler, "_get_shared_router", lambda: router)
        remaining = json.loads(get_status())["per_provider_cooldown_remaining"]
        if own_cooldown:
            assert 0 < remaining["claude-code"] <= 121
        else:
            assert remaining["claude-code"] == 0
        assert remaining["codex"] == 0


# ---- BUG-029 Part B — failure-taxonomy completeness matrix ---------------
#
# The chatbot UX surface depends on (failure_class, actionable_by,
# suggested_action) being uniformly populated for every recognised
# failure mode. A latent gap — a failure_class with empty actionable_by
# or empty suggested_action — would render as "Run failed" with no
# recovery hint, defeating the BUG-029 fix.
#
# These tests lock in the taxonomy contract:
#   1. Every key in `ACTIONABLE_BY` resolves to one of {host, chatbot,
#      user, none} via the canonical `_actionable_by` helper.
#   2. Every classifier branch in `_classify_run_outcome_error` returns
#      a non-empty `suggested_action` AND that the returned class is in
#      `ACTIONABLE_BY` (so chatbot can always look up `actionable_by`).
#
# Source of truth: `tinyassets.runs.ACTIONABLE_BY` (canonical map).
# Helpers under test live at `tinyassets.api.runs`.


_VALID_ACTIONABLE_BY = frozenset({"host", "chatbot", "user", "none"})


@pytest.fixture(scope="module")
def actionable_by_table() -> dict[str, str]:
    from tinyassets.runs import ACTIONABLE_BY
    return ACTIONABLE_BY


@pytest.fixture(scope="module")
def actionable_by_helper():
    from tinyassets.api.runs import _actionable_by
    return _actionable_by


class TestActionableByCompleteness:
    """Every failure_class in the canonical map must resolve to a known bucket."""

    def test_every_class_resolves_to_known_bucket(
        self, actionable_by_table, actionable_by_helper,
    ):
        for failure_class, expected_bucket in actionable_by_table.items():
            actual = actionable_by_helper(failure_class)
            assert actual == expected_bucket, (
                f"{failure_class}: helper returned {actual!r}, "
                f"map declares {expected_bucket!r}"
            )
            assert actual in _VALID_ACTIONABLE_BY, (
                f"{failure_class}: bucket {actual!r} is not one of "
                f"{sorted(_VALID_ACTIONABLE_BY)}"
            )

    def test_unknown_class_falls_back_to_user(self, actionable_by_helper):
        """Conservative-default: unmapped classes escalate to 'user', never silently drop."""
        assert actionable_by_helper("definitely_not_a_real_class") == "user"
        assert actionable_by_helper("") == "user"

    def test_taxonomy_covers_all_four_buckets(self, actionable_by_table):
        """Sanity-check: the canonical map exercises every bucket. If a future
        edit collapses the taxonomy to 3 buckets, this catches the regression."""
        buckets = set(actionable_by_table.values())
        assert buckets == _VALID_ACTIONABLE_BY, (
            f"ACTIONABLE_BY no longer covers all 4 buckets — got {sorted(buckets)}"
        )


# Strings keyed by the failure_class each classifier branch should emit.
# One representative string per regex/branch in `_classify_run_outcome_error`.
# When a new branch is added, this table MUST grow to match — otherwise the
# new branch will silently emit an unmapped failure_class.
_OUTCOME_ERROR_FIXTURES: tuple[tuple[str, str], ...] = (
    ("Empty LLM response from provider", "empty_llm_response"),
    ("Run timed out after 60s", "timeout"),
    ("Quota exhausted on Anthropic API", "quota_exhausted"),
    ("Provider overloaded — 503 service unavailable", "provider_overloaded"),
    ("Maximum context length exceeded for model", "context_length_exceeded"),
    ("Auth expired: token refresh required", "permission_denied:auth_expired"),
    ("Approval required for source_code node", "node_not_approved"),
    ("Permission denied", "permission_denied:approval_required"),
    ("Subprocess failure: exit code 127", "provider_subprocess_failed"),
    ("Concurrent modification conflict", "state_mutation_conflict"),
    ("Provider unavailable: no API key", "provider_unavailable"),
    ("Groq call failed: HTTP 502", "provider_error"),
    ("external write failed - "
     "open_pr/authenticated_external_call: far side answered HTTP 422: {} [far_side_error]",
     "external_write_failed"),
    ("external write failed - post/authenticated_external_call: "
     "refused before the wire: missing_consent [missing_consent]",
     "external_write_refused"),
    # sandboxed-code-node (2026-08-30): a code node that raised, and a public
    # foreign branch's code run directly.
    ("code node 'edit' failed: KeyError: 'content' | stderr: Traceback ...",
     "code_node_failed"),
    ("Node 'edit' carries source_code this run did not author (caller provenance: "
     "public-foreign). Code runs only in the command center that authored it: remix the "
     "branch into your universe with write_graph (fork_from) so the code is yours, "
     "then run your copy.",
     "node_not_accepted"),
    # run-usage-budgets: the shape caps are gone; usage bounds a runaway graph.
    ("external write failed - fetch_9/authenticated_external_call: run budget exhausted: "
     "500 effect dispatches in this run (budget 500); split the work across runs "
     "[effect_budget_exhausted]",
     "effect_budget_exhausted"),
)


@pytest.fixture(scope="module")
def classify_outcome():
    from tinyassets.api.runs import _classify_run_outcome_error
    return _classify_run_outcome_error


@pytest.mark.parametrize("error_string,expected_class", _OUTCOME_ERROR_FIXTURES)
class TestClassifyOutcomeErrorCompleteness:
    """For every classifier branch, the (failure_class, suggested_action) pair
    must be non-empty AND the failure_class must be in ACTIONABLE_BY (so the
    chatbot can always look up `actionable_by` for the same class)."""

    def test_classifier_returns_expected_class(
        self, classify_outcome, error_string, expected_class,
    ):
        result = classify_outcome(error_string)
        assert result is not None, (
            f"{error_string!r} → None (expected ({expected_class!r}, ...))"
        )
        actual_class, _ = result
        assert actual_class == expected_class, (
            f"{error_string!r} → {actual_class!r} (expected {expected_class!r})"
        )

    def test_classifier_returns_non_empty_suggested_action(
        self, classify_outcome, error_string, expected_class,
    ):
        result = classify_outcome(error_string)
        _, suggested_action = result
        assert suggested_action, (
            f"{error_string!r} ({expected_class!r}): empty suggested_action"
        )
        assert len(suggested_action) >= 20, (
            f"{error_string!r} ({expected_class!r}): suggested_action too "
            f"terse to be actionable: {suggested_action!r}"
        )

    def test_classified_class_resolves_to_known_actionable_by(
        self, classify_outcome, actionable_by_table, error_string, expected_class,
    ):
        """The class emitted by the classifier MUST appear in ACTIONABLE_BY.
        Otherwise the chatbot's `_actionable_by(failure_class)` lookup
        falls back to 'user' (safe default), masking the real recovery
        path the host/chatbot could have taken."""
        result = classify_outcome(error_string)
        actual_class, _ = result
        assert actual_class in actionable_by_table, (
            f"{error_string!r} → {actual_class!r}, but {actual_class!r} is "
            f"NOT in tinyassets.runs.ACTIONABLE_BY. Add it to the canonical "
            f"map so chatbot can render a non-default actionable_by."
        )
