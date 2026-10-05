"""Durable, session-anchored conversation store for the stateless universe turn.

The universe turn is rebuilt from scratch every call. The 2026-08-08 hotfix gave
the SLACK path a sliding window pulled from `conversations.history`, but the MCP
`converse` path had no memory at all and nothing was durable/surface-agnostic —
u-tiny's own diagnosis (Slack thread 1786225160): *"the sliding window injection
is built but not the session-anchored persistence layer."*

This is that layer: a SQLite store keyed by `(session_id, turn_no)`, per universe,
feeding the already-reviewed `conversation_memory.format_history` formatter.
Design: docs/design-notes/2026-08-09-persistent-conversation-memory.md.
"""

from __future__ import annotations

import threading

from tinyassets import conversation_store as cs
from tinyassets.conversation_memory import Msg


def test_record_then_load_roundtrips_oldest_first(tmp_path):
    cs.record_turn(tmp_path, "slack:C1", "founder", "post the update")
    cs.record_turn(tmp_path, "slack:C1", "universe", "on it")
    cs.record_turn(tmp_path, "slack:C1", "founder", "did it go out?")
    got = cs.load_recent(tmp_path, "slack:C1")
    assert [(m.speaker, m.text) for m in got] == [
        ("founder", "post the update"),
        ("universe", "on it"),
        ("founder", "did it go out?"),
    ]
    assert all(isinstance(m, Msg) for m in got)


def test_load_recent_is_empty_when_nothing_stored(tmp_path):
    assert cs.load_recent(tmp_path, "slack:never") == []


def test_sessions_are_isolated(tmp_path):
    cs.record_turn(tmp_path, "slack:A", "founder", "topic A")
    cs.record_turn(tmp_path, "slack:B", "founder", "topic B")
    a = cs.load_recent(tmp_path, "slack:A")
    b = cs.load_recent(tmp_path, "slack:B")
    assert [m.text for m in a] == ["topic A"]
    assert [m.text for m in b] == ["topic B"]


def test_universes_are_isolated(tmp_path):
    u1 = tmp_path / "u1"
    u2 = tmp_path / "u2"
    u1.mkdir()
    u2.mkdir()
    cs.record_turn(u1, "converse:u1", "founder", "secret for u1")
    got_u2 = cs.load_recent(u2, "converse:u1")
    # Same session-id string, different universe dir → no bleed.
    assert got_u2 == []
    assert [m.text for m in cs.load_recent(u1, "converse:u1")] == ["secret for u1"]


def test_turn_no_is_per_session_and_monotonic(tmp_path):
    t1 = cs.record_turn(tmp_path, "slack:A", "founder", "one")
    t2 = cs.record_turn(tmp_path, "slack:A", "universe", "two")
    other = cs.record_turn(tmp_path, "slack:B", "founder", "b-one")
    assert t1 == 1
    assert t2 == 2
    # A fresh session starts its own turn count at 1, not the global max.
    assert other == 1


def test_limit_keeps_only_the_most_recent_n(tmp_path):
    for i in range(30):
        cs.record_turn(tmp_path, "slack:A", "founder", f"m{i}")
    got = cs.load_recent(tmp_path, "slack:A", limit=5)
    assert [m.text for m in got] == ["m25", "m26", "m27", "m28", "m29"]


def test_empty_or_blank_text_is_not_recorded(tmp_path):
    cs.record_turn(tmp_path, "slack:A", "founder", "real")
    cs.record_turn(tmp_path, "slack:A", "founder", "")
    cs.record_turn(tmp_path, "slack:A", "founder", "   ")
    got = cs.load_recent(tmp_path, "slack:A")
    assert [m.text for m in got] == ["real"]


def test_load_recent_never_raises_on_bad_dir(tmp_path):
    # Best-effort: a store that cannot be opened returns [] (no memory this
    # turn), it never breaks the reply.
    bad = tmp_path / "does_not_exist"  # not created
    assert cs.load_recent(bad, "slack:A") == []


def test_concurrent_records_do_not_collide_on_turn_no(tmp_path):
    # Two turns racing on the same session must not both claim the same turn_no
    # and must not lose a write. SQLite is the single writer of record.
    session = "slack:race"
    n = 40
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            cs.record_turn(tmp_path, session, "founder", f"msg{i}")
        except Exception as exc:  # noqa: BLE001 - surface in assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"record_turn raised under concurrency: {errors[:3]}"
    got = cs.load_recent(tmp_path, session, limit=1000)
    # No writes lost, and every message is present exactly once.
    assert len(got) == n
    assert sorted(m.text for m in got) == sorted(f"msg{i}" for i in range(n))


