"""The four box tools, forwarded to the turn's bound box by ``op_id``.

``read``, ``write``, ``edit`` and ``bash`` are the same four tools as the tool
jail serves today (:mod:`tinyassets.universe_tools`), with the same arguments
and the same text answers. What changes is where they run: each call is one
``start_exec`` on the command center's box through the :class:`BoxProvider`
(target architecture D2), never a process the loop starts itself.

Binding. The session is constructed with a handle the caller bound ONCE at
turn start (``BoxProvider.bind(cc, account=..., turn=...)``). No call looks a
box up by name, so a loop bug cannot route one owner's tool call into another
owner's box; the box host refuses a handle minted for another command center
or turn as well (D2 "Authentication of every operation").

Lost replies. Every call carries an ``op_id`` derived from the journal
position of the call (turn, round, call). D2 makes ``start_exec`` idempotent by
``op_id``: a retry returns the recorded execution and never re-runs it. So a
transport failure is resolved by asking again with the SAME ``op_id``, once,
and anything still unresolved -- including ``unknown_after_restore`` -- is
reported as an UNKNOWN outcome. The coordinator journals it as unknown and the
turn holds. Nothing here retries with a new ``op_id``.

Cancellation. A cancelled turn cancels the execution in the box
(``BoxProvider.cancel``, which kills the process tree there) before the
cancellation propagates.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, Protocol

from tinyassets.engine_tool_client import EngineToolError

_LOG = logging.getLogger(__name__)

#: The tool names this module serves, in canonical order.
BOX_TOOLS: tuple[str, ...] = ("read", "write", "edit", "bash")

#: Where the command center's content sits inside its box (D2 default ``cwd``).
BOX_ROOT = "/cc"

_MiB = 1024 * 1024
MAX_WRITE_BYTES = 4 * _MiB
MAX_EDIT_BYTES = 1 * _MiB
DEFAULT_READ_LINES = 2000
DEFAULT_WALL_SECONDS = 120.0
MAX_BASH_SECONDS = 600.0
OUTPUT_BYTES = 64 * 1024
#: How long a cancel, or a cancelled execution's end, may take before the turn
#: stops waiting for it (and reports the outcome as unknown).
_CANCEL_GRACE_SECONDS = 5.0
#: Box calls (starts and stream reads) that may be in flight in this process.
#: A call the box host never answers keeps its slot, so a host that stops
#: answering exhausts this bound and new calls are refused loudly, instead of
#: threads accumulating without limit.
MAX_BOX_CALLS = 512
_BOX_CALL_SLOTS = threading.BoundedSemaphore(MAX_BOX_CALLS)
#: Cancels in flight, bounded separately so a stuck cancel can never take the
#: slot a new call needs. With none free, a cancel is not sent and the outcome
#: is reported unknown (the turn holds) rather than spawning another thread.
MAX_BOX_CANCELS = 128
_BOX_CANCEL_SLOTS = threading.BoundedSemaphore(MAX_BOX_CANCELS)


class BoxOperationRefused(RuntimeError):
    """The box host refused an operation BEFORE it existed.

    The one exception a driver may raise to say "nothing ran": a stale
    placement epoch, a handle for another command center, an account that does
    not own it. Anything else a driver raises is a transport failure, whose
    outcome is unknown until the box host says otherwise.
    """


class BoxExec(Protocol):
    """The part of ``BoxProvider`` (D2) the box tools use. Structural, so the
    real provider satisfies it without importing this module."""

    def start_exec(self, h: Any, op_id: str, argv: Sequence[str], *,
                   stdin: Any = None, env: Mapping[str, str] = ..., cwd: str = ...,
                   limits: Any, interactive_stdin: bool = False) -> Any: ...

    def send_stdin(self, h: Any, exec_id: Any, request_id: str, data: bytes) -> None: ...

    def stream(self, h: Any, exec_id: Any, *, from_offset: int = 0) -> Iterator[Any]: ...

    def cancel(self, h: Any, exec_id: Any) -> None: ...

    def exec_status(self, h: Any, op_id: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class ExecOutcome:
    """What one execution reported: merged output, exit code, and why it was cut."""

    output: bytes
    exit_code: int | None
    killed: str | None = None


def _unknown() -> EngineToolError:
    return EngineToolError("box_tool_outcome_unknown", outcome="unknown")


def _event_kind(event: Any) -> str:
    return str(getattr(event, "kind", "") or "")


def _event_offset(event: Any, fallback: int) -> int:
    offset = getattr(event, "offset", None)
    return offset if type(offset) is int and offset >= 0 else fallback


class BoxExecutor:
    """Runs argv in ONE bound box by ``op_id``; the only door the tools use."""

    def __init__(self, provider: BoxExec, handle: Any, *, limits: Any,
                 cwd: str = BOX_ROOT) -> None:
        if handle is None:
            raise ValueError("a bound box handle is required")
        self._provider = provider
        self._handle = handle
        self._limits = limits
        self._cwd = cwd
        self.ta_bridge = None

    @property
    def handle(self) -> Any:
        return self._handle

    def _start(self, op_id: str, argv: Sequence[str], stdin: bytes | None) -> Any:
        kwargs: dict[str, Any] = {"cwd": self._cwd, "limits": self._limits}
        if self.ta_bridge is not None and op_id in self._ta_executions:
            from tinyassets.ta_capabilities import MAX_REQUEST

            # Requests travel on stdout too. Keep a bounded transport allowance
            # separate from the decoded user-output limit enforced by _collect.
            kwargs["limits"] = replace(self._limits,
                                       output_bytes=max(self._limits.output_bytes,
                                                        8 * MAX_REQUEST))
            kwargs["interactive_stdin"] = True
        if stdin is not None:
            kwargs["stdin"] = stdin
        try:
            return self._provider.start_exec(self._handle, op_id, list(argv), **kwargs)
        except BoxOperationRefused:
            raise
        except Exception:
            # The reply was lost, not necessarily the operation. The same op_id
            # returns the recorded execution and never starts a second one.
            _LOG.warning("box start_exec reply lost; asking again with the same op_id")
            try:
                return self._provider.start_exec(self._handle, op_id, list(argv), **kwargs)
            except BoxOperationRefused:
                # Refused only on the retry: the first attempt may have run.
                raise _unknown() from None
            except Exception:
                raise _unknown() from None

    def _collect(self, exec_id: Any, op_id: str, cap: int) -> ExecOutcome:
        """Read the execution's events to its exit, resuming once by offset.

        Only an ``exit`` event proves the execution ended. Past the output cap
        the execution is cancelled and its remaining output discarded, but the
        read continues until that exit arrives.
        """
        output = bytearray()
        offset = 0
        resumed = False
        capped = False
        pending = bytearray()
        while True:
            try:
                for event in self._provider.stream(self._handle, exec_id, from_offset=offset):
                    kind = _event_kind(event)
                    if kind in ("stdout", "stderr", "output"):
                        data = bytes(getattr(event, "data", b"") or b"")
                        offset = _event_offset(event, offset + len(data))
                        if kind == "output":  # BoxProvider offsets name the block START.
                            offset += len(data)
                        if self.ta_bridge is not None and op_id in self._ta_executions:
                            pending.extend(data)
                            try:
                                data = self._ta_frames(pending, op_id, exec_id)
                            except Exception as exc:
                                _LOG.warning("ta box channel failed (%s)", type(exc).__name__)
                                self.ta_bridge.cancel_execution(op_id)
                                self.cancel_in_background(exec_id)
                                raise _unknown() from None
                        if capped:
                            continue
                        room = cap - len(output)
                        output += data[:max(room, 0)]
                        if len(data) > room:
                            capped = True
                            if self.ta_bridge is not None:
                                self.ta_bridge.cancel_execution(op_id)
                            self._provider.cancel(self._handle, exec_id)
                    elif kind == "exit":
                        code = getattr(event, "code", getattr(event, "exit_code", None))
                        if pending:
                            raise _unknown()
                        if (self.ta_bridge is not None and op_id in self._ta_executions
                                and op_id not in self._ta_ready):
                            raise EngineToolError("remote_ta_worker_unavailable")
                        killed = getattr(event, "killed", None)
                        if killed == "unknown_after_restore":
                            raise _unknown()
                        return ExecOutcome(bytes(output), code if type(code) is int else None,
                                           "output_limit" if capped else killed)
                # A stream that ends without an exit event is a lost reply.
                raise ConnectionError("box stream ended without an exit event")
            except EngineToolError:
                raise
            except Exception:
                if resumed:
                    break
                resumed = True
                _LOG.warning("box stream interrupted; resuming from offset %d", offset)
        status = None
        try:
            status = self._provider.exec_status(self._handle, op_id)
        except Exception:  # noqa: BLE001 - unresolved either way
            pass
        _LOG.warning("box execution outcome unresolved (status %r)", getattr(status, "state", None))
        raise _unknown()

    def enable_ta(self, bridge):
        self.ta_bridge = bridge
        self._ta_executions = set()
        self._ta_ready = set()

    def _ta_frames(self, pending, op_id, exec_id):
        from tinyassets.agent_loop.box_ta import PREFIX
        from tinyassets.ta_capabilities import MAX_REQUEST

        output = bytearray()
        while b"\n" in pending:
            raw, _, rest = pending.partition(b"\n")
            pending[:] = rest
            if not raw.startswith(PREFIX) and op_id not in self._ta_ready:
                output.extend(raw + b"\n")
                continue
            if len(raw) > MAX_REQUEST + 256 or not raw.startswith(PREFIX):
                raise _unknown()
            frame = json.loads(raw[len(PREFIX):])
            if frame == {"ready": True}:
                self._ta_ready.add(op_id)
                continue
            if set(frame) == {"output"}:
                output.extend(base64.b64decode(frame["output"], validate=True))
                continue
            if set(frame) != {"request", "message", "delivery"}:
                raise _unknown()
            request = frame["request"]
            # Validate before constructing a reply path, even for refused calls.
            import re
            if not isinstance(request, str) or not re.fullmatch(r"[a-f0-9]{32}", request):
                raise _unknown()
            delivery = frame["delivery"]
            if not isinstance(delivery, str) or not re.fullmatch(r"[a-f0-9]{32}", delivery):
                raise _unknown()
            answer = self.ta_bridge.request(self._handle, op_id, request, frame["message"])
            if isinstance(answer, dict) and "extension_roots" in answer:
                answer = {**answer, "extension_roots": {
                    scope: path.replace("/u/", self._cwd + "/", 1)
                    for scope, path in answer["extension_roots"].items()}}
            data = json.dumps({"request": request, "answer": answer}).encode() + b"\n"
            for attempt in range(2):
                try:
                    self._provider.send_stdin(self._handle, exec_id, delivery, data)
                    break
                except Exception:
                    # Replaying an already completed execution only needs its
                    # recorded output; its closed stdin cannot receive replies.
                    status = self._provider.exec_status(self._handle, op_id)
                    if status.state == "exited":
                        break
                    if attempt:
                        raise
        if len(pending) > MAX_REQUEST + 256:
            raise _unknown()
        return bytes(output)

    async def run(self, op_id: str, argv: Sequence[str], *, stdin: bytes | None = None,
                  wall_seconds: float, output_bytes: int = OUTPUT_BYTES) -> ExecOutcome:
        """One execution; a timeout or a cancelled turn kills it in the box.

        Every blocking provider call runs on a thread of its own, never a
        shared executor, so a cancel can never queue behind the reads waiting
        for it to land, and every wait on the box is bounded. A result that
        cannot be confirmed as ended is an UNKNOWN outcome, never a completed
        one.
        """
        if not isinstance(op_id, str) or not op_id:
            raise ValueError("an op_id is required")
        launch = _Launch(self, op_id, argv, stdin)
        try:
            exec_id = await _wait(launch.future)
        except asyncio.CancelledError:
            # The box may accept the command after the turn was cancelled.
            # Exactly one side cancels it: this one if the reply is in, else
            # the launching thread when the reply arrives, however late.
            if self.ta_bridge is not None:
                self.ta_bridge.cancel_execution(op_id)
            launch.abandon()
            raise
        try:
            collector = _in_thread(self._collect, exec_id, op_id, output_bytes,
                                   slots=_BOX_CALL_SLOTS)
        except BoxOperationRefused:
            # The command is running; only reading it was refused. Never "not sent".
            await self._cancel(exec_id)
            raise _unknown() from None
        try:
            return await _wait(collector, timeout=wall_seconds)
        except TimeoutError:
            if self.ta_bridge is not None:
                self.ta_bridge.cancel_execution(op_id)
            if not await self._cancel(exec_id):
                raise _unknown() from None
            try:
                outcome = await _wait(collector, timeout=_CANCEL_GRACE_SECONDS)
            except Exception:  # noqa: BLE001 - not confirmed ended
                raise _unknown() from None
            return ExecOutcome(outcome.output, outcome.exit_code, "timeout")
        except asyncio.CancelledError:
            if self.ta_bridge is not None:
                self.ta_bridge.cancel_execution(op_id)
            await self._cancel(exec_id)
            raise

    async def _cancel(self, exec_id: Any) -> bool:
        """Ask the box to kill the execution; ``True`` only if it acknowledged in time."""
        try:
            await _wait(_in_thread(self._provider.cancel, self._handle, exec_id,
                                   slots=_BOX_CANCEL_SLOTS),
                        timeout=_CANCEL_GRACE_SECONDS)
            return True
        except Exception:  # noqa: BLE001 - an unacknowledged cancel is reported as unknown
            _LOG.warning("box cancel was not acknowledged; the outcome is unknown")
            return False

    def cancel_quietly(self, exec_id: Any) -> None:
        """Cancel from a thread that has no turn to report to (it recorded unknown)."""
        try:
            self._provider.cancel(self._handle, exec_id)
        except Exception:  # noqa: BLE001 - the turn already recorded an unknown outcome
            _LOG.warning("box cancel of an abandoned execution failed")

    def cancel_in_background(self, exec_id: Any) -> None:
        """``cancel_quietly`` on a cancel slot's thread; none free, it is logged."""
        if not _BOX_CANCEL_SLOTS.acquire(blocking=False):
            _LOG.warning("no cancel slot free; an abandoned box execution was not cancelled")
            return

        def work() -> None:
            try:
                self.cancel_quietly(exec_id)
            finally:
                _BOX_CANCEL_SLOTS.release()

        try:
            threading.Thread(target=work, name="box-cancel", daemon=True).start()
        except BaseException:
            _BOX_CANCEL_SLOTS.release()
            raise


