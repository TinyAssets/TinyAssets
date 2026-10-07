# ruff: noqa: F811
"""A failed background activity must not wedge the owner's main chat."""
from datetime import datetime, timezone

import pytest

from tests.test_agent_turn_journal import journal, new  # noqa: F401
from tinyassets.agent_turn_reconcile import reconcile_orphaned_turns
from tinyassets.storage import agent_turn_runner as runner
from tinyassets.storage.agent_turn_boot import BootTurns
from tinyassets.turn_interrupt import request_interrupt


def working(journal):
    return journal.universe_working_turn(
        "home", now=datetime.now(timezone.utc), max_age_s=999999, boot=BootTurns())


def orphan(journal, *, legacy=False, agent="main"):
    turn = journal.create("owner", "home", prompt="activity", system="", agent_id=agent)
    runner.release(journal._ledger.base_path, turn.turn_id)
    if legacy:
        with journal._ledger.connection() as conn:
            conn.execute("UPDATE agent_turns SET runner_token = '' WHERE turn_id = ?",
                         (turn.turn_id,))
    return turn


@pytest.mark.parametrize("legacy", [False, True])
def test_orphan_is_idle_to_another_reader_and_stop_settles_it(journal, legacy):
    turn = orphan(journal, legacy=legacy)
    assert working(journal) is None
    assert request_interrupt("owner", "home", agent_id="main",
                             base_path=journal._ledger.base_path) == 1
    assert journal.get("owner", "home", turn.turn_id).state == "abandoned"
    assert request_interrupt("owner", "home", base_path=journal._ledger.base_path) == 0


def test_boot_recovers_legacy_row_in_current_live_owner_generation(journal):
    turn = orphan(journal, legacy=True)
    result = reconcile_orphaned_turns(journal._ledger.base_path)
    assert [(r["turn_id"], r["settled"]) for r in result] == [(turn.turn_id, "abandoned")]


def test_stop_cannot_settle_another_owner_agent_or_live_runner(journal):
    main = new(journal)
    other = orphan(journal, agent="researcher")
    base = journal._ledger.base_path
    assert request_interrupt("intruder", "home", base_path=base) == 0
    assert request_interrupt("owner", "elsewhere", base_path=base) == 0
    assert request_interrupt("owner", "home", agent_id="main", base_path=base) == 0
    assert working(journal)["turn_id"] == main.turn_id
    assert journal.get("owner", "home", other.turn_id).state == "ready"
    assert request_interrupt("owner", "home", agent_id="researcher", base_path=base) == 1
    runner.release(base, main.turn_id)


def test_runner_claim_is_visible_across_processes_and_dies_with_runner(tmp_path):
    import subprocess
    import sys

    code = """
import sys
from pathlib import Path
from tinyassets.storage.agent_turn_runner import claim
with claim(Path(sys.argv[1]), 'turn') as token:
    print(token, flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        token = child.stdout.readline().strip()
        assert runner.alive(tmp_path, token)
        assert not runner.orphan(tmp_path, token)
        child.kill()
        child.wait(timeout=10)
        assert runner.orphan(tmp_path, token)
        assert not runner.alive(tmp_path, token)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_failed_activity_reason_reaches_chat_and_next_agent_turn(tmp_path, monkeypatch):
    from tinyassets import activity_dispatcher as dispatcher
    from tinyassets import agent_activities as activities
    from tinyassets.conversation_store import load_recent

    root = tmp_path / "home"
    root.mkdir()
    activity = activities.create(root, owner_principal="owner", title="Background job",
                                 brief="Do work", origin_kind="ask")
    generation = activities.claim(root, activity["activity_id"], replaceable=lambda _: False)
    activities.bind_run(root, activity["activity_id"], generation, "failed-run")
    reason = "activity runs need an engine-inference executor until native yield is fenced"
    monkeypatch.setattr(dispatcher, "_run_outcome", lambda *a: ("failed", reason))
    assert dispatcher._settle_ended(tmp_path, "home", activities.get(root, activity["activity_id"]))
    dispatcher._publish_failures(root)
    dispatcher._publish_failures(root)
    rows = load_recent(root, "principal:owner")
    assert len(rows) == 1 and reason in rows[0].text
    assert any(reason in e["line"] for e in activities.undelivered_lines(root))


def test_native_only_start_queues_the_activity(tmp_path, monkeypatch):
    """A native-only connection runs activities like any other: every tool it
    sees crosses the engine route's activity fence, and its call is cancelled
    once the activity stops (tests/test_activity_fence.py,
    tests/test_activity_http_yield.py). The 2026-10-06 admission refusal is
    retired with that boundary in place."""
    from types import SimpleNamespace

    from tinyassets import api, provider_assignment
    from tinyassets.agent_activities import list_page
    from tinyassets.api.activities import write
    from tinyassets.providers import call

    (tmp_path / "home").mkdir()
    monkeypatch.setattr(provider_assignment, "load_provider_assignment", lambda *a, **k:
                        SimpleNamespace(provider="subscription", candidates=()))
    monkeypatch.setattr(call, "get_provider_router", lambda: SimpleNamespace(
        selected_agent_execution_kind=lambda _: "native_agent"))
    monkeypatch.setattr(api.activities, "_wake", lambda *a: None)
    result = write(tmp_path, universe_id="home", actor_id="owner", operation="start",
                   payload={"title": "job", "brief": "work"})
    assert "error" not in result, result
    assert [a["activity_id"] for a in list_page(tmp_path / "home")["activities"]] == [
        result["activity_id"]]
