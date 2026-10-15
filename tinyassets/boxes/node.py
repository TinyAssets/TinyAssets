"""Code nodes: validated on the host, executed inside the owner's box.

A node runs as a child Python process in the admitted owner box and receives
data and no credentials. The host filters state to the declared ``input_keys``,
answers ``invoke_mcp_action`` RPCs under the run's authority, and bounds output.
The guest-side runner is :mod:`tinyassets.boxes.node_runner`; import
allowlisting and source denylisting are defence in depth, the box is the
boundary.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tinyassets.box_process import run_bound
from tinyassets.boxes.node_runner import _RUNNER_SCRIPT
from tinyassets.providers.base import SandboxUnavailableError

RUNNER_SCRIPT = _RUNNER_SCRIPT
__all__ = ["ALLOWED_IMPORTS", "FORBIDDEN_PATTERNS", "NodeSandbox", "SandboxResult",
           "SandboxUnavailableError", "WorkspaceLimits", "WorkspaceMount",
           "WORKSPACE_MOUNT_POINT", "run_in_box"]
logger = logging.getLogger("universe_server.sandbox")

MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_RPC_REPLY_BYTES = 1024 * 1024
TAIL_CHARS = 2048
#: Where a node's workspace checkout appears inside the runner.
WORKSPACE_MOUNT_POINT = "/workspace"
MAX_WORKSPACE_OUTPUT_BYTES = 1024 * 1024
MAX_WORKSPACE_READ_BYTES = 1024 * 1024
MAX_WORKSPACE_GLOB_RESULTS = 10_000
WORKSPACE_TAIL_BYTES = 64 * 1024
#: A workspace node holds its lease for its whole run; this bounds it.
MAX_WORKSPACE_TIMEOUT_SECONDS = 1800.0
WORKSPACE_RSS_CAP_BYTES = 2 * 1024 * 1024 * 1024


def _cancel_requested(predicate: Callable[[], bool]) -> bool:
    """Ask the owner's cancel predicate, and never let it kill the run.

    A predicate that raises -- a closed database handle, a transient IO error --
    must not take down a node that is running fine. It is asked again on the
    next tick, and a genuine cancellation is not lost, only delayed by one
    interval. Failing the other way (treating an error as "cancelled") would
    let an unrelated fault kill the owner's work.
    """
    try:
        return bool(predicate())
    except Exception:
        logger.exception("cancel predicate raised; treating as not cancelled")
        return False


@dataclass(frozen=True)
class WorkspaceLimits:
    """Per-NODE workspace caps and the rlimit profile a workspace node runs under.

    ``command_timeout_s`` of ``None`` means each command may use whatever is
    left of the node timeout; a command never outlives the node, because the
    parent would kill the jail anyway and a shorter budget fails legibly.
    """

    max_output_bytes: int = MAX_WORKSPACE_OUTPUT_BYTES
    command_timeout_s: float | None = None
    max_read_bytes: int = MAX_WORKSPACE_READ_BYTES
    max_glob_results: int = MAX_WORKSPACE_GLOB_RESULTS
    tail_bytes: int = WORKSPACE_TAIL_BYTES
    rlimit_as: int = 1536 * 1024 * 1024
    #: Per-UID, not per-process. 128 was a guess that any host sharing a uid
    #: with other work breaks; it is a bound, not a promise about the host.
    rlimit_nproc: int = 1024
    rlimit_nofile: int = 1024
    rlimit_fsize: int = 512 * 1024 * 1024
    rlimit_core: int = 0
    #: Aggregate RSS across the process tree, watched from the parent.
    rss_cap_bytes: int = WORKSPACE_RSS_CAP_BYTES

    def rlimit_profile(self) -> dict[str, int]:
        """The limits the child applies before it reads its message."""
        return {
            "RLIMIT_AS": self.rlimit_as,
            "RLIMIT_CORE": self.rlimit_core,
            "RLIMIT_FSIZE": self.rlimit_fsize,
            "RLIMIT_NOFILE": self.rlimit_nofile,
            "RLIMIT_NPROC": self.rlimit_nproc,
        }

    def as_message(self) -> dict[str, Any]:
        """The caps the runner enforces, as they cross the pipe."""
        return {
            "max_output_bytes": self.max_output_bytes,
            "command_timeout_s": self.command_timeout_s,
            "max_read_bytes": self.max_read_bytes,
            "max_glob_results": self.max_glob_results,
            "tail_bytes": self.tail_bytes,
        }


@dataclass(frozen=True)
class WorkspaceMount:
    """One checkout generation, bound read-write at :data:`WORKSPACE_MOUNT_POINT`.

    Resolved only through the run's effect chain (design D2); it never travels
    through state, ``$ta.ref`` or JSON, which is why this is an object and not
    a string a branch could supply.
    """

    bind_source: str
    limits: WorkspaceLimits = field(default_factory=WorkspaceLimits)
    #: Descriptors the child must inherit. When ``bind_source`` is
    #: ``/proc/self/fd/<n>`` this holds ``n``: the bind then resolves in the
    #: bwrap process, which inherited it, to the directory the fd was opened
    #: on -- not to whatever the path names by the time bwrap looks.
    pass_fds: tuple[int, ...] = ()
    #: Roots a PLAIN path may sit beneath. The descriptor form needs none:
    #: its identity is the fd, not the string.
    allowed_roots: tuple[str, ...] = ()


@dataclass(frozen=True)
class SandboxResult:
    """Result of a sandboxed node execution.

    ``output_state`` is what ``run()`` returned, **unfiltered**;
    ``undeclared`` names the keys outside the node's ``output_keys`` so the
    compiler can apply its merge-writer guard before dropping them.
    """

    node_id: str
    success: bool
    output_state: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""
    warning: str = ""
    undeclared: list[str] = field(default_factory=list)
    #: A ``ws.run`` command outlived its budget. Carried as a flag, not a
    #: message the caller has to grep, so the compiler can classify it.
    workspace_timeout: bool = False
    #: The owner cancelled while this node was running. A FLAG for the same
    #: reason as above: the compiler has to tell "the owner stopped this" apart
    #: from "the node failed", and matching on an error string is how that
    #: distinction rots.
    cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "success": self.success,
            "output_state": self.output_state,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "warning": self.warning,
            "undeclared": list(self.undeclared),
        }


def _tail(text: str) -> str:
    """Last :data:`TAIL_CHARS` characters of *text*."""
    return text[-TAIL_CHARS:] if text else ""



ALLOWED_IMPORTS = {
    # Standard library — safe subset
    "json", "re", "math", "statistics", "datetime", "collections",
    "dataclasses", "typing", "textwrap", "difflib", "hashlib",
    "urllib.parse", "pathlib", "functools", "itertools", "copy",
    "string", "enum", "abc", "decimal", "fractions",
    # Text/bytes handling for the fetch → edit → write shape
    "base64", "io", "csv", "html", "unicodedata", "zlib", "struct",
    "operator", "heapq", "bisect", "time",
}

# Pre-check patterns for source outside literals/comments; the OS jail is the boundary.
FORBIDDEN_PATTERNS = [
    "os.system",
    "os.popen",
    "os.exec",
    "os.spawn",
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.makedirs",
    "os.rename",
    "subprocess",
    "shutil.rmtree",
    "shutil.move",
    "__import__",
    "eval(",
    "exec(",
    "compile(",
    "open(",
    "builtins",
    "globals(",
    "locals(",
    "breakpoint(",
    "importlib",
    "ctypes",
    "multiprocessing",
    "threading.Thread",
    "signal",
    "socket",
    "pickle",
]



def _rpc_reply_line(
    rpc_id: Any,
    action: str,
    kwargs: dict[str, Any],
    invoke: Callable[[str, dict[str, Any]], Any] | None,
) -> str:
    """Answer one ``invoke_mcp_action`` request. Never raises.

    The child is blocked on this reply, so every path — no invoker, a
    raising invoker, an unencodable or oversized result — must produce a
    line. Silence would hang the node until its timeout.
    """
    def _error(message: str) -> str:
        return json.dumps({"id": rpc_id, "error": message})

    if invoke is None:
        return _error("invoke_mcp_action is not available for this node")
    try:
        value = invoke(action, dict(kwargs))
    except Exception as exc:
        return _error(f"{type(exc).__name__}: {exc}")
    try:
        encoded = json.dumps({"id": rpc_id, "result": value}, default=str)
    except (TypeError, ValueError) as exc:
        return _error(
            f"action result is not JSON-serializable: {type(exc).__name__}: {exc}"
        )
    if len(encoded.encode("utf-8")) > MAX_RPC_REPLY_BYTES:
        return _error(
            f"action result exceeds the {MAX_RPC_REPLY_BYTES}-byte reply limit"
        )
    return encoded


class NodeSandbox:
    """Validates a node on the host and runs it in the admitted owner box."""

    def __init__(
        self,
        timeout: float = 30.0,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
        should_cancel: Callable[[], bool] | None = None,
        universe_dir: str | os.PathLike | None = None,
    ) -> None:
        self.default_timeout = timeout
        self.max_output_bytes = max_output_bytes
        #: Polled while the child runs; True means the owner asked to stop.
        self.should_cancel = should_cancel
        self.universe_dir = universe_dir

    def validate_source(self, source_code: str) -> list[str]:
        """Pre-validate source code before execution.

        Returns pre-check errors (empty if these checks pass).
        The OS jail is the security boundary.
        """
        errors = []

        from tinyassets.graph_compiler import dangerous_source_patterns

        try:
            patterns = dangerous_source_patterns(source_code, tuple(FORBIDDEN_PATTERNS))
        except RecursionError:
            return ["Source code is too deeply nested"]
        except (SyntaxError, ValueError):
            patterns = []  # Report the syntax error below, with its location.
        for pattern in patterns:
            errors.append(f"Forbidden pattern: '{pattern}'")

        # Check for excessive code size
        if len(source_code) > 50_000:
            errors.append("Source code exceeds 50KB limit")

        # Basic syntax check
        try:
            compile(source_code, "<node>", "exec")
        except (SyntaxError, ValueError, RecursionError) as exc:
            errors.append(f"Syntax error: {exc}")

        return errors

    def run_sync(
        self,
        node_id: str,
        source_code: str,
        input_state: dict[str, Any],
        input_keys: list[str],
        output_keys: list[str],
        timeout: float | None = None,
        effects: dict[str, Any] | None = None,
        dependencies: list[str] | None = None,
        invoke: Callable[[str, dict[str, Any]], Any] | None = None,
        workspace: WorkspaceMount | None = None,
    ) -> SandboxResult:
        """Execute the node in the admitted owner box, synchronously.

        ``input_state`` is filtered to ``input_keys``; the return comes back
        unfiltered with ``undeclared`` naming keys outside ``output_keys``.
        ``invoke`` answers ``invoke_mcp_action`` under the run's authority;
        ``workspace`` is the leased checkout bound at ``/workspace``.
        """
        from tinyassets.control_plane.binding import file_binding

        if self.universe_dir is None:
            raise SandboxUnavailableError("code node requires an admitted owner box")
        binding = file_binding(Path(self.universe_dir))
        if binding is None:
            raise SandboxUnavailableError("code node requires an admitted owner box")
        return run_in_box(self, binding, node_id=node_id, source_code=source_code,
                          input_state=input_state, input_keys=input_keys,
                          output_keys=output_keys,
                          timeout=timeout or self.default_timeout,
                          effects=effects, dependencies=dependencies, invoke=invoke,
                          workspace=workspace)

    async def execute(
        self,
        node_id: str,
        source_code: str,
        input_state: dict[str, Any],
        input_keys: list[str],
        output_keys: list[str],
        timeout: float | None = None,
        effects: dict[str, Any] | None = None,
        dependencies: list[str] | None = None,
        invoke: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> SandboxResult:
        """Async wrapper around :meth:`run_sync` (runs it on a worker thread)."""
        return await asyncio.to_thread(
            self.run_sync,
            node_id=node_id,
            source_code=source_code,
            input_state=input_state,
            input_keys=input_keys,
            output_keys=output_keys,
            timeout=timeout,
            effects=effects,
            dependencies=dependencies,
            invoke=invoke,
        )

    async def execute_registered(
        self,
        node_registration: dict[str, Any],
        graph_state: dict[str, Any],
        timeout: float | None = None,
        effects: dict[str, Any] | None = None,
    ) -> SandboxResult:
        """Execute a registered node from the node registry.

        Convenience wrapper that unpacks a NodeRegistration dict.
        """
        return await self.execute(
            node_id=node_registration["node_id"],
            source_code=node_registration["source_code"],
            input_state=graph_state,
            input_keys=node_registration.get("input_keys", []),
            output_keys=node_registration.get("output_keys", []),
            timeout=timeout,
            effects=effects,
            dependencies=node_registration.get("dependencies", []),
        )


def run_in_box(instance, binding, *, invoke, workspace, **request):
    timeout = request["timeout"]
    if (type(timeout) not in (int, float)
            or not 0 < timeout <= MAX_WORKSPACE_TIMEOUT_SECONDS):
        raise ValueError("invalid node box timeout")
    guest_workspace = None
    profile = ""
    if workspace is not None:
        from tinyassets.boxes.provider import BOX_ROOT

        guest_root = BOX_ROOT + "/" + binding.handle.command_center_id
        source = workspace.bind_source
        if (not isinstance(source, str) or workspace.pass_fds
                or not source.startswith(guest_root + "/")
                or any(part in ("", ".", "..")
                       for part in source[len(guest_root) + 1:].split("/"))):
            raise ValueError("node workspace requires a box-backed lease")
        guest_workspace = {"root": source, "limits": workspace.limits.as_message()}
        profile = json.dumps(workspace.limits.rlimit_profile())
    errors = instance.validate_source(request["source_code"])
    if errors:
        return SandboxResult(node_id=request["node_id"], success=False,
                                     error=f"Validation failed: {'; '.join(errors)}")
    payload = dict(source_code=request["source_code"],
                   input_state={k: v for k, v in request["input_state"].items()
                                if k in request["input_keys"]},
                   effects=request.get("effects") or {},
                   output_keys=request["output_keys"],
                   allowed_imports=sorted(ALLOWED_IMPORTS))
    if guest_workspace is not None:
        payload["workspace"] = guest_workspace
    message = json.dumps(payload, default=str).encode()
    if len(message) > MAX_INPUT_BYTES:
        return SandboxResult(node_id=request["node_id"], success=False,
                                     error="input too large for node box")

    def answer(line):
        try:
            frame = json.loads(line)
        except ValueError:
            return None
        if not isinstance(frame, dict) or set(frame) != {"rpc"}:
            return None
        rpc = frame["rpc"]
        if (not isinstance(rpc, dict) or set(rpc) != {"id", "action", "kwargs"}
                or type(rpc["id"]) is not int or type(rpc["action"]) is not str
                or type(rpc["kwargs"]) is not dict):
            raise ValueError("invalid node RPC frame")
        return _rpc_reply_line(rpc["id"], rpc["action"], rpc["kwargs"],
                                       invoke).encode()

    started = time.monotonic()
    done = run_bound(binding, "code-node", ["python", "-I", "-c", RUNNER_SCRIPT,
                                             str(timeout), "1", profile],
                     stdin=message + b"\n", wall_seconds=timeout + 5,
                     output_bytes=instance.max_output_bytes,
                     line_handler=answer)
    result = None
    for line in reversed(done.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict) and type(candidate.get("success")) is bool:
            result = candidate
            break
    if not isinstance(result, dict) or type(result.get("success")) is not bool:
        raise RuntimeError("node box returned no structured result")
    printed = result.get("user_stdout", "")
    return SandboxResult(
        node_id=request["node_id"], success=result["success"],
        output_state=result.get("output_state", {}), error=result.get("error", ""),
        duration_seconds=time.monotonic() - started, stdout=printed,
        stdout_tail=_tail(printed), warning=result.get("warning", ""),
        undeclared=[str(k) for k in result.get("undeclared") or []],
        workspace_timeout=bool(result.get("workspace_timeout")),
    )
