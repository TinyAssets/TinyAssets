"""Verified inbound events: owner configuration, receipts and durable session admission."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

from tinyassets.storage import inbound_events as inbox

MAX_BODY_BYTES = 1024 * 1024


def inbound_enabled() -> bool:
    return os.environ.get("TINYASSETS_INBOUND_ENABLED", "").lower() in {"1", "true", "yes", "on"}


def dispatch_receipt(base: Path, row: dict) -> dict:
    from tinyassets.control_plane import session

    if row["activity_id"] is not None:
        return {"session": row["activity_id"]}
    config = inbox.configuration(base, row["binding"])
    if (config is None or not config["enabled"] or config["revision"] != row["revision"]
            or config["agent_ref"] != row["agent_ref"] or config["universe"] != row["universe"]):
        raise PermissionError("inbound binding withdrawn")
    message = json.dumps({"inbound": {"receipt": row["receipt"],
                        "raw_base64": base64.b64encode(row["body"]).decode("ascii")}},
                       separators=(",", ":"))
    result = session.start(row["agent_ref"], message, session.Inbound(
        base_path=base, universe_id=row["universe"], owner=row["owner"], receipt=row["receipt"]))
    inbox.mark_started(base, row["receipt"], result["session"])
    return result


def recover_pending(base: Path) -> None:
    """Repair a crash between inbox commit and send admission using the same receipt."""
    from tinyassets.control_plane.session import AgentUnavailable

    for row in inbox.pending(base):
        try:
            dispatch_receipt(base, row)
        except (PermissionError, AgentUnavailable):
            # A missing package or withdrawn binding is a refusal, never another agent.
            inbox.finish(base, row["receipt"])


def reconcile_on_wake(base: Path) -> None:
    """Apply this owner's installation snapshot, then report readiness for tripped rows."""
    if not inbox.has_shared(base):
        return
    from tinyassets.owner_channel import request

    view = request(None, {"op": "INBOUND_INSTALLATIONS", "request": {"operation": "snapshot"}})
    rows = view.get("installations") if isinstance(view, dict) else None
    if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("binding"), str)
            or type(row.get("revision")) is not int for row in rows):
        raise RuntimeError("installation snapshot unavailable")
    inbox.reconcile_installations(base, rows)
    for row in rows:
        if row.get("degraded") is True:
            request(None, {"op": "INBOUND_INSTALLATIONS", "request": {
                "operation": "ready", "binding": row["binding"], "revision": row["revision"]}})


def ready_for_send(base: Path, ident: str) -> bool:
    if not ident.startswith("inb_"):
        return True
    row = inbox.receipt(base, ident)
    config = inbox.configuration(base, row["binding"])
    return bool(config and config["enabled"] and not config["paused"]
                and not (base / config["universe"] / ".pause").exists()
                and config["revision"] == row["revision"]
                and config["agent_ref"] == row["agent_ref"]
                and config["universe"] == row["universe"])


def handle_challenge(*, proof: dict, query: str, base: Path, owner: str) -> tuple[int, dict]:
    """Owner-local subscription challenge. It never admits an event or a session."""
    del owner
    config = inbox.configuration(base, proof.get("binding", ""))
    if config is None or not config["enabled"] or config["revision"] != proof.get("revision"):
        return 404, {"error": "not_found"}
    from urllib.parse import parse_qsl

    values = dict(parse_qsl(query, keep_blank_values=False))
    challenge = values.get("hub.challenge") or values.get("challenge")
    if (not isinstance(challenge, str) or not challenge
            or len(challenge.encode("utf-8")) > 1024):
        return 404, {"error": "not_found"}
    return 200, {"challenge": challenge}


def _refuse(base: Path, receipt: str, error: str) -> tuple[int, dict]:
    inbox.finish(base, receipt)
    return 200, {"receipt": receipt, "state": "refused", "error": error}


