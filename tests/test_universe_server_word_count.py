"""Tests for read-time `word_count` derivation.

status.json::word_count is a cached `DashboardMetrics` snapshot that
drifts across universe switches (user-sim P3 saw sporemarch reporting
46380 with 40+ scenes, test-universe reporting 52162 with 1 scene).
The fix reads prose files on disk at inspect time — slower but truthful.

These tests lock in the contract:
- Word counts derive from `<universe>/output/**/*.md`.
- Empty/missing output → 0 with `source: "none"` so callers can tell
  "hasn't written yet" apart from "zero real scenes".
- Sample reports the scene count so the number is auditable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tinyassets.api.universe as us


@pytest.fixture
def universe_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    _record_owner(base, "u")
    return base


def _record_owner(base: Path, uid: str) -> None:
    from tinyassets.daemon_server import _connect, initialize_author_server
    from tinyassets.universe_owner import record_creation

    initialize_author_server(base)
    with _connect(base) as conn:
        record_creation(conn, universe_id=uid, owner_id="test-owner")


def _make_scene(
    udir: Path, book: int, chapter: int, scene: int, words: int,
) -> Path:
    ch_dir = udir / "output" / f"book-{book}" / f"chapter-{chapter:02d}"
    ch_dir.mkdir(parents=True, exist_ok=True)
    # Simple deterministic prose: N single-word lines.
    text = " ".join(["word"] * words)
    path = ch_dir / f"scene-{scene:02d}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _declare_public(base: Path, uid: str, udir: Path) -> None:
    """Register and OWN a universe, then declare it public so it is not withheld
    by the fail-closed universe-visibility gate on list/inspect."""
    from tinyassets.api.visibility import set_universe_visibility
    from tinyassets.daemon_server import ensure_universe_registered, set_founder_home

    ensure_universe_registered(base, universe_id=uid, universe_path=udir)
    # An OWNER is what makes the directory a universe (2026-09-02: a folder on
    # disk is not one -- tests/test_a_universe_needs_an_owner.py). A
    # `founder_home` binding rather than an ACL grant, because a universe with
    # zero ACL rows is PUBLIC and that is the state these tests assert.
    set_founder_home(base, founder_sub=f"test-owner::{uid}", universe_id=uid)
    _record_owner(base, uid)
    set_universe_visibility(uid, "public", source="owner")


def test_empty_universe_returns_zero_with_source_none(
    universe_base: Path,
) -> None:
    udir = universe_base / "u"
    udir.mkdir()
    words, sample = us._compute_word_count_from_files(udir)
    assert words == 0
    assert sample["source"] == "none"
    assert sample["scenes"] == 0


def test_scenes_are_summed_and_counted(universe_base: Path) -> None:
    udir = universe_base / "u"
    udir.mkdir()
    _make_scene(udir, 1, 1, 1, 100)
    _make_scene(udir, 1, 1, 2, 250)
    _make_scene(udir, 1, 2, 1, 50)

    words, sample = us._compute_word_count_from_files(udir)
    assert words == 400
    assert sample == {"scenes": 3, "source": "output_files"}


def test_index_and_progress_files_are_excluded(universe_base: Path) -> None:
    """INDEX.md and progress.md live under output/ in some layouts — they
    must not inflate the prose word count.
    """
    udir = universe_base / "u"
    udir.mkdir()
    _make_scene(udir, 1, 1, 1, 100)
    (udir / "output" / "INDEX.md").write_text(
        "index " * 1000, encoding="utf-8",
    )
    (udir / "output" / "progress.md").write_text(
        "progress " * 1000, encoding="utf-8",
    )

    words, sample = us._compute_word_count_from_files(udir)
    assert words == 100
    assert sample["scenes"] == 1


def test_scenes_key_counts_only_scene_files_not_other_prose(
    universe_base: Path,
) -> None:
    """The `scenes` field reports scene-*.md files specifically (so users
    can audit the count). Other prose files still count toward word_count
    but not toward `scenes`.
    """
    udir = universe_base / "u"
    udir.mkdir()
    _make_scene(udir, 1, 1, 1, 100)
    # A chapter-level intro file — prose, but not a scene.
    ch_dir = udir / "output" / "book-1" / "chapter-01"
    (ch_dir / "intro.md").write_text("intro " * 50, encoding="utf-8")

    words, sample = us._compute_word_count_from_files(udir)
    assert words == 150  # 100 scene + 50 intro
    assert sample["scenes"] == 1  # only scene-*.md counts


def test_inspect_surfaces_on_disk_word_count_not_stale_status(
    universe_base: Path,
) -> None:
    """Regression guard for Task #12: status.json had `word_count: 99999`
    but only 1 scene on disk. Inspect must return the on-disk value.
    """
    udir = universe_base / "u"
    udir.mkdir()
    (udir / "PROGRAM.md").write_text("a premise", encoding="utf-8")
    (udir / "status.json").write_text(
        json.dumps({"current_phase": "draft", "word_count": 99999}),
        encoding="utf-8",
    )
    _make_scene(udir, 1, 1, 1, 40)
    _declare_public(universe_base, "u", udir)

    out = json.loads(us._action_inspect_universe(universe_id="u"))
    assert out["daemon"]["word_count"] == 40
    assert out["daemon"]["word_count_sample"] == {
        "scenes": 1, "source": "output_files",
    }


def test_inspect_reports_zero_for_universe_without_output(
    universe_base: Path,
) -> None:
    """default-universe had status.json::word_count=53543 but no output
    directory at all. Inspect must return 0, not the stale cache.
    """
    udir = universe_base / "u"
    udir.mkdir()
    (udir / "status.json").write_text(
        json.dumps({"current_phase": "draft", "word_count": 53543}),
        encoding="utf-8",
    )
    _declare_public(universe_base, "u", udir)

    out = json.loads(us._action_inspect_universe(universe_id="u"))
    assert out["daemon"]["word_count"] == 0
    assert out["daemon"]["word_count_sample"]["source"] == "none"


def test_control_daemon_status_uses_on_disk_word_count(
    universe_base: Path,
) -> None:
    udir = universe_base / "u"
    udir.mkdir()
    (udir / "status.json").write_text(
        json.dumps({"current_phase": "draft", "word_count": 9999}),
        encoding="utf-8",
    )
    _make_scene(udir, 1, 1, 1, 12)

    out = json.loads(
        us._action_control_daemon(universe_id="u", text="status"),
    )
    assert out["word_count"] == 12
    assert out["word_count_sample"]["scenes"] == 1


def test_list_surfaces_on_disk_word_count(universe_base: Path) -> None:
    udir_a = universe_base / "alive"
    udir_a.mkdir()
    _make_scene(udir_a, 1, 1, 1, 5)
    udir_b = universe_base / "empty"
    udir_b.mkdir()
    _declare_public(universe_base, "alive", udir_a)
    _declare_public(universe_base, "empty", udir_b)

    out = json.loads(us._action_list_universes())
    by_id = {u["id"]: u for u in out["universes"]}
    assert by_id["alive"]["word_count"] == 5
    assert by_id["empty"]["word_count"] == 0
