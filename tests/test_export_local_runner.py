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


def agent_policy(folder, **changes):
    from tinyassets.broker.policy import DEFAULT_RULES

    rules = dict(DEFAULT_RULES) | changes
    (folder / "agents/main/rules.json").write_text(json.dumps({
        "schema_version": 1, "behaviours": rules,
    }))


@pytest.mark.parametrize("behavior,consent,allowed", [
    ("do", False, True), ("ask_first", False, False), ("ask_first", True, True),
    ("do_if_preapproved", False, False), ("do_if_preapproved", True, True),
    ("hand_off", False, False), ("hand_off", True, False),
])
@pytest.mark.parametrize("name", ["read", "write", "edit", "bash"])
def test_exported_workspace_rules_require_fresh_local_authority(
        folder, behavior, consent, allowed, name):
    agent_policy(folder, **{"workspace.files": behavior, "workspace.shell": behavior})
    (folder / "run/rules.json").write_text('{"bash":"allow"}')
    (folder / "note.txt").write_text("original")
    prompts = []
    def approve(action):
        prompts.append(action)
        return consent
    arguments = {
        "read": {"path": "note.txt"},
        "write": {"path": "note.txt", "content": "changed"},
        "edit": {"path": "note.txt", "old": "original", "new": "changed"},
        "bash": {"command": "printf changed > note.txt"},
    }[name]
    result = RUNNER["LocalTools"](folder, approve=approve).call(name, arguments)
    assert result.startswith("Refused:") is not allowed
    if name == "read":
        assert (result == "original") is allowed
    else:
        assert (folder / "note.txt").read_text() == ("changed" if allowed else "original")
    assert bool(prompts) is (behavior in ("ask_first", "do_if_preapproved"))


@pytest.mark.parametrize("raw", ["null", "{}", '{"schema_version":1,"behaviours":{}}'])
def test_malformed_exported_policy_does_not_use_defaults(folder, raw):
    (folder / "agents/main/rules.json").write_text(raw)
    with pytest.raises(ValueError, match="policy"):
        RUNNER["LocalTools"](folder)


def test_declared_policy_must_exist_and_selected_agent_uses_own_policy(folder):
    (folder / "command-center.json").write_text(json.dumps({"roster": [
        {"agent": "main", "rules": "agents/main/rules.json"},
    ]}))
    with pytest.raises(ValueError, match="missing"):
        RUNNER["LocalTools"](folder)
    agent_policy(folder, **{"workspace.files": "hand_off"})
    (folder / "agents/editor").mkdir()
    (folder / "agents/editor/AGENTS.md").write_text("Edit notes")
    (folder / "note.txt").write_text("original")
    answers = iter([tool_call(), {"role": "assistant", "content": "done"}])
    captured = []
    def model(messages):
        captured[:] = messages
        return next(answers)
    RUNNER["run"](folder, "read", agent="main", model_call=model)
    assert captured[-1]["content"].startswith("Refused:")
    editor = RUNNER["LocalTools"](folder, agent="editor")
    assert editor.call("read", {"path": "note.txt"}) == "original"


