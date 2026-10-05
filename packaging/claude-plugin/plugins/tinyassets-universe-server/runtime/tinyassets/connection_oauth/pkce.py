"""PKCE primitives shared by every browser sign-in flow a connection uses.

Generic browser flows keep the verifier in the browser. MCP flows keep an
encrypted server-generated verifier and bind the initiating protected session.
Only the S256 challenge and hashed handles are stored in cleartext.

Two flows share this store:

* the bundled first-power acquisition preset (``onboarding.hosted_model_auth``),
  whose callback carries the handle in its path;
* a generic OAuth 2.0 authorization-code connection (``connection_oauth.flow``),
  whose callback is one fixed redirect URI and carries the handle as ``state``,
  because a standard authorization server matches ``redirect_uri`` exactly.
"""

from __future__ import annotations

import base64
import hashlib
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

FLOW_TTL_SECONDS = 600
CALLBACK_PREFIX = "/app/model-callback/"
#: The one fixed redirect URI path of the generic flow. A standard client is
#: registered for an exact redirect URI, so the handle travels as ``state``.
CONNECT_CALLBACK_PATH = CALLBACK_PREFIX + "connect"
HANDLE_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
VERIFIER_RE = re.compile(r"[A-Za-z0-9._~-]{43,128}\Z")
_DB_NAME = ".hosted-model-auth.db"


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def handle_digest(handle: str) -> str:
    return hashlib.sha256(handle.encode()).hexdigest()


def is_callback_path(path: str) -> bool:
    """A preset callback (handle in the path) or the generic fixed callback."""
    if path == CONNECT_CALLBACK_PATH:
        return True
    return path.startswith(CALLBACK_PREFIX) and bool(
        HANDLE_RE.fullmatch(path[len(CALLBACK_PREFIX):]))


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS hosted_model_flows ("
                 "handle_digest TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, "
                 "bound_home_id TEXT NOT NULL, preset_id TEXT NOT NULL, "
                 "preset_digest TEXT NOT NULL, challenge TEXT NOT NULL, "
                 "callback_origin TEXT NOT NULL, created_at REAL NOT NULL, "
                 "expires_at REAL NOT NULL)")
    # The generic flow binds to ONE pending connect request and the exact
    # action the owner was shown (its digest), plus the client the flow used.
    conn.execute("CREATE TABLE IF NOT EXISTS connection_oauth_flows ("
                 "handle_digest TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, "
                 "universe_id TEXT NOT NULL, request_id TEXT NOT NULL, "
                 "action_digest TEXT NOT NULL, challenge TEXT NOT NULL, "
                 "client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL, "
                 "created_at REAL NOT NULL, expires_at REAL NOT NULL)")

    columns = {row[1] for row in conn.execute("PRAGMA table_info(connection_oauth_flows)")}
    if "owner_session_hash" not in columns:
        conn.execute("ALTER TABLE connection_oauth_flows ADD COLUMN owner_session_hash TEXT")
    if "sealed_verifier" not in columns:
        conn.execute("ALTER TABLE connection_oauth_flows ADD COLUMN sealed_verifier BLOB")
    if "approved_owner" not in columns:
        conn.execute("ALTER TABLE connection_oauth_flows "
                     "ADD COLUMN approved_owner TEXT NOT NULL DEFAULT ''")


@contextmanager
def flows_db(base: Path | str) -> Iterator[tuple[sqlite3.Connection, float]]:
    """One IMMEDIATE transaction over the flow store, expired rows swept first."""
    conn = sqlite3.connect(Path(base) / _DB_NAME, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("BEGIN IMMEDIATE")
        _ensure_schema(conn)
        now = time.time()
        for table in ("hosted_model_flows", "connection_oauth_flows"):
            conn.execute(f"DELETE FROM {table} WHERE expires_at <= ? "  # noqa: S608 - fixed names
                         "OR created_at > ? OR expires_at <= created_at "
                         "OR expires_at - created_at > ?",
                         (now, now, FLOW_TTL_SECONDS))
        yield conn, now
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
