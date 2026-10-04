"""Standard OpenAI-compatible tool-call spellings, through the real served path.

Live 2026-09-24: a free-only universe connected its own OpenRouter account and
the first turn died with ``agent chat unsupported tool call shape`` -- the model
replied, and the codec only accepted one exact spelling of a tool call. These
drive the actual writer -> router -> provider -> codec -> journal -> engine
tool -> continuation composition with a synthetic remote wire, once per
spelling the chat-completions wire (and routers normalizing to it) emits.
Nothing here is a per-vendor or per-model branch.
"""

import json
import logging

import pytest

from tests import test_interactive_http_agent as composed
from tests.inference_usage_helpers import accounting_resolver
from tinyassets.providers import agent_chat_codec as codec
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.providers.protocol_encoders import ProtocolDecodeError

ARGS = {"target": "status"}
NAMES = frozenset({"read_graph"})

# The real writer/router/provider/journal/engine-client composition.
rig = composed.rig
reader = composed.reader
served = composed.served
agent = composed.agent
run = composed.run


def _call(**overrides):
    item = {"id": "wire-1", "type": "function",
            "function": {"name": "read_graph", "arguments": json.dumps(ARGS)}}
    item.update(overrides)
    return item


def _fn(**overrides):
    return {"name": "read_graph", "arguments": json.dumps(ARGS), **overrides}


def _body(message, finish="tool_calls"):
    return json.dumps({"model": "free-model", "choices": [
        {"message": {"role": "assistant", "content": None, **message}, "finish_reason": finish},
    ]})


def _sse(*chunks):
    lines = [": OPENROUTER PROCESSING", ""]
    for chunk in chunks:
        lines += ["data: " + json.dumps({"object": "chat.completion.chunk",
                                         "model": "free-model", **chunk}), ""]
    return "\n".join(lines + ["data: [DONE]", ""])


def _delta(delta, finish=None):
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


STREAMED = _sse(
    _delta({"role": "assistant", "content": ""}),
    _delta({"tool_calls": [{"index": 0, "id": "stream-1", "type": "function",
                            "function": {"name": "read_graph", "arguments": ""}}]}),
    _delta({"tool_calls": [{"index": 0, "function": {"arguments": '{"tar'}}]}),
    _delta({"tool_calls": [{"index": 0, "function": {"arguments": 'get": "status"}'}}]}),
    _delta({}, finish="tool_calls"),
)

#: name -> (first wire body, arguments the engine tool must receive)
VARIANTS = {
    "arguments_as_json_string": (_body({"tool_calls": [_call()]}), ARGS),
    "arguments_as_object": (
        _body({"tool_calls": [_call(function=_fn(arguments=ARGS))]}), ARGS),
    "arguments_empty_string": (_body({"tool_calls": [_call(function=_fn(arguments=""))]}), {}),
    "arguments_missing": (_body({"tool_calls": [_call(function={"name": "read_graph"})]}), {}),
    "id_missing": (_body({"tool_calls": [
        {"type": "function", "function": _fn()}]}), ARGS),
    "id_empty": (_body({"tool_calls": [_call(id="")]}), ARGS),
    "type_missing": (_body({"tool_calls": [{"id": "wire-1", "function": _fn()}]}), ARGS),
    "index_key_present": (_body({"tool_calls": [_call(index=0)]}), ARGS),
    "content_beside_tool_calls": (
        _body({"content": "Let me look that up.", "tool_calls": [_call()]}), ARGS),
    "streamed_deltas_by_index": (STREAMED, ARGS),
    "finish_tool_calls": (_body({"tool_calls": [_call()]}, finish="tool_calls"), ARGS),
    "finish_stop": (_body({"tool_calls": [_call()]}, finish="stop"), ARGS),
    "finish_null": (_body({"tool_calls": [_call()]}, finish=None), ARGS),
    "legacy_function_call": (
        _body({"function_call": _fn()}, finish="function_call"), ARGS),
}

FINAL = _body({"content": "finished exact answer"}, finish="stop")


