#!/usr/bin/env python3
"""Capture native CLI request bodies against a credential-free loopback sink.

This is an inventory/payload probe, NEVER a live-model success trial. No request
is forwarded upstream. Only synthetic prompts are accepted; inherited provider
credentials are removed and the CLI uses an empty temporary home/workspace.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

EXPECTED_MCP_TOOLS = frozenset("mcp__tinyassets__" + name
                               for name in ("read", "write", "edit", "bash"))


#: Codex sees the definition's tools under their own names (app-server
#: dynamicTools in its own namespace); Claude under its MCP server prefix.
EXPECTED_DYNAMIC_TOOLS = frozenset(("read", "write", "edit", "bash"))


def exposure_check(metrics: dict, expected: frozenset = EXPECTED_MCP_TOOLS) -> dict:
    names = set(metrics["tool_names"])
    missing, extra = expected - names, names - expected
    return {
        "missing_mcp_definitions": sorted(missing),
        "extra_tools": sorted(extra),
        "complete_agent_payload": not missing and not extra and not metrics["over_budget_chars"],
        "production_parity_proven": False,
    }


def payload_metrics(body: dict) -> dict:
    """Count Unicode characters, not billed tokens; keep tool names auditable."""
    tools = list(body.get("tools", []))
    instructions = body.get("system", body.get("instructions", ""))

    def compact(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    extra = []
    for item in body.get("input", body.get("messages", [])):
        if item.get("type") == "additional_tools":
            tools.extend(item.get("tools", []))
        elif item.get("role") in ("developer", "system"):
            extra.append(compact(item.get("content", [])))
    text = instructions if isinstance(instructions, str) else compact(instructions)
    text += "".join(extra)
    tool_chars = len(compact(tools))
    total = len(text) + tool_chars
    def names(items, prefix=""):
        # Codex groups MCP tools in a namespace named mcp__<server> whose members
        # are called as mcp__<server>__<tool>; its own tools sit in `functions`.
        found = []
        for item in items:
            function = item.get("function", item)
            name = prefix + function.get("name", item.get("type", "unknown"))
            if item.get("type") == "namespace":
                found.extend(names(item["tools"], "" if name == "functions"
                                   else name + ("__" if name.startswith("mcp__") else ".")))
            else:
                found.append(name)
        return found

    return {
        "tool_names": names(tools),
        "instruction_chars": len(text), "tool_schema_chars": tool_chars,
        "resident_chars": total, "estimated_tokens_chars_div_4": total / 4,
        "budget_chars": 4000, "over_budget_chars": max(0, total - 4000),
        "input_chars": len(compact(body.get("input", body.get("messages", [])))),
        "wire_chars": len(compact(body)),
        "wire_utf8_bytes": len(compact(body).encode("utf-8")),
    }


def inventory_definitions():
    """The shipping four schemas over stdio; never impersonate a successful call."""
    import asyncio

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tinyassets.engine_mcp_server import mcp
    from tinyassets.served_tools import FOUR_MODEL_TOOLS

    registered = asyncio.run(mcp.list_tools(run_middleware=False))
    return [{"name": t.name, "description": t.description, "inputSchema": t.parameters}
            for t in registered if t.name in FOUR_MODEL_TOOLS]


def serve_inventory(path):
    definitions = json.loads(Path(path).read_text(encoding="utf-8"))
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        method = request.get("method")
        if method == "initialize":
            result = {"protocolVersion": request["params"]["protocolVersion"],
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "inventory-probe", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": definitions}
        elif method == "tools/call":
            result = {"isError": True, "content": [{"type": "text", "text": "inventory only"}]}
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)


def http_payloads(system: str) -> list[dict]:
    """All registered HTTP dialects, using their installed wire encoder.

    These are encoded fixtures, not claims that an inference request ran.
    Native captures and these rows use precisely the same metric function.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mcp.types import Tool

    from tinyassets.providers.agent_chat_codec import tool_definitions
    from tinyassets.providers.definition import _HTTP_PROTOCOLS
    from tinyassets.providers.protocol_encoders import agent_codec_for

    definitions = tool_definitions(tuple(Tool(**item) for item in inventory_definitions()))
    rows = []
    for protocol in _HTTP_PROTOCOLS:
        codec = agent_codec_for(protocol)
        if codec is None:
            rows.append({"provider": protocol, "complete_agent_payload": False,
                         "error": "registered dialect has no agent codec"})
            continue
        _, body = codec.encode(prompt="Reply OK.", system=system, source_ref="probe",
                               model="probe", tools=definitions)
        rows.append({"provider": protocol, "live_model_trial": False,
                     "scope": "installed HTTP codec fixture, not a live capture",
                     "supplied_system": system, "metrics": payload_metrics(body), "body": body})
    return rows


