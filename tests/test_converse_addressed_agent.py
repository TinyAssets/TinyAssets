"""Talking to one of your agents directly (harness §4.18, the multi-agent invariant).

A command center holds the main agent and any custom agent the owner bound
into it. ``converse(agent_id=...)`` runs the turn AS that agent: its own
instructions, its own thread, the same shared brain; the main agent sees what
the others said; and an id that is not the owner's own agent there is refused
by name before anything runs.

Only the provider call is stubbed. Auth, the ACL, the binding store, persona
assembly, the conversation store and learning persistence are the real ones.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tinyassets.universe_intelligence as ui
import tinyassets.universe_server as us
from tinyassets import addressed_agents
from tinyassets.conversation_store import load_recent_readonly
from tinyassets.custom_agents import create_binding, publish_definition
from tinyassets.universe_bundle import seed_okf_bundle

OWNER = "founder-1"
WEAVER_RULE = "Help plan and critique a rigorous research paper."
CHECK_RULE = "Verify every claim against its cited source."


@pytest.fixture(autouse=True)
def _reset_auth():
    from tinyassets.auth.middleware import auth_middleware, set_provider
    from tinyassets.auth.provider import DevAuthProvider

    set_provider(DevAuthProvider())
    auth_middleware("dev")
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


def _become(base: Path, actor: str, uid: str) -> None:
    from tinyassets.auth.middleware import auth_middleware, set_provider
    from tinyassets.auth.provider import AuthProvider, Identity
    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(base, universe_id=uid, actor_id=actor, permission="admin")
    identity = Identity(user_id=actor, username=actor, capabilities=[
        "tinyassets.universe.read", "tinyassets.universe.write", "tinyassets.universe.admin",
    ])

    class _Static(AuthProvider):
        def resolve_token(self, token):
            return identity if token == "ok" else None

        def is_auth_required(self):
            return True

        def register_client(self, metadata):
            return {"client_id": "test-client", **metadata}

        def create_authorization(self, *_a, **_kw):
            raise NotImplementedError

        def exchange_code(self, *_a, **_kw):
            raise NotImplementedError

    set_provider(_Static())
    auth_middleware("ok")


def _universe(base: Path, uid: str) -> Path:
    from tests.conftest import own_universe
    from tinyassets.daemon_server import ensure_universe_registered

    udir = base / uid
    udir.mkdir()
    own_universe(base, uid)
    seed_okf_bundle(udir, purpose="To help my founder bring their projects to life.")
    ensure_universe_registered(base, universe_id=uid, universe_path=udir)
    return udir


def _agent(base: Path, uid: str, owner: str, name: str, **configuration) -> str:
    definition = publish_definition(base, author_id=owner, payload={
        "schema_version": 1, "name": name, "components": {
            "identity": {"kind": "soul", "config": {"instructions": WEAVER_RULE}},
            "fact_check": {"kind": "skill", "config": {"instructions": CHECK_RULE}},
        },
    })
    binding = create_binding(
        base, universe_id=uid, definition_id=definition["agent_definition_id"],
        created_by=owner, payload={"schema_version": 1, "name": name, **configuration},
    )
    return binding["agent_binding_id"]


class _Provider:
    """The model: records every call, answers the reply and the extraction."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.lesson: dict = {}

    def __call__(self, prompt, system="", **_kw):
        self.calls.append((prompt, system))
        if "strict JSON" in system:
            return json.dumps(self.lesson)
        return "reply-" + str(len(self.writer_calls()))

    def writer_calls(self):
        return [call for call in self.calls if "strict JSON" not in call[1]]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    udir = _universe(tmp_path, "u-home")
    _universe(tmp_path, "u-other")
    _become(tmp_path, OWNER, "u-home")
    provider = _Provider()
    monkeypatch.setattr(ui, "call_provider", provider)
    weaver = _agent(tmp_path, "u-home", OWNER, "Evidence Weaver")
    return {"base": tmp_path, "udir": udir, "provider": provider, "weaver": weaver}


def _converse(**kwargs) -> dict:
    return json.loads(us.converse(graph_id="u-home", **kwargs))


