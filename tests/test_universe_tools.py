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
from tinyassets.providers import provider_jail
from tinyassets.providers.provider_jail import ProviderConfinementError, default_view, jail_argv
from tinyassets.universe_tools import ToolRun, UniverseToolError

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX descriptors and signals")

FOUR = ("read", "write", "edit", "bash")


def _universe(tmp_path: Path, name: str = "u-alpha") -> Path:
    path = tmp_path / "data" / name
    path.mkdir(parents=True)
    return path


class _Spy:
    """A RUNNER that records every jailed call and answers from a script."""

    def __init__(self, *answers: ToolRun) -> None:
        self.calls: list[dict] = []
        self._answers = list(answers)

    def __call__(self, universe_dir, inner, **kwargs):
        self.calls.append({"universe_dir": universe_dir, "inner": list(inner), **kwargs})
        if not self._answers:
            return ToolRun(0, b"", None, 0.0)
        return self._answers.pop(0)


def _ok(output: bytes = b"") -> ToolRun:
    return ToolRun(exit_code=0, output=output, killed=None, elapsed=0.0)


# ── the tool list and where it is served ────────────────────────────────────


def test_exactly_four_tools_are_served_to_the_universe_agent_on_every_adapter():
    from tinyassets import engine_mcp_server as s
    from tinyassets.providers.codex_provider import _ENGINE_MCP_ENABLED_TOOLS
    from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS
    from tinyassets.universe_intelligence import _ENGINE_MCP_ALLOWED

    assert universe_tools.TOOL_NAMES == FOUR
    registered = {tool.name for tool in asyncio.run(s.mcp.list_tools())}
    for name in FOUR:
        assert name in registered
        assert name in SERVED_ENGINE_MCP_TOOLS  # the HTTP loop discovers from this
        assert name in _ENGINE_MCP_ENABLED_TOOLS  # codex
        assert f"mcp__tinyassets__{name}" in _ENGINE_MCP_ALLOWED  # claude


def test_the_public_connector_gains_no_top_level_tool():
    from tinyassets import universe_server

    public = {tool.name for tool in asyncio.run(universe_server.mcp.list_tools())}
    assert not public & set(FOUR)
    assert {"read_graph", "write_graph", "run_graph", "read_page", "write_page",
            "converse"} <= public


def test_the_vendor_cli_file_and_shell_builtins_stay_denied():
    """The platform runs the tools; the CLI's own Read/Bash never come back."""
    from tinyassets.universe_intelligence import _ENGINE_DISALLOWED_TOOLS_WITH_MCP

    for builtin in ("Bash", "Read", "Write", "Edit", "Glob", "Grep", "Monitor"):
        assert builtin in _ENGINE_DISALLOWED_TOOLS_WITH_MCP


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


# ── the tool jail argv ──────────────────────────────────────────────────────


def _pairs(argv: list[str], flag: str) -> list[tuple[str, str]]:
    return [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv[:-2]) if a == flag]


@pytest.mark.parametrize("agent_id,identity_flag", [
    ("agent_binding_w1", "--ro-bind-try"),
    ("main", "--bind-try"),
])
def test_identity_mount_is_writable_only_for_main(tmp_path, monkeypatch, agent_id, identity_flag):
    universe = _universe(tmp_path)
    for name in ("identity.md", "founder.md"):
        (universe / name).write_text("original", encoding="utf-8")
    monkeypatch.setattr(provider_jail, "BWRAP_RESOLVER", lambda: "/usr/bin/bwrap")
    argv = universe_tools.tool_jail_argv(universe, ["/bin/true"], agent_id=agent_id)
    identity = (str(universe.resolve() / "identity.md"), "/u/identity.md")
    assert identity in _pairs(argv, identity_flag)
    other_flag = "--bind-try" if identity_flag == "--ro-bind-try" else "--ro-bind-try"
    assert identity not in _pairs(argv, other_flag)
    assert (str(universe.resolve() / "founder.md"), "/u/founder.md") in _pairs(
        argv, "--bind-try")