class _Launch:
    """One ``start_exec`` whose late reply is never orphaned.

    If the turn stops waiting before the box answers, whichever side sees the
    other's state second cancels the execution the box accepted: the turn, if
    the exec id is already in; else the launching thread, when it arrives.
    """

    def __init__(self, executor: BoxExecutor, op_id: str, argv: Sequence[str],
                 stdin: bytes | None) -> None:
        self._executor = executor
        self._lock = threading.Lock()
        self._abandoned = False
        self._exec_id: Any = None
        self._started = False
        self.future = _in_thread(self._run, op_id, argv, stdin, slots=_BOX_CALL_SLOTS)

    def _run(self, op_id: str, argv: Sequence[str], stdin: bytes | None) -> Any:
        exec_id = self._executor._start(op_id, argv, stdin)
        with self._lock:
            self._started, self._exec_id = True, exec_id
            abandoned = self._abandoned
        if abandoned:
            self._executor.cancel_quietly(exec_id)
        return exec_id

    def abandon(self) -> None:
        with self._lock:
            self._abandoned = True
            started, exec_id = self._started, self._exec_id
        if started:
            self._executor.cancel_in_background(exec_id)


async def _wait(future: asyncio.Future, *, timeout: float | None = None) -> Any:
    """Await ``future`` without cancelling it: a timeout or a cancelled caller
    leaves the box call to finish on its own thread, which owns its cleanup."""
    done, _ = await asyncio.wait({future}, timeout=timeout)
    if not done:
        raise TimeoutError
    return future.result()


