"""Long-lived agent sessions (change `universe-agent-harness`, slice S1).

Before this every served turn was a fresh ``--ephemeral`` process that saw a
text summary of recent messages and none of its own tool work; every
background wake rebuilt that context from scratch. A session key now names the
thread or agent node a turn continues, and a resume-capable adapter continues
the same native session with only the input it has not seen.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import tinyassets.api.interlocutor as interlocutor
import tinyassets.universe_intelligence as ui
from tests.test_codex_app_server import served  # noqa: F401 - the shared fixture
from tinyassets import agent_sessions
from tinyassets.conversation_memory import Msg
from tinyassets.exceptions import ProviderError
from tinyassets.universe_bundle import seed_okf_bundle

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="native sessions are held with flock (Linux hosts)",
)


def _ref(udir: Path, *, prompt="fresh prompt", resume="new message", key="thread:principal:o"):
    return agent_sessions.AgentSessionRef(
        universe_dir=udir, key=key,
        fresh_prompt_digest=agent_sessions.digest(prompt),
        resume_prompt=resume, built_at=100.0,
    )


def _session_model(system="system") -> str:
    """What the codex adapter records a thread under: its model and tool set."""
    from tests.test_codex_app_server import _engine_tools
    from tinyassets.agent_definition import agent_definition
    from tinyassets.providers.codex_app_server import tools_digest

    return "#tools:" + tools_digest(agent_definition(_engine_tools(), system))


def _sent(server) -> str:
    return server.requests("turn/start")[0]["params"]["input"][0]["text"]


# --- the record ---------------------------------------------------------------


def test_a_record_resumes_only_on_its_own_adapter_model_and_prompt(tmp_path):
    ref = _ref(tmp_path)
    agent_sessions.save(ref, adapter="codex", model="m1", handle="h-1", system="s")
    assert agent_sessions.resumable(ref, adapter="codex", model="m1", prompt="fresh prompt")
    assert agent_sessions.resumable(ref, adapter="other", model="m1", prompt="fresh prompt") is None
    assert agent_sessions.resumable(ref, adapter="codex", model="m2", prompt="fresh prompt") is None
    # A caller that rewrote the input is never silently replaced by resume_prompt.
    assert agent_sessions.resumable(ref, adapter="codex", model="m1", prompt="rewritten") is None
    assert agent_sessions.consumed_at(tmp_path, ref.key) == 100.0


def test_records_live_outside_every_universe_folder(tmp_path):
    """No jail binds the data root's record store, so nothing a universe runs
    can plant a link the daemon writes through (gpt-6-astra refute of S1)."""
    universe = tmp_path / "u-one"
    universe.mkdir()
    ref = _ref(universe)
    agent_sessions.save(ref, adapter="codex", model="", handle="h", system="")
    assert not list(universe.rglob("*"))
    records = list((tmp_path / agent_sessions.RECORDS_DIR / "u-one").glob("*.json"))
    assert len(records) == 1


@posix_only
def test_a_planted_runtime_link_never_redirects_the_native_store(tmp_path):
    """A workflow provider jail binds the universe read-write, .runtime included."""
    universe = tmp_path / "u-one"
    (universe / ".runtime").mkdir(parents=True)
    victim = tmp_path / "u-other"
    victim.mkdir()
    (universe / ".runtime" / "agent-sessions").symlink_to(victim)
    with pytest.raises(OSError):
        agent_sessions.native_store(universe, "codex")
    assert not list(victim.iterdir())


@posix_only
def test_the_native_file_check_never_follows_a_link(tmp_path):
    universe = tmp_path / "u-one"
    universe.mkdir()
    store = agent_sessions.native_store(universe, "codex")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "rollout-x-abc.jsonl").write_text("{}")
    (store / "linked").symlink_to(elsewhere)
    assert not agent_sessions.native_file_exists(store, "abc.jsonl")
    (store / "rollout-y-abc.jsonl").write_text("{}")
    assert agent_sessions.native_file_exists(store, "abc.jsonl")


def test_unseen_keeps_only_later_messages_of_the_named_speakers():
    history = [
        Msg(speaker="founder", text="old", ts=50.0),
        Msg(speaker="founder", text="new", ts=150.0),
        Msg(speaker="platform", text="notice", ts=160.0),
    ]
    assert [m.text for m in agent_sessions.unseen(history, 100.0)] == ["new", "notice"]
    assert [m.text for m in agent_sessions.unseen(
        history, 100.0, speakers=frozenset({"platform"}))] == ["notice"]


# --- the codex adapter (app-server threads) -----------------------------------


@posix_only
@pytest.mark.asyncio
async def test_first_turn_keeps_its_native_session_and_records_it(served):  # noqa: F811
    run, launch, _state, config, udir = served
    ref = _ref(udir)
    _, server = await run(cfg=config(agent_session=ref))
    assert server.requests("thread/start")[0]["params"]["ephemeral"] is False
    assert server.requests("thread/resume") == []
    mounts = launch.call_args.kwargs["universe_view"].mounts
    store = agent_sessions.native_store(udir, "codex")
    assert any(m.op == "bind" and m.dest == "/codex-home/sessions" and m.source == store
               for m in mounts)
    record = agent_sessions.load(udir, ref.key)
    assert record["handle"] == "thr-1"
    # Instructions travel as the thread's own; the turn carries only the prompt.
    assert _sent(server) == "fresh prompt"


@posix_only
@pytest.mark.asyncio
async def test_next_turn_resumes_and_sends_only_what_is_new(served):  # noqa: F811
    run, _launch, _state, config, udir = served
    ref = _ref(udir, resume="[now]\nwhat did that command print?")
    handle = "0a1b2c3d-0000-4000-8000-000000000001"
    agent_sessions.save(ref, adapter="codex", model=_session_model(), handle=handle,
                        system="system")
    store = agent_sessions.native_store(udir, "codex")
    (store / "2026" / "10" / "01").mkdir(parents=True)
    (store / "2026" / "10" / "01" / f"rollout-2026-10-01T00-00-00-{handle}.jsonl").write_text("{}")

    _, server = await run(cfg=config(agent_session=ref))

    assert server.requests("thread/start") == []
    # The thread's own instructions are replaced with the current ones; the
    # turn carries only what is new.
    assert server.requests("thread/resume")[0]["params"] == {
        "threadId": handle, "baseInstructions": "system"}
    assert _sent(server) == "[now]\nwhat did that command print?"


@posix_only
@pytest.mark.asyncio
async def test_changed_instructions_replace_the_resumed_threads_own(served):  # noqa: F811
    """A resumed thread otherwise keeps the instructions it started with
    (codex-cli 0.160.0 request capture, K2 evidence): the current ones are
    sent as its own, and never folded into the user's input."""
    run, _launch, _state, config, udir = served
    ref = _ref(udir, resume="next message")
    handle = "0a1b2c3d-0000-4000-8000-000000000003"
    agent_sessions.save(ref, adapter="codex", model=_session_model(), handle=handle,
                        system="old system")
    store = agent_sessions.native_store(udir, "codex")
    (store / f"rollout-x-{handle}.jsonl").write_text("{}")
    _, server = await run(cfg=config(agent_session=ref), system="new system")
    (resume,) = server.requests("thread/resume")
    assert resume["params"]["baseInstructions"] == "new system"
    assert _sent(server) == "next message"
    assert "old system" not in json.dumps(server.received)


