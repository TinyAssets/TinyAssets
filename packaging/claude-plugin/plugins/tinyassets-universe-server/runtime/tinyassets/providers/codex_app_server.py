"""Codex translation of the one agent definition: ``codex app-server``.

The served agent turn runs Codex as a JSON-RPC app server whose ONLY
model-visible tools are the definition's tools, declared as
``thread/start.dynamicTools``. Codex executes none of them: each call arrives
here as an ``item/tool/call`` request and is forwarded through the same
owner-pinned engine route every other provider's tools use
(``engine_tool_client.open_engine_tools``), so every Codex tool call crosses
our route and its pre-tool fences.

No MCP server is configured, so Codex adds none of its MCP resource tools
(``codex-rs/core/src/tools/spec_plan.rs`` ``add_mcp_resource_tools``,
rust-v0.160.0, added whenever any MCP server is configured). The bundled model
catalog pins code mode (``apply_patch`` inside ``exec``) and the
collaboration tools to every GPT-5.6+/GPT-6 model, and no flag removes them,
so the launch supplies ``model_catalog_json`` with those per-model tool fields
cleared. A signed-in ChatGPT session keeps that catalog as authoritative
(``model/list`` still returns it after a live turn; 2026-10-05, codex-cli
0.160.0). The captures and the live check are recorded in
``openspec/changes/starter-agent-out-of-plumbing/implementation-evidence.md``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from tinyassets.exceptions import (
    InteractiveDeadlineError,
    ProviderError,
    ProviderIdleTimeoutError,
    ProviderProtocolError,
)
from tinyassets.providers import codex_launch_contract as contract
from tinyassets.providers.codex_launch_contract import SERVED_LAUNCH_ARGS

__all__ = ["SERVED_LAUNCH_ARGS", "AppServerTurn", "reduced_catalog"]

logger = logging.getLogger(__name__)

#: Methods a client may answer; anything else Codex asks is declined.
_TOOL_CALL = "item/tool/call"


def reduced_catalog(bundled: dict, model: str | None) -> dict:
    """``codex_launch_contract.reduced_catalog``, failing as a provider error."""
    try:
        return contract.reduced_catalog(bundled, model)
    except ValueError as exc:
        raise ProviderError(str(exc)) from None


_BUNDLED: dict[tuple, dict] = {}


async def bundled_catalog(base_cmd: list[str]) -> dict:
    """``codex debug models --bundled`` for this binary, cached by its stat.

    Static data shipped in the binary, read in the owner's provider cell with
    no credential in its environment, so no account state is read.
    """
    from tinyassets.providers.codex_provider import _resolved_codex_executable
    from tinyassets.providers.owned_process import aspawn_owned, kill_owned_tree

    try:
        real, _ = _resolved_codex_executable(base_cmd)
        stat = real.stat()
        key = (str(real), stat.st_size, stat.st_mtime_ns)
    except (OSError, ProviderError, ValueError):
        key = (tuple(base_cmd), None, None)
    if key in _BUNDLED:
        return _BUNDLED[key]
    proc = await aspawn_owned(
        [*base_cmd, "debug", "models", "--bundled"], env={},
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(60):
            stdout, _stderr = await proc.communicate()
        if proc.returncode != 0:
            raise ProviderError("codex bundled model catalog is unavailable")
        catalog = json.loads(stdout)
    except (OSError, TimeoutError, ValueError) as exc:
        raise ProviderError("codex bundled model catalog is unavailable") from exc
    finally:
        kill_owned_tree(proc)
    if not isinstance(catalog, dict):
        raise ProviderError("codex bundled model catalog is malformed")
    _BUNDLED[key] = catalog
    return catalog


def dynamic_tools(definition) -> list[dict]:
    """The definition's tools in ``DynamicToolSpec`` form, nothing added."""
    return [{"type": "function", "name": tool.name, "description": tool.description,
             "inputSchema": tool.input_schema} for tool in definition.tools]


