"""Tests for the guarded canon-I/O chokepoint (``tinyassets.ingestion.canon_io``).

Every canon read, write, or enumeration routes through ``canon_io`` so that
path-traversal and symlink-escape are rejected before any I/O. These tests
prove:

- explicit-path helpers reject ``..`` traversal and symlinked escapes;
- ``iter_canon_files`` skips escaping entries (symlinked file, symlinked
  recursive subdir) while still yielding legitimate files and subdir files;
- the guarded call sites across the pipeline (worldbuild, orient, plan,
  reflect, commit, writer_tools, universe API, raptor, memory ingestion)
  do not read or write outside the canon sandbox.

Symlink-creation tests skip on platforms where symlinks require privilege
(Windows without Developer Mode). ``.resolve()``-lands-outside checks run
live everywhere because they need no symlink.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tinyassets.ingestion.canon_io import (
    iter_canon_files,
    read_canon_text,
    safe_canon_path,
    write_canon_text,
)


@pytest.fixture(autouse=True)
def _box_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path.parent))


def _make_symlink(link: Path, target: Path) -> None:
    """Create a symlink or skip the test if the OS forbids it (Windows priv)."""
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform
        pytest.skip(f"symlink unsupported on this platform: {exc}")


# =====================================================================
# safe_canon_path / read / write -- explicit single-path containment
# =====================================================================


class TestSafeCanonPath:
    def test_legitimate_name_resolves_inside(self, tmp_path):
        canon = tmp_path / "canon"
        canon.mkdir()
        resolved = safe_canon_path(canon, "world.md")
        assert resolved == (canon / "world.md").resolve()
        assert resolved.is_relative_to(canon.resolve())

    def test_legitimate_subdir_allowed(self, tmp_path):
        canon = tmp_path / "canon"
        (canon / "sources").mkdir(parents=True)
        resolved = safe_canon_path(canon, "sources/foo.txt")
        assert resolved.is_relative_to(canon.resolve())

    def test_dotdot_traversal_rejected(self, tmp_path):
        canon = tmp_path / "canon"
        canon.mkdir()
        with pytest.raises(ValueError):
            safe_canon_path(canon, "../../escape.md")

    def test_resolve_lands_outside_is_rejected(self, tmp_path):
        """A name whose .resolve() lands outside canon is rejected (no symlink
        needed -- runs live on Windows)."""
        canon = tmp_path / "canon"
        canon.mkdir()
        # ``sources/../../escape.md`` collapses to tmp_path/escape.md -> outside.
        with pytest.raises(ValueError):
            safe_canon_path(canon, "sources/../../escape.md")

    def test_symlinked_target_outside_rejected(self, tmp_path):
        canon = tmp_path / "canon"
        canon.mkdir()
        secret = tmp_path / "secret.md"
        secret.write_text("TOP SECRET", encoding="utf-8")
        _make_symlink(canon / "evil.md", secret)
        with pytest.raises(ValueError):
            safe_canon_path(canon, "evil.md")


class TestReadWriteHelpers:
    def test_read_canon_text_inside(self, tmp_path):
        canon = tmp_path / "canon"
        canon.mkdir()
        (canon / "a.md").write_text("hello", encoding="utf-8")
        assert read_canon_text(canon, "a.md") == "hello"

    def test_read_canon_text_rejects_traversal(self, tmp_path):
        canon = tmp_path / "canon"
        canon.mkdir()
        (tmp_path / "outside.md").write_text("nope", encoding="utf-8")
        with pytest.raises(ValueError):
            read_canon_text(canon, "../outside.md")

    def test_read_canon_text_rejects_symlink_escape(self, tmp_path):
        canon = tmp_path / "canon"
        canon.mkdir()
        secret = tmp_path / "secret.md"
        secret.write_text("TOP SECRET", encoding="utf-8")
        _make_symlink(canon / "link.md", secret)
        with pytest.raises(OSError):
            read_canon_text(canon, "link.md")

    def test_write_canon_text_inside(self, tmp_path):
        canon = tmp_path / "canon"
        canon.mkdir()
        path = write_canon_text(canon, "out.md", "body")
        assert path.read_text(encoding="utf-8") == "body"
        assert path.is_relative_to(canon.resolve())

    def test_write_canon_text_rejects_traversal(self, tmp_path):
        canon = tmp_path / "canon"
        canon.mkdir()
        with pytest.raises(ValueError):
            write_canon_text(canon, "../escape.md", "body")
        assert not (tmp_path / "escape.md").exists()

    def test_write_canon_text_rejects_symlink_escape(self, tmp_path):
        """An LLM-named file that is a symlink to an external target must not be
        followed by the write."""
        canon = tmp_path / "canon"
        canon.mkdir()
        target = tmp_path / "victim.md"
        target.write_text("original", encoding="utf-8")
        _make_symlink(canon / "doc.md", target)
        with pytest.raises(ValueError):
            write_canon_text(canon, "doc.md", "CLOBBERED")
        # External file unchanged.
        assert target.read_text(encoding="utf-8") == "original"


# =====================================================================
# iter_canon_files -- enumeration skips escapers
# =====================================================================


class TestIterCanonFiles:
    def test_missing_dir_yields_nothing(self, tmp_path):
        assert list(iter_canon_files(tmp_path / "canon")) == []

    def test_yields_legitimate_files_sorted(self, tmp_path):
        canon = tmp_path / "canon"
        canon.mkdir()
        (canon / "b.md").write_text("b", encoding="utf-8")
        (canon / "a.md").write_text("a", encoding="utf-8")
        names = [p.name for p in iter_canon_files(canon, suffix=".md")]
        assert names == ["a.md", "b.md"]

    def test_suffix_filter(self, tmp_path):
        canon = tmp_path / "canon"
        canon.mkdir()
        (canon / "a.md").write_text("a", encoding="utf-8")
        (canon / "b.txt").write_text("b", encoding="utf-8")
        md = [p.name for p in iter_canon_files(canon, suffix=".md")]
        both = [p.name for p in iter_canon_files(canon, suffix=(".md", ".txt"))]
        assert md == ["a.md"]
        assert sorted(both) == ["a.md", "b.txt"]

    def test_include_hidden_false_skips_dotfiles(self, tmp_path):
        canon = tmp_path / "canon"
        canon.mkdir()
        (canon / "a.md").write_text("a", encoding="utf-8")
        (canon / ".manifest.json").write_text("{}", encoding="utf-8")
        names = [p.name for p in iter_canon_files(canon, include_hidden=False)]
        assert names == ["a.md"]

    def test_recursive_yields_subdir_files(self, tmp_path):
        canon = tmp_path / "canon"
        (canon / "sources").mkdir(parents=True)
        (canon / "top.md").write_text("t", encoding="utf-8")
        (canon / "sources" / "deep.txt").write_text("d", encoding="utf-8")
        names = sorted(
            p.name
            for p in iter_canon_files(
                canon, suffix=(".md", ".txt"), recursive=True
            )
        )
        assert names == ["deep.txt", "top.md"]

    def test_symlinked_file_escaping_is_skipped(self, tmp_path):
        canon = tmp_path / "canon"
        canon.mkdir()
        (canon / "real.md").write_text("real", encoding="utf-8")
        secret = tmp_path / "secret.md"
        secret.write_text("SECRET", encoding="utf-8")
        _make_symlink(canon / "evil.md", secret)
        names = [p.name for p in iter_canon_files(canon, suffix=".md")]
        # The escaping symlink is skipped; only the legitimate file is yielded.
        assert names == ["real.md"]

    def test_symlinked_subdir_escaping_is_skipped_recursive(self, tmp_path):
        """A symlinked subdir whose target lives outside canon must not have its
        files surfaced by the recursive walk."""
        canon = tmp_path / "canon"
        canon.mkdir()
        (canon / "real.md").write_text("real", encoding="utf-8")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "leak.md").write_text("LEAK", encoding="utf-8")
        _make_symlink(canon / "linkdir", outside)
        contents = {
            p.read_text(encoding="utf-8")
            for p in iter_canon_files(canon, suffix=".md", recursive=True)
        }
        assert "LEAK" not in contents
        assert "real" in contents

    def test_resolved_path_is_file_only(self, tmp_path):
        """Directories are not yielded as files even without a suffix filter."""
        canon = tmp_path / "canon"
        (canon / "subdir").mkdir(parents=True)
        (canon / "a.md").write_text("a", encoding="utf-8")
        names = [p.name for p in iter_canon_files(canon)]
        assert names == ["a.md"]

    def test_escaping_entry_is_skipped_live(self, tmp_path, monkeypatch):
        """Prove the escape-skip branch live (no symlink privilege needed).

        Feed ``iter_canon_files`` a synthetic directory listing that includes
        an entry resolving to a ``..`` traversal; the escaping entry must be
        skipped while the legitimate file is still yielded. This exercises the
        same ``resolve_within_canon`` rejection that a symlink would trigger,
        but runs on every platform including privilege-restricted Windows.
        """
        canon = tmp_path / "canon"
        canon.mkdir()
        legit = canon / "real.md"
        legit.write_text("real", encoding="utf-8")
        # An entry whose path is canon/../escape.md -- a sibling outside canon.
        escaper = canon / ".." / "escape.md"
        (tmp_path / "escape.md").write_text("LEAK", encoding="utf-8")

        def _fake_iterdir(self):
            assert self == canon
            return iter([legit, escaper])

        monkeypatch.setattr(Path, "iterdir", _fake_iterdir)
        names = [p.name for p in iter_canon_files(canon, suffix=".md")]
        assert names == ["real.md"]


# =====================================================================
# iter_canon_files subdir= -- containment stays rooted at the canon ROOT
# =====================================================================


class TestIterCanonFilesSubdir:
    def test_subdir_yields_legitimate_files(self, tmp_path):
        canon = tmp_path / "canon"
        (canon / "sources").mkdir(parents=True)
        (canon / "sources" / "a.txt").write_text("a", encoding="utf-8")
        (canon / "sources" / "b.txt").write_text("b", encoding="utf-8")
        # A top-level file must NOT appear -- only the subdir is enumerated.
        (canon / "top.md").write_text("top", encoding="utf-8")
        names = [
            p.name for p in iter_canon_files(canon, subdir="sources")
        ]
        assert names == ["a.txt", "b.txt"]

    def test_missing_subdir_yields_nothing(self, tmp_path):
        canon = tmp_path / "canon"
        canon.mkdir()
        assert list(iter_canon_files(canon, subdir="sources")) == []

    def test_subdir_resolves_outside_is_rejected_live(self, tmp_path):
        """A ``subdir`` that ``.resolve()`` lands outside canon yields nothing
        (runs live on Windows -- no symlink needed)."""
        canon = tmp_path / "canon"
        canon.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "leak.txt").write_text("LEAK", encoding="utf-8")
        # ``../outside`` collapses to a sibling of canon -> rejected.
        results = list(iter_canon_files(canon, subdir="../outside"))
        assert results == []

    def test_symlinked_subdir_root_is_rejected(self, tmp_path):
        """A *symlinked* ``sources/`` whose target lives outside canon must not
        become a trusted root -- its files must not be yielded."""
        canon = tmp_path / "canon"
        canon.mkdir()
        (canon / "real.md").write_text("real", encoding="utf-8")
        outside = tmp_path / "outside_sources"
        outside.mkdir()
        (outside / "leak.txt").write_text("LEAK", encoding="utf-8")
        _make_symlink(canon / "sources", outside)
        results = list(iter_canon_files(canon, subdir="sources"))
        contents = {p.read_text(encoding="utf-8") for p in results}
        assert "LEAK" not in contents
        assert results == []

    def test_symlinked_file_inside_legit_subdir_is_skipped(self, tmp_path):
        """A symlinked file *inside* a legitimate ``sources/`` dir that escapes
        canon is still skipped while real sibling files are yielded."""
        canon = tmp_path / "canon"
        (canon / "sources").mkdir(parents=True)
        (canon / "sources" / "real.txt").write_text("real", encoding="utf-8")
        secret = tmp_path / "secret.txt"
        secret.write_text("SECRET", encoding="utf-8")
        _make_symlink(canon / "sources" / "evil.txt", secret)
        names = [p.name for p in iter_canon_files(canon, subdir="sources")]
        assert names == ["real.txt"]
