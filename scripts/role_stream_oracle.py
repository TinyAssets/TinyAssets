"""Offline HTTPS fixture for the production-image broker streaming oracle.

Only the oracle imports this module. The separate fixture container publishes
its public certificate, never its key. No transport/credential test hooks are
enabled in the launcher or broker.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "uid-stream.invalid"
ADDRESS = "93.184.216.2"
TOKEN = "synthetic-uid-oracle-token-not-a-user-credential"
BODY = b'{"data":["launcher-broker-stream"]}'


def fixture():
    key, cert = "/tmp/fixture.key", "/tmp/fixture.crt"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-keyout", key, "-out", cert, "-subj", f"/CN={HOST}",
        "-addext", f"subjectAltName=DNS:{HOST}",
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    class Handler(BaseHTTPRequestHandler):
        posts = 0
        counter_lock = threading.Lock()

        def log_message(self, *_args):
            pass  # never log headers or fixture credentials

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size < 4096:
                self.send_error(400)
                return
            request = json.loads(self.rfile.read(size))
            if (self.path != "/inference" or request.get("model") != "oracle-model"
                    or self.headers.get("Authorization") != f"Bearer {TOKEN}"):
                self.send_error(403)
                return
            payload = json.dumps({
                "choices": [{"message": {"content": "accounted answer"}}]}).encode()
            with Handler.counter_lock:
                Handler.posts += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if (self.path not in {"/catalogue", "/counts"}
                    or self.headers.get("Authorization") != f"Bearer {TOKEN}"):
                self.send_error(403)
                return
            if self.path == "/counts":
                with Handler.counter_lock:
                    payload = json.dumps({"posts": Handler.posts}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            # Exercise an actual streaming HTTP body, not a scripted dispatcher.
            for part in (BODY[:9], BODY[9:]):
                self.wfile.write(part)
                self.wfile.flush()
                time.sleep(0.05)

    server = ThreadingHTTPServer(("0.0.0.0", 443), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    # Publish only after listen/TLS setup succeeds. Atomic readiness marker.
    public = Path("/fixture/ca.crt")
    temporary = public.with_suffix(".tmp")
    temporary.write_bytes(Path(cert).read_bytes())
    temporary.chmod(0o444)
    temporary.replace(public)
    server.serve_forever()


def install_fixture_trust():
    """Ephemeral container trust only; the built production image is unchanged."""
    source = Path("/fixture/ca.crt")
    destination = Path("/usr/local/share/ca-certificates/uid-stream-oracle.crt")
    destination.write_bytes(source.read_bytes())
    destination.chmod(0o444)
    subprocess.run(["update-ca-certificates"], check=True, stdout=subprocess.DEVNULL)


def seed(root):
    from tinyassets.credential_vault import http_credential_record
    from tinyassets.storage.outbound_connections import ConnectionLedger

    universe = root / "stream-owner"
    universe.mkdir()
    os.chown(universe, 1001, 1102)
    universe.chmod(0o750)
    vault = universe / ".credential-vault.json"
    vault.write_text(json.dumps([http_credential_record(destination="fixture", token=TOKEN)]))
    os.chown(vault, 1001, 1102)
    vault.chmod(0o640)
    ledger = ConnectionLedger(root / ".broker/outbound.db", data_root=root)
    ledger.create_connection(
        connection_id="stream-connection", owner_user_id="stream-owner",
        connection_class="http", connection_type="http", auth_scheme="bearer",
        scopes=("GET", "POST"), provider="http", destination="compute:stream-fixture",
        credential_ref="vault://http/fixture", allowed_endpoints=[{
            "host": HOST, "path_template": "/catalogue", "methods": ["GET"],
        }, {"host": HOST, "path_template": "/inference", "methods": ["POST"]},
            {"host": HOST, "path_template": "/counts", "methods": ["GET"]}],
    )
    ledger.grant_connection(grant_id="stream-grant", connection_id="stream-connection",
                            owner_user_id="stream-owner", universe_id="stream-owner")
    ledger.configure_capability(
        connection_id="stream-connection", capability_kind="model_use", enabled=True,
        descriptor={"wire": "chat_messages", "models": [{"id": "oracle-model", "tools": True,
                                                        "context": 20000}], "billing": "free"})
    for path in (root / ".broker").glob("outbound.db*"):
        os.chown(path, 1002, 1101)
        path.chmod(0o600)


def probe(root):
    from tinyassets.providers.discovery_http import read_granted_discovery_document

    def daemon_store_snapshot():
        return {path.name: (path.stat().st_uid, path.stat().st_gid,
                            path.stat().st_mode, hashlib.sha256(path.read_bytes()).hexdigest())
                for path in root.glob(".tinyassets.db*")}

    before = daemon_store_snapshot()
    document = read_granted_discovery_document(
        db_path=root / "outbound.db", grant_id="stream-grant",
        owner_user_id="stream-owner", universe_id="stream-owner",
        url=f"https://{HOST}/catalogue",
    )
    assert document == json.loads(BODY)
    assert not (root / "outbound.db").exists()
    assert daemon_store_snapshot() == before, "GET changed daemon accounting state"
    print("D22 actual launcher broker HTTPS stream: scoped discovery GET, vault bearer, "
          "verified TLS, real network/body, no daemon ledger: PASS", flush=True)
    from tinyassets.effectors.authenticated_external_call import _open_connection_proxy

    proxy = _open_connection_proxy(
        db_path=root / "outbound.db", grant_id="stream-grant",
        owner_user_id="stream-owner", universe_id="stream-owner",
        connection_id="stream-connection",
    )
    try:
        response = proxy.request("GET", {"url": f"https://{HOST}/catalogue"})
    finally:
        proxy.close()
    assert response["status"] == 200
    assert json.loads(response["body"]) == json.loads(BODY)
    assert not (root / "outbound.db").exists()
    assert daemon_store_snapshot() == before, "effector GET changed daemon accounting state"
    print("D24 actual effector proxy HTTPS through launcher broker: "
          "vault bearer, verified TLS, real body, no daemon ledger: PASS", flush=True)
    inference_probe(root)


def inference_probe(root):
    from dataclasses import replace

    from tinyassets.broker.client import BrokerRefused
    from tinyassets.broker.ops import new_op_id
    from tinyassets.effectors.authenticated_external_call import _open_connection_proxy
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.definition import register_definition
    from tinyassets.request_budget import TurnRequestBudget, requests_today

    fields = dict(universe_id="stream-owner", owner_user_id="stream-owner",
                  access_method="api_key_http", protocol="chat_messages", ref="stream-grant")
    definition = register_definition(**fields, model="oracle-model").id
    # Force a new atomic replacement on both passes, not only first creation.
    register_definition(**fields, model="replacement-" + new_op_id())
    metadata = root / "stream-owner/provider_definitions.json"
    info = metadata.stat()
    assert (info.st_uid, info.st_gid, info.st_mode & 0o7777) == (1001, 1102, 0o640)
    initial = requests_today(root, "stream-owner", "api_key_http:" + definition,
                             reset_timezone="UTC")
    assert initial is not None
    budget = TurnRequestBudget("stream-owner", "stream-owner", max_requests=2)
    budget.persist(root)
    proxy = _open_connection_proxy(db_path=root / "outbound.db", grant_id="stream-grant",
                                   owner_user_id="stream-owner", universe_id="stream-owner",
                                   connection_id="stream-connection")
    request = {"url": f"https://{HOST}/inference", "body": {"model": "oracle-model"}}

    def count():
        response = proxy.request("GET", {"url": f"https://{HOST}/counts"})
        assert response["status"] == 200
        return json.loads(response["body"])["posts"]

    before = count()
    try:
        for missing in (True, False):
            ordinal = budget.reserve(owner="stream-owner", universe="stream-owner",
                                      source_ref="api_key_http:" + definition,
                                      model="oracle-model", free=True)
            ref = budget.issue_reference(ordinal, grant_id="stream-grant",
                                          connection_id="stream-connection", verb="POST",
                                          request=request, operation_id=new_op_id())
            if missing:
                try:
                    proxy.request("POST", request)
                except ProviderAuthorityHeldError as exc:
                    assert str(exc) == "inference usage authority refused"
                else:
                    raise AssertionError("inference POST without accounting was sent")
                assert count() == before
            response = proxy.request("POST", request, inference_usage=ref)
            assert response["status"] == 200
            assert json.loads(response["body"])["choices"][0]["message"]["content"] == (
                "accounted answer")
            assert budget.settle_invocation(ordinal, "succeeded") == 1
            try:
                proxy.request("POST", request, inference_usage=ref)
            except BrokerRefused as exc:
                assert "duplicate" in str(exc)
            else:
                raise AssertionError("duplicate accounted inference was sent twice")
            try:
                proxy.request("POST", request, inference_usage=replace(
                    ref, operation_id=new_op_id()))
            except ProviderAuthorityHeldError as exc:
                assert str(exc) == "inference usage authority refused"
            else:
                raise AssertionError("fresh operation reused a bound accounting reference")
            assert count() == before + ordinal
        receipt = budget.receipt()
        assert receipt["dispatched"] == 2
        assert all(a["state"] == "succeeded" for a in receipt["attempts"])
        assert count() == before + 2
        daily = requests_today(root, "stream-owner", "api_key_http:" + definition,
                               reset_timezone="UTC")
        assert daily == (initial[0] + 2, initial[1] + 2)
        assert requests_today(root, "foreign", "api_key_http:" + definition,
                              reset_timezone="UTC") == (0, 0)
        daemon_db = root / ".tinyassets.db"
        if daemon_db.exists():
            from contextlib import closing

            with closing(sqlite3.connect(daemon_db.as_uri() + "?mode=ro", uri=True)) as conn:
                assert not conn.execute("SELECT name FROM sqlite_master WHERE name IN "
                                        "('agent_request_usage','agent_request_attempts',"
                                        "'agent_request_usage_links','agent_request_dispatches')").fetchall()
        assert not (root / "outbound.db").exists()
        print("D46 actual accounted HTTPS inference POST via launcher broker: kernel leases, "
              "source binding, one-use claims, dispatch/settlement receipts, missing/replay "
              "refusal: PASS (runtime metadata modes)", flush=True)
        print("D48 actual daemon definition registration/replacement retains broker read mode "
              "before atomic publish; broker-local source binding succeeds: PASS", flush=True)
        print("D47 actual daily evidence via launcher broker: counted HTTPS attempts across "
              "restart, foreign history absent, daemon tables untouched: PASS", flush=True)
    finally:
        budget.close()
        proxy.close()


if __name__ == "__main__":
    if sys.argv[1:] != ["fixture"]:
        raise SystemExit("only the isolated oracle fixture entrypoint is supported")
    fixture()
