"""Supervisor liveness is memoized, with a TTL chosen against the watchdog's threshold.

Measured on the live box 2026-08-28: computing it reads ~59 per-worker liveness files
and was 58% of what remained of a `get_status` request after the storage walk was
memoized. Caching both took a status read from 73 ms to 15 ms — a 4.8x cut in the CPU
each request costs, which is the same thing as 4.8x the requests one core can serve.

Caching a LIVENESS probe is the kind of change that can quietly break monitoring, so the
TTL is justified rather than picked: `daemon-liveness-watchdog.md (archived in git)` alerts on
`stuck_pending_max_age_s < 60`, and the real wedges it exists for measured 312 s, 420 s
and 851 s. A 5-second-old snapshot under-reports an age by at most 5 s, which cannot
flip a 60-second decision.
"""

from __future__ import annotations

import pytest

import tinyassets.api.status as status


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    status.reset_supervisor_liveness_cache()
    yield
    status.reset_supervisor_liveness_cache()


@pytest.fixture
def udir(tmp_path):
    d = tmp_path / "u-test"
    d.mkdir()
    return d


def _count(monkeypatch) -> list[int]:
    calls = [0]
    real = status._compute_supervisor_liveness_uncached

    def counted(u, *, now_ts=None):
        calls[0] += 1
        return real(u, now_ts=now_ts)

    monkeypatch.setattr(status, "_compute_supervisor_liveness_uncached", counted)
    return calls


def test_repeated_reads_compute_it_once(monkeypatch, udir):
    calls = _count(monkeypatch)
    for _ in range(6):
        status._compute_supervisor_liveness(udir)
    assert calls[0] == 1


def test_an_explicit_clock_always_computes_fresh(monkeypatch, udir):
    """A caller pinning `now_ts` wants the snapshot as of THAT instant. Serving one
    computed against a different instant is a wrong answer, not a stale one — so
    this bypasses the cache rather than keying on it."""
    calls = _count(monkeypatch)
    status._compute_supervisor_liveness(udir, now_ts=1000.0)
    status._compute_supervisor_liveness(udir, now_ts=2000.0)
    assert calls[0] == 2


def test_an_explicit_clock_does_not_poison_the_shared_entry(monkeypatch, udir):
    """The bug this shape avoids: a pinned-clock caller storing its answer where an
    ordinary reader would later find it, handing them ages measured from a clock
    they never asked for."""
    status._compute_supervisor_liveness(udir, now_ts=1.0)
    calls = _count(monkeypatch)
    status._compute_supervisor_liveness(udir)
    assert calls[0] == 1, "the pinned-clock call was cached and served to a normal read"


def test_two_universes_do_not_share_a_snapshot(monkeypatch, tmp_path, udir):
    other = tmp_path / "u-other"
    other.mkdir()
    calls = _count(monkeypatch)
    status._compute_supervisor_liveness(udir)
    status._compute_supervisor_liveness(other)
    assert calls[0] == 2, "one universe's liveness was reported for another's"


def test_a_zero_ttl_disables_it(monkeypatch, udir):
    monkeypatch.setenv("TINYASSETS_SUPERVISOR_LIVENESS_TTL_S", "0")
    calls = _count(monkeypatch)
    status._compute_supervisor_liveness(udir)
    status._compute_supervisor_liveness(udir)
    assert calls[0] == 2


def test_it_expires(monkeypatch, udir):
    monkeypatch.setenv("TINYASSETS_SUPERVISOR_LIVENESS_TTL_S", "0.01")
    calls = _count(monkeypatch)
    status._compute_supervisor_liveness(udir)
    import time as _t

    _t.sleep(0.05)
    status._compute_supervisor_liveness(udir)
    assert calls[0] == 2


def test_a_reader_cannot_poison_the_next_readers_snapshot(udir):
    """Mutating a cache HIT — the miss path copies anyway, so a test that mutates
    the first result proves nothing (learned the hard way on the storage memo)."""
    status._compute_supervisor_liveness(udir)
    hit = status._compute_supervisor_liveness(udir)
    hit["queue_state"]["depth"] = 999_999
    after = status._compute_supervisor_liveness(udir)
    assert after["queue_state"]["depth"] != 999_999


def test_the_default_ttl_cannot_flip_the_watchdog_threshold(udir):
    """The number is only defensible relative to what consumes it."""
    assert status._DEFAULT_LIVENESS_TTL_S <= 60 / 4, (
        "the watchdog alerts on stuck_pending_max_age_s < 60; a TTL near that "
        "threshold makes a stale snapshot able to change the verdict"
    )


def test_the_cache_does_not_grow_without_bound(monkeypatch, tmp_path):
    """Keyed by a path, so an unbounded map is a slow leak. The bound (and its LRU
    policy, replacing a clear-all that guaranteed churn at exactly the 1,000-universe
    scale this is for) lives in TTLMemo and is tested there; this asserts the wiring."""
    for i in range(300):
        d = tmp_path / f"u-{i}"
        d.mkdir()
        status._compute_supervisor_liveness(d)
    assert len(status._liveness_memo._entries) <= 256
