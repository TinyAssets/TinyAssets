"""Tests for wiki tools in universe_server.py."""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from tinyassets.api.wiki import (
    _WIKI_READ_DEFAULT_MAX_CHARS,
    _extract_keywords,
    _parse_frontmatter,
    _sanitize_slug,
    _wiki_similarity_score,
    wiki,
)
from tinyassets.universe_server import mcp, read_page

# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        content = "---\ntitle: Test Page\ntype: concept\n---\nBody text."
        meta, body = _parse_frontmatter(content)
        assert meta["title"] == "Test Page"
        assert meta["type"] == "concept"
        assert body == "Body text."

    def test_no_frontmatter(self):
        content = "Just a plain body."
        meta, body = _parse_frontmatter(content)
        assert meta == {}
        assert body == "Just a plain body."

    def test_empty_frontmatter(self):
        content = "---\n\n---\nBody."
        meta, body = _parse_frontmatter(content)
        assert meta == {}
        assert body == "Body."


class TestSanitizeSlug:
    def test_basic_slug(self):
        assert _sanitize_slug("My Page.md") == "my-page"

    def test_special_chars(self):
        assert _sanitize_slug("Hello World! (v2)") == "hello-world---v2"

    def test_already_clean(self):
        assert _sanitize_slug("clean-slug") == "clean-slug"


class TestExtractKeywords:
    def test_removes_stop_words(self):
        kw = _extract_keywords("the quick brown fox is very fast")
        assert "the" not in kw
        assert "very" not in kw
        assert "quick" in kw
        assert "brown" in kw
        assert "fox" in kw
        assert "fast" in kw

    def test_removes_short_words(self):
        kw = _extract_keywords("an ox is by me")
        assert len(kw) == 0

    def test_deduplicates(self):
        kw = _extract_keywords("hello hello hello world")
        assert kw == {"hello", "world"}


class TestSimilarityScore:
    def test_identical_content(self):
        body = "This page discusses [[workflow-engine]] and multi-agent patterns."
        meta = {"title": "Test Page"}
        score = _wiki_similarity_score(meta, body, meta, body)
        assert score > 0.5

    def test_different_content(self):
        body_a = "Quantum physics explores wave particle duality."
        body_b = "Baking bread requires flour yeast water salt."
        score = _wiki_similarity_score({}, body_a, {}, body_b)
        assert score < 0.15

    def test_title_bonus(self):
        body = "Some content about patterns."
        meta_a = {"title": "Multi-Agent Patterns"}
        meta_b = {"title": "Multi-Agent"}
        score = _wiki_similarity_score(meta_a, body, meta_b, body)
        # Should get the 0.3 title bonus
        assert score > 0.5


# ---------------------------------------------------------------------------
# Integration tests using a temporary wiki directory
# ---------------------------------------------------------------------------


@pytest.fixture
def wiki_dir(tmp_path, monkeypatch):
    """Create a temporary wiki directory structure."""
    wiki_root = tmp_path / "Wiki"
    wiki_root.mkdir()

    for sub in ["projects", "concepts", "people", "research"]:
        (wiki_root / "pages" / sub).mkdir(parents=True)
        (wiki_root / "drafts" / sub).mkdir(parents=True)
    (wiki_root / "raw").mkdir()

    # Create index
    index_content = (
        "---\ntitle: Index\ntype: index\nupdated: 2026-04-11\n---\n\n"
        "# Wiki Index\n\n"
        "## Projects\n\n"
        "- [[test-project]] -- Test project\n\n"
        "## Concepts\n\n"
        "## People\n\n"
        "## Research\n\n"
    )
    (wiki_root / "index.md").write_text(index_content, encoding="utf-8")

    # Create log
    (wiki_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")

    # Create WIKI.md schema
    (wiki_root / "WIKI.md").write_text("# Wiki Schema\n", encoding="utf-8")

    # Create a sample promoted page
    page_content = (
        "---\ntitle: Test Project\ntype: project\n"
        "created: 2026-04-01\nupdated: 2026-04-11\n"
        "sources: []\ntags: [python]\npath: /test\n"
        "confidence: high\n---\n\n"
        "# Test Project\n\nA test project for unit tests.\n\n"
        "## See Also\n\n- [[workflow-engine]]\n"
    )
    (wiki_root / "pages" / "projects" / "test-project.md").write_text(
        page_content, encoding="utf-8"
    )

    monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(wiki_root))
    return wiki_root


