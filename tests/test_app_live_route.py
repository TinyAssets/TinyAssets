"""The app live view requires write access and exposes only picked fields."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from tests.test_live_view import _seed_runs
from tests.test_turn_interrupt import _Request
from tinyassets import agent_activities as acts
from tinyassets import live_view, onboarding
from tinyassets.api import helpers, permissions
from tinyassets.auth import middleware


@pytest.fixture
def live(monkeypatch, tmp_path):
    from tinyassets.daemon_server import initialize_author_server

    initialize_author_server(tmp_path)
    universe = tmp_path / "u-alpha"
    universe.mkdir()
    record = acts.create(universe, owner_principal="owner-secret", title="map",
                         brief="private instructions", origin_kind="ask", agent_id="agent-1")
    activity_id = record["activity_id"]
    generation = acts.claim(universe, activity_id, replaceable=lambda r: False)
    acts.bind_run(universe, activity_id, generation, "private-run-token")
    _seed_runs(tmp_path, [("r1", "branch-map", "running", "universe:u-alpha", 100, None)])
    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    caller = SimpleNamespace(user_id="owner-secret")
    monkeypatch.setattr(middleware, "current_identity", lambda: caller)

    def access(universe_id, *, write=False):
        assert universe_id == "u-alpha" and write is True
        return caller.user_id == "owner-secret"

    def home(identity, *, raise_errors=False):
        assert identity is caller and raise_errors is True
        return "u-alpha"

    monkeypatch.setattr(permissions, "universe_access_allows", access)
    monkeypatch.setattr(onboarding, "_read_home", home)
    return SimpleNamespace(activity_id=activity_id, caller=caller)


def _post(body):
    response = asyncio.run(onboarding._handle_live(_Request(body)))
    assert response.headers["cache-control"] == "no-store"
    return response.status_code, json.loads(response.body)


@pytest.mark.parametrize("body", [{"universe_id": "u-alpha"}, {}])
def test_owner_gets_projects_activities_and_agent_states(live, body):
    before = time.time()
    status, result = _post(body)
    assert status == 200
    assert set(result) == {"universe_id", "as_of", "projects", "activities", "agent_states"}
    assert result["universe_id"] == "u-alpha"
    assert isinstance(result["as_of"], float) and before <= result["as_of"] <= time.time()
    assert result["projects"] == [{
        "project_id": "branch-map", "name": "Workflow", "runs": 1, "completed": 0,
        "failed": 0, "running": 1, "last_activity_at": 100, "state": "running",
    }]
    assert len(result["activities"]) == 1
    activity = result["activities"][0]
    assert activity["activity_id"] == live.activity_id
    assert activity["title"] == "map" and activity["status"] == acts.IN_PROGRESS
    assert result["agent_states"] == {"agent-1": "working"}


def test_caller_without_write_access_gets_no_data(live, monkeypatch):
    live.caller.user_id = "intruder"

    def unread(*args, **kwargs):
        pytest.fail("live data was read without write access")

    monkeypatch.setattr(live_view, "projects", unread)
    monkeypatch.setattr(live_view, "activity_rows", unread)
    assert _post({"universe_id": "u-alpha", "owner_principal": "owner-secret"}) == (
        404, {"error": "not_found"})


def test_live_body_never_contains_private_fields(live):
    status, result = _post({"universe_id": "u-alpha"})
    assert status == 200
    body = json.dumps(result)
    for private in ("owner_principal", "brief", "runner_token", "owner-secret",
                    "private instructions", "private-run-token"):
        assert private not in body
