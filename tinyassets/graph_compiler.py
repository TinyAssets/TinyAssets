"""Compile a BranchDefinition into a LangGraph StateGraph.

Pure function: ``compile_branch(branch) -> CompiledBranch``. No side effects
beyond the returned object. Failures are programmer errors (invalid inputs)
not user errors — run ``branch.validate()`` first if needed.

The compiler synthesizes a dynamic TypedDict from ``state_schema`` with
``Annotated`` reducers per field, builds node adapters for prompt_template
and (host-approved) source_code nodes, and wires simple + conditional edges.

Design rules (from `docs/specs/community_branches_phase3.md`):
- prompt_template nodes are always safe — rendered via a custom regex
  substitution (see ``_render_template`` + ``_PLACEHOLDER_RE``), NOT
  ``str.format_map``. Single ``{``/``}`` characters are literal; only
  ``{ident}`` matching a valid Python identifier is substituted. Jinja
  ``{{ident}}`` is normalized to ``{ident}`` first. Authors escape
  literal placeholders as ``\\{ident\\}``. Rendered output is sent via
  the role-based provider router.
- source_code nodes run in the OS sandbox (change ``sandboxed-code-node``);
  ``approved`` / ``approved_source_hash`` are provenance, never a gate.
  Compile time checks only the disallowed-pattern scan, the size cap and a
  syntax pass, through :func:`source_code_problems` -- the ONE definition the
  authoring receipts report ``runnable`` from as well.
- Conditional edges use a predicate over a single declared output_key.
  No user-code routers in v1.
"""

from __future__ import annotations

import concurrent.futures
import copy
import dataclasses as _dataclasses
import io
import json
import logging
import math
import operator
import os
import re
import stat
import threading
import time
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Callable

from langgraph.graph import END, START, StateGraph

from tinyassets.branches import BranchDefinition, GraphNodeRef, NodeDefinition
from tinyassets.exceptions import AllProvidersExhaustedError

if TYPE_CHECKING:
    from tinyassets.providers.base import UniverseContext
    from tinyassets.workspace_family import FamilyMember

logger = logging.getLogger(__name__)

_POLICY_PROVIDER_RETRY_BACKOFF_SECONDS = (2.0, 4.0)


class CompilerError(Exception):
    """Raised when the compiler cannot produce a runnable graph.

    FEAT-006: optionally carries the structured ``chain_state`` and
    ``attempts`` from an underlying ``AllProvidersExhaustedError`` so
    chatbots and the auto-fix loop can see *why* the chain exhausted
    without having to walk ``__cause__`` themselves.
    """

    def __init__(
        self,
        *args: Any,
        chain_state: dict | None = None,
        attempts: list | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.chain_state = chain_state
        self.attempts = attempts


class BranchValidationError(CompilerError, ValueError):
    """Raised when branch structure fails compile-time validation."""


class NodeTimeoutError(CompilerError):
    """Raised when a node's provider/source_code exceeds its timeout_seconds.

    Distinct from a generic CompilerError so the runner can emit a clean
    ``timeout`` event and set run status to ``failed`` with a specific
    reason, instead of the user seeing a silent stall (#61).

    ``node_id`` is exposed as an attribute so callers don't have to parse
    it out of the human-readable message.
    """

    def __init__(self, message: str, *, node_id: str = "") -> None:
        super().__init__(message)
        self.node_id = node_id


class _DeadlineExpiredBeforeStart(NodeTimeoutError):
    """A worker reached queued node work whose deadline had already passed.

    Internal to ``_run_with_timeout``: raised on the worker thread instead of
    invoking the call. A subclass of :class:`NodeTimeoutError` so it needs no
    new handling anywhere — in the ordinary case the caller has already raised
    its own ``NodeTimeoutError`` and nobody reads this one, and in the boundary
    case where the caller is still inside ``future.result()`` it surfaces as
    the node timeout it is.
    """


class ForeignCodeError(CompilerError):
    """Raised when a run would execute source_code it did not author (design
    D2, Codex round 1 P0): ``run_graph`` admits a PUBLIC foreign branch
    directly, and the cross-author approval strip only runs on fork, so
    authorship has to be checked where the run's identity exists. Code runs
    only in the universe that authored it; remixing the branch (write_graph
    fork_from) re-authors it under the caller and is the acceptance."""

    def __init__(self, message: str, *, node_id: str = "") -> None:
        super().__init__(message)
        self.node_id = node_id


class CodeNodeError(CompilerError):
    """Raised when a source_code node's sandboxed run fails: non-zero exit, an
    exception inside ``run()``, bad JSON, an output over the cap. Carries the
    node id and the child's stderr tail so the run error says what happened
    where, not just that it happened (design D2)."""

    def __init__(self, message: str, *, node_id: str = "", stderr_tail: str = "") -> None:
        super().__init__(message)
        self.node_id = node_id
        self.stderr_tail = stderr_tail


#: Whether this host can bind a directory through an inherited descriptor.
#: A module attribute rather than an inline platform test so the suite can
#: substitute it; production never changes it.
WORKSPACE_FD_BIND_SUPPORTED = os.name == "posix"


def _sandbox_workspace_mount(
    mount: Any, node_id: str, allowed_roots: tuple[str, ...] = ()
) -> Any:
    """Turn the run's workspace capability into the sandbox's mount.

    Prefers the held directory descriptor. ``/proc/self/fd/<n>`` is resolved
    by the **bwrap process**, which inherited ``n``, so it names the directory
    the fd was opened on -- not whatever the lease path points at by the time
    the mount happens, and not this parent's ``/proc/self``. A rename between
    admission and mount cannot swap what gets bound.

    Falls back to the path when there is no descriptor: Windows, the
    tests-only launcher, or a sink that has not published one.
    """
    from tinyassets.node_sandbox import WorkspaceLimits, WorkspaceMount

    bind_source = getattr(mount, "bind_source", "")
    if not isinstance(bind_source, str) or not bind_source:
        raise CodeNodeError(
            "workspace not available: the run's capability names no directory "
            f"(node '{node_id}')",
            node_id=node_id,
        )
    limits = getattr(mount, "limits", None)
    if not isinstance(limits, WorkspaceLimits):
        limits = WorkspaceLimits()
    # The capability's own roots win; otherwise the caller's. Only the PATH
    # form uses them at all -- a descriptor's identity is the fd, not a string.
    allowed_roots = tuple(getattr(mount, "allowed_roots", ()) or ()) or tuple(
        allowed_roots or ()
    )

    # The sink publishes the descriptors it wants bound. Carry them UNCHANGED:
    # the repository's own fd is the bind source, and deriving one here from
    # the lease root would mount the directory that CONTAINS the repository
    # (Codex round 2, P0 #14a).
    published = tuple(getattr(mount, "pass_fds", ()) or ())
    if published and WORKSPACE_FD_BIND_SUPPORTED:
        for descriptor in published:
            _require_live_directory(descriptor, node_id)
        return WorkspaceMount(
            bind_source=bind_source,
            limits=limits,
            pass_fds=published,
            allowed_roots=allowed_roots,
        )

    # `repo_fd` and nothing else. `lease_fd` names the LEASE, one level up, so
    # falling back to it would mount the directory that contains the repository
    # -- the case the comment above rules out -- with the rest of the lease
    # visible to node code.
    repo_fd = getattr(mount, "repo_fd", None)
    if repo_fd is None or not WORKSPACE_FD_BIND_SUPPORTED:
        return WorkspaceMount(
            bind_source=bind_source, limits=limits, allowed_roots=allowed_roots
        )

    descriptor = _require_live_directory(repo_fd, node_id)
    return WorkspaceMount(
        bind_source=f"/proc/self/fd/{descriptor}",
        limits=limits,
        pass_fds=(descriptor,),
        allowed_roots=allowed_roots,
    )


def _require_live_directory(descriptor: Any, node_id: str) -> int:
    """A descriptor that is closed or no longer a directory is a lease that went
    away. Fail the node: quietly binding the path instead is the swap the
    descriptor exists to prevent.
    """
    try:
        number = int(descriptor)
        info = os.fstat(number)
    except (OSError, TypeError, ValueError) as exc:
        raise CodeNodeError(
            "workspace not available: checkout did not deliver / was discarded "
            f"(node '{node_id}'): the lease descriptor is unusable ({exc})",
            node_id=node_id,
        ) from exc
    if not stat.S_ISDIR(info.st_mode):
        raise CodeNodeError(
            f"workspace not available: the lease descriptor for '{node_id}' is "
            "not a directory",
            node_id=node_id,
        )
    return number


class WorkspaceCommandTimeout(NodeTimeoutError):
    """Raised when a ``ws.run`` command outlived its budget (design D2/D6).

    A subclass of :class:`NodeTimeoutError` so every existing timeout path
    still catches it, and its own class so the terminal-status taxonomy can
    report ``workspace_command_timeout`` rather than a generic node timeout:
    the actionable fact is that a command in the workspace hung, and the
    whole jail was killed to end it.
    """


class NodeCancelledError(CompilerError):
    """The owner cancelled while this node's child process was running.

    Its own class, not a `CodeNodeError`, because "the owner stopped this" and
    "the node failed" must not land in the same terminal status: a cancelled
    run reported as failed tells the owner their workflow is broken.

    `runs._is_cancel_exception` recognises it by name alongside
    ``RunCancelledError``, which is the same deliberate duck-typing the rest of
    this boundary uses -- `graph_compiler` cannot import `runs`, because `runs`
    imports `compile_branch` from here.
    """

    def __init__(self, message: str, *, node_id: str = "") -> None:
        super().__init__(message)
        self.node_id = node_id


class EmptyResponseError(CompilerError):
    """Raised when an LLM provider returns an empty response.

    Distinct from a generic CompilerError so the runner can record a
    ``failed`` node event with ``reason: empty_response`` and surface a
    structured error rather than a generic crash message.

    ``node_id`` is exposed as an attribute mirroring ``NodeTimeoutError``.
    """

    def __init__(self, message: str, *, node_id: str = "") -> None:
        super().__init__(message)
        self.node_id = node_id


class ConcurrencyTracker:
    """Track concurrent node executions for observability + budget enforcement.

    Created per-run by ``compile_branch`` when ``concurrency_budget`` is set.
    Shared across all node callables in a single branch invocation via closure.
    Thread-safe: lock guards active_count + peak.
    """

    def __init__(self, budget: int | None) -> None:
        self.budget = budget
        self._semaphore = threading.Semaphore(budget) if budget else None
        self._lock = threading.Lock()
        self.active_count: int = 0
        self.peak: int = 0

    def acquire(self) -> None:
        if self._semaphore is not None:
            self._semaphore.acquire()
        with self._lock:
            self.active_count += 1
            if self.active_count > self.peak:
                self.peak = self.active_count

    def release(self) -> None:
        with self._lock:
            self.active_count = max(0, self.active_count - 1)
        if self._semaphore is not None:
            self._semaphore.release()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active_now": self.active_count,
                "peak": self.peak,
                "budget": self.budget,
            }


# Shared executor so every timeout-wrapped call doesn't spin up a
# fresh thread. Bounded worker count keeps a runaway graph from
# spawning unbounded threads on a slow provider.
#
# NOTE: when all 8 workers are busy, the 9th submit queues and its
# timeout is measured from submit(), not from worker-allocated-start.
# Both consequences are corrected, not absorbed: a call that spends its
# whole budget queued is cancelled, and refused at worker entry if it wins
# the cancel race, rather than started after its node went terminal; and a
# call that waited only part of its budget has that wait subtracted from
# its provider cap (see _deadline_cfg) instead of receiving the node's full
# timeout from the per-closure ModelConfig.
_TIMEOUT_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None

# Below this, the gap between submit() and worker start is scheduling jitter
# rather than queue wait, and the node's config is handed over untouched.
_QUEUE_WAIT_SUBTRACT_THRESHOLD_S = 0.05
# The remaining budget is handed over as-is: ModelConfig.stream_timeout_profile()
# accepts any finite positive float, so the absolute cap needs no floor of its
# own. This is an epsilon, NOT a budget: that resolver discards a non-positive
# cap and substitutes the 600s default, which would invert the correction. An
# already-expired call is stopped by the worker guard in _run_with_timeout, so
# this clamp never has to invent a budget for one.
_MIN_POSITIVE_PROVIDER_CAP_S = 0.001


def _get_timeout_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _TIMEOUT_EXECUTOR
    if _TIMEOUT_EXECUTOR is None:
        _TIMEOUT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="node-timeout",
        )
    return _TIMEOUT_EXECUTOR


_SHARED_ROUTER: Any = None


def _get_shared_router() -> Any:
    """Return the shared ProviderRouter singleton, or None if not available.

    Lazily imports so test environments without providers don't fail at
    import time.  The router is cached module-level after first import.
    """
    global _SHARED_ROUTER
    if _SHARED_ROUTER is not None:
        return _SHARED_ROUTER
    try:
        from tinyassets.providers.base import subscription_auth_health
        from tinyassets.providers.router import ProviderRouter
        _SHARED_ROUTER = ProviderRouter(auth_health=subscription_auth_health)
    except Exception:
        pass
    return _SHARED_ROUTER


def _seat_scope(universe_context, seat_scope) -> tuple[Path, str] | None:
    """(data root, universe id) whose account this agent call is charged to.

    The RUN's own universe first -- ``BranchExecutionContext.universe_id``, built
    from the authenticated run row -- because that is what a real run carries: a
    run compiles its nodes WITHOUT a ``universe_context`` (it rides inside the
    bound provider call), so a seat keyed only on ``universe_context`` was never
    taken by any run at all. A directly-built node with a universe context (the
    local and test paths) falls back to that.
    """
    if seat_scope is not None and seat_scope[1]:
        return Path(seat_scope[0]), str(seat_scope[1])
    udir = getattr(universe_context, "universe_dir", None)
    if udir is not None:
        return Path(udir).parent, Path(udir).name
    return None


def _run_agent_with_timeout(fn, *, timeout_s, node_id, universe_context, event_sink,
                            seat_scope=None, on_seated=None):
    """Run one agent call holding a seat of the universe's ACCOUNT.

    This is the executor, never the enqueuer: the seat is taken here, where the
    model call is about to happen, and waited for with no deadline -- work over
    the seat count queues, it is never refused (founder, 2026-09-30). The wait is
    published as a ``waiting`` event carrying the owner's waiting line, and a run
    cancelled while it waits stops waiting.

    A node inside a run that already holds a seat (an automation, a blocking
    parent) re-enters it; a parallel sibling pays for its own. Once the call is
    submitted, its future owns the release -- a timed-out call that is still
    running keeps its seat until it actually ends.

    ``on_seated`` runs once the seat is held, before the call is submitted: the
    node's provider budget starts THEN, never during the wait.
    """
    from tinyassets import universe_seats as seats

    scope = _seat_scope(universe_context, seat_scope)
    if scope is None:
        # No universe at all: the local single-tenant daemon, which has no account.
        return _run_with_timeout(fn, timeout_s=timeout_s, node_id=node_id)
    root, universe_id = scope
    account = seats.account_key(universe_id, root=root)
    tier = seats.tier_of_key(account, root=root)

    def waiting(state):
        if event_sink is None:
            return
        try:
            event_sink(
                node_id=node_id, phase="waiting", kind="waiting_for_seat",
                running=state.running, waiting=state.waiting,
                detail=seats.waiting_message(running=state.running, tier=tier),
            )
        except Exception as exc:  # noqa: BLE001
            if _is_cancel_exception(exc):
                raise
            logger.exception("event_sink raised in %s (waiting)", node_id)

    held = seats.acquire_blocking(
        account, kind=seats.KIND_AGENT_NODE, universe_id=universe_id,
        parent=seats.current_seat(), wait_s=None, on_waiting=waiting,
        db=seats.ledger_path(root),
    )
    if on_seated is not None:
        on_seated()
    # The call runs carrying its seat (the worker hop copies this context), so a
    # blocking agent call nested inside it re-enters the seat instead of waiting
    # for one its own blocked parent holds.
    with seats.carrying(held):
        return _run_with_timeout(
            fn, timeout_s=timeout_s, node_id=node_id,
            on_done=lambda: seats.release(held.seat_id, db=held.db),
        )


def _run_with_timeout(
    fn: Callable[[], Any],
    *,
    timeout_s: float,
    node_id: str,
    on_done=None,
) -> Any:
    """Call ``fn()`` on a worker thread, raise NodeTimeoutError on overrun.

    When a timeout fires, a worker thread that has ALREADY STARTED is NOT
    killed — Python has no safe way to do that. That call keeps running in
    the background (the provider's own subprocess/HTTP timeout is the
    backstop) and is left to settle, because an interrupted provider call
    leaves an effect nobody can classify. We return to the graph so the
    overall run can fail-fast instead of hanging the executor.

    Work still QUEUED at the deadline is a different case and never starts.
    ``timeout_s`` is measured from ``submit()``, so a call that waited out
    its whole budget behind a saturated pool would otherwise start strictly
    after the node went terminal — a provider call nobody awaits, holding a
    worker, outside the deadline that admitted it. Two mechanisms close it,
    because neither is sufficient alone:

    * ``Future.cancel()`` — by contract it succeeds only while the work has
      not begun, so it can never interrupt a call that already made it out.
      It is also a race the worker can WIN: it returns ``False`` once the
      item has been picked up, and the work then runs anyway.
    * The worker-entry deadline check below, which is what makes the
      no-new-work-after-the-deadline guarantee provable rather than a matter
      of scheduling luck. It runs on the worker, immediately before ``fn``,
      and refuses only work whose deadline has ALREADY passed. Work with
      positive budget left starts normally.

    Neither touches work that has already begun: the check is before the
    first line of ``fn``, so a call past it settles untouched.
    """
    from tinyassets.provider_admission import blocking_parent_slot

    # A fixed worker pool can itself fill with blocked ancestors. A blocking
    # provider child needs a worker independent of that pool, just as it needs
    # its ancestor's slot rather than one behind that ancestor in the queue.
    nested_executor = (
        concurrent.futures.ThreadPoolExecutor(max_workers=1)
        if blocking_parent_slot() is not None else None
    )
    executor = nested_executor or _get_timeout_executor()
    deadline = time.monotonic() + timeout_s

    def _guarded() -> Any:
        if time.monotonic() >= deadline:
            raise _DeadlineExpiredBeforeStart(
                f"Node '{node_id}' work reached a worker after its "
                f"{timeout_s:.3g}s deadline had already passed; it was "
                "never started.",
                node_id=node_id,
            )
        return fn()

    import contextvars

    # Preserve the explicit blocking-invoke loan across the node worker hop.
    try:
        future = executor.submit(contextvars.copy_context().run, _guarded)
    except BaseException:
        if on_done is not None:
            on_done()
        raise
    finally:
        if nested_executor is not None:
            nested_executor.shutdown(wait=False)
    if on_done is not None:
        # The future owns the release: a call still running past its timeout
        # keeps what it holds until it actually ends.
        future.add_done_callback(lambda _future: on_done())
    try:
        return future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError as exc:
        # Returns False once the worker picked it up. That case is not left to
        # chance: the worker-entry check refuses the pickup if the deadline has
        # passed, and a call that got past the check settles normally and is
        # never replayed.
        future.cancel()
        raise NodeTimeoutError(
            f"Node '{node_id}' exceeded {timeout_s:.0f}s timeout. "
            "The provider call may still be running in the background; "
            "its own subprocess/HTTP timeout is the backstop.",
            node_id=node_id,
        ) from exc


def _call_policy_router_with_retry(
    router: Any,
    *,
    role: str,
    prompt: str,
    system: str,
    policy: dict[str, Any],
    config: Any = None,
    universe_context: "UniverseContext | None" = None,
    response_observer: Callable | None = None,
) -> tuple[str, str, dict]:
    """Retry policy-aware provider dispatch on transient chain exhaustion."""
    # Only forward config when set AND the router's call_with_policy_sync
    # actually accepts it — protects 4-arg routers/stubs (backward-compat),
    # mirroring the injected provider_call bridge guard.
    pass_config = config is not None
    pass_context = universe_context is not None
    pass_observer = response_observer is not None
    if pass_config or pass_context or pass_observer:
        try:
            import inspect as _inspect
            _params = _inspect.signature(router.call_with_policy_sync).parameters
            accepts_kwargs = any(
                p.kind == p.VAR_KEYWORD for p in _params.values()
            )
            pass_config = ("config" in _params) or any(
                p.kind == p.VAR_KEYWORD for p in _params.values()
            )
            pass_context = "universe_context" in _params or accepts_kwargs
            pass_observer = pass_observer and ("response_observer" in _params or accepts_kwargs)
        except (ValueError, TypeError):
            pass_config = False
            pass_context = False
            pass_observer = False
    attempts = len(_POLICY_PROVIDER_RETRY_BACKOFF_SECONDS) + 1
    for attempt_index in range(attempts):
        try:
            kwargs: dict[str, Any] = {}
            if pass_context:
                kwargs["universe_context"] = universe_context
            if pass_observer:
                kwargs["response_observer"] = response_observer
            if pass_config:
                return router.call_with_policy_sync(
                    role, prompt, system, policy, config, **kwargs,
                )
            return router.call_with_policy_sync(
                role, prompt, system, policy, **kwargs,
            )
        except AllProvidersExhaustedError:
            if attempt_index == attempts - 1:
                raise
            delay = _POLICY_PROVIDER_RETRY_BACKOFF_SECONDS[attempt_index]
            logger.warning(
                "Policy provider chain exhausted for role=%s; retrying in %.1fs",
                role,
                delay,
            )
            time.sleep(delay)
    raise AssertionError("unreachable policy provider retry state")