class TestWikiRead:
    def test_read_page(self, wiki_dir):
        result = json.loads(wiki("read", page="test-project"))
        assert result["is_draft"] is False
        assert "Test Project" in result["content"]
        assert result["truncated"] is False

    def test_read_special_index(self, wiki_dir):
        result = json.loads(wiki("read", page="index"))
        assert "Wiki Index" in result["content"]

    def test_read_special_schema(self, wiki_dir):
        result = json.loads(wiki("read", page="schema"))
        assert "Wiki Schema" in result["content"]

    def test_read_not_found(self, wiki_dir):
        result = json.loads(wiki("read", page="nonexistent"))
        assert "error" in result

    def test_read_missing_page_param(self, wiki_dir):
        result = json.loads(wiki("read"))
        assert "error" in result

    def test_read_draft_does_not_duplicate_existing_draft_marker(self, wiki_dir):
        content = "[DRAFT] # Pending Concept\n\nDraft body.\n"
        (wiki_dir / "drafts" / "concepts" / "pending-concept.md").write_text(
            content, encoding="utf-8"
        )

        result = json.loads(wiki("read", page="pending-concept"))

        assert result["is_draft"] is True
        assert result["content"].startswith("[DRAFT] # Pending Concept")
        assert not result["content"].startswith("[DRAFT] [DRAFT]")

    def test_read_large_page_wrapper_exposes_offset_window(self, wiki_dir):
        content = (
            "---\ntitle: Huge Project\ntype: project\n---\n\n"
            + ("a" * 6000)
            + "\nfinal marker\n"
        )
        (wiki_dir / "pages" / "projects" / "huge-project.md").write_text(
            content, encoding="utf-8"
        )

        first = json.loads(wiki("read", page="huge-project", max_chars=1000))
        assert first["truncated"] is True
        assert "WIKI READ TRUNCATED" in first["content"]
        assert first["next_offset"] == 1000

        second = json.loads(
            wiki(
                "read",
                page="huge-project",
                offset=first["next_offset"],
                max_chars=10000,
            )
        )
        assert second["truncated"] is False
        assert "final marker" in second["content"]

    def test_read_wrapper_default_window_handles_medium_document(self, wiki_dir):
        content = (
            "---\ntitle: Medium Project\ntype: project\n---\n\n"
            + ("a" * 80_000)
            + "\nfinal marker\n"
        )
        (wiki_dir / "pages" / "projects" / "medium-project.md").write_text(
            content, encoding="utf-8"
        )

        result = json.loads(wiki("read", page="medium-project"))

        assert result["truncated"] is False
        assert result["read_limit"] == _WIKI_READ_DEFAULT_MAX_CHARS
        assert result["next_offset"] is None
        assert "final marker" in result["content"]


class TestWikiList:
    def test_list_pages(self, wiki_dir):
        result = json.loads(wiki("list"))
        assert result["promoted_count"] >= 1
        titles = [p["title"] for p in result["promoted"]]
        assert "Test Project" in titles

    def test_list_includes_drafts(self, wiki_dir):
        # Write a draft first
        wiki(
            "write",
            category="concepts",
            filename="new-concept",
            content="---\ntitle: New Concept\ntype: concept\n---\nContent.",
        )
        result = json.loads(wiki("list"))
        assert result["drafts_count"] >= 1