def tools_digest(definition) -> str:
    """Identifies the tool set a stored thread was started with."""
    body = json.dumps(dynamic_tools(definition), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def thread_resume_params(definition, thread_id: str) -> dict:
    """``thread/resume`` with the definition's CURRENT instructions.

    A resumed thread otherwise keeps the instructions it started with: on
    codex-cli 0.160.0 a resume without ``baseInstructions`` sends the stored
    ones, and one with them sends only the new ones (request capture,
    2026-10-06, K2 implementation evidence). The tools persist with the
    thread, and a changed tool set never resumes (``tools_digest``).
    """
    return {"threadId": thread_id, "baseInstructions": definition.instructions}


def thread_start_params(definition, *, model: str | None, cwd: str, ephemeral: bool) -> dict:
    """``thread/start`` carrying exactly the definition's tools and instructions."""
    params = {
        "dynamicTools": dynamic_tools(definition),
        "baseInstructions": definition.instructions,
        "cwd": cwd, "ephemeral": ephemeral,
        # Codex runs nothing itself, so neither its approvals nor its own
        # sandbox has anything to govern; our jail confines the process.
        "approvalPolicy": "never", "sandbox": "danger-full-access",
    }
    if model:
        params["model"] = model
    return params


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


def _scrubbed(text: str, limit: int = 300) -> str:
    """Server-supplied text with secrets removed BEFORE clipping: it reaches logs."""
    from tinyassets.providers.codex_provider import _redacted_stderr_excerpt

    return _redacted_stderr_excerpt(" ".join(str(text).splitlines()), limit=limit)


@dataclass
class TurnOutcome:
    """What one served app-server turn produced."""

    thread_id: str = ""
    configured_model: str = ""
    messages: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    usage_seen: bool = False
    status: str = ""
    error: str = ""
    tool_calls: int = 0


class AppServerTurn:
    """Drive one turn over the app server's stdio JSON-RPC.

    Timing follows the exec reader it replaces: before the first server
    message the launch budget applies; inside the turn silence is generation
    (``turn_wait``); while one of OUR tool calls is running the allowance is
    ``tool_wait``; the absolute cap bounds the whole turn. Only protocol
    messages are progress, never unparsable output. ``turn/completed`` ends
    it -- the process is ours and is ended by the caller.

    Tool calls run one at a time, in the order Codex asked for them, so a call
    queued behind one that stops an activity meets the engine route's fence
    after the stop. A tool call that fails outside the engine client's typed
    errors fails the turn with that cause instead of leaving Codex waiting.
    """

    def __init__(self, proc, *, tools, profile, start: float,
                 turn_wait: float, tool_wait: float) -> None:
        self.proc = proc
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
        self._heard = False
        self._in_turn = False
        self._done = asyncio.Event()
        self._failed = asyncio.Event()
        self._failure: BaseException | None = None
        self._last_error = ""

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
            if method == _TOOL_CALL:
                self._spawn(self._call_tool(message["id"], params), self._tool_tasks)
            else:
                # Approvals, user input, elicitation, token refresh: none of
                # these exist in the definition, so none is ever granted.
                self._spawn(self._write({"id": message["id"], "error": {
                    "code": -32601, "message": f"{method} is not available"}}),
                    self._other_tasks)
            return
        if method == "turn/started":
            self._in_turn = True
        elif method == "error":
            # Reported, never terminal: Codex may be retrying. Kept only as the
            # reason for a failed turn that names none of its own.
            error = params.get("error") or {}
            if isinstance(error, dict) and error.get("message"):
                self._last_error = str(error["message"])
        elif method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                self.outcome.messages.append(item["text"])
        elif method == "thread/tokenUsage/updated":
            last = (params.get("tokenUsage") or {}).get("last") or {}
            try:
                self.outcome.input_tokens += int(last.get("inputTokens", 0))
                self.outcome.output_tokens += (int(last.get("outputTokens", 0))
                                               + int(last.get("reasoningOutputTokens", 0)))
                self.outcome.usage_seen = True
            except (TypeError, ValueError):
                pass
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            self.outcome.status = str(turn.get("status") or "")
            error = turn.get("error") or {}
            if isinstance(error, dict):
                self.outcome.error = str(error.get("message") or "")
            if self.outcome.status != "completed" and not self.outcome.error:
                self.outcome.error = self._last_error
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
        idle_deadline = self._last_progress + allow
        absolute = self.start + self.profile.absolute_cap_s
        return min(idle_deadline, absolute) - now, absolute <= idle_deadline

    def _attach(self, exc: ProviderError) -> ProviderError:
        """Name where the turn was when it stopped, as the exec reader did."""
        exc.attempt_telemetry = {
            "provider": "codex",
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
            f"codex tool call failed unexpectedly: {type(self._failure).__name__}"))
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
                        "codex app-server line exceeded the reader buffer limit")) from None
                if not line:
                    for future in self._pending.values():
                        if not future.done():
                            future.set_exception(ProviderError("codex app-server exited"))
                    raise self._attach(
                        ProviderError("codex app-server exited before the turn completed"))
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
                f"codex exceeded the {self.profile.absolute_cap_s:.0f}s absolute "
                "interactive cap while still progressing"))
        raise self._attach(ProviderIdleTimeoutError(
            "codex app-server produced no protocol event within its allowance "
            "(idle watchdog fired; no provider cooldown)"))

    async def run(self, *, thread: tuple[str, dict], input_text: str,
                  effort: str | None) -> TurnOutcome:
        """``thread`` is ``("thread/start", params)`` or ``("thread/resume", params)``."""
        reader = asyncio.create_task(self.read())
        try:
            async def call(method, params):
                waiter = asyncio.create_task(self.request(method, params))
                try:
                    done, _ = await asyncio.wait({waiter, reader},
                                                 return_when=asyncio.FIRST_COMPLETED)
                    if waiter not in done:
                        reader.result()  # raises the reader's classified stop
                        raise ProviderError(f"codex app-server ended during {method}")
                    reply = waiter.result()
                finally:
                    # Never leave a request behind: not after a reader stop, and
                    # not when the caller cancels mid-handshake.
                    if not waiter.done():
                        waiter.cancel()
                        await asyncio.wait({waiter}, timeout=1)
                if "error" in reply:
                    message = (reply.get("error") or {}).get("message") or ""
                    raise ProviderError(
                        f"codex app-server refused {method}: {_scrubbed(message)}")
                return reply.get("result") or {}

            await call("initialize", {"clientInfo": {"name": "tinyassets", "version": "1"},
                                      "capabilities": {"experimentalApi": True}})
            await self.notify("initialized", {})
            started = await call(*thread)
            record = started.get("thread") or {}
            self.outcome.thread_id = str(record.get("id") or "")
            # The model the thread is configured with -- the CLI's own pick when
            # none was requested. Configuration, never answering-model evidence.
            self.outcome.configured_model = _label(record.get("model") or started.get("model"))
            if not self.outcome.thread_id:
                raise ProviderProtocolError("codex app-server returned no thread id")
            turn: dict[str, Any] = {"threadId": self.outcome.thread_id,
                                    "input": [{"type": "text", "text": input_text}]}
            if effort:
                turn["effort"] = effort
            await call("turn/start", turn)
            self._in_turn = True
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
