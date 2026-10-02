"""A free model's reply that fails in flight is retried, not the end of a build.

Live, the free-only account (``u-01ky3zh1arr8qth8jee7zx63pq``):

* 2026-09-30, turns ``c7d6279d`` and ``f3617ca3``: nemotron served 19 and 12
  good tool rounds, then OpenRouter answered HTTP 200 with an ``error`` object
  in place of the reply ("agent chat response unavailable").
* 2026-10-02, turn ``ef0c10e0`` (the Office command-center build): seven good
  rounds of reads, writes and bash, then a 200 whose one choice carried an
  ``error`` ("agent chat choice unavailable").

Each time the owner read "the connected model replied in a format this command
center could not read" and the turn ended: no path retried, the router cooled
the whole OpenRouter connection (every sibling free model with it), and every
model in the order had already been visited, so only a retry of the SAME model
could have saved it.

Two days earlier (turn ``8dc8ada5``) a turn died on our own context measurement
with nothing larger in the order; ``compact_history`` is that case.

Every integration test drives the real writer, router, ``ApiKeyHttpProvider``,
coordinator and journal; only the wire is synthetic.
"""

import json

import pytest
from mcp.types import CallToolResult, TextContent

from tests import test_agent_chat_codec as codec_tests
from tests import test_interactive_http_agent as integration
from tests import test_provider_model_refusal as refusal
from tests.test_agent_chat_codec import definitions
from tinyassets.agent_turn_coordinator import AgentTurnCoordinator
from tinyassets.exceptions import AllProvidersExhaustedError
from tinyassets.providers import agent_chat_codec as codec

rig = integration.rig
reader = integration.reader
served = integration.served
agent = integration.agent

#: OpenRouter's mid-generation failure, shape as documented for a 200 response:
#: the choice carries ``error`` and ``finish_reason: "error"``. The words are
#: synthetic -- the live bodies were discarded, which is part of the bug.
CHOICE_ERROR = json.dumps({
    "id": "gen-synthetic", "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "choices": [{
        "index": 0, "finish_reason": "error",
        "message": {"role": "assistant", "content": ""},
        "error": {"code": 502, "message": "Synthetic upstream: provider returned error"},
    }],
})
#: The same failure as a whole-body ``error`` on a 200 (turns c7d6279d, f3617ca3).
BODY_ERROR = json.dumps({
    "error": {"code": 502, "message": "Synthetic upstream: network connection lost"},
    "user_id": "user_synthetic",
})
#: And as it arrives when the source streams: an SSE chunk carrying ``error``.
STREAM_ERROR = "\n".join([
    'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}', "",
    'data: {"error":{"code":502,"message":"Synthetic upstream: stream reset"},'
    '"choices":[{"index":0,"delta":{},"finish_reason":"error"}]}', "",
    "data: [DONE]", "",
])
EMPTY_CHOICES = json.dumps({"model": "m", "choices": []})
#: 200, finish_reason "length", empty content: a cold start or a reasoning
#: model that spent max_tokens thinking ("succeeded with nothing").
EMPTY_LENGTH = json.dumps({"model": "m", "choices": [{
    "finish_reason": "length", "message": {"role": "assistant", "content": ""},
}]})
CUT_TOOL_BATCH = json.dumps({"model": "m", "choices": [{
    "finish_reason": "length",
    "message": {"role": "assistant", "content": None, "tool_calls": [{
        "id": "c1", "type": "function",
        "function": {"name": "read_graph", "arguments": "{}"},
    }]},
}]})


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(AgentTurnCoordinator, "BAD_REPLY_BACKOFF_S", (0.0, 0.0, 0.0))


def _fail(agent, wires, body):
    """Answer each of ``wires`` (1-based request numbers) with HTTP 200 + ``body``."""
    for number in wires:
        agent.capacity_failures[number] = 200
        agent.failure_bodies[number] = body


def _models(agent):
    return [wire[1]["body"]["model"] for wire in agent.wires]


