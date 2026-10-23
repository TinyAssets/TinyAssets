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


def handle_verified(*, proof: dict, body: bytes, headers: dict, base: Path,
                    owner: str, now: float | None = None) -> tuple[int, dict]:
    """Called only under per-box router authentication; no token authentication here."""
    from tinyassets.control_plane.session import AgentUnavailable

    if len(body) > MAX_BODY_BYTES:
        return 413, {"error": "too_large"}
    if hashlib.sha256(body).hexdigest() != proof.get("digest"):
        return 400, {"error": "body_digest_mismatch"}
    config = inbox.configuration(base, proof.get("binding", ""))
    if config is None or not config["enabled"] or config["revision"] != proof.get("revision"):
        return 404, {"error": "binding_not_configured"}
    names = {key.lower(): value for key, value in headers.items()}
    event_id, timestamp = names.get("idempotency-key", ""), names.get("x-event-timestamp", "")
    if proof.get("shared"):
        inbox.record_shared(base, config["binding"])
        try:
            envelope = json.loads(body)
            event_id, timestamp = envelope["event_id"], str(envelope["event_time"])
        except (ValueError, KeyError, TypeError):
            return 400, {"error": "event_identity_required"}
    if not isinstance(event_id, str) or not event_id.strip() or not re.fullmatch(
            r"[0-9]{1,12}", timestamp):
        return 400, {"error": "event_identity_required"}
    try:
        result = inbox.accept(base, binding=config["binding"], revision=config["revision"],
                              event_id=event_id, event_at=int(timestamp), body=body,
                              owner=owner, agent_ref=config["agent_ref"],
                              universe=config["universe"], now=now)
        row = inbox.receipt(base, result["receipt"])
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
        return 400, {"error": str(exc)}
    except (sqlite3.Error, OSError, RuntimeError):
        return 503, {"error": "inbound_admission_unavailable"}
