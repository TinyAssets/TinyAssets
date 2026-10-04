"""Claude provider -- ``claude -p`` subprocess.

Covered by the Claude Max subscription.  No API credits consumed.

``complete`` (the served interactive writer path) STREAMS the CLI with
``--output-format stream-json --verbose --include-partial-messages``: it reads
stdout NDJSON line-by-line, drains stderr concurrently, and judges liveness by
an idle watchdog that resets only on a real protocol event — never on a single
total wall-clock deadline. A progressing turn is never failed for elapsed time;
a genuinely idle turn is ended in ~30s (``provider_idle_timeout``) instead of
300s, and reaching the absolute cap is an ``interactive_deadline`` — neither
cools the provider. Exit code 1 within 5 seconds still signals API
unavailability (sticky cooldown). ``complete_json`` remains a blocking
non-streaming call for structured output.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from tinyassets.exceptions import (
    InteractiveDeadlineError,
    ProviderAuthenticationError,
    ProviderAuthorityHeldError,
    ProviderError,
    ProviderIdleTimeoutError,
    ProviderOverloadedError,
    ProviderProtocolError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from tinyassets.providers.base import (
    BaseProvider,
    ModelConfig,
    ProviderResponse,
    check_bwrap_failure,
    subprocess_env_for_provider,
)
from tinyassets.providers.owned_process import (
    akill_owned_tree,
    aspawn_owned,
    disk_stop_note,
    kill_owned_tree,
    no_window_kwargs,
)
from tinyassets.providers.protocol_encoders import model_receipt

logger = logging.getLogger(__name__)

# SDKAssistantMessage.error is an enum, not the free-text message.content.
# https://code.claude.com/docs/en/agent-sdk/typescript (September 15, 2026).
# Unknown future values remain usable protocol events but never enter logs raw.
_ASSISTANT_ERROR_CATEGORIES = frozenset({
    "authentication_failed", "oauth_org_not_allowed", "billing_error", "rate_limit",
    "overloaded", "invalid_request", "model_not_found", "server_error",
    "max_output_tokens", "unknown",
})


def _terminal_error_flag(terminal: dict | None) -> str:
    if terminal is None or "is_error" not in terminal:
        return "absent"
    value = terminal["is_error"]
    return ("true" if value else "false") if type(value) is bool else "non_boolean"

# Windows-specific crash codes: treat as unavailable so the router applies a
# cooldown instead of retrying immediately.
#   0xC0000374 (3221225588) = heap corruption
#   0xC0000005 (3221225477) = access violation
#   0xC000013A (3221225786) = control-C / abnormal termination
_WINDOWS_CRASH_CODES = frozenset({3221225588, 3221225477, 3221225786})

# Generous stdout reader buffer so a large single JSON `result` line does not
# trip the default 64 KiB StreamReader limit (which would read as a broken
# stream). Chat replies are far under this; an over-limit line is a real
# protocol fault.
_STDOUT_READER_LIMIT = 2 ** 22  # 4 MiB

# Margin added to a provider-stated retry_delay when extending the idle budget
# to cover a documented retry wait (blocker B): the CLI needs a little slack
# beyond its own stated wait to re-issue the request and resume streaming.
_RETRY_GRACE_MARGIN_S = 5.0

#: How long the turn may wait on ONE identified native tool before that wait
#: counts as idle. An identified tool start without its matching result is the
#: provider WORKING, not the model gone silent — the ordinary idle interval
#: killed healthy tool calls. Same bound as the codex reader's ``_TOOL_WAIT_S``
#: for the same reason: generous enough for any tool a served turn launches
#: today, short enough that a wedged tool still ends. The absolute cap is never
#: relaxed, so this is always taken as ``min(absolute cap, _TOOL_WAIT_S)``.
#: No provider tool-heartbeat cadence is assumed — none is documented.
_TOOL_WAIT_S = 900.0

#: ``system/status`` values the CLI DOCUMENTS as "busy until cleared"
#: (``@anthropic-ai/claude-agent-sdk`` ``SDKStatus = 'compacting' |
#: 'requesting' | null``). Only a value in this set opens a declared-busy
#: allowance; every other status string, an unknown frame type, or free text
#: that merely mentions compaction is ordinary liveness (one watchdog reset, no
#: window). ``requesting`` is deliberately absent: a request normally produces
#: stream framing within seconds and nothing documents a long silent window
#: for it — add it only on separate evidence.
_DECLARED_BUSY_STATES: frozenset[str] = frozenset({"compacting"})

#: How long a DECLARED busy window (``status: "compacting"``) may stay silent
#: before that silence counts as idle. Nothing documents a heartbeat cadence
#: during compaction, so the reader honours the declaration instead of the
#: ordinary idle interval — the same bound shape as ``_TOOL_WAIT_S`` for the
#: same reason, and always taken as ``min(absolute cap, _BUSY_WAIT_S)``: the
#: absolute cap is never relaxed, so a compaction that never clears still ends.
_BUSY_WAIT_S = _TOOL_WAIT_S


def _tool_identity(value: object) -> str | None:
    """The provider's own tool identity, or ``None`` when it is unusable.

    Only a non-empty string counts. A missing or malformed ``id`` /
    ``tool_use_id`` earns NO pending-tool allowance (fail closed): an
    unidentified start can never be paired with its result, so waiting on it
    would be unbounded guessing rather than evidence of work in flight.
    """
    if type(value) is str:
        trimmed = value.strip()
        if trimmed:
            return trimmed
    return None


@dataclass
class _AnswerModelEvidence:
    """Attempt-local, answer-owned metadata; never routing or liveness evidence."""

    message_id: str | None = None
    model: str = ""
    parts: list[str] = field(default_factory=list)

    def observe(self, obj: dict) -> None:
        parent = obj.get("parent_tool_use_id")
        if isinstance(parent, str) and parent.strip() and parent.isprintable():
            return  # A child cannot replace the root answer's evidence.
        message = obj.get("message")
        if ("parent_tool_use_id" not in obj or parent is not None
                or not isinstance(message, dict)):
            self.message_id, self.model, self.parts = None, "", []
            return
        message_id = message.get("id")
        if not isinstance(message_id, str) or not message_id:
            message_id = None
        model = model_receipt(message.get("model"))
        if obj.get("error") is not None or model == "<synthetic>":
            model = ""
        # Claude emits separate assistant frames for blocks of one API message.
        # Only explicitly equal nonempty IDs permit accumulation. Conflicting or
        # missing model evidence taints that message, even if a later block has it.
        if message_id is not None and message_id == self.message_id:
            if model != self.model:
                self.model = ""
        else:
            self.message_id, self.model, self.parts = message_id, model, []
        for block in _content_blocks(message):
            if block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    self.parts.append(text)
                else:
                    self.model = ""

    def for_answer(self, text: str) -> str:
        # Terminal text owns the answer. Init/configuration and aggregate usage
        # cannot prove its model. Drift or incomplete evidence stays unknown.
        return self.model if "".join(self.parts).strip() == text else ""


def _coerce_int(value: object) -> int | None:
    """Return *value* as an int, or ``None`` for non-integers (mock-safe)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _content_blocks(message: object) -> list[dict]:
    """Normalize an Anthropic message ``content`` to a list of block dicts."""
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    return []


