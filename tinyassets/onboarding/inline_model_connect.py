"""Short-lived, owner-bound popup transport for installed hosted acquisition.

The callback only deposits a sealed code. Only the authenticated originating
app can consume it through the existing hosted exchange and bootstrap path.
"""

import json
import secrets
import time
from contextlib import contextmanager

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from starlette.responses import HTMLResponse, RedirectResponse

from tinyassets.onboarding import hosted_model_auth as hosted
from tinyassets.onboarding.owner_sessions import HEADERS, hashed, store
from tinyassets.onboarding.session_store import seal_key
from tinyassets.sqlite_schema import ensure_table

RETURN_COOKIE = "__Host-ta-model-return"


def return_path(request, owner):
    """A system-browser login may return only to that same owner's flow."""
    handle = request.cookies.get(RETURN_COOKIE, "")
    if not hosted._HANDLE.fullmatch(handle):
        return "/app"
    with flows() as conn:
        row = conn.execute("SELECT 1 FROM inline_model_flows WHERE handle_hash=? "
                           "AND owner=? AND status='waiting' AND browser_hash=''",
                           (hashed(handle), owner)).fetchone()
    return hosted.CALLBACK_PREFIX + handle + "?launch=1" if row else "/app"


def discard_hosted(handle, owner):
    from tinyassets.api.helpers import _base_path
    from tinyassets.connection_oauth.pkce import handle_digest

    with hosted._flows(_base_path()) as (conn, _):
        conn.execute("DELETE FROM connection_oauth_flows WHERE handle_digest=? "
                     "AND owner_user_id=?", (handle_digest(handle), owner))
        conn.execute("DELETE FROM hosted_model_flows WHERE handle_digest=? "
                     "AND owner_user_id=?",
                     (handle_digest(handle), owner))


@contextmanager
def flows():
    with store() as conn:
        ensure_table(conn,
            "CREATE TABLE IF NOT EXISTS inline_model_flows ("
            "handle_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "sealed BLOB NOT NULL, browser_hash TEXT NOT NULL DEFAULT '', "
            "status TEXT NOT NULL, expires REAL NOT NULL)", kind="ephemeral")
        conn.execute("DELETE FROM inline_model_flows WHERE expires<=?", (time.time(),))
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        yield conn


def seal(handle, data):
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(seal_key()).encrypt(
        nonce, json.dumps(data).encode(), handle.encode())


def unseal(handle, value):
    return json.loads(AESGCM(seal_key()).decrypt(value[:12], value[12:], handle.encode()))


def begin(*, owner, preset_id, resource, owner_session=None, request_ref="", client="web"):
    if (owner_session is None
            or json.loads(owner_session.get("identity_json", "{}")).get("user_id") != owner):
        raise hosted.HostedAuthError("interactive_approval_required", 403)
    verifier = secrets.token_urlsafe(32)
    result = hosted.begin_flow(owner=owner, preset_id=preset_id,
                               challenge=hosted._challenge(verifier), public_resource=resource,
                               request_ref=request_ref)
    return stage(owner=owner, result=result, verifier=verifier,
                 owner_session=owner_session, exchange="exchange", client=client)


def stage(*, owner, result, verifier, owner_session, exchange, client="web"):
    from tinyassets.onboarding.approval_handoff import CLIENTS

    if client not in CLIENTS:
        raise ValueError("invalid return client")
    handle = result["flow"]
    with flows() as conn:
        conn.execute("INSERT INTO inline_model_flows "
                     "(handle_hash,owner,sealed,status,expires) VALUES (?,?,?,?,?)",
                     (hashed(handle), owner, seal(handle, {
                         "verifier": verifier, "authorize_url": result["authorize_url"],
                         "exchange": exchange, "client": client, "owner_session": owner_session}),
                      "waiting", time.time() + result["expires_in"]))
    return {"flow": handle, "launch_path": hosted.CALLBACK_PREFIX + handle + "?launch=1",
            "authorize_url": result["authorize_url"].split("?", 1)[0],
            "expires_in": result["expires_in"]}


