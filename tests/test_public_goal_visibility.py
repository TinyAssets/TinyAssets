"""Public Goal reads must never disclose non-public Goal records."""

from __future__ import annotations

import json

import pytest

from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import DevAuthProvider
from tinyassets.branch_tasks import BranchTask, append_task
from tinyassets.conformance_packs import record_conformance_pack
from tinyassets.daemon_server import (
    claim_gate,
    delete_goal,
    list_goals,
    save_branch_definition,
    save_goal,
    search_goals,
)
from tinyassets.gate_events import attest_gate_event
from tinyassets.subscriptions import subscribe
from tinyassets.universe_server import (
    extensions,
    gates,
    goals,
    read_graph,
    universe,
)


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


def _readers_universe(base, uid):
    """A universe the reader may read. These tests are about which GOALS show
    inside a readable universe. A bare directory is no universe at all: an
    unowned universe grants nothing (2026-09-02), so reading it was denied and
    the goal assertions never ran."""
    from tinyassets.daemon_server import grant_universe_access

    universe_dir = base / uid
    universe_dir.mkdir()
    grant_universe_access(
        base, universe_id=uid, actor_id="authenticated-reader",
        permission="admin", granted_by="authenticated-reader",
    )
    return universe_dir


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
    if action in {
        "list_branches",
        "quality_leaderboard",
        "recommended_parent_for_fork",
    }:
        return extensions(action=action, goal_id=goal_id)
    if action in {"get_ladder", "list_claims", "list_conformance_packs"}:
        return gates(action=action, goal_id=goal_id)
    if action == "gates_leaderboard":
        return gates(action="leaderboard", goal_id=goal_id)
    if action == "common_nodes":
        return goals(
            action=action,
            goal_id=goal_id,
            scope="this_goal",
        )
    return goals(action=action, goal_id=goal_id)


