"""The universe agent's four tools: policy, wiring and refusals (no real jail).

What a jailed call can actually reach is proven in a REAL bubblewrap jail by
``tests/test_universe_tools_jail.py`` (run and asserted by
``.github/workflows/linux-jail-proof.yml``). This module holds what is true on
any host: the tool list and where it is served, the jail argv's shape, the
fail-closed refusals, the cross-user refusal, the edit semantics, and how the
skill index reaches the prompt.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.engine_authority_helpers import mock_engine_admission, seed_engine_authority
from tinyassets import universe_tools

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX descriptors and signals")

FOUR = ("read", "write", "edit", "bash")


def _universe(tmp_path: Path, name: str = "u-alpha") -> Path:
    path = tmp_path / "data" / name
    path.mkdir(parents=True)
    return path


# ── the tool list and where it is served ────────────────────────────────────


def test_exactly_four_tools_are_served_to_the_universe_agent_on_every_adapter():
    from tinyassets import engine_mcp_server as s
    from tinyassets.providers.base import ModelConfig
    from tinyassets.served_tools import FOUR_MODEL_TOOLS, SERVED_ENGINE_MCP_TOOLS, model_tools

    assert universe_tools.TOOL_NAMES == FOUR
    registered = {tool.name for tool in asyncio.run(s.mcp.list_tools())}
    # Codex declares the turn's model tools as app-server dynamicTools.
    codex = model_tools(ModelConfig(engine_mcp_enabled=True))
    for name in FOUR:
        assert name in registered
        assert name in SERVED_ENGINE_MCP_TOOLS  # the HTTP loop discovers from this
        assert name in codex  # codex
        assert name in FOUR_MODEL_TOOLS


def test_the_public_connector_gains_no_top_level_tool():
    from tinyassets import universe_server

    public = {tool.name for tool in asyncio.run(universe_server.mcp.list_tools())}
    assert not public & set(FOUR)
    assert {"read_graph", "write_graph", "run_graph", "read_page", "write_page",
            "converse"} <= public




def test_the_four_definitions_fit_the_harness_budget():
    """Design budget: tool definitions <= 500 tokens (chars / 4)."""
    from tinyassets import engine_mcp_server as s

    size = 0
    for tool in asyncio.run(s.mcp.list_tools()):
        if tool.name in FOUR:
            served = tool.to_mcp_tool().model_dump(exclude_none=True)
            size += len(json.dumps({k: served[k] for k in ("name", "description",
                                                           "inputSchema")}))
    assert size // 4 <= 500, size


# ── the engine pins and refusals ─────────────────────────────────────────────


def _bind_engine(monkeypatch, root: Path, *, actor: str, graph: str):
    import tinyassets.api.helpers as helpers
    from tinyassets import engine_mcp_server as s

    monkeypatch.setattr(helpers, "_base_path", lambda: root)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    monkeypatch.setattr(s, "_ACTOR_ID", actor)
    monkeypatch.setattr(s, "_GRAPH_ID", graph)
    return s


def _call_all_four(s) -> list[str]:
    return [
        asyncio.run(s.read_file(path="founder.md")),
        asyncio.run(s.write_file(path="notes/x.md", content="x")),
        asyncio.run(s.edit_file(path="founder.md", old_text="a", new_text="b")),
        asyncio.run(s.run_bash(command="cat founder.md")),
    ]


def test_another_user_cannot_drive_the_tools_over_someone_elses_universe(
    tmp_path, monkeypatch,
):
    """Real SQLite authority: universe A is owned by actor A. An engine bound to
    actor B and pinned at A refuses every tool before any box is reached."""
    root = tmp_path / "data"
    root.mkdir()
    (root / "u-a").mkdir()
    (root / "u-a" / "founder.md").write_text("A's private founder notes", encoding="utf-8")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    seed_engine_authority(root, actor="actor-a", graph="u-a")
    seed_engine_authority(root, actor="actor-b", graph="u-b")
    s = _bind_engine(monkeypatch, root, actor="actor-b", graph="u-a")
    for out in _call_all_four(s):
        assert "current serving owner authority" in out, out
        assert "private founder notes" not in out

    # The real owner, same universe, same code path, with no admitted session:
    # the tools refuse rather than touching a host path.
    s = _bind_engine(monkeypatch, root, actor="actor-a", graph="u-a")
    for out in _call_all_four(s):
        assert "no admitted session" in out, out
        assert "private founder notes" not in out


def test_an_unbound_engine_refuses_every_tool(monkeypatch):
    from tinyassets import engine_mcp_server as s

    monkeypatch.setattr(s, "_ACTOR_ID", "")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-x")
    mock_engine_admission(monkeypatch, {"u-x"})
    for out in _call_all_four(s):
        assert "refusing" in out


_SKILL = (
    "---\nname: standup\ndescription: When my founder says standup, answer with "
    "Yesterday / Today / Blockers bullets.\n---\n\nThree bullets.\n"
)


@posix_only
def test_skill_index_lists_name_and_description_only(tmp_path):
    universe = _universe(tmp_path)
    (universe / "skills" / "standup").mkdir(parents=True)
    (universe / "skills" / "standup" / "SKILL.md").write_text(_SKILL, encoding="utf-8")
    (universe / "skills" / "nodesc").mkdir()
    (universe / "skills" / "nodesc" / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    prompt = universe_tools.harness_prompt(universe)
    assert "- `standup`: When my founder says standup" in prompt
    assert "Three bullets" not in prompt, "the body is read on demand, not preloaded"
    assert "nodesc" not in prompt


@posix_only
def test_a_skill_file_symlinked_elsewhere_is_never_read_into_the_prompt(tmp_path):
    universe = _universe(tmp_path)
    foreign = _universe(tmp_path, "u-bravo") / "SKILL.md"
    foreign.write_text(_SKILL.replace("standup", "stolen"), encoding="utf-8")
    (universe / "skills" / "stolen").mkdir(parents=True)
    (universe / "skills" / "stolen" / "SKILL.md").symlink_to(foreign)
    (universe / "skills" / "linkdir").symlink_to(foreign.parent, target_is_directory=True)
    assert universe_tools.skill_index(universe) == []


# ── untrusted universe files: the shared safe reader and skill parse ─────────


def test_skill_description_never_hands_frontmatter_to_a_yaml_loader():
    """An alias bomb expands to gigabytes under yaml.safe_load; the flat parse
    returns fast with bounded memory and no description."""
    bomb = (
        "---\n"
        "a: &a [x,x,x,x,x,x,x,x,x]\n"
        "b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]\n"
        "c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]\n"
        "d: &d [*c,*c,*c,*c,*c,*c,*c,*c,*c]\n"
        "description: *d\n"
        "---\n"
    ).encode("utf-8")
    # The value is a YAML alias, never expanded: it is read as the literal text.
    out = universe_tools._skill_description(bomb)
    assert out == "*d"
    assert len(bomb) < 300 and len(out) < 300


def test_skill_description_caps_length_before_building_a_string():
    huge = ("---\ndescription: " + "z" * 100_000 + "\n---\n").encode("utf-8")
    out = universe_tools._skill_description(huge)
    assert len(out) <= universe_tools._MAX_DESCRIPTION_CHARS


@pytest.mark.parametrize("body", [
    b"no frontmatter at all",
    b"---\nname: x\n---\nno description key\n",
    b"---\ndescription:\n---\n",
    b"\xff\xfe not even utf-8 \x00",
    b"---\n" + b"description: " + "é".encode() * 10 + b"\n---\n",
])
def test_skill_description_never_raises(body):
    universe_tools._skill_description(body)  # returns a str, never throws


@posix_only
def test_skill_index_leaves_out_a_bad_skill_without_breaking(tmp_path):
    universe = _universe(tmp_path)
    (universe / "skills" / "good").mkdir(parents=True)
    (universe / "skills" / "good" / "SKILL.md").write_text(_SKILL, encoding="utf-8")
    (universe / "skills" / "bomb").mkdir()
    (universe / "skills" / "bomb" / "SKILL.md").write_text(
        "---\ndescription: &a [*a]\n---\n", encoding="utf-8",
    )
    names = {name for name, _ in universe_tools.skill_index(universe)}
    assert "good" in names  # the turn still gets the working skills


def test_read_universe_file_refuses_a_symlink_component(tmp_path):
    from tinyassets.universe_files import read_universe_file

    universe = _universe(tmp_path)
    (universe / "founder.md").write_text("mine", encoding="utf-8")
    assert read_universe_file(universe, "founder.md") == b"mine"
    outside = tmp_path / "secret.txt"
    outside.write_text("SOMEONE ELSE", encoding="utf-8")
    try:
        (universe / "leak.md").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a symlink")
    with pytest.raises(OSError):
        read_universe_file(universe, "leak.md")


def test_read_universe_file_bounds_size(tmp_path):
    from tinyassets.universe_files import read_universe_file

    universe = _universe(tmp_path)
    (universe / "big.md").write_text("z" * 5000, encoding="utf-8")
    assert read_universe_file(universe, "big.md", max_bytes=10_000) == b"z" * 5000
    with pytest.raises(OSError):
        read_universe_file(universe, "big.md", max_bytes=100)


def test_a_planted_link_is_not_followed_by_the_persona_read(tmp_path):
    """A link that already exists (planted from outside, or via io_uring the
    seccomp cannot see) is refused by the daemon-side bundle read."""
    import tinyassets.universe_intelligence as ui

    universe = _universe(tmp_path)
    secret = tmp_path / "other" / "founder.md"
    secret.parent.mkdir()
    secret.write_text("ANOTHER USER'S PRIVATE FOUNDER", encoding="utf-8")
    (universe / "founder.md").write_text("my own founder notes", encoding="utf-8")
    assert ui._read_bundle_body(universe, "founder.md") == "my own founder notes"
    (universe / "founder.md").unlink()
    try:
        (universe / "founder.md").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a symlink")
    assert ui._read_bundle_body(universe, "founder.md") == ""


def _founder_turn(monkeypatch, root: Path, uid: str, message: str, *, founder: bool):
    """One real converse turn; returns the system prompt the model received."""
    import tinyassets.universe_intelligence as ui
    from tinyassets.auth import middleware as auth

    seen: dict = {}

    def model(prompt, system="", **_kw):
        seen["system"] = system
        return "ok"

    monkeypatch.setattr(ui, "_request_universe", lambda universe_id="": uid)
    monkeypatch.setattr(ui, "_universe_dir", lambda _uid: root / uid)
    monkeypatch.setattr(ui, "text_sync", model)
    monkeypatch.setattr(ui, "_build_persona_system_prompt", lambda *a, **k: "I am u.")
    monkeypatch.setattr(ui, "extract_learning", lambda *a, **k: None)
    monkeypatch.setattr(ui, "commit_learning", lambda *a, **k: None)
    tier = ui.interlocutor.FOUNDER if founder else ui.interlocutor.T1
    monkeypatch.setattr(ui.interlocutor, "resolve_interlocutor_tier",
                        lambda *_a, **_k: SimpleNamespace(tier=tier))
    reserve = auth.reserve_provider_request(
        principal_id="actor-a", session_id="s", request_id=message, tool_name="converse",
    )
    capability = auth.claim_provider_request(reserve, tool_name="converse")
    try:
        ui.converse(uid, message)
    finally:
        auth.revoke_provider_request(capability)
    return seen["system"]


@posix_only
def test_only_a_founder_turn_with_the_tools_is_shown_the_folder_and_skills(
    tmp_path, monkeypatch,
):
    root = tmp_path / "data"
    (root / "u-a" / "skills" / "standup").mkdir(parents=True)
    (root / "u-a" / "skills" / "standup" / "SKILL.md").write_text(_SKILL, encoding="utf-8")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    seed_engine_authority(root, actor="actor-a", graph="u-a")

    founder = _founder_turn(monkeypatch, root, "u-a", "hi", founder=True)
    assert "/u is the workspace. Use read/write/edit/bash." in founder
    assert "- `standup`:" in founder

    visitor = _founder_turn(monkeypatch, root, "u-a", "hi again", founder=False)
    assert "My folder" not in visitor and "standup" not in visitor

    monkeypatch.delenv("TINYASSETS_ENGINE_MCP_TOOLS")
    dark = _founder_turn(monkeypatch, root, "u-a", "hi dark", founder=True)
    assert "My folder" not in dark


# ── the class: config.yaml / soul-edit frontmatter / soul_versions ───────────

_ALIAS_BOMB = (
    "a: &a [x,x,x,x,x,x,x,x,x]\n"
    "b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]\n"
    "c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]\n"
    "d: &d [*c,*c,*c,*c,*c,*c,*c,*c,*c]\n"
    "e: &e [*d,*d,*d,*d,*d,*d,*d,*d,*d]\n"
    "timeout: 777\n"
)


def test_an_oversized_config_yaml_is_never_parsed(tmp_path, caplog):
    import time

    from tinyassets.config import UniverseConfig, load_universe_config

    universe = _universe(tmp_path)
    body = "timeout: 999\n" + "".join(f"k{i}: v{i}\n" for i in range(300_000))
    (universe / "config.yaml").write_text(body, encoding="utf-8")
    assert len(body) > 4 * 1024 * 1024
    started = time.monotonic()
    config = load_universe_config(universe)
    assert time.monotonic() - started < 1.0
    assert config.timeout == UniverseConfig().timeout, "defaults, not the 999 inside"
    assert "refused" in caplog.text


def test_an_alias_bomb_config_yaml_is_refused_before_expansion(tmp_path):
    import tracemalloc

    from tinyassets.config import UniverseConfig, load_universe_config

    universe = _universe(tmp_path)
    (universe / "config.yaml").write_text(_ALIAS_BOMB, encoding="utf-8")
    tracemalloc.start()
    try:
        config = load_universe_config(universe)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert config.timeout == UniverseConfig().timeout and config.extra == {}
    assert peak < 8 * 1024 * 1024, peak


def test_a_linked_config_yaml_is_not_followed(tmp_path):
    from tinyassets.config import UniverseConfig, load_universe_config

    universe = _universe(tmp_path)
    foreign = _universe(tmp_path, "u-bravo") / "config.yaml"
    foreign.write_text("timeout: 4242\n", encoding="utf-8")
    try:
        (universe / "config.yaml").symlink_to(foreign)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a symlink")
    assert load_universe_config(universe).timeout == UniverseConfig().timeout


def test_a_strict_config_write_refuses_to_erase_an_unreadable_config(tmp_path):
    from tinyassets.config import write_provider_assignment_projection

    universe = _universe(tmp_path)
    (universe / "config.yaml").write_text(_ALIAS_BOMB, encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        write_provider_assignment_projection(universe, state="unassigned", generation=0)
    assert (universe / "config.yaml").read_text(encoding="utf-8") == _ALIAS_BOMB


def _governed_universe(tmp_path: Path) -> Path:
    from tinyassets.universe_bundle import seed_okf_bundle

    universe = _universe(tmp_path)
    seed_okf_bundle(universe, purpose="help", loop_branch_def_id="")
    return universe


def test_a_soul_edit_refuses_alias_frontmatter_without_expanding_it(tmp_path):
    from tinyassets.soul_edit import SoulEditError, apply_soul_edit

    universe = _governed_universe(tmp_path)
    (universe / "identity.md").write_text("---\n" + _ALIAS_BOMB + "---\nbody\n",
                                          encoding="utf-8")
    with pytest.raises(SoulEditError, match="refused"):
        apply_soul_edit(universe, changes={"identity.md": "new identity body"},
                        agent_id="main", source="test", context="c", summary="s")


def test_a_soul_edit_refuses_an_oversized_governed_file(tmp_path):
    from tinyassets.soul_edit import SoulEditError, apply_soul_edit

    universe = _governed_universe(tmp_path)
    (universe / "identity.md").write_text("---\nname: x\n---\n" + "z" * (2 * 1024 * 1024),
                                          encoding="utf-8")
    with pytest.raises(SoulEditError, match="over its bound"):
        apply_soul_edit(universe, changes={"identity.md": "new identity body"},
                        agent_id="main", source="test", context="c", summary="s")


def test_soul_versions_are_listed_and_read_without_following_links(tmp_path):
    from tinyassets.universe_soul import SOUL_VERSIONS_DIR, _matching_soul_version_id

    universe = _universe(tmp_path)
    versions = universe / SOUL_VERSIONS_DIR
    versions.mkdir()
    (versions / "0001.md").write_text("own soul", encoding="utf-8")
    assert _matching_soul_version_id(universe, "own soul") == f"{SOUL_VERSIONS_DIR}/0001.md"
    foreign = _universe(tmp_path, "u-bravo") / "soul.md"
    foreign.write_text("FOREIGN SOUL", encoding="utf-8")
    try:
        (versions / "0002.md").symlink_to(foreign)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a symlink")
    assert _matching_soul_version_id(universe, "FOREIGN SOUL") is None


def test_agent_owned_paths_are_pinned():
    """Widening what the agent may write makes new daemon readers untrusted.

    Changing this set needs, in the same change: every reader of the new path
    routed through tinyassets.universe_files and its module added to TURN_PATH
    in tests/test_universe_file_reads_are_bounded.py
    (docs/concerns/2026-09-24-universe-file-readers-outside-the-turn-path.md).
    """
    assert universe_tools.AGENT_BRAIN_FILES == (
        "identity.md", "founder.md", "origin.md", "body.md", "orgchart.md",
        "projects.md", "goals.md", "index.md", "log.md", "voice.md", "AGENTS.md",
        # D7a: the agent writes its own memory continuously, so a root MEMORY.md
        # is bind-mounted and promoted. Its readers (memory_items,
        # harness_history) go through universe_files and are in TURN_PATH.
        "MEMORY.md",
    )
    assert universe_tools.AGENT_HARNESS_DIRS == (
        "skills", "prompts", "extensions", "workflows", "bin", "notes", "wiki",
    )