def take(*, owner, handle, cancel=False):
    """Atomic consumption. Foreign callers never learn status or change a row."""
    with flows() as conn:
        row = conn.execute("SELECT * FROM inline_model_flows WHERE handle_hash=? "
                           "AND owner=?", (hashed(handle), owner)).fetchone()
        if row is None:
            raise hosted.HostedAuthError("model_connection_expired", 409)
        status = row["status"]
        if cancel and status in {"waiting", "ready"}:
            conn.execute("UPDATE inline_model_flows SET status='cancelled',sealed=? "
                         "WHERE handle_hash=?", (seal(handle, {}), hashed(handle)))
            discard_hosted(handle, owner)
            return {"status": "cancelled"}
        if status != "ready":
            return {"status": status}
        data = unseal(handle, row["sealed"])
        if not data.get("owner_session"):
            raise hosted.HostedAuthError("interactive_approval_required", 403)
        conn.execute("UPDATE inline_model_flows SET status='consumed',sealed=? "
                     "WHERE handle_hash=?", (seal(handle, {}), hashed(handle)))
        return {"status": "ready", **data}


def callback(request):
    """Public navigation, browser-bound; never exchange or grant on GET."""
    handle = request.url.path.rsplit("/", 1)[-1]
    if handle == "connect":
        states = request.query_params.getlist("state")
        if len(states) != 1 or not hosted._HANDLE.fullmatch(states[0]):
            return None
        handle = states[0]
    cookie_name = "__Host-ta-model-" + handle
    from tinyassets.onboarding.owner_sessions import COOKIE, lookup

    session = lookup(request.cookies.get(COOKIE, ""))
    with flows() as conn:
        row = conn.execute("SELECT * FROM inline_model_flows WHERE handle_hash=?",
                           (hashed(handle),)).fetchone()
        if row is None:
            return None  # The pre-existing hosted redirect flow owns this callback.
        denied = HTMLResponse("Connection could not finish. Return to chat and try again.",
                              status_code=409, headers=HEADERS)
        if row["status"] != "waiting":
            return denied
        data = unseal(handle, row["sealed"])
        if request.query_params.get("launch") == "1":
            if row["browser_hash"] or request.headers.get("sec-fetch-dest") != "document":
                return denied
            # A copied launch link must never deposit the recipient's provider
            # credential into the link creator's home. Native opens in its own
            # browser and uses the same protected owner login as approvals.
            if session is None or json.loads(session["identity_json"])["user_id"] != row["owner"]:
                response = RedirectResponse("/app/owner-sign-in", headers=HEADERS)
                response.set_cookie(RETURN_COOKIE, handle, max_age=600, secure=True,
                                    httponly=True, samesite="lax")
                return response
            cookie = secrets.token_urlsafe(32)
            conn.execute("UPDATE inline_model_flows SET browser_hash=? WHERE handle_hash=?",
                         (hashed(cookie), hashed(handle)))
            response = RedirectResponse(data["authorize_url"], headers=HEADERS)
            response.set_cookie(cookie_name, cookie, max_age=600, secure=True,
                                httponly=True, samesite="lax")
            return response
        cookie = request.cookies.get(cookie_name, "")
        if (not cookie or not secrets.compare_digest(row["browser_hash"], hashed(cookie))
                or not data.get("owner_session")):
            return denied
        codes = request.query_params.getlist("code")
        valid = (not request.query_params.get("error") and len(codes) == 1
                 and 0 < len(codes[0]) <= 2048)
        if valid:
            data["code"] = codes[0]
            data["iss"] = request.query_params.get("iss", "")
        conn.execute("UPDATE inline_model_flows SET status=?,sealed=? WHERE handle_hash=?",
                     ("ready" if valid else "cancelled", seal(handle, data if valid else {}),
                      hashed(handle)))
        if not valid:
            discard_hosted(handle, row["owner"])
        from tinyassets.onboarding.approval_handoff import completion_url
        from tinyassets.onboarding.native_sign_in import app_return_page

        target = completion_url(handle, data.get("client", "web"))
        response = (RedirectResponse(target, status_code=303, headers=HEADERS)
                    if data.get("client", "web") == "web" else app_return_page(target))
        response.delete_cookie(cookie_name, secure=True, httponly=True, samesite="lax")
        return response
