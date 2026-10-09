"""The provider launch scope and the shared jail view pieces.

Every refusal here happens BEFORE a process exists, so it is asserted on any
host. A provider process itself runs in its owner's cell
(``tests/test_role_provider_execution.py``).
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

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _tripwire)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _tripwire)
    return launched


@pytest.fixture(autouse=True)
def prepared_workspace(monkeypatch):
    """The agent workspace is made by the owner's tool-files cell in production
    (``role_tools.prepare``); here a plain directory stands in for it."""
    from tinyassets import role_tools

    def prepare(universe_dir, *, agent_id):
        (Path(universe_dir) / provider_jail.AGENT_WORKSPACE_DIR).mkdir(exist_ok=True)
    monkeypatch.setattr(role_tools, "prepare", prepare)


def _universe(root: Path, name: str = "u-alpha") -> Path:
    universe = root / "data" / name
    (universe / ".runtime" / "provider-launch-credentials" / "own-1").mkdir(parents=True)
    (universe / ".runtime" / "provider-launch-credentials" / "other-2").mkdir(parents=True)
    return universe


def test_a_provider_launch_with_no_owning_universe_is_refused_before_spawn(no_spawn):
    """A host-authority provider call (no universe) never runs a CLI on the host."""

    async def drive():
        with provider_launch_scope(None):
            await owned_process.aspawn_owned([sys.executable, "-c", "pass"])

    with pytest.raises(ProviderConfinementError, match="not admitted"):
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




def test_the_router_grants_an_engine_route_only_when_the_call_wires_one():
    from tinyassets.providers.router import _engine_route

    assert _engine_route(ModelConfig()) is None
    assert _engine_route(ModelConfig(engine_mcp_enabled=True, engine_mcp_actor_id="a")) is None
    assert _engine_route(ModelConfig(
        engine_mcp_actor_id="a", engine_mcp_graph_id="g")) is None
    assert _engine_route(ModelConfig(
        engine_mcp_enabled=True, engine_mcp_actor_id=" a ", engine_mcp_graph_id="g",
    )) == ("a", "g")
