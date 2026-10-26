"""Shared identity erasure for the door-orchestrated account deletion.

The box imports only the common deletion error; it never calls identity erasure.
Billing cancellation belongs to billing.stripe_adapter.cancel_account_billing.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

_WORKOS_USER_ID = re.compile(r"user_[A-Za-z0-9]+\Z")
_WORKOS_BASE_URL = "https://api.workos.com"


class AccountDeletionError(RuntimeError):
    """Deletion refused. The message never carries a secret."""


def workos_sessions(user_id: str, *, request=None) -> list[dict]:
    """Read every active session; incomplete pagination is not an empty result."""
    if _WORKOS_USER_ID.fullmatch(user_id) is None:
        return []
    key = os.environ.get("WORKOS_API_KEY", "").strip()
    if not key:
        raise AccountDeletionError("WorkOS session reconciliation is not configured")
    sessions, seen = [], set()
    after = ""
    for _ in range(1000):
        query = urllib.parse.urlencode({"limit": 100, **({"after": after} if after else {})})
        req = urllib.request.Request(
            f"{_WORKOS_BASE_URL}/user_management/users/{user_id}/sessions?{query}",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        try:
            with (request or urllib.request.urlopen)(req, timeout=20) as response:
                if response.status != 200:
                    raise ValueError("unexpected status")
                page = json.load(response)
            if not isinstance(page["data"], list) or not isinstance(page["list_metadata"], dict):
                raise ValueError("invalid session list")
            for session in page["data"]:
                sid = session["id"]
                if not isinstance(sid, str) or not sid.startswith("session_") or sid in seen:
                    raise ValueError("invalid session identity")
                seen.add(sid)
                sessions.append(session)
            cursor = page["list_metadata"]["after"]
            if cursor is None:
                return sessions
            if not isinstance(cursor, str) or not cursor or cursor == after or not page["data"]:
                raise ValueError("invalid session cursor")
            after = cursor
        except (OSError, ValueError, KeyError, TypeError):
            raise AccountDeletionError("WorkOS session reconciliation unavailable") from None
    raise AccountDeletionError("WorkOS session reconciliation incomplete")


def revoke_workos_sessions(user_id: str, *, request=None) -> None:
    """Revoke upstream refresh authority for every surviving restored owner."""
    for session in workos_sessions(user_id, request=request):
        req = urllib.request.Request(
            f"{_WORKOS_BASE_URL}/user_management/sessions/revoke",
            data=json.dumps({"session_id": session["id"]}).encode(),
            headers={"Authorization": f"Bearer {os.environ['WORKOS_API_KEY'].strip()}",
                     "Content-Type": "application/json"}, method="POST")
        try:
            with (request or urllib.request.urlopen)(req, timeout=20) as response:
                if response.status not in (200, 204):
                    raise AccountDeletionError("WorkOS session revocation failed")
        except (OSError, TimeoutError):
            raise AccountDeletionError("WorkOS session revocation unavailable") from None


def revocation_path(root):
    from tinyassets.owner_runtime import refuse_router_store

    refuse_router_store("workos_revocations")
    return root / ".workos-revocations.sqlite3"


def revoke_local_tokens(root, owner, *, now=None, deleted=False):
    """Durable, monotonic router authority; deletion can never be undone."""
    import sqlite3
    import time
    from contextlib import closing

    path = revocation_path(root)
    mode = "rw" if path.exists() else "rwc"
    with closing(sqlite3.connect(path.as_uri() + "?mode=" + mode, uri=True)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS revocations ("
                   "owner TEXT PRIMARY KEY, not_before REAL NOT NULL, deleted INTEGER NOT NULL)")
        db.execute("INSERT INTO revocations VALUES (?, ?, ?) ON CONFLICT(owner) DO UPDATE SET "
                   "not_before=max(not_before, excluded.not_before), "
                   "deleted=max(deleted, excluded.deleted)",
                   (owner, time.time() if now is None else now, int(deleted)))


def local_revocation(owner, root=None):
    """Read committed authority on every request; storage errors fail closed."""
    import sqlite3
    from contextlib import closing

    from tinyassets.storage import data_dir

    path = revocation_path(root if root is not None else data_dir())
    try:
        path.stat()
    except FileNotFoundError:
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return db.execute("SELECT not_before, deleted FROM revocations WHERE owner=?",
                          (owner,)).fetchone()


def workos_user_exists(user_id: str, *, request=None) -> bool:
    """Restore reconciliation through the same WorkOS transport seam as deletion."""
    if _WORKOS_USER_ID.fullmatch(user_id) is None:
        return True  # A local/non-WorkOS principal has no upstream identity.
    api_key = os.environ.get("WORKOS_API_KEY", "").strip()
    if not api_key:
        raise AccountDeletionError("WorkOS restore reconciliation is not configured")
    req = urllib.request.Request(
        f"{_WORKOS_BASE_URL}/user_management/users/{urllib.parse.quote(user_id, safe='')}",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        method="GET")
    try:
        with (request or urllib.request.urlopen)(req, timeout=20) as response:
            if response.status != 200:
                raise AccountDeletionError("WorkOS restore reconciliation failed")
            import json

            if json.load(response).get("id") != user_id:
                raise AccountDeletionError("WorkOS restore identity mismatch")
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise AccountDeletionError("WorkOS restore reconciliation failed") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AccountDeletionError("WorkOS restore reconciliation unavailable") from None


def delete_workos_user(
    user_id: str, *, request: Callable[..., Any] | None = None
) -> str:
    """Delete the WorkOS user record — which is also what ends sessions this
    process cannot enumerate: another device's refresh handle is opaque here, so
    upstream deletion, not local sweeping, is what stops it renewing. Returns
    ``deleted`` / ``not_configured`` / ``not_applicable``; raises
    :class:`AccountDeletionError` (secret-free) on an API failure."""
    api_key = os.environ.get("WORKOS_API_KEY", "").strip()
    if not api_key:
        return "not_configured"
    if _WORKOS_USER_ID.fullmatch(user_id) is None:
        return "not_applicable"
    req = urllib.request.Request(
        f"{_WORKOS_BASE_URL}/user_management/users/{urllib.parse.quote(user_id, safe='')}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "tinyassets-account-deletion/1.0",
        },
        method="DELETE",
    )
    opener = request or urllib.request.urlopen
    try:
        with opener(req, timeout=20) as response:
            status = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "deleted"  # already gone — the outcome the caller wanted
        raise AccountDeletionError(
            f"WorkOS user deletion failed (HTTP {exc.code})"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AccountDeletionError("WorkOS user deletion request failed") from None
    if status in (200, 202, 204):
        return "deleted"
    raise AccountDeletionError(f"WorkOS user deletion returned HTTP {status}")
