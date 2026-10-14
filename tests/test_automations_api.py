"""The owner's automation surface: who may see it, who may change it, what it says.

Every assertion here is mutation-checked -- the guarding line in
``tinyassets/api/automations.py`` (or ``universe_server.py``) is flipped, the
test is confirmed red, the line is restored. The mutation table lives in the
commit message for this slice.

The one substituted seam is IDENTITY: ``permissions.current_request_actor_id``,
which in production reads the authenticated request subject out of the auth
middleware. Everything below it -- ``is_authenticated_request``,
``universe_access_allows``, ``universe_access_permission``, the ACL rows, the
store -- runs for real against a seeded data root. Stubbing
``universe_access_allows`` (as the fleet-era tests did) would have made every
authorization assertion here a test of the stub.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tinyassets.api.automations as api
from tests.test_automations import (
    BRANCH,
    OWNER,
    UNIVERSE,
    _seed_branch,
    _seed_owner,
    _switch_assignment_provider,
)
from tests.test_background_budget_finalization_e2e import _seed_serving_assignment
from tinyassets.automations import REFUSAL_KEY_PREFIX, AutomationStore

ADMIN = "acct_admin"
COLLABORATOR = "acct_writer"
STRANGER = "acct_mallory"

CREATE_PAYLOAD = json.dumps(
    {
        "name": "Nightly digest",
        "branch_def_id": BRANCH,
        "interval_seconds": 3600,
        "inputs": {"topic": "spec drift"},
    }
)


class _Identity:
    """The single substituted seam: who the authenticated request is."""

    def __init__(self, actor: str) -> None:
        self.actor = actor

    def __call__(self) -> str:
        return self.actor


@pytest.fixture
def env(tmp_path: Path, monkeypatch) -> _Identity:
    """A seeded universe whose owner can register, signed in as that owner."""
    from tinyassets.api import permissions

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)
    identity = _Identity(OWNER)
    monkeypatch.setattr(permissions, "current_request_actor_id", identity)
    return identity


def _grant(tmp_path: Path, actor: str, permission: str) -> None:
    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(
        tmp_path,
        universe_id=UNIVERSE,
        actor_id=actor,
        permission=permission,
        granted_by=OWNER,
    )


def _create(**overrides) -> dict:
    payload = json.loads(CREATE_PAYLOAD)
    payload.update(overrides)
    return api.automations(
        action="create",
        universe_id=UNIVERSE,
        payload=json.dumps(payload),
    )


# -- 1. Create, then see it ---------------------------------------------------


def test_owner_creates_and_lists_their_own_automation(tmp_path: Path, env) -> None:
    created = _create()

    assert created["status"] == "automation_created"
    row = created["automation"]
    assert row["name"] == "Nightly digest"
    assert row["branch_def_id"] == BRANCH
    assert row["trigger"] == {
        "kind": "interval",
        "interval_seconds": 3600,
        "cron_expr": "",
        "not_before": "",
        "event_type": "",
        "event_filter": {},
        # `automation-schedule-timezone` (2026-09-30): a cron trigger carries
        # the clock it runs in and a "7:00 AM America/Los_Angeles" rendering.
        # An INTERVAL has no wall-clock slot, so both are empty here -- an
        # interval must not appear to be scheduled in a zone it never uses.
        "timezone": "",
        "schedule_local": "",
    }
    assert row["inputs"] == {"topic": "spec drift"}
    assert row["desired_state"] == "active"
    assert row["revision"] == 1
    assert row["owner"] == {"is_you": True}

    listed = api.automations(action="list", universe_id=UNIVERSE)
    assert listed["universe_id"] == UNIVERSE
    assert listed["count"] == 1
    assert [item["automation_id"] for item in listed["automations"]] == [
        row["automation_id"]
    ]
    assert listed["automations"][0]["owner"] == {"is_you": True}

    fetched = api.automations(
        action="get",
        universe_id=UNIVERSE,
        automation_id=row["automation_id"],
    )
    assert fetched["automation"]["automation_id"] == row["automation_id"]


def test_owner_principal_id_is_never_echoed(tmp_path: Path, env) -> None:
    """A reader learns `is_you`, never WHO scheduled the run."""
    created = _create()
    listed = api.automations(action="list", universe_id=UNIVERSE)

    env.actor = ADMIN
    _grant(tmp_path, ADMIN, "admin")
    as_admin = api.automations(
        action="get",
        universe_id=UNIVERSE,
        automation_id=created["automation"]["automation_id"],
    )

    for payload in (created, listed, as_admin):
        assert OWNER not in json.dumps(payload, sort_keys=True)
    assert as_admin["automation"]["owner"] == {"is_you": False}


# -- 2. Authentication and universe access ------------------------------------


def test_a_create_with_nobody_behind_it_is_refused_and_stores_nothing(
    tmp_path: Path, env,
) -> None:
    """Nobody is an EMPTY actor, not one named "anonymous".

    That string used to mean "no principal". It is a legacy name now, so
    setting it here made the caller an authenticated principal who merely
    lacked a grant, and the refusal came back as `universe_access_denied` --
    the right outcome for the wrong reason, and one that would keep passing if
    the authentication gate were removed entirely.
    """
    env.actor = ""

    result = api.automations(
        action="create",
        universe_id=UNIVERSE,
        payload=CREATE_PAYLOAD,
    )

    assert result["error"] == "authentication_required"
    assert result["resource"] == "automation"
    assert result["action"] == "create"
    assert AutomationStore(tmp_path).list(universe_id=UNIVERSE) == []


def test_private_universe_read_is_denied_without_a_grant(tmp_path: Path, env) -> None:
    from tinyassets.daemon_server import (
        ensure_universe_registered,
        ensure_universe_rules,
        update_universe_rules,
    )

    _create()
    ensure_universe_registered(
        tmp_path,
        universe_id=UNIVERSE,
        universe_path=tmp_path / UNIVERSE,
    )
    ensure_universe_rules(tmp_path, universe_id=UNIVERSE)
    update_universe_rules(
        tmp_path,
        universe_id=UNIVERSE,
        updates={"public_read": False},
    )
    env.actor = STRANGER

    result = api.automations(action="list", universe_id=UNIVERSE)

    assert result["error"] == "universe_access_denied"
    assert result["surface"] == "read_graph"
    assert result["required_permission"] == "read"
    assert "automations" not in result


def test_ungranted_actor_cannot_write(tmp_path: Path, env) -> None:
    env.actor = STRANGER

    result = api.automations(
        action="create",
        universe_id=UNIVERSE,
        payload=CREATE_PAYLOAD,
    )

    assert result["error"] == "universe_access_denied"
    assert result["surface"] == "write_graph"
    assert result["required_permission"] == "write"
    assert AutomationStore(tmp_path).list(universe_id=UNIVERSE) == []


def test_write_grant_without_admin_is_refused_by_registration(
    tmp_path: Path,
    env,
) -> None:
    """A write collaborator may edit the universe's work, not schedule it."""
    _grant(tmp_path, COLLABORATOR, "write")
    env.actor = COLLABORATOR

    result = api.automations(
        action="create",
        universe_id=UNIVERSE,
        payload=CREATE_PAYLOAD,
    )

    assert result["error"] == "automation_unavailable"
    assert result["reason"] == "owner_not_admin"
    assert "admin grant" in result["detail"]
    assert AutomationStore(tmp_path).list(universe_id=UNIVERSE) == []


