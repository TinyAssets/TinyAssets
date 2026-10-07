"""A REAL bubblewrap jail proof for the codex coding-turn credential mask.

A codex CODING turn ro-binds the WHOLE universe root at ``/workspace``, so every
``.runtime/`` sibling is readable inside the jail unless it is masked. The
launch snapshot (``.runtime/provider-launch-credentials``) is masked with an
empty ``--tmpfs``. An argv check can name that mask and still leak if bubblewrap
applies the operations in an order that puts the mask under the bind, or if a
later mount re-exposes the tree. Only running the jail answers that.

So this file takes the argv the SHIPPING provider actually builds
(``tinyassets.providers.codex_provider.CodexProvider.complete`` on a coding
turn, captured at the ``aspawn_owned`` boundary and turned into bwrap argv by
the shared ``provider_jail.jail_argv``, as the spawn point does), swaps the
inner command --
everything after ``--`` -- for a harmless ``/bin/sh`` reader, and runs it.
Nothing about the mount list is re-typed here; a mask deleted from the provider
is a mask absent from this jail.

What it proves, on Linux with bwrap:

* positive control -- ordinary universe content IS readable at ``/workspace``,
  so a jail that simply failed to mount anything cannot pass by reading nothing.
  The synthetic universe has exactly the ``.runtime`` shape main creates (the
  launch snapshot tree and nothing else), so this case also proves the shipping
  argv LAUNCHES on a realistic universe;
* negative -- the launch snapshot is unreadable through that same
  whole-universe bind, and its mount point is an empty directory in the jail;
* detection control -- with the ``--tmpfs`` mask stripped from the captured
  argv, the synthetic snapshot bytes ARE readable, so the negative assertions
  are live rather than vacuous.

Deliberately NOT here: a mask over ``.runtime/credential-generations``. That
tree belongs to the (on-hold) subscription refresh lifecycle and nothing on
main creates it. Under ``--ro-bind <universe> /workspace`` bubblewrap must
``mkdir`` a missing mount point inside the read-only bind, and it dies instead:
``bwrap: Can't create file /workspace/.runtime/credential-generations:
Read-only file system`` (bubblewrap 0.12.0, measured 2026-09-24 in the
``scripts/linux_oracle.py`` image). An unconditional mask over a path main never
creates would therefore kill every coding turn. Whoever lands that tree must
create it host-side before the mask ships; the positive control above turns red
if they do not.

Every byte involved is synthetic: no real credential, provider, account,
network or user data. The codex binary is never resolved or launched, and the
jail's inner command is ``sh -c 'cat ...'``.

Linux-only by construction: it skips on Windows and runs in
``.github/workflows/linux-jail-proof.yml``, which fails if any of these cases is
absent or skipped.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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

LAUNCH_MOUNT = "/workspace/.runtime/provider-launch-credentials"

LAUNCH_FILE = f"{LAUNCH_MOUNT}/codex-1/auth.json"
ALLOWED_FILE = "/workspace/notes/allowed.txt"

# Deliberately loud non-secrets. If one of these ever appears in jail output it
# means the corresponding tree was readable -- that is the whole signal.
ALLOWED_MARKER = "POSITIVE-CONTROL-ORDINARY-WORKSPACE-CONTENT"
LAUNCH_MARKER = "SYNTHETIC-LAUNCH-SNAPSHOT-NOT-A-CREDENTIAL"

_PROBE = (
    "set -u\n"
    'for p in "$@"; do\n'
    '  if out=$(cat "$p" 2>/dev/null); then\n'
    '    printf "READ_OK %s %s\\n" "$p" "$out"\n'
    "  else\n"
    '    printf "READ_DENIED %s\\n" "$p"\n'
    "  fi\n"
    "done\n"
    'printf "LIST_LAUNCH [%s]\\n" "$(ls -A ' + LAUNCH_MOUNT + ' 2>&1 | tr "\\n" ",")"\n'
)

_PROBE_PATHS = (ALLOWED_FILE, LAUNCH_FILE)


@pytest.fixture
def jail_root():
    """A fresh directory the jail's own identity can traverse to.

    NOT ``tmp_path``. On the hosted runner's sudo fallback, bwrap runs as root
    and maps ONLY uid 0 into its user namespace; root's capabilities do not
    reach inodes owned by an unmapped uid, so it cannot traverse the runner's
    0750 ``/home/runner`` to reach a ``--basetemp`` under ``runner.temp`` and
    dies with ``Can't find source path ...: Permission denied`` (run
    36043548734; reproduced in the linux oracle with a uid-1001 0750 parent).
    ``/tmp`` is world-traversable, and the ``mkdtemp`` leaf is owned by
    whichever identity runs the test, so the bind source resolves on both the
    unprivileged and the sudo path. The production shape is unchanged: the
    provider still ro-binds exactly the directory it is handed.
    """

    root = Path(tempfile.mkdtemp(prefix="ta-native-jail-", dir="/tmp"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _synthetic_universe(root: Path) -> Path:
    """A universe root with main's ``.runtime`` shape, holding only fake markers."""

    notes = root / "notes"
    notes.mkdir(parents=True)
    (notes / "allowed.txt").write_text(ALLOWED_MARKER, encoding="utf-8")

    launch = root / ".runtime" / "provider-launch-credentials" / "codex-1"
    launch.mkdir(parents=True)
    (launch / "auth.json").write_text(
        '{"note": "' + LAUNCH_MARKER + '"}', encoding="utf-8"
    )
    return root


