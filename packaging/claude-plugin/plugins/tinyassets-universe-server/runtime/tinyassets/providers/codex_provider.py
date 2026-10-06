"""Codex / GPT provider -- ``codex exec`` subprocess.

Covered by the ChatGPT Plus subscription.  Different model family from
Claude, making it ideal as a judge when Claude is the writer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from tinyassets import agent_sessions
from tinyassets.exceptions import (
    InteractiveDeadlineError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderIdleTimeoutError,
    ProviderProtocolError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from tinyassets.providers import provider_jail
from tinyassets.providers.base import (
    BaseProvider,
    ModelConfig,
    ProviderResponse,
    check_bwrap_failure,
    get_sandbox_status,
    subprocess_env_for_provider,
)
from tinyassets.providers.owned_process import (
    aspawn_owned,
    disk_stop_note,
    kill_owned_tree,
    no_window_kwargs,
)
from tinyassets.providers.provider_jail import JailMount, UniverseView
from tinyassets.role_provider_execution import CellView
from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS, granted_tools

logger = logging.getLogger(__name__)


def _no_window_kwargs() -> dict:
    """Return subprocess kwargs to suppress console windows on Windows."""
    return no_window_kwargs()


def _resolve_codex_cmd() -> tuple[list[str], bool]:
    """Resolve the codex command, handling Windows .cmd/.bat wrappers.

    Returns (base_cmd, use_shell) where base_cmd is the command prefix
    and use_shell indicates whether to use shell execution.
    """
    codex_path = shutil.which("codex")
    if codex_path and sys.platform == "win32" and codex_path.lower().endswith((".cmd", ".bat")):
        return [codex_path], True
    if codex_path:
        return [codex_path], False
    return ["codex"], False


_CODEX_BIN_ASSIGNMENT = re.compile(
    r"(?m)^\s*CODEX_BIN\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|(\S+))\s*$"
)


def _resolved_codex_executable(base_cmd: list[str]) -> tuple[Path, Path]:
    """Return the invoked wrapper and the real executable it delegates to."""

    if not base_cmd:
        raise ProviderError("codex served sandbox cannot resolve an empty command")
    wrapper = Path(base_cmd[0]).expanduser()
    if not wrapper.is_absolute():
        located = shutil.which(str(wrapper))
        if not located:
            raise ProviderError("codex served sandbox cannot resolve the executable")
        wrapper = Path(located)
    try:
        wrapper = Path(os.path.abspath(wrapper))
        resolved = wrapper.resolve(strict=True)
    except OSError as exc:
        raise ProviderError(
            "codex served sandbox cannot resolve the executable"
        ) from exc
    real_executable = resolved
    if wrapper == resolved:
        try:
            with wrapper.open("rb") as stream:
                wrapper_text = stream.read(65_536).decode("utf-8", errors="strict")
        except (OSError, UnicodeError):
            wrapper_text = ""
        if "CODEX_BIN" in wrapper_text:
            match = _CODEX_BIN_ASSIGNMENT.search(wrapper_text)
            if match is None:
                raise ProviderError(
                    "codex served sandbox cannot resolve the wrapper's real binary"
                )
            try:
                raw_real_executable = next(
                    value for value in match.groups() if value is not None
                )
                real_path = Path(raw_real_executable)
                if not real_path.is_absolute():
                    raise OSError("wrapper target is not absolute")
                real_executable = real_path.resolve(strict=True)
            except OSError as exc:
                raise ProviderError(
                    "codex served sandbox cannot resolve the wrapper's real binary"
                ) from exc
    if not real_executable.is_file():
        raise ProviderError("codex served sandbox resolved binary is not a file")
    return wrapper, real_executable


def _codex_binary_tree(real_executable: Path) -> Path:
    for ancestor in real_executable.parents:
        if ancestor.name == "node_modules":
            tree = ancestor.parent
            break
    else:
        tree = real_executable.parent
    if not tree.is_dir():
        raise ProviderError("codex served sandbox cannot mount the resolved binary tree")
    return tree


_SECRET_SHAPES = re.compile(
    # Explicit secret shapes only (a generic long-token rule also hid hashes,
    # paths and model ids — the real cause). JWT fragments: any `eyJ…` run,
    # with or without the dotted tail.
    r"(sk-[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_.-]{10,}|"
    r"(?i:bearer\s+\S+)|(?i:(?:token|secret|api[_-]?key|password)[\"']?\s*[:=]\s*\S+))"
)


def _redacted_stderr_excerpt(stderr_text: str, limit: int = 240) -> str:
    """The last stderr line, secrets replaced, as a head+tail excerpt.

    Feeds user-visible diagnostics (router chain_state), so it must be safe
    even if codex ever echoes credential material. Head+tail (not a plain
    prefix) because codex 0.135 appends its auth error code at the END of
    the line."""
    lines = [line.strip() for line in stderr_text.strip().splitlines() if line.strip()]
    if not lines:
        return "(no stderr)"
    from tinyassets.workspace_git import scrub_text

    text = _SECRET_SHAPES.sub("[redacted]", scrub_text(lines[-1]))
    if len(text) <= limit:
        return text
    half = (limit - 5) // 2
    return text[:half] + " ... " + text[-half:]


def _structured_failure_excerpt(stdout: bytes, stderr_text: str, *, machine: bool) -> str:
    """Prefer a failed JSON turn's own reason over unrelated tracing stderr."""
    if machine:
        last_error = ""
        terminal_error = ""
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "turn.failed":
                error = event.get("error")
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    terminal_error = error["message"]
            elif event.get("type") == "error" and isinstance(event.get("message"), str):
                last_error = event["message"]
        if terminal_error or last_error:
            # Flatten before scrubbing: never drop an error's first line or
            # cut through a credential before matching its complete shape.
            return _redacted_stderr_excerpt(" ".join((terminal_error or last_error).splitlines()))
    return _redacted_stderr_excerpt(stderr_text)


#: Phrases in the CLI's OWN terminal error that mean the stored sign-in is
#: finished, not that the source is having a bad minute. A spent single-use
#: refresh token is the live case (founder's subscription, every turn since
#: 2026-09-24): the platform now refreshes before launch, but a launch that still
#: reaches this state must be reported as a SIGN-IN failure, not an outage.
#:
#: Why it matters which: a `ProviderUnavailableError` buys the source a 120s
#: cooldown and stops the turn, while a `ProviderAuthenticationError` marks the
#: source for reconnect and lets the router continue to the next model the owner
#: allowed (providers/router.py, `except ProviderAuthenticationError`). One is a
#: dead turn, the other is an answered one.
_TERMINAL_AUTH_PHRASES = (
    "already been used",
    "already used",
    "invalid_grant",
    "sign in again",
    "log in again",
    "not logged in",
    "please login",
    "please log in",
    "reauthenticate",
    "re-authenticate",
    "unauthorized",
)