def test_feeds_the_formatter(tmp_path):
    from tinyassets.conversation_memory import format_history

    cs.record_turn(tmp_path, "slack:A", "founder", "my favorite topic is tide pools")
    cs.record_turn(tmp_path, "slack:A", "universe", "noted")
    block = format_history(cs.load_recent(tmp_path, "slack:A"))
    assert "tide pools" in block
    # The formatter's untrusted-not-consent fence is intact.
    assert "NOT consent" in block


def test_has_prior_turns_helper(tmp_path):
    # Used by the Slack cold-store backfill decision: does this session already
    # have durable history, or should we backfill from the Slack API once?
    assert cs.has_prior_turns(tmp_path, "slack:A") is False
    cs.record_turn(tmp_path, "slack:A", "founder", "hi")
    assert cs.has_prior_turns(tmp_path, "slack:A") is True


def test_backfill_once_imports_a_timeline(tmp_path):
    msgs = [
        {"speaker": "founder", "text": "older ask"},
        {"speaker": "universe", "text": "older reply"},
        {"speaker": "founder", "text": "", "extra": "blank dropped"},
    ]
    n = cs.backfill_once(tmp_path, "slack:A", msgs)
    assert n == 2  # the blank is not imported
    assert [(m.speaker, m.text) for m in cs.load_recent(tmp_path, "slack:A")] == [
        ("founder", "older ask"),
        ("universe", "older reply"),
    ]
    assert cs.is_backfilled(tmp_path, "slack:A") is True


def test_backfill_once_is_idempotent(tmp_path):
    msgs = [{"speaker": "founder", "text": "one"}]
    assert cs.backfill_once(tmp_path, "slack:A", msgs) == 1
    # A second import claims nothing and does NOT duplicate.
    assert cs.backfill_once(tmp_path, "slack:A", msgs) == 0
    assert [m.text for m in cs.load_recent(tmp_path, "slack:A")] == ["one"]


def test_backfill_once_marks_even_an_empty_timeline(tmp_path):
    # An empty timeline still claims the marker, so we do not re-hit Slack every
    # turn for a brand-new conversation.
    assert cs.backfill_once(tmp_path, "slack:new", []) == 0
    assert cs.is_backfilled(tmp_path, "slack:new") is True


def test_concurrent_backfill_imports_exactly_once(tmp_path):
    # The bug Codex flagged: two cold turns racing both run the backfill loop and
    # duplicate every imported message. The marker + single transaction makes
    # exactly one worker win.
    msgs = [{"speaker": "founder", "text": f"m{i}"} for i in range(10)]
    session = "slack:race"
    results: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        got = cs.backfill_once(tmp_path, session, msgs)
        with lock:
            results.append(got)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one worker imported the 10 messages; the rest imported nothing.
    assert sorted(results) == [0, 0, 0, 0, 0, 0, 0, 10]
    # And the store holds each message exactly once — no duplication.
    got = cs.load_recent(tmp_path, session, limit=1000)
    assert sorted(m.text for m in got) == sorted(f"m{i}" for i in range(10))


def test_backfill_then_record_continues_turn_numbering(tmp_path):
    cs.backfill_once(tmp_path, "slack:A", [{"speaker": "founder", "text": "imported"}])
    t = cs.record_turn(tmp_path, "slack:A", "founder", "live turn")
    assert t == 2  # continues after the 1 imported turn, no collision
    assert [m.text for m in cs.load_recent(tmp_path, "slack:A")] == [
        "imported",
        "live turn",
    ]


# -- sync_tail: self-healing when a record_turn was dropped (silent drift) ----


