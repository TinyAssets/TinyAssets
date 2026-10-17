"""C16: the app agent can give its owner an inbound webhook that runs as the owner.

Live evidence (2026-09-24, read-only production): the scheduled half of C16
already works hostless -- 612 completed automation runs on the founder's
universe, all as ``universe:<id>`` on the owner's own provider. The inbound
half had never been used: zero hooks minted, because the only route to mint
one was the claude.ai connector's ``run_graph webhook_op``. The served agent
in the app had no way to do it.

These tests drive the served ``write_graph``/``read_graph`` handles the app
agent actually calls, against the real hook store, the real owner gate and the
real receiver. The one substituted seam is the final enqueue, which records
who the run would act for instead of starting a provider call.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from tests.test_automations import BRANCH, OWNER, UNIVERSE, _seed_branch, _seed_owner
from tests.test_background_budget_finalization_e2e import _seed_serving_assignment
from tinyassets import engine_mcp_server as engine
from tinyassets.storage import webhook_hooks


@pytest.fixture
def bound(tmp_path, monkeypatch):
    from tinyassets.auth.middleware import _current_identity

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "1")
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)
    webhook_hooks._initialized.clear()
    monkeypatch.setattr(engine, "_GRAPH_ID", UNIVERSE)
    monkeypatch.setattr(engine, "_ACTOR_ID", OWNER)
    from tests.engine_authority_helpers import seed_bound_engine

    seed_bound_engine(monkeypatch)
    token = _current_identity.set(None)
    yield tmp_path
    _current_identity.reset(token)


@pytest.fixture
def enqueued(monkeypatch):
    """Record each triggered run instead of starting it."""
    calls: list[dict] = []

    def _record(base_path, *, universe_id, branch_def_id, inputs, run_name="", principal_id=""):
        calls.append({
            "universe_id": universe_id,
            "branch_def_id": branch_def_id,
            "run_name": run_name,
            "principal_id": principal_id,
            "payload": inputs.get("webhook", {}).get("payload"),
        })
        return f"run-{len(calls)}"

    monkeypatch.setattr("tinyassets.api.runs.enqueue_universe_branch_run", _record)
    return calls


def create(branch_id: str = BRANCH) -> dict:
    return json.loads(engine.write_graph(
        target="webhook", operation="create", branch_id=branch_id,
    ))


def revoke(**selector) -> dict:
    return json.loads(engine.write_graph(
        target="webhook", operation="revoke", payload_json=json.dumps(selector),
    ))


def post(bound, token: str, body: dict) -> int:
    from tinyassets.webhook_inbound import handle_hook

    status, _ = handle_hook(
        token=token, body=json.dumps(body).encode(), headers={}, base_path=bound,
    )
    return status


def test_app_agent_creates_a_webhook_that_runs_the_branch_as_its_owner(bound, enqueued):
    created = create()
    assert created.get("error") is None, created
    token = created["token"]
    assert created["url"].endswith("/hooks/inbound/" + token)
    assert created["token_prefix"] == token[:12]
    assert created["branch_def_id"] == BRANCH

    # The stored hook belongs to THIS universe and THIS owner, taken from the
    # server's pins -- the agent never supplied either.
    binding = webhook_hooks.resolve(bound, token=token)
    assert binding["universe_id"] == UNIVERSE
    assert binding["owner_principal_id"] == OWNER

    assert post(bound, token, {"event": "push"}) == 202
    assert enqueued == [{
        "universe_id": UNIVERSE,
        "branch_def_id": BRANCH,
        "run_name": "webhook",
        "principal_id": OWNER,
        "payload": {"event": "push"},
    }]


def test_app_agent_lists_its_webhooks_without_the_secret(bound):
    token = create()["token"]
    listed = json.loads(engine.read_graph(target="webhooks"))
    assert listed["count"] == 1
    (row,) = listed["webhooks"]
    assert row["branch_def_id"] == BRANCH
    assert row["token_prefix"] == token[:12]
    assert token not in json.dumps(listed)


def test_revoke_by_the_listed_prefix_stops_deliveries(bound, enqueued):
    token = create()["token"]
    prefix = json.loads(engine.read_graph(target="webhooks"))["webhooks"][0]["token_prefix"]
    out = revoke(token_prefix=prefix)
    assert out["revoked"] is True, out
    assert post(bound, token, {"n": 1}) == 404
    assert enqueued == []
    assert json.loads(engine.read_graph(target="webhooks"))["count"] == 0


def test_revoke_by_the_full_token_also_works(bound):
    token = create()["token"]
    assert revoke(token=token)["revoked"] is True
    assert webhook_hooks.resolve(bound, token=token) is None


def test_revoke_cannot_touch_another_universes_hook(bound):
    other = webhook_hooks.mint(
        bound, universe_id="u-someone-else", branch_def_id="b-x",
        owner_principal_id="user-someone-else",
    )
    assert revoke(token_prefix=other[:12])["revoked"] is False
    assert revoke(token=other)["revoked"] is False
    assert webhook_hooks.resolve(bound, token=other) is not None


def test_create_refuses_a_branch_the_owner_did_not_author(bound):
    _seed_branch(bound, branch_def_id="b-foreign", author="user-someone-else")
    out = create("b-foreign")
    assert "not found" in out.get("error", ""), out
    assert webhook_hooks.list_for_universe(bound, universe_id=UNIVERSE) == []


@pytest.mark.parametrize(
    ("operation", "kwargs", "needle"),
    [
        ("mint", {"branch_id": BRANCH}, "unknown_webhook_action"),
        ("create", {}, "branch_id is required"),
        ("create", {"branch_id": BRANCH, "payload_json": "{\"universe_id\": \"x\"}"},
         "branch_id only"),
        ("revoke", {"payload_json": "{}"}, "token_prefix"),
        ("revoke", {"payload_json": "[1]"}, "JSON object"),
    ],
)
def test_malformed_requests_refuse_without_side_effects(bound, operation, kwargs, needle):
    out = json.loads(engine.write_graph(target="webhook", operation=operation, **kwargs))
    assert needle in json.dumps(out), out
    assert webhook_hooks.list_for_universe(bound, universe_id=UNIVERSE) == []


def test_documented_webhook_operations_are_the_dispatched_ones():
    from tinyassets.served_tools import SERVED_WEBHOOK_WRITE_OPERATIONS

    paragraph = engine.write_graph.__doc__.split("**Inbound webhooks:**", 1)[1].split(
        "\n\n", 1
    )[0]
    documented = set(re.findall(r'operation="([a-z_]+)"', paragraph))
    assert documented == SERVED_WEBHOOK_WRITE_OPERATIONS == {"create", "revoke"}
    assert "webhooks" in engine.read_graph.__doc__
    tools = {tool.name: tool for tool in asyncio.run(engine.mcp.list_tools())}
    properties = tools["write_graph"].parameters["properties"]
    assert not {"graph_id", "universe_id", "owner_principal_id"} & properties.keys()
