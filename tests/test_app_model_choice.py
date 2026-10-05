"""Execute actual app send/queue/recovery functions with captured model choices."""

import json
import shutil
import subprocess

import pytest

from tests.test_onboarding_app import _js_function, _run_app
from tinyassets.onboarding import render_app_html


def choice(model):
    return {"version": 1, "mode": "explicit", "saved_default": {
        "provider_ref": "owned:future-provider", "model_id": model,
    }, "fallbacks": []}


@pytest.mark.parametrize("kind", ["send", "voice"])
def test_typed_and_spoken_choice_reaches_converse(tmp_path, kind):
    expected = choice("chosen")
    result = _run_app(tmp_path, {"kind": kind, "message": "hello", "modelChoice": expected,
                                 "payload": {"reply": "answer"}})
    assert result["converseChoices"] == [expected]


def test_queued_choice_survives_picker_changes_and_object_mutation(tmp_path):
    result = _run_app(tmp_path, {
        "kind": "send", "message": "first", "secondMessage": "second", "slowFirst": True,
        "modelChoice": choice("first-model"), "secondModelChoice": choice("queued-model"),
        "mutateQueuedChoice": True, "afterQueueModelChoice": choice("later-model"),
        "payload": {"reply": "answer"},
    })
    assert result["converseChoices"] == [choice("first-model"), choice("queued-model")]
    assert result["savedWhileQueued"][0]["modelChoice"] == choice("queued-model")
    assert result["maxActive"] == 1


def test_retry_keeps_original_choice_not_new_picker_value(tmp_path):
    result = _run_app(tmp_path, {
        "kind": "send", "message": "hello", "modelChoice": choice("original"),
        "beforeRetryModelChoice": choice("later"), "clickResend": True,
        "payloads": [{"error": "held"}, {"reply": "answer"}],
    })
    assert result["converseChoices"] == [choice("original"), choice("original")]


@pytest.mark.parametrize("saved", [None, choice("original")])
def test_restored_inflight_keeps_its_choice_or_legacy_no_override(tmp_path, saved):
    result = _run_app(tmp_path, {
        "kind": "restore", "pending": "hello", "pendingModelChoice": saved,
        "modelChoice": choice("later"), "history": [], "payload": {"reply": "answer"},
        "clickAfterRestore": "Check saved conversation",
    })
    assert result["converseChoices"] == []
    assert result["inflight"].get("modelChoice") == saved


@pytest.mark.parametrize("saved", [None, choice("original")])
def test_restored_queue_keeps_its_choice_or_legacy_no_override(tmp_path, saved):
    # Both halves of the saved-row fence, as the harness signs them in:
    # principal "p-1" in home "u-1". A row missing either is KEPT and never
    # offered - test_onboarding_app.py owns that negative boundary.
    item = {"message": "hello", "display": "hello", "ts": 123,
            "owner": "p-1", "scope": "u-1"}
    if saved is not None:
        item["modelChoice"] = saved
    result = _run_app(tmp_path, {
        "kind": "restore", "queued": [item], "modelChoice": choice("later"),
        "history": [], "payload": {"reply": "answer"}, "clickAfterRestore": "Send it now",
    })
    assert result["converseChoices"] == [saved]


def test_same_text_and_timestamp_with_different_choices_remain_distinct(tmp_path):
    first = {"message": "hello", "ts": 123, "owner": "p-1", "scope": "u-1",
             "modelChoice": choice("first")}
    second = {**first, "modelChoice": choice("second")}
    result = _run_app(tmp_path, {
        "kind": "restore", "queued": [first, second], "history": [],
        "payload": {"reply": "answer"}, "clickAfterRestore": "Send it now",
    })
    assert result["converseChoices"] == [choice("second")]
    assert len(result["savedAfter"]) == 1
    assert result["savedAfter"][0]["modelChoice"] == choice("first")


@pytest.mark.parametrize("selected", [None, choice("original"), {
    "version": 1, "mode": "automatic", "saved_default": None, "fallbacks": [],
}, {
    **choice("primary"), "fallbacks": [
        {"provider_ref": "owned:second", "model_id": "later"},
        {"provider_ref": "owned:third", "model_id": "last"},
    ],
}])
def test_actual_mcp_method_forwards_choice_without_mutating_it(tmp_path, selected):
    html, _ = render_app_html()
    method = html[html.index('    converse(message,'):html.index('    getStatus(){')]
    functions = "\n".join(_js_function(html, name)
                          for name in ("turnInputMethod", "copyModelChoice"))
    program = functions + "\nconst seen=[]; const MCP={"
    program += "callTool:(name,args)=>{seen.push({name,args});},"
    program += method + "};\nconst selected=" + json.dumps(selected) + ";\n"
    program += 'MCP.converse("hello","typed",selected);\n'
    program += 'if(selected&&selected.saved_default) selected.saved_default.model_id="changed";\n'
    program += 'MCP.converse("legacy"); console.log(JSON.stringify(seen));\n'
    script = tmp_path / "model_choice_rpc.js"
    script.write_text(program, encoding="utf-8")
    node = shutil.which("node")
    assert node, "Node is required to verify the actual app method"
    result = subprocess.run([node, str(script)], capture_output=True, text=True,
                            encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    calls = json.loads(result.stdout)
    assert calls[0]["name"] == "converse"
    assert calls[0]["args"].get("model_choice") == selected
    if selected is None:
        assert "model_choice" not in calls[0]["args"]
    assert calls[1] == {"name": "converse",
                        "args": {"message": "legacy", "input_method": "unknown"}}
