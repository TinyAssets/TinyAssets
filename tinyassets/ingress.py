"""Opt-in durable app ingress; constructed only by the traffic harness for now.

An accept-v1 client understands a 202 receipt, not a completed MCP tool result.
Legacy MCP requests pass through unchanged. Normal server startup supplies no
adapter. Provisioning, policy and the eventual S8b importer are separate.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager

from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse

from tinyassets.api.permissions import current_actor_id
from tinyassets.storage import conversation_run_admissions as cr
from tinyassets.storage.current_home import CurrentHomeChanged
from tinyassets.storage.ingress_journal import Conflict, Expired, IngressJournal, Scope


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("non-JSON number")


class AppAcceptance:
    def __init__(self, base, root, *, admission_policy, max_payload_bytes=1024 * 1024):
        self.base = base
        self.journal = IngressJournal(
            root, authority=self.authority, admission_policy=admission_policy,
            max_payload_bytes=max_payload_bytes,
        )

    @contextmanager
    def authority(self, scope):
        # The bearer subject is independent of all caller-supplied routing data.
        if not current_actor_id() or current_actor_id() != scope.principal_id:
            raise PermissionError("authenticated principal required")
        with cr.authorized_scope(self.base, owner=scope.principal_id,
                                 universe=scope.command_center_id) as authorized:
            if scope != Scope(authorized.owner, authorized.universe, authorized.session):
                raise PermissionError("unsupported ingress scope")
            yield

    def handle(self, body, mode):
        document = json.loads(body, object_pairs_hook=_object, parse_constant=_invalid_constant)
        if (not isinstance(document, dict)
                or set(document) != {"jsonrpc", "id", "method", "params"}
                or document["jsonrpc"] != "2.0"
                or type(document["id"]) not in (str, int)
                or document["method"] != "tools/call"):
            raise ValueError("expected one identified tools/call")
        params = document["params"]
        if (not isinstance(params, dict) or set(params) != {"name", "arguments"}
                or params["name"] != "converse"):
            raise ValueError("only converse is supported")
        args = params["arguments"]
        required = {"graph_id", "client_send_id"}
        allowed = required if mode == "receipt-v1" else required | {
            "message", "input_method", "agent_id",
        }
        if not isinstance(args, dict) or not required <= args.keys() or args.keys() - allowed:
            raise ValueError("unsupported converse arguments")
        center, key = args["graph_id"], args["client_send_id"]
        if not isinstance(center, str) or not center or not isinstance(key, str):
            raise ValueError("explicit center and send identity required")
        parsed = uuid.UUID(key)
        if parsed.version != 4 or str(parsed) != key:
            raise ValueError("canonical UUIDv4 required")
        if mode == "accept-v1" and (
            not isinstance(args.get("message"), str) or not args["message"].strip()
            or args.get("agent_id", "main") != "main"
            or args.get("input_method", "typed") != "typed"
        ):
            raise ValueError("only typed main-agent messages are supported")
        principal = current_actor_id()
        scope = Scope(principal, center, f"principal:{principal}")
        envelope = (self.journal.accept(scope, key, body) if mode == "accept-v1"
                    else self.journal.receipt(scope, key))
        return {"protocol": mode, "ingress_id": envelope.ingress_id,
                "client_send_id": envelope.client_send_id, "state": envelope.state,
                "runtime_id": envelope.runtime_id}


class AppIngressMiddleware:
    """Inside AuthContextMiddleware, outside the runtime's MCP transport."""

    def __init__(self, app, acceptance=None):
        self.app = app
        self.acceptance = acceptance

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        mode = request.headers.get("x-tinyassets-ingress")
        if mode is None:
            return await self.app(scope, receive, send)
        status, output = 400, {"error": "invalid_ingress_protocol"}
        if (scope["path"] not in ("/mcp", "/mcp/") or scope["method"] != "POST"
                or mode not in ("accept-v1", "receipt-v1")):
            pass
        elif self.acceptance is None:
            status, output = 503, {"error": "durable_ingress_disabled"}
        else:
            body = bytearray()
            try:
                async for chunk in request.stream():
                    if len(body) + len(chunk) > self.acceptance.journal.max_payload_bytes:
                        response = JSONResponse({"error": "payload_too_large"}, status_code=413)
                        return await response(scope, receive, send)
                    body.extend(chunk)
                output = await run_in_threadpool(self.acceptance.handle, bytes(body), mode)
                status = 202 if mode == "accept-v1" else 200
            except ClientDisconnect:
                return
            except (PermissionError, CurrentHomeChanged):
                status, output = 403, {"error": "ingress_forbidden"}
            except Expired:
                status, output = 410, {"error": "ingress_expired"}
            except Conflict:
                status, output = 409, {"error": "ingress_conflict"}
            except LookupError:
                status, output = 404, {"error": "ingress_unavailable"}
            except (ValueError, TypeError, UnicodeError):
                status, output = 400, {"error": "invalid_ingress_request"}
            except (OSError, sqlite3.Error):
                status, output = 503, {"error": "ingress_storage_unavailable"}
        response = JSONResponse(output, status_code=status, headers={"Cache-Control": "no-store"})
        await response(scope, receive, send)