def _in_thread(fn: Callable[..., Any], /, *args: Any,
               slots: threading.BoundedSemaphore) -> asyncio.Future:
    """Run a blocking call on a fresh daemon thread; its result as a future.

    The call holds one of ``slots`` until it returns; none free means the box
    host has stopped answering, and the call is refused before it is sent.
    """
    if not slots.acquire(blocking=False):
        raise BoxOperationRefused("every box call slot is waiting on the box host")
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    # A call nobody waits for any more must not report its failure as unhandled.
    future.add_done_callback(lambda done: done.cancelled() or done.exception())

    def deliver(setter: Callable[[Any], None], value: Any) -> None:
        if not future.done():
            setter(value)

    def work() -> None:
        try:
            result = fn(*args)
        except BaseException as exc:  # noqa: BLE001 - delivered to the awaiting task
            with contextlib.suppress(RuntimeError):  # the loop already closed
                loop.call_soon_threadsafe(deliver, future.set_exception, exc)
        else:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(deliver, future.set_result, result)
        finally:
            slots.release()

    try:
        threading.Thread(target=work, name="box-op", daemon=True).start()
    except BaseException:
        slots.release()
        raise
    return future


# ── the four tools ──────────────────────────────────────────────────────────


def box_path(path: str, root: str = BOX_ROOT) -> str:
    """The path as the box sees it: relative paths are under the box root.

    No containment check on purpose: the box is the boundary, and a path
    outside the command center does not exist inside it.
    """
    raw = (path or "").strip()
    if not raw:
        raise ValueError("a path is required")
    if "\x00" in raw:
        raise ValueError("a path may not contain a NUL byte")
    candidate = PurePosixPath(raw)
    if not candidate.is_absolute():
        candidate = PurePosixPath(root) / candidate
    return str(candidate)


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def _trailer(outcome: ExecOutcome, wall: float, output_bytes: int) -> str:
    if outcome.killed == "timeout":
        return f"[killed: ran longer than {wall:g}s]"
    if outcome.killed == "output_limit":
        return f"[killed: output passed {output_bytes} bytes]"
    if outcome.exit_code is None:
        return "[exit code unknown]"
    if outcome.exit_code in (128 + 24, 128 + 9):
        return "[killed: a cpu time or memory limit]"
    return f"[exit code {outcome.exit_code}]"