def test_load_resyncs_a_tail_the_store_missed(tmp_path):
    """`backfill_once` runs exactly once, so a dropped `record_turn` leaves the
    store BEHIND the live thread forever — the exact silent drift that froze
    u-tiny's memory. `sync_tail` reconciles the missing trailing turns.

    Mutation-check: delete the `record_turn` inside `sync_tail`'s append loop and
    the store stays two turns behind — this assertion goes red.
    """
    session = "slack:C1"
    # The store has the older turns but MISSED the two most recent.
    cs.record_turn(
        tmp_path, session, "founder", "what's our plan?", ts=1.0, ext_id="1.0"
    )
    cs.record_turn(
        tmp_path, session, "universe", "ship the memory fix", ts=2.0, ext_id="2.0"
    )
    # The live Slack timeline is ahead by two turns.
    live = [
        {"speaker": "founder", "text": "what's our plan?", "ts": "1.0"},
        {"speaker": "universe", "text": "ship the memory fix", "ts": "2.0"},
        {"speaker": "founder", "text": "did the deploy go out?", "ts": "3.0"},
        {"speaker": "universe", "text": "yes, sha abc123 is live", "ts": "4.0"},
    ]
    appended = cs.sync_tail(tmp_path, session, live)
    assert appended == 2
    assert [m.text for m in cs.load_recent(tmp_path, session)] == [
        "what's our plan?",
        "ship the memory fix",
        "did the deploy go out?",
        "yes, sha abc123 is live",
    ]


def test_sync_tail_is_a_noop_when_the_store_is_current(tmp_path):
    session = "slack:C1"
    cs.record_turn(tmp_path, session, "founder", "hi")
    cs.record_turn(tmp_path, session, "universe", "hello")
    live = [
        {"speaker": "founder", "text": "hi"},
        {"speaker": "universe", "text": "hello"},
    ]
    assert cs.sync_tail(tmp_path, session, live) == 0
    assert [m.text for m in cs.load_recent(tmp_path, session)] == ["hi", "hello"]


def test_sync_tail_leaves_a_cold_store_to_backfill(tmp_path):
    # A cold store (nothing recorded) is `backfill_once`'s job, not sync_tail's;
    # it must not append a whole window here and race the backfill.
    live = [{"speaker": "founder", "text": "brand new"}]
    assert cs.sync_tail(tmp_path, "slack:new", live) == 0
    assert cs.load_recent(tmp_path, "slack:new") == []


def test_sync_tail_skips_when_there_is_no_overlap(tmp_path):
    # If the live window shares nothing with the store, appending a whole window
    # blind risks duplication — leave it to normal recording.
    session = "slack:C1"
    cs.record_turn(tmp_path, session, "founder", "old thing")
    live = [{"speaker": "founder", "text": "totally different"}]
    assert cs.sync_tail(tmp_path, session, live) == 0
    assert [m.text for m in cs.load_recent(tmp_path, session)] == ["old thing"]


def test_sync_tail_never_duplicates_a_repeated_phrase_at_the_seam(tmp_path):
    # "yes" appears in both stored tail and the missing tail; sync_tail must not
    # re-append the already-stored one.
    session = "slack:C1"
    cs.record_turn(tmp_path, session, "founder", "go ahead?", ts=1.0, ext_id="1.0")
    cs.record_turn(tmp_path, session, "founder", "yes", ts=2.0, ext_id="2.0")
    live = [
        {"speaker": "founder", "text": "go ahead?", "ts": "1.0"},
        {"speaker": "founder", "text": "yes", "ts": "2.0"},
        {"speaker": "universe", "text": "done", "ts": "3.0"},
    ]
    assert cs.sync_tail(tmp_path, session, live) == 1
    assert [m.text for m in cs.load_recent(tmp_path, session)] == [
        "go ahead?",
        "yes",
        "done",
    ]


def test_sync_tail_never_raises(tmp_path):
    # Best-effort: bad input degrades to 0, never breaks the turn.
    assert cs.sync_tail(tmp_path, "slack:C1", None) == 0
    assert cs.sync_tail(tmp_path, "", [{"speaker": "founder", "text": "x"}]) == 0


# -- hardening: bad-ts never-raise + stable-id dedup + no phantom resync -------


def test_record_turn_survives_a_bad_ts(tmp_path):
    # A malformed ts must degrade to "now", never raise into the turn (this runs
    # OUTSIDE record_turn's retry try, so an un-coerced float() would escape).
    n = cs.record_turn(tmp_path, "slack:C1", "founder", "hi", ts="not-a-ts")
    assert n == 1
    got = cs.load_recent(tmp_path, "slack:C1")
    assert [m.text for m in got] == ["hi"]
    assert got[0].ts is None or got[0].ts > 0  # a real "when", not a crash


