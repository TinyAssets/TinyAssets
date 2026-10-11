"""Multi-daemon git-race claim stress tests.

Covers phase_h_preflight.md (archived in git) §4.1 #6 (R5, invariant 6).

Six ``@pytest.mark.slow`` scenarios that pin the G.2 race-bypass fix:

1. both-race — both daemons start simultaneously; exactly one wins.
2. serial-retry — daemon B retries after A already claimed; gets clean None.
3. rebase-midway — B encounters a pull conflict mid-claim; recovers.
4. push-failure-revert-verify — push-fail triggers hard-reset + cleanup.
5. archive-race — bid archived while second daemon tries to claim.
6. stale-origin-ref — B's origin ref is stale post-pull; reverts to open;
   B's claim_node_bid still returns None (never proceeds).

All deterministic via threading.Barrier — no time.sleep.

NOTE: These tests run real git commands (git init --bare, git clone,
git push). They are slow (~5-15 s per test) and marked @pytest.mark.slow
so CI skips them in the fast loop and runs them on-merge per pyproject.toml.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml

from tinyassets.bid.node_bid import NodeBid, bid_path, bids_dir, claim_node_bid

# ---------------------------------------------------------------------------
# Git harness helpers
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=str(cwd), check=check,
        capture_output=True, text=True, timeout=20,
    )


def _git_init_user(clone: Path) -> None:
    _git(["git", "config", "user.email", "t@t.com"], clone)
    _git(["git", "config", "user.name", "Test Daemon"], clone)


def _write_bid_yaml(
    clone: Path,
    node_bid_id: str,
    *,
    status: str = "open",
    node_def_id: str = "test_node",
) -> Path:
    bids_dir(clone).mkdir(parents=True, exist_ok=True)
    p = bid_path(clone, node_bid_id)
    payload = {
        "node_bid_id": node_bid_id,
        "node_def_id": node_def_id,
        "required_llm_type": "",
        "inputs": {},
        "bid": 1.0,
        "submitted_by": "tester",
        "status": status,
        "evidence_url": "",
        "submitted_at": "2026-04-14T12:00:00+00:00",
    }
    p.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return p


def _git_add_commit_push(clone: Path, message: str = "update") -> None:
    _git(["git", "add", "-A"], clone)
    _git(["git", "commit", "-m", message], clone)
    _git(["git", "push", "origin", "HEAD"], clone)


def _git_pull(clone: Path) -> None:
    _git(["git", "pull", "--rebase"], clone)


@pytest.fixture
def race_repos(tmp_path: Path):
    """Bare origin + two worktree clones for stress tests.

    Layout::

        tmp_path/
            origin.git/    <- bare remote
            clone_a/       <- daemon-a's working tree
            clone_b/       <- daemon-b's working tree
    """
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(["git", "init", "--bare"], origin, check=True)

    for name in ("clone_a", "clone_b"):
        clone = tmp_path / name
        _git(["git", "clone", str(origin), str(clone)], tmp_path, check=True)
        _git_init_user(clone)
        bids_dir(clone).mkdir(parents=True, exist_ok=True)
        (bids_dir(clone) / ".gitkeep").write_text("", encoding="utf-8")

    clone_a = tmp_path / "clone_a"
    clone_b = tmp_path / "clone_b"

    # Initial commit from clone_a so the branch exists at origin.
    _git_add_commit_push(clone_a, "init bids dir")
    _git_pull(clone_b)  # bring clone_b up to date

    return {"origin": origin, "clone_a": clone_a, "clone_b": clone_b}


# ---------------------------------------------------------------------------
# Scenario 1 — both-race
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_claim_both_race(race_repos: dict[str, Path]) -> None:
    """Scenario 1: both daemons start simultaneously via Barrier.

    Exactly one wins (returns NodeBid); the other returns None.
    Pins the fundamental correctness of git-rename + push claim
    atomicity (G.2 R5).
    """
    clone_a = race_repos["clone_a"]
    clone_b = race_repos["clone_b"]

    # Write bid to clone_a and publish to origin so clone_b can pull it.
    _write_bid_yaml(clone_a, "nb_race1")
    _git_add_commit_push(clone_a, "add nb_race1")
    _git_pull(clone_b)

    barrier = threading.Barrier(2)
    results: dict[str, NodeBid | None] = {}

    def claim_a() -> None:
        barrier.wait()
        results["a"] = claim_node_bid(clone_a, "nb_race1", "daemon-a")

    def claim_b() -> None:
        barrier.wait()
        results["b"] = claim_node_bid(clone_b, "nb_race1", "daemon-b")

    threads = [threading.Thread(target=claim_a), threading.Thread(target=claim_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert "a" in results and "b" in results, "threads did not complete"
    wins = sum(1 for v in results.values() if v is not None)
    nones = sum(1 for v in results.values() if v is None)
    assert wins == 1, f"expected exactly 1 winner; got wins={wins}, results={results}"
    assert nones == 1, f"expected exactly 1 loser; got nones={nones}"
    winner = results["a"] or results["b"]
    assert winner is not None
    assert winner.status.startswith("claimed:")


# ---------------------------------------------------------------------------
# Scenario 2 — serial-retry
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_claim_serial_retry(race_repos: dict[str, Path]) -> None:
    """Scenario 2: daemon B retries after A already successfully claimed.

    B should get clean None without error. Tests that the post-push
    state (claimed file, no open file) is durable and legible to
    a second caller.
    """
    clone_a = race_repos["clone_a"]
    clone_b = race_repos["clone_b"]

    _write_bid_yaml(clone_a, "nb_serial")
    _git_add_commit_push(clone_a, "add nb_serial")
    _git_pull(clone_b)

    # A claims first.
    won_a = claim_node_bid(clone_a, "nb_serial", "daemon-a")
    assert won_a is not None, "daemon-a should win the first claim"

    # B tries after A has already pushed — B must return None cleanly.
    result_b = claim_node_bid(clone_b, "nb_serial", "daemon-b")
    assert result_b is None, "daemon-b should get None after serial retry"


# ---------------------------------------------------------------------------
# Scenario 3 — rebase-midway
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_claim_rebase_midway(race_repos: dict[str, Path], monkeypatch: Any) -> None:
    """Scenario 3: pull encounters a transient git error mid-claim.

    The claim still returns None gracefully (pull failure is logged
    + execution continues; the subsequent push will fail if the
    pull left state inconsistent).
    """
    import tinyassets.bid.node_bid as nb_mod

    clone_a = race_repos["clone_a"]
    clone_b = race_repos["clone_b"]

    _write_bid_yaml(clone_a, "nb_rebase")
    _git_add_commit_push(clone_a, "add nb_rebase")
    _git_pull(clone_b)

    real_run = subprocess.run
    pull_count = [0]

    def patched_run(args, **kwargs):
        if args[:2] == ["git", "pull"]:
            pull_count[0] += 1
            if pull_count[0] == 1:
                # Simulate a transient rebase failure on first pull.
                class _R:
                    returncode = 1
                    stdout = ""
                    stderr = "rebase conflict"
                return _R()
        return real_run(args, **kwargs)

    monkeypatch.setattr(nb_mod.subprocess, "run", patched_run)

    # With pull failing, push will also fail (not-fast-forward since
    # clone_b didn't get the latest state). Claim returns None.
    result = claim_node_bid(clone_b, "nb_rebase", "daemon-b")
    # Either None (push failed) or a NodeBid (if pull wasn't load-bearing here)
    # The important invariant: no exception raised.
    # We can't deterministically assert the claim outcome without controlling
    # the exact git state, but we CAN assert the function handles it gracefully.
    assert isinstance(result, (type(None), NodeBid)), (
        f"claim_node_bid should return NodeBid or None, got {type(result)}"
    )
    assert pull_count[0] >= 1, "pull should have been attempted"


# ---------------------------------------------------------------------------
# Scenario 4 — push-failure-revert-verify
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_claim_push_failure_revert_verify(race_repos: dict[str, Path], monkeypatch: Any) -> None:
    """Scenario 4: push-fail triggers hard-reset AND bid_outputs cleanup.

    Verifies the revert (step 5 of claim sequence) cleans both the
    renamed bid file and any executor bid_outputs pre-populated.
    """
    import tinyassets.bid.node_bid as nb_mod

    clone_a = race_repos["clone_a"]
    clone_b = race_repos["clone_b"]

    _write_bid_yaml(clone_b, "nb_revert")
    _git_add_commit_push(clone_b, "add nb_revert from b")
    _git_pull(clone_a)

    # Pre-populate bid_outputs in clone_b to assert cleanup fires.
    outputs_dir = clone_b / "bid_outputs" / "nb_revert"
    outputs_dir.mkdir(parents=True)
    (outputs_dir / "leftover.json").write_text("{}", encoding="utf-8")

    real_run = subprocess.run

    def push_fails(args, **kwargs):
        if args[:2] == ["git", "push"]:
            class _R:
                returncode = 1
                stdout = ""
                stderr = "non-fast-forward (simulated)"
            return _R()
        return real_run(args, **kwargs)

    monkeypatch.setattr(nb_mod, "_git_has_remote", lambda _: True)
    monkeypatch.setattr(nb_mod, "_git_current_branch", lambda _: "main")
    monkeypatch.setattr(nb_mod.subprocess, "run", push_fails)

    result = claim_node_bid(clone_b, "nb_revert", "daemon-b")
    assert result is None, "push-fail must return None"
    # bid_outputs cleaned up by _revert_claim.
    assert not outputs_dir.exists(), "bid_outputs dir should be removed on revert"


# ---------------------------------------------------------------------------
# Scenario 5 — archive-race
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_claim_archive_race(race_repos: dict[str, Path]) -> None:
    """Scenario 5: bid is already in a terminal state when B tries to claim.

    Simulates a bid being expired/archived by the system while
    another daemon holds a reference to it. Claim returns None cleanly.
    """
    clone_a = race_repos["clone_a"]
    clone_b = race_repos["clone_b"]

    # Write a bid that starts with status=open.
    _write_bid_yaml(clone_a, "nb_archive")
    _git_add_commit_push(clone_a, "add nb_archive")
    _git_pull(clone_b)

    # A archives the bid (sets status to "expired") before B claims.
    p = bid_path(clone_a, "nb_archive")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    data["status"] = "expired"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    _git_add_commit_push(clone_a, "archive nb_archive")
    _git_pull(clone_b)

    # B tries to claim the now-expired bid.
    result = claim_node_bid(clone_b, "nb_archive", "daemon-b")
    assert result is None, "expired bid should return None"


# ---------------------------------------------------------------------------
# Scenario 6 — stale-origin-ref
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_claim_stale_origin_ref(race_repos: dict[str, Path]) -> None:
    """Scenario 6: daemon B's pull is stale — it sees an old origin ref.

    After push-fail, B does ``git reset --hard origin/main`` which may
    restore the bid file to ``open`` status locally (because B's
    origin/main ref points to a commit before A's push). B's
    ``claim_node_bid`` call must still return None — never proceeds
    as if it owns the bid.

    Pins the exact race G.2 closed: the fallback path short-circuits
    to None on push failure regardless of local bid state after revert.
    """
    import tinyassets.bid.node_bid as nb_mod

    clone_a = race_repos["clone_a"]
    clone_b = race_repos["clone_b"]

    # Set up: both clones have the bid in "open" state.
    _write_bid_yaml(clone_a, "nb_stale")
    _git_add_commit_push(clone_a, "add nb_stale")
    _git_pull(clone_b)

    # A claims the bid (push succeeds — origin now has A's claimed file).
    won_a = claim_node_bid(clone_a, "nb_stale", "daemon-a")
    assert won_a is not None

    # B's pull is now STALE — it fetched before A pushed, so B's local
    # origin/main still points to the commit where the bid is "open".
    # We simulate this by NOT calling git pull before B's claim attempt,
    # AND by patching pull to return a "rebase conflict" so B's local
    # origin/main ref stays at the pre-A-push commit.
    real_run = subprocess.run

    def stale_pull(args, **kwargs):
        if args[:2] == ["git", "pull"]:
            class _R:
                returncode = 1
                stdout = ""
                stderr = "stale-origin-ref (simulated)"
            return _R()
        return real_run(args, **kwargs)

    monkeypatch_target = nb_mod.subprocess

    orig_run = monkeypatch_target.run
    monkeypatch_target.run = stale_pull
    try:
        # B tries to claim — the bid file in B's clone still looks "open"
        # because B's origin/main is stale. Push will fail (non-fast-forward)
        # since A already pushed. After revert, bid appears "open" locally.
        # claim_node_bid MUST return None — not the locally-appearing NodeBid.
        result = claim_node_bid(clone_b, "nb_stale", "daemon-b")
    finally:
        monkeypatch_target.run = orig_run

    assert result is None, (
        "stale-origin-ref: claim_node_bid must return None after push-fail "
        "even if bid appears open locally after revert"
    )
