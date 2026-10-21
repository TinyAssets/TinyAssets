"""Daemon reads and writes never follow a link planted in a universe.

A workflow provider jail binds its universe read-write and allows ``symlink``
(codex's nested sandbox needs it), so universe A's provider can plant
``activity.log -> /data/<B>/founder.md``. Before this, ``inspect`` read it with
a plain ``read_text`` and returned B's lines to A's owner, and the tier-config
action wrote ``dispatcher_config.yaml`` straight through such a link
(``docs/concerns/2026-10-01-provider-planted-link-reads-another-universe.md``).

These tests plant the links the jail allows and assert two things: B's bytes
never come back, and B's files are never changed. A refused read RAISES rather
than reading as absent, because a read-modify-write that saw "absent" would
overwrite the file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import tinyassets.api.universe as us
from tinyassets import notes, universe_files
from tinyassets.api.helpers import _read_json, _read_text
from tinyassets.universe_files import UniverseFileError, read_data_path, write_data_path

FOREIGN = "B-SECRET founder line"


def _link(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a symlink")


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "data"
    (base / "u-alpha").mkdir(parents=True)
    bravo = base / "u-bravo"
    (bravo / "output").mkdir(parents=True)
    (bravo / "canon" / "sources").mkdir(parents=True)
    (bravo / "founder.md").write_text(FOREIGN + "\n", encoding="utf-8")
    for name in ("work_targets.json", "notes.json", "requests.json", "ledger.json"):
        (bravo / name).write_text(json.dumps([{"secret": FOREIGN}]), encoding="utf-8")
    (bravo / "dispatcher_config.yaml").write_text(f"secret: {FOREIGN}\n", encoding="utf-8")
    (bravo / "output" / "draft.md").write_text(FOREIGN, encoding="utf-8")
    (bravo / "canon" / "lore.md").write_text(FOREIGN, encoding="utf-8")
    (bravo / "canon" / ".lore.md.meta.json").write_text(
        json.dumps({"provenance": FOREIGN}), encoding="utf-8")
    (bravo / "canon" / "sources" / "upload.md").write_text(FOREIGN, encoding="utf-8")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setattr(us, "_request_universe", lambda _uid: "u-alpha")
    return base


def _alpha(data: Path) -> Path:
    return (data / "u-alpha").resolve()


def _bravo_untouched(data: Path) -> None:
    bravo = data / "u-bravo"
    for name in ("notes.json", "work_targets.json", "ledger.json"):
        assert json.loads((bravo / name).read_text(encoding="utf-8")) == [{"secret": FOREIGN}]
    assert (bravo / "dispatcher_config.yaml").read_text(encoding="utf-8") == f"secret: {FOREIGN}\n"
    assert sorted(p.name for p in (bravo / "canon").iterdir()) == [
        ".lore.md.meta.json", "lore.md", "sources",
    ]


# --- reads ---------------------------------------------------------------








def test_a_real_platform_file_still_reads(data):
    udir = _alpha(data)
    (udir / "activity.log").write_bytes(b"one\r\ntwo\n")
    (udir / "work_targets.json").write_text('{"ok": true}', encoding="utf-8")
    assert _read_text(udir / "activity.log") == "one\ntwo\n"
    assert _read_json(udir / "work_targets.json") == {"ok": True}
    assert _read_text(udir / "missing.log", "none") == "none"
    assert _read_json(udir / "missing.json") is None
    assert read_data_path(udir / "missing" / "deeper.json") is None






def test_read_output_refuses_traversal_and_reads_a_real_file(data):
    (_alpha(data) / "output").mkdir()
    (_alpha(data) / "output" / "note.md").write_text("hello", encoding="utf-8")
    ok = json.loads(us._action_read_output(universe_id="u-alpha", path="note.md"))
    assert ok["content"] == "hello"
    for bad in ("../u-bravo/founder.md", "/etc/passwd", "", "a//b"):
        out = json.loads(us._action_read_output(universe_id="u-alpha", path=bad))
        assert out == {"error": "Path traversal not allowed."}
    missing = json.loads(us._action_read_output(universe_id="u-alpha", path="nope.md"))
    assert missing["error"].startswith("File not found")






def test_canon_still_reads_a_real_file(data):
    canon = _alpha(data) / "canon"
    canon.mkdir()
    (canon / "lore.md").write_bytes(b"alpha lore\r\n")
    out = json.loads(us._action_read_canon(universe_id="u-alpha", filename="lore.md"))
    assert out["content"] == "alpha lore\n"


# --- writes --------------------------------------------------------------




def test_a_write_onto_a_linked_file_replaces_the_link_not_the_target(data):
    link = _alpha(data) / "dispatcher_config.yaml"
    _link(data / "u-bravo" / "dispatcher_config.yaml", link)
    write_data_path(link, "mine: true\n")
    assert not link.is_symlink()
    assert link.read_text(encoding="utf-8") == "mine: true\n"
    _bravo_untouched(data)








def test_writes_still_land_for_a_real_universe(data):
    udir = _alpha(data)
    write_data_path(udir / "deep" / "er" / "file.json", "{}")
    assert (udir / "deep" / "er" / "file.json").read_text(encoding="utf-8") == "{}"
    write_data_path(udir / "deep" / "er" / "file.json", "[1]")
    assert (udir / "deep" / "er" / "file.json").read_text(encoding="utf-8") == "[1]"
    assert [p.name for p in (udir / "deep" / "er").iterdir()] == ["file.json"]
    notes.add_note(udir, source="user", text="hello", category="observation")
    assert "hello" in (udir / "notes.json").read_text(encoding="utf-8")


def test_universe_files_has_one_reader_and_one_writer():
    """The single-helper contract: callers import these, nothing reimplements them."""
    assert {"read_data_path", "write_data_path", "read_universe_file",
            "write_universe_file"} <= set(universe_files.__all__)


# --- round 2 refute (gpt-6-astra on #4254) ----------------------------------


def test_daemon_overview_tail_refuses_a_linked_activity_log(data):
    _link(data / "u-bravo" / "founder.md", _alpha(data) / "activity.log")
    with pytest.raises(UniverseFileError):
        us._tail_file_lines(_alpha(data) / "activity.log", 10)
    (data / "u-alpha" / "real.log").write_bytes(b"a\nb\nc\n")
    assert us._tail_file_lines(_alpha(data) / "real.log", 2) == ["b", "c"]
    assert us._tail_file_lines(_alpha(data) / "absent.log", 2) == []














def test_append_exclusive_and_unlink_never_cross_a_linked_dir(data):
    from tinyassets.universe_files import unlink_data_path

    _link(data / "u-bravo", _alpha(data) / "elsewhere")
    target = _alpha(data) / "elsewhere" / "founder.md"
    for call in (
        lambda: write_data_path(target, "x", mode="append"),
        lambda: write_data_path(_alpha(data) / "elsewhere" / "new.md", "x", mode="exclusive"),
        lambda: unlink_data_path(target),
    ):
        with pytest.raises(OSError):
            call()
    assert (data / "u-bravo" / "founder.md").read_text(encoding="utf-8") == FOREIGN + "\n"
    assert not (data / "u-bravo" / "new.md").exists()


def test_append_and_exclusive_refuse_a_linked_file(data):
    link = _alpha(data) / "log.md"
    _link(data / "u-bravo" / "founder.md", link)
    with pytest.raises(OSError):
        write_data_path(link, "x", mode="append")
    with pytest.raises(FileExistsError):
        write_data_path(link, "x", mode="exclusive")
    assert (data / "u-bravo" / "founder.md").read_text(encoding="utf-8") == FOREIGN + "\n"


def test_a_failed_write_leaves_no_temp_file(data, monkeypatch):
    if not getattr(universe_files.fs, "_POSIX", False):
        pytest.skip("the descriptor write path is POSIX")
    udir = _alpha(data)

    def broken_write(fd, view):
        raise OSError("disk full")

    monkeypatch.setattr(universe_files.os, "write", broken_write)
    with pytest.raises(OSError):
        write_data_path(udir / "x.json", "{}")
    monkeypatch.undo()
    assert [p.name for p in udir.iterdir()] == []
