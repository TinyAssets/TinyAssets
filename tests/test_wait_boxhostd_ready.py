"""The deploy readiness gate waits through post-drain work, with a hard cap."""

from scripts.wait_boxhostd_ready import wait_ready


def test_waits_for_two_idle_signals_after_busy_lifecycle():
    state = iter([False, False, True, True])
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    assert wait_ready(lambda operation: {"ready": next(state)}, timeout=10,
                      interval=1, clock=lambda: clock[0], sleep=sleep)
    assert clock[0] == 3


def test_readiness_wait_times_out_while_lifecycle_stays_busy():
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    assert not wait_ready(lambda operation: {"ready": False}, timeout=3,
                          interval=1, clock=lambda: clock[0], sleep=sleep)
    assert clock[0] == 3
