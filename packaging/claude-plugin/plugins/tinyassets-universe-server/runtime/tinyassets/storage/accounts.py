"""Accounts bounded context — user accounts, auth, sessions, capabilities.

Third R7 commit target (after __init__.py scaffolding). Owns the
``user_accounts`` / ``user_sessions`` / ``capability_grants`` tables
and the 10 functions that manage them.

Schema CREATE TABLE statements for these three tables remain in
``tinyassets.storage.__init__.initialize_author_server()`` until the R7
split completes; the split moves behavior (functions) before it moves
state (schema) to keep each commit reviewable.

TODO(R7): the in-function ``from tinyassets.daemon_server import
initialize_author_server`` imports are a lazy-import workaround for the
circular dep (storage→daemon_server→storage) that exists only while the
split is in flight. Remove once ``initialize_author_server`` migrates to
``tinyassets/storage/__init__.py`` alongside the schema.
"""

from __future__ import annotations

import hashlib
import math
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from tinyassets.auth.provider import PermissionContext, PermissionScope, resolve_permission
from tinyassets.storage import (
    DEFAULT_USER_CAPABILITIES,
    SESSION_PREFIX,
    _connect,
    _json_dumps,
    _json_loads,
    _now,
    _slugify,
)

PRIORITY_REQUEST_CAPABILITY = "submit_priority_request"


class CapabilityGrantAuthorizationError(PermissionError):
    """An issuer lacks exact-universe capability-administration authority."""


_CAPABILITY_GRANTS_V2_SCHEMA = """
CREATE TABLE capability_grants (
    user_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '*',
    granted_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL,
    revoked_at REAL,
    generation INTEGER NOT NULL DEFAULT 1 CHECK (generation >= 1),
    PRIMARY KEY(user_id, capability, scope, generation),
    CHECK (expires_at IS NULL OR expires_at > created_at),
    CHECK (revoked_at IS NULL OR revoked_at >= created_at),
    FOREIGN KEY(user_id) REFERENCES user_accounts(user_id) ON DELETE CASCADE
)
"""


def migrate_capability_grants_schema(conn: sqlite3.Connection) -> None:
    """Upgrade legacy grants to immutable, generation-preserving history."""

    columns = {
        str(row["name"]): row
        for row in conn.execute(
            "PRAGMA table_info(capability_grants)"
        ).fetchall()
    }
    expected_pk = {
        "user_id": 1,
        "capability": 2,
        "scope": 3,
        "generation": 4,
    }
    is_current = (
        {"expires_at", "revoked_at", "generation"} <= set(columns)
        and all(
            int(columns[name]["pk"]) == position
            for name, position in expected_pk.items()
        )
    )
    if is_current:
        _create_capability_grant_indexes(conn)
        return

    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "ALTER TABLE capability_grants "
            "RENAME TO capability_grants_legacy"
        )
        conn.execute(_CAPABILITY_GRANTS_V2_SCHEMA)
        conn.execute(
            """
            INSERT INTO capability_grants (
                user_id, capability, scope, granted_by, created_at,
                expires_at, revoked_at, generation
            )
            SELECT
                user_id, capability, scope, granted_by, created_at,
                NULL, NULL, 1
            FROM capability_grants_legacy
            """
        )
        conn.execute("DROP TABLE capability_grants_legacy")
        _create_capability_grant_indexes(conn)
        if owns_transaction:
            conn.commit()
    except Exception:
        if owns_transaction:
            conn.rollback()
        raise


