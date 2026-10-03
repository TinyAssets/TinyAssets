"""Wiki write-back external-write effector — PR-166.

Consumes a branch-declared ``external_write_packet`` and appends the result
packet back onto a same-universe wiki page. This is an explicit effect sink:
the dispatcher does not auto-publish loop output.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from tinyassets.effectors.authority import DENIED as SOUL_AUTHORITY_DENIED
from tinyassets.effectors.authority import resolve_soul_effect_authority
from tinyassets.universe_files import write_data_path

logger = logging.getLogger(__name__)

EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK = "wiki_write_back"
DESTINATION_RECONCILIATION = {
    "supported": True,
    "reason": (
        "trusted wiki destination metadata records the applied system effect"
    ),
}

_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
_DESTINATION_MARKER_DB = ".wiki_write_back_destination_markers.db"


def _parse_packet(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        packet = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped or not stripped.startswith("{"):
            return None
        try:
            packet = json.loads(stripped)
        except (TypeError, ValueError):
            return None
        if not isinstance(packet, dict):
            return None
    else:
        return None
    if packet.get("sink") != EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK:
        return None
    return packet


def _find_packet(
    *,
    output_keys: list[str],
    run_state: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    for key in output_keys or []:
        if not isinstance(key, str) or key not in run_state:
            continue
        packet = _parse_packet(run_state.get(key))
        if packet is not None:
            return key, packet
    return None, None


def _destination(packet: dict[str, Any]) -> str:
    value = packet.get("destination") or packet.get("target_page")
    if isinstance(value, str):
        return value.strip().replace("\\", "/")
    return ""


def _payload_text(packet: dict[str, Any]) -> tuple[str, str]:
    payload = packet.get("payload")
    if not isinstance(payload, dict):
        return "", "packet.payload must be a JSON object"
    body = payload.get("body") or payload.get("result_packet")
    if not isinstance(body, str) or not body.strip():
        return "", "packet.payload.body is required"
    return body.strip(), ""


def _payload_heading(packet: dict[str, Any]) -> str:
    payload = packet.get("payload")
    raw = payload.get("heading") if isinstance(payload, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return "TinyAssets result packet"
    heading = " ".join(raw.replace("#", "").split())
    return heading[:120] or "TinyAssets result packet"


def _universe_dir(base_path: str | Path | None) -> Path | None:
    if base_path is None:
        return None
    try:
        return Path(base_path)
    except (TypeError, ValueError):
        return None


def _wiki_root_for_universe(universe_dir: Path | None) -> Path | None:
    if universe_dir is None:
        return None
    return universe_dir / "wiki"


def _resolve_target_page(wiki_root: Path, destination: str) -> tuple[Path | None, str]:
    requested = destination.strip().replace("\\", "/")
    if not requested:
        return None, "destination is required"
    parts = requested.split("/")
    if (
        requested.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or len(parts) < 3
        or parts[0] not in {"pages", "drafts"}
        or not requested.endswith(".md")
    ):
        return None, (
            "destination must be an exact wiki-relative page path like "
            "pages/<category>/<slug>.md or drafts/<category>/<slug>.md"
        )
    candidate = (wiki_root / requested).resolve()
    root = wiki_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, "destination escapes the wiki root"
    if not candidate.exists() or not candidate.is_file():
        return None, f"Page not found: {requested}"
    return candidate, ""


def _check_consent(universe_dir: Path | None, destination: str) -> bool:
    if universe_dir is None or not destination:
        return False
    try:
        from tinyassets.storage.effector_consents import is_consent_active

        return is_consent_active(
            universe_dir,
            sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
            destination=destination,
        )
    except Exception:
        logger.exception("wiki write-back consent lookup crashed")
        return False


def _try_reserve(
    universe_dir: Path | None,
    *,
    idempotency_hint: str,
    run_id: str,
) -> dict[str, Any]:
    if universe_dir is None or not idempotency_hint:
        return {"status": "no_hint"}
    from tinyassets.storage.external_write_receipts import try_reserve_receipt

    return try_reserve_receipt(
        universe_dir,
        idempotency_hint=idempotency_hint,
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
        run_id=run_id or "",
    )


def _finalize_receipt(
    universe_dir: Path | None,
    *,
    idempotency_hint: str,
    evidence: dict[str, Any],
    run_id: str,
) -> bool:
    if universe_dir is None or not idempotency_hint:
        return False
    try:
        from tinyassets.storage.external_write_receipts import finalize_receipt

        return finalize_receipt(
            universe_dir,
            idempotency_hint=idempotency_hint,
            sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
            evidence=evidence,
            run_id=run_id or "",
        )
    except Exception:
        logger.exception("failed to finalize wiki write-back receipt")
        return False


def _release_reservation(
    universe_dir: Path | None,
    *,
    idempotency_hint: str,
    run_id: str,
) -> None:
    if universe_dir is None or not idempotency_hint:
        return
    try:
        from tinyassets.storage.external_write_receipts import release_reservation

        release_reservation(
            universe_dir,
            idempotency_hint=idempotency_hint,
            sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
            run_id=run_id or "",
            mark_failed=True,
        )
    except Exception:
        logger.exception("failed to release wiki write-back reservation")


def _is_lock_error(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return any(token in msg for token in ("locked", "busy", "deadlock", "timeout"))


def _render_section(*, packet: dict[str, Any], idem_hint: str) -> tuple[str, str]:
    body, error = _payload_text(packet)
    if error:
        return "", error
    if (
        "<!-- tinyassets-wiki-write-back:" in body
        or "<!-- /tinyassets-wiki-write-back:" in body
    ):
        return "", "packet.payload.body contains a reserved effect marker"
    if not _IDEMPOTENCY_RE.fullmatch(idem_hint):
        return "", "idempotency_hint contains unsupported characters"
    heading = _payload_heading(packet)
    marker = f"tinyassets-wiki-write-back:{idem_hint}"
    return (
        f"<!-- {marker} -->\n"
        f"## {heading}\n\n"
        f"{body}\n"
        f"<!-- /{marker} -->",
        "",
    )


def _destination_marker_db_path(universe_dir: Path) -> Path:
    """Trusted effect metadata, at the universe root where the tool jail never
    reaches it. It used to live inside ``wiki/``, which the universe's agent
    now writes; a copy left there is never read again."""
    return universe_dir / _DESTINATION_MARKER_DB


def _page_bytes(universe_dir: Path, target: Path) -> bytes:
    """A wiki page's exact bytes, read link-free and bounded from the
    universe's wiki root (the agent writes its own wiki)."""
    from tinyassets.universe_files import read_universe_file

    root = _wiki_root_for_universe(universe_dir).resolve()
    return read_universe_file(root, target.relative_to(root).as_posix())