@pytest.mark.parametrize("agent_id", ["", " \t\n"])
def test_tool_jail_refuses_blank_agent_id(tmp_path, agent_id):
    with pytest.raises(UniverseToolError, match="agent_id is required"):
        universe_tools.tool_jail_argv(tmp_path, ["/bin/true"], agent_id=agent_id)
    with pytest.raises(UniverseToolError, match="agent_id is required"):
        universe_tools._universe_view(tmp_path, agent_id=agent_id)


def test_tool_jail_argv_has_no_network_no_env_and_only_the_universe_at_u(
    tmp_path, monkeypatch,
):
    universe = _universe(tmp_path)
    (universe / "identity.md").write_text("---\nname: A\n---\n", encoding="utf-8")
    monkeypatch.setattr(provider_jail, "BWRAP_RESOLVER", lambda: "/usr/bin/bwrap")
    argv = universe_tools.tool_jail_argv(universe, ["/bin/true"], agent_id="main")
    root = str(universe.resolve())

    assert "--share-net" not in argv
    for flag in ("--unshare-all", "--clearenv", "--die-with-parent", "--new-session"):
        assert flag in argv, flag
    # /u is the agent's OWN workspace, bound read-write as a whole (harness W2);
    # the visible root entries are bound over it, agent-owned ones read-write.
    workspace = str(universe.resolve() / universe_tools.WORKSPACE_DIR)
    assert ("--bind", workspace, "/u") == tuple(argv[argv.index(workspace) - 1:
                                                     argv.index(workspace) + 2])
    workspace_at = argv.index(workspace)
    assert all(workspace_at < argv.index(dest) for _src, dest in (
        _pairs(argv, "--bind-try") + _pairs(argv, "--ro-bind-try")))
    assert "--remount-ro" not in argv
    assert root not in argv, "the root itself is never bound"
    rw = dict((dest, src) for src, dest in _pairs(argv, "--bind-try"))
    assert rw["/u/identity.md"] == str(universe.resolve() / "identity.md")
    assert rw["/u/skills"] == str(universe.resolve() / "skills")
    assert "/u" not in rw
    assert set(rw) <= {f"/u/{name}" for name in (
        *universe_tools.AGENT_BRAIN_FILES, *universe_tools.AGENT_HARNESS_DIRS)}
    for name in universe_tools.AGENT_HARNESS_DIRS:
        assert (universe / name).is_dir(), f"harness dir {name} is created first"
    # Nothing else of the data root, and no credential snapshot or install tree.
    for source, _dest in (_pairs(argv, "--bind") + _pairs(argv, "--ro-bind")
                          + _pairs(argv, "--bind-try") + _pairs(argv, "--ro-bind-try")):
        assert source.startswith(root) or not source.startswith(str(tmp_path)), source
    env = {argv[i + 1]: argv[i + 2] for i, a in enumerate(argv) if a == "--setenv"}
    assert env["HOME"] == "/tmp" and set(env) == {"PATH", "HOME", "LANG", "TERM"}
    assert argv[argv.index("--chdir") + 1] == "/u"
    assert argv[argv.index("--") + 1:] == ["/bin/true"]


def test_the_owners_credentials_and_authority_state_are_absent_from_the_jail(
    tmp_path, monkeypatch,
):
    """The credential vault and the consent / usage databases live in the
    universe ROOT, not .runtime. No hidden root entry is in the jail at all:
    not bound, not masked. Visible platform files are read-only."""
    universe = _universe(tmp_path)
    (universe / ".credential-vault.json").write_text('{"k": "SECRET"}', encoding="utf-8")
    (universe / ".credentials").mkdir()
    (universe / ".effector_consents.db").write_bytes(b"sqlite")
    (universe / ".effector_consents.db-shm").write_bytes(b"shm")
    (universe / ".usage_ledger.db").write_bytes(b"sqlite")
    (universe / ".runtime").mkdir()
    (universe / ".claude").mkdir()
    (universe / "soul.md").write_text("# soul", encoding="utf-8")
    (universe / "config.yaml").write_text("timeout: 5\n", encoding="utf-8")
    monkeypatch.setattr(provider_jail, "BWRAP_RESOLVER", lambda: "/usr/bin/bwrap")
    argv = universe_tools.tool_jail_argv(universe, ["/bin/true"], agent_id="main")

    workspace = str(universe.resolve() / universe_tools.WORKSPACE_DIR)
    for arg in argv:
        # The one hidden name allowed is the agent's own workspace, as /u's source.
        assert "/." not in arg or arg == workspace, (
            f"a hidden root entry reached the jail argv: {arg}")
    ro = {dest for _src, dest in _pairs(argv, "--ro-bind-try")}
    assert {"/u/soul.md", "/u/config.yaml"} <= ro
    rw = {dest for _src, dest in _pairs(argv, "--bind-try")}
    assert not {"/u/soul.md", "/u/config.yaml"} & rw


