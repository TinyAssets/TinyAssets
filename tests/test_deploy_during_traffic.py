"""Linux RED control for the wait/recreate listener gap and cut-off reply.

The edge's 520 mapping is simulated explicitly; the upstream disconnect,
SIGTERM, MCP transport, browser request and missing terminal are real. This
component oracle is not Compose/cloudflared or the full design's release gate.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx


def sync_playwright():
    """Playwright imported on use: the slow-tests job has no Playwright, and a
    module-level import fails collection there (as in the other browser suites)."""
    from playwright.sync_api import sync_playwright as _sync_playwright

    return _sync_playwright()

ROOT = Path(__file__).resolve().parents[1]


def eventually(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("fixture barrier timed out")


class Origin:
    def __init__(self, root, *, script="tests/fixtures/deploy_traffic_process.py",
                 ready_status=406, log_name="origin.log"):
        self.root = root
        self.ready_status = ready_status
        self.log_path = root / log_name
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.port = self.listener.getsockname()[1]
        self.log = self.log_path.open("w")
        self.process = subprocess.Popen(
            [sys.executable, script, str(root),
             str(self.listener.fileno())],
            cwd=ROOT, pass_fds=(self.listener.fileno(),), stdout=self.log,
            stderr=subprocess.STDOUT, env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        self.listener.close()
        eventually(self.ready)

    def ready(self):
        assert self.process.poll() is None, self.log_path.read_text()
        try:
            return (httpx.get(f"http://127.0.0.1:{self.port}/mcp", timeout=1).status_code
                    == self.ready_status)
        except httpx.TransportError:
            return False

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=10)
        self.log.close()


def edge(origin_port, traces, acceptance=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            # Test page deliberately does not pretend to be the production app.
            body = b'<textarea id="draft"></textarea><output id="status"></output>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if acceptance is not None:
                from tests.fixtures.deploy_traffic_acceptance import SCOPE

                accepted = acceptance.accept(SCOPE, json.loads(body)["client_send_id"], body)
                output = json.dumps({"ingress_id": accepted.ingress_id,
                                     "client_send_id": accepted.client_send_id}).encode()
                traces.append({"status": 202, "body": output.decode(), "request": body.decode()})
                self.send_response(202)
                self.end_headers()
                self.wfile.write(output)
                return
            conn = http.client.HTTPConnection("127.0.0.1", origin_port, timeout=2)
            try:
                conn.request("POST", "/mcp", body, {"Content-Type": "application/json"})
                response = conn.getresponse()
                status, output = response.status, response.read()
            except (OSError, http.client.HTTPException) as exc:
                # The public incident's status at the fixture edge. We prove
                # connection refusal, not Cloudflare's proprietary status rule.
                status, output = 520, type(exc).__name__.encode()
            finally:
                conn.close()
            traces.append({"status": status, "body": output.decode(), "request": body.decode()})
            self.send_response(status)
            self.end_headers()
            self.wfile.write(output)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def exercise_red(root, *, durable=False):
    acceptance = None
    if durable:
        from tests.fixtures.deploy_traffic_acceptance import journal
        from tinyassets.storage.ingress_journal import initialize

        initialize(root / "ingress")
        acceptance = journal(root)
    origin = Origin(root)
    traces = []
    proxy, thread = edge(origin.port, traces, acceptance)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{origin.port}", timeout=20,
                          headers={"Accept": "application/json, text/event-stream"}) as client:
            response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                "method": "initialize", "params": {"protocolVersion": "2025-03-26",
                "capabilities": {}, "clientInfo": {"name": "deploy-oracle", "version": "1"}}})
            response.raise_for_status()
            client.headers["mcp-session-id"] = response.headers["mcp-session-id"]
            client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            assert not (root / "started").exists()  # final idle poll

            def call():
                try:
                    result = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2,
                        "method": "tools/call", "params": {"name": "converse",
                        "arguments": {"client_send_id": "long-send", "message": "exact input"}}})
                    return {"status": result.status_code, "body": result.text}
                except httpx.TransportError as exc:
                    return {"error": type(exc).__name__}

            with ThreadPoolExecutor(max_workers=1) as pool, sync_playwright() as pw:
                browser = pw.chromium.launch(chromium_sandbox=True)
                try:
                    page = browser.new_page()
                    page.goto(f"http://127.0.0.1:{proxy.server_port}/app")
                    page.locator("#draft").fill("private unsent draft")
                    future = pool.submit(call)
                    eventually(lambda: (root / "started").exists())
                    origin.process.send_signal(signal.SIGTERM)
                    # Wait for the real listener to close, not an arbitrary sleep.
                    def closed():
                        with socket.socket() as probe:
                            return probe.connect_ex(("127.0.0.1", origin.port)) != 0
                    eventually(closed)
                    status = page.evaluate("""async () => {
                        const r = await fetch('/send', {method:'POST', body:JSON.stringify({
                            client_send_id:'cc2e9e60-89dd-43a8-9bea-f87c333b21e4',
                            message:'send during cutover'})});
                        document.querySelector('#status').textContent = r.status;
                        return r.status;
                    }""")
                    turn = future.result(timeout=15)
                    page.screenshot(path=str(root / "browser.png"))
                    draft = page.locator("#draft").input_value()
                    assert status == (202 if durable else 520)
                    assert "TURN_FINISHED" not in turn.get("body", "")
                    assert not (root / "completed").exists()
                    assert (root / "effect-long-send").read_text() == "exact input"
                    assert (root / "workspace.txt").read_text() == "exact input"
                    assert draft == "private unsent draft"
                finally:
                    browser.close()
        evidence = {"continuity": "RED", "edge_mapping": "fixture 520 on upstream failure",
                    "http": traces, "turn": turn, "terminal": False, "effect_count": 1,
                    "draft": draft, "source_digests": {
                        p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
                        for p in ("deploy/wait_for_turns.sh", "deploy/compose.yml",
                                  "tinyassets/universe_server.py",
                                  "tests/fixtures/deploy_traffic_process.py")}}
        (root / "evidence.json").write_text(json.dumps(evidence, indent=2))
        return evidence
    finally:
        origin.close()
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=5)


def test_wait_recreate_red_control(tmp_path):
    assert sys.platform == "linux", "run with scripts/linux_oracle.py; no skipped evidence"
    evidence = exercise_red(tmp_path)
    assert evidence["continuity"] == "RED"
    assert evidence["http"][0]["status"] == 520
    assert evidence["effect_count"] == 1
    output = os.environ.get("DEPLOY_TRAFFIC_EVIDENCE")
    if output:
        import shutil

        shutil.copytree(tmp_path, Path(output) / "red", dirs_exist_ok=True)


def test_durable_cutover_send_replays_after_process_replacement(tmp_path):
    from tests.fixtures.deploy_traffic_acceptance import SCOPE, SEND_ID, journal

    assert sys.platform == "linux", "run with scripts/linux_oracle.py; no skipped evidence"
    evidence = exercise_red(tmp_path, durable=True)
    assert evidence["http"][0]["status"] == 202
    # Reopen the independent journal and replay in a new Linux process. The
    # original serving process is dead; RAM cannot supply this acknowledgement.
    accepted = journal(tmp_path).receipt(SCOPE, SEND_ID)
    assert accepted.state == "pending"
    assert accepted.payload == evidence["http"][0]["request"].encode()
    command = [sys.executable, "tests/fixtures/deploy_traffic_acceptance.py", str(tmp_path)]
    results = [subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60,
                              env={**os.environ, "PYTHONPATH": str(ROOT)}, check=True)
               for _ in range(2)]
    receipts = [json.loads(r.stdout) for r in results]
    assert receipts[0] == receipts[1]
    assert receipts[0]["admissions"] == 1
    assert receipts[0]["effects"] == 1
    assert journal(tmp_path).events(SCOPE, SEND_ID) == [
        {"sequence": 1, "payload": b"CUTOVER_SEND_FINISHED", "terminal": 1}]
    assert journal(tmp_path).receipt(SCOPE, SEND_ID).state == "terminal"
    evidence.update({"acceptance_import": "GREEN", "execution": "transactional fixture sink",
                     "long_turn": "RED", "replay": receipts})
    (tmp_path / "evidence.json").write_text(json.dumps(evidence, indent=2))
    output = os.environ.get("DEPLOY_TRAFFIC_EVIDENCE")
    if output:
        import shutil

        shutil.copytree(tmp_path, Path(output) / "acceptance", dirs_exist_ok=True)


def test_authenticated_ingress_accepts_while_execution_listener_is_closed(tmp_path):
    from tests.fixtures.deploy_ingress_process import provision
    from tests.fixtures.deploy_traffic_acceptance import SCOPE, SEND_ID, journal
    from tests.test_ingress_http import headers, wire

    assert sys.platform == "linux", "run with scripts/linux_oracle.py; no skipped evidence"
    provision(tmp_path)
    origin = Origin(tmp_path)
    frontend_options = {"script": "tests/fixtures/deploy_ingress_process.py",
                        "ready_status": 401, "log_name": "frontend.log"}
    frontend = Origin(tmp_path, **frontend_options)
    body = wire(message="send during cutover")
    try:
        origin.process.send_signal(signal.SIGTERM)

        def closed():
            with socket.socket() as probe:
                return probe.connect_ex(("127.0.0.1", origin.port)) != 0

        eventually(closed)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(chromium_sandbox=True)
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{frontend.port}/fixture-browser")
                page.locator("#draft").fill("private unsent draft")
                accepted = page.evaluate("""async ({body, headers}) => {
                    const r = await fetch('/mcp', {method:'POST', headers, body});
                    document.querySelector('#status').textContent = r.status;
                    return {status:r.status, body:await r.json()};
                }""", {"body": body.decode(), "headers": headers()})
                assert accepted["status"] == 202
                assert accepted["body"]["state"] == "pending"
                assert page.locator("#draft").input_value() == "private unsent draft"
                page.screenshot(path=str(tmp_path / "ingress-browser.png"))
            finally:
                browser.close()
        # No executor exists when the acknowledgement is returned. Crash the
        # frontend too: the next process must recover entirely from storage.
        assert closed()
        assert journal(tmp_path).receipt(SCOPE, SEND_ID).payload == body
        frontend.close()
        frontend = Origin(tmp_path, **frontend_options)
        receipt_request = json.loads(body)
        receipt_request["params"]["arguments"] = {
            "graph_id": SCOPE.command_center_id, "client_send_id": SEND_ID,
        }
        receipt = httpx.post(  # hermetic-ok: isolated loopback frontend subprocess
            f"http://127.0.0.1:{frontend.port}/mcp", json=receipt_request,
            headers=headers("receipt-v1"))
        assert receipt.status_code == 200
        assert receipt.json()["ingress_id"] == accepted["body"]["ingress_id"]
        retry = httpx.post(  # hermetic-ok: isolated loopback frontend subprocess
            f"http://127.0.0.1:{frontend.port}/mcp", content=body, headers=headers())
        assert retry.status_code == 202
        assert retry.json() == accepted["body"]
        command = [sys.executable, "tests/fixtures/deploy_traffic_acceptance.py", str(tmp_path)]
        results = [subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60,
                                  env={**os.environ, "PYTHONPATH": str(ROOT)}, check=True)
                   for _ in range(2)]
        receipts = [json.loads(result.stdout) for result in results]
        assert receipts[0] == receipts[1]
        assert receipts[0]["admissions"] == 1
        assert receipts[0]["effects"] == 1
        assert journal(tmp_path).receipt(SCOPE, SEND_ID).state == "terminal"
        evidence = {"ingress_520_case": "GREEN", "acceptance": accepted,
                    "receipt_after_frontend_crash": receipt.json(), "replay": receipts,
                    "path": "production create_streamable_http_app /mcp",
                    "execution": "transactional fixture sink", "long_turn": "RED",
                    "cloud_and_token_issuer": "isolated fixture observations",
                    "source_digests": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
                        for p in ("tinyassets/ingress.py", "tinyassets/universe_server.py",
                                  "tests/fixtures/deploy_ingress_process.py")}}
        (tmp_path / "ingress-evidence.json").write_text(json.dumps(evidence, indent=2))
        output = os.environ.get("DEPLOY_TRAFFIC_EVIDENCE")
        if output:
            import shutil

            shutil.copytree(tmp_path, Path(output) / "authenticated-ingress", dirs_exist_ok=True)
    finally:
        frontend.close()
        origin.close()
