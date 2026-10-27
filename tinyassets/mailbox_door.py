"""S3a.2: the message ledger's doors (ADR-025, registry row S30), all on the trusted side.

Three MCP targets (registered in ``owner_router.TRUSTED`` beside the events door),
three ``/api`` routes for API-key agents and two owner ``/app/api`` routes, every one
answered by the router from its verified identity through ``broker.client
.ledger_request``. None reaches, wakes or asks a box. The reader and the sender are the
identity's ``outside_origin`` (none means the owner); no request field names either.

Until P0-3 an MCP connector carries no ``outside_origin`` and cannot be told apart from
the owner, so an MCP caller without one is refused, never read as the owner. A body
handed to an outside agent is wrapped in the untrusted envelope.

``cursor`` on a mailbox read acknowledges everything through it before reading, so a
pull client pages with the ``next_cursor`` each read returns; ``mailbox_ack`` acks
without reading.
"""
from __future__ import annotations

import json

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

#: The tool targets the router answers; the box answers ``served_on_trusted_side``.
READS = frozenset({"mailbox"})
WRITES = frozenset({"room_message", "mailbox_ack"})
OUTSIDE_UNCONFIGURED = "outside agent identity is not configured yet"
_HEADERS = {"Cache-Control": "no-store"}


class Refused(Exception):
    def __init__(self, status: int, error: str, reason: str = ""):
        super().__init__(reason or error)
        self.status, self.error, self.reason = status, error, reason

    def body(self) -> dict:
        return {"error": self.error, **({"reason": self.reason} if self.reason else {})}


def served_on_trusted_side(target: str) -> str:
    """What the box says if one of these targets ever reaches it: it holds no mail."""
    return json.dumps({"error": "served_on_trusted_side", "target": target})


def _ledger(identity, op: str, action: str, **fields) -> dict:
    from tinyassets.broker.client import BrokerRefused, ledger_request
    from tinyassets.storage import data_dir

    try:
        return ledger_request(data_dir(), identity, op, action, **fields)
    except BrokerRefused as exc:
        if str(exc) == "ledger unavailable":
            raise Refused(503, "ledger_unavailable") from None
        raise Refused(403, "refused", str(exc)) from None
    except PermissionError as exc:  # the router's own key check
        raise Refused(403, "refused", str(exc)) from None
    except (OSError, RuntimeError):
        raise Refused(503, "ledger_unavailable") from None


def _count(value, name: str, low: int, high: int | None = None) -> int:
    if type(value) is not int or value < low or (high is not None and value > high):
        span = f"{low} to {high}" if high is not None else f"at least {low}"
        raise Refused(400, "invalid_request", f"{name} is an integer, {span}")
    return value


def read(identity, *, cursor=0, limit=30) -> dict:
    """The caller's own mail past its cursor; ``cursor`` acks through it first."""
    _count(limit, "limit", 1, 100)
    if _count(cursor, "cursor", 0):
        _ledger(identity, "MAILBOX_RECEIPT", "ack", through=cursor)
    import os

    from tinyassets.engine_read_views import CEILING_HEADROOM_BYTES
    from tinyassets.engine_result_bounds import resolve_ceiling
    from tinyassets.untrusted import envelope

    outside = identity.metadata.get("outside_origin") is not None
    # The ledger pages whole messages under the box's read_graph ceiling (the
    # environment as the box reads it; the router has no served model context),
    # padded per message for the envelope wrapped around an outside reader's body.
    pad = len(json.dumps(envelope("mailbox:" + "x" * 64, ""), separators=(",", ":"))) - 2
    mail = _ledger(identity, "MAILBOX_RECEIPT", "read", limit=limit,
                   max_bytes=resolve_ceiling(dict(os.environ)) - CEILING_HEADROOM_BYTES,
                   pad_bytes=pad if outside else 0)
    messages = mail["messages"]
    if outside:
        messages = [{**message, "body": None if message["body"] is None else envelope(
            "mailbox:" + message["sender_id"], message["body"])} for message in messages]
    # After that ack the ledger's own cursor is at least ``cursor``; a page the
    # ledger cut ends at next_cursor, and the rest stays unread for the next read.
    return {**mail, "messages": messages,
            "next_cursor": messages[-1]["seq"] if messages else mail["cursor"]}


def ack(identity, *, through) -> dict:
    return _ledger(identity, "MAILBOX_RECEIPT", "ack", through=_count(through, "through", 0))


