"""Interactive approval sessions: server PKCE, browser-bound callback, no bearer upgrade.

Native shells use this same protected view in their system browser. They may
need a second sign-in; a bridge/localStorage credential is never approval proof.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from urllib.parse import urlencode, urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

COOKIE = "__Host-ta-owner"
FLOW_COOKIE = "__Host-ta-owner-login"
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def hashed(value):
    return hashlib.sha256(value.encode()).hexdigest()


@contextmanager
def store():
    from tinyassets.api.helpers import _base_path

    path = _base_path() / ".runtime" / "owner-sessions.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS owner_sessions "
        "(session_hash TEXT PRIMARY KEY, identity_json TEXT NOT NULL, expires_at REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS owner_login_flows "
        "(state_hash TEXT PRIMARY KEY, cookie_hash TEXT NOT NULL, verifier BLOB NOT NULL, "
        "redirect_uri TEXT NOT NULL, expires_at REAL NOT NULL)"
    )
    conn.execute("DELETE FROM owner_login_flows WHERE expires_at<=?", (time.time(),))
    conn.execute("DELETE FROM owner_sessions WHERE expires_at<=?", (time.time(),))
    conn.commit()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def lookup(cookie):
    if not cookie:
        return None
    with store() as conn:
        row = conn.execute(
            "SELECT * FROM owner_sessions WHERE session_hash=? AND expires_at>?",
            (hashed(cookie), time.time()),
        ).fetchone()
        return dict(row) if row else None


def require(request, *, owner=None):
    from tinyassets.onboarding import _same_origin_json, app_config

    if not _same_origin_json(request, app_config()["resource"]):
        raise PermissionError("Same-origin approval required.")
    origin = urlsplit(app_config()["resource"])
    if request.headers.get("origin") != f"{origin.scheme}://{origin.netloc}":
        raise PermissionError("Exact protected origin required.")
    session = lookup(getattr(request, "cookies", {}).get(COOKIE, ""))
    if session is None:
        raise PermissionError("Sign in to the protected owner view to approve.")
    if owner is not None and json.loads(session["identity_json"])["user_id"] != owner:
        raise PermissionError("The signed-in account changed; sign in again.")
    return session


def revoke(cookie):
    with store() as conn:
        conn.execute("DELETE FROM owner_sessions WHERE session_hash=?", (hashed(cookie),))


def revoke_owner(owner):
    with store() as conn:
        rows = conn.execute("SELECT session_hash,identity_json FROM owner_sessions").fetchall()
        conn.executemany(
            "DELETE FROM owner_sessions WHERE session_hash=?",
            [
                (row["session_hash"],)
                for row in rows
                if json.loads(row["identity_json"])["user_id"] == owner
            ],
        )


async def begin(request):
    from starlette.responses import JSONResponse, RedirectResponse

    from tinyassets.onboarding import app_config, onboarding_enabled
    from tinyassets.onboarding.session_store import seal_key

    cfg = app_config()
    if not onboarding_enabled() or not cfg["configured"]:
        return JSONResponse({"error": "not_configured"}, status_code=503, headers=HEADERS)
    # A top-level browser navigation, never an agent fetch or embedded layout.
    if (
        request.headers.get("sec-fetch-mode") != "navigate"
        or request.headers.get("sec-fetch-dest") != "document"
    ):
        return JSONResponse(
            {"error": "interactive_sign_in_required"}, status_code=403, headers=HEADERS
        )
    app_login = getattr(request, "query_params", {}).get("app") == "1"
    state, cookie, verifier = (
        ("oa_app_" if app_login else "oa_") + secrets.token_urlsafe(32),
        secrets.token_urlsafe(32),
        secrets.token_urlsafe(48),
    )
    resource = urlsplit(cfg["resource"])
    redirect = f"{resource.scheme}://{resource.netloc}/app"
    nonce = secrets.token_bytes(12)
    sealed = nonce + AESGCM(seal_key()).encrypt(nonce, verifier.encode(), state.encode())
    with store() as conn:
        conn.execute(
            "INSERT INTO owner_login_flows VALUES (?,?,?,?,?)",
            (hashed(state), hashed(cookie), sealed, redirect, time.time() + 600),
        )
    query = urlencode(
        {
            "client_id": cfg["client_id"],
            "redirect_uri": redirect,
            "response_type": "code",
            "response_mode": "query",
            "scope": cfg["scopes"],
            "state": state,
            "resource": cfg["resource"],
            "prompt": "login",
            "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("="),
        }
    )
    response = RedirectResponse(cfg["authorization_endpoint"] + "?" + query, headers=HEADERS)
    response.set_cookie(
        FLOW_COOKIE, cookie, max_age=600, secure=True, httponly=True, samesite="lax"
    )
    return response


async def callback(request):
    import httpx
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import HTMLResponse, RedirectResponse

    from tinyassets.auth.middleware import _get_provider
    from tinyassets.onboarding import app_config
    from tinyassets.onboarding.session_store import seal_key

    def refused():
        response = HTMLResponse(
            "Sign-in could not finish. Close this window and try again.",
            status_code=403,
            headers=HEADERS,
        )
        response.delete_cookie(FLOW_COOKIE, secure=True, httponly=True, samesite="lax")
        return response

    state = request.query_params.get("state", "")
    # Purpose is part of the server-minted, persisted state, not a callback flag.
    app_login = state.startswith("oa_app_")
    cookie = request.cookies.get(FLOW_COOKIE, "")
    with store() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM owner_login_flows WHERE state_hash=? AND cookie_hash=? AND expires_at>?",
            (hashed(state), hashed(cookie), time.time()),
        ).fetchone()
        if row is None or not request.query_params.get("code"):
            return refused()
        conn.execute("DELETE FROM owner_login_flows WHERE state_hash=?", (hashed(state),))
    cfg = app_config()
    try:
        sealed = row["verifier"]
        verifier = AESGCM(seal_key()).decrypt(sealed[:12], sealed[12:], state.encode()).decode()
        async with httpx.AsyncClient(timeout=15) as client:
            result = await client.post(
                cfg["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "client_id": cfg["client_id"],
                    "redirect_uri": row["redirect_uri"],
                    "code": request.query_params["code"],
                    "code_verifier": verifier,
                    "resource": cfg["resource"],
                },
            )
        result.raise_for_status()
        tokens = result.json()
        identity = await run_in_threadpool(_get_provider().resolve_token, tokens["access_token"])
        if identity is None or not identity.user_id:
            return refused()
        refresh = tokens.get("refresh_token")
        if app_login and (not isinstance(refresh, str) or not 0 < len(refresh) <= 4096):
            return refused()
    except (httpx.HTTPError, ValueError, KeyError, InvalidTag):
        return refused()
    revoke(request.cookies.get(COOKIE, ""))
    session_cookie = secrets.token_urlsafe(32)
    with store() as conn:
        conn.execute(
            "INSERT INTO owner_sessions VALUES (?,?,?)",
            (hashed(session_cookie), json.dumps(identity.to_dict()), time.time() + 28800),
        )
    # No tokens/codes in callback output or browser scripts. Strip the query.
    from tinyassets.onboarding.inline_model_connect import RETURN_COOKIE, return_path

    destination = "/app?owner_login=1" if app_login else return_path(request, identity.user_id)
    response = RedirectResponse(destination, status_code=303,
                                headers=HEADERS)
    if app_login:
        from tinyassets.onboarding import (
            _REFRESH_COOKIE,
            _REFRESH_COOKIE_MAX_AGE,
            _REFRESH_COOKIE_PATH,
        )

        response.set_cookie(
            _REFRESH_COOKIE, refresh, max_age=_REFRESH_COOKIE_MAX_AGE,
            path=_REFRESH_COOKIE_PATH, secure=True, httponly=True, samesite="strict",
        )
    response.delete_cookie(RETURN_COOKIE, secure=True, httponly=True, samesite="lax")
    response.delete_cookie(FLOW_COOKIE, secure=True, httponly=True, samesite="lax")
    response.set_cookie(
        COOKIE, session_cookie, max_age=28800, secure=True, httponly=True, samesite="lax"
    )
    return response