def _shared_identity(body: bytes) -> tuple[str, str] | None:
    """Slack event id and unix time, or None when the envelope cannot be keyed."""
    try:
        envelope = json.loads(body)
        event_id = envelope["event_id"]
        timestamp = envelope["event_time"]
    except (ValueError, KeyError, TypeError):
        return None
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        return None
    stamp = str(timestamp)
    if (not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 256
            or not re.fullmatch(r"[0-9]{1,12}", stamp)):
        return None
    return event_id, stamp


def _replay(row: dict) -> tuple[int, dict] | None:
    """An already admitted or refused receipt. Never opens another session."""
    if row["activity_id"] is not None:
        return 202, {"receipt": row["receipt"], "deduped": True, "state": "admitted"}
    if row["completed_at"] is not None:
        return 200, {"receipt": row["receipt"], "deduped": True, "state": "refused"}
    return None


def handle_verified(*, proof: dict, body: bytes, headers: dict, base: Path,
                    owner: str, now: float | None = None) -> tuple[int, dict]:
    """Called only under per-box router authentication; no token authentication here."""
    from tinyassets.control_plane.session import AgentUnavailable

    if len(body) > MAX_BODY_BYTES:
        return 413, {"error": "too_large"}
    if hashlib.sha256(body).hexdigest() != proof.get("digest"):
        return 400, {"error": "body_digest_mismatch"}
    config = inbox.configuration(base, proof.get("binding", ""))
    shared = bool(proof.get("shared"))
    matched = config is not None and config["revision"] == proof.get("revision")
    names = {key.lower(): value for key, value in headers.items()}
    if shared:
        identity = _shared_identity(body)
        if identity is None:
            # Malformed shared identity is an authenticated no-op: a 400 would
            # spend the shared app's failure budget on an event Slack will retry.
            return 200, {"state": "refused", "error": "event_identity_required"}
        event_id, timestamp = identity
    else:
        event_id, timestamp = names.get("idempotency-key", ""), names.get("x-event-timestamp", "")
    if not isinstance(event_id, str) or not event_id.strip() or not re.fullmatch(
            r"[0-9]{1,12}", timestamp):
        return 400, {"error": "event_identity_required"}
    withdrawn = matched and not config["enabled"]
    if not matched or withdrawn:
        if not shared:
            return 404, {"error": "binding_not_configured"}
        if not matched:
            revision = proof.get("revision")
            row = (inbox.find_event(base, str(proof.get("binding") or ""), revision, event_id)
                   if type(revision) is int else None)
            if row is None:
                return 200, {"state": "refused", "error": "binding_withdrawn"}
            replay = _replay(row)
            if replay is not None:
                return replay
            return _refuse(base, row["receipt"], "binding_withdrawn")
    if shared:
        inbox.record_shared(base, config["binding"])
    try:
        result = inbox.accept(base, binding=config["binding"], revision=config["revision"],
                              event_id=event_id, event_at=int(timestamp), body=body,
                              owner=owner, agent_ref=config["agent_ref"],
                              universe=config["universe"], now=now)
        row = inbox.receipt(base, result["receipt"])
        if withdrawn:
            replay = _replay(row)
            if replay is not None:
                return replay
            return _refuse(base, result["receipt"], "binding_withdrawn")
        if row["completed_at"] is not None and row["activity_id"] is None:
            return 200, {"receipt": result["receipt"], "deduped": True, "state": "refused"}
        try:
            dispatch_receipt(base, row)
        except AgentUnavailable:
            return _refuse(base, result["receipt"], "agent_unavailable")
        except PermissionError:
            return _refuse(base, result["receipt"], "binding_withdrawn")
        return 202, {"receipt": result["receipt"], "deduped": not result["accepted"],
                     "state": "deferred" if config["paused"] else "admitted"}
    except OverflowError:
        return 429, {"error": "rate_limited"}
    except ValueError as exc:
        if shared:
            return 200, {"state": "refused", "error": str(exc)}
        return 400, {"error": str(exc)}
    except (sqlite3.Error, OSError, RuntimeError):
        return 503, {"error": "inbound_admission_unavailable"}
