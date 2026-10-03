"""The control-plane scheduler: one owner tick over the trigger table (D7, D11).

The control plane is the only always-on layer. The execution owner runs one
tick, under its lease, that fires every owed trigger; boxes and jails keep no
timers (``tests/control_plane_timer_inventory.py``). Today the tick is driven by the assigned-queue
consumer's poll, which also pumps user-declared automations
(``.automations.db``) in the same pass and under the same lease check.

A proactive trigger fires when all of these hold (harness §4.5, design D7):

* its kind has a registered wake handler (``wake.py``) and the row is enabled;
* its due instant (``cadence.due_instant``: the decayed period, the idle wait
  after the owner's last interaction, active hours in the owner's clock) has
  passed;
* the current time is inside the owner's active hours -- a fire owed at 20:00
  and noticed at 23:00 waits for the morning;
* its previous fire's run is no longer live -- single flight. A due fire that
  waits on a live run is the ONE pending fire; when the run outlives several
  windows they collapse onto the latest, and the count is recorded as
  coalesced;
* the owner lease is still held at the moment of claiming, and the trigger is
  still at the revision the decision was made on (an engagement or override
  committed meanwhile voids the claim; the next tick decides again).

Every input is platform state: the trigger rows, the owner's stored timezone
(``storage.account_timezone``) and run status (the runs store). The tick never
opens a path inside a command center's directory.
"""

from __future__ import annotations

import logging
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from tinyassets.control_plane import cadence
from tinyassets.control_plane.lease import LeaseLost, OwnerLease, current_owner_lease
from tinyassets.control_plane.triggers import (
    DECLINED_PREFIX,
    DEFAULT_AGENT_ID,
    FAILED_PREFIX,
    KIND_PROACTIVE,
    OUTCOME_LOST,
    OUTCOME_STARTED,
    Trigger,
    TriggerStore,
    window_start,
)
from tinyassets.control_plane.wake import WakeRequest, WakeResult, wake_handler

logger = logging.getLogger(__name__)

RunIsLive = Callable[[Path, str], bool]
ZoneFor = Callable[[Path, str], ZoneInfo]


def run_is_live(base_path: Path, run_id: str) -> bool:
    """Whether ``run_id`` is still queued or running, from the ROOT runs store.

    Reads the one status column directly, read-only. Not ``runs.get_run``: that
    also resolves a queued run's workspace wait, which opens the command
    center's own ``.runs.db`` -- a read inside the box (refute round 1, P1).
    """
    import sqlite3

    from tinyassets.runs import _TERMINAL_STATUSES, runs_db_path

    path = runs_db_path(base_path)
    if not path.is_file():
        return False
    conn = None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30.0)
        row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    except sqlite3.Error as exc:
        if str(exc) == "no such table: runs" or not path.is_file():
            return False
        # Unknown liveness must preserve single flight, not authorize another run.
        logger.warning("run liveness unknown run_id=%s: %s", run_id, exc)
        return True
    finally:
        if conn is not None:
            conn.close()
    return row is not None and str(row[0] or "") not in _TERMINAL_STATUSES


def owner_zone(base_path: Path, owner_principal_id: str) -> ZoneInfo:
    """The owner's stored clock, UTC when none is known (platform state)."""
    from tinyassets.schedule_timezone import resolve_zone
    from tinyassets.storage.account_timezone import get_account_timezone

    name = get_account_timezone(base_path, owner_user_id=owner_principal_id)
    return resolve_zone(name or "UTC")


@dataclass
class TickReport:
    lease_held: bool = True
    fired: list[str] = field(default_factory=list)
    declined: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    waiting_on_run: list[str] = field(default_factory=list)
    no_handler: int = 0
    lost_settled: int = 0
    lags_s: list[float] = field(default_factory=list)