@pytest.mark.parametrize(
    "actor",
    ["unrelated-reader", "owner"],
)
@pytest.mark.parametrize("visibility_key", ["private", "deleted", "unrecognized"])
@pytest.mark.parametrize(
    "action",
    [
        "get_protocol",
        "leaderboard",
        "common_nodes",
        "archive_consultation",
        "gates_leaderboard",
        "get_ladder",
        "list_claims",
        "list_conformance_packs",
        "list_branches",
        "quality_leaderboard",
        "recommended_parent_for_fork",
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


@pytest.mark.parametrize("visibility_key", ["private", "deleted", "unrecognized"])
@pytest.mark.parametrize("actor", ["unrelated-reader", "owner"])
def test_exact_conformance_pack_read_hides_non_public_goal_record(
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
    hidden_pack_id = goal_catalog["packs"][visibility_key].pack_id

    hidden = json.loads(
        gates(
            action="get_conformance_pack",
            conformance_pack_id=hidden_pack_id,
        )
    )
    missing = json.loads(
        gates(
            action="get_conformance_pack",
            conformance_pack_id="missing-pack-id",
        )
    )

    assert hidden.keys() == missing.keys()
    assert hidden["status"] == missing["status"] == "rejected"
    assert (
        goal_catalog[visibility_key]["goal_id"]
        not in json.dumps(hidden)
    )


def test_unfiltered_conformance_pack_list_excludes_non_public_goal_records(
    goal_catalog,
):
    result = json.loads(gates(action="list_conformance_packs", limit=100))
    pack_ids = {
        record["pack_id"]
        for record in result["conformance_packs"]
    }

    assert pack_ids == {goal_catalog["packs"]["public"].pack_id}
    assert result["count"] == 1


def test_public_conformance_pack_survives_newer_private_records_before_limit(
    goal_catalog,
):
    for index in range(501):
        record_conformance_pack(
            goal_catalog["base"],
            goal_id=goal_catalog["private"]["goal_id"],
            pack={
                "standard_id": f"private-visibility-test-{index}",
            },
            created_by="private-pack-author",
        )

    result = json.loads(gates(action="list_conformance_packs", limit=1))

    assert result["count"] == 1
    assert result["conformance_packs"][0]["pack_id"] == (
        goal_catalog["packs"]["public"].pack_id
    )


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


def test_cross_goal_common_nodes_excludes_non_public_goal_contributions(
    goal_catalog,
):
    base = goal_catalog["base"]
    _save_node_branch(
        base,
        name="public branch",
        goal_id=goal_catalog["public"]["goal_id"],
        node_id="public_node",
    )
    _save_node_branch(
        base,
        name="private branch",
        goal_id=goal_catalog["private"]["goal_id"],
        node_id="private_node",
    )
    _save_node_branch(
        base,
        name="unbound branch",
        goal_id=None,
        node_id="unbound_node",
    )

    result = json.loads(
        goals(action="common_nodes", scope="all", min_branches=1, limit=100)
    )
    node_ids = {entry["node_id"] for entry in result["entries"]}
    serialized = json.dumps(result)

    assert node_ids == {"public_node", "unbound_node"}
    assert goal_catalog["private"]["goal_id"] not in serialized
    assert "private_node" not in serialized


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


def test_node_search_excludes_non_public_goal_contributions(goal_catalog):
    _save_node_branch(
        goal_catalog["base"],
        name="public shared-node branch",
        goal_id=goal_catalog["public"]["goal_id"],
        node_id="shared_visibility_node",
    )
    _save_node_branch(
        goal_catalog["base"],
        name="private shared-node branch",
        goal_id=goal_catalog["private"]["goal_id"],
        node_id="shared_visibility_node",
    )
    _save_node_branch(
        goal_catalog["base"],
        name="private-only-node branch",
        goal_id=goal_catalog["private"]["goal_id"],
        node_id="private_only_visibility_node",
    )

    shared = json.loads(
        extensions(action="search_nodes", node_query="shared_visibility_node")
    )
    private_only = json.loads(
        extensions(
            action="search_nodes",
            node_query="private_only_visibility_node",
        )
    )

    assert shared["count"] == 1
    assert shared["entries"][0]["reuse_count"] == 1
    assert shared["entries"][0]["goal_ids"] == [
        goal_catalog["public"]["goal_id"],
    ]
    assert private_only["entries"] == []
    assert private_only["count"] == 0
    assert goal_catalog["private"]["goal_id"] not in json.dumps(shared)
    assert goal_catalog["private"]["goal_id"] not in json.dumps(private_only)


def test_branch_filtered_gate_claims_exclude_non_public_goal_records(
    goal_catalog,
):
    branch = _save_node_branch(
        goal_catalog["base"],
        name="private-claim branch",
        goal_id=goal_catalog["private"]["goal_id"],
        node_id="private_claim_node",
    )
    claim_gate(
        goal_catalog["base"],
        branch_def_id=branch["branch_def_id"],
        goal_id=goal_catalog["private"]["goal_id"],
        rung_key="draft",
        evidence_url="https://example.test/evidence",
        claimed_by="claim-author",
    )

    result = json.loads(
        gates(
            action="list_claims",
            branch_def_id=branch["branch_def_id"],
            limit=100,
        )
    )

    assert result["claims"] == []
    assert result["count"] == 0
    assert goal_catalog["private"]["goal_id"] not in json.dumps(result)


def test_public_gate_claim_survives_newer_private_claim_before_limit(
    goal_catalog,
    monkeypatch,
):
    branch = _save_node_branch(
        goal_catalog["base"],
        name="mixed-claim branch",
        goal_id=goal_catalog["public"]["goal_id"],
        node_id="mixed_claim_node",
    )
    claim_times = iter([
        "2026-07-27T00:00:00+00:00",
        "2026-07-27T00:00:01+00:00",
    ])
    monkeypatch.setattr(
        "tinyassets.daemon_server._utc_iso_now",
        lambda: next(claim_times),
    )
    public_claim = claim_gate(
        goal_catalog["base"],
        branch_def_id=branch["branch_def_id"],
        goal_id=goal_catalog["public"]["goal_id"],
        rung_key="draft",
        evidence_url="https://example.test/public-evidence",
        claimed_by="public-claim-author",
    )
    claim_gate(
        goal_catalog["base"],
        branch_def_id=branch["branch_def_id"],
        goal_id=goal_catalog["private"]["goal_id"],
        rung_key="reviewed",
        evidence_url="https://example.test/private-evidence",
        claimed_by="private-claim-author",
    )

    result = json.loads(
        gates(
            action="list_claims",
            branch_def_id=branch["branch_def_id"],
            limit=1,
        )
    )

    assert result["count"] == 1
    assert result["claims"][0]["claim_id"] == public_claim["claim_id"]


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


@pytest.mark.parametrize("actor", ["unrelated-reader", "owner"])
def test_gate_event_reads_exclude_non_public_goal_records(
    goal_catalog,
    monkeypatch,
    actor,
):
    actor_id = (
        goal_catalog["private"]["author"]
        if actor == "owner"
        else actor
    )
    _read_as(actor_id)
    public_event = attest_gate_event(
        goal_catalog["base"],
        goal_id=goal_catalog["public"]["goal_id"],
        event_type="publication",
        event_date="2026-07-27",
        attested_by="event-author",
        cites=[],
    )
    private_event = attest_gate_event(
        goal_catalog["base"],
        goal_id=goal_catalog["private"]["goal_id"],
        event_type="private-milestone",
        event_date="2026-07-27",
        attested_by="event-author",
        cites=[],
    )

    listed = json.loads(extensions(action="list_gate_events", limit=100))
    hidden = json.loads(
        extensions(
            action="get_gate_event",
            event_id=private_event.event_id,
        )
    )
    missing = json.loads(
        extensions(
            action="get_gate_event",
            event_id="missing-event-id",
        )
    )

    assert [event["event_id"] for event in listed["events"]] == [
        public_event.event_id,
    ]
    assert listed["count"] == 1
    assert hidden.keys() == missing.keys()
    assert goal_catalog["private"]["goal_id"] not in json.dumps(hidden)


def test_public_gate_event_survives_newer_private_record_before_limit(
    goal_catalog,
):
    public_event = attest_gate_event(
        goal_catalog["base"],
        goal_id=goal_catalog["public"]["goal_id"],
        event_type="publication",
        event_date="2026-07-27",
        attested_by="event-author",
        cites=[],
    )
    attest_gate_event(
        goal_catalog["base"],
        goal_id=goal_catalog["private"]["goal_id"],
        event_type="private-milestone",
        event_date="2026-07-27",
        attested_by="event-author",
        cites=[],
    )

    result = json.loads(extensions(action="list_gate_events", limit=1))

    assert result["count"] == 1
    assert result["events"][0]["event_id"] == public_event.event_id


@pytest.mark.parametrize("action", ["list_subscriptions", "daemon_overview"])
def test_universe_goal_record_reads_exclude_non_public_subscriptions(
    goal_catalog,
    monkeypatch,
    tmp_path,
    action,
):
    uid = "subscriber-universe"
    universe_dir = _readers_universe(goal_catalog["base"], uid)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bids").mkdir()
    monkeypatch.setenv("TINYASSETS_REPO_ROOT", str(repo))
    monkeypatch.setenv("TINYASSETS_GOAL_POOL", "on")
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", uid)
    subscribe(universe_dir, goal_catalog["public"]["goal_id"])
    subscribe(universe_dir, goal_catalog["private"]["goal_id"])

    from tinyassets.api.universe import _OVERVIEW_CACHE

    _OVERVIEW_CACHE.clear()
    result = json.loads(universe(action=action, universe_id=uid))
    serialized = json.dumps(result)

    assert goal_catalog["public"]["goal_id"] in serialized
    assert goal_catalog["private"]["goal_id"] not in serialized


def test_universe_queue_excludes_non_public_goal_tasks(
    goal_catalog,
    monkeypatch,
):
    uid = "queue-universe"
    universe_dir = _readers_universe(goal_catalog["base"], uid)
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", uid)
    for label, goal_id in (
        ("public", goal_catalog["public"]["goal_id"]),
        ("private", goal_catalog["private"]["goal_id"]),
        ("unbound", ""),
        ("legacy-topic", "maintenance"),
    ):
        append_task(
            universe_dir,
            BranchTask(
                branch_task_id=f"{label}-task",
                branch_def_id=f"{label}-branch",
                universe_id=uid,
                goal_id=goal_id,
            ),
        )

    def per_record_goal_lookup_is_forbidden(*_args, **_kwargs):
        raise AssertionError("queue visibility must batch Goal resolution")

    monkeypatch.setattr(
        "tinyassets.daemon_server.get_goal",
        per_record_goal_lookup_is_forbidden,
    )
    result = json.loads(universe(action="queue_list", universe_id=uid))
    task_ids = {
        row["branch_task_id"]
        for row in result["queue"]
    }

    assert task_ids == {
        "public-task",
        "unbound-task",
        "legacy-topic-task",
    }
    assert result["pending_count"] == 3
    assert goal_catalog["private"]["goal_id"] not in json.dumps(result)


def test_universe_ledger_excludes_non_public_goal_records(
    goal_catalog,
    monkeypatch,
):
    uid = "ledger-universe"
    universe_dir = _readers_universe(goal_catalog["base"], uid)
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", uid)
    entries = [
        {
            "action": "subscribe_goal",
            "target": goal_catalog["public"]["goal_id"],
            "summary": "public subscription",
            "payload": {},
        },
        {
            "action": "subscribe_goal",
            "target": goal_catalog["private"]["goal_id"],
            "summary": "private subscription",
            "payload": {},
        },
    ]
    (universe_dir / "ledger.json").write_text(
        json.dumps(entries),
        encoding="utf-8",
    )

    def per_record_goal_lookup_is_forbidden(*_args, **_kwargs):
        raise AssertionError("ledger visibility must batch Goal resolution")

    monkeypatch.setattr(
        "tinyassets.daemon_server.get_goal",
        per_record_goal_lookup_is_forbidden,
    )
    result = json.loads(universe(action="get_ledger", universe_id=uid))

    assert result["count"] == 1
    assert result["entries"][0]["target"] == goal_catalog["public"]["goal_id"]
    assert goal_catalog["private"]["goal_id"] not in json.dumps(result)


@pytest.mark.parametrize("universe_alias", [".", "./", ".\\", "u1/.."])
def test_universe_ledger_rejects_global_ledger_alias(
    goal_catalog,
    universe_alias,
):
    private_goal = goal_catalog["private"]
    (goal_catalog["base"] / "ledger.json").write_text(
        json.dumps([
            {
                "action": "goals.update",
                "target": private_goal["goal_id"],
                "summary": f"Updated Goal {private_goal['name']}",
                "payload": None,
            },
        ]),
        encoding="utf-8",
    )

    result = json.loads(
        universe(action="get_ledger", universe_id=universe_alias)
    )
    serialized = json.dumps(result)

    assert "error" in result
    assert private_goal["goal_id"] not in serialized
    assert private_goal["name"] not in serialized


def test_exact_public_goal_remains_readable(goal_catalog):
    public_id = goal_catalog["public"]["goal_id"]

    canonical = json.loads(read_graph(target="goal", goal_id=public_id))
    legacy = json.loads(goals(action="get", goal_id=public_id))

    assert canonical["goal"]["goal_id"] == public_id
    assert legacy["goal"]["goal_id"] == public_id


# --------------------------------------------------------------------------
# nobody bound: refused before any oracle can exist
# --------------------------------------------------------------------------


@pytest.mark.parametrize("visibility_key", ["private", "deleted", "unrecognized"])
def test_a_read_with_nobody_bound_is_refused_before_any_oracle(goal_catalog, visibility_key):
    """There is no anonymous reader (founder, 2026-09-02). With nobody bound
    every goal read refuses with the same authentication error whether the
    goal is hidden or missing, and the hidden name never appears."""
    auth_middleware(None)
    hidden_id = goal_catalog[visibility_key]["goal_id"]
    name = goal_catalog[visibility_key]["name"]

    def _refused(call):
        try:
            payload = json.loads(call())
        except PermissionError as exc:
            assert "Authentication required" in str(exc)
            return {"error": "Authentication required"}
        assert payload.get("error") == "Authentication required", payload
        assert "goal" not in payload and "goals" not in payload
        return payload

    for goal_id in (hidden_id, "missing-goal-id"):
        outs = [
            _refused(lambda: read_graph(target="goals", limit=100)),
            _refused(lambda: read_graph(target="goal", goal_id=goal_id)),
            _refused(lambda: goals(action="get", goal_id=goal_id)),
            _refused(lambda: _call_goal_derived_read("leaderboard", goal_id)),
            _refused(lambda: _call_goal_derived_read("get_ladder", goal_id)),
            _refused(lambda: _call_goal_derived_read("list_branches", goal_id)),
        ]
        assert all(name not in json.dumps(o) for o in outs)