def test_consumer_off_stores_the_automation(tmp_path: Path, env, monkeypatch) -> None:
    monkeypatch.delenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", raising=False)

    result = _create()

    assert result["status"] == "automation_created"
    stored = AutomationStore(tmp_path).get(result["automation"]["automation_id"])
    assert stored is not None
    assert stored.desired_state == "active"


def test_consumer_off_resume_marks_the_row_active(
    tmp_path: Path, env, monkeypatch,
) -> None:
    created = _create()["automation"]
    paused = api.automations(
        action="pause", universe_id=UNIVERSE,
        automation_id=created["automation_id"], expected_revision=1,
    )
    assert paused["status"] == "automation_paused"
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "0")

    resumed = api.automations(
        action="resume", universe_id=UNIVERSE,
        automation_id=created["automation_id"], expected_revision=2,
    )

    assert resumed["status"] == "automation_resumed"
    stored = AutomationStore(tmp_path).get(created["automation_id"])
    assert stored is not None
    assert stored.desired_state == "active"
    assert stored.revision == 3


# -- 3. Payload and action validation -----------------------------------------


@pytest.mark.parametrize(
    "payload,fragment",
    [
        ("not json at all", "JSON object"),
        ('["a list"]', "JSON object"),
        ('{"branch_def_id": "b"}', "name"),
        ('{"name": "x"}', "branch_def_id"),
        ('{"name": "x", "branch_def_id": "b", "inputs": 5}', "inputs"),
        (
            '{"name": "x", "branch_def_id": "b", "interval_seconds": true}',
            "interval_seconds",
        ),
    ],
)
def test_malformed_create_payloads_are_named(
    tmp_path: Path,
    env,
    payload: str,
    fragment: str,
) -> None:
    result = api.automations(
        action="create",
        universe_id=UNIVERSE,
        payload=payload,
    )

    assert result["error"] == "automation_payload_invalid"
    assert fragment in result["detail"]
    assert AutomationStore(tmp_path).list(universe_id=UNIVERSE) == []


