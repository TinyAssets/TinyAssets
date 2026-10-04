"""Dormant-account duty under the engagement-decayed cadence (design D7 acceptance).

Duty is the fraction of the day a command center's box is awake for proactive
wakes: fires per day x awake seconds per fire / 86400. The awake time per fire
is calibrated to the design's "~1.1% today" figure for an undecayed 4/day
cadence (0.011 x 86400 / 4 = 237.6 s), so the measured number is comparable
with it. The fires are counted by the real owner tick, on a simulated clock,
for an owner who interacted once and then went away for 90 days.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tinyassets.control_plane import cadence
from tinyassets.control_plane.lease import SingleProcessLease, set_owner_lease
from tinyassets.control_plane.scheduler import (
    ControlPlaneScheduler,
    ensure_proactive_trigger,
    note_owner_engagement,
)
from tinyassets.control_plane.triggers import KIND_PROACTIVE
from tinyassets.control_plane.wake import (
    WakeResult,
    register_wake_handler,
    unregister_wake_handler,
)

UTC = timezone.utc
START = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
AWAKE_S_PER_FIRE = 0.011 * 86400 / 4


def _simulate(base: Path, days: int, *, policy_env: str | None, monkeypatch) -> list[datetime]:
    if policy_env is None:
        monkeypatch.delenv(cadence.POLICY_ENV, raising=False)
    else:
        monkeypatch.setenv(cadence.POLICY_ENV, policy_env)
    fired: list[datetime] = []
    register_wake_handler(
        KIND_PROACTIVE,
        lambda _b, req: (fired.append(datetime.fromisoformat(req.due_at.replace("Z", "+00:00")))
                         or WakeResult(run_id=f"run-{len(fired)}")),
        replace=True,
    )
    previous = set_owner_lease(SingleProcessLease())
    try:
        ensure_proactive_trigger(base, command_center_id="cc-duty", owner_principal_id="u:o",
                                 now=START)
        note_owner_engagement(base, command_center_id="cc-duty", principal_id="u:o", at=START)
        sched = ControlPlaneScheduler(base, live=lambda _b, _r: False,
                                      zone_for=lambda _b, _o: ZoneInfo("UTC"))
        moment = START
        while moment < START + timedelta(days=days):
            sched.tick(moment)
            moment += timedelta(hours=2)
    finally:
        set_owner_lease(previous)
        unregister_wake_handler(KIND_PROACTIVE)
    return fired


def _per_day(fired: list[datetime], first_day: int, last_day: int) -> float:
    lo, hi = START + timedelta(days=first_day), START + timedelta(days=last_day)
    return sum(lo <= f < hi for f in fired) / (last_day - first_day)


def _duty(per_day: float) -> float:
    return per_day * AWAKE_S_PER_FIRE / 86400


def test_dormant_duty_decays_from_about_one_percent_to_under_a_tenth(tmp_path, monkeypatch):
    fired = _simulate(tmp_path, 90, policy_env=None, monkeypatch=monkeypatch)
    engaged = _per_day(fired, 0, 7)
    cooling = _per_day(fired, 8, 30)
    dormant = _per_day(fired, 31, 90)
    assert engaged == pytest.approx(4, abs=0.15)
    assert cooling == pytest.approx(1, abs=0.05)
    assert dormant == pytest.approx(1 / 7, abs=0.02)
    assert _duty(engaged) == pytest.approx(0.011, rel=0.05)
    # The design's dormant floor is ~0.1%; weekly wakes put it well under that.
    assert _duty(dormant) < 0.001


def test_undecayed_policy_reproduces_todays_duty(tmp_path, monkeypatch):
    flat = '{"cooling_period_s": 14400, "dormant_period_s": 14400}'
    fired = _simulate(tmp_path, 35, policy_env=flat, monkeypatch=monkeypatch)
    assert _duty(_per_day(fired, 31, 35)) == pytest.approx(0.011, rel=0.05)
