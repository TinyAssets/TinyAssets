"""Per-user API keys for the REST API (``/api``).

A key is a long-lived, revocable bearer an owner mints from the protected owner
view. It never becomes an unrestricted owner login: each key IS one outside
client in ``tinyassets.outside_authority``, holding exact
``{universe, agent, capability}`` grants. The identity a key resolves to carries
``outside_origin``, so every existing outside confinement -- resource checks,
run capture, effect admission, the kill switch -- applies to REST calls with no
second authority path.

Only a SHA-256 digest of the secret is stored. The full key is returned once, at
mint, and is never readable again.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import math
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: Key wire shape: ``ta_key_<16 hex id>_<43 urlsafe secret>``.
PREFIX = "ta_key_"
_KEY_RE = re.compile(r"^ta_key_([0-9a-f]{16})_([A-Za-z0-9_-]{43})$")
#: REST capabilities a key may hold. Each is the MCP handle the route runs.
CAPABILITIES = frozenset({"read_graph", "run_graph", "converse"})
MAX_KEYS_PER_OWNER = 25
MAX_NAME = 80
#: ``last_used_at`` is coarse: written at most once a minute per key, with a
#: short lock wait, so polling a run is a read, not a disk-synced write.
TOUCH_INTERVAL = 60.0
TOUCH_TIMEOUT = 0.25

logger = logging.getLogger(__name__)


class ApiKeyRefused(PermissionError):
    pass


class ApiKeyRateLimited(PermissionError):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("API key request rate exceeded")


def looks_like_key(token: str | None) -> bool:
    return bool(token) and token.startswith(PREFIX)


def _client(key_id: str) -> str:
    return hashlib.sha256(b"tinyassets-api-key-client\0" + key_id.encode()).hexdigest()


def _family(key_id: str) -> str:
    return hashlib.sha256(b"tinyassets-api-key-family\0" + key_id.encode()).hexdigest()


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class ApiKeyStore:
    def __init__(self, base: Path | str) -> None:
        self.path = Path(base) / ".api-keys.sqlite3"

    @contextmanager
    def db(self, *, timeout: float = 10):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=timeout)
        try:
            with conn:  # commit on success, roll back on error
                conn.row_factory = sqlite3.Row
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS api_keys (
                      key_id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                      secret_sha256 TEXT NOT NULL, created_at REAL NOT NULL,
                      last_used_at REAL, revoked_at REAL)
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS api_key_request_counts (
                      kind TEXT NOT NULL, subject TEXT NOT NULL,
                      owner TEXT NOT NULL, window_start INTEGER NOT NULL,
                      requests INTEGER NOT NULL,
                      PRIMARY KEY (kind, subject))
                """)
                yield conn
        finally:
            conn.close()

    @staticmethod
    def request_key_id(owner: str, request_ref: str) -> str:
        return hashlib.sha256(("tinyassets-key-request\0" + owner + "\0" + request_ref)
                              .encode()).hexdigest()[:16]

    def mint(self, owner: str, *, name: str, scopes: list[dict[str, str]],
             authority: Any, request_ref: str = "") -> dict[str, Any]:
        """Create a key and its exact outside grant; return the secret once."""
        if not isinstance(owner, str) or not owner:
            raise ValueError("owner required")
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME:
            raise ValueError("key name required (at most 80 characters)")
        if not isinstance(scopes, list) or not scopes:
            raise ValueError("at least one scope required")
        for scope in scopes:
            if not isinstance(scope, dict) or scope.get("capability") not in CAPABILITIES:
                raise ValueError("unknown API capability")
        key_id = self.request_key_id(owner, request_ref) if request_ref else secrets.token_hex(8)
        secret = secrets.token_urlsafe(32)
        now = time.time()
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if request_ref and conn.execute("SELECT 1 FROM api_keys WHERE key_id=?",
                                            (key_id,)).fetchone():
                # Never replay a secret or replace a revoked request's key.
                return {"key_id": key_id, "already_created": True}
            live = conn.execute("SELECT COUNT(*) FROM api_keys WHERE owner=? AND revoked_at "
                                "IS NULL", (owner,)).fetchone()[0]
            if live >= MAX_KEYS_PER_OWNER:
                raise ValueError("too many live API keys; revoke one first")
            bound = {"client": _client(key_id), "family": _family(key_id),
                     "authenticated_at": int(now)}
            authority.observe(owner, bound)
            # Raises before the row commits, so a refused grant leaves no key.
            authority.change(owner, bound["client"], expected_generation=0,
                             family=bound["family"], scopes=scopes)
            conn.execute("INSERT INTO api_keys VALUES (?,?,?,?,?,NULL,NULL)",
                         (key_id, owner, name.strip(), _digest(secret), now))
        return {"key": f"{PREFIX}{key_id}_{secret}", "key_id": key_id, "name": name.strip(),
                "created_at": now, "scopes": scopes}

    def revoke(self, owner: str, key_id: str, *, authority: Any) -> dict[str, Any]:
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT revoked_at FROM api_keys WHERE key_id=? AND owner=?",
                               (key_id, owner)).fetchone()
            if row is None:
                raise ApiKeyRefused("API key not found")
            if row["revoked_at"] is None:
                conn.execute("UPDATE api_keys SET revoked_at=? WHERE key_id=?",
                             (time.time(), key_id))
                client = _client(key_id)
                grant = next((c for c in authority.inspect(owner)["clients"]
                              if c["client"] == client), None)
                if grant is not None and not grant["revoked"]:
                    authority.change(owner, client, expected_generation=grant["generation"],
                                     family=grant["family"], scopes=[], revoke=True)
        return {"key_id": key_id, "revoked": True}

    def list(self, owner: str, *, authority: Any) -> dict[str, Any]:
        import json

        grants = {c["client"]: c for c in authority.inspect(owner)["clients"]}
        with self.db() as conn:
            rows = conn.execute("SELECT key_id,name,created_at,last_used_at,revoked_at FROM "
                                "api_keys WHERE owner=? ORDER BY created_at", (owner,)).fetchall()
        keys = []
        for row in rows:
            grant = grants.get(_client(row["key_id"]))
            keys.append({**dict(row), "scopes": json.loads(grant["scopes"]) if grant else []})
        return {"keys": keys}

    def resolve(self, token: str, *, touch: bool = True) -> tuple[str, dict[str, Any]] | None:
        """Return ``(owner, bound)`` for a live key, else None. Never raises on input."""
        match = _KEY_RE.match(token or "")
        if match is None:
            return None
        key_id, secret = match.groups()
        try:
            with self.db() as conn:
                row = conn.execute("SELECT owner,secret_sha256,revoked_at,last_used_at FROM "
                                   "api_keys WHERE key_id=?", (key_id,)).fetchone()
        except (sqlite3.Error, OSError):
            return None
        if (row is None or row["revoked_at"] is not None
                or not hmac.compare_digest(row["secret_sha256"], _digest(secret))):
            return None
        if touch:
            self._touch(key_id, row["last_used_at"])
        return row["owner"], {"client": _client(key_id), "family": _family(key_id),
                              "key_id": key_id, "last_used_at": row["last_used_at"]}

    def admit_request(self, key_id: str, owner: str, *, now: float | None = None) -> bool:
        """Atomically bound both the key and all of its owner's keys before grant lookup.

        Fixed windows need two counter rows, not one row per request. A denied
        request does not consume either allowance. SQLite serializes admission
        across server workers and restart preserves the current window.
        """
        from tinyassets.usage_policy import (
            API_KEY_RATE_WINDOW_SECONDS,
            API_KEY_REQUESTS_PER_WINDOW,
            API_OWNER_REQUESTS_PER_WINDOW,
        )

        ts = time.time() if now is None else now
        window = int(ts // API_KEY_RATE_WINDOW_SECONDS) * API_KEY_RATE_WINDOW_SECONDS
        retry_after = max(1, math.ceil(window + API_KEY_RATE_WINDOW_SECONDS - ts))
        limits = (("key", key_id, API_KEY_REQUESTS_PER_WINDOW),
                  ("owner", owner, API_OWNER_REQUESTS_PER_WINDOW))
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Account erasure and revocation may have raced the preceding read.
            # Holding the write lock prevents a deleted owner gaining a new row.
            if conn.execute(
                "SELECT 1 FROM api_keys WHERE key_id=? AND owner=? AND revoked_at IS NULL",
                (key_id, owner),
            ).fetchone() is None:
                return False
            for kind, subject, maximum in limits:
                row = conn.execute(
                    "SELECT window_start, requests FROM api_key_request_counts "
                    "WHERE kind=? AND subject=?", (kind, subject),
                ).fetchone()
                if row is not None and row["window_start"] == window and row["requests"] >= maximum:
                    raise ApiKeyRateLimited(retry_after)
            for kind, subject, _ in limits:
                conn.execute(
                    "INSERT INTO api_key_request_counts "
                    "(kind, subject, owner, window_start, requests) VALUES (?,?,?,?,1) "
                    "ON CONFLICT(kind, subject) DO UPDATE SET "
                    "owner=excluded.owner, window_start=excluded.window_start, "
                    "requests=CASE WHEN window_start=excluded.window_start "
                    "THEN requests+1 ELSE 1 END",
                    (kind, subject, owner, window),
                )
        return True

    def _touch(self, key_id: str, last_used_at: float | None) -> None:
        """Record use, at most once per ``TOUCH_INTERVAL`` and never at the
        expense of the request: a busy or failing write leaves ``last_used_at``
        stale, it never turns a valid key into a 401 or holds the request."""
        now = time.time()
        if last_used_at is not None and now - last_used_at < TOUCH_INTERVAL:
            return
        try:
            with self.db(timeout=TOUCH_TIMEOUT) as conn:
                conn.execute("UPDATE api_keys SET last_used_at=? WHERE key_id=?",
                             (now, key_id))
        except (sqlite3.Error, OSError):
            logger.warning("api key %s: last_used_at not recorded (store busy)", key_id)


def current_store() -> ApiKeyStore:
    from tinyassets.api.helpers import _base_path

    return ApiKeyStore(_base_path())


def resolve_identity(token: str):
    """Resolve a REST bearer to a confined outside Identity, or None.

    The outside grant is admitted here, so a revoked key, a killed switch or a
    changed grant fails at the door, and again at every downstream check.
    """
    from tinyassets.auth.provider import Identity
    from tinyassets.outside_authority import OutsideRefused
    from tinyassets.outside_authority import current_store as outside_store

    store = current_store()
    resolved = store.resolve(token, touch=False)
    if resolved is None:
        return None
    owner, bound = resolved
    key_id = bound.pop("key_id")
    last_used_at = bound.pop("last_used_at")
    if not store.admit_request(key_id, owner):
        return None
    store._touch(key_id, last_used_at)
    try:
        with outside_store().db() as conn:
            row = conn.execute("SELECT authenticated_at FROM outside_families WHERE owner=? "
                               "AND client=? AND family=?",
                               (owner, bound["client"], bound["family"])).fetchone()
        if row is None:
            return None
        bound["authenticated_at"] = row["authenticated_at"]
        bound = outside_store().admit(owner, bound)
    except (OutsideRefused, sqlite3.Error, OSError):
        return None
    return Identity(
        user_id=owner,
        username=owner,
        capabilities=["read", "write", "costly", "submit_request", "list"],
        metadata={"auth_provider": "api_key", "api_key_id": key_id, "outside_origin": bound},
    )
