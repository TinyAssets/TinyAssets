"""A REAL bubblewrap proof that a provider launched for universe A sees only A.

The exposure (2026-09-24): a workflow node's provider CLI ran in the daemon's
working directory (``/app``, the platform source) as the daemon's user, with
shell and file tools. It could list ``/data`` -- every universe -- and read the
daemon's process environment. The fix is one OS jail at the shared spawn point
(``tinyassets.providers.owned_process.aspawn_owned`` +
``tinyassets.providers.provider_jail``), keyed on the owning universe the router
binds, so every provider inherits it with no vendor code.

Each case runs a SHIPPING launch path end to end -- the real adapter (or the
real router), the real environment builder, the real owned-family spawn and the
real jail -- with only the CLI binary replaced by a ``/bin/sh`` probe that
reports what it could reach. Nothing about the jail is re-typed here.

What the probe checks, from inside the provider process:

* positive controls -- its own universe is readable AND writable, its own
  launch credential is readable, its ``HOME`` exists, and it starts in its own
  universe, so the negatives cannot pass because nothing was mounted;
* universe B's file is unreadable, and B does not even appear in a listing of
  the data root;
* the platform source (this checkout's ``tinyassets/__init__.py``) is
  unreadable;
* another launch's credential snapshot in the SAME universe is masked;
* no process environment outside the jail is readable: a sentinel process
  carrying a marker in its environment stands in for the daemon.

Before the change every negative reads ``READ``/``LEAK`` (the CLI ran on the
host), so these cases are red on the unconfined tree.

Linux + bwrap only; ``.github/workflows/linux-jail-proof.yml`` runs them and
fails if any is absent or skipped. Every byte involved is synthetic.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

_BWRAP = shutil.which("bwrap") if sys.platform == "linux" else None

pytestmark = [
    pytest.mark.skipif(
        sys.platform != "linux" or not _BWRAP,
        reason="a real bubblewrap jail needs Linux + bwrap",
    ),
    # Runs in .github/workflows/linux-jail-proof.yml, where a skip fails.
    pytest.mark.real_jail,
]

OWN_MARKER = "POSITIVE-CONTROL-OWN-UNIVERSE"
FOREIGN_MARKER = "SYNTHETIC-UNIVERSE-B-CONTENT"
OWN_CRED_MARKER = "SYNTHETIC-OWN-LAUNCH-CREDENTIAL"
OTHER_CRED_MARKER = "SYNTHETIC-OTHER-LAUNCH-CREDENTIAL"
ENV_MARKER = "TA_JAIL_SENTINEL_DAEMON_ENV_MARKER"


@dataclass
class _World:
    data_root: Path
    universe_a: Path
    universe_b: Path
    own_cred: Path
    other_cred: Path
    bin_dir: Path


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch):
    """Two universes under one data root, a fake CLI dir, a sentinel process.

    Under ``/tmp`` (not ``tmp_path``) for the same reason as
    ``tests/test_native_refresh_jail.py``: on the hosted runner's sudo fallback,
    bwrap runs as root with only uid 0 mapped and cannot traverse the runner's
    0750 home to reach a ``--basetemp`` under it.
    """
    from tinyassets.providers import base

    root = Path(tempfile.mkdtemp(prefix="ta-universe-jail-", dir="/tmp"))
    bin_dir = Path(tempfile.mkdtemp(prefix="ta-universe-jail-bin-", dir="/tmp"))
    sentinel = None
    try:
        data_root = root / "data"
        universe_a = data_root / "u-alpha"
        universe_b = data_root / "u-bravo"
        (universe_a / "notes").mkdir(parents=True)
        (universe_b / "notes").mkdir(parents=True)
        (universe_a / "notes" / "own.txt").write_text(OWN_MARKER, encoding="utf-8")
        (universe_b / "notes" / "secret.txt").write_text(FOREIGN_MARKER, encoding="utf-8")
        launch = universe_a / ".runtime" / "provider-launch-credentials"
        own_cred = launch / "own-1"
        other_cred = launch / "other-9"
        own_cred.mkdir(parents=True)
        other_cred.mkdir(parents=True)
        (own_cred / "auth.json").write_text(
            '{"note": "' + OWN_CRED_MARKER + '"}', encoding="utf-8",
        )
        (own_cred / "config.toml").write_text("", encoding="utf-8")
        (other_cred / "auth.json").write_text(
            '{"note": "' + OTHER_CRED_MARKER + '"}', encoding="utf-8",
        )
        # Stand-in for the daemon: a live process whose environment carries a
        # marker that the provider's own environment never does.
        sentinel = subprocess.Popen(  # noqa: S603 - fixed argv
            ["/bin/sleep", "300"],
            env={"PATH": "/usr/bin:/bin", ENV_MARKER: "1"},
        )
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
        # The data root the platform would use is NOT this test's data root;
        # the jail must hold regardless of where universes live.
        base._sandbox_probe_cache = None
        yield _World(data_root, universe_a, universe_b, own_cred, other_cred, bin_dir)
    finally:
        if sentinel is not None:
            sentinel.kill()
            sentinel.wait(timeout=10)
        base._sandbox_probe_cache = None
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(bin_dir, ignore_errors=True)


def _probe_body(world: _World) -> str:
    """The shell that reports reachability as fixed tokens (no file content)."""
    import tinyassets

    source = Path(tinyassets.__file__).resolve()
    own = world.universe_a / "notes" / "own.txt"
    foreign = world.universe_b / "notes" / "secret.txt"
    listing = f"ls '{world.data_root}' | grep -q '{world.universe_b.name}'"
    environ = f"cat /proc/[0-9]*/environ | tr '\\000' '\\n' | grep -q '{ENV_MARKER}'"
    return f"""
