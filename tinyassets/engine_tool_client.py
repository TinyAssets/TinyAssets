"""Private, owner-pinned MCP transport for the engine-owned HTTP agent loop.

This client grants no inference authority and supplies no replay policy. Its
caller must journal tool intent before dispatch and preserve unknown outcomes.
The interactive runner supplies fresh serving authority and durable progress.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from tinyassets.engine_mcp_http import (
    EngineMcpRoute,
    read_engine_mcp_route,
    wait_for_engine_mcp_route,
)
from tinyassets.served_tools import BACKEND_ENGINE_CAPABILITIES
from tinyassets.storage import data_dir


class EngineToolError(RuntimeError):
    """Fixed transport diagnosis; unknown NEVER authorizes another tools/call."""

    def __init__(self, code: str, *, outcome: str = "not_sent") -> None:
        self.code = code
        self.outcome = outcome
        super().__init__(code)


async def _ignore_server_log(_message: Any) -> None:
    # Server notifications are content, not permission to write daemon logs.
    return None


def _local_schema_references(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("$ref", "$dynamicRef", "$recursiveRef") and (
                not isinstance(item, str) or not item.startswith("#")
            ):
                return False
            if not _local_schema_references(item):
                return False
    elif isinstance(value, list):
        return all(_local_schema_references(item) for item in value)
    return True


def _make_client(route: EngineMcpRoute, timeout: float):
    import httpx
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    def private_http_client(**_ignored):
        # FastMCP forwards ambient request headers/auth and redirect defaults.
        # Discard ALL of them, including cookies and connector credentials.
        return httpx.AsyncClient(
            headers={"Authorization": f"Bearer {route.secret}"},
            auth=None,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(30.0, read=timeout),
        )

    return Client(
        StreamableHttpTransport(route.url, httpx_client_factory=private_http_client),
        name="private-engine-tools",
        timeout=timeout,
        init_timeout=timeout,
        log_handler=_ignore_server_log,
    )


async def _close_client(client) -> str:
    """Bounded best-effort library exit, without replacing an already held result."""
    try:
        await client.__aexit__(None, None, None)
        return "pending" if client.is_connected() else "closed"
    except Exception:
        # Never include a raw transport error, route or bearer in diagnostics.
        return "failed"


class EngineToolSession:
    """One non-reconnecting session; constructed only by open_engine_tools."""

    def __init__(self, client, route: EngineMcpRoute, root: Path, enabled: tuple[str, ...]):
        self._client = client
        self._route = route
        self._root = root
        self._enabled = enabled
        self._tools: tuple[Any, ...] = ()
        self._active = True
        self.cleanup_status = "not_started"

    def _check_route(self) -> None:
        if not self._active:
            raise EngineToolError("engine_tools_closed")
        current = read_engine_mcp_route(
            actor_id=self._route.actor_id,
            graph_id=self._route.graph_id,
            root=self._root,
        )
        if current != self._route:
            raise EngineToolError("engine_tools_route_changed")

    @property
    def tools(self) -> tuple[Any, ...]:
        # Caller edits to a displayed schema must not alter our validated inventory.
        return tuple(tool.model_copy(deep=True) for tool in self._tools)

    async def _discover(self) -> None:
        from jsonschema import Draft202012Validator

        found: dict[str, Any] = {}
        cursor = None
        seen_cursors: set[str] = set()
        try:
            for _ in range(32):
                self._check_route()
                # SAME session primes MCP's output-schema cache before tools/call.
                page = await self._client.list_tools_mcp(cursor=cursor)
                for tool in page.tools:
                    if tool.name in found:
                        raise EngineToolError("engine_tools_invalid_inventory")
                    found[tool.name] = tool
                    if tool.name in self._enabled:
                        if tool.inputSchema.get("type") != "object":
                            raise EngineToolError("engine_tools_invalid_inventory")
                        if not all(
                            _local_schema_references(schema)
                            for schema in (
                                tool.inputSchema,
                                tool.outputSchema,
                            )
                        ):
                            raise EngineToolError("engine_tools_invalid_inventory")
                        Draft202012Validator.check_schema(tool.inputSchema)
                        if tool.outputSchema is not None:
                            Draft202012Validator.check_schema(tool.outputSchema)
                cursor = page.nextCursor
                if not cursor:
                    break
                if cursor in seen_cursors:
                    raise EngineToolError("engine_tools_invalid_inventory")
                seen_cursors.add(cursor)
            else:
                raise EngineToolError("engine_tools_invalid_inventory")
            self._check_route()
            if any(name not in found for name in self._enabled):
                raise EngineToolError("engine_tools_missing")
            self._tools = tuple(found[name] for name in self._enabled)
        except EngineToolError:
            raise
        except Exception:
            raise EngineToolError("engine_tools_unavailable") from None

    async def call(self, name: str, arguments: dict[str, Any]):
        """Return an exact MCP result, including isError; never retry or reconnect."""
        self._check_route()
        if name not in self._enabled or not isinstance(arguments, dict):
            raise EngineToolError("engine_tool_not_allowed")
        try:
            # MCP may validate/reject structured output AFTER the effect happened.
            # Session-terminated and all other unavailable outcomes are ambiguous.
            return await self._client.call_tool_mcp(name, arguments)
        except asyncio.CancelledError:
            self._active = False
            raise
        except Exception:
            self._active = False
            raise EngineToolError("engine_tool_outcome_unknown", outcome="unknown") from None

    async def call_ta(self, message):
        """Private resource on the SAME authenticated, signed launch session."""
        from tinyassets.ta_capabilities import MAX_REQUEST, MAX_RESPONSE

        self._check_route()
        if "bash" not in self._enabled:
            raise EngineToolError("engine_tool_not_allowed")
        raw = json.dumps(message).encode()
        if len(raw) > MAX_REQUEST:
            raise EngineToolError("remote_ta_request_too_large")
        payload = base64.urlsafe_b64encode(raw).decode()
        try:
            contents = await self._client.read_resource("ta-bridge://request/" + payload)
            if len(contents) != 1 or len(contents[0].text.encode()) > MAX_RESPONSE:
                raise ValueError("invalid bridge response")
            return json.loads(contents[0].text)
        except asyncio.CancelledError:
            self._active = False
            raise
        except Exception:
            self._active = False
            raise EngineToolError("remote_ta_outcome_unknown", outcome="unknown") from None


@asynccontextmanager
async def open_engine_tools(
    *,
    actor_id: str,
    graph_id: str,
    enabled_tools: Sequence[str],
    capability_grant: Sequence[str] | None = None,
    timeout: float = 60.0,
    session_key: str = "",
    turn: str = "",
    context_tokens: int | None = None,
) -> AsyncIterator[EngineToolSession]:
    """Use caller-verified identity; no caller-supplied URL, secret or transport."""
    if not isinstance(enabled_tools, Sequence) or isinstance(enabled_tools, (str, bytes)):
        raise EngineToolError("engine_tools_invalid_selection")
    enabled = tuple(enabled_tools)
    if (
        not enabled
        or any(not isinstance(name, str) for name in enabled)
        or len(set(enabled)) != len(enabled)
        or not set(enabled).issubset(BACKEND_ENGINE_CAPABILITIES)
    ):
        raise EngineToolError("engine_tools_invalid_selection")
    # The displayed subset is not the authority signed onto the launch. ta
    # needs the latter even when its backend handles are absent from discovery.
    if capability_grant is not None and (
        not isinstance(capability_grant, Sequence)
        or isinstance(capability_grant, (str, bytes))
    ):
        raise EngineToolError("engine_tools_invalid_grant")
    grant = enabled if capability_grant is None else tuple(capability_grant)
    if (any(not isinstance(name, str) for name in grant)
            or len(set(grant)) != len(grant)
            or not set(grant).issubset(BACKEND_ENGINE_CAPABILITIES)
            or not set(enabled).issubset(
                set(grant) | ({"bash"} if set(grant) - {"read", "write", "edit", "bash"} else set())
            )):
        raise EngineToolError("engine_tools_invalid_grant")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise EngineToolError("engine_tools_invalid_timeout")
    root = data_dir()
    route = await wait_for_engine_mcp_route(
        actor_id=actor_id, graph_id=graph_id, root=root, timeout=timeout,
    )
    if route is None:
        raise EngineToolError("engine_tools_unavailable")
    try:
        # The session and live turn ride on the URL the client dials; the
        # verified route itself, which the session checks against, is unchanged.
        from tinyassets.engine_steering import route_with_session

        dialled = replace(route, url=route_with_session(
            route.url, session_key, turn, grant_key=getattr(route, "grant_key", ""), tools=grant,
            context_tokens=context_tokens))
        client = _make_client(dialled, timeout)
    except Exception:
        raise EngineToolError("engine_tools_unavailable") from None
    session = EngineToolSession(client, route, root, enabled)
    try:
        try:
            await client.__aenter__()
        except Exception:
            raise EngineToolError("engine_tools_unavailable") from None
        await session._discover()
        yield session
    finally:
        session._active = False
        session.cleanup_status = await _close_client(client)
