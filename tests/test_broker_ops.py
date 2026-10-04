"""The broker's operation records: never sent twice, expiry never reopens an id."""

from __future__ import annotations

import threading

import pytest

from tinyassets.broker.ops import OpIdInvalid, OpStore, ulid_stamp_ms

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(ms: int, tail: str = "0" * 16) -> str:
    head = ""
    for _ in range(10):
        head = _CROCKFORD[ms % 32] + head
        ms //= 32
    return head + tail


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock(1_800_000_000.0)


@pytest.fixture
def store(tmp_path, clock):
    return OpStore(tmp_path / "ops.db", retention_s=3600, max_skew_s=300, clock=clock)


def now_id(clock, tail="0" * 16):
    return ulid(int(clock.now * 1000), tail)


def test_ulid_timestamp_round_trips():
    assert ulid_stamp_ms(ulid(1_800_000_000_123)) == 1_800_000_000_123
    for bad in ("short", "I" * 26, "8" + "0" * 25, 26 * "u"):
        with pytest.raises(OpIdInvalid):
            ulid_stamp_ms(bad)


def test_a_reused_op_id_finds_its_record_and_is_never_new_again(store, clock):
    op = now_id(clock)
    assert store.admit("alice|cc-1", op, "d1").kind == "new"
    store.mark_may_have_sent("alice|cc-1", op)
    again = store.admit("alice|cc-1", op, "d1")
    assert again.kind == "existing" and again.record.sent


def test_a_different_request_under_the_same_id_is_a_mismatch(store, clock):
    op = now_id(clock)
    store.admit("alice|cc-1", op, "d1")
    assert store.admit("alice|cc-1", op, "d2").kind == "mismatch"


def test_namespaces_never_see_each_others_operations(store, clock):
    op = now_id(clock)
    store.admit("alice|cc-1", op, "d1")
    assert store.status("bob|cc-2", op) == "not_found"
    assert store.admit("bob|cc-2", op, "d1").kind == "new"


def test_concurrent_admissions_reserve_exactly_once(store, clock):
    op = now_id(clock)
    results = []
    barrier = threading.Barrier(8)

    def admit():
        barrier.wait()
        results.append(store.admit("alice|cc-1", op, "d1").kind)

    threads = [threading.Thread(target=admit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["existing"] * 7 + ["new"]


def test_sent_is_never_cleared_by_a_terminal_state(store, clock):
    op = now_id(clock)
    store.admit("ns", op, "d")
    store.mark_may_have_sent("ns", op)
    store.finish("ns", op, "cancelled")
    record = store.status("ns", op)
    assert record.state == "cancelled" and record.sent


def test_recovery_turns_unsent_reservations_into_refusals_and_keeps_sent_unknown(store, clock):
    unsent, sent = now_id(clock, "A" * 16), now_id(clock, "B" * 16)
    store.admit("ns", unsent, "d")
    store.admit("ns", sent, "d")
    store.mark_may_have_sent("ns", sent)
    store.recover()
    assert store.status("ns", unsent).state == "refused"
    assert store.status("ns", sent).state == "may_have_sent"


def test_an_old_or_future_id_is_refused(store, clock):
    assert store.admit("ns", ulid(int((clock.now - 7200) * 1000)), "d").kind == "expired"
    assert store.admit("ns", ulid(int((clock.now + 3600) * 1000)), "d").kind == "future"


def test_expiry_never_reopens_an_id_even_when_the_clock_steps_back(store, clock):
    op = now_id(clock)
    store.admit("ns", op, "d")
    store.mark_may_have_sent("ns", op)
    clock.now += 7200  # past retention: the record is deleted
    assert store.admit("ns", now_id(clock), "x").kind == "new"  # advances the cutoff
    assert store.status("ns", op) == "expired"
    clock.now -= 7200  # the clock steps back to when the id was fresh
    assert store.admit("ns", op, "d").kind == "expired"
    assert store.status("ns", op) == "expired"


def test_at_capacity_new_admissions_are_refused_not_evicted(tmp_path, clock):
    store = OpStore(tmp_path / "ops.db", retention_s=3600, capacity=2, clock=clock)
    first = now_id(clock, "A" * 16)
    store.admit("ns", first, "d")
    store.admit("ns", now_id(clock, "B" * 16), "d")
    assert store.admit("ns", now_id(clock, "C" * 16), "d").kind == "full"
    assert store.status("ns", first).state == "reserved"


def test_records_survive_a_new_store_over_the_same_file(tmp_path, clock):
    op = now_id(clock)
    OpStore(tmp_path / "ops.db", clock=clock).admit("ns", op, "d")
    reopened = OpStore(tmp_path / "ops.db", clock=clock)
    assert reopened.admit("ns", op, "d").kind == "existing"


def test_case_spellings_of_one_ulid_are_one_operation(store, clock):
    op = now_id(clock, "ABCDEFGHJKMNPQRS")
    store.admit("ns", op, "d")
    store.mark_may_have_sent("ns", op.lower())
    again = store.admit("ns", op.lower(), "d")
    assert again.kind == "existing" and again.record.sent


def test_an_expired_answer_from_status_is_permanent(store, clock):
    fresh = now_id(clock)
    clock.now += 7200
    assert store.status("ns", fresh) == "expired"
    clock.now -= 7200
    assert store.admit("ns", fresh, "d").kind == "expired"
