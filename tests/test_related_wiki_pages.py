"""Tests for the get_branch `related_wiki_pages` surface.

STATUS.md Approved-bugs 2026-04-22 (reshape of BUG-018). Wiki pages
that mention a branch's id or any of its node ids get surfaced in the
branch read APIs so chatbots discover maintainer notes without a
separate wiki search.

Invariants covered:

a. Matches returned in get.
b. No matches returns empty list (not a missing key).
c. Summary is clipped to 140 characters.
d. Top-20 cap with truncated_count.
e. matched_via reflects which query terms hit.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def branch_wiki_env(tmp_path, monkeypatch, authenticate_request):
    base = tmp_path / "base"
    wiki = tmp_path / "wiki"
    (wiki / "pages" / "notes").mkdir(parents=True)
    (wiki / "drafts" / "notes").mkdir(parents=True)

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(wiki))

    # Branch mutation requires a credential-derived subject. Without one
    # `extensions build_branch` returns
    # `{"error": "Authenticated branch subject required."}` and these tests
    # die on `KeyError: 'branch_def_id'` before reaching the wiki-linking
    # behaviour under test. `extensions.costly` stays withheld.
    authenticate_request("tester")

    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us, Path(base), Path(wiki)
    importlib.reload(us)


def _call(us, action, **kwargs):
    return json.loads(us._extensions_impl(action=action, **kwargs))


def _write_page(wiki: Path, category: str, slug: str, *, title: str, body: str,
                draft: bool = False) -> None:
    root = wiki / ("drafts" if draft else "pages") / category
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{slug}.md").write_text(
        f"---\ntitle: {title}\n---\n{body}",
        encoding="utf-8",
    )


def _mk_branch(us, name="Demo", node_ids=("alpha", "beta")) -> str:
    """Build a branch whose nodes run in a line, via ``build_branch``."""
    chain = ["START", *node_ids, "END"]
    built = _call(us, "build_branch", spec_json=json.dumps({
        "name": name,
        "entry_point": node_ids[0],
        "node_defs": [
            {"node_id": nid, "display_name": nid, "prompt_template": f"do {nid}"}
            for nid in node_ids
        ],
        "edges": [{"from": a, "to": b} for a, b in zip(chain, chain[1:])],
    }))
    assert built["status"] == "built", built
    return built["branch_def_id"]


def test_get_branch_returns_related_wiki_pages(branch_wiki_env):
    us, _, wiki = branch_wiki_env
    bid = _mk_branch(us)
    _write_page(
        wiki, "notes", "branch-notes",
        title="Notes for demo branch",
        body=(
            "The branch {bid} is the current canonical demo.\n"
            "It uses node alpha for extraction."
        ).format(bid=bid),
    )

    got = _call(us, "get_branch", branch_def_id=bid)
    assert "related_wiki_pages" in got
    assert got["related_wiki_pages_truncated"] == 0
    paths = [p["path"] for p in got["related_wiki_pages"]]
    assert any("branch-notes" in p for p in paths)


def test_no_matches_returns_empty_list_not_missing_key(branch_wiki_env):
    us, _, wiki = branch_wiki_env
    bid = _mk_branch(us)
    # A wiki page that does NOT mention the branch id or any node id.
    _write_page(
        wiki, "notes", "unrelated",
        title="Unrelated content",
        body="This page is about cooking. Nothing to do with workflows.",
    )

    got = _call(us, "get_branch", branch_def_id=bid)
    assert got["related_wiki_pages"] == []
    assert got["related_wiki_pages_truncated"] == 0


def test_summary_is_clipped_to_140_chars(branch_wiki_env):
    us, _, wiki = branch_wiki_env
    bid = _mk_branch(us, node_ids=("alpha",))
    long_prose = (
        "This is a very long description that exceeds the 140-character "
        "cap enforced by _related_summary. It mentions the branch "
        f"{bid} several times so it scores as a match. More filler text "
        "here to push well past the limit and then some."
    )
    _write_page(
        wiki, "notes", "long-notes",
        title="Long notes page",
        body=long_prose,
    )

    got = _call(us, "get_branch", branch_def_id=bid)
    items = [
        i for i in got["related_wiki_pages"]
        if "long-notes" in i["path"]
    ]
    assert items, "expected long-notes to match"
    summary = items[0]["summary"]
    assert len(summary) <= 140
    assert summary.endswith("…")


def test_top_20_cap_with_truncated_count(branch_wiki_env):
    us, _, wiki = branch_wiki_env
    bid = _mk_branch(us, node_ids=("alpha",))
    # 25 pages all match on branch_def_id.
    for i in range(25):
        _write_page(
            wiki, "notes", f"page-{i:02d}",
            title=f"Page {i:02d}",
            body=f"References branch {bid}.",
        )

    got = _call(us, "get_branch", branch_def_id=bid)
    assert len(got["related_wiki_pages"]) == 20
    assert got["related_wiki_pages_truncated"] == 5


def test_matched_via_reflects_term_hits(branch_wiki_env):
    us, _, wiki = branch_wiki_env
    bid = _mk_branch(us, node_ids=("alpha", "beta"))

    # Page 1: matches branch_def_id only.
    _write_page(
        wiki, "notes", "branch-only",
        title="Branch only page",
        body=f"Talks about {bid} but no node names.",
    )
    # Page 2: matches node:alpha only.
    _write_page(
        wiki, "notes", "alpha-only",
        title="Alpha only page",
        body="Discusses the alpha node in isolation.",
    )
    # Page 3: matches branch_def_id AND node:beta.
    _write_page(
        wiki, "notes", "branch-and-beta",
        title="Branch and beta",
        body=f"Combines {bid} with the beta node.",
    )

    got = _call(us, "get_branch", branch_def_id=bid)
    by_path = {i["path"]: i for i in got["related_wiki_pages"]}

    branch_only = next(v for k, v in by_path.items() if "branch-only" in k)
    assert branch_only["matched_via"] == ["branch_def_id"]

    alpha_only = next(v for k, v in by_path.items() if "alpha-only" in k)
    assert alpha_only["matched_via"] == ["node:alpha"]

    both = next(v for k, v in by_path.items() if "branch-and-beta" in k)
    assert set(both["matched_via"]) == {"branch_def_id", "node:beta"}

    # Rank: the 2-match page outranks the 1-match pages.
    assert got["related_wiki_pages"][0]["path"] == both["path"]


def test_drafts_are_included(branch_wiki_env):
    us, _, wiki = branch_wiki_env
    bid = _mk_branch(us, node_ids=("alpha",))
    _write_page(
        wiki, "notes", "draft-note",
        title="Draft about branch",
        body=f"Draft mentioning {bid}.",
        draft=True,
    )

    got = _call(us, "get_branch", branch_def_id=bid)
    paths = [p["path"] for p in got["related_wiki_pages"]]
    assert any("drafts/" in p and "draft-note" in p for p in paths)


def test_summary_falls_back_to_frontmatter_description(branch_wiki_env, tmp_path):
    us, _, wiki = branch_wiki_env
    bid = _mk_branch(us, node_ids=("alpha",))
    # Body only has headings (no prose) — summary should fall back to
    # frontmatter description.
    (wiki / "pages" / "notes" / "heading-only.md").write_text(
        "---\n"
        "title: Heading only\n"
        "description: Short description from frontmatter.\n"
        "---\n"
        f"# Heading only\n## About {bid}\n",
        encoding="utf-8",
    )

    got = _call(us, "get_branch", branch_def_id=bid)
    item = next(
        i for i in got["related_wiki_pages"] if "heading-only" in i["path"]
    )
    assert item["summary"] == "Short description from frontmatter."