def _terminal_auth_failure(excerpt: str) -> bool:
    """Whether the CLI's own redacted words describe a finished sign-in.

    Read from the excerpt because that is the only channel the CLI gives for this:
    it exits 1 like any other early failure, and the reason is in its message.
    Matched against a NARROW phrase list rather than any mention of "auth", so an
    ordinary failure that happens to name an auth header is not turned into a
    request for the owner to sign in again.
    """
    lower = excerpt.lower()
    return any(phrase in lower for phrase in _TERMINAL_AUTH_PHRASES)


#: Where the adapter's private home is mounted inside its jail.
_JAIL_HOME = "/codex-home"

_THREAD_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z-]{0,127}")


def _native_session_exists(store: Path, thread_id: str) -> bool:
    """Whether ``store`` still holds the rollout file for ``thread_id``."""
    if not _THREAD_ID.fullmatch(thread_id or ""):
        return False
    try:
        return agent_sessions.native_file_exists(store, f"{thread_id}.jsonl")
    except OSError:
        return False


def _configured_rollout_model(
    universe_dir: Path, store: Path, thread_id: str, started_at: float,
) -> str:
    """Read this launch's configured model from its own native turn context.

    The pinned CLI's JSONL omits the model, but its saved rollout records it in
    turn_context.payload.model. That is configuration, not response-model
    verification. Never borrow an earlier resumed turn or another thread.
    """
    from tinyassets.providers.execution_receipt import _label
    from tinyassets.universe_files import read_universe_text

    if not _THREAD_ID.fullmatch(thread_id or ""):
        return ""
    model = ""
    for directory, _, files in os.walk(store, followlinks=False):
        for name in files:
            if not name.endswith(f"-{thread_id}.jsonl"):
                continue
            try:
                relative = (Path(directory) / name).relative_to(universe_dir)
                content = read_universe_text(universe_dir, str(relative))
                for line in content.splitlines():
                    event = json.loads(line)
                    if not isinstance(event, dict) or event.get("type") != "turn_context":
                        continue
                    stamp = datetime.fromisoformat(
                        event.get("timestamp", "").replace("Z", "+00:00"),
                    )
                    if stamp.tzinfo is None or stamp.timestamp() < started_at:
                        continue
                    payload = event.get("payload")
                    model = _label(payload.get("model"), 200) if isinstance(payload, dict) else ""
            except (OSError, ValueError, TypeError, AttributeError):
                # Optional display evidence: absent, old, oversized or unreadable
                # rollouts must not make a completed answer fail or invent a name.
                continue
    return model


def _codex_home_file_mounts(codex_home: Path) -> list[JailMount]:
    """A read-only bind for each regular file of the sealed snapshot."""
    mounts: list[JailMount] = []
    for entry in sorted(codex_home.iterdir()):
        if entry.is_symlink() or not entry.is_file():
            continue
        mounts.append(JailMount("ro-bind", f"{_JAIL_HOME}/{entry.name}", entry))
    if not mounts:
        raise ProviderError("codex served sandbox found no credential files to mount")
    return mounts


def _codex_sandbox_mounts(base_cmd: list[str]) -> tuple[Path, ...]:
    wrapper, real_executable = _resolved_codex_executable(base_cmd)
    candidates = [_codex_binary_tree(real_executable)]
    covered_roots = tuple(Path(path) for path in ("/usr", "/bin", "/lib", "/lib64"))
    if not any(wrapper.is_relative_to(root) for root in covered_roots):
        candidates.append(wrapper.parent)
    mounts: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve(strict=True)
        if resolved not in mounts:
            mounts.append(resolved)
    return tuple(mounts)


_VALID_CODEX_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh"})


def _reasoning_effort_args(effort: str | None) -> list[str]:
    """Map a generic ModelConfig.reasoning_effort to Codex's CLI override.

    Codex honors ``-c model_reasoning_effort=<minimal|low|medium|high|xhigh>``.
    Empty / unknown values yield no flag (provider default), so the knob is a
    pure opt-in and never breaks a call.
    """
    normalized = (effort or "").strip().lower()
    if normalized in _VALID_CODEX_EFFORTS:
        return ["-c", f"model_reasoning_effort={normalized}"]
    return []


def _codex_model() -> str:
    """Return an explicit operator override, or let the connected CLI choose.

    A compiled model default can become unsupported by an otherwise healthy
    subscription. An unspecified model belongs to the provider's own selection,
    not a platform-maintained catalogue. Never retry a rejected explicit choice
    with some other model.
    """
    return os.environ.get("TINYASSETS_CODEX_MODEL", "").strip()


def _codex_workdir() -> str:
    """Return the source workspace Codex should inspect for coding tasks."""
    configured = os.environ.get("TINYASSETS_CODEX_WORKDIR", "").strip()
    if configured:
        return configured
    return str(Path(__file__).resolve().parents[2])


#: Env var codex reads the engine-MCP bearer from (``bearer_token_env_var``). The
#: secret lands in the codex subprocess env, NOT on argv or in the prompt — so the
#: served model never sees it (mirrors claude_provider's --mcp-config headers).
_ENGINE_MCP_BEARER_ENV = "TINYASSETS_ENGINE_MCP_BEARER"

#: Exactly the engine tools the served agent may call (codex ``enabled_tools``).
#: Belt-and-suspenders with the server's own registration: even if a tool is
#: added to the server, it is not callable unless listed here. PUBLISH is
#: deliberately absent (deferred to the consent-gated slice — Codex ADAPT #5).
# run_graph + write_graph ARE included (2026-08-23): the invoke_branch closure is
# now sanitized (#2498), so a run/build reaching a public branch is safe.
# remix_shape (cross-author fork) stays EXCLUDED pending its own review slice.
# Served engine-MCP allowlist — the SINGLE canonical list from served_tools.py,
# shared verbatim with the claude surface (universe_intelligence._ENGINE_MCP_TOOLS)
# so the two provider surfaces CANNOT drift (founder rule: all surfaces do the same
# things). To change what the served agent can do, edit served_tools.py once.
_ENGINE_MCP_ENABLED_TOOLS = SERVED_ENGINE_MCP_TOOLS

#: Force the served CWD project untrusted so codex never loads a project-level
#: ``/workspace/.codex/config.toml`` (which a crafted universe could ship with its
#: OWN ``mcp_servers``). Codex ADAPT 2026-08-22 #3: ``-c mcp_servers={...}``
#: MERGES rather than replaces, so eliminating every lower-precedence config
#: source — not a blanket ``mcp_servers={}`` clear (a no-op) — is what keeps the
#: injected server the only one. Applied to every served turn.
_UNTRUSTED_WORKSPACE_ARGS = ("-c", 'projects."/workspace".trust_level="untrusted"')