def test_both_triggers_or_neither_is_refused(tmp_path: Path, env) -> None:
    both = _create(interval_seconds=3600, cron_expr="0 7 * * *")
    neither = _create(interval_seconds=0, cron_expr="")
    negative = _create(interval_seconds=-60)

    for result in (both, neither, negative):
        assert result["error"] == "automation_unavailable"
        assert result["reason"] == "trigger_invalid"
    assert AutomationStore(tmp_path).list(universe_id=UNIVERSE) == []


def test_automating_someone_elses_workflow_is_refused_with_an_actionable_reason(
    tmp_path: Path,
    env,
) -> None:
    """READABLE is not RUNNABLE: a public branch someone else authored would
    fail every run forever, so the refusal has to happen at registration."""
    _seed_branch(
        tmp_path,
        branch_def_id="branch_bobs_loop",
        author="acct_bob",
        visibility="public",
    )

    result = _create(branch_def_id="branch_bobs_loop")

    assert result["error"] == "automation_unavailable"
    assert result["reason"] == "branch_not_owned"
    assert "you authored" in result["detail"]
    assert AutomationStore(tmp_path).list(universe_id=UNIVERSE) == []


def test_an_open_compute_provider_carries_an_automation(
    tmp_path: Path,
    env,
) -> None:
    """The owner's own api_key_http source is a serving source like any other.

    Registration refused it until 2026-09-29, citing a foreground rule removed
    on 2026-09-03, so a free account could not create any automation. The run
    itself launching on that source is tests/test_open_provider_automations.py.
    """
    _switch_assignment_provider(
        tmp_path,
        universe_id=UNIVERSE,
        provider="api_key_http:kimi",
    )

    result = _create()

    assert result["status"] == "automation_created", result
    assert [row.automation_id for row in AutomationStore(tmp_path).list(
        universe_id=UNIVERSE,
    )] == [result["automation"]["automation_id"]]


def test_no_serving_assignment_names_any_source_the_owner_can_connect() -> None:
    """The one remaining cause is no ready assignment at all, and an owner with
    only an API-key source must read a sentence that is true for them."""
    from tinyassets.api.automations import _UNAVAILABLE_DETAIL

    detail = _UNAVAILABLE_DETAIL["no_serving_assignment"]
    assert "API-key" in detail and "subscription" in detail
    assert "cannot run an automation" not in detail