def _failed(outcome: ExecOutcome, wall: float, output_bytes: int) -> str:
    return "error: " + (_text(outcome.output).strip() or _trailer(outcome, wall, output_bytes))


_READ = (
    '[ -e "$1" ] || { echo "no such file: $1"; exit 1; }; '
    'if [ -d "$1" ]; then ls -la -- "$1"; exit $?; fi; '
    'tail -n "+$2" -- "$1" | head -n "$3"'
)
_CAT = '[ -f "$1" ] || { echo "no such file: $1"; exit 1; }; cat -- "$1"'
#: The content lands in a temp file first; the rename then happens under an
#: exclusive ``flock`` on the target's DIRECTORY, so a reader never sees half a
#: file and every write through these tools is ordered. The lock is on the
#: directory, not the file, because the rename replaces the file's inode: a
#: lock on the file would let a waiter on the old inode and a newcomer on the
#: new one run together. ``$2`` is the sha256 the target must still have
#: (edit), checked under the same lock, or empty (write). Two edits, or an
#: edit and a write, can therefore never silently overwrite each other: the
#: later one finds the hash changed and refuses. A process in the box that
#: writes the file WITHOUT the lock (an arbitrary ``bash`` command) is not
#: ordered by it; that residual is the same as for any editor. ``flock``
#: (util-linux) is a box-image requirement; without it the write fails loudly
#: rather than racing.
_WRITE = (
    'd="$(dirname -- "$1")"; '
    '[ -n "$2" ] || mkdir -p -- "$d" || exit 1; '
    't="$1.ta-write.$$"; cat > "$t" || { rm -f -- "$t"; exit 1; }; '
    'flock -x "$d" sh -c '
    '\'if [ -n "$2" ]; then [ -f "$1" ] || exit 4; '
    '[ "$(sha256sum -- "$1" | cut -d" " -f1)" = "$2" ] || exit 3; fi; '
    'mv -f -- "$3" "$1"\' '
    'sh "$1" "$2" "$t"; '
    'rc=$?; [ "$rc" -eq 0 ] && exit 0; rm -f -- "$t"; '
    '[ "$rc" -eq 3 ] && echo "$1 changed while it was being edited; read it again"; '
    '[ "$rc" -eq 4 ] && echo "no such file: $1"; '
    'exit "$rc"'
)


