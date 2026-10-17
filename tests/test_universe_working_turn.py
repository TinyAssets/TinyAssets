"""Is this universe working right now? Read from the journal, scoped to the universe.

The web app had no way to ask. Its working indicator was a fact about the PAGE,
so a turn started by answering a request, by a queued line, from another window
or device, or one still running across a reload showed nothing at all (founder,
2026-09-26: turn ``d1a01eec`` ran 20:18:23Z to about 20:22Z with a blank screen).

Two properties this reader must hold, both of which a naive version gets wrong:

* UNIVERSE-scoped, not owner-scoped. ``agent_turns.owner_user_id`` is a provider
  capability principal, not the principal a served request's caller presents, so
  an owner match would answer "idle" during a live turn.
* A row older than the cap the coordinator already enforces is reported as
  ``stale``, never as activity and never hidden -- a killed container leaves one
  behind, and both painting it and dropping it silently are wrong.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tinyassets.storage import agent_turn_records as records
from tinyassets.storage.agent_turn_journal import (
    WORKING_STATES,
    AgentTurnJournal,
    ensure_schema,
)


def test_the_bound_is_the_cap_the_coordinator_will_actually_enforce(tmp_path):
    """The bound follows the GRANTED turn's cap, not the library default.

    Codex refuted the first version of this on #4020: it used the library's 600s
    default, while the granted founder turn -- the only kind the app produces --
    gets 3600s with a per-universe override. A 630s bound called a healthy turn
    dead after ten and a half minutes, hiding the indicator for exactly the long
    turns it was added for.
    """
    from tinyassets.api.status import (
        _WORKING_TURN_STALE_AFTER_S,
        _working_turn_max_age_s,
    )
    from tinyassets.universe_intelligence import served_absolute_cap_s

    # A turn has NO platform cap now (founder, 2026-09-30: it runs until it is
    # finished), so the display bound cannot be derived from one -- deriving it
    # would answer "not working" about a turn that IS working, which is the same
    # bug #4020 fixed in the other direction. It is a DISPLAY freshness rule.
    plain = tmp_path / "plain"
    plain.mkdir()
    assert served_absolute_cap_s(None) is None
    assert _working_turn_max_age_s(plain) == _WORKING_TURN_STALE_AFTER_S
    assert _WORKING_TURN_STALE_AFTER_S >= 6 * 3600, (
        "far past any observed turn: a stale indicator beats a hidden one"
    )

    # A universe that caps ITS OWN turns knows when its row is dead, so its own
    # number still drives the bound -- the same resolver the coordinator uses.
    class _Raised:
        absolute_cap_s = 7200.0

    assert served_absolute_cap_s(_Raised()) == 7200.0

    class _Nonsense:
        absolute_cap_s = "later"

    assert served_absolute_cap_s(_Nonsense()) is None


_CAP = 3630.0  # the status projection's own bound; pinned to the real one just above


@pytest.fixture
def journal(tmp_path):
    from tinyassets.daemon_server import set_founder_home

    set_founder_home(tmp_path, founder_sub="owner", universe_id="home", platform_generated=True)
    return AgentTurnJournal(tmp_path)


def _now():
    return datetime.now(timezone.utc)


def _state(journal, owner, universe, turn_id, state):
    """Put one existing row into a state, without pretending to run a turn."""
    with journal._ledger.connection() as conn:
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute(
            "UPDATE agent_turns SET state = ? "
            "WHERE owner_user_id = ? AND universe_id = ? AND turn_id = ?",
            (state, owner, universe, turn_id),
        ).rowcount
        conn.commit()
    assert changed == 1


def _age(journal, turn_id, seconds):
    older = (_now() - timedelta(seconds=seconds)).isoformat(
        timespec="microseconds").replace("+00:00", "Z")
    with journal._ledger.connection() as conn:
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE agent_turns SET created_at = ? WHERE turn_id = ?", (older, turn_id))
        conn.commit()


def test_a_fresh_turn_is_reported_with_its_state_and_age(journal):
    turn = journal.create("owner", "home", prompt="summarise the run", system="s")
    _age(journal, turn.turn_id, 214)
    observed = journal.universe_working_turn("home", now=_now(), max_age_s=_CAP)
    assert observed is not None, "a just-created turn is the universe working"
    assert observed["turn_id"] == turn.turn_id and observed["state"] == "ready"
    assert observed["stale"] is False
    assert 213 <= observed["age_s"] <= 216, observed["age_s"]
    assert observed["started_at"].endswith("Z")
    # Nothing about WHAT it is working on rides along: no prompt, model or owner.
    assert set(observed) == {"turn_id", "state", "started_at", "age_s", "stale"}


@pytest.mark.parametrize("state", sorted(WORKING_STATES))
def test_every_progressing_state_reads_as_working(journal, state):
    turn = journal.create("owner", "home", prompt="p", system="s")
    _state(journal, "owner", "home", turn.turn_id, state)
    observed = journal.universe_working_turn("home", now=_now(), max_age_s=_CAP)
    assert observed is not None and observed["state"] == state


@pytest.mark.parametrize("state", ["completed", "abandoned", "held_refusal",
                                   "held_transport", "held_tool_unknown"])
def test_a_stopped_or_ambiguous_turn_is_not_working(journal, state):
    turn = journal.create("owner", "home", prompt="p", system="s")
    _state(journal, "owner", "home", turn.turn_id, state)
    assert journal.universe_working_turn("home", now=_now(), max_age_s=_CAP) is None, (
        f"{state} is a turn that has stopped; reporting it would leave a tab thinking"
    )


def test_the_answer_does_not_depend_on_the_callers_own_principal(journal):
    """The owner column is a provider capability principal, not the caller's id."""
    from tinyassets.daemon_server import set_founder_home

    set_founder_home(journal._ledger.base_path, founder_sub="capability:writer:7",
                     universe_id="home", platform_generated=True)
    turn = journal.create("capability:writer:7", "home", prompt="p", system="s")
    observed = journal.universe_working_turn("home", now=_now(), max_age_s=_CAP)
    assert observed is not None and observed["turn_id"] == turn.turn_id, (
        "a reader keyed on the caller's principal would answer 'idle' here, which "
        "is the blank screen this fixes")


