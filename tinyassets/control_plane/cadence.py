"""Engagement-decayed proactive cadence: a policy, not code paths (design D7).

A command center's proactive wake (harness §4.5, idle research) runs often
while its owner is engaged and decays while they are away:

* **engaged** -- the owner interacted within ``cooling_after_s`` (7 days):
  every ``engaged_period_s`` (4 h) inside active hours (08:00-22:00 in the
  owner's clock), i.e. 4 a day;
* **cooling** -- no interaction for 7 days: every ``cooling_period_s`` (1/day);
* **dormant** -- no interaction for ``dormant_after_s`` (30 days): every
  ``dormant_period_s`` (weekly).

The next owner interaction resets the state to engaged. A wake also waits
``idle_s`` (30 min) after the last interaction, so it never interrupts the
owner.

**The founder has not confirmed the decay values.** They live in exactly one
place, :data:`DEFAULT_POLICY`, overridable for the whole deploy by
``TINYASSETS_PROACTIVE_CADENCE`` (a JSON object of field overrides) and per
command center by its owner (``triggers.set_cadence_override``). Setting the
three periods equal turns decay off without a code change.

Everything here is pure: the due instant is a function of the trigger row and
``now``, so two evaluations either side of a restart derive the same
``due_at`` and the fire fence holds.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

ENGAGED = "engaged"
COOLING = "cooling"
DORMANT = "dormant"
DECAY_STATES = (ENGAGED, COOLING, DORMANT)

POLICY_ENV = "TINYASSETS_PROACTIVE_CADENCE"


@dataclass(frozen=True)
class CadencePolicy:
    engaged_period_s: int = 4 * 3600
    cooling_after_s: int = 7 * 86400
    cooling_period_s: int = 86400
    dormant_after_s: int = 30 * 86400
    dormant_period_s: int = 7 * 86400
    idle_s: int = 30 * 60
    active_start: str = "08:00"
    active_end: str = "22:00"

    def validated(self) -> "CadencePolicy":
        for name in ("engaged_period_s", "cooling_period_s", "dormant_period_s"):
            if int(getattr(self, name)) < 300:
                raise ValueError(f"{name} must be at least 300 seconds")
        if int(self.idle_s) < 0:
            raise ValueError("idle_s must not be negative")
        if not 0 < int(self.cooling_after_s) <= int(self.dormant_after_s):
            raise ValueError("cooling_after_s must be positive and <= dormant_after_s")
        _clock(self.active_start)
        _clock(self.active_end)
        return self

    def period_for(self, state: str) -> int:
        return {
            ENGAGED: self.engaged_period_s,
            COOLING: self.cooling_period_s,
            DORMANT: self.dormant_period_s,
        }[state]


#: The single config default (founder decision pending on the decay values).
DEFAULT_POLICY = CadencePolicy()

_INT_FIELDS = frozenset(
    f.name for f in fields(CadencePolicy) if f.name not in {"active_start", "active_end"}
)


def _clock(value: str) -> time:
    try:
        hour, minute = str(value).split(":")
        return time(int(hour), int(minute))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"active hours must be HH:MM, got {value!r}") from exc


def policy_with(base: CadencePolicy, overrides: dict[str, Any] | None) -> CadencePolicy:
    """``base`` with ``overrides`` applied; unknown or invalid fields raise."""
    if not overrides:
        return base
    if not isinstance(overrides, dict):
        raise ValueError("cadence overrides must be an object")
    known = {f.name for f in fields(CadencePolicy)}
    unknown = set(overrides) - known
    if unknown:
        raise ValueError(f"unknown cadence fields: {sorted(unknown)}")
    coerced: dict[str, Any] = {}
    for key, value in overrides.items():
        if key in _INT_FIELDS:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a number of seconds")
            if not math.isfinite(float(value)):
                raise ValueError(f"{key} must be finite")
            coerced[key] = int(value)
        else:
            coerced[key] = str(value)
    return replace(base, **coerced).validated()


def deploy_policy() -> CadencePolicy:
    """The deploy-wide policy: the default plus ``TINYASSETS_PROACTIVE_CADENCE``.

    A malformed value raises: a typo in the one cadence knob must not silently
    run every command center on a cadence nobody chose (Hard Rule 8).
    """
    raw = os.environ.get(POLICY_ENV, "").strip()
    if not raw:
        return DEFAULT_POLICY
    try:
        overrides = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{POLICY_ENV} is not JSON") from exc
    return policy_with(DEFAULT_POLICY, overrides)


def policy_dict(policy: CadencePolicy) -> dict[str, Any]:
    return asdict(policy)


def decay_state(policy: CadencePolicy, *, engaged_at: datetime, now: datetime) -> str:
    away = (now - engaged_at).total_seconds()
    if away < policy.cooling_after_s:
        return ENGAGED
    if away < policy.dormant_after_s:
        return COOLING
    return DORMANT


def _into_active_hours(moment: datetime, policy: CadencePolicy, zone: ZoneInfo) -> datetime:
    """Find the next real opening, preserving repeated windows and gap ends."""
    start, end = _clock(policy.active_start), _clock(policy.active_end)
    if start == end:  # a 24-hour window
        return moment
    local = moment.astimezone(zone)
    clock = local.time().replace(tzinfo=None)
    inside = (start <= clock < end) if start < end else (clock >= start or clock < end)
    if inside:
        return moment
    day = local.date()
    # Even after today's first closing, its second opening may still be ahead.
    while True:
        wall = datetime.combine(day, start)
        openings = []
        projections = []
        for fold in (0, 1):
            opening = wall.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
            projections.append(opening)
            back = opening.astimezone(zone)
            if back.replace(tzinfo=None) == wall and back.fold == fold:
                openings.append(opening)
        if not openings:
            # Imaginary fold projections bracket the gap. Find its first real
            # second, rather than treating either imaginary wall time as owed.
            low, high = min(projections), max(projections)
            while (high - low).total_seconds() > 1:
                middle = low + timedelta(seconds=int((high - low).total_seconds()) // 2)
                if middle.astimezone(zone).replace(tzinfo=None) >= wall:
                    high = middle
                else:
                    low = middle
            if high.astimezone(zone).date() == day:
                openings.append(high)
        upcoming = [opening for opening in openings if opening >= moment.astimezone(timezone.utc)]
        if upcoming:
            return min(upcoming)
        day += timedelta(days=1)


def due_instant(
    policy: CadencePolicy,
    *,
    zone: ZoneInfo,
    created_at: datetime,
    engaged_at: datetime | None,
    last_due_at: datetime | None,
    now: datetime,
) -> tuple[datetime, str, int]:
    """``(due_at, decay_state, collapsed)`` for a proactive trigger at ``now``.

    ``due_at`` may be in the future (not yet owed). When several periods were
    missed -- the owner was down, or a run outlived its window -- the due
    instant collapses onto the LATEST missed grid point, and ``collapsed``
    counts the windows folded into it: one pending fire, never a backlog.
    """
    anchor = engaged_at or created_at
    state = decay_state(policy, engaged_at=anchor, now=now)
    step = timedelta(seconds=policy.period_for(state))
    first = created_at if last_due_at is None else last_due_at + step
    due = _into_active_hours(first, policy, zone)
    collapsed = 0
    # Walk the active-hours grid -- for the first fire too, or a trigger first
    # served late would fire twice in a row -- so a night outside active hours
    # is not counted as missed windows. Bounded: a year at the shortest allowed
    # period is ~105k cheap steps, once.
    while True:
        following = _into_active_hours(due + step, policy, zone)
        if following <= due or following > now:
            break
        due = following
        collapsed += 1
    due = max(due, anchor + timedelta(seconds=policy.idle_s))
    due = _into_active_hours(due, policy, zone).replace(microsecond=0)
    return due, state, collapsed


def in_active_hours(moment: datetime, policy: CadencePolicy, zone: ZoneInfo) -> bool:
    """Whether ``moment`` is inside the owner's active hours. A fire owed from
    earlier still waits for this: active hours bound when a wake RUNS, not only
    the timestamp it was owed at."""
    return _into_active_hours(moment, policy, zone) == moment


__all__ = [
    "COOLING",
    "DECAY_STATES",
    "DEFAULT_POLICY",
    "DORMANT",
    "ENGAGED",
    "POLICY_ENV",
    "CadencePolicy",
    "decay_state",
    "deploy_policy",
    "due_instant",
    "in_active_hours",
    "policy_dict",
    "policy_with",
]
