"""The owner's surface for user-owned automations.

``tinyassets.automations`` is the whole storage + logic half (task 3.1/3.2);
this module is the *surface* half and adds nothing of its own. It reaches the
owner through the pinned MCP catalog only -- ``write_graph target=automation``
and ``read_graph target=automations|automation`` (Hard Rule 11 pins the
handles, so a new capability arrives as a new ``target``/``operation``, never a
new tool).

What this module owns, and why each piece is here rather than in the store:

* **Authorization.** Reads follow the universe's ordinary visibility rule;
  writes require an authenticated actor with a ``write``/``admin`` grant, and
  *controlling an existing row* additionally requires being its owner or an
  ``admin`` on the universe. A ``write`` collaborator can create their own
  automations but cannot pause someone else's -- the row is the owner's, not
  the universe's.
* **Projection.** The owner principal id is NEVER echoed. A caller learns only
  ``owner.is_you``, so a shared universe does not leak who scheduled what.
* **Refusal vocabulary.** ``register_automation`` fails loud with a snake_case
  ``reason`` (D4); this module maps each one to a sentence the owner can act on
  instead of returning a bare token. An unknown reason still surfaces verbatim
  -- a new refusal must never render as an empty explanation.
* **Fleet-era visibility.** ``list`` appends the old
  ``cloud_automation_controls`` rows flagged ``legacy``, so an owner who has
  rows from the retired activation layer can see them. They are read-only:
  no legacy action is reachable from here (task 3.3 deletes the rows).

Unsigned writes return ``authentication_required`` rather than the generic
access-denied envelope: the spec names that token, and "you are not signed in"
is a different instruction to the user than "your account lacks the grant".
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from pathlib import Path
from typing import Any

from tinyassets.api.helpers import _base_path, _request_universe
from tinyassets.automations import (
    EVENT_WOKE_PREFIX,
    REFUSAL_KEY_PREFIX,
    STATE_ACTIVE,
    STATE_PAUSED,
    TRIGGER_EVENT,
    TRIGGER_ONCE,
    Automation,
    AutomationStore,
    AutomationUnavailable,
    next_due_at,
    register_automation,
)
from tinyassets.consumer_reason_actions import RETIRED_FLEET_CONTROL_REASON

logger = logging.getLogger("universe_server.automations")

READ_ACTIONS = frozenset({"list", "get"})
WRITE_ACTIONS = frozenset({"create", "pause", "resume", "delete"})
ALLOWED_ACTIONS = tuple(sorted(READ_ACTIONS | WRITE_ACTIONS))

#: The owner-facing sentence for each refusal ``reason`` raised by
#: ``register_automation``. Keyed by the exact token the engine raises, so a
#: reason added there without a sentence here still reaches the owner (see
#: ``_unavailable``) rather than silently rendering as nothing.
_UNAVAILABLE_DETAIL = {
    "consumer_disabled": (
        "This daemon is not running automations right now, so nothing was "
        "stored. Ask the host to enable the assigned-queue consumer."
    ),
    "authentication_required": (
        "Sign in before creating an automation: an automation is owned by a "
        "person, never by the command center."
    ),
    "owner_not_admin": (
        "You need an admin grant on this command center to create an automation in "
        "it. A write grant lets you edit its work, not schedule it."
    ),
    "not_owner_home": (
        "Automations run in your own home command center. Create this one there, or "
        "make this command center your home first."
    ),
    "timezone_invalid": (
        "That timezone is not one I recognize. Pass an IANA name such as "
        "'America/Los_Angeles', 'Europe/Berlin' or 'UTC' -- or leave it out "
        "and I will use the zone your app reported."
    ),
    "no_serving_assignment": (
        "This command center has no model serving it yet, so a run would have "
        "nothing to run on. Connect a model -- a subscription or your own "
        "API-key source -- and set it serving first."
    ),
    "branch_not_readable": (
        "That workflow is not readable from here. Check the branch_def_id "
        "with read_graph target=branches."
    ),
    "branch_not_owned": (
        "You can only automate a workflow you authored. Remix that one into "
        "your own branch first, then automate the remix."
    ),
    "trigger_invalid": (
        "Give exactly one trigger: a positive interval_seconds, a valid "
        "cron_expr, a not_before (or delay_seconds) for one wake, or an "
        "event_type -- not two, and not none."
    ),
    "event_type_unknown": (
        "That event is not one the engine emits, so the automation would never "
        "fire. Subscribe to run_completed, pending_request_answered, app_event "
        "or owner_message."
    ),
    "event_filter_invalid": (
        "event_filter must be an object of non-empty strings over the event's "
        "own fields: run_completed takes branch_def_id (required), outcome and "
        "run_id; pending_request_answered takes request_id, kind and status; "
        "app_event takes name (required); owner_message takes none."
    ),
    "overlap_invalid": (
        "overlap must be queue (wait for the running one, the default), skip "
        "(drop this run) or cancel_previous (stop the running one first)."
    ),
    "not_owner_or_admin": (
        "This automation belongs to someone else. Only its owner or an admin "
        "on this command center can change it."
    ),
    "already_retired": (
        "This automation is deleted. Create a new one rather than reviving it."
    ),
}


# -- Envelopes ----------------------------------------------------------------


def _not_found() -> dict[str, Any]:
    """Non-oracular miss.

    The same envelope for "no such id" and "that id lives in another universe",
    so a caller cannot enumerate another universe's automations by probing.
    """
    return {"error": "not_found", "resource": "automation"}


def _unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    token = (reason or "unknown").strip() or "unknown"
    return {
        "error": "automation_unavailable",
        "reason": token,
        "detail": _UNAVAILABLE_DETAIL.get(
            token,
            f"This automation cannot be created or changed right now ({token}).",
        ),
        **extra,
    }


def _payload_invalid(detail: str) -> dict[str, Any]:
    return {"error": "automation_payload_invalid", "detail": detail}


def _document(payload: Any) -> dict[str, Any] | None:
    """Decode ``payload`` into a JSON object, or None when it is not one.

    ``write_graph`` hands the payload through as a string; a direct caller may
    pass the dict. An empty/absent payload is an empty document, not an error --
    ``list`` legitimately has nothing to say.
    """
    if payload is None:
        return {}
    document = payload
    if isinstance(document, str):
        text = document.strip()
        if not text:
            return {}
        try:
            document = json.loads(text)
        except (TypeError, ValueError):
            return None
    return document if isinstance(document, dict) else None


# -- Projection ---------------------------------------------------------------


def _next_due_at(automation: Automation) -> str:
    """The row's next fire time. A read must never fail on a malformed row."""
    try:
        return next_due_at(automation, datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001 - a projection field, not the trigger itself
        logger.warning("next_due_at unavailable for %s", automation.automation_id)
        return ""


def _cron_timezone(automation: Automation) -> str:
    """The zone a cron row runs in; '' for a trigger with no wall-clock slot."""
    from tinyassets.automations import TRIGGER_CRON, cron_zone_name

    if automation.trigger_kind != TRIGGER_CRON:
        return ""
    return cron_zone_name(automation)


def _schedule_local(automation: Automation) -> str:
    """``7:00 AM America/Los_Angeles``, or '' when there is no single slot.

    One place builds this string, so no surface can reintroduce a bare time --
    which is the whole defect: the universe said "7am server time" because
    nothing gave it the zone to say instead. A multi-slot expression (``0,30 *``)
    has no single wall time to name and returns '', leaving ``cron_expr`` as the
    honest answer rather than picking one of its slots to display.
    """
    from tinyassets.automations import TRIGGER_CRON, cron_zone_name
    from tinyassets.schedule_timezone import describe_slot
    from tinyassets.scheduler import CronParseError, CronSchedule

    if automation.trigger_kind != TRIGGER_CRON:
        return ""
    try:
        schedule = CronSchedule.parse(automation.cron_expr)
    except CronParseError:
        return ""
    if len(schedule.hours) != 1 or len(schedule.minutes) != 1:
        return ""
    slot = dt_time(next(iter(schedule.hours)), next(iter(schedule.minutes)))
    return describe_slot(slot, cron_zone_name(automation))


def _projection(
    automation: Automation,
    *,
    actor: str,
    recent_reason: str = "",
) -> dict[str, Any]:
    """The owner-visible shape of one row.

    Deliberately omits ``owner_principal_id``: on a shared universe the id of
    whoever scheduled a run is not the reader's business, and ``is_you`` is the
    only bit any surface actually branches on.
    """
    projected: dict[str, Any] = {
        "automation_id": automation.automation_id,
        "universe_id": automation.universe_id,
        "name": automation.name,
        "branch_def_id": automation.branch_def_id,
        "trigger": {
            "kind": automation.trigger_kind,
            "interval_seconds": automation.interval_seconds,
            "cron_expr": automation.cron_expr,
            # WHICH CLOCK the cron expression is written in. A schedule was
            # returned without one until 2026-09-30, so the only true thing a
            # universe could tell its owner was "7am server time" -- which is
            # midnight for a Pacific user. '' for a non-cron trigger, which has
            # no wall-clock slot.
            "timezone": _cron_timezone(automation),
            # The same fact as prose, so a surface cannot render the time
            # without the zone: "7:00 AM America/Los_Angeles".
            "schedule_local": _schedule_local(automation),
            # A one-shot wake's instant (kind "once"); '' for a cadence.
            "not_before": automation.not_before,
            # A subscription's event and filter (kind "event").
            "event_type": automation.event_type,
            "event_filter": dict(automation.event_filter or {}),
        },
        "inputs": dict(automation.inputs),
        # What a due run does while this agent (its branch) is still running.
        "overlap": automation.overlap,
        "desired_state": automation.desired_state,
        "pause_reason": automation.pause_reason,
        "revision": automation.revision,
        "created_at": automation.created_at,
        "updated_at": automation.updated_at,
        "retired_at": automation.retired_at,
        "last_due_at": automation.last_due_at,
        "last_run_id": automation.last_run_id,
        "last_reason": automation.last_reason,
        "last_finished_at": automation.last_finished_at,
        # When the pump next owes a run; '' while paused or retired.
        "next_due_at": _next_due_at(automation),
        # How close this automation is to auto-pausing itself. Read through
        # getattr so the surface does not depend on which half of task 3.1
        # lands first; a row without the counter reports a truthful zero.
        "consecutive_failures": int(
            getattr(automation, "consecutive_failures", 0) or 0
        ),
        "owner": {"is_you": automation.owner_principal_id == actor},
    }
    if recent_reason:
        projected["recent_reason"] = recent_reason
    return projected


def _recent_reasons(base: Path, universe_id: str) -> dict[str, str]:
    """Fresh ``automation:<id>`` refusal reasons, or {} if unreadable.

    The owner's list must still render when the refusal ledger is missing or
    the freshness window is misconfigured -- "why was this skipped" is an
    enrichment, never a precondition for seeing the row.
    """
    try:
        from tinyassets.runtime.assigned_queue_consumer import (
            assigned_queue_refusal_freshness_seconds,
        )
        from tinyassets.storage.assigned_queue_refusals import (
            AssignedQueueRefusalStore,
        )

        return AssignedQueueRefusalStore(base).fresh_reasons(
            universe_id=universe_id,
            max_age_seconds=assigned_queue_refusal_freshness_seconds(),
        )
    except Exception:  # noqa: BLE001 - visibility must not break the list
        logger.warning(
            "automation recent-reason lookup failed for command center %r",
            universe_id,
            exc_info=True,
        )
        return {}


def _legacy_rows(base: Path, universe_id: str) -> list[dict[str, Any]]:
    """The retired fleet-era control rows, flagged and read-only.

    Visible so an owner is not left wondering where an automation they made
    under the old activation layer went; not actionable, because that layer no
    longer has an executor. Task 3.3 deletes the rows themselves.
    """
    from tinyassets.storage import db_path

    if not db_path(base).is_file():
        return []
    try:
        from tinyassets.storage.cloud_automation_control import (
            CloudAutomationControlStore,
        )

        controls = CloudAutomationControlStore(base).list_controls(
            universe_id=universe_id,
            limit=100,
        )
    except Exception:  # noqa: BLE001 - a dead layer must not break a live read
        logger.warning(
            "legacy automation control listing failed for command center %r",
            universe_id,
            exc_info=True,
        )
        return []
    return [
        {
            "automation_id": control.automation_id,
            "legacy": True,
            "status": "retired_fleet_era",
            # The consumer stopped it with this reason (plan C1). Carried on
            # the row so it outlives the refusal ledger's freshness window.
            "detail": RETIRED_FLEET_CONTROL_REASON,
            "desired_state": getattr(
                control.desired_state, "value", control.desired_state
            ),
        }
        for control in controls
    ]


# -- Actions ------------------------------------------------------------------


def _create(
    base: Path,
    *,
    universe_id: str,
    actor: str,
    payload: Any,
) -> dict[str, Any]:
    document = _document(payload)
    if document is None:
        return _payload_invalid("payload_json must be a JSON object")

    name = document.get("name", "")
    branch_def_id = document.get("branch_def_id", "")
    inputs = document.get("inputs", {})
    cron_expr = document.get("cron_expr", "")
    raw_interval = document.get("interval_seconds", 0)
    event_type = document.get("event_type", "")
    event_filter = document.get("event_filter", {})
    overlap = document.get("overlap", "")
    timezone_name = document.get("timezone", "")
    not_before = document.get("not_before", "")
    raw_delay = document.get("delay_seconds")

    if not isinstance(name, str) or not name.strip():
        return _payload_invalid("name must be a non-empty string")
    if not isinstance(branch_def_id, str) or not branch_def_id.strip():
        return _payload_invalid("branch_def_id must be a non-empty string")
    if not isinstance(inputs, dict):
        return _payload_invalid("inputs must be a JSON object")
    if not isinstance(cron_expr, str):
        return _payload_invalid("cron_expr must be a string")
    if not isinstance(event_type, str):
        return _payload_invalid("event_type must be a string")
    if not isinstance(event_filter, dict):
        return _payload_invalid("event_filter must be a JSON object")
    if not isinstance(overlap, str):
        return _payload_invalid("overlap must be a string")
    if not isinstance(timezone_name, str):
        return _payload_invalid("timezone must be an IANA name string")
    # A bool is an int in Python; interval_seconds=true is a malformed payload,
    # not a zero-second interval.
    if isinstance(raw_interval, bool) or not isinstance(raw_interval, (int, str)):
        return _payload_invalid("interval_seconds must be an integer")
    try:
        interval_seconds = int(raw_interval or 0)
    except (TypeError, ValueError):
        return _payload_invalid("interval_seconds must be an integer")
    # A one-shot wake the agent sets for itself: "run this branch once, not
    # before then". The same row an agent node's enqueue_branch_run stores.
    if not isinstance(not_before, str):
        return _payload_invalid("not_before must be an ISO-8601 timestamp string")
    if raw_delay is not None:
        if not_before.strip():
            return _payload_invalid("give not_before or delay_seconds, not both")
        if (isinstance(raw_delay, bool) or not isinstance(raw_delay, (int, float))
                or raw_delay != raw_delay or raw_delay < 0):
            return _payload_invalid("delay_seconds must be a number >= 0")
        try:
            not_before = (
                datetime.now(timezone.utc) + timedelta(seconds=float(raw_delay))
            ).isoformat()
        except OverflowError:
            return _payload_invalid("delay_seconds is too large")

    try:
        created = register_automation(
            base,
            universe_id=universe_id,
            owner_principal_id=actor,
            name=name.strip(),
            branch_def_id=branch_def_id.strip(),
            interval_seconds=interval_seconds,
            cron_expr=cron_expr.strip(),
            not_before=not_before.strip(),
            event_type=event_type.strip(),
            event_filter=event_filter,
            overlap=overlap.strip(),
            timezone_name=timezone_name.strip(),
            inputs=inputs,
        )
    except AutomationUnavailable as exc:
        return _unavailable(exc.reason)
    return {
        "status": "automation_created",
        "automation": _projection(created, actor=actor),
    }


def _list(
    base: Path,
    *,
    universe_id: str,
    actor: str,
    payload: Any,
    limit: int | None,
) -> dict[str, Any]:
    document = _document(payload)
    if document is None:
        return _payload_invalid("payload_json must be a JSON object")
    include_retired = bool(document.get("include_retired", False))

    rows = AutomationStore(base).list(
        universe_id=universe_id,
        include_retired=include_retired,
    )
    reasons = _recent_reasons(base, universe_id)
    # ``limit=None`` is every row: a model door pages the whole list to fit its
    # ceiling itself, and a page it did not choose would hide the 31st row.
    bound = len(rows) if limit is None else max(1, int(limit or 30))
    records = [
        _with_last_wake(base, row, _projection(
            row,
            actor=actor,
            recent_reason=reasons.get(f"{REFUSAL_KEY_PREFIX}{row.automation_id}", ""),
        ))
        for row in rows[:bound]
    ]
    legacy = _legacy_rows(base, universe_id)
    records.extend(legacy)
    return {
        "universe_id": universe_id,
        "automations": records,
        "count": len(records),
        # How many automations exist, so a page smaller than that says so.
        "total": len(rows) + len(legacy),
        "include_retired": include_retired,
    }


def _with_last_wake(
    base: Path, automation: Automation, projected: dict[str, Any],
) -> dict[str, Any]:
    """An event subscription's latest wake and what its run did.

    The subscription row records only that it fired (``woke:<id>``); the run
    lives on the wake. Read here, never copied, and only a wake this
    subscription stored: the id comes from the runtime, but the lookup checks.
    """
    if automation.trigger_kind != TRIGGER_EVENT:
        return projected
    wake_id = automation.last_reason.removeprefix(EVENT_WOKE_PREFIX)
    if wake_id == automation.last_reason or not wake_id:
        return projected
    try:
        wake = AutomationStore(base).get(wake_id)
    except Exception:  # noqa: BLE001 - enrichment, never a precondition
        logger.warning("last wake lookup failed for %r", wake_id, exc_info=True)
        return projected
    # Provenance, not only scope: a one-shot wake of the same owner that this
    # subscription itself stored (refute concern, 2026-09-30).
    event = (wake.inputs or {}).get("event") if wake is not None else None
    if (
        wake is None
        or wake.universe_id != automation.universe_id
        or wake.owner_principal_id != automation.owner_principal_id
        or wake.trigger_kind != TRIGGER_ONCE
        or not isinstance(event, dict)
        or event.get("subscription_id") != automation.automation_id
    ):
        return projected
    projected["last_wake"] = {
        "automation_id": wake.automation_id,
        "not_before": wake.not_before,
        "last_run_id": wake.last_run_id,
        "last_reason": wake.last_reason,
        "last_finished_at": wake.last_finished_at,
        "pause_reason": wake.pause_reason,
        "retired_at": wake.retired_at,
    }
    return projected


def _controllable(
    base: Path,
    automation: Automation,
    *,
    actor: str,
) -> bool:
    """Owner-or-admin: who may pause/resume/delete THIS row.

    Universe write access already gated the request; this is the narrower
    question of whose automation it is. An admin is included because a universe
    owner must be able to stop work running in their universe when the person
    who scheduled it is gone.
    """
    if automation.owner_principal_id == actor:
        return True
    from tinyassets.daemon_server import universe_access_permission

    try:
        return (
            universe_access_permission(
                base,
                universe_id=automation.universe_id,
                actor_id=actor,
            )
            == "admin"
        )
    except Exception:  # noqa: BLE001 - fail closed on an unreadable ACL
        logger.warning(
            "automation control ACL read failed for command center %r",
            automation.universe_id,
            exc_info=True,
        )
        return False


def _control(
    base: Path,
    *,
    action: str,
    universe_id: str,
    automation_id: str,
    actor: str,
    expected_revision: Any,
) -> dict[str, Any]:
    store = AutomationStore(base)
    automation = store.get(str(automation_id or "").strip())
    if automation is None or automation.universe_id != universe_id:
        return _not_found()
    if not _controllable(base, automation, actor=actor):
        return _unavailable("not_owner_or_admin", automation_id=automation.automation_id)
    if automation.retired_at:
        return _unavailable("already_retired", automation_id=automation.automation_id)

    if isinstance(expected_revision, bool) or not isinstance(
        expected_revision, (int, str)
    ):
        return _payload_invalid("expected_revision must be an integer")
    try:
        revision = int(expected_revision or 0)
    except (TypeError, ValueError):
        return _payload_invalid("expected_revision must be an integer")
    if revision != automation.revision:
        return {
            "error": "automation_revision_conflict",
            "expected_revision": revision,
            "current_revision": automation.revision,
        }

    now = datetime.now(timezone.utc)
    try:
        if action == "delete":
            updated = store.retire(
                automation.automation_id,
                expected_revision=revision,
                now=now,
            )
            status = "automation_deleted"
        else:
            desired = STATE_PAUSED if action == "pause" else STATE_ACTIVE
            updated = store.set_desired_state(
                automation.automation_id,
                desired,
                expected_revision=revision,
                reason="owner_paused" if desired == STATE_PAUSED else "",
                now=now,
            )
            status = "automation_paused" if desired == STATE_PAUSED else "automation_resumed"
    except ValueError as exc:
        # The store re-checks existence, retirement and the revision inside its
        # own BEGIN IMMEDIATE. Losing that race is a real conflict, not a bug:
        # report it rather than reporting a change that did not happen.
        return {"error": "automation_control_conflict", "detail": str(exc)}
    return {"status": status, "automation": _projection(updated, actor=actor)}


# -- Entry point --------------------------------------------------------------


def automations(
    *,
    action: str,
    universe_id: str = "",
    automation_id: str = "",
    expected_revision: int = 0,
    payload: Any = None,
    limit: int | None = 30,
) -> dict[str, Any]:
    """Create, inspect and control the caller's universe automations."""
    normalized = (action or "").strip().lower()
    if normalized not in READ_ACTIONS and normalized not in WRITE_ACTIONS:
        return {
            "error": "unknown_automation_action",
            "action": action,
            "allowed_actions": list(ALLOWED_ACTIONS),
        }

    from tinyassets.api import permissions

    write = normalized in WRITE_ACTIONS
    uid = _request_universe(universe_id)

    # Ordered deliberately: "sign in" before "you lack the grant". The spec
    # names `authentication_required` for an unsigned create, and an unbound
    # caller has no grant to describe.
    if write and not permissions.is_authenticated_request():
        return {
            "error": "authentication_required",
            "resource": "automation",
            "action": normalized,
            "universe_id": uid,
        }
    if not permissions.universe_access_allows(uid, write=write):
        return permissions.universe_access_error(
            universe_id=uid,
            write=write,
            action=normalized,
            surface="write_graph" if write else "read_graph",
        )

    base = _base_path()
    actor = permissions.current_actor_id()

    if normalized == "create":
        return _create(base, universe_id=uid, actor=actor, payload=payload)
    if normalized == "list":
        return _list(
            base,
            universe_id=uid,
            actor=actor,
            payload=payload,
            limit=limit,
        )
    if normalized == "get":
        automation = AutomationStore(base).get(str(automation_id or "").strip())
        if automation is None or automation.universe_id != uid:
            return _not_found()
        reasons = _recent_reasons(base, uid)
        return {
            "automation": _with_last_wake(base, automation, _projection(
                automation,
                actor=actor,
                recent_reason=reasons.get(
                    f"{REFUSAL_KEY_PREFIX}{automation.automation_id}", ""
                ),
            ))
        }
    return _control(
        base,
        action=normalized,
        universe_id=uid,
        automation_id=automation_id,
        actor=actor,
        expected_revision=expected_revision,
    )


__all__ = ["automations", "ALLOWED_ACTIONS", "READ_ACTIONS", "WRITE_ACTIONS"]
