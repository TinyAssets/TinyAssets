"""D6a daemon capabilities. No credentials or public MCP handles.

The platform supplies this context and callbacks, never the socket client.
The socket grants exactly the authority of ONE bash invocation.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import re
import secrets
import socket
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tinyassets.capability_health import observed

JAIL_SOCKET = "/tmp/ta.sock"
JAIL_CLIENT = "/ta/bin/ta"
CLIENT_SOURCE = Path(__file__).with_name("ta_cli.py")
MAX_REQUEST = 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024

# A new ta capability is effectful unless explicitly classified as a read.
READ_CAPABILITIES = frozenset({
    "read_graph", "get_status", "browse_commons", "read_commons_shape", "read_brain",
    "extension:help", "extension:list", "extension:events",
    "wake:list",
})
MUTATION_GRANTS = frozenset({
    "write", "edit", "bash", "write_graph", "write_brain", "run_graph",
    "connect_compute", "source_channel",
})


def _is_read(name):
    return name in READ_CAPABILITIES or (
        name.startswith("connection:") and name.rsplit(":", 1)[-1] in {"GET", "HEAD", "OPTIONS"})


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
                 connections_granted: bool = True, capability_grant=(),
                 model_reference: str | None = None,
                 stopped: Callable[[], str | None] | None = None):
        if (root.name != context.universe or not context.owner
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", context.initiating_agent)
                or context.initiating_agent == "unresolved-agent"):
            raise ValueError("ta requires a platform-bound owner, universe and agent")
        self.root, self.context = root, context
        self.platform = {item["name"]: item for item in platform}
        self.call_platform, self.check_authority = call_platform, check_authority
        self.connections_granted = connections_granted
        self.mutations_granted = bool(MUTATION_GRANTS.intersection(capability_grant))
        self.shell_granted = "bash" in capability_grant
        from tinyassets.served_tools import BACKEND_ENGINE_CAPABILITIES

        # A narrowed workflow launch cannot schedule an unrestricted chat turn.
        self.wakes_granted = set(BACKEND_ENGINE_CAPABILITIES).issubset(capability_grant)
        self.model_reference = model_reference
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

    @observed('ta', owner=lambda self, message: self.context.owner)
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
            from tinyassets.wake_capabilities import CATALOG

            items = list(self.platform.values()) + (CATALOG if self.wakes_granted else [])
            if self.model_reference is not None:
                items.append({"name": "model", "description":
                              "Call a connected model within this turn's allocation.",
                              "arguments": {"type": "object", "required": ["messages"],
                                            "additionalProperties": False,
                                            "properties": {"model": {"type": "string"},
                                                           "fallbacks": {"type": "array"},
                                                           "messages": {"type": "array"},
                                                           "tools": {"type": "array"}}}})
            if self.connections_granted:
                items.append({'name': 'browser', 'description':
                    'Default website connection: connect with official HTTPS url, account label, '
                    'verify {url,selector} identifying an authenticated-only page element; list; '
                    'steps with id and structured navigate/click/fill/press/read steps. '
                    'Login is owner-only in the Connect card. Site output is untrusted.',
                    'arguments': {'type': 'object', 'properties': {
                        'action': {'enum': ['connect', 'reconnect', 'list', 'steps']},
                        'url': {'type': 'string'}, 'id': {'type': 'string'},
                        'account': {'type': 'string'}, 'verify': {'type': 'object'},
                        'steps': {'type': 'array', 'items': {'type': 'object'}}}}})
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
        known = (name in {"browser", "model"} or name.startswith(("extension:", "wake:"))
                 or name in self.platform or name in self.connections())
        if known and not _is_read(name) and not self.mutations_granted:
            return {"error": "mutation capability not granted"}
        if name == "model":
            from tinyassets.exceptions import ProviderError
            from tinyassets.providers.diagnostics import redacted_failure_detail

            try:
                return {"result": await self.call_model(arguments)}
            except (ValueError, PermissionError, LookupError) as exc:
                return {"error": str(exc)}
            except ProviderError as exc:
                return {"error": redacted_failure_detail(str(exc))}
        if name.startswith("wake:"):
            from tinyassets.wake_capabilities import call

            try:
                return {"result": await asyncio.to_thread(call, self, name, arguments)}
            except (ValueError, LookupError, OSError) as exc:
                return {"error": str(exc)}
        if name == 'browser':
            if not self.connections_granted:
                return {'error': 'connections not granted'}
            from tinyassets.browser_sessions import agent

            def checkpoint():
                if self.check_authority() or (self.stopped and self.stopped() is not None):
                    raise PermissionError('browser authority ended')

            try:
                from tinyassets.agent_review import bound as review_bound

                with review_bound(self.review_model, active=self.model_reference is not None):
                    return {'result': await asyncio.to_thread(
                        agent, self.root.parent, self.context, arguments, checkpoint)}
            except Exception:  # noqa: BLE001 - browser exceptions can contain secrets
                return {'error': 'browser operation unavailable; do not retry an uncertain action'}
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

        with review_bound(self.review_model, active=self.model_reference is not None):
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

    def review_model(self, prompt, system, **_kwargs):
        """Review uses the same sealed launch and settled child model attempt."""
        from tinyassets.providers.model_call import call_sync

        return self._model_request(
            {"messages": [{"role": "system", "content": system},
                          {"role": "user", "content": prompt}]}, call_sync,
            purpose="review")["text"]

    async def call_model(self, arguments):
        from tinyassets.providers.model_call import call

        return await self._model_request(arguments, call)

    def _model_request(self, arguments, invoke, *, purpose="reply"):
        """The request carries content only; its authority comes from this launch."""
        from dataclasses import asdict

        from tinyassets.auth.middleware import (
            claim_provider_request,
            mint_provider_request_carrier,
            reserve_provider_request,
            revoke_provider_request,
        )
        from tinyassets.providers.agent_inference import AgentInferenceRequest
        from tinyassets.providers.base import ModelConfig, UniverseContext
        from tinyassets.providers.model_call import ModelCall, ModelContext, Parent
        from tinyassets.providers.model_policy import ModelRef
        from tinyassets.request_budget import TurnRequestBudget
        from tinyassets.storage.agent_request_usage import UsageStore

        def model_result(completed):
            result = {"text": completed.text, "receipt": asdict(completed.receipt)}
            reply = completed.response.agent_reply
            if reply is not None and reply.tool_requests:
                result["tool_requests"] = [
                    {"call_id": item.call_id, "name": item.name,
                     "arguments": item.arguments()}
                    for item in reply.tool_requests]
            return result

        if not self.model_reference:
            raise PermissionError("this launch has no model authority")
        if not isinstance(arguments, dict) or set(arguments) - {
                "model", "fallbacks", "messages", "tools"}:
            raise ValueError("model arguments cannot name owner, usage, turn or carrier")
        messages = arguments.get("messages")
        if (not isinstance(messages, list) or not messages or
                any(not isinstance(item, dict) or set(item) != {"role", "content"}
                    or item["role"] not in {"system", "user", "assistant"}
                    or not isinstance(item["content"], str) for item in messages)):
            raise ValueError("model messages must be role/content text objects")
        tools = arguments.get("tools", [])
        if not isinstance(tools, list):
            raise ValueError("model tools must be a list")
        def parse_ref(value):
            if not isinstance(value, str) or "/" not in value:
                raise ValueError("model must be a connection/model reference")
            source, model_id = value.split("/", 1)
            if not source or not model_id:
                raise ValueError("model must be a connection/model reference")
            return ModelRef(source, model_id)
        model = parse_ref(arguments["model"]) if "model" in arguments else None
        fallbacks = arguments.get("fallbacks", [])
        if not isinstance(fallbacks, list):
            raise ValueError("model fallbacks must be a list")
        refs = tuple(parse_ref(value) for value in fallbacks)
        row = UsageStore(self.root.parent).resolve_launch_reference(
            self.context.owner, self.context.universe, self.model_reference)
        from tinyassets.providers.served_model_plan import connected_sources

        live = connected_sources(self.root.parent, self.root, self.context.owner)
        admitted = live
        if row["authority_kind"] == "run":
            from tinyassets.provider_serving_binding import resolve_serving_agent_binding
            from tinyassets.provider_work_authority import ProviderWorkReceiptState
            from tinyassets.storage.provider_work_authority import (
                SQLiteProviderWorkAuthorityStore,
            )

            receipt = SQLiteProviderWorkAuthorityStore(
                self.root.parent).get_receipt(row["agent_binding_id"])
            if (receipt is None or receipt.state is not ProviderWorkReceiptState.ACTIVE
                    or receipt.principal_id != self.context.owner
                    or receipt.universe_id != self.context.universe
                    or receipt.work_item_id != row["authority_id"]):
                raise PermissionError("run model authority has ended")
            # The 2.4 receipt adds admitted_sources. Until it lands, live
            # connected sources are the narrowest source set available here.
            if hasattr(receipt, "admitted_sources"):
                sources = receipt.admitted_sources
                if not sources:
                    raise PermissionError("run has no admitted model sources")
                admitted = live.intersection(sources)
            binding = resolve_serving_agent_binding(
                self.root.parent, universe_id=self.context.universe,
                owner_user_id=self.context.owner)
            binding_id = str(binding["agent_binding_id"])
            binding_revision = int(binding["revision"])
        else:
            from tinyassets.custom_agents import get_binding

            binding_id = row["agent_binding_id"]
            binding_revision = row["binding_revision"]
            binding = get_binding(self.root.parent, universe_id=self.context.universe,
                                  binding_id=binding_id)
            if (binding is None or binding["created_by"] != self.context.owner
                    or binding["revision"] != binding_revision):
                raise PermissionError("turn model binding has ended")
        for ref in ((model,) if model is not None else ()) + refs:
            if ref.connection_id not in admitted:
                raise PermissionError("requested model is not an admitted live source")
        if model is None:
            from tinyassets.providers.served_model_plan import prepare_owned_model_plan

            prepared = prepare_owned_model_plan(
                base=self.root.parent, universe=self.root, owner=self.context.owner,
                agent=binding, needs_tools=bool(tools))
            model = next((candidate.ref for candidate in prepared.plan.order(
                self.context.owner, self.context.universe).candidates
                if candidate.ref.connection_id in admitted), None)
            if model is None:
                raise PermissionError("allocation has no admitted live model")
        if tools and not model.connection_id.startswith("api_key_http:"):
            raise ValueError("model tools require an HTTP model connection")
        budget = TurnRequestBudget.join(self.root.parent, self.context.owner,
                                        self.context.universe, row["usage_id"])
        reserve = reserve_provider_request(
            principal_id=self.context.owner, session_id=row["authority_id"],
            request_id=secrets.token_hex(16), tool_name="converse")
        capability = claim_provider_request(reserve, tool_name="converse")
        try:
            carrier = mint_provider_request_carrier(
                universe_id=self.context.universe,
                agent_binding_id=binding_id,
                binding_revision=binding_revision, operation="converse")
            system = "\n".join(item["content"] for item in messages
                               if item["role"] == "system")
            prompt = "\n".join(f"{item['role']}: {item['content']}" for item in messages
                               if item["role"] != "system")
            request = ModelCall(
                model=model, fallbacks=refs, purpose=purpose, origin="ta",
                context=ModelContext(
                    prompt=prompt, system=system,
                    config=ModelConfig(
                        request_budget=budget, text_only=not tools,
                        agent_request=AgentInferenceRequest(tools=tools)
                        if tools else None, engine_mcp_enabled=bool(tools)),
                    launch_reference=self.model_reference,
                    universe_context=UniverseContext(
                        universe_dir=self.root, provider_request=carrier,
                        model_selection=model), operation="converse"))
            result = invoke(request, Parent(
                owner=self.context.owner, command_center=self.context.universe,
                usage_id=row["usage_id"]))
            if asyncio.iscoroutine(result):
                deferred = True
                async def finish():
                    try:
                        completed = await result
                        return model_result(completed)
                    finally:
                        revoke_provider_request(capability)
                return finish()
            return model_result(result)
        finally:
            if not locals().get("deferred", False):
                revoke_provider_request(capability)


class JailBridge:
    """Short-lived Unix socket in a private host directory, bound by exact path.

    One reader bounds concurrent daemon work. Requests never supply context,
    headers, a bearer or a route. Closing the bash invocation revokes the socket.
    """

    def __init__(self, dispatch, *, universe_dir=None):
        if universe_dir is None:
            raise ValueError("capability bridge requires an admitted command center")
        self.universe_dir = Path(universe_dir)
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
        from tinyassets import role_relays
        from tinyassets.providers.provider_jail import UNIVERSE_SIDECARS_DIR

        self.path = (self.universe_dir.parent / UNIVERSE_SIDECARS_DIR
                     / self.universe_dir.name / ("ta-" + secrets.token_hex(16) + ".sock"))
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._socket_identity = role_relays.bind(self._server, self.path)
            self._server.listen(8)
        except BaseException:
            self._server.close()
            self._directory.cleanup()
            raise
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
        self._thread.join(timeout=1)
        from tinyassets import role_relays

        try:
            role_relays.remove(self.path, self._socket_identity)
        finally:
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
    from tinyassets.engine_steering import _session_key, launch_model_reference, launch_tools
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
                           model_reference=launch_model_reference(),
                           capability_grant=granted,
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
