"""Memory-scope Stage 2b.3 — Stage-1 assertion extension + flag + ACL fixture.

Exec plan: ``2026-04-16-memory-scope-stage-2b.md (archived in git)``
§3 (assertion extension), §4 (flag gating), §5 (private-universe fixture).

Stage 2b.3 ships three guarantees tests here pin down:

1. **``assert_scope_match`` checks all 4 tiers** — universe_id is
   always enforced; the three sub-tiers are enforced only when
   ``TINYASSETS_TIERED_SCOPE=on``.
2. **Flag is a read-side no-op when off.** A caller pinned to a
   branch, receiving rows from another branch, sees no drops with
   the flag off (2b.2-era behavior preserved). With the flag on,
   mismatched rows drop.
3. **Private-universe Layer 1 ACL fixture.** The ACL functions
   (``universe_is_private``, ``universe_access_permission``) work in
   isolation — this is the "≥1 private-universe fixture" Stage 2c
   criterion.
"""

from __future__ import annotations

import pytest

from tinyassets.daemon_server import (
    grant_universe_access,
    initialize_author_server,
    list_universe_acl,
    revoke_universe_access,
    universe_access_permission,
    universe_is_private,
)
from tinyassets.knowledge.models import RetrievalResult
from tinyassets.memory.scoping import MemoryScope
from tinyassets.retrieval.router import (
    _drop_cross_universe_rows,
    _row_universe_id,
    assert_scope_match,
    tiered_scope_enabled,
)

# ─── Flag reader ─────────────────────────────────────────────────────────


class TestTieredScopeFlag:
    def test_flag_default_off(self, monkeypatch):
        monkeypatch.delenv("TINYASSETS_TIERED_SCOPE", raising=False)
        assert tiered_scope_enabled() is False

    def test_flag_explicit_off(self, monkeypatch):
        monkeypatch.setenv("TINYASSETS_TIERED_SCOPE", "off")
        assert tiered_scope_enabled() is False

    def test_flag_on(self, monkeypatch):
        monkeypatch.setenv("TINYASSETS_TIERED_SCOPE", "on")
        assert tiered_scope_enabled() is True

    @pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE", "Yes"])
    def test_flag_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv("TINYASSETS_TIERED_SCOPE", value)
        assert tiered_scope_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "", "maybe"])
    def test_flag_falsy_values(self, monkeypatch, value):
        monkeypatch.setenv("TINYASSETS_TIERED_SCOPE", value)
        assert tiered_scope_enabled() is False


# ─── assert_scope_match ──────────────────────────────────────────────────