def test_an_addressed_agent_answers_as_itself_on_its_own_thread(world):
    provider = world["provider"]
    out = _converse(message="What do you do?", agent_id=world["weaver"])

    assert out["reply"] == "reply-1"
    assert out["agent"] == {"agent_id": world["weaver"], "name": "Evidence Weaver"}
    prompt, system = provider.writer_calls()[0]
    assert "You are Evidence Weaver, one of the agents of this command center" in system
    assert WEAVER_RULE in system and CHECK_RULE in system
    # The shared brain is still what it speaks from.
    assert "# What I know so far" in system

    agent_thread = load_recent_readonly(
        world["udir"], addressed_agents.memory_session(OWNER, world["weaver"]))
    assert [(m.speaker, m.text) for m in agent_thread] == [
        ("founder", "What do you do?"), ("universe", "reply-1")]
    assert load_recent_readonly(world["udir"], f"principal:{OWNER}") == []


def test_main_keeps_its_thread_and_never_gets_an_agents_instructions(world):
    provider = world["provider"]
    out = _converse(message="hello main")
    assert "agent" not in out
    _, system = provider.writer_calls()[0]
    assert "Evidence Weaver" not in system and WEAVER_RULE not in system
    main = load_recent_readonly(world["udir"], f"principal:{OWNER}")
    assert [m.text for m in main] == ["hello main", "reply-1"]


def test_each_agent_continues_only_its_own_history(world):
    provider = world["provider"]
    _converse(message="main secret plan")
    _converse(message="weaver first", agent_id=world["weaver"])
    _converse(message="weaver second", agent_id=world["weaver"])
    weaver_prompt = provider.writer_calls()[-1][0]
    assert "weaver first" in weaver_prompt
    assert "main secret plan" not in weaver_prompt


def test_what_the_founder_teaches_an_agent_lands_in_the_shared_brain(world):
    from tinyassets.universe_self_model import read_self_model

    provider = world["provider"]
    provider.lesson = {
        "name": "Weave",
        "soul": {"founder.md": "My founder studies tidepool ecology.",
                 "identity.md": "I am Weave, a research critic."},
    }
    _converse(message="I study tidepools. Your name is Weave.", agent_id=world["weaver"])

    udir = world["udir"]
    assert "tidepool" in (udir / "founder.md").read_text(encoding="utf-8")
    # Naming THAT agent never renames the main one.
    assert read_self_model(udir).get("name") != "Weave"
    assert "Weave" not in (udir / "identity.md").read_text(encoding="utf-8")

    # And the main agent reads the same brain on its next turn.
    provider.lesson = {}
    _converse(message="what do I study?")
    assert "tidepool" in provider.writer_calls()[-1][1]


def test_main_sees_what_the_other_agents_said(world):
    provider = world["provider"]
    _converse(message="critique my methods section", agent_id=world["weaver"])
    _converse(message="what has Weaver been doing?")
    main_prompt = provider.writer_calls()[-1][0]
    assert "[Evidence Weaver's conversation] your founder: critique my methods section" in (
        main_prompt)
    assert "[Evidence Weaver's conversation] Evidence Weaver: reply-1" in main_prompt


def test_a_stranger_agent_is_refused_before_any_model_runs(world):
    base, provider = world["base"], world["provider"]
    # Another account's agent, bound into THIS universe and into its own.
    _become(base, "stranger", "u-other")
    in_home = _agent(base, "u-home", "stranger", "Planted")
    theirs = _agent(base, "u-other", "stranger", "Theirs")
    # The owner's own agent, but bound in a different command center.
    mine_elsewhere = _agent(base, "u-other", OWNER, "Elsewhere")
    _become(base, OWNER, "u-home")

    for agent_id in (in_home, theirs, mine_elsewhere, "agent_binding_nope", "a:b", "x y"):
        out = _converse(message="hi", agent_id=agent_id)
        assert out.get("agent_not_found") is True, agent_id
        assert "reply" not in out
    assert provider.calls == []
    assert load_recent_readonly(world["udir"], f"principal:{OWNER}") == []


