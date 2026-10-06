"""Credential-free real CLI gate for the served Codex launch, on loopback only.

Runs the pinned CLI exactly as the codex adapter launches a served agent turn
(``tinyassets/providers/codex_launch_contract.py``: ``codex app-server`` with
every native tool off and the reduced model catalog), declares four fixture
tools as ``thread/start.dynamicTools``, and inspects the first model request
the CLI sends to a fake Responses endpoint, which then refuses auth. The
model must see exactly those four tools: no ``exec``/``apply_patch``, no
collaboration, no MCP resource tools, no shell. No real provider request,
user home, credential or MCP server is used.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

#: The tools the smoke declares; the model must see these and nothing else.
FIXTURE_TOOLS = ("read", "write", "edit", "bash")


def _contract():
    """The adapter's launch contract: from the package, or copied beside this file
    (the image build runs before the package is installed)."""
    here = Path(__file__).resolve().parent
    if (here / "codex_launch_contract.py").is_file():
        sys.path.insert(0, str(here))
        import codex_launch_contract
        return codex_launch_contract
    sys.path.insert(0, str(here.parent))
    from tinyassets.providers import codex_launch_contract
    return codex_launch_contract


def request_tool_roots(request: dict) -> list:
    """Read classic and Responses-lite tool envelopes without model-name rules.

    Native CLI models may use input additional_tools instead of a top-level
    tools array. Both envelopes carry the same nested namespace/spec structure.
    Absence and malformed envelopes remain failures, not an empty success.
    """
    roots = []
    found = False
    if "tools" in request:
        if not isinstance(request["tools"], list):
            raise ValueError("tools is not an array")
        roots.extend(request["tools"])
        found = True
    inputs = request.get("input", [])
    if not isinstance(inputs, list):
        raise ValueError("input is not an array")
    for item in inputs:
        if isinstance(item, dict) and item.get("type") == "additional_tools":
            if not isinstance(item.get("tools"), list):
                raise ValueError("additional_tools.tools is not an array")
            roots.extend(item["tools"])
            found = True
    if not found or not roots:
        raise ValueError("model request omitted tool specifications")
    return roots


def model_visible_tool_names(request: dict) -> list[str]:
    """Every callable name in the request; Codex's own namespace is unprefixed."""
    def names(items, prefix=""):
        found = []
        for item in items:
            name = prefix + str(item.get("name", item.get("type", "?")))
            if item.get("type") == "namespace":
                inner = "" if name == "functions" else name + "."
                found.extend(names(item.get("tools", []), inner))
            else:
                found.append(name)
        return found

    return names(request_tool_roots(request))


def _fixture_tool(name: str) -> dict:
    return {"type": "function", "name": name, "description": f"Fixture {name}.",
            "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                            "required": ["path"], "additionalProperties": False}}


def run_smoke(command: list[str], *, tools: list[dict] | None = None,
              instructions: str = "Credential-free launch check.") -> dict:
    """Launch as the adapter does; return the first model request and its tools."""
    contract = _contract()
    declared = tools if tools is not None else [_fixture_tool(n) for n in FIXTURE_TOOLS]
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_error(405)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path.rstrip("/").endswith("/responses"):
                requests.append(json.loads(body))
            payload = json.dumps({"error": {"message": "Missing bearer authentication: CLI smoke",
                                            "type": "authentication_error"}}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory(prefix="tinyassets-cli-smoke-",
                                             ignore_cleanup_errors=True) as scratch:
                # Allowlist, not a denylist: never inherit provider/API tokens,
                # user config, proxies, or the host's plugin/runtime settings.
                env = {key: value for key, value in os.environ.items()
                       if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC",
                                          "PATHEXT", "TEMP", "TMP"}}
                env.update(CODEX_HOME=scratch, HOME=scratch, USERPROFILE=scratch)
                bundled = subprocess.run(
                    [*command, "debug", "models", "--bundled"], env=env, cwd=scratch,
                    capture_output=True, text=True, encoding="utf-8", timeout=30, check=True,
                )
                catalog = Path(scratch) / "model-catalog.json"
                catalog.write_text(json.dumps(contract.reduced_catalog(
                    json.loads(bundled.stdout), None)), encoding="utf-8")
                origin = f"http://127.0.0.1:{server.server_port}"
                argv = [
                    *command, *contract.SERVED_LAUNCH_ARGS,
                    "-c", "model_catalog_json=" + json.dumps(catalog.as_posix()),
                    "-c", 'model_provider="cli_smoke"',
                    "-c", 'model_providers.cli_smoke={'
                    f'name="CLI smoke",base_url="{origin}/v1",wire_api="responses"}}',
                ]
                outcome = _drive(argv, env, scratch, requests, declared, instructions)
        finally:
            server.shutdown()
            worker.join(timeout=5)
    return outcome


def _drive(argv, env, scratch, requests, declared, instructions) -> dict:
    proc = subprocess.Popen(argv, env=env, cwd=scratch, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8")
    lines: list[dict] = []

    def read():
        for line in proc.stdout:
            try:
                lines.append(json.loads(line))
            except ValueError:
                continue

    reader = threading.Thread(target=read, daemon=True)
    reader.start()

    def send(ident, method, params):
        message = {"method": method, "params": params}
        if ident is not None:
            message["id"] = ident
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def reply(ident, deadline):
        while time.monotonic() < deadline:
            for message in lines:
                if message.get("id") == ident and "method" not in message:
                    if "error" in message:
                        raise RuntimeError(f"app-server refused request {ident}: "
                                           f"{message['error']}")
                    return message["result"]
            time.sleep(0.05)
        raise RuntimeError(f"app-server did not answer request {ident}")

    try:
        deadline = time.monotonic() + 60
        send(1, "initialize", {"clientInfo": {"name": "tinyassets-cli-smoke", "version": "1"},
                               "capabilities": {"experimentalApi": True}})
        reply(1, deadline)
        send(None, "initialized", {})
        send(2, "thread/start", {
            "dynamicTools": declared, "baseInstructions": instructions, "cwd": scratch,
            "ephemeral": True, "approvalPolicy": "never", "sandbox": "danger-full-access"})
        thread = reply(2, deadline)["thread"]
        send(3, "turn/start", {"threadId": thread["id"],
                               "input": [{"type": "text", "text": "Reply OK."}]})
        reply(3, deadline)
        while not requests and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        if sys.platform == "win32":
            # The npm shim is a cmd.exe child; end the real binary with it.
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, check=False)
        proc.kill()
        proc.wait(timeout=10)
    if not requests:
        raise RuntimeError("Codex app-server sent no model request; stderr tail: "
                           + (proc.stderr.read() or "")[-500:])
    first = requests[0]
    names = model_visible_tool_names(first)
    model = first.get("model")
    expected = sorted(tool["name"] for tool in declared)
    if sorted(names) != expected or not isinstance(model, str) or not model:
        raise RuntimeError(f"Codex served launch exposed {sorted(names)} on model {model!r}; "
                           f"the model may see exactly {expected}")
    return {"model": model, "tools": names, "thread_model": thread.get("model"),
            "body": first}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    result = run_smoke(parser.parse_args().command or ["codex"])
    print("Codex served launch PASS: the model sees exactly the declared tools; "
          f"model={result['model']}; tools={sorted(result['tools'])}")