# --------------------------------------------------------------------------
# The live shapes: one failed reply after real work, and the turn finishes.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("body", [CHOICE_ERROR, BODY_ERROR, STREAM_ERROR],
                         ids=["choice_error", "body_error", "stream_error"])
def test_an_in_band_source_error_after_real_work_is_retried_and_the_turn_finishes(
    agent, body,
):
    _fail(agent, [2], body)
    assert integration.run(agent) == "finished exact answer"
    turn = agent.latest()
    assert turn.state == "completed"
    assert [round.state for round in turn.rounds] == ["received", "failed", "received"]
    # The first round's tool ran once; the retry carried its result, not a re-run.
    assert len(agent.tools) == 1
    messages = agent.wires[-1][1]["body"]["messages"]
    assert json.loads(messages[-1]["content"])["content"][0]["text"] == "exact result 🪐"
    # Same model: the order is not needed for a one-off upstream error.
    assert len(set(_models(agent))) == 1


@pytest.mark.parametrize(
    "body", [EMPTY_CHOICES, CUT_TOOL_BATCH, EMPTY_LENGTH, "", "<html>bad gateway</html>"],
    ids=["empty_choices", "length_cut_tool_batch", "empty_length", "empty_body", "not_json"],
)
def test_an_unreadable_2xx_reply_is_retried_and_its_calls_never_run(agent, body):
    agent.requested_rounds = 2  # the retry asks for the tool; the next answers
    _fail(agent, [1], body)
    assert integration.run(agent) == "finished exact answer"
    assert [round.state for round in agent.latest().rounds] == [
        "failed", "received", "received",
    ]
    # Only the retried round's one call ran; nothing from the unreadable reply.
    assert len(agent.tools) == 1


def test_a_persistent_in_band_error_ends_honestly_in_the_sources_words(agent):
    _fail(agent, range(2, 20), CHOICE_ERROR)
    with pytest.raises(AllProvidersExhaustedError) as error:
        integration.run(agent)
    # One same-model retry; no order to move along, so the turn reports.
    assert len(agent.wires) == 3 and len(agent.tools) == 1
    attempts = error.value.attempts
    assert [a.failure_class for a in attempts] == ["provider_reply_error"] * 2
    assert "provider returned error" in attempts[-1].detail
    assert "code 502" in attempts[-1].detail

    record, notice = refusal._record(error.value)
    assert record.code == "provider_reply_error"
    assert record.stage == "model_reply"
    assert record.effects == "some"  # the first round's tool completed
    assert "provider returned error" in record.provider_detail
    assert "format" not in notice
    assert "reported an error partway through its reply" in notice
    assert "asking it to continue usually works" in notice


def test_a_bad_reply_does_not_cool_the_connection(agent):
    """Cooling the source skipped every sibling free model on the same key."""
    _fail(agent, range(2, 20), BODY_ERROR)
    with pytest.raises(AllProvidersExhaustedError):
        integration.run(agent)
    provider = agent.served.context.model_selection.connection_id
    assert agent.served.router._quota.cooldown_remaining(provider) == 0


def test_an_unrecognized_status_is_the_request_refused_and_is_not_retried(agent):
    """A 4xx the source rejected would only be rejected again."""
    agent.capacity_failures[1] = 418
    with pytest.raises(AllProvidersExhaustedError) as error:
        integration.run(agent)
    assert len(agent.wires) == 1
    assert error.value.attempts[-1].failure_class == "provider_protocol_error"


# --------------------------------------------------------------------------
# Failover: same model once, then the next model the owner accepted.
# --------------------------------------------------------------------------


def test_a_model_failing_twice_moves_to_the_next_accepted_model(agent, monkeypatch):
    refusal._order(agent, monkeypatch, ["lab/answering:free"])
    _fail(agent, [2, 3], CHOICE_ERROR)
    assert integration.run(agent) == "finished exact answer"
    models = _models(agent)
    assert models[1] == models[2] == models[0]  # the one same-model retry
    assert models[3] == "lab/answering:free"
    assert len(agent.tools) == 1
    # Same owner grant, same zero ceilings on every attempt.
    assert all(
        set(wire[1]["body"]["provider"]["max_price"].values()) == {"0"}
        for wire in agent.wires
    )


