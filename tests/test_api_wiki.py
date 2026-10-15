"""Task #9 — direct tests for `tinyassets.api.wiki` after decomp Step 2.

The legacy test files (`test_wiki_*.py`) import from `tinyassets.universe_server`
to cover chatbot-facing MCP wrappers. This file exercises `tinyassets.api.wiki`
directly to lock in the canonical implementation surface.
"""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from tinyassets.api import visibility
from tinyassets.api import wiki as wiki_mod
from tinyassets.api.wiki import (
    _BUG_DEDUP_THRESHOLD,
    _BUGS_CATEGORY,
    _KIND_ROUTING,
    _UNSUPPORTED_FILE_BUG_BODY_KWARGS,
    _VALID_BUG_KINDS,
    _VALID_SEVERITIES,
    _WIKI_CATEGORIES,
    _WIKI_READ_DEFAULT_MAX_CHARS,
    _WIKI_READ_MAX_CHARS,
    _bug_token_set,
    _ensure_wiki_scaffold,
    _extract_keywords,
    _jaccard,
    _next_id,
    _parse_frontmatter,
    _sanitize_slug,
    _slugify_title,
    _wiki_file_bug,
    _wiki_similarity_score,
    wiki,
)
from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import DevAuthProvider, Identity
from tinyassets.daemon_server import ensure_universe_registered, grant_universe_access

# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def wiki_env(tmp_path, monkeypatch):
    """Isolated wiki root, scaffold pre-built."""
    wiki_root = tmp_path / "wiki"
    monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(wiki_root))
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _ensure_wiki_scaffold(wiki_root)
    # Directories live in the owner's box: a write through it makes a page's
    # parents. Tests that seed a page by its host path make that parent here.
    (wiki_root / "pages" / "notes").mkdir(parents=True, exist_ok=True)
    return wiki_root


@pytest.fixture(autouse=True)
def _reset_auth_context():
    set_provider(DevAuthProvider())
    auth_middleware("dev")
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


class _StaticAuthProvider(DevAuthProvider):
    def __init__(self, identity: Identity) -> None:
        self.identity = identity

    def resolve_token(self, token: str) -> Identity | None:
        return self.identity if token == "ok" else None

    def is_auth_required(self) -> bool:
        return True


def _authenticate_reader(user_id: str) -> None:
    identity = Identity(user_id, user_id, capabilities=["tinyassets.wiki.read"])
    set_provider(_StaticAuthProvider(identity))
    auth_middleware("ok")