@posix_only
@pytest.mark.asyncio
async def test_a_vanished_native_session_starts_a_new_one(served):  # noqa: F811
    run, _launch, _state, config, udir = served
    ref = _ref(udir)
    agent_sessions.save(ref, adapter="codex", model=_session_model(), handle="0a1b2c3d-dead",
                        system="system")
    _, server = await run(cfg=config(agent_session=ref))
    assert server.requests("thread/resume") == []
    assert _sent(server) == "fresh prompt"


@posix_only
@pytest.mark.asyncio
async def test_a_failed_resume_is_forgotten_so_the_next_turn_starts_fresh(served):  # noqa: F811
    from tests.support.fake_codex_app_server import Turn

    run, _launch, _state, config, udir = served
    ref = _ref(udir)
    handle = "0a1b2c3d-0000-4000-8000-000000000002"
    agent_sessions.save(ref, adapter="codex", model=_session_model(), handle=handle,
                        system="system")
    store = agent_sessions.native_store(udir, "codex")
    (store / f"rollout-x-{handle}.jsonl").write_text("{}")
    with pytest.raises(ProviderError):
        await run(Turn(reply=""), cfg=config(agent_session=ref))
    assert agent_sessions.load(udir, ref.key) is None


@posix_only
@pytest.mark.asyncio
async def test_a_session_held_by_another_launch_runs_unrecorded(served):  # noqa: F811
    run, _launch, _state, config, udir = served
    ref = _ref(udir)
    with agent_sessions.exclusive(ref) as held:
        assert held
        _, server = await run(cfg=config(agent_session=ref))
    assert server.requests("thread/start")[0]["params"]["ephemeral"] is True
    assert agent_sessions.load(udir, ref.key) is None


@pytest.mark.asyncio
async def test_a_turn_without_a_session_stays_ephemeral(served):  # noqa: F811
    run, _launch, _state, config, udir = served
    _, server = await run(cfg=config())
    assert server.requests("thread/start")[0]["params"]["ephemeral"] is True
    assert not (udir / ".runtime" / "agent-sessions").exists()


# --- converse builds the reference ----------------------------------------------


