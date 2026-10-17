"""Base types for the provider layer.

Every provider implements :class:`BaseProvider`.  The router and all
consumers work with :class:`ProviderResponse` and :class:`ModelConfig`.
"""

from __future__ import annotations

import abc
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, get_args

from tinyassets.addressed_agents import MAIN_AGENT

if TYPE_CHECKING:
    from tinyassets.auth.middleware import ProviderRequestCarrier
    from tinyassets.config import UniverseConfig
    from tinyassets.provider_assignment import ServedProviderAuthority
    from tinyassets.provider_work_authority import ProviderInvocationCarrier
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
    :data:`HOST_REACH_TOOLS` and :data:`ACCOUNT_REACH_TOOLS`."""

    text_only: bool = False
    """Restrictive per-invocation mode: no tools, agent request or session resume.

    Only an executor explicitly implementing this contract may accept it.
    This is a restriction, never authority to call a model or access credentials.
    """

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
    turn continues, used to track consumed conversation progress."""

    selected_model: SelectedModel | None = None
    """Router-owned selection from current serving authority, never caller policy.

    None preserves legacy model semantics. The router always overwrites caller
    input, including clearing it for calls without selected-model authority.
    """

    agent_request: AgentInferenceRequest | None = field(default=None, repr=False)
    """Internal tool inventory/completed history, never execution authority."""

    request_budget: TurnRequestBudget | None = field(default=None, repr=False, compare=False)
    """Server-owned parent ledger; not wire data, quota evidence or authority."""
    model_launch_parent: tuple[str, str, str, int | None] | None = field(
        default=None, repr=False, compare=False)
    """Server-derived allocation link and turn binding or run receipt for engine tools."""
    request_purpose: str = "reply"
    request_attempt: int | None = field(default=None, repr=False, compare=False)
    """Router-owned ordinal. The HTTP broker consumes it; never caller authority."""

    invocation_owner_user_id: str = field(default="", repr=False, compare=False)
    """Router-owned principal from admitted serving/work authority.

    Internal broker routing context, not a capability. The router overwrites
    caller input for every dispatch; source definitions cannot supply it.
    """

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

    configured_model: str = ""
    """This launch's model configuration, never proof of the answering model.

    Render only with an explicit configured label when reported_model is absent.
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
    execution_receipt: object | None = field(default=None, repr=False, compare=False)
    """Detached requested/resolved/reported identity and usage for this call."""


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


def subprocess_env_without_api_keys() -> dict[str, str] | None:
    """Return this process's env with every model API-key variable removed."""
    env = os.environ.copy()
    for name in API_KEY_PROVIDER_ENV_VARS:
        env.pop(name, None)
    return env


class SandboxUnavailableError(PermissionError):
    """A code node or CLI has no admitted owner box to run in."""


def get_sandbox_status() -> dict[str, object]:
    """Whether this process serves owner boxes: ``{box_available, reason}``.

    The box provider is installed once at daemon start by the owner-box
    frontend; a process without one runs no owner code at all.
    """
    from tinyassets.owner_runtime import current

    if current() is not None:
        return {"box_available": True, "reason": ""}
    from tinyassets.agent_loop.served_chat import configured_box_provider

    provider, _ = configured_box_provider()
    if provider is None:
        return {"box_available": False, "reason": "no owner-box provider is configured"}
    return {"box_available": True, "reason": ""}


class BaseProvider(abc.ABC):
    """Abstract base for all LLM providers."""

    name: str = ""
    """Canonical connection identifier used in fallback chains."""

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
            "engine_tool_grant", "agent_node_id", "agent_node_key",
        )) or any(getattr(config, key, None) is not None for key in (
            "agent_request", "agent_session",
        )):
            raise ProviderAuthorityHeldError("text-only provider restriction conflicts with tools")

    @classmethod
    def is_available(cls) -> bool:
        """Return whether the executor is available."""
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

        *universe_dir* scopes the connection grant to its command center.
        """