def _wire(agent, monkeypatch, first, *, times=1):
    """Replace only the remote bytes; every local layer stays real.

    ``first`` answers the first ``times`` requests, ``FINAL`` the rest: a turn
    retries an unreadable reply, so a persistent one must be sent persistently.
    """
    class Proxy:
        def close(self):
            pass

        def request(self, verb, document):
            assert agent.latest().state == "inference_started"
            agent.wires.append((verb, document))
            return {"status": 200, "body": first if len(agent.wires) <= times else FINAL}

    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy",
                        accounting_resolver(lambda *a, **k: Proxy()))


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_each_standard_spelling_runs_the_tool_and_continues(agent, monkeypatch, variant):
    first, expected_args = VARIANTS[variant]
    _wire(agent, monkeypatch, first)
    assert run(agent) == "finished exact answer"
    assert agent.tools == [("read_graph", expected_args)]
    assert agent.latest().state == "completed"
    # The continuation replays ONE canonical call whose id the result answers.
    messages = agent.wires[-1][1]["body"]["messages"]
    assistant, result = messages[-2], messages[-1]
    (call,) = assistant["tool_calls"]
    assert set(call) == {"id", "type", "function"} and call["type"] == "function"
    assert set(call["function"]) == {"name", "arguments"}
    assert json.loads(call["function"]["arguments"]) == expected_args
    assert call["id"].strip() and result["tool_call_id"] == call["id"]
    assert "function_call" not in assistant


def test_synthesized_ids_are_stable_and_distinct_within_a_batch():
    batch = [{"function": _fn()}, {"function": _fn(arguments="{}")}, {"function": _fn()}]
    first = codec._normalize_calls(batch)
    assert first == codec._normalize_calls(batch)
    ids = [item["id"] for item in first]
    assert len(set(ids)) == 3 and all(i.startswith("call_") for i in ids)
    # Idempotent: a stored continuation normalizes to itself.
    assert codec._normalize_calls(first) == first


def test_streamed_calls_without_index_split_on_a_new_id():
    body = _sse(
        _delta({"tool_calls": [{"id": "a", "function": {"name": "read_graph",
                                                        "arguments": "{}"}}]}),
        _delta({"tool_calls": [{"id": "b", "function": {"name": "read_graph",
                                                        "arguments": '{"x":'}}]}),
        _delta({"tool_calls": [{"function": {"arguments": "1}"}}]}, finish="tool_calls"),
    )
    reply = codec.decode_openai_chat_agent(
        codec.fold_chat_stream(body), source_ref="s", requested_model="m", tool_names=NAMES,
    )
    assert [(r.call_id, r.arguments()) for r in reply.tool_requests] == [
        ("a", {}), ("b", {"x": 1}),
    ]


def _cut(*chunks):
    """A stream that ends at EOF: no finish_reason and no ``[DONE]``."""
    return "\n".join("data: " + json.dumps(chunk) + "\n" for chunk in chunks)


NAME_ONLY_THEN_EOF = _cut(
    _delta({"tool_calls": [{"index": 0, "id": "cut-1", "type": "function",
                            "function": {"name": "write_page"}}]}),
)


def test_review_probe_a_cut_off_name_only_stream_never_becomes_a_tool_request():
    """Round-1 probe, exactly: one chunk carrying only a name, then EOF."""
    with pytest.raises(ProtocolDecodeError, match="incomplete"):
        codec.decode_openai_chat_agent(
            codec.fold_chat_stream(NAME_ONLY_THEN_EOF), source_ref="s", requested_model="m",
            tool_names=frozenset({"write_page"}),
        )


@pytest.mark.parametrize("ending", ["finish", "done"])
def test_streamed_blank_arguments_are_not_defaulted_even_when_complete(ending):
    """A fold that produced no argument text did not receive a call's arguments;
    only the non-streamed documented spellings may default to ``{}``."""
    chunks = [_delta({"tool_calls": [{"index": 0, "id": "a", "type": "function",
                                      "function": {"name": "write_page"}}]},
                     finish="tool_calls" if ending == "finish" else None)]
    body = _sse(*chunks) if ending == "done" else _cut(*chunks)
    with pytest.raises(ProtocolDecodeError, match="incomplete"):
        codec.fold_chat_stream(body)


