"""Unread receipts over the real conversation store; no network or model calls."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from tinyassets import conversation_store as store
from tinyassets.conversation_attention import (
    UNREAD_EPOCH,
    acknowledge,
    returned_page,
    unread_count,
)

SESSION = "principal:founder"


def chunk(ident, text, offset=0, count=32768):
    value = text[offset:offset + count]
    end = offset + len(value)
    return dict(available=True, field_name=str(ident), offset=offset,
                total_chars=len(text), chunk=value, offset_unit="unicode_code_points",
                next_offset=end if end < len(text) else None)


def say(root, text, session=SESSION, ts=None):
    """The owner's message plus the universe's reply; returns the owner row id."""
    assert store.record_exchange(root, session, text, "reply", ts=ts)
    with closing(sqlite3.connect(root / ".conversation_memory.db")) as conn:
        return conn.execute(
            "SELECT max(id) FROM conversation_turns WHERE session_id=? AND speaker='founder'",
            (session,),
        ).fetchone()[0]


def unread(root, session=SESSION):
    return unread_count(root, session)


def test_only_the_owners_own_messages_count(tmp_path: Path):
    assert unread(tmp_path) == 0  # no conversation yet: nothing was sent
    say(tmp_path, "one")
    assert unread(tmp_path) == 1  # the universe's reply is not a message to it
    say(tmp_path, "theirs", session="principal:other")
    assert unread(tmp_path) == 1
    assert unread(tmp_path, "principal:other") == 1


def test_history_from_before_the_counter_is_not_news(tmp_path: Path):
    say(tmp_path, "last month", ts=UNREAD_EPOCH - 1)
    assert unread(tmp_path) == 0
    say(tmp_path, "today")
    assert unread(tmp_path) == 1


def test_new_arrival_during_read_remains_unread(tmp_path: Path):
    first = say(tmp_path, "earlier")
    page = chunk(first, "earlier")
    say(tmp_path, "arrived while reading")
    acknowledge(tmp_path, SESSION, page)
    assert unread(tmp_path) == 1


def test_catalog_is_not_a_read(tmp_path: Path):
    ident = say(tmp_path, "body")
    acknowledge(tmp_path, SESSION, {"available": True, "messages": [{"id": ident}]})
    assert unread(tmp_path) == 1


def test_partial_unicode_gap_and_duplicate_chunks(tmp_path: Path):
    text = "a🌱b\x00cdef"
    ident = say(tmp_path, text)
    for offset, count in [(4, 4), (0, 2), (0, 2)]:
        acknowledge(tmp_path, SESSION, chunk(ident, text, offset, count))
        assert unread(tmp_path) == 1
    acknowledge(tmp_path, SESSION, chunk(ident, text, 2, 2))
    assert unread(tmp_path) == 0


def test_reading_the_newer_message_leaves_the_older_unread(tmp_path: Path):
    say(tmp_path, "one")
    second = say(tmp_path, "two")
    acknowledge(tmp_path, SESSION, chunk(second, "two"))
    assert unread(tmp_path) == 1


def test_another_threads_message_cannot_be_acknowledged(tmp_path: Path):
    say(tmp_path, "mine")
    theirs = say(tmp_path, "theirs", session="principal:other")
    acknowledge(tmp_path, SESSION, chunk(theirs, "theirs"))
    assert unread(tmp_path) == 1
    assert unread(tmp_path, "principal:other") == 1
    assert not (tmp_path / ".conversation_attention.db").exists()


def test_deleted_history_not_in_count(tmp_path: Path):
    say(tmp_path, "gone")
    with closing(sqlite3.connect(tmp_path / ".conversation_memory.db")) as conn:
        conn.execute("DELETE FROM conversation_turns")
        conn.commit()
    assert unread(tmp_path) == 0


def test_symlink_store_refused(tmp_path: Path):
    say(tmp_path, "x")
    target = tmp_path / "outside.db"
    (tmp_path / ".conversation_memory.db").rename(target)
    try:
        (tmp_path / ".conversation_memory.db").symlink_to(target)
    except OSError:
        pytest.skip("this host cannot create symlinks; CI exercises the guard")
    with pytest.raises(PermissionError):
        unread(tmp_path)


def test_invalid_and_truncated_results_do_not_ack(tmp_path: Path):
    ident = say(tmp_path, "body")
    page = chunk(ident, "body")
    for change in [{"truncated": True}, {"available": False}, {"error": "failed"},
                   {"offset": -1}, {"next_offset": 5}, {"field_name": "99"}]:
        acknowledge(tmp_path, SESSION, dict(page, **change))
        assert unread(tmp_path) == 1
    bounded = {"truncated": True, "content": json.dumps(page)}
    acknowledge(tmp_path, SESSION, returned_page(json.dumps(bounded)))
    assert unread(tmp_path) == 1


def test_exact_untrusted_envelope(tmp_path: Path):
    ident = say(tmp_path, "body")
    envelope = {"untrusted": True, "source": "conversation", "content": chunk(ident, "body")}
    acknowledge(tmp_path, SESSION, returned_page(json.dumps(envelope)))
    assert unread(tmp_path) == 0
    envelope["source"] = "commons:another"
    assert returned_page(json.dumps(envelope)) is None


def test_parallel_chunks_merge_without_lost_update(tmp_path: Path):
    ident = say(tmp_path, "abcdefgh")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda page: acknowledge(tmp_path, SESSION, page),
                      [chunk(ident, "abcdefgh", 0, 4), chunk(ident, "abcdefgh", 4, 4)]))
    assert unread(tmp_path) == 0


def test_a_call_that_reads_no_message_writes_nothing(tmp_path: Path):
    say(tmp_path, "one")
    for _ in range(3):
        acknowledge(tmp_path, SESSION, None)
        assert unread(tmp_path) == 1
    assert not (tmp_path / ".conversation_attention.db").exists()


def _age(root: Path, seconds: float) -> None:
    """Backdate every store file, as if the last write were ``seconds`` ago."""
    import os
    import time

    stamp = time.time_ns() - int(seconds * 1e9)
    for path in root.glob(".conversation_*.db*"):
        os.utime(path, ns=(stamp, stamp))


def test_a_read_in_the_same_mtime_tick_is_not_served_from_cache(tmp_path: Path):
    """Two receipt writes in one kernel timestamp tick at the same size used to
    keep the earlier count: the badge stayed at 1 after the message was read."""
    import os

    text = "a🌱b\x00cdef"
    ident = say(tmp_path, text)
    acknowledge(tmp_path, SESSION, chunk(ident, text, 0, 4))
    assert unread(tmp_path) == 1
    receipts = tmp_path / ".conversation_attention.db"
    before = os.stat(receipts)
    acknowledge(tmp_path, SESSION, chunk(ident, text, 4, 4))
    after = os.stat(receipts)
    assert after.st_size == before.st_size
    # The completing write landed in the same tick: its mtime did not move.
    os.utime(receipts, ns=(after.st_atime_ns, before.st_mtime_ns))
    assert unread(tmp_path) == 0


def test_a_settled_store_is_still_answered_from_cache(tmp_path: Path, monkeypatch):
    from tinyassets import conversation_attention as attention

    say(tmp_path, "settled")
    unread(tmp_path)  # the first read creates the transcript's empty -wal
    _age(tmp_path, 10)
    calls = []
    real = attention._owner_ids
    monkeypatch.setattr(
        attention, "_owner_ids", lambda *a, **k: calls.append(1) or real(*a, **k),
    )
    assert unread(tmp_path) == 1
    assert unread(tmp_path) == 1
    assert len(calls) == 1  # the second call cost only the stat calls