def _write_page(
    wiki_root: Path,
    relative_path: str,
    *,
    title: str,
    body: str,
    audience: str | None = None,
    updated: str = "2026-07-24T12:00:00Z",
    visibility_value: str = "",
) -> Path:
    path = wiki_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = ["---", f"title: {title}", "type: note", f"updated: {updated}"]
    if audience is not None:
        frontmatter.append(f"audience: {audience}")
    if visibility_value:
        frontmatter.append(f"visibility: {visibility_value}")
    path.write_text(
        "\n".join([*frontmatter, "---", "", body, ""]),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _result_paths(payload: dict[str, Any]) -> set[str]:
    return {item["path"] for item in payload.get("results", [])}


# ── module surface ──────────────────────────────────────────────────────────


def test_module_exposes_expected_public_names():
    """The new submodule's contract surface — guards against silent removal."""
    expected = {
        "wiki", "_ensure_wiki_scaffold", "_WIKI_CATEGORIES",
        "_wiki_file_bug", "_wiki_cosign_bug",
    }
    missing = expected - set(dir(wiki_mod))
    assert not missing, f"wiki.py is missing public names: {missing}"


def test_wiki_categories_canonical_order():
    """Category enum stays in a stable, explicit order — this tuple is the
    single source of truth (the former wiki-mcp/server.js mirror is retired)."""
    assert _WIKI_CATEGORIES == (
        "projects", "concepts", "people", "research", "recipes", "workflows",
        "notes", "references", "plans", "bugs", "feature-requests",
        "design-proposals", "patch-requests",
    )
    assert _WIKI_CATEGORIES[0] == "projects"
    assert _BUGS_CATEGORY in _WIKI_CATEGORIES


def test_kind_routing_covers_all_valid_kinds():
    assert set(_KIND_ROUTING.keys()) == set(_VALID_BUG_KINDS)
    for kind, (subdir, prefix) in _KIND_ROUTING.items():
        assert subdir, f"empty subdir for kind={kind}"
        assert prefix, f"empty prefix for kind={kind}"


# ── helper unit tests ───────────────────────────────────────────────────────


def test_sanitize_slug_strips_extension_and_normalizes():
    assert _sanitize_slug("My Page.md") == "my-page"
    assert _sanitize_slug("UPPER_CASE") == "upper-case"
    assert _sanitize_slug("a/b!c.md") == "a-b-c"


def test_slugify_title_truncates_and_handles_empty():
    long = "x" * 100
    assert len(_slugify_title(long, max_len=20)) <= 20
    assert _slugify_title("!!!") == "untitled"


def test_parse_frontmatter_roundtrip():
    raw = "---\ntitle: Foo\ntype: note\n---\nbody here\n"
    meta, body = _parse_frontmatter(raw)
    assert meta == {"title": "Foo", "type": "note"}
    assert body == "body here\n"


def test_parse_frontmatter_no_frontmatter_returns_empty_meta():
    raw = "just body, no frontmatter"
    meta, body = _parse_frontmatter(raw)
    assert meta == {}
    assert body == raw


def test_extract_keywords_drops_stop_words():
    kws = _extract_keywords("The quick brown fox jumps over the lazy dog")
    assert "quick" in kws
    assert "brown" in kws
    assert "the" not in kws  # stop word
    assert "a" not in kws


def test_wiki_similarity_score_identical_pages_high():
    meta = {"title": "Foo"}
    body = "rabbit hops through forest with [[carrots]]"
    score = _wiki_similarity_score(meta, body, meta, body)
    assert score > 0.4  # same body + same title


def test_wiki_similarity_score_disjoint_pages_low():
    score = _wiki_similarity_score(
        {"title": "A"}, "rabbits hop forest carrots",
        {"title": "B"}, "elephants stomp savanna grass",
    )
    assert score < 0.2


def test_jaccard_basic():
    assert _jaccard(set(), set()) == 1.0
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0
    assert _jaccard({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)


def test_bug_token_set_filters_short_words():
    tokens = _bug_token_set("To do or not to do, that is the question.")
    assert "question" in tokens
    assert "to" not in tokens  # too short
    assert "do" not in tokens


def test_bug_dedup_threshold_constant():
    assert 0 < _BUG_DEDUP_THRESHOLD <= 1.0


def test_next_id_starts_at_001_for_empty_dirs(tmp_path):
    assert _next_id(tmp_path / "missing", tmp_path / "also_missing", "BUG") == "BUG-001"
    pages = tmp_path / "pages"
    drafts = tmp_path / "drafts"
    pages.mkdir()
    drafts.mkdir()
    assert _next_id(pages, drafts, "BUG") == "BUG-001"


def test_next_id_increments_past_existing(tmp_path):
    pages = tmp_path / "pages"
    drafts = tmp_path / "drafts"
    pages.mkdir()
    drafts.mkdir()
    (pages / "BUG-001-foo.md").write_text("x")
    (pages / "BUG-007-bar.md").write_text("x")
    (drafts / "BUG-003-baz.md").write_text("x")
    assert _next_id(pages, drafts, "BUG") == "BUG-008"


# ── scaffold ────────────────────────────────────────────────────────────────


def test_ensure_wiki_scaffold_creates_full_tree(tmp_path):
    root = tmp_path / "fresh"
    _ensure_wiki_scaffold(root)
    for cat in _WIKI_CATEGORIES:
        assert (root / "pages" / cat).is_dir()
        assert (root / "drafts" / cat).is_dir()
    assert (root / "raw").is_dir()
    assert (root / "log").is_dir()
    assert (root / "index.md").is_file()
    assert (root / "WIKI.md").is_file()
    assert (root / "log.md").is_file()


def test_ensure_wiki_scaffold_idempotent_preserves_user_content(tmp_path):
    root = tmp_path / "fresh"
    _ensure_wiki_scaffold(root)
    (root / "index.md").write_text("MY CUSTOM INDEX")
    _ensure_wiki_scaffold(root)  # second call must not overwrite
    assert (root / "index.md").read_text() == "MY CUSTOM INDEX"


# ── dispatch entry ──────────────────────────────────────────────────────────


def test_wiki_unknown_action_returns_error(wiki_env):
    res = json.loads(wiki(action="bogus_action"))
    assert "error" in res
    assert "available_actions" in res
    assert "read" in res["available_actions"]


def test_wiki_list_returns_promoted_and_drafts_keys(wiki_env):
    res = json.loads(wiki(action="list"))
    assert "promoted" in res
    assert "drafts" in res
    assert "promoted_count" in res
    assert "drafts_count" in res


def test_wiki_search_requires_query(wiki_env):
    res = json.loads(wiki(action="search", query=""))
    assert "error" in res


def test_wiki_search_no_results_returns_empty_list(wiki_env):
    res = json.loads(wiki(action="search", query="zzz_nothing_should_match"))
    assert res["results"] == []
    assert res["search_complete"] is False
    assert "read_page changed_since=" in res["completeness_warning"]


def test_wiki_search_returns_completeness_warning_with_matches(wiki_env):
    _write_page(
        wiki_env,
        "pages/workflows/search-target.md",
        title="Search Target",
        body="This reusable workflow mentions cross AI discovery gaps.",
        audience="discovery",
        updated="2026-05-06T12:00:00Z",
    )
    _write_page(
        wiki_env,
        "pages/notes/internal-search-target.md",
        title="Internal Search Target",
        body="This coordination note also mentions cross AI discovery gaps.",
        audience="coordination",
        updated="2026-05-06T12:00:00Z",
    )

    res = json.loads(wiki(action="search", query="discovery"))

    assert res["count"] == 1
    assert res["scope"] == "discovery"
    assert _result_paths(res) == {"pages/workflows/search-target.md"}
    assert res["search_complete"] is False
    assert "lexical" in res["completeness_warning"]
    assert "read_page changed_since=" in res["completeness_warning"]


def test_wiki_since_returns_pages_updated_after_timestamp(wiki_env):
    _write_page(
        wiki_env,
        "pages/research/fresh-research.md",
        title="Fresh Research",
        body="Fresh discovery research content.",
        audience="discovery",
        updated="2026-05-06T12:00:00Z",
    )
    _write_page(
        wiki_env,
        "pages/patch-requests/fresh-patch.md",
        title="Fresh Patch",
        body="Fresh coordination patch request content.",
        audience="coordination",
        updated="2026-05-06T12:00:00Z",
    )
    _write_page(
        wiki_env,
        "pages/research/older-research.md",
        title="Older Research",
        body="Older discovery content.",
        audience="discovery",
        updated="2026-05-01T12:00:00Z",
    )

    res = json.loads(
        wiki(action="since", changed_since="2026-05-05T00:00:00Z")
    )

    assert res["changed_since"] == "2026-05-05T00:00:00Z"
    assert res["scope"] == "discovery"
    assert res["count"] == 1
    assert res["results"][0]["path"] == "pages/research/fresh-research.md"
    assert res["results"][0]["title"] == "Fresh Research"
    assert res["results"][0]["updated"] == "2026-05-06T12:00:00Z"


def test_wiki_since_requires_valid_changed_since(wiki_env):
    missing = json.loads(wiki(action="since"))
    invalid = json.loads(wiki(action="since", changed_since="not-a-date"))

    assert "changed_since parameter is required" in missing["error"]
    assert "valid ISO" in invalid["error"]


def test_wiki_read_requires_page(wiki_env):
    res = json.loads(wiki(action="read", page=""))
    assert "error" in res


def test_wiki_read_index_after_scaffold(wiki_env):
    res = json.loads(wiki(action="read", page="index"))
    assert "content" in res
    assert "Wiki Index" in res["content"]


def test_wiki_read_returns_source_proof_and_ambient_feed(wiki_env):
    source = wiki_env / "pages" / "notes" / "live-brain.md"
    source.write_text(
        "---\n"
        "title: Live Brain\n"
        "type: note\n"
        "updated: 2026-05-01\n"
        "audience: coordination\n"
        "tags: ambient relevance\n"
        "---\n\n"
        "# Live Brain\n\n"
        "Ambient relevance should move source-read proof across sessions.\n",
        encoding="utf-8",
        newline="\n",
    )
    related = wiki_env / "pages" / "notes" / "fresh-related.md"
    related.write_text(
        "---\n"
        "title: Fresh Related\n"
        "type: note\n"
        "updated: 2026-05-06T12:00:00Z\n"
        "audience: coordination\n"
        "tags: relevance feed\n"
        "---\n\n"
        "This note mentions ambient source-read proof for adjacent goals.\n",
        encoding="utf-8",
        newline="\n",
    )
    old = wiki_env / "pages" / "notes" / "old-related.md"
    old.write_text(
        "---\n"
        "title: Old Related\n"
        "type: note\n"
        "updated: 2026-04-01\n"
        "audience: coordination\n"
        "tags: ambient relevance\n"
        "---\n\n"
        "Old ambient relevance should be excluded by changed_since.\n",
        encoding="utf-8",
        newline="\n",
    )
    _write_page(
        wiki_env,
        "pages/workflows/discovery-related.md",
        title="Discovery Related",
        body="A discovery page mentions ambient source-read proof.",
        audience="discovery",
        updated="2026-05-06T12:00:00Z",
    )

    res = json.loads(
        wiki(
            action="read",
            page="live-brain",
            query="ambient relevance source proof",
            changed_since="2026-05-02T00:00:00Z",
            max_results=5,
        )
    )

    assert res["source_read_proof"]["path"] == "pages/notes/live-brain.md"
    assert res["source_read_proof"]["title"] == "Live Brain"
    assert len(res["source_read_proof"]["sha256"]) == 64
    feed = res["ambient_relevance_feed"]
    assert feed["source_path"] == "pages/notes/live-brain.md"
    assert feed["scope"] == "coordination"
    paths = [item["path"] for item in feed["items"]]
    assert "pages/notes/fresh-related.md" in paths
    assert "pages/notes/old-related.md" not in paths
    assert "pages/notes/live-brain.md" not in paths
    assert "pages/workflows/discovery-related.md" not in paths
    fresh = next(item for item in feed["items"] if item["path"].endswith("fresh-related.md"))
    assert "ambient" in fresh["matched_terms"]


def test_wiki_scope_and_audience_matrix_is_fail_closed_and_self_auditing(
    wiki_env, caplog
):
    cases = [
        ("pages/plans/explicit-discovery.md", "discovery", "discovery"),
        ("pages/workflows/explicit-coordination.md", "coordination", "coordination"),
        ("pages/notes/unset.md", None, "coordination"),
        ("pages/notes/blank.md", "", "coordination"),
        ("pages/notes/whitespace.md", "   ", "coordination"),
        ("pages/plans/padded.md", "   DiScOvErY   ", "discovery"),
        ("pages/feature-requests/feature.md", None, "coordination"),
        ("pages/magic-systems/custom.md", None, "discovery"),
        ("pages/root-page.md", None, "discovery"),
        ("drafts/root-draft.md", None, "discovery"),
        *((f"pages/workflows/invalid-{i}.md", "external", "coordination") for i in range(8)),
    ]
    for path, audience, classified in cases:
        marker = " quiet-default" if classified == "discovery" else ""
        _write_page(
            wiki_env,
            path,
            title=Path(path).stem,
            body=f"audience-matrix{marker} canary",
            audience=audience,
        )
    expected = {
        scope: {path for path, _audience, classified in cases if classified == scope}
        for scope in ("discovery", "coordination")
    }

    for raw_scope in (None, "", "   ", "discovery", "coordination", "all"):
        kwargs = dict(
            action="search", query="audience-matrix", max_results=100
        )
        if raw_scope is not None:
            kwargs["scope"] = raw_scope
        result = json.loads(wiki(**kwargs))
        applied = raw_scope.strip() if raw_scope and raw_scope.strip() else "discovery"
        paths = (
            expected["discovery"] | expected["coordination"]
            if applied == "all"
            else expected[applied]
        )
        assert result["scope"] == applied
        assert _result_paths(result) == paths
        if raw_scope in (None, "", "   "):
            assert all(value in result["scope_note"] for value in ("coordination", "all"))
        else:
            assert "scope_note" not in result

    invalid = json.loads(wiki(action="search", query="audience-matrix", scope="private"))
    assert all(value in invalid["error"] for value in ("discovery", "coordination", "all"))
    assert invalid.get("results") == []
    assert "scope" not in invalid and "scope_note" not in invalid

    quiet = json.loads(wiki(action="search", query="quiet-default", max_results=100))
    assert quiet["scope"] == "discovery"
    assert "scope_note" not in quiet

    caplog.set_level(logging.WARNING)
    json.loads(wiki(action="search", query="audience-matrix", scope="coordination"))
    assert sum("audience" in record.getMessage().lower() for record in caplog.records) <= 1


def test_wiki_category_filters_search_since_and_ambient_without_widening(wiki_env):
    for path, audience in (
        ("pages/magic-systems/discovery.md", "discovery"),
        ("drafts/magic-systems/coordination.md", "coordination"),
        ("pages/workflows/other.md", "discovery"),
    ):
        _write_page(
            wiki_env,
            path,
            title=Path(path).stem,
            body="category-matrix ambient-category canary",
            audience=audience,
        )
    for action in ("search", "since"):
        kwargs = dict(action=action, category="Magic Systems", scope="all")
        kwargs["query" if action == "search" else "changed_since"] = (
            "category-matrix" if action == "search" else "2026-07-01T00:00:00Z"
        )
        assert _result_paths(json.loads(wiki(**kwargs))) == {
            "pages/magic-systems/discovery.md",
            "drafts/magic-systems/coordination.md",
        }
        absent = json.loads(wiki(**{**kwargs, "category": "not-yet-created"}))
        assert "error" not in absent and absent["results"] == []
        for invalid_category in ("///", "   "):
            invalid = json.loads(
                wiki(**{**kwargs, "category": invalid_category})
            )
            assert "category" in invalid["error"].lower()
            assert invalid.get("results") == []

    source_body = "exact source body stays unchanged"
    _write_page(
        wiki_env,
        "pages/workflows/source.md",
        title="Source",
        body=source_body,
        audience="discovery",
    )
    ambient = json.loads(
        wiki(
            action="read",
            page="pages/workflows/source.md",
            query="ambient-category",
            category="Magic Systems",
            scope="all",
        )
    )
    assert source_body in ambient["content"]
    assert {item["path"] for item in ambient["ambient_relevance_feed"]["items"]} == {
        "pages/magic-systems/discovery.md",
        "drafts/magic-systems/coordination.md",
    }
    ordered = json.loads(
        wiki(action="search", query="category-matrix", category="Magic Systems",
             scope="discovery")
    )
    assert _result_paths(ordered) == {"pages/magic-systems/discovery.md"}
    for invalid_category in ("!!!", "   "):
        nested = json.loads(
            wiki(
                action="read",
                page="pages/workflows/source.md",
                category=invalid_category,
            )
        )
        assert source_body in nested["content"] and "error" not in nested
        assert nested["ambient_relevance_feed"]["items"] == []
        assert "category" in nested["ambient_relevance_feed"]["error"].lower()


def test_wiki_exact_read_source_scope_and_list_structure_stay_distinct(wiki_env):
    for path, audience in (
        ("pages/workflows/source-discovery.md", "discovery"),
        ("pages/notes/source-coordination.md", "coordination"),
        ("pages/workflows/source-invalid.md", "external"),
        ("pages/workflows/sibling-discovery.md", "discovery"),
        ("pages/notes/sibling-coordination.md", "coordination"),
    ):
        _write_page(
            wiki_env,
            path,
            title=Path(path).stem,
            body="source-scope exact-body canary",
            audience=audience,
        )
    expected = {
        "source-discovery": ("discovery", "sibling-discovery", "sibling-coordination"),
        "source-coordination": ("coordination", "sibling-coordination", "sibling-discovery"),
        "source-invalid": ("coordination", "sibling-coordination", "sibling-discovery"),
    }
    for source, (scope, included, excluded) in expected.items():
        result = json.loads(
            wiki(action="read", page=source, query="source-scope")
        )
        assert "exact-body" in result["content"]
        feed = result["ambient_relevance_feed"]
        assert feed["scope"] == scope
        blob = json.dumps(feed["items"])
        assert included in blob and excluded not in blob

    opposite = json.loads(
        wiki(action="read", page="source-discovery", scope="coordination")
    )
    assert "exact-body" in opposite["content"]
    assert opposite["ambient_relevance_feed"]["scope"] == "coordination"

    listed = json.loads(wiki(action="list"))
    listed_blob = json.dumps(listed)
    assert "source-discovery" in listed_blob and "source-coordination" in listed_blob
    assert "scope" not in listed and "scope_note" not in listed


@pytest.mark.parametrize("surface", ["search", "since", "ambient"])
@pytest.mark.parametrize("scope", ["discovery", "coordination", "all"])
def test_wiki_visibility_denial_precedes_scope_with_real_grant_control(
    wiki_env, surface, scope
):
    data_root = wiki_env.parent
    universe_id = f"visibility-{surface}-{scope}"
    universe_dir = data_root / universe_id
    ensure_universe_registered(
        data_root, universe_id=universe_id, universe_path=universe_dir
    )
    visibility.set_universe_visibility(universe_id, "public", source="owner")
    universe_wiki = universe_dir / "wiki"
    _ensure_wiki_scaffold(universe_wiki)
    audience = "coordination" if scope == "coordination" else "discovery"
    for path, title, marker, restricted in (
        ("pages/workflows/source.md", "Source", "visibility-matrix", False),
        ("pages/workflows/public.md", "Public", "PUBLIC-CONTROL visibility-matrix", False),
        ("pages/workflows/secret.md", "Secret", "SECRET-BODY visibility-matrix", True),
    ):
        _write_page(
            universe_wiki,
            path,
            title=title,
            body=marker,
            audience=audience,
            visibility_value="private" if restricted else "",
        )

    def invoke() -> dict[str, Any]:
        with wiki_mod._scoped_wiki_root(universe_wiki):
            if surface == "search":
                raw = wiki_mod._wiki_search(
                    query="visibility-matrix", scope=scope, universe_id=universe_id
                )
            elif surface == "since":
                raw = wiki_mod._wiki_since(
                    changed_since="2026-07-01T00:00:00Z",
                    scope=scope,
                    universe_id=universe_id,
                )
            else:
                raw = wiki_mod._wiki_read(
                    page="source",
                    query="visibility-matrix",
                    scope=scope,
                    universe_id=universe_id,
                )
        result = json.loads(raw)
        return result["ambient_relevance_feed"] if surface == "ambient" else result

    denied_result = invoke()
    grant_universe_access(
        data_root, universe_id=universe_id, actor_id="alice", permission="read"
    )
    _authenticate_reader("alice")
    granted_result = invoke()
    denied, granted = json.dumps(denied_result), json.dumps(granted_result)
    assert "Public" in denied
    assert all(secret in granted for secret in ("secret.md", "Secret", "SECRET-BODY"))
    assert not any(secret in denied for secret in ("secret.md", "Secret", "SECRET-BODY"))
    assert granted_result["scope"] == scope


def test_wiki_default_discovery_dispatch_is_deterministic_across_256_calls(wiki_env):
    for path, audience in (
        ("pages/workflows/concurrency-discovery.md", "discovery"),
        ("pages/notes/concurrency-coordination.md", "coordination"),
    ):
        _write_page(
            wiki_env,
            path,
            title=Path(path).stem,
            body="dispatcher-concurrency canary",
            audience=audience,
        )

    # A pool worker starts with an EMPTY context, so the bound caller is not
    # there and every search refuses for authentication rather than exercising
    # the dispatcher. The server binds per request on whatever thread serves
    # it; each worker binds the same caller here.
    from tinyassets.auth import middleware as _mw

    _caller = _mw.current_identity_or_none()

    def invoke(_: int) -> str:
        _mw._current_identity.set(_caller)
        return wiki(action="search", query="dispatcher-concurrency")

    reference = invoke(-1)
    with ThreadPoolExecutor(max_workers=32) as executor:
        responses = list(executor.map(invoke, range(256)))
    assert len(responses) == 256 and all(item == reference for item in responses)
    assert 0 < len(reference.encode()) < 16_384
    parsed = json.loads(reference)
    assert parsed["scope"] == "discovery"
    assert _result_paths(parsed) == {"pages/workflows/concurrency-discovery.md"}


def test_wiki_read_large_page_marks_content_and_supports_offset(wiki_env):
    path = wiki_env / "pages" / "notes" / "large-read.md"
    body = "start marker\n" + ("x" * 16000) + "\nend marker\n"
    path.write_text(
        "---\n"
        "title: Large Read\n"
        "type: note\n"
        "updated: 2026-05-27T00:00:00Z\n"
        "---\n\n"
        + body,
        encoding="utf-8",
        newline="\n",
    )

    first = json.loads(wiki(action="read", page="large-read", max_chars=4000))

    assert first["truncated"] is True
    assert first["read_start"] == 0
    assert first["read_end"] == 4000
    assert first["next_offset"] == 4000
    assert "WIKI READ TRUNCATED" in first["content"]
    assert "not exposed by the advertised handles" in first["content"]
    assert "end marker" not in first["content"]

    second = json.loads(
        wiki(
            action="read",
            page="large-read",
            offset=first["next_offset"],
            max_chars=20000,
        )
    )

    assert second["truncated"] is False
    assert second["read_start"] == 4000
    assert second["next_offset"] is None
    assert "end marker" in second["content"]


def test_wiki_read_default_window_handles_medium_design_doc(wiki_env):
    path = wiki_env / "pages" / "notes" / "medium-design-doc.md"
    body = "start marker\n" + ("x" * 80_000) + "\nend marker\n"
    path.write_text(
        "---\n"
        "title: Medium Design Doc\n"
        "type: note\n"
        "updated: 2026-05-27T00:00:00Z\n"
        "---\n\n"
        + body,
        encoding="utf-8",
        newline="\n",
    )

    res = json.loads(wiki(action="read", page="medium-design-doc"))

    assert res["truncated"] is False
    assert res["read_limit"] == _WIKI_READ_DEFAULT_MAX_CHARS
    assert res["next_offset"] is None
    assert "end marker" in res["content"]


def test_wiki_read_max_chars_is_capped(wiki_env):
    path = wiki_env / "pages" / "notes" / "oversize-design-doc.md"
    body = "start marker\n" + ("x" * 300_000) + "\nend marker\n"
    path.write_text(
        "---\n"
        "title: Oversize Design Doc\n"
        "type: note\n"
        "updated: 2026-05-27T00:00:00Z\n"
        "---\n\n"
        + body,
        encoding="utf-8",
        newline="\n",
    )

    res = json.loads(
        wiki(action="read", page="oversize-design-doc", max_chars=999_999)
    )

    assert res["truncated"] is True
    assert res["read_limit"] == _WIKI_READ_MAX_CHARS
    assert res["next_offset"] == _WIKI_READ_MAX_CHARS
    assert "WIKI READ TRUNCATED" in res["content"]


def test_wiki_write_requires_filename_and_content(wiki_env):
    res = json.loads(wiki(action="write", category="notes"))
    assert "error" in res


def test_wiki_write_accepts_custom_category(wiki_env):
    # OKF organic growth: the seed taxonomy is a set of defaults, not a closed
    # whitelist. A universe can grow a custom category to match its founder.
    res = json.loads(
        wiki(
            action="write",
            category="magic-systems",
            filename="resonance",
            content="The Resonance is the world's magic system.",
        )
    )
    assert "error" not in res
    assert res["status"] == "drafted"
    assert "magic-systems" in res["path"]


def test_wiki_write_sanitizes_custom_category(wiki_env):
    # A free-form category is slugified (no path-traversal, no spaces/case).
    res = json.loads(
        wiki(
            action="write",
            category="Magic Systems",
            filename="resonance",
            content="body about the resonance magic.",
        )
    )
    assert "error" not in res
    assert "drafts/magic-systems/resonance.md" == res["path"]


def test_wiki_write_rejects_unsluggable_category(wiki_env):
    # A category with no letters/digits cannot become a safe slug -> rejected.
    res = json.loads(
        wiki(action="write", category="!!!", filename="x", content="body")
    )
    assert "error" in res


def test_wiki_custom_category_promotes_and_indexes(wiki_env):
    body = (
        "---\ntitle: The Resonance\ntype: reference\nsources: [canon]\n"
        "confidence: high\n---\nThe Resonance magic system links [[cells]] and "
        "[[bonds]] with enough body text to clear the promotion lint floor.\n"
    )
    drafted = json.loads(
        wiki(action="write", category="magic-systems",
             filename="the-resonance", content=body)
    )
    assert drafted["status"] == "drafted"
    # Promote with the category omitted -> must find the custom-category draft.
    promoted = json.loads(wiki(action="promote", filename="the-resonance"))
    assert promoted["status"] == "promoted"
    assert "magic-systems" in promoted["path"]
    idx = json.loads(wiki(action="read", page="index"))
    assert "Magic Systems" in idx.get("content", "")


def test_wiki_promote_category_is_traversal_safe(wiki_env):
    # A crafted category on promote must be slugified, not used as a raw path
    # component (unsanitized it could unlink a promoted page). It resolves to a
    # harmless slug -> a benign "draft not found", never a path escape.
    res = json.loads(
        wiki(action="promote", category="../pages/notes", filename="whatever")
    )
    assert "error" in res
    assert "not found" in res["error"].lower()


def test_wiki_write_drafts_then_promote_roundtrip(wiki_env):
    body = (
        "---\ntitle: My Note\ntype: note\nsources: [scratch]\nconfidence: medium\n"
        "---\nThis is the body of my note about [[topic-x]] and [[topic-y]] "
        "with enough characters to clear the body-length lint floor.\n"
    )
    res = json.loads(
        wiki(
            action="write",
            category="notes",
            filename="my-note",
            content=body,
        )
    )
    assert res["status"] == "drafted"
    assert "drafts/notes/my-note.md" in res["path"]

    promoted = json.loads(wiki(action="promote", filename="my-note"))
    assert promoted["status"] == "promoted"
    assert "pages/notes/my-note.md" in promoted["path"]


def test_wiki_write_accepts_wiki_relative_draft_filename(wiki_env):
    res = json.loads(
        wiki(
            action="write",
            category="notes",
            filename="drafts/notes/path-note.md",
            content="body",
        )
    )

    assert res["status"] == "drafted"
    assert res["path"] == "drafts/notes/path-note.md"
    assert (wiki_env / "drafts" / "notes" / "path-note.md").read_text(
        encoding="utf-8"
    ) == "body"
    assert not (wiki_env / "drafts" / "notes" / "drafts-notes-path-note.md").exists()


def test_wiki_patch_updates_long_page_without_full_replace(wiki_env):
    path = wiki_env / "pages" / "notes" / "long-note.md"
    original = (
        "---\ntitle: Long Note\ntype: note\nsources: []\n---\n\n"
        "intro marker\n"
        + ("x" * (_WIKI_READ_DEFAULT_MAX_CHARS + 1_000))
        + "\noriginal tail marker\n"
    )
    path.write_text(original, encoding="utf-8")

    read_result = json.loads(wiki(action="read", page="long-note"))
    assert read_result["truncated"] is True
    assert "original tail marker" not in read_result["content"]

    result = json.loads(
        wiki(
            action="patch",
            page="long-note",
            old_text="intro marker",
            new_text="intro marker\npatched detail",
            expected_sha256=hashlib.sha256(original.encode("utf-8")).hexdigest(),
            dry_run=False,
            log_entry="test partial patch",
        )
    )

    assert result["status"] == "patched"
    assert result["path"] == "pages/notes/long-note.md"
    patched = path.read_text(encoding="utf-8")
    assert "intro marker\npatched detail" in patched
    assert "original tail marker" in patched
    assert len(patched) > _WIKI_READ_DEFAULT_MAX_CHARS


def test_wiki_patch_dry_run_reports_without_writing(wiki_env):
    path = wiki_env / "pages" / "notes" / "dry-run-note.md"
    original = "---\ntitle: Dry\ntype: note\n---\n\nbefore\n"
    path.write_text(original, encoding="utf-8")

    result = json.loads(
        wiki(
            action="patch",
            page="dry-run-note",
            old_text="before",
            new_text="after",
        )
    )

    assert result["status"] == "dry_run"
    assert result["would_write"] is True
    assert path.read_text(encoding="utf-8") == original


def test_wiki_patch_rejects_hash_mismatch_without_writing(wiki_env):
    path = wiki_env / "pages" / "notes" / "hash-note.md"
    original = "---\ntitle: Hash\ntype: note\n---\n\nbefore\n"
    path.write_text(original, encoding="utf-8")

    result = json.loads(
        wiki(
            action="patch",
            page="hash-note",
            old_text="before",
            new_text="after",
            expected_sha256="0" * 64,
            dry_run=False,
        )
    )

    assert result["error"] == "content hash mismatch"
    assert result["status"] == "conflict"
    assert path.read_text(encoding="utf-8") == original


def test_wiki_patch_rejects_ambiguous_old_text_without_writing(wiki_env):
    path = wiki_env / "pages" / "notes" / "ambiguous-note.md"
    original = "---\ntitle: Ambiguous\ntype: note\n---\n\nsame\nsame\n"
    path.write_text(original, encoding="utf-8")

    result = json.loads(
        wiki(
            action="patch",
            page="ambiguous-note",
            old_text="same",
            new_text="different",
            dry_run=False,
        )
    )

    assert result["error"] == "old_text must match exactly once"
    assert result["matches"] == 2
    assert path.read_text(encoding="utf-8") == original


def test_wiki_promote_lint_blocks_when_required_fields_missing(wiki_env):
    body = "---\ntitle: Skinny\n---\ntoo short\n"
    json.loads(
        wiki(action="write", category="notes", filename="skinny", content=body)
    )
    res = json.loads(wiki(action="promote", filename="skinny"))
    assert "error" in res
    assert res["error"] == "Promotion blocked."
    assert any("Body too short" in i for i in res["issues"])


def test_wiki_promote_lint_accepts_block_sources_with_non_http_uri(wiki_env):
    body = (
        "---\n"
        "title: Local Source Note\n"
        "type: note\n"
        "sources:\n"
        "  - file:///tmp/local-source.md\n"
        "---\n"
        "This note cites a local [[source-reference]] with enough content "
        "to satisfy promotion lint without requiring an HTTP URL.\n"
    )
    meta, _ = _parse_frontmatter(body)
    assert meta["sources"] == "- file:///tmp/local-source.md"
    assert "- file" not in meta

    json.loads(
        wiki(
            action="write",
            category="notes",
            filename="local-source-note",
            content=body,
        )
    )

    res = json.loads(wiki(action="promote", filename="local-source-note"))

    assert res["status"] == "promoted"


def test_wiki_supersede_requires_three_args(wiki_env):
    res = json.loads(wiki(action="supersede"))
    assert "error" in res


# ── bug-filing dispatch ─────────────────────────────────────────────────────


def test_wiki_file_bug_requires_title_component_severity(wiki_env):
    res = json.loads(wiki(action="file_bug"))
    assert "error" in res
    assert "title" in res["error"]


def test_wiki_file_bug_rejects_invalid_severity(wiki_env):
    res = json.loads(
        wiki(
            action="file_bug",
            component="x",
            severity="catastrophic",  # not a valid level
            title="Something broke",
        )
    )
    assert "error" in res
    assert "valid" in res
    assert set(res["valid"]) == set(_VALID_SEVERITIES)


def test_wiki_file_bug_files_clean_when_no_dups(wiki_env):
    res = json.loads(
        wiki(
            action="file_bug",
            component="universe.inspect",
            severity="minor",
            title="A unique never-seen title aardvark zeppelin",
            observed="aardvark",
            expected="zeppelin",
            force_new=True,
        )
    )
    assert res["status"] == "filed"
    assert res["bug_id"].startswith("BUG-")


def test_wiki_file_bug_title_only_path_still_succeeds(wiki_env):
    res = json.loads(
        wiki(
            action="file_bug",
            component="api.wiki",
            severity="minor",
            title="Title only filing still works",
            force_new=True,
        )
    )

    assert res["status"] == "filed"
    assert res["bug_id"].startswith("BUG-")


def test_wiki_file_bug_rejects_unsupported_body_field(wiki_env):
    res = json.loads(
        wiki(
            action="file_bug",
            component="api.wiki",
            severity="major",
            title="Content body should not be discarded",
            content="This body would be lost if file_bug silently accepted it.",
        )
    )

    assert "error" in res
    assert "content" in res["error"]
    assert "repro, observed, expected, workaround" in res["error"]
    assert 'write_page kind="bug"' in res["hint"]
    assert "wiki" not in res["hint"]
    assert not list((wiki_env / "pages" / "bugs").glob("bug-*.md"))


def test_wiki_file_bug_rejects_unknown_direct_kwarg(wiki_env):
    res = json.loads(
        _wiki_file_bug(
            component="api.wiki",
            severity="major",
            title="Unknown direct kwarg should not be discarded",
            body="This field is not part of the file_bug contract.",
        )
    )

    assert "error" in res
    assert "body" in res["error"]
    assert "content/body are not supported here" in res["error"]
    assert set(_UNSUPPORTED_FILE_BUG_BODY_KWARGS) == {"body", "content"}
    assert not list((wiki_env / "pages" / "bugs").glob("bug-*.md"))


def test_wiki_file_bug_is_filing_only_with_retired_environment_set(
    wiki_env, tmp_path, monkeypatch,
):
    monkeypatch.setenv(
        "TINYASSETS_BUG_INVESTIGATION_BRANCH_DEF_ID",
        "bug-investigation-branch",
    )
    monkeypatch.setenv(
        "TINYASSETS_BUG_INVESTIGATION_GOAL_ID",
        "bug-investigation-goal",
    )
    monkeypatch.delenv("TINYASSETS_REQUEST_TYPE_PRIORITIES", raising=False)

    res = json.loads(
        wiki(
            action="file_bug",
            component="loop",
            severity="major",
            title="Lease metadata response shape",
            observed="queued task lacks visible lease metadata",
            force_new=True,
            verbose=True,
        )
    )

    assert res["status"] == "filed"
    assert "investigation" not in res
    assert "trigger" not in res
    assert "branch_task" not in json.dumps(res)
    assert "dispatcher_request_id" not in json.dumps(res)
    assert "run_id" not in json.dumps(res)
    assert not (tmp_path / "wiki_trigger_attempts.db").exists()
    page = (wiki_env / res["path"]).read_text(encoding="utf-8")
    assert "## Investigation" not in page
    assert "## Patch Packet" not in page


def test_wiki_file_bug_dedup_returns_similar_found(wiki_env):
    base = dict(
        action="file_bug",
        component="universe.inspect",
        severity="minor",
        title="Connector returns wrong universe id on inspect",
        observed="inspect returned the wrong universe id under load",
    )
    json.loads(wiki(**base, force_new=True))
    dup = json.loads(wiki(**base))  # no force_new — should dedup
    assert dup["status"] == "similar_found"
    assert dup["bug_id"] is None
    assert isinstance(dup["similar"], list)
    assert len(dup["similar"]) >= 1
    assert dup["effort_dispatch_route"]["lane"] == "standard-triage"
    assert "cosign_bug" not in dup["hint"]
    assert "not exposed by the advertised handles" in dup["hint"]


def test_wiki_cosign_bug_requires_args(wiki_env):
    res = json.loads(wiki(action="cosign_bug"))
    assert "error" in res
    res = json.loads(wiki(action="cosign_bug", bug_id="BUG-001"))
    assert "error" in res


def test_wiki_cosign_bug_unknown_id_errors(wiki_env):
    res = json.loads(
        wiki(
            action="cosign_bug",
            bug_id="BUG-999",
            reporter_context="me too on the dev box",
        )
    )
    assert "error" in res


def test_wiki_cosign_bug_appends_to_existing_filing(wiki_env):
    filed = json.loads(
        wiki(
            action="file_bug",
            component="x",
            severity="minor",
            title="The cosign smoke test bug uniquely worded",
            observed="something broke uniquely",
            force_new=True,
        )
    )
    bug_id = filed["bug_id"]
    res = json.loads(
        wiki(
            action="cosign_bug",
            bug_id=bug_id,
            reporter_context="seen on the staging tunnel as well",
        )
    )
    assert res["status"] == "cosigned"
    assert res["cosign_count"] == 1

    # Second cosign increments the count.
    res2 = json.loads(
        wiki(
            action="cosign_bug",
            bug_id=bug_id,
            reporter_context="and on local dev",
        )
    )
    assert res2["cosign_count"] == 2

    # Body now contains both contexts.
    bugs_dir = wiki_env / "pages" / "bugs"
    files = list(bugs_dir.glob(f"{bug_id.lower()}-*.md"))
    assert files, f"expected the bug file in {bugs_dir}"
    body = files[0].read_text(encoding="utf-8")
    assert "## Cosigns" in body
    assert "staging tunnel" in body
    assert "local dev" in body
    _ = filed  # path tracked via bug_id glob above
