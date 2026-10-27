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
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from tinyassets.asgi_scope import refuse_non_http
from tinyassets.mailbox_door import mcp as mailbox_mcp
from tinyassets.mcp_events_http import door as events_door

_HOP = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
        b"te", b"trailer", b"transfer-encoding", b"upgrade"}
_TRUSTED = {b"x-owner", b"x-box-secret", b"x-owner-identity",
            b"x-tinyassets-caller-user-id"}
_INBOUND_PROOF = {b"x-slack-signature", b"x-hub-signature-256",
                  b"x-slack-request-timestamp", b"proxy-authorization"}
_MCP = frozenset({"/mcp", "/mcp/"})
ANY = "*"
#: The box middleware's refusal of an outside client off the MCP surface.
OUTSIDE_SURFACE = "outside clients use the MCP capability surface"


@dataclass(frozen=True)
class Front:
    """What a trusted handler sees: the request and the router's verified identity.

    ``route`` is the caller's own box, set for owner registrations only. A
    handler reaches owner state through OWNER-role broker ops, never a box mount.
    """
    router: OwnerRouter
    scope: dict
    receive: object
    send: object
    identity: object = None
    route: BoxRoute | None = None

    async def ask_box(self, document):
        return await box_result(self.scope, self.identity, self.route, document)


@dataclass(frozen=True)
class Trusted:
    """One registered answer. An ``owner`` answer needs a verified owner and their box.

    ``session``: a browser's owner-session cookie names the caller, as on a forwarded door.
    """
    answer: object
    owner: bool = True
    session: bool = False