def _create_capability_grant_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_capability_grants_active
        ON capability_grants (
            user_id, capability, scope, revoked_at, expires_at, created_at
        )
        """
    )


def _account_id_for_username(username: str) -> str:
    return f"user::{_slugify(username, 'user')}"


def ensure_host_account(base_path: str | Path, username: str) -> dict[str, Any]:
    from tinyassets.storage import ALL_CAPABILITIES

    return create_or_update_account(
        base_path,
        username=username,
        display_name=username,
        capabilities=ALL_CAPABILITIES,
        metadata={"operator_managed": True},
    )


def create_or_update_account(
    base_path: str | Path,
    *,
    username: str,
    display_name: str | None = None,
    capabilities: list[str] | tuple[str, ...] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from tinyassets.daemon_server import initialize_author_server

    normalized_capabilities = _ordinary_capabilities(capabilities or ())
    initialize_author_server(base_path)
    now = _now()
    user_id = _account_id_for_username(username)
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO user_accounts (
                user_id, username, display_name, is_active,
                created_at, updated_at, metadata_json
            ) VALUES (?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                display_name=excluded.display_name,
                is_active=1,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                user_id,
                username,
                display_name or username,
                now,
                now,
                _json_dumps(metadata or {}),
            ),
        )
    if normalized_capabilities:
        grant_capabilities(
            base_path,
            user_id=user_id,
            capabilities=list(normalized_capabilities),
            granted_by=user_id,
        )
    return get_account(base_path, user_id=user_id) or {
        "user_id": user_id,
        "username": username,
        "display_name": display_name or username,
        "capabilities": list(normalized_capabilities),
    }


def get_account(
    base_path: str | Path,
    *,
    user_id: str | None = None,
    username: str | None = None,
) -> dict[str, Any] | None:
    from tinyassets.daemon_server import initialize_author_server

    if not user_id and not username:
        return None
    initialize_author_server(base_path)
    query = (
        "SELECT * FROM user_accounts WHERE user_id = ?"
        if user_id else
        "SELECT * FROM user_accounts WHERE username = ? COLLATE NOCASE"
    )
    value = user_id or username
    with _connect(base_path) as conn:
        row = conn.execute(query, (value,)).fetchone()
    if row is None:
        return None
    account = dict(row)
    account.pop("is" + "_host", None)
    account["is_active"] = bool(account["is_active"])
    account["metadata"] = _json_loads(account.pop("metadata_json", None), {})
    account["capabilities"] = list_capabilities(
        base_path,
        user_id=account["user_id"],
    )
    return account


def list_accounts(base_path: str | Path) -> list[dict[str, Any]]:
    from tinyassets.daemon_server import initialize_author_server

    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        rows = conn.execute(
            "SELECT * FROM user_accounts ORDER BY created_at, user_id"
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        account = dict(row)
        account.pop("is" + "_host", None)
        account["is_active"] = bool(account["is_active"])
        account["metadata"] = _json_loads(account.pop("metadata_json", None), {})
        account["capabilities"] = list_capabilities(
            base_path,
            user_id=account["user_id"],
        )
        result.append(account)
    return result


def list_capabilities(
    base_path: str | Path,
    *,
    user_id: str,
    universe_id: str | None = None,
    evaluated_at: float | None = None,
) -> list[str]:
    from tinyassets.daemon_server import initialize_author_server

    initialize_author_server(base_path)
    scopes = ["*"]
    if universe_id:
        scopes.append(universe_id)
    placeholders = ", ".join("?" for _ in scopes)
    now = _finite_timestamp(
        _now() if evaluated_at is None else evaluated_at,
        "evaluated_at",
    )
    with _connect(base_path) as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT capability
            FROM capability_grants
            WHERE user_id = ? AND scope IN ({placeholders})
              AND created_at <= ?
              AND (expires_at IS NULL OR ? < expires_at)
              AND (revoked_at IS NULL OR ? < revoked_at)
            ORDER BY capability
            """,
            (user_id, *scopes, now, now, now),
        ).fetchall()
    return [str(row["capability"]) for row in rows]


