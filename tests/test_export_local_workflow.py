"""Real portable prompt graphs, explicit execution, and pre-effect refusal."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_export_local_runner import RUNNER, SOURCE, agent_policy, folder  # noqa: F401
from tests.test_okf_workflow_export import automation, branch
from tinyassets.okf.workflow_export import PREFIX, SCHEDULES, serialize

PATH = PREFIX + "workflow-1.json"


def configure(folder, *, twice=False, scheduled=False):  # noqa: F811
    raw = branch()
    if twice:
        raw["graph"]["nodes"].append({"id": "later", "node_def_id": "later-node"})
        raw["graph"]["edges"] = [{"from": "step-source", "to": "later"}]
        raw["node_defs"].append({"node_id": "later-node", "display_name": "Finish",
                                "prompt_template": 'Finish {{summary}}; literal {"x": 1}',
                                "input_keys": ["summary"], "output_keys": ["result"]})
    result = serialize([raw], [automation()] if scheduled else [],
                       owner="owner-a", center="center-a")
    for path, data in result.files.items():
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return json.loads((folder / PATH).read_text())


def test_linear_prompt_workflow_preserves_state_and_placeholder_semantics(folder):  # noqa: F811
    configure(folder, twice=True)
    prompts = []
    def model(messages):
        prompts.append(messages[0]["content"])
        return {"role": "assistant", "content": "first" if len(prompts) == 1 else "second"}
    result = RUNNER["workflow"](folder, PATH, {"notes": "hello"}, model_call=model)
    assert result == {"notes": "hello", "summary": "first", "result": "second"}
    assert prompts == ["Summarize hello", 'Finish first; literal {"x": 1}']


@pytest.mark.parametrize("change", [
    "code", "cycle", "fanout", "missing", "extra", "tool", "typed", "timeout",
])
def test_entire_graph_is_validated_before_first_model_call(folder, change):  # noqa: F811
    doc = configure(folder, twice=True)
    definition = doc["definition"]
    last = definition["node_defs"][-1]
    if change == "code":
        last["source_code"] = "raise RuntimeError('never execute')"
    elif change == "cycle":
        definition["graph"]["edges"].append({"from": "step-2", "to": "step-1"})
    elif change == "fanout":
        definition["graph"]["edges"].append({"from": "step-1", "to": "__end__"})
    elif change == "missing":
        last["prompt_template"] = "Read {unavailable}"
    elif change == "extra":
        last["unknown_effect"] = {"launch": "something"}
    elif change == "typed":
        definition["state_schema"] = [{"name": "result", "type": "string"}]
    elif change == "timeout":
        last["timeout_seconds"] = 1
    else:
        last["tools_allowed"] = ["bash"]
    (folder / PATH).write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        RUNNER["workflow"](folder, PATH, {"notes": "hello"},
                           model_call=lambda _: pytest.fail("model called before full validation"))


def test_workflow_policy_requires_local_consent_and_never_executes_model_tool_calls(folder):  # noqa: F811
    configure(folder)
    agent_policy(folder, **{"workspace.workflows": "hand_off"})
    with pytest.raises(PermissionError):
        RUNNER["workflow"](folder, PATH, {"notes": "hello"}, approve=lambda _: True,
                           model_call=lambda _: pytest.fail("hand-off ran"))
    agent_policy(folder, **{"workspace.workflows": "ask_first"})
    with pytest.raises(PermissionError):
        RUNNER["workflow"](folder, PATH, {"notes": "hello"}, approve=lambda _: False,
                           model_call=lambda _: pytest.fail("unapproved run"))
    with pytest.raises(ValueError, match="without tools"):
        RUNNER["workflow"](folder, PATH, {"notes": "hello"}, approve=lambda _: True,
                           model_call=lambda _: {"role": "assistant", "content": "no",
                                                 "tool_calls": [{"function": "bash"}]})


def test_changed_workflow_invalidates_schedule_state_before_another_run(folder):  # noqa: F811
    configure(folder, scheduled=True)
    doc = json.loads((folder / SCHEDULES).read_text())
    doc["automations"][0]["enabled"] = True
    doc["automations"][0]["trigger"] = {
        "kind": "interval", "seconds": 60, "start_at": "2026-10-14T00:00:00+00:00"}
    (folder / SCHEDULES).write_text(json.dumps(doc))
    now = RUNNER["_instant"]("2026-10-14T00:00:00+00:00")
    turns = []
    RUNNER["schedule"](folder, enabled=True, once=True, turn=lambda r: turns.append(r["id"]),
                       clock=lambda: now)
    workflow = json.loads((folder / PATH).read_text())
    workflow["definition"]["node_defs"][0]["prompt_template"] = "A different prompt"
    (folder / PATH).write_text(json.dumps(workflow))
    with pytest.raises(ValueError, match="configuration changed"):
        RUNNER["schedule"](folder, enabled=True, once=True, turn=lambda _: pytest.fail("replayed"),
                           clock=lambda: now + 60)
    assert turns == ["schedule-1"]


def test_disabled_event_data_is_readable_but_cannot_start_a_local_listener(folder):  # noqa: F811
    configure(folder, scheduled=True)
    doc = json.loads((folder / SCHEDULES).read_text())
    doc["automations"][0]["trigger"] = {"kind": "event", "event_type": "owner_message",
                                         "event_filter": {}}
    (folder / SCHEDULES).write_text(json.dumps(doc))
    RUNNER["schedule"](folder, enabled=True, once=True,
                       turn=lambda _: pytest.fail("disabled event ran"))
    doc["automations"][0]["enabled"] = True
    (folder / SCHEDULES).write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="Unsupported local schedule"):
        RUNNER["schedules"](folder)


def test_copied_workflow_cli_runs_against_real_local_model_without_platform(folder):  # noqa: F811
    configure(folder)
    (folder / "run/agent.py").write_bytes(SOURCE.read_bytes())
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "done"}}]})
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body.encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        (folder / ".env").write_text(
            f"MODEL_BASE_URL=http://127.0.0.1:{server.server_port}/v1\nMODEL_NAME=local\n")
        result = subprocess.run([sys.executable, str(folder / "run/agent.py"), "--workflow", PATH,
                                 "--inputs", '{"notes":"hello"}'],
                                capture_output=True, text=True, timeout=20,
                                env={"PATH": "/usr/bin"})
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["summary"] == "done"
        assert received[0]["messages"] == [{"role": "user", "content": "Summarize hello"}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_workflow_changed_after_durable_claim_never_reaches_model(folder, monkeypatch):  # noqa: F811
    configure(folder, scheduled=True)
    doc = json.loads((folder / SCHEDULES).read_text())
    doc['automations'][0]['enabled'] = True
    doc['automations'][0]['trigger'] = {'kind': 'once', 'at': '2026-10-14T00:00:00+00:00'}
    (folder / SCHEDULES).write_text(json.dumps(doc))
    namespace = RUNNER['schedule'].__globals__
    original = namespace['ScheduleState'].save
    def change_after_claim(self, ledger):
        original(self, ledger)
        document = json.loads((folder / PATH).read_text())
        document['definition']['node_defs'][0]['prompt_template'] = 'Changed after approval'
        (folder / PATH).write_text(json.dumps(document))
    monkeypatch.setattr(namespace['ScheduleState'], 'save', change_after_claim)
    monkeypatch.setitem(namespace, 'model_environment',
                        lambda *args: ('http://localhost', 'model', ''))
    monkeypatch.setitem(namespace, 'complete', lambda *args, **kw: pytest.fail('changed model ran'))
    with pytest.raises(ValueError, match='Workflow configuration changed'):
        RUNNER['schedule'](folder, enabled=True, once=True,
                           clock=lambda: RUNNER['_instant']('2026-10-14T00:00:00+00:00'))
    ledger = json.loads((folder / '.run-state/schedules.json').read_text())
    assert ledger['attempts']['schedule-1']['status'] == 'claimed'
