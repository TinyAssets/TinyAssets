"""The provider jail's policy: when it applies, what it refuses, what it binds.

Platform-neutral where it can be: every refusal here happens BEFORE a process
exists, so it is asserted on any host, and the spawn functions are replaced by
tripwires that fail the test if anything is launched. The real-jail proof of
what a jailed provider can read is ``tests/test_provider_universe_jail.py``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tinyassets.providers import owned_process, provider_jail
from tinyassets.providers.base import (
    BaseProvider,
    ModelConfig,
    ProviderResponse,
    UniverseContext,
)
from tinyassets.providers.provider_jail import (
    JailMount,
    ProviderConfinementError,
    UniverseView,
    confine_launch,
    default_view,
    jail_argv,
    provider_launch_scope,
)

posix_paths = pytest.mark.skipif(
    os.name != "posix", reason="bwrap argv carries POSIX paths",
)


@pytest.fixture
def no_spawn(monkeypatch: pytest.MonkeyPatch) -> list:
    """Fail loudly if anything reaches a real process spawn."""
    launched: list = []

    async def _tripwire(*args, **kwargs):
        launched.append(args)
        raise AssertionError("a refused launch reached the process spawn")

    monkeypatch.setattr(owned_process, "_aspawn_anchored", _tripwire)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _tripwire)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _tripwire)
    return launched


def _universe(root: Path, name: str = "u-alpha") -> Path:
    universe = root / "data" / name
    (universe / ".runtime" / "provider-launch-credentials" / "own-1").mkdir(parents=True)
    (universe / ".runtime" / "provider-launch-credentials" / "other-2").mkdir(parents=True)
    return universe


def test_a_launch_outside_any_provider_scope_is_unchanged():
    assert confine_launch(["tool", "--flag"]) is None


def test_a_provider_launch_with_no_owning_universe_is_refused_before_spawn(no_spawn):
    """A host-authority provider call (no universe) never runs a CLI on the host."""

    async def drive():
        with provider_launch_scope(None):
            await owned_process.aspawn_owned([sys.executable, "-c", "pass"])

    with pytest.raises(ProviderConfinementError, match="no owning command center"):
        asyncio.run(drive())
    assert no_spawn == []


def test_no_os_sandbox_refuses_rather_than_running_unconfined(
    tmp_path, monkeypatch, no_spawn,
):
    from tinyassets.providers import base

    monkeypatch.setattr(
        base, "get_sandbox_status",
        lambda: {"bwrap_available": False, "reason": "bwrap not found on PATH"},
    )
    universe = _universe(tmp_path)

    async def drive():
        with provider_launch_scope(universe):
            await owned_process.aspawn_owned([sys.executable, "-c", "pass"])

    with pytest.raises(ProviderConfinementError, match="no OS sandbox"):
        asyncio.run(drive())
    assert no_spawn == []


def test_a_refusal_is_authority_held_so_no_fallback_text_replaces_it():
    """Callers already never fold ProviderAuthorityHeldError into fallback text."""
    from tinyassets.exceptions import ProviderAuthorityHeldError, ProviderUnavailableError

    assert issubclass(ProviderConfinementError, ProviderAuthorityHeldError)
    # Not "unavailable": that would cool the provider for every owner.
    assert not issubclass(ProviderConfinementError, ProviderUnavailableError)


def test_a_view_cannot_bind_anything_outside_its_universe(tmp_path):
    universe = _universe(tmp_path)
    other = _universe(tmp_path, "u-bravo")
    view = UniverseView(
        universe_dir=universe,
        mounts=(JailMount("ro-bind", "/workspace", other),),
    )
    with pytest.raises(ProviderConfinementError, match="inside its own command center"):
        jail_argv(["cli"], view, bwrap_path="bwrap")


@pytest.mark.parametrize("dest", ["/", "/usr/bin", "/etc", "/proc/self", "relative"])
def test_a_view_cannot_mount_over_system_roots(tmp_path, dest):
    universe = _universe(tmp_path)
    view = UniverseView(universe_dir=universe, mounts=(JailMount("tmpfs", dest),))
    with pytest.raises(ProviderConfinementError, match="may not mount"):
        jail_argv(["cli"], view, bwrap_path="bwrap")


def test_a_view_for_another_universe_than_the_call_is_refused(tmp_path, no_spawn):
    universe = _universe(tmp_path)
    other = _universe(tmp_path, "u-bravo")
    view = UniverseView(universe_dir=other, mounts=())
    with provider_launch_scope(universe):
        with pytest.raises(ProviderConfinementError, match="different command center"):
            confine_launch(["cli"], view=view)


@posix_paths
def test_default_view_binds_the_universe_masks_launches_and_rebinds_its_own(tmp_path):
    universe = _universe(tmp_path).resolve()
    own = universe / ".runtime" / "provider-launch-credentials" / "own-1"
    view = default_view(universe, credential_dir=own, cwd=str(universe / "sub"))
    argv = jail_argv(["cli", "-p"], view, bwrap_path="/usr/bin/bwrap")

    for flag in ("--die-with-parent", "--new-session", "--unshare-all"):
        assert flag in argv, flag
    # No host network, ever: a way out is only an egress socket a caller binds.
    assert "--share-net" not in argv
    assert argv[argv.index("--proc") + 1] == "/proc"
    bind_u = argv.index(str(universe))
    mask = argv.index(str(universe / ".runtime" / "provider-launch-credentials"))
    rebind = max(i for i, a in enumerate(argv) if a == str(own))
    assert argv[bind_u - 1] == "--bind" and argv[mask - 1] == "--tmpfs"
    assert bind_u < mask < rebind, "the mask must sit over the bind and under its own snapshot"
    assert argv[rebind - 2] == "--bind"
    # The data root holding every universe is never bound, nor anything above it.
    for i, arg in enumerate(argv[:-3]):
        if arg in ("--bind", "--ro-bind"):
            assert not Path(universe.parent).is_relative_to(Path(argv[i + 1])), argv[i + 1]
    # A cwd inside the universe is honoured; one outside falls back to the universe.
    assert argv[argv.index("--chdir") + 1] == str(universe / "sub")
    outside = default_view(universe, cwd=str(universe.parent))
    assert outside.chdir == str(universe)
    assert argv[argv.index("--") + 1:] == ["cli", "-p"]


@posix_paths
def test_a_credential_dir_at_the_universe_root_is_not_rebound_over_the_masks(tmp_path):
    """A rebind of the universe root after the masks would re-expose every hidden
    entry (gpt-6-astra refute). The rebind is only for a snapshot under .runtime,
    so a credential_dir that is the root itself (or outside .runtime) is ignored."""
    universe = _universe(tmp_path).resolve()
    (universe / ".credential-vault.json").write_text("secret", encoding="utf-8")
    view = default_view(universe, credential_dir=universe)
    argv = jail_argv(["cli"], view, bwrap_path="/usr/bin/bwrap")
    # Exactly one bind of the root (the initial read-write bind), none after it.
    root_binds = [i for i, a in enumerate(argv) if a == str(universe) and argv[i - 1] == "--bind"]
    assert len(root_binds) == 1, argv
    # The vault mask still stands (nothing rebound the root over it).
    vault = argv.index(f"{universe}/.credential-vault.json")
    assert argv[vault - 2] == "--ro-bind" and argv[vault - 1] == "/dev/null"
    assert vault > root_binds[0]


@posix_paths
def test_an_install_path_reaching_universe_data_is_refused(tmp_path):
    universe = _universe(tmp_path).resolve()
    view = default_view(universe)
    with pytest.raises(ProviderConfinementError, match="overlaps command center data"):
        jail_argv(["cli"], view, bwrap_path="/usr/bin/bwrap", install_paths=[universe.parent])


@posix_paths
def test_an_install_path_reaching_platform_source_is_refused(tmp_path):
    import tinyassets

    universe = _universe(tmp_path).resolve()
    source = Path(tinyassets.__file__).resolve().parent
    with pytest.raises(ProviderConfinementError, match="platform source"):
        jail_argv(
            ["cli"], default_view(universe), bwrap_path="/usr/bin/bwrap",
            install_paths=[source],
        )


# --- the router binds the owning universe ----------------------------------


class _ScopeRecorder(BaseProvider):
    family = "test"

    def __init__(self, name: str) -> None:
        self.name = name
        self.scopes: list = []

    async def complete(self, prompt, system, config, *, universe_dir=None):
        self.scopes.append(provider_jail._SCOPE.get())
        return ProviderResponse(
            text="ok", provider=self.name, model="m", family=self.family, latency_ms=1.0,
        )


class _SpawningProvider(BaseProvider):
    """A command adapter: spawns through the shared helper and nothing else."""

    family = "test"

    def __init__(self, name: str) -> None:
        self.name = name

    async def complete(self, prompt, system, config, *, universe_dir=None):
        await owned_process.aspawn_owned([sys.executable, "-c", "pass"])
        raise AssertionError("unreachable: the launch should have been refused")


def test_router_binds_the_owning_universe_around_the_provider_call(tmp_path):
    from tests.support.owner_bound import owner_carrier
    from tinyassets.providers.router import ProviderRouter

    universe = _universe(tmp_path)
    recorder = _ScopeRecorder("codex")
    router = ProviderRouter(providers={"codex": recorder})
    # Hard Rule 15 refuses an unbound call before the jail is reached. The
    # universe must be the real one on disk for the scope assertion below, so
    # the carrier is bound by hand rather than through ``owner_bound_call``.
    carrier = owner_carrier("codex")
    context = UniverseContext(universe_dir=universe, provider_invocation=carrier)

    with patch("tinyassets.providers.router._provider_invocation_carrier",
               return_value=carrier):
        asyncio.run(router.call(
            "writer", "p", "", ModelConfig(max_tokens=10),
            operation="run_graph", universe_context=context,
        ))

    assert len(recorder.scopes) == 1
    assert recorder.scopes[0].universe_dir == universe
    assert provider_jail._SCOPE.get() is None, "the binding leaked past the call"


def test_router_refuses_a_host_authority_launch_without_cooling_the_provider(no_spawn):
    """The selector/leaderboard shape: a router call with no universe context.

    Hard Rule 15 (#3961) now refuses this one step earlier than the jail does,
    with the sibling ``PlatformLLMCallRefusedError``. Both are the same
    ``ProviderAuthorityHeldError`` contract the jail was built to honour, and
    the claims that matter here are unchanged: nothing spawns, and a refusal
    that is about the host rather than the credential never cools the provider.
    """
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.router import ProviderRouter

    router = ProviderRouter(providers={"claude-code": _SpawningProvider("claude-code")})
    with pytest.raises(ProviderAuthorityHeldError):
        asyncio.run(router.call("writer", "p", "", ModelConfig()))
    assert no_spawn == []
    assert router._quota.available("claude-code"), "a host refusal cooled the provider"
    assert router._quota._cooldowns == {}


def test_powershell_is_on_the_one_host_reach_floor():
    from tinyassets.providers.base import HOST_REACH_TOOLS
    from tinyassets.universe_intelligence import _ENGINE_DISALLOWED_TOOLS

    assert "PowerShell" in HOST_REACH_TOOLS
    assert "PowerShell" in _ENGINE_DISALLOWED_TOOLS


# --- the network, filter and limits every provider launch gets -------------


def _sidecar(universe: Path, name: str) -> Path:
    directory = universe.parent / provider_jail.UNIVERSE_SIDECARS_DIR / universe.name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.touch()
    return path


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A universe whose proxy (and engine relay) are stand-in sidecar files."""
    from tinyassets import universe_egress

    universe = _universe(tmp_path).resolve()
    egress = _sidecar(universe, "egress-1.sock")
    engine = _sidecar(universe, "engine-1-abc.sock")
    relays: list = []

    def _relay(universe_dir, *, actor_id, graph_id):
        relays.append((Path(universe_dir), actor_id, graph_id))
        return engine, 8791

    monkeypatch.setattr(provider_jail, "BWRAP_RESOLVER", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(universe_egress, "ensure_proxy", lambda universe_dir: egress)
    monkeypatch.setattr(universe_egress, "ensure_engine_relay", _relay)
    monkeypatch.setattr(provider_jail.shutil, "which",
                        lambda name, path=None: f"/usr/bin/{name}")
    return universe, egress, engine, relays


def _bind_of(argv: list[str], dest: str) -> str | None:
    for i, arg in enumerate(argv[:-2]):
        if arg == "--bind" and argv[i + 2] == dest:
            return argv[i + 1]
    return None


@posix_paths
def test_a_provider_launch_has_no_host_network_only_its_universe_proxy(wired):
    from tinyassets import universe_egress

    universe, egress, _engine, relays = wired
    with provider_launch_scope(universe):
        launch = confine_launch(["cli", "-p"], env={"HTTPS_PROXY": "http://elsewhere:1"})
    try:
        argv = launch.argv
        outer = argv[: argv.index("--")]
        assert "--share-net" not in argv
        assert _bind_of(outer, universe_egress.JAIL_SOCKET) == str(egress)
        # No engine route was granted, so no relay was asked for or bound.
        assert relays == [] and _bind_of(outer, universe_egress.JAIL_ENGINE_SOCKET) is None
        # The proxy environment is set by the jail and overrides the provider's.
        setenv = {outer[i + 1]: outer[i + 2] for i, a in enumerate(outer) if a == "--setenv"}
        for name, value in universe_egress.PROXY_ENV:
            assert setenv[name] == value, name
        # Inside: limits first, then the forwarder, then the command itself.
        inner = argv[argv.index("--") + 1:]
        assert inner[0] == "/usr/bin/prlimit"
        assert {a.split("=")[0] for a in inner[1:inner.index("--")]} == {
            "--nproc", "--nofile", "--core"}
        forwarder = inner[inner.index("--") + 1:]
        assert forwarder[:5] == ["/usr/bin/python3", "-I", "-S", "-c", universe_egress.FORWARDER]
        assert forwarder[5:] == [f"3128={universe_egress.JAIL_SOCKET}", "--", "cli", "-p"]
    finally:
        launch.close()


@posix_paths
@pytest.mark.parametrize("nested", [False, True], ids=["deny", "served"])
def test_the_seccomp_filter_is_handed_to_the_jail_and_released(wired, nested):
    """Every launch gets the full deny profile unless its adapter declared a
    nested sandbox (a served codex turn), which gets the permissive one."""
    universe, *_ = wired
    with provider_launch_scope(universe):
        launch = (confine_launch(["cli"], nested_sandbox=True) if nested
                  else confine_launch(["cli"]))
    (fd,) = launch.pass_fds
    assert launch.argv[launch.argv.index("--seccomp") + 1] == str(fd)
    from tinyassets.providers.jail_seccomp import deny_program

    assert os.read(fd, 1 << 16) == deny_program(nested_sandbox=nested)
    launch.close()
    with pytest.raises(OSError):
        os.fstat(fd)


@posix_paths
def test_an_engine_route_binds_only_the_relay_to_that_route(wired):
    from tinyassets import universe_egress

    universe, _egress, engine, relays = wired
    with provider_launch_scope(universe, engine_route=("user_1", "u-alpha")):
        launch = confine_launch(["cli"])
    launch.close()
    assert relays == [(universe, "user_1", "u-alpha")]
    assert _bind_of(launch.argv, universe_egress.JAIL_ENGINE_SOCKET) == str(engine)
    assert f"8791={universe_egress.JAIL_ENGINE_SOCKET}" in launch.argv


@posix_paths
def test_no_proxy_means_no_launch_never_an_unfiltered_one(wired, monkeypatch, no_spawn):
    from tinyassets import universe_egress

    universe, *_ = wired
    monkeypatch.setattr(universe_egress, "ensure_proxy", lambda universe_dir: None)

    async def drive():
        with provider_launch_scope(universe):
            await owned_process.aspawn_owned(["cli"])

    with pytest.raises(ProviderConfinementError, match="egress proxy"):
        asyncio.run(drive())
    assert no_spawn == []


@posix_paths
def test_a_host_without_prlimit_refuses_rather_than_running_unlimited(wired, monkeypatch):
    universe, *_ = wired
    monkeypatch.setattr(provider_jail.shutil, "which",
                        lambda name, path=None: None if name == "prlimit" else f"/usr/bin/{name}")
    with provider_launch_scope(universe):
        with pytest.raises(ProviderConfinementError, match="prlimit is not installed"):
            confine_launch(["cli"])


@posix_paths
def test_the_spawn_point_passes_the_filter_and_closes_its_copy(wired, monkeypatch):
    universe, *_ = wired
    seen: dict = {}

    async def _spawn(argv, *, extra_fds=(), **kwargs):
        seen["fds"] = extra_fds
        seen["open_during_spawn"] = [_is_open(fd) for fd in extra_fds]
        raise RuntimeError("spawn failed after the jail was built")

    monkeypatch.setattr(owned_process, "_aspawn_anchored", _spawn)

    async def drive():
        with provider_launch_scope(universe):
            await owned_process.aspawn_owned(["cli"])

    with pytest.raises(RuntimeError, match="spawn failed"):
        asyncio.run(drive())
    assert seen["open_during_spawn"] == [True]
    assert [_is_open(fd) for fd in seen["fds"]] == [False], "the filter fd leaked"


def _is_open(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


def test_the_router_grants_an_engine_route_only_when_the_call_wires_one():
    from tinyassets.providers.router import _engine_route

    assert _engine_route(ModelConfig()) is None
    assert _engine_route(ModelConfig(engine_mcp_enabled=True, engine_mcp_actor_id="a")) is None
    assert _engine_route(ModelConfig(
        engine_mcp_actor_id="a", engine_mcp_graph_id="g")) is None
    assert _engine_route(ModelConfig(
        engine_mcp_enabled=True, engine_mcp_actor_id=" a ", engine_mcp_graph_id="g",
    )) == ("a", "g")
