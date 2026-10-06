"""R3: the read-only ``owner_state`` probe against main's O_RDWR probe.

Carried by isolation slice L1 from the R3 audit
(PR #4543, ``docs/design-notes/2026-10-06-isolation-landing-audits.md``). The read-only
probe is POSIX-only and ungated; run it on Linux with
``python scripts/linux_oracle.py -- tests/test_owner_state_readonly_parity.py``.

``LEGACY`` is main's ``owner_state`` before L1 (7e68ef26cb) verbatim, so every row
compares the two answers for one on-disk state. The reclaim consumer is
``storage/agent_turn_runner``: ``orphan`` is ``== DEAD`` and ``alive`` is
``== ALIVE``, so the only harmful drift is DEAD -> anything (stuck turn) or
anything -> ALIVE (a dead turn reads as running).
"""
# ruff: noqa: F811
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_agent_turn_journal import journal, new  # noqa: F401
from tinyassets import process_liveness as pl
from tinyassets.storage import agent_turn_runner as runner

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX flock probe")


def LEGACY(base_path, token):
    from tinyassets.singleton_lock import _lock_fd, _unlock_fd

    path = pl.liveness_path(base_path, token)
    if path is None or not path.is_file():
        return pl.UNKNOWN
    try:
        fd = os.open(str(path), os.O_RDWR)
    except OSError:
        return pl.UNKNOWN
    try:
        if not _lock_fd(fd):
            return pl.ALIVE
        _unlock_fd(fd)
        return pl.DEAD
    finally:
        os.close(fd)


HOLD = """
import sys
from pathlib import Path
from tinyassets.storage.agent_turn_runner import claim
with claim(Path(sys.argv[1]), 'turn') as token:
    print(token, flush=True)
    sys.stdin.readline()
"""


def _child(base):
    child = subprocess.Popen([sys.executable, "-c", HOLD, str(base)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    return child, child.stdout.readline().strip()


def _both(base, token):
    root = runner._root(base)
    return LEGACY(root, token), pl.owner_state(root, token)


def _dead_token(base):
    child, token = _child(base)
    child.kill()
    child.wait(timeout=10)
    return token


def test_live_runner_reads_alive_in_both(tmp_path):
    child, token = _child(tmp_path)
    try:
        assert _both(tmp_path, token) == (pl.ALIVE, pl.ALIVE)
        assert runner.alive(tmp_path, token) and not runner.orphan(tmp_path, token)
    finally:
        child.kill()
        child.wait(timeout=10)


def test_killed_runner_reads_dead_in_both_and_is_reclaimed(tmp_path):
    token = _dead_token(tmp_path)
    assert _both(tmp_path, token) == (pl.DEAD, pl.DEAD)
    assert runner.orphan(tmp_path, token) and not runner.alive(tmp_path, token)


def test_released_in_process_claim_reads_dead_in_both(tmp_path):
    with runner.claim(tmp_path, "t1") as token:
        pass
    runner.release(tmp_path, "t1")
    assert _both(tmp_path, token) == (pl.DEAD, pl.DEAD)


def test_missing_proof_is_unknown_in_both(tmp_path):
    (runner._root(tmp_path) / pl.LIVENESS_DIR).mkdir(parents=True)
    assert _both(tmp_path, "nosuchtoken") == (pl.UNKNOWN, pl.UNKNOWN)
    assert not runner.orphan(tmp_path, "nosuchtoken")


def test_empty_token_is_orphan_without_probing(tmp_path):
    assert runner.orphan(tmp_path, "")


@pytest.mark.skipif(getattr(os, "geteuid", lambda: -1)() == 0,
                    reason="Linux root ignores the mode bits; runs-in=linux_oracle")
def test_unwritable_dead_proof_was_unknown_and_is_now_dead(tmp_path):
    """The one intended change: main needed O_RDWR to prove death."""
    token = _dead_token(tmp_path)
    proof = pl.liveness_path(runner._root(tmp_path), token)
    proof.chmod(0o444)
    assert _both(tmp_path, token) == (pl.UNKNOWN, pl.DEAD)


def test_hardlinked_dead_proof_was_dead_and_is_now_unknown(tmp_path):
    token = _dead_token(tmp_path)
    proof = pl.liveness_path(runner._root(tmp_path), token)
    os.link(proof, tmp_path / "alias.lock")
    assert _both(tmp_path, token) == (pl.DEAD, pl.UNKNOWN)
    assert not runner.orphan(tmp_path, token)  # this turn would stay stuck


def test_symlinked_dead_proof_was_dead_and_is_now_unknown(tmp_path):
    token = _dead_token(tmp_path)
    proof = pl.liveness_path(runner._root(tmp_path), token)
    real = tmp_path / "real.lock"
    proof.rename(real)
    proof.symlink_to(real)
    assert _both(tmp_path, token) == (pl.DEAD, pl.UNKNOWN)


def test_base_through_a_symlink_was_dead_and_is_now_unknown(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    token = _dead_token(real)
    link = tmp_path / "link"
    link.symlink_to(real)
    assert _both(link, token) == (pl.DEAD, pl.UNKNOWN)
    # data_dir() always returns a resolved path, so production never does this.
    assert _both(real, token) == (pl.DEAD, pl.DEAD)


def test_fifo_proof_is_unknown_in_both_and_never_blocks(tmp_path):
    root = runner._root(tmp_path) / pl.LIVENESS_DIR
    root.mkdir(parents=True)
    os.mkfifo(root / "fifo.lock")
    assert _both(tmp_path, "fifo") == (pl.UNKNOWN, pl.UNKNOWN)


def test_relative_base_is_unknown_not_a_crash(tmp_path, monkeypatch):
    token = _dead_token(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    rel = Path(tmp_path.name)
    assert LEGACY(runner._root(rel), token) == pl.DEAD
    assert pl.owner_state(runner._root(rel), token) == pl.UNKNOWN


def test_reconcile_settles_a_killed_runner_turn(journal):
    """End to end through the boot sweep the new probe feeds."""
    from tinyassets.agent_turn_reconcile import reconcile_orphaned_turns

    base = journal._ledger.base_path
    turn = new(journal)
    runner.release(base, turn.turn_id)  # this process's claim closes = runner died
    result = reconcile_orphaned_turns(base)
    assert [(r["turn_id"], r["settled"]) for r in result] == [(turn.turn_id, "abandoned")]