def test_another_universes_turn_is_never_reported_here(journal, tmp_path):
    from tinyassets.daemon_server import set_founder_home

    set_founder_home(tmp_path, founder_sub="neighbour", universe_id="other",
                     platform_generated=True)
    journal.create("neighbour", "other", prompt="p", system="s")
    assert journal.universe_working_turn("home", now=_now(), max_age_s=_CAP) is None
    assert journal.universe_working_turn("other", now=_now(), max_age_s=_CAP) is not None


def test_a_row_past_the_cap_is_reported_stale_rather_than_as_work(journal):
    """A killed container leaves a progressing row behind forever."""
    turn = journal.create("owner", "home", prompt="p", system="s")
    _state(journal, "owner", "home", turn.turn_id, "inference_started")
    _age(journal, turn.turn_id, _CAP + 60)
    observed = journal.universe_working_turn("home", now=_now(), max_age_s=_CAP)
    assert observed is not None, (
        "a wedged row must stay observable; hiding it is how it stops being fixed")
    assert observed["stale"] is True and observed["state"] == "inference_started"
    # Same row, a cap that has not expired: only the bound differs.
    fresh = journal.universe_working_turn("home", now=_now(), max_age_s=_CAP + 600)
    assert fresh is not None and fresh["stale"] is False


def test_a_fresh_turn_wins_over_a_wedged_one(journal):
    old = journal.create("owner", "home", prompt="old", system="s")
    _state(journal, "owner", "home", old.turn_id, "tools_pending")
    _age(journal, old.turn_id, _CAP + 3600)
    live = journal.create("owner", "home", prompt="live", system="s")
    observed = journal.universe_working_turn("home", now=_now(), max_age_s=_CAP)
    assert observed["turn_id"] == live.turn_id and observed["stale"] is False


def test_no_database_and_no_turn_table_are_both_simply_idle(tmp_path):
    """The read creates neither the database nor the schema."""
    empty = AgentTurnJournal(tmp_path / "nothing-here")
    assert empty.universe_working_turn("home", now=_now(), max_age_s=_CAP) is None
    assert not (tmp_path / "nothing-here").exists(), (
        "an observational read must not bring a database into being")


def test_the_read_creates_no_tables_in_a_database_that_lacks_them(tmp_path):
    """Codex on #4020 reproduced this: the read created five ledger tables.

    The first version went through ``SQLiteProviderWorkAuthorityStore.connection()``,
    which runs ``executescript(_SCHEMA)`` on every open. An observational status
    read must not initialize storage, and a schema write can take BEGIN IMMEDIATE
    against a turn that is running right now.
    """
    import sqlite3

    from tinyassets.storage import db_path

    base = tmp_path / "bare"
    base.mkdir()
    path = db_path(base)
    with sqlite3.connect(path) as seed:
        seed.execute("CREATE TABLE sentinel (only_this TEXT)")
    before = {row[0] for row in sqlite3.connect(path).execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert before == {"sentinel"}

    assert AgentTurnJournal(base).universe_working_turn(
        "home", now=_now(), max_age_s=_CAP) is None

    after = {row[0] for row in sqlite3.connect(path).execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert after == before, f"the read created {sorted(after - before)}"


def test_a_non_positive_bound_and_a_naive_clock_are_refused(journal):
    with pytest.raises(ValueError):
        journal.universe_working_turn("home", now=_now(), max_age_s=0)
    with pytest.raises(ValueError):
        journal.universe_working_turn("home", now=datetime.now(), max_age_s=_CAP)
    with pytest.raises(ValueError):
        journal.universe_working_turn("", now=_now(), max_age_s=_CAP)


def test_the_reset_blocker_set_still_covers_the_ambiguous_holds(journal):
    """WORKING_STATES is narrower than what blocks a reset, and must stay so.

    The blocker set is now expressed as ``WORKING_STATES | _AMBIGUOUS_STATES`` so
    neither question carries its own literal list. Widening one must not silently
    widen the other -- "is a reset safe" and "is this universe working" are
    different questions about the same rows.
    """
    from tinyassets.storage.agent_turn_journal import _AMBIGUOUS_STATES, reset_blockers

    # A created-but-unstarted turn: working, and it blocks an offline reset.
    journal.create("owner", "home", prompt="p", system="s")
    with journal._ledger.connection() as conn:
        ensure_schema(conn)
        assert reset_blockers(conn, "owner", "home") == [
            "active or ambiguous agent turn references exact home"]
    assert journal.universe_working_turn("home", now=_now(), max_age_s=_CAP) is not None

    assert WORKING_STATES.isdisjoint(_AMBIGUOUS_STATES)
    assert (WORKING_STATES | _AMBIGUOUS_STATES) == {
        "ready", "inference_started", "tools_pending",
        "held_tool_unknown"}
    assert (WORKING_STATES | _AMBIGUOUS_STATES) <= records.STATES