def _sandbox_argv(monkeypatch: pytest.MonkeyPatch, universe_root: Path) -> list[str]:
    """Return the bwrap argv the SHIPPING provider builds for a coding turn.

    Only the things that would need a real machine are faked: the codex binary
    resolution, the sandbox status probe (pointed at the real local bwrap), the
    provider environment, the two mount helpers that bind a real codex install /
    credential snapshot, and the owned-family spawn. The mount list under test
    -- the ``/workspace`` bind and its masks -- is built by the provider itself.
    """

    from tinyassets.providers import codex_provider as provider
    from tinyassets.providers.base import ModelConfig

    codex_home = universe_root / ".runtime" / "provider-launch-credentials" / "codex-1"
    proc = MagicMock()
    proc.returncode = 0
    proc.wait = AsyncMock(return_value=0)
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(provider, "_resolve_codex_cmd", lambda: (["codex"], False))
    monkeypatch.setattr(
        provider,
        "get_sandbox_status",
        lambda: {"bwrap_available": True, "bwrap_path": _BWRAP},
    )
    monkeypatch.setattr(
        provider,
        "subprocess_env_for_provider",
        lambda *a, **kw: {"CODEX_HOME": str(codex_home)},
    )
    monkeypatch.setattr(provider, "_codex_sandbox_mounts", lambda command: [])
    monkeypatch.setattr(provider, "_codex_home_file_mounts", lambda path: [])
    monkeypatch.setattr(provider, "aspawn_owned", spawn)
    monkeypatch.setattr(provider, "kill_owned_tree", lambda proc: None)
    monkeypatch.setattr("tinyassets.providers.codex_app_server.bundled_catalog",
                        lambda base_cmd, **kw: {"models": [{"slug": "m"}]})

    async def _drive() -> None:
        with pytest.raises(Exception):
            # The stand-in process cannot speak the app-server protocol, so the
            # turn fails AFTER argv construction, which is the artifact under test.
            await provider.CodexProvider().complete(
                "prompt",
                "system",
                ModelConfig(sandbox_workspace=True),
                universe_dir=universe_root,
            )

    asyncio.run(_drive())

    assert spawn.call_args is not None, "codex never reached argv construction"
    # The provider hands the shared spawn point its argv plus its own view of
    # the universe; the shared jail (provider_jail.jail_argv) turns them into
    # the bwrap argv that actually runs. Build it with that same function.
    from tinyassets.providers.provider_jail import jail_argv

    view = spawn.call_args.kwargs.get("universe_view")
    assert view is not None, "a coding turn reached the spawn point without a jail view"
    argv = jail_argv(
        list(spawn.call_args.args[0]), view, bwrap_path=_BWRAP,
        env=spawn.call_args.kwargs.get("env"),
    )
    assert argv[0] == _BWRAP, argv[:1]
    return argv


