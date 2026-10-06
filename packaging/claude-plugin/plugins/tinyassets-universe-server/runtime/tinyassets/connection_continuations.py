"""Bind agent-raised connection asks to the existing durable answer wake."""

import json
import time
from contextlib import closing

from tinyassets import agent_activities, bound_requests, turn_interrupt
from tinyassets.approval_scopes import task_for
from tinyassets.owner_control import control


def bind(home, request_id):
    from tinyassets.auth.middleware import current_identity
    from tinyassets.effectors.authenticated_external_call import _initiating_agent
    from tinyassets.engine_steering import _route_params

    owner = current_identity().user_id
    agent = _initiating_agent(home)
    conversation, turn = _route_params()
    live = turn_interrupt.current()
    turn = live.live_id if live else turn
    if not agent or not turn:
        return False  # A settings draft has no paused agent to resume.
    with control(home), closing(bound_requests.connect(home)) as conn:
        row = conn.execute(
            "SELECT context_json,status FROM pending_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        if not row or row["status"] != "pending":
            return False
        if json.loads(row["context_json"]):
            return True  # A duplicate ask cannot reassign its saved origin.
        task_id = task_for(home, owner, agent)
        task = conn.execute(
            "SELECT activity_id,task_generation,task_expires_at FROM activities "
            "WHERE owner_principal=? AND agent_id=? AND (activity_id=? OR origin_ref=?) "
            "AND continuation_only=1 AND status NOT IN ('paused','completed','failed') "
            "AND task_expires_at>?",
            (owner, agent, task_id or "", turn, time.time()),
        ).fetchone()
        if not task:
            if task_id:
                raise bound_requests.RequestRefused("The initiating task ended.")
            record = agent_activities.create(
                home,
                owner_principal=owner,
                agent_id=agent,
                origin_kind="ask",
                origin_ref=turn,
                title="Waiting for a connection",
                brief="Continue after the owner connects.",
                continuation_only=True,
            )
            conn.execute(
                "UPDATE activities SET status='waiting_on_you' WHERE activity_id=?",
                (record["activity_id"],),
            )
            task = conn.execute(
                "SELECT activity_id,task_generation,task_expires_at "
                "FROM activities WHERE activity_id=?",
                (record["activity_id"],),
            ).fetchone()
        context = dict(
            owner=owner,
            home=home.name,
            agent=agent,
            task_id=task[0],
            task_generation=task[1],
            conversation=conversation or f"owner:{owner}:{agent}",
            turn=turn,
            kind="connection",
        )
        conn.execute(
            "UPDATE pending_requests SET context_json=? WHERE request_id=?",
            (json.dumps(context), request_id),
        )
        conn.commit()
    return True


def answered(conn, request_id, decision):
    """Called inside the same transaction that resolves the connection ask."""
    if "context_json" not in {
        row[1] for row in conn.execute("PRAGMA table_info(pending_requests)")
    }:
        return
    row = conn.execute(
        "SELECT context_json,revision FROM pending_requests WHERE request_id=?", (request_id,)
    ).fetchone()
    context = json.loads(row[0]) if row else {}
    if context.get("kind") != "connection":
        return
    current = {
        "request_id": request_id,
        "revision": row[1],
        "action": {"envelope": {"subject": context}},
    }
    # Never copy pasted fields, provider tokens, or arbitrary response text.
    bound_requests._wake(conn, current, {"connection": decision})
