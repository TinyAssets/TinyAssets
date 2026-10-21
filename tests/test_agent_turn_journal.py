"""Synthetic durable progress: no provider or tool call, no execution authority."""


import pytest
from mcp.types import CallToolResult, TextContent

from tinyassets.providers.agent_chat_codec import decode_openai_chat_agent
from tinyassets.storage import agent_turn_records as records
from tinyassets.storage.agent_turn_journal import (
    AgentTurnJournal,
)


def candidate(source="owned:future", model="opaque-llm"):
    return records.RoundInput(
        source,
        model,
        records.dump(
            {
                "version": 1,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "tool",
                            "description": "exact 🪐",
                            "parameters": {"type": "object"},
                        },
                    },
                ],
            }
        ),
        "binding",
        "reservation",
        1,
        "sha256:" + "a" * 64,
        "sha256:" + "b" * 64,
    )


def reply(*, count=1, text=None, finish=None, refusal=None):
    message = {"role": "assistant", "content": text}
    if refusal is not None:
        message["refusal"] = refusal
    if count:
        message["tool_calls"] = [
            {
                "id": f"call{i}",
                "type": "function",
                "function": {
                    "name": "tool",
                    "arguments": '{ "i": ' + str(i) + " }",
                },
            }
            for i in range(count)
        ]
    return decode_openai_chat_agent(
        {
            "choices": [
                {"message": message, "finish_reason": finish or ("tool_calls" if count else "stop")}
            ],
            "usage": {"prompt_tokens": 10},
        },
        source_ref="owned:future",
        requested_model="opaque-llm",
        tool_names=frozenset({"tool"}),
    )


@pytest.fixture
def journal(tmp_path):
    from tinyassets.daemon_server import set_founder_home

    set_founder_home(tmp_path, founder_sub="owner", universe_id="home", platform_generated=True)
    return AgentTurnJournal(tmp_path)


def new(journal, *, owner="owner", universe="home"):
    return journal.create(owner, universe, prompt="exact\nuser 🪐", system="system\n")










def begin(journal, turn):
    return journal.begin_round(
        "owner", "home", turn.turn_id, expected_generation=turn.generation, candidate=candidate()
    ).snapshot


def receive(journal, turn, response=None):
    return journal.finish_inference(
        "owner",
        "home",
        turn.turn_id,
        expected_generation=turn.generation,
        ordinal=len(turn.rounds),
        reply=reply() if response is None else response,
    ).snapshot


def start(journal, turn, call=1):
    return journal.start_tool(
        "owner",
        "home",
        turn.turn_id,
        expected_generation=turn.generation,
        ordinal=len(turn.rounds),
        call_ordinal=call,
    )


def finish(journal, turn, call=1, **kwargs):
    return journal.finish_tool(
        "owner",
        "home",
        turn.turn_id,
        expected_generation=turn.generation,
        ordinal=len(turn.rounds),
        call_ordinal=call,
        request=turn.rounds[-1].tools[call - 1].request,
        **kwargs,
    )


def result(text="exact\nresult 🪐", error=False):
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent={"exact": [1, None]},
        isError=error,
    )


























































