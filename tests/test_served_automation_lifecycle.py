"""Existing owner controls must be reachable without rewriting private workflows."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace

import pytest

from tests.test_automations import BRANCH, OWNER, UNIVERSE, _seed_branch, _seed_owner
from tests.test_automations_api import CREATE_PAYLOAD
from tests.test_background_budget_finalization_e2e import _seed_serving_assignment
from tinyassets import engine_mcp_server as engine
from tinyassets.automations import AutomationStore


@pytest.fixture
def bound(tmp_path, monkeypatch):
    from tinyassets.auth.middleware import _current_identity

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)
    monkeypatch.setattr(engine, "_GRAPH_ID", UNIVERSE)
    monkeypatch.setattr(engine, "_ACTOR_ID", OWNER)
    from tests.engine_authority_helpers import seed_bound_engine
    seed_bound_engine(monkeypatch)
    token = _current_identity.set(None)
    yield tmp_path
    _current_identity.reset(token)


def create():
    return json.loads(
        engine.write_graph(
            target="automation",
            operation="create",
            payload_json=CREATE_PAYLOAD,
        )
    )


def test_advertised_schema_has_selectors_without_caller_authority():
    from tinyassets.served_tools import BACKEND_ENGINE_CAPABILITIES

    tools = {tool.name: tool for tool in asyncio.run(engine.mcp.list_tools())}
    for handle in ("read_graph", "write_graph"):
        assert handle in BACKEND_ENGINE_CAPABILITIES
        properties = tools[handle].parameters["properties"]
        assert "automation_id" in properties
        assert not {"graph_id", "universe_id", "actor_id", "owner_principal_id"} & properties.keys()
    assert "expected_revision" in tools["write_graph"].parameters["properties"]


def control(row, op, **overrides):
    args = dict(
        target="automation",
        operation=op,
        automation_id=row["automation_id"],
        expected_revision=row["revision"],
    )
    args.update(overrides)
    return json.loads(engine.write_graph(**args))


def read(row):
    return json.loads(
        engine.read_graph(
            target="automation",
            automation_id=row["automation_id"],
        )
    )


def test_documented_lifecycle_reaches_real_adapter_and_store(bound):
    from tinyassets.api.automations import WRITE_ACTIONS
    from tinyassets.auth.middleware import current_identity_or_none
    from tinyassets.served_tools import SERVED_AUTOMATION_WRITE_OPERATIONS

    # The dedicated recurring-work paragraph's operation vocabulary is executable.
    paragraph = engine.write_graph.__doc__.split("**Recurring work:**", 1)[1].split(
        '- ``operation="create"``', 1
    )[0]
    documented = set(re.findall(r'operation="([a-z_]+)"', paragraph))
    assert documented == SERVED_AUTOMATION_WRITE_OPERATIONS == WRITE_ACTIONS
    created = create()
    assert created["status"] == "automation_created"
    row = created["automation"]
    assert row["revision"] == 1
    assert row["owner"] == {"is_you": True}
    listed = json.loads(engine.read_graph(target="automations"))
    # The list names each input's size, never its body; the get returns it.
    (summary,) = listed["automations"]
    assert summary.pop("input_chars") == {
        key: len(value if isinstance(value, str) else json.dumps(value))
        for key, value in row["inputs"].items()
    }
    assert summary.pop("inputs_read_with").startswith('read_graph target="automation"')
    assert summary == {k: v for k, v in row.items() if k != "inputs"}
    assert listed["complete"] is True and listed["total"] == 1
    assert read(row)["automation"] == row
    seen = {"create"}
    for op, state in [("pause", "paused"), ("resume", "active"), ("delete", "paused")]:
        updated = control(row, op)
        assert (
            updated["status"]
            == f"automation_{ {'pause': 'paused', 'resume': 'resumed', 'delete': 'deleted'}[op] }"
        )
        assert updated["automation"]["revision"] == row["revision"] + 1
        row = updated["automation"]
        assert row["desired_state"] == state
        assert read(row)["automation"] == row
        seen.add(op)
    assert row["retired_at"]
    assert json.loads(engine.read_graph(target="automations"))["count"] == 0
    assert seen == documented
    assert current_identity_or_none() is None


def test_retiring_removes_only_that_automation_dependency(bound):
    from tinyassets.api.branches import _branch_dependents

    row = create()["automation"]
    other = create()["automation"]
    before = _branch_dependents(bound, branch_def_id=BRANCH, actor=OWNER)
    assert set(before["automations"]) == {row["automation_id"], other["automation_id"]}
    control(row, "delete")
    after = _branch_dependents(bound, branch_def_id=BRANCH, actor=OWNER)
    assert after["automations"] == [other["automation_id"]]


def test_stale_revision_does_not_overwrite_new_state(bound):
    row = create()["automation"]
    paused = control(row, "pause")["automation"]
    refused = control(row, "delete")
    assert refused["error"] == "automation_revision_conflict"
    assert read(row)["automation"] == paused


@pytest.mark.parametrize("op", ["pause", "delete"])
def test_stopping_does_not_require_execution_admission_or_provider(bound, monkeypatch, op):
    row = create()["automation"]
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "0")
    monkeypatch.setattr(
        "tinyassets.provider_assignment.load_provider_assignment", lambda *a, **k: None
    )
    monkeypatch.setattr(
        engine, "_engine_run_admit", lambda **k: pytest.fail("stop spent admission")
    )
    assert "error" not in control(row, op)


@pytest.mark.parametrize("actor", ["stranger", "writer"])
def test_current_acl_and_owner_checks_are_real(bound, monkeypatch, actor):
    from tinyassets.daemon_server import grant_universe_access

    row = create()["automation"]
    if actor == "writer":
        grant_universe_access(
            bound, universe_id=UNIVERSE, actor_id=actor, permission="write", granted_by=OWNER
        )
    monkeypatch.setattr(engine, "_ACTOR_ID", actor)
    refused = control(row, "delete")
    assert "error" in refused
    assert not AutomationStore(bound).get(row["automation_id"]).retired_at


def test_admin_can_control_another_owners_row_but_text_is_untrusted(bound, monkeypatch):
    from tinyassets.daemon_server import grant_universe_access

    row = create()["automation"]
    grant_universe_access(
        bound, universe_id=UNIVERSE, actor_id="admin", permission="admin", granted_by=OWNER
    )
    monkeypatch.setattr(engine, "_ACTOR_ID", "admin")
    # This admin becomes the legitimate serving creator, not an impersonator.
    import sqlite3

    from tinyassets.storage import DB_FILENAME
    with sqlite3.connect(bound / DB_FILENAME) as conn:
        conn.execute("UPDATE agent_bindings SET created_by = 'admin' WHERE universe_id = ?",
                     (UNIVERSE,))
    assert read(row)["untrusted"] is True
    listed = json.loads(engine.read_graph(target="automations"))
    assert listed["untrusted"] is True
    assert listed["content"]["automations"][0]["owner"] == {"is_you": False}
    assert listed["own"]["automations"] == []
    assert control(row, "pause")["content"]["status"] == "automation_paused"


def test_foreign_universe_selector_is_uniform_miss_even_for_same_owner(bound):
    from tinyassets.daemon_server import grant_universe_access

    row = create()["automation"]
    store = AutomationStore(bound)
    foreign = replace(
        store.get(row["automation_id"]), automation_id="foreign-row", universe_id="other-universe"
    )
    store.insert(foreign)
    grant_universe_access(
        bound, universe_id="other-universe", actor_id=OWNER, permission="admin", granted_by=OWNER
    )
    for automation_id in [foreign.automation_id, "missing-row"]:
        selected = {"automation_id": automation_id, "revision": 1}
        assert read(selected) == {"error": "not_found", "resource": "automation"}
        assert control(selected, "delete") == read(selected)
    assert store.get(foreign.automation_id) == foreign


@pytest.mark.parametrize("field", ["_GRAPH_ID", "_ACTOR_ID"])
def test_unbound_identity_never_reaches_control(bound, monkeypatch, field):
    row = create()["automation"]
    monkeypatch.setattr(engine, field, "")
    assert "not bound" in read(row)["error"]
    assert "not bound" in control(row, "delete")["error"]


def test_disabled_engine_cannot_mutate(bound, monkeypatch):
    row = create()["automation"]
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "0")
    assert "current serving owner" in control(row, "delete")["error"]
    assert not AutomationStore(bound).get(row["automation_id"]).retired_at


@pytest.mark.parametrize("payload", ["[]", "null", "broken", '{"name": true}'])
def test_bad_payloads_do_not_create_rows(bound, payload):
    assert "error" in json.loads(
        engine.write_graph(
            target="automation",
            operation="create",
            payload_json=payload,
        )
    )
    assert AutomationStore(bound).list(universe_id=UNIVERSE) == []


def test_unknown_operation_and_control_payload_do_not_fall_through(bound):
    row = create()["automation"]
    assert control(row, "rebind")["error"] == "unknown_automation_action"
    assert "error" in control(row, "delete", payload_json='{"universe_id":"other"}')
    assert not AutomationStore(bound).get(row["automation_id"]).retired_at


def test_ta_check_my_board_each_morning_and_stop_when_asked(bound, monkeypatch):
    from tests.ta_task_helpers import call
    from tests.test_ta_capabilities import signed_launch

    signed_launch(monkeypatch)
    created = call(engine, "write_graph", target="automation", operation="create",
                   payload_json=CREATE_PAYLOAD)
    assert created["status"] == "automation_created", created
    row = created["automation"]
    read_back = call(engine, "read_graph", target="automation", automation_id=row["automation_id"])
    assert read_back["automation"] == row
    paused = call(engine, "write_graph", target="automation", operation="pause",
                  automation_id=row["automation_id"], expected_revision=row["revision"])
    assert paused["automation"]["revision"] == row["revision"] + 1
    assert paused["automation"]["desired_state"] == "paused"
    assert AutomationStore(bound).get(row["automation_id"]).desired_state == "paused"