def test_the_failure_counter_is_visible_before_the_auto_pause(
    tmp_path: Path,
    env,
) -> None:
    """An owner should see an automation drifting toward its own cap, not just
    discover it already paused."""
    created = _create()["automation"]
    assert created["consecutive_failures"] == 0

    store = AutomationStore(tmp_path)
    for index in range(2):
        due_at = f"2026-08-29T1{index}:00:00+00:00"
        assert store.claim_attempt(
            created["automation_id"],
            due_at,
            now=datetime.now(timezone.utc),
        )
        store.finish_attempt(
            created["automation_id"],
            due_at,
            run_id="",
            status="failed",
            reason="run_failed:failed",
            now=datetime.now(timezone.utc),
            succeeded=False,
        )

    listed = api.automations(action="list", universe_id=UNIVERSE)

    assert listed["automations"][0]["consecutive_failures"] == 2


def test_unknown_action_lists_the_allowed_ones(tmp_path: Path, env) -> None:
    result = api.automations(action="rebind", universe_id=UNIVERSE)

    assert result["error"] == "unknown_automation_action"
    assert result["action"] == "rebind"
    assert result["allowed_actions"] == [
        "create",
        "delete",
        "get",
        "list",
        "pause",
        "resume",
    ]


def test_fleet_era_operations_are_no_longer_reachable(tmp_path: Path, env) -> None:
    """bind_provider/reconcile_provider/rebind/stop configured an executor that
    no longer exists; offering them would be a control that cannot take effect."""
    for action in ("bind_provider", "reconcile_provider", "rebind", "stop"):
        result = api.automations(action=action, universe_id=UNIVERSE)
        assert result["error"] == "unknown_automation_action", action


# -- 4. Controls: revision fence and ownership --------------------------------


def test_pause_and_resume_move_the_revision(tmp_path: Path, env) -> None:
    created = _create()["automation"]

    paused = api.automations(
        action="pause",
        universe_id=UNIVERSE,
        automation_id=created["automation_id"],
        expected_revision=1,
    )
    resumed = api.automations(
        action="resume",
        universe_id=UNIVERSE,
        automation_id=created["automation_id"],
        expected_revision=2,
    )

    assert paused["status"] == "automation_paused"
    assert paused["automation"]["desired_state"] == "paused"
    assert paused["automation"]["pause_reason"] == "owner_paused"
    assert paused["automation"]["revision"] == 2
    assert resumed["status"] == "automation_resumed"
    assert resumed["automation"]["desired_state"] == "active"
    assert resumed["automation"]["pause_reason"] == ""
    assert resumed["automation"]["revision"] == 3


def test_stale_revision_is_a_conflict_and_changes_nothing(
    tmp_path: Path,
    env,
) -> None:
    created = _create()["automation"]
    api.automations(
        action="pause",
        universe_id=UNIVERSE,
        automation_id=created["automation_id"],
        expected_revision=1,
    )

    stale = api.automations(
        action="resume",
        universe_id=UNIVERSE,
        automation_id=created["automation_id"],
        expected_revision=1,
    )

    assert stale == {
        "error": "automation_revision_conflict",
        "expected_revision": 1,
        "current_revision": 2,
    }
    stored = AutomationStore(tmp_path).get(created["automation_id"])
    assert stored is not None
    assert stored.desired_state == "paused"
    assert stored.revision == 2


def test_a_non_owner_with_write_access_cannot_pause_someone_elses_row(
    tmp_path: Path,
    env,
) -> None:
    created = _create()["automation"]
    _grant(tmp_path, COLLABORATOR, "write")
    env.actor = COLLABORATOR

    result = api.automations(
        action="pause",
        universe_id=UNIVERSE,
        automation_id=created["automation_id"],
        expected_revision=1,
    )

    assert result["error"] == "automation_unavailable"
    assert result["reason"] == "not_owner_or_admin"
    stored = AutomationStore(tmp_path).get(created["automation_id"])
    assert stored is not None
    assert stored.desired_state == "active"
    assert stored.revision == 1


def test_a_universe_admin_can_pause_another_owners_row(tmp_path: Path, env) -> None:
    """A universe owner must be able to stop work running in their universe."""
    created = _create()["automation"]
    _grant(tmp_path, ADMIN, "admin")
    env.actor = ADMIN

    result = api.automations(
        action="pause",
        universe_id=UNIVERSE,
        automation_id=created["automation_id"],
        expected_revision=1,
    )

    assert result["status"] == "automation_paused"
    assert result["automation"]["owner"] == {"is_you": False}
    stored = AutomationStore(tmp_path).get(created["automation_id"])
    assert stored is not None
    assert stored.desired_state == "paused"


