"""Base types for the provider layer.

Every provider implements :class:`BaseProvider`.  The router and all
consumers work with :class:`ProviderResponse` and :class:`ModelConfig`.
"""

from __future__ import annotations

import abc
import logging
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, get_args

from tinyassets.addressed_agents import MAIN_AGENT
from tinyassets.ttl_memo import TTLMemo as _TTLMemo

if TYPE_CHECKING:
    from tinyassets.auth.middleware import ProviderRequestCarrier
    from tinyassets.config import UniverseConfig
    from tinyassets.provider_assignment import ServedProviderAuthority
    from tinyassets.provider_work_authority import ProviderInvocationCarrier
    from tinyassets.providers.agent_capacity_boundary import NativeCompletionEvidence
    from tinyassets.providers.agent_chat_codec import AgentReply
    from tinyassets.providers.agent_inference import AgentInferenceRequest
    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.model_selection import SelectedModel
    from tinyassets.request_budget import TurnRequestBudget

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Streamed-attempt taxonomy (Slice 1: stream-and-classify-provider-attempts)
# ---------------------------------------------------------------------------

#: Normalized kinds a raw provider stream line collapses to. Only the "real
#: protocol event" kinds reset the idle watchdog; ``ignored`` (whitespace,
#: internal reasoning/thinking, unknown-but-well-formed) never does.
StreamEventKind = Literal[
    "init",            # system/init — CLI + MCP came up
    "text_delta",      # assistant text produced (partial delta or full message)
    "tool_use",        # a tool call started
    "tool_result",     # a tool call returned
    "api_retry",       # documented provider retry event (rate-limit/overload/…)
    "heartbeat",       # any OTHER recognized protocol event (thinking, hooks,
                       # status, notification, stream framing, tool_progress,
                       # tool_heartbeat, an informational rate_limit_event) — it
                       # proves the CLI is alive and working, so it resets the
                       # idle watchdog, but its content is NEVER relayed.
    "result",          # terminal result event (canonical response)
    "declared_busy",   # a DOCUMENTED provider busy status (system/status
                       # ``compacting``): liveness + opens a bounded declared-
                       # busy allowance in the reader; never relayed.
    "declared_clear",  # the explicit clear of that status (``status: null`` /
                       # ``compact_boundary``): liveness, ends the allowance.
    "ignored",         # whitespace / unparseable-suppressed / non-liveness —
                       # do NOT reset
]

#: The event kinds that count as real progress (reset the idle deadline). Note
#: ``heartbeat`` is included: a reasoning-only stretch emits ONLY thinking +
#: framing events (verified against a real Claude 2.1.236 trace), so excluding
#: them false-killed a working turn at the idle boundary. They are liveness,
#: never relayed.
LIVENESS_EVENT_KINDS: frozenset[str] = frozenset({
    "init", "text_delta", "tool_use", "tool_result", "api_retry",
    "heartbeat", "result",
    # A DOCUMENTED provider busy status (``system/status`` ``compacting``) and
    # its explicit clear (``status: null`` / ``compact_boundary``). Liveness,
    # never relayed; the busy one also opens a bounded declared-busy allowance
    # in the reader (same bound shape as a tool wait, never past the cap).
    "declared_busy", "declared_clear",
})

# Internal-contract guard: every liveness kind is a declared stream event kind,
# and ``ignored`` never counts as liveness. Import-time so a drift between the
# two definitions fails loudly instead of silently never resetting the watchdog.
assert LIVENESS_EVENT_KINDS <= frozenset(get_args(StreamEventKind)), (
    sorted(LIVENESS_EVENT_KINDS - frozenset(get_args(StreamEventKind)))
)
assert "ignored" not in LIVENESS_EVENT_KINDS

#: Structured failure classes derived from the stream + process exit. These
#: replace the substring ``"exhausted" -> capacity`` heuristic.
FailureClass = Literal[
    "provider_rate_limited",
    "provider_overloaded",
    "authority_held",
    "provider_idle_timeout",
    "interactive_deadline",
    "provider_protocol_error",
    "provider_reply_error",
    "provider_unreadable_reply",
    "provider_stalled",
    "provider_refused",
    "provider_reply_timeout",
]

# Idle-watchdog profile defaults (seconds) — a PROFILE, not one wall-clock.
# The deadline resets only on a real protocol event; a progressing turn is
# never failed for total elapsed time. See design.md § "Idle watchdog".
DEFAULT_INIT_TIMEOUT_S = 10.0          # process -> valid system/init
DEFAULT_FIRST_PROGRESS_S = 20.0        # init -> first useful progress
DEFAULT_IDLE_TIMEOUT_S = 30.0          # inter-event idle (hung completion/tool)
DEFAULT_SOFT_SLO_S = 60.0              # status only; NOT a failure
# A GENEROUS safety net, not a total wall-clock deadline. Idle (30s) is the
# primary fast-hang control; this only bounds a turn that keeps streaming
# progress for an unreasonable duration. Set well past 300s so a long but
# genuinely progressing reply is never failed for elapsed time (the old 300s
# total deadline was the live capacity-mislabel root cause). Reaching it is an
# ``interactive_deadline`` — it does NOT cool the provider.
DEFAULT_ABSOLUTE_CAP_S = 600.0         # end the turn; NO provider cooldown


@dataclass(frozen=True, slots=True)
class StreamTimeoutProfile:
    """Resolved idle-watchdog thresholds for one streamed attempt."""

    init_s: float = DEFAULT_INIT_TIMEOUT_S
    first_progress_s: float = DEFAULT_FIRST_PROGRESS_S
    idle_s: float = DEFAULT_IDLE_TIMEOUT_S
    soft_slo_s: float = DEFAULT_SOFT_SLO_S
    absolute_cap_s: float = DEFAULT_ABSOLUTE_CAP_S


@dataclass(frozen=True, slots=True)
class UniverseContext:
    """Explicit per-universe routing context threaded through provider calls.

    Carries the universe directory (for credential-vault auth resolution) and
    the resolved :class:`~tinyassets.config.UniverseConfig` (for provider
    preference / allowlist), so the router and vault resolve per-universe config
    from an EXPLICIT argument instead of the process-global
    ``runtime.universe_config`` / ``TINYASSETS_UNIVERSE``. This is the
    multi-universe seam: a single daemon process can serve interleaved calls for
    different universes without a global bleeding across them. ``None`` fields
    preserve today's single-universe-daemon behavior (fall back to the globals).
    """

    universe_dir: Path | None = None
    config: "UniverseConfig | None" = None
    provider_invocation: "ProviderInvocationCarrier | None" = None
    provider_request: "ProviderRequestCarrier | None" = None
    served_provider: "ServedProviderAuthority | None" = None
    model_selection: ModelRef | None = None
    """Requested candidate, not authority; revalidated by the serving boundary."""
    agent_model_plan: AgentModelPlan | None = None
    """Captured advisory owner policy; never a grant or a tool-replay instruction."""
    agent_id: str = MAIN_AGENT
    """WHICH of the owner's agents this work belongs to (harness §4.18).

    The one carrier for the addressed agent through a turn. Set ONLY at
    authenticated ingress, from the resolution that ingress already performed
    (``universe_intelligence.converse`` passes the ``addressed_agent`` it was
    handed, which ``universe_server.converse`` resolved inside the owner/universe
    scope). It is ``MAIN_AGENT`` only where ingress genuinely had no addressed
    agent -- the main agent, or a caller with no conversation at all.

    Deliberately NOT derived anywhere downstream. Not from
    ``turn_interrupt.current()``, which is in-process state a workflow-node turn
    does not have and which would make the journal's attribution depend on
    whether a Stop happened to be registrable; and not from a session key, which
    may locate or cross-check a record but can never establish one, so it is not
    what may select whose controls apply. A downstream reader that cannot see
    this field is missing a thread, not licensed to guess.

    The per-launch snapshot that change ``addressed-agent-control-provenance``
    proposes reads THIS field rather than introducing a second source, so
    extending provenance later does not mean replacing this carrier.
    """