_MAX_SOURCE_CODE_BYTES = 50_000

_DANGEROUS_PATTERNS = (
    "os.system", "subprocess", "eval(", "exec(", "__import__",
)

# Phase G preflight §4.1 #5d: stricter list for NodeBid executor.
# Wrapper nodes (Phase D) trust the narrower list — they're
# domain-trusted-callables registered by the host at import time.
# Bid-referenced nodes are adversarially-accessible (anyone can
# post a bid), so the sandbox catches a wider surface. Network-call
# patterns (urllib, requests, socket, http.client) are intentionally
# EXCLUDED — approved nodes may legitimately call LLM APIs.
# Single source of truth: both producer-side + executor-side sandbox
# layers (invariant 1) import from here so the bid-market posture
# can't drift from the wrapper's.
_BID_DANGEROUS_PATTERNS = _DANGEROUS_PATTERNS + (
    "compile(", "open(", "importlib", "pickle", "marshal",
)


def _source_without_literals(source: str) -> str:
    """Blank literal/comment tokens without moving any remaining source text.

    Tokenization errors retain the original text for the conservative raw scan.
    On Python 3.12+, f-string expression tokens remain code; earlier tokenizers
    expose an entire f-string as one STRING token, which must remain unmasked.
    Lone carriage returns also retain the raw scan: compile treats them as
    newlines, whereas StringIO.readline does not.
    """
    if "\r" in source.replace("\r\n", ""):
        return source
    offsets = [0]
    for line in io.StringIO(source):
        offsets.append(offsets[-1] + len(line))
    masked = list(source)
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.ERRORTOKEN:
                return source
            if token.type == tokenize.STRING and not hasattr(tokenize, "FSTRING_START"):
                prefix = token.string.split('"', 1)[0].split("'", 1)[0]
                if "f" in prefix.lower():
                    continue
            if token.type in (tokenize.STRING, tokenize.COMMENT) or (
                tokenize.tok_name[token.type].startswith(("FSTRING_", "TSTRING_"))
            ):
                start = offsets[token.start[0] - 1] + token.start[1]
                end = offsets[token.end[0] - 1] + token.end[1]
                masked[start:end] = [
                    char if char in "\r\n" else " " for char in source[start:end]
                ]
    except (tokenize.TokenError, SyntaxError, ValueError):
        return source
    return "".join(masked)


def dangerous_source_patterns(source: str, patterns: tuple[str, ...]) -> list[str]:
    """Pre-check with the original substring rules, excluding literal/comment text.

    This is not a Python security analysis: sandboxed code uses the OS jail
    as its boundary. Node bids run in-process behind approval/hash checks.
    If tokenization fails, scan the unmodified source (fail closed on masking).
    """
    if "\x00" in source:
        raise ValueError("source code cannot contain null bytes")
    code = _source_without_literals(source)
    return [pattern for pattern in patterns if pattern in code]


def _is_cancel_exception(exc: BaseException) -> bool:
    """Duck-type check for the runner's cancel exception.

    Declared as a name-match so `graph_compiler` has no import dependency
    on `runs` (which imports `compile_branch` from here). Any exception
    class named ``RunCancelledError`` propagates past the event_sink
    catch-all; everything else is logged and swallowed.
    """
    return type(exc).__name__ == "RunCancelledError"


#: Normalized stream-event kinds a provider may name as ``max_silence_after``.
_SILENCE_AFTER_KINDS = frozenset({
    "launch", "init", "heartbeat", "declared_busy", "declared_clear",
    "text_delta", "tool_use", "tool_result", "api_retry", "assistant_error",
    "result",
})


def _provider_timing(resp: Any) -> dict[str, Any]:
    """Allowlisted per-call timing scalars for a node's ``ran`` event.

    Before 2026-09-24 a node's receipt carried no provider timing at all
    (``provider_calls`` recorded ``latency_ms: null``), so a node that took
    200s instead of 10s could not say whether the time went to tool calls,
    output length or a silent wait. Numbers and one allowlisted label only:
    never prompt, tool input or response text.
    """
    out: dict[str, Any] = {}
    for key in ("latency_ms", "ttft_ms", "max_silence_ms"):
        value = getattr(resp, key, None)
        finite = (isinstance(value, (int, float)) and not isinstance(value, bool)
                  and math.isfinite(value))
        if finite and value >= 0:
            out[key] = round(float(value), 1)
    for key in ("input_tokens", "output_tokens", "tool_uses"):
        value = getattr(resp, key, None)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out[key] = value
    after = getattr(resp, "max_silence_after", None)
    if isinstance(after, str) and after in _SILENCE_AFTER_KINDS:
        out["max_silence_after"] = after
    return out


def _emit_failed_event(
    event_sink: Callable[..., None] | None,
    node_id: str,
    exc: BaseException,
) -> None:
    """Preserve the existing failure-emission entry point."""
    _emit_terminal_event(event_sink, node_id, exc, phase="failed")


def _emit_terminal_event(
    event_sink: Callable[..., None] | None,
    node_id: str,
    exc: BaseException,
    *,
    phase: str,
) -> None:
    """Emit an observed failure/cancellation before propagating its exception.

    FEAT-006: when the underlying exception carries ``chain_state``
    (an ``AllProvidersExhaustedError``), forward it as a structured
    ``provider_chain`` field on the event so downstream consumers
    (chatbots, auto-fix loop, get_run.events) can read per-provider
    skip reasons without parsing the human-readable error string.
    """
    if event_sink is None:
        return
    chain_state = getattr(exc, "chain_state", None)
    kwargs: dict[str, Any] = {
        "node_id": node_id,
        "phase": phase,
        "error": str(exc),
        "error_type": type(exc).__name__,
    }
    if chain_state is not None:
        kwargs["provider_chain"] = chain_state
    try:
        event_sink(**kwargs)
    except TypeError:
        # Older event_sink signatures may not accept `provider_chain`;
        # retry without it so a kwarg-mismatch never blocks the failed event.
        try:
            event_sink(
                node_id=node_id,
                phase=phase,
                error=str(exc),
                error_type=type(exc).__name__,
            )
        except Exception as sink_exc:  # noqa: BLE001
            if _is_cancel_exception(sink_exc):
                raise
            logger.exception("event_sink raised in %s (%s)", node_id, phase)
    except Exception as sink_exc:  # noqa: BLE001
        if _is_cancel_exception(sink_exc):
            raise
        logger.exception("event_sink raised in %s (%s)", node_id, phase)


def _wrap_provider_failure(node_id: str, exc: BaseException) -> "CompilerError":
    """Wrap a provider-side exception into a ``CompilerError``.

    FEAT-006: when the cause carries ``chain_state`` / ``attempts``
    (an ``AllProvidersExhaustedError`` from the router), copy them
    onto the new ``CompilerError`` and append a compact JSON suffix
    to the error message so chatbots and the auto-fix loop can see
    per-provider skip reasons without walking ``__cause__``.

    Without this, the wrap stringifies the cause to just
    ``"All providers exhausted for role=writer. Daemon should retry
    with backoff."`` and the structured diagnostics are silently
    dropped — the failure mode that makes BUG-097's investigation
    daemon recursively self-block on the same opaque error.
    """
    chain_state = getattr(exc, "chain_state", None)
    attempts = getattr(exc, "attempts", None)
    base_msg = f"Provider call failed in node '{node_id}': {exc}"
    if chain_state is not None:
        try:
            suffix = json.dumps(chain_state, default=str, separators=(",", ":"))
            base_msg = f"{base_msg} [chain_state]: {suffix}"
        except Exception:  # noqa: BLE001
            # Never let a serialization edge case prevent the wrap.
            # `default=str` can re-enter into arbitrary user objects whose
            # ``__repr__`` raises, so the catch must be broad.
            logger.exception(
                "Failed to serialize chain_state on provider failure in %s",
                node_id,
            )
    return CompilerError(base_msg, chain_state=chain_state, attempts=attempts)


def _dict_merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge for compile-enforced single-writer state fields."""
    out = dict(left)
    out.update(right)
    return out


def _merge_reducer_fields(schema: list[dict[str, Any]]) -> set[str]:
    return {
        field["name"]
        for field in schema
        if field.get("name")
        and (field.get("reducer") or "").strip().lower() == "merge"
    }


def _declared_node_outputs(node: NodeDefinition) -> set[str]:
    outputs = set(node.output_keys or [])
    for spec_name in (
        "invoke_branch_spec",
        "invoke_branch_version_spec",
        "await_run_spec",
    ):
        spec = getattr(node, spec_name, None)
        if isinstance(spec, dict) and isinstance(spec.get("output_mapping"), dict):
            outputs.update(spec["output_mapping"])
    return outputs


def _validate_single_writer_merge_fields(
    branch: BranchDefinition,
    merge_fields: set[str],
) -> None:
    node_by_id = {node.node_id: node for node in branch.node_defs}
    for field_name in sorted(merge_fields):
        writers = [
            graph_node.id
            for graph_node in branch.graph_nodes
            if field_name in _declared_node_outputs(
                node_by_id[graph_node.node_def_id or graph_node.id],
            )
        ]
        if len(writers) > 1:
            raise CompilerError(
                f"State field '{field_name}' uses reducer='merge' and requires "
                f"a single writer; graph nodes {writers!r} declare it as output."
            )


def _guard_single_writer_merge_outputs(
    fn: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    graph_node_id: str,
    declared_outputs: set[str],
    merge_fields: set[str],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    undeclared_merge_fields = merge_fields - declared_outputs
    if not undeclared_merge_fields:
        return fn

    def _guarded(state: dict[str, Any]) -> dict[str, Any]:
        result = fn(state)
        unexpected = sorted(undeclared_merge_fields.intersection(result))
        if unexpected:
            raise CompilerError(
                f"Graph node '{graph_node_id}' wrote merge-reduced state "
                f"field(s) {unexpected!r} without declaring it in "
                "output_keys/output_mapping; single-writer merge fields "
                "fail closed."
            )
        return result

    return _guarded


_BUILTIN_TYPES: dict[str, Any] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "any": Any,
}


def _resolve_field_type(type_name: str) -> Any:
    """Map a state_schema ``type`` string to a Python type hint.

    Unknown types fall back to ``Any`` — the spec treats state_schema as an
    unvalidated JSON blob in Phase 2/3. Richer typing is Phase 4+.
    """
    return _BUILTIN_TYPES.get((type_name or "").strip().lower(), Any)


def _build_state_typeddict(schema: list[dict[str, Any]]) -> type:
    """Synthesize a TypedDict class from the branch's state_schema.

    Honors PLAN.md hard rule #5: fields declared with ``reducer="append"`` use
    ``Annotated[list, operator.add]``; ``reducer="merge"`` uses a shallow
    dict merger after compile-time single-writer enforcement; anything else
    overwrites.
    """
    annotations: dict[str, Any] = {}
    for field in schema:
        name = (field.get("name") or "").strip()
        if not name:
            continue
        base_type = _resolve_field_type(field.get("type", "any"))
        reducer = (field.get("reducer") or "").strip().lower()
        if reducer == "append":
            annotations[name] = Annotated[list, operator.add]
        elif reducer == "merge":
            annotations[name] = Annotated[dict, _dict_merge]
        else:
            annotations[name] = base_type

    # Build a plain class-based TypedDict at runtime.
    # We construct it via the functional syntax because class-syntax
    # TypedDict requires name binding at class-definition time.
    from typing import TypedDict as _TypedDict

    return _TypedDict("BranchRuntimeState", annotations, total=False)  # type: ignore[operator]


# ─────────────────────────────────────────────────────────────────────────────
# Node adapters
# ─────────────────────────────────────────────────────────────────────────────


# Placeholder match: ``{ident}`` NOT preceded by a backslash. The
# negative lookbehind is what implements the literal-brace escape —
# templates that want a literal ``{foo}`` in output write ``\{foo\}`` and
# the leading backslash keeps the substitution regex from biting.
_PLACEHOLDER_RE = re.compile(r"(?<!\\){([a-zA-Z_][a-zA-Z0-9_]*)}")
# Matches Jinja/Handlebars-style {{ident}} placeholders. Claude.ai and many
# MCP clients emit this form by convention. We normalize to `{ident}` so
# the single regex-driven substitution below handles both forms.
_DOUBLE_PLACEHOLDER_RE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")
# Matches ``\{ident\}`` — an explicitly-escaped placeholder. After
# substitution runs (skipping these thanks to ``_PLACEHOLDER_RE``'s
# lookbehind), we strip the backslashes so the rendered output contains
# ``{ident}`` verbatim.
_ESCAPED_PLACEHOLDER_RE = re.compile(r"\\{([a-zA-Z_][a-zA-Z0-9_]*)\\}")


def _normalize_placeholders(template: str) -> str:
    """Convert ``{{ident}}`` Jinja-style placeholders to Python's ``{ident}``.

    Claude.ai-authored templates typically use doubled braces by
    convention. We do the substitution ourselves (see ``_render_template``)
    so non-identifier braces — JSON examples like ``{"doc": "X"}``, code
    fences, math expressions — pass through as literal text.

    ``\\{ident\\}`` (escaped form) is untouched here — the normalizer only
    handles the Jinja→Python alias; the escape handling lives in
    ``_render_template`` + ``_PLACEHOLDER_RE``'s lookbehind.
    """
    if not template:
        return template
    return _DOUBLE_PLACEHOLDER_RE.sub(r"{\1}", template)


def _unescape_literal_braces(template: str) -> str:
    """Strip backslashes from ``\\{ident\\}`` so rendered output carries
    ``{ident}`` as literal text. Runs after placeholder substitution so
    the escape survives the lookbehind-gated rewrite."""
    if not template:
        return template
    return _ESCAPED_PLACEHOLDER_RE.sub(r"{\1}", template)


def _render_template(template: str, state: dict[str, Any]) -> str:
    """Substitute ``{ident}`` placeholders with ``state[ident]`` values.

    Unlike Python's ``str.format``/``str.format_map`` we do not treat
    single ``{`` / ``}`` as special. Literal braces in JSON examples,
    code fences, and math expressions survive verbatim. Only substrings
    matching a valid identifier placeholder (``{name}``) are replaced;
    everything else is left alone.

    Authors who need a literal ``{ident}`` (for example, documenting
    the substitution syntax itself) escape it as ``\\{ident\\}`` — the
    placeholder regex skips escaped forms and the unescape pass strips
    the backslashes after substitution.

    Raises ``KeyError`` if a valid placeholder references a state key
    that is not present — caller maps that to a ``CompilerError``.
    """
    if not template:
        return template

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in state:
            raise KeyError(key)
        return str(state[key])

    normalized = _normalize_placeholders(template)
    substituted = _PLACEHOLDER_RE.sub(_sub, normalized)
    return _unescape_literal_braces(substituted)


def _missing_state_keys(template: str, state: dict[str, Any]) -> list[str]:
    normalized = _normalize_placeholders(template or "")
    refs = _PLACEHOLDER_RE.findall(normalized)
    return [k for k in refs if k not in state]


def _placeholder_keys(template: str) -> list[str]:
    """Return the set of placeholder identifiers referenced by a template.

    Static analysis — no state lookup, no rendering. Used by
    ``collect_build_warnings`` + the strict-isolation pre-check.
    Escaped ``\\{ident\\}`` forms are NOT placeholders — they render
    literally — so the lookbehind-gated regex naturally excludes them.
    """
    normalized = _normalize_placeholders(template or "")
    # de-dupe while preserving first-occurrence order for stable warnings
    seen: set[str] = set()
    out: list[str] = []
    for k in _PLACEHOLDER_RE.findall(normalized):
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _out_of_input_keys(node: NodeDefinition) -> list[str]:
    """Return placeholder identifiers that reference state keys outside
    the node's declared ``input_keys``.

    Empty list when no input_keys are declared (the node opted out of
    static isolation) or when every placeholder is covered.
    """
    if not node.prompt_template:
        return []
    if not node.input_keys:
        # Node didn't declare an input contract — nothing to check.
        return []
    declared = set(node.input_keys)
    return [k for k in _placeholder_keys(node.prompt_template) if k not in declared]


def collect_build_warnings(branch: BranchDefinition) -> list[dict[str, Any]]:
    """Return non-fatal warnings detected at compile time.

    Currently surfaces one warning per prompt_template node placeholder
    that references a state key outside the node's declared
    ``input_keys``. These leaks are often unintentional and almost
    always reduce the portability of a branch (it implicitly depends on
    producers upstream whose output_keys happen to match the reference).

    Warning shape::

        {
          "kind": "input_keys_leak",
          "node_id": "<id>",
          "placeholder": "<state_key>",
          "declared_input_keys": ["..."],
          "message": "<human-readable>",
        }

    Non-fatal regardless of ``strict_input_isolation`` — the flag
    controls *runtime* rejection. Build-time warnings always surface
    so authors see the leak whether they've opted into strict or not.
    """
    warnings: list[dict[str, Any]] = []
    for node in branch.node_defs:
        leaks = _out_of_input_keys(node)
        for placeholder in leaks:
            warnings.append({
                "kind": "input_keys_leak",
                "node_id": node.node_id,
                "placeholder": placeholder,
                "declared_input_keys": list(node.input_keys),
                "message": (
                    f"Node '{node.node_id}' prompt_template references "
                    f"state key '{placeholder}' which is not in declared "
                    f"input_keys {sorted(node.input_keys)!r}. This is "
                    f"an implicit cross-node dependency that reduces "
                    f"branch portability. Add '{placeholder}' to "
                    f"input_keys, or set strict_input_isolation=false "
                    f"only when the cross-key read is intentional."
                ),
            })
    return warnings


def inspect_node_dry(
    branch: BranchDefinition,
    *,
    node_id: str = "",
) -> dict[str, Any]:
    """Return a side-effect-free structural preview of one node (or all nodes).

    Zero state writes, zero provider calls, zero wiki touches.  Suitable for
    use from ``dry_inspect_node`` MCP action.

    Shape returned per node::

        {
          "node_id": str,
          "node_def": dict,                          # to_dict() snapshot
          "resolved_prompt_template": str | None,    # {{..}} → {..} normalized
          "declared_input_keys": list[str],
          "declared_output_keys": list[str],
          "state_schema_refs": list[str],            # placeholder keys found in template
          "placeholder_validation": {
            "missing": list[str],   # in template but not in state_schema
            "extra": list[str],     # in input_keys but not referenced
            "escaped": list[str],   # \\{ident\\} literals found
          },
          "policy_resolution": {
            "source": "node" | "branch" | "default",
            "effective_policy": dict | None,
            "fallback_chain": list,
          },
          "warnings": list[dict],   # from collect_build_warnings for this node
        }

    If ``node_id`` is empty the return is ``{"nodes": [<shape>, ...]}``.
    If ``node_id`` is given and not found the return is ``{"error": "...", "node_id": node_id}``.
    """
    schema_keys: set[str] = {
        (f.get("name") or "").strip()
        for f in (branch.state_schema or [])
        if (f.get("name") or "").strip()
    }

    def _inspect_one(nd: NodeDefinition) -> dict[str, Any]:
        template = nd.prompt_template or ""
        normalized = _normalize_placeholders(template) if template else ""
        placeholder_keys = _placeholder_keys(template) if template else []
        escaped: list[str] = _ESCAPED_PLACEHOLDER_RE.findall(template) if template else []

        # Keys in template but absent from state_schema
        missing = [k for k in placeholder_keys if k not in schema_keys]
        # Keys declared in input_keys but not referenced in template
        extra = [
            k for k in nd.input_keys
            if k not in placeholder_keys
        ] if nd.prompt_template else []

        # Policy resolution: node > branch > default
        effective_policy = nd.llm_policy or getattr(branch, "default_llm_policy", None)
        if nd.llm_policy is not None:
            policy_source = "node"
        elif getattr(branch, "default_llm_policy", None) is not None:
            policy_source = "branch"
        else:
            policy_source = "default"

        fallback_chain: list[dict[str, Any]] = []
        if isinstance(effective_policy, dict):
            fallback_chain = effective_policy.get("fallback_chain", [])

        # Per-node warnings from the branch-level collector (filter to this node)
        branch_warnings = collect_build_warnings(branch)
        node_warnings = [w for w in branch_warnings if w.get("node_id") == nd.node_id]

        return {
            "node_id": nd.node_id,
            "node_def": nd.to_dict(),
            "resolved_prompt_template": normalized if template else None,
            "declared_input_keys": list(nd.input_keys),
            "declared_output_keys": list(nd.output_keys),
            "state_schema_refs": placeholder_keys,
            "placeholder_validation": {
                "missing": missing,
                "extra": extra,
                "escaped": escaped,
            },
            "policy_resolution": {
                "source": policy_source,
                "effective_policy": effective_policy,
                "fallback_chain": fallback_chain,
            },
            "warnings": node_warnings,
        }

    if node_id:
        nd = branch.get_node_def(node_id)
        if nd is None:
            return {"error": f"Node '{node_id}' not found.", "node_id": node_id}
        return _inspect_one(nd)

    return {"nodes": [_inspect_one(nd) for nd in branch.node_defs]}


_JSON_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _state_type_map(state_schema: list[dict[str, Any]]) -> dict[str, str]:
    """Index ``state_schema`` entries by name for quick type lookup.

    Unknown entries resolve to ``str`` via the caller's fallback. Empty
    schemas yield an empty map — every caller defaults to ``str``.
    """
    types: dict[str, str] = {}
    for field in state_schema or []:
        name = (field.get("name") or "").strip()
        if not name:
            continue
        ftype = (field.get("type") or "str").strip().lower() or "str"
        types[name] = ftype
    return types


def _state_schema_defaults(
    state_schema: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Extract ``{field_name: default_value}`` for every state_schema entry
    that carries a non-None ``default_value``.

    BUG-085 M3: state_schema fields with declared defaults must be
    available to strict-isolation prompt placeholders even when the
    caller didn't pass them in ``inputs``. Callers merge this dict UNDER
    user-provided inputs so explicit caller values still win.

    Codex checker finding 2: each call returns FRESH deepcopies of the
    default values so mutable defaults (``[]``, ``{}``) cannot leak
    mutation across runs. This makes ``_state_schema_defaults`` the
    canonical "fresh defaults" entry point.
    """
    defaults: dict[str, Any] = {}
    for field in state_schema or []:
        if not isinstance(field, dict):
            continue
        name = (field.get("name") or "").strip()
        if not name:
            continue
        # BUG-094: prefer canonical ``default_value`` key (StateFieldDecl),
        # fall back to legacy storage key ``default`` so existing branches
        # built before the key alignment still seed correctly without
        # requiring a data migration.
        if "default_value" in field:
            value = field.get("default_value")
        elif "default" in field:
            value = field.get("default")
        else:
            continue
        if value is None:
            continue
        defaults[name] = copy.deepcopy(value)
    return defaults


