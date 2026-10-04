"""User-owned automations: the fence, the fail-loud registration, and the pump.

Every assertion here is mutation-checked -- the guarding line is flipped, the
test goes red, the line is restored. The mutation table lives in the commit
message for this slice.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tinyassets.automations as automations_module
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_background_budget_finalization_e2e import _seed_serving_assignment
from tinyassets.automations import (
    MAX_CONSECUTIVE_FAILURES,
    Automation,
    AutomationStore,
    AutomationUnavailable,
    automation_lease_key,
    cancel_grace_seconds,
    cron_min_gap_seconds,
    due_automations,
    register_automation,
    run_due_automation,
    run_timeout_seconds,
)
from tinyassets.branch_tasks_v2 import EPOCH2_TASK_LEASE_SECONDS
from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.runtime.assigned_queue_consumer import AssignedQueueConsumer
from tinyassets.storage import db_path

OWNER = "acct_alice"
UNIVERSE = "universe_alice"
BRANCH = "branch_automation_demo"

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)


pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture(autouse=True)
def _pin_data_dir(tmp_path: Path, monkeypatch):
    """Every test's data root is its own tmp_path.

    `_engine_run_admit` resolves its rolling-cap ledger from
    `TINYASSETS_DATA_DIR` and falls back to `"."` -- the REPO ROOT -- when it is
    unset (engine_mcp_server.py:104). Without this, running these tests wrote
    `.engine_run_admissions.db` into the working tree AND shared one 20/hour cap
    across every test in the file, so later tests were rate-limited by earlier
    ones. Both were observed; this pins the root the way the daemon does.
    """
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


# -- Seeds --------------------------------------------------------------------


def _seed_branch(
    tmp_path: Path,
    *,
    branch_def_id: str = BRANCH,
    author: str = OWNER,
    visibility: str = "public",
) -> str:
    from tinyassets.daemon_server import initialize_author_server, save_branch_definition

    node = NodeDefinition(
        node_id="n1",
        display_name="Automation writer",
        prompt_template="Do the owner's recurring work.",
    )
    branch = BranchDefinition(
        branch_def_id=branch_def_id,
        name="Automation demo",
        author=author,
        visibility=visibility,
        graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
        edges=[EdgeDefinition(from_node="n1", to_node="END")],
        entry_point="n1",
        node_defs=[node],
        state_schema=[],
    )
    initialize_author_server(tmp_path)
    save_branch_definition(tmp_path, branch_def=branch.to_dict())
    return branch_def_id


def _seed_nested_branch(
    tmp_path: Path,
    *,
    parent_id: str = "branch_parent_invoke",
    child_id: str = "branch_child_prompt",
    author: str = OWNER,
    wait_mode: str = "blocking",
) -> str:
    """A root whose only node invokes a CHILD branch that holds the prompt.

    The root itself never calls a provider, so a guard wired only to the root's
    nodes never runs where the spend happens.
    """
    from tinyassets.daemon_server import initialize_author_server, save_branch_definition

    initialize_author_server(tmp_path)
    child = BranchDefinition(
        branch_def_id=child_id,
        name="Nested child",
        author=author,
        visibility="public",
        graph_nodes=[GraphNodeRef(id="c1", node_def_id="c1")],
        edges=[EdgeDefinition(from_node="c1", to_node="END")],
        entry_point="c1",
        node_defs=[
            NodeDefinition(
                node_id="c1",
                display_name="Child writer",
                prompt_template="Do the nested work.",
                output_keys=["child_out"],
            )
        ],
        state_schema=[{"name": "child_out", "type": "str"}],
    )
    save_branch_definition(tmp_path, branch_def=child.to_dict())

    parent = BranchDefinition(
        branch_def_id=parent_id,
        name="Parent invoker",
        author=author,
        visibility="public",
        graph_nodes=[GraphNodeRef(id="p1", node_def_id="p1")],
        edges=[EdgeDefinition(from_node="p1", to_node="END")],
        entry_point="p1",
        node_defs=[
            NodeDefinition(
                node_id="p1",
                display_name="Invoke the child",
                invoke_branch_spec={
                    "branch_def_id": child_id,
                    "wait_mode": wait_mode,
                    "inputs_mapping": {},
                    "output_mapping": {"parent_out": "child_out"},
                },
            )
        ],
        state_schema=[{"name": "parent_out", "type": "str"}],
    )
    save_branch_definition(tmp_path, branch_def=parent.to_dict())
    return parent_id


def _seed_version_invoking_branch(
    tmp_path: Path,
    *,
    parent_id: str = "branch_parent_version",
    child_id: str = "branch_child_version",
    author: str = OWNER,
    public: bool = True,
    child_visibility: str = "public",
) -> str:
    """A root that invokes a PUBLISHED VERSION of a child, async.

    The version-pinned async launch is a fourth child path, and it was the one
    that still dropped the guard after round 2 (Codex round 3 §a). ``public``
    is the author's publication mark; ``False`` pins an unpublished snapshot.
    """
    from tinyassets.branch_versions import publish_branch_version
    from tinyassets.daemon_server import initialize_author_server, save_branch_definition

    initialize_author_server(tmp_path)
    child = BranchDefinition(
        branch_def_id=child_id,
        name="Versioned child",
        author=author,
        visibility=child_visibility,
        graph_nodes=[GraphNodeRef(id="v1", node_def_id="v1")],
        edges=[EdgeDefinition(from_node="v1", to_node="END")],
        entry_point="v1",
        node_defs=[
            NodeDefinition(
                node_id="v1",
                display_name="Versioned writer",
                prompt_template="Do the versioned work.",
                output_keys=["child_out"],
            )
        ],
        state_schema=[{"name": "child_out", "type": "str"}],
    )
    save_branch_definition(tmp_path, branch_def=child.to_dict())
    version = publish_branch_version(tmp_path, child.to_dict(), publisher=author,
                                     public=public)

    parent = BranchDefinition(
        branch_def_id=parent_id,
        name="Version invoker",
        author=author,
        visibility="public",
        graph_nodes=[GraphNodeRef(id="p1", node_def_id="p1")],
        edges=[EdgeDefinition(from_node="p1", to_node="END")],
        entry_point="p1",
        node_defs=[
            NodeDefinition(
                node_id="p1",
                display_name="Invoke the published version",
                invoke_branch_version_spec={
                    "branch_version_id": version.branch_version_id,
                    "wait_mode": "async",
                    "inputs_mapping": {},
                    "output_mapping": {"parent_out": "ignored"},
                },
            )
        ],
        state_schema=[{"name": "parent_out", "type": "str"}],
    )
    save_branch_definition(tmp_path, branch_def=parent.to_dict())
    return parent_id


def _seed_owner(
    tmp_path: Path,
    *,
    universe_id: str = UNIVERSE,
    owner: str = OWNER,
    permission: str = "admin",
    home: str | None = None,
) -> None:
    from tinyassets.daemon_server import (
        grant_universe_access,
        initialize_author_server,
        set_founder_home,
    )

    initialize_author_server(tmp_path)
    (tmp_path / universe_id).mkdir(parents=True, exist_ok=True)
    grant_universe_access(
        tmp_path,
        universe_id=universe_id,
        actor_id=owner,
        permission=permission,
        granted_by=owner,
    )
    set_founder_home(
        tmp_path,
        founder_sub=owner,
        universe_id=universe_id if home is None else home,
    )


def _copy_assignment_to(tmp_path: Path, *, universe_id: str, owner: str) -> None:
    """Give a SECOND universe its own ready assignment row.

    The seeded serving path is hard-wired to one universe; the pump-isolation
    tests need a second owner whose automation must keep running while the
    first one's is refused.
    """
    from tinyassets.provider_assignment import (
        load_provider_assignment,
        provider_assignment_digest,
        store_provider_assignment_in_transaction,
    )

    source = load_provider_assignment(tmp_path, universe_id=UNIVERSE)
    assert source is not None
    clone = replace(source, universe_id=universe_id, owner_user_id=owner)
    clone = replace(
        clone,
        assignment_digest=provider_assignment_digest(
            owner_user_id=clone.owner_user_id,
            universe_id=clone.universe_id,
            provider=clone.provider,
            generation=clone.generation,
            binding_id=clone.binding_id,
            credential_reference_id=clone.credential_reference_id,
            credential_reference_generation=clone.credential_reference_generation,
            credential_reference_digest=clone.credential_reference_digest,
        ),
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        store_provider_assignment_in_transaction(conn, clone)
        conn.commit()


def _switch_assignment_provider(tmp_path: Path, *, universe_id: str, provider: str) -> None:
    """Rebind the universe to another provider, the way an owner would."""
    from tinyassets.provider_assignment import (
        load_provider_assignment,
        provider_assignment_digest,
        store_provider_assignment_in_transaction,
    )

    current = load_provider_assignment(tmp_path, universe_id=universe_id)
    assert current is not None
    switched = replace(current, provider=provider, generation=current.generation + 1)
    switched = replace(
        switched,
        assignment_digest=provider_assignment_digest(
            owner_user_id=switched.owner_user_id,
            universe_id=switched.universe_id,
            provider=switched.provider,
            generation=switched.generation,
            binding_id=switched.binding_id,
            credential_reference_id=switched.credential_reference_id,
            credential_reference_generation=switched.credential_reference_generation,
            credential_reference_digest=switched.credential_reference_digest,
        ),
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        store_provider_assignment_in_transaction(conn, switched)
        conn.commit()


@pytest.fixture
def registered(tmp_path: Path, monkeypatch) -> Automation:
    """A universe whose owner can register, plus one active automation."""
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)
    return register_automation(
        tmp_path,
        universe_id=UNIVERSE,
        owner_principal_id=OWNER,
        name="Nightly digest",
        branch_def_id=BRANCH,
        interval_seconds=600,
        inputs={"topic": "spec drift"},
        now=NOW,
    )


class _FakeOutcome:
    def __init__(self, run_id: str = "run_fake", status: str = "completed") -> None:
        self.run_id = run_id
        self.status = status
        self.output: dict[str, object] = {}
        self.error = ""

    def with_error(self, error: str) -> "_FakeOutcome":
        self.error = error
        return self


class _SeamRecorder:
    """Stands in for `_execute`, the one substitutable seam."""

    def __init__(self, status: str = "completed") -> None:
        self.calls: list[tuple[str, dict]] = []
        self.status = status

    def __call__(
        self, base_path, automation, provider_call, branch, inputs,
        on_run_started=None,
    ):
        self.calls.append((automation.automation_id, dict(inputs)))
        return _FakeOutcome(
            run_id=f"run_{len(self.calls)}",
            status=self.status,
        )


def _child_run_ids(tmp_path: Path, branch_def_id: str) -> list[str]:
    """Run ids launched for a child branch, newest last."""
    from tinyassets.runs import _connect, initialize_runs_db

    initialize_runs_db(tmp_path)
    with _connect(tmp_path) as conn:
        rows = conn.execute(
            "SELECT run_id FROM runs WHERE branch_def_id = ? ORDER BY rowid",
            (branch_def_id,),
        ).fetchall()
    return [str(row[0]) for row in rows]


def _lease_ttl_seconds(store, universe_id: str) -> float:
    """Seconds until the universe lease expires, from its stored row."""
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT expires_at FROM universe_leases WHERE universe_id = ?",
            (universe_id,),
        ).fetchone()
    assert row is not None, "no universe lease row"
    expires = datetime.fromisoformat(str(row[0]))
    return (expires - datetime.now(timezone.utc)).total_seconds()


def _refusal_rows(tmp_path: Path) -> dict[str, str]:
    database = db_path(tmp_path)
    if not database.is_file():
        return {}
    with sqlite3.connect(database) as conn:
        try:
            rows = conn.execute(
                "SELECT branch_task_id, reason FROM assigned_queue_refusals"
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
    return {str(key): str(reason) for key, reason in rows}


# -- 1. The (automation_id, due_at) fence -------------------------------------


def test_claim_attempt_admits_exactly_one_caller(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path)
    results = [
        store.claim_attempt("a1", "2026-08-29T12:00:00+00:00", now=NOW),
        store.claim_attempt("a1", "2026-08-29T12:00:00+00:00", now=NOW),
    ]

    assert results == [True, False]
    # A different instant of the SAME automation is a different claim.
    assert store.claim_attempt("a1", "2026-08-29T12:10:00+00:00", now=NOW) is True


def test_second_run_of_the_same_due_instant_does_not_reach_the_seam(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    seam = _SeamRecorder()
    monkeypatch.setattr(automations_module, "_execute", seam)
    due_at = "2026-08-29T12:10:00+00:00"

    first = run_due_automation(tmp_path, registered, due_at, now=NOW)
    second = run_due_automation(tmp_path, registered, due_at, now=NOW)

    assert first == "ok:ran:run_1"
    assert second == "attempt_exists"
    assert len(seam.calls) == 1


# -- 2. Restart survival ------------------------------------------------------


def test_a_fresh_store_recomputes_the_same_due_at_and_skips_the_claimed_one(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """A daemon that dies mid-run must not re-launch the same instant on boot."""
    later = NOW + timedelta(seconds=900)
    due_before = due_automations(tmp_path, universe_id=UNIVERSE, now=later)
    assert [pair[1] for pair in due_before] == ["2026-08-29T12:10:00+00:00"]

    # In-flight when the process died: claimed, never finished.
    assert AutomationStore(tmp_path).claim_attempt(
        registered.automation_id, due_before[0][1], now=later
    ) is True

    # Reboot: new store, new consumer, same wall-clock -> same due_at.
    reborn_store = AutomationStore(tmp_path)
    reborn_consumer = AssignedQueueConsumer(tmp_path, max_concurrency=1)
    try:
        due_after = due_automations(tmp_path, universe_id=UNIVERSE, now=later)
        assert [pair[1] for pair in due_after] == [pair[1] for pair in due_before]
        assert reborn_store.get(registered.automation_id) is not None

        seam = _SeamRecorder()
        monkeypatch.setattr(automations_module, "_execute", seam)
        reason = run_due_automation(
            tmp_path,
            due_after[0][0],
            due_after[0][1],
            now=later,
            consumer_id=reborn_consumer.consumer_id,
        )
    finally:
        reborn_consumer.stop()

    assert reason == "attempt_exists"
    assert seam.calls == []


def test_interval_due_at_floors_onto_the_period_grid(
    tmp_path: Path,
    registered: Automation,
) -> None:
    """A daemon down for many intervals owes ONE run, not a backlog burst."""
    assert due_automations(tmp_path, universe_id=UNIVERSE, now=NOW) == []
    much_later = NOW + timedelta(seconds=600 * 7 + 90)
    due = due_automations(tmp_path, universe_id=UNIVERSE, now=much_later)

    assert len(due) == 1
    assert due[0][1] == "2026-08-29T13:10:00+00:00"  # created_at + 7 * 600s


# -- 3. Provider switch, nothing pinned ---------------------------------------


def test_no_column_or_field_pins_a_provider_or_an_executor(
    tmp_path: Path,
    registered: Automation,
) -> None:
    forbidden = ("provider", "worker", "daemon", "runtime", "credential", "binding")
    field_names = {f.name for f in fields(Automation)}
    with sqlite3.connect(AutomationStore(tmp_path).db_path) as conn:
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(automations)")
        }

    for token in forbidden:
        assert not [name for name in field_names if token in name], token
        assert not [name for name in columns if token in name], token


def test_a_provider_switch_between_runs_needs_no_repreparation(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    from tinyassets.provider_assignment import load_provider_assignment

    built: list[dict] = []
    real_factory = automations_module.__dict__  # keep the import site honest

    def recording_factory(base_path, *, universe_id, principal_id, provider_call):
        from tinyassets.foreground_run_provider import (
            _ForegroundRunProviderSession,
        )

        session = _ForegroundRunProviderSession(
            base_path,
            universe_id=universe_id,
            principal_id=principal_id,
            provider_call=provider_call,
        )
        assignment = load_provider_assignment(base_path, universe_id=universe_id)
        built.append(
            {
                "constructor_inputs": session.constructor_inputs(),
                # What the session's own _admit resolves, at the moment it is built.
                "resolves_provider": "" if assignment is None else assignment.provider,
            }
        )
        return session

    monkeypatch.setattr(
        "tinyassets.foreground_run_provider.new_foreground_run_provider_session",
        recording_factory,
    )
    seam = _SeamRecorder()
    monkeypatch.setattr(automations_module, "_execute", seam)
    assert real_factory is automations_module.__dict__

    run_due_automation(tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW)
    _switch_assignment_provider(tmp_path, universe_id=UNIVERSE, provider="claude-code")
    run_due_automation(tmp_path, registered, "2026-08-29T12:20:00+00:00", now=NOW)

    assert len(built) == 2
    first, second = built
    # Nothing provider-derived crosses the boundary: the two sessions are built
    # from identical inputs, and only what they RESOLVE differs.
    assert first["constructor_inputs"] == second["constructor_inputs"]
    assert first["constructor_inputs"]["universe_id"] == UNIVERSE
    assert first["constructor_inputs"]["principal_id"] == OWNER
    assert first["resolves_provider"] == "codex"
    assert second["resolves_provider"] == "claude-code"


# -- 4. Registration fails loud -----------------------------------------------


def _registration_kwargs(**overrides):
    kwargs = {
        "universe_id": UNIVERSE,
        "owner_principal_id": OWNER,
        "name": "Nightly digest",
        "branch_def_id": BRANCH,
        "interval_seconds": 600,
        "cron_expr": "",
        "inputs": {},
        "now": NOW,
    }
    kwargs.update(overrides)
    return kwargs


def test_registration_refuses_when_the_consumer_is_dark(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", raising=False)
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)

    with pytest.raises(AutomationUnavailable) as caught:
        register_automation(tmp_path, **_registration_kwargs())

    assert caught.value.reason == "consumer_disabled"
    assert AutomationStore(tmp_path).list(universe_id=UNIVERSE) == []


@pytest.mark.parametrize("principal", ["", "   ", "anonymous"])
def test_registration_refuses_an_unauthenticated_principal(
    tmp_path: Path, monkeypatch, principal: str
) -> None:
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)

    with pytest.raises(AutomationUnavailable) as caught:
        register_automation(
            tmp_path, **_registration_kwargs(owner_principal_id=principal)
        )

    assert caught.value.reason == "authentication_required"


def test_registration_refuses_a_writer_who_is_not_an_admin(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path, permission="write")
    _seed_branch(tmp_path)

    with pytest.raises(AutomationUnavailable) as caught:
        register_automation(tmp_path, **_registration_kwargs())

    assert caught.value.reason == "owner_not_admin"


def test_registration_refuses_an_admin_of_someone_elses_universe(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path, home="universe_somewhere_else")
    _seed_branch(tmp_path)

    with pytest.raises(AutomationUnavailable) as caught:
        register_automation(tmp_path, **_registration_kwargs())

    assert caught.value.reason == "not_owner_home"


def test_registration_refuses_a_universe_with_no_ready_assignment(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)

    with pytest.raises(AutomationUnavailable) as caught:
        register_automation(tmp_path, **_registration_kwargs())

    assert caught.value.reason == "no_serving_assignment"


def test_registration_refuses_a_branch_the_owner_cannot_read(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(
        tmp_path,
        branch_def_id="branch_private_other",
        author="acct_bob",
        visibility="private",
    )

    with pytest.raises(AutomationUnavailable) as caught:
        register_automation(
            tmp_path, **_registration_kwargs(branch_def_id="branch_private_other")
        )
    assert caught.value.reason == "branch_not_readable"

    with pytest.raises(AutomationUnavailable) as missing:
        register_automation(
            tmp_path, **_registration_kwargs(branch_def_id="branch_does_not_exist")
        )
    assert missing.value.reason == "branch_not_readable"


@pytest.mark.parametrize(
    "trigger",
    [
        {"interval_seconds": 0, "cron_expr": ""},          # neither
        {"interval_seconds": 600, "cron_expr": "0 * * * *"},  # both
        {"interval_seconds": -600, "cron_expr": ""},        # nonsense
        {"interval_seconds": 0, "cron_expr": "not a cron"},  # unparseable
        {"interval_seconds": 0, "cron_expr": "0 0 * *"},     # four fields
    ],
)
def test_registration_refuses_a_trigger_that_cannot_fire(
    tmp_path: Path, monkeypatch, trigger: dict
) -> None:
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)

    with pytest.raises(AutomationUnavailable) as caught:
        register_automation(tmp_path, **_registration_kwargs(**trigger))

    assert caught.value.reason == "trigger_invalid"
    assert AutomationStore(tmp_path).list(universe_id=UNIVERSE) == []


def test_registration_is_unlimited(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)
    for index in range(12):
        register_automation(tmp_path, **_registration_kwargs(name=f"automation {index}"))
    assert len(AutomationStore(tmp_path).list(universe_id=UNIVERSE)) == 12


def test_a_short_interval_is_a_cadence_not_a_refusal(
    tmp_path: Path, monkeypatch
) -> None:
    """The 300s floor is gone: a 60s cadence registers, and what it may spend is
    metered when each run fires."""
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)
    created = register_automation(
        tmp_path, **_registration_kwargs(interval_seconds=60)
    )
    assert created.interval_seconds == 60


def test_a_cron_registration_is_stored_and_comes_due_on_its_minute(
    tmp_path: Path, monkeypatch
) -> None:
    import time as _time

    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)
    automation = register_automation(
        tmp_path,
        **_registration_kwargs(interval_seconds=0, cron_expr="*/5 * * * *"),
    )
    assert automation.trigger_kind == "cron"

    # `*/5` matches minute 0 of every hour, so NOW's bucket is a match in any
    # timezone -- the assertion does not depend on the runner's clock offset.
    local = _time.localtime(NOW.timestamp())
    assert local.tm_min == 0
    due = due_automations(tmp_path, universe_id=UNIVERSE, now=NOW + timedelta(seconds=30))

    assert [pair[1] for pair in due] == ["2026-08-29T12:00:00+00:00"]


# -- 5. Authority is re-derived at run time -----------------------------------


def test_an_owner_who_lost_admin_is_refused_and_the_automation_pauses(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    from tinyassets.daemon_server import grant_universe_access

    seam = _SeamRecorder()
    monkeypatch.setattr(automations_module, "_execute", seam)
    grant_universe_access(
        tmp_path,
        universe_id=UNIVERSE,
        actor_id=OWNER,
        permission="read",
        granted_by=OWNER,
    )

    reason = run_due_automation(
        tmp_path,
        registered,
        "2026-08-29T12:10:00+00:00",
        now=NOW,
        consumer_id="worker_assigned_test",
    )

    assert reason == "owner_lost_admin"
    assert seam.calls == []
    paused = AutomationStore(tmp_path).get(registered.automation_id)
    assert paused is not None
    assert paused.desired_state == "paused"
    assert paused.pause_reason == "owner_lost_admin"
    assert paused.revision == registered.revision + 1
    assert _refusal_rows(tmp_path) == {
        f"automation:{registered.automation_id}": "owner_lost_admin"
    }
    # Paused means the next poll stops offering it.
    assert due_automations(
        tmp_path, universe_id=UNIVERSE, now=NOW + timedelta(seconds=3600)
    ) == []


def test_a_home_rebind_between_ticks_is_refused_as_not_owner_home(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    from tinyassets.daemon_server import set_founder_home

    seam = _SeamRecorder()
    monkeypatch.setattr(automations_module, "_execute", seam)
    set_founder_home(tmp_path, founder_sub=OWNER, universe_id="universe_elsewhere")

    reason = run_due_automation(
        tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW
    )

    assert reason == "not_owner_home"
    assert seam.calls == []


def test_a_revoked_assignment_between_ticks_is_refused(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    seam = _SeamRecorder()
    monkeypatch.setattr(automations_module, "_execute", seam)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("DELETE FROM provider_assignments WHERE universe_id = ?", (UNIVERSE,))
        conn.commit()

    reason = run_due_automation(
        tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW
    )

    assert reason == "no_serving_assignment"
    assert seam.calls == []


def test_one_owners_refusal_leaves_another_owners_run_alone(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    from tinyassets.daemon_server import grant_universe_access

    _seed_owner(tmp_path, universe_id="universe_bob", owner="acct_bob")
    _copy_assignment_to(tmp_path, universe_id="universe_bob", owner="acct_bob")
    _seed_branch(tmp_path, branch_def_id="branch_bob", author="acct_bob")
    bob = register_automation(
        tmp_path,
        universe_id="universe_bob",
        owner_principal_id="acct_bob",
        name="Bob's digest",
        branch_def_id="branch_bob",
        interval_seconds=600,
        now=NOW,
    )
    seam = _SeamRecorder()
    monkeypatch.setattr(automations_module, "_execute", seam)
    grant_universe_access(
        tmp_path,
        universe_id=UNIVERSE,
        actor_id=OWNER,
        permission="read",
        granted_by=OWNER,
    )

    refused = run_due_automation(
        tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW
    )
    ran = run_due_automation(tmp_path, bob, "2026-08-29T12:10:00+00:00", now=NOW)

    assert refused == "owner_lost_admin"
    assert ran == "ok:ran:run_1"
    assert [call[0] for call in seam.calls] == [bob.automation_id]


def test_a_raising_run_records_a_bounded_reason_and_does_not_propagate(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    def exploding(*_args, **_kwargs):
        raise RuntimeError(r"boom at C:\Users\secret\path with a token")

    monkeypatch.setattr(automations_module, "_execute", exploding)

    reason = run_due_automation(
        tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW
    )

    assert reason.startswith("automation_error:RuntimeError")
    assert "secret" not in reason
    assert _refusal_rows(tmp_path)[f"automation:{registered.automation_id}"] == reason


# -- Owner controls -----------------------------------------------------------


def test_pause_resume_and_retire_move_the_revision_and_gate_the_due_scan(
    tmp_path: Path,
    registered: Automation,
) -> None:
    store = AutomationStore(tmp_path)
    later = NOW + timedelta(seconds=1200)

    paused = store.set_desired_state(
        registered.automation_id,
        "paused",
        expected_revision=registered.revision,
        reason="owner_requested",
        now=NOW,
    )
    assert paused.desired_state == "paused"
    assert due_automations(tmp_path, universe_id=UNIVERSE, now=later) == []

    with pytest.raises(ValueError):
        store.set_desired_state(
            registered.automation_id,
            "active",
            expected_revision=registered.revision,  # stale
            now=NOW,
        )

    resumed = store.set_desired_state(
        registered.automation_id,
        "active",
        expected_revision=paused.revision,
        now=NOW,
    )
    assert resumed.pause_reason == ""
    assert len(due_automations(tmp_path, universe_id=UNIVERSE, now=later)) == 1

    retired = store.retire(
        registered.automation_id, expected_revision=resumed.revision, now=NOW
    )
    assert retired.retired_at
    assert store.list(universe_id=UNIVERSE) == []
    assert len(store.list(universe_id=UNIVERSE, include_retired=True)) == 1
    assert due_automations(tmp_path, universe_id=UNIVERSE, now=later) == []
    with pytest.raises(ValueError):
        store.set_desired_state(
            registered.automation_id,
            "active",
            expected_revision=retired.revision,
            now=NOW,
        )


# -- 6. The consumer pump -----------------------------------------------------


class _InlineExecutor:
    """Runs submissions on the calling thread so a poll is assertable."""

    def __init__(self) -> None:
        self.submissions: list[tuple] = []

    def submit(self, fn, *args):
        self.submissions.append(args)
        future: Future = Future()
        try:
            future.set_result(fn(*args))
        except BaseException as exc:  # pragma: no cover - surfaced by the assertion
            future.set_exception(exc)
        return future

    def shutdown(self, **_kwargs) -> None:
        pass


def _consumer_with_inline_executor(tmp_path: Path, *, max_concurrency: int = 2):
    consumer = AssignedQueueConsumer(tmp_path, max_concurrency=max_concurrency)
    inline = _InlineExecutor()
    consumer._executor.shutdown(wait=False)
    consumer._executor = inline
    return consumer, inline


def test_poll_once_submits_a_universes_due_automations(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.list_serving_universes",
        lambda _base: [UNIVERSE],
    )
    ran: list[str] = []
    monkeypatch.setattr(
        automations_module,
        "run_due_automation",
        lambda base, automation, due_at, **kwargs: ran.append(
            f"{automation.automation_id}@{due_at}"
        )
        or "ok:ran:run_1",
    )
    consumer, inline = _consumer_with_inline_executor(tmp_path)

    try:
        submitted = consumer.poll_once()
    finally:
        consumer.stop()

    # The poll uses the real clock, so the expected due_at is whatever the due
    # scan itself yields at this instant -- the contract is that the pump passes
    # the scan's pair through untouched, not that it invents one.
    expected = due_automations(
        tmp_path, universe_id=UNIVERSE, now=datetime.now(timezone.utc)
    )
    assert submitted == 1
    assert inline.submissions and inline.submissions[0][0] == UNIVERSE
    assert ran == [f"{registered.automation_id}@{expected[0][1]}"]


def test_poll_once_leaves_a_paused_universe_alone(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.list_serving_universes",
        lambda _base: [UNIVERSE],
    )
    scanned: list[str] = []
    monkeypatch.setattr(
        automations_module,
        "due_automations",
        lambda base, *, universe_id, now: scanned.append(universe_id) or [],
    )
    (tmp_path / UNIVERSE / ".pause").write_text("owner paused", encoding="utf-8")
    consumer, inline = _consumer_with_inline_executor(tmp_path)

    try:
        consumer.poll_once()
    finally:
        consumer.stop()

    assert scanned == []
    assert inline.submissions == []


def test_one_universes_scan_failure_does_not_stop_the_poll(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    _seed_owner(tmp_path, universe_id="universe_bob", owner="acct_bob")
    _copy_assignment_to(tmp_path, universe_id="universe_bob", owner="acct_bob")
    _seed_branch(tmp_path, branch_def_id="branch_bob", author="acct_bob")
    bob = register_automation(
        tmp_path,
        universe_id="universe_bob",
        owner_principal_id="acct_bob",
        name="Bob's digest",
        branch_def_id="branch_bob",
        interval_seconds=600,
        now=NOW,
    )
    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.list_serving_universes",
        lambda _base: [UNIVERSE, "universe_bob"],
    )
    real_due = automations_module.due_automations

    def flaky(base, *, universe_id, now):
        if universe_id == UNIVERSE:
            raise RuntimeError("scan exploded")
        return real_due(base, universe_id=universe_id, now=now)

    monkeypatch.setattr(automations_module, "due_automations", flaky)
    ran: list[str] = []
    monkeypatch.setattr(
        automations_module,
        "run_due_automation",
        lambda base, automation, due_at, **kwargs: ran.append(automation.automation_id)
        or "ok:ran:run_1",
    )
    consumer, _inline = _consumer_with_inline_executor(tmp_path)

    try:
        submitted = consumer.poll_once()
    finally:
        consumer.stop()

    assert submitted == 1
    assert ran == [bob.automation_id]
    refusals = _refusal_rows(tmp_path)
    assert refusals[f"universe:{UNIVERSE}:automations"].startswith(
        "automation_scan_error:RuntimeError"
    )


def test_a_dark_consumer_scans_no_automations_at_all(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    monkeypatch.delenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", raising=False)
    scanned: list[str] = []
    monkeypatch.setattr(
        automations_module,
        "due_automations",
        lambda base, *, universe_id, now: scanned.append(universe_id) or [],
    )
    consumer, inline = _consumer_with_inline_executor(tmp_path)

    try:
        assert consumer.poll_once() == 0
    finally:
        consumer.stop()

    assert scanned == []
    assert inline.submissions == []


# -- Codex ADAPT 2026-08-29 folds ---------------------------------------------


@contextmanager
def _real_providers(**providers):
    """Install a router of fake providers for a REAL run through the session."""
    import tinyassets.providers.call as provider_call_module
    from tinyassets.providers.router import ProviderRouter

    previous_router = provider_call_module.get_provider_router()
    previous_force_mock = provider_call_module.is_force_mock()
    provider_call_module.set_provider_router(ProviderRouter(dict(providers)))
    provider_call_module.set_force_mock(False)
    try:
        yield
    finally:
        provider_call_module.set_provider_router(previous_router)
        provider_call_module.set_force_mock(previous_force_mock)


def _receipt_provider(tmp_path: Path, run_id: str) -> tuple[str, int]:
    """The provider + assignment generation the REAL admission recorded."""
    from tinyassets.storage.provider_work_authority import (
        SQLiteProviderWorkAuthorityStore,
    )

    store = SQLiteProviderWorkAuthorityStore(tmp_path)
    with store.connection() as conn:
        row = conn.execute(
            "SELECT record_json FROM provider_work_receipts WHERE work_item_id = ?",
            (run_id,),
        ).fetchone()
    assert row is not None, f"no provider work receipt for run {run_id}"
    record = json.loads(row[0])
    return str(record["provider"]), int(record["assignment_generation"])


# §5 -- the ACL race


def test_admin_revoked_between_precheck_and_launch_never_reaches_a_provider(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """Codex's exact repro, folded as a test.

    Revoking admin AFTER `_runtime_authority_reason` returns and BEFORE the
    launch used to yield `ok:ran` with one real provider call. The per-node
    authority guard now re-checks before every node body, so the run dies before
    reaching a provider and the automation pauses.

    No seam: the real `_execute`, the real foreground session, a real router.
    """
    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tinyassets.daemon_server import grant_universe_access

    real_check = automations_module._runtime_authority_reason
    checks: list[str] = []

    def racing(base, automation):
        verdict = real_check(base, automation)
        checks.append(verdict)
        if len(checks) == 1:
            # The window: precheck has passed, the run has not launched.
            grant_universe_access(
                tmp_path,
                universe_id=UNIVERSE,
                actor_id=OWNER,
                permission="read",
                granted_by="acct_someone_else",
            )
        return verdict

    monkeypatch.setattr(automations_module, "_runtime_authority_reason", racing)
    fake = _CountingProvider()

    with _real_providers(codex=fake):
        reason = run_due_automation(
            tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW
        )

    assert checks[0] == ""  # the precheck really did pass
    assert len(checks) > 1  # ...and the guard really did re-check
    assert fake.calls == []  # the whole point: zero provider calls
    assert reason == "run_failed:cancelled"
    paused = AutomationStore(tmp_path).get(registered.automation_id)
    assert paused is not None
    assert paused.desired_state == "paused"
    assert paused.pause_reason == "owner_lost_admin"


def test_a_nested_child_branch_is_guarded_too(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Codex round 2 §1: the guard has to reach CHILD prompts.

    An `invoke_branch` node emits no `starting` event of its own, and the child
    launch used to drop `on_node_status`, so a nested prompt's provider call ran
    unguarded. The single-root ACL test stays green under that omission, which
    is exactly why this fixture exists.
    """
    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tinyassets.daemon_server import grant_universe_access
    from tinyassets.runs import get_run

    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_nested_branch(tmp_path)
    nested = register_automation(
        tmp_path,
        **_registration_kwargs(
            name="nested", branch_def_id="branch_parent_invoke"
        ),
    )
    real_check = automations_module._runtime_authority_reason
    checks: list[str] = []

    def racing(base, automation):
        verdict = real_check(base, automation)
        checks.append(verdict)
        if len(checks) == 1:
            grant_universe_access(
                tmp_path,
                universe_id=UNIVERSE,
                actor_id=OWNER,
                permission="read",
                granted_by="acct_someone_else",
            )
        return verdict

    monkeypatch.setattr(automations_module, "_runtime_authority_reason", racing)
    fake = _CountingProvider()

    with _real_providers(codex=fake):
        run_due_automation(
            tmp_path, nested, "2026-08-29T12:10:00+00:00", now=NOW
        )

    assert checks[0] == ""
    assert fake.calls == []
    # `fake.calls == []` alone is NOT proof here, and neither is the guard
    # verdict list: a blocking child shares the parent's session, whose snapshot
    # has no prompt node, so its provider call is refused by admission even with
    # the guard absent, and the pause path re-reads authority either way.
    # Measured while writing this test. What DOES discriminate is how the child
    # run ended: guarded it is `cancelled` naming the authority loss; unguarded
    # it is `failed` on the provider-authority refusal.
    child_ids = _child_run_ids(tmp_path, "branch_child_prompt")
    assert child_ids, "the child run never started"
    child = get_run(tmp_path, child_ids[0]) or {}
    assert child.get("status") == "cancelled"
    assert "automation_owner_lost_admin" in str(child.get("error"))
    paused = AutomationStore(tmp_path).get(nested.automation_id)
    assert paused is not None
    assert paused.desired_state == "paused"
    assert paused.pause_reason == "owner_lost_admin"


