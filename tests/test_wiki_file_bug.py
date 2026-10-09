"""Tests for Task #3 — wiki `file_bug` action + BUG-NNN allocator.

Covers `_wiki_file_bug` helper, `_next_bug_id` allocator, `_slugify_title`
filesystem-safe slug, `_render_bug_markdown` frontmatter shape, and the
end-to-end `wiki(action="file_bug", ...)` dispatch.

The module used to skip itself unless these symbols existed on
`tinyassets.universe_server`. They moved to `tinyassets.api.wiki`, so the
guard read "not landed yet" for months while all of this went unexecuted.
The imports below now fail loudly instead: a missing symbol is a red test,
never a skipped module.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tinyassets.api.wiki import (
    _next_bug_id,
    _render_bug_markdown,
    _slugify_title,
    _wiki_file_bug,
    wiki,
)


@pytest.fixture
def wiki_dir(tmp_path, monkeypatch):
    """Temporary wiki tree with a `bugs` category pre-created."""
    wiki_root = tmp_path / "Wiki"
    wiki_root.mkdir()
    for sub in ["projects", "concepts", "bugs"]:
        (wiki_root / "pages" / sub).mkdir(parents=True)
        (wiki_root / "drafts" / sub).mkdir(parents=True)
    (wiki_root / "raw").mkdir()
    (wiki_root / "index.md").write_text(
        "---\ntitle: Index\ntype: index\n---\n# Wiki\n", encoding="utf-8"
    )
    (wiki_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    (wiki_root / "WIKI.md").write_text("# Wiki Schema\n", encoding="utf-8")
    monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(wiki_root))
    return wiki_root


class TestNextBugId:
    def test_empty_dir_returns_bug_001(self, wiki_dir):
        bugs_dir = wiki_dir / "pages" / "bugs"
        assert _next_bug_id(bugs_dir) == "BUG-001"

    def test_existing_bug_007_returns_bug_008(self, wiki_dir):
        bugs_dir = wiki_dir / "pages" / "bugs"
        (bugs_dir / "BUG-007-example.md").write_text("x", encoding="utf-8")
        assert _next_bug_id(bugs_dir) == "BUG-008"

    def test_non_integer_id_ignored(self, wiki_dir):
        bugs_dir = wiki_dir / "pages" / "bugs"
        (bugs_dir / "BUG-xyz-invalid.md").write_text("x", encoding="utf-8")
        (bugs_dir / "BUG-005-real.md").write_text("x", encoding="utf-8")
        assert _next_bug_id(bugs_dir) == "BUG-006"

    def test_scans_drafts_too(self, wiki_dir):
        (wiki_dir / "pages" / "bugs" / "BUG-001-p.md").write_text(
            "x", encoding="utf-8"
        )
        (wiki_dir / "drafts" / "bugs" / "BUG-002-d.md").write_text(
            "x", encoding="utf-8"
        )
        assert _next_bug_id(wiki_dir / "pages" / "bugs") == "BUG-003"


class TestSlugifyTitle:
    def test_basic(self):
        assert _slugify_title("My Bug Report") == "my-bug-report"

    def test_special_chars(self):
        assert _slugify_title("foo/bar:baz (v2)") == "foo-bar-baz-v2"

    def test_truncation_at_60(self):
        long = "a" * 100
        assert len(_slugify_title(long, max_len=60)) == 60

    def test_truncates_at_word_boundary(self):
        title = "Wiki slug generation truncates mid word instead of at word boundary"
        # The 55-char cut lands inside "word"; the slug backs off to the last
        # whole word ("at") rather than keeping the fragment "w".
        assert _slugify_title(title, max_len=55) == (
            "wiki-slug-generation-truncates-mid-word-instead-of-at"
        )

    def test_long_first_token_falls_back_to_hard_cut(self):
        title = "supercalifragilisticexpialidocious trailing words"
        assert _slugify_title(title, max_len=12) == "supercalifra"

    def test_empty_returns_untitled(self):
        assert _slugify_title("") == "untitled"
        assert _slugify_title("!!!") == "untitled"


class TestRenderBugMarkdown:
    def test_all_frontmatter_fields_present(self):
        md = _render_bug_markdown(
            bug_id="BUG-042",
            title="Test title",
            component="extensions.patch_branch",
            severity="major",
            repro="call X",
            observed="got Y",
            expected="wanted Z",
            workaround="use W",
            first_seen_date="2026-04-20",
        )
        assert "id: BUG-042" in md
        assert "title: Test title" in md
        assert "component: extensions.patch_branch" in md
        assert "severity: major" in md
        assert "status: open" in md
        assert "## What happened\n\ngot Y" in md
        assert "## Repro\n\ncall X" in md
        assert "## Workaround\n\nuse W" in md

    def test_empty_optionals_render_placeholders(self):
        md = _render_bug_markdown(
            bug_id="BUG-001",
            title="x",
            component="y",
            severity="low",
            repro="",
            observed="",
            expected="",
            workaround="",
            first_seen_date="2026-04-20",
        )
        assert "_not specified_" in md
        assert "_none_" in md


class TestFileBugValidation:
    def test_missing_title_rejected(self, wiki_dir):
        out = json.loads(
            _wiki_file_bug(component="x", severity="major", title="")
        )
        assert "error" in out

    def test_missing_component_rejected(self, wiki_dir):
        out = json.loads(
            _wiki_file_bug(component="", severity="major", title="t")
        )
        assert "error" in out

    def test_missing_severity_rejected(self, wiki_dir):
        out = json.loads(
            _wiki_file_bug(component="x", severity="", title="t")
        )
        assert "error" in out

    def test_invalid_severity_rejected(self, wiki_dir):
        out = json.loads(
            _wiki_file_bug(component="x", severity="wombat", title="t")
        )
        assert "error" in out


class TestFileBugWrites:
    def test_happy_path_creates_page(self, wiki_dir):
        out = json.loads(
            _wiki_file_bug(
                component="extensions.patch_branch",
                severity="major",
                title="First bug",
                repro="call X",
                observed="Y",
                expected="Z",
                workaround="W",
            )
        )
        assert out["status"] == "filed"
        assert out["bug_id"] == "BUG-001"
        path = wiki_dir / out["path"]
        assert path.exists()
        body = path.read_text(encoding="utf-8")
        assert "BUG-001" in body
        assert "First bug" in body

    def test_log_appended(self, wiki_dir):
        _wiki_file_bug(
            component="x",
            severity="minor",
            title="logged",
        )
        log = (wiki_dir / "log.md").read_text(encoding="utf-8")
        assert "file_bug" in log
        assert "BUG-001" in log


class TestFileBugCollisionRetry:
    def test_collision_retries_and_advances_id(self, wiki_dir):
        # Pre-seed BUG-001 so _next_bug_id first returns BUG-002.
        (wiki_dir / "pages" / "bugs" / "BUG-001-seed.md").write_text(
            "x", encoding="utf-8"
        )
        from tinyassets.api import wiki as wiki_mod

        real_write = wiki_mod.write_data_path
        first_call = {"fired": False}

        def fake_write(path, data, *args, mode="replace", **kwargs):
            p = Path(path)
            if (
                mode == "exclusive"
                and "bug-002" in p.name.lower()
                and not first_call["fired"]
            ):
                # A concurrent filer took the id between the scan and the create.
                first_call["fired"] = True
                real_write(path, "", *args, **kwargs)
                raise FileExistsError(path)
            return real_write(path, data, *args, mode=mode, **kwargs)

        with patch("tinyassets.api.wiki.write_data_path", side_effect=fake_write):
            out = json.loads(
                _wiki_file_bug(
                    component="x", severity="minor", title="racy"
                )
            )
        assert out["status"] == "filed"
        assert out["bug_id"] == "BUG-003"


class TestFileBugViaWikiDispatch:
    def test_dispatch_routes_file_bug(self, wiki_dir):
        out = json.loads(
            wiki(
                action="file_bug",
                component="extensions.patch_branch",
                severity="major",
                title="Dispatched",
                repro="r",
                observed="o",
                expected="e",
            )
        )
        assert out["status"] == "filed"
        assert out["bug_id"] == "BUG-001"

    def test_bugs_in_valid_categories(self):
        """Smoke test that 'bugs' is registered via patch (a)."""
        from tinyassets.api.wiki import _WIKI_CATEGORIES
        assert "bugs" in _WIKI_CATEGORIES


class TestFileBugKindField:
    """Tests for the optional `kind` field (bug | feature | design)."""

    def test_no_kind_defaults_to_bug(self, wiki_dir):
        out = json.loads(
            _wiki_file_bug(component="x", severity="minor", title="t")
        )
        assert out["kind"] == "bug"

    def test_kind_feature_annotates_frontmatter(self, wiki_dir):
        out = json.loads(
            _wiki_file_bug(
                component="x", severity="minor", title="Add feature Y",
                kind="feature",
            )
        )
        assert out["status"] == "filed"
        assert out["kind"] == "feature"
        path = wiki_dir / out["path"]
        body = path.read_text(encoding="utf-8")
        assert "kind: feature" in body
        assert "feature" in body.split("tags:")[1].split("\n")[0]

    def test_kind_design_annotates_frontmatter(self, wiki_dir):
        out = json.loads(
            _wiki_file_bug(
                component="x", severity="minor", title="Design proposal Z",
                kind="design",
            )
        )
        assert out["kind"] == "design"
        path = wiki_dir / out["path"]
        body = path.read_text(encoding="utf-8")
        assert "kind: design" in body

    def test_invalid_kind_rejected(self, wiki_dir):
        out = json.loads(
            _wiki_file_bug(
                component="x", severity="minor", title="t",
                kind="banana",
            )
        )
        assert "error" in out
        assert "banana" in out["error"]

    def test_missing_kind_reads_as_bug(self, wiki_dir):
        """No kind kwarg = kind defaults to 'bug' (backward-compat)."""
        out = json.loads(
            _wiki_file_bug(component="x", severity="minor", title="legacy bug")
        )
        assert out["kind"] == "bug"


class TestFileBugTagsField:
    """Tests for the optional `tags` free-form labels field."""

    def test_tags_appear_in_frontmatter(self, wiki_dir):
        out = json.loads(
            _wiki_file_bug(
                component="x", severity="minor", title="t",
                tags="ux,performance",
            )
        )
        assert out["status"] == "filed"
        path = wiki_dir / out["path"]
        body = path.read_text(encoding="utf-8")
        tags_line = [ln for ln in body.split("\n") if ln.startswith("tags:")][0]
        assert "ux" in tags_line
        assert "performance" in tags_line

    def test_empty_tags_does_not_break(self, wiki_dir):
        out = json.loads(
            _wiki_file_bug(
                component="x", severity="minor", title="t",
                tags="",
            )
        )
        assert out["status"] == "filed"


def test_render_bug_markdown_kind_and_extra_tags():
    """Direct unit test on _render_bug_markdown with kind + extra_tags."""
    md = _render_bug_markdown(
        bug_id="BUG-001",
        title="Feature req",
        component="extensions",
        severity="minor",
        repro="",
        observed="",
        expected="wanted new thing",
        workaround="",
        first_seen_date="2026-04-24",
        kind="feature",
        extra_tags=["ux", "roadmap"],
    )
    assert "kind: feature" in md
    tags_line = [ln for ln in md.split("\n") if ln.startswith("tags:")][0]
    assert "feature" in tags_line
    assert "ux" in tags_line
    assert "roadmap" in tags_line


# ── BUG-028: slug-case roundtrip ─────────────────────────────────────────────
# file_bug must create lowercase filenames so that wiki action=write can
# resolve the same slug without a case mismatch (BUG-001-... vs bug-001-...).


class TestBug028SlugCaseRoundtrip:
    def test_file_bug_creates_lowercase_filename(self, wiki_dir):
        """file_bug must produce an all-lowercase filename (no BUG-NNN prefix)."""
        result = json.loads(wiki(
            action="file_bug",
            title="Slug Case Test",
            component="wiki",
            severity="minor",
        ))
        assert result.get("status") == "filed"
        path_str = result["path"]
        filename = path_str.split("/")[-1]
        assert filename == filename.lower(), (
            f"file_bug produced a non-lowercase filename: {filename!r}. "
            "BUG-028: write action uses _sanitize_slug which lowercases; "
            "filenames must match."
        )

    def test_file_bug_then_write_roundtrips(self, wiki_dir):
        """file_bug followed by wiki write to the same slug updates, not duplicates."""
        filed = json.loads(wiki(
            action="file_bug",
            title="Roundtrip Test Bug",
            component="wiki",
            severity="minor",
            observed="broken",
            expected="works",
        ))
        assert filed.get("status") == "filed"
        path_str = filed["path"]
        filename = path_str.split("/")[-1]  # e.g. bug-001-roundtrip-test-bug.md

        # Write an update to the same file using the same filename.
        updated_content = "---\nid: BUG-001\ntitle: Updated\n---\n# Updated\n"
        write_result = json.loads(wiki(
            action="write",
            category="bugs",
            filename=filename,
            content=updated_content,
        ))
        assert write_result.get("status") in ("updated", "drafted", "draft-update"), (
            f"Expected an update, got: {write_result}"
        )
        # Verify only one file exists for this bug (no duplicate).
        bugs_dir = wiki_dir / "pages" / "bugs"
        bug_files = list(bugs_dir.glob("*.md"))
        assert len(bug_files) == 1, (
            f"Expected exactly 1 bug file, got {[f.name for f in bug_files]}. "
            "BUG-028: write must update in-place, not create a duplicate."
        )

    def test_write_updates_mixed_case_bug_slug_in_place(self, wiki_dir):
        """Lowercase bug writes must resolve to the canonical mixed-case page path."""
        canonical = wiki_dir / "pages" / "bugs" / "BUG-001-mixed-case-bug.md"
        canonical.write_text("old body\n", encoding="utf-8")

        updated_content = "---\nid: BUG-001\ntitle: Mixed Case Updated\n---\n# Updated\n"
        write_result = json.loads(wiki(
            action="write",
            category="bugs",
            filename="bug-001-mixed-case-bug.md",
            content=updated_content,
        ))
        assert write_result == {
            "path": "pages/bugs/BUG-001-mixed-case-bug.md",
            "status": "updated",
            "note": "Updated existing promoted page in-place.",
        }
        assert canonical.read_text(encoding="utf-8") == updated_content
        assert not (wiki_dir / "drafts" / "bugs" / "bug-001-mixed-case-bug.md").exists()
        bug_files = list((wiki_dir / "pages" / "bugs").glob("*.md"))
        assert [path.name for path in bug_files] == ["BUG-001-mixed-case-bug.md"]

    def test_next_bug_id_finds_lowercase_files(self, wiki_dir):
        """_next_bug_id must find lowercase bug-NNN-... files written by file_bug."""
        bugs_dir = wiki_dir / "pages" / "bugs"
        (bugs_dir / "bug-003-some-title.md").write_text("x", encoding="utf-8")
        assert _next_bug_id(bugs_dir) == "BUG-004"

    def test_next_bug_id_finds_uppercase_files_too(self, wiki_dir):
        """_next_bug_id must also find legacy uppercase BUG-NNN-... files."""
        bugs_dir = wiki_dir / "pages" / "bugs"
        (bugs_dir / "BUG-007-legacy.md").write_text("x", encoding="utf-8")
        assert _next_bug_id(bugs_dir) == "BUG-008"


class TestFileBugKindRouting:
    """Task #52 — kind=feature → pages/feature-requests/FEAT-NNN,
    kind=design → pages/design-proposals/DESIGN-NNN, kind=bug stays in
    pages/bugs/BUG-NNN. Each prefix has its own counter."""

    def test_kind_feature_lands_in_feature_requests_dir(self, wiki_dir):
        out = json.loads(
            _wiki_file_bug(
                component="x", severity="minor", title="Add feature Y",
                kind="feature",
            )
        )
        assert out["status"] == "filed"
        assert out["kind"] == "feature"
        assert out["bug_id"].startswith("FEAT-")
        assert out["path"].startswith("pages/feature-requests/")
        feat_dir = wiki_dir / "pages" / "feature-requests"
        assert feat_dir.is_dir()
        assert any(p.stem.startswith("feat-") for p in feat_dir.glob("*.md"))

    def test_kind_design_lands_in_design_proposals_dir(self, wiki_dir):
        out = json.loads(
            _wiki_file_bug(
                component="x", severity="minor", title="Design proposal Z",
                kind="design",
            )
        )
        assert out["status"] == "filed"
        assert out["kind"] == "design"
        assert out["bug_id"].startswith("DESIGN-")
        assert out["path"].startswith("pages/design-proposals/")
        design_dir = wiki_dir / "pages" / "design-proposals"
        assert design_dir.is_dir()
        assert any(p.stem.startswith("design-") for p in design_dir.glob("*.md"))

    def test_kind_bug_still_lands_in_bugs_dir(self, wiki_dir):
        out = json.loads(
            _wiki_file_bug(
                component="x", severity="minor", title="A real bug",
            )
        )
        assert out["status"] == "filed"
        assert out["kind"] == "bug"
        assert out["bug_id"].startswith("BUG-")
        assert out["path"].startswith("pages/bugs/")

    def test_kind_patch_request_lands_in_patch_requests_dir(self, wiki_dir):
        """Task #70 — patch_request kind routes to PR-NNN in pages/patch-requests/.
        Used by Task #55 external-PR bridge to file inbound patch submissions."""
        out = json.loads(
            _wiki_file_bug(
                component="x", severity="minor",
                title="Inbound PR from external contributor",
                kind="patch_request",
            )
        )
        assert out["status"] == "filed"
        assert out["kind"] == "patch_request"
        assert out["bug_id"].startswith("PR-")
        assert out["path"].startswith("pages/patch-requests/")
        pr_dir = wiki_dir / "pages" / "patch-requests"
        assert pr_dir.is_dir()
        patch_files = [p for p in pr_dir.glob("*.md") if p.stem.startswith("pr-")]
        assert len(patch_files) == 1
        body = patch_files[0].read_text(encoding="utf-8")
        assert "type: patch_request" in body
        assert "kind: patch_request" in body

    def test_patch_request_dedup_uses_patch_request_ids(self, wiki_dir):
        first = json.loads(_wiki_file_bug(
            component="x", severity="minor",
            title="Patch connector guidance for community requests",
            observed="Guidance frames platform changes as bugs only",
            kind="patch_request",
        ))
        second = json.loads(_wiki_file_bug(
            component="x", severity="minor",
            title="Patch connector guidance for community requests",
            observed="Guidance frames platform changes as bugs only",
            kind="patch_request",
        ))
        assert first["status"] == "filed"
        assert second["status"] == "similar_found"
        assert second["similar"][0]["bug_id"].startswith("PR-")

    def test_independent_id_counters_per_kind(self, wiki_dir):
        """BUG-001 / FEAT-001 / DESIGN-001 must coexist — independent sequences."""
        b = json.loads(_wiki_file_bug(
            component="x", severity="minor", title="bug one",
        ))
        f = json.loads(_wiki_file_bug(
            component="x", severity="minor", title="feat one", kind="feature",
        ))
        d = json.loads(_wiki_file_bug(
            component="x", severity="minor", title="design one", kind="design",
        ))
        assert b["bug_id"] == "BUG-001"
        assert f["bug_id"] == "FEAT-001"
        assert d["bug_id"] == "DESIGN-001"

    def test_per_kind_counter_increments_independently(self, wiki_dir):
        """Filing 2 features then 1 bug: features get FEAT-001/FEAT-002, bug gets BUG-001."""
        f1 = json.loads(_wiki_file_bug(
            component="x", severity="minor", title="feat one", kind="feature",
        ))
        f2 = json.loads(_wiki_file_bug(
            component="x", severity="minor", title="feat two completely different",
            kind="feature",
        ))
        b1 = json.loads(_wiki_file_bug(
            component="x", severity="minor", title="some bug",
        ))
        assert f1["bug_id"] == "FEAT-001"
        assert f2["bug_id"] == "FEAT-002"
        assert b1["bug_id"] == "BUG-001"

    def test_dedup_is_per_kind_not_cross_kind(self, wiki_dir):
        """Same title may be filed as both bug AND feature — different surfaces."""
        b = json.loads(_wiki_file_bug(
            component="x", severity="minor",
            title="Database connection pool exhaustion under load",
            observed="Connections timeout after 5 seconds when pool is exhausted",
        ))
        # Same title as feature should NOT be flagged as similar (different kind dir)
        f = json.loads(_wiki_file_bug(
            component="x", severity="minor",
            title="Database connection pool exhaustion under load",
            observed="Connections timeout after 5 seconds when pool is exhausted",
            kind="feature",
        ))
        assert b["status"] == "filed"
        assert b["bug_id"].startswith("BUG-")
        assert f["status"] == "filed"
        assert f["bug_id"].startswith("FEAT-")

    def test_invalid_kind_still_rejected(self, wiki_dir):
        """Invalid kinds are rejected before routing kicks in (existing contract)."""
        out = json.loads(
            _wiki_file_bug(
                component="x", severity="minor", title="t", kind="banana",
            )
        )
        assert "error" in out
        assert "banana" in out["error"]