def _codex_engine_mcp_args(config: ModelConfig, proc_env: dict[str, str]) -> list[str]:
    """The ``-c`` config args governing the served codex turn's MCP surface.

    Always forces ``/workspace`` untrusted so no project ``.codex/config.toml``
    (and its ``mcp_servers``) loads. Then, when the founder-scoped engine MCP is
    enabled AND a per-universe HTTP engine server is running (its loopback
    owner-matched route in ``.engine_mcp_http_routes.json``, written 0600 by
    ``engine_mcp_http``), wires codex to that ONE trusted server — the same
    commons + own-universe handles the browser chatbot has — restricted to
    ``enabled_tools`` and marked ``required``, and injects the bearer into
    ``proc_env`` (codex reads it via ``bearer_token_env_var``, keeping it off
    argv/prompt). This is the codex analogue of
    ``claude_provider._engine_mcp_flags``.

    Injection safety (verified against codex-cli 0.146, 2026-08-22): ``-c
    mcp_servers={...}`` MERGES rather than replaces, so a served turn is kept to
    exactly this one server by eliminating every other source —
    ``--ignore-user-config`` drops ``$CODEX_HOME/config.toml``; the untrusted
    ``/workspace`` skips project config; the chat turn's ``/workspace`` is an
    empty tmpfs anyway; and ``--disable apps`` removes account ChatGPT connectors.

    FAIL CLOSED: engine MCP requested but no running HTTP server (no route / no
    secret) -> add no server (WebFetch-only), never a half-wired or
    unauthenticated one. stdio is not an option (the package is not in the jail).
    """
    # This environment belongs to this launch; do not retain a previous route's
    # bearer if the new route is missing, disabled, or belongs to another owner.
    proc_env.pop(_ENGINE_MCP_BEARER_ENV, None)
    args = list(_UNTRUSTED_WORKSPACE_ARGS)
    if not (
        getattr(config, "engine_mcp_enabled", False)
        and (getattr(config, "engine_mcp_actor_id", "") or "").strip()
        and (getattr(config, "engine_mcp_graph_id", "") or "").strip()
    ):
        return args
    graph_id = config.engine_mcp_graph_id.strip()
    from tinyassets.engine_mcp_http import read_engine_mcp_route

    route = read_engine_mcp_route(
        actor_id=config.engine_mcp_actor_id.strip(), graph_id=graph_id,
    )
    if route is None:
        return args
    proc_env[_ENGINE_MCP_BEARER_ENV] = route.secret
    # An agent node's grant narrows the served set (served_tools.granted_tools).
    enabled = ",".join(f'"{t}"' for t in granted_tools(config))
    # Dotted key merges the one server into the (otherwise-empty) map.
    # default_tools_approval_mode="approve": codex MCP tools default to `auto`,
    # which requires per-call approval; a non-interactive served `codex exec` has
    # no approver, so the prompt auto-cancels ("user cancelled MCP tool call").
    # Auto-approve this ONE trusted, enabled_tools-restricted server so its tools
    # actually execute (Codex diagnosis 2026-08-22; verified key parses on 0.146).
    from tinyassets.engine_steering import route_with_session, session_of, turn_of

    # The route names this launch's session and live turn, so the engine steers
    # only the owner's chat thread, in this turn, with a message sent mid-turn
    # (harness S2).
    url = route_with_session(route.url, session_of(config), turn_of(),
                             grant_key=getattr(route, "grant_key", ""), tools=granted_tools(config))
    server = (
        "mcp_servers.tinyassets={"
        f'url="{url}",bearer_token_env_var="{_ENGINE_MCP_BEARER_ENV}",'
        f'required=true,default_tools_approval_mode="approve",'
        f"enabled_tools=[{enabled}]"
        "}"
    )
    args += ["-c", server]
    return args


def _terminate(proc) -> None:
    """Kill a provider subprocess tree, tolerating one that has already exited.

    Signals only the group recorded for this process at spawn, so a descendant
    the CLI started (the Windows shim's real binary, the engine-MCP server) dies
    with the turn instead of outliving it unowned. A process this adapter did
    not spawn -- a test double, an externally supplied handle -- is unmarked and
    is killed individually exactly as before, never by group.

    Killing a finished process raises ProcessLookupError on POSIX, which would
    replace the real exception (often CancelledError) with a confusing one; the
    helper suppresses that on every path.
    """
    with contextlib.suppress(ProcessLookupError, OSError):
        kill_owned_tree(proc)


# --- streamed reader (parity with claude_provider._read_stream) --------------
#: ``codex exec --json`` item types that mean "the turn is waiting on its own
#: tool". While one is open the idle watchdog stands down: the tool has its own
#: timeout, and a turn waiting on work it asked for is not idle.
_CODEX_TOOL_ITEM_TYPES = frozenset({
    "mcp_tool_call", "command_execution", "web_search", "file_change",
})
#: Event types that count as liveness: the CLI talking in its protocol.
_CODEX_LIVENESS_PREFIXES = ("thread.", "turn.", "item.", "error")
#: Terminal-failure events. They still prove the process is alive, but a tool
#: that was open when the turn failed is not coming back, so the idle watchdog
#: re-arms rather than waiting out the tool allowance (Codex round 2, P1).
#: ONLY ``turn.failed``: codex 0.146 also emits a top-level ``error`` for a
#: notification whose ``will_retry`` is true - the JSONL projection drops that
#: flag - while the turn stays Running and the open tool is still coming back.
#: Treating it as terminal re-armed idle mid-retry and killed a healthy turn
#: (Codex round 3, P1, reproduced). ``error`` remains plain liveness.
_CODEX_TERMINAL_FAILURE_TYPES = frozenset({"turn.failed"})
#: How long a turn may wait on ONE open tool before that wait counts as idle.
#: The tool-in-flight rule exists because a 30s idle budget killed healthy
#: 42s tool calls; but "not idle until the absolute cap" turned a wedged tool
#: into an hour-long wait (Codex round 2, P1). Fifteen minutes covers any run a
#: served tool call launches today and still ends a silent wedge.
_TOOL_WAIT_S = 900.0

