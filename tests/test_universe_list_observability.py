"""Tests for Task #4 observability fix — universe list + get_status diagnostics.

Covers:
- `_action_list_universes` returning a `note` field when universes list is
  empty, distinguishing base-dir-missing / empty / all-filtered.
- `get_status` returning a `universe_exists` boolean so the chatbot can tell
  when `_default_universe()` fell through to a hardcoded fallback that
  doesn't correspond to a real directory.
"""

from __future__ import annotations

import json

import pytest

from tinyassets.api.universe import _action_list_universes
from tinyassets.universe_server import get_status


@pytest.fixture
def empty_base(tmp_path, monkeypatch):
    """Point TINYASSETS_DATA_DIR at an empty existing directory."""
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def missing_base(tmp_path, monkeypatch):
    """Point TINYASSETS_DATA_DIR at a path that does not exist."""
    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(missing))
    return missing


@pytest.fixture
def hidden_only_base(tmp_path, monkeypatch):
    """Base dir contains only hidden entries — all filtered out by the
    `name.startswith('.')` guard."""
    (tmp_path / ".hidden1").mkdir()
    (tmp_path / ".hidden2").mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    return tmp_path


def _declare_public(base, uid, udir):
    """Register a universe, give it an OWNER, and declare it explicitly public.

    Under the universe-visibility contract an undeclared universe is withheld
    from enumeration/status (fail closed); these observability tests assert
    public-universe listing behavior, so they must declare intent.

    The owner is what makes it a universe at all (2026-09-02: a directory on
    disk is not one -- ``tests/test_a_universe_needs_an_owner.py``). A
    ``founder_home`` binding rather than an ACL grant, because a universe with
    zero ACL rows is PUBLIC and that is the state under test here.
    """
    from tinyassets.api.visibility import set_universe_visibility
    from tinyassets.daemon_server import ensure_universe_registered, set_founder_home

    ensure_universe_registered(base, universe_id=uid, universe_path=udir)
    set_founder_home(base, founder_sub=f"test-owner::{uid}", universe_id=uid)
    set_universe_visibility(uid, "public", source="owner")


@pytest.fixture
def populated_base(tmp_path, monkeypatch):
    """Base dir contains one legitimate universe."""
    udir = tmp_path / "alpha"
    udir.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _declare_public(tmp_path, "alpha", udir)
    return tmp_path


@pytest.fixture
def operational_dirs_base(tmp_path, monkeypatch):
    """Base dir contains storage subsystem directories beside universes.

    The four names the retired ``_TOP_LEVEL_OPERATIONAL_DATA_DIRS`` denylist
    carried, kept here on purpose: they must still be excluded now that the
    denylist is gone and ownership decides.
    """
    udir = tmp_path / "alpha"
    udir.mkdir()
    for name in ("wiki", "runs", "lance", "output"):
        (tmp_path / name).mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _declare_public(tmp_path, "alpha", udir)
    return tmp_path


class TestListUniversesEmptyNote:
    def test_missing_base_returns_does_not_exist_note(self, missing_base):
        result = json.loads(_action_list_universes())
        assert result["universes"] == []
        assert result["count"] == 0
        assert "does not exist" in result["note"]

    def test_empty_base_returns_empty_note(self, empty_base):
        result = json.loads(_action_list_universes())
        assert result["universes"] == []
        assert result["count"] == 0
        assert "empty" in result["note"]

    def test_hidden_only_base_returns_filtered_note(self, hidden_only_base):
        result = json.loads(_action_list_universes())
        assert result["universes"] == []
        assert result["count"] == 0
        assert "hidden or non-directories" in result["note"]

    def test_populated_base_has_no_note(self, populated_base):
        result = json.loads(_action_list_universes())
        assert result["count"] == 1
        assert result["universes"][0]["id"] == "alpha"
        assert "note" not in result

    def test_operational_storage_dirs_are_not_universes(self, operational_dirs_base):
        result = json.loads(_action_list_universes())
        assert result["count"] == 1
        assert [u["id"] for u in result["universes"]] == ["alpha"]


class TestGetStatusUniverseExists:
    def test_no_universe_bound_says_so_rather_than_naming_a_fallback(
        self, empty_base,
    ):
        """The chatbot must narrate the situation accurately rather than
        reporting a live universe.

        This used to assert `universe_exists: False` on the full shape, because
        an unauthenticated caller fell through to a `default-universe` that was
        not on disk. A signed-in founder with no home never reaches that
        fallback: they get the short shape, which says no universe is bound and
        tells them what to do. That is the same guarantee, stated by the code
        instead of inferred from a flag about a directory nobody made.
        """
        result = json.loads(get_status())
        assert "universe_id" not in result, (
            f"a universe was named for an account with no home: {result}"
        )
        assert result["first_contact"]["event"] == "no_universe_yet"
        assert "not bound" in result["first_contact"]["note"].lower() or (
            "no complete home" in result["first_contact"]["note"].lower()
        ), result["first_contact"]
        assert result["next_step_for_user"], result

    def test_existing_universe_dir_flags_true(self, populated_base):
        result = json.loads(get_status(command_center_id="alpha"))
        assert result["universe_exists"] is True
        assert not any(
            "does not exist on disk" in c for c in result["caveats"]
        )

    def test_explicit_missing_universe_flags_false(self, populated_base):
        """Explicit universe_id that does not exist — same diagnostic."""
        result = json.loads(get_status(command_center_id="ghost"))
        assert result["universe_exists"] is False
        assert any(
            "does not exist on disk" in c for c in result["caveats"]
        )