r=""
check() {{
  name=$1; yes=$2; no=$3; shift 3
  if "$@" >/dev/null 2>&1; then r="$r $name=$yes"; else r="$r $name=$no"; fi
}}
check own ok denied grep -qx '{OWN_MARKER}' '{own}'
check write ok denied touch '{world.universe_a}/notes/.jail-write-probe'
check foreign READ denied cat '{foreign}'
check listing FOREIGN clean sh -c "{listing}"
check source READ denied cat '{source}'
check owncred ok denied grep -q '{OWN_CRED_MARKER}' '{world.own_cred}/auth.json'
check othercred READ denied cat '{world.other_cred}/auth.json'
check environ LEAK clean sh -c "{environ} 2>/dev/null"
check home ok missing test -d "$HOME"
case "$(pwd)" in '{world.universe_a}'*) r="$r cwd=own";; *) r="$r cwd=elsewhere";; esac
"""


def _install_cli(world: _World, name: str, emit: str) -> Path:
    script = world.bin_dir / name
    script.write_text("#!/bin/sh\n" + _probe_body(world) + emit, encoding="utf-8")
    script.chmod(0o755)
    return script


_CLAUDE_EMIT = (
    "printf '%s\\n' '{\"type\":\"system\",\"subtype\":\"init\"}'\n"
    "printf '{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false,"
    "\"result\":\"%s\",\"usage\":{\"input_tokens\":1,\"output_tokens\":1}}\\n' \"$r\"\n"
)
_PLAIN_EMIT = "printf '%s\\n' \"$r\"\n"


def _assert_confined(report: str, world: _World, *, own_credential: bool = True) -> None:
    tokens = dict(item.split("=", 1) for item in report.split())
    # Positive controls first: a jail that mounted nothing cannot pass.
    assert tokens.get("own") == "ok", report
    assert tokens.get("write") == "ok", report
    # A launch with its own credential sees exactly that one; a launch with
    # none sees no snapshot at all.
    assert tokens.get("owncred") == ("ok" if own_credential else "denied"), report
    assert tokens.get("home") == "ok", report
    assert tokens.get("cwd") == "own", report
    # The cross-user floor.
    assert tokens.get("foreign") == "denied", report
    assert tokens.get("listing") == "clean", report
    assert tokens.get("source") == "denied", report
    assert tokens.get("othercred") == "denied", report
    assert tokens.get("environ") == "clean", report
    assert FOREIGN_MARKER not in report and OTHER_CRED_MARKER not in report


def test_claude_node_call_reads_only_its_own_universe(world: _World) -> None:
    """The shipping claude adapter, a workflow-node call, inside the jail."""
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.claude_provider import ClaudeProvider
    from tinyassets.providers.provider_jail import provider_launch_scope

    _install_cli(world, "claude", _CLAUDE_EMIT)
    config = ModelConfig(workflow_node=True, credential_snapshot_dir=world.own_cred)

    async def drive():
        with provider_launch_scope(world.universe_a, credential_dir=world.own_cred):
            return await ClaudeProvider().complete(
                "prompt", "", config, universe_dir=world.universe_a,
            )

    response = asyncio.run(drive())
    _assert_confined(response.text, world)


def test_codex_node_call_reads_only_its_own_universe(world: _World) -> None:
    """The shipping codex adapter's ordinary (node) path, inside the jail."""
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.codex_provider import CodexProvider
    from tinyassets.providers.provider_jail import provider_launch_scope

    _install_cli(world, "codex", _PLAIN_EMIT)
    config = ModelConfig(workflow_node=True, credential_snapshot_dir=world.own_cred)

    async def drive():
        with provider_launch_scope(world.universe_a, credential_dir=world.own_cred):
            return await CodexProvider().complete(
                "prompt", "", config, universe_dir=world.universe_a,
            )

    response = asyncio.run(drive())
    _assert_confined(response.text, world)


