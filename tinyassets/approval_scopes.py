"""Owner-issued HTTP preapprovals in the existing Rules store.

Rows are inert until their protected issuing decision is finalized. They never
change behavior precedence, connection consent, or another pending preview.
"""

import json
import secrets
import time
from contextlib import closing, contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit

from tinyassets import agent_rules
from tinyassets.owner_control import control

continuation_task = ContextVar("approval_continuation_task", default=None)
SCOPES = ("once", "task", "site", "always")


def task_for(home, owner, agent):
    from tinyassets import turn_interrupt
    from tinyassets.engine_steering import _route_params

    live = turn_interrupt.current()
    route = continuation_task.get() or (live.approval_task if live else None)
    if route is None:
        route = turn_interrupt.approval_task_for(owner, home.name, agent, _route_params()[1])
    return route[1] if route and route[0] == str(home.resolve()) else None


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


def materialize(home, conn, request_id, decision, envelope):
    """Caller owns owner-control; never resurrect a removed grant on recovery."""
    key = f"{request_id}:{decision['revision']}"
    grant = dict(decision["predicate"])
    grant.update(request_id=request_id, decision_id=key)
    with closing(agent_rules._connect(home)) as rules:
        rules.execute(
            "INSERT OR IGNORE INTO rules (agent,action_class,connection,operation,behaviour,"
            "note,updated_at,record_kind,decision_id,grant_json) "
            "VALUES (?,?,?,?,?,?,?,'preapproval',?,?)",
            (
                grant["agent"],
                grant["action_class"],
                grant["connection"],
                grant["operation"],
                agent_rules.DO_IF_PREAPPROVED,
                f"{grant['scope']}: {grant['operation']} {grant['origin']}",
                time.time(),
                key,
                json.dumps(grant),
            ),
        )
    decision["finalized"] = True
    conn.execute(
        "UPDATE pending_requests SET decision_json=? WHERE request_id=?",
        (json.dumps(decision), request_id),
    )
    conn.commit()


def matches(home, packet, owner, agent):
    from tinyassets import bound_requests as bound
    from tinyassets import turn_interrupt

    if not owner or not agent:
        return False
    with control(home), closing(bound.connect(home)) as conn:
        authority = bound._authority(home, packet, owner, agent)
        candidate = predicate(
            {
                **authority,
                "arguments": packet,
                "subject": {"owner": owner, "home": home.name, "agent": agent},
            },
            "site",
        )
        for rule in agent_rules.list_rules(home, agent):
            if rule.kind != "preapproval":
                continue
            grant = rule.grant
            if grant.get("revoked"):
                continue
            if any(
                grant.get(k) != v for k, v in candidate.items() if k not in ("scope", "expires_at")
            ):
                continue
            if grant.get("expires_at") and grant["expires_at"] <= time.time():
                continue
            row = conn.execute(
                "SELECT decision_json FROM pending_requests WHERE request_id=?",
                (grant["request_id"],),
            ).fetchone()
            decision = json.loads(row[0]) if row else {}
            if not decision.get("finalized") or decision.get("choice") != "approve":
                continue
            if grant["scope"] == "task":
                task = conn.execute(
                    "SELECT * FROM activities WHERE activity_id=?", (grant["task_id"],)
                ).fetchone()
                live = turn_interrupt.current()
                same_task = task_for(home, owner, agent) == grant["task_id"]
                same_turn = task and live and task["origin_ref"] == live.live_id
                if (
                    not task
                    or not (same_task or same_turn)
                    or task["owner_principal"] != owner
                    or task["agent_id"] != agent
                    or task["task_generation"] != grant["task_generation"]
                    or task["task_expires_at"] <= time.time()
                    or task["status"] in ("paused", "completed", "failed")
                ):
                    continue
            return grant
    return False


@contextmanager
def dispatch(home, packet, owner, agent, *, run_id, node_id):
    """Record the send boundary under owner-control, then release it for I/O."""
    from tinyassets import bound_requests as bound

    key = "scoped:" + secrets.token_hex(16)
    with bound.execution_attempt(home, key):
        with control(home), closing(bound.connect(home)) as conn:
            grant = matches(home, packet, owner, agent)
            if not grant:
                raise bound.RequestRefused("Preapproval changed before dispatch.")
            issuing = bound.card(conn, grant["request_id"])
            task_id = issuing["action"]["envelope"]["subject"]["task_id"]
            conn.execute(
                "INSERT INTO effect_intents (intent_key,activity_id,run_id,node_key,"
                "effect_index,wire_digest,connection_id,operation,path,state,"
                "created_at,updated_at) "
                "VALUES (?,?,?,?,0,?,?,?,?,'sent',?,?)",
                (
                    key,
                    task_id,
                    run_id,
                    node_id,
                    bound.digest(packet),
                    packet["connection_id"],
                    packet["verb"],
                    packet["request"]["path"],
                    time.time(),
                    time.time(),
                ),
            )
            conn.commit()
        receipt = {}
        state = "unknown"
        try:
            yield receipt
            state = "confirmed"
        finally:
            with control(home), closing(bound.connect(home)) as conn:
                conn.execute(
                    "UPDATE effect_intents SET state=?,receipt_json=?,updated_at=? "
                    "WHERE intent_key=?",
                    (state, json.dumps(receipt), time.time(), key),
                )
                conn.commit()