class TrustedFront:
    """The router's one registry of requests it answers itself (ADR-030).

    MCP requests are keyed by JSON-RPC method, tool name and ``target``; app
    requests by path (a trailing ``/`` names the subtree, a ``*`` one whole
    segment) and method. Every
    other request on an owner door is forwarded to the owner's box with its
    original bytes; a path outside the owner doors (discovery, ``/mcp/pulse``,
    a non-canonical spelling) gets the router's own shared answer, as before.
    """

    def __init__(self):
        self.mcp_routes: dict[tuple[str, str | None, str | None], Trusted] = {}
        self.app_routes: dict[tuple[str, str], Trusted] = {}

    def mcp(self, method, tool=None, target=None):
        key = (method, tool, target)
        if method == "tools/list":  # S12: the box serves the one canonical tools list
            raise ValueError("tools/list stays the box's")

        def register(answer):
            if key in self.mcp_routes:
                raise ValueError(f"{key} is already registered")
            self.mcp_routes[key] = Trusted(answer)
            return answer
        return register

    def app(self, path, *methods, public=False, session=False):
        """The router's own route table answers ``path``; ``public`` GETs skip admission."""
        async def shared(front):
            await _serve_shared(front, public)

        for method in methods or (ANY,):
            if (path, method) in self.app_routes:
                raise ValueError(f"{path} {method} is already registered")
            self.app_routes[path, method] = Trusted(shared, owner=False, session=session)

    def box_routes(self, routes):
        """What a box mounts of ``routes``: each loses the methods registered here (ADR-030).

        The router never forwards a registered request, so the box never builds
        its route. A route left with no method is dropped whole. A route this
        cannot narrow faithfully refuses the build instead of changing shape.
        """
        from starlette.routing import Route

        kept = []
        for route in routes:
            methods = getattr(route, "methods", None)
            if methods is None:  # Any method, or a mount: all or nothing.
                if self._app(route.path, None) is None:
                    kept.append(route)
                elif self._app(route.path, ANY) is None:
                    raise ValueError(f"{route.path} takes any method; register all or none")
                continue
            left = sorted(verb for verb in methods if self._app(route.path, verb) is None)
            if len(left) == len(methods):
                kept.append(route)
            elif left:
                narrowed = Route(route.path, route.endpoint, methods=left, name=route.name,
                                 include_in_schema=route.include_in_schema)
                # Rebuilt from its endpoint, a subclass or route middleware would be lost.
                if type(route) is not Route or _shape(route.app) != _shape(narrowed.app):
                    raise ValueError(f"{route.path} cannot be narrowed to {left}")
                kept.append(narrowed)
        return kept

    def _slashed(self, path, method):
        """An exact ``/app`` registration spelled with a trailing ``/``.

        The box redirected it to the exact path (Starlette ``redirect_slashes``);
        the router answers the same 307 itself (``_slash_redirect``). ``/api``
        registrations never had a box route, so their spelling stays a 404.
        """
        exact = path.rstrip("/")
        return path != exact and exact.startswith("/app") and any(
            key == exact and verb in {method, ANY} for key, verb in self.app_routes)

    def _app(self, path, method):
        """The registration answering ``method`` on ``path``; ``method=None`` asks for any."""
        for (key, verb), trusted in self.app_routes.items():
            if (method is None or verb in {method, ANY}) and _covers(key, path):
                return trusted
        return None

    async def match(self, scope, receive):
        """``(Trusted | None, receive)``: the one lookup for every request.

        A POST to ``/mcp`` is read up to the request bound. Anything that is
        not a registered request -- over the bound (no registered request is
        that large), not JSON, or unregistered -- is replayed to the box with
        the bytes already read, then the rest of the stream, so the box applies
        its own limits and errors as before. Duplicate keys resolve last-wins,
        as the box's parsers do. Only a batch holding a registered request is
        refused: forwarding it would let that request bypass the router.
        """
        path, method = scope["path"], scope["method"]
        trusted = self._app(path, method)
        if trusted is None and self._slashed(path, method):
            trusted = Trusted(_slash_redirect, owner=False)
        if trusted is not None or not _door(path):
            return trusted or Trusted(_shared_default, owner=False), receive
        if path not in _MCP or method != "POST":
            return None, receive
        from tinyassets.auth.middleware import _buffer_request_body, _replay_receive

        body, messages, disconnected, oversized = await _buffer_request_body(receive)
        if disconnected:
            return Trusted(_ignore, owner=False), receive
        replay = _replay_receive(messages, receive)
        try:
            document = None if oversized else json.loads(body)
        except (ValueError, TypeError, RecursionError):  # The box answers these itself.
            document = None
        batch = document if isinstance(document, list) else [document]
        found = [self.mcp_routes[key] for key in map(mcp_key, batch) if key in self.mcp_routes]
        if found and isinstance(document, list):
            # An owner answer: the same 404 and maintenance checks as forwarding come first.
            return Trusted(_batch_refused), replay
        return (found[0] if found else None), replay


def _covers(key, path):
    """``key`` names ``path`` exactly, as a trailing-``/`` subtree, or with ``*`` segments."""
    if key.endswith("/"):
        return path.startswith(key)
    keys, parts = key.split("/"), path.split("/")
    return len(keys) == len(parts) and all(
        k == p or k == "*" and p for k, p in zip(keys, parts))


def _shape(app):
    """What a route runs, by kind: a plain endpoint wrapper or what wraps it."""
    return type(app), getattr(app, "__qualname__", None)


def mcp_key(document):
    """``(method, tool, target)`` of one JSON-RPC request, as the box would dispatch it."""
    if not isinstance(document, dict) or not isinstance(document.get("method"), str):
        return None
    if document["method"] != "tools/call":
        return document["method"], None, None
    params = document.get("params")
    params = params if isinstance(params, dict) else {}
    arguments = params.get("arguments")
    target = arguments.get("target") if isinstance(arguments, dict) else None
    name = params.get("name")
    # The box reads ``target`` stripped and lower-cased; so does the key.
    return ("tools/call", name if isinstance(name, str) else None,
            target.strip().lower() if isinstance(target, str) else None)


def _door(path: str) -> bool:
    return (path.startswith("/app/") or path in {"/mcp", "/mcp/", "/mcp/events", "/api"}
            or path.startswith(("/api/", "/hooks/inbound/")))