def _run_in_jail(argv: list[str]) -> str:
    """Execute the captured jail with a harmless reader instead of codex."""

    outer = argv[: argv.index("--")]
    completed = subprocess.run(  # noqa: S603 - fixed argv from the provider
        [*outer, "--", "/bin/sh", "-c", _PROBE, "sh", *_PROBE_PATHS],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0 and _is_namespace_refusal(completed.stderr):
        # The kernel refused to create the namespace at all (restricted
        # unprivileged userns, no seccomp relaxation). That is "no jail here",
        # never "the jail leaked" -- say so rather than reporting a red. The CI
        # job refuses to read this skip as a pass.
        pytest.skip(f"bwrap cannot create a namespace here: {completed.stderr.strip()}")
    assert completed.returncode == 0, (
        f"the jail itself failed (rc={completed.returncode}); "
        f"stderr={completed.stderr!r} stdout={completed.stdout!r}"
    )
    return completed.stdout


def _is_namespace_refusal(stderr: str) -> bool:
    """True only for bwrap's own "I could not build a sandbox" refusals."""

    return any(
        phrase in stderr
        for phrase in (
            "Creating new namespace failed",
            "No permissions to creating new namespace",
            "No permissions to create new namespace",
            "setting up uid map",
        )
    )


def _strip_mask(argv: list[str], mountpoint: str) -> list[str]:
    """Drop one ``--tmpfs <mountpoint>`` (and the ``--size`` sizing it), leaving
    everything else intact."""

    stripped = list(argv)
    for index in range(len(stripped) - 1):
        if stripped[index] == "--tmpfs" and stripped[index + 1] == mountpoint:
            start = index - 2 if index >= 2 and stripped[index - 2] == "--size" else index
            del stripped[start : index + 2]
            return stripped
    raise AssertionError(f"no --tmpfs {mountpoint} in the provider's argv: {argv}")


def test_jail_reads_workspace_but_not_launch_credentials(
    monkeypatch: pytest.MonkeyPatch, jail_root: Path
) -> None:
    """Positive control passes; the launch snapshot is an empty dir in the jail."""

    root = _synthetic_universe(jail_root / "universe")
    out = _run_in_jail(_sandbox_argv(monkeypatch, root))

    # Positive control: the whole-universe bind IS live, so the negatives below
    # cannot pass merely because nothing was mounted.
    assert f"READ_OK {ALLOWED_FILE} {ALLOWED_MARKER}" in out, out

    assert f"READ_DENIED {LAUNCH_FILE}" in out, out
    assert LAUNCH_MARKER not in out, out
    # Masked by an EMPTY tmpfs: not a renamed, hidden or partially shadowed tree.
    assert "LIST_LAUNCH []" in out, out


def test_removing_the_launch_mask_exposes_the_snapshot(
    monkeypatch: pytest.MonkeyPatch, jail_root: Path
) -> None:
    """Detection control: without the mask the synthetic bytes ARE readable."""

    root = _synthetic_universe(jail_root / "universe")
    argv = _sandbox_argv(monkeypatch, root)
    out = _run_in_jail(_strip_mask(argv, LAUNCH_MOUNT))

    assert f"READ_OK {ALLOWED_FILE} {ALLOWED_MARKER}" in out, out
    assert f"READ_OK {LAUNCH_FILE}" in out, out
    assert LAUNCH_MARKER in out, out
