"""A REAL bubblewrap proof that a provider cannot read, write or re-link the
per-universe platform state kept in hidden files at the universe root.

The default provider view binds the universe read-write, so the CLI can work in
its own folder. The daemon keeps per-universe state in hidden root files it
reads AND writes from OUTSIDE the jail -- the credential vault, and the run,
consent and usage SQLite databases. A provider that could replace
``.runs.db`` with a link to ``/data/<other>/.runs.db`` would steer the daemon's
own ``sqlite3.connect`` into another universe's database. ``default_view`` masks
every hidden root entry but ``.runtime``: directories with an empty tmpfs, files
with a read-only ``/dev/null`` bind. This runs the shipping launch path and
proves the file masks hold.

Linux + bwrap only; ``.github/workflows/linux-jail-proof.yml`` runs them and
fails if any is absent or skipped.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_BWRAP = shutil.which("bwrap") if sys.platform == "linux" else None

pytestmark = [
    pytest.mark.skipif(
        sys.platform != "linux" or not _BWRAP,
        reason="a real bubblewrap jail needs Linux + bwrap",
    ),
    pytest.mark.real_jail,
]

VAULT_SECRET = "SYNTHETIC-CREDENTIAL-VAULT-SECRET"
DB_SECRET = "SYNTHETIC-RUNS-DB-SECRET"


def _run(universe: Path, script: str) -> str:
    from tinyassets.providers import base
    from tinyassets.providers.owned_process import aspawn_owned, kill_owned_tree
    from tinyassets.providers.provider_jail import provider_launch_scope

    base._sandbox_probe_cache = None

    async def drive():
        with provider_launch_scope(universe):
            proc = await aspawn_owned(
                ["/bin/sh", "-c", script],
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"PATH": "/usr/bin:/bin", "HOME": str(universe)},
            )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), 60)
        finally:
            kill_owned_tree(proc)
        return (out + err).decode(errors="replace")

    return asyncio.run(drive())


@pytest.fixture
def universe():
    """A universe with platform state in hidden root files, under /tmp (the jail
    on the hosted runner's root fallback cannot traverse a 0750 home)."""
    root = Path(tempfile.mkdtemp(prefix="ta-rootmask-", dir="/tmp"))
    try:
        u = root / "u-alpha"
        (u / ".runtime").mkdir(parents=True)
        (u / ".credential-vault.json").write_text(VAULT_SECRET, encoding="utf-8")
        (u / ".runs.db").write_text(DB_SECRET, encoding="utf-8")
        (u / "notes").mkdir()
        (u / "notes" / "own.txt").write_text("own-content", encoding="utf-8")
        yield u
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_hidden_root_files_read_empty_inside_the_jail(universe):
    out = _run(universe, "cat .credential-vault.json; echo --; cat .runs.db; echo --; cat notes/own.txt")
    assert VAULT_SECRET not in out, out
    assert DB_SECRET not in out, out
    # Positive control: ordinary content is readable, so the masks are not
    # passing by mounting nothing.
    assert "own-content" in out, out


def test_a_provider_cannot_write_through_the_mask(universe):
    out = _run(universe, "echo tampered > .runs.db 2>&1; echo rc=$?")
    # The host file keeps its real content: the write hit /dev/null or was refused.
    assert (universe / ".runs.db").read_text(encoding="utf-8") == DB_SECRET, out


def test_a_provider_cannot_replace_a_masked_file_with_a_link(universe):
    other = universe.parent / "u-bravo"
    other.mkdir()
    (other / ".runs.db").write_text("OTHER-UNIVERSE-DB", encoding="utf-8")
    out = _run(
        universe,
        f"rm -f .runs.db 2>&1; ln -s {other}/.runs.db .runs.db 2>&1; echo rc=$?",
    )
    # The mount holds the name, so neither unlink nor a new link takes effect on
    # the host file. The daemon still opens this universe's own database.
    assert not (universe / ".runs.db").is_symlink(), out
    assert (universe / ".runs.db").read_text(encoding="utf-8") == DB_SECRET, out