def test_a_conversation_design_row_is_not_an_agent(world):
    base = world["base"]
    design = _agent(base, "u-home", OWNER, "Design", role="app_experience")
    out = _converse(message="hi", agent_id=design)
    assert out.get("agent_not_found") is True
    assert [row["agent_id"] for row in addressed_agents.roster(
        base, universe_id="u-home", owner=OWNER)] == ["main", world["weaver"]]


def test_main_by_name_is_the_main_thread(world):
    out = _converse(message="hi", agent_id="main")
    assert out["reply"] == "reply-1" and "agent" not in out
    assert load_recent_readonly(world["udir"], f"principal:{OWNER}")


def test_the_status_peek_reads_the_addressed_agents_thread(world):
    from tinyassets.api.status import get_status

    _converse(message="main line")
    _converse(message="weaver line", agent_id=world["weaver"])

    peek = json.loads(get_status(universe_id="u-home", include_conversation=True,
                                 conversation_agent=world["weaver"]))["recent_conversation"]
    assert peek["session_scope"] == "agent"
    assert peek["agent"]["agent_id"] == world["weaver"]
    assert [t["text"] for t in peek["turns"]] == ["weaver line", "reply-2"]

    main = json.loads(get_status(universe_id="u-home", include_conversation=True)
                      )["recent_conversation"]
    assert [t["text"] for t in main["turns"]] == ["main line", "reply-1"]

    refused = json.loads(get_status(universe_id="u-home", include_conversation=True,
                                    conversation_agent="agent_binding_nope")
                         )["recent_conversation"]
    assert refused.get("agent_not_found") is True and "turns" not in refused


def test_session_keys_round_trip_and_never_cross_owners():
    key = addressed_agents.memory_session("o-1", "agent_binding_x")
    assert key == "agent:agent_binding_x:principal:o-1"
    assert addressed_agents.agent_of_session(key, "o-1") == "agent_binding_x"
    assert addressed_agents.agent_of_session(key, "o-2") is None
    assert addressed_agents.memory_session("o-1") == "principal:o-1"
    assert addressed_agents.agent_of_session("principal:o-1", "o-1") == "main"