#: Silence INSIDE the turn is the model generating. ``codex exec --json`` emits
#: no deltas at all - not for reasoning, not for the assistant message - so the
#: gap between one event and the next is one full model round-trip, and on
#: 2026-08-29 (deployed #2674) a 31s gap between two engine tool calls was
#: killed as "idle" at the 30s interval. The turn was healthy.
#:
#: "Inside the turn" begins at the FIRST protocol event, not at ``turn.started``:
#: codex 0.146 delivers ``turn.started`` / ``item.started`` / ``item.completed``
#: best-effort - the in-process app-server queue guarantees only
#: ``TurnCompleted`` (app-server/src/in_process.rs) - so there is NO reliable
#: signal for the turn's start, and ``codex exec`` runs exactly one turn.
#: Two windows are therefore read as generation although nothing is
#: generating, and are bounded by this constant rather than detected sooner
#: (Codex round 2, P1s; accepted):
#:
#: * ``thread.started`` is printed by exec itself BEFORE it requests
#:   ``turn/start`` (exec/src/lib.rs). A stall between the two is an
#:   in-process RPC hanging, never a model wait; guarding it with the short
#:   init budget would re-kill every first generation whose ``turn.started``
#:   was dropped - the round-1 P1.
#: * ``TurnCompleted(Interrupted)`` projects no ``turn.completed`` /
#:   ``turn.failed`` (event_processor_with_jsonl_output.rs), so a shutdown
#:   that stalls after an interruption keeps this bound, not the tail grace.
#:   Only SIGINT interrupts an exec turn, and the daemon never sends one.
#:
#: Same bound as a tool wait: with ``item.started`` droppable, "in a tool" and
#: "generating" are not reliably distinguishable, and no evidence supports a
#: tighter round-trip bound (31s live; ~100s per round-trip observed).
_TURN_WAIT_S = _TOOL_WAIT_S
#: After ``turn.completed`` / ``turn.failed`` - both projections of the one
#: guaranteed notification, ``TurnCompleted`` - the result is in hand; what
#: remains is codex's own shutdown, which 0.146 bounds at 45s
#: (``IN_PROCESS_SHUTDOWN_TIMEOUT``: exec unsubscribes the thread, then awaits
#: ``client.shutdown()``). A stalled shutdown is not the turn's failure: past
#: this bound the reader ends the child and RETURNS the finished stream
#: (Codex round 1, P1: a 30s idle here discarded a completed, healthy turn).
_TAIL_WAIT_S = 60.0
#: asyncio's default 64 KiB stream limit raises on one long JSON line. A
#: single ``item.completed`` carrying an MCP result - a GET /contents reply is
#: the base64 of a whole file - can exceed it (Codex round 2, P1: a 70,000-char
#: event raised ``Separator is found, but chunk is longer than limit``). Same
#: bound the claude reader uses; an over-long line is still a protocol error,
#: not a hang.
_STDOUT_READER_LIMIT = 32 * 1024 * 1024


def _codex_turn_completed(stdout: bytes) -> bool:
    """True when the ``--json`` stream carries codex's own ``turn.completed``.

    ``TurnCompleted`` is the one notification codex 0.146 guarantees end to
    end (the in-process queue drops ``item.*`` under backpressure and backfills
    completions only for items it saw start - so a finished turn can even lack
    its ``agent_message``; ``complete()`` then fails loud, see
    ``docs/concerns/2026-08-29-codex-agent-message-can-be-dropped-under-backpressure.md``).
    It is the protocol's word that the turn finished; the exit code describes
    the PROCESS. When the turn
    completed, a non-zero exit afterwards - the reader ending a stalled
    shutdown (``_TAIL_WAIT_S``), or codex failing in its own teardown - is
    logged, never raised: raising would discard a finished, healthy turn.
    """
    if b"turn.completed" not in stdout:
        return False
    for line in stdout.splitlines():
        if b"turn.completed" not in line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and obj.get("type") == "turn.completed":
            return True
    return False