def own_room(identity) -> str:
    """An outside agent's one ``dm:`` room, from its verified identity."""
    origin = identity.metadata["outside_origin"]
    mine = [agent["agent_id"] for agent in _ledger(identity, "OUTSIDE_AGENTS", "list")["agents"]
            if agent["disabled_reason"] is None and (agent["outside_client"], agent[
                "outside_family"]) == (origin["client"], origin["family"])]
    if len(mine) != 1:  # as the ledger's own reader check
        raise Refused(403, "refused", "this outside identity is not one enabled agent")
    return "dm:" + mine[0]


def post(identity, *, client_message_id, body, parent_id=None, room_id=None, to=None) -> dict:
    """One message in a dm room. In a 1:1 room ``to`` is always the other party."""
    outside = identity.metadata.get("outside_origin") is not None
    if outside and to not in (None, "", "owner"):
        raise Refused(400, "invalid_request", "an outside agent's dm message goes to the owner")
    for name, value in (("client_message_id", client_message_id), ("body", body)):
        if not isinstance(value, str) or not value:
            raise Refused(400, "invalid_request", f"{name} is required text")
    if parent_id is not None and not isinstance(parent_id, str):
        raise Refused(400, "invalid_request", "parent_id is a message id")
    room = room_id or own_room(identity)
    return _ledger(identity, "ROOM_POST", "post", room_id=room,
                   client_message_id=client_message_id, body=body, parent_id=parent_id or None)


def _payload(arguments: dict) -> dict:
    raw = arguments.get("payload_json") or "{}"
    try:
        payload = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise Refused(400, "invalid_request", "payload_json is a JSON object")
    return payload


def _tool(identity, target: str, arguments: dict) -> dict:
    if target == "mailbox":
        return read(identity, cursor=arguments.get("cursor", 0),
                    limit=arguments.get("limit", 30))
    payload = _payload(arguments)
    if target == "mailbox_ack":
        return ack(identity, through=payload.get("through"))
    return post(identity, **{key: payload.get(key) for key in (
        "room_id", "client_message_id", "body", "parent_id", "to")})


async def mcp(front) -> None:
    """``read_graph target=mailbox``, ``write_graph target=room_message|mailbox_ack``."""
    from tinyassets.auth.middleware import _buffer_request_body
    from tinyassets.mcp_events_http import VERSION, Envelope, parse, refusal
    from tinyassets.owner_router import mcp_answer, mcp_key, tool_result

    raw, _, disconnected, _ = await _buffer_request_body(front.receive)
    if disconnected:
        return
    document = json.loads(raw)  # the registry already read it as one registered request
    ident, (_, _, target) = document.get("id"), mcp_key(document)
    headers = Headers(scope=front.scope)
    if headers.get("mcp-protocol-version") == VERSION:
        try:  # the box's own envelope checks for a modern request
            parse(headers, VERSION, raw)
        except Envelope as exc:
            return await refusal(exc.ident, exc)(front.scope, front.receive, front.send)
    if front.identity.metadata.get("outside_origin") is None:
        return await mcp_answer(front, ident, error={
            "code": -32024, "message": OUTSIDE_UNCONFIGURED,
            "data": {"reason": "outside_identity_unconfigured"}})
    try:
        result = await run_in_threadpool(
            _tool, front.identity, target, document["params"]["arguments"])
    except Refused as exc:
        if exc.status == 503:
            return await mcp_answer(front, ident, error={"code": -32603,
                                                         "message": "ledger unavailable"})
        result = exc.body()
    await mcp_answer(front, ident, result=tool_result(result))


# -- /api: API-key agents --------------------------------------------------------


async def _answer(call, *args, **kwargs) -> JSONResponse:
    try:
        return JSONResponse(await run_in_threadpool(call, *args, **kwargs), headers=_HEADERS)
    except Refused as exc:
        return JSONResponse(exc.body(), status_code=exc.status, headers=_HEADERS)


def _query_int(request, name: str, default: int):
    raw = request.query_params.get(name)
    try:
        return default if raw is None else int(raw)
    except ValueError:
        return None


def _agent(request):
    """The caller's API-key identity; the auth middleware refused every other bearer."""
    from tinyassets.rest_api import _api_key_identity

    identity = _api_key_identity()
    if identity is None or identity.metadata.get("outside_origin") is None:
        return None, JSONResponse({"error": "api_key_required"}, status_code=401,
                                  headers=_HEADERS)
    return identity, None


async def rest_mailbox(request):
    identity, refused = _agent(request)
    return refused or await _answer(read, identity, cursor=_query_int(request, "cursor", 0),
                                    limit=_query_int(request, "limit", 30))