class TestWikiSearch:
    def test_search_finds_page(self, wiki_dir):
        result = json.loads(wiki("search", query="test project"))
        assert result["count"] >= 1
        assert any("Test Project" in r["title"] for r in result["results"])

    def test_search_no_results(self, wiki_dir):
        result = json.loads(wiki("search", query="zzzyyyxxx"))
        assert result.get("count", 0) == 0 or len(result.get("results", [])) == 0

    def test_search_missing_query(self, wiki_dir):
        result = json.loads(wiki("search"))
        assert "error" in result

    def test_public_read_page_uses_default_discovery_without_advertising_scope(
        self, wiki_dir
    ):
        for category, audience in (("workflows", "discovery"), ("notes", "coordination")):
            parent = wiki_dir / "pages" / category
            parent.mkdir(parents=True, exist_ok=True)
            (parent / f"wrapper-{audience}.md").write_text(
                f"---\ntitle: Wrapper {audience}\naudience: {audience}\n"
                "---\n\npublic-wrapper-scope canary\n",
                encoding="utf-8",
            )

        result = json.loads(read_page(query="public-wrapper-scope"))
        tools = asyncio.run(mcp.list_tools(run_middleware=False))
        read_page_tool = next(tool for tool in tools if tool.name == "read_page")

        assert result["scope"] == "discovery"
        assert {item["path"] for item in result["results"]} == {
            "pages/workflows/wrapper-discovery.md"
        }
        assert "scope" not in inspect.signature(read_page).parameters
        assert "scope" not in read_page_tool.parameters["properties"]


class TestWikiWrite:
    def test_write_new_draft(self, wiki_dir):
        content = (
            "---\ntitle: New Concept\ntype: concept\n"
            "sources: [test]\n---\n\nSome content about [[test-project]].\n"
        )
        result = json.loads(
            wiki("write", category="concepts", filename="new-concept", content=content)
        )
        assert result["status"] == "drafted"
        assert (wiki_dir / "drafts" / "concepts" / "new-concept.md").exists()

    def test_write_updates_existing_promoted(self, wiki_dir):
        new_content = (
            "---\ntitle: Test Project\ntype: project\n"
            "updated: 2026-04-11\nsources: []\ntags: [python]\n---\n\n"
            "Updated content.\n"
        )
        result = json.loads(
            wiki(
                "write",
                category="projects",
                filename="test-project",
                content=new_content,
            )
        )
        assert result["status"] == "updated"
        actual = (wiki_dir / "pages" / "projects" / "test-project.md").read_text(
            encoding="utf-8"
        )
        assert "Updated content." in actual

    def test_write_accepts_custom_category(self, wiki_dir):
        # Organic growth: a non-seed category is allowed (slugified), not rejected.
        result = json.loads(
            wiki("write", category="lore", filename="test", content="test")
        )
        assert "error" not in result
        assert "lore" in result["path"]

    def test_write_rejects_unsluggable_category(self, wiki_dir):
        result = json.loads(
            wiki("write", category="   ", filename="test", content="test")
        )
        assert "error" in result

    def test_write_missing_params(self, wiki_dir):
        result = json.loads(wiki("write"))
        assert "error" in result

    @pytest.mark.parametrize("category", [
        # Original four.
        "projects", "concepts", "people", "research",
        # 2026-04-13 expansion — stop user-intent content landing in
        # research/ by default. Mirrors wiki-mcp/server.js.
        "recipes", "workflows", "notes", "references", "plans",
    ])
    def test_write_accepts_all_expanded_categories(self, wiki_dir, category):
        """Regression gate for #55: every documented category is
        actually accepted by the write handler."""
        body = (
            "---\ntitle: Test\ntype: note\nsources: []\n---\n\nBody.\n"
        )
        result = json.loads(
            wiki(
                "write",
                category=category,
                filename=f"test-{category}",
                content=body,
            )
        )
        assert result.get("status") in {"drafted", "updated"}, result