def _record_destination_marker(
    *,
    universe_dir: Path,
    target: Path,
    destination: str,
    effect_key: str,
) -> None:
    """Record trusted destination metadata only after the page write lands."""
    page_sha256 = hashlib.sha256(_page_bytes(universe_dir, target)).hexdigest()
    db_path = _destination_marker_db_path(universe_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=30.0) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS applied_wiki_effects (
                effect_key  TEXT PRIMARY KEY,
                destination TEXT NOT NULL,
                page_sha256 TEXT NOT NULL,
                recorded_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO applied_wiki_effects (
                effect_key, destination, page_sha256, recorded_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(effect_key) DO NOTHING
            """,
            (effect_key, destination, page_sha256, time.time()),
        )
        row = connection.execute(
            """
            SELECT destination, page_sha256
              FROM applied_wiki_effects
             WHERE effect_key = ?
            """,
            (effect_key,),
        ).fetchone()
    if row != (destination, page_sha256):
        raise RuntimeError(
            "wiki destination marker conflicts with an existing effect"
        )


def _reconcile_destination_marker(
    *,
    universe_dir: Path,
    target: Path,
    destination: str,
    effect_key: str,
) -> dict[str, Any]:
    """Resolve stale intent from trusted metadata the payload cannot write."""
    try:
        if not target.is_file():
            return {"status": "unknown"}
        with sqlite3.connect(
            _destination_marker_db_path(universe_dir),
            timeout=30.0,
        ) as connection:
            row = connection.execute(
                """
                SELECT destination, page_sha256
                  FROM applied_wiki_effects
                 WHERE effect_key = ?
                """,
                (effect_key,),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return {"status": "unknown"}
    if row is None or row[0] != destination:
        return {"status": "unknown"}
    return {
        "status": "succeeded",
        "evidence": {
            "destination": destination,
            "path": destination,
            "destination_page_sha256": row[1],
            "reconciliation": "server_destination_marker_found",
        },
    }


def _stale_replay_must_not_fire() -> None:
    raise RuntimeError("stale wiki intent must reconcile before fire")


def _append_or_update_section(path: Path, section: str, idem_hint: str) -> dict[str, Any]:
    from tinyassets.api.helpers import _read_text
    from tinyassets.api.wiki import _append_wiki_log, _page_rel_path

    text = _read_text(path)
    old_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    marker = f"tinyassets-wiki-write-back:{idem_hint}"
    start = f"<!-- {marker} -->"
    end = f"<!-- /{marker} -->"
    start_idx = text.find(start)
    end_idx = text.find(end)
    if start_idx >= 0 and end_idx >= start_idx:
        end_idx += len(end)
        new_text = text[:start_idx] + section + text[end_idx:]
        status = "updated"
    else:
        new_text = text.rstrip() + "\n\n" + section + "\n"
        status = "written"
    new_sha = hashlib.sha256(new_text.encode("utf-8")).hexdigest()
    # Link-free: the page or a parent swapped for a link after the read above
    # refuses instead of being written through.
    write_data_path(path, new_text)
    rel = _page_rel_path(path)
    _append_wiki_log(f"wiki_write_back | {rel} | idempotency_hint={idem_hint}")
    return {
        "status": status,
        "path": rel,
        "old_sha256": old_sha,
        "new_sha256": new_sha,
        "old_total_chars": len(text),
        "new_total_chars": len(new_text),
    }


def run_wiki_write_back_effector(
    *,
    node_id: str,
    output_keys: list[str],
    run_state: dict[str, Any],
    base_path: str | Path | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    """Append/update one wiki result-packet section.

    The function never raises to the run-completion path. All refusal and
    failure states are returned as structured evidence.
    """
    matched_key, packet = _find_packet(output_keys=output_keys, run_state=run_state)
    if packet is None:
        return {
            "error": (
                f"node '{node_id}' declared effects=["
                f"{EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK}] but no output_key held "
                "a parseable wiki write-back packet"
            ),
            "error_kind": "no_matching_packet",
        }

    destination = _destination(packet)
    universe_dir = _universe_dir(base_path)
    wiki_root = _wiki_root_for_universe(universe_dir)
    from tinyassets.idempotency import resolve_effector_identity

    idem_hint = resolve_effector_identity(
        packet,
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
        universe_dir=universe_dir,
    ).active_key

    if not destination:
        return {
            "error": "packet.destination is required",
            "error_kind": "invalid_destination",
            "phase": "phase_2",
            "matched_output_key": matched_key,
        }

    authority = resolve_soul_effect_authority(
        universe_dir,
        EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
        destination,
    )
    if authority == SOUL_AUTHORITY_DENIED:
        return {
            "dry_run": True,
            "phase": "phase_2",
            "reason": "soul_authority_denied",
            "destination": destination,
            "matched_output_key": matched_key,
            "intent": packet,
        }

    if not _check_consent(universe_dir, destination):
        return {
            "dry_run": True,
            "phase": "phase_2",
            "reason": "missing_consent",
            "destination": destination,
            "matched_output_key": matched_key,
            "intent": packet,
            "hint": (
                "Effector consent grants are not exposed by the advertised "
                "handles; an operator must authorize this destination through "
                "the internal consent surface before dispatching wiki "
                "write-back effects."
            ),
        }

    if not idem_hint:
        return {
            "dry_run": True,
            "phase": "phase_2",
            "reason": "missing_idempotency_hint",
            "destination": destination,
            "matched_output_key": matched_key,
            "intent": packet,
        }

    section, render_error = _render_section(packet=packet, idem_hint=idem_hint)
    if render_error:
        return {
            "error": render_error,
            "error_kind": "invalid_payload",
            "phase": "phase_2",
            "destination": destination,
            "matched_output_key": matched_key,
        }

    if wiki_root is None:
        return {
            "error": "base_path is required for wiki write-back effects",
            "error_kind": "missing_universe_context",
            "phase": "phase_2",
            "destination": destination,
            "matched_output_key": matched_key,
        }

    from tinyassets.api.wiki import _ensure_wiki_scaffold, _scoped_wiki_root

    try:
        _ensure_wiki_scaffold(wiki_root)
    except OSError as exc:
        return {
            "error": f"Wiki scaffold failed at {wiki_root}: {exc}",
            "error_kind": "wiki_scaffold_failed",
            "phase": "phase_2",
            "destination": destination,
            "matched_output_key": matched_key,
        }

    with _scoped_wiki_root(wiki_root):
        target, destination_error = _resolve_target_page(wiki_root, destination)
        if destination_error or target is None:
            return {
                "error": destination_error or "Page not found.",
                "error_kind": "invalid_destination",
                "phase": "phase_2",
                "destination": destination,
                "matched_output_key": matched_key,
            }

        try:
            reservation = _try_reserve(
                universe_dir,
                idempotency_hint=idem_hint,
                run_id=run_id,
            )
        except sqlite3.OperationalError as exc:
            return {
                "error": (
                    "receipt store unavailable; refusing wiki write-back to "
                    f"avoid duplicate writes: {exc}"
                ),
                "error_kind": (
                    "receipt_store_locked"
                    if _is_lock_error(exc) else "receipt_store_error"
                ),
                "phase": "phase_2",
                "destination": destination,
                "idempotency_hint": idem_hint,
                "matched_output_key": matched_key,
            }

        status = reservation.get("status")
        if status == "duplicate":
            recorded = reservation.get("row") or {}
            return {
                "idempotency_dedup_hit": True,
                "phase": "phase_2",
                "destination": destination,
                "matched_output_key": matched_key,
                "evidence": recorded.get("evidence") or {},
                "recorded_run_id": recorded.get("run_id"),
                "recorded_at": recorded.get("created_at"),
                "idempotency_hint": idem_hint,
            }
        if status == "in_flight":
            held = reservation.get("row") or {}
            return {
                "dry_run": True,
                "phase": "phase_2",
                "reason": "concurrent_in_flight",
                "destination": destination,
                "idempotency_hint": idem_hint,
                "matched_output_key": matched_key,
                "held_by_run_id": held.get("run_id"),
                "reservation_created_at": held.get("created_at"),
                "intent": packet,
            }
        if status == "reconciliation_required":
            from tinyassets.effectors.outbound_boundary import (
                execute_replay_safe_effect,
            )

            reconciled = execute_replay_safe_effect(
                universe_dir=universe_dir,
                effect_key=idem_hint,
                sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
                run_id=run_id,
                invoke=_stale_replay_must_not_fire,
                reconcile=lambda effect_key: _reconcile_destination_marker(
                    universe_dir=universe_dir,
                    target=target,
                    destination=destination,
                    effect_key=effect_key,
                ),
            )
            return {
                **reconciled,
                "dry_run": reconciled.get("status") != "succeeded",
                "phase": "phase_2",
                "destination": destination,
                "idempotency_hint": idem_hint,
                "matched_output_key": matched_key,
                "intent": packet,
            }
        if status not in (
            "reserved",
            "reserved_after_failed",
        ):
            return {
                "dry_run": True,
                "phase": "phase_2",
                "reason": "reservation_unknown_state",
                "destination": destination,
                "idempotency_hint": idem_hint,
                "reservation_status": str(status),
                "matched_output_key": matched_key,
                "intent": packet,
            }

        try:
            evidence = _append_or_update_section(target, section, idem_hint)
        except Exception as exc:
            _release_reservation(
                universe_dir,
                idempotency_hint=idem_hint,
                run_id=run_id,
            )
            return {
                "error": f"wiki write-back failed: {exc}",
                "error_kind": "wiki_write_back_failed",
                "phase": "phase_2",
                "destination": destination,
                "idempotency_hint": idem_hint,
                "reservation_released": bool(idem_hint),
                "matched_output_key": matched_key,
            }

        try:
            _record_destination_marker(
                universe_dir=universe_dir,
                target=target,
                destination=destination,
                effect_key=idem_hint,
            )
        except Exception as exc:
            evidence.update({
                "phase": "phase_2",
                "destination": destination,
                "matched_output_key": matched_key,
                "idempotency_hint": idem_hint,
                "recorded_at": time.time(),
                "receipt_finalize_failed": True,
                "reconciliation_required": True,
                "destination_marker_error_type": type(exc).__name__,
            })
            return evidence

        evidence.update({
            "phase": "phase_2",
            "destination": destination,
            "matched_output_key": matched_key,
            "idempotency_hint": idem_hint,
            "recorded_at": time.time(),
        })
        if status == "reserved_after_failed":
            evidence["reservation_origin"] = status

        if not _finalize_receipt(
            universe_dir,
            idempotency_hint=idem_hint,
            evidence=evidence,
            run_id=run_id,
        ):
            evidence["receipt_finalize_failed"] = True
            evidence["reconciliation_required"] = True
        return evidence


__all__ = [
    "EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK",
    "run_wiki_write_back_effector",
]
