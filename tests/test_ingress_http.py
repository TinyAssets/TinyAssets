"""Opt-in acceptance through the production app's auth and HTTP assembly."""

import json
import sqlite3
from contextlib import contextmanager

import pytest
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from tests.fixtures.deploy_ingress_process import FixtureAuth, policy, provision
from tests.fixtures.deploy_traffic_acceptance import SCOPE, SEND_ID
from tinyassets.ingress import AppAcceptance
from tinyassets.storage import db_path


def wire(**updates):
    args = {"graph_id": SCOPE.command_center_id, "client_send_id": SEND_ID,
            "message": "  exact\nmessage 日本語  ", "input_method": "typed"}
    args.update(updates)
    return json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "converse", "arguments": args}},
                      ensure_ascii=False).encode()


def headers(mode="accept-v1", token="fixture-token"):
    return {"Authorization": f"Bearer {token}", "X-TinyAssets-Ingress": mode,
            "Content-Type": "application/json"}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from tinyassets import universe_server

    provision(tmp_path)
    monkeypatch.setattr("tinyassets.auth.middleware._provider", FixtureAuth())
    monkeypatch.setattr("tinyassets.origin_admission.cached_process_is_cloud_admitted",
                        lambda: True)
    adapter = AppAcceptance(tmp_path / "runtime", tmp_path / "ingress", admission_policy=policy)
    app = universe_server.create_streamable_http_app(ingress=adapter)
    # No lifespan: no execution owners, reconciliation or schedulers in ingress.
    return TestClient(app), adapter, tmp_path


def test_exact_durable_acceptance_and_receipt_after_reopen(rig):
    client, adapter, root = rig
    body = wire()
    accepted = client.post("/mcp", content=body, headers=headers())
    assert accepted.status_code == 202
    assert accepted.json()["state"] == "pending"
    again = client.post("/mcp", content=body, headers=headers())
    assert again.json() == accepted.json()
    receipt = json.loads(body)
    receipt["params"]["arguments"] = {"graph_id": SCOPE.command_center_id,
                                         "client_send_id": SEND_ID}
    found = client.post("/mcp", json=receipt, headers=headers("receipt-v1"))
    assert found.status_code == 200
    assert found.json()["ingress_id"] == accepted.json()["ingress_id"]
    assert found.headers["cache-control"] == "no-store"
    with sqlite3.connect(root / "ingress" / "ingress-v1.sqlite3") as conn:
        assert conn.execute("SELECT payload FROM requests").fetchone()[0] == body
        assert conn.execute("SELECT count(*) FROM requests").fetchone()[0] == 1
        assert b"fixture-token" not in conn.execute("SELECT payload FROM requests").fetchone()[0]


def test_conflict_and_authority_are_checked_on_replay(rig):
    client, adapter, root = rig
    assert client.post("/mcp", content=wire(), headers=headers()).status_code == 202
    conflict = client.post("/mcp", content=wire(message="changed"), headers=headers())
    assert conflict.status_code == 409
    for mode in ("accept-v1", "receipt-v1"):
        document = json.loads(wire())
        if mode == "receipt-v1":
            document["params"]["arguments"] = {"graph_id": SCOPE.command_center_id,
                                                 "client_send_id": SEND_ID}
        assert client.post("/mcp", json=document,
                           headers=headers(mode, "foreign-token")).status_code == 403
    with sqlite3.connect(db_path(root / "runtime")) as conn:
        conn.execute("DELETE FROM universe_acl WHERE actor_id=?", (SCOPE.principal_id,))
    assert client.post("/mcp", content=wire(), headers=headers()).status_code == 403
    receipt = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
        "name": "converse", "arguments": {"graph_id": SCOPE.command_center_id,
                                            "client_send_id": SEND_ID}}}
    assert client.post("/mcp", json=receipt, headers=headers("receipt-v1")).status_code == 403


@pytest.mark.parametrize("updates", [
    {"client_send_id": "not-a-uuid"}, {"message": ""}, {"agent_id": "other"},
    {"principal_id": "foreign-user"}, {"model_choice": {}}, {"graph_id": "../elsewhere"},
])
def test_unsupported_or_forged_arguments_never_enter_journal(rig, updates):
    client, adapter, root = rig
    assert client.post("/mcp", content=wire(**updates), headers=headers()).status_code in (400, 403)
    with sqlite3.connect(root / "ingress" / "ingress-v1.sqlite3") as conn:
        assert conn.execute("SELECT count(*) FROM requests").fetchone()[0] == 0


def test_auth_size_and_disk_errors_never_acknowledge(rig, monkeypatch):
    client, adapter, root = rig
    for auth in ({"X-TinyAssets-Ingress": "accept-v1"}, headers(token="bad")):
        refused = client.post("/mcp", content=wire(), headers=auth)
        # Existing MCP auth returns a linking tool error, never acceptance.
        assert refused.status_code == 200
        assert refused.json()["result"]["isError"] is True
        assert "mcp/www_authenticate" in refused.json()["result"]["_meta"]
        assert "ingress_id" not in refused.json()
    assert client.post("/mcp", content=b"x" * (1024 * 1024 + 1), headers=headers()
                       ).status_code == 413

    @contextmanager
    def failed_commit():
        with original() as conn:
            yield conn
            raise sqlite3.OperationalError("fixture commit failure")

    original = adapter.journal._connection
    monkeypatch.setattr(adapter.journal, "_connection", failed_commit)
    assert client.post("/mcp", content=wire(), headers=headers()).status_code == 503
    with sqlite3.connect(root / "ingress" / "ingress-v1.sqlite3") as conn:
        assert conn.execute("SELECT count(*) FROM requests").fetchone()[0] == 0


def test_disabled_adapter_and_legacy_passthrough(rig):
    from tinyassets import universe_server

    async def probe(request):
        return Response(status_code=204)

    app = universe_server.create_streamable_http_app()
    app.routes.insert(0, Route("/fixture-probe", probe))
    client = TestClient(app)
    assert client.post("/mcp", content=wire(), headers=headers()).status_code == 503
    assert client.get("/fixture-probe").status_code == 204
    assert client.post("/mcp", content=wire(), headers=headers("unknown")).status_code == 400


@pytest.mark.parametrize("body", [b"[]", b"null", b"{", b'{"id":1,"id":2}', b'NaN'])
def test_malformed_requests_are_not_accepted(rig, body):
    client, _, root = rig
    assert client.post("/mcp", content=body, headers=headers()).status_code == 400
    with sqlite3.connect(root / "ingress" / "ingress-v1.sqlite3") as conn:
        assert conn.execute("SELECT count(*) FROM requests").fetchone()[0] == 0