class TestWikiLint:
    def test_lint_finds_issues(self, wiki_dir):
        result = json.loads(wiki("lint"))
        # Should find issues (missing wikilink targets, etc.)
        assert isinstance(result.get("issues", []), list)

    def test_lint_detects_orphan(self, wiki_dir):
        # Create an orphan page (not linked from anywhere, not in index)
        content = (
            "---\ntitle: Orphan\ntype: concept\n"
            "confidence: high\nsources: [test]\n---\n\n"
            "An orphan page nobody links to.\n"
        )
        (wiki_dir / "pages" / "concepts" / "orphan-page.md").write_text(
            content, encoding="utf-8"
        )
        result = json.loads(wiki("lint"))
        issues = result.get("issues", [])
        assert any("ORPHAN" in i and "orphan-page" in i for i in issues)

    def test_lint_single_page_excludes_whole_wiki_backlog(self, wiki_dir):
        clean_content = (
            "---\ntitle: Clean Page\ntype: concept\n"
            "confidence: medium\nsources: [test]\n---\n\n"
            "A clean page that links to [[test-project]] and has enough body "
            "content for page-specific linting.\n"
        )
        (wiki_dir / "pages" / "concepts" / "clean-page.md").write_text(
            clean_content, encoding="utf-8"
        )
        (wiki_dir / "index.md").write_text(
            (wiki_dir / "index.md").read_text(encoding="utf-8")
            + "- [[clean-page]] -- Clean Page\n",
            encoding="utf-8",
        )
        (wiki_dir / "pages" / "concepts" / "orphan-page.md").write_text(
            "---\ntitle: Orphan\ntype: concept\nconfidence: high\n"
            "sources: [test]\n---\n\nUnrelated orphan backlog.\n",
            encoding="utf-8",
        )
        wiki(
            "write",
            category="concepts",
            filename="pending-draft",
            content="---\ntitle: Pending Draft\ntype: concept\n---\n\nDraft backlog.",
        )

        result = json.loads(wiki("lint", page="clean-page"))

        assert result["status"] == "healthy"
        assert result["issues"] == []

    def test_lint_single_draft_reports_promotion_blockers_only(self, wiki_dir):
        wiki(
            "write",
            category="notes",
            filename="skinny",
            content="---\ntitle: Skinny\n---\ntoo short\n",
        )
        (wiki_dir / "pages" / "concepts" / "orphan-page.md").write_text(
            "---\ntitle: Orphan\ntype: concept\nconfidence: high\n"
            "sources: [test]\n---\n\nUnrelated orphan backlog.\n",
            encoding="utf-8",
        )

        result = json.loads(wiki("lint", page="skinny"))
        issues = result.get("issues", [])

        assert result["status"] == "issues_found"
        assert any("Missing type" in issue for issue in issues)
        assert any("Body too short" in issue for issue in issues)
        assert not any("orphan-page" in issue for issue in issues)

    def test_lint_accepts_seed_index_as_wikilink_target(self, tmp_path, monkeypatch):
        wiki_root = tmp_path / "FreshWiki"
        monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(wiki_root))
        content = (
            "---\ntitle: First Concept\ntype: concept\n"
            "confidence: medium\nsources: [first-note]\n---\n\n"
            "This first page links to the canonical seed [[index]] while the "
            "fresh wiki has no other promoted content to cross-reference.\n"
        )
        wiki("write", category="concepts", filename="first-concept", content=content)
        # Promote by moving the draft into pages/ directly; the ``promote``
        # action is gone, and lint only reads the promoted page.
        draft = wiki_root / "drafts" / "concepts" / "first-concept.md"
        page = wiki_root / "pages" / "concepts" / "first-concept.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        draft.replace(page)

        result = json.loads(wiki("lint", page="first-concept"))

        assert "MISSING: [[index]]" not in result.get("issues", [])