#: Claude CLI builtins that reach the host through its filesystem or a shell.
#: The cross-user boundary is the OS jail every provider launch made for a
#: universe runs in (``tinyassets.providers.provider_jail``): inside it these
#: see only the owner's own universe. The deny list stays as a second, narrower
#: layer on the chat turn and on workflow nodes (they have no use for them).
#: The ONE definition: the universe engine's denylist starts from it and a
#: workflow node call denies it (``ModelConfig.workflow_node``).
HOST_REACH_TOOLS: tuple[str, ...] = (
    # shell / process execution (Monitor also runs shell commands; PowerShell
    # is the CLI's shell tool on hosts where it replaces Bash)
    "Bash", "BashOutput", "KillShell", "Monitor", "PowerShell",
    # filesystem
    "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "NotebookRead",
    "Glob", "Grep", "LS",
)


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """Configuration passed to every provider call."""

    timeout: int = 300
    """Legacy subprocess / HTTP total timeout in seconds.

    Still honored by non-streaming providers (``complete_json``, codex, ollama)
    and by the sync router wrappers' outer margin. The STREAMING served-writer
    path (``ClaudeProvider.complete``) no longer uses this as a total
    wall-clock deadline — it uses the idle-watchdog profile below (which falls
    back to the design defaults when its fields are left ``None``)."""

    idle_timeout_s: float | None = None
    """Idle-watchdog inter-event idle interval (default 30s). ``None`` = use the
    design default; a caller may tighten/loosen a single knob without touching
    the others (backward-compat from the legacy ``timeout`` scalar)."""

    first_progress_s: float | None = None
    """Deadline for the first useful progress after ``system/init`` (default 20s)."""

    init_timeout_s: float | None = None
    """Deadline from process start to a valid ``system/init`` (default 10s)."""

    soft_slo_s: float | None = None
    """Interactive soft SLO (default 60s). Status only — never a failure."""

    absolute_cap_s: float | None = None
    """Absolute interactive safety cap (default 600s — a generous backstop, NOT a
    total wall-clock deadline; idle (30s) is the primary fast-hang control).
    Reaching it ENDS the turn (``interactive_deadline``) but does NOT cool the
    provider."""

    max_tokens: int | None = None
    """Optional token cap (provider-specific interpretation)."""

    temperature: float = 0.7

    native_model_id: str | None = None
    """Router-owned native request: None is legacy; empty omits the CLI flag.

    Not authority or an actual answering-model receipt. The router overwrites
    caller values from the current serving authority or sealed work carrier.
    """

    reasoning_effort: str = ""
    """Generic per-call reasoning/effort level (e.g. ``minimal`` / ``low`` /
    ``medium`` / ``high``). Empty = provider default. Each provider maps this to
    its own real setting — e.g. Codex ``-c model_reasoning_effort=<v>`` — so a
    branch can run a light node (localize) cheap+fast and a hard node
    (propose_changes) deep. Not a prompt hint; a real subprocess setting."""

    workflow_node: bool = False
    """Set by the run providers for a user command center's workflow node call.

    Provider-agnostic marker. The cross-user boundary does NOT depend on it:
    every provider launch made for a command center is OS-jailed to that command center by
    the shared spawn point, whatever its config (``provider_jail``). A provider
    may use the mark to narrow further, e.g. pin cwd to the command center and deny
    :data:`HOST_REACH_TOOLS`."""

    text_only: bool = False
    """Restrictive per-invocation mode: no tools, agent request or session resume.

    Only an executor explicitly implementing this contract may accept it.
    This is a restriction, never authority to call a model or access credentials.
    """

    sandbox_workspace: bool = False
    # A chat turn (converse): still OS-isolated, but NOT handed the universe as a
    # coding workspace. Codex `exec` mounted at the universe with -C /workspace
    # behaves as a code agent on the files and replies with coding-agent output
    # instead of a chat answer (live 2026-08-22). Chat turns run in the same
    # bwrap jail over an EMPTY scratch workspace.
    sandbox_chat: bool = False
    """Run the CLI subprocess isolated to the command center's OWN dir instead of the
    host's cwd. When True, subprocess providers set ``cwd=universe_dir`` so the
    call does NOT inherit the daemon's working directory (which may be a source
    checkout, exposing repo files / ``CLAUDE.md`` / other command centers). Set for the
    founder-facing universe-intelligence turn; leave False for host-trusted engine
    roles. The isolation is only as strong as the tool policy below — pair it with
    ``disallowed_tools`` to deny shell escape (a Bash tool can ``cd`` out)."""

    allowed_tools: tuple[str, ...] | None = None
    """Allowlist of CLI tool names the subprocess may use (e.g.
    ``("WebFetch", "Read")``). ``None`` = provider default (no restriction). Maps
    to ``claude -p --allowedTools``. Default-deny: when set, only these are
    usable."""

    disallowed_tools: tuple[str, ...] | None = None
    """Denylist of CLI tool names the subprocess must NOT use (e.g.
    ``("Bash", "WebSearch")``). ``None`` = no explicit denies. Maps to ``claude -p
    --disallowedTools`` and takes precedence over ``allowed_tools`` — the hard
    floor that closes shell-escape / host-access even if a settings file would
    grant them."""

    engine_mcp_enabled: bool = False
    """When True, the founder-facing universe-intelligence turn gets a LOCAL,
    founder-scoped TinyAssets MCP server (``tinyassets.engine_mcp_server``) wired
    in via ``--mcp-config`` + ``--strict-mcp-config``, so the command center agent has
    the SAME MCP handles the founder's browser chatbot has (read_graph /
    write_graph / run_graph / read_page / write_page / get_status), acting AS the
    founder, scoped to its OWN command center. Requires ``engine_mcp_actor_id`` +
    ``engine_mcp_graph_id`` — the wiring FAILS CLOSED (no tools) if either is
    empty. Only ever set for a FOUNDER-tier turn; never for the learning
    extractor or a non-founder caller. See ``engine_mcp_server`` for the identity
    binding + graph pin."""

    engine_mcp_actor_id: str = ""
    """The founder actor_id the local engine MCP server binds identity to. Empty
    disables the engine MCP wiring (fail-closed)."""

    engine_mcp_graph_id: str = ""
    """The command center graph_id the local engine MCP server PINS every handler call
    to. Empty disables the engine MCP wiring (fail-closed)."""

    engine_tool_grant: tuple[str, ...] | None = None
    """The served engine tools this turn may call. ``None`` = the whole served
    set (``served_tools.SERVED_ENGINE_MCP_TOOLS``). Set from an agent node's own
    ``tools_allowed`` grant, resolved from the admitted snapshot; every surface
    (the HTTP loop and each native turn) reads it through ``served_tools.granted_tools``."""

    agent_node_id: str = ""
    """The graph compiler's name for the agent node making this call. It selects
    which node, never whether: the run session resolves it against its own
    admitted immutable snapshot and refuses an id that is not an agent node."""

    agent_node_key: str = ""
    """``shared_self.agent_node_key`` of the compiled node and its branch. The run
    session refuses unless its admitted snapshot's node has the same key."""

    agent_session: object | None = field(default=None, repr=False, compare=False)
    """``agent_sessions.AgentSessionRef`` naming the thread or agent node this
    turn continues. An adapter that declares ``native_resume`` resumes that
    key's native session instead of starting a fresh one; every other adapter
    ignores it."""

    credential_snapshot_dir: Path | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    """Internal per-launch credential snapshot. Served adapters must use this
    immutable copy instead of resolving mutable vault paths at use time."""

    selected_model: SelectedModel | None = None
    """Router-owned selection from current serving authority, never caller policy.

    None preserves legacy model semantics. The router always overwrites caller
    input, including clearing it for calls without selected-model authority.
    """

    agent_request: AgentInferenceRequest | None = field(default=None, repr=False)
    """Internal tool inventory/completed history, never execution authority."""

    request_budget: TurnRequestBudget | None = field(default=None, repr=False, compare=False)
    """Server-owned parent ledger; not wire data, quota evidence or authority."""
    request_purpose: str = "reply"
    request_attempt: int | None = field(default=None, repr=False, compare=False)
    """Router-owned ordinal. The HTTP broker consumes it; never caller authority."""

    secondary_call: bool = False
    """This call is the platform's own bookkeeping beside a founder turn, not the
    turn. Set for post-reply learning extraction; never for a served reply.

    A secondary call is OPTIONAL by construction: nothing it produces is shown to
    the founder and its failure ends only itself. So its failure is not allowed to
    change what the founder's NEXT turn may do -- the router reads the shared
    quota/cooldown gate for a secondary call (skipping a source known to be
    cooling is restrictive, and cheap) but never WRITES it.

    Live 2026-09-25: an answered free-model turn was followed by learning
    extraction on the same source, which took a 429 carrying no ``Retry-After``
    and so wrote our own fixed 120s window. Every answered turn bought the
    founder a two-minute lockout from a request they never made. Cooling on a
    secondary call converts "we spent one extra request" into "the user is
    locked out", and the reply that just succeeded is better evidence about the
    source than the extra call that did not."""

    owns_capacity_siblings: bool = False
    """This caller holds a candidate ORDER over the source and will settle its
    cooldown itself.

    The capacity handler withholds a source's cooldown when an unknown window
    may still be per-model, because cooling the connection would skip the very
    sibling the caller is about to try. That purchase only makes sense for a
    caller that HAS a sibling to try and cools the source once it runs out.

    An agent turn already qualifies through ``agent_request``; this is how a
    WORKFLOW node says the same thing
    (``_ForegroundRunProviderSession._cool_abandoned_sources``). Additive on
    purpose: the capacity decoder now runs for every call rather than only agent
    rounds (live 2026-09-30), so the handler is newly reachable from callers
    that hold no order -- a post-reply learning extraction, say -- and those
    must keep being cooled, or a source at a DAILY cap is re-asked by every
    later turn forever."""

    def stream_timeout_profile(self) -> StreamTimeoutProfile:
        """Resolve the idle-watchdog profile, filling ``None`` knobs with the
        design defaults. Backward-compat: a config that only ever set the legacy
        ``timeout`` scalar gets the standard profile (the streaming path does
        not treat ``timeout`` as a total wall-clock deadline)."""
        def _pos(value: float | None, default: float) -> float:
            try:
                v = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return default
            # Non-finite / non-positive knobs fall back to the default rather
            # than silently disabling a deadline (same hardening class as the
            # auth-probe window below).
            import math
            return v if math.isfinite(v) and v > 0 else default

        return StreamTimeoutProfile(
            init_s=_pos(self.init_timeout_s, DEFAULT_INIT_TIMEOUT_S),
            first_progress_s=_pos(self.first_progress_s, DEFAULT_FIRST_PROGRESS_S),
            idle_s=_pos(self.idle_timeout_s, DEFAULT_IDLE_TIMEOUT_S),
            soft_slo_s=_pos(self.soft_slo_s, DEFAULT_SOFT_SLO_S),
            absolute_cap_s=_pos(self.absolute_cap_s, DEFAULT_ABSOLUTE_CAP_S),
        )


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """Uniform response envelope returned by every provider."""

    text: str
    provider: str
    model: str
    family: str
    latency_ms: float
    degraded: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_microunits: int | None = None
    # --- Optional streamed-attempt telemetry (Slice 1) -------------------
    # All default None so every existing construction site and non-streaming
    # provider stays a valid terminal ProviderResponse (backward-safe).
    failure_class: str | None = None
    """Set only on a degraded/telemetry envelope; a successful terminal result
    leaves this ``None``."""
    ttft_ms: float | None = None
    """Time-to-first-token (first assistant text delta) in ms."""
    last_progress_age_ms: float | None = None
    """Age of the last real protocol event when the stream ended, in ms."""
    tool_phase: str | None = None
    """The last tool phase observed (``tool_use`` / ``tool_result``) or None."""
    exit_code: int | None = None
    """The subprocess exit code, when the stream came from a subprocess."""
    side_effect_state: str | None = None
    """``none`` | ``possible`` | ``committed`` — whether a tool may have run."""
    tool_uses: int | None = None
    """Distinct native tool calls the provider made during this turn."""
    max_silence_ms: float | None = None
    """Longest gap between protocol events in a turn that still completed."""
    max_silence_after: str | None = None
    """Normalized event kind that preceded ``max_silence_ms`` (e.g. ``tool_result``)."""
    reported_model: str = ""
    """Validated answering-model evidence; empty when the adapter cannot report it.

    Legacy ``model`` may contain a requested/default label. Such a label is not
    proof of the model that answered and must not be substituted here.
    """

    requested_model: str = ""
    """The model id this call explicitly asked the source for; empty for its default.

    A request, never answering evidence: a renderer may show it only labelled as
    requested, beside an unknown ``reported_model`` -- never in its place.
    """

    provider_display: str = ""
    """The owner's own name for the connection that answered, for display only.

    ``provider`` is the routing identity (``api_key_http:provdef_ed0169c8...``),
    which the founder saw on the "Answered by" line. This carries the label read
    from their connection instead -- the installed acquisition preset's
    ``display_name`` when the connection was deposited by the
    guided sign-in, else the destination they named it. Empty when nothing
    resolves; a renderer then falls back to ``provider`` rather than invent one.
    Never routing authority, never a model id, never substituted for either.

    No channel or vendor name belongs in this string. The label is READ from
    installed preset data at runtime, so the substrate never learns one
    (``scripts/check_channel_agnostic.py``). Note this is an ATTRIBUTE docstring,
    NOT a real one -- only a module's, class's or function's FIRST statement is
    exempt from that survey -- so a name here counts as a runtime literal and
    fails the ratchet even though nothing executes it. That cost a CI round on
    #3988; run the check before pushing prose under a field."""

    agent_reply: AgentReply | None = field(default=None, repr=False)
    """One inference's validated result; requested tools have not been executed."""
    request_receipt: dict | None = field(default=None, repr=False, compare=False)
    """Detached local dispatch accounting, never a confirmed provider quota."""
    native_evidence: NativeCompletionEvidence | None = field(default=None, repr=False)
    """Local execution evidence, not provider-reported billing or HTTP progress."""


