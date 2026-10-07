"""BUG-033 — goals.search multi-token query returns 0 results.

Root cause: `search_goals` wrapped the entire query in a single LIKE pattern
(`%agent teams build software%`), which never matched because the combined
phrase isn't a substring of any goal's name/description/tags. Single-token
queries happened to work, masking the bug.

Fix: tokenize the query and match each token independently; rank by
token-overlap count.

All tests use a tmp_path SQLite database.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def goals_db(tmp_path):
    """Seed a goals DB with a handful of goals and return base_path."""
    from tinyassets.daemon_server import initialize_author_server, save_goal

    base = tmp_path / "output"
    base.mkdir()
    initialize_author_server(base)

    save_goal(base, goal={"name": "Complete a software project end-to-end",
              "description": "Ship a real working piece of software.",
              "tags": ["software", "project", "shipping"]})
    save_goal(base, goal={"name": "Build an AI agent",
              "description": "Create an autonomous agent that can complete tasks.",
              "tags": ["agent", "AI", "automation"]})
    save_goal(base, goal={"name": "Write a research paper",
              "description": "Conduct research and produce a peer-reviewed paper.",
              "tags": ["research", "writing", "academic"]})
    save_goal(base, goal={"name": "Design a database schema",
              "description": "Model a relational schema for a new application.",
              "tags": ["database", "schema", "SQL"]})
    return base


# ── multi-token query ────────────────────────────────────────────────────────


def test_multi_token_query_returns_matching_goal(goals_db):
    """'agent teams build software' should match the software goal (and/or
    the agent goal) — not return 0 results."""
    from tinyassets.daemon_server import search_goals

    results = search_goals(goals_db, query="agent teams build software")
    names = [r["name"] for r in results]
    assert results, (
        "Multi-token query 'agent teams build software' returned 0 results. "
        f"Expected at least one match. Available goals: {names}"
    )


def test_multi_token_query_matches_by_any_token(goals_db):
    """Each token is matched independently — a goal matching 3/4 tokens
    comes before one matching 1/4 tokens."""
    from tinyassets.daemon_server import search_goals

    results = search_goals(goals_db, query="software project shipping research")
    assert results, "Expected results for multi-token query"
    # 'Complete a software project end-to-end' has name + tags matching
    # software, project, shipping — should rank above the research paper
    # which only matches 'research'.
    top_name = results[0]["name"]
    assert "software" in top_name.lower(), (
        f"Expected software goal to rank first. Got: {top_name!r}"
    )


def test_single_token_still_works(goals_db):
    """Regression: single-token behavior must not break."""
    from tinyassets.daemon_server import search_goals

    results = search_goals(goals_db, query="software")
    assert results
    assert any("software" in r["name"].lower() for r in results)


def test_tag_match_contributes_to_results(goals_db):
    """Tags count as part of the haystack — a query hitting only a tag field
    still returns the goal."""
    from tinyassets.daemon_server import search_goals

    results = search_goals(goals_db, query="shipping")
    assert results, "Expected 'shipping' tag match to return the software goal"
    assert any("software" in r["name"].lower() for r in results)


def test_description_match_contributes_to_results(goals_db):
    """Description is also searched."""
    from tinyassets.daemon_server import search_goals

    results = search_goals(goals_db, query="peer-reviewed")
    assert results, "Expected description match for 'peer-reviewed'"
    assert any("research" in r["name"].lower() for r in results)


def test_space_separated_query_matches_hyphenated_field(goals_db):
    """Hyphen punctuation should not hide a clear token match."""
    from tinyassets.daemon_server import search_goals

    results = search_goals(goals_db, query="peer reviewed")
    assert results, "Expected query 'peer reviewed' to match 'peer-reviewed'"
    assert any("research" in r["name"].lower() for r in results)


def test_hyphenated_query_matches_space_separated_field(goals_db):
    """A chatbot may hyphenate a phrase that the Goal stores with spaces."""
    from tinyassets.daemon_server import search_goals

    results = search_goals(goals_db, query="research-paper")
    assert results, "Expected query 'research-paper' to match 'research paper'"
    assert any("research" in r["name"].lower() for r in results)


def test_empty_query_returns_empty_list(goals_db):
    """Empty query must return empty list, not crash."""
    from tinyassets.daemon_server import search_goals

    results = search_goals(goals_db, query="")
    assert results == []


def test_whitespace_only_query_returns_empty_list(goals_db):
    from tinyassets.daemon_server import search_goals

    results = search_goals(goals_db, query="   ")
    assert results == []


def test_unmatched_query_returns_empty_list(goals_db):
    from tinyassets.daemon_server import search_goals

    results = search_goals(goals_db, query="zzznomatchxyz")
    assert results == []


def test_limit_is_respected(goals_db):
    from tinyassets.daemon_server import search_goals

    results = search_goals(goals_db, query="a", limit=2)
    assert len(results) <= 2


def test_deleted_goals_excluded(goals_db):
    """visibility='deleted' goals must not appear in search results."""
    from tinyassets.daemon_server import delete_goal, save_goal, search_goals

    deleted = save_goal(goals_db, goal={"name": "Deleted goal for secretsearch",
                        "description": "This should not appear.",
                        "tags": ["secretsearch"]})
    delete_goal(goals_db, goal_id=deleted["goal_id"])
    results = search_goals(goals_db, query="secretsearch")
    assert not results, (
        "Deleted goal must not appear in search results"
    )


def test_higher_token_overlap_ranks_first(goals_db):
    """A goal matching more tokens must rank above one matching fewer."""
    from tinyassets.daemon_server import save_goal, search_goals

    # 'xyztoken' appears in both goals; 'alphatarget' only in the first.
    save_goal(goals_db, goal={"name": "xyztoken alphatarget goal",
              "description": "matches two search tokens",
              "tags": []})
    save_goal(goals_db, goal={"name": "xyztoken only goal",
              "description": "matches one search token",
              "tags": []})

    results = search_goals(goals_db, query="xyztoken alphatarget")
    names = [r["name"] for r in results]
    assert names[0] == "xyztoken alphatarget goal", (
        f"Higher-overlap goal should rank first. Got order: {names}"
    )


# ── via goals MCP action dispatch ────────────────────────────────────────────


def test_goals_action_search_multi_token(tmp_path, monkeypatch):
    """End-to-end: goals action=search with a multi-token query must return
    results via the MCP dispatch surface."""
    from tinyassets.api.market import goals
    from tinyassets.daemon_server import initialize_author_server, save_goal
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    initialize_author_server(tmp_path)
    save_goal(tmp_path, goal={"name": "Complete a software project end-to-end",
              "description": "Ship a real working piece of software.",
              "tags": ["software", "project", "shipping"]})

    raw = goals(action="search", query="agent software build")
    result = json.loads(raw)
    assert result["count"] > 0, (
        f"MCP goals search multi-token returned count=0. Response: {result}"
    )