class TestWikiDispatch:

    def test_wiki_missing_root_auto_scaffolds(self, monkeypatch, tmp_path):
        """Post-Task-#6: a nonexistent wiki root auto-scaffolds on first
        call and returns the empty-wiki list rather than an error.

        Pre-#6 contract was ``{"error": "Wiki not found at ..."}``. The
        droplet-seeding task flipped this so fresh deploys (empty
        ``/data/wiki``) don't face a broken read path — the scaffold
        writes pages/, drafts/, raw/, log/, plus anchor ``index.md`` /
        ``WIKI.md`` / ``log.md`` files.
        """
        root = tmp_path / "nonexistent"
        assert not root.exists()
        monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(root))
        result = json.loads(wiki("list"))
        assert "error" not in result, (
            f"wiki list errored instead of auto-scaffolding: {result!r}"
        )
        # The new contract: list returns page-collection keys; for a
        # freshly-scaffolded empty wiki both are empty lists.
        assert result.get("promoted") == []
        assert result.get("drafts") == []
        assert result.get("promoted_count") == 0
        assert result.get("drafts_count") == 0
        # Scaffold landed on disk.
        assert root.is_dir()
        assert (root / "pages").is_dir()
        assert (root / "drafts").is_dir()
        assert (root / "index.md").is_file()
        assert (root / "WIKI.md").is_file()
        assert (root / "log.md").is_file()