class BoxTools:
    """``read``/``write``/``edit``/``bash`` over one bound box."""

    def __init__(self, executor: BoxExecutor, *, root: str = BOX_ROOT) -> None:
        self._exec = executor
        self._root = root

    async def read(self, op_id: str, path: str, offset: int = 0, limit: int = 0) -> str:
        target = box_path(path, self._root)
        start = max(1, int(offset or 1))
        count = int(limit) if limit and int(limit) > 0 else DEFAULT_READ_LINES
        outcome = await self._exec.run(
            op_id, ["/bin/sh", "-c", _READ, "sh", target, str(start), str(count)],
            wall_seconds=DEFAULT_WALL_SECONDS,
        )
        if outcome.killed == "output_limit":
            return (_text(outcome.output) + f"\n[truncated at {OUTPUT_BYTES} bytes; read a "
                    "smaller range with offset and limit]")
        if outcome.killed or outcome.exit_code != 0:
            return _failed(outcome, DEFAULT_WALL_SECONDS, OUTPUT_BYTES)
        return _text(outcome.output)

    async def _put(self, op_id: str, target: str, payload: bytes, expect: str) -> ExecOutcome:
        return await self._exec.run(
            op_id, ["/bin/sh", "-c", _WRITE, "sh", target, expect],
            stdin=payload, wall_seconds=DEFAULT_WALL_SECONDS,
        )

    async def write(self, op_id: str, path: str, content: str) -> str:
        target = box_path(path, self._root)
        payload = (content or "").encode("utf-8")
        if len(payload) > MAX_WRITE_BYTES:
            return f"error: content is over the {MAX_WRITE_BYTES}-byte write limit"
        outcome = await self._put(op_id, target, payload, "")
        if outcome.killed or outcome.exit_code != 0:
            return _failed(outcome, DEFAULT_WALL_SECONDS, OUTPUT_BYTES)
        return f"wrote {len(payload)} bytes to {target}"

    async def edit(self, op_id: str, path: str, old_text: str, new_text: str) -> str:
        target = box_path(path, self._root)
        if not old_text:
            return "error: old_text is required: the exact passage to replace"
        read = await self._exec.run(
            op_id + "/read", ["/bin/sh", "-c", _CAT, "sh", target],
            wall_seconds=DEFAULT_WALL_SECONDS, output_bytes=MAX_EDIT_BYTES,
        )
        if read.killed == "output_limit":
            return f"error: {target} is over the {MAX_EDIT_BYTES}-byte edit limit; use bash"
        if read.killed or read.exit_code != 0:
            return _failed(read, DEFAULT_WALL_SECONDS, MAX_EDIT_BYTES)
        try:
            current = read.output.decode("utf-8")
        except UnicodeDecodeError:
            return f"error: {target} is not UTF-8 text; use bash"
        found = current.count(old_text)
        if found == 0:
            return f"error: old_text was not found in {target}"
        if found > 1:
            return (f"error: old_text matches {found} places in {target}; include more "
                    "surrounding text so it matches exactly one")
        # Written only if the file still has the bytes just read: a concurrent
        # writer (a bash call, another agent of this command center) wins.
        payload = current.replace(old_text, new_text, 1).encode("utf-8")
        outcome = await self._put(
            op_id + "/write", target, payload, hashlib.sha256(read.output).hexdigest(),
        )
        if outcome.killed or outcome.exit_code != 0:
            return _failed(outcome, DEFAULT_WALL_SECONDS, OUTPUT_BYTES)
        return f"edited {target}"

    async def bash(self, op_id: str, command: str, timeout: float = 0) -> str:
        if not (command or "").strip():
            return "error: a command is required"
        wall = float(timeout) if timeout and float(timeout) > 0 else DEFAULT_WALL_SECONDS
        wall = min(max(wall, 1.0), MAX_BASH_SECONDS)
        # argv, never a command string to the box API: the command is bash's
        # argument, exactly as the tool jail runs it.
        argv = ["/bin/bash", "-c", command]
        stdin = None
        if self._exec.ta_bridge is not None:
            from tinyassets.agent_loop.box_ta import worker_argv

            extensions = await self._exec.ta_bridge.extensions(self._exec.handle, op_id)
            argv, stdin = worker_argv(command, root=self._root, execution=op_id,
                                      extensions=extensions)
            self._exec._ta_executions.add(op_id)
        outcome = await self._exec.run(op_id, argv, stdin=stdin, wall_seconds=wall)
        body = _text(outcome.output)
        if body and not body.endswith("\n"):
            body += "\n"
        return body + _trailer(outcome, wall, OUTPUT_BYTES)

    async def call(self, name: str, op_id: str, arguments: Mapping[str, Any]) -> str:
        """Dispatch one validated call; an argument the tool does not take is refused."""
        handler, allowed = {
            "read": (self.read, {"path", "offset", "limit"}),
            "write": (self.write, {"path", "content"}),
            "edit": (self.edit, {"path", "old_text", "new_text"}),
            "bash": (self.bash, {"command", "timeout"}),
        }[name]
        unexpected = set(arguments) - allowed
        if unexpected:
            return f"error: {name} does not take {sorted(unexpected)}"
        try:
            return await handler(op_id, **arguments)
        except (TypeError, ValueError) as exc:
            return f"error: {exc}"