async def _stream_codex_exec(
    proc, stdin_bytes: bytes, config: ModelConfig, *, start: float,
) -> tuple[bytes, bytes]:
    """Read ``codex exec --json`` incrementally under the idle-watchdog profile.

    Founder rule 2026-08-29: *"a turn should continue till finished unless
    interrupted by the user or should stop for some other reason."* The previous
    reader buffered everything under one ``wait_for(communicate(),
    timeout=config.timeout)``, so a productive multi-call turn was killed at
    300s - three clean GitHub round-trips, then the wall clock - and the router
    then cooled the provider for 120s as if it were sick. Claude's streamed
    reader had already moved past that (``claude_provider._read_stream``); this
    gives codex the same profile.

    The genuine stop reasons, and what each raises:

    * **idle** - no protocol event for ``profile.init_s`` before the first one
      (launch), or none for ``_TURN_WAIT_S`` / ``_TOOL_WAIT_S`` inside the turn
      (silence there is the model generating - codex emits no deltas; see the
      constants) -> :class:`ProviderIdleTimeoutError` (no provider cooldown);
    * **tail** - after ``turn.completed`` / ``turn.failed`` the stream is
      complete. A child that has not exited ``_TAIL_WAIT_S`` later is ended and
      the finished stream is RETURNED - a slow shutdown is never a failure;
    * **cap** - still progressing past ``profile.absolute_cap_s`` ->
      :class:`InteractiveDeadlineError` (no cooldown; a runaway backstop, not
      a deadline - see ``universe_intelligence._sandboxed_config``).

    A turn waiting on its OWN tool is not idle. ``run_graph`` took 42s live on
    2026-08-29; a 30s idle budget would have killed a healthy turn mid-call,
    which is worse than the cap it replaces. So while an ``item.started`` tool
    item has no matching ``item.completed``, the allowance is ``_TOOL_WAIT_S``
    and ``tool_phase`` telemetry says ``in_tool``. Claude's reader likewise
    pairs native tool identities and honors a bounded pending-tool allowance.

    Returns ``(stdout_bytes, stderr_bytes)`` exactly as ``communicate()`` did,
    so every downstream parse is unchanged.
    """
    profile = config.stream_timeout_profile()
    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    last_progress = start
    in_turn = False      # any protocol event seen: the one turn is running
    turn_done = False    # turn.completed / turn.failed seen: only exit remains
    tools_in_flight: set[str] = set()
    soft_slo_logged = False

    async def _drain_stderr() -> None:
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not isinstance(chunk, (bytes, bytearray)) or not chunk:
                    break
                err_chunks.append(bytes(chunk))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - stderr drain must never break a turn
            return

    async def _feed_stdin() -> None:
        try:
            if proc.stdin is not None:
                proc.stdin.write(stdin_bytes)
                await proc.stdin.drain()
                proc.stdin.close()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a closed stdin must not break a turn
            return

    stderr_task = asyncio.create_task(_drain_stderr())
    stdin_task = asyncio.create_task(_feed_stdin())

    async def _finish_stderr() -> bytes:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(stderr_task), timeout=2)
        return b"".join(err_chunks)

    def _attach(exc: ProviderError) -> ProviderError:
        exc.attempt_telemetry = {
            "provider": "codex",
            "failure_class": getattr(exc, "failure_class", None),
            "phase": "streaming" if in_turn else "launch",
            "tool_phase": (
                "in_tool" if tools_in_flight
                else "in_turn" if in_turn and not turn_done
                else None
            ),
            "last_progress_age_ms": (time.monotonic() - last_progress) * 1000,
            "exit_code": proc.returncode,
        }
        return exc

    async def _raise_timeout(bound_is_absolute: bool, allow: float) -> None:
        _terminate(proc)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=5)
        check_bwrap_failure(
            (await _finish_stderr()).decode("utf-8", errors="replace")
        )
        if bound_is_absolute:
            raise _attach(InteractiveDeadlineError(
                f"codex exec exceeded the {profile.absolute_cap_s:.0f}s absolute "
                "interactive cap while still progressing"
            ))
        raise _attach(ProviderIdleTimeoutError(
            f"codex exec produced no protocol event for {allow:.0f}s "
            "(idle watchdog fired; no provider cooldown)"
        ))

    async def _cut_tail() -> None:
        # The turn is complete (its guaranteed terminal event was read); only
        # codex's own shutdown is outstanding and it has overrun its 45s bound.
        # End the child and keep the finished stream. The exit code this leaves
        # is process trivia: the caller reads past it when ``turn.completed`` is
        # in the stream (``_codex_turn_completed``).
        logger.warning(
            "codex exec completed its turn but did not exit within %.0fs; "
            "ending it and keeping the finished stream",
            _TAIL_WAIT_S,
        )
        _terminate(proc)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=5)

    try:
        while True:
            now = time.monotonic()
            if turn_done:
                # The cap bounds a RUNNING turn; this one has finished, so the
                # only clock left is the tail grace for codex's own exit.
                allow = _TAIL_WAIT_S
                budget = last_progress + allow - now
                bound_is_absolute = False
            else:
                if tools_in_flight:
                    allow = min(profile.absolute_cap_s, _TOOL_WAIT_S)
                elif in_turn:
                    allow = min(profile.absolute_cap_s, _TURN_WAIT_S)
                else:
                    allow = profile.init_s
                idle_deadline = last_progress + allow
                abs_deadline = start + profile.absolute_cap_s
                budget = min(idle_deadline, abs_deadline) - now
                bound_is_absolute = abs_deadline <= idle_deadline
            if budget <= 0:
                if turn_done:
                    await _cut_tail()
                    break
                await _raise_timeout(bound_is_absolute, allow)
            if not soft_slo_logged and now - start >= profile.soft_slo_s:
                soft_slo_logged = True
                logger.info(
                    "served codex turn exceeded soft SLO %.0fs; still progressing",
                    profile.soft_slo_s,
                )
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=budget)
            except asyncio.TimeoutError:
                if turn_done:
                    await _cut_tail()
                    break
                await _raise_timeout(bound_is_absolute, allow)
            except (ValueError, asyncio.LimitOverrunError):
                _terminate(proc)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5)
                await _finish_stderr()
                raise _attach(ProviderProtocolError(
                    "codex exec stream line exceeded the reader buffer limit"
                ))
            if not line:
                break
            out_chunks.append(line)
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except (ValueError, TypeError):
                # Not protocol. Proves the process is alive, not that it is
                # making progress; only real events reset the clock.
                continue
            if not (isinstance(obj, dict) and isinstance(obj.get("type"), str)):
                continue
            etype = obj["type"]
            if etype.startswith(_CODEX_LIVENESS_PREFIXES):
                last_progress = time.monotonic()
                in_turn = True
            if etype == "turn.completed" or etype in _CODEX_TERMINAL_FAILURE_TYPES:
                # Both projections of the guaranteed TurnCompleted. Nothing the
                # turn started is still coming back; the tail rule above already
                # outranks an open tool (Codex round 1, P1), and clearing keeps
                # tool_phase telemetry honest.
                turn_done = True
                tools_in_flight.clear()
            item = obj.get("item")
            if isinstance(item, dict) and item.get("type") in _CODEX_TOOL_ITEM_TYPES:
                key = str(item.get("id") or item.get("type"))
                if etype == "item.started":
                    tools_in_flight.add(key)
                elif etype in ("item.completed", "item.failed"):
                    tools_in_flight.discard(key)
        # NORMAL exit only (we fell out of the loop on EOF): let the stderr
        # drain FINISH before the reap below cancels it. On a clean EOF the
        # child's stderr is usually a few bytes still in flight, and cancelling
        # first returned b"" for stderr the caller then parsed for auth / bwrap
        # signals. Bounded, so a child holding fd 2 open cannot hang us. This
        # sits INSIDE the try on purpose: an exceptional or cancelled exit must
        # not be held for it (Codex round 3, P2 - the grace delayed a caller's
        # cancellation by 2-7s).
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(stderr_task), timeout=2)
    finally:
        # Every exit path - EOF, a classified raise, a reader exception, or
        # CALLER CANCELLATION - reaps BOTH helper tasks, bounded, so neither
        # drain leaks (Codex round 2, P2; same ownership the claude reader
        # settled on). ``_terminate`` is idempotent and only kills a process
        # that is still running, so a clean exit keeps its real returncode.
        # suppress(Exception), NOT BaseException: a CancelledError delivered to
        # the caller during this bounded gather must propagate, or the caller's
        # cancellation is silently lost (Codex round 3, P1, reproduced).
        stdin_task.cancel()
        stderr_task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                asyncio.gather(stdin_task, stderr_task, return_exceptions=True),
                timeout=5,
            )

    # stdout EOF is NOT process exit (Codex round 2, P1): a child can close fd 1
    # and keep running. Give it a bounded grace to exit on its own, then end it,
    # so the caller never gets returncode=None with a live orphan behind it.
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=5)
    if proc.returncode is None:
        logger.warning("codex exec closed stdout but did not exit; terminating")
        _terminate(proc)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=5)
    return b"".join(out_chunks), b"".join(err_chunks)