def test_bad_reply_retries_are_bounded_per_turn(agent, monkeypatch):
    refusal._order(agent, monkeypatch, [f"lab/model-{index}:free" for index in range(6)])
    _fail(agent, range(2, 40), CHOICE_ERROR)
    with pytest.raises(AllProvidersExhaustedError) as error:
        integration.run(agent)
    retries = AgentTurnCoordinator.MAX_BAD_REPLY_RETRIES
    assert len(agent.wires) == 2 + retries
    assert len(error.value.attempts) == 1 + retries
    assert len(agent.tools) == 1


def test_a_native_agent_round_is_never_retried_this_way():
    """A native agent's failure can follow real work it did on its own."""
    from types import SimpleNamespace

    turn = AgentTurnCoordinator.__new__(AgentTurnCoordinator)
    turn.execution_kind = "native_agent"
    turn.adapter = SimpleNamespace(relaunches_same_model=True)
    turn.turn = SimpleNamespace(state="held_transport")
    turn.bad_reply_retries = 0
    exc = AllProvidersExhaustedError("x", attempts=[
        SimpleNamespace(failure_class="provider_reply_error", side_effect_state="unknown"),
    ])
    assert turn._next_after_bad_reply(exc) is False


def test_a_bad_reply_mixed_with_another_class_is_not_this_paths():
    from types import SimpleNamespace

    turn = AgentTurnCoordinator.__new__(AgentTurnCoordinator)
    turn.execution_kind = "engine_inference"
    turn.adapter = SimpleNamespace(relaunches_same_model=True)
    turn.turn = SimpleNamespace(state="held_transport")
    turn.bad_reply_retries = 0
    exc = AllProvidersExhaustedError("x", attempts=[
        SimpleNamespace(failure_class="provider_reply_error", side_effect_state="unknown"),
        SimpleNamespace(failure_class="auth_invalid", side_effect_state="none"),
    ])
    assert turn._next_after_bad_reply(exc) is False


def _bare_turn(adapter):
    from types import SimpleNamespace

    turn = AgentTurnCoordinator.__new__(AgentTurnCoordinator)
    turn.execution_kind = "engine_inference"
    turn.adapter = adapter
    turn.plan = None
    turn.turn = SimpleNamespace(state="held_transport", rounds=())
    turn.context = SimpleNamespace(model_selection="model-a")
    turn.bad_reply_retries, turn.bad_reply_models, turn.spent_attempts = 0, set(), []
    turn.interrupt = None
    return turn


def test_a_workflow_node_is_not_retried_on_the_same_model():
    """Its failed round settled the one launch carrier it holds (Codex, 2026-10-02)."""
    from types import SimpleNamespace

    exc = AllProvidersExhaustedError("x", attempts=[
        SimpleNamespace(failure_class="provider_reply_error", side_effect_state="unknown"),
    ])
    assert _bare_turn(SimpleNamespace())._next_after_bad_reply(exc) is False
    assert _bare_turn(SimpleNamespace())._compact_to_fit() is False
    assert _bare_turn(SimpleNamespace(relaunches_same_model=True))._next_after_bad_reply(exc)


def test_the_served_adapters_say_they_relaunch_each_round():
    from tinyassets.interactive_http_agent import ServedChatAgentAdapter
    from tinyassets.workflow_agent import WorkAgentAdapter

    assert ServedChatAgentAdapter.relaunches_same_model is True
    assert not hasattr(WorkAgentAdapter, "relaunches_same_model")


