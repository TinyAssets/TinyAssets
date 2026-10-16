"""Tests for tinyassets/api/helpers.py — extracted universe-server path helpers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyassets.api.helpers import (
    _base_path,
    _default_universe,
    _find_all_pages,
    _read_json,
    _read_text,
    _universe_dir,
    _wiki_drafts_dir,
    _wiki_pages_dir,
    _wiki_root,
)


def _own(base: Path, *names: str) -> None:
    """Give each directory an OWNER, which is what makes it a universe.

    Since 2026-09-02 a universe exists because an ownership row names it, not
    because a folder is on disk, so a test whose subject is resolution ORDER has
    to hand ``_default_universe`` real universes to order. A ``founder_home``
    binding (one synthetic founder each) is used rather than an ACL grant
    because a universe with zero ACL rows is PUBLIC, and these tests are not
    about privacy.
    """
    from tinyassets.daemon_server import set_founder_home

    for name in names:
        set_founder_home(base, founder_sub=f"test-owner::{name}", universe_id=name)


# ---------------------------------------------------------------------------
# _base_path
# ---------------------------------------------------------------------------

class TestBasePath:
    def test_returns_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = _base_path()
        assert isinstance(result, Path)

    def test_honours_workflow_data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        assert _base_path() == tmp_path

    def test_absolute(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        assert _base_path().is_absolute()


# ---------------------------------------------------------------------------
# _universe_dir
# ---------------------------------------------------------------------------

class TestUniverseDir:
    @pytest.mark.parametrize("name", ["", ".", "..", "a/../b", "a/b", "a\\b"])
    def test_authority_key_cannot_be_normalized(self, tmp_path, monkeypatch, name):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        with pytest.raises(ValueError, match="Invalid universe_id"):
            _universe_dir(name)

    def test_returns_child_of_base(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = _universe_dir("my-universe")
        assert result.parent == tmp_path.resolve()

    def test_correct_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = _universe_dir("my-universe")
        assert result.name == "my-universe"

    def test_path_traversal_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        with pytest.raises(ValueError, match="Invalid universe_id"):
            _universe_dir("../../etc/passwd")

    def test_path_traversal_absolute_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        # An absolute path that escapes base also raises
        with pytest.raises(ValueError, match="Invalid universe_id"):
            _universe_dir("/tmp/outside")

    def test_nested_name_stays_inside(self, tmp_path, monkeypatch):
        # A simple subdirectory within base is fine
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = _universe_dir("valid")
        assert result.is_relative_to(tmp_path)


# ---------------------------------------------------------------------------
# _default_universe
# ---------------------------------------------------------------------------

class TestDefaultUniverse:
    def test_env_var_overrides(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "my-default")
        assert _default_universe() == "my-default"

    def test_active_marker_overrides_env_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "my-default")
        (tmp_path / "my-default").mkdir()
        (tmp_path / "active-now").mkdir()
        _own(tmp_path, "my-default", "active-now")
        (tmp_path / ".active_universe").write_text("active-now", encoding="utf-8")
        assert _default_universe() == "active-now"

    def test_invalid_active_marker_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "my-default")
        (tmp_path / "my-default").mkdir()
        _own(tmp_path, "my-default")
        (tmp_path / ".active_universe").write_text("../outside", encoding="utf-8")
        assert _default_universe() == "my-default"

    def test_first_subdir_if_no_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", raising=False)
        (tmp_path / "alpha").mkdir()
        (tmp_path / "beta").mkdir()
        _own(tmp_path, "alpha", "beta")
        assert _default_universe() == "alpha"

    def test_skips_dotdirs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", raising=False)
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "visible").mkdir()
        _own(tmp_path, ".hidden", "visible")
        assert _default_universe() == "visible"

    def test_fallback_when_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", raising=False)
        assert _default_universe() == "default-universe"

    def test_fallback_when_no_base(self, tmp_path, monkeypatch):
        nonexistent = tmp_path / "nonexistent"
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(nonexistent))
        monkeypatch.delenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", raising=False)
        assert _default_universe() == "default-universe"

    def test_skips_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", raising=False)
        (tmp_path / "afile.txt").write_text("x")
        (tmp_path / "zdir").mkdir()
        _own(tmp_path, "afile.txt", "zdir")
        # File is sorted before zdir alphabetically but must be skipped
        assert _default_universe() == "zdir"


# ---------------------------------------------------------------------------
# _read_json
# ---------------------------------------------------------------------------

class TestReadJson:
    def test_reads_dict(self, tmp_path):
        p = tmp_path / "data.json"
        p.write_text('{"key": "value"}', encoding="utf-8")
        result = _read_json(p)
        assert result == {"key": "value"}

    def test_reads_list(self, tmp_path):
        p = tmp_path / "data.json"
        p.write_text('[1, 2, 3]', encoding="utf-8")
        result = _read_json(p)
        assert result == [1, 2, 3]

    def test_returns_none_missing(self, tmp_path):
        p = tmp_path / "nonexistent.json"
        assert _read_json(p) is None

    def test_returns_none_invalid_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json", encoding="utf-8")
        assert _read_json(p) is None

    def test_returns_none_empty_file(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("", encoding="utf-8")
        assert _read_json(p) is None

    def test_nested_structure(self, tmp_path):
        data = {"a": {"b": [1, 2]}, "c": None}
        p = tmp_path / "nested.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        assert _read_json(p) == data


# ---------------------------------------------------------------------------
# _read_text
# ---------------------------------------------------------------------------

class TestReadText:
    def test_reads_content(self, tmp_path):
        p = tmp_path / "file.txt"
        p.write_text("hello world", encoding="utf-8")
        assert _read_text(p) == "hello world"

    def test_returns_default_missing(self, tmp_path):
        p = tmp_path / "nonexistent.txt"
        assert _read_text(p) == ""

    def test_custom_default(self, tmp_path):
        p = tmp_path / "nonexistent.txt"
        assert _read_text(p, default="fallback") == "fallback"

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.txt"
        p.write_text("", encoding="utf-8")
        assert _read_text(p) == ""

    def test_multiline(self, tmp_path):
        content = "line1\nline2\nline3"
        p = tmp_path / "multi.txt"
        p.write_text(content, encoding="utf-8")
        assert _read_text(p) == content


# ---------------------------------------------------------------------------
# _wiki_root  (Task #8 — wiki-adjacent batch)
# ---------------------------------------------------------------------------

class TestWikiRoot:
    def test_honours_workflow_wiki_path(self, tmp_path, monkeypatch):
        target = tmp_path / "wiki-root"
        monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(target))
        assert _wiki_root() == target.resolve()

    def test_returns_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(tmp_path))
        assert isinstance(_wiki_root(), Path)


# ---------------------------------------------------------------------------
# _wiki_pages_dir / _wiki_drafts_dir
# ---------------------------------------------------------------------------

class TestWikiSubdirs:
    def test_pages_dir_under_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(tmp_path))
        assert _wiki_pages_dir() == _wiki_root() / "pages"

    def test_drafts_dir_under_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(tmp_path))
        assert _wiki_drafts_dir() == _wiki_root() / "drafts"

    def test_pages_drafts_distinct(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(tmp_path))
        assert _wiki_pages_dir() != _wiki_drafts_dir()


# ---------------------------------------------------------------------------
# _find_all_pages
# ---------------------------------------------------------------------------

class TestFindAllPages:
    def test_empty_directory(self, tmp_path):
        assert _find_all_pages(tmp_path) == []

    def test_nonexistent_directory(self, tmp_path):
        assert _find_all_pages(tmp_path / "nope") == []

    def test_finds_md_files(self, tmp_path):
        (tmp_path / "a.md").write_text("a", encoding="utf-8")
        (tmp_path / "b.md").write_text("b", encoding="utf-8")
        result = _find_all_pages(tmp_path)
        assert sorted(p.name for p in result) == ["a.md", "b.md"]

    def test_skips_non_md_files(self, tmp_path):
        (tmp_path / "page.md").write_text("md", encoding="utf-8")
        (tmp_path / "data.json").write_text("{}", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("txt", encoding="utf-8")
        result = _find_all_pages(tmp_path)
        assert [p.name for p in result] == ["page.md"]

    def test_recursive(self, tmp_path):
        (tmp_path / "top.md").write_text("t", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.md").write_text("n", encoding="utf-8")
        deep = sub / "deep"
        deep.mkdir()
        (deep / "leaf.md").write_text("l", encoding="utf-8")
        result = _find_all_pages(tmp_path)
        assert sorted(p.name for p in result) == ["leaf.md", "nested.md", "top.md"]

    def test_returns_sorted(self, tmp_path):
        (tmp_path / "z.md").write_text("z", encoding="utf-8")
        (tmp_path / "a.md").write_text("a", encoding="utf-8")
        (tmp_path / "m.md").write_text("m", encoding="utf-8")
        result = _find_all_pages(tmp_path)
        assert result == sorted(result)

    def test_returns_files_only(self, tmp_path):
        (tmp_path / "real.md").write_text("file", encoding="utf-8")
        # Create a directory whose name ends with .md (rglob includes it)
        d = tmp_path / "dir.md"
        d.mkdir()
        result = _find_all_pages(tmp_path)
        assert [p.name for p in result] == ["real.md"]
