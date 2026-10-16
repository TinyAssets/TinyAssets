"""A universe can delete its own branches from the surfaces it actually uses.

Tiny, in the app, 2026-09-02, asked "are you able to delete branches yet?":

    Not yet. I can list branches, read them, run them, create them, and patch
    them, but I do not have a branch delete operation exposed right now.
    I counted 106 branches in the current universe.

`delete_branch` has existed for months behind the deprecated `extensions`
tool, author-gated. It was never put on `write_graph`, on either served
surface. Inside your universe you are god; the only invariant is not
affecting other users. So: an OWN branch that nothing of the owner's depends
on deletes, public or private (founder, 2026-09-02: a public branch is a
shape others copy or remix into their own universe; it runs nothing for
anyone else); one with dependents is refused with every dependent named;
internal patch snapshots are not dependents (Codex on the first cut: every
patch mints two, so counting them made any edited branch undeletable).
"""
from __future__ import annotations

import json

import pytest


def _spec(name: str, *, invokes: str = "") -> dict:
    node = {"node_id": "ready", "display_name": "Ready", "prompt_template": "Do the work."}
    if invokes:
        node = {
            "node_id": "ready",
            "display_name": "Ready",
            "invoke_branch_spec": {"branch_def_id": invokes, "wait_mode": "blocking"},
        }
    return {
        "name": name,
        "entry_point": "ready",
        "node_defs": [node],
        "edges": [{"from": "START", "to": "ready"}, {"from": "ready", "to": "END"}],
        "state_schema": [{"name": "x", "type": "str"}],
    }


# ------------------------------------------- the universe/app surface (real)


@pytest.fixture
def universe_surface(monkeypatch, tmp_path):
    import tinyassets.universe_server as us
    from tinyassets.api import permissions

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(us, "write_gate_rejection", lambda name: None)
    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: True)
    actor = {"id": "alice"}
    monkeypatch.setattr(permissions, "current_actor_id", lambda: actor["id"])
    # Branch authoring reads the credential-validated request subject, never an
    # env actor (`_request_branch_actor`).
    monkeypatch.setattr(permissions, "current_request_actor_id", lambda: actor["id"])
    return us, actor


def _create(us, name: str, **kw) -> str:
    out = json.loads(us.write_graph(
        target="branch", operation="create", payload_json=json.dumps(_spec(name, **kw)),
        idempotency_key=f"create-{name}-0123456789",
    ))
    assert "branch_def_id" in out, out
    return out["branch_def_id"]


def _delete(us, bid: str) -> dict:
    return json.loads(us.write_graph(target="branch", operation="delete", branch_id=bid))


def _make_private(us, bid: str) -> None:
    out = json.loads(us.write_graph(
        target="branch", operation="patch", branch_id=bid,
        changes_json=json.dumps([{"op": "set_visibility", "visibility": "private"}]),
    ))
    assert "error" not in out, out


def _make_public(us, bid: str) -> None:
    out = json.loads(us.write_graph(
        target="branch", operation="patch", branch_id=bid,
        changes_json=json.dumps([{"op": "set_visibility", "visibility": "public"}]),
    ))
    assert "error" not in out, out


def _listed(us) -> set[str]:
    out = json.loads(us.read_graph(target="branches"))
    return {b["branch_def_id"] for b in out.get("branches", [])}


def test_an_OWN_private_branch_deletes_and_is_gone_from_the_listing(universe_surface):
    us, _actor = universe_surface
    bid = _create(us, "probe")
    assert bid in _listed(us)

    out = _delete(us, bid)

    assert out == {"branch_def_id": bid, "status": "deleted"}, out
    assert bid not in _listed(us)


def test_a_branch_that_was_PATCHED_still_deletes(universe_surface):
    """Every patch mints pre- and post-patch version snapshots. They are not a
    publication and must not make the branch undeletable."""
    us, _actor = universe_surface
    bid = _create(us, "edited")
    patched = json.loads(us.write_graph(
        target="branch", operation="patch", branch_id=bid,
        changes_json=json.dumps([{"op": "set_description", "description": "edited once"}]),
    ))
    assert "error" not in patched, patched

    assert _delete(us, bid)["status"] == "deleted"
    assert bid not in _listed(us)