def test_an_automation_in_another_universe_is_not_found(tmp_path: Path, env) -> None:
    """Same envelope for 'no such id' and 'not in this universe' -- a caller
    must not be able to confirm another universe's automation exists."""
    created = _create()["automation"]
    other = "universe_bob"
    (tmp_path / other).mkdir(parents=True, exist_ok=True)
    _seed_owner(tmp_path, universe_id=other, owner=OWNER, home=UNIVERSE)

    cross = api.automations(
        action="get",
        universe_id=other,
        automation_id=created["automation_id"],
    )
    missing = api.automations(
        action="get",
        universe_id=other,
        automation_id="nope",
    )
    controlled = api.automations(
        action="pause",
        universe_id=other,
        automation_id=created["automation_id"],
        expected_revision=1,
    )

    assert cross == {"error": "not_found", "resource": "automation"}
    assert missing == cross
    assert controlled == cross
    stored = AutomationStore(tmp_path).get(created["automation_id"])
    assert stored is not None
    assert stored.revision == 1


# -- 5. Delete -----------------------------------------------------------------


def test_delete_retires_the_row_and_list_hides_it(tmp_path: Path, env) -> None:
    created = _create()["automation"]

    deleted = api.automations(
        action="delete",
        universe_id=UNIVERSE,
        automation_id=created["automation_id"],
        expected_revision=1,
    )
    default_list = api.automations(action="list", universe_id=UNIVERSE)
    with_retired = api.automations(
        action="list",
        universe_id=UNIVERSE,
        payload='{"include_retired": true}',
    )

    assert deleted["status"] == "automation_deleted"
    assert deleted["automation"]["retired_at"]
    assert deleted["automation"]["desired_state"] == "paused"
    assert default_list["automations"] == []
    assert default_list["include_retired"] is False
    assert [item["automation_id"] for item in with_retired["automations"]] == [
        created["automation_id"]
    ]
    assert with_retired["include_retired"] is True


def test_a_deleted_automation_cannot_be_resumed(tmp_path: Path, env) -> None:
    created = _create()["automation"]
    api.automations(
        action="delete",
        universe_id=UNIVERSE,
        automation_id=created["automation_id"],
        expected_revision=1,
    )

    result = api.automations(
        action="resume",
        universe_id=UNIVERSE,
        automation_id=created["automation_id"],
        expected_revision=2,
    )

    assert result["error"] == "automation_unavailable"
    assert result["reason"] == "already_retired"
    stored = AutomationStore(tmp_path).get(created["automation_id"])
    assert stored is not None
    assert stored.desired_state == "paused"
    assert stored.retired_at


# -- 6. Why a run was skipped --------------------------------------------------


def test_list_surfaces_a_recent_refusal_reason(tmp_path: Path, env) -> None:
    from tinyassets.storage.assigned_queue_refusals import AssignedQueueRefusalStore

    created = _create()["automation"]
    AssignedQueueRefusalStore(tmp_path).record(
        branch_task_id=f"{REFUSAL_KEY_PREFIX}{created['automation_id']}",
        universe_id=UNIVERSE,
        reason="owner_not_admin",
        observed_at=datetime.now(timezone.utc).isoformat(),
        consumer_id="consumer_test",
    )

    listed = api.automations(action="list", universe_id=UNIVERSE)

    assert listed["automations"][0]["recent_reason"] == "owner_not_admin"


