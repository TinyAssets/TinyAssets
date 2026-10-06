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
from tinyassets.api import interlocutor
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
MAX_STOCK_RESIDENT_CHARS = 4_000


@pytest.mark.parametrize("adapter", ["http", "claude", "codex"])
def test_whole_stock_resident_payload_per_adapter(
    tmp_path, monkeypatch, record_property, adapter, signed_in,
):
    import asyncio

    from tests.conftest import own_universe
    from tinyassets import engine_mcp_server, universe_tools
    from tinyassets.api import visibility
    from tinyassets.providers.agent_wire_codec import agent_wire_for
    from tinyassets.served_tools import FOUR_MODEL_TOOLS
    from tinyassets.starter_release import starter_manifest
    from tinyassets.starter_seeds import seed_store
    from tinyassets.universe_bundle import seed_okf_bundle

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    root = tmp_path / "stock"
    root.mkdir()
    own_universe(tmp_path, "stock")
    daemon_server.grant_universe_ownership(
        tmp_path, universe_id="stock", owner_id="test-owner::stock",
    )
    signed_in("test-owner::stock")
    daemon_server.ensure_universe_registered(tmp_path, universe_id="stock", universe_path=root)
    visibility.set_universe_visibility("stock", "private", source="owner")
    seed_okf_bundle(root)
    with seed_store(root, owner_id="test-owner::stock", center_id="stock") as seeds:
        seeds.install(starter_manifest(), fresh=True)

    def resident():
        return (
            _prompt(root, "stock") + "\n\n" + universe_tools.harness_prompt(root)
            + universe_tools.command_center_summary(root, "test-owner::stock")
            + "\n\n" + universe_intelligence._turn_input_method_context("typed")
        )

    system = resident()
    registered = asyncio.run(engine_mcp_server.mcp.list_tools(run_middleware=False))
    tools = tuple(Tool(name=t.name, description=t.description, inputSchema=t.parameters)
                  for t in registered if t.name in FOUR_MODEL_TOOLS)
    assert {t.name for t in tools} == set(FOUR_MODEL_TOOLS) == set(SERVED_ENGINE_MCP_TOOLS)
    if adapter == "http":
        from tinyassets.providers.agent_chat_codec import tool_definitions

        _, body = agent_wire_for("chat_messages").encode(
            prompt="Do my task", system=system, source_ref="stock", model="stock",
            tools=tool_definitions(tools),
        )
        definitions = json.dumps(body["tools"], ensure_ascii=False)
    else:
        definitions = json.dumps([
            t.model_copy(update={"name": "mcp__tinyassets__" + t.name}).model_dump(
                exclude_none=True)
            if adapter == "claude" else t.model_dump(exclude_none=True)
            for t in tools
        ], ensure_ascii=False)
    total = len(system) + len(definitions)
    record_property(f"{adapter}_stock_system_chars", len(system))
    record_property(f"{adapter}_stock_schema_chars", len(definitions))
    record_property(f"{adapter}_stock_chars_per_4_tokens", total / 4)
    assert total <= MAX_STOCK_RESIDENT_CHARS, (adapter, len(system), len(definitions), total)

    owner_text = "My owner prefers cobalt and works on orchard forecasts. " * 20
    (root / "founder.md").write_text(owner_text, encoding="utf-8")
    expanded = resident()
    assert owner_text.strip() in expanded
    dynamic = len(expanded) - len(system)
    assert dynamic >= len(owner_text.strip())
    record_property(f"{adapter}_dynamic_owner_context_chars", dynamic)
    # Dynamic context is reported separately, never sliced to make a budget pass.
    assert len(expanded) + len(definitions) == total + dynamic


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