# Sentinel for quality-floor-only degraded judge responses.
DEGRADED_JUDGE_RESPONSE = ProviderResponse(
    text="",
    provider="none",
    model="quality-floor-only",
    family="none",
    latency_ms=0.0,
    degraded=True,
)


API_KEY_PROVIDER_ENV_VARS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "XAI_API_KEY",
)


def _truthy_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def api_key_providers_enabled() -> bool:
    """Always False: no host may opt the platform into its own API keys.

    The platform has no LLM (AGENTS.md Hard Rule 15). The former
    ``TINYASSETS_ALLOW_API_KEY_PROVIDERS`` switch let a host serve calls from
    ``*_API_KEY`` variables in its own environment; it was retired 2026-09-24
    and is no longer read. An owner who wants an API-key provider connects it to
    their universe as their own open provider.
    """
    return False


# Legacy denylist retained for regression assertions. Universe-scoped children
# now start from an empty allowlisted environment instead of mutating this set.
HOST_SUBSCRIPTION_ENV_VARS: tuple[str, ...] = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
)

_PROVIDER_CHILD_INHERITED_ENV_VARS: tuple[str, ...] = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "SYSTEMDRIVE",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_COLLATE",
    "LC_CTYPE",
    "LC_MESSAGES",
    "LC_MONETARY",
    "LC_NUMERIC",
    "LC_TIME",
    "LC_ADDRESS",
    "LC_IDENTIFICATION",
    "LC_MEASUREMENT",
    "LC_NAME",
    "LC_PAPER",
    "LC_TELEPHONE",
    "TZ",
    "TERM",
    "COLORTERM",
    "NO_COLOR",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
)
_PROVIDER_CHILD_CA_FILE_ENV_VARS: tuple[str, ...] = (
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "CODEX_CA_CERTIFICATE",
)
_PROVIDER_AUTH_OVERLAY_ENV_VARS: dict[str, frozenset[str]] = {
    "claude-code": frozenset({
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
    }),
    "codex": frozenset({"CODEX_HOME", "OPENAI_API_KEY"}),
}