def test_a_stale_refusal_is_not_reported_as_current(tmp_path: Path, env) -> None:
    from tinyassets.storage.assigned_queue_refusals import AssignedQueueRefusalStore

    created = _create()["automation"]
    AssignedQueueRefusalStore(tmp_path).record(
        branch_task_id=f"{REFUSAL_KEY_PREFIX}{created['automation_id']}",
        universe_id=UNIVERSE,
        reason="owner_not_admin",
        observed_at=(
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat(),
        consumer_id="consumer_test",
    )

    listed = api.automations(action="list", universe_id=UNIVERSE)

    assert "recent_reason" not in listed["automations"][0]


def test_an_unreadable_refusal_ledger_does_not_break_the_list(
    tmp_path: Path,
    env,
    monkeypatch,
) -> None:
    """Seeing the row matters more than seeing why its last run was skipped."""
    created = _create()["automation"]

    def _boom(**_kwargs):
        raise sqlite3.OperationalError("ledger unavailable")

    monkeypatch.setattr(
        "tinyassets.storage.assigned_queue_refusals."
        "AssignedQueueRefusalStore.fresh_reasons",
        lambda self, **kwargs: _boom(**kwargs),
    )

    listed = api.automations(action="list", universe_id=UNIVERSE)

    assert [item["automation_id"] for item in listed["automations"]] == [
        created["automation_id"]
    ]


# -- 7. Fleet-era rows stay visible -------------------------------------------


def test_legacy_control_rows_are_listed_and_flagged(tmp_path: Path, env) -> None:
    from tests.cloud_automation_fixtures import _definition
    from tinyassets.storage.cloud_automation_control import (
        CloudAutomationControlStore,
    )

    created = _create()["automation"]
    CloudAutomationControlStore(tmp_path).create_control(
        _definition(),
        automation_id="automation_spec_drain",
        cadence_seconds=300,
    )

    listed = api.automations(action="list", universe_id=UNIVERSE)

    by_id = {item["automation_id"]: item for item in listed["automations"]}
    assert by_id[created["automation_id"]].get("legacy") is None
    legacy = by_id["automation_spec_drain"]
    assert legacy["legacy"] is True
    assert legacy["status"] == "retired_fleet_era"
    # The fleet-era row's own desired_state, reported as-is: it is a record of
    # what the old layer was told, not a claim that anything will run.
    assert legacy["desired_state"] == "active"
    # The disposition the consumer records when it stops these (plan C1) is on
    # the row itself, so it outlives the refusal ledger's freshness window.
    from tinyassets.consumer_reason_actions import RETIRED_FLEET_CONTROL_REASON

    assert legacy["detail"] == RETIRED_FLEET_CONTROL_REASON
    assert listed["count"] == 2


# -- 8. The pinned handles reach the new surface ------------------------------


def test_read_and_write_graph_route_to_the_owner_surface(tmp_path: Path, env) -> None:
    """End-to-end through the pinned catalog: no mock between handle and store."""
    from tinyassets import universe_server as server

    created = json.loads(
        server.write_graph(
            target="automation",
            operation="create",
            graph_id=UNIVERSE,
            payload_json=CREATE_PAYLOAD,
        )
    )
    automation_id = created["automation"]["automation_id"]

    listed = json.loads(server.read_graph(target="automations", graph_id=UNIVERSE))
    fetched = json.loads(
        server.read_graph(
            target="automation",
            graph_id=UNIVERSE,
            automation_id=automation_id,
        )
    )
    paused = json.loads(
        server.write_graph(
            target="automation",
            operation="pause",
            graph_id=UNIVERSE,
            automation_id=automation_id,
            expected_revision=1,
        )
    )

    assert created["status"] == "automation_created"
    assert [item["automation_id"] for item in listed["automations"]] == [automation_id]
    assert fetched["automation"]["automation_id"] == automation_id
    assert paused["automation"]["desired_state"] == "paused"
    assert AutomationStore(tmp_path).get(automation_id).desired_state == "paused"


def test_read_graph_never_exposes_retired_rows(tmp_path: Path, env) -> None:
    """The read handle carries no payload, so its list cannot ask for them."""
    from tinyassets import universe_server as server

    created = _create()["automation"]
    api.automations(
        action="delete",
        universe_id=UNIVERSE,
        automation_id=created["automation_id"],
        expected_revision=1,
    )

    listed = json.loads(server.read_graph(target="automations", graph_id=UNIVERSE))

    assert listed["automations"] == []
    assert listed["include_retired"] is False
