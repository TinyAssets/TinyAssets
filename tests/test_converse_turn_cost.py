"""What one served founder turn actually COSTS, measured, not guessed.

Live 2026-09-25 (production, free-only account, a free source): every reply to a
trivial message took 1-2 minutes of the founder's wall clock -- "what's a good
name for a cat?" sent 6:12, answered 6:14. This module drives the REAL converse
path (persona assembly -> router -> the HTTP executor -> the agent tool loop ->
``extract_learning``) against a synthetic wire that records each model
round-trip, so the cost is an executable fact instead of a hypothesis.

What the measurement showed, and what these tests pin:

1. A served turn is an AGENTIC LOOP. One tool step is not one extra call, it is
   one extra WHOLE round-trip that re-sends the entire system prompt AND the
   engine tool-schema block. On a slow source each round-trip is tens of seconds,
   so round-trip COUNT is the turn's dominant cost.
2. The engine tool-definition block is ~63 KB on the current served set, and one
   tool's manual is ~61% of it. It rides on every round of every turn.
   ``test_engine_tool_description_budget_does_not_grow`` is the ratchet.
3. ``extract_learning`` is a THIRD round-trip that runs after the reply text
   already exists but BEFORE ``converse`` returns it, so the founder waits for
   platform bookkeeping. Pinned here as the cost it is; it cannot move out of the
   request without a provider lease that outlives it (see
   ``docs/concerns/2026-09-25-converse-turn-round-trip-cost.md``).
4. The grounding files are quoted verbatim in the system prompt, so a turn that
   re-fetches one pays a full round-trip for text it is already holding. The
   prompt now says so.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from tests import test_interactive_http_agent as integration
from tests.inference_usage_helpers import accounting_resolver
from tinyassets import daemon_server, engine_tool_client, universe_intelligence
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS

#: Captured at import: the `rig` fixture replaces this with a raising guard
#: ("discovery must not depend on a powered agent"), and converse's
#: model-preference step legitimately reads it.
_GET_FOUNDER_HOME = daemon_server.get_founder_home

rig = integration.rig
reader = integration.reader
served = integration.served
agent = integration.agent

#: Synthetic per-round-trip source latency. Small enough to keep the suite fast,
#: large enough that "the founder waited for one more round-trip" is measurable
#: rather than inferred from timing noise.
WIRE_LATENCY_S = 0.20

#: The final answer this rig's source returns.
FINAL_REPLY = "Nebula is a good cat name."

#: Ceiling on the engine tool DESCRIPTIONS re-sent on every round of every served
#: founder turn. Not a target -- a ratchet. This text is re-transmitted per
#: round-trip, so growing it makes every turn on every account slower. Adding real
#: guidance for an agent is legitimate; doing it without noticing the per-round
#: bill is what this catches.
#:
#: 2026-09-25: 56,328 chars across the 14 served handles, 38,513 of them one
#: handle's manual; ratchet 58,000.
#: 2026-09-26: 28,562 after that manual moved into handbook chapters
#: (`openspec/changes/engine-tool-manual-on-demand/`), so the block fell 63,383 ->
#: 34,361 B, ~7.3k fewer tokens on every round-trip. (28,074 before the PR #4000
#: review asked for the base64 rule to stay resident -- 488 chars back, because
#: skipping it produces a wrong effectful call.) Ratchet lowered to 30,000:
#: raising it again means stating the per-round latency cost, and ~28.5k is the
#: level the reachable-not-resident rule holds the surface at.
#: 2026-10-03: 30,096, ratchet 30,100. +165 chars on write_graph for the same
#: reason the base64 rule came back, one step worse: the resident text SAID
#: "Publishing to the commons ... NOT available here (they stay in the browser
#: flow)", which went stale when #4315 shipped publish as a pending-request ask.
#: Live on prod, the founder asked their agent to publish and it answered that it
#: has no publish surface at all -- it was obeying this text. A chapter the agent
#: has no reason to fetch cannot fix that, so the correction is resident: the ask
#: exists, and chapter `systems` has the payload. Cost: ~41 tokens per
#: round-trip; ~124 if a two-tool turn takes three model rounds. A token
#: estimate, not measured latency.
# K2: four descriptions measure 490 characters; backend manuals are on demand.
MAX_SERVED_TOOL_DESCRIPTION_CHARS = 500

# Whole stock resident payload: Unicode characters / 4 is the stated token
# estimate, not a vendor tokenizer or a billing measure. Includes real schemas,
# shipped instructions, skill metadata, empty stock inventory and transport context.
# Native rows measure the platform-supplied payload using real MCP schemas, not
# opaque CLI-added system instructions or native tools. Those release blockers
# are recorded in docs/concerns/2026-10-06-k2-native-and-box-inventories.md.
# 2026-10-06: 4,000 -> 4,600. #4520 (Muse package) added seven starter skills
# whose index lines are resident: system 2,972 chars, totals 4,546 (http) /
# 4,482 (claude) / 4,414 (codex), about +137 tokens per round-trip by the
# chars/4 estimate. The skills are the shipped stock; trimming their
# descriptions is that package's call.
# 2026-10-06: 4,600 -> 5,250. The persona prompt had dropped the first-person
# line and honesty floor and cut the untrusted-envelope rule to one clause; the
# starter-agent-out-of-plumbing design keeps all three in plumbing (a voice fork
# must not dissolve the floor; envelope content is never the founder speaking or
# brain-worthy). Restored verbatim: system 3,628 chars, totals 5,202 (http) /
# 5,138 (claude) / 5,070 (codex), about +164 tokens per round-trip by chars/4.
MAX_STOCK_RESIDENT_CHARS = 5_250




def _learning_call(system: str) -> bool:
    """Whether this wire body is the learning extraction, not the reply turn."""
    return "now doing one narrow job" in system


@pytest.fixture
def turn(agent, monkeypatch, signed_in):
    """The real served-turn rig, re-pointed so the FULL converse() path runs.

    Records one row per model round-trip: when it started and ended, the exact
    request size, how much of that was the system prompt, and how much was the
    tool-schema block.
    """
    uid = agent.served.context.universe_dir.name
    monkeypatch.setattr(daemon_server, "get_founder_home", _GET_FOUNDER_HOME)
    signed_in("owner")
    calls: list[dict] = []
    state = SimpleNamespace(agent=agent, uid=uid, calls=calls, tool_name="read_brain")

    class Client:
        """The engine handle, returning whatever `state.tool_result` says.

        The shared rig returns plain text for every tool. That is fine for counting
        round-trips, but a turn only skips the learning pass when its `write_brain`
        RETURNED the handler's success shape — a returned refusal is not a write
        (PR #4001 review) — so the recorded case has to say so.
        """

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def is_connected(self):
            return True

        async def list_tools_mcp(self, *, cursor=None):
            return ListToolsResult(tools=[
                Tool(name=name, inputSchema={"type": "object"})
                for name in SERVED_ENGINE_MCP_TOOLS
            ])

        async def call_tool_mcp(self, name, arguments):
            assert name == "bash"
            return CallToolResult(content=[TextContent(type="text", text="exact result")],
                structuredContent={"completed_capabilities":
                    ["write_brain"] if state.tool_name == "write_brain" else []})

    monkeypatch.setattr(engine_tool_client, "_make_client", lambda *_: Client())

    class Proxy:
        def close(self):
            pass

        def request(self, verb, document):
            started = time.perf_counter()
            time.sleep(WIRE_LATENCY_S)
            body = document["body"]
            messages = body.get("messages", [])
            system = "".join(
                str(m.get("content") or "") for m in messages if m.get("role") == "system"
            )
            tools = body.get("tools") or []
            learning = _learning_call(system)
            writers = sum(1 for row in calls if row["kind"] != "extract_learning")
            calls.append({
                "kind": "extract_learning" if learning else f"writer_round_{writers + 1}",
                "t_start": started,
                "body_bytes": len(json.dumps(body)),
                "system_chars": len(system),
                "messages": len(messages),
                "tool_schemas": len(tools),
                "tool_schema_bytes": len(json.dumps(tools)) if tools else 0,
            })
            wants_tool = not learning and writers < agent.requested_rounds
            if learning:
                message = {"role": "assistant", "content": "{}"}
            elif wants_tool:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": f"call-{len(calls)}",
                        "type": "function",
                        "function": {
                            # read_brain by default: a READ is not evidence that
                            # the turn recorded its lesson, so the extraction
                            # still runs. A test that wants the recorded case sets
                            # `turn.tool_name = "write_brain"`.
                            "name": "bash",
                            "arguments": json.dumps({"command":
                                f"ta call {state.tool_name} --json '{{}}'"}),
                        },
                    }],
                }
            else:
                message = {"role": "assistant", "content": FINAL_REPLY}
            calls[-1]["t_end"] = time.perf_counter()
            return {
                "status": 200,
                "body": json.dumps({
                    "model": "free-model",
                    "choices": [{
                        "message": message,
                        "finish_reason": "tool_calls" if wants_tool else "stop",
                    }],
                }),
            }

    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy",
                        accounting_resolver(lambda *a, **k: Proxy()))
    return state


def _converse(turn, message="what's a good name for a cat?"):
    started = time.perf_counter()
    reply = universe_intelligence.converse(
        turn.uid, message, actor_id="owner", input_method="typed",
    )
    return reply, time.perf_counter() - started


# ---------------------------------------------------------------------------
# Where the founder's wall clock goes
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# The per-round payload: what gets re-sent every time
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# The fix: the prompt stops inviting a round-trip it already answered
# ---------------------------------------------------------------------------










