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
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def eventually(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("fixture barrier timed out")


class Origin:
    def __init__(self, root):
        self.root = root
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.port = self.listener.getsockname()[1]
        self.log = (root / "origin.log").open("w")
        self.process = subprocess.Popen(
            [sys.executable, "tests/fixtures/deploy_traffic_process.py", str(root),
             str(self.listener.fileno())],
            cwd=ROOT, pass_fds=(self.listener.fileno(),), stdout=self.log,
            stderr=subprocess.STDOUT, env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        self.listener.close()
        eventually(self.ready)

    def ready(self):
        assert self.process.poll() is None, (self.root / "origin.log").read_text()
        try:
            return httpx.get(f"http://127.0.0.1:{self.port}/mcp", timeout=1).status_code == 406
        except httpx.TransportError:
            return False

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=10)
        self.log.close()


def edge(origin_port, traces):
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


def exercise_red(root):
    origin = Origin(root)
    traces = []
    proxy, thread = edge(origin.port, traces)
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
                            client_send_id:'cutover-send', message:'send during cutover'})});
                        document.querySelector('#status').textContent = r.status;
                        return r.status;
                    }""")
                    turn = future.result(timeout=15)
                    page.screenshot(path=str(root / "browser.png"))
                    draft = page.locator("#draft").input_value()
                    assert status == 520
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