CUT_WRITE = _cut(
    _delta({"tool_calls": [{"index": 0, "id": "cut-1", "type": "function",
                            "function": {"name": "write_graph"}}]}),
)


def test_a_cut_off_stream_runs_no_tool_through_the_real_path(agent, monkeypatch):
    _wire(agent, monkeypatch, CUT_WRITE, times=99)
    with pytest.raises(Exception) as caught:
        run(agent)
    assert agent.tools == []  # engine.call never ran, on any retry
    details = " ".join(str(a.detail) for a in caught.value.attempts)
    assert "event stream incomplete" in details


def test_a_cut_off_stream_once_is_retried_and_its_half_call_never_runs(agent, monkeypatch):
    """A stream cut mid-call is a slip: the retry answers, the cut call stays unrun."""
    _wire(agent, monkeypatch, CUT_WRITE)
    assert run(agent) == "finished exact answer"
    assert agent.tools == []
    assert [r.state for r in agent.latest().rounds] == ["failed", "received"]


def test_a_complete_stream_ending_at_done_without_finish_still_runs():
    reply = codec.decode_openai_chat_agent(
        codec.fold_chat_stream(_sse(_delta({"tool_calls": [{
            "index": 0, "id": "a", "type": "function",
            "function": {"name": "read_graph", "arguments": "{}"}}]}))),
        source_ref="s", requested_model="m", tool_names=NAMES,
    )
    assert [(r.call_id, r.arguments()) for r in reply.tool_requests] == [("a", {})]


def test_streamed_error_chunk_is_a_response_error_not_an_answer():
    body = _sse({"error": {"message": "upstream private detail"}})
    with pytest.raises(ProtocolDecodeError, match="response unavailable"):
        codec.decode_openai_chat_agent(
            codec.fold_chat_stream(body), source_ref="s", requested_model="m", tool_names=NAMES,
        )


# ------------------------------------------------------------ still unsupported


UNKNOWN_ARGS = "private argument value 9f3c"
UNKNOWN = _body({"content": "private content 7a1e", "tool_calls": [
    _call(function=_fn(arguments=[UNKNOWN_ARGS]))]})


def test_an_unsupported_shape_fails_loudly_with_structure_only(agent, monkeypatch, caplog):
    import tinyassets.universe_server as us

    _wire(agent, monkeypatch, UNKNOWN, times=99)
    with pytest.raises(Exception) as caught:
        run(agent)
    exc = caught.value
    text = str(exc) + " ".join(str(getattr(a, "detail", "")) for a in exc.attempts or [])
    assert "tool_calls[0]={id:str,type:str,function:{name:str,arguments:list[1]}}" in text
    assert UNKNOWN_ARGS not in text and "private content" not in text
    assert agent.tools == []  # nothing dispatched on an unreadable reply

    record = us._served_failure_record(exc)
    assert (record.code, record.stage, record.effects) == (
        "provider_unreadable_reply", "model_reply", "none",
    )
    assert record.ref == agent.latest().turn_id
    with caplog.at_level(logging.WARNING, logger="universe_server"):
        us._record_served_failure("u-x", exc, ref=record.ref)
    line = next(r for r in caplog.records if "served turn failed" in r.getMessage())
    assert line.levelno == logging.WARNING
    assert f"ref={record.ref}" in line.getMessage()
    assert "arguments:list[1]" in line.getMessage()
    assert UNKNOWN_ARGS not in caplog.text


def test_structure_reports_key_names_and_types_never_values():
    with pytest.raises(ProtocolDecodeError) as caught:
        codec._normalize_calls([{"id": 7, "function": {"name": "read_graph",
                                                       "arguments": 12.5, "sk-secret": True}}])
    message = str(caught.value)
    assert "arguments:float" in message and "sk-secret:bool" in message
    assert "12.5" not in message and len(message) < 200


