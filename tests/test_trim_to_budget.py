"""Regression tests for BUG-024 — _trim_to_budget enforces ≤ budget.

Root bug: the previous `_trim_to_budget` did proportional list-length
reduction once against the CoreMemory token count, with no remeasure
and no escalation. In prod the bundle remained at its exact input
size post-trim (48535 stayed 48535 every scene).

Fix contract (task #6):
- Post-trim bundle must be ≤ MAX_CONTEXT_TOKENS, OR
- The method must raise `ContextBundleOverflowError` loud.
"""
from __future__ import annotations

import pytest

from tinyassets.exceptions import ContextBundleOverflowError
from tinyassets.memory.manager import (
    MAX_CONTEXT_TOKENS,
    MemoryManager,
    _estimate_bundle_tokens,
)


def _make_manager(canonical_source) -> MemoryManager:
    return MemoryManager(
        universe_id="trim-test",
        source=canonical_source("episodic", key=":memory:", namespaces=["trim-test"]),
    )


class TestTrimListsReducesBundle:
    def test_200_canon_facts_trimmed_below_budget(self, canonical_source):
        """Primary regression gate from the task spec.

        Bundle with 200 long canon_facts (each ~500 chars) exceeds the
        15k-token budget. After trim, bundle must be ≤ budget or raise.
        """
        mgr = _make_manager(canonical_source)
        try:
            bundle = {
                "canon_facts": [
                    {"text": f"fact-{i}: " + "x" * 480}
                    for i in range(200)
                ],
                "world_state": {"time": "dawn"},
                "phase": "evaluate",
            }
            tokens_before = _estimate_bundle_tokens(bundle)
            assert tokens_before > MAX_CONTEXT_TOKENS, (
                f"fixture should overflow; got {tokens_before}"
            )

            trimmed = mgr._trim_to_budget(bundle, tokens_before)
            tokens_after = _estimate_bundle_tokens(trimmed)

            assert tokens_after <= MAX_CONTEXT_TOKENS, (
                f"trim failed to enforce budget: {tokens_after} > "
                f"{MAX_CONTEXT_TOKENS}"
            )
        finally:
            mgr.close()

    def test_list_trim_shrinks_multiple_list_fields(self, canonical_source):
        mgr = _make_manager(canonical_source)
        try:
            bundle = {
                "facts": [{"text": "x" * 200} for _ in range(100)],
                "canon_facts": [{"text": "y" * 200} for _ in range(100)],
                "recent_summaries": [{"summary": "z" * 200} for _ in range(100)],
                "phase": "plan",
            }
            tokens_before = _estimate_bundle_tokens(bundle)
            assert tokens_before > MAX_CONTEXT_TOKENS

            trimmed = mgr._trim_to_budget(bundle, tokens_before)

            assert _estimate_bundle_tokens(trimmed) <= MAX_CONTEXT_TOKENS
            # All three list fields should have shrunk.
            assert len(trimmed["facts"]) < 100
            assert len(trimmed["canon_facts"]) < 100
            assert len(trimmed["recent_summaries"]) < 100
        finally:
            mgr.close()


class TestTrimCharacterDict:
    def test_aggressive_pass_strips_verbose_character_fields(self, canonical_source):
        mgr = _make_manager(canonical_source)
        try:
            bundle = {
                "active_characters": {
                    f"char-{i}": {
                        "id": f"char-{i}",
                        "name": f"Character {i}",
                        "role": "protagonist",
                        "goals": ["save the world"],
                        "status": "alive",
                        "bio": "x" * 5000,  # verbose — should get stripped
                        "backstory": "y" * 5000,  # not in keep set
                    }
                    for i in range(10)
                },
                "phase": "orient",
            }
            tokens_before = _estimate_bundle_tokens(bundle)
            assert tokens_before > MAX_CONTEXT_TOKENS

            trimmed = mgr._trim_to_budget(bundle, tokens_before)

            assert _estimate_bundle_tokens(trimmed) <= MAX_CONTEXT_TOKENS
            # Aggressive pass (attempt 2+) drops `goals` and `bio`/`backstory`.
            for cdata in trimmed["active_characters"].values():
                assert "bio" not in cdata
                assert "backstory" not in cdata
        finally:
            mgr.close()


class TestTrimRaisesOnIrreducibleOverflow:
    def test_raises_when_single_field_dominates_irreducibly(self, canonical_source):
        """World_state is highest priority (never trimmed); if it alone
        exceeds budget, the trim must raise rather than return over-budget.
        """
        mgr = _make_manager(canonical_source)
        try:
            # world_state dict with a huge irreducible string inside.
            # `_trim_character_dict` doesn't touch world_state;
            # `_truncate_string_bodies` only touches top-level str values,
            # so this dict with a large nested string is irreducible.
            bundle = {
                "world_state": {"terrain": "x" * (MAX_CONTEXT_TOKENS * 4 * 2)},
                "phase": "orient",
            }
            tokens_before = _estimate_bundle_tokens(bundle)

            with pytest.raises(ContextBundleOverflowError) as exc_info:
                mgr._trim_to_budget(bundle, tokens_before)

            msg = str(exc_info.value)
            assert "tokens after" in msg
            assert "TINYASSETS_DEBUG_CONTEXT" in msg
        finally:
            mgr.close()


class TestTrimUnderBudgetIsNoOp:
    def test_already_under_budget_returns_bundle_unchanged(self, canonical_source):
        mgr = _make_manager(canonical_source)
        try:
            bundle = {
                "facts": [{"text": "short"} for _ in range(5)],
                "canon_facts": [{"text": "short"} for _ in range(5)],
                "phase": "evaluate",
            }
            tokens_before = _estimate_bundle_tokens(bundle)
            assert tokens_before <= MAX_CONTEXT_TOKENS

            result = mgr._trim_to_budget(bundle, tokens_before)

            assert len(result["facts"]) == 5
            assert len(result["canon_facts"]) == 5
        finally:
            mgr.close()


class TestEstimateBundleTokens:
    def test_empty_bundle(self):
        assert _estimate_bundle_tokens({}) == 0

    def test_scalar_values(self):
        bundle = {"phase": "orient", "counter": 42}
        tokens = _estimate_bundle_tokens(bundle)
        assert tokens > 0
        assert tokens < 10

    def test_large_string_field_counted(self):
        bundle = {"draft": "x" * 4000}
        assert _estimate_bundle_tokens(bundle) >= 1000