async def rest_ack(request):
    from tinyassets.rest_api import _body

    identity, refused = _agent(request)
    if refused:
        return refused
    data = await _body(request)
    if data is None:
        return JSONResponse({"error": "invalid_json"}, status_code=400, headers=_HEADERS)
    return await _answer(ack, identity, through=data.get("through"))


async def rest_reply(request):
    """``{client_message_id, body, parent_id}`` in the caller's own dm room."""
    from tinyassets.rest_api import _body

    identity, refused = _agent(request)
    if refused:
        return refused
    data = await _body(request)
    if data is None:
        return JSONResponse({"error": "invalid_json"}, status_code=400, headers=_HEADERS)
    return await _answer(post, identity, **{key: data.get(key) for key in (
        "client_message_id", "body", "parent_id")})


def rest_routes() -> list:
    from starlette.routing import Route

    return [Route("/api/mailbox", rest_mailbox, methods=["GET"]),
            Route("/api/mailbox/ack", rest_ack, methods=["POST"]),
            Route("/api/mailbox/replies", rest_reply, methods=["POST"])]


# -- /app/api: the owner, from a protected owner session -------------------------


async def _owner(request):
    """``(identity, session, refusal)``: the owner in their protected session, as policy."""
    from starlette.responses import PlainTextResponse

    from tinyassets.auth.middleware import current_identity_or_none
    from tinyassets.onboarding import _app_identity_required, onboarding_enabled, owner_sessions
    from tinyassets.owner_runtime import current

    def refused(error, status=403):
        return None, None, JSONResponse({"error": error}, status_code=status, headers=_HEADERS)

    if not onboarding_enabled():
        return None, None, PlainTextResponse("Not Found", status_code=404)
    denied = _app_identity_required()
    if denied is not None:
        return None, None, denied
    identity = current_identity_or_none()
    if current() is not None or identity.metadata.get("outside_origin") is not None:
        return refused("owner_session_required")
    try:
        if request.method == "GET":
            session = await run_in_threadpool(
                owner_sessions.lookup, request.cookies.get(owner_sessions.COOKIE, ""))
            if session is None or json.loads(session["identity_json"])["user_id"] != (
                    identity.user_id):
                raise PermissionError("protected owner session required")
        else:
            session = await run_in_threadpool(owner_sessions.require, request,
                                              owner=identity.user_id)
    except PermissionError:
        return refused("interactive_owner_session_required")
    return identity, session, None


async def outside_agents(request):
    """GET lists; POST registers an owned API key as a pull agent, or disables an agent."""
    from tinyassets.onboarding import _read_small_json

    identity, session, refused = await _owner(request)
    if refused:
        return refused
    if request.method == "GET":
        return await _answer(_ledger, identity, "OUTSIDE_AGENTS", "list")
    data = await _read_small_json(request) or {}
    proof = {"session_hash": session["session_hash"]}
    if set(data) == {"action", "key_id", "display_name", "command_center_id"} and data[
            "action"] == "register":
        return await _answer(_ledger, identity, "OUTSIDE_AGENTS", "register",
                             session_proof=proof, command_center_id=data["command_center_id"],
                             source="api_key", source_ref=data["key_id"],
                             display_name=data["display_name"], delivery_method="pull",
                             recipe_id=None)
    if set(data) == {"action", "agent_id", "reason"} and data["action"] == "disable":
        return await _answer(_ledger, identity, "OUTSIDE_AGENTS", "disable",
                             session_proof=proof, agent_id=data["agent_id"],
                             reason=data["reason"])
    return JSONResponse({"error": "invalid_request"}, status_code=400, headers=_HEADERS)


async def agent_messages(request):
    """GET reads the owner's own mailbox (``cursor``, ``limit``); POST sends to an agent's dm."""
    from tinyassets.onboarding import _read_small_json

    identity, _, refused = await _owner(request)
    if refused:
        return refused
    if request.method == "GET":
        return await _answer(read, identity, cursor=_query_int(request, "cursor", 0),
                             limit=_query_int(request, "limit", 30))
    data = await _read_small_json(request, limit=65536) or {}
    if not isinstance(data.get("agent_id"), str) or not data["agent_id"]:
        return JSONResponse({"error": "invalid_request"}, status_code=400, headers=_HEADERS)
    return await _answer(post, identity, room_id="dm:" + data["agent_id"],
                         client_message_id=data.get("client_message_id"),
                         body=data.get("body"), parent_id=data.get("parent_id"))