# --------------------------------------------------------------- the notice


def test_protocol_error_notice_names_the_format_and_does_not_cry_actions(agent, monkeypatch):
    import tinyassets.universe_server as us

    _wire(agent, monkeypatch, UNKNOWN, times=99)
    with pytest.raises(Exception) as caught:
        run(agent)
    notice = us._served_failure_notice(caught.value)
    assert "could not identify why" not in notice
    assert "sent a reply this command center could not read" in notice
    assert "asking it to continue usually works" in notice
    assert "Nothing ran." in notice
    assert "Actions may already have occurred" not in notice
    assert "may already have occurred" not in notice.lower()


def test_a_universe_with_no_model_is_told_to_connect_one_and_nothing_ran():
    import tinyassets.universe_server as us
    from tinyassets.conversation_failure import failure_notice
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.router import _CONNECT_PROVIDER_MESSAGE

    record = us._served_failure_record(ProviderAuthorityHeldError(_CONNECT_PROVIDER_MESSAGE))
    assert (record.code, record.stage, record.effects) == (
        "setup_required", "before_send", "none",
    )
    notice = failure_notice(record)
    assert "no model it can use" in notice and "connect a model" in notice
    # Nothing is named when nothing accepted is held: the plain connect refusal.
    assert record.provider_detail == ""
    assert "may already have occurred" not in notice.lower()


def test_actions_caution_appears_only_when_the_ledger_says_something_ran(agent, monkeypatch):
    import tinyassets.universe_server as us
    from tinyassets import engine_tool_client

    agent.fail_tool = True  # the tool is dispatched and its outcome is unknown
    _wire(agent, monkeypatch, VARIANTS["arguments_as_json_string"][0])
    with pytest.raises(engine_tool_client.EngineToolError) as caught:
        run(agent)
    record = us._served_failure_record(caught.value)
    assert (record.stage, record.effects) == ("tool", "unknown")
    assert "may already have occurred" in us._served_failure_notice(caught.value, record)


@pytest.mark.parametrize("root", ["/Users/", "/Volumes/", "/private/", "C:\\Users\\"])
def test_paths_are_redacted_before_the_detail_is_clipped(root):
    import tinyassets.universe_server as us

    # The clip keeps the head and the last ~95 characters: a long path at the
    # end loses its root to the clip, so clipping first would let
    # ".../owner-name/..." through unrecognized.
    detail = "x" * 150 + " " + root + "a" * 120 + "/owner-name/secret.txt"
    shown = us._served_failure_record(RuntimeError(detail)).provider_detail
    assert "owner-name" not in shown and "<path>" in shown


def test_history_re_renders_the_same_composed_notice(tmp_path):
    from tinyassets import conversation_store as store
    from tinyassets.conversation_failure import failure_notice, turn_failure

    record = turn_failure(
        "provider_protocol_error", stage="model_reply", effects="none",
        provider_detail="agent chat unsupported tool call shape", ref="0123abcd",
    )
    assert store.record_failure(tmp_path, "principal:a", "hi! what can you do?", record)
    row = store.load_recent_readonly(tmp_path, "principal:a")[-1]
    assert row.failure == record
    assert row.text == failure_notice(record)
    assert "Ref: 0123abcd" in row.text and "Nothing ran." in row.text


def test_app_adds_no_caution_when_the_record_says_nothing_ran(tmp_path):
    from tests.test_onboarding_app import _run_app
    from tinyassets.conversation_failure import failure_notice, normalize_turn_failure, turn_failure

    record = turn_failure("provider_protocol_error", stage="model_reply", effects="none",
                          ref="0123abcd")
    notice = failure_notice(record)
    payload = {"error": notice, "turn_failure": normalize_turn_failure(record),
               "failure_notice": notice, "history_saved": True}
    result = _run_app(tmp_path, {"kind": "send", "message": "hi! what can you do?",
                                 "payload": payload, "expectFailure": True})
    text = result["messages"][-1]["text"]
    assert text.startswith(notice)
    assert "Check progress before sending again" not in text
