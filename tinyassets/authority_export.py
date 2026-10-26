"""N10 one-shot: hand a box's old authority files to the broker, then remove them.

Before N10 a box held its owner's action rules (``POLICY_FILE``) and its
effector consents (one ``CONSENTS_FILE`` per center sidecar folder). The
broker now holds both. At box start, right after identity and before the box
serves, this sends them once as the owner's first box operation; the broker
accepts it only while that owner's import is pending, validates each entry and
counts what it drops. The files are removed only after the broker's RESULT.

Delete this module once every owner's import has settled.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

_LOG = logging.getLogger(__name__)

POLICY_FILE = ".action-policy.db"
CONSENTS_FILE = ".effector_consents.db"


def _read(path: Path, query: str) -> list:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return db.execute(query).fetchall()


def export(root: Path, owner: str) -> dict | None:
    """Import this box's authority files once; None when there is nothing to send.

    KNOWN BLOCKER (N10 PR1, reported): a box's broker channel cannot open
    while the broker's wake of this box holds the owner lock, so a call made
    here, before the box answers its readiness probe, waits on that lock.
    """
    from tinyassets.boxes.layout import UNIVERSE_SIDECARS_DIR
    from tinyassets.broker.client import BrokerRefused, owner_policy_operation

    root = Path(root)
    policy = root / POLICY_FILE
    sources = [policy] if policy.is_file() else []
    sources += sorted((root / UNIVERSE_SIDECARS_DIR).glob("*/" + CONSENTS_FILE))
    if not sources:
        return None
    entries, consents, readable = [], [], []
    for path in sources:
        try:
            if path == policy:
                rows = _read(path, "SELECT document FROM policy WHERE singleton=1")
                entries += json.loads(rows[0][0]).get("entries", []) if rows else []
            else:
                consents += [{"sink": sink, "destination": destination,
                              "granted_at": granted_at, "granted_by": granted_by}
                             for sink, destination, granted_at, granted_by in _read(
                                 path, "SELECT sink,destination,granted_at,granted_by "
                                       "FROM effector_consents WHERE revoked_at IS NULL")]
        except (sqlite3.Error, ValueError, TypeError, AttributeError):
            _LOG.error("ALARM: unreadable authority file kept for an operator: %s", path.name)
            continue
        readable.append(path)
    canonical = json.dumps({"entries": entries, "consents": consents}, sort_keys=True,
                           separators=(",", ":"), default=str)
    document = {"source_digest": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest(),
                "entries": entries, "consents": consents}
    try:
        result = owner_policy_operation(root, owner, "import", document=document)
    except BrokerRefused as exc:
        # Not pending: already imported, or another operation came first.
        _LOG.error("ALARM: owner authority import refused (%s); box files kept", exc)
        return None
    for path in readable:
        for suffix in ("", "-wal", "-shm", "-journal"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)
    return result