def test_another_authors_PRIVATE_branch_reads_as_not_found(universe_surface):
    us, actor = universe_surface
    bid = _create(us, "alices")
    actor["id"] = "mallory"

    out = _delete(us, bid)

    assert out.get("error", "").startswith("Branch '"), out
    actor["id"] = "alice"
    assert bid in _listed(us), "a non-author deleted someone else's branch"


def test_a_non_author_cannot_even_probe_a_PUBLIC_branch_for_deletion(universe_surface):
    """A public branch is readable by anyone, so the resolve step lets a
    non-author through; the author gate must still answer not-found before the
    public reason is given, or the gate is decoration."""
    us, actor = universe_surface
    bid = _create(us, "alices-public")
    _make_public(us, bid)
    actor["id"] = "mallory"

    out = _delete(us, bid)

    assert out.get("error", "").startswith("Branch '"), out
    assert out.get("error") != "branch_has_dependents"
    actor["id"] = "alice"
    assert bid in _listed(us)


def test_a_PUBLIC_branch_deletes_like_any_other(universe_surface):
    """Founder, 2026-09-02: a public branch is a shape others copy or remix into
    their own universe; it runs nothing for anyone else. So there is nothing to
    protect on the far side of a delete, and the owner's own branch is theirs."""
    us, _actor = universe_surface
    bid = _create(us, "commons")
    _make_public(us, bid)

    assert _delete(us, bid)["status"] == "deleted"
    assert bid not in _listed(us)


# ------------------------------------------------ dependents are named, never broken


def test_an_ACTIVE_automation_bound_to_the_branch_is_named_and_nothing_is_deleted(
    universe_surface, tmp_path,
):
    """Registration promises never to store an automation that cannot fire;
    deleting its branch would create exactly that, degrading asynchronously."""
    from tinyassets.automations import Automation, AutomationStore

    us, _actor = universe_surface
    bid = _create(us, "nightly-source")
    AutomationStore(tmp_path).insert(Automation(
        automation_id="auto-1", universe_id="u-1", owner_principal_id="alice",
        name="Nightly", branch_def_id=bid, trigger_kind="interval",
        interval_seconds=3600, cron_expr="", inputs={}, desired_state="active",
        pause_reason="", revision=1, created_at="2026-09-02T00:00:00Z",
        updated_at="2026-09-02T00:00:00Z", retired_at="", last_due_at="",
        last_run_id="", last_reason="", last_finished_at="",
    ))

    out = _delete(us, bid)

    assert out.get("error") == "branch_has_dependents", out
    assert out["dependents"]["automations"] == ["auto-1"]
    assert bid in _listed(us)


def test_a_RETIRED_automation_does_not_hold_the_branch(universe_surface, tmp_path):
    from tinyassets.automations import Automation, AutomationStore

    us, _actor = universe_surface
    bid = _create(us, "was-nightly")
    AutomationStore(tmp_path).insert(Automation(
        automation_id="auto-old", universe_id="u-1", owner_principal_id="alice",
        name="Old", branch_def_id=bid, trigger_kind="interval",
        interval_seconds=3600, cron_expr="", inputs={}, desired_state="paused",
        pause_reason="retired", revision=2, created_at="2026-09-01T00:00:00Z",
        updated_at="2026-09-02T00:00:00Z", retired_at="2026-09-02T00:00:00Z",
        last_due_at="", last_run_id="", last_reason="", last_finished_at="",
    ))

    assert _delete(us, bid)["status"] == "deleted"


def test_a_goal_pinned_to_one_of_its_versions_is_named(universe_surface):
    """`invoke_branch_version` maps a version back to its definition; a goal
    whose canonical binding points at a version of this branch would break."""
    us, _actor = universe_surface
    bid = _create(us, "canon-source")
    published = json.loads(us.write_graph(target="branch", operation="publish", branch_id=bid))
    version_id = published.get("branch_version_id")
    assert version_id, published
    goal = json.loads(us.write_graph(target="goal", name="Ship it", description="a goal"))
    goal_id = goal.get("goal_id") or (goal.get("goal") or {}).get("goal_id")
    assert goal_id, goal
    bound = json.loads(us.write_graph(
        target="goal", operation="set_canonical", goal_id=goal_id, branch_version_id=version_id,
    ))
    assert "error" not in bound, bound

    out = _delete(us, bid)

    assert out.get("error") == "branch_has_dependents", out
    assert goal_id in out["dependents"]["goals"]
    assert bid in _listed(us)