def _finite_nonneg(value: object) -> float | None:
    """Return ``value`` as a non-negative float, else ``None`` (bool-safe)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def _extract_api_retry(obj: dict) -> dict:
    """Classify a ``system/api_retry`` event -> {failure_class, retry_after}.

    REAL Claude 2.1.236 schema (Codex-documented, not invented): the CLI emits
    ``error`` (a string, e.g. ``"rate_limit"`` / ``"overloaded"``),
    ``error_status`` (an int HTTP status, e.g. ``429`` / ``529``), and
    ``retry_delay_ms`` (an int). Only rate-limit / overload map to a cooling
    failure class; anything else is a pure liveness signal (the CLI is retrying)
    and only surfaces if the stream never recovers.
    """
    raw = ""
    err = obj.get("error")
    if isinstance(err, str):
        raw = err
    elif isinstance(err, dict) and isinstance(err.get("type"), str):
        # Tolerate a nested ``{type: ...}`` shape defensively; the documented
        # 2.1.236 field is the bare string above.
        raw = err["type"]
    low = raw.lower()
    status = _coerce_int(obj.get("error_status"))
    failure_class: str | None = None
    if "overload" in low or status == 529:
        failure_class = "provider_overloaded"
    elif "rate" in low or status == 429:
        failure_class = "provider_rate_limited"
    ms = _finite_nonneg(obj.get("retry_delay_ms"))
    retry_after = ms / 1000.0 if ms is not None else None
    return {"failure_class": failure_class, "retry_after": retry_after}


def _extract_rate_limit_event(obj: dict, *, now: float | None = None) -> dict:
    """Classify a top-level ``rate_limit_event`` -> {failure_class, retry_after}.

    REAL Claude 2.1.236 schema: ``rate_limit_info.{status, resetsAt,
    rateLimitType, overageStatus}``. ``status == "allowed"`` is INFORMATIONAL —
    the reference trace shows it emitted on a SUCCESSFUL turn — so it is never a
    failure (the caller treats it as a liveness heartbeat). Any other status is
    an active limit; ``retry_after`` is derived from ``resetsAt`` (unix seconds).
    """
    info = obj.get("rate_limit_info")
    if not isinstance(info, dict):
        return {"failure_class": None, "retry_after": None}
    status = str(info.get("status") or "").strip().lower()
    if status in ("", "allowed"):
        return {"failure_class": None, "retry_after": None}
    retry_after: float | None = None
    resets_at = _finite_nonneg(info.get("resetsAt"))
    if resets_at is not None:
        current = time.time() if now is None else now
        delta = resets_at - current
        if delta > 0:
            retry_after = delta
    return {"failure_class": "provider_rate_limited", "retry_after": retry_after}


def _normalize_stream_obj(obj: dict) -> list[tuple[str, dict]]:
    """Collapse one stream-json object to normalized (kind, payload) events.

    RELAY only assistant text (``text_delta``) and the terminal ``result``.
    ``answer_evidence`` is optional internal metadata, never relayed or counted
    as liveness; the stream reader matches it to the final returned text.
    Every OTHER recognized protocol event — reasoning/thinking, hooks, status,
    notification, structural stream framing, ``tool_progress``,
    ``system/tool_heartbeat``, an informational ``rate_limit_event`` — is a
    ``heartbeat``: it proves the CLI is alive and working (verified against a
    real 2.1.236 trace where a reasoning stretch emits ONLY thinking + framing),
    so it resets the idle watchdog, but its content is NEVER relayed. Only
    whitespace / unparseable-suppressed / unknown-but-well-formed types return
    nothing.
    """
    events: list[tuple[str, dict]] = []
    kind = obj.get("type")
    if kind == "system":
        subtype = obj.get("subtype")
        if subtype == "init":
            events.append(("init", {}))
        elif subtype == "api_retry":
            events.append(("api_retry", _extract_api_retry(obj)))
        elif subtype == "status":
            # Published ``SDKStatusMessage``: ``status`` is a documented busy
            # value, or ``null`` when the CLI is done. Only the documented
            # value opens a typed declared-busy lifecycle; an explicit null
            # closes it. A missing key, an undocumented string, or free
            # ``text`` is plain liveness — never a window (fail closed).
            status = obj.get("status")
            if type(status) is str and status in _DECLARED_BUSY_STATES:
                events.append(("declared_busy", {"state": status}))
            elif status is None and "status" in obj:
                events.append(("declared_clear", {}))
            else:
                events.append(("heartbeat", {}))
        elif subtype == "compact_boundary":
            # Published ``SDKCompactBoundaryMessage``: compaction has ended.
            events.append(("declared_clear", {}))
        else:
            # thinking_tokens / notification / hook_started / hook_response /
            # tool_heartbeat / ... — recognized activity.
            events.append(("heartbeat", {}))
    elif kind == "assistant":
        events.append(("answer_evidence", {"obj": obj}))
        has_error = "error" in obj and obj["error"] is not None
        if has_error:
            error = obj["error"]
            category = (error if type(error) is str and error in _ASSISTANT_ERROR_CATEGORIES
                        else "unrecognized")
            events.append(("assistant_error", {"category": category}))
        for block in _content_blocks(obj.get("message")):
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text") or ""
                # Error rendering proves liveness, not useful model output.
                # Never reuse it as the reply after an empty success result.
                if text and not has_error:
                    events.append(("text_delta", {"text": text}))
                else:
                    events.append(("heartbeat", {}))
            elif block_type == "tool_use":
                # ``id`` is the provider's own tool identity; the matching
                # ``tool_result`` references it as ``tool_use_id``. Carried in
                # memory so pending work can be PAIRED rather than guessed from
                # the last tool event seen.
                events.append(
                    ("tool_use", {"name": block.get("name"), "id": block.get("id")})
                )
            else:
                # thinking / redacted_thinking / signature — liveness, never
                # relayed.
                events.append(("heartbeat", {}))
    elif kind == "user":
        for block in _content_blocks(obj.get("message")):
            if block.get("type") == "tool_result":
                events.append(
                    ("tool_result", {"tool_use_id": block.get("tool_use_id")})
                )
            else:
                events.append(("heartbeat", {}))
    elif kind == "stream_event":
        event = obj.get("event")
        if isinstance(event, dict):
            event_type = event.get("type")
            if event_type == "content_block_delta":
                delta = event.get("delta")
                if (
                    isinstance(delta, dict)
                    and delta.get("type") == "text_delta"
                    and (delta.get("text") or "")
                ):
                    events.append(
                        ("text_delta", {"text": delta["text"], "partial": True})
                    )
                else:
                    # thinking_delta / signature_delta / input_json_delta /
                    # empty text_delta -> liveness, never relayed.
                    events.append(("heartbeat", {}))
            elif event_type == "content_block_start":
                cb = event.get("content_block")
                if isinstance(cb, dict) and cb.get("type") == "tool_use":
                    # The partial framing of the SAME tool start, carrying the
                    # same identity. Re-announcing one already in flight must be
                    # idempotent, never a second pending tool.
                    events.append(
                        ("tool_use", {"name": cb.get("name"), "id": cb.get("id")})
                    )
                else:
                    events.append(("heartbeat", {}))
            else:
                # message_start/_delta/_stop, content_block_stop, ping -> liveness
                events.append(("heartbeat", {}))
    elif kind == "result":
        events.append(("result", {"obj": obj}))
    elif kind == "rate_limit_event":
        classified = _extract_rate_limit_event(obj)
        if classified["failure_class"]:
            events.append(("api_retry", classified))
        else:
            # status == "allowed": informational (seen even on success).
            events.append(("heartbeat", {}))
    elif kind == "tool_progress":
        events.append(("heartbeat", {}))
    # unknown well-formed type -> ignored (tolerant; not a protocol error and
    # not counted as liveness — a truly hung process emits nothing at all).
    return events


def _result_is_success(obj: dict) -> bool:
    """Whether a terminal ``result`` event represents a successful turn."""
    if bool(obj.get("is_error")):
        return False
    subtype = str(obj.get("subtype") or "").lower()
    return subtype in ("", "success")


def _no_window_kwargs() -> dict:
    """Return subprocess kwargs to suppress console windows on Windows."""
    return no_window_kwargs()


def _resolve_claude_cmd() -> tuple[list[str], bool]:
    """Resolve the claude command, handling Windows .cmd/.bat wrappers.

    Returns (base_cmd, use_shell) where base_cmd is the command prefix
    and use_shell indicates whether to use shell execution.
    """
    claude_path = shutil.which("claude")
    if claude_path and sys.platform == "win32" and claude_path.lower().endswith((".cmd", ".bat")):
        return [claude_path], True
    return ["claude"], False


def _engine_mcp_flags(config: ModelConfig, universe_dir: Path) -> list[str]:
    """Wire the local, founder-scoped TinyAssets MCP server into the engine turn.

    Founder directive 2026-08-12: the universe agent ("Tiny") gets the SAME MCP
    handles the founder's browser chatbot has. This writes a per-universe
    ``--mcp-config`` pointing at ``python -m tinyassets.engine_mcp_server`` and
    returns the flags that admit EXACTLY that one server:

      * ``--strict-mcp-config`` — grants ONLY the servers in ``--mcp-config`` and
        excludes the logged-in claude.ai account connectors (Google Drive /
        codex → code exec). Verified 2026-08-13: with strict + a single-server
        config, ``mcp__codex__codex`` is unreachable — this is what actually
        closes the 2026-07-03 ambient-MCP leak, not ``--setting-sources``.

    FAIL-CLOSED: the engine MCP is wired only when the founder actor_id AND the
    universe graph_id are both present; a missing either returns no flags so the
    turn stays WebFetch-only rather than exposing tools with an unbound identity.
    The server itself binds ``_current_identity`` to the founder and pins every
    handler to ``engine_mcp_graph_id`` (see ``tinyassets.engine_mcp_server``).
    """
    actor_id = (config.engine_mcp_actor_id or "").strip()
    graph_id = (config.engine_mcp_graph_id or "").strip()
    if not (actor_id and graph_id):
        return []
    import json as _json
    import sys as _sys

    # Config lives under the universe's platform-owned ``.runtime/``, which the
    # universe tool jail masks: the agent's own read/bash tools never see it.
    # HTTP config carries the private bearer; never put this config in the
    # prompt or logs. Overwritten each turn. The pre-harness location at the
    # universe root is removed so no stale bearer stays readable there.
    # One config per session: the route names the launch's session and turn for
    # owner steering (S2), so two sessions' launches must never share a file and
    # read each other's route (gpt-6-astra on #4188).
    from tinyassets.agent_sessions import digest as _session_digest
    from tinyassets.engine_mcp_http import read_engine_mcp_route
    from tinyassets.engine_steering import route_with_session, session_of, turn_of
    from tinyassets.storage import data_dir

    session_key = session_of(config)
    config_path = universe_dir / ".runtime" / (
        f"engine-mcp-config-{_session_digest(session_key)[:16]}.json"
        if session_key else "engine-mcp-config.json"
    )
    legacy_path = universe_dir / ".engine_mcp_config.json"
    server_env = {
        "TINYASSETS_ENGINE_ACTOR_ID": actor_id,
        "TINYASSETS_ENGINE_GRAPH_ID": graph_id,
    }
    root = data_dir()
    server_env["TINYASSETS_DATA_DIR"] = str(root)
    # The ceiling on a single tool result scales with the window the result has
    # to fit in (``engine_result_bounds``). Passed only when this turn's model is
    # known; the persistent HTTP transport below outlives any one turn's choice,
    # so it runs on the safe default instead of a stale number.
    selected = getattr(config, "selected_model", None)
    context_tokens = getattr(selected, "context_tokens", None)
    if type(context_tokens) is int and context_tokens > 0:
        server_env["TINYASSETS_ENGINE_MODEL_CONTEXT_TOKENS"] = str(context_tokens)
    # Transport selection. The claude CLI's STDIO MCP spawn is flaky in the
    # headless served subprocess (verified live 2026-08-19: the server process
    # never launched, CLI reported "still connecting"); HTTP MCP connects
    # reliably. So when a persistent per-universe HTTP engine server is running,
    # point --mcp-config at its loopback URL + inject the per-server bearer
    # secret (Codex gate #6). Falls back to stdio when none is running. The route
    # owner-bound route map is written 0600 by engine_mcp_http; the secret goes
    # in the --mcp-config HEADERS (which the CLI
    # holds internally — never surfaced to the LLM), not the prompt.
    route = read_engine_mcp_route(actor_id=actor_id, graph_id=graph_id, root=root)
    if route is not None:
        mcp_config = {
            "mcpServers": {
                "tinyassets": {
                    "type": "http",
                    # Names this launch's session for owner steering (S2).
                    "url": route_with_session(route.url, session_key, turn_of()),
                    "headers": {"Authorization": "Bearer " + route.secret},
                }
            }
        }
    else:
        mcp_config = {
            "mcpServers": {
                "tinyassets": {
                    "command": _sys.executable,
                    "args": ["-m", "tinyassets.engine_mcp_server"],
                    "env": server_env,
                }
            }
        }
    try:
        config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        config_path.write_text(_json.dumps(mcp_config), encoding="utf-8")
        if legacy_path.is_file() and not legacy_path.is_symlink():
            legacy_path.unlink()
    except OSError:
        # If we cannot write the config, fail closed to WebFetch-only rather than
        # passing --mcp-config a missing path (which would error the whole turn).
        return []
    return ["--mcp-config", str(config_path), "--strict-mcp-config"]


def _confine_workflow_node(config: ModelConfig) -> ModelConfig:
    """Pin a workflow node's call to its owner's universe, host tools denied.

    A workflow node reached this CLI with a bare ``ModelConfig``: no cwd pin,
    so the CLI started in the daemon's working directory (``/app``, the
    platform's own source tree) with its default builtins. Production
    transcripts (2026-09-24) show about half of a probe's "one short prompt"
    nodes turning into explorations of that tree -- dozens of
    ``find``/``grep``/``Read`` calls over ``/app/tinyassets`` and ``PLAN.md``,
    ``Explore`` subagents, ``ls /data`` -- at 5-16K output tokens and 100-300s,
    against 0.3-1.4K tokens and 10-25s when the model used no tools. The
    cross-user floor itself is the OS jail the shared spawn point puts every
    universe launch in (``provider_jail``); this narrows the node further.

    The served universe turn already runs this way (``sandbox_workspace``);
    this applies the existing rule to workflow nodes. Web tools, subagents and
    every other owner-level capability are untouched: only
    :data:`HOST_REACH_TOOLS` and :data:`ACCOUNT_REACH_TOOLS` are denied. No
    timeout, cap or retry changes.

    ``ACCOUNT_REACH_TOOLS`` is denied for a different reason than the host
    tools, not as latency control: those act on the DAEMON HOST'S claude.ai
    account, so no amount of jailing or ``--strict-mcp-config`` contains them,
    and a node that published an artifact or messaged another session would be
    reaching outside its owner's command center entirely. The engine turn
    already denied them; a node did not, which is the gap this closes.
    """
    from dataclasses import replace

    from tinyassets.providers.base import ACCOUNT_REACH_TOOLS, HOST_REACH_TOOLS

    denied = tuple(dict.fromkeys((
        *(config.disallowed_tools or ()), *HOST_REACH_TOOLS, *ACCOUNT_REACH_TOOLS,
    )))
    return replace(config, sandbox_workspace=True, disallowed_tools=denied)


def _sandbox_cli_args(
    config: ModelConfig, universe_dir: Path | None
) -> tuple[list[str], str | None]:
    """Build tool-policy flags + isolated cwd for a sandboxed subprocess turn.

    Returns ``(extra_cmd_flags, run_cwd)``. This is the P0 isolation seam for the
    founder-facing universe-intelligence turn (2026-07-03 live-test finding): the
    universe engine must NOT inherit the daemon's checkout (repo source,
    ``CLAUDE.md``, other universes) nor keep host tools (Bash → arbitrary host
    commands / clone / gh). ``--disallowedTools`` is the hard floor that denies
    shell escape even if a settings file would grant it; ``run_cwd`` pins the
    subprocess to the universe's own dir. Both are no-ops for host-trusted roles
    that leave the config fields at their defaults.
    """
    # Empty allowed_tools is not a CLI deny-all: it emits no flag below.
    # No verified native tool-free contract is enabled for this adapter.
    if config.text_only:
        raise ProviderAuthorityHeldError(
            "native tool configuration does not support enforced text-only review"
        )
    flags: list[str] = []
    if config.workflow_node:
        config = _confine_workflow_node(config)
    if config.sandbox_workspace:
        # Load ONLY project-tier settings. A universe dir is bare, so this loads
        # NOTHING — critically it excludes the USER's global settings, which carry
        # MCP servers and `bypassPermissions`. Without it, the sandboxed engine
        # still inherits the user's MCP tools (verified 2026-07-03: it saw
        # `mcp__codex__codex`), so a founder's universe could call e.g. Codex →
        # arbitrary code execution, fully bypassing the Bash deny. This strips all
        # ambient MCP + config from the founder-facing turn.
        flags += ["--setting-sources", "project"]
        # Decide the permission mode; never inherit it (Codex ADAPT 2026-10-03,
        # CLI 2.1.288 review). 2.1.285 starts `claude -p` in AUTO mode when no
        # mode is configured on third-party providers or with telemetry off, so
        # an unspecified mode is now an upstream-owned variable that could begin
        # auto-approving tools this turn never pre-approved. `default` (accepted
        # alongside its newer name `manual`) approves NOTHING implicitly: the
        # only callable tools are the ones `--allowedTools` pre-approves --
        # WebFetch plus, when engine MCP is on, the declared
        # `mcp__tinyassets__*` handles. A headless turn cannot answer a prompt,
        # so anything else is refused rather than waiting. This pins the
        # behaviour this provider already had with first-party OAuth; it is
        # stated so an upstream default change cannot move it.
        #
        # Deliberately confined-turn only (`sandbox_workspace`, which
        # `_confine_workflow_node` also sets): host-trusted roles that leave the
        # tool fields at their defaults keep whatever mode they run today.
        flags += ["--permission-mode", "default"]
    allowed = config.allowed_tools
    disallowed = config.disallowed_tools
    # ``--allowedTools``/``--disallowedTools`` are variadic (<tools...>): each
    # tool is its OWN argv token, not one space-joined string (a joined string is
    # read as a single bogus tool name and silently matches nothing).
    if allowed:
        flags += ["--allowedTools", *allowed]
    if disallowed:
        flags += ["--disallowedTools", *disallowed]
    # Fail-closed (2026-07-03 P0 review, Codex ADAPT): a sandboxed turn with no
    # universe_dir would inherit the daemon's cwd (the checkout) — the exact leak
    # this fixes. Refuse rather than silently run un-isolated.
    if config.sandbox_workspace and universe_dir is None:
        raise ProviderError(
            "sandboxed command center turn requires a universe_dir — refusing to run "
            "un-isolated in the daemon's working directory (fail-closed)."
        )
    # Founder-scoped engine MCP: only inside the sandbox (universe_dir present),
    # and only when the caller opted in with a bound founder + universe. The
    # matching ``mcp__tinyassets__*`` handles are added to ``allowed_tools`` by
    # the caller (universe_intelligence._sandboxed_config).
    if config.engine_mcp_enabled and universe_dir is not None:
        engine_flags = _engine_mcp_flags(config, universe_dir)
        # FAIL CLOSED (Codex ADAPT 2026-08-13 #1): when engine MCP is on, the
        # caller has ALREADY relaxed the tool policy — it dropped the ``mcp__*``
        # wildcard deny so the tinyassets handles are admittable, trusting that
        # ``--strict-mcp-config`` will exclude every OTHER (ambient account)
        # connector. If the config could not be written (``_engine_mcp_flags``
        # returned no ``--strict-mcp-config``), running anyway would fail OPEN —
        # ambient connectors would load with neither the wildcard deny NOR strict
        # mode. Refuse the turn instead of silently downgrading isolation.
        if "--strict-mcp-config" not in engine_flags:
            raise ProviderError(
                "engine MCP was requested but --strict-mcp-config could not be "
                "installed; refusing to run with a relaxed tool policy that would "
                "expose ambient MCP connectors (fail-closed)."
            )
        flags += engine_flags
    run_cwd = str(universe_dir) if config.sandbox_workspace else None
    return flags, run_cwd


#: Flags that make the CLI answer a metadata request and nothing else: print
#: mode, a typed control stream both ways, and no session written to disk for a
#: read that is not a conversation. ``--verbose`` is required by the CLI
#: alongside ``--output-format stream-json`` in print mode.
#:
#: Deliberately NOT ``--bare``: it documents that "Anthropic auth is strictly
#: ANTHROPIC_API_KEY or apiKeyHelper (OAuth and keychain are never read)", which
#: would make a subscription-backed catalogue read fail as unauthenticated.
_METADATA_ARGUMENTS = (
    "-p",
    "--input-format", "stream-json",
    "--output-format", "stream-json",
    "--verbose",
    "--no-session-persistence",
)


def _effort_args(effort: str | None) -> list[str]:
    """Map a generic ModelConfig.reasoning_effort to Claude Code's own flag.

    The CLI's real setting is ``--effort <low|medium|high|xhigh|max>``. An empty
    value yields no flag, which leaves the CLI on its own default rather than
    this platform choosing one. The level is NOT validated against a hardcoded
    set here: the admitted set is whatever the model advertised through
    discovery, and the caller has already held the choice to it.
    """
    normalized = (effort or "").strip().lower()
    if not normalized:
        return []
    return ["--effort", normalized]


class ClaudeProvider(BaseProvider):
    """Calls Claude via the ``claude -p`` CLI binary."""

    agent_execution_kind = "native_agent"

    name = "claude-code"
    family = "anthropic"
    native_credential_service = "claude"
    native_command_resolver = staticmethod(lambda: _resolve_claude_cmd())
    native_process_options = staticmethod(_no_window_kwargs)
    native_metadata_arguments = _METADATA_ARGUMENTS
    from tinyassets.providers.native_jsonrpc_discovery import NativeControlProtocol

    #: The CLI's own selectable catalogue, which is what makes a newly released
    #: model a normal choice without a platform release. Before this, Claude
    #: declared custody but no metadata protocol, so enumeration returned None
    #: and the picker fell back to the reviewed static list -- where every row
    #: is a candidate to GRANT, so the founder's current flagship read
    #: "model access opt-in required" instead of being selectable (2026-10-02).
    #:
    #: Keyed on ``resolvedModel``, not ``value``: the alias rows (``opus``,
    #: ``default``) resolve differently between CLI versions, which is exactly
    #: how "claude-code · opus" came to mean an older model than the owner
    #: expected. Storing the resolved id makes a saved preference mean one thing.
    #: The default is the distinguished ``default`` alias row.
    native_discovery_protocol = NativeControlProtocol(
        list_method="list_models", items_key="models", model_key="resolvedModel",
        default_key="value", default_match_value="default",
        modalities_key="inputModalities", hidden_key="disabled",
        effort_key="supportsEffort", effort_levels_key="supportedEffortLevels",
        # This metadata contract is text-in/text-out and reports no modalities.
        # Unreported must not read as "accepts nothing", which would make every
        # enumerated row fail the text requirement and vanish from the picker.
        assumed_input_modalities=frozenset({"text"}),
        # Production pins an older CLI than the one that added `list_models`
        # (2.1.183 today; #4351 moves it to 2.1.288). An older build answers
        # "Unsupported control request subtype: list_models" in 0.6s, measured
        # 2026-10-02, and this marker turns that into the honest
        # `native_enumeration_unsupported` instead of a broken-source reason.
        # The provider default and the reviewed list keep working meanwhile, so
        # the fallback is the pre-change behaviour exactly.
        unsupported_error_marker="unsupported control request",
        list_params_json="{}",
    )

    @classmethod
    def is_available(cls) -> bool:
        return shutil.which("claude") is not None

    async def complete(
        self,
        prompt: str,
        system: str,
        config: ModelConfig,
        *,
        universe_dir: Path | None = None,
    ) -> ProviderResponse:
        """Stream a served interactive turn, judged by an idle watchdog.

        Returns a terminal :class:`ProviderResponse` on success. Raises a
        classified provider exception on failure (``ProviderIdleTimeoutError`` /
        ``InteractiveDeadlineError`` / ``ProviderRateLimitedError`` /
        ``ProviderOverloadedError`` / ``ProviderProtocolError`` /
        ``ProviderUnavailableError`` / ``ProviderError``).
        """
        self.require_text_only_support(config)
        base_cmd, use_shell = self.native_command_resolver()
        from tinyassets.providers.native_model_selection import native_model_arguments

        cmd = [
            *base_cmd, "-p",
            *native_model_arguments(config.native_model_id, "--model"),
            # The owner's per-model effort choice, carried as the CLI's real
            # setting rather than a prompt hint. Empty leaves the CLI default.
            *_effort_args(getattr(config, "reasoning_effort", "")),
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
        ]
        if system:
            cmd.extend(["--system-prompt", system])
        extra_flags, run_cwd = _sandbox_cli_args(config, universe_dir)
        cmd.extend(extra_flags)
        proc_env = subprocess_env_for_provider(
            self.name,
            universe_dir=universe_dir,
            credential_snapshot_dir=config.credential_snapshot_dir,
        )
        # Spawn as an owned FAMILY: on POSIX a live anchor holds the group id
        # so teardown reaches what the CLI starts without ever naming a group
        # integer that could have been recycled. Fails closed if it cannot.
        proc = await aspawn_owned(
            cmd,
            shell=use_shell,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
            cwd=run_cwd,
            limit=_STDOUT_READER_LIMIT,
        )
        return await self._read_stream(proc, prompt, config)

    @staticmethod
    async def _terminate(proc) -> None:
        """Kill and reap a subprocess tree, tolerating an already-dead process.

        Targets only the group recorded for this process at spawn; a process we
        did not spawn (a test double, an externally supplied handle) is killed
        individually exactly as before.
        """
        await akill_owned_tree(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:  # noqa: BLE001 - reap best-effort
            pass

    @staticmethod
    def _parse_line(raw: bytes) -> tuple[list[tuple[str, dict]], bool]:
        """Parse one stdout line -> (normalized events, malformed?).

        Whitespace-only lines are ignored (no events, not malformed). A
        non-whitespace line that is not a JSON object is malformed (fail loud).
        """
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ([], True)
        stripped = text.strip()
        if not stripped:
            return ([], False)
        try:
            obj = json.loads(stripped)
        except (ValueError, TypeError):
            return ([], True)
        if not isinstance(obj, dict):
            return ([], True)
        return (_normalize_stream_obj(obj), False)

    async def _read_stream(
        self, proc, prompt: str, config: ModelConfig,
    ) -> ProviderResponse:
        profile = config.stream_timeout_profile()
        start = time.monotonic()
        stderr_chunks: list[bytes] = []

        async def _drain_stderr() -> None:
            try:
                while True:
                    chunk = await proc.stderr.read(4096)
                    if not isinstance(chunk, (bytes, bytearray)) or not chunk:
                        break
                    stderr_chunks.append(bytes(chunk))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - stderr drain must never break a turn
                return

        async def _feed_stdin() -> None:
            try:
                if proc.stdin is not None:
                    proc.stdin.write(prompt.encode("utf-8"))
                    await proc.stdin.drain()
                    proc.stdin.close()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a closed stdin must not break a turn
                return

        stderr_task = asyncio.create_task(_drain_stderr())
        stdin_task = asyncio.create_task(_feed_stdin())

        async def _finish_stderr() -> str:
            try:
                await asyncio.wait_for(asyncio.shield(stderr_task), timeout=2)
            except Exception:  # noqa: BLE001 - best-effort collect
                pass
            return b"".join(stderr_chunks).decode(errors="replace")

        # Mutable attempt telemetry (blocker K). Declared BEFORE the closures so
        # both ``_raise_timeout`` and every classified raise can attach a
        # snapshot to the exception. Only writes need ``nonlocal``; the closures
        # below read these by late binding.
        assembled: list[str] = []
        partial: list[str] = []
        answer_model = _AnswerModelEvidence()
        terminal: dict | None = None
        last_retry: dict | None = None
        last_assistant_error: str | None = None
        seen_init = False
        seen_progress = False
        ttft_ms: float | None = None
        tool_phase: str | None = None
        # Identities of native tool starts with no matching result yet. A SET,
        # so a duplicate start frame (full assistant + partial
        # content_block_start) is idempotent and parallel/nested calls stay
        # independent: one tool finishing cannot close another.
        tools_in_flight: set[str] = set()
        side_effect_state = "none"
        last_progress = start
        soft_slo_logged = False
        # A documented retry event carries a provider-stated wait; while it is in
        # flight the idle budget is extended to cover it so a real retry wait is
        # NOT relabeled a hang (blocker B). Cleared on the next real progress.
        pending_retry_delay: float | None = None
        # The CLI's own DECLARED busy state (a documented ``system/status``
        # value, today only ``compacting``), or ``None``. While set, silence is
        # provider work — bounded like a tool wait, never past the cap. Cleared
        # by the explicit ``status: null`` / ``compact_boundary`` frame or by
        # any real progress; unknown frames never open it.
        declared_busy: str | None = None
        # Silence evidence for SUCCESSFUL turns (2026-09-24). A failure already
        # reports its fatal gap (``last_progress_age_ms`` + ``tool_phase``); a
        # turn that came within seconds of the idle bound and then recovered
        # left no trace, so an intermittent post-tool silence could only be
        # studied after it killed a turn. Records the longest gap between
        # progress events and the event kind that preceded it -- ``tool_result``
        # there means the wait for the model's next response after a tool.
        max_silence_s = 0.0
        max_silence_after: str | None = None
        last_progress_kind = "launch"
        # Native tool calls this turn made, by provider identity (the full
        # assistant frame and the partial start frame name the same call).
        tool_use_ids: set[str] = set()
        unidentified_tool_uses = 0

        def _attach(exc: ProviderError) -> ProviderError:
            """Attach the current attempt-telemetry snapshot to a raised error."""
            exc.attempt_telemetry = {
                "provider": self.name,
                "failure_class": getattr(exc, "failure_class", None),
                "phase": (
                    "streaming" if seen_progress
                    else "init" if seen_init else "launch"
                ),
                "side_effect_state": side_effect_state,
                # Pending work outranks the last tool event seen: with two tools
                # started and one finished, the last event is a ``tool_result``
                # while the turn is still IN a tool. Reporting the last kind
                # there would make a pending-tool timeout read as post-tool
                # silence — the distinction this evidence exists to carry.
                "tool_phase": "in_tool" if tools_in_flight else tool_phase,
                "ttft_ms": ttft_ms,
                "last_progress_age_ms": (time.monotonic() - last_progress) * 1000,
                "exit_code": _coerce_int(proc.returncode),
                "terminal": terminal is not None,
                "terminal_is_error": _terminal_error_flag(terminal),
                "last_assistant_error": last_assistant_error,
            }
            # Liveness normalization deliberately tolerates unknown events; it
            # is not a complete effects trace and cannot attest a safe retry.
            from tinyassets.providers.agent_capacity_boundary import NativeCompletionEvidence

            exc.native_evidence = NativeCompletionEvidence(
                self.name, False, type(proc.returncode) is int, side_effect_state,
            )
            return exc

        async def _raise_timeout(bound_is_absolute: bool, allow: float) -> None:
            await self._terminate(proc)
            check_bwrap_failure(await _finish_stderr())
            if bound_is_absolute:
                raise _attach(InteractiveDeadlineError(
                    f"claude -p exceeded the {profile.absolute_cap_s:.0f}s "
                    "absolute interactive cap while still streaming"
                ))
            raise _attach(ProviderIdleTimeoutError(
                f"claude -p produced no protocol event for {allow:.0f}s "
                "(idle watchdog fired; no provider cooldown)"
            ))

        try:
            while True:
                now = time.monotonic()
                if not seen_init:
                    allow = profile.init_s
                elif not seen_progress:
                    allow = profile.first_progress_s
                else:
                    allow = profile.idle_s
                # A known provider retry wait extends the idle budget (blocker B):
                # a 60s documented retry_delay must not be relabeled idle at 30s.
                # The absolute cap still bounds the total turn.
                if pending_retry_delay is not None:
                    allow = max(allow, pending_retry_delay + _RETRY_GRACE_MARGIN_S)
                # An identified tool is still running: silence is its work, not
                # a hang. Only this branch is extended — the ordinary model-idle
                # interval is unchanged, and ``max`` keeps a longer documented
                # retry grace. The absolute cap below still bounds the turn.
                if tools_in_flight:
                    allow = max(allow, min(profile.absolute_cap_s, _TOOL_WAIT_S))
                # The CLI declared itself busy (compacting): silence until the
                # matching clear is its work, bounded exactly like a tool wait.
                # The absolute cap below still bounds the turn.
                if declared_busy is not None:
                    allow = max(allow, min(profile.absolute_cap_s, _BUSY_WAIT_S))
                idle_deadline = last_progress + allow
                abs_deadline = start + profile.absolute_cap_s
                budget = min(idle_deadline, abs_deadline) - now
                bound_is_absolute = abs_deadline <= idle_deadline
                if budget <= 0:
                    await _raise_timeout(bound_is_absolute, allow)
                if not soft_slo_logged and now - start >= profile.soft_slo_s:
                    soft_slo_logged = True
                    logger.info(
                        "served claude turn exceeded soft SLO %.0fs; still "
                        "progressing", profile.soft_slo_s,
                    )
                try:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=budget,
                    )
                except asyncio.TimeoutError:
                    await _raise_timeout(bound_is_absolute, allow)
                except (ValueError, asyncio.LimitOverrunError):
                    await self._terminate(proc)
                    await _finish_stderr()
                    raise _attach(ProviderProtocolError(
                        "claude -p stream line exceeded the reader buffer limit"
                    ))
                if not line:
                    break  # EOF
                events, malformed = self._parse_line(line)
                if malformed:
                    await self._terminate(proc)
                    check_bwrap_failure(await _finish_stderr())
                    raise _attach(ProviderProtocolError(
                        "claude -p emitted a malformed (non-JSON) stream line"
                    ))
                progressed = False
                line_kind = last_progress_kind
                for kind, payload in events:
                    if kind == "answer_evidence":
                        answer_model.observe(payload["obj"])
                        continue  # Optional metadata must not reset the watchdog.
                    progressed = True
                    line_kind = kind
                    if kind == "init":
                        seen_init = True
                    elif kind == "heartbeat":
                        # A recognized protocol event (thinking / hooks / status /
                        # stream framing / tool_progress / informational
                        # rate_limit_event): liveness only. Reset the watchdog
                        # (progressed=True above), never relay, never change phase.
                        pass
                    elif kind == "declared_busy":
                        # A documented busy status: liveness AND the start of a
                        # declared window. NOT seen_progress and no phase change.
                        declared_busy = payload["state"]
                    elif kind == "declared_clear":
                        # ``status: null`` / ``compact_boundary``: the window is
                        # over; ordinary idle applies from here.
                        declared_busy = None
                    elif kind == "text_delta":
                        seen_init = True
                        seen_progress = True
                        pending_retry_delay = None
                        last_assistant_error = None
                        declared_busy = None
                        if ttft_ms is None:
                            ttft_ms = (time.monotonic() - start) * 1000
                        if payload.get("partial"):
                            partial.append(payload.get("text") or "")
                        else:
                            assembled.append(payload.get("text") or "")
                    elif kind == "tool_use":
                        seen_init = True
                        seen_progress = True
                        pending_retry_delay = None
                        last_assistant_error = None
                        declared_busy = None
                        tool_phase = "tool_use"
                        identity = _tool_identity(payload.get("id"))
                        if identity is not None:
                            tools_in_flight.add(identity)
                            tool_use_ids.add(identity)
                        else:
                            unidentified_tool_uses += 1
                        if side_effect_state == "none":
                            side_effect_state = "possible"
                    elif kind == "tool_result":
                        seen_init = True
                        seen_progress = True
                        pending_retry_delay = None
                        last_assistant_error = None
                        declared_busy = None
                        tool_phase = "tool_result"
                        # Only the tool this result NAMES is closed. An unknown
                        # or malformed ``tool_use_id`` closes nothing, so it can
                        # never end another tool's allowance early.
                        identity = _tool_identity(payload.get("tool_use_id"))
                        if identity is not None:
                            tools_in_flight.discard(identity)
                        side_effect_state = "committed"
                    elif kind == "api_retry":
                        seen_init = True
                        # A documented retry is liveness AND arms the retry-wait
                        # grace so the idle watchdog covers the provider's stated
                        # wait (blocker B). NOT seen_progress: a retry is not the
                        # first useful output.
                        retry_after = payload.get("retry_after")
                        if isinstance(retry_after, (int, float)) and retry_after > 0:
                            pending_retry_delay = float(retry_after)
                        if payload.get("failure_class"):
                            last_retry = payload
                    elif kind == "assistant_error":
                        # Unsuperseded diagnostic clue, not proof of final cause.
                        # Later useful output/tools clear it; liveness does not.
                        # No capacity/retry classification or authority change.
                        last_assistant_error = payload["category"]
                    elif kind == "result":
                        terminal = payload.get("obj")
                        # The turn is over; nothing it started is still coming
                        # back. Clearing keeps the tool_phase evidence honest.
                        tools_in_flight.clear()
                        declared_busy = None
                if progressed:
                    progress_at = time.monotonic()
                    if progress_at - last_progress > max_silence_s:
                        max_silence_s = progress_at - last_progress
                        max_silence_after = last_progress_kind
                    last_progress = progress_at
                    last_progress_kind = line_kind
                if terminal is not None:
                    break

            # Normal loop exit (terminal result or EOF). Reap + collect stderr.
            await self._terminate(proc)
            returncode = proc.returncode
            stderr_text = await _finish_stderr()
            check_bwrap_failure(stderr_text)
            elapsed_ms = (time.monotonic() - start) * 1000

            if terminal is not None and _result_is_success(terminal):
                from tinyassets.providers.agent_capacity_boundary import NativeCompletionEvidence

                final_text = str(terminal.get("result") or "").strip()
                if not final_text:
                    final_text = (
                        "".join(assembled).strip() or "".join(partial).strip()
                    )
                if not final_text:
                    raise _attach(ProviderError(
                        "claude -p returned a success result with no assistant text"
                    ))
                usage = terminal.get("usage")
                usage = usage if isinstance(usage, dict) else {}
                tool_uses = len(tool_use_ids) + unidentified_tool_uses
                if max_silence_s * 2 >= profile.idle_s:
                    # Allowlisted scalars only: never prompt, tool input or text.
                    logger.info(
                        "%s stream near-idle: max_silence_ms=%.0f after=%s "
                        "idle_s=%.0f tool_uses=%d elapsed_ms=%.0f",
                        self.name, max_silence_s * 1000, max_silence_after, profile.idle_s,
                        tool_uses, elapsed_ms,
                    )
                cost = terminal.get("total_cost_usd")
                cost_micro = (
                    round(float(cost) * 1_000_000)
                    if isinstance(cost, (int, float)) and math.isfinite(cost)
                    else None
                )
                return ProviderResponse(
                    text=final_text,
                    provider=self.name,
                    model=config.native_model_id or self.native_credential_service,
                    reported_model=answer_model.for_answer(final_text),
                    family=self.family,
                    latency_ms=elapsed_ms,
                    input_tokens=_coerce_int(usage.get("input_tokens")),
                    output_tokens=_coerce_int(usage.get("output_tokens")),
                    cost_microunits=cost_micro,
                    ttft_ms=ttft_ms,
                    last_progress_age_ms=(time.monotonic() - last_progress) * 1000,
                    tool_phase=tool_phase,
                    exit_code=_coerce_int(returncode),
                    side_effect_state=side_effect_state,
                    tool_uses=tool_uses,
                    max_silence_ms=max_silence_s * 1000,
                    max_silence_after=max_silence_after,
                    native_evidence=NativeCompletionEvidence(
                        self.name, False, type(returncode) is int, side_effect_state,
                    ),
                )

            # Not a successful terminal result — classify the failure.
            # A TYPED provider retry signal (real system/api_retry / rate_limit_event)
            # is authoritative and MUST be checked BEFORE the exit-code heuristics
            # (Codex re-review #2 blocker A): a genuine 429/529 followed by a quick
            # exit 1 was otherwise mislabeled as generic "unavailable" with no
            # retry_after, losing the honest rate-limit classification + cooldown.
            if last_retry is not None and last_retry.get("failure_class"):
                retry_after = last_retry.get("retry_after")
                if last_retry["failure_class"] == "provider_overloaded":
                    raise _attach(ProviderOverloadedError(
                        "claude -p reported provider overload and did not recover",
                        retry_after=retry_after,
                    ))
                raise _attach(ProviderRateLimitedError(
                    "claude -p reported a provider rate limit and did not recover",
                    retry_after=retry_after,
                ))
            if returncode == 1 and elapsed_ms < 5000 and terminal is None:
                raise _attach(ProviderUnavailableError(
                    "claude -p returned exit code 1 quickly -- API likely unavailable"
                ))
            if returncode in _WINDOWS_CRASH_CODES:
                raise _attach(ProviderUnavailableError(
                    f"claude -p crashed with Windows exit code {returncode:#x} "
                    "— subprocess failure, applying cooldown"
                ))
            if terminal is not None:
                # Keep the observed terminal verdict and last typed category,
                # never upstream result/errors/content or arbitrary subtype text.
                subtype = "success" if terminal.get("subtype") == "success" else "non_success"
                if (terminal.get("is_error") is True
                        and last_assistant_error == "authentication_failed"):
                    raise _attach(ProviderAuthenticationError(
                        "The provider reported a sign-in failure during this turn"
                    ))
                raise _attach(ProviderError(
                    f"claude -p terminal result was not success "
                    f"(subtype={subtype}, is_error={_terminal_error_flag(terminal)}, "
                    f"last_assistant_error={last_assistant_error or 'not_reported'})"
                ))
            if returncode not in (0, None):
                raise _attach(ProviderError(
                    f"claude -p exit {returncode}{disk_stop_note(proc)}: "
                    f"{stderr_text[:400]}"
                ))
            # EOF with a clean/absent exit but NO terminal result: the stream was
            # truncated (blocker J). Classify it as a protocol error rather than a
            # bare, unclassified ProviderError.
            raise _attach(ProviderProtocolError(
                "claude -p stream ended without a terminal result event "
                "(truncated stream)"
            ))
        finally:
            # Every exit path — normal EOF, a classified raise, an unexpected
            # reader exception, or CALLER CANCELLATION — must kill the subprocess
            # and reap BOTH helper tasks so neither the process nor the
            # stdin/stderr drains leak (blocker E: the Codex probe caught
            # ``killed_after_caller_cancel: False``). ``proc.kill()`` is
            # synchronous, so the process is signalled even if a subsequent await
            # is itself cancelled; the in-band ``_terminate`` above is idempotent
            # with this.
            # Synchronous, so the tree is signalled even if a later await is
            # itself cancelled; idempotent with the in-band ``_terminate``.
            kill_owned_tree(proc)
            stdin_task.cancel()
            stderr_task.cancel()
            # BOUND the reap (Codex re-review #2 blocker E): a kill-resistant or
            # wedged ``proc.wait()`` must not hang the finally — and thus a caller
            # cancellation — forever. The process was already signalled by
            # ``proc.kill()`` above, so on a reap timeout we accept a best-effort
            # detach rather than blocking indefinitely.
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        proc.wait(), stdin_task, stderr_task,
                        return_exceptions=True,
                    ),
                    timeout=5,
                )
            except Exception:  # noqa: BLE001 - best-effort reap (CancelledError still propagates)
                pass

    async def complete_json(
        self,
        prompt: str,
        system: str,
        config: ModelConfig,
        *,
        universe_dir: Path | None = None,
    ) -> ProviderResponse:
        """Call with ``--output-format json`` for structured output."""
        self.require_text_only_support(config)
        base_cmd, use_shell = self.native_command_resolver()
        cmd = [*base_cmd, "-p", "--output-format", "json"]
        from tinyassets.providers.native_model_selection import native_model_arguments

        cmd.extend(native_model_arguments(config.native_model_id, "--model"))
        if system:
            cmd.extend(["--system-prompt", system])
        extra_flags, run_cwd = _sandbox_cli_args(config, universe_dir)
        cmd.extend(extra_flags)
        proc_env = subprocess_env_for_provider(
            self.name,
            universe_dir=universe_dir,
            credential_snapshot_dir=config.credential_snapshot_dir,
        )
        # Same owned-family spawn as the streamed path.
        proc = await aspawn_owned(
            cmd,
            shell=use_shell,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
            cwd=run_cwd,
        )

        # EVERY exit -- success, classified raise, cancellation -- ends the
        # owned family. Without this the anchor and its control descriptor
        # outlive each successful turn until the Process is collected.
        try:
            start = time.monotonic()

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=prompt.encode("utf-8")),
                    timeout=config.timeout,
                )
            except asyncio.TimeoutError:
                await akill_owned_tree(proc)
                await proc.wait()
                raise ProviderTimeoutError("claude -p (json) timed out")

            elapsed_ms = (time.monotonic() - start) * 1000

            if proc.returncode == 1 and elapsed_ms < 5000:
                raise ProviderUnavailableError(
                    "claude -p (json) returned exit code 1 quickly"
                )

            _WINDOWS_CRASH_CODES = {3221225588, 3221225477, 3221225786}
            if proc.returncode in _WINDOWS_CRASH_CODES:
                raise ProviderUnavailableError(
                    f"claude -p (json) crashed with Windows exit code "
                    f"{proc.returncode:#x} — applying cooldown"
                )

            stderr_text_json = stderr.decode(errors="replace")
            check_bwrap_failure(stderr_text_json)

            if proc.returncode != 0:
                raise ProviderError(
                    f"claude -p (json) exit {proc.returncode}{disk_stop_note(proc)}: "
                    f"{stderr_text_json}"
                )

            raw = stdout.decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            text = parsed.get("result", raw)

            return ProviderResponse(
                text=text,
                provider=self.name,
                model=config.native_model_id or self.native_credential_service,
                family=self.family,
                latency_ms=elapsed_ms,
            )
        finally:
            kill_owned_tree(proc)