def test_a_steer_goes_to_the_agent_the_owner_is_talking_to(world, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from tests.test_turn_interrupt import _Request
    from tinyassets import agent_steering, onboarding
    from tinyassets.auth import middleware
    from tinyassets.turn_interrupt import interactive_turn

    udir = world["udir"]
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(middleware, "current_identity", lambda: SimpleNamespace(user_id=OWNER))

    def post(body):
        response = asyncio.run(onboarding._handle_turn_steer(_Request(body)))
        return response.status_code, json.loads(response.body)

    weaver_thread = "thread:" + addressed_agents.memory_session(OWNER, world["weaver"])
    with interactive_turn(OWNER, "u-home") as live:
        agent_steering.open_turn(udir, weaver_thread, live.live_id)
        status, body = post({"universe_id": "u-home", "text": "focus on methods",
                             "agent_id": world["weaver"]})
        assert status == 200 and body["steered"] is True
        # The main thread has no open turn, so a steer for main is not taken.
        assert post({"universe_id": "u-home", "text": "to main"})[1]["steered"] is False
        status, body = post({"universe_id": "u-home", "text": "x",
                             "agent_id": "agent_binding_nope"})
        assert status == 404 and body["error"] == "agent_not_found"
        assert [m.text for m in agent_steering.take(udir, weaver_thread, live.live_id)] == [
            "focus on methods"]


def test_owner_delimiters_never_alias_another_owners_agent_session(tmp_path):
    """Keys are built from parts and read back only by rebuilding them.

    An owner id carrying the delimiter cannot be part of a key at all, and a key
    already in the store with an adversarial shape (as if planted) never reads as
    another owner's thread.
    """
    from tinyassets.conversation_store import load_recent_agent_turns, record_turn

    for hostile in ("alice:principal:bob", "a%3Ab", "x y", ""):
        with pytest.raises(addressed_agents.AgentNotAddressable):
            addressed_agents.memory_session(hostile, "weaver")
        with pytest.raises(addressed_agents.AgentNotAddressable):
            addressed_agents.memory_session(hostile)
    planted = "agent:weaver:principal:alice:principal:bob"
    for malformed in (planted, "agent::principal:bob", "agent:x:y:principal:bob",
                      "agent:x:principal:bob:", "principal:bob:x", "principal:"):
        assert addressed_agents.agent_of_session(malformed, "bob") is None, malformed
    record_turn(tmp_path, addressed_agents.memory_session("bob", "mine"),
                "founder", "bob's line", ts=1)
    record_turn(tmp_path, planted, "founder", "alice's private line", ts=2)
    assert [(a, m.text) for a, m in load_recent_agent_turns(tmp_path, "bob")] == [
        ("mine", "bob's line")]
    assert load_recent_agent_turns(tmp_path, "alice:principal:bob") == []


def test_an_adversarial_owner_cannot_converse_into_any_thread(world, monkeypatch):
    from tinyassets.api import permissions

    monkeypatch.setattr(permissions, "current_actor_id", lambda: "x:principal:" + OWNER)
    out = _converse(message="hi", agent_id=world["weaver"])
    assert "reply" not in out and world["provider"].calls == []


def test_huge_agent_activity_preserves_the_main_threads_newest_exchange(world):
    from tinyassets.conversation_memory import Msg, format_history
    from tinyassets.conversation_store import record_turn

    for ts in range(3, 9):
        record_turn(world["udir"], addressed_agents.memory_session(OWNER, world["weaver"]),
                    "universe", "enormous reply " * 10000, ts=ts)
    history = [Msg("founder", "main newest question", 1), Msg("universe", "main newest answer", 2)]
    combined = us._with_agent_activity(history, world["udir"], "u-home", OWNER)
    rendered = format_history(combined)
    assert "main newest question" in rendered and "main newest answer" in rendered
    notices = [m for m in combined if m.speaker == "platform"]
    assert sum(len(m.text) for m in notices) <= us._AGENT_ACTIVITY_TOTAL_CHARS
    assert all(len(m.text) <= us._AGENT_ACTIVITY_NOTICE_CHARS for m in notices)


def test_long_message_expansion_reads_only_the_addressed_agents_thread(world):
    from tinyassets.api.graph_reads import read_graph
    from tinyassets.conversation_store import record_turn
    from tinyassets.daemon_server import set_founder_home

    set_founder_home(
        world["base"], founder_sub=OWNER, universe_id="u-home", platform_generated=True)
    text = "weaver's long reply " * 600
    record_turn(world["udir"], addressed_agents.memory_session(OWNER, world["weaver"]),
                "universe", text)
    args = dict(target="conversation", graph_id="u-home", agent_binding_id=world["weaver"])
    page = json.loads(read_graph(**args))
    message_id = str(page["messages"][0]["id"])
    chunks = []
    offset = 0
    while offset is not None:
        part = json.loads(read_graph(**args, field_name=message_id, output_offset=offset))
        chunks.append(part["chunk"])
        offset = part["next_offset"]
    assert "".join(chunks) == text
    assert json.loads(read_graph(target="conversation", graph_id="u-home", field_name=message_id))[
        "error"] == "conversation_message_not_found"
    foreign = _agent(world["base"], "u-home", "stranger", "Foreign")
    assert json.loads(read_graph(**dict(args, agent_binding_id=foreign)))["agent_not_found"]


def test_public_status_forwards_the_addressed_agent(world):
    _converse(message="weaver only", agent_id=world["weaver"])
    out = json.loads(us.get_status(command_center_id="u-home", include_conversation=True,
                                  conversation_agent=world["weaver"]))
    assert out["recent_conversation"]["agent"]["agent_id"] == world["weaver"]
    assert out["recent_conversation"]["turns"][0]["text"] == "weaver only"


def test_an_addressed_turn_registers_under_that_agent_so_stop_can_reach_it(world, monkeypatch):
    """Harness §4.18: the owner's Stop must reach the agent they are talking to.

    ``request_interrupt`` filters on ``live.agent_id`` and ``LiveTurn`` carries
    it, but ``converse`` registered every turn at the ``main`` default whoever
    it was addressed to. So a Stop aimed at a custom agent matched nothing and
    did nothing, and a Stop aimed at main stopped that custom agent's turn.
    """
    from tinyassets import turn_interrupt as ti

    seen: dict[str, object] = {}
    provider = world["provider"]

    def capture(prompt, system="", **kwargs):
        live = ti.current()
        seen["registered_agent"] = None if live is None else live.agent_id
        # A Stop addressed to MAIN must not match this turn. Side-effect free
        # only because this fixture runs no main turn in the same
        # (owner, universe) bucket -- if one existed this call would correctly
        # stop it, so do not reuse this line where one does (Codex refute of
        # this PR, finding E).
        seen["main_stop"] = ti.request_interrupt(OWNER, "u-home", agent_id="main")
        seen["still_running"] = live is not None and not live.requested()
        return provider(prompt, system=system, **kwargs)

    monkeypatch.setattr(ui, "call_provider", capture)
    out = _converse(message="hello", agent_id=world["weaver"])

    assert seen["registered_agent"] == world["weaver"], (
        "the turn registered under "
        f"{seen['registered_agent']!r}, so a Stop addressed to the agent cannot find it"
    )
    assert seen["main_stop"] == 0, "a Stop addressed to main reached a custom agent's turn"
    assert seen["still_running"] is True, "main's Stop asked a custom agent's turn to stop"
    assert out.get("interrupted") is not True and "error" not in out
    assert ti.live_count(OWNER, "u-home") == 0, "the turn stayed registered after returning"


def test_a_stop_addressed_to_the_agent_interrupts_its_live_turn(world, monkeypatch):
    """The positive half, end to end through a real converse turn."""
    from tinyassets import turn_interrupt as ti

    stopped: dict[str, object] = {}

    def capture(prompt, system="", **kwargs):
        stopped["matched"] = ti.request_interrupt(OWNER, "u-home", agent_id=world["weaver"])
        live = ti.current()
        stopped["requested"] = live is not None and live.requested()
        live.check()  # raises TurnInterrupted at this boundary
        raise AssertionError("the stop did not take effect")

    monkeypatch.setattr(ui, "call_provider", capture)
    out = _converse(message="stop me", agent_id=world["weaver"])

    assert stopped["matched"] == 1, "the addressed Stop did not match the agent's live turn"
    assert stopped["requested"] is True
    assert out["interrupted"] is True
    assert ti.live_count(OWNER, "u-home") == 0


def test_a_main_turn_still_registers_as_main(world, monkeypatch):
    """No change for the main agent: its Stop target is exactly what it was."""
    from tinyassets import turn_interrupt as ti

    seen: dict[str, object] = {}
    provider = world["provider"]

    def capture(prompt, system="", **kwargs):
        live = ti.current()
        seen["registered_agent"] = None if live is None else live.agent_id
        seen["weaver_stop"] = ti.request_interrupt(OWNER, "u-home", agent_id=world["weaver"])
        return provider(prompt, system=system, **kwargs)

    monkeypatch.setattr(ui, "call_provider", capture)
    _converse(message="hello")

    assert seen["registered_agent"] == "main"
    assert seen["weaver_stop"] == 0, "a custom agent's Stop reached the main turn"


def test_ingress_puts_the_addressed_agent_on_the_context(world, monkeypatch):
    """The ONE place UniverseContext.agent_id is set (harness §4.18).

    Everything downstream -- the journal's attribution today, the per-launch
    snapshot later -- reads that field, so if ingress does not set it the whole
    carrier is silently main. Asserted here because the suites that exercise
    the journal build their own context and cannot see what converse built.
    """
    seen: list[str] = []
    provider = world["provider"]

    def capture(prompt, system="", **kwargs):
        context = kwargs.get("universe_context")
        seen.append(None if context is None else context.agent_id)
        return provider(prompt, system=system, **kwargs)

    monkeypatch.setattr(ui, "call_provider", capture)

    _converse(message="as the weaver", agent_id=world["weaver"])
    assert seen and seen[0] == world["weaver"], (
        f"ingress built a context for {seen[:1]!r}, not the addressed agent")

    seen.clear()
    _converse(message="as main")
    assert seen and seen[0] == addressed_agents.MAIN_AGENT, (
        "a turn with no addressed agent must carry main, not an empty string")