def test_a_branch_that_INVOKES_this_one_is_named(universe_surface):
    us, _actor = universe_surface
    child = _create(us, "child")
    parent = _create(us, "parent", invokes=child)

    out = _delete(us, child)

    assert out.get("error") == "branch_has_dependents", out
    assert out["dependents"]["branches"] == [parent]
    assert child in _listed(us)
    # Delete the invoker first, and the child is free.
    assert _delete(us, parent)["status"] == "deleted"
    assert _delete(us, child)["status"] == "deleted"


def test_an_ACTIVE_webhook_bound_to_the_branch_is_named(universe_surface, tmp_path):
    """Codex round 2: a minted hook resolves its token to the branch at every
    delivery; deleting the branch leaves the hook active and every delivery
    failing."""
    from tinyassets.storage import webhook_hooks

    us, _actor = universe_surface
    bid = _create(us, "hooked")
    token = webhook_hooks.mint(
        tmp_path,
        universe_id="u-1",
        branch_def_id=bid,
        owner_principal_id="owner-test",
    )

    out = _delete(us, bid)
    assert out.get("error") == "branch_has_dependents", out
    [hook] = out["dependents"]["webhooks"]
    assert hook.startswith("hook:") and token.startswith(hook[len("hook:"):]), hook
    assert bid in _listed(us)

    assert webhook_hooks.revoke(tmp_path, token=token) is True
    assert _delete(us, bid)["status"] == "deleted"


def test_an_ACTIVE_subscription_is_named_and_a_retired_schedule_is_not(
    universe_surface, tmp_path,
):
    """Schedules are retired: a leftover row that was active is stamped retired
    by the scheduler migration, so it no longer holds the branch."""
    from tinyassets.control_plane import events as scheduler

    us, _actor = universe_surface
    bid = _create(us, "scheduled")
    # The scheduler lays its tables down at daemon start; do the same here.
    with scheduler._connect(scheduler._runs_db(tmp_path)) as conn:
        conn.executescript(scheduler.SCHEDULER_SCHEMA)
        # A row the retired tick loop would once have fired.
        conn.execute(
            "INSERT INTO branch_schedules (schedule_id, branch_def_id, owner_actor, "
            "universe_id, owner_principal_id, interval_seconds, active, created_at) "
            "VALUES ('sched-old', ?, 'universe:u-1', 'u-1', 'alice', 3600, 1, 0)",
            (bid,),
        )
    sub = scheduler.register_subscription(
        tmp_path, branch_def_id=bid, owner_actor="universe:u-1", event_type="source:s1",
    )

    out = _delete(us, bid)
    assert out.get("error") == "branch_has_dependents", out
    assert out["dependents"]["schedules"] == []
    assert out["dependents"]["subscriptions"] == [sub]

    # Deactivation authority is the scheduler's own concern, not this test's.
    assert scheduler.unregister_subscription(
        tmp_path, sub, requesting_actor="alice", admin=True,
    ) is True
    assert _delete(us, bid)["status"] == "deleted"


def test_a_default_canonical_recorded_ONLY_in_canonical_bindings_is_named(
    universe_surface, tmp_path,
):
    """A live database can hold a default canonical in `canonical_bindings`
    alone (the personal table and the legacy column both empty). The
    set_canonical path today writes all three, which is why a test through it
    cannot tell the readers apart; this one writes the single store directly."""
    import sqlite3

    from tinyassets.storage import db_path

    us, _actor = universe_surface
    bid = _create(us, "bindings-only")
    published = json.loads(us.write_graph(target="branch", operation="publish", branch_id=bid))
    version_id = published["branch_version_id"]
    goal = json.loads(us.write_graph(target="goal", name="Bound", description="a goal"))
    goal_id = goal.get("goal_id") or (goal.get("goal") or {}).get("goal_id")
    conn = sqlite3.connect(db_path(tmp_path))
    try:
        conn.execute(
            "INSERT INTO canonical_bindings (goal_id, scope_token, branch_version_id, "
            "bound_by_actor_id, bound_at) VALUES (?, '', ?, 'alice', 0)",
            (goal_id, version_id),
        )
        conn.commit()
    finally:
        conn.close()

    out = _delete(us, bid)
    assert out.get("error") == "branch_has_dependents", out
    assert goal_id in out["dependents"]["goals"]


