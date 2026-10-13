"""Bounded JSON-RPC job stream for a manifest-declared external agent."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from dataclasses import dataclass, field

from tinyassets.exceptions import (
    InteractiveDeadlineError,
    ProviderError,
    ProviderIdleTimeoutError,
    ProviderProtocolError,
)
from tinyassets.external_agents.contract import expand_template, pointer


def dynamic_tools(definition) -> list[dict]:
    """The definition's tools in ``DynamicToolSpec`` form, nothing added."""
    return [{"type": "function", "name": tool.name, "description": tool.description,
             "inputSchema": tool.input_schema} for tool in definition.tools]


def tools_digest(definition) -> str:
    """Identifies the tool set a stored thread was started with."""
    body = json.dumps(dynamic_tools(definition), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def method_params(operation: dict, values: dict) -> dict:
    """Expand only the placeholders declared by the package contract."""
    return expand_template(operation["params"], values)


def refusal_category(detail) -> str:
    """Classify upstream errors without carrying their text into a receipt."""
    message = (detail.get("message", "") if isinstance(detail, dict)
               else str(detail)).lower()
    if any(word in message for word in ("temporarily unavailable", "capacity",
                                        "high demand", "high-demand")):
        return "transient_capacity"
    if (any(word in message for word in ("model not found", "unknown model",
                                          "model unavailable", "unsupported model"))
            or ("model" in message and any(word in message for word in (
                "not available", "unavailable", "not supported", "does not exist")))):
        return "model_unavailable"
    if any(word in message for word in ("permission denied", "access denied", "forbidden")):
        return "permission_denied"
    if any(word in message for word in ("rate limit", "too many requests")):
        return "rate_limited"
    if "invalid" in message:
        return "invalid_request"
    return "unknown"


def tool_result_items(result) -> tuple[bool, list[dict]]:
    """An MCP ``CallToolResult`` as ``DynamicToolCallResponse`` content."""
    items: list[dict] = []
    for block in getattr(result, "content", None) or ():
        kind = getattr(block, "type", "")
        if kind == "text":
            items.append({"type": "inputText", "text": block.text})
        elif kind == "image":
            items.append({"type": "inputImage",
                          "imageUrl": f"data:{block.mimeType};base64,{block.data}"})
        else:
            dumped = block.model_dump(mode="json") if hasattr(block, "model_dump") else block
            items.append({"type": "inputText", "text": json.dumps(dumped)})
    structured = getattr(result, "structuredContent", None)
    if not items and structured is not None:
        items.append({"type": "inputText", "text": json.dumps(structured)})
    if not items:
        items.append({"type": "inputText", "text": ""})
    return not bool(getattr(result, "isError", False)), items


def _label(value) -> str:
    from tinyassets.providers.execution_receipt import _label as label

    return label(value, 200)


def _failure_items(text: str) -> dict:
    return {"success": False, "contentItems": [{"type": "inputText", "text": text}]}


@dataclass
class TurnOutcome:
    """What one served app-server turn produced."""

    thread_id: str = ""
    configured_model: str = ""
    reported_model: str = ""
    messages: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    usage_seen: bool = False
    status: str = ""
    auth_rejected: bool = False
    tool_calls: int = 0


class JsonRpcJob:
    """One bounded native JSON-RPC turn, with serial tool calls and timeouts.

    Only authenticated progress extends idle time; the caller owns teardown.
    """

    def __init__(self, proc, *, job: dict, agent: str, tools, profile,
                 start: float, turn_wait: float, tool_wait: float) -> None:
        self.proc = proc
        self.job = job
        self.agent = agent
        self.tools = tools
        self.profile = profile
        self.start = start
        self.turn_wait = turn_wait
        self.tool_wait = tool_wait
        self.outcome = TurnOutcome()
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._write_lock = asyncio.Lock()
        self._tool_lock = asyncio.Lock()
        self._tool_tasks: set[asyncio.Task] = set()
        self._other_tasks: set[asyncio.Task] = set()
        self._last_progress = start
        self._last_liveness = start
        self._heard = False
        self._in_turn = False
        self._done = asyncio.Event()
        self._failed = asyncio.Event()
        self._failure: BaseException | None = None
        self.phase = "initialize"

    async def _write(self, message: dict) -> None:
        async with self._write_lock:
            self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            await self.proc.stdin.drain()

    async def request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        ident = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[ident] = future
        try:
            await self._write({"id": ident, "method": method, "params": params})
            return await future
        finally:
            self._pending.pop(ident, None)

    async def notify(self, method: str, params: dict) -> None:
        await self._write({"method": method, "params": params})

    async def _call_tool(self, ident, params: dict) -> None:
        from tinyassets.engine_tool_client import EngineToolError

        async with self._tool_lock:
            self.outcome.tool_calls += 1
            name, arguments = params.get("tool"), params.get("arguments")
            try:
                if params.get("namespace") or not isinstance(name, str):
                    response = _failure_items(f"unknown tool {name!r}")
                elif not isinstance(arguments, dict):
                    response = _failure_items("tool arguments must be an object")
                else:
                    success, items = tool_result_items(await self.tools.call(name, arguments))
                    response = {"success": success, "contentItems": items}
            except EngineToolError as exc:
                response = _failure_items(f"tool call failed: {exc.code}")
            except Exception:
                # Answer Codex so it is not left waiting, then fail the turn
                # with the real cause (``_settle``).
                with contextlib.suppress(Exception):
                    await self._write({"id": ident, "result": _failure_items(
                        "tool call failed: internal error")})
                raise
            try:
                await self._write({"id": ident, "result": response})
            finally:
                self._last_progress = time.monotonic()

    def _spawn(self, coro, tasks: set[asyncio.Task]) -> None:
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(lambda done: self._settle(done, tasks))

    def _settle(self, task: asyncio.Task, tasks: set[asyncio.Task]) -> None:
        tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None and self._failure is None:
            self._failure = error
            self._failed.set()

    def _dispatch(self, message: dict) -> None:
        method = message.get("method")
        if method is None:
            future = self._pending.get(message.get("id"))
            if future is not None and not future.done():
                future.set_result(message)
            return
        self._last_progress = time.monotonic()
        self._heard = True
        params = message.get("params") or {}
        if "id" in message:
            if method == self.job["tools"]["call_method"]:
                self._spawn(self._call_tool(message["id"], params), self._tool_tasks)
            else:
                # Approvals, user input, elicitation, token refresh: none of
                # these exist in the definition, so none is ever granted.
                self._spawn(self._write({"id": message["id"], "error": {
                    "code": -32601, "message": f"{method} is not available"}}),
                    self._other_tasks)
            return
        events = self.job["events"]
        if method == events["model"]["method"]:
            self._in_turn = True
            reported = pointer(params, events["model"]["value"])
            if isinstance(reported, str):
                self.outcome.reported_model = _label(reported)
        elif method == events.get("item_done", {}).get("method"):
            event = events["item_done"]
            if pointer(params, event["type"]) == event["agent_type"]:
                message = pointer(params, event["text"])
                if isinstance(message, str):
                    self.outcome.messages.append(message)
        elif method == events["usage"]["method"]:
            usage = events["usage"]
            try:
                self.outcome.input_tokens += int(pointer(params, usage["input"]) or 0)
                self.outcome.output_tokens += int(pointer(params, usage["output"]) or 0)
                if "reasoning_output" in usage:
                    self.outcome.output_tokens += int(
                        pointer(params, usage["reasoning_output"]) or 0)
                self.outcome.usage_seen = True
            except (TypeError, ValueError):
                pass
        elif method == events["done"]["method"]:
            event = events["done"]
            self.outcome.status = str(pointer(params, event["status"]) or "")
            self.outcome.auth_rejected = (
                "auth_error" in event
                and pointer(params, event["auth_error"]) == event["auth_code"]
            )
            if not self.outcome.messages and "items" in event:
                items = pointer(params, event["items"])
                if isinstance(items, list):
                    for item in items:
                        if (isinstance(item, dict)
                                and pointer(item, event["item_type"]) == event["agent_type"]):
                            message = pointer(item, event["item_text"])
                            if isinstance(message, str):
                                self.outcome.messages.append(message)
            # The turn is over: nothing it started is still owed an answer.
            for task in list(self._tool_tasks):
                task.cancel()
            self._done.set()

    def _allowance(self) -> tuple[float, bool]:
        now = time.monotonic()
        if self._tool_tasks:
            allow = min(self.profile.absolute_cap_s, self.tool_wait)
        elif self._in_turn or self._heard:
            allow = min(self.profile.absolute_cap_s, self.turn_wait)
        else:
            allow = self.profile.init_s
        idle_deadline = max(self._last_progress, self._last_liveness) + allow
        absolute = self.start + self.profile.absolute_cap_s
        return min(idle_deadline, absolute) - now, absolute <= idle_deadline

    def _attach(self, exc: ProviderError) -> ProviderError:
        """Name where the turn was when it stopped, as the exec reader did."""
        exc.attempt_telemetry = {
            "provider": self.agent,
            "failure_class": getattr(exc, "failure_class", None),
            "phase": "streaming" if self._heard else "launch",
            "tool_phase": ("in_tool" if self._tool_tasks
                           else "in_turn" if self._in_turn and not self._done.is_set()
                           else None),
            "last_progress_age_ms": (time.monotonic() - self._last_progress) * 1000,
            "exit_code": getattr(self.proc, "returncode", None),
        }
        return exc

    def _tool_failure(self) -> ProviderError:
        error = self._attach(ProviderError(
            f"{self.agent} tool call failed unexpectedly: {type(self._failure).__name__}"))
        error.__cause__ = self._failure
        return error

    async def read(self) -> None:
        """Read until ``turn/completed``; raise the classified stop otherwise."""
        failed = asyncio.ensure_future(self._failed.wait())
        line_task: asyncio.Future | None = None
        try:
            while not self._done.is_set():
                if self._failure is not None:
                    raise self._tool_failure()
                budget, absolute = self._allowance()
                if budget <= 0:
                    from tinyassets.providers.owned_process import cell_heartbeat

                    if not absolute and self._in_turn and await cell_heartbeat(self.proc):
                        self._last_liveness = time.monotonic()
                        continue
                    self._timeout(absolute)
                if line_task is None:
                    line_task = asyncio.ensure_future(self.proc.stdout.readline())
                done, _ = await asyncio.wait({line_task, failed}, timeout=budget,
                                             return_when=asyncio.FIRST_COMPLETED)
                if line_task not in done:
                    continue
                finished, line_task = line_task, None
                try:
                    line = finished.result()
                except (ValueError, asyncio.LimitOverrunError):
                    raise self._attach(ProviderProtocolError(
                        "external-agent JSON-RPC line exceeded the reader buffer limit")) from None
                if not line:
                    for future in self._pending.values():
                        if not future.done():
                            future.set_exception(ProviderError("external agent exited"))
                    raise self._attach(
                        ProviderError("external agent exited before the turn completed"))
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    self._dispatch(message)
            if self._failure is not None:
                raise self._tool_failure()
        finally:
            leftovers = [task for task in (failed, line_task)
                         if task is not None and not task.done()]
            for task in leftovers:
                task.cancel()
            if leftovers:
                await asyncio.wait(leftovers, timeout=1)

    def _timeout(self, absolute: bool) -> None:
        if absolute:
            raise self._attach(InteractiveDeadlineError(
                f"{self.agent} exceeded the {self.profile.absolute_cap_s:.0f}s absolute "
                "interactive cap while still progressing"))
        raise self._attach(ProviderIdleTimeoutError(
            f"{self.agent} produced no protocol event within its allowance "
            "(idle watchdog fired; no provider cooldown)"))

    async def run(self, *, definition, model: str | None, input_text: str,
                  effort: str | None, workdir: str, resume: str = "") -> TurnOutcome:
        """Start or resume a package-declared JSON-RPC native session."""
        reader = asyncio.create_task(self.read())
        try:
            async def call(method, params):
                waiter = asyncio.create_task(self.request(method, params))
                try:
                    done, _ = await asyncio.wait({waiter, reader},
                                                 return_when=asyncio.FIRST_COMPLETED)
                    if waiter not in done:
                        reader.result()  # raises the reader's classified stop
                        raise ProviderError(f"external agent ended during {method}")
                    reply = waiter.result()
                finally:
                    # Never leave a request behind: not after a reader stop, and
                    # not when the caller cancels mid-handshake.
                    if not waiter.done():
                        waiter.cancel()
                        await asyncio.wait({waiter}, timeout=1)
                if "error" in reply:
                    detail = reply["error"]
                    message = (detail.get("message", "") if isinstance(detail, dict)
                               else str(detail)).lower()
                    if any(marker in message for marker in (
                        "invalid_grant", "unauthorized", "not logged in",
                        "authentication required", "refresh token rejected",
                    )):
                        from tinyassets.exceptions import ProviderAuthenticationError

                        raise ProviderAuthenticationError(
                            "external agent needs a fresh sign-in")
                    raise ProviderError(
                        f"external agent refused {method}:{refusal_category(detail)}")
                return reply.get("result") or {}

            init = self.job["initialize"]
            self.phase = init["method"]
            await call(init["method"], init["params"])
            await self.notify(init["notification"], {})
            values = {"model": model, "effort": effort, "workdir": workdir,
                      "session": resume, "input": [{"type": "text", "text": input_text}],
                      "system": definition.instructions,
                      "mcp_config": None}
            phase = self.job["resume"] if resume else self.job["start"]
            self.phase = phase["method"]
            params = method_params(phase, values)
            if not resume:
                params[self.job["tools"]["define_at"].removeprefix("/")] = dynamic_tools(definition)
                if model is None:
                    params.pop("model", None)
            started = await call(phase["method"], params)
            self.outcome.thread_id = str(pointer(started, self.job["session_handle"]) or "")
            # The model the thread is configured with -- the CLI's own pick when
            # none was requested. Configuration, never answering-model evidence.
            self.outcome.configured_model = _label(
                pointer(started, self.job["configured_model"]))
            if not self.outcome.thread_id:
                raise ProviderProtocolError("external agent returned no session handle")
            values["session"] = self.outcome.thread_id
            turn = method_params(self.job["input"], values)
            if not effort:
                turn.pop("effort", None)
            self.phase = self.job["input"]["method"]
            await call(self.phase, turn)
            self._in_turn = True
            self.phase = "stream"
            await reader
            return self.outcome
        finally:
            if not reader.done():
                reader.cancel()
            helpers = [*self._tool_tasks, *self._other_tasks]
            for task in helpers:
                task.cancel()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.gather(
                    reader, *helpers, return_exceptions=True), timeout=5)
