"""Session references and ephemeral native turns in isolated provider cells.

References retain conversation and steering identity. A Codex owner cell has
only a private temporary HOME, so it starts an ephemeral thread with the full
prompt and never resumes a record left by the previous launch architecture.
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


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_handle", [None, "0a1b2c3d-0000-4000-8000-000000000001"])
async def test_owner_cells_start_ephemeral_with_full_context(served, stale_handle):  # noqa: F811
    run, launch, _state, config, udir = served
    ref = _ref(udir, resume="only the new message")
    if stale_handle:
        agent_sessions.save(ref, adapter="codex", model=_session_model(), handle=stale_handle,
                            system="old system")
        store = agent_sessions.native_store(udir, "codex")
        (store / f"rollout-x-{stale_handle}.jsonl").write_text("{}")
    prior = agent_sessions.load(udir, ref.key)
    _, server = await run(cfg=config(agent_session=ref), system="current system")
    start = server.requests("thread/start")[0]["params"]
    assert start["ephemeral"] is True
    assert start["baseInstructions"] == "current system"
    assert server.requests("thread/resume") == []
    assert _sent(server) == "fresh prompt"
    assert "only the new message" not in json.dumps(server.received)
    assert "universe_view" not in launch.call_args.kwargs
    assert agent_sessions.load(udir, ref.key) == prior


@pytest.mark.asyncio
async def test_failed_ephemeral_turn_does_not_record_a_native_session(served):  # noqa: F811
    from tests.support.fake_codex_app_server import Turn

    run, _launch, _state, config, udir = served
    ref = _ref(udir)
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
