"""Remote MCP contributions through today's governed connection effector.

The adapter owns no credential, attachment registry or independent activation.
HTTP replies are bounded by the existing broker; SSE frames are parsed by the
ported protocol client. Session headers never leave this adapter.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from tinyassets.extension_manifest import ExtensionError
from tinyassets.mcp_remote import Binding, McpError, RemoteEndpoint, RemoteMcp
from tinyassets.storage.outbound_connections import AmbiguousProxyOutcome


class Held(Exception):
    def __init__(self, receipt):
        self.receipt = receipt


class EffectorTransport:
    def __init__(self, service, check):
        self.service, self.check = service, check

    @asynccontextmanager
    async def stream(self, *, grant_id, connection_id, verb, request, op_id):
        from tinyassets.agent_review import bound
        from tinyassets.effectors.authenticated_external_call import (
            run_authenticated_external_call_effector,
        )

        self.check()
        backend = self.service.backend
        with bound(backend.review_provider, active=backend.review_provider is not None):
            result = await asyncio.to_thread(
                run_authenticated_external_call_effector,
                node_id="extension-mcp", output_keys=["call"], base_path=backend.root,
                execution_context=backend.context,
                run_state={"call": {"sink": "authenticated_external_call",
                                   "connection_id": connection_id, "grant_id": grant_id,
                                   "verb": verb, "request": request}},
            )
        response = result.get("response")
        if not result.get("delivered") or not isinstance(response, dict):
            if result.get("error_kind") == "outbound_request_failed":
                raise AmbiguousProxyOutcome("MCP outcome unknown; do not replay")
            # Preserve protected approval receipts; never call again automatically.
            raise Held(result)

        class Stream:
            admitted = True

            async def head(self):
                return response

            async def body(self):
                body = response.get("body", "")
                yield body.encode("utf-8") if isinstance(body, str) else body

        yield Stream()


async def invoke(service, state, doc, row, arguments):
    requirements = {item["name"]: item for item in doc.get("connections", [])}
    requirement = requirements.get(row.get("slot"))
    if requirement is None:
        return {"state": "binding_required", "grants_created": False}
    pin = service.connection(state, requirement, "POST")
    if pin is None:
        return {"state": "binding_required", "slot": requirement["name"],
                "grants_created": False}
    action = arguments["action"]
    expected = {"action"} if action == "discover" else {
        "action", "tool", "arguments", "catalog_hash"}
    if set(arguments) != expected:
        raise ExtensionError("invalid remote MCP action arguments")

    def check(_binding=None):
        service._authority()
        if not service._enabled(state["name"]):
            raise ExtensionError("extension disabled by settings")
        if service.connection(state, requirement, "POST") != pin:
            raise ExtensionError("extension connection changed")

    remote = RemoteMcp(EffectorTransport(service, check), Binding(
        pin["grant_id"], pin["connection_id"], pin["incarnation"],
        RemoteEndpoint(row["url"])), check_authority=check)
    try:
        tools = await remote.discover()
        if action == "discover":
            return {"tools": tools, "catalog_hash": remote.catalog_hash,
                    "revision": state["revision"], "generation": state["generation"]}
        from tinyassets.broker.ops import new_op_id

        return await remote.call(arguments["tool"], arguments["arguments"],
                                 catalog_hash=arguments["catalog_hash"], op_id=new_op_id())
    except Held as held:
        return held.receipt
    except AmbiguousProxyOutcome:
        return {"error": "mcp_outcome_unknown", "hint": "Do not repeat the call blindly."}
    except McpError as exc:
        return {"error": "mcp_protocol_refused", "detail": str(exc)}
