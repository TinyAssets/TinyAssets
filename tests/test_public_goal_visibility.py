"""Public Goal reads must never disclose non-public Goal records."""

from __future__ import annotations

import json

import pytest

from tinyassets.api.extensions import _extensions_impl as extensions
from tinyassets.api.market import gates, goals
from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import DevAuthProvider
from tinyassets.conformance_packs import record_conformance_pack
from tinyassets.daemon_server import (
    claim_gate,
    delete_goal,
    list_goals,
    save_branch_definition,
    save_goal,
    search_goals,
)
from tinyassets.universe_server import read_graph


def _read_as(actor_id: str) -> None:
    """Bind a NAMED reader. Identity comes from a bound principal, never from
    an environment variable (no anonymous principal, 2026-09-02)."""
    set_provider(DevAuthProvider(user_id=actor_id))
    auth_middleware("bearer")


@pytest.fixture(autouse=True)
def _no_env_identity(monkeypatch):
    monkeypatch.delenv("UNIVERSE_SERVER_USER", raising=False)
    set_provider(DevAuthProvider(user_id="dev-tests"))
    auth_middleware(None)
    yield
    set_provider(DevAuthProvider(user_id="dev-tests"))
    auth_middleware(None)


@pytest.fixture
def goal_catalog(tmp_path, monkeypatch):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    _read_as("authenticated-reader")
    monkeypatch.setenv("GATES_ENABLED", "1")

    public = save_goal(
        base,
        goal={
            "name": "Visible catalog goal",
            "description": "publiconlytoken",
            "author": "public-author",
            "visibility": "public",
        },
    )
    private = save_goal(
        base,
        goal={
            "name": "Private catalog goal",
            "description": "privateonlytoken",
            "author": "private-author",
            "visibility": "private",
        },
    )
    deleted = save_goal(
        base,
        goal={
            "name": "Deleted catalog goal",
            "description": "deletedonlytoken",
            "author": "deleted-author",
            "visibility": "public",
        },
    )
    delete_goal(base, goal_id=deleted["goal_id"])
    unrecognized = save_goal(
        base,
        goal={
            "name": "Internal catalog goal",
            "description": "internalonlytoken",
            "author": "internal-author",
            "visibility": "internal",
        },
    )
    packs = {}
    for label, goal in {
        "public": public,
        "private": private,
        "deleted": deleted,
        "unrecognized": unrecognized,
    }.items():
        packs[label] = record_conformance_pack(
            base,
            goal_id=goal["goal_id"],
            pack={"standard_id": "visibility-test"},
            created_by="pack-author",
        )
    return {
        "base": base,
        "public": public,
        "private": private,
        "deleted": deleted,
        "unrecognized": unrecognized,
        "packs": packs,
    }


def test_storage_catalog_reads_allow_only_exact_public_visibility(goal_catalog):
    base = goal_catalog["base"]

    listed = list_goals(base, limit=100)
    searched_private = search_goals(
        base,
        query="privateonlytoken",
        limit=100,
    )
    searched_public = search_goals(
        base,
        query="publiconlytoken",
        limit=100,
    )

    assert [goal["goal_id"] for goal in listed] == [
        goal_catalog["public"]["goal_id"],
    ]
    assert searched_private == []
    assert [goal["goal_id"] for goal in searched_public] == [
        goal_catalog["public"]["goal_id"],
    ]


@pytest.mark.parametrize(
    "actor",
    ["unrelated-reader", "private-author"],
)
def test_canonical_list_and_ranked_search_do_not_leak_non_public_goals(
    goal_catalog,
    monkeypatch,
    actor,
):
    _read_as(actor)
    listed = json.loads(read_graph(target="goals", limit=100))
    private_author = json.loads(
        read_graph(
            target="goals",
            author="private-author",
            limit=100,
        )
    )
    private_search = json.loads(
        read_graph(
            target="goals",
            query="privateonlytoken",
            limit=100,
        )
    )

    assert [goal["goal_id"] for goal in listed["goals"]] == [
        goal_catalog["public"]["goal_id"],
    ]
    assert private_author["goals"] == []
    assert private_author["count"] == 0
    assert private_author["excluded_count"] == 0
    assert private_search["goals"] == []
    assert private_search["count"] == 0


