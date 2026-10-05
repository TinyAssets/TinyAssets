"""Offline HTTPS fixture for the production-image broker streaming oracle.

Only the oracle imports this module. The separate fixture container publishes
its public certificate, never its key. No transport/credential test hooks are
enabled in the launcher or broker.
"""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import subprocess
import sys
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
        def log_message(self, *_args):
            pass  # never log headers or fixture credentials

        def do_GET(self):
            if (self.path != "/catalogue"
                    or self.headers.get("Authorization") != f"Bearer {TOKEN}"):
                self.send_error(403)
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
        scopes=("GET",), provider="http", destination="compute:stream-fixture",
        credential_ref="vault://http/fixture", allowed_endpoints=[{
            "host": HOST, "path_template": "/catalogue", "methods": ["GET"],
        }],
    )
    ledger.grant_connection(grant_id="stream-grant", connection_id="stream-connection",
                            owner_user_id="stream-owner", universe_id="stream-owner")
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


if __name__ == "__main__":
    if sys.argv[1:] != ["fixture"]:
        raise SystemExit("only the isolated oracle fixture entrypoint is supported")
    fixture()