def test_a_symlinked_root_entry_is_never_bound(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    other = _universe(tmp_path, "u-bravo")
    try:
        (universe / ".runtime").symlink_to(other, target_is_directory=True)
        (universe / "notes").symlink_to(other, target_is_directory=True)
        (universe / "founder.md").symlink_to(other / "founder.md")
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a symlink")
    monkeypatch.setattr(provider_jail, "BWRAP_RESOLVER", lambda: "/usr/bin/bwrap")
    argv = universe_tools.tool_jail_argv(universe, ["/bin/true"], agent_id="main")
    assert not any(str(other.resolve()) in arg for arg in argv)
    assert "/u/notes" not in argv and "/u/founder.md" not in argv


def test_an_entry_gone_before_the_launch_is_skipped_not_refused(tmp_path, monkeypatch):
    """The daemon owns the folder concurrently: a SQLite sidecar or a temp file
    that exists at the scan may be gone when bubblewrap runs. Every bind is a
    ``-try``, and the view still validates when its source has vanished."""
    universe = _universe(tmp_path)
    (universe / "story.db-shm").write_bytes(b"shm")
    view = universe_tools._universe_view(universe.resolve(), agent_id="main")
    (universe / "story.db-shm").unlink()
    argv = jail_argv(["/bin/true"], view, bwrap_path="/usr/bin/bwrap")
    assert (str(universe.resolve() / "story.db-shm"), "/u/story.db-shm") in _pairs(
        argv, "--ro-bind-try")
    # Replaced by a link after the scan: the argv is refused, not bound to it.
    other = _universe(tmp_path, "u-bravo")
    (universe / "lore").mkdir()
    view = universe_tools._universe_view(universe.resolve(), agent_id="main")
    (universe / "lore").rmdir()
    try:
        (universe / "lore").symlink_to(other, target_is_directory=True)
    except (OSError, NotImplementedError):
        pass
    else:
        with pytest.raises(ProviderConfinementError, match="inside its own command center"):
            jail_argv(["/bin/true"], view, bwrap_path="/usr/bin/bwrap")
    with pytest.raises(ProviderConfinementError, match="does not exist"):
        jail_argv(["/bin/true"], provider_jail.UniverseView(
            universe_dir=universe, mounts=(provider_jail.JailMount(
                "ro-bind", "/u/gone", universe / "gone"),),
        ), bwrap_path="/usr/bin/bwrap")


def test_the_jail_loads_a_filter_refusing_links_and_special_files(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    monkeypatch.setattr(provider_jail, "BWRAP_RESOLVER", lambda: "/usr/bin/bwrap")
    argv = universe_tools.tool_jail_argv(universe, ["/bin/true"], agent_id="main", seccomp_fd=7)
    assert argv[argv.index("--seccomp") + 1] == "7"
    assert argv.index("--seccomp") < argv.index("--")

    # The tool jail runs no CLI sandbox of its own, so it gets the full filter:
    # links, special files, io_uring, new user namespaces and the kernel
    # interfaces. What each one decides is asserted in tests/test_jail_seccomp.py.
    from tinyassets.providers.jail_seccomp import deny_program

    assert universe_tools.seccomp_program() == deny_program(nested_sandbox=False)


def test_limits_wrap_the_command_and_prove_themselves_before_it_runs(monkeypatch):
    monkeypatch.setattr(universe_tools.shutil, "which",
                        lambda name, path=None: f"/usr/bin/{name}")
    limits = universe_tools.ToolLimits(memory_bytes=1, processes=2, cpu_seconds=3,
                                       file_bytes=4, open_files=5)
    wrapped = universe_tools._limited(["/usr/bin/bash", "-c", "x"], limits, cpu_seconds=3)
    assert wrapped[0] == "/usr/bin/prlimit"
    assert wrapped[1:8] == ["--as=1", "--nproc=2", "--cpu=3:4", "--fsize=4", "--nofile=5",
                            "--core=0", "--"]
    assert wrapped[-3:] == ["/usr/bin/bash", "-c", "x"]
    assert universe_tools._LIMITS_MARK.decode() in wrapped


def test_no_prlimit_refuses_before_anything_runs(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    monkeypatch.setattr(universe_tools.shutil, "which", lambda name, path=None: None)
    spawned = []
    monkeypatch.setattr(universe_tools.subprocess, "Popen",
                        lambda *a, **k: spawned.append(a))
    with pytest.raises(UniverseToolError, match="prlimit"):
        universe_tools.run_jailed(universe, ["/bin/true"], agent_id="main")
    assert spawned == []


def test_no_os_sandbox_refuses_before_anything_runs(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    monkeypatch.setattr(universe_tools.shutil, "which",
                        lambda name, path=None: f"/usr/bin/{name}")

    def no_bwrap():
        raise ProviderConfinementError("provider launch refused: no OS sandbox")

    monkeypatch.setattr(provider_jail, "BWRAP_RESOLVER", no_bwrap)
    spawned = []
    monkeypatch.setattr(universe_tools.subprocess, "Popen",
                        lambda *a, **k: spawned.append(a))
    with pytest.raises(ProviderConfinementError):
        universe_tools.run_jailed(universe, ["/bin/true"], agent_id="main")
    assert spawned == []


@posix_only
def test_a_jail_that_never_proves_its_limits_is_refused(tmp_path, monkeypatch):
    """Output without the marker means the limits were not in place: refuse."""
    universe = _universe(tmp_path)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(universe_tools.shutil, "which",
                        lambda name, path=None: f"/usr/bin/{name}")
    monkeypatch.setattr(universe_tools, "TOOL_JAIL_ARGV",
                        lambda udir, inner, **_kw: ["/bin/sh", "-c", "echo unlimited output"])
    with pytest.raises(UniverseToolError, match="did not start under its resource limits"):
        universe_tools.run_jailed(universe, ["/bin/true"], agent_id="main")


@posix_only
def test_a_root_run_jail_without_a_cgroup_is_refused(tmp_path, monkeypatch):
    """Root is exempt from RLIMIT_NPROC: no cgroup to hold it, no call."""
    universe = _universe(tmp_path)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(universe_tools.os, "geteuid", lambda: 0)
    monkeypatch.setattr(universe_tools, "CGROUP_ROOT", tmp_path / "no-cgroup")
    monkeypatch.setattr(universe_tools.shutil, "which",
                        lambda name, path=None: f"/usr/bin/{name}")
    monkeypatch.setattr(universe_tools, "TOOL_JAIL_ARGV",
                        lambda udir, inner, **_kw: ["/bin/true"])
    spawned = []
    monkeypatch.setattr(universe_tools.subprocess, "Popen",
                        lambda *a, **k: spawned.append(a))
    with pytest.raises(UniverseToolError, match="exempts root from the process limit"):
        universe_tools.run_jailed(universe, ["/bin/true"], agent_id="main")
    assert spawned == []


# ── the launch jail masks vendor-native harness dirs ────────────────────────


@posix_only
def test_a_provider_launch_view_masks_every_hidden_root_dir(tmp_path):
    """A CLI's own project settings dir is never a loading mechanism, for any
    CLI: every hidden root dir but .runtime is an empty tmpfs at launch."""
    universe = _universe(tmp_path).resolve()
    for name in (".claude", ".codex", ".some-future-cli"):
        (universe / name).mkdir()
        (universe / name / "settings.json").write_text('{"hooks": {}}', encoding="utf-8")
    (universe / ".runtime").mkdir()
    view = default_view(universe)
    argv = jail_argv(["cli", "-p"], view, bwrap_path="/usr/bin/bwrap")
    for name in (".claude", ".codex", ".some-future-cli"):
        mask = argv.index(f"{universe}/{name}")
        assert argv[mask - 1] == "--tmpfs", name
        assert mask > argv.index(str(universe)), "the mask sits over the universe bind"
    assert f"{universe}/.runtime" not in argv, "the launch still needs its runtime"
    # No jail shares the host network; only the tool jail clears the env.
    assert "--share-net" not in argv and "--clearenv" not in argv


def test_a_provider_launch_view_masks_every_hidden_root_file(tmp_path):
    """Per-universe platform state at the root -- the credential vault, the run,
    consent and usage databases -- is a /dev/null bind, so the provider can
    neither read it nor replace it with a link the daemon would follow."""
    universe = _universe(tmp_path).resolve()
    for name in (".credential-vault.json", ".runs.db", ".runs.db-wal",
                 ".effector_consents.db", ".usage.db", ".some-future-cli-config"):
        (universe / name).write_text("platform state", encoding="utf-8")
    (universe / ".runtime").mkdir()
    view = default_view(universe)
    argv = jail_argv(["cli", "-p"], view, bwrap_path="/usr/bin/bwrap")
    universe_bind = argv.index(str(universe))
    for name in (".credential-vault.json", ".runs.db", ".runs.db-wal",
                 ".effector_consents.db", ".usage.db", ".some-future-cli-config"):
        mask = argv.index(f"{universe}/{name}")
        assert argv[mask - 2] == "--ro-bind" and argv[mask - 1] == "/dev/null", name
        assert mask > universe_bind, "the mask sits over the universe bind"
    assert f"{universe}/.runtime" not in argv, "the launch still needs its runtime"


def test_a_provider_launch_refuses_a_symlinked_hidden_file(tmp_path):
    """A hidden root entry that is already a link cannot be masked over, so the
    launch is refused rather than following it."""
    universe = _universe(tmp_path).resolve()
    (tmp_path / "elsewhere.db").write_text("other", encoding="utf-8")
    try:
        (universe / ".runs.db").symlink_to(tmp_path / "elsewhere.db")
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a symlink")
    with pytest.raises(ProviderConfinementError, match="is a link"):
        default_view(universe)


def test_a_provider_launch_refuses_a_symlinked_hidden_dir(tmp_path):
    universe = _universe(tmp_path).resolve()
    try:
        (universe / ".codex").symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a symlink")
    with pytest.raises(ProviderConfinementError, match="is a link"):
        default_view(universe)


def test_the_engine_route_bearer_config_lives_under_masked_runtime(tmp_path, monkeypatch):
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.claude_provider import _engine_mcp_flags

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    legacy = tmp_path / ".engine_mcp_config.json"
    legacy.write_text('{"stale": "bearer"}', encoding="utf-8")
    flags = _engine_mcp_flags(
        ModelConfig(engine_mcp_actor_id="a", engine_mcp_graph_id="u-a"), tmp_path,
    )
    config = Path(flags[flags.index("--mcp-config") + 1])
    assert config == tmp_path / ".runtime" / "engine-mcp-config.json"
    assert ".runtime" in universe_tools.MASKED_DIRS
    assert not legacy.exists(), "no stale bearer left where the agent can read it"


# ── the four tools' semantics (runner substituted) ──────────────────────────


def test_edit_replaces_exactly_one_match_and_writes_it_back(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    spy = _Spy(_ok(b"alpha\nbeta\n"), _ok())
    monkeypatch.setattr(universe_tools, "RUNNER", spy)
    assert universe_tools.edit_file(universe, "notes/a.md", "beta", "gamma", agent_id="main") == (
        "edited /u/notes/a.md"
    )
    write = spy.calls[1]
    assert write["stdin"] == b"alpha\ngamma\n"
    assert write["inner"][-1] == "/u/notes/a.md"


@pytest.mark.parametrize(("content", "expect"), [
    (b"alpha\n", "was not found"),
    (b"beta beta\n", "matches 2 places"),
])
def test_edit_refuses_an_ambiguous_or_missing_passage(tmp_path, monkeypatch, content, expect):
    universe = _universe(tmp_path)
    spy = _Spy(_ok(content))
    monkeypatch.setattr(universe_tools, "RUNNER", spy)
    assert expect in universe_tools.edit_file(universe, "a.md", "beta", "x", agent_id="main")
    assert len(spy.calls) == 1, "nothing is written back"


def test_paths_are_the_jails_paths_and_the_jail_is_the_boundary(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    spy = _Spy()
    monkeypatch.setattr(universe_tools, "RUNNER", spy)
    universe_tools.read_file(universe, "skills/x/SKILL.md", agent_id="main")
    universe_tools.read_file(universe, "/data/u-bravo/founder.md", agent_id="main")
    assert spy.calls[0]["inner"][4] == "/u/skills/x/SKILL.md"
    # Not rewritten, not "cleaned": outside /u it simply does not exist in the jail.
    assert spy.calls[1]["inner"][4] == "/data/u-bravo/founder.md"
    with pytest.raises(UniverseToolError):
        universe_tools.read_file(universe, "", agent_id="main")


@pytest.mark.parametrize(("run", "trailer"), [
    (ToolRun(0, b"hi\n", None, 0.1), "[exit code 0]"),
    (ToolRun(137, b"", "timeout", 5.0), "ran longer than"),
    (ToolRun(137, b"y\n" * 10, "output_limit", 0.1), "output passed"),
    (ToolRun(137, b"", "memory_limit", 0.1), "used more than"),
    (ToolRun(137, b"", "process_limit", 0.1), "more than 64 processes"),
    (ToolRun(137, b"", "disk_limit", 0.1), "disk was nearly full"),
    (ToolRun(152, b"", None, 2.0), "cpu time limit"),
    (ToolRun(137, b"", None, 2.0), "killed by the kernel"),
])
def test_bash_reports_how_the_command_ended(tmp_path, monkeypatch, run, trailer):
    universe = _universe(tmp_path)
    monkeypatch.setattr(universe_tools.shutil, "which",
                        lambda name, path=None: f"/usr/bin/{name}")
    monkeypatch.setattr(universe_tools, "RUNNER", _Spy(run))
    assert trailer in universe_tools.bash(universe, "echo hi", agent_id="main")


def test_bash_clamps_its_wall_clock(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    monkeypatch.setattr(universe_tools.shutil, "which",
                        lambda name, path=None: f"/usr/bin/{name}")
    spy = _Spy(_ok(), _ok())
    monkeypatch.setattr(universe_tools, "RUNNER", spy)
    universe_tools.bash(universe, "true", agent_id="main", timeout=99999)
    universe_tools.bash(universe, "true", agent_id="main")
    assert spy.calls[0]["wall_seconds"] == universe_tools.MAX_BASH_SECONDS
    assert spy.calls[1]["wall_seconds"] == universe_tools.DEFAULT_LIMITS.wall_seconds


# ── cross-user refusal through the engine surface ───────────────────────────


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
    actor B and pinned at A refuses every tool before any jail is built."""
    root = tmp_path / "data"
    root.mkdir()
    (root / "u-a").mkdir()
    (root / "u-a" / "founder.md").write_text("A's private founder notes", encoding="utf-8")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    seed_engine_authority(root, actor="actor-a", graph="u-a")
    seed_engine_authority(root, actor="actor-b", graph="u-b")
    spy = _Spy()
    monkeypatch.setattr(universe_tools, "RUNNER", spy)

    s = _bind_engine(monkeypatch, root, actor="actor-b", graph="u-a")
    for out in _call_all_four(s):
        assert "current serving owner authority" in out, out
        assert "private founder notes" not in out
    assert spy.calls == [], "no jail was built for a foreign universe"

    # Control: the real owner, same universe, same code path, reaches the runner.
    s = _bind_engine(monkeypatch, root, actor="actor-a", graph="u-a")
    monkeypatch.setattr(universe_tools.shutil, "which",
                        lambda name, path=None: f"/usr/bin/{name}")
    _call_all_four(s)
    assert spy.calls and all(c["universe_dir"] == root / "u-a" for c in spy.calls)


def test_an_unbound_engine_refuses_every_tool(monkeypatch):
    from tinyassets import engine_mcp_server as s

    monkeypatch.setattr(s, "_ACTOR_ID", "")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-x")
    mock_engine_admission(monkeypatch, {"u-x"})
    for out in _call_all_four(s):
        assert "refusing" in out


def test_a_jail_refusal_reaches_the_agent_as_an_error_not_a_crash(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "u-a").mkdir(parents=True)
    s = _bind_engine(monkeypatch, root, actor="actor-a", graph="u-a")
    mock_engine_admission(monkeypatch, {"u-a"})

    def refuse(*_a, **_k):
        raise ProviderConfinementError("provider launch refused: no OS sandbox")

    monkeypatch.setattr(universe_tools, "RUNNER", refuse)
    assert asyncio.run(s.read_file(path="x")).startswith("error: provider launch refused")


# ── the skill index reaches the prompt ──────────────────────────────────────

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
    monkeypatch.setattr(ui, "call_provider", model)
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
    assert "# My folder and my four tools" in founder
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


# ---------------------------------------------------------------------------
# Host slots: a busy host WAITS, and there is no per-universe count
# ---------------------------------------------------------------------------


def test_there_is_no_per_universe_tool_slot_count():
    """``_PER_UNIVERSE_SLOTS = 2`` and the 30s refusal deadline are gone.

    They were a second, account-shaped ceiling stacked on the host floor: a
    universe was told it could not run a third tool even on an otherwise idle
    host, and a busy host refused after thirty seconds. An account has exactly
    two limits, cloud bytes and concurrent agent seats (founder, 2026-09-30), and
    over the concurrency line work WAITS.
    """
    import inspect

    assert not hasattr(universe_tools, "_PER_UNIVERSE_SLOTS")
    assert not hasattr(universe_tools, "_SLOT_WAIT_SECONDS")
    # The host floor stays -- it is what keeps one universe from being an outage
    # for the others on a 1 vCPU box.
    assert universe_tools._HOST_SLOTS == 4

    # Read the CODE, not the prose: the docstring and comments explain what was
    # removed, so parse the function and unparse it without its docstring.
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(universe_tools._slot)))
    fn = tree.body[0]
    if ast.get_docstring(fn) is not None:
        fn.body = fn.body[1:]
    body = ast.unparse(fn)
    assert "deadline" not in body, "a deadline here is a refusal wearing a timeout"
    assert "raise" not in body, "_slot must never refuse for the host being busy"
    assert "UniverseToolError" not in body


@posix_only
def test_a_full_host_makes_the_next_call_wait_and_then_run(tmp_path, monkeypatch):
    """Every host slot held: the next caller queues, is told it is waiting, and runs.

    Drives the real ``_slot`` with real ``flock`` files. The held slots are
    released after 0.25 s, so what this proves is the WAIT resolving into a run
    and ``on_wait`` firing.

    It does NOT by itself prove there is no deadline -- a restored 30-second one
    would pass this too (Codex refute, 2026-09-30). What catches that is
    ``test_there_is_no_per_universe_tool_slot_count``, which unparses ``_slot``
    and refuses any ``deadline``, ``raise`` or ``UniverseToolError`` in its body.
    Asserted here as well so the pair cannot drift apart.
    """
    import ast as _ast
    import inspect as _inspect
    import textwrap as _textwrap

    _fn = _ast.parse(
        _textwrap.dedent(_inspect.getsource(universe_tools._slot))
    ).body[0]
    if _ast.get_docstring(_fn) is not None:
        _fn.body = _fn.body[1:]
    assert "deadline" not in _ast.unparse(_fn)
    import fcntl
    import threading

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(universe_tools, "_HOST_SLOTS", 2)
    monkeypatch.setattr(universe_tools, "_SLOT_POLL_SECONDS", 0.01)
    universe = _universe(tmp_path)

    directory = universe_tools._slot_dir()
    held = []
    for i in range(2):
        fd = os.open(directory / f"host-{i}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held.append(fd)

    waits: list[float] = []
    released = threading.Event()

    def _release():
        for fd in held:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        released.set()

    timer = threading.Timer(0.25, _release)
    timer.start()
    try:
        with universe_tools._slot(universe, on_wait=waits.append):
            assert released.is_set(), "the call ran only after a slot came free"
    finally:
        timer.cancel()
        if not released.is_set():
            _release()

    assert len(waits) == 1, "a blocked caller is told exactly once that it is waiting"
    assert waits[0] >= 0.0