def test_router_jails_a_new_command_adapter_with_no_jail_code(world: _World) -> None:
    """A provider that knows nothing about jails, called through the router.

    The adapter below is what a future command-style provider looks like: it
    builds an argv and spawns it through the shared helper. It names no
    universe view, no mount and no sandbox flag. The ROUTER binds the owning
    universe and the spawn point jails it.
    """
    from unittest.mock import patch

    from tests.support.owner_bound import owner_carrier
    from tinyassets.providers.base import (
        BaseProvider,
        ProviderResponse,
        UniverseContext,
    )
    from tinyassets.providers.owned_process import aspawn_owned, kill_owned_tree
    from tinyassets.providers.router import ProviderRouter

    cli = _install_cli(world, "future-cli", _PLAIN_EMIT)

    class FutureCommandAdapter(BaseProvider):
        name = "codex"  # occupies a judge slot; nothing codex-specific runs
        family = "future"

        async def complete(self, prompt, system, config, *, universe_dir=None):
            proc = await aspawn_owned(
                [str(cli)],
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"PATH": "/usr/bin:/bin", "HOME": str(universe_dir)},
            )
            try:
                stdout, _ = await proc.communicate()
            finally:
                kill_owned_tree(proc)
            return ProviderResponse(
                text=stdout.decode().strip(), provider=self.name, model="future",
                family=self.family, latency_ms=1.0,
            )

    router = ProviderRouter(providers={"codex": FutureCommandAdapter()})
    # The owner authority every router call now carries (Hard Rule 15). It
    # names the provider and nothing else; the jail under test is unrelated to
    # it, and without it the call is refused before anything launches. The
    # universe here must be the real one on disk, so the carrier is bound by
    # hand rather than through ``owner_bound_call``'s placeholder directory.
    carrier = owner_carrier("codex", role="judge")
    context = UniverseContext(
        universe_dir=world.universe_a, provider_invocation=carrier,
    )
    with patch("tinyassets.providers.router._provider_invocation_carrier",
               return_value=carrier):
        results = asyncio.run(router.call_judge_ensemble(
            "prompt", "", operation="run_graph", universe_context=context,
        ))
    assert len(results) == 1, "the router dropped the only judge"
    _assert_confined(results[0].text, world, own_credential=False)



# ── the disk budget (concern 2026-10-01-no-per-universe-disk-budget-in-jails) ─

_MiB = 1024 * 1024
_FILL = (
    "mkdir -p many; for i in $(seq 1 400); do "
    "head -c 262144 /dev/zero > many/f$i || exit 3; done; echo filled"
)


