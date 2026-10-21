"""Call-scoped projection of the box's schedules into one timestamp."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone


def next_wake_at(base, *, now=None):
    from tinyassets.agent_sessions import RECORDS_DIR
    from tinyassets.automations import AutomationStore
    from tinyassets.control_plane import cadence
    from tinyassets.control_plane.scheduler import owner_zone
    from tinyassets.control_plane.triggers import KIND_PROACTIVE, TriggerStore
    from tinyassets.control_plane.wake import wake_handler

    moment = now or datetime.now(timezone.utc)
    instants = [AutomationStore(base).next_wake_at(moment)]
    for trigger in TriggerStore(base).list(kind=KIND_PROACTIVE, enabled_only=True):
        if (wake_handler(trigger.kind) is None
                or (base / trigger.command_center_id / ".pause").exists()):
            continue
        policy = trigger.policy(cadence.deploy_policy())
        zone = owner_zone(base, trigger.owner_principal_id)
        due, _, _ = cadence.due_instant(
            policy, zone=zone, created_at=trigger.created_at,
            engaged_at=trigger.engaged_at, last_due_at=trigger.last_due_at, now=moment)
        # An overdue trigger outside active hours waits for the next opening.
        instants.append(cadence._into_active_hours(max(due, moment), policy, zone).timestamp())
    for path in (base / RECORDS_DIR).glob("*/agent-activities.db"):
        home = base / path.parent.name
        if not home.is_dir() or (home / ".pause").exists():
            continue
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master "
                                                  "WHERE type='table'")}
            for table, column, predicate in (
                ("agent_wakes", "next_check", "status IN ('pending','probing','firing')"),
                ("activity_events", "next_attempt_at", "wake_required=1 AND processed_at IS NULL"),
                ("request_answer_deliveries", "next_attempt_at", "delivered_at IS NULL"),
            ):
                if table in tables:
                    if table == "activity_events" and "wake_required" not in {
                        row[1] for row in db.execute("PRAGMA table_info(activity_events)")
                    }:
                        continue  # Plain activity history has no continuation wakes yet.
                    instants.append(db.execute(
                        f"SELECT MIN({column}) FROM {table} WHERE {predicate}").fetchone()[0])
    return min((at for at in instants if at is not None), default=None)


def report(base):
    from tinyassets.owner_channel import request

    request("", {"op": "NEXT_WAKE", "at": next_wake_at(base)})
