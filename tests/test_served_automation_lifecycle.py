"""Existing owner controls must be reachable without rewriting private workflows."""

from __future__ import annotations

import json

import pytest

from tests.test_automations import OWNER, UNIVERSE, _seed_branch, _seed_owner
from tests.test_automations_api import CREATE_PAYLOAD
from tests.test_background_budget_finalization_e2e import _seed_serving_assignment
from tinyassets import engine_mcp_server as engine


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
