def seed_initial_state(
    inputs: dict[str, Any],
    state_schema: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Return a fresh dict with state_schema defaults merged UNDER inputs.

    BUG-085 M3 — used at branch invocation to pre-populate the runtime
    state with any state_schema field that carries a ``default_value``.
    Explicit caller-provided ``inputs`` always win; defaults only fill
    keys the caller did not pass.
    """
    seeded = dict(_state_schema_defaults(state_schema))
    seeded.update(inputs or {})
    return seeded


def _needs_json_contract(
    node: NodeDefinition, state_types: dict[str, str],
) -> bool:
    """True when the node's outputs require structured JSON.

    Two triggers, matching the sibling-bug framing in the navigator
    spec: (1) >=2 output_keys — single-key plain-string write drops
    siblings; (2) any declared output_key has a non-``str`` type in
    ``state_schema`` — prose output can't satisfy a typed slot.
    """
    keys = list(node.output_keys or [])
    if len(keys) >= 2:
        return True
    for k in keys:
        if state_types.get(k, "str") != "str":
            return True
    return False


def _json_contract_suffix(
    node: NodeDefinition, state_types: dict[str, str],
) -> str:
    """Deterministic JSON-schema-style suffix to append to the prompt.

    Kept as a plain ``str`` append (no f-string in the caller) so the
    rendered prompt is still copy-pasteable into other tools. We do not
    use ``response_format`` — 3/6 providers are CLI-wrapped (claude,
    codex, ollama) where no native structured-output hook is wirable.
    """
    lines = [
        "",
        "",
        "RESPONSE FORMAT",
        "---------------",
        "Respond with a single JSON object, no prose, no fences. "
        "Each declared field is required:",
    ]
    for k in node.output_keys:
        t = state_types.get(k, "str")
        lines.append(f"  - {k!r}: {t}")
    lines.append(
        "Do not wrap the object in ``` fences. Do not include any text "
        "before or after the JSON object."
    )
    return "\n".join(lines)


def _coerce_value(raw: Any, t: str) -> Any:
    """Coerce ``raw`` into the declared state_schema type or raise.

    Caller turns failures into ``CompilerError`` with node context. The
    bool/int/float parsers accept common LLM shapes (``"true"`` etc.)
    since the LLM is producing JSON but may emit strings for scalar
    fields.
    """
    if t == "str":
        return str(raw)
    if t == "int":
        if isinstance(raw, bool):
            raise TypeError("bool is not int for this schema")
        return int(raw)
    if t == "float":
        if isinstance(raw, bool):
            raise TypeError("bool is not float for this schema")
        return float(raw)
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(raw)
        if isinstance(raw, str):
            v = raw.strip().lower()
            if v in ("true", "yes", "1"):
                return True
            if v in ("false", "no", "0"):
                return False
            raise TypeError(f"cannot parse {raw!r} as bool")
        raise TypeError(f"cannot parse {type(raw).__name__} as bool")
    if t == "list":
        if not isinstance(raw, list):
            raise TypeError(f"expected list, got {type(raw).__name__}")
        return raw
    if t == "dict":
        if not isinstance(raw, dict):
            raise TypeError(f"expected dict, got {type(raw).__name__}")
        return raw
    # any / unknown — pass through.
    return raw


def _extract_json_object(response: str) -> dict[str, Any]:
    """Parse ``response`` as a JSON object.

    Tolerates a code-fenced object (```json {...}```), a bare object,
    or an object embedded in prose. Raises ValueError on failure so
    the caller can wrap with node context.
    """
    text = response.strip()
    if not text:
        raise ValueError("empty response")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        fence = _JSON_CODE_FENCE_RE.search(text)
        if fence:
            try:
                parsed = json.loads(fence.group(1))
            except json.JSONDecodeError as exc:
                raise ValueError(f"fenced JSON malformed: {exc}") from exc
        else:
            match = _JSON_OBJECT_RE.search(text)
            if not match:
                raise ValueError("no JSON object found in response")
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise ValueError(f"embedded JSON malformed: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"expected JSON object, got {type(parsed).__name__}"
        )
    return parsed


def _build_prompt_template_node(
    node: NodeDefinition,
    *,
    provider_call: Callable[..., str] | None,
    event_sink: Callable[..., None] | None,
    state_schema: list[dict[str, Any]] | None = None,
    llm_policy: dict[str, Any] | None = None,
    concurrency_tracker: ConcurrencyTracker | None = None,
    universe_context: "UniverseContext | None" = None,
    branch_def_id: str = "",
    seat_scope: tuple[Path, str] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Return a node function that fills the prompt template and calls an
    LLM. Output is stored under the node's first ``output_keys`` entry
    (or ``<node_id>_output`` if none declared).

    When ``llm_policy`` is set, an injected caller that owns a
    ``call_with_policy_sync`` method retains dispatch authority. Ordinary
    function callers use the shared ``ProviderRouter`` when available and
    otherwise fall back to the plain ``provider_call`` callable.
    """

    output_key = (
        node.output_keys[0] if node.output_keys else f"{node.node_id}_output"
    )
    role = (node.model_hint or "writer").strip().lower() or "writer"
    template = node.prompt_template or ""
    timeout_s = float(node.timeout_seconds or 300.0)
    from tinyassets.served_tools import AGENT_NODE_MARKERS

    agent_node_id = (
        node.node_id if AGENT_NODE_MARKERS.intersection(node.tools_allowed or []) else ""
    )
    agent_node_key = ""
    if agent_node_id:
        from tinyassets.served_tools import node_tool_grant
        from tinyassets.shared_self import agent_node_key as _agent_node_key

        agent_node_key = _agent_node_key(branch_def_id, node)

        try:
            node_tool_grant(node.tools_allowed)
        except ValueError as exc:
            raise CompilerError(f"Node '{node.node_id}': {exc}") from exc
        # An agent node is the converse turn, which runs until it finishes: its
        # seat is that turn's bound, not a node timeout. The executor wants a
        # number, so an unset universe cap becomes an unreachable one rather than
        # a made-up hour (founder, 2026-09-30: a turn runs until it is finished).
        from tinyassets.universe_intelligence import (
            UNBOUNDED_TURN_SECONDS,
            served_absolute_cap_s,
        )

        own_cap = served_absolute_cap_s(getattr(universe_context, "config", None))
        timeout_s = UNBOUNDED_TURN_SECONDS if own_cap is None else own_cap
    strict_isolation = bool(getattr(node, "strict_input_isolation", True))
    declared_inputs = list(node.input_keys)
    # BUG-085 (Codex checker finding 1): state_schema fields carrying a
    # default_value count as effectively-declared input. Without this,
    # the strict render filter drops seeded defaults when the node
    # declares any explicit input_keys, and the truly_outside partition
    # falsely flags defaulted-but-referenced keys as isolation
    # violations. Compute the union ONCE at closure-build time.
    schema_defaulted_keys = set(_state_schema_defaults(state_schema).keys())
    effective_declared = set(declared_inputs) | schema_defaulted_keys
    state_types = _state_type_map(state_schema or [])
    needs_json = _needs_json_contract(node, state_types)
    json_suffix = _json_contract_suffix(node, state_types) if needs_json else ""
    effective_policy: dict[str, Any] | None = llm_policy

    # Per-node provider config — REAL settings threaded to the subprocess, not
    # prompt hints: reasoning_effort (e.g. localize=minimal so a light task is
    # fast+cheap) + the node's own timeout. Built once per node. ModelConfig is
    # imported lazily so graph_compiler keeps no hard provider import.
    _node_reasoning_effort = (getattr(node, "reasoning_effort", "") or "").strip()
    try:
        from tinyassets.providers.base import ModelConfig as _ModelConfig
        _node_cfg: Any = _ModelConfig(
            # Floor at 1s: a sub-second node timeout (e.g. 0.5) must not become
            # a provider timeout of 0 (int(0.5)==0 → instant provider timeout).
            timeout=max(1, int(timeout_s)),
            # Streaming providers use this cap, not the legacy timeout scalar.
            absolute_cap_s=timeout_s,
            reasoning_effort=_node_reasoning_effort,
            agent_node_id=agent_node_id,
            agent_node_key=agent_node_key,
        )
    except Exception:  # pragma: no cover - defensive; provider import is optional
        _node_cfg = None
    # Only pass config to the injected provider bridge when its signature
    # accepts it (protects test stubs / older bridges).
    try:
        import inspect as _inspect
        _bridge_params = _inspect.signature(provider_call).parameters
        _bridge_takes_config = bool(provider_call) and (
            "config" in _bridge_params
            or any(
                parameter.kind == parameter.VAR_KEYWORD
                for parameter in _bridge_params.values()
            )
        )
        _bridge_takes_observer = bool(provider_call) and (
            "response_observer" in _bridge_params
            or any(p.kind == p.VAR_KEYWORD for p in _bridge_params.values())
        )
    except (ValueError, TypeError):
        _bridge_takes_config = False
        _bridge_takes_observer = False
    _injected_policy_caller = (
        provider_call
        if callable(getattr(provider_call, "call_with_policy_sync", None))
        else None
    )

    def _bridge(_p: str, _s: str, observer, cfg: Any = None) -> str:
        kwargs: dict[str, Any] = {"role": role}
        effective_cfg = cfg if cfg is not None else _node_cfg
        if _bridge_takes_config and effective_cfg is not None:
            kwargs["config"] = effective_cfg
        if universe_context is not None:
            kwargs["universe_context"] = universe_context
        if _bridge_takes_observer:
            kwargs["response_observer"] = observer
        return provider_call(_p, _s, **kwargs)

    # Lazy import so graph_compiler doesn't hard-depend on providers at import
    # time. Aliased so the except-clauses below can reference it by name without
    # re-importing inside every invocation.
    try:
        from tinyassets.providers.base import SandboxUnavailableError as _SandboxUnavailableError
    except Exception:  # noqa: BLE001 — import failure must not break compilation
        _SandboxUnavailableError = type("_SandboxUnavailableError", (Exception,), {})  # type: ignore[assignment,misc]

    def _fn(state: dict[str, Any]) -> dict[str, Any]:
        # Normalize Jinja-style ``{{var}}`` into Python's ``{var}``.
        # Claude.ai-authored prompt_templates tend to use doubled braces
        # by convention; without this the braces are passed through to
        # the LLM as literal text.
        rendered_template = _normalize_placeholders(template)

        # Strict input-keys isolation: filter the state view to just
        # the declared input_keys BEFORE rendering. Out-of-input-keys
        # placeholders then trip the missing-state-keys check below
        # and raise CompilerError — never silently read leaked state.
        if strict_isolation and declared_inputs:
            # BUG-085: include state_schema-defaulted keys in the render
            # allow-list so seeded defaults are visible to the prompt
            # even when not duplicated into the node's input_keys.
            render_state: dict[str, Any] = {
                k: state[k] for k in effective_declared if k in state
            }
        else:
            render_state = state

        # Non-strict path: emit a warning event per out-of-input-keys
        # reference so the warning shows up in the per-run event log
        # even when the author hasn't opted into strict mode. Run-time
        # warnings mirror the build-time ones from collect_build_warnings.
        if not strict_isolation and declared_inputs and event_sink is not None:
            for placeholder in _out_of_input_keys(node):
                try:
                    event_sink(
                        node_id=node.node_id,
                        phase="warning",
                        kind="input_keys_leak",
                        placeholder=placeholder,
                        declared_input_keys=declared_inputs,
                    )
                except Exception as exc:  # noqa: BLE001
                    if _is_cancel_exception(exc):
                        raise
                    logger.exception(
                        "event_sink raised emitting input_keys_leak "
                        "warning for %s", node.node_id,
                    )

        missing = _missing_state_keys(template, render_state)
        if missing:
            if strict_isolation:
                # BUG-085: partition the missing keys into two categories
                # so the operator sees the ACTUAL failure mode, not a
                # contradictory "outside declared input_keys" message for
                # keys that ARE in the declared list but simply aren't in
                # state yet (upstream node hasn't produced them, or a
                # state_schema default wasn't seeded).
                # state_schema-defaulted keys count as effectively-declared
                # (Codex checker finding 1) — a referenced default key
                # that simply isn't in state at execution time is NOT an
                # isolation violation, it's an unavailability problem.
                truly_outside = [
                    k for k in missing if k not in effective_declared
                ]
                declared_but_unavailable = [
                    k for k in missing if k in effective_declared
                ]
                # Prioritize the actionable "outside" diagnosis when both
                # categories are present — that's the real isolation
                # violation; the unavailable-declared keys are noted as
                # secondary context so the operator gets the whole picture.
                if truly_outside and declared_but_unavailable:
                    raise CompilerError(
                        f"Node '{node.node_id}' (strict_input_isolation=true) "
                        f"prompt references state keys {truly_outside} "
                        f"outside declared input_keys "
                        f"{sorted(declared_inputs)!r}. "
                        f"Add the keys to input_keys or clear the flag. "
                        f"Additionally, declared input_keys "
                        f"{declared_but_unavailable} are not present in "
                        f"state at execution time — likely an upstream "
                        f"node did not produce them, or a state_schema "
                        f"default was not initialized."
                    )
                if truly_outside:
                    raise CompilerError(
                        f"Node '{node.node_id}' (strict_input_isolation=true) "
                        f"prompt references state keys {truly_outside} "
                        f"outside declared input_keys "
                        f"{sorted(declared_inputs)!r}. "
                        f"Add the keys to input_keys or clear the flag."
                    )
                # All missing keys ARE declared — the failure is that
                # state didn't contain them at execution time.
                raise CompilerError(
                    f"Node '{node.node_id}' (strict_input_isolation=true) "
                    f"prompt references declared input_keys "
                    f"{declared_but_unavailable} that are not present in "
                    f"state at execution time. "
                    f"Likely cause: an upstream node did not produce these "
                    f"keys, or a state_schema field default was not "
                    f"initialized into the run's initial state."
                )
            raise CompilerError(
                f"Node '{node.node_id}' prompt references missing "
                f"state keys: {missing}"
            )
        try:
            prompt = _render_template(rendered_template, render_state)
        except KeyError as exc:
            raise CompilerError(
                f"Node '{node.node_id}' prompt format failed: "
                f"missing state key {exc}"
            ) from exc

        # Multi-output or typed-output nodes get a JSON contract
        # appended. Providers stay untouched — the contract is plain
        # text so every provider (including CLI-wrapped ones) sees the
        # same prompt shape (hard-rule #8: no divergent silent-drop).
        if needs_json:
            prompt = prompt + json_suffix

        # Emit a "starting" event BEFORE the provider call so long-running
        # LLM nodes don't look frozen to a polling client (#60). The
        # matching "ran" event fires after the call completes.
        if event_sink is not None:
            try:
                event_sink(
                    node_id=node.node_id,
                    phase="starting", role=role,
                    prompt_preview=prompt[:200],
                )
            except Exception as exc:
                if _is_cancel_exception(exc):
                    raise
                logger.exception(
                    "event_sink raised in %s (starting)", node.node_id,
                )

        if concurrency_tracker is not None:
            concurrency_tracker.acquire()
        try:
            from tinyassets.providers.execution_receipt import (
                WriterExecutionReceipt,
                normalize_execution_receipt,
            )

            # Per invocation, never a graph/session-wide first/last-response slot.
            execution_receipt = WriterExecutionReceipt()
            provider_served: str = "unknown"
            provider_meta: dict[str, Any] = {}
            provider_timing: dict[str, Any] = {}

            def _observe_response(resp: Any) -> None:
                execution_receipt.observe(resp)
                provider_timing.clear()
                provider_timing.update(_provider_timing(resp))

            # _run_with_timeout counts timeout_s from submit(), so a call that
            # waited in the pool queue must not then be handed the node's FULL
            # budget as its provider cap — it would outlive the node's own
            # deadline by exactly the queue wait. Measured on the worker (these
            # closures run there), so it reflects real wait, not submit time.
            # Restamped when the call gets its account seat (`_seated`): a wait
            # for a seat is not the provider's time, and charging it would hand a
            # served call an already-spent budget (gpt-6-astra round 2, P1).
            _submitted = [time.monotonic()]

            def _seated() -> None:
                _submitted[0] = time.monotonic()

            def _deadline_cfg() -> Any:
                """The node's REMAINING budget as a provider cap.

                Returns a fresh ModelConfig — never a mutation. ModelConfig is
                frozen, and one compiled closure serves concurrent invocations,
                so the shared per-node config must stay untouched.
                """
                if _node_cfg is None:
                    return None
                waited = time.monotonic() - _submitted[0]
                if waited < _QUEUE_WAIT_SUBTRACT_THRESHOLD_S:
                    # Scheduling jitter, not queue wait. Hand over the node's
                    # own config unchanged so an unqueued call is unaffected.
                    return _node_cfg
                # The cap is the REMAINING budget, with no floor of its own: a
                # 0.5s node that spent 0.4s queued gets 0.1s, not 0.5s. Any
                # floor above an epsilon re-grants time the queue already
                # spent, which is the defect. See _MIN_POSITIVE_PROVIDER_CAP_S.
                remaining = max(_MIN_POSITIVE_PROVIDER_CAP_S, timeout_s - waited)
                return _dataclasses.replace(
                    _node_cfg,
                    # Legacy int-seconds scalar, honored by the NON-streaming
                    # providers. It cannot represent a sub-second budget at all,
                    # so it carries the same max(1, int(...)) floor the node's
                    # own config already carries — a representation limit of
                    # that field, NOT the node's remaining budget, which is
                    # absolute_cap_s. It is never raised above the node's own
                    # legacy timeout, since remaining <= timeout_s.
                    timeout=max(1, int(remaining)),
                    absolute_cap_s=remaining,
                )

            if provider_call is None:
                if effective_policy:
                    preferred = effective_policy.get("preferred", {})
                    requested = preferred.get("model_id", preferred.get("model")) or "default"
                    raise CompilerError(
                        f"Requested model {requested} "
                        f"on {preferred.get('provider') or 'selected source'} "
                        "cannot run: no bound provider policy execution path"
                    )
                response = f"[Mock response for {node.node_id}]"
                provider_served = "mock"
            elif effective_policy:
                # Policy-aware path: route through ProviderRouter.call_with_policy_sync
                try:
                    # Only the run's own injected, universe-bound caller may
                    # honour a policy. There is no shared-router fallback
                    # (Hard Rule 15): without one, the policy node goes
                    # through the same bound bridge as any other node.
                    _policy_router = _injected_policy_caller
                    router_providers = getattr(
                        _policy_router, "available_providers", None,
                    )
                    router_has_providers = (
                        router_providers is None or bool(router_providers)
                    )
                    if _policy_router is not None and router_has_providers:
                        def _policy_call() -> tuple[str, str, dict]:
                            return _call_policy_router_with_retry(
                                _policy_router,
                                role=role,
                                prompt=prompt,
                                system="",
                                policy=effective_policy,
                                config=_deadline_cfg(),
                                universe_context=universe_context,
                                response_observer=_observe_response,
                            )
                        text_and_name = _run_agent_with_timeout(
                            _policy_call,
                            timeout_s=timeout_s,
                            node_id=node.node_id,
                            universe_context=universe_context, event_sink=event_sink,
                            seat_scope=seat_scope, on_seated=_seated,
                        )
                        response, provider_served, provider_meta = text_and_name
                    else:
                        preferred = effective_policy.get("preferred", {})
                        requested = preferred.get("model_id", preferred.get("model")) or "default"
                        raise CompilerError(
                            f"Requested model {requested} "
                            f"on {preferred.get('provider') or 'selected source'} cannot run: "
                            "the bound provider has no available policy execution path"
                        )
                except NodeTimeoutError:
                    raise
                except _SandboxUnavailableError:
                    raise
                except Exception as exc:
                    logger.exception("Policy provider call failed in %s", node.node_id)
                    _emit_failed_event(event_sink, node.node_id, exc)
                    raise _wrap_provider_failure(node.node_id, exc) from exc
            else:
                try:
                    response = _run_agent_with_timeout(
                        lambda: _bridge(
                                prompt, "", _observe_response, _deadline_cfg(),
                            ),
                        timeout_s=timeout_s,
                        node_id=node.node_id,
                        universe_context=universe_context, event_sink=event_sink,
                        seat_scope=seat_scope, on_seated=_seated,
                    )
                except NodeTimeoutError:
                    raise
                except _SandboxUnavailableError:
                    raise
                except Exception as exc:
                    logger.exception("Provider call failed in %s", node.node_id)
                    _emit_failed_event(event_sink, node.node_id, exc)
                    raise _wrap_provider_failure(node.node_id, exc) from exc
        finally:
            if concurrency_tracker is not None:
                concurrency_tracker.release()

        if not response:
            raise EmptyResponseError(
                f"Node '{node.node_id}': LLM returned empty response — "
                f"check provider availability and credentials",
                node_id=node.node_id,
            )

        # Defense-in-depth: if a subprocess provider leaked a bwrap failure
        # into the response text instead of raising, catch it here so the
        # garbage never propagates into state (hard-rule #8 fail-loudly).
        try:
            from tinyassets.providers.base import check_bwrap_failure
            check_bwrap_failure(response)
        except _SandboxUnavailableError:
            raise
        except Exception:  # noqa: BLE001 — import/probe errors must not block runs
            pass

        if event_sink is not None:
            try:
                _meta_detail = {}
                if provider_meta:
                    _meta_detail = {
                        "provider_model": provider_meta.get("model", ""),
                        "provider_latency_ms": provider_meta.get("latency_ms"),
                        "provider_attempts": provider_meta.get("attempts"),
                        "provider_degraded": provider_meta.get("degraded", False),
                    }
                if provider_timing:
                    _meta_detail["provider_timing"] = dict(provider_timing)
                event_sink(
                    node_id=node.node_id,
                    phase="ran",
                    prompt=prompt, response=response, role=role,
                    provider_served=provider_served,
                    execution=(execution_receipt.projection() or
                               normalize_execution_receipt(provider_meta.get("execution"))),
                    **_meta_detail,
                )
            except Exception as exc:
                if _is_cancel_exception(exc):
                    raise
                logger.exception("event_sink raised in %s", node.node_id)

        # JSON-contract path: parse response, assign EVERY declared
        # output_key from the parsed object, coerce types. Missing
        # key / wrong type / malformed JSON all raise CompilerError
        # (hard-rule #8). Fixes the multi-output silent-drop + typed
        # no-op bugs at the same layer.
        if needs_json:
            try:
                parsed = _extract_json_object(response)
            except ValueError as exc:
                raise CompilerError(
                    f"Node '{node.node_id}' expected JSON object response "
                    f"for output_keys {list(node.output_keys)!r}: {exc}. "
                    f"Raw response: {response[:400]!r}"
                ) from exc
            result: dict[str, Any] = {}
            for key in node.output_keys:
                if key not in parsed:
                    raise CompilerError(
                        f"Node '{node.node_id}' JSON response missing "
                        f"declared output_key '{key}'. Got keys: "
                        f"{sorted(parsed.keys())!r}."
                    )
                t = state_types.get(key, "str")
                try:
                    result[key] = _coerce_value(parsed[key], t)
                except (TypeError, ValueError) as exc:
                    raise CompilerError(
                        f"Node '{node.node_id}' output_key '{key}' type "
                        f"coercion to '{t}' failed: {exc}. "
                        f"Got: {parsed[key]!r}."
                    ) from exc
            return result

        return {output_key: response}

    return _fn


def source_code_problems(source_code: str, node_id: str) -> list[str]:
    """Everything the compiler will refuse about one code node, as sentences.

    This is the single definition of "will this code node compile". The
    compile-time gate raises the first entry; the authoring receipts
    (``get_branch`` / ``validate`` / ``describe`` / the build receipt) report
    ``runnable`` from whether the list is empty. Two definitions of that one
    fact is what sent the founder's universe planning around a ``runnable:
    false`` the runtime never enforced (concern 2026-09-01, live thread
    2026-09-02: "platform-side source-code approval is the missing piece").

    These are pre-checks; the OS jail is the execution boundary.
    An empty ``source_code`` is not a code node and has no problems.
    """
    src = source_code or ""
    if not src:
        return []
    problems: list[str] = []
    try:
        patterns = dangerous_source_patterns(src, _DANGEROUS_PATTERNS)
    except (SyntaxError, ValueError):
        patterns = []  # The syntax diagnostic below retains the compiler's detail.
    for pattern in patterns:
        problems.append(
            f"Node '{node_id}' source_code contains disallowed "
            f"pattern: '{pattern}'"
        )
    size = len(src.encode("utf-8"))
    if size > _MAX_SOURCE_CODE_BYTES:
        problems.append(
            f"Node '{node_id}' source_code is {size} bytes; "
            f"the cap is {_MAX_SOURCE_CODE_BYTES}"
        )
    try:
        compile(src, f"<node {node_id}>", "exec")
    except (SyntaxError, ValueError, RecursionError) as exc:
        problems.append(f"Node '{node_id}' source_code does not parse: {exc}")
    return problems


def _validate_source_code(node: NodeDefinition) -> None:
    """Compile-time gate for a source_code node (design D2, change
    `sandboxed-code-node`). The OS sandbox is the authority boundary - the
    child has no credentials, no network and no data dir, and its only output
    is a state delta that reaches the world through the owner's consent-gated
    effects - so there is no host-approval check here:
    ``approved`` / ``approved_source_hash`` are provenance, not a gate.

    What stays is defence in depth: the disallowed-pattern scan, a size cap
    and a syntax check, all in :func:`source_code_problems`. A code node
    SHOULD declare ``output_keys`` - anything it returns under another key is
    dropped, named in the node's event."""
    problems = source_code_problems(node.source_code or "", node.node_id)
    if not problems:
        return
    if problems[0].startswith(f"Node '{node.node_id}' source_code does not parse"):
        # Keep the SyntaxError as __cause__, as the gate always did (Codex).
        try:
            compile(node.source_code or "", f"<node {node.node_id}>", "exec")
        except SyntaxError as exc:
            raise CompilerError(problems[0]) from exc
    raise CompilerError(problems[0])


_NODE_MCP_ACTION_ALIASES: dict[str, tuple[str, str]] = {
    "goals.leaderboard": ("goals", "leaderboard"),
    "goal_leaderboard": ("goals", "leaderboard"),
    "quality_leaderboard": ("goals", "leaderboard"),
    "goals.archive_consultation": ("goals", "archive_consultation"),
    "goal_archive_consultation": ("goals", "archive_consultation"),
    "gates.leaderboard": ("gates", "leaderboard"),
    "gates_leaderboard": ("gates", "leaderboard"),
    "gates.get_ladder": ("gates", "get_ladder"),
    # Wiki READS only — knowledge-commons consultation (e.g. a driver branch
    # enumerating the bug/patch backlog). Writes are never aliased in-node; a
    # node that needs to publish goes through an effect, not invoke_mcp_action.
    "wiki.read": ("wiki", "read"),
    "wiki_read": ("wiki", "read"),
    "wiki.search": ("wiki", "search"),
    "wiki_search": ("wiki", "search"),
    "wiki.list": ("wiki", "list"),
    "wiki_list": ("wiki", "list"),
    "wiki.since": ("wiki", "since"),
    "wiki_since": ("wiki", "since"),
    "wiki.lint": ("wiki", "lint"),
    "wiki_lint": ("wiki", "lint"),
    # Paced ENQUEUE — append a run request to this universe's dispatcher queue.
    # NOT a synchronous spawn: the daemon's concurrency cap + per-provider
    # cooldown pace execution. Bounded by a spawn-depth cap + a per-run enqueue
    # budget, and gated behind TINYASSETS_NODE_ENQUEUE_ENABLED (fail-closed
    # by default; the production deploy explicitly enables it).
    "enqueue_branch_run": ("dispatch", "enqueue"),
    "dispatch.enqueue": ("dispatch", "enqueue"),
    # Native delivery reuses explicit link authority; no arbitrary graph writes.
    "deliver_output": ("delivery", "deliver_output"),
    "read_run_file": ("run_files", "read_run_file"),
    "notify": ("notifications", "notify"),
}


@dataclass(frozen=True)
class NodeEnqueueContext:
    """Trusted, server-set execution context for the in-node enqueue verb.

    Carries the *current run's* universe from the dispatcher down to the
    enqueue helper; none of it is branch-authored. ``actor`` and the lineage
    fields are retained for context compatibility but are not authority: the
    wake's owner is the principal bound for the run (``owner_run_identity``).
    """

    universe_id: str = ""
    actor: str = ""
    parent_branch_task_id: str = ""
    origin_branch_task_id: str = ""


def _node_enqueue_branch_run(
    node: "NodeDefinition",
    kwargs: dict[str, Any],
    *,
    base_path: str | Path | None = None,
    context: "NodeEnqueueContext | None" = None,
) -> str:
    """Wake one of the owner's branches in this universe, now or not before a time.

    Stores a one-shot automation (``tinyassets.automations``, kind ``once``)
    that the automation pump fires once ``not_before`` has passed -- durable
    across deploys, retried if its process dies before the run starts, and
    re-checked for the owner's authority when it fires. Any wake behaviour a
    graph wants (a loop, a backoff, a reminder) is this plus the graph's own
    logic (founder, 2026-09-27: "really any wake behavior").

    Limited by usage, not shape: each wake is charged to the universe's
    engine admissions when it is stored, and to its run admission when it
    fires. There is no spawn-depth, fan-out, lineage or pending-count cap.

    The cross-user floor:
      * the universe is the run's trusted one, never a branch-named one;
      * the owner is the principal bound for this run -- the request actor, or
        the owner ``owner_run_identity`` binds for a background run -- and
        ``register_automation`` requires them to be an admin whose home this is;
      * the branch must be one that owner authored.
    Returns a JSON string so the caller's standard parse step applies.
    """
    from datetime import datetime, timezone

    ctx = context or NodeEnqueueContext()

    target = str(kwargs.get("branch_def_id", "")).strip()
    if not target:
        raise CompilerError(
            f"Node '{node.node_id}' enqueue requires a non-empty branch_def_id."
        )
    run_inputs = kwargs.get("inputs") or {}
    if not isinstance(run_inputs, dict):
        raise CompilerError(
            f"Node '{node.node_id}' enqueue inputs must be an object, got "
            f"{type(run_inputs).__name__}."
        )

    # Universe targeting: ONLY the run's own trusted universe (set server-side),
    # never a branch-named one. A caller-supplied universe_id may only echo it.
    trusted_uid = str(ctx.universe_id or "").strip()
    if not trusted_uid:
        raise CompilerError(
            f"Node '{node.node_id}' enqueue refused: no trusted command center "
            f"context. In-node enqueue is available only for command center runs."
        )
    requested_uid = str(kwargs.get("universe_id", "")).strip()
    if requested_uid and requested_uid != trusted_uid:
        raise CompilerError(
            f"Node '{node.node_id}' enqueue refused: cannot target command center "
            f"'{requested_uid}'; this run executes in '{trusted_uid}'."
        )
    if base_path is None:
        raise CompilerError(
            f"Node '{node.node_id}' enqueue refused: no run context available "
            f"to register the wake."
        )

    now = datetime.now(timezone.utc)
    not_before = _enqueue_not_before(node, kwargs, now)

    from tinyassets.api.permissions import current_request_actor_id
    from tinyassets.automations import AutomationUnavailable, register_automation

    owner = current_request_actor_id()
    if not owner:
        raise CompilerError(
            f"Node '{node.node_id}' enqueue refused: no owner is bound to this "
            f"run, so there is no one to run the wake as."
        )
    try:
        wake = register_automation(
            base_path,
            universe_id=trusted_uid,
            owner_principal_id=owner,
            name=f"wake:{target}"[:120],
            branch_def_id=target,
            not_before=not_before,
            inputs=dict(run_inputs),
            now=now,
        )
    except AutomationUnavailable as exc:
        raise CompilerError(
            f"Node '{node.node_id}' enqueue refused: {exc.reason}."
        ) from exc
    return json.dumps({
        "status": "enqueued",
        "automation_id": wake.automation_id,
        "branch_def_id": wake.branch_def_id,
        "universe_id": wake.universe_id,
        "not_before": wake.not_before,
    })


def _node_served_tool_call(
    node: NodeDefinition,
    name: str,
    arguments: dict[str, Any],
    *,
    allowed: set[str],
    execution_context: "BranchExecutionContext | None",
    should_cancel: Callable[[], bool] | None,
) -> dict[str, Any]:
    """One served tool, called as the run's owner and pinned to its universe.

    The grant is the node's ``tools_allowed``: naming no served tool grants all
    of them (what the owner's chat has), naming some grants exactly those. Owner
    and universe come from the run's immutable execution context, never from the
    node. Foreign code is refused before its sandbox starts; this rechecks it.
    """
    import asyncio

    from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS

    granted = allowed & set(SERVED_ENGINE_MCP_TOOLS)
    if granted and name not in granted:
        raise CompilerError(
            f"Node '{node.node_id}' is not granted the served tool '{name}'; "
            f"its grant is {sorted(granted)}."
        )
    ctx = execution_context
    owner = (getattr(ctx, "owner_user_id", "") or "").strip()
    universe = (getattr(ctx, "universe_id", "") or "").strip()
    if (not owner or not universe
            or getattr(ctx, "caller_provenance", "") != "own"
            or (getattr(ctx, "definition_author", "") or "").strip() != owner):
        raise CompilerError(
            f"Node '{node.node_id}' may call served tools only in its owner's own "
            "run of a branch that owner authored."
        )
    # Current serving-owner authority is checked by the route read itself.
    if should_cancel is not None and should_cancel():
        raise CompilerError(f"Node '{node.node_id}': run cancelled before '{name}'")

    async def _call():
        from tinyassets.engine_tool_client import open_engine_tools

        async with open_engine_tools(actor_id=owner, graph_id=universe,
                                     enabled_tools=(name,)) as tools:
            return await tools.call(name, arguments)

    try:
        result = asyncio.run(_call())
    except Exception as exc:
        if _is_cancel_exception(exc):
            raise
        raise CompilerError(
            f"Node '{node.node_id}' served tool '{name}' failed: {exc}"
        ) from exc
    text = "".join(
        getattr(part, "text", "") for part in (getattr(result, "content", None) or ())
    )
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        data = None
    return {"is_error": bool(getattr(result, "isError", False)), "text": text, "data": data}


def _enqueue_not_before(
    node: "NodeDefinition", kwargs: dict[str, Any], now: Any,
) -> str:
    """``not_before`` (ISO) or ``delay_seconds`` (a number), at most one."""
    from datetime import timedelta

    raw_at = kwargs.get("not_before")
    raw_delay = kwargs.get("delay_seconds")
    if raw_at not in (None, "") and raw_delay not in (None, ""):
        raise CompilerError(
            f"Node '{node.node_id}' enqueue takes not_before OR delay_seconds, "
            f"not both."
        )
    if raw_delay not in (None, ""):
        if isinstance(raw_delay, bool):
            raise CompilerError(
                f"Node '{node.node_id}' enqueue delay_seconds must be a number."
            )
        try:
            delay = float(raw_delay)
        except (TypeError, ValueError):
            raise CompilerError(
                f"Node '{node.node_id}' enqueue delay_seconds must be a number."
            ) from None
        if delay != delay or delay < 0:  # NaN or negative
            raise CompilerError(
                f"Node '{node.node_id}' enqueue delay_seconds must be >= 0."
            )
        try:
            return (now + timedelta(seconds=delay)).isoformat()
        except OverflowError:
            raise CompilerError(
                f"Node '{node.node_id}' enqueue delay_seconds is too large."
            ) from None
    if raw_at not in (None, ""):
        return str(raw_at)
    return now.isoformat()


def _build_node_mcp_invoker(
    node: NodeDefinition,
    *,
    event_sink: Callable[..., None] | None,
    invocation_depth: int = 0,
    base_path: str | Path | None = None,
    enqueue_context: "NodeEnqueueContext | None" = None,
    delivery_source: Any = None,
    file_source: Any = None,
    file_inputs: dict[str, Any] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    execution_context: "BranchExecutionContext | None" = None,
) -> Callable[..., dict[str, Any]]:
    allowed = set(node.tools_allowed or [])

    def _invoke_mcp_action(action_name: str, **kwargs: Any) -> dict[str, Any]:
        requested = str(action_name or "").strip()
        if not requested:
            raise CompilerError(
                f"Node '{node.node_id}' invoke_mcp_action requires action_name."
            )
        from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS

        if requested in SERVED_ENGINE_MCP_TOOLS:
            return _node_served_tool_call(
                node, requested, kwargs, allowed=allowed,
                execution_context=execution_context, should_cancel=should_cancel,
            )
        resolved = _NODE_MCP_ACTION_ALIASES.get(requested)
        if resolved is None:
            raise CompilerError(
                f"Node '{node.node_id}' requested unsupported MCP action "
                f"'{requested}'. Supported actions: "
                f"{sorted(_NODE_MCP_ACTION_ALIASES)}"
            )
        canonical = ".".join(resolved)
        if requested not in allowed and canonical not in allowed:
            raise CompilerError(
                f"Node '{node.node_id}' is not allowed to call MCP action "
                f"'{requested}'. Declare it in tools_allowed first."
            )

        if event_sink is not None:
            try:
                event_sink(
                    node_id=node.node_id,
                    phase="mcp_action",
                    action_name=requested,
                    canonical_action=canonical,
                )
            except Exception as exc:
                if _is_cancel_exception(exc):
                    raise
                logger.exception(
                    "event_sink raised for node MCP action in %s",
                    node.node_id,
                )

        tool_name, action = resolved
        if tool_name == "notifications":
            return _node_served_tool_call(
                node, "write_graph",
                {"target": "pending_request", "operation": "notify",
                 "payload_json": json.dumps(kwargs)},
                allowed={"write_graph"}, execution_context=execution_context,
                should_cancel=should_cancel,
            )
        if tool_name == "run_files":
            from tinyassets.run_file_node import read_node_file

            if set(kwargs) != {"file_id", "offset", "count"}:
                raise CompilerError("file_read_parameters")
            if base_path is None:
                raise CompilerError("file_node_authority_unavailable")
            return read_node_file(
                base_path, source=file_source, incoming=file_inputs,
                should_cancel=should_cancel or (lambda: False), **kwargs,
            )
        if tool_name == "delivery":
            from tinyassets.api.deliveries import deliver_node_output

            if set(kwargs) != {"link_id", "occurrence_id", "outputs"}:
                raise CompilerError("invalid_delivery_parameters")
            if base_path is None:
                raise CompilerError("delivery_source_unavailable")
            return deliver_node_output(
                base_path, source=delivery_source, should_cancel=should_cancel or (lambda: False),
                **kwargs,
            )
        if tool_name == "goals":
            from tinyassets.api.market import goals

            raw = goals(action=action, **kwargs)
        elif tool_name == "gates":
            from tinyassets.api.market import gates

            raw = gates(action=action, **kwargs)
        elif tool_name == "wiki":
            from tinyassets.api.wiki import WIKI_WRITE_ACTIONS, wiki

            # Defense-in-depth: the alias map only lists reads, but never let a
            # write reach the wiki from in-node code even if one is aliased.
            if action in WIKI_WRITE_ACTIONS:
                raise CompilerError(
                    f"Node '{node.node_id}' may use in-node knowledge reads only; "
                    f"'{action}' is a write and is not exposed in-node."
                )
            raw = wiki(action=action, **kwargs)
        elif tool_name == "dispatch":
            raw = _node_enqueue_branch_run(
                node, kwargs,
                base_path=base_path, context=enqueue_context,
            )
        else:  # pragma: no cover - mapping owns the dispatch domains.
            raise CompilerError(
                f"Node '{node.node_id}' requested unsupported MCP tool "
                f"'{tool_name}'."
            )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CompilerError(
                f"Node '{node.node_id}' MCP action '{requested}' returned "
                f"invalid JSON: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise CompilerError(
                f"Node '{node.node_id}' MCP action '{requested}' returned "
                f"{type(parsed).__name__}, expected object."
            )
        return parsed

    return _invoke_mcp_action


def _workspace_bind_roots(base_path: str | Path | None) -> tuple[str, ...]:
    """The two roots a workspace bind may live under, derived the way the
    adapter derives them: the shared scratch pool beside the universe, and the
    universe's own workspaces. An unknown base path vouches for nothing, and an
    empty tuple refuses every bind - the fail-closed direction.
    """
    if not base_path:
        return ()
    universe_dir = Path(base_path)
    return (
        str(universe_dir.parent / "scratch"),
        str(universe_dir / "workspaces"),
    )


def _build_source_code_node(
    node: NodeDefinition,
    *,
    event_sink: Callable[..., None] | None,
    concurrency_tracker: ConcurrencyTracker | None = None,
    invocation_depth: int = 0,
    base_path: str | Path | None = None,
    enqueue_context: "NodeEnqueueContext | None" = None,
    effect_chain: Any = None,
    state_schema: list[dict[str, Any]] | None = None,
    ancestors: set[str] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    execution_context: "BranchExecutionContext | None" = None,
    delivery_source: Any = None,
    file_source: Any = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Return a node function that runs the node's ``source_code`` in the OS
    sandbox (``tinyassets.node_sandbox``, design D2): a child process that
    receives the node's declared inputs and every earlier effect's full
    response, and nothing else - no credentials, no network, no data dir. The
    source defines ``def run(state, effects=None) -> dict``; the return is
    filtered to ``output_keys`` and merged into state like any node's delta.
    An owner-authored node needs no host approval: the sandbox is the
    authority boundary. No bwrap on the host -> ``SandboxUnavailableError``
    (the run fails loudly as ``sandbox_unavailable``), never an unsandboxed run.
    """
    # `invoke_mcp_action` inside the sandbox is a synchronous RPC to the
    # parent: the child asks over its pipes, the parent answers with the run's
    # authority through this invoker (node-enqueue, wiki_read, ...). The
    # child never holds the invoker or any credential.
    provenance = "own"
    if execution_context is not None:
        provenance = getattr(execution_context, "caller_provenance", "own") or "own"
    if provenance != "own":
        # The run executes as someone who did not author this code (a public
        # foreign branch run directly). The sandbox bounds what the code can
        # touch; it does not decide whose code may run - authorship does.
        raise ForeignCodeError(
            f"Node '{node.node_id}' carries source_code this run did not author "
            f"(caller provenance: {provenance}). Code runs only in the command center "
            f"that authored it: remix the branch into your command center with "
            f"write_graph (fork_from) so the code is yours, then run your copy.",
            node_id=node.node_id,
        )
    _validate_source_code(node)
    # A workspace is resolved from the graph, at compile time: the node must
    # name an ANCESTOR, so no run can reach a checkout it does not depend on
    # and no branch can smuggle a lease id through state or `$ta.ref`.
    workspace_node = (getattr(node, "workspace", "") or "").strip()
    if workspace_node:
        known = set(ancestors or ())
        if workspace_node not in known:
            raise CodeNodeError(
                f"Node '{node.node_id}' declares workspace: "
                f"'{workspace_node}', which is not one of its ancestors "
                f"({sorted(known)}). A workspace is resolved through the "
                "run's effect chain from a checkout this node depends on; it is "
                "never named through state or $ta.ref.",
                node_id=node.node_id,
            )
    src = node.source_code
    timeout_s = float(node.timeout_seconds or 300.0)
    if workspace_node:
        from tinyassets.node_sandbox import MAX_WORKSPACE_TIMEOUT_SECONDS

        # The DECLARED value, not the defaulted one: `or 300.0` above reads a
        # zero as "unset", and a workspace node has to say how long it may hold
        # the host-wide slot rather than inherit a default by writing nothing.
        declared = float(getattr(node, "timeout_seconds", 0.0) or 0.0)
        if not 0 < declared <= MAX_WORKSPACE_TIMEOUT_SECONDS:
            raise CodeNodeError(
                f"Node '{node.node_id}' declares workspace: "
                f"'{workspace_node}' with timeout_seconds={declared}, outside "
                f"the bound 0 < t <= {MAX_WORKSPACE_TIMEOUT_SECONDS:.0f}. A "
                "workspace node holds the command center's job lock and the "
                "host-wide slot for its whole run.",
                node_id=node.node_id,
            )
    input_keys = list(node.input_keys or [])
    output_keys = list(node.output_keys or [])
    defaulted = list(_state_schema_defaults(state_schema or []).keys())
    # A code node reads only what it declares (input_keys + schema defaults),
    # the same rule a packet lives under. `strict_input_isolation=False` is the
    # declared escape hatch - the whole state - exactly as it is for a prompt
    # node's render view.
    strict_inputs = bool(getattr(node, "strict_input_isolation", True))

    def _fn_body(state: dict[str, Any]) -> dict[str, Any]:
        from tinyassets.node_sandbox import NodeSandbox

        if event_sink is not None:
            try:
                event_sink(node_id=node.node_id, phase="starting", source_code=True)
            except Exception as exc:
                if _is_cancel_exception(exc):
                    raise
                logger.exception("event_sink raised in %s (starting)", node.node_id)
        effects = effect_chain.effects_view(ancestors) if effect_chain is not None else {}
        if concurrency_tracker is not None:
            concurrency_tracker.acquire()
        try:
            visible = input_keys + defaulted if strict_inputs else list(dict(state).keys())
            # Freeze only declared incoming file fields per invocation. Neither
            # mutable output state nor the sandbox's RPC kwargs can grant input
            # authority. In particular whole-state/default escape paths do not.
            import copy

            file_inputs = copy.deepcopy({
                field.name: state[field.name] for field in getattr(file_source, "fields", ())
                if field.name in state
            })
            invoke_mcp_action = _build_node_mcp_invoker(
                node, event_sink=event_sink, invocation_depth=invocation_depth,
                base_path=base_path, enqueue_context=enqueue_context,
                delivery_source=delivery_source,
                file_source=file_source, file_inputs=file_inputs,
                should_cancel=should_cancel, execution_context=execution_context,
            )
            # The sandbox answers RPCs from a drain THREAD, which carries no
            # ContextVars: without this, an RPC resolved the daemon's env
            # identity instead of the run's authenticated actor (Codex round 2,
            # P0). Copy this thread's context and run the invoker inside it;
            # count every round-trip against the RUN's cap.
            import contextvars

            request_ctx = contextvars.copy_context()

            # Resolved HERE, per run, not at compile time: the checkout has
            # to have delivered, and a discard may have revoked it since.
            mount = None
            acquired = None
            if workspace_node:
                if effect_chain is None:
                    raise CodeNodeError(
                        "workspace not available: checkout did not deliver "
                        f"/ was discarded (node '{workspace_node}'); this "
                        "run has no effect chain",
                        node_id=node.node_id,
                    )
                # ACQUIRED, not looked up: the lookup hands back the registry's
                # own mount, and a parallel discard closes its descriptors
                # while this node is using them - after which the next
                # checkout gets the same fd NUMBERS and the node is reading
                # another branch's repository (Codex code round 3, P0 #2). An
                # acquisition holds dups for the length of the run and is
                # decided inside the chain's lock, so it cannot straddle a
                # revoke.
                acquired = effect_chain.acquire_workspace(workspace_node)
                raw_mount = None if acquired is None else acquired.mount
                if raw_mount is None:
                    # The checkout never delivered, or a discard revoked it.
                    # Neither is recoverable for a node that declared a
                    # workspace: fail it by name (design D2).
                    raise CodeNodeError(
                        "workspace not available: checkout did not deliver "
                        f"/ was discarded (node '{workspace_node}')",
                        node_id=node.node_id,
                    )
                # The capability stays owned by the chain (the sink holds the
                # descriptor open); this only reads it, for the length of the run.
                mount = _sandbox_workspace_mount(raw_mount, node.node_id)

            def _invoke_current(action: str, kwargs: dict[str, Any]) -> Any:
                from tinyassets.runs import _execution_use_scope

                # This is the actual drain callback, including a trailing line.
                # Its lifetime may exceed the node's bounded drain-thread joins.
                with _execution_use_scope():
                    if effect_chain is not None:
                        effect_chain.rpc_permit()
                    return invoke_mcp_action(action, **dict(kwargs or {}))

            def _invoke(action: str, kwargs: dict[str, Any]) -> Any:
                return request_ctx.run(_invoke_current, action, kwargs)

            # The chain's mount and the sandbox's are DIFFERENT objects with
            # the same name: the chain's carries the lease identity a push
            # resolves through (node_id, lease_fd, generation), the sandbox's
            # carries the jail profile. Handing one to the other raised
            # AttributeError on `limits` for every workspace node - the seam
            # neither lane's own suite could see (found by the end-to-end
            # chain test, 2026-08-30). And a launcher built with no bind
            # reports /workspace as its root while emitting no --bind for it,
            # so the node would have run against a mount point that does not
            # exist.
            sandbox_mount = None
            workspace_launcher = None
            if mount is not None:
                from tinyassets.node_sandbox import WORKSPACE_LAUNCHER_FACTORY

                # Through the translator, never inline: it is the one place
                # that turns a held descriptor into the bind and the pass_fds
                # list, and the one place that checks the descriptor is still a
                # live directory. Building the mount at the call site is how
                # both were dropped (Codex #14b). The factory takes the MOUNT so
                # they cannot be dropped again on the way to the launcher.
                sandbox_mount = _sandbox_workspace_mount(
                    mount,
                    node.node_id,
                    allowed_roots=_workspace_bind_roots(base_path),
                )
                workspace_launcher = WORKSPACE_LAUNCHER_FACTORY(sandbox_mount)
            result = NodeSandbox(
                timeout=timeout_s,
                launcher=workspace_launcher,
                should_cancel=should_cancel,
            ).run_sync(
                node_id=node.node_id,
                source_code=src,
                input_state=dict(state),
                input_keys=visible,
                output_keys=output_keys,
                timeout=timeout_s,
                effects=effects,
                invoke=_invoke,
                workspace=sandbox_mount,
            )
        finally:
            # Before the tracker: the capability is the scarcer resource, and
            # releasing it is what lets a revoke's deferred close finally
            # happen. It runs on every path out of the node, including the
            # sandbox raising.
            if acquired is not None:
                acquired.release()
            if concurrency_tracker is not None:
                concurrency_tracker.release()
        stderr_tail = getattr(result, "stderr_tail", "") or (result.stderr or "")[-2048:]
        stdout_tail = getattr(result, "stdout_tail", "") or (result.stdout or "")[-2048:]
        if not result.success:
            error = result.error or "code node failed"
            if getattr(result, "cancelled", False):
                # Checked FIRST: a cancelled node is not a failed one, and the
                # owner's stop must not be reported as their workflow breaking.
                cancellation = NodeCancelledError(
                    f"Node '{node.node_id}' (code): {error}",
                    node_id=node.node_id,
                )
                _emit_terminal_event(event_sink, node.node_id, cancellation, phase="cancelled")
                raise cancellation
            if getattr(result, "workspace_timeout", False):
                raise WorkspaceCommandTimeout(
                    f"Node '{node.node_id}' (code): {error}",
                    node_id=node.node_id,
                )
            if "timed out" in error.lower():
                raise NodeTimeoutError(
                    f"Node '{node.node_id}' (code) exceeded its timeout of {timeout_s}s",
                    node_id=node.node_id,
                )
            message = f"code node '{node.node_id}' failed: {error}"
            if stderr_tail.strip():
                message += f" | stderr: {stderr_tail.strip()[-600:]}"
            failure = CodeNodeError(message, node_id=node.node_id, stderr_tail=stderr_tail)
            _emit_failed_event(event_sink, node.node_id, failure)
            raise failure
        output = dict(result.output_state or {})
        # The return passes through exactly as the in-process node's did:
        # the single-merge-writer guard wrapping this function sees every key
        # (Codex round 1, P1) and refuses an undeclared MERGE writer; other
        # undeclared keys land in state as before and are named in the event
        # so the author can see what run() wrote beyond its declared outputs.
        undeclared = sorted(k for k in output if k not in output_keys)
        if event_sink is not None:
            try:
                event_sink(
                    node_id=node.node_id,
                    phase="ran",
                    source_code=True,
                    output=output,
                    undeclared_outputs=undeclared,
                    duration_seconds=round(float(result.duration_seconds or 0.0), 3),
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    warning=getattr(result, "warning", "") or "",
                )
            except Exception as exc:
                if _is_cancel_exception(exc):
                    raise
                logger.exception("event_sink raised in %s", node.node_id)
        return output

    def _fn(state: dict[str, Any]) -> dict[str, Any]:
        from tinyassets.runs import _execution_use_scope

        with _execution_use_scope():
            return _fn_body(state)

    return _fn


def _build_opaque_node(
    node: NodeDefinition,
    fn: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    event_sink: Callable[..., None] | None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Wrap a domain-registered opaque callable as a graph node.

    Opaque nodes bypass ``_validate_source_code`` — the domain
    registry is host-controlled at registration time, not
    per-invocation, so the per-node ``approved`` flag is irrelevant.
    Emits ``phase="starting"`` and ``phase="ran"`` events so outer
    stream loops observe entry/exit at wrapper-boundary granularity
    (Phase D §4.10).
    """

    def _fn(state: dict[str, Any]) -> dict[str, Any]:
        if event_sink is not None:
            try:
                event_sink(
                    node_id=node.node_id,
                    phase="starting",
                    opaque=True,
                )
            except Exception as exc:
                if _is_cancel_exception(exc):
                    raise
                logger.exception(
                    "event_sink raised in %s (starting)", node.node_id,
                )
        try:
            result = fn(state)
        except Exception as exc:
            if _is_cancel_exception(exc):
                raise
            logger.exception("opaque node %s raised", node.node_id)
            raise CompilerError(
                f"Opaque node '{node.node_id}' raised at runtime: {exc}"
            ) from exc
        if not isinstance(result, dict):
            raise CompilerError(
                f"Opaque node '{node.node_id}' must return a dict, "
                f"got {type(result).__name__}."
            )
        if event_sink is not None:
            try:
                event_sink(
                    node_id=node.node_id,
                    phase="ran",
                    opaque=True,
                    output=result,
                )
            except Exception as exc:
                if _is_cancel_exception(exc):
                    raise
                logger.exception("event_sink raised in %s", node.node_id)
        return result

    return _fn


def _checkpoint_predicate_matches(
    reached_when: dict[str, Any], merged_state: dict[str, Any],
) -> bool:
    """Return True when merged_state satisfies reached_when.

    Supported shapes:
    - {"state_key": K, "value": V} — fires when merged_state[K] == V
    - {"state_key": K, "exists": true} — fires when K is present and non-None/non-empty
    - {"state_key": K} — same as exists=true (key presence)
    """
    key = reached_when.get("state_key", "")
    if not key:
        return False
    val = merged_state.get(key)
    if "value" in reached_when:
        return val == reached_when["value"]
    # exists check (default): truthy non-None value
    if val is None:
        return False
    if isinstance(val, (str, list, dict)):
        return bool(val)
    return True


def _wrap_with_checkpoints(
    inner_fn: Callable[[dict[str, Any]], dict[str, Any]],
    node: NodeDefinition,
    event_sink: Callable[..., None] | None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Wrap a node function to evaluate checkpoints after execution.

    After inner_fn returns a state delta, this evaluates each checkpoint's
    reached_when predicate against the merged state (incoming + delta).
    Matching checkpoints emit a checkpoint_reached event via event_sink
    and record the checkpoint_id in _fired_checkpoints to prevent re-firing.
    """
    checkpoints = list(node.checkpoints)
    if not checkpoints:
        return inner_fn

    node_id = node.node_id

    def _fn(state: dict[str, Any]) -> dict[str, Any]:
        delta = inner_fn(state)
        # Merge incoming state with delta to evaluate predicates.
        merged = {**state, **delta}
        # _fired_checkpoints uses an append reducer — LangGraph concatenates
        # the delta list onto the accumulated state list. We emit only
        # newly-fired IDs so each ID appears at most once in the final list.
        already_fired: set[str] = set(state.get("_fired_checkpoints") or [])
        newly_fired: list[str] = []

        for ckpt in checkpoints:
            ckpt_id = ckpt.get("checkpoint_id", "")
            if not ckpt_id or ckpt_id in already_fired:
                continue
            rw = ckpt.get("reached_when")
            if not isinstance(rw, dict):
                continue
            if _checkpoint_predicate_matches(rw, merged):
                newly_fired.append(ckpt_id)
                if event_sink is not None:
                    try:
                        event_sink(
                            node_id=node_id,
                            phase="checkpoint_reached",
                            checkpoint_id=ckpt_id,
                            earns_fraction=ckpt.get("earns_fraction", 0.0),
                        )
                    except Exception as exc:  # noqa: BLE001
                        if _is_cancel_exception(exc):
                            raise
                        logger.exception(
                            "event_sink raised emitting checkpoint_reached "
                            "for %s/%s", node_id, ckpt_id,
                        )

        if newly_fired:
            # Emit only the new IDs; the append reducer accumulates them.
            delta = {**delta, "_fired_checkpoints": newly_fired}
        return delta

    return _fn


# Child-run retries are the AUTHOR'S policy: `retry_budget` on the invoke spec,
# and nothing overrides it. `TINYASSETS_MAX_CHILD_RETRIES_TOTAL` (default 5) used
# to cap the total across a parent run, so a branch whose author asked for three
# retries on each of four children silently got five in total and then behaved as
# `propagate` -- the author's declared policy quietly replaced by a host env var
# (founder, 2026-09-30: honour the author's retry policy).
#
# What bounds a retry storm instead: every child run charges the universe's
# admission and holds a seat like any other run, and the parent waits on each
# child in turn, so retries are serial work inside one seat rather than fan-out.
#
# `_retry_state` is kept for the per-run counter that the receipt reports; it
# counts, and refuses nothing.
_retry_state = threading.local()


def _retry_budget_consume() -> None:
    """Increment the threadlocal retry counter by 1. Counts; never refuses."""
    _retry_state.used = getattr(_retry_state, "used", 0) + 1


def _retry_budget_reset() -> None:
    """Reset the threadlocal counter — called by ``_invoke_graph`` at run
    start so each parent run's count starts at zero."""
    _retry_state.used = 0


def _classify_child_failure(child_status: str) -> str:
    """Map a child run's terminal status to a failure_class label.

    Phase A item 5 / Task #76b. Used by invoke_branch / invoke_branch_version
    builders when populating ``ChildFailure.failure_class`` on the parent's
    ``RunOutcome.child_failures`` list.
    """
    from tinyassets.runs import (
        RUN_STATUS_CANCELLED,
        RUN_STATUS_FAILED,
        RUN_STATUS_INTERRUPTED,
    )
    if child_status == RUN_STATUS_FAILED:
        return "child_failed"
    if child_status == RUN_STATUS_CANCELLED:
        return "child_cancelled"
    if child_status == RUN_STATUS_INTERRUPTED:
        return "child_timeout"
    return "child_unknown"


def _dispatch_invoke_outcome(
    *,
    child_status: str,
    child_run_id: str,
    child_output: dict[str, Any],
    output_mapping: dict[str, str],
    on_child_fail: str,
    default_outputs: dict[str, Any] | None,
    node_id: str,
) -> tuple[dict[str, Any], "object | None"]:
    """Apply the ``on_child_fail`` policy to a completed child run.

    Returns ``(updates, child_failure_or_none)``. ``child_failure_or_none`` is
    a ``ChildFailure`` instance when the child terminated non-completed
    (worth recording on the parent's ``RunOutcome.child_failures``); None on
    successful completion.

    Policies:
      - ``"propagate"`` (default): child failure raises ``ChildFailedError``
        so the parent run terminates with the structured error.
      - ``"default"``: parent continues; ``output_mapping`` populates from
        ``default_outputs`` dict (or None when not declared).
      - ``"retry"``: caller handles retry via the closure's retry counter;
        this helper treats retry-exhausted as ``"propagate"``.
    """
    from tinyassets.runs import RUN_STATUS_COMPLETED, ChildFailure

    if child_status == RUN_STATUS_COMPLETED:
        updates: dict[str, Any] = {}
        for parent_key, child_key in output_mapping.items():
            updates[parent_key] = child_output.get(child_key)
        return updates, None

    failure = ChildFailure(
        run_id=child_run_id,
        failure_class=_classify_child_failure(child_status),
        child_status=child_status,
        partial_output=dict(child_output) if child_output else None,
    )

    if on_child_fail == "default":
        defaults = default_outputs or {}
        updates = {
            parent_key: defaults.get(parent_key)
            for parent_key in output_mapping
        }
        return updates, failure

    # propagate (default) — raise so the parent's _invoke_graph catches.
    raise ChildFailedError(
        f"Sub-branch invocation in node '{node_id}' produced a "
        f"non-completed terminal status: {failure.failure_class} "
        f"(child run_id={child_run_id})",
        failure=failure,
    )


class ChildFailedError(Exception):
    """Raised by ``_dispatch_invoke_outcome`` under ``on_child_fail="propagate"``.

    Phase A item 5 / Task #76b. Carries the ``ChildFailure`` so the parent
    ``_invoke_graph`` can surface it via ``RunOutcome.child_failures`` rather
    than discarding the structured failure data.
    """

    def __init__(self, message: str, *, failure: object) -> None:
        super().__init__(message)
        self.failure = failure


def _emit_invoke_design_used(
    *,
    base_path: "Path",
    parent_run_id: str,
    parent_node_id: str,
    artifact_kind: str,
    artifact_id: str,
    branch_def_id_for_author_lookup: str,
    metadata_extra: dict[str, Any] | None = None,
) -> None:
    """Phase A item 5 / Task #76c — emit a ``design_used`` event for a
    successful sub-branch invocation.

    Fires only on child success per #56 + #75 discipline ("only successful
    uses count"). Resolves the credited author by reading the live
    ``BranchDefinition.author`` keyed by ``branch_def_id_for_author_lookup``;
    for ``invoke_branch_version_spec`` we still resolve via the live def
    because ``branch_versions.snapshot`` only carries topology, not author.

    Skips emit when the author is unowned — orphan-row
    prevention. Per #48 §1.4 + impl-pair-read on #75: ``execute_step``
    events may carry an empty actor for retired rows; ``design_used`` events
    MUST NEVER, because crediting an unowned row pollutes the ledger with
    attribution. Different events, different discipline.

    Wrapped in try/except by callers so emit failure never breaks the
    parent step (mirrors Task #72/#75 decoupling).
    """
    from tinyassets.daemon_server import get_branch_definition

    try:
        raw = get_branch_definition(
            base_path, branch_def_id=branch_def_id_for_author_lookup,
        )
    except KeyError:
        return  # Live def gone; skip emit (lineage walk handles attribution).

    from tinyassets.principals import named_principal

    author = named_principal(raw.get("author") if isinstance(raw, dict) else "")
    if not author:
        return

    metadata = {
        "graph_node_id": parent_node_id,
        artifact_kind + "_id": artifact_id,
    }
    if metadata_extra:
        metadata.update(metadata_extra)

    from tinyassets.contribution_events import record_contribution_event

    record_contribution_event(
        base_path,
        event_id=f"design_used:{parent_run_id}:{parent_node_id}:{artifact_id}",
        event_type="design_used",
        actor_id=author,
        source_run_id=parent_run_id,
        source_artifact_id=artifact_id,
        source_artifact_kind=artifact_kind,
        weight=1.0,
        metadata_json=json.dumps(metadata),
    )


@dataclass(frozen=True)
class BranchExecutionContext:
    """Immutable per-run authority carried through every invoke_branch edge.

    Built ONCE at authenticated run entry (never filled from a node spec) and
    threaded compile -> node builder -> invoke
    closure -> child run -> child's builders. It is the single source of truth for who
    the run executes as (``actor``), where (``universe_id``), and how much the running
    definition is trusted (``caller_provenance``: ``own`` = authored by ``actor``, else
    ``public-foreign``). A nested edge can only NARROW it; it never widens authority.
    Current run/ACL reads may narrow freshness, never manufacture missing context.
    """

    actor: str = ""
    universe_id: str = ""
    caller_provenance: str = "own"  # "own" | "public-foreign"
    depth: int = 0
    owner_user_id: str = ""  # persisted authenticated owner, not the universe actor
    definition_author: str = ""  # actual compiled definition, not fork attribution
    workspace_family: FamilyMember | None = None  # persisted snapshot, not a grant


def _workspace_invocation_parent(ctx: BranchExecutionContext):
    from tinyassets.workspace_family import UNMANAGED_PARENT

    # Missing legacy authority must not turn each child into a new budget root.
    return ctx.workspace_family if ctx.workspace_family is not None else UNMANAGED_PARENT


#: Uniform refusal for any child ref that is absent OR not authorized — never reveal
#: which, so the invoke surface is not an existence/authorization oracle.
_CHILD_UNAVAILABLE = "invoke_branch child is not available"


def _authorize_child_ref(
    base: "Path", child_def_id: str, ctx: "BranchExecutionContext", *, parent_run_id: str = "",
    require_authorship: bool = False,
) -> "Any":
    """Authorize an AUTHOR-chosen child branch ref under DELEGATED authority.

    The child id comes from the branch spec (author-controlled), so it is authorized
    against what the AUTHORING definition may reference — NOT the runner's ambient
    readability (the run actor is the potential victim; their own readability would
    authorize a foreign spec's reference to the victim's private branch). Rule:
      * ``own`` provenance: may reference an actor-authored child or any public
        child. An owner-authored parent may also reference that owner's private
        child while current owner and persisted running-parent authority agree.
      * ``public-foreign`` provenance: may reference ONLY a public child.
    Returns the child ``BranchDefinition`` on success; raises ``CompilerError`` with a
    uniform message otherwise (absent and unauthorized are indistinguishable).
    """
    from tinyassets.daemon_server import get_branch_definition

    # 1) Fetch the raw descriptor; ANY failure (absent, unreadable, corrupt) is the
    # SAME uniform refusal — no existence/ownership/error oracle (Codex exact-diff #5).
    try:
        raw = get_branch_definition(base, branch_def_id=child_def_id)
    except Exception:  # noqa: BLE001 - absent/unreadable both -> uniform not-found
        logger.debug("invoke_branch child fetch failed", exc_info=True)
        raise CompilerError(_CHILD_UNAVAILABLE) from None
    if not isinstance(raw, dict):
        raise CompilerError(_CHILD_UNAVAILABLE)

    # 2) Authorize on MINIMAL metadata BEFORE deserializing the full definition
    # (Codex exact-diff #1): a malformed private body must not raise a distinguishable
    # error. Missing/blank/malformed visibility is NOT public -> fail closed (#6).
    visibility = str(raw.get("visibility") or "").strip().lower()
    author = str(raw.get("author") or "").strip()
    is_public = visibility == "public" and not require_authorship
    if ctx.caller_provenance == "own":
        authorized = is_public or (bool(author) and author == ctx.actor)
        if (not authorized and author and author == ctx.owner_user_id
                and ctx.definition_author == ctx.owner_user_id and parent_run_id):
            from tinyassets import runs
            from tinyassets.api.source_channel import universe_owner_actor

            if universe_owner_actor(base, ctx.universe_id, ctx.owner_user_id):
                try:
                    with runs._connect(base) as conn:
                        row = conn.execute(
                            "SELECT actor, owner_user_id, queue_universe_id, status "
                            "FROM runs WHERE run_id=?", (parent_run_id,),
                        ).fetchone()
                    authorized = bool(
                        row is not None and row["status"] == "running"
                        and row["actor"] == ctx.actor
                        and row["owner_user_id"] == ctx.owner_user_id
                        and row["queue_universe_id"] == ctx.universe_id
                    )
                except Exception:  # noqa: BLE001 - uniform authority refusal
                    logger.debug("invoke_branch parent authority unavailable", exc_info=True)
                    authorized = False
    else:
        authorized = is_public
    if not authorized:
        raise CompilerError(_CHILD_UNAVAILABLE)

    # 3) Only now deserialize; a malformed AUTHORIZED def is uniform too.
    from tinyassets.branches import BranchDefinition as _BD

    try:
        return _BD.from_dict(raw)
    except Exception:  # noqa: BLE001
        logger.debug("invoke_branch child deserialize failed", exc_info=True)
        raise CompilerError(_CHILD_UNAVAILABLE) from None


def _enforce_foreign_mapping_confidentiality(
    ctx: "BranchExecutionContext",
    *,
    inputs_mapping: dict[str, str],
    output_mapping: dict[str, str],
    node_id: str,
) -> None:
    """Restrict data plumbed across a FOREIGN-provenance invoke edge (task 4.1).

    When a run invokes a definition it does NOT own (``caller_provenance`` !=
    ``own``), a credential/secret/auth-state field must not be plumbed into the
    child's inputs or harvested out of its output back into parent state. Both
    sides of every mapping pair are classified with the SAME redaction key-classes
    the agent-definition surface uses (``_is_sensitive_field_name``); any sensitive
    key on a foreign edge fails closed at compile time. Own-provenance edges (the
    runner's own shapes plumbing their own fields) are unrestricted.
    """
    if (ctx.caller_provenance or "").strip() == "own":
        return
    from tinyassets.custom_agents import _is_sensitive_field_name

    for label, mapping in (("inputs_mapping", inputs_mapping), ("output_mapping", output_mapping)):
        for parent_key, child_key in (mapping or {}).items():
            for side in (parent_key, child_key):
                if _is_sensitive_field_name(side):
                    raise CompilerError(
                        f"Node '{node_id}': foreign invoke_branch {label} may not map "
                        f"a credential/secret/auth-state field ('{side}')."
                    )


def _build_invoke_branch_node(
    node: NodeDefinition,
    *,
    base_path: str | Path,
    event_sink: Callable[..., None] | None,
    provider_call: Callable[..., str] | None = None,
    depth: int = 0,
    parent_run_id: str = "",
    execution_context: "BranchExecutionContext | None" = None,
    on_node_status: Callable[[str, str], None] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build a callable for an ``invoke_branch_spec`` node.

    The callable spawns a child branch run (blocking or async) and writes
    declared output_mapping fields back into the parent state.
    """
    from tinyassets.runs import execute_branch, execute_branch_async

    spec = node.invoke_branch_spec or {}
    child_branch_def_id: str = spec.get("branch_def_id", "")
    inputs_mapping: dict[str, str] = spec.get("inputs_mapping", {})
    output_mapping: dict[str, str] = spec.get("output_mapping", {})
    wait_mode: str = spec.get("wait_mode", "blocking")
    on_child_fail: str = spec.get("on_child_fail", "propagate")
    default_outputs = spec.get("default_outputs")
    retry_budget: int = int(spec.get("retry_budget", 1) or 1)
    # ``child_actor`` is DELIBERATELY ignored (removed 2026-08-23): an author-supplied
    # actor is identity spoofing. The child always runs as the immutable ctx.actor.

    if not child_branch_def_id:
        raise CompilerError(
            f"Node '{node.node_id}': invoke_branch_spec missing 'branch_def_id'."
        )
    if wait_mode not in ("blocking", "async"):
        raise CompilerError(
            f"Node '{node.node_id}': invoke_branch_spec wait_mode must be "
            f"'blocking' or 'async', got '{wait_mode}'."
        )
    # No depth cap on a BLOCKING invoke (plan item 6): its child runs in this
    # thread, holds no shared pool slot, and is charged to the universe's
    # usage meter below. An ASYNC child runs on the child pool every universe
    # shares, and a parent that awaits it holds a pool thread while it waits,
    # so async nesting is bounded by that pool's size (Codex refute
    # 2026-09-28, P1: seven async+await levels starved every user's children).
    from tinyassets.runs import _max_child_workers

    pool_threads = _max_child_workers()
    if wait_mode == "async" and depth + 1 > pool_threads:
        raise CompilerError(
            f"Node '{node.node_id}': an async invoke_branch runs on the shared "
            f"sub-branch pool ({pool_threads} threads), so it cannot nest "
            f"{depth + 1} deep; use wait_mode blocking, which runs in this "
            f"thread, or enqueue_branch_run."
        )

    _base = Path(base_path)
    _ctx = execution_context or BranchExecutionContext()
    _enforce_foreign_mapping_confidentiality(
        _ctx,
        inputs_mapping=inputs_mapping,
        output_mapping=output_mapping,
        node_id=node.node_id,
    )

    def _resolve_actor() -> str:
        # The child runs as the parent run's authenticated actor — never a spec actor,
        # never re-read from the mutable run record, never a synthetic fallback.
        from tinyassets.principals import named_principal

        actor = named_principal(_ctx.actor)
        if not actor:
            raise CompilerError(
                f"Node '{node.node_id}': invoke_branch has no authenticated "
                f"execution context; refusing (fail-closed)."
            )
        return actor

    def _node_fn(state: dict[str, Any]) -> dict[str, Any]:
        # Validate the actor FIRST (Codex exact-diff #4): an empty-actor run must fail
        # closed before any child lookup, so it cannot distinguish public vs
        # absent/private children by differing errors.
        actor_arg = _resolve_actor()
        # Then delegated authorization of the author-chosen child BEFORE any execute.
        child_branch = _authorize_child_ref(
            _base, child_branch_def_id, _ctx, parent_run_id=parent_run_id,
        )

        child_inputs: dict[str, Any] = {
            child_key: state.get(parent_key)
            for parent_key, child_key in inputs_mapping.items()
        }
        if wait_mode == "blocking":
            # Phase A item 5 / Task #76b — on_child_fail policy + retry.
            # Blocking-mode invocation knows the child's terminal status
            # synchronously. async-mode failures surface at the await
            # node (#56 §8 Q6) and aren't policy-handled here.
            attempt = 0
            while True:
                attempt += 1
                if _stack_nearly_exhausted():
                    raise CompilerError(f"Node '{node.node_id}': {_STACK_EXHAUSTED}.")
                ticket = _charge_child_run(node, _ctx)
                try:
                    from tinyassets.provider_admission import blocking_provider_child

                    # Blocking invoke transfers ownership just like a parent seat;
                    # the async branch below must acquire its own provider slot.
                    with blocking_provider_child():
                        outcome = execute_branch(
                            _base, branch=child_branch, inputs=child_inputs,
                            actor=actor_arg,
                            owner_user_id=_ctx.owner_user_id or None,
                            _workspace_parent=_workspace_invocation_parent(_ctx),
                            _enqueue_universe_id=_ctx.universe_id,
                            provider_call=provider_call,
                            on_node_status=on_node_status,
                            _invocation_depth=depth + 1,
                        )
                except RecursionError:
                    raise CompilerError(
                        f"Node '{node.node_id}': {_STACK_EXHAUSTED}."
                    ) from None
                _bind_child_ticket(ticket, str(outcome.run_id or ""))
                if outcome.status == "completed":
                    try:
                        _emit_invoke_design_used(
                            base_path=_base,
                            parent_run_id=parent_run_id,
                            parent_node_id=node.node_id,
                            artifact_kind="branch_def",
                            artifact_id=child_branch_def_id,
                            branch_def_id_for_author_lookup=child_branch_def_id,
                        )
                    except Exception:
                        pass
                    return {
                        parent_key: outcome.output.get(child_key)
                        for parent_key, child_key in output_mapping.items()
                    }
                # Non-completed terminal status — apply policy.
                retries_left = (
                    retry_budget - (attempt - 1)
                    if on_child_fail == "retry" else 0
                )
                # The author's `retry_budget` is the whole decision.
                if on_child_fail == "retry" and retries_left > 0:
                    _retry_budget_consume()
                    continue
                updates, _failure = _dispatch_invoke_outcome(
                    child_status=outcome.status,
                    child_run_id=outcome.run_id,
                    child_output=outcome.output,
                    output_mapping=output_mapping,
                    on_child_fail=(
                        "propagate" if on_child_fail == "retry"
                        else on_child_fail
                    ),
                    default_outputs=default_outputs,
                    node_id=node.node_id,
                )
                # _dispatch_invoke_outcome raised on propagate (default
                # for retry-exhausted); only "default" path returns here.
                return updates
        else:
            ticket = _charge_child_run(node, _ctx)
            outcome = execute_branch_async(
                _base, branch=child_branch, inputs=child_inputs,
                actor=actor_arg,
                owner_user_id=_ctx.owner_user_id or None,
                _workspace_parent=_workspace_invocation_parent(_ctx),
                _enqueue_universe_id=_ctx.universe_id,
                provider_call=provider_call,
                on_node_status=on_node_status,
                _invocation_depth=depth + 1,
            )
            _bind_child_ticket(ticket, str(outcome.run_id or ""))
            # async: write the child run_id into the first output_mapping target.
            # design_used emit deferred to await_branch_run on success
            # (#56 §8 Q6 — async failures surface at the await site).
            updates = {}
            if output_mapping:
                first_parent_key = next(iter(output_mapping))
                updates[first_parent_key] = outcome.run_id
            return updates

    return _node_fn


def _charge_child_run(node: NodeDefinition, ctx: "BranchExecutionContext") -> Any:
    """Record a sub-branch run for effect settlement; return the ticket.

    Never a refusal: a child run's agent calls wait for the account's seats
    (``tinyassets.universe_seats``), which is what bounds a chain that invokes
    itself. A run with no universe (the local single-tenant daemon) records
    nothing.
    """
    from tinyassets import engine_admissions as ea

    universe_id = (getattr(ctx, "universe_id", "") or "").strip()
    if not universe_id:
        return None
    return ea.admit(universe_id)


def _bind_child_ticket(ticket: Any, run_id: str) -> None:
    """Bind a child's admission to its run, so a read-only child settles off
    the write budget like any other run."""
    if ticket is None or not run_id:
        return
    from tinyassets.engine_admissions import attach_run

    try:
        attach_run(ticket, run_id)
    except Exception:  # noqa: BLE001 - the admission is already counted
        logger.warning("child admission bind failed run=%s", run_id, exc_info=True)


#: Why a blocking invoke of a live definition can still fail on depth: it runs
#: in its parent's thread, so an unbounded self-invoking chain eventually
#: exhausts the interpreter's stack. That is a physical limit, reported by name.
_STACK_EXHAUSTED = (
    "the sub-branch chain is so deeply nested that it exhausted the "
    "interpreter's stack; use wait_mode async or enqueue_branch_run to go deeper"
)

#: Refuse the next blocking child while this share of the interpreter's
#: recursion limit is still free. A RecursionError raised deeper down is caught
#: by whichever layer it lands in and reported as that layer's failure, so the
#: chain must stop BEFORE the stack runs out to say why it stopped.
_STACK_HEADROOM = 0.25


def _stack_nearly_exhausted() -> bool:
    import sys

    depth = 0
    frame = sys._getframe()
    while frame is not None:
        depth += 1
        frame = frame.f_back
    return depth > sys.getrecursionlimit() * (1 - _STACK_HEADROOM)


def _build_invoke_branch_version_node(
    node: NodeDefinition,
    *,
    base_path: str | Path,
    event_sink: Callable[..., None] | None,
    provider_call: Callable[..., str] | None = None,
    depth: int = 0,
    parent_run_id: str = "",
    execution_context: "BranchExecutionContext | None" = None,
    on_node_status: Callable[[str, str], None] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build a callable for an ``invoke_branch_version_spec`` node.

    Phase A item 5 (Task #76a). Sibling to :func:`_build_invoke_branch_node`
    that resolves a frozen ``branch_version_id`` snapshot via
    :func:`tinyassets.runs.execute_branch_version_async` instead of calling
    :func:`execute_branch` against the live def. Same input/output mapping
    contract; same recursion-cap discipline.

    Failure-policy + retry-budget logic lands in Task #76b — this builder
    uses the default ``on_child_fail="propagate"`` semantics today: the
    parent step receives the child's outcome.output as-is, and child
    failures surface as ``None`` values in the mapping (matches existing
    invoke_branch behavior; structured propagation lands in 76b).
    """
    from tinyassets.runs import _max_child_workers

    spec = node.invoke_branch_version_spec or {}
    child_branch_version_id: str = spec.get("branch_version_id", "")
    inputs_mapping: dict[str, str] = spec.get("inputs_mapping", {})
    output_mapping: dict[str, str] = spec.get("output_mapping", {})
    wait_mode: str = spec.get("wait_mode", "blocking")
    on_child_fail: str = spec.get("on_child_fail", "propagate")
    default_outputs = spec.get("default_outputs")
    retry_budget: int = int(spec.get("retry_budget", 1) or 1)
    # ``child_actor`` is DELIBERATELY ignored (removed 2026-08-23) — spoofing vector.

    if not child_branch_version_id:
        raise CompilerError(
            f"Node '{node.node_id}': invoke_branch_version_spec missing "
            f"'branch_version_id'."
        )
    if wait_mode not in ("blocking", "async"):
        raise CompilerError(
            f"Node '{node.node_id}': invoke_branch_version_spec wait_mode "
            f"must be 'blocking' or 'async', got '{wait_mode}'."
        )
    # Not a shape cap: a version invoke runs its child on the child pool every
    # universe shares, and a blocking one (or an await of an async one) waits
    # on it while holding a thread of that pool itself. Nested deeper than the
    # pool has threads, the chain starves every user's sub-branch runs until
    # its timeouts cascade. That pool size -- not a policy number -- is the
    # bound (plan item 6). Only a blocking invoke by definition, which runs in
    # its parent's thread, has none.
    pool_threads = _max_child_workers()
    if depth + 1 > pool_threads:
        raise CompilerError(
            f"Node '{node.node_id}': an invoke_branch_version runs on the shared "
            f"sub-branch pool ({pool_threads} threads), so it cannot nest "
            f"{depth + 1} deep; invoke the branch by definition with wait_mode "
            f"blocking, which runs in this thread."
        )

    _base = Path(base_path)
    _ctx = execution_context or BranchExecutionContext()
    _enforce_foreign_mapping_confidentiality(
        _ctx,
        inputs_mapping=inputs_mapping,
        output_mapping=output_mapping,
        node_id=node.node_id,
    )

    def _resolve_actor() -> str:
        from tinyassets.principals import named_principal

        actor = named_principal(_ctx.actor)
        if not actor:
            raise CompilerError(
                f"Node '{node.node_id}': invoke_branch_version has no authenticated "
                f"execution context; refusing (fail-closed)."
            )
        return actor

    def _authorize_version() -> None:
        # Authorize BEFORE the snapshot loads (Codex exact-diff #2): resolve the
        # version's definition via a METADATA-ONLY lookup (no snapshot load), then
        # authorize under the delegated rule. Require NONEMPTY def-id equality so a
        # version with no/def-mismatched id fails closed (#3). Any lookup failure is
        # the same uniform refusal (#5).
        from tinyassets.branch_versions import branch_version_def_id

        try:
            ver_def_id = branch_version_def_id(_base, child_branch_version_id)
        except Exception:  # noqa: BLE001
            logger.debug("invoke_branch_version metadata lookup failed", exc_info=True)
            raise CompilerError(_CHILD_UNAVAILABLE) from None
        if not ver_def_id:
            raise CompilerError(_CHILD_UNAVAILABLE)
        child = _authorize_child_ref(_base, ver_def_id, _ctx, parent_run_id=parent_run_id)
        child_def_id = (getattr(child, "branch_def_id", "") or "").strip()
        if not child_def_id or child_def_id != ver_def_id:
            raise CompilerError(_CHILD_UNAVAILABLE)
        from tinyassets.branch_versions import branch_version_is_public

        if not branch_version_is_public(_base, child_branch_version_id):
            # An unmarked snapshot is private even when its live branch is public.
            # Reuse the delegated-author gate, including owner-bound run checks.
            _authorize_child_ref(
                _base, ver_def_id, _ctx, parent_run_id=parent_run_id,
                require_authorship=True,
            )

    def _node_fn(state: dict[str, Any]) -> dict[str, Any]:
        # Lazy module-attribute lookups so unittest.mock.patch on
        # tinyassets.runs.* takes effect (matches the patch-where-the-
        # function-is-looked-up gotcha from Task #46 Failure #1).
        from tinyassets.runs import (
            execute_branch_version_async,
            poll_child_run_status,
        )

        # Actor FIRST (Codex exact-diff #4), then authorize before any snapshot load.
        actor_arg = _resolve_actor()
        _authorize_version()
        child_inputs: dict[str, Any] = {
            child_key: state.get(parent_key)
            for parent_key, child_key in inputs_mapping.items()
        }

        def _resolve_branch_def_id_for_author() -> str:
            """Map child_branch_version_id → branch_def_id for author lookup.

            ``branch_versions.snapshot`` is topology-only; author lives on the
            live BranchDefinition. ``get_branch_version`` returns None on
            missing — return "" so emit silently skips (orphan-row prevention).
            """
            from tinyassets.branch_versions import get_branch_version
            ver = get_branch_version(_base, child_branch_version_id)
            return ver.branch_def_id if ver else ""

        if wait_mode == "blocking":
            # Phase A item 5 / Task #76b — on_child_fail policy + retry,
            # mirroring _build_invoke_branch_node's blocking path.
            attempt = 0
            while True:
                attempt += 1
                # Async helper handles the snapshot-load + reconstruction +
                # SnapshotSchemaDrift + KeyError contract per Task #65b.
                ticket = _charge_child_run(node, _ctx)
                from tinyassets.provider_admission import (
                    blocking_parent_slot,
                    blocking_provider_child,
                )

                with blocking_provider_child():
                    outcome = execute_branch_version_async(
                        _base,
                        branch_version_id=child_branch_version_id,
                        inputs=child_inputs,
                        actor=actor_arg,
                        owner_user_id=_ctx.owner_user_id or None,
                        _workspace_parent=_workspace_invocation_parent(_ctx),
                        _enqueue_universe_id=_ctx.universe_id,
                        provider_call=provider_call,
                        on_node_status=on_node_status,
                        _invocation_depth=depth + 1,
                        _provider_parent=blocking_parent_slot(),
                        # This node blocks until the child ends, so the child
                        # may borrow the seat it is carrying, if any.
                        _lend_seat=True,
                    )
                    _bind_child_ticket(ticket, str(outcome.run_id or ""))
                    # Block until the child terminates; harvest its output dict.
                    # No deadline, as the blocking definition invoke has none: the
                    # child may first wait for its account's seat, and a wait is
                    # never a failure (gpt-6-astra round 3). A dead child still
                    # ends -- its run is terminalized on its owner's proven death.
                    record = poll_child_run_status(
                        _base, outcome.run_id, timeout_seconds=None,
                    )
                child_status = record.get("status", "")
                child_output = record.get("output") or {}

                if child_status == "completed":
                    try:
                        bdid = _resolve_branch_def_id_for_author()
                        if bdid:
                            _emit_invoke_design_used(
                                base_path=_base,
                                parent_run_id=parent_run_id,
                                parent_node_id=node.node_id,
                                artifact_kind="branch_version",
                                artifact_id=child_branch_version_id,
                                branch_def_id_for_author_lookup=bdid,
                            )
                    except Exception:
                        pass
                    return {
                        parent_key: child_output.get(child_key)
                        for parent_key, child_key in output_mapping.items()
                    }
                # Non-completed terminal status — apply policy.
                retries_left = (
                    retry_budget - (attempt - 1)
                    if on_child_fail == "retry" else 0
                )
                # The author's `retry_budget` is the whole decision.
                if on_child_fail == "retry" and retries_left > 0:
                    _retry_budget_consume()
                    continue
                updates, _failure = _dispatch_invoke_outcome(
                    child_status=child_status,
                    child_run_id=outcome.run_id,
                    child_output=child_output,
                    output_mapping=output_mapping,
                    on_child_fail=(
                        "propagate" if on_child_fail == "retry"
                        else on_child_fail
                    ),
                    default_outputs=default_outputs,
                    node_id=node.node_id,
                )
                return updates
        else:
            # Async: spawn and write child run_id; failure handling deferred
            # to the await_branch_run node per #56 §8 Q6.
            ticket = _charge_child_run(node, _ctx)
            outcome = execute_branch_version_async(
                _base,
                branch_version_id=child_branch_version_id,
                inputs=child_inputs,
                actor=actor_arg,
                owner_user_id=_ctx.owner_user_id or None,
                _workspace_parent=_workspace_invocation_parent(_ctx),
                _enqueue_universe_id=_ctx.universe_id,
                provider_call=provider_call,
                on_node_status=on_node_status,
                _invocation_depth=depth + 1,
            )
            _bind_child_ticket(ticket, str(outcome.run_id or ""))
            # design_used emit deferred to await on success (mirrors
            # invoke_branch async path).
            updates = {}
            if output_mapping:
                first_parent_key = next(iter(output_mapping))
                updates[first_parent_key] = outcome.run_id
            return updates

    return _node_fn


def _build_await_branch_run_node(
    node: NodeDefinition,
    *,
    base_path: str | Path,
    event_sink: Callable[..., None] | None,
    execution_context: "BranchExecutionContext | None" = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build a callable for an ``await_run_spec`` node.

    The callable reads a run_id from parent state, polls until the child run
    reaches a terminal status, then writes declared output_mapping fields. The
    awaited run is BOUND to the execution context's actor + universe (task 4.2):
    a run_id in parent state that resolves to a different actor/universe is refused
    as not-found, so a foreign run id cannot be awaited via a planted state field.
    """
    from tinyassets.runs import poll_child_run_status

    spec = node.await_run_spec or {}
    run_id_field: str = spec.get("run_id_field", "")
    output_mapping: dict[str, str] = spec.get("output_mapping", {})
    timeout_seconds: float = float(spec.get("timeout_seconds", 300.0))

    if not run_id_field:
        raise CompilerError(
            f"Node '{node.node_id}': await_run_spec missing 'run_id_field'."
        )

    _base = Path(base_path)
    _ctx = execution_context or BranchExecutionContext()

    def _node_fn(state: dict[str, Any]) -> dict[str, Any]:
        import json as _json
        run_id: str = state.get(run_id_field, "") or ""
        if not run_id:
            raise RuntimeError(
                f"await_branch_run node '{node.node_id}': "
                f"state field '{run_id_field}' is empty or missing."
            )
        record = poll_child_run_status(
            _base, run_id, timeout_seconds=timeout_seconds,
            expected_actor=(_ctx.actor or "").strip() or None,
            expected_universe_id=(_ctx.universe_id or "").strip() or None,
        )
        raw_output = record.get("output") or {}
        if isinstance(raw_output, str):
            try:
                raw_output = _json.loads(raw_output)
            except Exception:
                raw_output = {}

        updates: dict[str, Any] = {}
        for parent_key, child_key in output_mapping.items():
            updates[parent_key] = raw_output.get(child_key)
        return updates

    return _node_fn


def _delta_view(
    state: dict[str, Any],
    delta: dict[str, Any],
    append_fields: set[str],
    merge_fields: set[str],
) -> dict[str, Any]:
    """The state a node's effects render against: what the node saw, merged
    with the delta it returned exactly as LangGraph's reducers will merge it
    (append concatenates, merge shallow-merges right-biased, else overwrite)."""
    view = dict(state)
    for key, value in delta.items():
        if key in append_fields and isinstance(value, list):
            view[key] = list(state.get(key) or []) + value
        elif key in merge_fields and isinstance(value, dict):
            view[key] = {**(state.get(key) or {}), **value}
        else:
            view[key] = value
    return view


def _validate_parallel_overwrites(
    branch: BranchDefinition, schema: list[dict[str, Any]],
) -> None:
    """Two graph nodes that fan out from one parent run in the same LangGraph
    superstep; if both write a field with no reducer, LangGraph rejects the
    step at the barrier - AFTER both nodes' effects fired (Codex round 3,
    P0). Refuse that shape at compile instead: declare a reducer, or
    serialise the nodes."""
    reduced = {
        (f.get("name") or "").strip()
        for f in schema
        if f.get("name") and (f.get("reducer") or "").strip().lower() in ("append", "merge")
    }
    def_of = {gn.id: (gn.node_def_id or gn.id) for gn in getattr(branch, "graph_nodes", None) or []}
    by_id = {n.node_id: n for n in getattr(branch, "node_defs", None) or []}
    children: dict[str, list[str]] = {}
    for edge in getattr(branch, "edges", None) or []:
        if edge.from_node in def_of and edge.to_node in def_of:
            children.setdefault(edge.from_node, []).append(edge.to_node)
    for parent, kids in children.items():
        if len(kids) < 2:
            continue
        seen: dict[str, str] = {}
        for gid in kids:
            node = by_id.get(def_of.get(gid, ""))
            if node is None:
                continue
            for key in _declared_node_outputs(node):
                if key in reduced:
                    continue
                if key in seen and seen[key] != gid:
                    raise CompilerError(
                        f"Graph nodes '{seen[key]}' and '{gid}' run in parallel after "
                        f"'{parent}' and both write '{key}', which has no reducer: "
                        f"LangGraph refuses that after their effects fired. Declare "
                        f"a reducer for '{key}' (append/merge) or serialise the nodes."
                    )
                seen[key] = gid


def _graph_ancestors(branch: BranchDefinition) -> dict[str, set[str]]:
    """graph node id -> the graph node ids of every ancestor (plain and
    conditional edges alike; a conditional target is a possible ancestor).
    A reference to an earlier effect - ``$ta.effect``, a code node's
    ``effects`` - is legal only within this relation (Codex round 1, P1:
    graph-defined, never scheduler-timing-defined)."""
    def_of: dict[str, str] = {}
    for gn in getattr(branch, "graph_nodes", None) or []:
        def_of[gn.id] = gn.node_def_id or gn.id
    parents: dict[str, set[str]] = {gid: set() for gid in def_of}
    for edge in getattr(branch, "edges", None) or []:
        if edge.from_node in def_of and edge.to_node in def_of:
            parents[edge.to_node].add(edge.from_node)
    for cedge in getattr(branch, "conditional_edges", None) or []:
        for target in (cedge.conditions or {}).values():
            if cedge.from_node in def_of and target in def_of:
                parents[target].add(cedge.from_node)
    out: dict[str, set[str]] = {}
    for gid in def_of:
        seen: set[str] = set()
        stack = list(parents.get(gid, ()))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(parents.get(cur, ()))
        # Keyed by GRAPH node id: two graph nodes sharing one definition have
        # their own ancestors and their own effect identity (Codex round 2, P1).
        out[gid] = seen
    return out


def _validate_delta_reducers(
    node_id: str,
    delta: dict[str, Any],
    append_fields: set[str],
    merge_fields: set[str],
) -> None:
    """What LangGraph will reject when it applies the reducers, rejected HERE,
    before any effect fires (Codex round 2, P0): an append field needs a list,
    a merge field needs a dict."""
    for key, value in delta.items():
        if key in append_fields and not isinstance(value, list):
            raise CompilerError(
                f"Node '{node_id}' returned {key!r} as {type(value).__name__}, but "
                f"that field's reducer is append (a list); nothing fired."
            )
        if key in merge_fields and not isinstance(value, dict):
            raise CompilerError(
                f"Node '{node_id}' returned {key!r} as {type(value).__name__}, but "
                f"that field's reducer is merge (a dict); nothing fired."
            )


def _wrap_with_effects(
    inner_fn: Callable[[dict[str, Any]], dict[str, Any]],
    node: NodeDefinition,
    effect_chain: Any,
    state_schema: list[dict[str, Any]] | None,
    event_sink: Callable[..., None] | None,
    ancestors: set[str] | None = None,
    chain_key: str = "",
    should_cancel: Callable[[], bool] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Fire the node's declared ``effects`` the moment the node returns
    (design D1, change `sandboxed-code-node`): against the state merged with
    the node's delta, recording full results on the run's effect chain so a
    later node - a packet's ``$ta.effect`` or a code node's ``effects`` - can
    read them. A refused packet or a write the far side refused raises
    ``EffectFailedError`` from inside the node: the run fails there and later
    nodes never run. Without an effect chain (tests, legacy callers) the
    post-run dispatcher is still the path and this is a no-op."""
    effects = list(getattr(node, "effects", None) or [])
    if not effects or effect_chain is None:
        return inner_fn
    schema = list(state_schema or [])
    append_fields = {
        f["name"] for f in schema
        if f.get("name") and (f.get("reducer") or "").strip().lower() == "append"
    }
    merge_fields = _merge_reducer_fields(schema)
    node_id = node.node_id

    def _fn(state: dict[str, Any]) -> dict[str, Any]:
        delta = inner_fn(state)
        if not isinstance(delta, dict):
            return delta
        from tinyassets.effectors import EffectFailedError, dispatch_node_effects
        from tinyassets.node_sandbox import _cancel_requested

        if should_cancel is not None and _cancel_requested(should_cancel):
            raise NodeCancelledError(
                "run cancelled before effects were dispatched", node_id=chain_key or node_id,
            )

        _validate_delta_reducers(node_id, delta, append_fields, merge_fields)
        view = _delta_view(state, delta, append_fields, merge_fields)
        try:
            evidence = dispatch_node_effects(
                effect_chain, node, view, state_schema=schema, ancestors=ancestors,
                node_key=chain_key or node_id,
                should_cancel=should_cancel,
            )
        except EffectFailedError as exc:
            # Workspace stages return typed failure evidence after stopping
            # their children. Only the root-owned predicate, never that packet's
            # claimed reason, may turn the graph outcome into cancellation.
            if should_cancel is not None and _cancel_requested(should_cancel):
                raise NodeCancelledError(
                    "run cancelled during effects", node_id=chain_key or node_id,
                ) from exc
            raise
        if event_sink is not None:
            try:
                event_sink(node_id=node_id, phase="effect", effects=evidence)
            except Exception as exc:
                if _is_cancel_exception(exc):
                    raise
                logger.exception("event_sink raised in %s (effect)", node_id)
        return delta

    return _fn


def _build_node(
    node: NodeDefinition,
    *,
    provider_call: Callable[..., str] | None,
    event_sink: Callable[..., None] | None,
    domain_id: str = "",
    state_schema: list[dict[str, Any]] | None = None,
    llm_policy: dict[str, Any] | None = None,
    concurrency_tracker: ConcurrencyTracker | None = None,
    base_path: str | Path | None = None,
    parent_run_id: str = "",
    invocation_depth: int = 0,
    enqueue_context: "NodeEnqueueContext | None" = None,
    universe_context: "UniverseContext | None" = None,
    execution_context: "BranchExecutionContext | None" = None,
    branch_def_id: str = "",
    on_node_status: Callable[[str, str], None] | None = None,
    effect_chain: Any = None,
    ancestors: set[str] | None = None,
    merge_fields: set[str] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    graph_node_id: str = "",
    delivery_source: Any = None,
    file_source: Any = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build the node function for ``node``: the inner adapter, then the
    single-merge-writer guard (when ``merge_fields`` is given), then the
    effect wrapper - the guard MUST see the delta before any effect fires
    (Codex round 2, P0). ``ancestors`` is the set of graph node ids this node
    may reference; ``graph_node_id`` keys its effects on the run's chain."""
    if event_sink is not None and graph_node_id and graph_node_id != node.node_id:
        definition_event_sink = event_sink

        def graph_event_sink(node_id: str, **detail: Any) -> None:
            # One definition can back several graph nodes. Starting and terminal
            # events must address the instance the graph actually scheduled.
            definition_event_sink(
                node_id=graph_node_id if node_id == node.node_id else node_id,
                **detail,
            )

        event_sink = graph_event_sink
    inner = _build_node_inner(
        node,
        provider_call=provider_call,
        event_sink=event_sink,
        domain_id=domain_id,
        state_schema=state_schema,
        llm_policy=llm_policy,
        concurrency_tracker=concurrency_tracker,
        base_path=base_path,
        parent_run_id=parent_run_id,
        invocation_depth=invocation_depth,
        enqueue_context=enqueue_context,
        universe_context=universe_context,
        execution_context=execution_context,
        branch_def_id=branch_def_id,
        delivery_source=delivery_source,
        file_source=file_source,
        on_node_status=on_node_status,
        effect_chain=effect_chain,
        ancestors=ancestors,
        should_cancel=should_cancel,
    )
    if merge_fields is not None:
        inner = _guard_single_writer_merge_outputs(
            inner,
            graph_node_id=graph_node_id or node.node_id,
            declared_outputs=_declared_node_outputs(node),
            merge_fields=merge_fields,
        )
    wrapped = _wrap_with_effects(
        inner, node, effect_chain, state_schema, event_sink, ancestors=ancestors,
        chain_key=graph_node_id or node.node_id,
        should_cancel=should_cancel,
    )
    if not graph_node_id or graph_node_id == node.node_id:
        return wrapped

    def graph_instance_node(state: dict[str, Any]) -> dict[str, Any]:
        try:
            return wrapped(state)
        except CompilerError as exc:
            # Timeout/effect/empty-result terminal events are recorded by the
            # runner from the exception, not by the inner event sink. Keep their
            # identity aligned with the graph-instance starting event as well.
            if getattr(exc, "node_id", None) == node.node_id:
                exc.node_id = graph_node_id
            raise

    return graph_instance_node


def _build_node_inner(
    node: NodeDefinition,
    *,
    provider_call: Callable[..., str] | None,
    event_sink: Callable[..., None] | None,
    domain_id: str = "",
    state_schema: list[dict[str, Any]] | None = None,
    llm_policy: dict[str, Any] | None = None,
    concurrency_tracker: ConcurrencyTracker | None = None,
    base_path: str | Path | None = None,
    parent_run_id: str = "",
    invocation_depth: int = 0,
    enqueue_context: "NodeEnqueueContext | None" = None,
    universe_context: "UniverseContext | None" = None,
    execution_context: "BranchExecutionContext | None" = None,
    branch_def_id: str = "",
    delivery_source: Any = None,
    file_source: Any = None,
    on_node_status: Callable[[str, str], None] | None = None,
    effect_chain: Any = None,
    should_cancel: Callable[[], bool] | None = None,
    ancestors: set[str] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Dispatch a NodeDefinition to the right adapter.

    ``domain_id`` is threaded from ``compile_branch`` (Phase D
    Option B). NodeDefinition has no ``domain_id`` field; domain is
    a Branch-level attribute. When ``domain_id`` is non-empty and
    ``(domain_id, node.node_id)`` resolves in the domain registry,
    the opaque-node branch is taken.

    ``llm_policy`` is the effective policy for this node — the node's
    own policy or the branch default; resolved by ``compile_branch``.
    ``concurrency_tracker`` limits concurrent LLM/sandbox calls via a
    semaphore acquired before the provider call and released after.
    """
    from tinyassets.domain_registry import resolve_domain_callable

    has_template = bool((node.prompt_template or "").strip())
    has_source = bool((node.source_code or "").strip())
    if has_template and has_source:
        raise CompilerError(
            f"Node '{node.node_id}' has both prompt_template and "
            f"source_code — exactly one must be set."
        )
    if has_source:
        inner = _build_source_code_node(
            node, event_sink=event_sink, concurrency_tracker=concurrency_tracker,
            invocation_depth=invocation_depth,
            base_path=base_path, enqueue_context=enqueue_context,
            effect_chain=effect_chain, state_schema=state_schema,
            ancestors=ancestors, execution_context=execution_context,
            delivery_source=delivery_source,
            file_source=file_source,
            should_cancel=should_cancel,
        )
        return _wrap_with_checkpoints(inner, node, event_sink)
    if has_template:
        inner = _build_prompt_template_node(
            node, provider_call=provider_call, event_sink=event_sink,
            state_schema=state_schema, llm_policy=llm_policy,
            concurrency_tracker=concurrency_tracker,
            universe_context=universe_context, branch_def_id=branch_def_id,
            # The run's authenticated universe, so the agent call holds a seat
            # of that universe's ACCOUNT (`universe_seats`).
            seat_scope=(
                (Path(base_path), execution_context.universe_id)
                if base_path is not None and execution_context is not None
                and execution_context.universe_id else None
            ),
        )
        return _wrap_with_checkpoints(inner, node, event_sink)
    if domain_id:
        # Ensure platform opaque callables (e.g. read_repo_files) are
        # registered before we resolve — importing the effectors package runs
        # its registration side effects. Lazy + best-effort so a missing
        # optional dependency can't break compilation of unrelated branches.
        try:
            import tinyassets.effectors  # noqa: F401
        except Exception:  # pragma: no cover - registration is best-effort
            logger.debug("effectors import for opaque registration failed", exc_info=True)
        opaque = resolve_domain_callable(domain_id, node.node_id)
        if opaque is not None:
            inner = _build_opaque_node(node, opaque, event_sink=event_sink)
            return _wrap_with_checkpoints(inner, node, event_sink)
    if node.invoke_branch_spec is not None:
        if base_path is None:
            raise CompilerError(
                f"Node '{node.node_id}' uses invoke_branch_spec but "
                f"compile_branch was not given base_path."
            )
        inner = _build_invoke_branch_node(
            node, base_path=base_path, event_sink=event_sink,
            provider_call=provider_call,
            parent_run_id=parent_run_id,
            depth=invocation_depth,
            execution_context=execution_context,
            on_node_status=on_node_status,
        )
        return _wrap_with_checkpoints(inner, node, event_sink)
    if node.invoke_branch_version_spec is not None:
        if base_path is None:
            raise CompilerError(
                f"Node '{node.node_id}' uses invoke_branch_version_spec but "
                f"compile_branch was not given base_path."
            )
        inner = _build_invoke_branch_version_node(
            node, base_path=base_path, event_sink=event_sink,
            provider_call=provider_call,
            parent_run_id=parent_run_id,
            depth=invocation_depth,
            execution_context=execution_context,
            on_node_status=on_node_status,
        )
        return _wrap_with_checkpoints(inner, node, event_sink)
    if node.await_run_spec is not None:
        if base_path is None:
            raise CompilerError(
                f"Node '{node.node_id}' uses await_run_spec but "
                f"compile_branch was not given base_path."
            )
        inner = _build_await_branch_run_node(
            node, base_path=base_path, event_sink=event_sink,
            execution_context=execution_context,
        )
        return _wrap_with_checkpoints(inner, node, event_sink)
    # Fallback: a genuine body-less node in a non-domain-trusted
    # context is a malformed Branch. Preserve the CompilerError
    # contract so user Branches that omit both template and source
    # (and don't resolve via a domain registry) fail loudly at
    # compile time rather than silently running as pass-throughs.
    # Phase D §4.1 #2.
    raise CompilerError(
        f"Node '{node.node_id}' must have either prompt_template or "
        f"source_code (or resolve via the domain-trusted opaque "
        f"registry when domain_id is set)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Conditional edge predicate
# ─────────────────────────────────────────────────────────────────────────────


def _build_conditional_router(
    source_node: NodeDefinition | None,
    conditions: dict[str, str],
) -> Callable[[dict[str, Any]], str]:
    """Return a LangGraph-compatible router function.

    LangGraph's ``add_conditional_edges(source, router, path_map)``
    contract: the router returns a KEY into ``path_map``, and LangGraph
    looks up the target node via ``self.ends[router_result]``. Returning
    a target node directly makes LangGraph raise ``KeyError`` (the
    target isn't a path_map key). Conditions IS the path_map here, so
    the router reads the state's output_key and returns it verbatim
    when it's a valid label; otherwise falls back to the first declared
    label so the graph cannot hang on a missing/malformed output.

    Rationale for returning-label-not-target: matches
    ``graph.add_conditional_edges(..., path_map=conditions)`` semantics.
    Prior shape returned ``conditions[value]`` (a target) which LangGraph
    then tried to look up as a path_map KEY — always KeyError for any
    non-empty conditions dict. BUG-019/021/022 root cause (Tier-1
    investigation, 2026-04-23).
    """
    output_key = ""
    if source_node and source_node.output_keys:
        output_key = source_node.output_keys[0]

    # Fallback must be a LABEL (path_map key), not a target.
    fallback = next(iter(conditions.keys()), END)

    def _route(state: dict[str, Any]) -> str:
        if not output_key:
            return fallback
        value = state.get(output_key, "")
        if not isinstance(value, str):
            value = str(value)
        # Return the label when it's a valid path_map key; otherwise
        # fall back to the first declared label so the graph advances
        # rather than KeyError-ing.
        if value in conditions:
            return value
        return fallback

    return _route


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CompiledBranch:
    """Result of compiling a BranchDefinition.

    ``state_type`` is the synthesized TypedDict — useful for validating
    user-provided inputs before invoking the graph.
    ``graph`` is the uncompiled ``StateGraph``; callers attach their own
    checkpointer via ``graph.compile(checkpointer=...)`` so the runner can
    use a shared SqliteSaver.
    ``node_ids_in_order`` is the declared node ordering from graph_nodes.
    """

    graph: StateGraph
    state_type: type
    branch: BranchDefinition
    node_ids_in_order: list[str]
    concurrency_tracker: ConcurrencyTracker | None = None
    #: The run's effect chain when the runner supplied one (design D1): the
    #: evidence of every effect that fired at node time, what the run persists.
    effect_chain: Any = None


def compile_branch(
    branch: BranchDefinition,
    *,
    provider_call: Callable[..., str] | None = None,
    event_sink: Callable[..., None] | None = None,
    concurrency_budget_override: int | None = None,
    base_path: str | Path | None = None,
    parent_run_id: str = "",
    invocation_depth: int = 0,
    enqueue_context: "NodeEnqueueContext | None" = None,
    universe_context: "UniverseContext | None" = None,
    execution_context: "BranchExecutionContext | None" = None,
    on_node_status: Callable[[str, str], None] | None = None,
    effect_chain: Any = None,
    should_cancel: Callable[[], bool] | None = None,
) -> CompiledBranch:
    """Compile a validated BranchDefinition into a StateGraph.

    Parameters
    ----------
    branch
        The branch to compile. Must have passed ``branch.validate()``.
    provider_call
        Synchronous LLM caller with signature ``(prompt, system, *, role)
        -> str``. When ``None``, prompt_template nodes return a mock
        string (useful for tests).
    effect_chain
        The run's ``tinyassets.effectors.EffectChain``. When given, every
        node's declared effects fire the moment the node returns (design D1)
        and their evidence accumulates on the chain; when ``None`` (tests,
        legacy callers) effects are the caller's business after the run.
    on_node_status
        The runner's per-node status callback. Threaded down to CHILD branch
        launches so a nested prompt node is gated by the same pre-node checks
        the root's nodes are. Without it an `invoke_branch` node could launch a
        child whose provider call ran unguarded (Codex 2026-08-29 round 2 §1);
        an invoke node emits no `starting` event of its own, so the child is the
        only place the callback can fire.
    event_sink
        Optional callable invoked after each node executes with
        per-node diagnostics. Used by the runner to record
        ``RunStepEvent`` rows.
    concurrency_budget_override
        Override the branch-level ``concurrency_budget`` for this
        compilation. When ``None``, falls back to ``branch.concurrency_budget``.
        When both are ``None``, concurrency is unbounded (current behavior).

    Returns
    -------
    CompiledBranch
        An uncompiled StateGraph + synthesized TypedDict. The caller
        attaches a checkpointer via ``graph.compile(checkpointer=...)``
        and invokes with the synthesized state type.
    """
    errors = branch.validate()
    if errors:
        raise CompilerError(
            "Cannot compile invalid branch:\n  - " + "\n  - ".join(errors)
        )

    merge_fields = _merge_reducer_fields(branch.state_schema or [])
    _validate_single_writer_merge_fields(branch, merge_fields)
    _validate_parallel_overwrites(branch, list(branch.state_schema or []))

    # Build-time warnings (input_keys leaks, etc.) — emit through the
    # event_sink so callers' per-run event logs see them before the
    # first node runs. Warnings are non-fatal regardless of strict
    # isolation; strict mode only gates *runtime* behavior.
    if event_sink is not None:
        for warning in collect_build_warnings(branch):
            try:
                event_sink(
                    node_id=warning["node_id"],
                    phase="warning",
                    kind=warning["kind"],
                    placeholder=warning.get("placeholder", ""),
                    declared_input_keys=warning.get("declared_input_keys", []),
                    message=warning.get("message", ""),
                )
            except Exception as exc:  # noqa: BLE001
                if _is_cancel_exception(exc):
                    raise
                logger.exception(
                    "event_sink raised emitting build-time warning for %s",
                    warning.get("node_id", "?"),
                )

    # Inject _fired_checkpoints into the schema when any node uses checkpoints.
    # This field accumulates the list of fired checkpoint IDs across the run
    # so each checkpoint fires at most once (idempotent on resume).
    schema = list(branch.state_schema or [])
    has_checkpoints = any(
        getattr(nd, "checkpoints", None) for nd in branch.node_defs
    )
    if has_checkpoints:
        if not any(f.get("name") == "_fired_checkpoints" for f in schema):
            schema.append({"name": "_fired_checkpoints", "type": "list", "reducer": "append"})
    state_type = _build_state_typeddict(schema)
    graph: StateGraph = StateGraph(state_type)

    # Build concurrency tracker: override > branch-level > None (unbounded).
    effective_budget = (
        concurrency_budget_override
        if concurrency_budget_override is not None
        else getattr(branch, "concurrency_budget", None)
    )
    concurrency_tracker: ConcurrencyTracker | None = (
        ConcurrencyTracker(effective_budget) if effective_budget is not None else None
    )
    node_by_id: dict[str, NodeDefinition] = {
        n.node_id: n for n in branch.node_defs
    }
    graph_node_by_id: dict[str, GraphNodeRef] = {
        gn.id: gn for gn in branch.graph_nodes
    }

    node_ids_in_order = [gn.id for gn in branch.graph_nodes]
    ancestors_by_gid = _graph_ancestors(branch) if effect_chain is not None else {}
    from tinyassets.run_file_binding import file_declarations

    declared_files = {
        declaration.name: declaration
        for declaration in file_declarations(branch).inputs if declaration.is_file
    }

    # Add graph nodes to the StateGraph. Each graph_node points at a
    # node_def via ``node_def_id`` (usually the same as ``id``).
    for gn in branch.graph_nodes:
        def_id = gn.node_def_id or gn.id
        node_def = node_by_id.get(def_id)
        if node_def is None:
            raise CompilerError(
                f"Graph node '{gn.id}' references unknown node_def_id "
                f"'{def_id}'."
            )
        # Effective llm_policy: node-level takes precedence over branch default.
        effective_policy = node_def.llm_policy or getattr(
            branch, "default_llm_policy", None,
        )
        delivery_source = None
        file_source = None
        if execution_context is not None:
            from tinyassets.api.deliveries import NodeDeliverySource
            from tinyassets.run_file_node import NodeFileSource

            delivery_source = NodeDeliverySource(
                owner_user_id=execution_context.owner_user_id,
                universe_id=execution_context.universe_id, actor=execution_context.actor,
                run_id=parent_run_id, branch_def_id=branch.branch_def_id,
                node_id=gn.id, output_keys=tuple(node_def.output_keys),
            )
            file_source = NodeFileSource(
                owner_user_id=execution_context.owner_user_id,
                universe_id=execution_context.universe_id, actor=execution_context.actor,
                run_id=parent_run_id, branch_def_id=branch.branch_def_id,
                node_id=gn.id,
                fields=tuple(declared_files[name] for name in node_def.input_keys
                             if name in declared_files),
            )
        fn = _build_node(
            node_def,
            provider_call=provider_call,
            event_sink=event_sink,
            domain_id=branch.domain_id,
            state_schema=branch.state_schema,
            llm_policy=effective_policy,
            concurrency_tracker=concurrency_tracker,
            base_path=base_path,
            parent_run_id=parent_run_id,
            invocation_depth=invocation_depth,
            enqueue_context=enqueue_context,
            universe_context=universe_context,
            execution_context=execution_context,
            branch_def_id=branch.branch_def_id,
            on_node_status=on_node_status,
            effect_chain=effect_chain,
            ancestors=ancestors_by_gid.get(gn.id, set()) if effect_chain is not None else None,
            merge_fields=merge_fields,
            graph_node_id=gn.id,
            delivery_source=delivery_source,
            file_source=file_source,
            should_cancel=should_cancel,
        )
        graph.add_node(gn.id, fn)

    # Entry point: connect START to the declared entry node.
    if branch.entry_point:
        graph.add_edge(START, branch.entry_point)

    # Simple edges.
    for edge in branch.edges:
        src = START if edge.from_node == "START" else edge.from_node
        dst = END if edge.to_node == "END" else edge.to_node
        if src == START and branch.entry_point == edge.to_node:
            # Already wired via add_edge(START, entry_point) above.
            continue
        graph.add_edge(src, dst)

    # Conditional edges.
    for cedge in branch.conditional_edges:
        source_ref = graph_node_by_id.get(cedge.from_node)
        source_def_id = source_ref.node_def_id if source_ref else cedge.from_node
        source_def = node_by_id.get(source_def_id or cedge.from_node)
        conditions = {
            label: (END if tgt == "END" else tgt)
            for label, tgt in cedge.conditions.items()
        }
        router = _build_conditional_router(source_def, conditions)
        graph.add_conditional_edges(cedge.from_node, router, conditions)

    return CompiledBranch(
        graph=graph,
        state_type=state_type,
        branch=branch,
        node_ids_in_order=node_ids_in_order,
        concurrency_tracker=concurrency_tracker,
        effect_chain=effect_chain,
    )


# ── Teammate messaging primitives ─────────────────────────────────────────────
# These are graph-compiler-level helpers that nodes call at runtime to send /
# receive teammate messages.  They are thin wrappers around tinyassets.runs so
# that graph_compiler owns the dispatch contract.


def compile_send_message_spec(
    base_path: "Path | str",
    *,
    run_id: str,
    to_node_id: str,
    message_type: str,
    body: "dict[str, Any]",
    reply_to_id: str = "",
) -> "dict[str, Any]":
    """Send a teammate message from a running node.

    Calls post_teammate_message and returns the persisted record dict.
    Raises KeyError if run_id does not exist; raises ValueError on invalid args.
    """
    from tinyassets.runs import post_teammate_message

    record = post_teammate_message(
        base_path,
        from_run_id=run_id,
        to_node_id=to_node_id,
        message_type=message_type,
        body=body,
        reply_to_id=reply_to_id or None,
    )
    return record


def compile_receive_messages_spec(
    base_path: "Path | str",
    *,
    node_id: str,
    timeout: int = 0,
    run_id: str = "",
    message_types: "list[str] | None" = None,
    since: "str | None" = None,
    limit: int = 50,
) -> "dict[str, Any]":
    """Receive queued teammate messages for a node.

    Non-blocking (timeout=0 is the contract; positive timeout ignored for now).
    When run_id is given, returns only messages sent from that run (cross-run
    isolation).  Returns ``{"messages": [...], "count": N}``.
    """
    from tinyassets.runs import read_teammate_messages

    rows = read_teammate_messages(
        base_path,
        node_id=node_id,
        since=since,
        message_types=message_types,
        limit=limit,
    )
    if run_id:
        rows = [r for r in rows if r.get("from_run_id") == run_id]
    return {"messages": rows, "count": len(rows)}


def validate_message_recipients(
    branch: "BranchDefinition",
    send_message_specs: "list[dict[str, Any]]",
) -> None:
    """Compile-time validation: every to_node_id must exist in the branch.

    Raises BranchValidationError (subclass of ValueError) listing all unknown
    recipients so the caller gets a single actionable error.
    """
    known_node_ids = {n.node_id for n in branch.node_defs}
    unknown = [
        spec["to_node_id"]
        for spec in send_message_specs
        if spec.get("to_node_id") and spec["to_node_id"] not in known_node_ids
    ]
    if unknown:
        raise BranchValidationError(
            "send_message_spec recipient(s) not found in branch: "
            + ", ".join(repr(u) for u in unknown)
        )