#: The model-facing definitions. Arguments and meaning are the tool jail's
#: (``engine_mcp_server`` ``read``/``write``/``edit``/``bash``); only the root
#: is the box's. ``tests/test_agent_loop_box_tools.py`` pins the parity.
def box_tool_definitions(root: str = BOX_ROOT) -> dict[str, dict[str, Any]]:
    def schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": properties, "required": required}

    text = {"type": "string"}
    integer = {"type": "integer"}
    return {
        "read": {
            "description": (f"Read a file in your folder {root} (relative paths are under "
                            f"{root}).\noffset: first line (1-based); limit: line count "
                            "(default 2000)."),
            "inputSchema": schema(
                {"path": text, "offset": dict(integer, default=0),
                 "limit": dict(integer, default=0)}, ["path"]),
        },
        "write": {
            "description": f"Create or replace a file in {root}, making parent folders.",
            "inputSchema": schema({"path": text, "content": text}, ["path", "content"]),
        },
        "edit": {
            "description": (f"In a file in {root}, replace old_text (must match exactly "
                            "once) with new_text."),
            "inputSchema": schema({"path": text, "old_text": text, "new_text": text},
                                  ["path", "old_text", "new_text"]),
        },
        "bash": {
            "description": (f"Run a bash command in {root}. Memory, processes and time are "
                            "limited.\ntimeout: seconds (default 120, max 600)."),
            "inputSchema": schema({"command": text, "timeout": dict(integer, default=0)},
                                  ["command"]),
        },
    }
