"""Tests for durable push intents (``tinyassets.workspace_intents``).

A push is not idempotent from the daemon's side, so the question this module
answers is "did the remote take it?" -- and both wrong answers are expensive:
reporting success for a push that never landed, or retrying one that did. The
tests below are mostly about the states, because the states ARE the contract.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tinyassets import runs
from tinyassets.workspace_intents import (
    INTENT_STATES,
    open_intents,
    reconcile_push_intents,
    record_push_intent,
    settle_push_intent,
)

SHA = "a" * 40
OTHER = "b" * 40


@pytest.fixture()
def universe(tmp_path: Path) -> Path:
    base = tmp_path / "data" / "universe-1"
    base.mkdir(parents=True)
    runs.initialize_runs_db(base)
    return base


def _record(universe: Path, **over) -> str:
    fields = {
        "run_id": "run-1",
        "node_id": "push-node",
        "connection_id": "conn-git",
        "repo": "owner/name",
        "remote_ref": "refs/heads/tiny/u/slug",
        "sha": SHA,
        "host": "github.com",
        "grant_id": "grant-git",
        "universe_id": "universe-1",
    }
    fields.update(over)
    return record_push_intent(universe, **fields)


def _states(universe: Path) -> list[tuple[str, str]]:
    conn = sqlite3.connect(runs.runs_db_path(universe))
    try:
        return [
            (row[0], row[1])
            for row in conn.execute(
                "SELECT state, COALESCE(observed_sha, '') FROM workspace_push_intents"
            )
        ]
    finally:
        conn.close()


def test_an_intent_is_durable_the_moment_it_is_recorded(universe: Path) -> None:
    """Written BEFORE the wire: a crash mid-flight must leave a row."""
    intent_id = _record(universe)
    assert intent_id
    open_now = open_intents(universe)
    assert [i.intent_id for i in open_now] == [intent_id]
    assert open_now[0].state == "sent"
    assert open_now[0].sha == SHA


def test_settling_removes_it_from_the_open_set(universe: Path) -> None:
    intent_id = _record(universe)
    settle_push_intent(universe, intent_id, "done")
    assert open_intents(universe) == []
    assert _states(universe) == [("done", "")]


@pytest.mark.parametrize("state", INTENT_STATES)
def test_every_declared_state_is_settleable(universe: Path, state: str) -> None:
    intent_id = _record(universe)
    settle_push_intent(universe, intent_id, state)
    assert _states(universe)[0][0] == state


def test_an_undeclared_state_is_rejected(universe: Path) -> None:
    """The CHECK constraint is the backstop; this is the loud front door."""
    intent_id = _record(universe)
    with pytest.raises(ValueError):
        settle_push_intent(universe, intent_id, "probably-fine")


def test_the_table_refuses_an_undeclared_state_at_the_database_too(
    universe: Path,
) -> None:
    _record(universe)
    conn = sqlite3.connect(runs.runs_db_path(universe))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE workspace_push_intents SET state = 'maybe'")
            conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #


def test_the_same_sha_already_at_the_ref_reconciles_to_done(universe: Path) -> None:
    """A repeated non-force push of the same commit is success, not failure."""
    _record(universe)
    settled = reconcile_push_intents(
        universe,
        execute=lambda request: {"ok": True, "observed_sha": SHA},
        principal_for=lambda intent: "user-1",
    )
    assert [state for _id, state in settled] == ["done"]
    assert open_intents(universe) == []


def test_a_different_sha_at_the_ref_reconciles_to_failed_and_records_it(
    universe: Path,
) -> None:
    _record(universe)
    settled = reconcile_push_intents(
        universe,
        execute=lambda request: {"ok": True, "observed_sha": OTHER},
        principal_for=lambda intent: "user-1",
    )
    assert [state for _id, state in settled] == ["failed"]
    assert _states(universe) == [("failed", OTHER)]


def test_a_transport_failure_leaves_the_intent_claimable(universe: Path) -> None:
    """A pass that could not ASK must not retire the intent.

    Marking it `unknown` here would close a question nobody put to the remote;
    it stays `sent` so the next pass picks it up (Codex round 3, P1 #5).
    """
    _record(universe)
    settled = reconcile_push_intents(
        universe,
        execute=lambda request: {"ok": False, "error": "transport"},
        principal_for=lambda intent: "user-1",
    )
    assert [state for _id, state in settled] == ["sent"]
    assert _states(universe)[0][0] == "sent"
    still_open = open_intents(universe)
    assert len(still_open) == 1
    assert still_open[0].attempts == 1, "the attempt is counted"
    assert still_open[0].next_after, "and a retry floor is recorded"


def test_repeated_transport_failures_back_off_without_losing_the_intent(
    universe: Path,
) -> None:
    _record(universe)
    for expected in (1, 2, 3):
        reconcile_push_intents(
            universe,
            execute=lambda request: {"ok": False},
            principal_for=lambda intent: "user-1",
        )
        current = open_intents(universe)[0]
        assert current.attempts == expected
    assert open_intents(universe), "an unaskable intent is never dropped"


def test_a_successful_ls_remote_with_no_ref_is_a_real_failure(universe: Path) -> None:
    """Empty AND ok means the ref is absent: the push did not land.

    That is a different fact from "the remote could not be reached", and
    collapsing the two loses the only one that is actionable.
    """
    _record(universe)
    settled = reconcile_push_intents(
        universe,
        execute=lambda request: {"ok": True, "observed_sha": ""},
        principal_for=lambda intent: "user-1",
    )
    assert [state for _id, state in settled] == ["failed"]
    assert open_intents(universe) == []


def test_reconciliation_uses_the_intents_own_host(universe: Path) -> None:
    """Never a module default: that would contact a host this push never used."""
    seen: list[dict] = []
    _record(universe, host="git.example.test")
    reconcile_push_intents(
        universe,
        execute=lambda request: seen.append(request) or {"ok": True, "observed_sha": SHA},
        principal_for=lambda intent: "user-1",
    )
    assert seen[0]["host"] == "git.example.test"


def test_an_intent_with_no_recorded_host_is_deferred_not_guessed(
    universe: Path,
) -> None:
    seen: list[dict] = []
    _record(universe, host="")
    settled = reconcile_push_intents(
        universe,
        execute=lambda request: seen.append(request) or {"ok": True, "observed_sha": SHA},
        principal_for=lambda intent: "user-1",
    )
    assert [state for _id, state in settled] == ["sent"]
    assert seen == [], "no host means no contact at all"


def test_a_revoked_authority_stops_the_reconciler_before_the_host(
    universe: Path,
) -> None:
    """A grant revoked since the push must not be used to ask about it."""
    seen: list[dict] = []
    _record(universe)
    settled = reconcile_push_intents(
        universe,
        execute=lambda request: seen.append(request) or {"ok": True, "observed_sha": SHA},
        principal_for=lambda intent: "user-1",
        revalidate=lambda intent: False,
    )
    assert [state for _id, state in settled] == ["sent"]
    assert seen == [], "the host is never contacted on a dead grant"


def test_a_live_authority_lets_the_reconciler_through(universe: Path) -> None:
    """The guard must not be one that always fires."""
    _record(universe)
    settled = reconcile_push_intents(
        universe,
        execute=lambda request: {"ok": True, "observed_sha": SHA},
        principal_for=lambda intent: "user-1",
        revalidate=lambda intent: True,
    )
    assert [state for _id, state in settled] == ["done"]


def test_an_execute_that_raises_leaves_the_intent_claimable(universe: Path) -> None:
    def explode(request):
        raise OSError("the worker could not start")

    _record(universe)
    settled = reconcile_push_intents(
        universe, execute=explode, principal_for=lambda intent: "user-1"
    )
    assert [state for _id, state in settled] == ["sent"]
    assert open_intents(universe), "a crash is not an answer"


def test_reconciliation_asks_about_the_intent_s_own_ref_and_repo(
    universe: Path,
) -> None:
    seen: list[dict] = []
    _record(universe, repo="owner/other", remote_ref="refs/heads/tiny/u/other")
    reconcile_push_intents(
        universe,
        execute=lambda request: seen.append(request) or {"ok": True, "observed_sha": SHA},
        principal_for=lambda intent: "user-1",
    )
    assert seen[0]["op"] == "ls_remote"
    assert seen[0]["owner_repo"] == "owner/other"
    assert seen[0]["remote_ref"] == "refs/heads/tiny/u/other"
    # ...and it names the grant it was recorded under, never a credential:
    # the probe runs in the owner's cell and only the broker holds a token.
    assert (seen[0]["principal"], seen[0]["grant_id"]) == ("user-1", "grant-git")
    assert seen[0]["connection_id"] == "conn-git"
    assert "credential_ref" not in seen[0] and "staging_dir" not in seen[0]


def test_nothing_open_asks_the_remote_nothing(universe: Path) -> None:
    """A startup path that queries a remote for no reason is a startup path
    that fails when the remote is down."""
    seen: list[dict] = []
    assert reconcile_push_intents(universe, execute=lambda r: seen.append(r) or {}) == []
    assert seen == []


def test_a_settled_intent_is_not_reconciled_again(universe: Path) -> None:
    intent_id = _record(universe)
    settle_push_intent(universe, intent_id, "done")
    seen: list[dict] = []
    assert reconcile_push_intents(universe, execute=lambda r: seen.append(r) or {}) == []
    assert seen == []


def test_several_open_intents_are_all_settled(universe: Path) -> None:
    _record(universe, node_id="a")
    _record(universe, node_id="b", sha=OTHER)
    settled = reconcile_push_intents(
        universe,
        execute=lambda request: {"ok": True, "observed_sha": SHA},
        principal_for=lambda intent: "user-1",
    )
    assert sorted(state for _id, state in settled) == ["done", "failed"]


def test_the_worker_exposes_the_reconciler_for_the_startup_path() -> None:
    """``runs.py`` calls ``workspace_worker.reconcile_push_intents``."""
    from tinyassets import workspace_worker

    assert callable(workspace_worker.reconcile_push_intents)
    assert "reconcile_push_intents" in workspace_worker.__all__