def test_a_PERSONAL_canonical_is_named_too(universe_surface, tmp_path):
    """Actor-scoped canonicals live in `goal_canonicals`, read before the
    goal default; the first cut queried only the default table."""
    us, _actor = universe_surface
    bid = _create(us, "personal-canon")
    published = json.loads(us.write_graph(target="branch", operation="publish", branch_id=bid))
    version_id = published["branch_version_id"]
    goal = json.loads(us.write_graph(target="goal", name="Mine", description="a goal"))
    goal_id = goal.get("goal_id") or (goal.get("goal") or {}).get("goal_id")
    from tinyassets.daemon_server import set_goal_canonical

    set_goal_canonical(
        tmp_path, goal_id=goal_id, scope_actor="alice",
        branch_version_id=version_id, set_by="alice",
    )

    out = _delete(us, bid)
    assert out.get("error") == "branch_has_dependents", out
    assert goal_id in out["dependents"]["goals"]


def test_an_invocation_inside_another_branchs_PUBLISHED_SNAPSHOT_is_named(
    universe_surface, tmp_path,
):
    """Codex round 2: a published snapshot is executable on its own and its
    invoke node reloads the child live -- even after the parent's CURRENT
    definition stopped naming the child (here: the parent's definition is
    gone entirely, its snapshot remains)."""
    from tinyassets.daemon_server import delete_branch_definition

    us, _actor = universe_surface
    child = _create(us, "snap-child")
    parent = _create(us, "snap-parent", invokes=child)
    published = json.loads(us.write_graph(target="branch", operation="publish", branch_id=parent))
    assert published.get("branch_version_id"), published
    assert delete_branch_definition(tmp_path, branch_def_id=parent) is True

    out = _delete(us, child)
    assert out.get("error") == "branch_has_dependents", out
    assert out["dependents"]["branches"] == [parent]


def test_version_ids_are_read_uncapped(tmp_path):
    """`list_branch_versions` caps at 500; a dependency check must not."""
    from tinyassets.branch_versions import (
        list_branch_versions,
        list_version_ids,
        publish_branch_version,
    )

    branch = {"branch_def_id": "b-many", "name": "many", "author": "alice", "visibility": "private",
              "entry_point": "ready", "node_defs": [{"node_id": "ready", "display_name": "R",
              "prompt_template": "x"}], "edges": [], "state_schema": []}
    for i in range(505):
        branch["node_defs"][0]["prompt_template"] = f"x{i}"
        publish_branch_version(tmp_path, branch, publisher="alice", notes=str(i))
    assert len(list_branch_versions(tmp_path, "b-many", limit=500)) == 500
    assert len(list_version_ids(tmp_path, "b-many")) == 505


def test_a_universe_whose_soul_declares_it_as_the_LOOP_branch_is_named(universe_surface, tmp_path):
    """Codex round 3: request admission queues the universe's declared loop
    branch for every incoming request; deleting it leaves the universe
    queuing a workflow that does not exist."""
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import set_founder_home
    from tinyassets.universe_soul import write_universe_soul

    us, _actor = universe_surface
    bid = _create(us, "the-loop")
    udir = _base_path() / "u-loop"
    udir.mkdir()
    # ...and an OWNER, or it is not a universe and cannot be a dependent
    # (2026-09-02 -- tests/test_a_universe_needs_an_owner.py).
    set_founder_home(_base_path(), founder_sub="test-owner::u-loop", universe_id="u-loop")
    write_universe_soul(udir, name="Loop universe", purpose="runs the loop", loop_branch_def_id=bid)

    out = _delete(us, bid)
    assert out.get("error") == "branch_has_dependents", out
    assert out["dependents"]["universes"] == ["u-loop"]
    assert bid in _listed(us)

    write_universe_soul(udir, clear_loop_branch=True)
    assert _delete(us, bid)["status"] == "deleted"


