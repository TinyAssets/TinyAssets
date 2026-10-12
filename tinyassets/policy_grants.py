"""Preview predicates and read-only matching for broker-held owner grants."""

import time
from contextlib import closing
from contextvars import ContextVar
from urllib.parse import urlsplit

from tinyassets import policy_rules

continuation_task = ContextVar("approval_continuation_task", default=None)
SCOPES = ("once", "task", "site", "always")


def task_is_current(conn, task_id, owner, agent, generation):
    """Check the originating task fence, never adopt its latest generation."""
    task = conn.execute(
        "SELECT * FROM activities WHERE activity_id=?", (task_id,),
    ).fetchone()
    return bool(
        task
        and task["owner_principal"] == owner
        and task["agent_id"] == agent
        and task["task_generation"] == generation
        and task["task_expires_at"] > time.time()
        and task["status"] not in ("paused", "completed", "failed")
    )


def task_for(home, owner, agent):
    from tinyassets import turn_interrupt
    from tinyassets.engine_steering import _route_params

    live = turn_interrupt.current()
    route = continuation_task.get() or (live.approval_task if live else None)
    if route is None:
        route = turn_interrupt.approval_task_for(owner, home.name, agent, _route_params()[1])
    if not route or route[0] != str(home.resolve()):
        return None
    if len(route) == 3:
        from tinyassets import bound_requests

        with closing(bound_requests.connect(home)) as conn:
            if not task_is_current(conn, route[1], owner, agent, route[2]):
                raise turn_interrupt.TurnInterrupted("continuation task changed")
    return route[1]


def predicate(envelope, scope):
    from tinyassets.bound_requests import RequestRefused

    if scope not in SCOPES:
        raise RequestRefused("Choose once, task, site or always.")
    if scope != "once" and envelope["action_class"] in ("spend", "money.move"):
        raise RequestRefused(
            "Standing payments need a budget-capped grant; use once for this preview."
        )
    subject = envelope["subject"]
    url = urlsplit(envelope["destination"])
    origin = f"{url.scheme.lower()}://{url.hostname.lower()}"
    if url.port and url.port != 443:
        origin += f":{url.port}"
    result = {
        k: envelope[k]
        for k in (
            "policy_digest",
            "connection_revision",
            "consent_digest",
            "binding_revision",
            "action_class",
            "operation",
        )
    }
    result.update(
        owner=subject["owner"],
        home=subject["home"],
        agent=subject["agent"],
        connection=envelope["arguments"]["connection_id"],
        origin=origin,
        scope=scope,
        expires_at=None,
    )
    # Even always remains limited to the displayed operation, class, account,
    # and exact origin. It does not imply every operation on a connector.
    if scope == "task":
        if not subject.get("task_id") or not subject.get("task_generation"):
            raise RequestRefused("Task context is missing; request a fresh preview.")
        result.update(task_id=subject["task_id"], task_generation=subject["task_generation"])
    return result


def matches(home, packet, owner, agent):
    """Find a candidate; the broker checks the signed policy before dispatch."""
    from tinyassets import bound_requests as bound

    if not owner or not agent:
        return False
    try:
        authority = bound._authority(home, packet, owner, agent)
    except (OSError, PermissionError, RuntimeError):
        return False
    candidate = predicate({**authority, "arguments": packet,
                           "subject": {"owner": owner, "home": home.name,
                                       "agent": agent}}, "site")
    for grant in policy_rules.standing_grants(home, agent):
        if any(grant.get(key) != value for key, value in candidate.items()
               if key not in ("scope", "expires_at", "policy_digest")):
            continue
        if grant.get("expires_at") and grant["expires_at"] <= time.time():
            continue
        if grant.get("scope") == "task" and task_for(home, owner, agent) != grant.get("task_id"):
            continue
        return grant
    return False
