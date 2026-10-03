"""Tests for scripts/watchdog.py — consecutive-red threshold, rate limit, recovery.

Uses injection seams (probe_fn, restart_fn) so tests don't shell out
to the real canary or systemctl.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import watchdog as _watchdog_module  # noqa: E402
from watchdog import watchdog_tick  # noqa: E402


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "state.json"


@pytest.fixture
def alarm_path(tmp_path):
    """Per-test alarm-log path. Pair with `alarm_log=alarm_path` on
    every `watchdog_tick()` call to keep test writes out of the
    production-shared `.agents/uptime_alarms.log`. The autouse
    `_isolate_alarm_log` fixture below is a belt-and-suspenders guard
    in case a future test forgets to inject this explicitly.
    """
    return tmp_path / "uptime_alarms.log"


@pytest.fixture(autouse=True)
def _isolate_alarm_log(monkeypatch, tmp_path):
    """Rebind `watchdog.ALARM_LOG` to a per-test tmp path so any
    `watchdog_tick()` call that doesn't pass an explicit `alarm_log=`
    cannot write to the production-shared file. Closes the test-
    pollution class diagnosed in
    docs/audits/2026-04-26-restart-loop-correlation.md.
    """
    monkeypatch.setattr(
        _watchdog_module, "ALARM_LOG", tmp_path / "uptime_alarms.log",
    )


def _green_probe():
    return (True, "green")


def _red_probe(msg="HTTP 502"):
    def fn():
        return (False, msg)
    return fn


class _RestartRecorder:
    def __init__(self, success=True, msg="restarted"):
        self.calls: list[str] = []
        self.success = success
        self.msg = msg

    def __call__(self, unit):
        self.calls.append(unit)
        return (self.success, self.msg)


# ---- basic state transitions ---------------------------------------------


def test_green_keeps_state_zero(state_path):
    state = watchdog_tick(
        state_file=state_path,
        probe_fn=_green_probe,
        restart_fn=_RestartRecorder(),
    )
    assert state["consecutive_reds"] == 0


def test_first_red_increments(state_path):
    recorder = _RestartRecorder()
    state = watchdog_tick(
        state_file=state_path,
        probe_fn=_red_probe(),
        restart_fn=recorder,
    )
    assert state["consecutive_reds"] == 1
    assert recorder.calls == [], "single red should NOT trigger restart"


def test_second_red_increments_no_restart(state_path):
    recorder = _RestartRecorder()
    watchdog_tick(state_file=state_path, probe_fn=_red_probe(), restart_fn=recorder)
    state = watchdog_tick(state_file=state_path, probe_fn=_red_probe(), restart_fn=recorder)
    assert state["consecutive_reds"] == 2
    assert recorder.calls == [], "2 reds (below threshold=3) should NOT restart"


def test_third_red_triggers_restart(state_path):
    recorder = _RestartRecorder()
    watchdog_tick(state_file=state_path, probe_fn=_red_probe(), restart_fn=recorder)
    watchdog_tick(state_file=state_path, probe_fn=_red_probe(), restart_fn=recorder)
    state = watchdog_tick(state_file=state_path, probe_fn=_red_probe(), restart_fn=recorder)
    assert recorder.calls == ["tinyassets-daemon.service"]
    # After successful restart, streak optimistically resets.
    assert state["consecutive_reds"] == 0
    assert state["last_restart_ts"] is not None


def test_green_resets_streak(state_path):
    recorder = _RestartRecorder()
    # Two reds, then green.
    watchdog_tick(state_file=state_path, probe_fn=_red_probe(), restart_fn=recorder)
    watchdog_tick(state_file=state_path, probe_fn=_red_probe(), restart_fn=recorder)
    state = watchdog_tick(state_file=state_path, probe_fn=_green_probe, restart_fn=recorder)
    assert state["consecutive_reds"] == 0
    assert recorder.calls == []


def test_recovery_after_restart(state_path):
    recorder = _RestartRecorder()
    # 3 reds → restart
    for _ in range(3):
        watchdog_tick(state_file=state_path, probe_fn=_red_probe(), restart_fn=recorder)
    assert recorder.calls == ["tinyassets-daemon.service"]
    # Next probe green → streak stays zero, no additional restart.
    state = watchdog_tick(state_file=state_path, probe_fn=_green_probe, restart_fn=recorder)
    assert state["consecutive_reds"] == 0
    assert len(recorder.calls) == 1


# ---- rate limit on restart -----------------------------------------------


def test_rate_limit_blocks_rapid_restarts(state_path):
    """After a restart, next restart is blocked until min-interval passes."""
    recorder = _RestartRecorder()
    for _ in range(3):
        watchdog_tick(state_file=state_path, probe_fn=_red_probe(), restart_fn=recorder)
    assert recorder.calls == ["tinyassets-daemon.service"]

    # State file's last_restart_ts is now. Another round of 3 reds
    # should NOT trigger restart because min_restart_interval (default
    # 600s) hasn't elapsed.
    for _ in range(3):
        watchdog_tick(
            state_file=state_path,
            probe_fn=_red_probe("hung again"),
            restart_fn=recorder,
            min_restart_interval=600.0,
        )
    assert recorder.calls == ["tinyassets-daemon.service"], (
        "rate-limit should block a second restart within the interval"
    )


def test_rate_limit_allows_restart_after_interval(state_path):
    """Setting min_restart_interval to 0 allows back-to-back restarts."""
    recorder = _RestartRecorder()
    for _ in range(3):
        watchdog_tick(state_file=state_path, probe_fn=_red_probe(), restart_fn=recorder)
    assert len(recorder.calls) == 1

    for _ in range(3):
        watchdog_tick(
            state_file=state_path,
            probe_fn=_red_probe("hung again"),
            restart_fn=recorder,
            min_restart_interval=0.0,
        )
    assert len(recorder.calls) == 2


# ---- failure modes --------------------------------------------------------


def test_restart_failure_keeps_streak(state_path):
    """If systemctl restart fails, the streak stays so the next tick retries."""
    recorder = _RestartRecorder(success=False, msg="systemctl not found")
    for _ in range(3):
        state = watchdog_tick(
            state_file=state_path,
            probe_fn=_red_probe(),
            restart_fn=recorder,
        )
    assert recorder.calls == ["tinyassets-daemon.service"]
    # Streak preserved so next tick tries again (don't "forget" the
    # outage just because we couldn't fix it).
    assert state["consecutive_reds"] >= 3


def test_corrupt_state_resets_to_zero(state_path):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("not valid json", encoding="utf-8")
    state = watchdog_tick(
        state_file=state_path,
        probe_fn=_green_probe,
        restart_fn=_RestartRecorder(),
    )
    assert state["consecutive_reds"] == 0


def test_missing_state_file_treated_as_zero(state_path):
    # state_path does not exist.
    state = watchdog_tick(
        state_file=state_path,
        probe_fn=_red_probe(),
        restart_fn=_RestartRecorder(),
    )
    assert state["consecutive_reds"] == 1


def test_state_persists_across_ticks(state_path):
    """Verify the state file actually persists — not just in-memory."""
    recorder = _RestartRecorder()
    watchdog_tick(state_file=state_path, probe_fn=_red_probe(), restart_fn=recorder)
    # Read the file directly — should show consecutive_reds=1.
    disk_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert disk_state["consecutive_reds"] == 1
    assert disk_state["last_probe_ts"] is not None


# ---- threshold override --------------------------------------------------


def test_threshold_1_single_red_restarts(state_path):
    recorder = _RestartRecorder()
    watchdog_tick(
        state_file=state_path,
        probe_fn=_red_probe(),
        restart_fn=recorder,
        threshold=1,
    )
    assert recorder.calls == ["tinyassets-daemon.service"]


def test_threshold_5_needs_5_reds(state_path):
    recorder = _RestartRecorder()
    for _ in range(4):
        watchdog_tick(state_file=state_path, probe_fn=_red_probe(),
                      restart_fn=recorder, threshold=5)
    assert recorder.calls == []
    watchdog_tick(state_file=state_path, probe_fn=_red_probe(),
                  restart_fn=recorder, threshold=5)
    assert recorder.calls == ["tinyassets-daemon.service"]


# ---- GH issue emission -------------------------------------------------------


class _GhRecorder:
    def __init__(self, success=True):
        self.calls: list[tuple[str, str]] = []
        self.success = success

    def __call__(self, title, body):
        self.calls.append((title, body))
        return (self.success, "issue #42: https://github.com/example/issues/42")


def test_gh_issue_fired_on_restart(state_path):
    """Restart should trigger one GH issue emission."""
    recorder = _RestartRecorder()
    gh = _GhRecorder()
    for _ in range(3):
        watchdog_tick(
            state_file=state_path,
            probe_fn=_red_probe(),
            restart_fn=recorder,
            gh_issue_fn=gh,
        )
    assert recorder.calls == ["tinyassets-daemon.service"]
    assert len(gh.calls) == 1
    title, body = gh.calls[0]
    assert "watchdog" in title.lower()
    assert "daemon" in body.lower()


def test_gh_issue_not_fired_below_threshold(state_path):
    """No GH issue until restart threshold is crossed."""
    recorder = _RestartRecorder()
    gh = _GhRecorder()
    for _ in range(2):
        watchdog_tick(
            state_file=state_path,
            probe_fn=_red_probe(),
            restart_fn=recorder,
            gh_issue_fn=gh,
        )
    assert gh.calls == []


# ---- DRY_RUN suppression ---------------------------------------------------


def test_dry_run_suppresses_restart(state_path):
    """dry_run=True must not call restart_fn even at threshold."""
    recorder = _RestartRecorder()
    gh = _GhRecorder()
    for _ in range(3):
        watchdog_tick(
            state_file=state_path,
            probe_fn=_red_probe(),
            restart_fn=recorder,
            gh_issue_fn=gh,
            dry_run=True,
        )
    assert recorder.calls == [], "dry_run must suppress restart"
    assert gh.calls == [], "dry_run must suppress GH issue"


def test_dry_run_env_suppresses_restart(state_path, monkeypatch):
    """DRY_RUN=1 env var is equivalent to dry_run=True."""
    monkeypatch.setenv("DRY_RUN", "1")
    recorder = _RestartRecorder()
    for _ in range(3):
        watchdog_tick(
            state_file=state_path,
            probe_fn=_red_probe(),
            restart_fn=recorder,
        )
    assert recorder.calls == [], "DRY_RUN=1 env must suppress restart"


# ---- alarm log writes -------------------------------------------------------


def test_alarm_log_written_on_restart(state_path, tmp_path):
    """Alarm line must be appended to alarm_log on successful restart."""
    alarm_log = tmp_path / "uptime_alarms.log"
    recorder = _RestartRecorder()
    for _ in range(3):
        watchdog_tick(
            state_file=state_path,
            probe_fn=_red_probe(),
            restart_fn=recorder,
            gh_issue_fn=_GhRecorder(),
            alarm_log=alarm_log,
        )
    assert alarm_log.exists(), "alarm log file must be created"
    content = alarm_log.read_text(encoding="utf-8")
    assert "WATCHDOG_RESTART" in content


def test_alarm_log_not_written_on_dry_run(state_path, tmp_path):
    """dry_run must not write to the alarm log."""
    alarm_log = tmp_path / "uptime_alarms.log"
    recorder = _RestartRecorder()
    for _ in range(3):
        watchdog_tick(
            state_file=state_path,
            probe_fn=_red_probe(),
            restart_fn=recorder,
            alarm_log=alarm_log,
            dry_run=True,
        )
    assert not alarm_log.exists(), "alarm log must not be written in dry_run mode"


def test_alarm_log_not_written_below_threshold(state_path, tmp_path):
    """No alarm until restart threshold is crossed."""
    alarm_log = tmp_path / "uptime_alarms.log"
    recorder = _RestartRecorder()
    for _ in range(2):
        watchdog_tick(
            state_file=state_path,
            probe_fn=_red_probe(),
            restart_fn=recorder,
            alarm_log=alarm_log,
        )
    assert not alarm_log.exists()


def test_gh_issue_skipped_on_restart_failure(state_path):
    """If systemctl restart fails, GH issue is NOT emitted (restart_fn=fail)."""
    recorder = _RestartRecorder(success=False, msg="no sudoers")
    gh = _GhRecorder()
    for _ in range(3):
        watchdog_tick(
            state_file=state_path,
            probe_fn=_red_probe(),
            restart_fn=recorder,
            gh_issue_fn=gh,
        )
    assert recorder.calls == ["tinyassets-daemon.service"]
    assert gh.calls == [], "GH issue should not fire when restart failed"


def test_gh_issue_failure_does_not_crash_watchdog(state_path):
    """A GH API failure must not propagate — watchdog tick still returns state."""
    recorder = _RestartRecorder()
    gh = _GhRecorder(success=False)
    for _ in range(3):
        state = watchdog_tick(
            state_file=state_path,
            probe_fn=_red_probe(),
            restart_fn=recorder,
            gh_issue_fn=gh,
        )
    # Watchdog should still complete normally.
    assert state is not None
    assert len(gh.calls) == 1


# ---- standing down while a deploy holds the host-mutation lock ------------
#
# 2026-10-01: a deploy's own recreate read as three reds, this watchdog ran
# `systemctl restart`, and the unit's second compose run killed the new
# container and failed the rollback
# (docs/audits/2026-10-01-deploy-drain-repro/INCIDENT.md).


def _lock_held():
    import contextlib

    @contextlib.contextmanager
    def held():
        yield False

    return held


def test_a_held_host_mutation_lock_suppresses_probe_and_restart(state_path, alarm_path):
    recorder = _RestartRecorder()
    probes: list[int] = []

    def probe():
        probes.append(1)
        return (False, "connection refused")

    for _ in range(5):
        state = watchdog_tick(
            state_file=state_path, probe_fn=probe, restart_fn=recorder,
            alarm_log=alarm_path, host_mutation_lock=_lock_held(),
        )
    assert recorder.calls == []
    assert probes == [], "a deploy in progress must not even be probed"
    assert state["consecutive_reds"] == 0


def test_reds_counted_before_a_deploy_do_not_carry_across_it(state_path, alarm_path):
    """Two reds, then the deploy's lock, then one red: no restart.

    Without the reset, the first red after the deploy releases the lock would be
    red #3 and restart the daemon the deploy just brought up.
    """
    recorder = _RestartRecorder()
    for _ in range(2):
        watchdog_tick(state_file=state_path, probe_fn=_red_probe(),
                      restart_fn=recorder, alarm_log=alarm_path)
    watchdog_tick(state_file=state_path, probe_fn=_red_probe(), restart_fn=recorder,
                  alarm_log=alarm_path, host_mutation_lock=_lock_held())
    state = watchdog_tick(state_file=state_path, probe_fn=_red_probe(),
                          restart_fn=recorder, alarm_log=alarm_path)
    assert recorder.calls == []
    assert state["consecutive_reds"] == 1


def test_an_absent_lock_file_does_not_block_and_is_not_created(tmp_path):
    lock = tmp_path / "host-mutation.lock"
    with _watchdog_module._host_mutation_lock(lock) as free:
        assert free is True
    assert not lock.exists(), (
        "the watchdog must never create the deploy's lock file: under "
        "fs.protected_regular=2 root's `exec 9>` on a file this user owns fails")


@pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX-only")
def test_the_real_lock_reports_held_while_another_holder_has_it(tmp_path):
    import fcntl
    import os

    lock = tmp_path / "host-mutation.lock"
    lock.write_text("", encoding="utf-8")
    holder = os.open(lock, os.O_RDONLY)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with _watchdog_module._host_mutation_lock(lock) as free:
            assert free is False
    finally:
        os.close(holder)
    with _watchdog_module._host_mutation_lock(lock) as free:
        assert free is True


def _unit_timeout_s(name: str) -> int:
    import re

    text = (_SCRIPTS.parent / "deploy" / name).read_text(encoding="utf-8")
    match = re.search(r"^TimeoutStartSec=(\d+)s?$", text, re.M)
    assert match, f"{name} TimeoutStartSec is no longer plain seconds"
    return int(match.group(1))


def test_the_lock_outlives_the_restart_job_it_covers():
    """systemctl waits for the restart JOB, which the daemon unit's
    TimeoutStartSec bounds. If watchdog.py or its own unit gave up first, the
    lock would be released while the job's compose run was still mutating, and a
    deploy could start on top of it (Codex refute, 2026-10-01)."""
    # A failed start is followed by systemd's stop, so the job ends after
    # start + stop, not start alone (Codex refute round 2).
    unit = (_SCRIPTS.parent / "deploy" / "tinyassets-daemon.service").read_text(
        encoding="utf-8")
    import re

    stop = re.search(r"^TimeoutStopSec=(\d+)s?$", unit, re.M)
    assert stop, "the daemon unit must pin TimeoutStopSec; the lock lifetime depends on it"
    daemon_job = _unit_timeout_s("tinyassets-daemon.service") + int(stop.group(1))
    assert _watchdog_module.RESTART_JOB_TIMEOUT_SECONDS > daemon_job
    assert _unit_timeout_s("tinyassets-watchdog.service") > (
        _watchdog_module.RESTART_JOB_TIMEOUT_SECONDS)
    assert _unit_timeout_s("daemon-watchdog.service") > daemon_job + 20  # restart -t 20


def test_the_watchdog_unit_creates_the_lock_as_root():
    """The script runs as tinyassets and must never create the lock (under
    protected_regular=2 that would lock root out of it). The unit's root
    ExecStartPre makes sure it exists, so the watchdog is never lockless."""
    text = (_SCRIPTS.parent / "deploy" / "tinyassets-watchdog.service").read_text(
        encoding="utf-8")
    assert "ExecStartPre=+/usr/bin/touch /var/lock/tinyassets-host-mutation.lock" in text
