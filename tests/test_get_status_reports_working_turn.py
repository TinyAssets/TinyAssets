"""``get_status`` carries whether the universe is working, so a client can show it.

The web app's working indicator was local to whichever page had sent the message.
It now reads this field, which is the only thing that can tell a tab that did not
send -- or a tab that just reloaded -- that a turn is running (founder,
2026-09-26).

The field is gated the way the conversation peek is (write access to the
universe) and carries no prompt or owner: that a turn is progressing, since
when, its journal state, and -- once the running turn has opened a round -- the
step number, the model id that step is waiting on, and for how long. The model
is the one the same readers already see on every reply's "Answered by" line; a
10-minute wait on one model request read exactly like a hang (live 2026-10-02).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tinyassets.api.status import get_status
from tinyassets.storage.agent_turn_journal import AgentTurnJournal, ensure_schema


@pytest.fixture(autouse=True)
def _home(founder_home):
    return founder_home


def _journal(founder_home):
    return AgentTurnJournal(founder_home.parent)


def _start_turn(founder_home, *, state="inference_started", age_s=0.0):
    """One journal row for this universe, as a served turn would leave it."""
    from tinyassets.daemon_server import set_founder_home

    base = founder_home.parent
    set_founder_home(base, founder_sub="capability:writer:1",
                     universe_id=founder_home.name, platform_generated=True)
    journal = _journal(founder_home)
    turn = journal.create("capability:writer:1", founder_home.name,
                          prompt="summarise the run", system="s")
    started = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat(
        timespec="microseconds").replace("+00:00", "Z")
    with journal._ledger.connection() as conn:
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE agent_turns SET state = ?, created_at = ? WHERE turn_id = ?",
                     (state, started, turn.turn_id))
        conn.commit()
    return turn.turn_id


def test_an_idle_universe_reports_the_field_as_null(founder_home):
    """Present and null, so a client can tell idle from a build that cannot say."""
    payload = json.loads(get_status())
    assert "active_turn" in payload, (
        "an absent key is indistinguishable from an older daemon; the app treats "
        "it that way and would leave a stale indicator up")
    assert payload["active_turn"] is None


def test_a_running_turn_is_reported_with_its_state_and_age(founder_home):
    turn_id = _start_turn(founder_home, state="tools_pending", age_s=214.0)
    row = json.loads(get_status())["active_turn"]
    assert row is not None, "a turn the journal says is running must be observable"
    assert row["turn_id"] == turn_id and row["state"] == "tools_pending"
    assert row["stale"] is False and 213 <= row["age_s"] <= 217
    # The prompt is private; the indicator needs none of it.
    assert set(row) == {"turn_id", "state", "started_at", "age_s", "stale"}
    assert "summarise the run" not in json.dumps(row)


def test_a_wedged_row_is_reported_stale_and_not_as_work(founder_home):
    from tinyassets.api.status import _working_turn_max_age_s

    bound = _working_turn_max_age_s(founder_home)
    _start_turn(founder_home, state="native_started", age_s=bound + 120)
    row = json.loads(get_status())["active_turn"]
    assert row is not None and row["stale"] is True, (
        "a row a killed container left behind must stay visible, and must not be "
        "painted as activity")


def test_a_finished_turn_leaves_the_universe_idle(founder_home):
    _start_turn(founder_home, state="completed", age_s=5.0)
    assert json.loads(get_status())["active_turn"] is None


def test_an_unreadable_journal_says_so_instead_of_reporting_idle(founder_home, monkeypatch):
    """Hard Rule 8: a read that failed is not evidence that nothing is happening."""
    import tinyassets.storage.agent_turn_journal as journal_module

    def explode(self, universe, *, now, max_age_s):
        raise journal_module.JournalUnavailable("unreadable")

    monkeypatch.setattr(journal_module.AgentTurnJournal, "universe_working_turn", explode)
    row = json.loads(get_status())["active_turn"]
    assert row == {"state": "unreadable", "reason": "JournalUnavailable"}, (
        "an unreadable journal reported as idle is the silent fallback this "
        "project refuses")


def test_a_reader_without_write_access_is_not_told(founder_home, monkeypatch):
    from tinyassets.api import permissions

    _start_turn(founder_home, state="inference_started", age_s=3.0)
    assert json.loads(get_status())["active_turn"] is not None   # the gate is open here
    monkeypatch.setattr(permissions, "universe_access_allows",
                        lambda universe_id, *, write=False: False)
    payload = json.loads(get_status())
    assert "active_turn" not in payload, (
        "activity is gated like the conversation peek: a reader the universe has "
        "not granted write access is told nothing about it")


def test_a_running_turn_says_which_step_and_model_it_waits_on(founder_home, monkeypatch):
    """Turn c6ae56f9 waited ~10 minutes on one qwen request with no way to tell."""
    from tinyassets.api import status
    from tinyassets.storage.agent_turn_boot import BOOT

    monkeypatch.setattr(status, "_reader_owns", lambda _uid: True)

    turn_id = _start_turn(founder_home, state="inference_started", age_s=900.0)
    BOOT.claim(founder_home.name, turn_id)
    BOOT.note_round(founder_home.name, turn_id, round=4, model="qwen/qwen3.8-27b:free",
                    now=datetime.now(timezone.utc) - timedelta(seconds=420))
    try:
        row = json.loads(get_status())["active_turn"]
        assert row["round"] == 4 and row["model"] == "qwen/qwen3.8-27b:free"
        assert 419 <= row["round_age_s"] <= 423
        assert "summarise the run" not in json.dumps(row)
    finally:
        BOOT.release(founder_home.name, turn_id)
    # Released with the turn: a finished turn leaves no step behind.
    assert BOOT.progress(founder_home.name, turn_id) is None


def test_a_round_is_not_noted_for_a_turn_this_boot_is_not_running():
    from tinyassets.storage.agent_turn_boot import BootTurns

    boot = BootTurns()
    boot.note_round("u-x", "t-1", round=1, model="m")
    assert boot.progress("u-x", "t-1") is None


def test_a_co_admin_sees_the_step_but_never_the_owners_model_id(founder_home, monkeypatch):
    """Codex: the requested selector can be an account-bearing private id that the
    reply's "Answered by" never shows; only the owning account reads it."""
    from tinyassets.api import status
    from tinyassets.storage.agent_turn_boot import BOOT

    monkeypatch.setattr(status, "_reader_owns", lambda _uid: False)
    turn_id = _start_turn(founder_home, state="inference_started", age_s=60.0)
    BOOT.claim(founder_home.name, turn_id)
    BOOT.note_round(founder_home.name, turn_id, round=2, model="owner-alice@example.com-private-9")
    try:
        row = json.loads(get_status())["active_turn"]
        assert row["round"] == 2 and "round_age_s" in row
        assert "model" not in row and "private-9" not in json.dumps(row)
    finally:
        BOOT.release(founder_home.name, turn_id)


def test_ownership_is_read_from_the_one_owner_resolver(monkeypatch):
    from tinyassets import universe_owner
    from tinyassets.api import permissions, status

    monkeypatch.setattr(permissions, "current_actor_id", lambda: "user_a")
    monkeypatch.setattr(universe_owner, "owner_of", lambda _root, _uid: "user_a")
    assert status._reader_owns("u-1") is True
    monkeypatch.setattr(universe_owner, "owner_of", lambda _root, _uid: "user_b")
    assert status._reader_owns("u-1") is False

    def broken(_root, _uid):
        raise OSError("unreadable")

    monkeypatch.setattr(universe_owner, "owner_of", broken)
    assert status._reader_owns("u-1") is False