def subprocess_env_without_api_keys() -> dict[str, str] | None:
    """Return this process's env with every model API-key variable removed."""
    env = os.environ.copy()
    for name in API_KEY_PROVIDER_ENV_VARS:
        env.pop(name, None)
    return env


def _inherited_env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value is not None or os.name != "nt":
        return value
    canonical = name.casefold()
    return next(
        (value for key, value in os.environ.items() if key.casefold() == canonical),
        None,
    )


def _safe_provider_child_base_env() -> dict[str, str]:
    env = {
        name: value
        for name in _PROVIDER_CHILD_INHERITED_ENV_VARS
        if (value := _inherited_env_value(name)) is not None
    }
    for name in _PROVIDER_CHILD_CA_FILE_ENV_VARS:
        value = _inherited_env_value(name)
        if not value:
            continue
        path = Path(value)
        try:
            if path.is_absolute() and path.is_file():
                env[name] = value
        except OSError:
            continue
    return env


def _ensure_private_provider_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _resolved_universe_child(universe_root: Path, path: Path) -> Path:
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(universe_root):
        raise ValueError("provider path escapes command center")
    return resolved


def _preflight_provider_auth_paths(
    provider_name: str,
    universe_root: Path,
    configured_auth_path: Path | None,
) -> None:
    service = "claude" if provider_name == "claude-code" else "codex"
    default_materialization = universe_root / ".credentials" / service
    _resolved_universe_child(universe_root, default_materialization)
    if configured_auth_path is not None:
        _resolved_universe_child(universe_root, configured_auth_path)