def test_one_tool_step_costs_a_whole_extra_round_trip(turn):
    """The shape of the cost: N tool steps means N+1 reply round-trips."""
    turn.agent.requested_rounds = 1
    reply, _total = _converse(turn)
    assert reply == FINAL_REPLY
    writers = [row for row in turn.calls if row["kind"] != "extract_learning"]
    assert [row["kind"] for row in writers] == ["writer_round_1", "writer_round_2"]
    # Every round re-sends the whole system prompt and the whole tool block: the
    # second round is the first one again plus the tool result.
    assert writers[1]["system_chars"] == writers[0]["system_chars"]
    assert writers[1]["tool_schema_bytes"] == writers[0]["tool_schema_bytes"]
    assert writers[1]["body_bytes"] > writers[0]["body_bytes"]


def test_a_turn_that_records_its_own_lesson_pays_for_two_round_trips(turn):
    """The saving, pinned as CONDITIONAL — which is the honest shape of it.

    A turn whose tool step was the governed brain write has already recorded what
    it was taught, so the extraction call is skipped: two round-trips, not three.
    A turn that recorded nothing still pays three (the test below), so no lesson is
    lost and no turn is slower than it was
    (`openspec/changes/deferred-learning-never-blocks-the-reply/`).
    """
    turn.agent.requested_rounds = 1
    turn.tool_name = "write_brain"
    reply, total = _converse(turn)
    assert reply == FINAL_REPLY
    assert [row["kind"] for row in turn.calls] == ["writer_round_1", "writer_round_2"]
    assert "extract_learning" not in [row["kind"] for row in turn.calls]
    # And the founder waits on two round-trips of source latency, not three. Wire
    # time, not wall clock: this rig's local overhead is a real fraction of a
    # 0.2s synthetic latency and is not what the saving is about.
    waited = sum(row["t_end"] - row["t_start"] for row in turn.calls)
    assert 2 * WIRE_LATENCY_S <= waited < 3 * WIRE_LATENCY_S
    assert total >= waited


def test_extract_learning_is_a_third_round_trip_inside_the_founders_wait(turn):
    """A turn that recorded NOTHING still pays the third round-trip, as before.

    Pinned as a MEASUREMENT, not as desired behaviour: taking this call off the
    founder's clock entirely needs a provider lease that outlives the request (the
    lease is revoked in ``universe_server._register_structured_tool``'s
    ``finally``), which is an authority change, not an orchestration one — its own
    change, designed against the cursor-settle rate this one produces.
    """
    turn.agent.requested_rounds = 1
    reply, total = _converse(turn)
    assert reply == FINAL_REPLY
    assert [row["kind"] for row in turn.calls] == [
        "writer_round_1", "writer_round_2", "extract_learning",
    ]
    # The learning call is the LAST thing that happens inside converse, and it
    # starts only after the round-trip that produced the reply has ended.
    assert turn.calls[-1]["t_start"] >= turn.calls[-2]["t_end"]
    reply_ready_at = turn.calls[-2]["t_end"]
    withheld_s = turn.calls[-1]["t_end"] - reply_ready_at
    assert withheld_s >= WIRE_LATENCY_S
    assert total >= 3 * WIRE_LATENCY_S
    # It carries no tool block of its own, so its cost is the round-trip itself.
    assert turn.calls[-1]["tool_schemas"] == 0


def test_the_learning_call_never_costs_the_founder_their_reply(turn, caplog):
    """The measured cost is time, never the answer: a dead extractor is silent."""
    turn.agent.requested_rounds = 0

    def explode(*_args, **_kwargs):
        raise RuntimeError("synthetic extractor failure")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(universe_intelligence, "extract_learning", explode)
        reply, _total = _converse(turn)
    assert reply == FINAL_REPLY
    assert [row["kind"] for row in turn.calls] == ["writer_round_1"]


# ---------------------------------------------------------------------------
# The per-round payload: what gets re-sent every time
# ---------------------------------------------------------------------------


