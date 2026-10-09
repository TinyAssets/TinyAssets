"""Owner-bound navigation, never approval authority or credential transport."""

import json
import re
import secrets
import time
from contextlib import contextmanager
from urllib.parse import urlencode

from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from tinyassets.onboarding.owner_sessions import COOKIE, HEADERS, hashed, lookup, require, store

RETURN_COOKIE = "__Host-ta-approval-return"
PREFIX = "/app/approval-handoff/"
HANDLE = re.compile(r"[A-Za-z0-9_-]{43}")
CLIENTS = {"android", "android-debug", "ios", "desktop", "web"}


@contextmanager
def flows():
    with store() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS approval_handoffs ("
            "handle_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL, "
            "client TEXT NOT NULL, expires REAL NOT NULL)"
        )
        conn.execute("DELETE FROM approval_handoffs WHERE expires<=?", (time.time(),))
        yield conn


def load(handle, owner=None):
    if not HANDLE.fullmatch(handle):
        return None
    with flows() as conn:
        row = conn.execute(
            "SELECT * FROM approval_handoffs WHERE handle_hash=?", (hashed(handle),)
        ).fetchone()
    return dict(row) if row and (owner is None or row["owner"] == owner) else None


def return_path(request, owner):
    handle = request.cookies.get(RETURN_COOKIE, "")
    return PREFIX + handle if load(handle, owner) else None


def completion_url(handle, client):
    query = urlencode({"completion": handle})
    if client in {"android", "android-debug"}:
        package = "io.tinyassets.app" + (".debug" if client == "android-debug" else "")
        return f"intent://auth?{query}#Intent;scheme=tinyassets;package={package};end"
    if client == "ios":
        return "tinyassets://auth?" + query
    if client == "desktop":
        return "tinyassets-desktop://auth?" + query
    return "/app"


async def handle(request):
    from tinyassets.auth.middleware import current_identity
    from tinyassets.onboarding import _app_identity_required, _read_small_json, onboarding_enabled

    if not onboarding_enabled():
        return JSONResponse({"error": "not_found"}, status_code=404, headers=HEADERS)
    denied = _app_identity_required()
    if denied is not None:
        return denied
    owner = current_identity().user_id
    if not request.query_params.get("ref"):
        data = await _read_small_json(request, limit=4096)
        if (
            not isinstance(data, dict)
            or data.get("client") not in CLIENTS
            or not isinstance(data.get("request_id"), str)
            or not 0 < len(data["request_id"]) <= 256
        ):
            return JSONResponse({"error": "invalid_request"}, status_code=400, headers=HEADERS)
        # No action, draft, bearer or approval token is persisted in this transport.
        ref = secrets.token_urlsafe(32)
        with flows() as conn:
            conn.execute(
                "INSERT INTO approval_handoffs VALUES (?,?,?,?,?)",
                (hashed(ref), owner, data["request_id"], data["client"], time.time() + 3600),
            )
        return JSONResponse({"launch_path": PREFIX + ref}, headers=HEADERS)
    try:
        require(request, owner=owner)
    except PermissionError:
        return JSONResponse(
            {"error": "interactive_approval_required"}, status_code=403, headers=HEADERS
        )
    ref = request.query_params.get("ref", "")
    row = load(ref, owner)
    if row is None:
        return JSONResponse(
            {"error": "handoff_expired_or_wrong_account"}, status_code=409, headers=HEADERS
        )
    return JSONResponse(
        {"request_id": row["request_id"], "return_url": completion_url(ref, row["client"])},
        headers=HEADERS,
    )


async def launch(request):
    from tinyassets.onboarding import onboarding_enabled

    ref = request.path_params["ref"]
    row = load(ref) if onboarding_enabled() else None
    if row is None:
        return HTMLResponse(
            "This request link expired. Open Needs you in the app and try again.",
            status_code=410,
            headers=HEADERS,
        )
    session = lookup(request.cookies.get(COOKIE, ""))
    if session is None or request.query_params.get("signin") == "1":
        response = RedirectResponse("/app/owner-sign-in?app=1", status_code=303, headers=HEADERS)
        response.set_cookie(
            RETURN_COOKIE, ref, max_age=600, secure=True, httponly=True, samesite="lax"
        )
        return response
    if json.loads(session["identity_json"])["user_id"] != row["owner"]:
        return HTMLResponse(
            "This request belongs to another account. "
            '<a href="?signin=1">Sign in to that account</a>.',
            status_code=403,
            headers=HEADERS,
        )
    return RedirectResponse(
        "/app?" + urlencode({"approval": ref}), status_code=303, headers=HEADERS
    )