@pytest.mark.parametrize("path", [
    "../outside", "/tmp/outside", "a/../outside", "a//b", "./note", "a\\b",
    ".env", ".env.production", ".ssh/key", ".aws/config", ".git/config",
    "run/rules.json", "run/agent.py", "command-center.json",
    "RUN/agent.py", "RUN/rules.json", "COMMAND-CENTER.JSON",
    "agents/main/rules.json", "agents/main/settings.yaml", "AGENTS/main/RULES.JSON",
    "automations.json", ".run-state/schedules.json",
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


@pytest.mark.parametrize("configuration", ["global", "agent", "dotenv"])
def test_copied_runner_uses_four_tools_over_http_without_platform(folder, configuration):
    """Real subprocess and HTTP transport; fixture responses, not a paid model."""
    (folder / "run/agent.py").write_bytes(SOURCE.read_bytes())
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            assert self.headers.get("Authorization") is None
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
        if configuration == "dotenv":
            (folder / ".env").write_text(
                f"MODEL_BASE_URL={env.pop('MODEL_BASE_URL')}\nMODEL_NAME={env.pop('MODEL_NAME')}\n"
            )
        if configuration == "agent":
            (folder / "command-center.json").write_text(json.dumps({"roster": [
                {"agent": "main", "model_env": "AGENT_1"},
            ]}))
            env.update({"AGENT_1_MODEL_BASE_URL": env["MODEL_BASE_URL"],
                        "AGENT_1_MODEL_NAME": "fixture",
                        "MODEL_BASE_URL": "https://unused.invalid/v1",
                        "MODEL_API_KEY": "global-key-must-not-reach-agent-endpoint"})
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


@pytest.mark.parametrize("prefix", ["AWS", "AGENT_0", "AGENT_1_MODEL", "AGENT_１２", 1, 0, None])
def test_roster_cannot_select_arbitrary_environment_names(folder, prefix):
    (folder / "command-center.json").write_text(json.dumps({"roster": [
        {"agent": "main", "model_env": prefix},
    ]}))
    with pytest.raises(ValueError, match="environment reference"):
        RUNNER["model_environment"](folder, "main")


def test_agent_model_does_not_inherit_global_credentials(folder, monkeypatch):
    (folder / "command-center.json").write_text(json.dumps({"roster": [
        {"agent": "main", "model_env": "AGENT_1"},
    ]}))
    monkeypatch.setenv("MODEL_API_KEY", "global-secret")
    monkeypatch.setenv("MODEL_NAME", "global-model")
    monkeypatch.setenv("MODEL_BASE_URL", "https://global.example/v1")
    monkeypatch.setenv("AGENT_1_MODEL_NAME", "local-model")
    with pytest.raises(ValueError, match="together"):
        RUNNER["model_environment"](folder, "main")
    monkeypatch.setenv("AGENT_1_MODEL_BASE_URL", "http://localhost:11434/v1")
    assert RUNNER["model_environment"](folder, "main") == (
        "http://localhost:11434/v1", "local-model", "",
    )


def test_local_environment_is_literal_private_data_with_process_precedence(folder, monkeypatch):
    for key in list(os.environ):
        if RUNNER["_model_key"](key):
            monkeypatch.delenv(key)
    marker = folder / "shell-must-not-run"
    (folder / ".env").write_text(
        "# owner local configuration\nMODEL_BASE_URL=http://localhost:11434/v1\n"
        "MODEL_NAME='local-model'\n"
        f'MODEL_API_KEY="$(touch {marker})"\n'
    )
    assert RUNNER["model_environment"](folder, "main") == (
        "http://localhost:11434/v1", "local-model", f"$(touch {marker})",
    )
    assert not marker.exists()
    assert "MODEL_API_KEY" not in os.environ
    monkeypatch.setenv("MODEL_NAME", "process-model")
    assert RUNNER["model_environment"](folder, "main")[1] == "process-model"
    with pytest.raises(PermissionError):
        RUNNER["LocalTools"](folder).read(".env")


@pytest.mark.parametrize("prefix", ["", "AGENT_1_"])
@pytest.mark.parametrize("endpoint", [None, "https://first.example/v1", "https://second.example/v1"])
@pytest.mark.parametrize("process_key", [None, "", "process-key"])
def test_process_model_overrides_keep_credentials_with_their_endpoint(
    folder, monkeypatch, prefix, endpoint, process_key,
):
    for key in list(os.environ):
        if RUNNER["_model_key"](key):
            monkeypatch.delenv(key)
    (folder / ".env").write_text(
        f"{prefix}MODEL_BASE_URL=https://first.example/v1\n"
        f"{prefix}MODEL_NAME=first\n{prefix}MODEL_API_KEY=first-endpoint-key\n"
    )
    if prefix:
        (folder / "command-center.json").write_text(json.dumps({"roster": [
            {"agent": "main", "model_env": "AGENT_1"},
        ]}))
        monkeypatch.setenv("MODEL_BASE_URL", "https://global.example/v1")
        monkeypatch.setenv("MODEL_NAME", "global-model")
        monkeypatch.setenv("MODEL_API_KEY", "global-key-must-not-leak")
    monkeypatch.setenv(prefix + "MODEL_NAME", "process-model")
    if endpoint is not None:
        monkeypatch.setenv(prefix + "MODEL_BASE_URL", endpoint)
    if process_key is not None:
        monkeypatch.setenv(prefix + "MODEL_API_KEY", process_key)
    expected_key = process_key if process_key is not None else (
        "" if endpoint == "https://second.example/v1" else "first-endpoint-key"
    )
    assert RUNNER["model_environment"](folder, "main") == (
        endpoint or "https://first.example/v1", "process-model", expected_key,
    )


@pytest.mark.parametrize("raw", [
    b"MODEL_NAME=one\nMODEL_NAME=two\n", b"UNKNOWN_SECRET=never-expose\n",
    b"export MODEL_NAME=one\n", b'MODEL_NAME="unterminated\n',
    b'MODEL_NAME="escaped\\nline"\n', b"MODEL_NAME='unterminated\n",
    b"MODEL_NAME=" + b"x" * (64 * 1024),
])
def test_malformed_environment_refuses_without_global_fallback(folder, monkeypatch, raw):
    (folder / ".env").write_bytes(raw)
    monkeypatch.setenv("MODEL_NAME", "valid-global-model")
    with pytest.raises(ValueError):
        RUNNER["model_environment"](folder, "main")


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_environment_does_not_follow_links_or_read_special_files(folder, kind):
    source = folder / "owner-config"
    source.write_text("MODEL_NAME=fixture\n")
    target = folder / ".env"
    if kind == "symlink":
        target.symlink_to(source)
    elif kind == "hardlink":
        os.link(source, target)
    else:
        os.mkfifo(target)
    with pytest.raises((OSError, ValueError)):
        RUNNER["model_environment"](folder, "main")


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