def grant_capabilities(
    base_path: str | Path,
    *,
    user_id: str,
    capabilities: list[str],
    granted_by: str,
    universe_id: str | None = None,
) -> None:
    from tinyassets.daemon_server import initialize_author_server

    normalized = _ordinary_capabilities(capabilities)
    if not normalized:
        return
    from tinyassets.principals import named_principal

    subject = named_principal(user_id)
    if not subject:
        raise ValueError("capability grant requires an authenticated subject")
    initialize_author_server(base_path)
    scope = universe_id or "*"
    with _connect(base_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        now = float(_now())
        _ensure_capability_subject_account(
            conn,
            subject,
            created_at=now,
        )
        for capability in normalized:
            active = _active_grant_row(
                conn,
                user_id=subject,
                capability=capability,
                scope=scope,
                evaluated_at=now,
            )
            if active is not None:
                continue
            generation = _next_grant_generation(
                conn,
                user_id=subject,
                capability=capability,
                scope=scope,
            )
            conn.execute(
                """
                INSERT INTO capability_grants (
                    user_id, capability, scope, granted_by, created_at,
                    expires_at, revoked_at, generation
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    subject,
                    capability,
                    scope,
                    granted_by,
                    now,
                    generation,
                ),
            )


def issue_priority_grant(
    base_path: str | Path,
    *,
    subject_id: str,
    universe_id: str,
    issuer_id: str,
    expires_at: float | None = None,
) -> dict[str, Any]:
    """Issue one exact-universe operator-priority generation.

    Authority is read from the same SQLite transaction that writes the grant.
    No host/environment/caller-supplied authorization shortcut is accepted.
    """

    from tinyassets.daemon_server import initialize_author_server

    subject, universe, issuer = _priority_grant_identity(
        subject_id=subject_id,
        universe_id=universe_id,
        issuer_id=issuer_id,
    )
    expiry = (
        None
        if expires_at is None
        else _finite_timestamp(expires_at, "expires_at")
    )

    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        now = _finite_timestamp(_now(), "server_time")
        if expiry is not None and expiry <= now:
            raise ValueError("expires_at must be strictly after issued_at")
        _require_priority_grant_administrator(
            conn,
            issuer_id=issuer,
            universe_id=universe,
            evaluated_at=now,
        )
        _ensure_capability_subject_account(
            conn,
            subject,
            created_at=now,
        )
        latest = _latest_grant_row(
            conn,
            user_id=subject,
            capability=PRIORITY_REQUEST_CAPABILITY,
            scope=universe,
        )
        if latest is not None and now < float(latest["created_at"]):
            raise ValueError(
                "issued_at cannot predate an existing grant generation"
            )
        existing = _active_grant_row(
            conn,
            user_id=subject,
            capability=PRIORITY_REQUEST_CAPABILITY,
            scope=universe,
            evaluated_at=now,
        )
        if existing is not None:
            result = _grant_row(existing)
            if result["expires_at"] != expiry:
                raise ValueError(
                    "active priority grant already exists with "
                    "different expiry"
                )
            return result
        generation = _next_grant_generation(
            conn,
            user_id=subject,
            capability=PRIORITY_REQUEST_CAPABILITY,
            scope=universe,
        )
        conn.execute(
            """
            INSERT INTO capability_grants (
                user_id, capability, scope, granted_by, created_at,
                expires_at, revoked_at, generation
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                subject,
                PRIORITY_REQUEST_CAPABILITY,
                universe,
                issuer,
                now,
                expiry,
                generation,
            ),
        )
        row = conn.execute(
            """
            SELECT *
            FROM capability_grants
            WHERE user_id = ? AND capability = ? AND scope = ?
              AND generation = ?
            """,
            (
                subject,
                PRIORITY_REQUEST_CAPABILITY,
                universe,
                generation,
            ),
        ).fetchone()
        return _grant_row(row)


def revoke_priority_grant(
    base_path: str | Path,
    *,
    subject_id: str,
    universe_id: str,
    issuer_id: str,
) -> dict[str, Any] | None:
    """Prospectively revoke the latest generation; repeated calls are stable."""

    from tinyassets.daemon_server import initialize_author_server

    subject, universe, issuer = _priority_grant_identity(
        subject_id=subject_id,
        universe_id=universe_id,
        issuer_id=issuer_id,
    )
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        now = _finite_timestamp(_now(), "server_time")
        _require_priority_grant_administrator(
            conn,
            issuer_id=issuer,
            universe_id=universe,
            evaluated_at=now,
        )
        row = _latest_grant_row(
            conn,
            user_id=subject,
            capability=PRIORITY_REQUEST_CAPABILITY,
            scope=universe,
        )
        if row is None:
            return None
        if row["revoked_at"] is None:
            if now < float(row["created_at"]):
                raise ValueError("revoked_at cannot predate grant issuance")
            conn.execute(
                """
                UPDATE capability_grants
                SET revoked_at = ?
                WHERE user_id = ? AND capability = ? AND scope = ?
                  AND generation = ? AND revoked_at IS NULL
                """,
                (
                    now,
                    subject,
                    PRIORITY_REQUEST_CAPABILITY,
                    universe,
                    row["generation"],
                ),
            )
            row = conn.execute(
                """
                SELECT *
                FROM capability_grants
                WHERE user_id = ? AND capability = ? AND scope = ?
                  AND generation = ?
                """,
                (
                    subject,
                    PRIORITY_REQUEST_CAPABILITY,
                    universe,
                    row["generation"],
                ),
            ).fetchone()
        return _grant_row(row)


def get_active_priority_grant(
    base_path: str | Path,
    *,
    subject_id: str,
    universe_id: str,
    evaluated_at: float | None = None,
) -> dict[str, Any] | None:
    from tinyassets.daemon_server import initialize_author_server

    subject, universe, _ = _priority_grant_identity(
        subject_id=subject_id,
        universe_id=universe_id,
        issuer_id="read",
    )
    now = _finite_timestamp(
        _now() if evaluated_at is None else evaluated_at,
        "evaluated_at",
    )
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        row = _active_grant_row(
            conn,
            user_id=subject,
            capability=PRIORITY_REQUEST_CAPABILITY,
            scope=universe,
            evaluated_at=now,
        )
    return _grant_row(row) if row is not None else None


def list_capability_grant_history(
    base_path: str | Path,
    *,
    subject_id: str,
    universe_id: str | None = None,
    capability: str | None = None,
) -> list[dict[str, Any]]:
    from tinyassets.daemon_server import initialize_author_server

    initialize_author_server(base_path)
    clauses = ["user_id = ?"]
    params: list[Any] = [str(subject_id or "").strip()]
    if universe_id is not None:
        clauses.append("scope = ?")
        params.append(str(universe_id).strip())
    if capability is not None:
        clauses.append("capability = ?")
        params.append(str(capability).strip())
    with _connect(base_path) as conn:
        rows = conn.execute(
            "SELECT * FROM capability_grants WHERE "
            + " AND ".join(clauses)
            + " ORDER BY capability, scope, generation",
            params,
        ).fetchall()
    return [_grant_row(row) for row in rows]


def active_priority_grant_from_connection(
    conn: sqlite3.Connection,
    *,
    subject_id: str,
    universe_id: str,
    evaluated_at: float,
) -> dict[str, Any] | None:
    """Re-read priority authority inside an admission transaction."""

    subject, universe, _ = _priority_grant_identity(
        subject_id=subject_id,
        universe_id=universe_id,
        issuer_id="read",
    )
    row = _active_grant_row(
        conn,
        user_id=subject,
        capability=PRIORITY_REQUEST_CAPABILITY,
        scope=universe,
        evaluated_at=_finite_timestamp(
            evaluated_at,
            "evaluated_at",
        ),
    )
    return _grant_row(row) if row is not None else None


def _require_priority_grant_administrator(
    conn: sqlite3.Connection,
    *,
    issuer_id: str,
    universe_id: str,
    evaluated_at: float,
) -> None:
    admin = conn.execute(
        """
        SELECT 1
        FROM universe_acl
        WHERE universe_id = ? AND actor_id = ? AND permission = 'admin'
        """,
        (universe_id, issuer_id),
    ).fetchone()
    exact_capability = _active_grant_row(
        conn,
        user_id=issuer_id,
        capability="grant_capabilities",
        scope=universe_id,
        evaluated_at=evaluated_at,
    )
    if admin is None or exact_capability is None:
        raise CapabilityGrantAuthorizationError(
            "exact-universe admin and grant_capabilities authority required"
        )


def _active_grant_row(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    capability: str,
    scope: str,
    evaluated_at: float,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM capability_grants
        WHERE user_id = ? AND capability = ? AND scope = ?
          AND created_at <= ?
          AND (expires_at IS NULL OR ? < expires_at)
          AND (revoked_at IS NULL OR ? < revoked_at)
        ORDER BY generation DESC
        LIMIT 1
        """,
        (
            user_id,
            capability,
            scope,
            evaluated_at,
            evaluated_at,
            evaluated_at,
        ),
    ).fetchone()


def _latest_grant_row(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    capability: str,
    scope: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM capability_grants
        WHERE user_id = ? AND capability = ? AND scope = ?
        ORDER BY generation DESC
        LIMIT 1
        """,
        (user_id, capability, scope),
    ).fetchone()


def _next_grant_generation(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    capability: str,
    scope: str,
) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(generation), 0) + 1
        FROM capability_grants
        WHERE user_id = ? AND capability = ? AND scope = ?
        """,
        (user_id, capability, scope),
    ).fetchone()
    return int(row[0])


def _ensure_capability_subject_account(
    conn: sqlite3.Connection,
    user_id: str,
    *,
    created_at: float,
) -> None:
    """Provision an opaque authenticated subject without granting authority."""

    from tinyassets.principals import has_named_principal

    if not has_named_principal(user_id):
        raise ValueError("capability subject must be authenticated")
    row = conn.execute(
        "SELECT 1 FROM user_accounts WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is not None:
        return
    subject_digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    username = f"identity::{subject_digest}"
    conn.execute(
        """
        INSERT INTO user_accounts (
            user_id, username, display_name, is_active,
            created_at, updated_at, metadata_json
        ) VALUES (?, ?, 'Authenticated subject', 1, ?, ?, ?)
        """,
        (
            user_id,
            username,
            created_at,
            created_at,
            _json_dumps({"opaque_request_subject": True}),
        ),
    )


def _ordinary_capabilities(
    capabilities: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    normalized = tuple(
        dict.fromkeys(
            str(capability or "").strip()
            for capability in capabilities
            if str(capability or "").strip()
        )
    )
    if PRIORITY_REQUEST_CAPABILITY in normalized:
        raise ValueError(
            f"{PRIORITY_REQUEST_CAPABILITY} must be issued through the "
            "trusted priority-grant service"
        )
    return normalized


def _priority_grant_identity(
    *,
    subject_id: str,
    universe_id: str,
    issuer_id: str,
) -> tuple[str, str, str]:
    subject = str(subject_id or "").strip()
    universe = str(universe_id or "").strip()
    issuer = str(issuer_id or "").strip()
    if not subject or not issuer:
        raise ValueError("priority grant requires subject and issuer")
    if not universe or universe == "*":
        raise ValueError("priority grant requires an exact command center")
    return subject, universe, issuer


def _grant_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "user_id": str(row["user_id"]),
        "capability": str(row["capability"]),
        "scope": str(row["scope"]),
        "granted_by": str(row["granted_by"]),
        "created_at": float(row["created_at"]),
        "expires_at": (
            None
            if row["expires_at"] is None
            else float(row["expires_at"])
        ),
        "revoked_at": (
            None
            if row["revoked_at"] is None
            else float(row["revoked_at"])
        ),
        "generation": int(row["generation"]),
    }


def _finite_timestamp(value: float, name: str) -> float:
    timestamp = float(value)
    if not math.isfinite(timestamp):
        raise ValueError(f"{name} must be finite")
    return timestamp


def create_session(
    base_path: str | Path,
    *,
    username: str,
    display_name: str | None = None,
    created_by: str = "system",
    capabilities: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    account = create_or_update_account(
        base_path,
        username=username,
        display_name=display_name,
        capabilities=capabilities or list(DEFAULT_USER_CAPABILITIES),
        metadata=metadata,
    )
    token = SESSION_PREFIX + secrets.token_urlsafe(24)
    now = _now()
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO user_sessions (
                session_token, user_id, created_at, last_seen, expires_at, metadata_json
            ) VALUES (?, ?, ?, ?, NULL, ?)
            """,
            (token, account["user_id"], now, now, _json_dumps(metadata or {})),
        )
    return {
        "token": token,
        "account": account,
        "created_at": now,
        "created_by": created_by,
    }