def test_sync_tail_dedups_by_stable_id_keeps_a_repeated_message(tmp_path):
    # A legitimately REPEATED message (same text, DIFFERENT Slack ts) must not be
    # lost: id-based dedup keeps both, where a pure text set would drop the second.
    session = "slack:C1"
    cs.record_turn(tmp_path, session, "founder", "ping", ts=100.0001, ext_id="100.0001")
    live = [
        {"speaker": "founder", "text": "ping", "ts": "100.0001"},  # already stored (id)
        {"speaker": "founder", "text": "ping", "ts": "100.0002"},  # NEW: same text, new id
    ]
    assert cs.sync_tail(tmp_path, session, live) == 1
    assert [m.text for m in cs.load_recent(tmp_path, session)] == ["ping", "ping"]


def test_sync_tail_consumes_each_legacy_text_match_once(tmp_path):
    """One id-less legacy row may explain one live row, never every later repeat."""
    session = "slack:C1"
    cs.record_turn(tmp_path, session, "founder", "yes")  # legacy: no ext_id
    live = [
        {"speaker": "founder", "text": "yes", "ts": "100.0001"},
        {"speaker": "founder", "text": "yes", "ts": "100.0002"},
    ]

    assert cs.sync_tail(tmp_path, session, live) == 1
    identities = cs._recent_identities(tmp_path, session, limit=10)
    assert identities.count(("founder", "yes", "")) == 1
    assert ("founder", "yes", "100.0002") in identities


def test_sync_tail_never_re_appends_the_same_id(tmp_path):
    # The same Slack message (same id) is never appended twice, even across calls.
    session = "slack:C1"
    cs.backfill_once(tmp_path, session, [{"speaker": "founder", "text": "a", "ts": "1.1"}])
    live = [
        {"speaker": "founder", "text": "a", "ts": "1.1"},   # known by id
        {"speaker": "universe", "text": "b", "ts": "1.2"},  # new
    ]
    assert cs.sync_tail(tmp_path, session, live) == 1
    assert cs.sync_tail(tmp_path, session, live) == 0  # b now known by id
    assert [m.text for m in cs.load_recent(tmp_path, session)] == ["a", "b"]


def test_sync_tail_stops_at_a_failed_write_and_retries_the_gap(tmp_path, monkeypatch):
    """A failed A must stop B becoming an anchor that strands A forever."""
    session = "slack:C1"
    cs.record_turn(tmp_path, session, "founder", "anchor", ts=1.0, ext_id="1.0")
    live = [
        {"speaker": "founder", "text": "anchor", "ts": "1.0"},
        {"speaker": "founder", "text": "gap A", "ts": "2.0"},
        {"speaker": "universe", "text": "tail B", "ts": "3.0"},
    ]
    real_record = cs.record_turn

    def fail_a(*args, **kwargs):
        if args[3] == "gap A":
            return 0
        return real_record(*args, **kwargs)

    monkeypatch.setattr(cs, "record_turn", fail_a)
    assert cs.sync_tail(tmp_path, session, live) == 0
    assert [m.text for m in cs.load_recent(tmp_path, session)] == ["anchor"]

    monkeypatch.setattr(cs, "record_turn", real_record)
    assert cs.sync_tail(tmp_path, session, live) == 2
    assert [m.text for m in cs.load_recent(tmp_path, session)] == [
        "anchor",
        "gap A",
        "tail B",
    ]


def test_coerce_ts_survives_overflow_and_junk():
    # A poisoned ts must NEVER raise into the turn. float(10**10000) raises
    # OverflowError (not TypeError/ValueError), and NaN/inf must be rejected so a
    # bad ts degrades to "now" rather than storing junk (Codex FIX1 2026-08-10).
    assert cs._coerce_ts(10**10000) is None       # OverflowError swallowed
    assert cs._coerce_ts("nan") is None           # NaN rejected (not > 0)
    assert cs._coerce_ts("inf") is None           # +inf rejected (would sort above all)
    assert cs._coerce_ts("-inf") is None          # -inf rejected (not > 0)
    assert cs._coerce_ts(float("inf")) is None    # non-string inf too
    assert cs._coerce_ts(object()) is None        # TypeError still handled
    assert cs._coerce_ts("1786.5") == 1786.5      # a real Slack ts still parses