def capture_codex(executable: str, *, system: str = "Synthetic stock prompt.") -> dict:
    """The production served launch (codex_launch_contract) with the real four
    definitions as dynamicTools, captured by the image build's own driver."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.codex_cli_smoke import run_smoke

    tools = [{"type": "function", "name": d["name"], "description": d["description"],
              "inputSchema": d["inputSchema"]} for d in inventory_definitions()]
    result = run_smoke([executable], tools=tools, instructions=system)
    metrics = payload_metrics(result["body"])
    return {"provider": "codex", "live_model_trial": False,
            "scope": "local CLI probe of the production app-server launch",
            **exposure_check(metrics, EXPECTED_DYNAMIC_TOOLS),
            "supplied_system": system, "requests": 1, "metrics": metrics,
            "body": result["body"]}


def capture(provider: str, executable: str, *, timeout: float = 45,
            system: str = "Synthetic stock prompt.") -> dict:
    bodies = []

    class Sink(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            if size > 8 * 1024 * 1024:
                self.send_error(413)
                return
            body = json.loads(self.rfile.read(size))
            if urlsplit(self.path).path.rstrip("/").endswith(("/responses", "/messages")):
                bodies.append(body)
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": {
                "type": "invalid_request_error", "message": "inventory probe complete",
            }}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="ta-native-payload-") as temporary:
            root = Path(temporary)
            env = {k: v for k, v in os.environ.items() if k.upper() in {
                "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
                "LANG", "LC_ALL",
            }}
            env.update(HOME=str(root), USERPROFILE=str(root),
                       CODEX_HOME=str(root), CLAUDE_CONFIG_DIR=str(root),
                       ENABLE_TOOL_SEARCH="false")
            url = f"http://127.0.0.1:{server.server_port}"
            schemas = root / "schemas.json"
            schemas.write_text(json.dumps(inventory_definitions()), encoding="utf-8")
            mcp = {"command": sys.executable,
                   "args": [str(Path(__file__).resolve()), "--inventory-server", str(schemas)]}
            config = root / "mcp.json"
            config.write_text(json.dumps({"mcpServers": {"tinyassets": mcp}}), encoding="utf-8")
            env.update(ANTHROPIC_BASE_URL=url, ANTHROPIC_API_KEY="synthetic-probe-key",
                       CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
            argv = [executable, "-p", "--model", "fable", "--tools", "",
                    "--system-prompt", system,
                    "--setting-sources", "project", "--permission-mode", "default",
                    "--strict-mcp-config", "--mcp-config", str(config),
                    "--allowedTools", "mcp__tinyassets__read", "mcp__tinyassets__write",
                    "mcp__tinyassets__edit", "mcp__tinyassets__bash",
                    "--no-session-persistence", "Reply OK."]
            completed = subprocess.run(argv, cwd=root, env=env, capture_output=True,
                                       input="", timeout=timeout, text=True,
                                       encoding="utf-8", errors="replace")
            if not bodies:
                raise RuntimeError(
                    f"{provider}: no request captured (exit {completed.returncode}): "
                    + completed.stderr[-1200:])
            metrics = payload_metrics(bodies[0])
            return {"provider": provider, "live_model_trial": False,
                    "scope": "local CLI probe with stdio MCP, not the production HTTP launch",
                    **exposure_check(metrics),
                    "supplied_system": system,
                    "diagnostic_stderr": completed.stderr[-3000:],
                    "diagnostic_stdout": completed.stdout[-3000:],
                    "requests": len(bodies), "metrics": metrics,
                    "all_request_metrics": [payload_metrics(body) for body in bodies],
                    "body": bodies[0]}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=("claude-code", "codex", "registered-http"))
    parser.add_argument("--executable")
    parser.add_argument("--system-file", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    system = (args.system_file.read_text(encoding="utf-8") if args.system_file
              else "Synthetic stock prompt.")
    if args.provider == "registered-http":
        rows = http_payloads(system)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps([{k: v for k, v in row.items() if k != "body"} for row in rows]))
        return 0 if all("metrics" in row for row in rows) else 2
    executable = args.executable or shutil.which(
        "claude" if args.provider == "claude-code" else "codex")
    if not executable:
        parser.error("CLI executable unavailable")
    result = (capture_codex(executable, system=system) if args.provider == "codex"
              else capture(args.provider, executable, system=system))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["metrics"], ensure_ascii=False))
    if not result["complete_agent_payload"]:
        print("Four-tool/budget check failed; see missing/extra tools and budget overflow. "
              "This local probe is not a production parity proof.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--inventory-server"]:
        serve_inventory(sys.argv[2])
    else:
        sys.exit(main())
