"""System-browser OAuth transport; only the initiating PKCE client can redeem."""

import base64
import hashlib
import hmac
import html
import json
import re
import secrets
import time
from contextlib import contextmanager
from urllib.parse import urlencode, urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from starlette.responses import HTMLResponse, JSONResponse

from tinyassets.onboarding.owner_sessions import HEADERS, hashed, store
from tinyassets.onboarding.session_store import seal_key

HANDLE = re.compile(r"[A-Za-z0-9_-]{43}")
CLIENTS = {"android", "android-debug", "ios", "desktop"}


@contextmanager
def flows():
    with store() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS native_login_flows ("
            "ref_hash TEXT PRIMARY KEY, challenge TEXT NOT NULL, client TEXT NOT NULL, "
            "redirect_uri TEXT NOT NULL, payload BLOB, expires REAL NOT NULL)"
        )
        conn.execute("DELETE FROM native_login_flows WHERE expires<=?", (time.time(),))
        yield conn


async def begin(request):
    from tinyassets.onboarding import (
        _read_small_json,
        _same_origin_json,
        app_config,
        onboarding_enabled,
    )

    cfg = app_config()
    if not onboarding_enabled() or not cfg["configured"]:
        return JSONResponse({"error": "not_configured"}, status_code=503, headers=HEADERS)
    if not _same_origin_json(request, cfg["resource"]):
        return JSONResponse({"error": "cross_origin_rejected"}, status_code=403, headers=HEADERS)
    data = await _read_small_json(request)
    if (
        not isinstance(data, dict)
        or data.get("client") not in CLIENTS
        or not isinstance(data.get("code_challenge"), str)
        or not HANDLE.fullmatch(data["code_challenge"])
    ):
        return JSONResponse({"error": "invalid_request"}, status_code=400, headers=HEADERS)
    ref = secrets.token_urlsafe(32)
    resource = urlsplit(cfg["resource"])
    redirect = f"{resource.scheme}://{resource.netloc}/app"
    with flows() as conn:
        conn.execute(
            "INSERT INTO native_login_flows VALUES (?,?,?,?,NULL,?)",
            (hashed(ref), data["code_challenge"], data["client"], redirect, time.time() + 600),
        )
    query = urlencode(
        dict(
            response_type="code",
            client_id=cfg["client_id"],
            redirect_uri=redirect,
            scope=cfg["scopes"],
            state="na_" + ref,
            resource=cfg["resource"],
            code_challenge=data["code_challenge"],
            code_challenge_method="S256",
            prompt="login",
        )
    )
    return JSONResponse(
        {"ref": ref, "url": cfg["authorization_endpoint"] + "?" + query}, headers=HEADERS
    )


def return_url(ref, client):
    query = urlencode({"signin": ref})
    if client.startswith("android"):
        package = "io.tinyassets.app" + (".debug" if client == "android-debug" else "")
        return f"intent://auth?{query}#Intent;scheme=tinyassets;package={package};end"
    scheme = "tinyassets-desktop" if client == "desktop" else "tinyassets"
    return f"{scheme}://auth?{query}"


async def callback(request):
    ref = request.query_params.get("state", "")[3:]
    if not HANDLE.fullmatch(ref):
        return HTMLResponse("Invalid sign-in return.", status_code=400, headers=HEADERS)
    code = request.query_params.get("code", "")
    if len(code) > 8192:
        return HTMLResponse("Invalid sign-in return.", status_code=400, headers=HEADERS)
    nonce = secrets.token_bytes(12)
    payload = json.dumps({"code": code} if code else {"error": "sign_in_cancelled"}).encode()
    sealed = nonce + AESGCM(seal_key()).encrypt(nonce, payload, ref.encode())
    with flows() as conn:
        row = conn.execute(
            "UPDATE native_login_flows SET payload=? WHERE ref_hash=? AND payload IS NULL "
            "AND expires>? RETURNING client",
            (sealed, hashed(ref), time.time()),
        ).fetchone()
    if row is None:
        return HTMLResponse(
            "Sign-in expired or already returned. Start again in the app.",
            status_code=410,
            headers=HEADERS,
        )
    target = return_url(ref, row["client"])
    script_nonce = secrets.token_urlsafe(16)
    return HTMLResponse(
        '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Return to TinyAssets</title><p>Return to TinyAssets to finish signing in.</p>"
        f'<a href="{html.escape(target, quote=True)}">Open TinyAssets</a>'
        f'<script nonce="{script_nonce}">location.replace({json.dumps(target)});</script>',
        headers={
            **HEADERS,
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'nonce-{script_nonce}'; frame-ancestors 'none'"
            ),
        },
    )


def consume(ref, verifier):
    """Atomically take a ready callback. None means still waiting; no retry of a code."""
    if (
        not isinstance(ref, str)
        or not HANDLE.fullmatch(ref)
        or not isinstance(verifier, str)
        or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
    ):
        raise ValueError("invalid_native_sign_in")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    with flows() as conn:
        # flows() already holds the write transaction through its expiry sweep.
        row = conn.execute(
            "SELECT * FROM native_login_flows WHERE ref_hash=? AND expires>?",
            (hashed(ref), time.time()),
        ).fetchone()
        if row is None or not hmac.compare_digest(row["challenge"], challenge):
            raise ValueError("native_sign_in_expired")
        if row["payload"] is None:
            return None
        conn.execute("DELETE FROM native_login_flows WHERE ref_hash=?", (hashed(ref),))
        sealed = row["payload"]
        payload = json.loads(AESGCM(seal_key()).decrypt(sealed[:12], sealed[12:], ref.encode()))
    if "error" in payload:
        raise ValueError(payload["error"])
    return {"code": payload["code"], "redirect_uri": row["redirect_uri"]}