def test_an_infinite_ts_is_never_stored_as_infinity(tmp_path):
    """An 'inf' ts must never be persisted: now that load_recent orders by ts, an
    infinite ts would sort above every real turn and evict valid history from the
    bounded window. _coerce_ts rejects it, so _when falls back to a finite "now"
    (Codex 2026-08-10).

    Mutation-check: drop `math.isfinite` from _coerce_ts and the stored ts is inf
    — this goes red.
    """
    import math as _math
    import sqlite3 as _sqlite3

    session = "slack:C1"
    cs.record_turn(tmp_path, session, "founder", "poison", ts=float("inf"), ext_id="inf")
    db = tmp_path / cs._DB_NAME
    conn = _sqlite3.connect(str(db))
    try:
        stored_ts = conn.execute(
            "SELECT ts FROM conversation_turns WHERE content = 'poison'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert _math.isfinite(stored_ts), f"an inf ts must degrade to finite, got {stored_ts}"


def test_record_turn_is_idempotent_on_ext_id(tmp_path):
    # The same stable id is stored exactly once, no matter how many times a
    # re-sync offers it — the DB-level guarantee behind FIX2/NEW1 (2026-08-10).
    session = "slack:C1"
    first = cs.record_turn(tmp_path, session, "universe", "reply", ts=5.5, ext_id="5.5")
    again = cs.record_turn(tmp_path, session, "universe", "reply", ts=5.5, ext_id="5.5")
    assert first == 1
    assert again == 0  # already stored — quiet no-op, not a new turn
    assert [m.text for m in cs.load_recent(tmp_path, session)] == ["reply"]


def test_connect_tolerates_duplicate_legacy_ext_ids(tmp_path):
    """A dirty pre-index DB must remain usable even when index creation fails."""
    db_path = tmp_path / cs._DB_NAME
    conn = cs._connect(db_path)
    conn.execute("DROP INDEX ix_turns_extid")
    conn.executemany(
        "INSERT INTO conversation_turns "
        "(session_id, turn_no, speaker, content, ts, ext_id) "
        "VALUES (?, ?, 'founder', 'legacy', 1.0, 'same-ts')",
        [("slack:C1", 1), ("slack:C1", 2)],
    )
    conn.commit()
    conn.close()

    reopened = cs._connect(db_path)
    try:
        assert reopened.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0] == 2
        indexes = {
            row[0]
            for row in reopened.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert "ix_turns_extid" not in indexes
    finally:
        reopened.close()


def test_id_less_turns_are_not_deduped_by_id(tmp_path):
    # Legacy/non-Slack callers may still have genuinely distinct id-less turns;
    # ext_id idempotency applies only to id-bearing turns.
    session = "slack:C1"
    cs.record_turn(tmp_path, session, "founder", "same words")
    cs.record_turn(tmp_path, session, "founder", "same words")
    assert len(cs.load_recent(tmp_path, session)) == 2


def test_concurrent_sync_stores_a_reply_once(tmp_path):
    # Two threads reconciling the same tail at once must not both persist the
    # reply: the in-transaction ext_id check (serialized by BEGIN IMMEDIATE)
    # closes the read-before-write window (Codex NEW1 2026-08-10).
    session = "slack:C1"
    cs.backfill_once(tmp_path, session, [{"speaker": "founder", "text": "hi", "ts": "1.0"}])
    live = [
        {"speaker": "founder", "text": "hi", "ts": "1.0"},          # anchor (known)
        {"speaker": "universe", "text": "the reply", "ts": "2.0"},  # the tail
    ]
    errors = []

    def worker():
        try:
            cs.sync_tail(tmp_path, session, live)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"sync_tail raised under concurrency: {errors[:3]}"
    replies = [m.text for m in cs.load_recent(tmp_path, session, limit=100)
               if m.text == "the reply"]
    assert replies == ["the reply"], f"reply stored {len(replies)} times, want 1"


def test_load_recent_renders_a_backfilled_middle_turn_in_ts_order(tmp_path):
    """A middle turn the store missed and later re-synced must render in its real
    CHRONOLOGICAL position, not last. sync_tail gives a back-filled row
    turn_no=max+1 (appended last); ordering by turn_no alone would render a
    stored 1,3 + synced 2 as "1,3,2". load_recent orders by ts (Codex 2026-08-10).

    Mutation-check: revert load_recent's ORDER BY to `turn_no DESC` and the order
    below becomes m1, reply, m2 — this goes red.
    """
    session = "slack:C1"
    # Store has turns 1 and 3 (the middle one, ts=2.0, was missed).
    cs.record_turn(tmp_path, session, "founder", "m1", ts=1.0, ext_id="1.0")
    cs.record_turn(tmp_path, session, "universe", "reply", ts=3.0, ext_id="3.0")
    live = [
        {"speaker": "founder", "text": "m1", "ts": "1.0"},      # known (id)
        {"speaker": "founder", "text": "m2", "ts": "2.0"},      # MISSED middle turn
        {"speaker": "universe", "text": "reply", "ts": "3.0"},  # known (id)
    ]
    assert cs.sync_tail(tmp_path, session, live) == 1  # m2 back-filled
    # Rendered oldest-first by CHRONOLOGY, not by insertion order.
    assert [m.text for m in cs.load_recent(tmp_path, session)] == ["m1", "m2", "reply"]


def test_a_new_message_is_not_swallowed_by_a_stale_legacy_text_match(tmp_path):
    """FIX2 ts-guard (Codex 2026-08-10): a legacy id-less row may only stand in for
    an OLD live message. A genuinely NEW message (ts newer than every stored id)
    must be appended even if its words repeat a legacy row whose original has
    rolled out of the window.

    Mutation-check: remove the `live_ts <= newest_id_ts` guard and the new "yes"
    is consumed by the stale legacy "yes" and dropped — this goes red.
    """
    session = "slack:C1"
    cs.record_turn(tmp_path, session, "founder", "yes")               # legacy id-less
    cs.record_turn(tmp_path, session, "founder", "hello", ts=100.0, ext_id="100.0")
    live = [
        {"speaker": "founder", "text": "hello", "ts": "100.0"},  # known by id (anchor)
        {"speaker": "founder", "text": "yes", "ts": "200.0"},    # NEW, ts > newest id
    ]
    assert cs.sync_tail(tmp_path, session, live) == 1  # the new "yes" is appended
    yeses = [m for m in cs.load_recent(tmp_path, session, limit=50) if m.text == "yes"]
    assert len(yeses) == 2, f"the new distinct 'yes' was dropped: {len(yeses)}"


def test_send_identity_additive_migration_preserves_legacy_history(tmp_path):
    import sqlite3

    assert cs.record_exchange(tmp_path, "principal:owner", "old yes", "old reply")
    db = tmp_path / cs._DB_NAME
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE conversation_turns DROP COLUMN client_send_id")
    # Read-only status/history must work before any writer migrates the store.
    legacy = cs.load_recent_readonly(tmp_path, "principal:owner")
    assert [m.text for m in legacy] == ["old yes", "old reply"]
    assert [m.client_send_id for m in legacy] == ["", ""]
    with sqlite3.connect(db) as conn:
        assert "client_send_id" not in {r[1] for r in conn.execute(
            "PRAGMA table_info(conversation_turns)")}
    assert cs.record_exchange_turns(tmp_path, "principal:owner", "yes", "new reply",
                                    client_send_id="new-send") is not None
    rows = cs.load_recent_readonly(tmp_path, "principal:owner")
    assert [m.text for m in rows] == ["old yes", "old reply", "yes", "new reply"]
    assert [m.client_send_id for m in rows] == ["", "", "new-send", ""]
    assert cs.load_recent_readonly(tmp_path, "principal:other") == []


def test_failure_pair_preserves_send_identity_only_on_founder(tmp_path):
    assert cs.record_failure(tmp_path, "principal:owner", "yes", "unknown",
                             client_send_id="failed-send")
    rows = cs.load_recent_readonly(tmp_path, "principal:owner")
    assert [m.speaker for m in rows] == ["founder", "platform"]
    assert [m.client_send_id for m in rows] == ["failed-send", ""]


def test_unavailable_send_identity_column_keeps_text_and_reports_no_identity(tmp_path, monkeypatch,
                                                                           caplog):
    import sqlite3

    from tests.test_conversation_execution_history import legacy_database

    path = legacy_database(tmp_path)
    monkeypatch.setattr(cs, "_connect", lambda _: sqlite3.connect(path))
    assert cs.record_exchange_turns(tmp_path, "a", "yes", "answer",
                                    client_send_id="new-send") is not None
    rows = cs.load_recent_readonly(tmp_path, "a")
    assert [m.text for m in rows][-2:] == ["yes", "answer"]
    assert [m.client_send_id for m in rows][-2:] == ["", ""]
    assert "saved text without send identity" in caplog.text