def resolve_bearer_token(
    base_path: str | Path,
    token: str,
    *,
    master_api_key: str = "",
    master_username: str = "host",
) -> dict[str, Any] | None:
    from tinyassets.daemon_server import initialize_author_server

    initialize_author_server(base_path)
    if master_api_key and token == master_api_key:
        actor = ensure_host_account(base_path, master_username)
        actor["token_type"] = "master_api_key"
        return actor

    with _connect(base_path) as conn:
        row = conn.execute(
            """
            SELECT s.session_token, s.user_id, s.expires_at, a.username, a.display_name
            FROM user_sessions AS s
            JOIN user_accounts AS a ON a.user_id = s.user_id
            WHERE s.session_token = ?
            """,
            (token,),
        ).fetchone()
        if row is None:
            return None
        from tinyassets.principals import has_named_principal

        if not has_named_principal(row["user_id"]):
            conn.execute("DELETE FROM user_sessions WHERE session_token = ?", (token,))
            return None
        expires_at = row["expires_at"]
        if expires_at is not None and float(expires_at) < _now():
            conn.execute("DELETE FROM user_sessions WHERE session_token = ?", (token,))
            return None
        conn.execute(
            "UPDATE user_sessions SET last_seen = ? WHERE session_token = ?",
            (_now(), token),
        )
    actor = get_account(base_path, user_id=str(row["user_id"]))
    if actor is None:
        return None
    actor["token_type"] = "session"
    actor["session_token"] = token
    return actor


