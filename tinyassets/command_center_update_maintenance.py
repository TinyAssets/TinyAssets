"""Admitted service maintenance for stored presentation grants; no timer or identity."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_STATES: dict[str, dict] = {}
_CURSORS: dict[str, tuple[tuple, str]] = {}


def _key(base) -> str:
    return str(Path(base).absolute())


def status(base) -> dict:
    with _LOCK:
        return dict(_STATES.get(_key(base), {"state": "waiting_for_service",
                                           "last_checked_at": None}))


def scheduled(base) -> None:
    """Called only after the existing admitted maintenance thread starts."""
    with _LOCK:
        _STATES[_key(base)] = {"state": "scheduled", "last_checked_at": None}


def unavailable(base) -> None:
    with _LOCK:
        _STATES[_key(base)] = {"state": "unavailable", "last_checked_at": None}


def tick(base) -> dict:
    """One bounded sweep under the same service-writer admission/reset barrier."""
    from tinyassets.platform_runtime_provenance import require_process_cloud_admission
    from tinyassets.scoped_reset import prepare_service_writer_barrier

    key = _key(base)
    barrier = None
    try:
        # Admission precedes imports which could reach storage, including schema
        # initialization. This check is cached by the serving process.
        require_process_cloud_admission(surface="command center update maintenance")
        barrier = prepare_service_writer_barrier(Path(base), timeout=0.0)
        from tinyassets.command_center_update_executor import process_policies, settle_pending

        with _LOCK:
            policy_cursor, receipt_cursor = _CURSORS.get(key, ((), ""))
        settled = settle_pending(base, after=receipt_cursor, limit=10)
        applied = process_policies(base, after=policy_cursor, limit=10)
        with _LOCK:
            _CURSORS[key] = (tuple(applied["after"]), settled["after"])
            _STATES[key] = {"state": "ready", "last_checked_at": time.time()}
    except Exception as exc:  # noqa: BLE001 - independent maintenance must remain visible
        logger.warning("Command center update maintenance blocked", exc_info=True)
        with _LOCK:
            _STATES[key] = {"state": "blocked", "last_checked_at": time.time(),
                            "reason": type(exc).__name__}
    finally:
        if barrier is not None:
            barrier.release()
    return status(base)