def _preflight_vault_source(universe_root: Path, vault_path: Path) -> None:
    try:
        source_stat = os.lstat(vault_path)
    except FileNotFoundError:
        return
    is_reparse_point = bool(
        getattr(source_stat, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )
    if (
        not stat.S_ISREG(source_stat.st_mode)
        or is_reparse_point
        or source_stat.st_nlink != 1
    ):
        raise ValueError("credential vault source is not a private regular file")
    resolved = vault_path.resolve(strict=True)
    if not resolved.is_relative_to(universe_root):
        raise ValueError("credential vault source escapes command center")


def _provider_child_runtime_env(
    provider_name: str, universe_dir: Path,
) -> dict[str, str]:
    if provider_name not in _PROVIDER_AUTH_OVERLAY_ENV_VARS:
        raise ValueError("unsupported command center provider")

    runtime_root = universe_dir / ".runtime" / "provider-child" / provider_name
    raw_paths = {
        "home": runtime_root / "home",
        "appdata": runtime_root / "home" / "AppData" / "Roaming",
        "local_appdata": runtime_root / "home" / "AppData" / "Local",
        "xdg_config": runtime_root / "home" / ".config",
        "xdg_cache": runtime_root / "home" / ".cache",
        "xdg_data": runtime_root / "home" / ".local" / "share",
        "xdg_state": runtime_root / "home" / ".local" / "state",
        "xdg_runtime": runtime_root / "xdg-runtime",
        "temp": runtime_root / "tmp",
        "claude_config": runtime_root / "auth-empty" / "claude",
        "codex_home": runtime_root / "auth-empty" / "codex",
    }
    paths = {
        name: _resolved_universe_child(universe_dir, path)
        for name, path in raw_paths.items()
    }
    for path in paths.values():
        _ensure_private_provider_dir(path)

    env = _safe_provider_child_base_env()
    env.update({
        "HOME": str(paths["home"]),
        "USERPROFILE": str(paths["home"]),
        "APPDATA": str(paths["appdata"]),
        "LOCALAPPDATA": str(paths["local_appdata"]),
        "XDG_CONFIG_HOME": str(paths["xdg_config"]),
        "XDG_CACHE_HOME": str(paths["xdg_cache"]),
        "XDG_DATA_HOME": str(paths["xdg_data"]),
        "XDG_STATE_HOME": str(paths["xdg_state"]),
        "XDG_RUNTIME_DIR": str(paths["xdg_runtime"]),
        "TMPDIR": str(paths["temp"]),
        "TMP": str(paths["temp"]),
        "TEMP": str(paths["temp"]),
        "CLAUDE_CONFIG_DIR": str(paths["claude_config"]),
        "CODEX_HOME": str(paths["codex_home"]),
        "AWS_EC2_METADATA_DISABLED": "true",
    })
    home = paths["home"]
    if os.name == "nt" and len(home.drive) == 2 and home.drive.endswith(":"):
        env["HOMEDRIVE"] = home.drive
        env["HOMEPATH"] = str(home)[len(home.drive):]
    return env


def _valid_provider_auth_overlay(
    overlay: object, provider_name: str, universe_dir: Path,
) -> bool:
    if not isinstance(overlay, dict):
        return False
    allowed = _PROVIDER_AUTH_OVERLAY_ENV_VARS.get(provider_name, frozenset())
    for name, value in overlay.items():
        if name not in allowed or not isinstance(value, str) or not value:
            return False
        if name not in {"CLAUDE_CONFIG_DIR", "CODEX_HOME"}:
            continue
        try:
            _resolved_universe_child(universe_dir, Path(value))
        except (OSError, RuntimeError, ValueError):
            return False
    return True


#: Reasons safe to log verbatim: fixed sentinels raised by THIS module's own
#: containment checks. An allow-list, not a scrub — an arbitrary exception's
#: message is upstream text that can carry a credential (a review demonstrated
#: exactly that with `secret=do-not-leak`), so anything unrecognised is reduced
#: to its type name.
_SAFE_RESOLUTION_REASONS: frozenset[str] = frozenset({
    "provider path escapes command center",
    "unsupported command center provider",
    "auth overlay is not universe-contained",
})


def _safe_resolution_reason(exc: BaseException) -> str:
    """A loggable reason for a credential-resolution failure."""
    message = str(exc)
    if message in _SAFE_RESOLUTION_REASONS:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def subprocess_env_for_provider(
    provider_name: str,
    *,
    universe_dir: Path | None = None,
    credential_snapshot_dir: Path | None = None,
) -> dict[str, str]:
    """Return subprocess env with API-key policy and exact launch auth applied.

    When *universe_dir* is given it takes precedence over the process-global
    ``TINYASSETS_UNIVERSE`` for vault-auth resolution, so a single daemon can
    resolve per-universe credentials for an explicitly threaded universe.
    A credential snapshot, when supplied, replaces vault resolution entirely.
    """
    bound_universe = os.environ.get("TINYASSETS_UNIVERSE", "").strip()
    resolved_universe = (
        Path(universe_dir)
        if universe_dir is not None
        else Path(bound_universe) if bound_universe else None
    )
    if resolved_universe is None:
        # The host's own environment. The platform never reaches this branch:
        # the provider router is the only caller of ``complete`` and it refuses
        # any launch without a universe directory and the owner's authority
        # (Hard Rule 15, ``tinyassets/providers/owner_binding.py``). What is
        # left is developer tooling on a developer's own machine
        # (``scripts/peer_agent.py``), which is not the platform.
        return subprocess_env_without_api_keys() or os.environ.copy()

    credential_resolution_failed = False
    reason = ""
    env: dict[str, str] = {}
    try:
        if provider_name not in _PROVIDER_AUTH_OVERLAY_ENV_VARS:
            raise ValueError("unsupported command center provider")
        universe_root = resolved_universe.expanduser().resolve(strict=False)
        if credential_snapshot_dir is not None:
            snapshot = _resolved_universe_child(
                universe_root,
                credential_snapshot_dir,
            )
            if snapshot.is_symlink() or not snapshot.is_dir():
                raise ValueError("credential snapshot is unavailable")
            env = _provider_child_runtime_env(provider_name, universe_root)
            if provider_name == "codex":
                env["CODEX_HOME"] = str(snapshot)
            else:
                # The sealed snapshot stores the claude OAuth token in
                # auth.json. Claude Code authenticates from
                # CLAUDE_CODE_OAUTH_TOKEN (see entrypoint).
                try:
                    _tok = (snapshot / "auth.json").read_text(
                        encoding="utf-8"
                    ).strip()
                except OSError:
                    _tok = ""
                if _tok.startswith("sk-ant-"):
                    # Raw-token credential: authenticate via the ENV token and
                    # KEEP CLAUDE_CONFIG_DIR on the clean isolated dir that
                    # _provider_child_runtime_env already set — do NOT point it
                    # at the snapshot. The snapshot is a codex-format credential
                    # dir (config.toml + bare auth.json); pointing the claude
                    # CLI at a non-claude config dir SILENTLY stops it from
                    # spawning --mcp-config MCP servers, which broke the
                    # founder-scoped engine tools (read_graph/run_graph) on every
                    # SERVED turn while a manual `claude -p` with the same flags
                    # worked (isolated live 2026-08-19: the engine MCP server
                    # never launched). The token env fully authenticates, so the
                    # config dir only needs to be a clean claude config home.
                    env["CLAUDE_CODE_OAUTH_TOKEN"] = _tok
                else:
                    # JSON (.credentials.json) material: leave auth to
                    # CLAUDE_CONFIG_DIR resolution against the snapshot.
                    env["CLAUDE_CONFIG_DIR"] = str(snapshot)
            return env
        from tinyassets.credential_vault import (
            apply_provider_auth_env,
            credential_vault_path,
            resolve_claude_config_dir,
            resolve_codex_home,
        )

        _preflight_vault_source(
            universe_root, credential_vault_path(universe_root),
        )
        configured_auth_path = (
            resolve_claude_config_dir(universe_root)
            if provider_name == "claude-code"
            else resolve_codex_home(universe_root)
        )
        _preflight_provider_auth_paths(
            provider_name, universe_root, configured_auth_path,
        )
        env = _provider_child_runtime_env(provider_name, universe_root)
        overlay = apply_provider_auth_env(
            {}, provider_name, universe_dir=universe_root,
        )
        if not _valid_provider_auth_overlay(
            overlay, provider_name, universe_root,
        ):
            credential_resolution_failed = True
            reason = "auth overlay is not universe-contained"
        else:
            env.update(overlay)
    except Exception as exc:  # noqa: BLE001 - see _safe_resolution_reason
        credential_resolution_failed = True
        reason = _safe_resolution_reason(exc)
    if credential_resolution_failed:
        from tinyassets.exceptions import ProviderUnavailableError
        # The reason goes to the DAEMON LOG only. The raised error stays
        # generic on purpose: it crosses into caller-visible surfaces, and
        # `test_universe_credential_resolution_failure_is_explicit` and
        # `test_malformed_real_vault_failure_is_sanitized_without_artifact_creation`
        # both exist because a message here previously disclosed vault
        # internals. Logging the cause fixes diagnosability without reopening
        # that; a bare `except Exception: failed = True` had made every
        # containment refusal indistinguishable, which cost a live debugging
        # session to unpick.
        logger.warning(
            "%s credential resolution failed for command center %s: %s",
            provider_name,
            resolved_universe.name,
            reason,
        )
        raise ProviderUnavailableError(
            f"{provider_name} credential resolution failed for "
            "universe-scoped provider"
        )
    return env


# ---------------------------------------------------------------------------
# Codex refresh-viability probe (layered on top of the presence check below).
# ---------------------------------------------------------------------------

# Signatures captured live 2026-07-14 by running `codex exec` against the
# dead token stranded on the old workflow-data volume (exit code was 0 even
# on failure, so output text — stdout+stderr — is the only reliable signal).
# Matched case-insensitively (Codex review: the CLI's casing is not a
# contract). Additionally the probe mirrors CodexProvider's silent-auth
# heuristic: EMPTY stdout + a broad auth signal in stderr is also dead —
# broad signals are only trusted when the model produced no reply, so
# model text can never false-positive.
_CODEX_AUTH_FAILURE_PATTERNS: tuple[str, ...] = (
    "your access token could not be refreshed",
    "please log out and sign in again",
    "401 unauthorized",
)
_CODEX_SILENT_AUTH_SIGNALS: tuple[str, ...] = (
    "401", "unauthorized", "reconnecting", "auth",
)

_AUTH_PROBE_PROMPT = "Reply with exactly: OK"


DEFAULT_CODEX_AUTH_FRESH_S = 24 * 3600.0
DEFAULT_AUTH_PROBE_TTL_S = 1800.0
DEFAULT_AUTH_PROBE_TIMEOUT_S = 120.0

_PROBE_FALSY = {"0", "false", "off", "no"}

# Live-probe verdict cache. The supervisor calls the gate every loop tick;
# the probe subprocess must not run per tick. The AUTHORITATIVE cache is a
# small JSON file NEXT TO auth.json (shared volume): production runs the
# daemon and workers as separate containers sharing CODEX_HOME, so an
# in-memory dict would let a worker quarantine while the daemon's
# get_status kept reporting "ok" (Codex review 2026-07-14). The in-memory
# layer below remains as a fallback for read-only CODEX_HOMEs.
PROBE_CACHE_FILENAME = ".tinyassets_auth_probe.json"

_auth_probe_cache: dict[str, tuple[float, dict[str, str]]] = {}


def _reset_auth_probe_cache() -> None:
    """Test seam (in-memory layer only; tests isolate the disk layer via
    per-test CODEX_HOME tmp dirs)."""
    _auth_probe_cache.clear()


def _read_probe_cache_file(codex_home: Path) -> tuple[float, dict[str, str]] | None:
    """Read the cross-process verdict file; any corruption reads as absent."""
    import json as _json
    import math

    try:
        data = _json.loads(
            (codex_home / PROBE_CACHE_FILENAME).read_text(encoding="utf-8"),
        )
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    checked_at = data.get("checked_at")
    status = data.get("status")
    detail = data.get("detail")
    if not isinstance(checked_at, (int, float)) or not math.isfinite(float(checked_at)):
        return None
    if status not in ("ok", "not_logged_in") or not isinstance(detail, str):
        return None
    return float(checked_at), {
        "provider": "codex", "status": status, "detail": detail,
    }


def _write_probe_cache_file(
    codex_home: Path, checked_at: float, health: dict[str, str],
) -> None:
    """Best-effort atomic write of the cross-process verdict file."""
    import json as _json

    payload = _json.dumps({
        "checked_at": checked_at,
        "status": health["status"],
        "detail": health["detail"],
    }, ensure_ascii=True)
    target = codex_home / PROBE_CACHE_FILENAME
    tmp = target.with_suffix(".json.tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass  # read-only home: the in-memory layer still covers this process


def _viability_probe_enabled() -> bool:
    raw = os.environ.get("TINYASSETS_AUTH_VIABILITY_PROBE", "").strip().lower()
    return raw not in _PROBE_FALSY


def _finite_positive_env_s(var: str, default: float) -> float:
    """Parse a seconds env var; only finite positive values are accepted
    (same hardening class as the idle-cycle window — Codex review
    2026-07-14: ``inf``/``nan`` must not silently disable comparisons)."""
    import math

    raw = os.environ.get(var, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return value


def _codex_last_refresh_age_s(codex_home: Path, now: float | None = None) -> float | None:
    """Age in seconds of the auth.json ``last_refresh`` field.

    The file-mtime fallback applies ONLY to a VALID JSON object that lacks a
    usable ``last_refresh`` (e.g. a mid-write `codex login`). An unreadable
    or corrupt auth.json returns ``None`` — suspicious, so the caller
    escalates to the live probe instead of trusting mtime (Codex review
    2026-07-14: a fresh file containing garbage must not read viable and
    claim-and-poison)."""
    import json as _json
    import math
    import time as _time
    from datetime import datetime, timezone

    auth_path = codex_home / "auth.json"
    current = _time.time() if now is None else now
    try:
        data = _json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("last_refresh")
    if isinstance(raw, str) and raw.strip():
        try:
            text = raw.strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age = current - parsed.timestamp()
            if math.isfinite(age):
                return age
        except (ValueError, TypeError, OverflowError):
            # A present-but-unparseable last_refresh is suspicious, not
            # mtime-fresh.
            return None
    try:
        return current - auth_path.stat().st_mtime
    except OSError:
        return None



#: Real single-flight, not just serialization. A bare lock only serializes: Codex
#: reproduced two simultaneous cache misses spawning two probes 81 ms apart, because the
#: second acquired the lock after the first released and then ran its own. The second
#: caller must wait for the first's ANSWER — correct here, because the result describes
#: the machine's credentials, not the caller.
#:
#: `TTLMemo` already is single-flight plus a short TTL, and is tested against exactly
#: this stampede, so this reuses it rather than hand-rolling the primitive twice.
_auth_probe_memo = _TTLMemo(max_entries=4)
#: SINGLE-FLIGHT ONLY, not caching. Codex asked for single-flight and I first shipped a
#: 30-second cache, which is a different thing: it also serves SEQUENTIAL callers a stale
#: verdict, so a re-login would read `not_logged_in` for half a minute. Concurrent
#: duplicates are what cost a ~189 MB spawn; a later caller asking again deserves a real
#: answer.
_AUTH_PROBE_TTL_S = 0.0


def _codex_live_auth_probe(timeout_s: float) -> dict[str, str]:
    """Single-flighted, admission-bounded wrapper around the real probe.

    The probe spawns a REAL `codex exec`, so it costs the same ~189 MB as any provider
    turn — and it used to run outside the admission bound entirely, before the router.
    Both fixed here: one probe at a time, and it counts against the limit like anything
    else that spawns.

    On a busy box this degrades to "inconclusive" rather than queueing. The probe is a
    diagnostic, and making a diagnostic wait behind real user turns is the wrong trade.
    `try_provider_slot` checks immediately rather than waiting for admission.
    """
    from tinyassets.provider_admission import ProviderBusy, try_provider_slot

    def _run() -> dict[str, str]:
        try:
            with try_provider_slot():
                return _codex_live_auth_probe_uncached(timeout_s)
        except ProviderBusy:
            return {"status": "inconclusive",
                    "detail": "provider slots busy; auth probe deferred"}

    return _auth_probe_memo.get("codex", _run, ttl=_AUTH_PROBE_TTL_S)


def _codex_live_auth_probe_uncached(timeout_s: float) -> dict[str, str]:
    """One tiny real ``codex exec`` call; the only check that catches a
    dead refresh token (``codex login status`` reads the file locally and
    reported "Logged in" for the very token that 401'd — live 2026-07-14).

    Returns ``{"status": "ok"|"not_logged_in"|"inconclusive", "detail"}``.
    Uses whatever ``codex`` is on PATH so flock-wrapper deployments keep
    their single-use refresh-token serialization.
    """
    import subprocess
    import tempfile

    from tinyassets.providers.codex_provider import _resolve_codex_cmd

    base_cmd, use_shell = _resolve_codex_cmd()
    cmd = [
        *base_cmd, "exec", "--skip-git-repo-check", "-s", "read-only",
        _AUTH_PROBE_PROMPT,
    ]
    # Admission and single-flight belong to the WRAPPER, not here. Doing both in both
    # places double-counted the bound — one probe reported `admitted=2, peak=2`, and at
    # limit 1 the inner acquire refused itself into a false "inconclusive" having
    # spawned nothing (Codex round 3).
    try:
        proc = subprocess.run(
            cmd if not use_shell else subprocess.list2cmdline(cmd),
            shell=use_shell,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=tempfile.gettempdir(),
        )
    except FileNotFoundError:
        return {"status": "inconclusive",
                "detail": "codex binary not on PATH; probe skipped"}
    except subprocess.TimeoutExpired:
        return {"status": "inconclusive",
                "detail": f"live auth probe timed out after {timeout_s:.0f}s"}
    except OSError as exc:
        return {"status": "inconclusive",
                "detail": f"live auth probe could not run: {exc}"}

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    combined_lower = f"{stdout}\n{stderr}".lower()
    matched = next(
        (p for p in _CODEX_AUTH_FAILURE_PATTERNS if p in combined_lower), None,
    )
    # CodexProvider silent-auth mirror: empty stdout + a broad auth signal
    # in stderr is dead too. Broad signals are only trusted when the model
    # produced NO reply, so probe/model text can never false-positive.
    if matched is None and not stdout.strip():
        matched = next(
            (s for s in _CODEX_SILENT_AUTH_SIGNALS if s in stderr.lower()),
            None,
        )
    if matched is not None:
        return {
            "status": "not_logged_in",
            "detail": (
                f"refresh-viability probe FAILED (matched {matched!r}); "
                "token is dead despite auth.json being present — run a "
                "fresh `codex login` for this CODEX_HOME"
            ),
        }
    if proc.returncode != 0:
        return {"status": "inconclusive",
                "detail": f"live auth probe exit {proc.returncode} without an "
                          "auth-failure signature"}
    if not stdout.strip():
        return {"status": "inconclusive",
                "detail": "live auth probe returned empty output without an "
                          "auth-failure signature"}
    return {"status": "ok", "detail": "live auth probe passed (real call ok)"}


def _codex_refresh_viability(
    codex_home: Path, *, allow_probe: bool = True,
) -> dict[str, str]:
    """Layered viability verdict for a PRESENT auth.json (see the
    subscription_auth_health docs below for the full ladder).

    ``allow_probe=False`` is for latency-sensitive callers (get_status —
    an MCP request must never block on a probe subprocess): it serves the
    freshness fast path and any cached verdict, and reports stale creds as
    "ok" with a probe-deferred detail instead of probing inline. The
    quarantine decision itself lives in the claim gate, which always
    probes.
    """
    import time as _time

    presence_ok = {
        "provider": "codex", "status": "ok",
        "detail": f"auth.json present at {codex_home}",
    }
    if not _viability_probe_enabled():
        return presence_ok

    fresh_s = _finite_positive_env_s(
        "TINYASSETS_CODEX_AUTH_FRESH_S", DEFAULT_CODEX_AUTH_FRESH_S,
    )
    age = _codex_last_refresh_age_s(codex_home)
    if age is not None and 0 <= age < fresh_s:
        presence_ok["detail"] = (
            f"auth.json present at {codex_home}; last_refresh "
            f"{age:.0f}s ago (< {fresh_s:.0f}s) — refresh-viable"
        )
        return presence_ok

    ttl_s = _finite_positive_env_s(
        "TINYASSETS_AUTH_PROBE_TTL_S", DEFAULT_AUTH_PROBE_TTL_S,
    )
    now = _time.time()
    # Disk cache first (cross-process/container truth: the worker's probe
    # verdict must be visible to the daemon's get_status), then the
    # in-memory layer (covers read-only CODEX_HOMEs).
    cached = _read_probe_cache_file(codex_home)
    if cached is None:
        cached = _auth_probe_cache.get(str(codex_home))
    if cached is not None and 0 <= now - cached[0] < ttl_s:
        return dict(cached[1])

    if not allow_probe:
        presence_ok["detail"] = (
            f"auth.json present at {codex_home}; last_refresh stale "
            f"(age {'unknown' if age is None else f'{age:.0f}s'}) — live "
            "probe deferred to the worker gate"
        )
        return presence_ok

    timeout_s = _finite_positive_env_s(
        "TINYASSETS_AUTH_PROBE_TIMEOUT_S", DEFAULT_AUTH_PROBE_TIMEOUT_S,
    )
    probe = _codex_live_auth_probe(timeout_s)
    if probe["status"] == "not_logged_in":
        health = {"provider": "codex", "status": "not_logged_in",
                  "detail": probe["detail"]}
    else:
        # "ok" and "inconclusive" both read ok: only a POSITIVE dead
        # signature quarantines (false not_logged_in on a healthy worker is
        # worse; a false ok still fails at call time + trips loop_stalled).
        health = {"provider": "codex", "status": "ok",
                  "detail": f"auth.json present at {codex_home}; {probe['detail']}"}
    _auth_probe_cache[str(codex_home)] = (now, dict(health))
    _write_probe_cache_file(codex_home, now, health)
    return health


# Subscription-auth health. The 2026-06-25 loop-wedge root cause was a worker
# whose claude-code auth was dead (no credentials) that kept claiming tasks
# and failing every one, poisoning the queue for ~3 weeks undetected.
# ``is_available()`` only checks the binary is on PATH (``shutil.which``); it
# does NOT check login state. This helper checks login state so workers can
# self-quarantine before claiming, and get_status can surface dead writer
# auth instead of leaving it buried in logs.
#
# Returns ``{"provider", "status", "detail"}`` where status is one of:
#   "ok"            — subscription credentials are present (and, for codex,
#                     refresh-viable per the layered probe below)
#   "not_logged_in" — credentials are missing or proven dead (the actionable
#                     failure)
#   "unknown"       — no checkable subscription auth here (API-key providers,
#                     ollama, or an unrecognized name); callers never gate on it
#
# Codex gets a layered refresh-viability check on top of presence
# (live-proven gap 2026-07-14: a stale /data/.codex/auth.json stranded by the
# Jun-27 volume migration passed BOTH this presence check AND `codex login
# status`, yet 401'd at call time — the exact 2026-06-25 queue-poison class):
#   1. presence — auth.json missing => not_logged_in (unchanged fast path).
#   2. freshness — auth.json `last_refresh` (fallback: file mtime) younger
#      than TINYASSETS_CODEX_AUTH_FRESH_S => ok without any subprocess. An
#      actively-used token is refreshed by real calls, so busy workers never
#      pay for a probe.
#   3. live probe — stale creds trigger one tiny `codex exec` call (the check
#      that actually caught the dead token; `codex login status` only reads
#      the file locally and lies). Output matching the refresh-failure
#      signatures => not_logged_in (quarantine BEFORE the queue is poisoned).
#      Verdicts are cached per CODEX_HOME for TINYASSETS_AUTH_PROBE_TTL_S.
#
# Failure philosophy per the claude-code note below: inconclusive probe
# outcomes (binary missing, timeout, transport error) read "ok" — a false
# "ok" still fails at call time and trips loop_stalled; only a POSITIVE dead
# signature quarantines. The probe invokes whatever `codex` is on PATH, so
# deployments that ship the flock wrapper for the single-use refresh-token
# chain keep their serialization.
def subscription_auth_health(
    provider_name: str, *, allow_probe: bool = True,
) -> dict[str, str]:
    """Return subscription-auth health for *provider_name*.

    ``allow_probe=False`` for latency-sensitive callers (get_status): never
    spawns the live-probe subprocess; serves fast paths + cached verdicts.
    """
    name = (provider_name or "").strip()
    if name == "codex":
        codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
        if not (codex_home / "auth.json").is_file():
            return {"provider": name, "status": "not_logged_in",
                    "detail": f"no auth.json at {codex_home}"}
        return _codex_refresh_viability(codex_home, allow_probe=allow_probe)
    if name == "claude-code":
        if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
            return {"provider": name, "status": "ok",
                    "detail": "CLAUDE_CODE_OAUTH_TOKEN set"}
        config_dir = Path(
            os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")
        )
        # Deliberately conservative: any non-empty config dir reads "ok". For a
        # quarantine gate, a false "not_logged_in" (quarantining a HEALTHY
        # worker) is worse than a false "ok" (which still fails at call time and
        # trips the loop_stalled warning). Only the empty/absent dir — the exact
        # 2026-06-25 incident — yields "not_logged_in".
        try:
            if config_dir.is_dir() and any(config_dir.iterdir()):
                return {"provider": name, "status": "ok",
                        "detail": f"config dir populated at {config_dir}"}
        except OSError as exc:
            return {"provider": name, "status": "not_logged_in",
                    "detail": f"config dir unreadable: {exc}"}
        return {"provider": name, "status": "not_logged_in",
                "detail": f"no token and empty/absent {config_dir}"}
    return {"provider": name, "status": "unknown",
            "detail": "no subscription-auth probe for this provider"}


# bwrap failure signature emitted to stderr on Linux hosts that lack
# unprivileged user namespaces. When this appears the CLI silently wrote
# the error to state and returned exit=0 — hard-rule #8 demands we detect
# and raise rather than let the garbage propagate.
_BWRAP_FAILURE_PATTERNS: tuple[str, ...] = (
    "bwrap: No permissions to create a new namespace",
    "bwrap: No permissions to create new namespace",
    "bwrap: No such file or directory",
    "sandbox initialization failed",
    # The 2026-08-21 production outage: Docker's masked /proc paths make the
    # --proc mount EPERM inside the sandbox namespace.
    "bwrap: Can't mount proc",
    "Can't mount proc on",
    # codex's launcher takes a lock in the sandboxed CODEX_HOME; a read-only
    # home dies here. Scoped to that exact path so unrelated lock errors are
    # not misread as sandbox failures.
    "cannot open lock file /codex-home/.lock",
)


class SandboxUnavailableError(Exception):
    """Raised when bwrap / sandbox is unavailable on the host.

    Carries the exact stderr excerpt so callers can surface guidance.
    """


def check_bwrap_failure(stderr_text: str) -> None:
    """Raise SandboxUnavailableError if *stderr_text* contains a bwrap error.

    Called by subprocess-backed providers after every CLI invocation so the
    failure is loud (raises) rather than silent (appears in state as output).
    No-op on Windows (bwrap is Linux-only).
    """
    import sys as _sys
    if _sys.platform == "win32":
        return
    lower = stderr_text.lower()
    for pattern in _BWRAP_FAILURE_PATTERNS:
        if pattern.lower() in lower:
            raise SandboxUnavailableError(
                f"Sandbox (bwrap) is unavailable on this host. "
                f"The CLI subprocess emitted a sandboxing failure:\n"
                f"  {stderr_text[:400].strip()}\n\n"
                f"Fix options:\n"
                f"  1. Enable unprivileged user namespaces: "
                f"sysctl -w kernel.unprivileged_userns_clone=1\n"
                f"  2. Use a branch that contains only design-only nodes "
                f"(requires_sandbox=false). These nodes don't need bwrap.\n"
                f"  3. Run the daemon on a host where bwrap is available."
            )


def probe_sandbox_available() -> dict[str, object]:
    """Probe whether bwrap is available on this host.

    Returns {bwrap_available: bool, reason: str | None}.  Cached at
    module level after first call so get_status probes once at startup.

    This is a THIN adapter over :func:`tinyassets.sandbox.detect.detect_bwrap`
    and deliberately owns no probe of its own. It used to carry a second,
    divergent one -- ``bwrap --ro-bind / / /bin/sh -c true`` -- which asks a
    different question than the launcher does. #2736 fixed that in
    ``sandbox/detect.py`` and this copy survived, so the two answered
    differently on the same host: measured 2026-08-31 in the Linux oracle
    container, the old probe here exited 1 ("Creating new namespace failed:
    Operation not permitted") while the launcher's real argv exited 0.

    Two consequences, both silent. ``get_status`` would advertise the sandbox
    as unavailable on a host where every code node in fact runs; and
    ``tests/test_node_sandbox.py`` gates ``requires_bwrap`` on THIS function,
    so the six hostile-code jail tests -- including the positive control that
    proves the jail runs a node at all -- skipped on CI and on the oracle
    rather than failing. A boundary test that skips is a boundary that is
    never checked.

    One probe, one answer. The dict shape is kept because callers and the
    status surface read these keys.
    """
    from tinyassets.sandbox.detect import detect_bwrap

    status = detect_bwrap()
    return {"bwrap_available": status.available, "reason": status.reason}


# Module-level cache populated on first get_status call.
_sandbox_probe_cache: dict[str, object] | None = None


def get_sandbox_status() -> dict[str, object]:
    """Return cached sandbox probe result (probes once per process)."""
    global _sandbox_probe_cache  # noqa: PLW0603
    if _sandbox_probe_cache is None:
        _sandbox_probe_cache = probe_sandbox_available()
    return _sandbox_probe_cache


class BaseProvider(abc.ABC):
    """Abstract base for all LLM providers."""

    name: str = ""
    """Short identifier used in fallback chains (e.g. ``'claude-code'``)."""

    family: str = ""
    """Model family for judge diversity enforcement."""

    agent_execution_kind: str | None = None
    """Installed execution capability; unknown executors cannot claim an agent lane."""

    supports_text_only: bool = False
    """Adapter enforces tool-free requests, including at its direct call boundary."""

    def require_text_only_support(self, config: ModelConfig) -> None:
        """Refuse unsupported or conflicting restrictions before any provider IO."""
        from tinyassets.exceptions import ProviderAuthorityHeldError

        mode = getattr(config, "text_only", False)
        if type(mode) is not bool:
            raise ProviderAuthorityHeldError("invalid text-only provider restriction")
        if not mode:
            return
        if self.supports_text_only is not True:
            raise ProviderAuthorityHeldError(
                "selected provider does not support enforced text-only review; "
                "nothing was launched"
            )
        if any(getattr(config, key, None) for key in (
            "engine_mcp_enabled", "engine_mcp_actor_id", "engine_mcp_graph_id",
            "allowed_tools", "engine_tool_grant", "agent_node_id", "agent_node_key",
        )) or any(getattr(config, key, None) is not None for key in (
            "agent_request", "agent_session",
        )):
            raise ProviderAuthorityHeldError("text-only provider restriction conflicts with tools")

    native_credential_service: str | None = None
    """Native custody service declared by this executor; not a model identifier."""
    native_discovery_protocol = None
    native_metadata_arguments: tuple[str, ...] = ()
    native_command_resolver = None

    @staticmethod
    def native_process_options():
        return {}

    async def enumerate_models(self, *, universe_dir: Path, credential_snapshot_dir: Path):
        """Optional native metadata adapter; None means enumeration is unknown.

        Invoked only with owned snapshot custody by the discovery boundary.
        Returns NativeCatalogue, never execution authority or inference output.
        Future executors override this without adding model releases to policy.
        """
        if self.native_discovery_protocol is None:
            return None
        from tinyassets.exceptions import ProviderError
        from tinyassets.providers.native_jsonrpc_discovery import read_native_catalogue

        if universe_dir is None or credential_snapshot_dir is None:
            raise ProviderError("native model discovery requires owned credentials")
        if not callable(self.native_command_resolver):
            raise ProviderError("native model discovery requires an executable resolver")
        base_cmd, use_shell = self.native_command_resolver()
        if use_shell:
            raise ProviderError("native model discovery requires a direct executable")
        from tinyassets.providers.native_jsonrpc_discovery import NativeMetadataUnsupported

        env = subprocess_env_for_provider(
            self.name, universe_dir=universe_dir, credential_snapshot_dir=credential_snapshot_dir,
        )
        try:
            return await read_native_catalogue(
                [*base_cmd, *self.native_metadata_arguments],
                protocol=self.native_discovery_protocol,
                env=env, cwd=str(credential_snapshot_dir),
                spawn_kwargs=self.native_process_options(),
            )
        except NativeMetadataUnsupported:
            # An installed executor that ANSWERED "I do not implement this"
            # is the same honest unknown as one declaring no protocol at all:
            # None, so the source reads `native_enumeration_unsupported` and
            # its own default stays usable. This is the feature detection --
            # by asking, never by a version table.
            return None

    @classmethod
    def is_available(cls) -> bool:
        """Return True if this provider's binary/dependency is present.

        Subprocess-backed providers override this to probe the binary with
        ``shutil.which`` so the router skips registration on cloud hosts
        where the CLI is absent — avoiding 30s+ wasted cooldowns per call.
        """
        return True

    @abc.abstractmethod
    async def complete(
        self,
        prompt: str,
        system: str,
        config: ModelConfig,
        *,
        universe_dir: Path | None = None,
    ) -> ProviderResponse:
        """Send *prompt* with *system* instructions and return a response.

        *universe_dir*, when supplied, scopes vault-backed subscription auth to
        that universe (subscription-backed providers pass it into
        :func:`subprocess_env_for_provider`); providers that never touch the
        vault ignore it.
        """