class TestWikiFileBugDispatch:
    """Dispatch-level tests for wiki(action='file_bug', ...).

    Exercises the wiki() router path — not the helper directly — so that
    the dispatch table wiring is independently verified.
    """

    def test_missing_title_returns_error(self, wiki_dir):
        out = json.loads(
            wiki("file_bug", component="extensions.core", severity="major", title="")
        )
        assert "error" in out

    def test_missing_component_returns_error(self, wiki_dir):
        out = json.loads(
            wiki("file_bug", component="", severity="major", title="Some bug")
        )
        assert "error" in out

    def test_invalid_severity_returns_error(self, wiki_dir):
        out = json.loads(
            wiki("file_bug", component="extensions.core", severity="critical-ish", title="t")
        )
        assert "error" in out

    def test_valid_call_returns_bug_id_and_path(self, wiki_dir):
        (wiki_dir / "pages" / "bugs").mkdir(parents=True, exist_ok=True)
        (wiki_dir / "drafts" / "bugs").mkdir(parents=True, exist_ok=True)
        out = json.loads(
            wiki(
                "file_bug",
                component="extensions.patch_branch",
                severity="major",
                title="Widget explodes on save",
                repro="Click save",
                observed="500 error",
                expected="200 ok",
            )
        )
        assert out["status"] == "filed"
        assert "bug_id" in out
        assert out["bug_id"].startswith("BUG-")
        assert "path" in out

    def test_file_bug_flags_patch_request_ghost_risk_attention(self, wiki_dir):
        (wiki_dir / "pages" / "patch-requests").mkdir(parents=True, exist_ok=True)
        (wiki_dir / "drafts" / "patch-requests").mkdir(parents=True, exist_ok=True)

        out = json.loads(
            wiki(
                "file_bug",
                component="community_loop.classifier",
                severity="minor",
                title="Filing-time effort-class prediction circuit-breaker classifier",
                observed=(
                    "Mechanical filing cites MSR prior art with AUC 0.96 on "
                    "33K agent PRs and needs opposite-family checker attention."
                ),
                expected="Carrier attention lands before stall.",
                kind="patch_request",
                tags="mechanical",
            )
        )

        assert out["status"] == "filed"
        effort = out["effort_classification"]
        assert effort["effort_class"] == "ghost-risk"
        assert effort["attention"] == "carrier-review-before-daemon-pickup"
        assert "research_prior_art" in effort["signals"]
        assert out["effort_dispatch_route"]["lane"] == "carrier-attention"
        assert (
            out["effort_dispatch_route"]["attention_family"]
            == "opposite-family-checker"
        )
        body = (wiki_dir / out["path"]).read_text(encoding="utf-8")
        assert "effort_class: ghost-risk" in body
        assert "effort_attention: carrier-review-before-daemon-pickup" in body
        assert "effort_dispatch_lane: carrier-attention" in body
        assert "## Carrier Attention" in body
        assert "Attention family: opposite-family-checker" in body

    def test_file_bug_flags_patch_request_merge_instant(self, wiki_dir):
        (wiki_dir / "pages" / "patch-requests").mkdir(parents=True, exist_ok=True)
        (wiki_dir / "drafts" / "patch-requests").mkdir(parents=True, exist_ok=True)

        out = json.loads(
            wiki(
                "file_bug",
                component="docs",
                severity="cosmetic",
                title="Fix typo in connector docs",
                observed="Mechanical docs-only copy edit with no runtime behavior change.",
                kind="patch_request",
            )
        )

        assert out["status"] == "filed"
        effort = out["effort_classification"]
        assert effort["effort_class"] == "merge-instant"
        assert effort["attention"] == "normal-review-gates"
        assert "mechanical_shape" in effort["signals"]
        assert out["effort_dispatch_route"]["lane"] == "merge-instant-fast-lane"
        assert out["effort_dispatch_route"]["pickup_signal_weight"] > 0.0
        body = (wiki_dir / out["path"]).read_text(encoding="utf-8")
        assert "effort_class: merge-instant" in body
        assert "effort_dispatch_lane: merge-instant-fast-lane" in body

    def test_file_bug_accepts_tags_via_public_wrapper(self, wiki_dir):
        """BUG-040: public wiki wrapper must pass tags through to file_bug."""
        (wiki_dir / "pages" / "bugs").mkdir(parents=True, exist_ok=True)
        (wiki_dir / "drafts" / "bugs").mkdir(parents=True, exist_ok=True)

        out = json.loads(
            wiki(
                "file_bug",
                component="wiki-mcp",
                severity="minor",
                title="Tagged schema regression",
                tags="schema, regression",
                force_new=True,
            )
        )

        assert out["status"] == "filed"
        body = (wiki_dir / out["path"]).read_text(encoding="utf-8")
        tags_line = [ln for ln in body.split("\n") if ln.startswith("tags:")][0]
        assert "schema" in tags_line
        assert "regression" in tags_line

    def test_id_collision_retry_via_dispatch(self, wiki_dir):
        """Collision retry is end-to-end via the wiki() router."""
        from pathlib import Path
        from unittest.mock import patch

        (wiki_dir / "pages" / "bugs").mkdir(parents=True, exist_ok=True)
        (wiki_dir / "drafts" / "bugs").mkdir(parents=True, exist_ok=True)

        from tinyassets.api import wiki as wiki_mod

        real_write = wiki_mod.write_data_path
        first_call = {"fired": False}

        def fake_write(path, data, *args, mode="replace", **kwargs):
            p = Path(path)
            if mode == "exclusive" and "bug-001" in p.name and not first_call["fired"]:
                # A concurrent filer took the id between the scan and the create.
                first_call["fired"] = True
                real_write(path, "", *args, **kwargs)
                raise FileExistsError(path)
            return real_write(path, data, *args, mode=mode, **kwargs)

        with patch("tinyassets.api.wiki.write_data_path", side_effect=fake_write):
            out = json.loads(
                wiki(
                    "file_bug",
                    component="x",
                    severity="minor",
                    title="collision test",
                )
            )
        assert out["status"] == "filed"
        assert out["bug_id"] == "BUG-002"


class TestWikiIsNotAConnectorTool:
    def test_wiki_is_reached_through_the_page_handles_only(self):
        """The `wiki` fat tool is no longer registered (2026-09-30); clients
        read and write pages through read_page / write_page."""
        tool_names = {t.name for t in asyncio.run(mcp.list_tools(run_middleware=False))}
        assert "wiki" not in tool_names
        assert {"read_page", "write_page"} <= tool_names
