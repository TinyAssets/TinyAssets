"""Stored version-one reply records load without a provider decoder."""

from dataclasses import asdict, replace

import pytest

from tests.test_agent_turn_journal import candidate
from tinyassets.providers import agent_chat_codec as codec
from tinyassets.storage import agent_turn_records as records


def observe(fn, *args, **kwargs):
    try:
        return ("ok", fn(*args, **kwargs))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return ("error", type(exc))


def decode(fn, body):
    return fn(
        body, source_ref="owned:future", requested_model="opaque-llm",
        tool_names=frozenset({"tool"}),
    )


def call(**overrides):
    return {"id": "batch-id", "type": "function", "function": {
        "name": "tool", "arguments": '{ "exact": "🪐", "n": 1 }',
    }, **overrides}


def seed_reply(*, tools=False, finish="stop", extra=None):
    body = {"choices": [{"finish_reason": finish, "message": {
        "role": "assistant", "content": "exact answer",
        **({"tool_calls": [call()]} if tools else {}), **(extra or {}),
    }}]}
    return decode(codec.decode_openai_chat_agent, body)


def _stored_legacy_row(**overrides):
    """A journal row exactly as origin/main (before 2026-09-24) persisted a
    legacy ``function_call`` reply: the call dropped from the saved message,
    the raw finish kept, the turn held as ``unknown``."""
    return records.dump({
        "version": 1, "stop": "unknown", "text": None, "refusal": None,
        "tool_requests": [],
        "continuation_json": '{"role":"assistant","content":null}',
        "dropped_fields": ["function_call"], "source_ref": "owned:future",
        "requested_model": "opaque-llm", "reported_model": "",
        "raw_finish_reason": "function_call", "input_tokens": None,
        "output_tokens": None, **overrides,
    })


@pytest.mark.parametrize("row", [
    _stored_legacy_row(),
    _stored_legacy_row(dropped_fields=["refusal", "function_call"]),
    _stored_legacy_row(continuation_json='{"role":"assistant","content":"answer"}',
                       raw_finish_reason="stop", text="answer"),
])
def test_review_probe_old_held_function_call_rows_still_load_held(row):
    reply = records.load_reply(row, candidate())
    assert (reply.stop, reply.tool_requests) == ("unknown", ())
    assert records.reply_json(reply, candidate()) == row


def test_record_loader_does_not_call_a_wire_decoder(monkeypatch):
    reply = seed_reply(tools=True)
    raw = records.dump({"version": 1, **asdict(reply)})

    def forbidden(*args, **kwargs):
        pytest.fail("journal validation called a provider response decoder")

    monkeypatch.setattr(codec, "decode_openai_chat_agent", forbidden)
    assert records.load_reply(raw, candidate()) == reply
    assert records.reply_json(reply, candidate()) == raw


@pytest.mark.parametrize("source,model", [("", "m"), ("source", ""), ("x\n", "m")])
def test_invalid_captured_identity_is_not_bypassed(source, model):
    reply = replace(seed_reply(), source_ref=source, requested_model=model)
    raw = records.dump({"version": 1, **asdict(reply)})
    context = candidate(source, model)
    assert observe(records.load_reply, raw, context)[0] == "error"