class ControlPlaneScheduler:
    """Fires owed control-plane triggers. One instance per owner process."""

    def __init__(
        self,
        base_path: str | Path,
        *,
        lease: OwnerLease | None = None,
        live: RunIsLive = run_is_live,
        zone_for: ZoneFor = owner_zone,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.base_path = Path(base_path)
        self.store = TriggerStore(self.base_path)
        self._lease = lease
        self._live = live
        self._zone_for = zone_for
        self._clock = clock
        # This instance's identity on every claim it makes; a successor settles
        # every claim that is not its own (see ``settle_lost_claims``).
        self.incarnation = uuid.uuid4().hex
        self._settled_generation: int | None = None

    @property
    def lease(self) -> OwnerLease:
        return self._lease or current_owner_lease()

    def tick(self, now: datetime | None = None) -> TickReport:
        moment = now if now is not None else self._clock()
        report = TickReport()
        lease = self.lease
        if not lease.held():
            report.lease_held = False
            return report
        base = cadence.deploy_policy()
        for trigger in self.store.list(kind=KIND_PROACTIVE, enabled_only=True):
            handler = wake_handler(trigger.kind)
            if handler is None:
                report.no_handler += 1
                continue
            try:
                self._consider(trigger, handler, base, moment, report, now=now)
            except LeaseLost:
                report.lease_held = False
                return report
            except Exception:  # noqa: BLE001 - one owner's row cannot stop the tick
                logger.exception("control-plane trigger failed key=%s", trigger.trigger_key)
                report.failed.append(trigger.trigger_key)
        return report

    def _consider(
        self,
        trigger: Trigger,
        handler: Callable[[Path, WakeRequest], WakeResult],
        base: cadence.CadencePolicy,
        moment: datetime,
        report: TickReport,
        *,
        now: datetime | None,
    ) -> None:
        policy = trigger.policy(base)
        zone = self._zone_for(self.base_path, trigger.owner_principal_id)
        due, state, collapsed = cadence.due_instant(
            policy,
            zone=zone,
            created_at=trigger.created_at,
            engaged_at=trigger.engaged_at,
            last_due_at=trigger.last_due_at,
            now=moment,
        )
        eligible = due <= moment and cadence.in_active_hours(moment, policy, zone)
        waiting = eligible and trigger.last_run_id and (
            trigger.last_run_id.startswith("claim:")
            or self._live(self.base_path, trigger.last_run_id)
        )
        lease = self.lease
        lease.check()
        generation = int(lease.generation)
        if self._settled_generation != generation:
            report.lost_settled += self.store.settle_lost_claims(
                owner_generation=generation, owner_incarnation=self.incarnation,
            )
            self._settled_generation = generation
        if not eligible:
            return
        if trigger.last_run_id.startswith("claim:"):
            # Settlement may have cleared this snapshot's barrier, including
            # one settled by an earlier trigger in the same tick.
            current = self.store.get(trigger.trigger_key)
            if current is None:
                return
            waiting = current.last_run_id and (
                current.last_run_id.startswith("claim:")
                or self._live(self.base_path, current.last_run_id)
            )
        if waiting:
            report.waiting_on_run.append(trigger.trigger_key)
            return

        def admit() -> bool:
            """SQLite lock waits, like earlier handlers, can run past closing."""
            nonlocal moment
            moment = self._clock()
            return due <= moment and cadence.in_active_hours(moment, policy, zone)

        if not self.store.claim_fire(
            trigger.trigger_key,
            due_at=due,
            expected_revision=trigger.revision,
            owner_generation=generation,
            owner_incarnation=self.incarnation,
            collapsed=collapsed,
            now=moment,
            admit=admit if now is None else None,
        ):
            return
        request = WakeRequest(
            kind=trigger.kind,
            trigger_key=trigger.trigger_key,
            command_center_id=trigger.command_center_id,
            agent_id=trigger.agent_id,
            owner_principal_id=trigger.owner_principal_id,
            due_at=due.strftime("%Y-%m-%dT%H:%M:%SZ"),
            owner_generation=generation,
            decay_state=state,
        )
        try:
            result = handler(self.base_path, request)
        except Exception as exc:  # noqa: BLE001 - recorded on the fire row
            logger.exception("wake handler raised key=%s", trigger.trigger_key)
            self.store.finish_fire(
                trigger.trigger_key, due_at=due, run_id="",
                outcome=f"{FAILED_PREFIX}{type(exc).__name__}", now=moment,
            )
            report.failed.append(trigger.trigger_key)
            return
        if result.run_id:
            self.store.finish_fire(
                trigger.trigger_key, due_at=due, run_id=result.run_id,
                outcome=OUTCOME_STARTED, now=moment,
            )
            report.fired.append(trigger.trigger_key)
        else:
            self.store.finish_fire(
                trigger.trigger_key, due_at=due, run_id="",
                outcome=f"{DECLINED_PREFIX}{result.declined}", now=moment,
            )
            report.declined.append(trigger.trigger_key)
        report.lags_s.append(max(0.0, (moment - due).total_seconds()))


def ensure_proactive_trigger(
    base_path: str | Path, *, command_center_id: str, owner_principal_id: str,
    agent_id: str = DEFAULT_AGENT_ID, now: datetime | None = None,
) -> Trigger:
    """Enrol a command center's proactive wake (idempotent).

    Called by the consumer that registers the ``proactive`` wake handler (the
    harness's idle research), for a new user by default. Until a handler is
    registered an enrolled row is never fired.
    """
    return TriggerStore(base_path).ensure(
        KIND_PROACTIVE,
        command_center_id=command_center_id,
        owner_principal_id=owner_principal_id,
        agent_id=agent_id,
        now=now or datetime.now(timezone.utc),
    )


def note_owner_engagement(
    base_path: str | Path, *, command_center_id: str, principal_id: str,
    at: datetime | None = None,
) -> int:
    """The owner interacted with their command center: reset decay. Never raises."""
    try:
        return TriggerStore(base_path).note_engagement(
            command_center_id, principal_id=principal_id,
            at=at or datetime.now(timezone.utc),
        )
    except Exception:  # noqa: BLE001 - engagement bookkeeping never fails its cause
        logger.exception("engagement record failed cc=%s", command_center_id)
        return 0


def scheduler_metrics(
    base_path: str | Path, *, now: datetime | None = None,
    zone_for: ZoneFor = owner_zone,
) -> dict[str, Any]:
    """Due-vs-fired lag, coalesced count and decay state, from platform state.

    ``automations`` reports the same lag for user-declared schedules, read off
    their attempt rows (claimed minus due).
    """
    moment = now or datetime.now(timezone.utc)
    base = Path(base_path)
    store = TriggerStore(base)
    policy = cadence.deploy_policy()
    states = {state: 0 for state in cadence.DECAY_STATES}
    triggers = store.list(kind=KIND_PROACTIVE)
    enabled = 0
    for trigger in triggers:
        if not trigger.enabled:
            continue
        enabled += 1
        anchor = trigger.engaged_at or trigger.created_at
        try:
            state = cadence.decay_state(trigger.policy(policy), engaged_at=anchor, now=moment)
        except ValueError:
            continue
        states[state] += 1
    fires = store.fires_since(window_start(moment))
    lags = [float(f["lag_s"]) for f in fires if f["lag_s"] is not None]
    outcomes: dict[str, int] = {}
    for fire in fires:
        kind = str(fire["outcome"]).split(":", 1)[0]
        outcomes[kind] = outcomes.get(kind, 0) + 1
    return {
        "policy": cadence.policy_dict(policy),
        "proactive": {
            "triggers": len(triggers),
            "enabled": enabled,
            "decay_states": states,
            "registered": wake_handler(KIND_PROACTIVE) is not None,
            "coalesced_total": sum(t.coalesced_total for t in triggers),
            "fires_24h": outcomes,
            "lost_24h": outcomes.get(OUTCOME_LOST, 0),
            "lag_s_24h": _lag_summary(lags),
        },
        "automations": {"lag_s_24h": _lag_summary(_automation_lags(base, moment))},
    }


def _lag_summary(lags: list[float]) -> dict[str, Any]:
    if not lags:
        return {"count": 0, "p50": None, "max": None}
    return {
        "count": len(lags),
        "p50": round(statistics.median(lags), 3),
        "max": round(max(lags), 3),
    }


def _automation_lags(base: Path, moment: datetime) -> list[float]:
    """Claimed-minus-due for automation attempts claimed in the last 24 h."""
    import sqlite3

    from tinyassets.automations import automations_db_path

    path = automations_db_path(base)
    if not path.is_file():
        return []
    since = window_start(moment).astimezone(timezone.utc).replace(microsecond=0).isoformat()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        rows = conn.execute(
            "SELECT due_at, claimed_at FROM automation_attempts WHERE claimed_at >= ?",
            (since,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    lags: list[float] = []
    for due_at, claimed_at in rows:
        try:
            due = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
            claimed = datetime.fromisoformat(str(claimed_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        lags.append(max(0.0, (claimed - due).total_seconds()))
    return lags


__all__ = [
    "ControlPlaneScheduler",
    "TickReport",
    "ensure_proactive_trigger",
    "note_owner_engagement",
    "owner_zone",
    "run_is_live",
    "scheduler_metrics",
]
