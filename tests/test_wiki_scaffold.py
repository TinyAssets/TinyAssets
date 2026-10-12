"""Tests for Task #6 — `_ensure_wiki_scaffold()` auto-seeds an empty
wiki root so post-scrub deploys don't error on read/list/search/lint.

Covers:
- Cold start: fresh empty directory gets the canonical tree + anchors.
- Idempotency: re-running is a no-op (no overwrite, no error).
- Pre-existing anchor files are preserved byte-for-byte.
- Pre-existing custom subdirs are preserved (scaffold only creates,
  never deletes).
"""

from __future__ import annotations

import pytest

from tinyassets.api.wiki import (
    _WIKI_CATEGORIES,
    _ensure_wiki_scaffold,
)


@pytest.mark.role_split
def test_fresh_volume_scaffolds_the_platform_wiki(tmp_path, monkeypatch):
    """A non-dot child of the data dir is a command center. The commons is not."""
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TINYASSETS_WIKI_PATH", raising=False)
    from tinyassets.storage import wiki_path

    root = wiki_path()
    assert root == (tmp_path / ".wiki").resolve()
    _ensure_wiki_scaffold(root)
    assert (tmp_path / ".wiki" / "index.md").is_file()
    assert not (tmp_path / "wiki").exists()


@pytest.fixture
def wiki_root(tmp_path):
    return tmp_path / "wiki"


class TestScaffoldColdStart:
    def test_creates_full_category_tree(self, wiki_root):
        assert not wiki_root.exists()
        _ensure_wiki_scaffold(wiki_root)
        assert wiki_root.is_dir()
        for cat in _WIKI_CATEGORIES:
            assert (wiki_root / "pages" / cat).is_dir(), (
                f"pages/{cat} missing"
            )
            assert (wiki_root / "drafts" / cat).is_dir(), (
                f"drafts/{cat} missing"
            )
        assert (wiki_root / "log").is_dir()
        assert (wiki_root / "raw").is_dir()

    def test_creates_anchor_files(self, wiki_root):
        _ensure_wiki_scaffold(wiki_root)
        for anchor in ("index.md", "WIKI.md", "log.md"):
            path = wiki_root / anchor
            assert path.exists(), f"{anchor} missing"
            body = path.read_text(encoding="utf-8")
            assert len(body) > 0, f"{anchor} is empty"
        # Index should mention the daemon + seeding context so chatbot
        # reading it orients correctly.
        index_body = (wiki_root / "index.md").read_text(encoding="utf-8")
        assert "seeded" in index_body.lower()


class TestScaffoldIdempotent:
    def test_twice_is_no_op(self, wiki_root):
        _ensure_wiki_scaffold(wiki_root)
        index_mtime_1 = (wiki_root / "index.md").stat().st_mtime_ns
        # Second call must not re-write anchors.
        _ensure_wiki_scaffold(wiki_root)
        index_mtime_2 = (wiki_root / "index.md").stat().st_mtime_ns
        assert index_mtime_1 == index_mtime_2, (
            "scaffold overwrote an existing index.md (not idempotent)"
        )


class TestScaffoldPreservesExisting:
    def test_preserves_user_anchor_content(self, wiki_root):
        wiki_root.mkdir(parents=True)
        custom_index = "# My custom wiki\n\nHand-written.\n"
        (wiki_root / "index.md").write_text(custom_index, encoding="utf-8")
        _ensure_wiki_scaffold(wiki_root)
        assert (wiki_root / "index.md").read_text(encoding="utf-8") == (
            custom_index
        )

    def test_preserves_user_custom_subdir(self, wiki_root):
        """Scaffold only creates — never deletes or clears."""
        wiki_root.mkdir(parents=True)
        custom = wiki_root / "pages" / "bugs" / "BUG-042-real.md"
        custom.parent.mkdir(parents=True, exist_ok=True)
        custom.write_text("pre-existing bug", encoding="utf-8")
        # Unknown top-level subdir — scaffold must leave alone.
        (wiki_root / "custom-folder").mkdir()
        (wiki_root / "custom-folder" / "note.md").write_text(
            "custom content", encoding="utf-8"
        )

        _ensure_wiki_scaffold(wiki_root)

        assert custom.exists()
        assert custom.read_text(encoding="utf-8") == "pre-existing bug"
        assert (wiki_root / "custom-folder" / "note.md").exists()
        assert (wiki_root / "custom-folder" / "note.md").read_text(
            encoding="utf-8"
        ) == "custom content"