def test_a_slow_reply_is_never_retried_or_moved_off(agent, monkeypatch):
    """Founder, 2026-10-02: "if the model response is just slow your skipping it
    and then that call is used up for the user". A non-streamed reply that
    outruns the broker's ceiling ends the turn honestly; no second request is
    spent on the same slowness, on this model or another."""
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
    from tinyassets.storage.outbound_connections import OutboundDeadlineExceeded

    refusal._order(agent, monkeypatch, ["lab/answering:free"])
    real_resolve = ApiKeyHttpProvider._resolve_proxy

    def resolve(self, **kwargs):
        proxy = real_resolve(self, **kwargs)
        inner = proxy.request

        def request(verb, document):
            if len(agent.wires) == 1:  # the second request: after the tool round
                agent.wires.append((verb, document))
                raise OutboundDeadlineExceeded("outbound request exceeded its time budget")
            return inner(verb, document)

        proxy.request = request
        return proxy

    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy", resolve)
    with pytest.raises(AllProvidersExhaustedError) as error:
        integration.run(agent)
    assert len(agent.wires) == 2 and len(agent.tools) == 1
    record, notice = refusal._record(error.value)
    assert record.code == "provider_reply_timeout"
    assert record.requests == 2
    assert "This turn sent 2 requests to your model" in notice


def test_a_reply_timeout_names_the_budget_that_actually_ended_it(agent):
    """Live: "within its reply budget (2591705s)" -- the turn's remaining time, not
    the broker's 600s ceiling that ended the request."""
    from dataclasses import replace

    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
    from tinyassets.storage.outbound_connections import (
        INFERENCE_MAX_SECONDS,
        OutboundDeadlineExceeded,
    )

    agent.config = replace(agent.config, absolute_cap_s=2_591_705)
    real_resolve = ApiKeyHttpProvider._resolve_proxy

    def resolve(self, **kwargs):
        proxy = real_resolve(self, **kwargs)

        def request(verb, document):
            agent.wires.append((verb, document))
            raise OutboundDeadlineExceeded("outbound request exceeded its time budget")

        proxy.request = request
        return proxy

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ApiKeyHttpProvider, "_resolve_proxy", resolve)
        with pytest.raises(AllProvidersExhaustedError) as error:
            integration.run(agent)
    detail = error.value.attempts[-1].detail
    assert f"({int(INFERENCE_MAX_SECONDS)}s)" in detail and "2591" not in detail


# --------------------------------------------------------------------------
# Context: render older tool results shorter when no larger model exists.
# --------------------------------------------------------------------------


def _history(rounds=3, result_text="r" * 9000, argument="a" * 6000):
    reply = codec_tests.decode(codec_tests.response(
        [codec_tests.call("one", "read_graph", json.dumps({"topic": argument}))],
        reasoning="private scratch " * 50,
    ))
    outcome = codec.ToolOutcome("one", json.dumps({
        "content": [{"type": "text", "text": result_text}],
        "structuredContent": None, "isError": False,
    }), False)
    return tuple(
        codec.CapturedToolRound(round=codec.ToolRound(reply, (outcome,)), tools=definitions())
        for _ in range(rounds)
    )


def test_level_zero_is_the_history_unchanged():
    history = _history()
    assert codec.compact_history(history, 0) == history


def test_each_level_renders_smaller_and_says_what_it_left_out():
    history = _history()
    sizes = [codec.history_size(codec.compact_history(history, level)) for level in range(4)]
    assert sizes == sorted(sizes, reverse=True) and len(set(sizes)) == 4
    clipped = codec.compact_history(history, 3)[0].round
    text = json.loads(clipped.outcomes[0].result_json)["content"][0]["text"]
    assert "omitted here to fit the model's context window" in text
    assert "call the tool again" in text
    assert "it ran with them in full" in clipped.reply.tool_requests[0].arguments()["topic"]
    # Older reasoning is dropped; the call batch is still exact and correlated.
    assert "reasoning" not in json.loads(clipped.reply.continuation_json)


def test_level_one_keeps_the_latest_rounds_whole():
    history = _history()
    compacted = codec.compact_history(history, 1)
    assert compacted[1:] == history[1:]
    assert compacted[0] != history[0]


