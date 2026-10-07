"""Goals as a first-class shared primitive.

Covers AC from ``docs/specs/community_branches_phase5.md`` §Executable
Addendum:

1. Storage: goals table + branch_definitions.goal_id column.
2. 8 MCP actions on the `goals` tool with tool_return_shapes.md patterns.
3. Leaderboard metrics: run_count + forks today; outcome stub.
4. common_nodes compares on node_id equality.
5. Soft-delete via visibility='deleted'.
6. Ledger write-through on propose/update/bind.
7. build_branch accepts goal_id top-level; patch_branch set_goal/unset_goal ops.
8. Mission-5-independent parts pass without prompt routing.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from tinyassets.api.helpers import _base_path as _helpers_base_path


def _become(user_id: str) -> None:
    """Sign in as ``user_id``.

    These tests used to set ``UNIVERSE_SERVER_USER``, which named the actor by
    environment variable -- authority from a string anybody can set. The
    autouse operator fixture rebinds between tests, so this does not leak.
    """
    from tinyassets.auth import middleware as _mw
    from tinyassets.auth.provider import Identity

    _mw._current_identity.set(
        Identity(
            user_id=user_id,
            username=user_id,
            display_name=user_id,
            capabilities=[
                "tinyassets.universe.read",
                "tinyassets.universe.write",
                "tinyassets.universe.admin",
                "tinyassets.extensions.read",
                "tinyassets.extensions.write",
            ],
        )
    )


@pytest.fixture
def p5_env(tmp_path, monkeypatch, authenticate_request):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    _become("tester")
    # Branch mutation requires a credential-derived subject. Without one the
    # extensions surface returns
    # `{"error": "Authenticated branch subject required."}` and these tests
    # die before reaching their own concern. The conftest default grants
    # extensions read/write/admin only, and the scope check is per-family:
    # this file drives `goals` too, so it needs the goals scopes explicitly.
    # `extensions.costly` and `goals.costly` are deliberately withheld.
    authenticate_request("tester", capabilities=[
        "tinyassets.extensions.read",
        "tinyassets.extensions.write",
        "tinyassets.extensions.admin",
        "tinyassets.goals.read",
        "tinyassets.goals.write",
    ])
    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us, base
    importlib.reload(us)


def _call(us, tool, action, **kwargs):
    """Dispatch to the named MCP tool function with action + kwargs."""
    fn = getattr(us, f"_{tool}_impl")
    return json.loads(fn(action=action, **kwargs))


def _bind(branch_def_id: str, goal_id: str) -> None:
    """Bind a branch to a Goal through storage; the ``goals bind`` action
    is gone and the reads under test only look at the stored ``goal_id``."""
    from tinyassets.daemon_server import update_branch_definition

    update_branch_definition(
        _helpers_base_path(), branch_def_id=branch_def_id,
        updates={"goal_id": goal_id},
    )


def _soft_delete_goal(goal_id: str) -> None:
    """Soft-delete through storage; ``goals update`` is gone."""
    from tinyassets.daemon_server import update_goal

    update_goal(_helpers_base_path(), goal_id=goal_id,
                updates={"visibility": "deleted"})


# ─────────────────────────────────────────────────────────────────────────────
# propose + storage
# ─────────────────────────────────────────────────────────────────────────────


def test_propose_creates_goal_with_id(p5_env):
    us, _ = p5_env
    result = _call(us, "goals", "propose",
                   name="Research paper",
                   description="Produce an academic research paper",
                   tags="research,academic")
    assert result["status"] == "proposed"
    assert result["goal"]["goal_id"]
    assert result["goal"]["name"] == "Research paper"
    assert result["goal"]["tags"] == ["research", "academic"]
    assert result["goal"]["visibility"] == "public"
    assert "text" in result
    # #58: raw goal_id must stay in structuredContent, not the text channel.
    assert result["goal"]["goal_id"] not in result["text"]
    # The Goal name surfaces in text instead.
    assert "Research paper" in result["text"]


def test_propose_requires_name(p5_env):
    us, _ = p5_env
    result = _call(us, "goals", "propose")
    assert result["status"] == "rejected"
    assert "name" in result["error"].lower()


def test_propose_rejects_bad_visibility(p5_env):
    us, _ = p5_env
    result = _call(us, "goals", "propose",
                   name="x", visibility="deleted")
    assert result["status"] == "rejected"


def test_list_empty_returns_friendly_text(p5_env):
    us, _ = p5_env
    result = _call(us, "goals", "list")
    assert result["count"] == 0
    assert "no goals" in result["text"].lower()


def test_list_returns_proposed_goals(p5_env):
    us, _ = p5_env
    _call(us, "goals", "propose", name="A", tags="x")
    _call(us, "goals", "propose", name="B", tags="y")
    result = _call(us, "goals", "list")
    assert result["count"] == 2
    names = {g["name"] for g in result["goals"]}
    assert names == {"A", "B"}
    assert "- `" in result["text"]


def test_list_can_filter_to_production_goals(p5_env):
    us, _ = p5_env
    _call(us, "goals", "propose", name="Real workflow goal", tags="research")
    _call(us, "goals", "propose", name="Smoke probe goal", tags="smoke")
    _call(us, "goals", "propose", name="Disposable scout goal", tags="disposable")
    _call(us, "goals", "propose", name="RETRACTED old goal", tags="research")
    _call(us, "goals", "propose", name="Private real goal", visibility="private")

    result = _call(us, "goals", "list", production_only=True)

    assert result["count"] == 1
    assert result["production_only"] is True
    # Private Goals are removed by the storage predicate and therefore do not
    # contribute to public counts.
    assert result["excluded_count"] == 3
    assert [g["name"] for g in result["goals"]] == ["Real workflow goal"]
    assert "production" in result["text"].lower()


def test_list_production_filter_applies_before_limit(p5_env):
    us, _ = p5_env
    _call(us, "goals", "propose", name="Real workflow goal", tags="research")
    _call(us, "goals", "propose", name="Smoke probe goal", tags="smoke")

    result = _call(us, "goals", "list", production_only=True, limit=1)

    assert result["count"] == 1
    assert [g["name"] for g in result["goals"]] == ["Real workflow goal"]


def test_get_returns_full_goal_with_branches(p5_env):
    us, _ = p5_env
    gid = _call(us, "goals", "propose", name="Test")["goal"]["goal_id"]
    result = _call(us, "goals", "get", goal_id=gid)
    assert result["goal"]["goal_id"] == gid
    assert result["branch_count"] == 0
    assert "Test" in result["text"]


def test_get_rejects_missing_goal(p5_env):
    us, _ = p5_env
    result = _call(us, "goals", "get", goal_id="deadbeef")
    assert result["status"] == "rejected"


# ─────────────────────────────────────────────────────────────────────────────
# bind + list_branches filter
# ─────────────────────────────────────────────────────────────────────────────


def _build_branch(us, name: str = "Trivial") -> str:
    spec = {
        "name": name,
        "entry_point": "n",
        "node_defs": [{"node_id": "n", "display_name": "N",
                       "prompt_template": "Go: {x}"}],
        "edges": [
            {"from": "START", "to": "n"},
            {"from": "n", "to": "END"},
        ],
        "state_schema": [{"name": "x", "type": "str"}],
    }
    return _call(us, "extensions", "build_branch",
                 spec_json=json.dumps(spec))["branch_def_id"]


def test_list_branches_goal_id_filter(p5_env):
    us, _ = p5_env
    gid1 = _call(us, "goals", "propose", name="G1")["goal"]["goal_id"]
    gid2 = _call(us, "goals", "propose", name="G2")["goal"]["goal_id"]
    b1 = _build_branch(us, name="B1")
    b2 = _build_branch(us, name="B2")
    b3 = _build_branch(us, name="B3")
    _bind(b1, gid1)
    _bind(b2, gid1)
    _bind(b3, gid2)

    # scope="all" so unpublished drafts built by _build_branch are included
    # (default scope="published" would filter them out).
    result = _call(us, "extensions", "list_branches", goal_id=gid1, scope="all")
    assert result["count"] == 2
    ids = {b["branch_def_id"] for b in result["branches"]}
    assert ids == {b1, b2}


def test_get_goal_shows_bound_branches(p5_env):
    us, _ = p5_env
    gid = _call(us, "goals", "propose", name="G")["goal"]["goal_id"]
    bid = _build_branch(us, name="Bound")
    _bind(bid, gid)

    result = _call(us, "goals", "get", goal_id=gid)
    assert result["branch_count"] == 1
    assert "Bound" in result["text"]


# ─────────────────────────────────────────────────────────────────────────────
# search
# ─────────────────────────────────────────────────────────────────────────────


def test_search_matches_name(p5_env):
    us, _ = p5_env
    _call(us, "goals", "propose",
          name="Research paper pipeline",
          description="Build an academic research paper end-to-end")
    _call(us, "goals", "propose",
          name="Wedding planner",
          description="Track vendor and budget across months")

    result = _call(us, "goals", "search", query="research")
    assert result["count"] == 1
    assert result["goals"][0]["name"] == "Research paper pipeline"


def test_search_matches_description(p5_env):
    us, _ = p5_env
    _call(us, "goals", "propose", name="X",
          description="tracking bees and honey")
    result = _call(us, "goals", "search", query="honey")
    assert result["count"] == 1


def test_search_requires_query(p5_env):
    us, _ = p5_env
    result = _call(us, "goals", "search")
    assert result["status"] == "rejected"


def test_search_hides_deleted_goals(p5_env):
    us, _ = p5_env
    gid = _call(us, "goals", "propose", name="Hidden")["goal"]["goal_id"]
    _soft_delete_goal(gid)
    result = _call(us, "goals", "search", query="hidden")
    assert result["count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# leaderboard
# ─────────────────────────────────────────────────────────────────────────────


def test_leaderboard_run_count(p5_env):
    us, _ = p5_env
    gid = _call(us, "goals", "propose", name="G")["goal"]["goal_id"]
    b1 = _build_branch(us, name="B1")
    b2 = _build_branch(us, name="B2")
    _bind(b1, gid)
    _bind(b2, gid)

    # B2 has 2 runs, B1 has 1. Run sync via execute_branch to avoid
    # background worker waits.
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import get_branch_definition
    from tinyassets.runs import execute_branch

    for _ in range(1):
        br = BranchDefinition.from_dict(
            get_branch_definition(Path(_helpers_base_path()), branch_def_id=b1)
        )
        execute_branch(
            _helpers_base_path(), branch=br, inputs={"x": "a"},
            actor="universe:u-goals",
        )
    for _ in range(2):
        br = BranchDefinition.from_dict(
            get_branch_definition(Path(_helpers_base_path()), branch_def_id=b2)
        )
        execute_branch(
            _helpers_base_path(), branch=br, inputs={"x": "a"},
            actor="universe:u-goals",
        )

    result = _call(us, "goals", "leaderboard",
                   goal_id=gid, metric="run_count")
    assert len(result["entries"]) == 2
    assert result["entries"][0]["branch_def_id"] == b2
    assert result["entries"][0]["value"] == 2


def test_leaderboard_outcome_gated_off_when_flag_unset(p5_env):
    # Phase 6.2.1: GATES_ENABLED gates the outcome metric. The p5_env
    # fixture doesn't set the flag, so outcome falls back to a
    # friendly gated-off envelope — not a stub, not a live empty
    # leaderboard.
    us, _ = p5_env
    gid = _call(us, "goals", "propose", name="G")["goal"]["goal_id"]
    result = _call(us, "goals", "leaderboard",
                   goal_id=gid, metric="outcome")
    assert result["metric"] == "outcome"
    assert result["entries"] == []
    assert result["status"] == "gates_disabled"
    assert "GATES_ENABLED" in result["text"]


def test_leaderboard_rejects_unknown_metric(p5_env):
    us, _ = p5_env
    gid = _call(us, "goals", "propose", name="G")["goal"]["goal_id"]
    result = _call(us, "goals", "leaderboard",
                   goal_id=gid, metric="moonshots")
    assert result["status"] == "rejected"
    assert "available_metrics" in result
    assert "run_count" in result["available_metrics"]
    assert "outcome" in result["available_metrics"]


def test_leaderboard_forks_counts_parent_chain(p5_env):
    us, _ = p5_env
    gid = _call(us, "goals", "propose", name="G")["goal"]["goal_id"]
    parent_bid = _build_branch(us, name="Parent")
    _bind(parent_bid, gid)

    # Create two "forks" — manually save branches with parent_def_id
    # pointing at parent.
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import save_branch_definition

    parent = BranchDefinition.from_dict({"branch_def_id": parent_bid})
    for i in range(2):
        forked = parent.fork(new_name=f"Fork{i}", author="tester")
        forked.goal_id = gid  # bind fork too so leaderboard sees both
        save_branch_definition(_helpers_base_path(), branch_def=forked.to_dict())

    result = _call(us, "goals", "leaderboard",
                   goal_id=gid, metric="forks")
    # Parent entry should show 2 forks.
    ranked = {r["branch_def_id"]: r["value"] for r in result["entries"]}
    assert ranked.get(parent_bid) == 2


def test_archive_consultation_uses_gate_leaderboard_parent_signal(p5_env):
    us, base = p5_env
    gid = _call(us, "goals", "propose", name="G")["goal"]["goal_id"]

    from tinyassets.daemon_server import (
        claim_gate,
        set_goal_ladder,
        update_branch_definition,
    )

    set_goal_ladder(
        _helpers_base_path(),
        goal_id=gid,
        ladder=[
            {"rung_key": "draft", "label": "Draft"},
            {"rung_key": "reviewed", "label": "Reviewed"},
        ],
    )

    stronger_outcome = _build_branch(us, name="Stronger Outcome")
    higher_quality = _build_branch(us, name="Higher Quality")
    for branch_id, quality in (
        (stronger_outcome, 0.75),
        (higher_quality, 0.95),
    ):
        _bind(branch_id, gid)
        update_branch_definition(
            base,
            branch_def_id=branch_id,
            updates={"stats": {"avg_quality_score": quality}},
        )

    claim_gate(
        base,
        branch_def_id=stronger_outcome,
        goal_id=gid,
        rung_key="reviewed",
        evidence_url="https://example.com/reviewed",
        claimed_by="tester",
    )
    claim_gate(
        base,
        branch_def_id=higher_quality,
        goal_id=gid,
        rung_key="draft",
        evidence_url="https://example.com/draft",
        claimed_by="tester",
    )

    result = _call(us, "goals", "archive_consultation", goal_id=gid, limit=2)

    assert result["status"] == "ok"
    assert [entry["branch_def_id"] for entry in result["candidates"]] == [
        stronger_outcome,
        higher_quality,
    ]
    assert result["candidates"][0]["outcome_signal"]["highest_rung_key"] == "reviewed"
    assert result["outcome_leaderboard"][0]["branch_def_id"] == stronger_outcome
    assert "gate leaderboard" in result["text"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# build_branch goal_id top-level + patch_branch set/unset_goal
# ─────────────────────────────────────────────────────────────────────────────


def test_build_branch_accepts_goal_id(p5_env):
    us, _ = p5_env
    gid = _call(us, "goals", "propose", name="G")["goal"]["goal_id"]
    spec = {
        "name": "Preloaded",
        "entry_point": "n",
        "goal_id": gid,
        "node_defs": [{"node_id": "n", "display_name": "N",
                       "prompt_template": "{x}"}],
        "edges": [
            {"from": "START", "to": "n"},
            {"from": "n", "to": "END"},
        ],
        "state_schema": [{"name": "x", "type": "str"}],
    }
    result = _call(us, "extensions", "build_branch",
                   spec_json=json.dumps(spec))
    assert result["status"] == "built"
    got = _call(us, "extensions", "get_branch",
                branch_def_id=result["branch_def_id"])
    assert got["goal_id"] == gid


def test_build_branch_binds_top_level_goal_id(p5_env):
    us, _ = p5_env
    gid = _call(us, "goals", "propose", name="G")["goal"]["goal_id"]
    spec = {
        "name": "Top-level goal",
        "entry_point": "n",
        "node_defs": [{"node_id": "n", "display_name": "N",
                       "prompt_template": "{x}"}],
        "edges": [
            {"from": "START", "to": "n"},
            {"from": "n", "to": "END"},
        ],
        "state_schema": [{"name": "x", "type": "str"}],
    }
    result = _call(us, "extensions", "build_branch",
                   spec_json=json.dumps(spec), goal_id=gid)
    assert result["status"] == "built"
    got = _call(us, "extensions", "get_branch",
                branch_def_id=result["branch_def_id"])
    assert got["goal_id"] == gid


def test_patch_branch_set_goal_and_unset_goal(p5_env):
    us, _ = p5_env
    gid = _call(us, "goals", "propose", name="G")["goal"]["goal_id"]
    bid = _build_branch(us)
    patch = [{"op": "set_goal", "goal_id": gid}]
    _call(us, "extensions", "patch_branch",
          branch_def_id=bid, changes_json=json.dumps(patch))
    assert _call(us, "extensions", "get_branch",
                 branch_def_id=bid)["goal_id"] == gid

    _call(us, "extensions", "patch_branch",
          branch_def_id=bid,
          changes_json=json.dumps([{"op": "unset_goal"}]))
    got = _call(us, "extensions", "get_branch", branch_def_id=bid)
    assert got.get("goal_id") in (None, "")


# ─────────────────────────────────────────────────────────────────────────────
# soft-delete semantics
# ─────────────────────────────────────────────────────────────────────────────


def test_soft_delete_is_non_disclosing_on_public_list_and_get(p5_env):
    us, _ = p5_env
    gid = _call(us, "goals", "propose", name="Doomed")["goal"]["goal_id"]
    _soft_delete_goal(gid)

    # list hides it
    lst = _call(us, "goals", "list")
    assert all(g["goal_id"] != gid for g in lst["goals"])

    # Public exact read uses the same non-disclosing shape as a missing Goal.
    got = _call(us, "goals", "get", goal_id=gid)
    missing = _call(us, "goals", "get", goal_id="missing-goal-id")
    assert got.keys() == missing.keys()
    assert got["status"] == missing["status"] == "rejected"


# ─────────────────────────────────────────────────────────────────────────────
# ledger write-through
# ─────────────────────────────────────────────────────────────────────────────


def test_propose_writes_ledger(p5_env):
    us, base = p5_env
    _call(us, "goals", "propose", name="Ledgered")
    ledger = json.loads((Path(base) / "ledger.json").read_text("utf-8"))
    assert any(e["action"] == "goals.propose" for e in ledger)


def test_rejected_propose_does_not_ledger(p5_env):
    us, base = p5_env
    _call(us, "goals", "propose")  # no name → rejected
    ledger_path = Path(base) / "ledger.json"
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text("utf-8"))
    else:
        ledger = []
    assert not any(e["action"] == "goals.propose" for e in ledger)


def test_reads_do_not_ledger(p5_env):
    us, base = p5_env
    gid = _call(us, "goals", "propose", name="G")["goal"]["goal_id"]
    _call(us, "goals", "list")
    _call(us, "goals", "get", goal_id=gid)
    _call(us, "goals", "search", query="G")
    _call(us, "goals", "leaderboard", goal_id=gid, metric="run_count")
    _call(us, "goals", "archive_consultation", goal_id=gid)
    ledger = json.loads((Path(base) / "ledger.json").read_text("utf-8"))
    read_actions = {"goals.list", "goals.get", "goals.search",
                    "goals.leaderboard", "goals.common_nodes",
                    "goals.archive_consultation"}
    for e in ledger:
        assert e["action"] not in read_actions


# ─────────────────────────────────────────────────────────────────────────────
# migration (back-compat)
# ─────────────────────────────────────────────────────────────────────────────


def test_branch_from_pre_phase5_install_reads_cleanly(tmp_path):
    """An installation that existed before Phase 5 has no goal_id column.
    initialize_author_server adds the column; existing rows surface
    goal_id=None without errors."""
    from tinyassets.daemon_server import (
        _connect,
        get_branch_definition,
        initialize_author_server,
        save_branch_definition,
    )

    base = tmp_path

    # Simulate a pre-Phase-5 install by creating the branch_definitions
    # table without goal_id, inserting a row, then running init to
    # trigger the ADD COLUMN path.
    base.mkdir(exist_ok=True)
    with _connect(base) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS branch_definitions (
            branch_def_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            author TEXT NOT NULL DEFAULT 'anonymous',
            domain_id TEXT NOT NULL DEFAULT 'workflow',
            tags_json TEXT NOT NULL DEFAULT '[]',
            version INTEGER NOT NULL DEFAULT 1,
            parent_def_id TEXT,
            entry_point TEXT NOT NULL DEFAULT '',
            graph_json TEXT NOT NULL DEFAULT '{}',
            node_defs_json TEXT NOT NULL DEFAULT '[]',
            state_schema_json TEXT NOT NULL DEFAULT '[]',
            published INTEGER NOT NULL DEFAULT 0,
            stats_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        """)
        conn.execute(
            """
            INSERT INTO branch_definitions (
                branch_def_id, name, created_at, updated_at
            ) VALUES ('legacy-1', 'Legacy', 1.0, 1.0)
            """,
        )

    # Now init: should add the goal_id column without loss.
    initialize_author_server(base)
    got = get_branch_definition(base, branch_def_id="legacy-1")
    assert got["name"] == "Legacy"
    assert got["goal_id"] in (None, "")

    # And new saves work end-to-end.
    new_bid = save_branch_definition(base, branch_def={
        "name": "New",
        "goal_id": "some-goal",
    })["branch_def_id"]
    again = get_branch_definition(base, branch_def_id=new_bid)
    assert again["goal_id"] == "some-goal"