def test_an_UNOWNED_directory_carrying_the_soul_is_not_a_dependent(
    universe_surface, tmp_path,
):
    """An archived or restored directory keeps the ``soul.md`` it was archived
    with, so scanning every directory named one as a live dependent -- a wrong
    refusal, and an id the caller should never have seen. A universe exists
    because an ownership row says so (2026-09-02)."""
    from tinyassets.api.helpers import _base_path
    from tinyassets.universe_soul import write_universe_soul

    us, _actor = universe_surface
    bid = _create(us, "the-loop")
    # Owner files are written only through an admitted box; an archive has no
    # owner to admit it, so its soul is written aside and the directory moved in.
    staged = tmp_path / "archive-staging" / "removed-universe"
    staged.mkdir(parents=True)
    write_universe_soul(
        staged, name="Archived", purpose="used to run the loop",
        loop_branch_def_id=bid,
    )
    archived = _base_path() / "_removed_universes_20260829"
    staged.rename(archived)
    assert (archived / "soul.md").is_file()

    assert _delete(us, bid)["status"] == "deleted"


def test_a_FOREIGN_snapshot_from_the_public_days_does_not_block_forever(universe_surface, tmp_path):
    """Codex round 3: the owner cannot edit another author's branch, so a
    foreign published snapshot that invoked this branch while it was public
    must not hold it. Under the founder's model a public branch runs nothing
    for anyone else anyway: that invoker should have remixed."""
    us, actor = universe_surface
    child = _create(us, "was-public")
    _make_public(us, child)
    actor["id"] = "bob"
    parent = _create(us, "bobs-parent", invokes=child)
    published = json.loads(us.write_graph(target="branch", operation="publish", branch_id=parent))
    assert published.get("branch_version_id"), published
    actor["id"] = "alice"
    _make_private(us, child)

    out = _delete(us, child)
    assert out.get("status") == "deleted", out


def test_a_prompt_that_merely_MENTIONS_the_id_is_not_a_dependent(universe_surface):
    """Dependents come from the structured child-ref fields, never free text."""
    us, _actor = universe_surface
    bid = _create(us, "mentioned")
    spec = _spec("mentioner")
    spec["node_defs"][0]["prompt_template"] = f"see branch {bid} for context"
    out = json.loads(us.write_graph(
        target="branch", operation="create", payload_json=json.dumps(spec),
        idempotency_key="create-mentioner-0123456789",
    ))
    assert "branch_def_id" in out, out

    assert _delete(us, bid)["status"] == "deleted"


def test_delete_needs_a_branch_id(universe_surface):
    us, _actor = universe_surface
    assert "error" in json.loads(us.write_graph(target="branch", operation="delete"))


def test_the_tool_text_names_delete_and_both_refusals(universe_surface):
    us, _ = universe_surface
    import inspect

    doc = (inspect.getdoc(us.write_graph) or "").lower()
    assert "delete" in doc and "public" in doc and "depend" in doc


# ------------------------------------------ the served build surface (engine)


def _bind(monkeypatch, tmp_path, *, actor="sub-9", graph="u-9", allow=("u-9",)):
    from tinyassets import engine_mcp_server as s

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(s, "_ACTOR_ID", actor)
    monkeypatch.setattr(s, "_GRAPH_ID", graph)
    # Admission is isolated here; downstream operation/consent guards stay real.
    from tests.engine_authority_helpers import mock_engine_admission
    mock_engine_admission(monkeypatch, allow)
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    return s


def test_served_delete_runs_the_REAL_handler_under_the_bound_identity(monkeypatch, tmp_path):
    """No stub: the served surface creates a branch as the bound identity, then
    deletes it through the guarded handler, and the listing no longer has it."""
    s = _bind(monkeypatch, tmp_path)
    created = json.loads(s.write_graph(
        target="branch", operation="create", payload_json=json.dumps(_spec("served-probe")),
        idempotency_key="served-create-0123456789",
    ))
    bid = created.get("branch_def_id")
    assert bid, created
    listed = json.loads(s.read_graph(target="branches"))
    assert bid in {b["branch_def_id"] for b in listed.get("branches", [])}

    out = json.loads(s.write_graph(target="branch", operation="delete", branch_id=bid))

    assert out == {"branch_def_id": bid, "status": "deleted"}, out
    listed = json.loads(s.read_graph(target="branches"))
    assert bid not in {b["branch_def_id"] for b in listed.get("branches", [])}