@pytest.mark.parametrize(
    "actor",
    ["unrelated-reader", "owner"],
)
@pytest.mark.parametrize("visibility_key", ["private", "deleted", "unrecognized"])
def test_exact_canonical_and_legacy_reads_hide_non_public_goal(
    goal_catalog,
    monkeypatch,
    visibility_key,
    actor,
):
    actor_id = (
        goal_catalog[visibility_key]["author"]
        if actor == "owner"
        else actor
    )
    _read_as(actor_id)
    hidden_id = goal_catalog[visibility_key]["goal_id"]
    missing_id = "missing-goal-id"

    canonical_hidden = json.loads(
        read_graph(target="goal", goal_id=hidden_id)
    )
    canonical_missing = json.loads(
        read_graph(target="goal", goal_id=missing_id)
    )
    legacy_hidden = json.loads(goals(action="get", goal_id=hidden_id))
    legacy_missing = json.loads(goals(action="get", goal_id=missing_id))

    assert canonical_hidden.keys() == canonical_missing.keys()
    assert legacy_hidden.keys() == legacy_missing.keys()
    assert canonical_hidden["status"] == canonical_missing["status"] == "rejected"
    assert legacy_hidden["status"] == legacy_missing["status"] == "rejected"
    assert "goal" not in canonical_hidden
    assert "goal" not in legacy_hidden
    assert goal_catalog[visibility_key]["name"] not in json.dumps(canonical_hidden)
    assert goal_catalog[visibility_key]["name"] not in json.dumps(legacy_hidden)


def _call_goal_derived_read(action, goal_id):
    if action == "list_branches":
        return extensions(action=action, goal_id=goal_id)
    if action == "get_ladder":
        return gates(action=action, goal_id=goal_id)
    if action == "gates_leaderboard":
        return gates(action="leaderboard", goal_id=goal_id)
    return goals(action=action, goal_id=goal_id)


@pytest.mark.parametrize(
    "actor",
    ["unrelated-reader", "owner"],
)
@pytest.mark.parametrize("visibility_key", ["private", "deleted", "unrecognized"])
@pytest.mark.parametrize(
    "action",
    [
        "leaderboard",
        "archive_consultation",
        "gates_leaderboard",
        "get_ladder",
        "list_branches",
    ],
)
def test_goal_derived_legacy_reads_have_no_non_public_oracle(
    goal_catalog,
    monkeypatch,
    visibility_key,
    actor,
    action,
):
    actor_id = (
        goal_catalog[visibility_key]["author"]
        if actor == "owner"
        else actor
    )
    _read_as(actor_id)
    hidden_id = goal_catalog[visibility_key]["goal_id"]

    hidden = json.loads(_call_goal_derived_read(action, hidden_id))
    missing = json.loads(_call_goal_derived_read(action, "missing-goal-id"))

    assert hidden.keys() == missing.keys()
    assert hidden["status"] == missing["status"] == "rejected"
    assert goal_catalog[visibility_key]["name"] not in json.dumps(hidden)


def _save_node_branch(base, *, name, goal_id, node_id):
    from tinyassets.branches import (
        BranchDefinition,
        EdgeDefinition,
        GraphNodeRef,
        NodeDefinition,
    )

    node = NodeDefinition(node_id=node_id, display_name=node_id)
    branch = BranchDefinition(
        name=name,
        author="branch-author",
        domain_id="workflow",
        entry_point=node_id,
        graph_nodes=[
            GraphNodeRef(id=node_id, node_def_id=node_id, position=0),
        ],
        edges=[
            EdgeDefinition(from_node="START", to_node=node_id),
            EdgeDefinition(from_node=node_id, to_node="END"),
        ],
        node_defs=[node],
    ).to_dict()
    if goal_id is not None:
        branch["goal_id"] = goal_id
    return save_branch_definition(base, branch_def=branch)


