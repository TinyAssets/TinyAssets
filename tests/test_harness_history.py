"""D7a bounded, link-safe history and conflict-checked Undo."""
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from tinyassets import harness_history as history
from tinyassets import memory_items


@pytest.fixture
def universe(tmp_path):
    root = tmp_path / "u-owner"
    root.mkdir()
    return root


def test_owner_edit_records_prior_bytes_outside_universe_and_undo(universe):
    path = universe / "AGENTS.md"
    path.write_bytes(b"Original\r\n")
    change = history.write_file(universe, "AGENTS.md", "Edited")
    store = universe.parent / ".agent-sessions" / universe.name / "history.db"
    assert store.exists() and not store.is_relative_to(universe)
    assert not list(universe.rglob("*.db"))
    with closing(sqlite3.connect(store)) as conn:
        row = conn.execute("SELECT prior_content, who, new_digest FROM changes").fetchone()
    assert row == (b"Original\r\n", "owner", history.digest(b"Edited"))
    assert history.list_history(universe)[0]["changed_at"] > 0
    history.undo(universe, change)
    assert path.read_bytes() == b"Original\r\n"
    assert len(history.list_history(universe)) == 2


def test_undo_conflicts_after_later_change(universe):
    change = history.write_file(universe, "AGENTS.md", "first")
    (universe / "AGENTS.md").write_text("later")
    with pytest.raises(history.HistoryConflict, match=r"AGENTS.md.*conflict"):
        history.undo(universe, change)
    assert (universe / "AGENTS.md").read_text() == "later"
    assert len(history.list_history(universe)) == 1


def test_undo_creation_removes_file_and_can_itself_be_undone(universe):
    change = history.write_file(universe, "MEMORY.md", "new")
    undo = history.undo(universe, change)
    assert not (universe / "MEMORY.md").exists()
    history.undo(universe, undo)
    assert (universe / "MEMORY.md").read_text() == "new"


@pytest.mark.parametrize("size", [
    history.MAX_PRIOR_BYTES, history.MAX_PRIOR_BYTES + 1, 9 * 1024 * 1024,
])
def test_prior_bound_is_explicit_and_never_truncated(universe, size):
    (universe / "AGENTS.md").write_bytes(b"x" * size)
    change = history.write_file(universe, "AGENTS.md", "replacement")
    entry = history.list_history(universe)[0]
    if size > history.MAX_PRIOR_BYTES:
        assert entry["prior_state"] == "too large"
        with pytest.raises(history.HistoryConflict, match="too large"):
            history.undo(universe, change)
    else:
        history.undo(universe, change)
        assert (universe / "AGENTS.md").read_bytes() == b"x" * size


@pytest.mark.parametrize("path", [
    "../other/AGENTS.md", "/AGENTS.md", "skills/../private", "rules.db",
])
def test_non_harness_paths_refuse(universe, path):
    with pytest.raises(ValueError):
        history.write_file(universe, path, "bad")


def _symlink(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")


def test_links_never_read_or_overwrite_other_files(universe, tmp_path):
    other = tmp_path / "secret"
    other.write_text("private")
    _symlink(universe / "MEMORY.md", other)
    with pytest.raises(OSError):
        memory_items.list_items(universe)
    with pytest.raises(OSError):
        memory_items.set_item(universe, None, "bad")
    assert other.read_text() == "private"


def test_parent_link_is_refused(universe, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _symlink(universe / "skills", other, directory=True)
    with pytest.raises(OSError):
        history.write_file(universe, "skills/new.md", "bad")
    assert not (other / "new.md").exists()


def test_hardlink_is_replaced_without_changing_other_inode(universe, tmp_path):
    other = tmp_path / "other"
    other.write_text("private")
    os.link(other, universe / "AGENTS.md")
    history.write_file(universe, "AGENTS.md", "mine")
    assert other.read_text() == "private"


def test_link_at_the_name_is_replaced_not_written_through(universe, tmp_path):
    """``_replace`` alone, with no prior read in front of it to refuse first."""
    other = tmp_path / "secret"
    other.write_text("private")
    _symlink(universe / "AGENTS.md", other)
    history._replace(universe, "AGENTS.md", b"mine")
    assert other.read_text() == "private"
    assert not (universe / "AGENTS.md").is_symlink()
    assert (universe / "AGENTS.md").read_bytes() == b"mine"


def test_a_link_at_or_above_the_universe_root_is_refused(universe, tmp_path):
    """Delegating to ``universe_files`` must not lose the root's own check.

    That writer resolves the universe dir before walking it link-free, so the
    walk can only refuse a link BELOW the root. Reaching a universe through a
    linked ancestor has to refuse before any byte is written.
    """
    real = tmp_path / "real"
    real.mkdir()
    (real / "AGENTS.md").write_text("original")
    _symlink(tmp_path / "via", real, directory=True)
    with pytest.raises(OSError):
        history._replace(tmp_path / "via", "AGENTS.md", b"new")
    with pytest.raises(OSError):
        history._replace(tmp_path / "via", "AGENTS.md", None)
    assert (real / "AGENTS.md").read_text() == "original"


def test_a_harness_write_never_creates_its_parent_directory(universe):
    with pytest.raises(OSError):
        history.write_file(universe, "skills/absent/SKILL.md", "skill")
    assert not (universe / "skills").exists()


def test_nested_skill_can_be_undone(universe):
    (universe / "skills" / "example").mkdir(parents=True)
    change = history.write_file(universe, "skills/example/SKILL.md", "skill")
    history.undo(universe, change)
    assert not (universe / "skills/example/SKILL.md").exists()


def test_concurrent_owner_adds_do_not_lose_items(universe):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: memory_items.set_item(universe, None, f"item {n}"), range(8)))
    assert len(memory_items.list_items(universe)) == 8
    assert len(history.list_history(universe)) == 8


def test_failed_replace_does_not_publish_history(monkeypatch, universe):
    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(history, "_replace", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        history.write_file(universe, "AGENTS.md", "unsaved")
    assert history.list_history(universe) == []