def test_an_async_child_branch_is_guarded_too(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The async child launch drops the callback in exactly the same way.

    The parent returns as soon as the child is queued, so this waits for the
    child run itself before asserting -- a bare `fake.calls == []` right after
    `run_due_automation` would be racy and could pass for the wrong reason.
    """
    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tinyassets.daemon_server import grant_universe_access
    from tinyassets.runs import get_run, wait_for

    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_nested_branch(
        tmp_path,
        parent_id="branch_parent_async",
        child_id="branch_child_async",
        wait_mode="async",
    )
    nested = register_automation(
        tmp_path,
        **_registration_kwargs(
            name="nested async", branch_def_id="branch_parent_async"
        ),
    )
    real_check = automations_module._runtime_authority_reason
    checks: list[str] = []

    def racing(base, automation):
        verdict = real_check(base, automation)
        checks.append(verdict)
        if len(checks) == 1:
            grant_universe_access(
                tmp_path,
                universe_id=UNIVERSE,
                actor_id=OWNER,
                permission="read",
                granted_by="acct_someone_else",
            )
        return verdict

    monkeypatch.setattr(automations_module, "_runtime_authority_reason", racing)
    fake = _CountingProvider()

    with _real_providers(codex=fake):
        run_due_automation(
            tmp_path, nested, "2026-08-29T12:10:00+00:00", now=NOW
        )
        child_ids = _child_run_ids(tmp_path, "branch_child_async")
        for child_id in child_ids:
            wait_for(child_id, timeout=30)

    assert checks[0] == ""
    # The guard REALLY ran inside the child; without this the assertion below
    # could pass for an unrelated admission failure.
    assert "owner_lost_admin" in checks[1:]
    assert fake.calls == []
    assert child_ids, "the child run never started"
    assert (get_run(tmp_path, child_ids[0]) or {}).get("status") == "cancelled"
    assert "automation_owner_lost_admin" in str(
        (get_run(tmp_path, child_ids[0]) or {}).get("error")
    )
    for child_id in child_ids:
        assert (get_run(tmp_path, child_id) or {}).get("status") != "completed"


def test_an_async_version_pinned_child_is_guarded_too(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The fourth child path. Same discriminator as the other nested tests.

    Round 2 threaded three of the four child launches; the version-pinned async
    one was still bare (Codex round 3 §a), and the unit test for it asserted
    only that the launch happened, so nothing went red.
    """
    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tinyassets.daemon_server import grant_universe_access
    from tinyassets.runs import get_run, wait_for

    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_version_invoking_branch(tmp_path)
    versioned = register_automation(
        tmp_path,
        **_registration_kwargs(
            name="versioned", branch_def_id="branch_parent_version"
        ),
    )
    real_check = automations_module._runtime_authority_reason
    checks: list[str] = []

    def racing(base, automation):
        verdict = real_check(base, automation)
        checks.append(verdict)
        if len(checks) == 1:
            grant_universe_access(
                tmp_path,
                universe_id=UNIVERSE,
                actor_id=OWNER,
                permission="read",
                granted_by="acct_someone_else",
            )
        return verdict

    monkeypatch.setattr(automations_module, "_runtime_authority_reason", racing)
    fake = _CountingProvider()

    with _real_providers(codex=fake):
        run_due_automation(
            tmp_path, versioned, "2026-08-29T12:10:00+00:00", now=NOW
        )
        child_ids = _child_run_ids(tmp_path, "branch_child_version")
        for child_id in child_ids:
            wait_for(child_id, timeout=30)

    assert checks[0] == ""
    assert fake.calls == []
    assert child_ids, "the versioned child run never started"
    child = get_run(tmp_path, child_ids[0]) or {}
    assert child.get("status") == "cancelled"
    assert "automation_owner_lost_admin" in str(child.get("error"))


@pytest.mark.parametrize("child_visibility", ["public", "private"])
def test_an_owners_automation_runs_its_own_unpublished_pinned_child(
    tmp_path: Path,
    monkeypatch,
    child_visibility: str,
) -> None:
    """The publication mark gates OTHER people. An owner's own automation (run
    actor ``universe:<id>``, owner the user) pins a version of the owner's own
    branch that was never published -- on a public or a private branch -- and
    that child must run (astra refute 2026-09-30: the private case was still
    refused, because the owner path keyed on an owner_user_id automation runs
    do not carry)."""
    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tinyassets.branch_versions import list_branch_versions
    from tinyassets.runs import get_run, wait_for

    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_version_invoking_branch(tmp_path, public=False, child_visibility=child_visibility)
    assert [v.public for v in list_branch_versions(tmp_path, "branch_child_version")] == [False]
    versioned = register_automation(
        tmp_path,
        **_registration_kwargs(name="versioned", branch_def_id="branch_parent_version"),
    )
    import threading

    from tinyassets.foreground_run_provider import _ForegroundRunProviderSession
    from tinyassets.storage.agent_request_usage import UsageStore

    parent_finished = threading.Event()
    child_entered = threading.Event()
    call = _ForegroundRunProviderSession._call

    def after_parent(self, *args, **kwargs):
        if self._branch_def_id == "branch_child_version":
            child_entered.set()
            assert parent_finished.wait(10), "parent failed to release its run session"
        return call(self, *args, **kwargs)

    monkeypatch.setattr(_ForegroundRunProviderSession, "_call", after_parent)
    fake = _CountingProvider()
    with _real_providers(codex=fake):
        try:
            result = run_due_automation(
                tmp_path, versioned, "2026-08-29T12:10:00+00:00", now=NOW,
            )
            assert child_entered.wait(10), "the admitted child never reached its provider call"
            parent_id = result.removeprefix("ok:ran:")
            assert get_run(tmp_path, parent_id)["status"] == "completed"
            parent_usage = UsageStore(tmp_path).for_subject(OWNER, UNIVERSE, "run", parent_id)
            assert len(parent_usage) == 1 and not parent_usage[0]["closed"]
            assert parent_usage[0]["dispatched"] == 0
        finally:
            parent_finished.set()
        child_ids = _child_run_ids(tmp_path, "branch_child_version")
        for child_id in child_ids:
            wait_for(child_id, timeout=30)

    assert child_ids, "the owner's own pinned child never started"
    assert (get_run(tmp_path, child_ids[0]) or {}).get("status") == "completed"
    child_usage = UsageStore(tmp_path).for_subject(OWNER, UNIVERSE, "run", child_ids[0])
    assert len(child_usage) == 1 and child_usage[0]["closed"]
    assert child_usage[0]["usage_id"] == parent_usage[0]["usage_id"]
    assert child_usage[0]["dispatched"] == len(fake.calls) == 1


def test_the_run_row_names_the_authority_loss_not_a_generic_cancel(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """Codex round 2 §2: an authority cancel used to read like a user's stop."""
    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tinyassets.daemon_server import grant_universe_access
    from tinyassets.runs import get_run

    real_check = automations_module._runtime_authority_reason
    checks: list[str] = []
    started: list[str] = []
    real_execute = automations_module._execute

    def capturing(base, automation, provider_call, branch, inputs, on_run_started=None):
        def note(run_id: str) -> None:
            started.append(run_id)
            if callable(on_run_started):
                on_run_started(run_id)

        return real_execute(base, automation, provider_call, branch, inputs, note)

    def racing(base, automation):
        verdict = real_check(base, automation)
        checks.append(verdict)
        if len(checks) == 1:
            grant_universe_access(
                tmp_path,
                universe_id=UNIVERSE,
                actor_id=OWNER,
                permission="read",
                granted_by="acct_someone_else",
            )
        return verdict

    monkeypatch.setattr(automations_module, "_runtime_authority_reason", racing)
    monkeypatch.setattr(automations_module, "_execute", capturing)

    with _real_providers(codex=_CountingProvider()):
        run_due_automation(
            tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW
        )

    assert started, "the run id was never published"
    record = get_run(tmp_path, started[0])
    assert record is not None
    assert record["status"] == "cancelled"
    assert "automation_owner_lost_admin" in str(record["error"])
    assert "cancelled between nodes" not in str(record["error"])


def test_stop_cancels_the_run_through_the_real_callback_timing(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """Codex round 2 §3c/§6: the previous test inserted the id by hand.

    `on_run_started` used to fire only after `_execute` had finished waiting, so
    `stop()` could never see a live run. This drives the REAL callback, from
    inside the wait, the way the consumer does.
    """
    from tinyassets.runs import is_cancel_requested

    consumer, _inline = _consumer_with_inline_executor(tmp_path)
    seen: list[str] = []

    def waiting(run_id, timeout=None):
        # We are now exactly where a real run blocks. If the id was published
        # before the wait, stop() can find it.
        seen.append(run_id)
        consumer.stop()

    monkeypatch.setattr("tinyassets.runs.wait_for", waiting)
    monkeypatch.setattr(
        automations_module, "_runtime_authority_reason", lambda *_a: ""
    )

    with _real_providers():
        run_due_automation(
            tmp_path,
            registered,
            "2026-08-29T12:10:00+00:00",
            now=NOW,
            consumer_id=consumer.consumer_id,
            on_run_started=consumer._note_automation_run,
        )

    assert seen, "the run never reached the wait"
    assert is_cancel_requested(tmp_path, seen[0]) is True


def test_a_run_that_ignores_cancellation_keeps_the_universe_leased(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """Codex round 2 §3b: releasing on timeout lets a second process double-spend.

    The grace really ELAPSES here. The previous version made both waits fail
    instantly, so it proved "lease retained" without ever exercising the wait it
    is named for (Codex round 3 §d). A fake clock advances past the configured
    grace while the future stays unfinished.
    """
    from concurrent.futures import Future

    grace = 7.0
    monkeypatch.setenv("AUTOMATION_CANCEL_GRACE_SECONDS", str(int(grace)))
    # A run has NO timeout by default -- it finishes when it is finished (founder,
    # 2026-09-30) -- so the timeout path this exercises is only reachable when an
    # OPERATOR sets one. Set it here; the behaviour under test is what happens
    # when a worker then ignores the cancel, which is unchanged.
    monkeypatch.setenv("AUTOMATION_RUN_TIMEOUT_SECONDS", "900")
    clock = {"now": 0.0}
    still_running: Future = Future()  # never resolved: the worker ignores cancel
    waits: list[tuple[str, float | None, float]] = []

    def waiting(run_id, timeout=None):
        waits.append((run_id, timeout, clock["now"]))
        # The run wait and the post-cancel grace both consume their full budget
        # on the fake clock, then report the future is STILL not done.
        clock["now"] += float(timeout or 0.0)
        if still_running.done():  # pragma: no cover - defensive
            return
        raise TimeoutError("this worker ignores cancellation")

    monkeypatch.setattr("tinyassets.runs.wait_for", waiting)
    monkeypatch.setattr(
        automations_module, "_runtime_authority_reason", lambda *_a: ""
    )
    consumer, _inline = _consumer_with_inline_executor(tmp_path)

    with _real_providers():
        try:
            consumer._run_automations(
                UNIVERSE, [(registered, "2026-08-29T12:10:00+00:00")]
            )
        finally:
            consumer.stop()

    # Two waits: the run timeout, then the grace -- and the grace really ran
    # for its configured duration on the clock.
    assert [entry[1] for entry in waits] == [run_timeout_seconds(), grace]
    assert clock["now"] == run_timeout_seconds() + grace
    assert (
        _refusal_rows(tmp_path)[f"automation:{registered.automation_id}"]
        == "run_timeout_unreleased"
    )
    # The universe is NOT handed back while a provider call may still be live,
    # and the row is still there carrying its full TTL rather than deleted.
    store = AutomationStore(tmp_path)
    key = automation_lease_key(registered)
    assert store.universe_lease_holder(
        key, now=datetime.now(timezone.utc)
    ) == consumer.consumer_id
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT holder, expires_at FROM universe_leases WHERE universe_id = ?",
            (key,),
        ).fetchone()
    assert row is not None, "the lease row was deleted, not retained"
    assert str(row[0]) == consumer.consumer_id
    expires = datetime.fromisoformat(str(row[1]))
    assert expires > datetime.now(timezone.utc)


def test_a_stale_legacy_lease_frees_the_universe_after_the_claim_envelope(
    tmp_path: Path,
    registered: Automation,
) -> None:
    """A crashed legacy process must free the universe when its Epoch2 claim
    becomes recoverable, not 2.5 hours later (Codex round 3 §b)."""
    store = AutomationStore(tmp_path)
    crashed_at = datetime.now(timezone.utc) - timedelta(
        seconds=EPOCH2_TASK_LEASE_SECONDS + 60
    )
    assert store.acquire_universe_lease(
        UNIVERSE,
        holder="worker_assigned_crashed_legacy",
        now=crashed_at,
        ttl_seconds=EPOCH2_TASK_LEASE_SECONDS,
    ) is True

    # Past the claim envelope: nobody is refreshing, so it is stealable.
    assert store.universe_lease_holder(
        UNIVERSE, now=datetime.now(timezone.utc)
    ) == ""
    assert store.acquire_universe_lease(
        UNIVERSE,
        holder="worker_assigned_new_process",
        now=datetime.now(timezone.utc),
        ttl_seconds=EPOCH2_TASK_LEASE_SECONDS,
    ) is True


def test_a_universe_lease_left_by_an_older_process_keeps_agents_out(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """Codex round 2 §3a, one direction kept: a whole-universe lease held by a
    live older process (the retired epoch-2 claim pass took one) still keeps
    every agent out. The claim pass itself is gone (plan C2)."""
    ran: list[str] = []
    monkeypatch.setattr(
        automations_module,
        "run_due_automation",
        lambda *_a, **_k: ran.append("automation") or "ok:ran:run_1",
    )
    store = AutomationStore(tmp_path)
    assert store.acquire_universe_lease(
        UNIVERSE,
        holder="worker_assigned_legacy_process",
        now=datetime.now(timezone.utc),
        ttl_seconds=3600,
    ) is True
    consumer, _inline = _consumer_with_inline_executor(tmp_path)
    try:
        consumer._run_automations(UNIVERSE, [(registered, "2026-08-29T12:10:00+00:00")])
    finally:
        consumer.stop()
    assert ran == []
    assert _refusal_rows(tmp_path)[f"universe:{UNIVERSE}:automations"] == (
        "universe_busy:worker_assigned_legacy_process"
    )


# §6 -- registration refuses what admission refuses


def test_registration_refuses_a_branch_the_owner_does_not_author(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex registered a public Bob-authored branch for Alice; every run then
    failed before the provider, forever, with the row still active."""
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(
        tmp_path,
        branch_def_id="branch_bob_public",
        author="acct_bob",
        visibility="public",
    )

    with pytest.raises(AutomationUnavailable) as caught:
        register_automation(
            tmp_path, **_registration_kwargs(branch_def_id="branch_bob_public")
        )

    assert caught.value.reason == "branch_not_owned"
    assert AutomationStore(tmp_path).list(universe_id=UNIVERSE) == []


def test_an_admission_failure_pauses_instead_of_looping_every_period(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        automations_module,
        "_execute",
        lambda *_a, **_k: _FakeOutcome(
            run_id="run_x",
            status="failed",
        ).with_error("Provider authority admission failed: ProviderAuthorityHeldError"),
    )

    reason = run_due_automation(
        tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW
    )

    assert reason == "run_failed:failed"
    row = AutomationStore(tmp_path).get(registered.automation_id)
    assert row is not None
    assert row.desired_state == "paused"
    assert row.pause_reason == "run_admission_refused"
    assert (
        _refusal_rows(tmp_path)[f"automation:{registered.automation_id}"]
        == "run_admission_refused"
    )


def test_a_failure_while_authority_is_gone_pauses_on_the_first_attempt(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """Authority loss beats the failure counter.

    The per-node guard names the cause in the run error when it fires, but it
    only fires if a node starts. A run that dies EARLIER -- compile error, a
    branch with no prompt node, an admission blip -- carries no such marker,
    and retrying it three times while the owner no longer holds admin would
    spend a subscription they no longer control. So the pause path re-reads
    live authority before it consults the counter.
    """
    from tinyassets.daemon_server import grant_universe_access

    monkeypatch.setattr(
        automations_module,
        "_execute",
        lambda *_a, **_k: _FakeOutcome(run_id="run_x", status="failed").with_error(
            "the model was briefly unreachable"
        ),
    )
    real_check = automations_module._runtime_authority_reason
    calls: list[str] = []

    def racing(base, automation):
        verdict = real_check(base, automation)
        calls.append(verdict)
        if len(calls) == 1:
            grant_universe_access(
                tmp_path,
                universe_id=UNIVERSE,
                actor_id=OWNER,
                permission="read",
                granted_by="acct_someone_else",
            )
        return verdict

    monkeypatch.setattr(automations_module, "_runtime_authority_reason", racing)

    run_due_automation(tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW)

    row = AutomationStore(tmp_path).get(registered.automation_id)
    assert row is not None
    assert row.consecutive_failures == 1  # nowhere near the counter
    assert row.desired_state == "paused"
    assert row.pause_reason == "owner_lost_admin"


def test_three_consecutive_ordinary_failures_pause_the_automation(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """A transient failure must not retire an automation; a permanent one must."""
    monkeypatch.setattr(
        automations_module,
        "_execute",
        lambda *_a, **_k: _FakeOutcome(run_id="run_x", status="failed").with_error(
            "the model was briefly unreachable"
        ),
    )
    store = AutomationStore(tmp_path)
    states = []
    for index in range(MAX_CONSECUTIVE_FAILURES):
        run_due_automation(
            tmp_path,
            registered,
            f"2026-08-29T12:{10 + index}:00+00:00",
            now=NOW,
        )
        row = store.get(registered.automation_id)
        assert row is not None
        states.append((row.desired_state, row.consecutive_failures))

    assert states[0] == ("active", 1)
    assert states[1] == ("active", 2)
    assert states[2] == ("paused", 3)
    assert store.get(registered.automation_id).pause_reason == "repeated_failures"


def test_a_success_resets_the_consecutive_failure_counter(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    outcomes = [
        _FakeOutcome(run_id="run_a", status="failed").with_error("transient"),
        _FakeOutcome(run_id="run_b", status="completed"),
    ]
    monkeypatch.setattr(
        automations_module, "_execute", lambda *_a, **_k: outcomes.pop(0)
    )
    store = AutomationStore(tmp_path)

    run_due_automation(tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW)
    assert store.get(registered.automation_id).consecutive_failures == 1
    reason = run_due_automation(
        tmp_path, registered, "2026-08-29T12:20:00+00:00", now=NOW
    )

    row = store.get(registered.automation_id)
    assert reason == "ok:ran:run_b"
    assert row.consecutive_failures == 0
    assert row.desired_state == "active"
    # The ROW reads a plain owner-legible `ok`; the ledger keeps the convention.
    assert row.last_reason == "ok"
    assert row.last_run_id == "run_b"
    assert (
        _refusal_rows(tmp_path)[f"automation:{registered.automation_id}"]
        == "ok:ran:run_b"
    )


# §7 -- engine admission and the cron cadence floor


def test_an_automation_binds_its_admission_to_the_run_it_starts(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """Codex round 1 (P1): a scheduled run admitted against the ledger but
    never bound could not settle as a read, so a GET-only automation spent
    the write budget on every period."""
    import sqlite3
    import types

    from tinyassets import engine_admissions as adm

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(automations_module, "_load_branch", lambda base, a: object())
    monkeypatch.setattr(
        automations_module, "_bind_automation_provider_call", lambda base, a: None
    )
    started: list[str] = []

    def seam(base, automation, provider_call, branch, inputs, on_run_started):
        on_run_started("run-auto-1")                      # published before blocking
        return types.SimpleNamespace(run_id="run-auto-1", status="completed")

    monkeypatch.setattr(automations_module, "_execute", seam)

    reason = run_due_automation(
        tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW,
        on_run_started=started.append,
    )

    assert reason == "ok:ran:run-auto-1" and started == ["run-auto-1"]
    conn = sqlite3.connect(str(tmp_path / adm.LEDGER_NAME))
    rows = conn.execute(
        "SELECT universe_id, kind, run_id FROM admissions WHERE kind != 'engine'"
    ).fetchall()
    conn.close()
    # (The registration itself is an engine-kind row, plan item 6.)
    assert rows == [(UNIVERSE, "write", "run-auto-1")]
    # ...so a read-only period can settle off the write budget
    assert adm.reclassify_read("run-auto-1", db=tmp_path / adm.LEDGER_NAME) is True


@pytest.mark.parametrize("expr", ["* * * * *", "*/2 * * * *", "0,3 * * * *"])
def test_a_minute_cron_is_accepted_now_that_usage_bounds_it(
    tmp_path: Path, monkeypatch, expr: str
) -> None:
    """Plan item 6: no cron gap floor. Each fire is charged as a run."""
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)

    created = register_automation(
        tmp_path, **_registration_kwargs(interval_seconds=0, cron_expr=expr)
    )

    assert created.cron_expr == expr


@pytest.mark.parametrize("expr", ["0,5 * * * *", "*/5 * * * *"])
def test_a_cron_cadence_at_the_floor_is_accepted(
    tmp_path: Path, monkeypatch, expr: str
) -> None:
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)

    automation = register_automation(
        tmp_path, **_registration_kwargs(interval_seconds=0, cron_expr=expr)
    )

    assert automation.cron_expr == expr


def test_cron_min_gap_measures_the_smallest_gap_including_the_wrap() -> None:
    """`0,3 * * * *` looks hourly until you measure INSIDE the hour."""
    assert cron_min_gap_seconds("* * * * *") == 60
    assert cron_min_gap_seconds("0,3 * * * *") == 180
    assert cron_min_gap_seconds("*/5 * * * *") == 300
    assert cron_min_gap_seconds("0 * * * *") == 3600
    assert cron_min_gap_seconds("0 0 * * *") == 86400
    # The wrap term is load-bearing, not decoration. Sunday+Monday at midnight
    # sits 6 days apart INSIDE a Monday-origin week and 1 day apart across its
    # boundary. Measuring only consecutive matches reports 518400 here. A brute
    # force over 1,350 expressions found 30 of this shape; none of them are near
    # the 300s floor, so only this assertion can catch dropping the wrap.
    assert cron_min_gap_seconds("0 0 * * 0,1") == 86400


# §8 / §1 -- the shared universe lease, pause, timeout and stop


def test_a_universe_lease_held_by_a_live_holder_blocks_a_second_process(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """The cross-process fence `_active` cannot be: an old process still working
    a universe blocks a restarted one, whose `_active` map is empty."""
    ran: list[str] = []
    monkeypatch.setattr(
        automations_module,
        "run_due_automation",
        lambda *_a, **_k: ran.append("ran") or "ok:ran:run_1",
    )
    store = AutomationStore(tmp_path)
    assert store.acquire_universe_lease(
        UNIVERSE,
        holder="worker_assigned_old_process",
        now=datetime.now(timezone.utc),
        ttl_seconds=3600,
    ) is True
    consumer, _inline = _consumer_with_inline_executor(tmp_path)

    try:
        consumer._run_automations(UNIVERSE, [(registered, "2026-08-29T12:10:00+00:00")])
    finally:
        consumer.stop()

    assert ran == []
    assert _refusal_rows(tmp_path)[f"universe:{UNIVERSE}:automations"] == (
        "universe_busy:worker_assigned_old_process"
    )


def test_an_expired_universe_lease_does_not_wedge_the_universe(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """A process that died mid-run must not hold its universe forever."""
    ran: list[str] = []
    monkeypatch.setattr(
        automations_module,
        "run_due_automation",
        lambda *_a, **_k: ran.append("ran") or "ok:ran:run_1",
    )
    store = AutomationStore(tmp_path)
    store.acquire_universe_lease(
        UNIVERSE,
        holder="worker_assigned_dead",
        now=datetime.now(timezone.utc) - timedelta(hours=4),
        ttl_seconds=60,
    )
    assert store.universe_lease_holder(UNIVERSE, now=datetime.now(timezone.utc)) == ""
    consumer, _inline = _consumer_with_inline_executor(tmp_path)

    try:
        consumer._run_automations(UNIVERSE, [(registered, "2026-08-29T12:10:00+00:00")])
    finally:
        consumer.stop()

    assert ran == ["ran"]


def test_the_lease_is_released_when_the_batch_finishes(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        automations_module, "run_due_automation", lambda *_a, **_k: "ok:ran:run_1"
    )
    consumer, _inline = _consumer_with_inline_executor(tmp_path)

    try:
        consumer._run_automations(UNIVERSE, [(registered, "2026-08-29T12:10:00+00:00")])
    finally:
        consumer.stop()

    holder = AutomationStore(tmp_path).universe_lease_holder(
        UNIVERSE, now=datetime.now(timezone.utc)
    )
    assert holder == ""


def test_pausing_mid_batch_stops_the_remaining_rows(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    _seed_branch(tmp_path, branch_def_id="branch_second")
    second = register_automation(
        tmp_path,
        **_registration_kwargs(name="second", branch_def_id="branch_second"),
    )
    ran: list[str] = []

    def running(base, automation, due_at, **_kwargs):
        ran.append(automation.automation_id)
        (tmp_path / UNIVERSE / ".pause").write_text("owner", encoding="utf-8")
        return "ok:ran:run_1"

    monkeypatch.setattr(automations_module, "run_due_automation", running)
    consumer, _inline = _consumer_with_inline_executor(tmp_path)

    try:
        consumer._run_automations(
            UNIVERSE,
            [
                (registered, "2026-08-29T12:10:00+00:00"),
                (second, "2026-08-29T12:10:00+00:00"),
            ],
        )
    finally:
        consumer.stop()

    assert ran == [registered.automation_id]
    assert _refusal_rows(tmp_path)[f"universe:{UNIVERSE}:automations"] == "paused"


def test_a_run_that_outlives_its_timeout_is_cancelled_not_abandoned(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """An unbounded wait holds the worker AND its provider authority claim."""
    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tinyassets.runs import is_cancel_requested

    started: list[str] = []
    waited: list[float | None] = []
    monkeypatch.setenv("AUTOMATION_RUN_TIMEOUT_SECONDS", "1234")

    def timing_out(run_id, timeout=None):
        started.append(run_id)
        waited.append(timeout)
        # First call is the run wait and times out; the second is the
        # post-cancel grace, which returns -- this worker HONOURS the cancel.
        if len(waited) == 1:
            raise TimeoutError("graph never finished")

    monkeypatch.setattr("tinyassets.runs.wait_for", timing_out)
    fake = _CountingProvider()

    with _real_providers(codex=fake):
        reason = run_due_automation(
            tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW
        )

    assert reason == "run_timeout"
    # Two waits: the bounded run wait, then the post-cancel grace on the SAME run.
    assert started == [started[0], started[0]]
    # The wait is actually BOUNDED, and by the configured value -- a `wait_for`
    # with no timeout would block this consumer slot forever.
    assert waited[0] == 1234.0
    assert waited[1] == cancel_grace_seconds()
    assert is_cancel_requested(tmp_path, started[0]) is True
    assert (
        _refusal_rows(tmp_path)[f"automation:{registered.automation_id}"]
        == "run_timeout"
    )
    # The fence row stays: this instant was attempted and must not relaunch.
    assert run_due_automation(
        tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW
    ) == "attempt_exists"


@pytest.mark.parametrize("status", ["running", "completed"])
def test_stop_cancels_an_in_flight_automation_run(tmp_path: Path, status: str) -> None:
    from tinyassets.runs import create_run, is_cancel_requested, update_run_status

    consumer, _inline = _consumer_with_inline_executor(tmp_path)
    # Production publishes this ID only after execute_branch_async has persisted
    # the run. Use the actual storage contract, not a never-created placeholder.
    run_id = create_run(tmp_path, branch_def_id="test-automation", thread_id="test",
                        inputs={}, actor=f"universe:{UNIVERSE}")
    update_run_status(tmp_path, run_id, status=status)
    consumer._note_automation_run(run_id)

    consumer.stop()

    assert is_cancel_requested(tmp_path, run_id) is (status == "running")


# §6 -- no silent skips


def test_an_already_claimed_instant_is_recorded_not_silently_skipped(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    seam = _SeamRecorder()
    monkeypatch.setattr(automations_module, "_execute", seam)
    AutomationStore(tmp_path).claim_attempt(
        registered.automation_id, "2026-08-29T12:10:00+00:00", now=NOW
    )

    reason = run_due_automation(
        tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW
    )

    assert reason == "attempt_exists"
    assert seam.calls == []
    assert (
        _refusal_rows(tmp_path)[f"automation:{registered.automation_id}"]
        == "attempt_exists"
    )


def test_a_fence_write_failure_is_recorded_as_claim_error(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """A SQLite failure in the claim used to escape with no row and no reason."""
    seam = _SeamRecorder()
    monkeypatch.setattr(automations_module, "_execute", seam)

    def exploding(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(AutomationStore, "claim_attempt", exploding)

    reason = run_due_automation(
        tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW
    )

    assert reason.startswith("claim_error:OperationalError")
    assert seam.calls == []
    assert (
        _refusal_rows(tmp_path)[f"automation:{registered.automation_id}"] == reason
    )


# §9 -- tests that exercise the real thing


def test_the_real_admission_records_the_current_assignment_on_the_receipt(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """The no-pin property, through the REAL session and its real receipt.

    The previous version replaced `_execute` and observed a monkeypatched
    session factory, so it could not see admission or custody at all (Codex §9).
    This one runs the real path and reads what admission ACTUALLY wrote, then
    takes the live assignment away and shows the automation cannot run without
    it.

    Deliberately NOT a provider flip. Rewriting `provider` on the assignment row
    is not a rebind: `_current_serving_authority` re-derives the agent binding
    and credential custody, which a hand-edited row no longer matches, so the
    run fails on the binding rather than on the property under test. A real flip
    needs a second deposited subscription, which this fixture has no way to
    seed. What IS provable here is the same claim from the other side -- the
    receipt carries the assignment that was live at run time, and with no live
    assignment there is nothing stored anywhere to run from.
    """
    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tinyassets.provider_assignment import load_provider_assignment

    live = load_provider_assignment(tmp_path, universe_id=UNIVERSE)
    assert live is not None
    fake = _CountingProvider()

    with _real_providers(codex=fake):
        first = run_due_automation(
            tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW
        )
    assert first.startswith("ok:ran:"), first
    provider, generation = _receipt_provider(tmp_path, first.split("ok:ran:", 1)[1])

    # What admission recorded is what the ASSIGNMENT said, not what the
    # automation said -- the automation says nothing about a provider.
    assert provider == live.provider == "codex"
    assert generation == live.generation
    assert len(fake.calls) == 1

    # Take the assignment away. A row carrying a pin would still run.
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "DELETE FROM provider_assignments WHERE universe_id = ?", (UNIVERSE,)
        )
        conn.commit()

    with _real_providers(codex=fake):
        second = run_due_automation(
            tmp_path, registered, "2026-08-29T12:20:00+00:00", now=NOW
        )

    assert second == "no_serving_assignment"
    assert len(fake.calls) == 1  # unchanged: nothing ran from a pin


def test_a_restart_into_a_new_period_cannot_overlap_the_old_process(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """Codex §2: a restart after a period boundary derives a NEW due_at, which
    the attempt fence does not cover. The universe lease does."""
    ran: list[str] = []
    monkeypatch.setattr(
        automations_module,
        "run_due_automation",
        lambda base, automation, due_at, **_k: ran.append(due_at) or "ok:ran:run_1",
    )
    first_period = NOW + timedelta(seconds=900)
    second_period = NOW + timedelta(seconds=1500)
    due_one = due_automations(tmp_path, universe_id=UNIVERSE, now=first_period)
    due_two = due_automations(tmp_path, universe_id=UNIVERSE, now=second_period)
    assert due_one[0][1] != due_two[0][1]  # genuinely different periods

    old_consumer, _old_inline = _consumer_with_inline_executor(tmp_path)
    new_consumer, _new_inline = _consumer_with_inline_executor(tmp_path)
    assert old_consumer.consumer_id != new_consumer.consumer_id
    try:
        # The old process is mid-batch: it holds the universe lease.
        AutomationStore(tmp_path).acquire_universe_lease(
            UNIVERSE,
            holder=old_consumer.consumer_id,
            now=datetime.now(timezone.utc),
            ttl_seconds=3600,
        )
        new_consumer._run_automations(UNIVERSE, due_two)
    finally:
        old_consumer.stop()
        new_consumer.stop()

    assert ran == []
    assert _refusal_rows(tmp_path)[f"universe:{UNIVERSE}:automations"].startswith(
        "universe_busy:"
    )


# -- Liveness is not activity -------------------------------------------------


def test_a_poll_beats_for_a_serving_universe_with_no_runtime_at_all(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """The watchdog asks "is the daemon alive", not "does it have a runtime".

    Verified on the droplet 2026-08-29: every runtime row is fleet-era, so
    `_serving_runtime` returns None for every universe and the beat file stopped
    being written. `deploy/daemon-watchdog.sh` restarts on a beat older than
    900s, so its timer had to stay disabled and the host-services installer
    refuses to run while it is.

    Note on what is asserted: the watchdog measures the file's MTIME
    (`stat -c %Y`, daemon-watchdog.sh:78), not the `ts` field, so the
    load-bearing property is that every poll REWRITES the file. That is checked
    by backdating it and polling again. `ts` is asserted too because that is the
    field human readers and `_classify_epoch2_workers` consume.
    """
    from tinyassets.runtime.assigned_queue_consumer import (
        supervisor_heartbeat_filename,
    )

    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.list_serving_universes",
        lambda _base: [UNIVERSE],
    )
    monkeypatch.setattr(
        automations_module,
        "run_due_automation",
        lambda *_args, **_kwargs: "ok:ran:run_1",
    )
    consumer, _inline = _consumer_with_inline_executor(tmp_path)
    before = datetime.now(timezone.utc).replace(microsecond=0)
    beat_path = tmp_path / UNIVERSE / supervisor_heartbeat_filename(
        consumer.consumer_id
    )

    try:
        consumer.poll_once()
        assert beat_path.is_file()
        # Every poll REWRITES the beat -- the property the watchdog actually
        # measures. Backdate it an hour; the next poll must lift it back.
        stale = datetime.now(timezone.utc).timestamp() - 3600
        os.utime(beat_path, (stale, stale))
        assert beat_path.stat().st_mtime <= stale + 1
        consumer.poll_once()
        age = datetime.now(timezone.utc).timestamp() - beat_path.stat().st_mtime
    finally:
        consumer.stop()

    assert age < 60
    beat = json.loads(beat_path.read_text(encoding="utf-8"))
    assert beat["worker_id"] == consumer.consumer_id
    assert beat["universe_id"] == UNIVERSE
    assert beat["subprocess_pid"] == os.getpid()
    stamped = datetime.strptime(beat["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    assert before - timedelta(seconds=5) <= stamped
    assert stamped <= datetime.now(timezone.utc) + timedelta(seconds=5)
    # No runtime means no audience and no invented executor identity...
    assert beat["runtime_instance_id"] == ""
    # ...and it says so honestly: this universe HAS a ready serving assignment,
    # so it is never told "no serving provider selected" (which sent the
    # founder's universe hunting for a selection surface that does not exist,
    # app thread 2026-09-02). The `ok:` row is filtered out of status.
    assert _refusal_rows(tmp_path)[f"universe:{UNIVERSE}:-"] == "ok:serving"


def test_a_paused_universe_still_beats(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """Skipping the beat while paused turns the P0 pause repair into a restart
    loop: a restart preserves `.pause`, so the daemon would never beat again."""
    from tinyassets.runtime.assigned_queue_consumer import (
        supervisor_heartbeat_filename,
    )

    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.list_serving_universes",
        lambda _base: [UNIVERSE],
    )
    (tmp_path / UNIVERSE / ".pause").write_text("owner paused", encoding="utf-8")
    consumer, _inline = _consumer_with_inline_executor(tmp_path)

    try:
        consumer.poll_once()
    finally:
        consumer.stop()

    assert (
        tmp_path / UNIVERSE / supervisor_heartbeat_filename(consumer.consumer_id)
    ).is_file()


# -- 7. The real path ---------------------------------------------------------


def test_the_real_run_path_admits_through_the_live_session_and_records_a_run(
    tmp_path: Path,
    registered: Automation,
    monkeypatch,
) -> None:
    """No seam: the branch runs through the real foreground session.

    This is the whole D1 claim end to end -- the automation row carries no
    provider, and the run still reaches the universe's seeded codex assignment,
    admits on the owner's own authority, and lands a run row actored to the
    universe.
    """
    import tinyassets.providers.call as provider_call_module
    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tinyassets.providers.router import ProviderRouter
    from tinyassets.runs import get_run

    fake = _CountingProvider()
    previous_router = provider_call_module.get_provider_router()
    previous_force_mock = provider_call_module.is_force_mock()
    provider_call_module.set_provider_router(ProviderRouter({"codex": fake}))
    provider_call_module.set_force_mock(False)
    try:
        reason = run_due_automation(
            tmp_path,
            registered,
            "2026-08-29T12:10:00+00:00",
            now=NOW,
            consumer_id="worker_assigned_real",
        )
    finally:
        provider_call_module.set_provider_router(previous_router)
        provider_call_module.set_force_mock(previous_force_mock)

    assert reason.startswith("ok:ran:"), reason
    run_id = reason.split("ok:ran:", 1)[1]
    record = get_run(tmp_path, run_id)
    assert record is not None
    assert record["status"] == "completed"
    assert record["actor"] == f"universe:{UNIVERSE}"
    assert record["run_name"] == f"automation:{registered.automation_id[:8]}"
    assert len(fake.calls) == 1

    finished = AutomationStore(tmp_path).get(registered.automation_id)
    assert finished is not None
    assert finished.last_run_id == run_id
    assert finished.last_due_at == "2026-08-29T12:10:00+00:00"
    assert finished.desired_state == "active"
    assert _refusal_rows(tmp_path)[f"automation:{registered.automation_id}"] == reason


def test_waiting_for_a_seat_claims_no_attempt_and_counts_as_no_failure(
    tmp_path, registered, monkeypatch,
):
    """Over the account's seat count an automation WAITS: no attempt is claimed,
    nothing counts toward `MAX_CONSECUTIVE_FAILURES`, the consumer thread is not
    parked, and the queue position is kept from poll to poll -- all keyed on the
    OWNER's account via `universe_owner`. When a seat frees it runs, and the gate's
    seat is given back BEFORE the run is queued: a seat held by a run waiting for a
    pool worker is the deadlock astra found (round 1)."""
    from tinyassets import universe_seats as seats
    from tinyassets.daemon_server import grant_universe_ownership

    grant_universe_ownership(tmp_path, universe_id=UNIVERSE, owner_id=OWNER)
    key = seats.account_key(UNIVERSE, root=tmp_path)
    assert key == f"account:{OWNER}"
    db = seats.ledger_path(tmp_path)
    held_during_run = []
    seam = _SeamRecorder()

    def execute(*args, **kwargs):
        held_during_run.append(seats.occupancy(key, db=db)["running"])
        return seam(*args, **kwargs)

    monkeypatch.setattr(automations_module, "_execute", execute)
    blockers = [seats.acquire(key, db=db) for _ in range(2)]  # free: 2 background
    try:
        tickets = set()
        for _poll in range(4):
            started = time.monotonic()
            reason = run_due_automation(
                tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW,
            )
            assert reason == automations_module.WAITING_FOR_SEAT
            assert time.monotonic() - started < 5, "a wait must not park the consumer"
            tickets.add(seats._tickets[f"automation:{registered.automation_id}"])
        assert len(tickets) == 1, "each poll re-presents the same queue position"
        with sqlite3.connect(AutomationStore(tmp_path).db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM automation_attempts").fetchone()[0] == 0
        assert not seam.calls
        current = AutomationStore(tmp_path).get(registered.automation_id)
        assert current.attempt_count == 0
        assert current.consecutive_failures == 0
        assert current.desired_state == "active"
        assert seats.occupancy(key, db=db)["waiting"] == 1
    finally:
        for held in blockers:
            seats.release(held.seat_id, db=db)
    assert run_due_automation(
        tmp_path, registered, "2026-08-29T12:10:00+00:00", now=NOW,
    ) == "ok:ran:run_1"
    assert held_during_run == [0], (
        "no seat may be held by a run still queued for a pool worker; the run's "
        "agent calls take their own at the executor"
    )
    occupancy = seats.occupancy(key, db=db)
    assert (occupancy["running"], occupancy["waiting"]) == (0, 0)
    seats.stop_refresher()
