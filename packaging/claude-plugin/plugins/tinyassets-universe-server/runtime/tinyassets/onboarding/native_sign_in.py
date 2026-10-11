"""System-browser OAuth transport; redemption requires PKCE and possession of the app return."""

import base64
import hashlib
import hmac
import html
import json
import re
import secrets
import time
from contextlib import contextmanager
from ipaddress import ip_address, ip_network
from urllib.parse import urlencode, urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from starlette.responses import HTMLResponse, JSONResponse

from tinyassets.onboarding.owner_sessions import HEADERS, hashed, store
from tinyassets.onboarding.session_store import seal_key

HANDLE = re.compile(r"[A-Za-z0-9_-]{43}")
MAX_PENDING = 1000
IP_ATTEMPTS = 10
FLOW_TTL = 600
LEGACY_UNTIL = 1792800000  # 2026-10-24 UTC; Android <=1.0.6 cached pages.
CLIENTS = {"android", "android-debug", "ios", "desktop"}
# Host loopback and Docker bridges: the only hops cloudflared reaches the daemon from.
TUNNEL_PEERS = tuple(
    ip_network(net)
    for net in ("127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                "fc00::/7")
)


@contextmanager
def flows():
    with store() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS native_login_flows ("
            "ref_hash TEXT PRIMARY KEY, challenge TEXT NOT NULL, client TEXT NOT NULL, "
            "redirect_uri TEXT NOT NULL, payload BLOB, expires REAL NOT NULL)"
        )
        conn.execute("DELETE FROM native_login_flows WHERE expires<=?", (time.time(),))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS native_login_limits ("
            "ip_hash TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires REAL NOT NULL)"
        )
        conn.execute("DELETE FROM native_login_limits WHERE expires<=?", (time.time(),))
        yield conn


def client_ip(request):
    """The rate-limit identity: the edge-stamped client behind our own tunnel.

    The daemon publishes only on loopback, and cloudflared reaches it from the
    host or a Docker bridge, so only those peers are our tunnel. Cloudflare's edge
    overwrites CF-Connecting-IP; the Worker forwards it and keeps caller-chosen
    X-Forwarded-For, so XFF is never identity. Any other peer is its own identity.
    """
    peer = request.client.host if request.client else "unknown"
    try:
        trusted = any(ip_address(peer) in net for net in TUNNEL_PEERS)
    except ValueError:
        return peer
    edge = request.headers.get("cf-connecting-ip", "").strip()
    try:
        return str(ip_address(edge)) if trusted and edge else peer
    except ValueError:
        return peer


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
        or not isinstance(data.get("client"), str)
        or data.get("client") not in CLIENTS
        or not isinstance(data.get("code_challenge"), str)
        or not HANDLE.fullmatch(data["code_challenge"])
    ):
        return JSONResponse({"error": "invalid_request"}, status_code=400, headers=HEADERS)
    ref = secrets.token_urlsafe(32)
    resource = urlsplit(cfg["resource"])
    redirect = f"{resource.scheme}://{resource.netloc}/app"
    ip_hash = hashed(client_ip(request))  # Hash IPs at rest.
    with flows() as conn:
        limit = conn.execute(
            "SELECT attempts FROM native_login_limits WHERE ip_hash=?", (ip_hash,)
        ).fetchone()
        if limit and limit["attempts"] >= IP_ATTEMPTS:
            return JSONResponse(
                {
                    "error": "native_sign_in_rate_limited",
                    "message": "Too many sign-in attempts. Try again in ten minutes.",
                },
                status_code=429,
                headers={**HEADERS, "Retry-After": "600"},
            )
        # The expiry sweep holds the write lock: count+insert is atomic across workers.
        count = conn.execute("SELECT COUNT(*) FROM native_login_flows").fetchone()[0]
        counters = conn.execute("SELECT COUNT(*) FROM native_login_limits").fetchone()[0]
        if count >= MAX_PENDING or (not limit and counters >= MAX_PENDING):
            return JSONResponse(
                {
                    "error": "native_sign_in_capacity_reached",
                    "message": "Sign-in is busy. Try again in ten minutes.",
                },
                status_code=503,
                headers={**HEADERS, "Retry-After": "600"},
            )
        conn.execute(
            "INSERT INTO native_login_limits VALUES (?,1,?) "
            "ON CONFLICT(ip_hash) DO UPDATE SET attempts=attempts+1",
            (ip_hash, time.time() + FLOW_TTL),
        )
        conn.execute(
            "INSERT INTO native_login_flows VALUES (?,?,?,?,NULL,?)",
            (hashed(ref), data["code_challenge"], data["client"], redirect, time.time() + FLOW_TTL),
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


def return_url(ref, client, return_secret):
    query = urlencode({"signin": ref, "return_secret": return_secret})
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
    return_secret = secrets.token_urlsafe(32)
    payload = json.dumps(
        {
            "return_secret": return_secret,
            **({"code": code} if code else {"error": "sign_in_cancelled"}),
        }
    ).encode()
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
    return app_return_page(return_url(ref, row["client"], return_secret))


def app_return_page(target):
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


def consume(ref, verifier, return_secret=None):
    """Atomically take a callback only with both independent secrets."""
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
        sealed = row["payload"]
        payload = json.loads(AESGCM(seal_key()).decrypt(sealed[:12], sealed[12:], ref.encode()))
        if (
            not isinstance(return_secret, str)
            or not HANDLE.fullmatch(return_secret)
            or not hmac.compare_digest(payload.get("return_secret", ""), return_secret)
        ):
            raise ValueError("invalid_native_return_secret")
        conn.execute("DELETE FROM native_login_flows WHERE ref_hash=?", (hashed(ref),))
    if "error" in payload:
        raise ValueError(payload["error"])
    return {"code": payload["code"], "redirect_uri": row["redirect_uri"]}


async def legacy_callback(request):
    """Temporary pinned return for cached Android <=1.0.6 pages, never a poll flow."""
    if time.time() >= LEGACY_UNTIL:
        return HTMLResponse(
            "Update or reopen TinyAssets, then start sign-in again.",
            status_code=410,
            headers=HEADERS,
        )
    state = request.query_params.get("state", "")
    code = request.query_params.get("code", "")
    if not re.fullmatch(r"app(?:debug)?\.[A-Za-z0-9_-]{24}", state) or not code or len(code) > 8192:
        return HTMLResponse(
            "Sign-in cancelled or invalid. Start again in the app.",
            status_code=400,
            headers=HEADERS,
        )
    package = "io.tinyassets.app" + (".debug" if state.startswith("appdebug.") else "")
    query = urlencode({"code": code, "state": state})
    return app_return_page(f"intent://auth?{query}#Intent;scheme=tinyassets;package={package};end")