def test_served_delete_refuses_another_identitys_branch(monkeypatch, tmp_path):
    s = _bind(monkeypatch, tmp_path, actor="sub-9")
    created = json.loads(s.write_graph(
        target="branch", operation="create", payload_json=json.dumps(_spec("mine")),
        idempotency_key="served-create-mine-0123456789",
    ))
    bid = created["branch_def_id"]
    s2 = _bind(monkeypatch, tmp_path, actor="sub-other", graph="u-other", allow=("u-other",))

    out = json.loads(s2.write_graph(target="branch", operation="delete", branch_id=bid))

    assert "error" in out and "deleted" not in json.dumps(out)


def test_served_delete_reaches_the_GUARDED_handler_not_the_raw_one(monkeypatch, tmp_path):
    """The raw `delete_branch` would delete a branch an active automation is
    bound to; the guarded one names the automation instead."""
    from tinyassets.automations import Automation, AutomationStore

    s = _bind(monkeypatch, tmp_path, actor="sub-9")
    created = json.loads(s.write_graph(
        target="branch", operation="create", payload_json=json.dumps(_spec("automated")),
        idempotency_key="served-create-automated-0123456789",
    ))
    bid = created["branch_def_id"]
    AutomationStore(tmp_path).insert(Automation(
        automation_id="auto-served", universe_id="u-9", owner_principal_id="sub-9",
        name="Nightly", branch_def_id=bid, trigger_kind="interval",
        interval_seconds=3600, cron_expr="", inputs={}, desired_state="active",
        pause_reason="", revision=1, created_at="2026-09-02T00:00:00Z",
        updated_at="2026-09-02T00:00:00Z", retired_at="", last_due_at="",
        last_run_id="", last_reason="", last_finished_at="",
    ))

    out = json.loads(s.write_graph(target="branch", operation="delete", branch_id=bid))

    assert out.get("error") == "branch_has_dependents", out
    assert out["dependents"]["automations"] == ["auto-served"]


def test_served_delete_requires_branch_id(monkeypatch, tmp_path):
    s = _bind(monkeypatch, tmp_path)
    out = json.loads(s.write_graph(target="branch", operation="delete"))
    assert "error" in out and "branch_id" in out["error"]


def test_served_surface_advertises_delete(monkeypatch, tmp_path):
    s = _bind(monkeypatch, tmp_path)
    out = json.loads(s.write_graph(target="branch", operation="destroy", branch_id="b-1"))
    assert "delete" in out["error"], "the refusal does not name the operation that exists"
    import inspect

    doc = (inspect.getdoc(s.write_graph) or "").lower()
    assert "delete" in doc and "public" in doc and "depend" in doc


def test_dependency_scan_continues_after_one_box_refusal(universe_surface, tmp_path, monkeypatch):
    from tinyassets.daemon_server import set_founder_home
    from tinyassets.universe_soul import write_universe_soul

    us, _actor = universe_surface
    bid = _create(us, "scan-loop")
    for uid in ("a-refused", "b-loop"):
        center = tmp_path / uid
        center.mkdir()
        set_founder_home(tmp_path, founder_sub=f"test-owner::{uid}", universe_id=uid)
        write_universe_soul(center, name=uid, purpose="loop", loop_branch_def_id=bid)
    from tinyassets.broker import owner_identities

    original = owner_identities.admitted_owner

    def admitted(root, *, center):
        if center == "a-refused":
            raise PermissionError("not admitted")
        return original(root, center=center)

    monkeypatch.setattr(owner_identities, "admitted_owner", admitted)
    out = _delete(us, bid)
    assert out["error"] == "branch_has_dependents"
    assert out["dependents"]["universes"] == ["a-refused?", "b-loop"]
    assert out["dependents"]["unavailable"] == [{
        "universe_id": "a-refused", "loop_branch_def_id": None,
        "unavailable_reason": "soul dependency read failed",
    }]
    assert bid in _listed(us)
