"""Platform authority for shared-app installation metadata.

Provider exchange, credential validation and revocation are supplied by the
broker's provider client. This store contains references, never tokens. Boxes
can read only their owner's view and may request a verified retirement.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Callable


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class InstallationAuthority:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("CREATE TABLE IF NOT EXISTS oauth_states ("
                   "state_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, "
                   "session_hash TEXT NOT NULL, expires_at REAL NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS installations ("
                   "app TEXT NOT NULL, kind TEXT NOT NULL, installation_id TEXT NOT NULL, "
                   "owner TEXT NOT NULL, binding TEXT NOT NULL UNIQUE, "
                   "credential_ref TEXT NOT NULL, revision INTEGER NOT NULL, "
                   "PRIMARY KEY(app,kind,installation_id))")
        db.execute("CREATE TABLE IF NOT EXISTS revision_sequence ("
                   "id INTEGER PRIMARY KEY AUTOINCREMENT)")
        db.execute("CREATE TABLE IF NOT EXISTS shared_profiles ("
                   "binding TEXT PRIMARY KEY, profile_json TEXT NOT NULL, "
                   "secret_ref TEXT NOT NULL, revision INTEGER NOT NULL)")
        return db

    def publish_profile(self, binding: str, profile: dict, secret_ref: str) -> int:
        """Platform administration only; never exposed by the owner relay."""
        from tinyassets.broker.inbound_verification import validate_profile

        validate_profile(profile)
        if profile["method"] != "hmac-sha256" or not binding or not secret_ref:
            raise ValueError("shared app requires a named broker signing secret")
        with closing(self._connect()) as db, db:
            current = db.execute("SELECT revision FROM shared_profiles WHERE binding=?",
                                 (binding,)).fetchone()
            revision = 1 if current is None else current["revision"] + 1
            db.execute("INSERT OR REPLACE INTO shared_profiles VALUES (?,?,?,?)",
                       (binding, json.dumps(profile, sort_keys=True), secret_ref, revision))
        return revision

    def profile(self, binding: str) -> dict | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM shared_profiles WHERE binding=?",
                             (binding,)).fetchone()
        if row is None:
            return None
        return {"profile": json.loads(row["profile_json"]),
                "secret_ref": row["secret_ref"], "revision": row["revision"]}

    def begin(self, owner: str, browser_session: str, *, now: float | None = None) -> str:
        if not owner or not browser_session:
            raise ValueError("authenticated owner and browser session required")
        state = secrets.token_urlsafe(32)
        at = time.time() if now is None else now
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO oauth_states VALUES (?,?,?,?)",
                       (_digest(state), owner, _digest(browser_session), at + 600))
        return state

    def complete(self, state: str, owner: str, browser_session: str,
                 exchange: Callable[[], tuple[str, str, str]],
                 inspect_existing: Callable[[str], str], *, app: str = "shared",
                 now: float | None = None) -> dict:
        """Exchange server-side only after same-session state proof.

        ``app`` is the installation manifest's provider id. Callers that omit
        it record the shared app.

        The exchange returns (installation kind, ID, scoped credential ref).
        An existing live credential wins; indeterminate status defers. A
        refused exchange result is discarded without provider token revocation.
        """
        at = time.time() if now is None else now
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM oauth_states WHERE state_hash=?",
                                 (_digest(state),)).fetchone()
                if (row is None or row["owner"] != owner
                        or row["session_hash"] != _digest(browser_session)
                        or row["expires_at"] < at):
                    raise PermissionError("OAuth state is not in this session")
                db.execute("DELETE FROM oauth_states WHERE state_hash=?", (_digest(state),))
                db.commit()  # Single use even if the provider exchange or publication fails.
            except BaseException:
                db.rollback()
                raise
        kind, installation_id, credential_ref = exchange()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if kind not in {"workspace", "enterprise"} or not all(
                        isinstance(value, str) and 0 < len(value) <= 256
                        for value in (installation_id, credential_ref)):
                    raise ValueError("invalid provider-inspected installation")
                overlap = db.execute(
                    "SELECT kind FROM installations WHERE app=? AND installation_id=? "
                    "AND kind!=?", (app, installation_id, kind)).fetchone()
                if overlap is not None:
                    raise PermissionError("installation coverage conflict")
                old = db.execute("SELECT * FROM installations WHERE app=? AND kind=? "
                                 "AND installation_id=?", (app, kind, installation_id)).fetchone()
                if old is not None:
                    if old["owner"] == owner:
                        # A reconnect still rotates the record revision.
                        pass
                    else:
                        status = inspect_existing(old["credential_ref"])
                        if status == "live":
                            raise PermissionError("installation belongs to another owner")
                        if status != "revoked":
                            raise TimeoutError("existing installation status unknown")
                    db.execute("DELETE FROM installations WHERE app=? AND kind=? AND "
                               "installation_id=?", (app, kind, installation_id))
                revision = db.execute("INSERT INTO revision_sequence DEFAULT VALUES").lastrowid
                binding = secrets.token_urlsafe(24)
                db.execute("INSERT INTO installations VALUES (?,?,?,?,?,?,?)",
                           (app, kind, installation_id, owner, binding, credential_ref, revision))
                db.commit()
                return self.lookup(kind, installation_id, app=app)
            except BaseException:
                db.rollback()
                raise

    def lookup(self, kind: str, installation_id: str, *, app: str = "shared") -> dict | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM installations WHERE app=? AND kind=? "
                             "AND installation_id=?", (app, kind, installation_id)).fetchone()
            return dict(row) if row else None

    def for_owner(self, owner: str) -> list[dict]:
        with closing(self._connect()) as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM installations WHERE owner=? ORDER BY revision", (owner,))]

    def retire(self, owner: str, binding: str,
               inspect_existing: Callable[[str], str]) -> None:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM installations WHERE binding=? AND owner=?",
                                 (binding, owner)).fetchone()
                if row is None or inspect_existing(row["credential_ref"]) != "revoked":
                    raise PermissionError("installation retirement lacks revocation proof")
                db.execute("DELETE FROM installations WHERE binding=? AND owner=?",
                           (binding, owner))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def erase_owner(self, owner: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM installations WHERE owner=?", (owner,))
            db.execute("DELETE FROM oauth_states WHERE owner=?", (owner,))


def owner_operation(root: Path, owner: str, request: dict) -> dict:
    """The admitted box can read its metadata; custody references never leave."""
    from tinyassets.router_credentials import routing_request

    authority = InstallationAuthority(root / ".broker" / "inbound-installations.db")
    if request == {"operation": "snapshot"}:
        answer = routing_request(root, {"operation": "inbound_state", "owner": owner})
        if answer.get("op") != "ROUTING_SNAPSHOT_IS":
            raise RuntimeError("inbound routing state unavailable")
        breakers = {(row["binding"], row["revision"]): row["tripped"]
                    for row in answer["result"]["breakers"]}
        return {"installations": [
            {**{key: row[key] for key in
                ("app", "kind", "installation_id", "binding", "revision")},
             "degraded": bool(breakers.get((row["binding"], row["revision"]), False))}
            for row in authority.for_owner(owner)]}
    if (set(request) == {"operation", "binding", "revision"}
            and request.get("operation") == "ready"
            and isinstance(request.get("binding"), str) and request["binding"]
            and type(request.get("revision")) is int and request["revision"] >= 1):
        owned = [row for row in authority.for_owner(owner)
                 if row["binding"] == request["binding"]
                 and row["revision"] == request["revision"]]
        if not owned:
            raise PermissionError("installation readiness is not owner-bound")
        answer = routing_request(root, {
            "operation": "inbound_ready", "owner": owner,
            "binding": request["binding"], "revision": request["revision"]})
        if answer.get("op") != "ROUTING_SNAPSHOT_IS" or not answer.get("result", {}).get("ready"):
            raise RuntimeError("inbound routing state unavailable")
        return {"ready": True}
    raise ValueError("invalid installation operation")
