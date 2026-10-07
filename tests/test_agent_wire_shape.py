"""Envelope decoupling, no network."""

import copy
import json
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import pytest

from tests.test_agent_chat_codec import (
    MODEL,
    NAMES,
    SOURCE,
    call,
    completed_round,
    definitions,
    response,
)
from tinyassets.providers import agent_chat_codec as core
from tinyassets.providers.agent_wire_codec import AgentWireShape, installed_agent_wire
from tinyassets.providers.protocol_encoders import agent_codec_for


def document():
    # The envelope lives in the chat_messages dialect document.
    return json.loads(Path(__file__).parents[1].joinpath(
        "tinyassets/providers/dialects/chat_messages.json",
    ).read_text("utf-8"))["envelope"]


def context():
    return {"source_ref": SOURCE, "requested_model": MODEL, "tool_names": NAMES}


def request(**kwargs):
    return {"prompt": "  original\n🪐 ", "system": " same system\n",
            "source_ref": SOURCE, "model": MODEL, "tools": definitions(), **kwargs}


def result(function, *args, **kwargs):
    try:
        value = function(*args, **kwargs)
    except (ValueError, TypeError, OverflowError) as exc:
        return type(exc).__name__, str(exc)
    return "ok", asdict(value)


def test_engine_adapter_does_not_use_compatibility_wrappers(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("engine called a historical protocol wrapper")

    for name in ("encode_openai_chat_agent", "encode_openai_chat_agent_portable",
                 "decode_openai_chat_agent"):
        monkeypatch.setattr(core, name, forbidden)
    codec = agent_codec_for("openai_chat")
    path, body = codec.encode(**request())
    assert path == "/v1/chat/completions" and body["model"] == MODEL
    assert codec.decode(response([], content="ok", finish="stop"), **context()).text == "ok"


def test_alternate_installed_envelope_uses_same_core_and_exact_history():
    doc = document()
    doc["path"] = "/engine/answer"
    doc["request"] = {name: "wire_" + name for name in doc["request"]}
    doc["response"] = {"choices": "/result/candidates", "error": "/failure",
                       "model": "/receipt/actual", "input_tokens": "/meter/in",
                       "output_tokens": "/meter/out"}
    doc["choice"] = {"message": "/answer/body", "finish": "/answer/stop", "error": "/fault"}
    shape = AgentWireShape.compile(doc)
    body = {"result": {"candidates": [{"answer": {
        "body": {"content": None, "tool_calls": [call()]}, "stop": "tool_calls",
    }}]}, "receipt": {"actual": "opaque/latest"}, "meter": {"in": 9, "out": 2}}
    reply = shape.decode(body, **context())
    assert reply.stop == "tool_requests" and reply.reported_model == "opaque/latest"
    assert (reply.input_tokens, reply.output_tokens) == (9, 2)
    historical = core.CapturedToolRound(round=completed_round(), tools=definitions())
    kwargs = request(history=(historical,), source_ref="other/source")
    path, wire = shape.encode(**kwargs)
    canonical = core.build_portable_agent_body(**kwargs)
    assert path == "/engine/answer"
    assert wire == {"wire_" + name: value for name, value in canonical.items()}
    assert [m["content"] for m in wire["wire_messages"] if m["role"] == "tool"] == [
        item.result_json for item in historical.round.outcomes
    ]
    assert set(wire) == {"wire_model", "wire_messages", "wire_tools", "wire_tool_choice"}
    assert core.build_agent_body(**request())["messages"][:2] == canonical["messages"][:2]


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(credentials="forbidden"),
    lambda d: d.update(path="https://example.test/request"),
    lambda d: d.update(path="//example.test/request"),
    lambda d: d.update(path="/../request"),
    lambda d: d.update(path="/request?key=secret"),
    lambda d: d.update(path="/request#fragment"),
    lambda d: d["request"].update(model="messages"),
    lambda d: d["request"].update(model="x" * 65),
    lambda d: d["request"].update(model=["model"]),
    lambda d: d["request"].pop("tools"),
    lambda d: d["response"].update(model=""),
    lambda d: d["response"].update(error="/bad~2escape"),
    lambda d: d["response"].update(model="/x" * 17),
    lambda d: d["choice"].update(callback="python.module"),
])
def test_descriptor_is_closed_and_bounded(mutate):
    doc = document()
    mutate(doc)
    with pytest.raises(ValueError):
        AgentWireShape.compile(doc)


def test_compiled_shape_is_detached_immutable_and_cwd_independent(tmp_path, monkeypatch):
    doc = document()
    shape = AgentWireShape.compile(doc)
    original = copy.deepcopy(doc)
    doc["request"]["model"] = "changed"
    assert dict(shape.request_fields)["model"] == original["request"]["model"]
    with pytest.raises(FrozenInstanceError):
        shape.path = "/changed"
    installed_agent_wire.cache_clear()
    monkeypatch.chdir(tmp_path)
    assert installed_agent_wire().path == original["path"]


@pytest.mark.parametrize("root_error,choice_error", [(False, None), ({}, None),
                                                     (None, "denied"), (None, False)])
def test_errors_block_all_tools(root_error, choice_error):
    body = response([call()])
    body["error"] = root_error
    body["choices"][0]["error"] = choice_error
    with pytest.raises(ValueError, match="unavailable"):
        installed_agent_wire().decode(body, **context())