@pytest.mark.parametrize("level", [1, 2, 3])
def test_a_compacted_history_still_validates_as_complete_correlated_batches(level):
    from tests.test_agent_chat_codec import MODEL, SOURCE

    body = codec.build_portable_agent_body(
        prompt="p", system="s", source_ref=SOURCE, model=MODEL, tools=definitions(),
        history=codec.compact_history(_history(), level),
    )
    codec.validate_agent_body(body)
    calls = [m for m in body["messages"] if m.get("tool_calls")]
    results = [m for m in body["messages"] if m["role"] == "tool"]
    assert len(calls) == len(results) == 3
    assert all(r["tool_call_id"] == c["tool_calls"][0]["id"] for c, r in zip(calls, results))


def _big_results(monkeypatch, size):
    from tinyassets import engine_tool_client

    original = engine_tool_client._make_client

    def make(*args):
        client = original(*args)
        call = client.call_tool_mcp

        async def big(name, arguments):
            await call(name, arguments)
            return CallToolResult(content=[TextContent(type="text", text="y" * size)])

        client.call_tool_mcp = big
        return client

    monkeypatch.setattr(engine_tool_client, "_make_client", make)


def test_a_turn_outgrowing_its_only_model_compacts_and_finishes(agent, monkeypatch):
    """Turn 8dc8ada5's shape: tool output overflowed, nothing larger accepted."""
    selected = agent.served.context.model_selection.model_id
    # Measure what the first request costs, then give the model just enough
    # window for it plus a compacted history -- never the whole 120k result.
    assert integration.run(agent) == "finished exact answer"
    first = len(json.dumps(agent.wires[0][1]["body"]).encode("utf-8"))
    agent.wires.clear()
    agent.tools.clear()
    _big_results(monkeypatch, 120_000)
    refusal._order(agent, monkeypatch, [], contexts={selected: first + 1024 + 20_000})
    assert integration.run(agent) == "finished exact answer"
    assert len(agent.tools) == 1
    final = agent.wires[-1][1]["body"]["messages"][-1]["content"]
    assert "omitted here to fit the model's context window" in final
    assert len(final) < 2_000
    # The journal keeps the result whole: compaction changes only the rendering.
    with agent.journal._ledger.connection() as conn:
        stored = conn.execute(
            "SELECT result_json FROM agent_turn_tools ORDER BY rowid DESC"
        ).fetchone()[0]
    assert "y" * 120_000 in stored


def test_a_turn_too_large_even_compacted_still_reports_the_window(agent, monkeypatch):
    from tinyassets import universe_intelligence

    refusal._order(agent, monkeypatch, [], contexts={
        agent.served.context.model_selection.model_id: 32_000,
    })
    with pytest.raises(PermissionError) as error:
        universe_intelligence._call_writer(
            "x" * 150_000, system="exact system",
            universe_context=agent.served.context, config=agent.config,
        )
    assert not agent.wires
    record, _ = refusal._record(error.value)
    assert record.code == "context_window_exceeded"


def test_a_request_larger_in_bytes_than_the_window_fits_by_its_tokens(agent, monkeypatch):
    """Turn 8dc8ada5's root cause: JSON bytes compared against a TOKEN window.

    Production first rounds measured 3.86-4.07 bytes per reported token; a
    window smaller than the request's byte count still holds it and the answer.
    """
    selected = agent.served.context.model_selection.model_id
    assert integration.run(agent) == "finished exact answer"
    size = max(len(json.dumps(wire[1]["body"]).encode("utf-8")) for wire in agent.wires)
    agent.wires.clear()
    agent.tools.clear()
    refusal._order(agent, monkeypatch, [], contexts={selected: size - 1})
    assert integration.run(agent) == "finished exact answer"
    assert max(len(json.dumps(w[1]["body"]).encode("utf-8")) for w in agent.wires) >= size


def test_the_window_estimate_is_conservative_against_the_measured_ratio():
    from tinyassets.providers.agent_inference import CONTEXT_BYTES_PER_TOKEN

    # Below the lowest measured ratio (3.86), so the estimate over-counts tokens.
    assert CONTEXT_BYTES_PER_TOKEN < 3.86