class TestAssertScopeMatch:
    """The four-tier assertion. universe_id always enforced; sub-tiers
    flag-gated."""

    def test_universe_mismatch_always_drops(self, monkeypatch):
        monkeypatch.delenv("TINYASSETS_TIERED_SCOPE", raising=False)
        caller = MemoryScope(universe_id="world-a")
        row = {"universe_id": "world-b"}
        assert assert_scope_match(row, caller) is False

    def test_universe_match_passes(self, monkeypatch):
        monkeypatch.delenv("TINYASSETS_TIERED_SCOPE", raising=False)
        caller = MemoryScope(universe_id="world")
        row = {"universe_id": "world"}
        assert assert_scope_match(row, caller) is True

    def test_missing_universe_passes(self, monkeypatch):
        # Legacy rows with no universe_id attribute pass through —
        # KG/vector in Stage 1 was path-tagged, not row-tagged.
        monkeypatch.delenv("TINYASSETS_TIERED_SCOPE", raising=False)
        caller = MemoryScope(universe_id="world")
        assert assert_scope_match({}, caller) is True

    def test_branch_mismatch_passes_with_flag_off(self, monkeypatch):
        """2b.2-era behavior: sub-tier mismatch does NOT drop with flag off."""
        monkeypatch.delenv("TINYASSETS_TIERED_SCOPE", raising=False)
        caller = MemoryScope(universe_id="world", branch_id="main")
        row = {"universe_id": "world", "branch_id": "dev"}
        assert assert_scope_match(row, caller) is True

    def test_branch_mismatch_drops_with_flag_on(self, monkeypatch):
        monkeypatch.setenv("TINYASSETS_TIERED_SCOPE", "on")
        caller = MemoryScope(universe_id="world", branch_id="main")
        row = {"universe_id": "world", "branch_id": "dev"}
        assert assert_scope_match(row, caller) is False

    def test_branch_match_passes_with_flag_on(self, monkeypatch):
        monkeypatch.setenv("TINYASSETS_TIERED_SCOPE", "on")
        caller = MemoryScope(universe_id="world", branch_id="main")
        row = {"universe_id": "world", "branch_id": "main"}
        assert assert_scope_match(row, caller) is True

    def test_null_subtier_row_passes_with_flag_on(self, monkeypatch):
        """Legacy / universe-public rows (NULL sub-tier) still pass."""
        monkeypatch.setenv("TINYASSETS_TIERED_SCOPE", "on")
        caller = MemoryScope(universe_id="world", branch_id="main")
        row = {"universe_id": "world", "branch_id": None}
        assert assert_scope_match(row, caller) is True

    def test_empty_string_subtier_treated_as_null(self, monkeypatch):
        """LanceDB uses '' as the string-null equivalent."""
        monkeypatch.setenv("TINYASSETS_TIERED_SCOPE", "on")
        caller = MemoryScope(universe_id="world", branch_id="main")
        row = {"universe_id": "world", "branch_id": ""}
        assert assert_scope_match(row, caller) is True

    def test_unpinned_caller_ignores_row_subtier(self, monkeypatch):
        """Unpinned caller sees any branch row, flag on or off."""
        monkeypatch.setenv("TINYASSETS_TIERED_SCOPE", "on")
        caller = MemoryScope(universe_id="world")  # no branch pin
        row = {"universe_id": "world", "branch_id": "dev"}
        assert assert_scope_match(row, caller) is True

    def test_goal_mismatch_drops_with_flag_on(self, monkeypatch):
        monkeypatch.setenv("TINYASSETS_TIERED_SCOPE", "on")
        caller = MemoryScope(universe_id="world", goal_id="book-1")
        row = {"universe_id": "world", "goal_id": "book-2"}
        assert assert_scope_match(row, caller) is False

    def test_user_mismatch_drops_with_flag_on(self, monkeypatch):
        monkeypatch.setenv("TINYASSETS_TIERED_SCOPE", "on")
        caller = MemoryScope(universe_id="world", user_id="alice")
        row = {"universe_id": "world", "user_id": "bob"}
        assert assert_scope_match(row, caller) is False

    def test_dataclass_row_with_attributes(self, monkeypatch):
        """Dataclass-style rows read tier values via getattr."""
        monkeypatch.setenv("TINYASSETS_TIERED_SCOPE", "on")

        class Row:
            def __init__(self, universe_id: str, branch_id: str | None = None):
                self.universe_id = universe_id
                self.branch_id = branch_id

        caller = MemoryScope(universe_id="world", branch_id="main")
        assert assert_scope_match(Row("world", "main"), caller) is True
        assert assert_scope_match(Row("world", "dev"), caller) is False

    def test_row_universe_id_compat_alias(self):
        """Backward-compat helper returns the universe_id only."""
        assert _row_universe_id({"universe_id": "world"}) == "world"
        assert _row_universe_id({}) is None
        assert _row_universe_id({"universe_id": ""}) is None  # empty -> None


# ─── _drop_cross_universe_rows (integration with flag) ──────────────────


class TestDropCrossUniverseRowsWithFlag:
    def _result_with_rows(self, rows: list[dict]) -> RetrievalResult:
        result = RetrievalResult()
        result.facts = list(rows)
        return result

    def test_universe_mismatch_drops_regardless_of_flag(self, monkeypatch):
        monkeypatch.delenv("TINYASSETS_TIERED_SCOPE", raising=False)
        caller = MemoryScope(universe_id="world-a")
        result = self._result_with_rows([
            {"universe_id": "world-a"},  # keep
            {"universe_id": "world-b"},  # drop
        ])
        dropped = _drop_cross_universe_rows(result, caller)
        assert len(dropped.facts) == 1
        assert dropped.facts[0]["universe_id"] == "world-a"

    def test_flag_off_preserves_2b2_behavior_on_subtiers(self, monkeypatch):
        """With flag off, cross-branch rows PASS through — exactly 2b.2-era semantics."""
        monkeypatch.delenv("TINYASSETS_TIERED_SCOPE", raising=False)
        caller = MemoryScope(universe_id="world", branch_id="main")
        result = self._result_with_rows([
            {"universe_id": "world", "branch_id": "main"},
            {"universe_id": "world", "branch_id": "dev"},
        ])
        dropped = _drop_cross_universe_rows(result, caller)
        # Both survive — sub-tier enforcement is off.
        assert len(dropped.facts) == 2

    def test_flag_on_drops_cross_branch(self, monkeypatch):
        monkeypatch.setenv("TINYASSETS_TIERED_SCOPE", "on")
        caller = MemoryScope(universe_id="world", branch_id="main")
        result = self._result_with_rows([
            {"universe_id": "world", "branch_id": "main"},
            {"universe_id": "world", "branch_id": "dev"},
            {"universe_id": "world", "branch_id": None},  # legacy, passes
        ])
        dropped = _drop_cross_universe_rows(result, caller)
        assert len(dropped.facts) == 2
        branches = {r["branch_id"] for r in dropped.facts}
        assert branches == {"main", None}

    def test_flag_on_drops_across_all_result_fields(self, monkeypatch):
        monkeypatch.setenv("TINYASSETS_TIERED_SCOPE", "on")
        caller = MemoryScope(universe_id="world", branch_id="main")
        result = RetrievalResult()
        result.facts = [{"universe_id": "world", "branch_id": "dev"}]
        result.relationships = [{"universe_id": "world", "branch_id": "dev"}]
        result.prose_chunks = [{"universe_id": "world", "branch_id": "dev"}]
        result.community_summaries = [{"universe_id": "world", "branch_id": "dev"}]
        filtered = _drop_cross_universe_rows(result, caller)
        assert filtered.facts == []
        assert filtered.relationships == []
        assert filtered.prose_chunks == []
        assert filtered.community_summaries == []


