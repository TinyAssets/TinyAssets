"""Harness W: the universe agent writes its own wiki (no real jail needed).

Live, 2026-10-01: the founder's agent wrote a 6 KB evidence-cited page, then
said "my wiki is read-only through the available tools" and filed it under
``notes/``. A dot's own computer includes its own wiki (design #4172 §4.3).

Making ``wiki/`` agent-writable makes every daemon reader of it a reader of
untrusted input, so this module also pins how those readers read: link-free,
bounded, and loud instead of silently empty. The real-jail proof is
``tests/test_universe_tools_jail.py::test_the_agent_writes_its_own_wiki_but_not_the_trusted_write_back_markers``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tinyassets import universe_files, universe_tools
from tinyassets.api.helpers import _read_text, _scoped_wiki_root
from tinyassets.effectors import wiki_write_back
from tinyassets.providers import provider_jail


def _pairs(argv: list[str], flag: str) -> list[tuple[str, str]]:
    return [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv[:-2]) if a == flag]


def _universe(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "u-alpha"
    (path / "wiki" / "pages").mkdir(parents=True)
    return path


def test_the_wiki_is_bound_read_write_in_the_tool_jail(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    monkeypatch.setattr(provider_jail, "BWRAP_RESOLVER", lambda: "/usr/bin/bwrap")
    argv = universe_tools.tool_jail_argv(universe, ["/bin/true"], agent_id="main")
    rw = dict((dest, src) for src, dest in _pairs(argv, "--bind-try"))
    assert rw["/u/wiki"] == str(universe.resolve() / "wiki")
    ro = {dest for _src, dest in _pairs(argv, "--ro-bind-try")}
    assert "/u/wiki" not in ro


def test_a_universe_without_a_wiki_gets_one_to_write(tmp_path, monkeypatch):
    universe = tmp_path / "data" / "u-new"
    universe.mkdir(parents=True)
    monkeypatch.setattr(provider_jail, "BWRAP_RESOLVER", lambda: "/usr/bin/bwrap")
    argv = universe_tools.tool_jail_argv(universe, ["/bin/true"], agent_id="main")
    assert (universe / "wiki").is_dir()
    assert "/u/wiki" in {dest for _src, dest in _pairs(argv, "--bind-try")}


def test_the_trusted_write_back_markers_live_outside_the_wiki(tmp_path):
    """The markers decide whether a stale wiki effect already happened. Inside
    ``wiki/`` the agent could forge one; at the root the jail never binds it."""
    universe = _universe(tmp_path)
    path = wiki_write_back._destination_marker_db_path(universe)
    assert path.parent == universe
    assert path.name.startswith("."), "a hidden root entry is absent from the jail"


def test_a_marker_file_left_in_the_wiki_is_never_read(tmp_path):
    universe = _universe(tmp_path)
    stale = universe / "wiki" / wiki_write_back._DESTINATION_MARKER_DB
    import sqlite3

    with sqlite3.connect(stale) as connection:
        connection.execute(
            "CREATE TABLE applied_wiki_effects (effect_key TEXT PRIMARY KEY, "
            "destination TEXT, page_sha256 TEXT, recorded_at REAL)"
        )
        connection.execute(
            "INSERT INTO applied_wiki_effects VALUES ('k1', 'pages/a.md', 'x', 0)"
        )
    target = universe / "wiki" / "pages" / "a.md"
    target.write_text("# A\n", encoding="utf-8")
    result = wiki_write_back._reconcile_destination_marker(
        universe_dir=universe, target=target, destination="pages/a.md", effect_key="k1",
    )
    assert result == {"status": "unknown"}, "a forged marker in wiki/ must not count"


def test_a_wiki_page_reads_through_the_bounded_reader(tmp_path):
    universe = _universe(tmp_path)
    page = universe / "wiki" / "pages" / "a.md"
    page.write_bytes(b"# A\r\nbody\r\n")
    with _scoped_wiki_root(universe / "wiki"):
        assert _read_text(page.resolve()) == "# A\nbody\n"
        assert _read_text((universe / "wiki" / "pages" / "absent.md").resolve(), "dflt") == "dflt"


def test_an_oversized_wiki_page_fails_loudly_instead_of_reading_empty(tmp_path, monkeypatch):
    """A read-modify-write that saw "" would overwrite the page with one section."""
    universe = _universe(tmp_path)
    page = universe / "wiki" / "pages" / "huge.md"
    page.write_bytes(b"x" * 2048)
    monkeypatch.setattr(universe_files, "MAX_UNIVERSE_FILE_BYTES", 1024)
    original = universe_files.read_universe_file

    def bounded(root, relpath, *, max_bytes=1024):
        return original(root, relpath, max_bytes=max_bytes)

    monkeypatch.setattr(universe_files, "read_universe_file", bounded)
    with _scoped_wiki_root(universe / "wiki"):
        # POSIX refuses with workspace_fs.UnsafePoolPath, other hosts with
        # UniverseFileError: either way an OSError that is not "absent".
        with pytest.raises(OSError) as refused:
            _read_text(page.resolve())
        assert not isinstance(refused.value, FileNotFoundError)


def test_every_refusal_but_absent_propagates(tmp_path, monkeypatch):
    """gpt-6-astra on #4185: the POSIX reader raises its own OSError type, which
    an earlier draft logged and turned into "" -- and write-back then replaced
    the page with one section."""
    universe = _universe(tmp_path)
    page = universe / "wiki" / "pages" / "a.md"
    page.write_text("# A\n", encoding="utf-8")

    class HostRefusal(OSError):
        pass

    def refuse(root, relpath, **_kw):
        raise HostRefusal("refused by the host's safe reader")

    monkeypatch.setattr(universe_files, "read_universe_file", refuse)
    with _scoped_wiki_root(universe / "wiki"):
        with pytest.raises(HostRefusal):
            _read_text(page.resolve())


def test_a_linked_wiki_page_is_refused(tmp_path):
    universe = _universe(tmp_path)
    foreign = tmp_path / "data" / "u-bravo"
    foreign.mkdir()
    (foreign / "founder.md").write_text("FOREIGN", encoding="utf-8")
    link = universe / "wiki" / "pages" / "planted.md"
    try:
        os.symlink(foreign / "founder.md", link)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a symlink")
    with _scoped_wiki_root(universe / "wiki"):
        with pytest.raises(OSError):
            _read_text(universe / "wiki" / "pages" / "planted.md")


def test_a_file_outside_the_wiki_still_reads_as_before(tmp_path):
    """activity.log and run logs are platform records the jail binds read-only;
    outside a wiki operation they keep the plain reader, not the wiki bound."""
    universe = _universe(tmp_path)
    log = universe / "activity.log"
    log.write_text("line\n", encoding="utf-8")
    assert _read_text(log) == "line\n"
    assert _read_text(universe / "missing.log", "none") == "none"


def test_inside_a_universe_wiki_operation_an_outside_path_is_refused(tmp_path):
    """gpt-6-astra on #4185: a page path resolved through a planted wiki/pages
    link lands outside the wiki, where the plain reader would follow it into
    another universe. Inside a universe-scoped operation that is refused."""
    universe = _universe(tmp_path)
    foreign = tmp_path / "data" / "u-bravo"
    foreign.mkdir()
    (foreign / "founder.md").write_text("FOREIGN", encoding="utf-8")
    with _scoped_wiki_root(universe / "wiki"):
        with pytest.raises(universe_files.UniverseFileError):
            _read_text((foreign / "founder.md").resolve())