def test_a_chat_thread_is_sent_only_notices_it_has_not_seen(tmp_path):
    key = "thread:principal:o"
    agent_sessions.save(_ref(tmp_path, key=key), adapter="codex", model="", handle="h", system="")
    history = [
        Msg(speaker="founder", text="already in the session", ts=150.0),
        Msg(speaker="platform", text="your run failed", ts=160.0),
    ]
    ref = ui.session_ref(tmp_path, key, "fresh", "next question", history,
                         speakers=frozenset({"platform"}))
    assert "your run failed" in ref.resume_prompt
    assert "already in the session" not in ref.resume_prompt
    assert ref.resume_prompt.endswith("next question")
    assert ref.fresh_prompt_digest == agent_sessions.digest("fresh")


def test_an_agent_node_is_sent_the_conversation_since_its_last_wake(tmp_path):
    key = "node:abc"
    agent_sessions.save(_ref(tmp_path, key=key), adapter="codex", model="", handle="h", system="")
    history = [
        Msg(speaker="founder", text="before", ts=50.0),
        Msg(speaker="founder", text="stop the village work", ts=150.0),
    ]
    ref = ui.session_ref(tmp_path, key, "fresh", "wake", history)
    assert "stop the village work" in ref.resume_prompt
    assert "before" not in ref.resume_prompt


# --- tone and authority ---------------------------------------------------------


def _seed(tmp_path: Path) -> Path:
    import tinyassets.api.visibility as vis
    from tests.conftest import own_universe
    from tinyassets.daemon_server import ensure_universe_registered

    udir = tmp_path / "u-test"
    udir.mkdir()
    own_universe(udir.parent, udir.name)
    seed_okf_bundle(udir, purpose="To help my founder.")
    ensure_universe_registered(tmp_path, universe_id="u-test", universe_path=udir)
    vis.set_universe_visibility("u-test", "public", source="owner")
    return udir


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    return tmp_path


def test_the_founder_prompt_is_result_first_and_proactive(data_dir):
    udir = _seed(data_dir)
    prompt = ui._build_persona_system_prompt(udir, universe_id="u-test", tier=interlocutor.FOUNDER)
    from tinyassets.starter_release import starter_manifest
    from tinyassets.starter_seeds import seed_store

    with seed_store(udir, owner_id="test-owner::u-test", center_id="u-test") as seeds:
        seeds.install(starter_manifest(), fresh=True)
    prompt = ui._build_persona_system_prompt(udir, universe_id="u-test", tier=interlocutor.FOUNDER)
    assert "Finish authorized work" in prompt
    assert "the result first" in prompt
    for teaches_hedging in ("warmly", "genuinely curious", "being raised", "raising",
                            "getting to know", "ask to clarify"):
        assert teaches_hedging not in prompt


def test_operating_instructions_are_seeded_once_and_then_the_universes_own(data_dir):
    udir = _seed(data_dir)
    ui._build_persona_system_prompt(udir, universe_id="u-test", tier=interlocutor.FOUNDER)
    assert not (udir / "AGENTS.md").exists()  # Rendering never provisions.
    from tinyassets.starter_release import starter_manifest
    from tinyassets.starter_seeds import seed_store
    from tinyassets.starter_skills import starter_agent_files

    with seed_store(udir, owner_id="test-owner::u-test", center_id="u-test") as seeds:
        seeds.install(starter_manifest(), fresh=True)
    seeded = (udir / "AGENTS.md").read_text(encoding="utf-8")
    assert seeded == starter_agent_files()["AGENTS.md"]
    (udir / "AGENTS.md").write_text("Answer in one sentence.\n", encoding="utf-8")
    prompt = ui._build_persona_system_prompt(udir, universe_id="u-test", tier=interlocutor.FOUNDER)
    assert "Answer in one sentence." in prompt
    assert "Inside my command center I act without asking" not in prompt


def test_a_visitor_is_never_handed_the_operating_instructions(data_dir):
    udir = _seed(data_dir)
    (udir / "AGENTS.md").write_text("OWNER-ONLY RULES\n", encoding="utf-8")
    prompt = ui._build_persona_system_prompt(udir, universe_id="u-test", tier=interlocutor.T1)
    assert "OWNER-ONLY RULES" not in prompt
    assert "How I work" not in prompt


@posix_only
def test_a_linked_instructions_file_is_never_followed_or_overwritten(data_dir, tmp_path):
    udir = _seed(data_dir)
    outside = tmp_path / "elsewhere.md"
    outside.write_text("FOREIGN RULES\n", encoding="utf-8")
    (udir / "AGENTS.md").symlink_to(outside)
    prompt = ui._build_persona_system_prompt(udir, universe_id="u-test", tier=interlocutor.FOUNDER)
    assert "FOREIGN RULES" not in prompt
    assert "no substitute instructions loaded" in prompt
    assert "Finish authorized work" not in prompt
    assert outside.read_text(encoding="utf-8") == "FOREIGN RULES\n"


def test_memory_no_longer_tells_the_model_to_re_ask_for_consent():
    from tinyassets.conversation_memory import format_history

    block = format_history([Msg(speaker="founder", text="hi", ts=1.0)], now=2.0)
    assert "consent recorded THIS turn" not in block
    assert "NEWEST message below is a live instruction" in block
