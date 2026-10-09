"""D6a daemon capabilities. No credentials, new tables, or public MCP handles.

The platform supplies this context and callbacks, never the socket client.
The socket grants exactly the authority of ONE bash invocation.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import re
import socket
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

JAIL_SOCKET = "/tmp/ta.sock"
JAIL_CLIENT = "/ta/bin/ta"
CLIENT_SOURCE = Path(__file__).with_name("ta_cli.py")
MAX_REQUEST = 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024


@dataclass(frozen=True)
class ExecutionContext:
    universe: str
    owner: str
    initiating_agent: str
    research: bool = False
    delegated_authority: str = "serving-owner"
    approval_id: str | None = None


class Capabilities:
    def __init__(self, root: Path, context: ExecutionContext, platform: list[dict],
                 call_platform, check_authority: Callable, *,
                 connections_granted: bool = True, review_provider=None,
                 stopped: Callable[[], str | None] | None = None):
        if (root.name != context.universe or not context.owner
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", context.initiating_agent)
                or context.initiating_agent == "unresolved-agent"):
            raise ValueError("ta requires a platform-bound owner, universe and agent")
        self.root, self.context = root, context
        self.platform = {item["name"]: item for item in platform}
        self.call_platform, self.check_authority = call_platform, check_authority
        self.connections_granted = connections_granted
        self.review_provider = review_provider
        # An activity's launch: every request is refused once it stops running
        # (tinyassets/activity_fence.py), connection calls included.
        self.stopped = stopped
        from tinyassets.auth.middleware import current_identity_or_none

        self.outside_identity = current_identity_or_none()

    def connections(self):
        # A launch whose grant withholds connections neither lists nor calls one.
        if not self.connections_granted:
            return {}
        from tinyassets.broker.catalog import connections

        # No catalogue truncation: the broker's catalog pages to exhaustion.
        inventory = ((grant, view) for grant, view, _ in connections(
            self.root.parent, principal=self.context.owner,
            command_center=self.context.universe))
        found = {}
        for grant, view in inventory:
            if (view is None or view.owner_user_id != self.context.owner
                    or view.revoked_at is not None or view.connection_type != "http"):
                continue
            for verb in view.scopes:
                name = f"connection:{view.connection_id}:{verb}"
                found[name] = (grant, view, verb)
        return found

    async def dispatch(self, message):
        from tinyassets.auth.middleware import identity_context

        # Socket/event-loop dispatch may run without the originating HTTP
        # context. Keep the signed launch identity through awaited effects too.
        if self.outside_identity is not None:
            with identity_context(self.outside_identity):
                return await self._dispatch(message)
        return await self._dispatch(message)

    async def _dispatch(self, message):
        from tinyassets.outside_authority import check_identity

        identity = self.outside_identity
        if identity is not None and identity.metadata.get("outside_origin") is not None:
            try:
                check_identity(identity, universe=self.context.universe,
                               agent=self.context.initiating_agent,
                               capability=message.get("name") if isinstance(message, dict)
                               and message.get("op") == "call" else "ta:catalog")
            except PermissionError:
                return {"error": "outside client capability not granted"}
        error = self.check_authority()
        if error:
            return {"error": "serving owner authority unavailable"}
        stopped = await asyncio.to_thread(self.stopped) if self.stopped is not None else None
        if stopped is not None:
            return {"error": stopped}
        if self.context.research:
            return {"error": "research_is_read_only"}
        if not isinstance(message, dict):
            return {"error": "request must be an object"}
        if message.get("op") == "catalog" and set(message) == {"op"}:
            items = list(self.platform.values())
            for name, (_grant, view, verb) in self.connections().items():
                items.append({
                    "name": name, "description": f"{view.destination}: {verb}",
                    "arguments": {"type": "object", "required": ["request"],
                                  "additionalProperties": False,
                                  "properties": {"request": {"type": "object",
                                      "description": "path or url, query, headers, body"}}},
                    "endpoints": [ep.as_dict() for ep in view.allowed_endpoints],
                    "access_mode": view.access_mode,
                })
            from tinyassets.extension_capabilities import LIFECYCLE, ExtensionCapabilities

            try:
                extension_items = ExtensionCapabilities(self).catalog()
                extension_error = None
            except (ValueError, LookupError, OSError) as exc:
                extension_items, extension_error = list(LIFECYCLE), str(exc)
            return {"capabilities": items, "extension_capabilities": extension_items,
                    "extension_error": extension_error, "extension_roots": {
                "shared": "/u/extensions",
                "agent": f"/u/agents/{self.context.initiating_agent}/extensions",
            }}
        if (message.get("op") != "call" or set(message) != {"op", "name", "arguments"}
                or not isinstance(message.get("arguments"), dict)):
            return {"error": "invalid ta request"}
        name, arguments = message["name"], message["arguments"]
        if not isinstance(name, str):
            return {"error": "invalid capability name"}
        if name.startswith("extension:"):
            from tinyassets.extension_capabilities import ExtensionCapabilities

            try:
                import inspect

                result = ExtensionCapabilities(self).call(name, arguments)
                return {"result": await result if inspect.isawaitable(result) else result}
            except (ValueError, LookupError, OSError) as exc:
                return {"error": str(exc)}
        if name in self.platform:
            return {"result": await self.call_platform(name, arguments)}
        match = self.connections().get(name)
        if match is None:
            return {"error": "unknown capability"}
        if set(arguments) != {"request"} or not isinstance(arguments["request"], dict):
            return {"error": "connection arguments require only a request object"}
        grant, view, verb = match
        from tinyassets.agent_review import bound as review_bound
        from tinyassets.effectors.authenticated_external_call import (
            run_authenticated_external_call_effector,
        )

        with review_bound(self.review_provider, active=self.review_provider is not None):
            result = await asyncio.to_thread(
                run_authenticated_external_call_effector,
                node_id="ta", output_keys=["call"], base_path=self.root,
                execution_context=self.context,
                run_state={"call": {"sink": "authenticated_external_call",
                                   "connection_id": view.connection_id,
                                   "grant_id": grant.grant_id, "verb": verb,
                                   "request": arguments["request"]}},
            )
        response = result.get("response")
        if isinstance(response, dict) and isinstance(response.get("headers"), dict):
            # Same custody rule as bounded_evidence: even a non-secret-looking
            # header can carry a rotated cookie or an encoded credential echo.
            # Scripts retain the full body, but no response header values.
            response = dict(response)
            response["header_names"] = sorted(str(k) for k in response.pop("headers"))
            result = {**result, "response": response}
        return {"result": result}


class JailBridge:
    """Short-lived Unix socket in a private host directory, bound by exact path.

    One reader bounds concurrent daemon work. Requests never supply context,
    headers, a bearer or a route. Closing the bash invocation revokes the socket.
    """

    def __init__(self, dispatch):
        self.dispatch = dispatch
        self._context = contextvars.copy_context()
        self._closed = threading.Event()

    def __enter__(self):
        self._directory = tempfile.TemporaryDirectory(prefix="ta-")
        self.extension_root = None
        backend = getattr(self.dispatch, "extension_backend", None)
        if backend is not None:
            from tinyassets.extension_capabilities import ExtensionCapabilities

            candidate = Path(self._directory.name) / "extensions"
            try:
                if self._context.run(ExtensionCapabilities(backend).materialize, candidate):
                    self.extension_root = candidate
            except Exception:
                self._directory.cleanup()
                raise
        self.path = Path(self._directory.name) / "cap.sock"
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(self.path))
        self.path.chmod(0o600)
        self._server.listen(8)
        self._server.settimeout(0.1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def _serve(self):
        while not self._closed.is_set():
            try:
                client, _ = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with client:
                client.settimeout(10)
                try:
                    with client.makefile("rb") as stream:
                        raw = stream.readline(MAX_REQUEST + 1)
                    if len(raw) > MAX_REQUEST or not raw.endswith(b"\n"):
                        answer = {"error": "invalid or oversized ta request"}
                    elif self._closed.is_set():
                        return
                    else:
                        answer = self._context.copy().run(self.dispatch, json.loads(raw))
                    data = json.dumps(answer).encode() + b"\n"
                    if len(data) > MAX_RESPONSE:
                        data = b'{"error":"ta response too large; request a smaller page"}\n'
                    client.sendall(data)
                except Exception:  # no host exception text crosses into the jail
                    try:
                        client.sendall(b'{"error":"ta request failed"}\n')
                    except OSError:
                        pass

    def __exit__(self, *_):
        self._closed.set()
        self._server.close()
        self._directory.cleanup()


async def engine_dispatch(server, *, completed: list | None = None):
    """Capture the engine launch and schedule nested calls on its existing loop.

    ``ta`` reaches exactly the launch's own grant: the served tools the platform
    signed onto this launch's route (an agent node's ``tools_allowed``, else the
    whole served set). A launch with no signed grant gets no ``ta``. Without
    shell authority, run_bash accepts only a parsed ta invocation; no shell or
    local extension process runs. Delegation never increases authority.
    """
    from tinyassets.activity_fence import stop_check
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.engine_steering import _session_key, launch_tools
    from tinyassets.research_capability import is_research_session
    from tinyassets.served_tools import connections_granted

    granted = launch_tools()
    if granted is None:
        return None
    context = ExecutionContext(server._GRAPH_ID, server._ACTOR_ID, server._acting_agent(),
                               research=is_research_session(_session_key()))
    allowed = set(granted) - {"read", "write", "edit", "bash"}
    platform = [{"name": tool.name, "description": tool.description or "",
                 "arguments": tool.parameters}
                for tool in await server.mcp.list_tools(run_middleware=False)
                if tool.name in allowed]

    async def call_platform(name, arguments):
        from fastmcp.exceptions import ToolError
        from fastmcp.exceptions import ValidationError as MCPValidationError
        from pydantic import ValidationError

        from tinyassets.engine_mcp_server import bounded_tool_error

        try:
            result = await server.mcp.call_tool(name, arguments)
        except (ToolError, MCPValidationError, ValidationError) as exc:
            return {"error": bounded_tool_error(str(exc), tool=name)}
        blocks = [block.model_dump(exclude_none=True) for block in result.content]
        if len(blocks) == 1 and blocks[0].get("type") == "text":
            try:
                value = json.loads(blocks[0]["text"])
                if (completed is not None and name == "write_brain"
                        and not getattr(result, "isError", False)
                        and isinstance(value, dict) and value.get("ok") is True
                        and value.get("written")):
                    completed.append("write_brain")
                return value
            except ValueError:
                return blocks[0]["text"]
        return {"content": blocks}

    loop = asyncio.get_running_loop()
    universe_dir = _universe_dir(context.universe)
    backend = Capabilities(universe_dir, context, platform,
                           call_platform, server._binding_error,
                           review_provider=_turn_reviewer(loop),
                           connections_granted=connections_granted(granted),
                           stopped=stop_check(universe_dir, _session_key()))

    def dispatch(message):
        future = asyncio.run_coroutine_threadsafe(backend.dispatch(message), loop)
        try:
            return future.result(timeout=600)
        except TimeoutError:
            future.cancel()
            return {"error": "ta call timed out; outcome may be unknown; do not retry blindly"}

    dispatch.extension_backend = backend
    return dispatch


def _turn_reviewer(loop):
    """Use only this MCP caller's model; never a daemon/host model fallback.

    The engine is a separate process from the turn. MCP sampling is its
    provider connection. Clients without sampling leave owner-enabled review
    unavailable, which the same graph review gate reports as a clear hold.
    Capture the session before the bridge changes threads / nests tool calls.
    """
    from fastmcp.server.dependencies import get_context
    from mcp.types import ClientCapabilities, SamplingCapability, SamplingMessage, TextContent

    from tinyassets.agent_review import REVIEW_TIMEOUT_S

    try:
        session = get_context().session
    except RuntimeError:
        return None
    if not session.check_client_capability(ClientCapabilities(sampling=SamplingCapability())):
        return None

    async def sample(prompt, system):
        answer = await session.create_message(
            [SamplingMessage(role="user", content=TextContent(type="text", text=prompt))],
            system_prompt=system, max_tokens=512, include_context="none",
        )
        return answer.content.text if isinstance(answer.content, TextContent) else ""

    def review(prompt, system, **_kwargs):
        future = asyncio.run_coroutine_threadsafe(sample(prompt, system), loop)
        try:
            return future.result(timeout=REVIEW_TIMEOUT_S)
        except TimeoutError:
            future.cancel()
            raise

    return review