async def _batch_refused(front):
    await JSONResponse({"jsonrpc": "2.0", "id": None, "error": {
        "code": -32600, "message": "Invalid JSON-RPC request"}}, status_code=400)(
            front.scope, front.receive, front.send)


async def _ignore(front):
    """The client left before its request body arrived; nothing is answered."""


async def _serve_shared(front, public, answer=None):
    """The router's own route table, or ``answer``; only public assets skip closed admission."""
    from tinyassets.router_traffic import admits
    from tinyassets.storage import data_dir

    scope = front.scope
    if (not public and scope["path"].startswith("/app/") and front.identity is not None
            and (front.identity.metadata or {}).get("outside_origin") is not None):
        # The box's own rule, before any handler: an outside client has the MCP
        # surface only. Public assets need no identity; keyed /api routes are not /app.
        return await JSONResponse({"error": "forbidden", "detail": OUTSIDE_SURFACE},
                                  status_code=403)(scope, front.receive, front.send)
    if not (public and scope["method"] in {"GET", "HEAD"}) and not await run_in_threadpool(
            admits, data_dir(), front.identity.user_id if front.identity else None):
        return await JSONResponse({"error": "maintenance"}, status_code=503)(
            scope, front.receive, front.send)
    if answer is not None:
        return await answer(scope, front.receive, front.send)
    await front.router.app(scope, front.receive, front.send)


async def _shared_default(front):
    await _serve_shared(front, False)


async def _slash_redirect(front):
    """The box's 307 to the exact path, query kept. Relative: the Host the router
    sees is the internal tunnel host, which a browser must never be sent to."""
    scope = front.scope
    if front.identity is None:  # As a forwarded door refuses an anonymous caller.
        return await JSONResponse({"error": "not_found"}, status_code=404)(
            scope, front.receive, front.send)
    query = scope.get("query_string", b"").decode("latin-1")
    location = scope["path"].rstrip("/") + ("?" + query if query else "")
    await _serve_shared(front, False, RedirectResponse(location, status_code=307))


TRUSTED = TrustedFront()
# The router's own app pages, sign-in and callbacks. Anonymous token, OAuth and
# billing callbacks write shared authority, so only static assets are public.
for _path in ("/app/account/delete", "/app/client-error", "/app/token",
              "/app/native-sign-in", "/app/owner-sign-in", "/app/owner-sign-in/complete",
              # Sign-in navigation: its rows live in the router's owner-sessions store.
              "/app/approval-handoff", "/app/connections",
              # Undo stays on the router: the session proof never enters the box.
              "/app/owner/restore", "/app/owner/checkpoint", "/app/owner/restore-points",
              # N10: owner policy widens only here, with the proof, on the owner channel.
              "/app/api/policy",
              # S30: the owner's outside agents and their dm rooms (mailbox_door).
              "/app/api/outside-agents", "/app/api/agent-messages",
              "/api/mailbox", "/api/mailbox/ack", "/api/mailbox/replies",
              "/app/billing/", "/app/run/", "/app/model-callback/",
              "/app/approval-handoff/", "/app/model-connect/", "/app/account/deletion/"):
    TRUSTED.app(_path)
for _path in ("/app", "/app/sw.js", "/app/ui-frame", "/app/oauth/client-metadata.json",
              "/app/m/"):
    TRUSTED.app(_path, public=True)
# S34: a published app's room is a collection in its publisher's broker store
# (room_door); a signed-in browser's session names the player, as it did at the box.
for _path in ("/app/*/state",):
    TRUSTED.app(_path, session=True)
# A rate-limited log line from the signed-in app; it needs no owner state.
for _path in ("/app/trace",):
    TRUSTED.app(_path, "POST", session=True)


@TRUSTED.mcp("events/subscribe")
@TRUSTED.mcp("events/unsubscribe")
async def _events(front):
    """P0-4: subscriptions live in the broker; the box only authorizes the caller."""
    await events_door(front.scope, front.receive, front.send, front.identity, front.ask_box)


