"""C12's standalone runner: copied source, real fixture HTTP, no platform account."""
from __future__ import annotations

import json
import os
import runpy
import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "tinyassets/okf/assets/agent.py.txt"
RUNNER = runpy.run_path(str(SOURCE))


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "agents/main").mkdir(parents=True)
    (tmp_path / "agents/main/AGENTS.md").write_text("Use files to finish the user's task.")
    return tmp_path


def tool_call(name="read", arguments=None):
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": "call-1", "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments or {"path": "note.txt"}),
        },
    }]}


@pytest.mark.parametrize("path", [
    "../outside", "/tmp/outside", "a/../outside", "a//b", "./note", "a\\b",
    ".env", ".env.production", ".ssh/key", ".aws/config", ".git/config",
    "run/rules.json", "run/agent.py", "command-center.json",
    "RUN/agent.py", "RUN/rules.json", "COMMAND-CENTER.JSON",
])
def test_file_tools_refuse_escape_credentials_and_policy(folder, path):
    tools = RUNNER["LocalTools"](folder)
    with pytest.raises((ValueError, PermissionError)):
        tools.write(path, "model edit")
    assert not (folder / "run/rules.json").exists()


def test_file_tools_do_not_follow_links_or_special_files(folder, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    secret = outside / "secret"
    secret.write_text("original")
    (folder / "link").symlink_to(outside, target_is_directory=True)
    (folder / "file-link").symlink_to(secret)
    os.link(secret, folder / "hard-link")
    os.mkfifo(folder / "fifo")
    tools = RUNNER["LocalTools"](folder)
    for path in ("link/secret", "file-link", "hard-link", "fifo"):
        with pytest.raises((OSError, ValueError)):
            tools.read(path)
        with pytest.raises((OSError, ValueError)):
            tools.write(path, "changed")
    with pytest.raises(ValueError):
        tools.write("hard-link", "changed")
    assert secret.read_text() == "original"


def test_edit_is_exact_and_failed_edit_preserves_file(folder):
    tools = RUNNER["LocalTools"](folder)
    tools.write("note.txt", "one two two")
    with pytest.raises(ValueError, match="exactly one"):
        tools.edit("note.txt", "two", "three")
    assert tools.read("note.txt") == "one two two"
    tools.edit("note.txt", "one", "zero")
    assert tools.read("note.txt") == "zero two two"
    assert not list(folder.glob(".runner-*"))


@pytest.mark.parametrize("policy,approval,executes", [
    ("ask", False, False), ("ask", True, True),
    ("deny", True, False), ("allow", False, True),
])
def test_bash_uses_owner_policy(folder, policy, approval, executes):
    (folder / "run/rules.json").write_text(json.dumps({"bash": policy}))
    tools = RUNNER["LocalTools"](folder, approve=lambda command: approval)
    tools.call("bash", {"command": "printf allowed > proof"})
    assert (folder / "proof").exists() is executes


def test_default_bash_refuses_noninteractive_input(folder, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    tools = RUNNER["LocalTools"](folder)
    assert "Refused" in tools.bash("touch proof")
    assert not (folder / "proof").exists()


@pytest.mark.parametrize("rules", [{"bash": "yes"}, {"unknown": True}, []])
def test_invalid_policy_fails_visibly(folder, rules):
    (folder / "run/rules.json").write_text(json.dumps(rules))
    with pytest.raises(ValueError):
        RUNNER["LocalTools"](folder)


def test_tool_errors_return_to_model_and_bound_does_not_claim_success(folder):
    seen = []

    def model(messages):
        seen.append(list(messages))
        return tool_call("delete", {"path": "note.txt"})

    with pytest.raises(RuntimeError, match="unfinished"):
        RUNNER["run"](folder, "test", model_call=model, max_calls=2)
    assert "Unknown tool" in seen[1][-1]["content"]
    assert seen[1][-1]["tool_call_id"] == "call-1"


@pytest.mark.parametrize("answer", [None, {}, {"role": "assistant", "content": None},
    {"role": "assistant", "tool_calls": "not a list"}])
def test_malformed_model_reply_fails(folder, answer):
    with pytest.raises(ValueError):
        RUNNER["run"](folder, "test", model_call=lambda _: answer)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:pass@example.com/v1",
                                      "http://example.com/v1", "https://example.com/v1?key=x"])
def test_endpoint_validation_precedes_network(url):
    with pytest.raises(ValueError):
        RUNNER["complete"](url, "fixture", [], api_key="test-only")


def test_copied_runner_uses_four_tools_over_http_without_platform(folder):
    """Real subprocess and HTTP transport; fixture responses, not a paid model."""
    (folder / "run/agent.py").write_bytes(SOURCE.read_bytes())
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            packet = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(packet)
            if len(received) == 1:
                answer = tool_call("write", {"path": "note.txt", "content": "completed"})
            else:
                answer = {"role": "assistant", "content": "Created note.txt"}
            body = json.dumps({"choices": [{"message": answer}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        env = {"PATH": os.environ["PATH"], "MODEL_NAME": "fixture",
               "MODEL_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1"}
        result = subprocess.run([sys.executable, "-I", str(folder / "run/agent.py"), "write note"],
                                env=env, capture_output=True, text=True, timeout=20, check=True)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    assert result.stdout.strip() == "Created note.txt"
    assert (folder / "note.txt").read_text() == "completed"
    assert len(received) == 2
    assert {tool["function"]["name"] for tool in received[0]["tools"]} == {
        "read", "write", "edit", "bash"}
    assert received[1]["messages"][-1]["content"] == "Written"


def test_bash_output_is_bounded_and_process_group_is_stopped(folder):
    tools = RUNNER["LocalTools"](folder, approve=lambda _: True)
    command = shlex.join([sys.executable, "-c", "import os; os.write(1, b'x' * 1000000)"])
    result = json.loads(tools.bash(command))
    assert result["stopped"] == "output_limit"
    assert len(result["output"]) == RUNNER["MAX_COMMAND_BYTES"]


def test_bash_timeout_stops_background_descendants(folder, monkeypatch):
    tools = RUNNER["LocalTools"](folder, approve=lambda _: True)
    monkeypatch.setitem(tools.bash.__globals__, "COMMAND_SECONDS", 0.2)
    script = "import time; from pathlib import Path; time.sleep(0.8); Path('escaped').touch()"
    command = shlex.join([sys.executable, "-c", script]) + " & wait"
    result = json.loads(tools.bash(command))
    assert result["stopped"] == "timeout"
    # A separate subprocess keeps the assertion independent of runner internals.
    subprocess.run([sys.executable, "-c", "import time; time.sleep(1)"], check=True)
    assert not (folder / "escaped").exists()