# ─── Private-universe ACL fixture (Stage 2c criterion) ──────────────────


@pytest.fixture
def base_path(tmp_path, monkeypatch):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    initialize_author_server(base)
    return base


class TestPrivateUniverseACLFixture:
    """Private-universe Layer 1 ACL isolation — the Stage 2c criterion
    "≥1 private-universe fixture" lives here. These tests exercise the
    ACL functions directly, independent of the read-side flag."""

    def test_public_universe_has_no_acl(self, base_path):
        assert universe_is_private(base_path, universe_id="u-public") is False
        acl = list_universe_acl(base_path, universe_id="u-public")
        assert acl == []

    def test_public_universe_grants_default_read(self, base_path):
        """Public universes (no ACL rows) default to 'read' for any actor —
        so the read-side path treats public-universe-any-actor uniformly
        with private-universe-granted-reader."""
        perm = universe_access_permission(
            base_path, universe_id="u-public", actor_id="anyone",
        )
        assert perm == "read"

    def test_grant_makes_universe_private(self, base_path):
        grant_universe_access(
            base_path,
            universe_id="u-priv",
            actor_id="alice",
            permission="admin",
            granted_by="alice",
        )
        assert universe_is_private(base_path, universe_id="u-priv") is True
        # Sibling universes unaffected.
        assert universe_is_private(base_path, universe_id="u-other") is False

    def test_private_universe_denies_ungranted_actor(self, base_path):
        grant_universe_access(
            base_path,
            universe_id="u-priv",
            actor_id="alice",
            permission="admin",
            granted_by="alice",
        )
        perm = universe_access_permission(
            base_path, universe_id="u-priv", actor_id="bob",
        )
        assert perm == ""  # not granted → empty → denied

    def test_private_universe_allows_granted_actor(self, base_path):
        grant_universe_access(
            base_path,
            universe_id="u-priv",
            actor_id="alice",
            permission="admin",
            granted_by="alice",
        )
        grant_universe_access(
            base_path,
            universe_id="u-priv",
            actor_id="bob",
            permission="read",
            granted_by="alice",
        )
        assert universe_access_permission(
            base_path, universe_id="u-priv", actor_id="alice",
        ) == "admin"
        assert universe_access_permission(
            base_path, universe_id="u-priv", actor_id="bob",
        ) == "read"

    def test_revoke_removes_permission(self, base_path):
        grant_universe_access(
            base_path,
            universe_id="u-priv",
            actor_id="alice",
            permission="admin",
            granted_by="alice",
        )
        grant_universe_access(
            base_path,
            universe_id="u-priv",
            actor_id="bob",
            permission="read",
            granted_by="alice",
        )
        revoke_universe_access(
            base_path, universe_id="u-priv", actor_id="bob",
        )
        assert universe_access_permission(
            base_path, universe_id="u-priv", actor_id="bob",
        ) == ""
        # Last grant survives — universe stays private.
        assert universe_is_private(base_path, universe_id="u-priv") is True

    def test_flag_off_does_not_affect_acl_check(self, base_path, monkeypatch):
        """ACL isolation is independent of TINYASSETS_TIERED_SCOPE. Layer 1
        (universe-level ACL) runs regardless of whether Layer 2 (sub-tier
        filtering) is enabled."""
        monkeypatch.delenv("TINYASSETS_TIERED_SCOPE", raising=False)
        grant_universe_access(
            base_path,
            universe_id="u-priv",
            actor_id="alice",
            permission="admin",
            granted_by="alice",
        )
        assert universe_is_private(base_path, universe_id="u-priv") is True
        # ACL identical result with flag on or off.
        monkeypatch.setenv("TINYASSETS_TIERED_SCOPE", "on")
        assert universe_is_private(base_path, universe_id="u-priv") is True

    def test_empty_universe_id_is_never_private(self, base_path):
        assert universe_is_private(base_path, universe_id="") is False

    def test_empty_actor_gets_no_permission(self, base_path):
        grant_universe_access(
            base_path,
            universe_id="u-priv",
            actor_id="alice",
            permission="admin",
            granted_by="alice",
        )
        assert universe_access_permission(
            base_path, universe_id="u-priv", actor_id="",
        ) == ""
