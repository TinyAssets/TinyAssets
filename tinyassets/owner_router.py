"""The shared door: authenticated identity -> installed snapshot -> Unix HTTP stream.

This module never imports an owner store. The broker
owns the cold wake step; the router never manages a process.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import anyio
import httpx
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse

from tinyassets.asgi_scope import refuse_non_http
from tinyassets.mcp_events_http import door as events_door
from tinyassets.mcp_events_http import routed as events_routed

_HOP = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
        b"te", b"trailer", b"transfer-encoding", b"upgrade"}
_TRUSTED = {b"x-owner", b"x-box-secret", b"x-owner-identity",
            b"x-tinyassets-caller-user-id"}
_INBOUND_PROOF = {b"x-slack-signature", b"x-hub-signature-256",
                  b"x-slack-request-timestamp", b"proxy-authorization"}
_SHARED_APP = {
    "/app/account/delete",
    "/app", "/app/client-error", "/app/token", "/app/native-sign-in",
    "/app/owner-sign-in", "/app/owner-sign-in/complete",
    "/app/oauth/client-metadata.json", "/app/ui-frame", "/app/sw.js",
    # Sign-in navigation: its rows live in the router's owner-sessions store.
    "/app/approval-handoff", "/app/connections",
    # Undo stays on the router: the session proof never enters the box.
    "/app/owner/restore", "/app/owner/checkpoint", "/app/owner/restore-points",
    # N10: owner policy widens only here, with the proof, on the owner channel.
    "/app/api/policy",
}
_SHARED_APP_PREFIXES = ("/app/billing/", "/app/run/", "/app/m/", "/app/model-callback/",
                        "/app/approval-handoff/", "/app/model-connect/",
                        "/app/account/deletion/")


def hook_token(path: str) -> str | None:
    prefix = "/hooks/inbound/"
    if path.startswith(prefix):
        token = path[len(prefix):]
        if token and "/" not in token:
            return token
    return None


def forwarded_path(path: str) -> bool:
    """Owner doors are removed from the shared route table, not shadowed."""
    if path.startswith("/app/"):
        return path not in _SHARED_APP and not path.startswith(_SHARED_APP_PREFIXES)
    return (path in {"/mcp", "/mcp/", "/mcp/events", "/api"}
            or path.startswith(("/api/", "/hooks/inbound/")))


@dataclass(frozen=True)
class BoxRoute:
    socket: Path
    secret: str


def resolve_box(owner: str) -> BoxRoute:
    from tinyassets.routing_snapshot import snapshot
    from tinyassets.storage import data_dir

    route = snapshot(data_dir()).route(owner)
    return BoxRoute(route.socket, route.secret)


def admit_box(owner: str) -> None:
    """Ask the broker to admit an authenticated owner with no route yet.

    A refusal (erased, recovery) answers like an unknown owner; an unreachable
    broker stays a retryable 503.
    """
    from tinyassets.broker.owner_identities import admit_owner
    from tinyassets.storage import data_dir

    try:
        admit_owner(data_dir(), principal=owner)
    except PermissionError:
        raise LookupError("owner admission refused") from None


def _headers(headers, *, private=False, proof_headers=()):
    blocked = set(_HOP)
    for name, value in headers:
        if name.lower() == b"connection":
            blocked.update(piece.strip().lower() for piece in value.split(b","))
    if private:
        blocked.update(_TRUSTED | {b"authorization", b"cookie"})
        blocked.update(name.lower() for name in proof_headers)
    return [(name, value) for name, value in headers if name.lower() not in blocked]


class OwnerRouter:
    def __init__(self, app, *, resolver=resolve_box, admitter=admit_box):
        self.app, self.resolver, self.admitter = app, resolver, admitter

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            return await refuse_non_http(scope, send)
        from tinyassets.auth.middleware import current_identity_or_none, published_box_owner
        from tinyassets.router_traffic import admits
        from tinyassets.routing_snapshot import snapshot
        from tinyassets.storage import data_dir

        try:
            snapshot(data_dir()).require_current()
        except RuntimeError:
            return await JSONResponse({"error": "routing_unavailable"}, status_code=503)(
                scope, receive, send)
        identity = current_identity_or_none()
        if not forwarded_path(scope["path"]):
            # Only immutable public app assets may bypass closed admission.
            # Anonymous token, OAuth and billing callbacks can write shared
            # authority too.
            path = scope["path"]
            probe = (scope["method"] in {"GET", "HEAD"} and (
                path in {"/app", "/app/sw.js", "/app/ui-frame",
                         "/app/oauth/client-metadata.json"}
                or path.startswith("/app/m/")))
            if not probe and not await run_in_threadpool(
                    admits, data_dir(), identity.user_id if identity else None):
                return await JSONResponse({"error": "maintenance"}, status_code=503)(
                    scope, receive, send)
            return await self.app(scope, receive, send)
        token = hook_token(scope["path"])
        if token is not None:
            return await inbound(scope, receive, send, token, self.resolver)
        if identity is None:
            return await JSONResponse({"error": "not_found"}, status_code=404)(
                scope, receive, send)
        if not await run_in_threadpool(admits, data_dir(), identity.user_id):
            return await JSONResponse({"error": "maintenance"}, status_code=503)(
                scope, receive, send)
        if scope["path"].startswith("/app/"):
            from tinyassets.onboarding.owner_sessions import COOKIE, lookup

            session = await run_in_threadpool(lookup, Request(scope).cookies.get(COOKIE, ""))
            if session is not None:
                if json.loads(session["identity_json"])["user_id"] != identity.user_id:
                    return await JSONResponse({"error": "identity_conflict"}, status_code=401)(
                        scope, receive, send)
                # Protected-view proof is shared session state; its consumer in
                # the box retains the existing exact-origin/expiry checks.
                from dataclasses import replace

                identity = replace(identity, metadata={**identity.metadata,
                                                       "router_owner_session": session})
        try:
            box_owner = await run_in_threadpool(
                published_box_owner, data_dir(), scope["path"], identity.user_id)
            try:
                route = await run_in_threadpool(self.resolver, box_owner)
            except LookupError:
                # A first sign-in admits the caller only. A published slug never
                # creates a box for an owner the directory did not already name,
                # and it never admits the visitor into the app owner's place.
                if box_owner != identity.user_id:
                    raise
                await run_in_threadpool(self.admitter, identity.user_id)
                route = await run_in_threadpool(self.resolver, identity.user_id)
        except LookupError:
            return await JSONResponse({"error": "not_found"}, status_code=404)(
                scope, receive, send)
        except (OSError, RuntimeError):
            return await _unavailable()(scope, receive, send)
        if box_owner != identity.user_id:
            await forward(scope, receive, send, identity, route, box_owner=box_owner)
        elif events_routed(scope):
            async def ask_box(document):
                return await box_result(scope, identity, route, document)

            await events_door(scope, receive, send, identity, ask_box)
        else:
            await forward(scope, receive, send, identity, route)


#: The largest JSON-RPC answer the router reads from a box for itself.
BOX_RESULT_BYTES = 65536


async def box_result(scope, identity, route: BoxRoute, document):
    """One JSON-RPC request the router composes, forwarded to the box; its result.

    A JSON-RPC error from the box is raised as the same ``EventError``; anything
    else that is not a 200 with a result fails loudly.
    """
    from tinyassets.mcp_event_webhooks import EventError

    body = json.dumps(document, separators=(",", ":")).encode()
    headers = [(name, value) for name, value in scope["headers"]
               if name.lower() != b"content-length"]
    headers.append((b"content-length", str(len(body)).encode("ascii")))
    status, chunks, size, delivered = 0, [], 0, False

    async def replay():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await anyio.sleep_forever()  # forward() cancels this watcher when the reply ends

    async def capture(message):
        nonlocal status, size
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            size += len(message.get("body", b""))
            if size <= BOX_RESULT_BYTES:  # keep reading to the end, holding no more
                chunks.append(message.get("body", b""))

    # A bounded read: a hung box becomes a 503, never a stalled subscribe.
    await forward(dict(scope, headers=headers, bounded_reply=True), replay, capture,
                  identity, route)
    if size > BOX_RESULT_BYTES:
        raise RuntimeError("owner box answer exceeds its bound")
    try:
        reply = json.loads(b"".join(chunks))
    except ValueError:
        reply = None
    error = reply.get("error") if isinstance(reply, dict) else None
    if isinstance(error, dict) and type(error.get("code")) is int:
        data = error.get("data")
        raise EventError(error["code"], str(error.get("message", "")),
                         **(data if isinstance(data, dict) else {}))
    if status == 403:
        # The box's own auth refused this caller (a revoked client, a key without
        # the capability): access, not an outage, so no retry is invited.
        raise PermissionError("owner box refused the caller")
    if status != 200 or not isinstance(reply, dict) or not isinstance(reply.get("result"), dict):
        raise RuntimeError(f"owner box answered {status}")
    return reply["result"]


def _inbound_query(scope, token: str) -> bytes:
    """POST events carry no query. A GET challenge keeps its query except capability tokens."""
    if scope.get("method") != "GET":
        return b""
    blocked = {"token", "verify_token", "hub.verify_token", "access_token"}
    raw = scope.get("query_string", b"")
    kept = [(key, value) for key, value in parse_qsl(
        raw.decode("ascii", "replace"), keep_blank_values=True)
            if key.lower() not in blocked and value != token]
    return urlencode(kept).encode()


async def inbound(scope, receive, send, token, resolver):
    """Proof -> positive box mapping -> transport; no owner-local decisions."""
    from tinyassets.auth.provider import Identity
    from tinyassets.broker.inbound_verification import MAX_BODY_BYTES, request_shared
    from tinyassets.inbound_routing import DeliveryBreakers
    from tinyassets.router_credentials import HookOwners
    from tinyassets.router_traffic import admits
    from tinyassets.routing_snapshot import snapshot
    from tinyassets.storage import data_dir

    root = data_dir()
    async def answer(status, error):
        await JSONResponse({"error": error}, status_code=status,
                           headers={"Retry-After": "5"} if status in {429, 503} else {})(
                               scope, receive, send)
    if os.environ.get("TINYASSETS_INBOUND_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
        return await answer(404, "not_found")
    if scope.get("method") not in {"GET", "POST"}:
        return await answer(405, "method_not_allowed")
    owner = await run_in_threadpool(HookOwners(root).resolve, token)
    shared = owner is None
    # Shared subscription GETs are one-time platform setup outside this door.
    if shared and scope.get("method") == "GET":
        return await answer(404, "not_found")
    body = bytearray()
    async for chunk in Request(scope, receive).stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            return await answer(413, "too_large")
    body = bytes(body)
    if shared:
        # Only named platform endpoints reach broker verification. Unknown
        # capability URLs cannot turn a broker outage into a false acceptance.
        if not await run_in_threadpool(HookOwners(root).shared_endpoint, token):
            return await answer(404, "not_found")
        try:
            proof = await run_in_threadpool(request_shared, root, token,
                                            dict(Request(scope).headers), body)
        except (OSError, RuntimeError, PermissionError, ValueError):
            return await answer(503, "verification_unavailable")
        if proof.get("proof") == "no_op":
            return await answer(200, "no_op")
        if proof.get("proof") == "setup":
            challenge = proof.get("challenge")
            if not isinstance(challenge, str) or not challenge:
                return await answer(200, "no_op")
            return await JSONResponse({"challenge": challenge})(scope, receive, send)
        if proof.get("proof") != "mapped":
            return await answer(404, "not_found")
        owner = proof["owner"]
    else:
        proof = {"binding": hashlib.sha256(token.encode()).hexdigest(), "revision": 1}
    try:
        snapshot(root).route(owner)
    except (LookupError, PermissionError):
        return await answer(200 if shared else 404, "no_op" if shared else "not_found")
    breakers = DeliveryBreakers(root)
    if shared and await run_in_threadpool(breakers.tripped, proof["binding"], proof["revision"]):
        return await answer(200, "no_op")

    async def observed(message):
        if shared and message["type"] == "http.response.start" and message["status"] in {429, 503}:
            await run_in_threadpool(breakers.failure, owner, proof["binding"], proof["revision"])
        await send(message)

    if not await run_in_threadpool(admits, root, owner):
        return await JSONResponse({"error": "maintenance"}, status_code=503,
                                  headers={"Retry-After": "5"})(scope, receive, observed)
    try:
        route = await run_in_threadpool(resolver, owner)
    except (LookupError, OSError, RuntimeError):
        return await _unavailable()(scope, receive, observed)
    identity = Identity(user_id=owner, username=owner, metadata={"inbound": {
        "binding": proof["binding"], "revision": proof["revision"],
        "digest": hashlib.sha256(body).hexdigest(), "shared": shared}})
    path = "/hooks/inbound/" + proof["binding"]
    forwarded = dict(scope, path=path, raw_path=path.encode(),
                     query_string=_inbound_query(scope, token))
    forwarded["inbound_proof_headers"] = [name.encode("ascii")
                                          for name in proof.get("proof_headers", [])]
    delivered = False
    async def replay():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()
    async def revalidate():
        snapshot(root).route(owner)
        if shared:
            current = await run_in_threadpool(request_shared, root, token,
                                              dict(Request(scope).headers), body)
            if any(current.get(key) != proof.get(key)
                   for key in ("proof", "owner", "binding", "revision")):
                raise LookupError("installation withdrawn")
        elif await run_in_threadpool(HookOwners(root).resolve, token) != owner:
            raise LookupError("binding withdrawn")
    forwarded["inbound_revalidate"] = revalidate
    await forward(forwarded, replay, observed, identity, route)


def _unavailable(*, retryable=True):
    headers = {"Cache-Control": "no-store"}
    if retryable:
        headers["Retry-After"] = "5"
    return JSONResponse({"error": "owner_box_unavailable"}, status_code=503, headers=headers)


def _public_host() -> bytes:
    resource = os.environ.get(
        "UNIVERSE_SERVER_URL",
        f"http://localhost:{os.environ.get('UNIVERSE_SERVER_PORT', '8001')}",
    )
    if os.environ.get("UNIVERSE_SERVER_AUTH", "").strip().lower() == "workos":
        resource = os.environ.get("WORKOS_MCP_RESOURCE", "").strip() or resource
    return urlsplit(resource).netloc.lower().encode("ascii")


def _forward_timeout(scope) -> httpx.Timeout:
    """Inbound replies are small JSON. A silent box must become a counted 503.

    Other owner doors stream, so they keep an unbounded read. Connect, write and
    pool limits stay the same. The inbound read budget is what turns a hung box
    into the 429/503/timeout failure the per-installation breaker counts.
    """
    if scope.get("inbound_revalidate") is not None or scope.get("bounded_reply"):
        return httpx.Timeout(connect=5, read=30, write=30, pool=5)
    return httpx.Timeout(connect=5, read=None, write=30, pool=5)


def door_headers(scope, identity, route: BoxRoute, box_owner=None):
    """Router-authenticated owner and caller. Client copies of either are dropped."""
    proof_headers = (_INBOUND_PROOF | set(scope.get("inbound_proof_headers", ()))
                     if hook_token(scope["path"]) is not None else ())
    headers = _headers(scope["headers"], private=True, proof_headers=proof_headers)
    # The tunnel rewrites Host before the router sees it. The box checks a JSON
    # request's Origin against its Host, so it must see the public host the
    # user addressed; a cross-site Origin still fails that check.
    public = _public_host()
    if public:
        headers = [(name, value) for name, value in headers if name.lower() != b"host"]
        headers.append((b"host", public))
    owner = box_owner or identity.user_id
    headers.extend([
        (b"x-owner", owner.encode("utf-8")),
        (b"x-box-secret", route.secret.encode("ascii")),
        (b"x-owner-identity", base64.urlsafe_b64encode(
            json.dumps(asdict(identity), separators=(",", ":")).encode("utf-8"))),
        (b"x-tinyassets-caller-user-id", identity.user_id.encode("utf-8")),
    ])
    return owner, headers


async def forward(scope, receive, send, identity, route: BoxRoute, *, cold=True,
                  box_owner=None):
    """One reverse proxy for every owner door, with bounded streaming and cancellation."""
    owner, headers = door_headers(scope, identity, route, box_owner)
    target = scope.get("raw_path", scope["path"].encode("utf-8"))
    if scope.get("query_string"):
        target += b"?" + scope["query_string"]
    url = httpx.URL("http://owner").copy_with(raw_path=target)
    started = False
    try:
        transport = httpx.AsyncHTTPTransport(uds=str(route.socket))
        timeout = _forward_timeout(scope)
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            async with client.stream(scope["method"], url, headers=headers,
                                     content=Request(scope, receive).stream()) as response:
                await send({"type": "http.response.start", "status": response.status_code,
                            "headers": _headers(response.headers.raw)})
                started = True
                async with anyio.create_task_group() as tasks:
                    async def disconnect():
                        while (await receive())["type"] != "http.disconnect":
                            pass
                        tasks.cancel_scope.cancel()

                    tasks.start_soon(disconnect)
                    async for chunk in response.aiter_raw():
                        await send({"type": "http.response.body", "body": chunk,
                                    "more_body": True})
                    await send({"type": "http.response.body", "body": b""})
                    tasks.cancel_scope.cancel()
    except ClientDisconnect:
        return
    except httpx.ConnectError:
        if not cold:
            return await _unavailable()(scope, receive, send)
        from tinyassets.broker.owner_identities import wake_owner
        from tinyassets.storage import data_dir

        try:
            await run_in_threadpool(wake_owner, data_dir(), principal=owner)
            if scope.get("inbound_revalidate") is not None:
                await scope["inbound_revalidate"]()
            route = resolve_box(owner)
        except (LookupError, OSError, RuntimeError):
            return await _unavailable()(scope, receive, send)
        # ConnectError occurs before HTTP headers or the request body are sent.
        # Wake replies contain no route: always select the current projection.
        if box_owner is not None:
            await forward(
                scope, receive, send, identity, route, cold=False, box_owner=owner)
        else:
            await forward(scope, receive, send, identity, route, cold=False)
    except httpx.HTTPError:
        if started:
            raise  # A truncated stream must never look like a successful response.
        await _unavailable(retryable=False)(scope, receive, send)