def _launch(universe: Path, script: str, *, env=None):
    """One provider process through the shipping spawn point, run to its end."""
    from tinyassets.providers import owned_process
    from tinyassets.providers.provider_jail import provider_launch_scope

    async def go():
        with provider_launch_scope(universe):
            proc = await owned_process.aspawn_owned(
                ["/bin/sh", "-c", script],
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        out, _ = await proc.communicate()
        await proc.disk_watch
        return proc, out

    return asyncio.run(go())


def test_a_provider_process_writing_past_its_budget_is_stopped(world, monkeypatch):
    from tinyassets import jail_disk
    from tinyassets.providers import owned_process

    monkeypatch.setattr(jail_disk, "LAUNCH_BYTES_CAP", 24 * _MiB)
    monkeypatch.setattr(owned_process, "DISK_POLL_SECONDS", 0.1)
    try:
        proc, out = _launch(world.universe_a, _FILL)
        assert proc.disk_killed == jail_disk.STORAGE_LIMIT
        assert b"filled" not in out and proc.returncode != 0
        written = sum(p.stat().st_size for p in (world.universe_a / "many").iterdir())
        assert 24 * _MiB < written < 100 * _MiB, written
    finally:
        shutil.rmtree(world.universe_a / "many", ignore_errors=True)


def test_a_provider_process_is_stopped_at_the_shared_volume_floor(world, monkeypatch):
    from tinyassets import jail_disk
    from tinyassets.providers import owned_process

    free = jail_disk.free_bytes(world.universe_a)
    assert free > 400 * _MiB, "the runner needs room for this proof"
    monkeypatch.setattr(jail_disk, "MIN_FREE_DISK_BYTES", free - 40 * _MiB)
    monkeypatch.setattr(owned_process, "DISK_POLL_SECONDS", 0.1)
    try:
        proc, out = _launch(world.universe_a, _FILL)
        assert proc.disk_killed == jail_disk.DISK_LIMIT
        assert b"filled" not in out
    finally:
        shutil.rmtree(world.universe_a / "many", ignore_errors=True)


def test_a_provider_launch_below_the_volume_floor_never_starts(world, monkeypatch):
    from tinyassets import jail_disk
    from tinyassets.providers.provider_jail import ProviderConfinementError

    free = jail_disk.free_bytes(world.universe_a)
    monkeypatch.setattr(jail_disk, "MIN_FREE_DISK_BYTES", free * 2)
    with pytest.raises(ProviderConfinementError, match="nearly full"):
        _launch(world.universe_a, "touch started")
    assert not (world.universe_a / "started").exists()


def test_a_provider_filling_its_runtime_dir_is_stopped_too(world, monkeypatch):
    """Persistent writable runtime is charged and the launch walk bounds growth."""
    from tinyassets import jail_disk
    from tinyassets.providers import owned_process

    monkeypatch.setattr(jail_disk, "LAUNCH_BYTES_CAP", 24 * _MiB)
    monkeypatch.setattr(owned_process, "DISK_POLL_SECONDS", 0.1)
    target = world.universe_a / ".runtime" / "many"
    try:
        proc, out = _launch(world.universe_a, _FILL.replace("many", ".runtime/many"))
        assert proc.disk_killed == jail_disk.STORAGE_LIMIT
        assert b"filled" not in out
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_provider_runtime_cache_writes_are_disposable_between_launches(world):
    child = world.universe_a / ".runtime" / "provider-child"
    child.mkdir()
    for _ in range(3):
        proc, out = _launch(
            world.universe_a,
            "mkdir -p .runtime/provider-child/home; "
            "printf hidden > .runtime/provider-child/home/cache; echo done",
        )
        assert proc.returncode == 0 and b"done" in out
        assert list(child.iterdir()) == []


@pytest.mark.parametrize("remove_old", [False, True])
def test_renamed_runtime_and_recreated_cache_are_charged_across_launches(world, remove_old):
    from tinyassets.storage_accounting import _universe_files

    root = world.universe_a
    before = _universe_files(world.data_root, root.name)
    proc, _ = _launch(
        root,
        "mv .runtime .rt-old && mkdir -p .runtime/provider-child && "
        "head -c 5000000 /dev/zero > .runtime/provider-child/hidden",
    )
    assert proc.returncode == 0
    hidden = root / ".runtime/provider-child/hidden"
    assert hidden.stat().st_size == 5_000_000
    assert _universe_files(world.data_root, root.name) == before + 5_000_000
    if remove_old:
        shutil.rmtree(root / ".rt-old")
    charged = _universe_files(world.data_root, root.name)
    assert charged >= 5_000_000
    proc, _ = _launch(root, "test ! -e .runtime/provider-child/hidden")
    assert proc.returncode == 0
    assert hidden.stat().st_size == 5_000_000
    assert _universe_files(world.data_root, root.name) == charged


def test_provider_runtime_environment_directories_exist_inside_disposable_home(world):
    from tinyassets.providers.base import _provider_child_runtime_env

    env = _provider_child_runtime_env("claude-code", world.universe_a)
    proc, out = _launch(
        world.universe_a,
        'test -d "$HOME" && test -d "$XDG_CACHE_HOME" && '
        'head -c 20971520 /dev/zero > "$XDG_CACHE_HOME/probe" && echo done',
        env=env,
    )
    assert proc.returncode == 0 and b"done" in out
    assert not (Path(env["XDG_CACHE_HOME"]) / "probe").exists()


def test_provider_cannot_persist_data_in_excluded_credentials_even_on_first_launch(world):
    from tinyassets import storage_accounting as sa

    root = world.universe_a
    assert not (root / ".credentials").exists()
    proc, out = _launch(
        root, "mkdir -p .credentials/cache; echo hidden > .credentials/cache/data; echo done",
    )
    assert proc.returncode == 0 and b"done" in out
    assert list((root / ".credentials").iterdir()) == []
    # A later launch also gets a disposable mask, never the persistent tree.
    (root / ".credentials" / "platform-cache").write_bytes(b"p" * 112_000)
    proc, out = _launch(root, "echo forged > .credentials/platform-cache; echo done")
    assert proc.returncode == 0 and b"done" in out
    assert (root / ".credentials" / "platform-cache").read_bytes() == b"p" * 112_000
    before = sa._universe_files(root.parent, root.name)
    proc, out = _launch(root, "mkdir -p notes/.credentials; printf user > notes/.credentials/data")
    assert proc.returncode == 0
    assert sa._universe_files(root.parent, root.name) == before + 4
