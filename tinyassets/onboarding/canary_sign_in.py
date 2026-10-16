"""Canary-only sign-in: one pinned account, two proofs, no new cookie.

The production deploy gate needs its dedicated synthetic owner to sign in and
prove the product works. That owner used to drive AuthKit's hosted page with a
headless browser; AuthKit now shows a headless browser a "verify you are human"
check, and defeating one is never an option. This door replaces that step and
is deliberately useless to anybody else:

- It exists only when the host sets BOTH ``TINYASSETS_CANARY_DOOR_KEY`` (a
  server-held shared secret, 32+ bytes) and ``TINYASSETS_CANARY_USER_ID`` (the
  dedicated canary's WorkOS user id). Either unset and the route answers the
  404 a route that does not exist answers, so nothing here is even visible.
- The caller presents that key AND the canary's real password, which WorkOS
  authenticates server-side. That is one proof MORE than an ordinary sign-in.
- The account is pinned by the SERVER's configured WorkOS user id, never by
  anything in the request: the address must be that user's own (read from
  WorkOS before any password is forwarded, so the door cannot test a third
  party's credentials either), and the authenticated user id must be that user
  again. A valid key plus another account is refused before any token exists.
- What it returns is the ordinary AuthKit access token ``/mcp`` and ``/app``
  already accept, obtained from AuthKit. This server mints no credential of its
  own, and the last gate is this server's own bearer validation: the only thing
  the door can hand back is a session the connector would have accepted anyway,
  for exactly one account.

The protected approval cookie (``__Host-ta-owner``) is NOT issued here. That
cookie is the interactive, browser-bound owner session that answers approvals;
minting one from a shared key would hand out approval authority on less proof
than a real user gives, which is exactly what this door must not do. The
canary's approval row is advisory for that reason —
``scripts/core_capability_live.py``.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import time
from contextlib import contextmanager
from urllib.parse import quote

#: WorkOS User Management. The password grant is a server-to-server API call
#: carrying the environment's API key, never anything a browser could make.
AUTHENTICATE_URL = "https://api.workos.com/user_management/authenticate"
USERS_URL = "https://api.workos.com/user_management/users"
KEY_ENV = "TINYASSETS_CANARY_DOOR_KEY"
USER_ENV = "TINYASSETS_CANARY_USER_ID"
MIN_KEY_BYTES = 32
WORKOS_USER_ID = re.compile(r"user_[A-Za-z0-9]+")
ATTEMPTS = 10
WINDOW = 600.0
MAX_COUNTERS = 1000
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}

logger = logging.getLogger(__name__)


class _Refused(Exception):
    """A bounded refusal code and its status. Never carries a value."""

    def __init__(self, code: str, status: int = 403) -> None:
        super().__init__(code)
        self.code, self.status = code, status


def configured() -> tuple[str, str] | None:
    """``(door key, pinned WorkOS user id)``, or None when the door is off.

    A short key is the same as no key: a door whose only unguessable part is
    weak would be worse than the browser sign-in it replaces.
    """
    key = os.environ.get(KEY_ENV, "").strip()
    user_id = os.environ.get(USER_ENV, "").strip()
    if len(key.encode("utf-8", "ignore")) < MIN_KEY_BYTES:
        return None
    if WORKOS_USER_ID.fullmatch(user_id) is None:
        return None
    return key, user_id


def _matches(presented: str, expected: str) -> bool:
    try:
        return secrets.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
    except UnicodeEncodeError:  # lone surrogates from a crafted JSON body
        return False


@contextmanager
def _limits():
    from tinyassets.onboarding.owner_sessions import store

    with store() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS canary_door_limits "
            "(ip_hash TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires REAL NOT NULL)"
        )
        conn.execute("DELETE FROM canary_door_limits WHERE expires<=?", (time.time(),))
        yield conn


def _rate_limited(request) -> bool:
    """Count this attempt against its caller; True once the window is spent.

    The shared key is the boundary, not this counter — but a password door with
    no counter is a password door someone can grind at.
    """
    from tinyassets.onboarding.native_sign_in import client_ip
    from tinyassets.onboarding.owner_sessions import hashed

    ip_hash = hashed(client_ip(request))  # Hash callers at rest.
    with _limits() as conn:
        row = conn.execute(
            "SELECT attempts FROM canary_door_limits WHERE ip_hash=?", (ip_hash,)
        ).fetchone()
        if row and row["attempts"] >= ATTEMPTS:
            return True
        counters = conn.execute("SELECT COUNT(*) FROM canary_door_limits").fetchone()[0]
        if row is None and counters >= MAX_COUNTERS:
            return True
        conn.execute(
            "INSERT INTO canary_door_limits VALUES (?,1,?) "
            "ON CONFLICT(ip_hash) DO UPDATE SET attempts=attempts+1",
            (ip_hash, time.time() + WINDOW),
        )
    return False


async def _accepted(token: str, canary_user: str) -> bool:
    """Whether THIS server's own bearer validation accepts the token for the
    pinned canary. The gate that makes the door safe no matter what an upstream
    returns."""
    from starlette.concurrency import run_in_threadpool

    from tinyassets.auth.middleware import _get_provider

    try:
        identity = await run_in_threadpool(_get_provider().resolve_token, token)
    except Exception:  # noqa: BLE001 - an unusable provider refuses, loudly
        logger.exception("canary_sign_in could not validate the upstream session")
        return False
    return identity is not None and _matches(identity.user_id or "", canary_user)


async def _pinned_email(client, api_key: str, canary_user: str) -> str:
    """The pinned account's own address, read from WorkOS by its user id.

    Read BEFORE any password is forwarded, so a key holder can never use this
    door to test some third party's credentials: the only password this server
    ever offers upstream is one submitted for the canary's own address.
    """
    import httpx

    try:
        response = await client.get(
            f"{USERS_URL}/{quote(canary_user, safe='')}",
            headers={"Authorization": "Bearer " + api_key, "Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise _Refused("pinned_account_unknown", status=502) from None
        payload = response.json()
    except httpx.HTTPError:
        raise _Refused("password_check_unavailable", status=502) from None
    except (ValueError, TypeError):
        raise _Refused("pinned_account_unknown", status=502) from None
    email = str((payload or {}).get("email", "")).strip() if isinstance(payload, dict) else ""
    if not email:
        raise _Refused("pinned_account_unknown", status=502)
    return email


async def _resource_token(client, cfg: dict, refresh: str) -> str:
    """Trade the session's refresh token for one bound to the MCP resource —
    the same grant ``/app/token`` performs for the app's own page. Empty when
    AuthKit refuses; the caller then has nothing to offer and is refused."""
    import httpx

    try:
        response = await client.post(
            cfg["token_endpoint"],
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": cfg["client_id"],
                "resource": cfg["resource"],
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            return ""
        payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return ""
    return str(payload.get("access_token", "")).strip() if isinstance(payload, dict) else ""


async def _canary_session(
    cfg: dict, api_key: str, email: str, password: str, canary_user: str
) -> tuple[str, str]:
    """``(access token, how it was issued)`` for the pinned canary.

    The pin decides the account twice — the address the request names must be
    the pinned user's own, and the authenticated user id must be that same user
    — then WorkOS checks the password and AuthKit issues the token. The
    resource-bound token comes first because that is what the connector
    requires wherever audience binding is on (production always). The password
    grant's own session token is offered second for a deployment that runs
    without audience binding, where it IS the session ``/mcp`` accepts — and
    either way ``_accepted`` has the last word.
    """
    import httpx

    async with httpx.AsyncClient(timeout=20) as client:
        if email.strip().casefold() != (
            await _pinned_email(client, api_key, canary_user)
        ).strip().casefold():
            raise _Refused("not_the_canary_account")
        try:
            response = await client.post(
                AUTHENTICATE_URL,
                json={
                    "grant_type": "password",
                    "client_id": cfg["client_id"],
                    "client_secret": api_key,
                    "email": email,
                    "password": password,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError:
            raise _Refused("password_check_unavailable", status=502) from None
        if response.status_code >= 400:
            raise _Refused("password_refused")
        try:
            payload = response.json()
            user_id = str(payload["user"]["id"])
        except (ValueError, TypeError, KeyError, IndexError):
            raise _Refused("password_check_unavailable", status=502) from None
        if not _matches(user_id, canary_user):
            # The pin again, on what WorkOS actually authenticated.
            raise _Refused("not_the_canary_account")
        candidates = []
        refresh = str(payload.get("refresh_token", "")).strip()
        if refresh:
            candidates.append(("resource_refresh", await _resource_token(client, cfg, refresh)))
        candidates.append(("password_grant", str(payload.get("access_token", "")).strip()))
    for via, token in candidates:
        if token and await _accepted(token, canary_user):
            return token, via
    raise _Refused("no_accepted_session", status=502)


async def handle(request):
    """POST ``/app/canary-sign-in``: ``{key, email, password}`` -> an access token."""
    from starlette.responses import JSONResponse, PlainTextResponse

    from tinyassets.onboarding import _read_small_json, app_config, onboarding_enabled

    door = configured()
    if not onboarding_enabled() or door is None:
        # Unconfigured is indistinguishable from "no such route": an unkeyed
        # caller cannot learn that this deployment has a canary door at all.
        return PlainTextResponse("Not Found", status_code=404)
    key, canary_user = door
    try:
        content_type = str(request.headers.get("content-type", "")).split(";")[0].strip().lower()
        data = await _read_small_json(request, limit=4096)
        if content_type != "application/json" or data is None:
            raise _Refused("invalid_request", status=400)
        presented, email, password = (
            str(data.get(name, "")) for name in ("key", "email", "password")
        )
        if not presented or not email or not password:
            raise _Refused("invalid_request", status=400)
        if _rate_limited(request):
            raise _Refused("rate_limited", status=429)
        if not _matches(presented, key):
            # Nothing is distinguished before the shared key verifies: an
            # unkeyed caller learns only that it was refused.
            raise _Refused("refused")
        cfg = app_config()
        if not cfg.get("configured"):
            raise _Refused("not_configured", status=503)
        api_key = os.environ.get("WORKOS_API_KEY", "").strip()
        if not api_key:
            # Distinct from the above so a live run says which half is absent.
            # Only a caller that already holds the door key ever sees either.
            raise _Refused("password_check_unconfigured", status=503)
        token, via = await _canary_session(cfg, api_key, email, password, canary_user)
    except _Refused as refusal:
        # Codes only: no key, password, address, token or upstream text.
        logger.warning("canary_sign_in refused code=%s", refusal.code)
        headers = dict(HEADERS)
        if refusal.status == 429:
            headers["Retry-After"] = str(int(WINDOW))
        return JSONResponse({"error": refusal.code}, status_code=refusal.status, headers=headers)
    logger.info("canary_sign_in issued a canary session via=%s", via)
    return JSONResponse({"access_token": token, "issued_via": via}, headers=HEADERS)