def test_the_sources_words_are_scrubbed_whole_before_any_clip(agent):
    """Clipping first cut a secret's closing quote off and let its head through."""
    secret = "PRIVATE_TOKEN_ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    body = json.loads(CHOICE_ERROR)
    body["choices"][0]["error"]["message"] = "x" * 265 + ' {"api_key": "' + secret + '"}'
    _fail(agent, range(1, 20), json.dumps(body))
    with pytest.raises(AllProvidersExhaustedError) as error:
        integration.run(agent)
    record, notice = refusal._record(error.value)
    for text in [a.detail for a in error.value.attempts] + [record.provider_detail, notice]:
        assert "PRIVATE_TOKEN" not in text


def _dense_cases():
    import base64
    import random
    import textwrap

    rng = random.Random(7)
    blob = base64.b64encode(bytes(rng.getrandbits(8) for _ in range(30000))).decode()[:40000]
    return {
        "base64": blob,
        "base64_wrapped": "\n".join(textwrap.wrap(blob, 60)),
        "hex": "".join(rng.choice("0123456789abcdef") for _ in range(40000)),
        "minified_js": "for(let i=0;i<100;i++){a[i]=b[i]*2+c[i];}" * 1000,
        "cjk": "汉字测试内容，这是一个句子。" * 1500,
        "emoji": "🪐✨🚀 ok " * 3000,
    }


#: The larger of cl100k_base and o200k_base for each case's tool-result content,
#: measured offline with tiktoken 0.12 (not a runtime dependency).
MEASURED_TOKENS = {
    "base64": 28_830, "base64_wrapped": 29_626, "hex": 22_775,
    "minified_js": 21_027, "cjk": 19_527, "emoji": 27_027,
}


@pytest.mark.parametrize("case", sorted(MEASURED_TOKENS))
def test_dense_tool_output_is_not_underestimated(case):
    """Codex R2/R3: base64 (plain and wrapped) and minified JS were admitted into
    windows they overflow; escaped non-ASCII is counted by escape too."""
    from tinyassets.providers.agent_inference import estimate_tokens

    content = json.dumps({"content": [{"type": "text", "text": _dense_cases()[case]}],
                          "structuredContent": None, "isError": False}, ensure_ascii=False)
    wire = json.dumps({"messages": [{"role": "tool", "content": content}]}).encode()
    assert estimate_tokens(wire) >= MEASURED_TOKENS[case]


def test_prose_and_schemas_still_get_most_of_their_window():
    from tinyassets.providers.agent_inference import estimate_tokens

    prose = json.dumps({"messages": [{"role": "user", "content": "Build me a command "
                        "center themed on The Office, with a sales board. " * 400}]}).encode()
    estimate = estimate_tokens(prose)
    # Never below the measured 3.86-4.07 bytes per token, never back to bytes.
    assert len(prose) / 3.86 <= estimate <= len(prose) / 2.9


@pytest.mark.parametrize("finish,message", [
    ("length", {"role": "assistant", "content": "a partial answer"}),
    ("length", {"role": "assistant", "content": None, "refusal": "not this"}),
    ("content_filter", {"role": "assistant", "content": ""}),
], ids=["truncated_with_text", "refusal_at_length", "content_filter"])
def test_a_reply_with_something_in_it_is_not_retried_as_empty(agent, finish, message):
    """Only the empty "length" reply is a slip; these say something and stand."""
    _fail(agent, [1], json.dumps({"model": "m", "choices": [
        {"finish_reason": finish, "message": message},
    ]}))
    with pytest.raises(Exception):  # noqa: B017 - each ends the turn its own way
        integration.run(agent)
    assert len(agent.wires) == 1


# --------------------------------------------------------------------------
# Streaming: judged by inactivity, never by total time (founder, 2026-10-02).
# --------------------------------------------------------------------------

