"""Owner control waits a bounded time for its holder; it never steals."""

import threading
import time

import pytest

from tinyassets import singleton_lock
from tinyassets.owner_control import WAIT_ENV, ControlUnavailable, control


@pytest.fixture
def home(tmp_path):
    path = tmp_path / "owner"
    path.mkdir()
    return path


def _refused_once(monkeypatch):
    """An event set once a waiter has been refused by the held lock."""
    refused = threading.Event()
    real = singleton_lock._lock_fd

    def observed(fd):
        ok = real(fd)
        if not ok:
            refused.set()
        return ok

    monkeypatch.setattr(singleton_lock, "_lock_fd", observed)
    return refused


def _contend(home, outcome):
    try:
        with control(home):
            outcome.append("acquired")
    except ControlUnavailable:
        outcome.append("refused")


def test_a_second_caller_waits_and_succeeds_when_the_holder_releases(home, monkeypatch):
    monkeypatch.setenv(WAIT_ENV, "30")
    refused = _refused_once(monkeypatch)
    outcome = []
    with control(home):
        waiter = threading.Thread(target=_contend, args=(home, outcome))
        waiter.start()
        assert refused.wait(10), "the waiter never met the held lock"
        assert outcome == []  # Still waiting, not refused.
    waiter.join(10)
    assert outcome == ["acquired"]


def test_a_holder_past_the_bound_still_wins_and_the_caller_is_refused(home, monkeypatch):
    monkeypatch.setenv(WAIT_ENV, "0.2")
    outcome = []
    with control(home):
        started = time.monotonic()
        waiter = threading.Thread(target=_contend, args=(home, outcome))
        waiter.start()
        waiter.join(10)
        assert outcome == ["refused"]
        assert time.monotonic() - started >= 0.2
        # Not stolen: the holder's lock still excludes a fresh caller.
        monkeypatch.setenv(WAIT_ENV, "0")
        waiter = threading.Thread(target=_contend, args=(home, outcome))
        waiter.start()
        waiter.join(10)
    assert outcome == ["refused", "refused"]


def test_nested_reentry_on_one_thread_does_not_wait(home, monkeypatch):
    monkeypatch.setenv(WAIT_ENV, "30")
    started = time.monotonic()
    with control(home):
        with control(home):
            pass
        # The inner exit did not release the outer hold.
        monkeypatch.setenv(WAIT_ENV, "0")
        outcome = []
        waiter = threading.Thread(target=_contend, args=(home, outcome))
        waiter.start()
        waiter.join(10)
        assert outcome == ["refused"]
    assert time.monotonic() - started < 5