def test_engine_tool_description_budget_does_not_grow():
    """Ratchet: the tool manual rides on EVERY round of EVERY served turn."""
    import asyncio

    from tinyassets import engine_mcp_server

    async def descriptions():
        tools = {tool.name: tool for tool in await engine_mcp_server.mcp.list_tools()}
        missing = [name for name in SERVED_ENGINE_MCP_TOOLS if name not in tools]
        assert not missing, f"served handles absent from the engine surface: {missing}"
        return {name: (tools[name].description or "") for name in SERVED_ENGINE_MCP_TOOLS}

    bodies = asyncio.run(descriptions())
    total = sum(len(text) for text in bodies.values())
    biggest = max(bodies, key=lambda name: len(bodies[name]))
    assert total <= MAX_SERVED_TOOL_DESCRIPTION_CHARS, (
        f"served tool descriptions are {total} chars, over the "
        f"{MAX_SERVED_TOOL_DESCRIPTION_CHARS} ratchet; largest is {biggest!r} at "
        f"{len(bodies[biggest])}. This text is re-sent on every round-trip of "
        "every served founder turn -- raise the ratchet only with the latency "
        "cost stated."
    )


def test_static_folder_harness_budget_does_not_grow():
    """New request-economy guidance pays for itself in the existing harness."""
    from tinyassets.universe_tools import _HARNESS_HEAD

    assert len(_HARNESS_HEAD) <= 220


# ---------------------------------------------------------------------------
# The fix: the prompt stops inviting a round-trip it already answered
# ---------------------------------------------------------------------------


def _prompt(universe_dir, uid, tier=interlocutor.FOUNDER):
    return universe_intelligence._build_persona_system_prompt(
        universe_dir, universe_id=uid, tier=tier,
    )


def test_quoted_grounding_is_declared_current_so_recall_needs_no_round_trip(turn):
    """Red before the fix: the files were inlined but never declared current."""
    universe_dir = turn.agent.served.context.universe_dir
    (universe_dir / "founder.md").write_text(
        "My founder's favourite colour is cobalt.\n", encoding="utf-8",
    )
    text = _prompt(universe_dir, turn.uid)
    assert "## founder.md" in text
    assert "My founder's favourite colour is cobalt." in text
    lowered = text.lower()
    assert "current and complete contents as of this turn" in lowered
    # The claim is about the quoted sections, and it must precede them.
    assert lowered.index("current and complete contents") < lowered.index("## founder.md")
    # It removes the REASON to re-fetch, never the ability: nothing here forbids
    # a read, and the read-before-edit rule survives.
    assert "edit" in lowered
    for forbidding in ("do not read", "never read", "you may not read"):
        assert forbidding not in lowered


def test_nothing_is_declared_current_when_no_file_was_inlined(turn):
    """The claim is never made about contents that are not there."""
    universe_dir = turn.agent.served.context.universe_dir
    for name in universe_intelligence._GROUNDING_FILES:
        path = universe_dir / name
        if path.exists():
            path.unlink()
    text = _prompt(universe_dir, turn.uid)
    assert "nothing learned yet" in text
    assert "current and complete contents" not in text.lower()


def test_a_withheld_tier_still_gets_the_refusal_not_a_claim(turn):
    """Nothing about the fix reaches a tier the visibility filter closes out."""
    universe_dir = turn.agent.served.context.universe_dir
    (universe_dir / "founder.md").write_text("Founder dossier.\n", encoding="utf-8")
    with pytest.raises(PermissionError, match="no authorized content"):
        _prompt(universe_dir, turn.uid, tier=interlocutor.T1)


def test_the_claim_names_only_the_files_the_filter_permitted(turn, monkeypatch):
    """A narrowed grounding set gets the header for THAT set and nothing more."""
    universe_dir = turn.agent.served.context.universe_dir
    (universe_dir / "founder.md").write_text("Founder dossier.\n", encoding="utf-8")
    (universe_dir / "identity.md").write_text("I am the rig universe.\n", encoding="utf-8")
    monkeypatch.setattr(
        universe_intelligence.interlocutor,
        "permitted_grounding_files",
        lambda universe_id, files, *, tier: ("identity.md",),
    )
    text = _prompt(universe_dir, turn.uid)
    assert "## identity.md" in text
    assert "current and complete contents as of this turn" in text.lower()
    # The header must not imply the withheld file exists, and its body must not
    # ride in under another heading.
    assert "founder.md" not in text
    assert "Founder dossier." not in text