#: A stream that delivered some text and then went silent past the idle window;
#: the broker returns it as far as it got, marked ``stalled``.
STALLED_STREAM = "\n".join([
    'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":"Here is the "}}]}',
    "",
    'data: {"choices":[{"index":0,"delta":{"content":"sales board layout"}}]}',
    "",
])


def _stall(agent, wires):
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider

    real_resolve = ApiKeyHttpProvider._resolve_proxy

    def resolve(self, **kwargs):
        proxy = real_resolve(self, **kwargs)
        inner = proxy.request

        def request(verb, document):
            if len(agent.wires) + 1 in wires:
                agent.wires.append((verb, document))
                return {"status": 200, "headers": {}, "body": STALLED_STREAM, "stalled": True}
            return inner(verb, document)

        proxy.request = request
        return proxy

    return resolve


def test_an_agent_request_streams_and_asks_for_an_inactivity_window(agent):
    from tinyassets.providers.api_key_http_provider import DEFAULT_REPLY_IDLE_S

    integration.run(agent)
    for _verb, document in agent.wires:
        assert document["body"]["stream"] is True
        assert document["reply_idle_s"] == DEFAULT_REPLY_IDLE_S
        assert document["reply_budget_s"] > 0


def test_a_stalled_stream_is_asked_again_and_the_turn_finishes(agent, monkeypatch):
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider

    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy", _stall(agent, {2}))
    assert integration.run(agent) == "finished exact answer"
    assert [r.state for r in agent.latest().rounds] == ["received", "failed", "received"]
    assert len(agent.tools) == 1


def test_a_persistent_stall_keeps_what_the_model_wrote_and_says_what_it_cost(
    agent, monkeypatch,
):
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider

    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy", _stall(agent, set(range(2, 9))))
    with pytest.raises(AllProvidersExhaustedError) as error:
        integration.run(agent)
    assert len(agent.wires) == 3  # one tool round, one stall, one same-model retry
    record, notice = refusal._record(error.value)
    assert record.code == "provider_stalled" and record.stage == "model_reply"
    assert record.partial_text == "Here is the sales board layout"
    assert 'had written: "Here is the sales board layout"' in notice
    assert "This turn sent 3 requests to your model" in notice
    assert "slow reply is never cut off" in notice
    # The owner's model output is for the owner's notice, never a run record.
    assert all("partial_text" not in a.to_dict() for a in error.value.attempts)


def test_partial_text_survives_the_stored_record_round_trip(tmp_path):
    from tinyassets import conversation_store as store
    from tinyassets.conversation_failure import turn_failure

    record = turn_failure("provider_stalled", stage="model_reply", effects="some",
                          ref="0123abcd", requests=7, partial_text="汉字" * 2000)
    assert store.record_failure(tmp_path, "principal:a", "build it", record)
    row = store.load_recent_readonly(tmp_path, "principal:a")[-1]
    assert row.failure == record and row.failure.requests == 7
    assert row.failure.partial_text.startswith("...")



def test_a_worst_case_record_stays_readable():
    """Codex: every field at its bound used to pass 4096 and drop the record."""
    from tinyassets.conversation_failure import read_turn_failure, turn_failure

    record = turn_failure("provider_stalled", stage="model_reply", effects="some",
                          ref="a" * 64, requests=10000, provider_detail=chr(0x1F600) * 200,
                          partial_text=chr(0x1F600) * 1500)
    stored = json.dumps(record_dict(record))
    assert len(stored) <= 4096
    assert read_turn_failure("platform", stored) == record


def record_dict(record):
    from tinyassets.conversation_failure import normalize_turn_failure

    return normalize_turn_failure(record)


def test_the_journal_records_the_request_exactly_as_sent(agent):
    """Codex: ``stream`` was added after hashing, so the digest named another request."""
    import hashlib

    integration.run(agent)
    rounds = agent.latest().rounds
    for (_verb, document), previous in zip(agent.wires, rounds):
        assert document["body"]["stream"] is True
        sent = "sha256:" + hashlib.sha256(json.dumps(document["body"]).encode()).hexdigest()
        assert previous.candidate.request_digest == sent
