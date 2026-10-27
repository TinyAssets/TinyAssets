"""The deploy readiness gate waits through post-drain work, with a hard cap."""

import pytest

from scripts.wait_boxhostd_ready import (
    PUBLISH_TRANSFERS,
    SNAPSHOT_TRANSFER_S,
    WAIT_TIMEOUT_S,
    wait_ready,
)

pytestmark = pytest.mark.verifies("owner-daemon-in-box:D11")


def test_cap_outlasts_a_snapshot_upload_and_readback():
    assert PUBLISH_TRANSFERS == 2
    assert WAIT_TIMEOUT_S >= PUBLISH_TRANSFERS * SNAPSHOT_TRANSFER_S


def test_only_the_exact_ready_document_counts():
    """Host and broker success is {"ready": True} and nothing else."""
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    assert not wait_ready(
        lambda operation: {"ready": True, "background_active": 0},
        broker_probe=lambda operation: {"ready": True},
        timeout=2, interval=1, clock=lambda: clock[0], sleep=sleep)
    logged = []
    named = {"ready": False, "tasks": 0, "pending": {"snapshot": 1},
             "faults": {"lifecycle_error:tick_owner:RuntimeError": 1}}
    assert not wait_ready(
        lambda operation: {"ready": True}, broker_probe=lambda operation: named,
        timeout=2, interval=1, clock=lambda: clock[0], sleep=sleep, log=logged.append)
    # The deploy log carries the broker's named fault verbatim.
    assert logged == [{"host": {"ready": True}, "broker": named}]


def test_waits_for_two_idle_signals_after_busy_lifecycle():
    state = iter([False, False, True, True])
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    assert wait_ready(lambda operation: {"ready": next(state)},
                      broker_probe=lambda operation: {"ready": True}, timeout=10,
                      interval=1, clock=lambda: clock[0], sleep=sleep)
    assert clock[0] == 3


def test_readiness_wait_times_out_while_lifecycle_stays_busy():
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    assert not wait_ready(lambda operation: {"ready": False},
                          broker_probe=lambda operation: {"ready": True}, timeout=3,
                          interval=1, clock=lambda: clock[0], sleep=sleep)
    assert clock[0] == 3


def test_pending_broker_work_prevents_ready_and_eof_is_retried():
    clock = [0.0]
    broker = iter([{"ready": False}, EOFError(), {"ready": True}, {"ready": True}])

    def broker_probe(operation):
        result = next(broker)
        if isinstance(result, Exception):
            raise result
        return result

    assert wait_ready(lambda operation: {"ready": True}, broker_probe=broker_probe,
                      timeout=10, interval=1, clock=lambda: clock[0],
                      sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert clock[0] == 3