def test_extension_branch_list_cannot_reveal_private_goal_association(
    goal_catalog,
):
    private_id = goal_catalog["private"]["goal_id"]
    _save_node_branch(
        goal_catalog["base"],
        name="private-associated public branch",
        goal_id=private_id,
        node_id="private_associated_node",
    )

    hidden = json.loads(
        extensions(
            action="list_branches",
            goal_id=private_id,
            scope="all",
        )
    )
    missing = json.loads(
        extensions(
            action="list_branches",
            goal_id="missing-goal-id",
            scope="all",
        )
    )

    assert hidden.keys() == missing.keys()
    assert hidden["status"] == missing["status"] == "rejected"
    assert "private_associated_node" not in json.dumps(hidden)


def test_unfiltered_branch_list_excludes_non_public_goal_records(goal_catalog):
    public = _save_node_branch(
        goal_catalog["base"],
        name="public-associated branch",
        goal_id=goal_catalog["public"]["goal_id"],
        node_id="public_associated_node",
    )
    _save_node_branch(
        goal_catalog["base"],
        name="private-associated branch",
        goal_id=goal_catalog["private"]["goal_id"],
        node_id="private_associated_node",
    )
    unbound = _save_node_branch(
        goal_catalog["base"],
        name="unbound branch",
        goal_id=None,
        node_id="unbound_node",
    )

    result = json.loads(extensions(action="list_branches", scope="all"))
    branch_ids = {
        branch["branch_def_id"]
        for branch in result["branches"]
    }
    serialized = json.dumps(result)

    assert branch_ids == {
        public["branch_def_id"],
        unbound["branch_def_id"],
    }
    assert goal_catalog["private"]["goal_id"] not in serialized
    assert "private-associated branch" not in serialized


def test_exact_public_branch_redacts_non_public_goal_association(goal_catalog):
    branch = _save_node_branch(
        goal_catalog["base"],
        name="private-associated public branch",
        goal_id=goal_catalog["private"]["goal_id"],
        node_id="public_branch_node",
    )

    result = json.loads(
        read_graph(target="branch", branch_id=branch["branch_def_id"])
    )

    assert result.get("goal_id") in {None, ""}
    assert goal_catalog["private"]["goal_id"] not in json.dumps(result)


def test_exact_branch_public_claim_survives_private_claim_storage_limit(
    goal_catalog,
    monkeypatch,
):
    branch = _save_node_branch(
        goal_catalog["base"],
        name="over-limit mixed-claim branch",
        goal_id=goal_catalog["public"]["goal_id"],
        node_id="over_limit_mixed_claim_node",
    )
    claim_times = iter([
        f"2026-07-27T00:00:00.{index:06d}+00:00"
        for index in range(102)
    ])
    monkeypatch.setattr(
        "tinyassets.daemon_server._utc_iso_now",
        lambda: next(claim_times),
    )
    public_claim = claim_gate(
        goal_catalog["base"],
        branch_def_id=branch["branch_def_id"],
        goal_id=goal_catalog["public"]["goal_id"],
        rung_key="public-draft",
        evidence_url="https://example.test/public-evidence",
        claimed_by="public-claim-author",
    )
    for index in range(101):
        claim_gate(
            goal_catalog["base"],
            branch_def_id=branch["branch_def_id"],
            goal_id=goal_catalog["private"]["goal_id"],
            rung_key=f"private-rung-{index}",
            evidence_url="https://example.test/private-evidence",
            claimed_by="private-claim-author",
        )

    result = json.loads(
        read_graph(target="branch", branch_id=branch["branch_def_id"])
    )

    assert [claim["claim_id"] for claim in result["gate_claims"]] == [
        public_claim["claim_id"],
    ]
    assert goal_catalog["private"]["goal_id"] not in json.dumps(result)


def test_exact_public_goal_remains_readable(goal_catalog):
    public_id = goal_catalog["public"]["goal_id"]

    canonical = json.loads(read_graph(target="goal", goal_id=public_id))
    legacy = json.loads(goals(action="get", goal_id=public_id))

    assert canonical["goal"]["goal_id"] == public_id
    assert legacy["goal"]["goal_id"] == public_id


# --------------------------------------------------------------------------
# nobody bound: refused before any oracle can exist
# --------------------------------------------------------------------------