def actor_has_capability(actor: dict[str, Any], capability: str) -> bool:
    from tinyassets.principals import named_principal

    grants = tuple(str(item) for item in actor.get("capabilities", []))
    actor_id = named_principal(actor.get("user_id") or actor.get("username"))
    verdict = resolve_permission(
        actor_id=actor_id,
        action=capability,
        grants=grants,
        scope=PermissionScope(
            universe_id=str(actor.get("universe_id", "")),
            actor_scope=str(actor.get("token_type", "user")),
        ),
        context=PermissionContext(
            actor_id=actor_id,
            presented_grants=grants,
            metadata={
                "username": actor.get("username", ""),
                "token_type": actor.get("token_type", ""),
            },
        ),
    )
    return verdict.allowed


__all__ = [
    "PRIORITY_REQUEST_CAPABILITY",
    "CapabilityGrantAuthorizationError",
    "_account_id_for_username",
    "active_priority_grant_from_connection",
    "actor_has_capability",
    "create_or_update_account",
    "create_session",
    "ensure_host_account",
    "get_account",
    "get_active_priority_grant",
    "grant_capabilities",
    "issue_priority_grant",
    "list_accounts",
    "list_capability_grant_history",
    "list_capabilities",
    "migrate_capability_grants_schema",
    "revoke_priority_grant",
    "resolve_bearer_token",
]