class CodexProvider(BaseProvider):
    """Calls GPT via the ``codex exec`` CLI binary."""

    agent_execution_kind = "native_agent"
    #: Continues a stored native session by its thread id (``agent_sessions``).
    native_resume = True

    name = "codex"
    family = "openai"
    native_credential_service = name
    native_command_resolver = staticmethod(lambda: _resolve_codex_cmd())
    native_process_options = staticmethod(_no_window_kwargs)
    native_install_mounts = staticmethod(lambda command: _codex_sandbox_mounts(command))
    native_metadata_arguments = ("app-server",)
    from tinyassets.providers.native_jsonrpc_discovery import NativeJsonRpcProtocol

    native_discovery_protocol = NativeJsonRpcProtocol(
        list_method="model/list", items_key="data", model_key="model", default_key="isDefault",
        modalities_key="inputModalities", hidden_key="hidden", cursor_key="nextCursor",
        cursor_param="cursor", initialize_method="initialize",
        initialized_notification="initialized",
        # Effort, from the source rather than a constant here. Codex advertises
        # no boolean gate and lists OBJECTS, so support is implied by a
        # non-empty list and the level name sits inside each entry. Its
        # vocabulary also differs from Claude Code's -- a live catalogue offers
        # `ultra`, which Claude does not -- which is why the admissible set is
        # always per model and never a shared enum.
        effort_levels_key="supportedReasoningEfforts",
        effort_level_key="reasoningEffort",
        initialize_params_json='{"clientInfo":{"name":"tinyassets_model_discovery","version":"1"}}',
        list_params_json='{"limit":100,"includeHidden":true}',
    )

    @classmethod
    def is_available(cls) -> bool:
        return shutil.which("codex") is not None

    async def complete(
        self,
        prompt: str,
        system: str,
        config: ModelConfig,
        *,
        universe_dir: Path | None = None,
    ) -> ProviderResponse:
        # Local codex-cli 0.159.0-alpha.3 exec help documents individual
        # feature/sandbox switches, not a verified all-tools-off contract.
        # Until that contract is proven, reviews never reach env/argv/spawn.
        self.require_text_only_support(config)
        full_input = f"{system}\n\n{prompt}" if system else prompt

        base_cmd, use_shell = self.native_command_resolver()
        model = _codex_model() if config.native_model_id is None else config.native_model_id
        sandbox_status = get_sandbox_status()
        # Our provider jail (tinyassets.providers.provider_jail) is the sandbox
        # whenever this launch is confined. codex's OWN workspace-write sandbox
        # is a nested bubblewrap inside ours: it adds no confinement our jail
        # does not already give (the universe RW, nothing else writable, no
        # network off the egress proxy), and a nested bwrap is what forced the
        # jail's seccomp to keep user namespaces and symlinks open. So drop it
        # and let codex run its commands directly in our jail. Off the jail (a
        # host-authority call with no owning universe) codex keeps its own
        # sandbox, falling back to bypass only where bwrap is unavailable.
        # A served turn (sandbox_workspace) replaces these arguments below and
        # keeps its own sandbox: apply_patch needs it.
        if provider_jail.launch_is_confined():
            sandbox_args = ["--dangerously-bypass-approvals-and-sandbox"]
        else:
            sandbox_args = (
                ["--sandbox", "workspace-write"] if sandbox_status.get("bwrap_available")
                else ["--dangerously-bypass-approvals-and-sandbox"]
            )
        # Prompt-node calls use Codex as a subscription-backed text model, but
        # loop-investigation coding prompts still need repo source/tests mounted.
        # Prefer Codex's sandboxed auto mode when bwrap is actually usable;
        # bwrap-less hosts fall back to the hosted subscription mode already
        # used by auto-fix, with API keys stripped.
        # Per-node effort (real Codex setting, not a prompt hint): when the
        # branch node declares config.reasoning_effort, override Codex's
        # model_reasoning_effort so a light node (e.g. localize) runs minimal/
        # low and finishes fast+cheap instead of deep-reasoning a trivial task.
        effort_args = _reasoning_effort_args(
            getattr(config, "reasoning_effort", "")
        )
        proc_env = subprocess_env_for_provider(
            self.name,
            universe_dir=universe_dir,
            credential_snapshot_dir=config.credential_snapshot_dir,
        )
        machine_accounting = bool(config.sandbox_workspace)
        if config.sandbox_workspace:
            if universe_dir is None or use_shell or not sandbox_status.get("bwrap_available"):
                raise ProviderError(
                    "codex served turns require the OS sandbox; refusing unconfined launch"
                )
            bwrap_path = str(sandbox_status.get("bwrap_path") or shutil.which("bwrap") or "")
            codex_home = Path(proc_env.get("CODEX_HOME", "")).resolve(strict=False)
            universe_root = universe_dir.resolve(strict=False)
            try:
                codex_home.relative_to(universe_root)
            except ValueError as exc:
                raise ProviderError("codex auth home is outside the served command center") from exc
            if not bwrap_path or not codex_home.is_dir():
                raise ProviderError(
                    "codex served turns require an available OS sandbox and command center auth"
                )
            sandbox_args = [
                "--sandbox",
                "workspace-write",
                "--ignore-user-config",
                "--ignore-rules",
                "--disable",
                "shell_tool",
                # Legacy compatibility flag; modern CLI gates all command
                # tools on shell_tool (unified_exec is now always enabled).
                "--disable",
                "unified_exec",
                # MCP surface for the served turn (see _codex_engine_mcp_args):
                # forces `/workspace` untrusted so no project `.codex/config.toml`
                # loads, then — when the founder-scoped engine MCP is on + its
                # per-universe HTTP server is running — wires codex to that ONE
                # trusted server (commons + own-universe handles, restricted to
                # enabled_tools, bearer via env), else no server (WebFetch-only).
                # With `--ignore-user-config` + untrusted workspace, that server is
                # the turn's only mcp_servers source. Account ChatGPT connectors
                # are removed separately by `--disable apps` below.
                *_codex_engine_mcp_args(config, proc_env),
                "-c",
                'web_search="cached"',
                "--json",
            ]
        from tinyassets.providers.native_model_selection import native_model_arguments

        model_args = native_model_arguments(model, "-m")
        cmd = [
            *base_cmd,
            "exec",
            *model_args,
            *effort_args,
            *sandbox_args,
            # Disable the `apps` feature (codex >= 0.135 default: stable/on) on
            # EVERY codex launch — served and non-served alike. It exposes the
            # subscription account's installed ChatGPT connectors — including
            # TinyAssets' OWN /mcp connector — to the model as `codex_apps` MCP
            # tools. `--ignore-user-config` does NOT strip these: they are
            # account/cloud-side, not config.toml. Seeing its own persona prompt,
            # the served universe intelligence "relays" the turn back through the
            # tinyassets_converse/write_graph app tool, which needs a fresh
            # ChatGPT-side OAuth and returns "This app connection requires
            # reauthentication..." — a confused-deputy loop that intermittently
            # replaced the real reply (live-diagnosed 2026-08-22, raw codex --json
            # showed the codex_apps tool call). No codex turn — served text,
            # served code, or the non-served auto-fix path — is ever a legitimate
            # client of the account's connectors. codex rejects unknown feature
            # names, so this fails closed on any future version that renames it.
            # Disabling apps also removes the codex_apps MCP server upstream, so
            # `enable_mcp_apps` / `apps_mcp_path_override` cannot resurrect it.
            "--disable",
            "apps",
            # Remote plugin sync became default-on in CLI 0.153.4. This
            # provider consumes only the engine's explicit MCP surface, never
            # account/local plugin tools or their injected instructions.
            "--disable",
            "plugins",
            "--disable",
            "remote_plugin",
            "--skip-git-repo-check",
        ]

        # A served turn that names a session continues it (change
        # `universe-agent-harness`, S1): its native session files persist under
        # the universe's `.runtime/`, and a resumable one is resumed with only
        # the input it has not seen. Anything else stays `--ephemeral`, as every
        # launch was before. The session is held for the whole launch, so a
        # second concurrent turn of the same key runs unrecorded instead of
        # interleaving one native history.
        session_ref = getattr(config, "agent_session", None) if config.sandbox_workspace else None
        session_hold = contextlib.ExitStack()
        persist = False
        resume_record: dict | None = None
        session_store: Path | None = None
        if session_ref is not None:
            persist = session_hold.enter_context(agent_sessions.exclusive(session_ref))
        if persist:
            session_store = agent_sessions.native_store(universe_root, self.name)
            resume_record = agent_sessions.resumable(
                session_ref, adapter=self.name, model=model or "", prompt=prompt,
            )
            if resume_record is not None and not _native_session_exists(
                session_store, str(resume_record["handle"]),
            ):
                logger.warning("native session for %s is gone; starting a new one", session_ref.key)
                resume_record = None
            if resume_record is not None:
                full_input = agent_sessions.resume_input(session_ref, resume_record, system)
        if not persist:
            cmd.append("--ephemeral")

        universe_view: UniverseView | None = None
        cell_view: CellView | None = None
        if config.sandbox_workspace:
            launch_cmd = [*cmd, "-C", "/workspace"]
            if resume_record is not None:
                launch_cmd += ["resume", str(resume_record["handle"]), "-"]
            # A converse/chat turn is NOT a coding task: give codex an EMPTY
            # scratch /workspace (tmpfs) inside the same jail instead of the
            # universe, so it answers as a chat model rather than acting as a
            # code agent on the mounted files (live 2026-08-22: served converse
            # replied with persona-echo / "reauthentication" while hosted-mode
            # codex chatted + recalled memory correctly). Coding turns
            # (run_graph etc.) keep the read-only universe workspace.
            sandbox_chat = getattr(config, "sandbox_chat", False)
            workspace_mount = (
                JailMount("tmpfs", "/workspace")
                if sandbox_chat
                else JailMount("ro-bind", "/workspace", universe_root)
            )
            # The universe agent's own workspace (harness W2) is masked here as
            # in every provider launch: what its agent writes there never
            # reaches a provider's view (gpt-6-astra on #4194, round 2).
            workspace_masks: tuple[JailMount, ...] = ()
            if not sandbox_chat:
                from tinyassets.providers.provider_jail import (
                    AGENT_WORKSPACE_DIR,
                    ensure_agent_workspace,
                )

                ensure_agent_workspace(universe_root)
                workspace_masks = (
                    JailMount("tmpfs", f"/workspace/{AGENT_WORKSPACE_DIR}"),
                )
            # This adapter's own view of its universe inside the shared jail
            # (tinyassets.providers.provider_jail): narrower than the default,
            # never wider -- every bind below comes from inside universe_root.
            universe_view = UniverseView(
                universe_dir=universe_root,
                mounts=(
                    workspace_mount,
                    *workspace_masks,
                    JailMount(
                        "tmpfs", "/workspace/.runtime/provider-launch-credentials",
                    ),
                    # CODEX_HOME is a private tmpfs with the snapshot's credential
                    # FILES bound read-only into it: codex >= 0.135's launcher
                    # takes `flock $CODEX_HOME/.lock` before starting, so a
                    # read-only home dir died instantly ("cannot open lock file
                    # /codex-home/.lock: Read-only file system", exit 73 in 56 ms
                    # -> "codex exhausted", live 2026-08-22). The credential bytes
                    # stay immutable; only scratch files can be created beside them.
                    JailMount("tmpfs", _JAIL_HOME),
                    *_codex_home_file_mounts(codex_home),
                    *(
                        (JailMount("bind", f"{_JAIL_HOME}/sessions", session_store),)
                        if session_store is not None else ()
                    ),
                ),
                chdir="/workspace",
                setenv=(("CODEX_HOME", _JAIL_HOME), ("HOME", "/tmp")),
            )
            # The same launch in its owner's provider-exec cell (D88): the
            # snapshot copy is CODEX_HOME there and its `sessions` is the
            # owner's own store. A chat turn keeps scratch.
            cell_view = CellView(
                persistent=not sandbox_chat,
                home=next(name for name, value in universe_view.setenv if value == _JAIL_HOME),
            )
            proc_env["CODEX_HOME"] = _JAIL_HOME
            proc_env["HOME"] = "/tmp"
        else:
            # A universe's call runs in that universe (the shared jail binds
            # nothing else); only a host call keeps the source checkout.
            workdir = str(universe_dir) if universe_dir is not None else _codex_workdir()
            launch_cmd = [*cmd, "-C", workdir]
            # In an owner cell (D88) that universe is the owner's persistent
            # workspace; CODEX_HOME already names the sealed snapshot.
            cell_view = CellView(persistent=universe_dir is not None)
        # Spawn as an owned FAMILY: on POSIX a live anchor holds the group id
        # so teardown reaches what the CLI starts without ever naming a group
        # integer that could have been recycled. Fails closed if it cannot.
        # The shared spawn point jails every launch made for a universe; this
        # adapter only names where its own install lives (the wrapper script
        # execs a binary the generic command lookup cannot see).
        started_at = time.time()
        try:
            proc = await aspawn_owned(
                launch_cmd,
                shell=use_shell,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=_STDOUT_READER_LIMIT,
                env=proc_env,
                universe_view=universe_view,
                cell_view=cell_view,
                install_mounts=lambda: self.native_install_mounts(base_cmd),
                # A served turn keeps codex's own --sandbox workspace-write: its
                # native apply_patch runs through a filesystem sandbox helper
                # that needs a nested user namespace, so the jail loads its
                # permissive seccomp profile for it. A non-served call runs
                # with its sandbox off and gets the full deny profile.
                nested_sandbox=bool(config.sandbox_workspace),
            )
        except BaseException:
            session_hold.close()
            raise
        session_saved = False

        # EVERY exit -- success, classified raise, cancellation -- ends the
        # owned family. The clean-exit path never reaped anything before, so
        # a descendant the CLI left behind outlived each successful turn,
        # along with the anchor and its control descriptor.
        try:
            start = time.monotonic()

            try:
                if machine_accounting:
                    # `--json` is on this path only, so ONLY this path has protocol
                    # events to watch. Streamed under the idle-watchdog profile,
                    # like claude: a progressing turn is never killed by a wall
                    # clock, a hung one ends in ~30s. See _stream_codex_exec.
                    stdout, stderr = await _stream_codex_exec(
                        proc, full_input.encode("utf-8"), config, start=start,
                    )
                else:
                    # Plain-text stdout, no events to reset a watchdog on: the
                    # legacy total timeout stays exactly as it was. Streaming this
                    # path killed every long non-served call on the 10s init
                    # budget (Codex round 2, P0 - reproduced against the real CLI).
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(input=full_input.encode("utf-8")),
                        timeout=config.timeout,
                    )
            except asyncio.TimeoutError:
                _terminate(proc)
                await proc.wait()
                raise ProviderTimeoutError(
                    f"codex exec exceeded {config.timeout}s timeout"
                )
            except BaseException:
                # Every OTHER way out — cancellation, shutdown, an unexpected error in
                # communicate() — used to leave the subprocess running. Cross-family review
                # reproduced it: cancelling an in-flight call gave
                # `{'slot_live': 0, 'subprocess_killed': False}`. The admission slot was
                # returned while the ~189 MB process it was accounting for was still alive,
                # so the bound would drift further from reality with every cancellation
                # until the box ran out of memory it believed was free.
                #
                # BaseException, not Exception: `asyncio.CancelledError` derives from
                # BaseException, and cancellation is the case that actually happens.
                _terminate(proc)
                with contextlib.suppress(Exception):
                    await proc.wait()
                raise

            elapsed_ms = (time.monotonic() - start) * 1000

            stderr_text = stderr.decode("utf-8", errors="replace")
            failure_excerpt = _structured_failure_excerpt(
                stdout, stderr_text, machine=machine_accounting,
            )
            # Sandbox failures are classified FIRST: they are a host defect, not a
            # provider outage, and must surface as such instead of being folded
            # into a "likely unavailable" cooldown (how the 2026-08-21 outage hid).
            check_bwrap_failure(stderr_text)
            # The protocol's word beats the exit code: a stream that carries
            # ``turn.completed`` is a finished turn whatever the process did in its
            # teardown (see _codex_turn_completed). Only the --json path has it.
            if machine_accounting and proc.returncode != 0 and _codex_turn_completed(stdout):
                logger.warning(
                    "codex exec exit %s after turn.completed; keeping the finished turn",
                    proc.returncode,
                )
            # A finished sign-in is classified BEFORE the exit-code heuristics: it
            # exits 1 quickly like an outage does, and reading it as one cost the
            # turn. Checked on EVERY non-zero exit, not only the quick one, because
            # the CLI can also spend time failing to refresh before it gives up.
            elif proc.returncode != 0 and _terminal_auth_failure(failure_excerpt):
                raise ProviderAuthenticationError(
                    "the stored sign-in is no longer accepted: " + failure_excerpt
                )
            # Quick exit-code-1 => provider unavailable (same heuristic as claude).
            # Carry a REDACTED excerpt of codex's own words so the real cause is
            # visible; never raw stderr (it can carry token material).
            elif proc.returncode == 1 and elapsed_ms < 5000:
                raise ProviderUnavailableError(
                    "codex exec returned exit code 1 quickly -- likely unavailable: "
                    + failure_excerpt
                )
            elif proc.returncode != 0:
                raise ProviderError(
                    f"codex exec exit {proc.returncode}{disk_stop_note(proc)}: "
                    f"{failure_excerpt}"
                )

            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            input_tokens = None
            output_tokens = None
            cost_microunits = None
            configured_model = model
            if machine_accounting:
                messages: list[str] = []
                usage: dict[str, object] | None = None
                thread_id = ""
                try:
                    events = [json.loads(line) for line in stdout_text.splitlines() if line.strip()]
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ProviderError("codex returned invalid accounting output") from exc
                for event in events:
                    if not isinstance(event, dict):
                        raise ProviderError("codex returned invalid accounting output")
                    if event.get("type") == "thread.started" and isinstance(
                        event.get("thread_id"), str
                    ):
                        thread_id = event["thread_id"]
                    item = event.get("item")
                    if (
                        event.get("type") == "item.completed"
                        and isinstance(item, dict)
                        and item.get("type") == "agent_message"
                        and isinstance(item.get("text"), str)
                    ):
                        messages.append(item["text"])
                    if event.get("type") == "turn.completed" and isinstance(
                        event.get("usage"), dict
                    ):
                        usage = event["usage"]
                if not messages or usage is None:
                    raise ProviderError("codex accounting output omitted result or usage")
                try:
                    input_tokens = int(usage["input_tokens"])
                    output_tokens = int(usage["output_tokens"]) + int(
                        usage.get("reasoning_output_tokens", 0)
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ProviderError("codex accounting output contained invalid usage") from exc
                if input_tokens < 0 or output_tokens < 0:
                    raise ProviderError("codex accounting output contained invalid usage")
                cost_microunits = (input_tokens + output_tokens) * 100
                text = messages[-1].strip()
                if session_store is not None and thread_id:
                    configured_model = _configured_rollout_model(
                        universe_root, session_store, thread_id, started_at,
                    ) or model
                if persist and thread_id:
                    agent_sessions.save(
                        session_ref, adapter=self.name, model=model or "",
                        handle=thread_id, system=system,
                    )
                    session_saved = True
            else:
                text = stdout_text

            if not text:
                # codex v0.122+ exits 0 on auth failure (401) but emits nothing to
                # stdout. Detect the silent-auth-failure pattern and surface it as a
                # hard error rather than returning an empty response that cascades
                # silently through downstream nodes.
                _auth_patterns = ("401", "Unauthorized", "Reconnecting", "auth")
                stderr_lower = stderr_text.lower()
                if any(p.lower() in stderr_lower for p in _auth_patterns):
                    excerpt = stderr_text[:300].strip()
                    raise ProviderError(
                        f"codex returned empty stdout with auth-error signal in stderr "
                        f"(exit={proc.returncode}): {excerpt}"
                    )
                raise ProviderError(
                    f"codex returned empty response (exit={proc.returncode}); "
                    f"stderr: {stderr_text[:200].strip() or '(empty)'}"
                )

            from tinyassets.providers.agent_capacity_boundary import NativeCompletionEvidence

            return ProviderResponse(
                text=text,
                provider=self.name,
                # CLI 0.153.4 drops model verification from exec's JSONL. The
                # rollout / -m value is labelled configuration, never actual
                # answering-model evidence. Ephemeral defaults remain unknown.
                model=model or "provider-default",
                requested_model=model,
                configured_model=configured_model,
                family=self.family,
                latency_ms=elapsed_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_microunits=cost_microunits,
                # JSONL intermediate tool events are best-effort. Even a recognized
                # successful terminal proves no absence of earlier internal effects.
                native_evidence=NativeCompletionEvidence(
                    self.name, False, type(proc.returncode) is int, "unknown",
                ) if machine_accounting else None,
            )
        finally:
            kill_owned_tree(proc)
            if resume_record is not None and not session_saved:
                # A resumed launch that did not finish leaves no claim that the
                # session is healthy: the next turn starts a new one rather than
                # resuming into the same failure.
                logger.warning("native session %s did not complete; next turn starts fresh",
                               session_ref.key)
                agent_sessions.clear(session_ref)
            session_hold.close()
