"""Once-only protected previews for the existing generic HTTP effector.

No bearer or model-supplied identity constitutes approval. This module's
dispatch context is private, exact-packet-bound and consumed by one effector.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path

import rfc8785

from tinyassets import agent_activities as activities
from tinyassets import agent_review, agent_rules
from tinyassets.owner_control import control
from tinyassets.storage import pending_requests as requests

_dispatch = ContextVar("bound_request_dispatch", default=None)


class RequestRefused(ValueError):
    pass


def digest(value):
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _schema(conn):
    additions = {
        "pending_requests": {
            "revision": "INTEGER NOT NULL DEFAULT 1",
            "action_sha256": "TEXT NOT NULL DEFAULT ''",
            "context_json": "TEXT NOT NULL DEFAULT '{}'",
            "policy_digest": "TEXT NOT NULL DEFAULT ''",
            "expires_at": "REAL NOT NULL DEFAULT 0",
            "decision_json": "TEXT NOT NULL DEFAULT '{}'",
            "effect_intent_key": "TEXT NOT NULL DEFAULT ''",
        },
        "activities": {
            "task_generation": "INTEGER NOT NULL DEFAULT 1",
            "task_expires_at": "REAL NOT NULL DEFAULT 0",
            "continuation_only": "INTEGER NOT NULL DEFAULT 0",
        },
        "effect_intents": {
            "request_id": "TEXT",
            "request_revision": "INTEGER",
            "decision_ref": "TEXT",
        },
        "activity_events": {
            "dedupe_key": "TEXT",
            "request_id": "TEXT",
            "payload_json": "TEXT NOT NULL DEFAULT '{}'",
            "wake_required": "INTEGER NOT NULL DEFAULT 0",
            "attempt_ref": "TEXT NOT NULL DEFAULT ''",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "next_attempt_at": "REAL NOT NULL DEFAULT 0",
            "processed_at": "REAL",
            "result_json": "TEXT",
        },
    }
    for table, columns in additions.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS bound_effect_revision "
        "ON effect_intents(request_id, request_revision)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS request_wake_dedupe ON activity_events(dedupe_key)"
    )


def connect(home):
    conn = requests._db(home)
    _schema(conn)
    conn.commit()
    conn.row_factory = __import__("sqlite3").Row
    return conn


def validate_action(raw):
    from tinyassets.credential_shape import looks_like_credential

    if not isinstance(raw, dict) or set(raw) != {"executor", "arguments"}:
        raise RequestRefused("Use executor and arguments only; identity is server-bound.")
    if raw["executor"] != "authenticated_external_call":
        raise RequestRefused("This executor does not support bound previews yet.")
    packet = raw["arguments"]
    if not isinstance(packet, dict) or set(packet) - {
        "connection_id",
        "grant_id",
        "verb",
        "request",
    }:
        raise RequestRefused("Invalid action arguments.")
    if not all(
        isinstance(packet.get(k), str) and packet[k] for k in ("connection_id", "grant_id", "verb")
    ):
        raise RequestRefused("Name the connection, grant and HTTP verb.")
    request = packet.get("request")
    if not isinstance(request, dict) or set(request) - {"method", "host", "path", "query", "body"}:
        raise RequestRefused("Only method, host, path, query and body are supported in this slice.")
    if not isinstance(request.get("path"), str) or not request["path"].startswith("/"):
        raise RequestRefused("An absolute request path is required.")
    if request.get("method", packet["verb"]).upper() != packet["verb"].upper():
        raise RequestRefused("The request method must match the verb.")

    def no_secret_fields(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower().replace("-", "_") in {
                    "password",
                    "secret",
                    "token",
                    "access_token",
                    "refresh_token",
                    "api_key",
                    "apikey",
                    "authorization",
                    "credential",
                    "code_verifier",
                }:
                    raise RequestRefused("Credentials belong in connection custody, not an action.")
                no_secret_fields(item)
        elif isinstance(value, list):
            for item in value:
                no_secret_fields(item)

    no_secret_fields(raw)
    wire = json.dumps(raw, ensure_ascii=False, allow_nan=False)
    # Raw headers and unresolved file/state transforms are not supported here.
    # Inline strings remain byte-for-byte; another slice can add versioned blobs.
    if len(wire.encode()) > 65536 or "$ta." in wire or looks_like_credential(wire):
        raise RequestRefused("Use non-secret bounded inputs and connection references only.")
    return json.loads(wire)


def _authority(home, packet, owner, agent):
    from tinyassets import addressed_agents
    from tinyassets.broker.supervisor import broker_selected
    from tinyassets.custom_agents import get_binding
    from tinyassets.effectors import authenticated_external_call as effector
    from tinyassets.storage.effector_consents import list_consents
    from tinyassets.storage.outbound_connections import ConnectionLedger

    try:
        addressed_agents.resolve(home.parent, universe_id=home.name, owner=owner, agent_id=agent)
    except addressed_agents.AgentNotAddressable as exc:
        raise RequestRefused("The initiating agent is no longer available.") from exc
    if broker_selected():
        from tinyassets.broker.ledger_queries import authorized_connection
        from tinyassets.storage.outbound_connections import GrantResolutionError

        try:
            grant, resource, incarnation = authorized_connection(
                home.parent, principal=owner, command_center=home.name,
                grant_id=packet["grant_id"], connection_id=packet["connection_id"])
        except GrantResolutionError:
            raise RequestRefused("Connection authority is unavailable.") from None
        view, error = resource.to_view(), ""
    else:
        grant, view, error = effector._read_connection_context(
            db_path=home.parent / "outbound.db",
            grant_id=packet["grant_id"],
            connection_id=packet["connection_id"],
            universe_id=home.name,
        )
        incarnation = None
    if error or grant.owner_user_id != owner or view.owner_user_id != owner:
        raise RequestRefused("Connection authority is unavailable.")
    if incarnation is None:
        incarnation = ConnectionLedger(home.parent / "outbound.db").incarnation(
            packet["connection_id"])
    if not effector._check_consent(home, view.destination):
        raise RequestRefused("This connection needs current owner consent.")
    host, error = effector._resolve_host(packet["request"], view)
    if error:
        raise RequestRefused("The request destination is invalid.")
    url, error = effector._build_url(packet["request"], host)
    if error:
        raise RequestRefused("The request destination is invalid.")
    cls, operation = agent_rules.classify(
        home, packet["connection_id"], packet["verb"], effector._request_path(packet["request"])
    )
    with closing(agent_rules._connect(home)) as conn:
        agent_rules.list_rules(home, agent)
        matching = [
            list(r)
            for r in conn.execute(
                "SELECT id,action_class,connection,operation,behaviour,updated_at FROM rules "
                "WHERE agent=? AND action_class=? AND connection IN ('',?) AND operation IN ('',?) "
                "ORDER BY id",
                (agent, cls, packet["connection_id"], operation),
            )
        ]
        path = agent_rules._canonical_path(effector._request_path(packet["request"]))
        kinds = [
            list(row)
            for row in conn.execute(
                "SELECT id,method,path_prefix,kind,updated_at FROM operation_kinds "
                "WHERE connection=? AND method IN ('',?) ORDER BY id",
                (packet["connection_id"], operation),
            )
            if row[2] == "/" or path == row[2] or path.startswith(row[2].rstrip("/") + "/")
        ]
    with closing(agent_review._connect(home)) as conn:
        reviews = [
            list(row)
            for row in conn.execute(
                "SELECT 'on',updated_at FROM review_on WHERE agent=? AND action_class=? "
                "UNION ALL SELECT 'off',updated_at FROM review_off "
                "WHERE agent=? AND action_class=?",
                (agent, cls, agent, cls),
            )
        ]
    policy = digest(
        {
            "rules": matching,
            "classification": [cls, operation, kinds],
            "review": reviews,
        }
    )
    consent = [r for r in list_consents(home) if r.get("destination") == view.destination]
    binding = (
        get_binding(home.parent, universe_id=home.name, binding_id=agent)
        if agent != "main"
        else None
    )
    return {
        "policy_digest": policy,
        "connection_revision": digest(
            [
                view.as_dict(),
                incarnation,
            ]
        ),
        "consent_digest": digest([asdict(grant), consent]),
        "binding_revision": digest(binding),
        "destination": url,
        "action_class": cls,
        "operation": operation,
    }


def capture(home, raw):
    from tinyassets import turn_interrupt
    from tinyassets.auth.middleware import current_identity
    from tinyassets.effectors.authenticated_external_call import _initiating_agent
    from tinyassets.engine_steering import _route_params

    action = validate_action(raw)
    owner = current_identity().user_id
    agent = _initiating_agent(home)
    live = turn_interrupt.current()
    session, turn = _route_params()
    turn = live.live_id if live else turn
    if not agent or not turn:
        raise RequestRefused("A bound preview needs a trusted initiating agent turn.")
    with control(home), closing(connect(home)) as conn:
        authority = _authority(home, action["arguments"], owner, agent)
        decision = agent_rules.decide(
            home,
            authority["action_class"],
            connection=action["arguments"]["connection_id"],
            operation=authority["operation"],
            agent=agent,
        )
        if decision.behaviour != agent_rules.ASK_FIRST:
            raise RequestRefused("The initiating agent's current rule does not ask first.")
        task = conn.execute(
            "SELECT activity_id,task_generation,task_expires_at FROM activities "
            "WHERE origin_ref=? AND owner_principal=? AND agent_id=? "
            "AND continuation_only=1",
            (turn, owner, agent),
        ).fetchone()
        if task is None:
            task_record = activities.create(
                home,
                owner_principal=owner,
                title="Awaiting your decision",
                brief="Continue the original owner turn after its decision.",
                origin_kind="ask",
                origin_ref=turn,
                agent_id=agent,
                continuation_only=True,
            )
            expiry = time.time() + 86400
            conn.execute(
                "UPDATE activities SET continuation_only=1,task_expires_at=?,"
                "status='waiting_on_you' WHERE activity_id=?",
                (expiry, task_record["activity_id"]),
            )
            conn.commit()
            task = (task_record["activity_id"], 1, expiry)
        envelope = {
            "version": 1,
            **action,
            **authority,
            "subject": {
                "owner": owner,
                "home": home.name,
                "agent": agent,
                "task_id": task[0],
                "task_generation": task[1],
                "conversation": session or f"owner:{owner}:{agent}",
                "turn": turn,
            },
            "expires_at": min(time.time() + 1800, task[2]),
        }
        bound = {"type": "approve_action", "envelope": envelope}
        row = requests.create_request(
            home,
            kind="Approval",
            title=f"{action['arguments']['verb']} {authority['destination']}",
            body="",
            fields=[],
            action=bound,
            dedupe_key=digest({k: v for k, v in envelope.items() if k != "expires_at"}),
            agent=agent,
        )
        if not row or row.get("error"):
            raise RequestRefused("The approval could not be stored.")
        if not row.get("created", True):
            return card(conn, row["request_id"])
        conn.execute(
            "UPDATE pending_requests SET action_sha256=?,policy_digest=?,expires_at=?,"
            "context_json=? WHERE request_id=?",
            (
                digest(envelope),
                authority["policy_digest"],
                envelope["expires_at"],
                json.dumps(envelope["subject"]),
                row["request_id"],
            ),
        )
        conn.commit()
        return card(conn, row["request_id"])


def card(conn, request_id):
    row = conn.execute(
        "SELECT * FROM pending_requests WHERE request_id=?", (request_id,)
    ).fetchone()
    if row is None:
        raise RequestRefused("Request not found.")
    action = json.loads(row["action_json"])
    envelope = action.get("envelope")
    if (
        action.get("type") != "approve_action"
        or not envelope
        or digest(envelope) != row["action_sha256"]
    ):
        raise RequestRefused("This action needs a fresh protected preview.")
    intent = conn.execute(
        "SELECT state,receipt_json FROM effect_intents WHERE intent_key=?",
        (row["effect_intent_key"],),
    ).fetchone()
    phase = intent["state"] if intent else row["status"]
    return {
        "request_id": request_id,
        "revision": row["revision"],
        "action_sha256": row["action_sha256"],
        "status": row["status"],
        "phase": phase,
        "agent": envelope["subject"]["agent"],
        "title": f"{envelope['arguments']['verb']} {envelope['destination']}",
        "draft": envelope["arguments"]["request"].get("body"),
        "destination": envelope["destination"],
        "expires_at": envelope["expires_at"],
        "scope": "once",
        "action": action,
        "result": json.loads(intent["receipt_json"]) if intent and intent["receipt_json"] else None,
    }


def _owned(home, conn, request_id, owner):
    result = card(conn, request_id)
    env = result["action"]["envelope"]
    subject = env["subject"]
    if subject["owner"] != owner or subject["home"] != home.name:
        raise RequestRefused("Request not found.")
    return result


def _current(home, conn, request_id, owner):
    result = _owned(home, conn, request_id, owner)
    env = result["action"]["envelope"]
    subject = env["subject"]
    task = conn.execute(
        "SELECT * FROM activities WHERE activity_id=?", (subject["task_id"],)
    ).fetchone()
    if (
        not task
        or task["owner_principal"] != owner
        or task["agent_id"] != subject["agent"]
        or task["task_generation"] != subject["task_generation"]
        or task["status"] in ("paused", "completed", "failed")
        or min(task["task_expires_at"], env["expires_at"]) <= time.time()
    ):
        raise RequestRefused("This task stopped or expired; request a fresh preview.")
    current = _authority(home, env["arguments"], owner, subject["agent"])
    if any(current[k] != env[k] for k in current):
        raise RequestRefused("Authority changed; request a fresh preview.")
    return result


def stop(home, owner, agent):
    if not activities.store_path(home).is_file():
        return
    with control(home), closing(connect(home)) as conn:
        conn.execute(
            "UPDATE activities SET task_generation=task_generation+1,status='paused' "
            "WHERE owner_principal=? AND (? IS NULL OR agent_id=?) AND continuation_only=1 "
            "AND status NOT IN ('completed','failed')",
            (owner, agent, agent),
        )
        conn.commit()


def preview(home, request_id, session, *, draft=None, edit=False):
    owner = json.loads(session["identity_json"])["user_id"]
    with control(home), closing(connect(home)) as conn:
        current = _owned(home, conn, request_id, owner)
        if current["status"] not in ("pending", "deferred", "unresolved"):
            return current
        unavailable = ""
        try:
            current = _current(home, conn, request_id, owner)
        except RequestRefused as exc:
            if edit:
                raise
            unavailable = str(exc)
        if edit and current["status"] == "unresolved":
            raise RequestRefused("An uncertain action cannot be edited and resent.")
        if edit:
            env = current["action"]["envelope"]
            env["arguments"]["request"]["body"] = draft
            validate_action({"executor": env["executor"], "arguments": env["arguments"]})
            env.update(_authority(home, env["arguments"], owner, env["subject"]["agent"]))
            conn.execute(
                "UPDATE pending_requests SET action_json=?,action_sha256=?,revision=revision+1,"
                "decision_json='{}' WHERE request_id=?",
                (json.dumps(current["action"]), digest(env), request_id),
            )
            conn.commit()
            current = _current(home, conn, request_id, owner)
        token = secrets.token_urlsafe(32)
        decision = {
            "token_hash": digest(token),
            "session_hash": session["session_hash"],
            "revision": current["revision"],
            "action_sha256": current["action_sha256"],
            "scope": "once",
            "dismiss_only": bool(unavailable),
            "expires_at": min(
                time.time() + 300, session["expires_at"],
                time.time() + 300 if unavailable else current["expires_at"],
            ),
        }
        conn.execute(
            "UPDATE pending_requests SET decision_json=? WHERE request_id=?",
            (json.dumps(decision), request_id),
        )
        conn.commit()
        return {**current, "approval_token": token, "approval_unavailable": unavailable}


def _wake(conn, current, outcome):
    env = current["action"]["envelope"]
    task = env["subject"]["task_id"]
    key = f"answer:{current['request_id']}:{current['revision']}:{digest(outcome)}"
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq),0)+1 FROM activity_events WHERE activity_id=?", (task,)
    ).fetchone()[0]
    payload = {
        "owner": env["subject"]["owner"],
        "agent": env["subject"]["agent"],
        "task_generation": env["subject"]["task_generation"],
        "outcome": outcome,
        "request_id": current["request_id"],
    }
    conn.execute(
        "INSERT OR IGNORE INTO activity_events "
        "(activity_id,seq,ts,kind,line,dedupe_key,request_id,payload_json,wake_required) "
        "VALUES (?,?,?,'request_answer','Owner request completed',?,?,?,1)",
        (task, seq, time.time(), key, current["request_id"], json.dumps(payload)),
    )


def decide(home, data, session):
    with execution_attempt(home, data.get("request_id", "")):
        return _decide(home, data, session)


@contextmanager
def execution_attempt(home, request_id):
    from tinyassets import agent_sessions
    from tinyassets.owner_control import ControlUnavailable
    from tinyassets.singleton_lock import _lock_fd, _unlock_fd
    from tinyassets.universe_files import open_lock_file

    root = Path(home)
    relpath = f"{agent_sessions.RECORDS_DIR}/{root.name}/request-{digest(request_id)}.lock"
    fd = open_lock_file(root.parent, relpath, mode=0o600)
    if not _lock_fd(fd):
        os.close(fd)
        raise ControlUnavailable("This action is executing; refresh its status.")
    try:
        yield
    finally:
        _unlock_fd(fd)
        os.close(fd)


def _decide(home, data, session):
    from tinyassets.effectors.authenticated_external_call import (
        run_authenticated_external_call_effector,
    )
    from tinyassets.onboarding.owner_sessions import store

    owner = json.loads(session["identity_json"])["user_id"]
    request_id = data.get("request_id", "")
    with control(home), closing(connect(home)) as conn:
        current = _owned(home, conn, request_id, owner)
        env = current["action"]["envelope"]
        # Duplicate clicks may observe the committed state, never reserve again.
        if current["status"] not in ("pending", "deferred", "unresolved"):
            return current
        if data.get("decision") == "approve":
            current = _current(home, conn, request_id, owner)
        if (
            data.get("expected_revision") != current["revision"]
            or data.get("action_sha256") != current["action_sha256"]
        ):
            raise RequestRefused("Preview changed. Review the current action.")
        choice = data.get("decision")
        if current["status"] == "unresolved" and choice not in ("deny", "skip", "alternative"):
            raise RequestRefused("Check the uncertain outcome; this card cannot send it again.")
        if choice not in ("approve", "deny", "skip", "defer", "alternative"):
            raise RequestRefused("This action cannot be retried blindly; request a fresh preview.")
        if data.get("scope", "once") != "once":
            raise RequestRefused("Only once approval is available in this slice.")
        # Serialize decision admission against logout/account-switch revocation.
        with store() as sessions:
            sessions.execute("BEGIN IMMEDIATE")
            if not sessions.execute(
                "SELECT 1 FROM owner_sessions WHERE session_hash=? AND expires_at>?",
                (session["session_hash"], time.time()),
            ).fetchone():
                raise RequestRefused("Sign in again to approve.")
            conn.execute("BEGIN IMMEDIATE")
            stored = json.loads(
                conn.execute(
                    "SELECT decision_json FROM pending_requests WHERE request_id=?", (request_id,)
                ).fetchone()[0]
            )
            if (
                stored.get("token_hash") != digest(data.get("approval_token", ""))
                or stored.get("session_hash") != session["session_hash"]
                or stored.get("expires_at", 0) <= time.time()
                or stored.get("revision") != current["revision"]
                or stored.get("choice")
                or (choice == "approve" and stored.get("dismiss_only"))
            ):
                raise RequestRefused(
                    "Approval expired or belongs to another session. Preview again."
                )
            stored.pop("token_hash")
            stored.update(choice=choice, decided_at=time.time())
            conn.execute(
                "UPDATE pending_requests SET decision_json=? WHERE request_id=?",
                (json.dumps(stored), request_id),
            )
            if choice != "approve":
                status = "deferred" if choice == "defer" else "answered"
                conn.execute(
                    "UPDATE pending_requests SET status=?,answer_json=?,resolved_at=? "
                    "WHERE request_id=?",
                    (status, json.dumps({"decision": choice}), time.time(), request_id),
                )
                if choice != "defer":
                    _wake(conn, current, {"decision": choice})
                conn.commit()
                return card(conn, request_id)
            key = f"{request_id}:{current['revision']}"
            args = env["arguments"]
            conn.execute(
                "INSERT INTO effect_intents (intent_key,activity_id,run_id,node_key,effect_index,"
                "wire_digest,connection_id,operation,path,state,created_at,updated_at,request_id,"
                "request_revision,decision_ref) VALUES (?,?,?,'owner_approval',0,?,?,?,?"
                ",'planned',?,?,?,?,?)",
                (
                    key,
                    env["subject"]["task_id"],
                    key,
                    digest(args),
                    args["connection_id"],
                    args["verb"],
                    args["request"]["path"],
                    time.time(),
                    time.time(),
                    request_id,
                    current["revision"],
                    key,
                ),
            )
            conn.execute(
                "UPDATE pending_requests SET status='approved',effect_intent_key=? "
                "WHERE request_id=?",
                (key, request_id),
            )
            conn.commit()
            # Current policy/Stop cannot race this check: their writers share control.
            _current(home, conn, request_id, owner)
            conn.execute(
                "UPDATE effect_intents SET state='sent',updated_at=? WHERE intent_key=?",
                (time.time(), key),
            )
            conn.commit()
    packet = {"sink": env["executor"], **args}
    token = _dispatch.set((str(home.resolve()), digest(packet), env["subject"]["agent"]))
    try:
        result = run_authenticated_external_call_effector(
            node_id="owner_approval",
            output_keys=["action"],
            run_state={"action": packet},
            base_path=home,
            run_id=key,
        )
    finally:
        _dispatch.reset(token)
    # The effector's broker response is already sanitized. Never retain exceptions,
    # request echoes or arbitrary credential-bearing provider output here.
    if result.get("error_kind"):
        state = "failed" if result.get("dry_run") else "unknown"
        receipt = {"error_kind": result["error_kind"], "state": state}
    else:
        state = "confirmed"
        receipt = {k: result[k] for k in ("response", "status", "reason") if k in result}
    with control(home), closing(connect(home)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE effect_intents SET state=?,receipt_json=?,updated_at=? WHERE intent_key=?",
            (state, json.dumps(receipt), time.time(), key),
        )
        conn.execute(
            "UPDATE pending_requests SET status=?,resolved_at=? WHERE request_id=?",
            ("answered" if state == "confirmed" else "unresolved", time.time(), request_id),
        )
        _wake(conn, current, receipt)
        conn.commit()
        return card(conn, request_id)


def dispatch_agent(home, packet):
    held = _dispatch.get()
    if held and held[:2] == (str(home.resolve()), digest(packet)):
        _dispatch.set(None)  # One effector invocation, not permission for another packet.
        return held[2]
    return None