@TRUSTED.mcp("tools/call", "read_graph", "mailbox")
@TRUSTED.mcp("tools/call", "write_graph", "room_message")
@TRUSTED.mcp("tools/call", "write_graph", "mailbox_ack")
async def _mailbox(front):
    """S3a.2: the ledger lives in the broker (ADR-025); reading or posting wakes no box."""
    await mailbox_mcp(front)


def tool_result(structured: dict) -> dict:
    """A ``tools/call`` result in the box's own shape (``universe_server._structured_return``)."""
    from tinyassets.engine_result_bounds import faithful_text

    return {"content": [{"type": "text", "text": faithful_text(structured)}],
            "structuredContent": structured, "isError": False}


async def mcp_answer(front, ident, *, result=None, error=None):
    """The router's one composed JSON-RPC answer, framed as the box frames it for this client.

    A 2026-07-28 request gets stateless JSON with ``resultType``. A legacy client that
    accepts a stream (every first-party and hosted client today) gets the box's one SSE
    ``message`` event; its session id is box-held, so the router echoes the client's own
    and never mints or checks one: the answer depends only on the verified identity.
    """
    from tinyassets.mcp_events_http import VERSION

    headers = Headers(scope=front.scope)
    modern = headers.get("mcp-protocol-version") == VERSION
    if result is not None and modern:
        result = {**result, "resultType": "complete"}
    body = json.dumps({"jsonrpc": "2.0", "id": ident,
                       **({"error": error} if error is not None else {"result": result})},
                      separators=(",", ":")).encode()
    if modern or "text/event-stream" not in headers.get("accept", ""):
        framed = {"content-type": "application/json", "cache-control": "no-store"}
    else:
        body = b"event: message\r\ndata: " + body + b"\r\n\r\n"
        framed = {"content-type": "text/event-stream", "x-accel-buffering": "no",
                  "cache-control": "no-cache, no-transform"}
        if headers.get("mcp-session-id"):
            framed["mcp-session-id"] = headers["mcp-session-id"]
    await Response(body, headers=framed)(front.scope, front.receive, front.send)


def hook_token(path: str) -> str | None:
    prefix = "/hooks/inbound/"
    if path.startswith(prefix):
        token = path[len(prefix):]
        if token and "/" not in token:
            return token
    return None


def forwarded_path(path: str, method: str | None = None) -> bool:
    """Owner doors are removed from the shared route table, not shadowed.

    Without ``method``, whether every method of ``path`` reaches the box.
    """
    return _door(path) and TRUSTED._app(path, method) is None


def session_door(path: str, method: str) -> bool:
    """Whether a browser's owner-session cookie names the caller on ``path``.

    A door the box answers, or a registration the router answers in its place.
    """
    trusted = TRUSTED._app(path, method)
    return _door(path) and (trusted is None or trusted.session)


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
        from tinyassets.auth.middleware import current_identity_or_none
        from tinyassets.routing_snapshot import snapshot
        from tinyassets.storage import data_dir

        try:
            snapshot(data_dir()).require_current()
        except RuntimeError:
            return await JSONResponse({"error": "routing_unavailable"}, status_code=503)(
                scope, receive, send)
        identity = current_identity_or_none()
        trusted, receive = await TRUSTED.match(scope, receive)
        route = None
        if trusted is None or trusted.owner:
            routed = await self._owner(scope, receive, send, identity, forward_only=trusted is None)
            if routed is None:
                return
            identity, route = routed
        # The one place the router answers a request instead of forwarding it.
        await trusted.answer(Front(self, scope, receive, send, identity, route))

    async def _owner(self, scope, receive, send, identity, *, forward_only):
        """``(identity, route)`` of the caller's own box, or ``None`` once forwarded or refused."""
        from tinyassets.auth.middleware import published_box_owner
        from tinyassets.router_traffic import admits
        from tinyassets.storage import data_dir

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
            return await forward(scope, receive, send, identity, route, box_owner=box_owner)
        if forward_only:
            return await forward(scope, receive, send, identity, route)
        return identity, route


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
