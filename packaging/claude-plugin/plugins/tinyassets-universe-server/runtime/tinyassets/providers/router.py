"""Provider router -- serves one universe's owner-authorized provider.

Hard invariant (AGENTS.md Hard Rule 15, 2026-09-24): the platform has no LLM.
Every call must carry one universe's owner authority -- a server-minted
``ProviderInvocationCarrier`` or a live provider request the router authorizes
against the owner's serving binding -- and is served only by the provider that
authority names, on that universe's own credentials. There is no platform
fallback chain, no host pin and no host-credential provider; a call without
owner authority is refused before any provider is touched
(``tinyassets/providers/owner_binding.py``). ``FALLBACK_CHAINS`` survives only
as the catalogue of built-in executor names status surfaces report.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import logging
import math
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from tinyassets.exceptions import (
    AllProvidersExhaustedError,
    InteractiveDeadlineError,
    ProviderAuthenticationError,
    ProviderAuthorityHeldError,
    ProviderError,
    ProviderIdleTimeoutError,
    ProviderModelRefusedError,
    ProviderOverloadedError,
    ProviderProtocolError,
    ProviderRateLimitedError,
    ProviderReplyTimeoutError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    SelectedModelCapacityError,
    SelectedModelContextError,
)
from tinyassets.provider_admission import ProviderBusy as _ProviderBusy

# Admission deadline refusals must release reservations for launches that never ran.
from tinyassets.provider_admission import (
    blocking_parent_slot,
    blocking_provider_child,
)
from tinyassets.provider_admission import provider_slot_async as _provider_slot
from tinyassets.provider_work_authority import (
    ProviderInvocationCarrier,
    ProviderInvocationReservationState,
    ProviderInvocationSettlementOwner,
)
from tinyassets.providers.base import (
    BaseProvider,
    ModelConfig,
    ProviderResponse,
    UniverseContext,
    api_key_providers_enabled,
)
from tinyassets.providers.diagnostics import (
    ProviderAttemptDiagnostic,
    admitted_tool_phase,
    build_chain_state,
    classify_unavailable,
    dominant_capacity_scope,
    dominant_failure_class,
    dominant_retry_after_s,
    finite_progress_age_ms,
    redacted_failure_detail,
)
from tinyassets.providers.owner_binding import (
    CONNECT_PROVIDER_MESSAGE,
    require_owner_bound_context,
    require_owner_bound_dispatch,
)
from tinyassets.providers.provider_jail import (
    ProviderConfinementError,
    provider_launch_scope,
)
from tinyassets.providers.quota import (
    COOLDOWN_OTHER,
    COOLDOWN_TIMEOUT,
    COOLDOWN_UNAVAILABLE,
    MAX_COOLDOWN_S,
    QuotaTracker,
)

if TYPE_CHECKING:
    from tinyassets.config import UniverseConfig

logger = logging.getLogger(__name__)

_CONNECT_PROVIDER_MESSAGE = CONNECT_PROVIDER_MESSAGE

# Per-call served output reservation when the caller sets no explicit max_tokens
# (the production `_sandboxed_config` converse path leaves it None). This MUST be
# decoupled from the binding's aggregate in-flight ceiling
# (ServedProviderAuthority.max_tokens): substituting the whole ceiling made each
# turn reserve the ENTIRE budget, so the second concurrent turn always bricked
# regardless of how high the ceiling was raised (Codex 2026-08-22). A bounded
# per-call reservation lets many concurrent turns share the ceiling. It is a
# reservation estimate, not a hard generation cap (the CLI subprocess is not
# passed a token limit — see finalize_served_provider_budget), so it only needs
# to be generous for one reply while small relative to the aggregate ceiling.
_SERVED_PER_CALL_MAX_TOKENS = 65_536


def _provider_invocation_carrier(
    universe_context: UniverseContext | None,
    *,
    role: str,
    operation: str | None,
) -> ProviderInvocationCarrier | None:
    carrier = universe_context.provider_invocation if universe_context else None
    if carrier is None:
        return None
    if operation is None:
        raise PermissionError("armed provider invocation requires an operation")
    if type(carrier) is not ProviderInvocationCarrier:
        raise PermissionError("provider invocation carrier is not server-owned")
    carrier.validate_for_call(role=role, operation=operation)
    return carrier

def _engine_route(cfg: ModelConfig) -> tuple[str, str] | None:
    """The engine MCP route this call's provider jail may reach, if any.

    The same three fields every adapter checks before wiring the engine server
    (``claude_provider._engine_mcp_flags``, ``codex_provider._codex_engine_mcp_args``);
    the route itself is re-read, owner-checked, by the jail's relay.
    """
    actor_id = (cfg.engine_mcp_actor_id or "").strip()
    graph_id = (cfg.engine_mcp_graph_id or "").strip()
    if not (cfg.engine_mcp_enabled and actor_id and graph_id):
        return None
    return actor_id, graph_id


def _resolve_universe_config(
    universe_context: UniverseContext | None,
) -> "UniverseConfig | None":
    """Resolve the effective UniverseConfig for a call.

    An explicit ``universe_context.config`` wins; otherwise fall back to the
    process-global ``runtime.universe_config`` (preserving today's
    single-universe-daemon behavior). Returns ``None`` only when neither is
    available.
    """
    if universe_context is not None and universe_context.config is not None:
        return universe_context.config
    try:
        from tinyassets import runtime_singletons as runtime

        return runtime.universe_config
    except Exception:
        return None


def _effective_universe_provider_ceiling(
    universe_context: UniverseContext | None,
    resolved_config: "UniverseConfig | None",
    *,
    carrier_armed: bool,
) -> list[str] | None:
    """Return the requester's provider ceiling, or legacy/platform ``None``.

    A server-minted invocation carrier is already pinned to one provider and
    remains subject to any explicit assignment allowlist. An unarmed explicit
    universe context is requester work: legacy ``allowed_providers=None`` may
    use only providers the universe itself selected, never the process-global
    fallback chain. Missing/empty selection holds before provider access.
    """
    from tinyassets.provider_authority import current

    universe_dir = universe_context.universe_dir if universe_context is not None else None
    if carrier_armed or universe_context is None:
        resolved_config = current(universe_dir, resolved_config)
        return (
            resolved_config.allowed_providers
            if resolved_config is not None
            else None
        )
    # An unarmed explicit context is requester authority. Its config must be
    # carried on the request; ``resolved_config`` may be the ambient runtime
    # fallback and therefore cannot establish requester authority here.
    requester_config = universe_context.config
    if requester_config is None:
        raise ProviderAuthorityHeldError(_CONNECT_PROVIDER_MESSAGE)
    requester_config = current(universe_dir, requester_config)
    if requester_config.allowed_providers is not None:
        ceiling = [
            str(provider).strip()
            for provider in requester_config.allowed_providers
            if str(provider).strip()
        ]
    elif universe_context.served_provider is not None:
        # The exact candidate was accepted by the owner and revalidated against
        # the current manifest. The legacy preferred_writer structural anchor
        # is not a separate ceiling on that membership. Explicit allowlists above
        # still win, including an explicitly empty list.
        ceiling = [universe_context.served_provider.provider]
    else:
        ceiling = list(dict.fromkeys(
            provider
            for provider in (
                str(requester_config.preferred_writer or "").strip(),
                str(requester_config.preferred_judge or "").strip(),
            )
            if provider
        ))
    if not ceiling:
        raise ProviderAuthorityHeldError(_CONNECT_PROVIDER_MESSAGE)
    return ceiling


def _default_config(resolved: "UniverseConfig | None" = None) -> ModelConfig:
    """Build default ModelConfig from the resolved universe config if available.

    ``resolved`` is the config produced by :func:`_resolve_universe_config`.
    When omitted, falls back to the process-global ``runtime.universe_config``
    so bare callers keep today's behavior.
    """
    try:
        if resolved is None:
            from tinyassets import runtime_singletons as runtime

            resolved = runtime.universe_config
        return ModelConfig(
            temperature=resolved.temperature,
            timeout=resolved.timeout,
            max_tokens=resolved.max_tokens,
        )
    except Exception:
        return ModelConfig()

# Built-in executor names per role (spec Section 8.3). NOT a routing chain:
# Hard Rule 15 removed platform fallback routing, and every call is served
# only by the provider its owner's authority names. Status surfaces still use
# this as the catalogue of executor names the host could register.
FALLBACK_CHAINS: dict[str, list[str]] = {
    "writer": ["claude-code", "codex", "ollama-local"],
    "judge": ["codex", "ollama-local"],
    "extract": ["codex", "ollama-local"],
    "embed": ["ollama-local"],
}


def _retry_after_cooldown_s(retry_after: object) -> int:
    """Cooldown seconds implied by a source's own ``Retry-After``.

    Honors it (+1s margin) when it is a usable positive number, bounded by
    ``MAX_COOLDOWN_S``; else falls back to the fixed unavailable cooldown. One
    definition, so an after-the-fact cooling (``ProviderRouter.cool_source``) and
    the capacity handler's own cooling cannot drift apart -- and one place to
    bound remote input, so no header can retire a source indefinitely.
    """
    if (
        isinstance(retry_after, (int, float))
        and not isinstance(retry_after, bool)
        and math.isfinite(retry_after)
        and retry_after > 0
    ):
        return min(int(retry_after) + 1, MAX_COOLDOWN_S)
    return COOLDOWN_UNAVAILABLE


def _rate_limit_cooldown_s(exc: BaseException) -> int:
    """Cooldown seconds for a genuine rate-limit / overload outcome."""
    return _retry_after_cooldown_s(getattr(exc, "retry_after", None))


def _live_interactive_turn():
    """The owner's live chat turn this call runs under, if any (turn_interrupt)."""
    from tinyassets.turn_interrupt import current

    return current()


def _stoppable_native_turn(operation, provider):
    """The live chat turn whose Stop may cancel this dispatch, or ``None``.

    Only a NATIVE agent on the ``converse`` operation: cancellation is what ends
    its process family. An HTTP request cannot be aborted (its provider waits for
    it even when cancelled), so it returns and its real usage settles, and the
    turn stops at its next boundary (``tinyassets/turn_interrupt``). A Stop
    already asked for cancels before anything is claimed or launched, so the
    reservation is released and the carrier settles cancelled-before-launch
    rather than indeterminate.
    """
    if operation != "converse":
        return None
    if getattr(provider, "agent_execution_kind", None) != "native_agent":
        return None
    return _live_interactive_turn()


def _sync_call_timeout_s(cfg: ModelConfig) -> float:
    """Timeout for a sync-wrapper call: at least the stream absolute cap.

    The streaming served path is judged by its idle watchdog + absolute cap
    (``stream_timeout_profile().absolute_cap_s``, default 600s), NOT the legacy
    ``timeout`` scalar. A sync wrapper firing at ``timeout + 30`` (330s by
    default) would return failure while the subprocess kept streaming up to the
    600s cap (blocker L). Take the larger of the legacy timeout and the absolute
    cap, plus a margin for async overhead + the in-band reap.
    """
    try:
        absolute_cap = cfg.stream_timeout_profile().absolute_cap_s
    except Exception:  # noqa: BLE001 - a malformed cfg falls back to the legacy path
        absolute_cap = 0.0
    legacy = float(getattr(cfg, "timeout", 0) or 0)
    return max(legacy, absolute_cap) + 30.0


# Below this, the gap between submit() and worker pickup is scheduling jitter,
# not queue wait, and correcting for it would only add noise. Same value and
# same reasoning as the compiler's own subtraction one hop earlier
# (``graph_compiler._QUEUE_WAIT_SUBTRACT_THRESHOLD_S``).
_QUEUE_WAIT_SUBTRACT_THRESHOLD_S = 0.05
# An epsilon, NOT a budget. An already-expired call is refused outright at the
# worker entry, so this never has to invent time for one; it exists because
# ``ModelConfig.stream_timeout_profile()`` discards a non-positive cap and
# substitutes the 600s default, which would invert the correction.
_MIN_POSITIVE_PROVIDER_CAP_S = 0.001


def _caller_deadline_budget_s(cfg: ModelConfig) -> float | None:
    """The caller's EXPLICIT remaining-budget hand-over in seconds, or ``None``.

    ``absolute_cap_s`` is how a node's remaining budget crosses into the
    provider layer (``graph_compiler._deadline_cfg``). It is also the only field
    that *can* carry one: the legacy ``timeout`` scalar is an int with a
    ``max(1, ...)`` representation floor, so it cannot tell "0.2s left" from "1s
    left" and must never be read as a deadline.

    ``None`` means no deadline was handed over — a default config leaves the cap
    unset and resolves to the 600s backstop. Such a call is never refused.
    """
    explicit = getattr(cfg, "absolute_cap_s", None)
    if not isinstance(explicit, (int, float)) or isinstance(explicit, bool):
        return None
    value = float(explicit)
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def _queue_adjusted_config(
    cfg: ModelConfig, budget_s: float | None, waited_s: float,
) -> ModelConfig:
    """``cfg`` with the provider-sync queue wait already deducted.

    Returns a NEW config — ``ModelConfig`` is frozen and one caller config is
    shared across concurrent invocations, so it is never mutated. With no
    caller deadline to deduct from, or a wait below the jitter threshold, the
    caller's own config is handed over untouched.
    """
    if budget_s is None or waited_s < _QUEUE_WAIT_SUBTRACT_THRESHOLD_S:
        return cfg
    remaining = max(_MIN_POSITIVE_PROVIDER_CAP_S, budget_s - waited_s)
    legacy = getattr(cfg, "timeout", 0)
    try:
        legacy_int = int(legacy)
    except (TypeError, ValueError):
        legacy_int = 0
    return replace(
        cfg,
        # Only ever lowered. The legacy int-seconds scalar carries its own
        # max(1, int(...)) representation floor for the non-streaming
        # providers, and ``budget_s`` may exceed it (a caller can set a large
        # absolute cap with a small legacy timeout), so it is clamped to the
        # caller's own value — deducting a queue wait must never buy a call
        # more time than it arrived with.
        timeout=min(legacy_int, max(1, int(remaining))) if legacy_int > 0
        else max(1, int(remaining)),
        absolute_cap_s=remaining,
    )


def _side_effect_from(exc: BaseException) -> str | None:
    """Read the streamed-attempt ``side_effect_state`` off a raised exception.

    The streaming reader attaches an ``attempt_telemetry`` snapshot (blocker K);
    surfacing ``side_effect_state`` into the ProviderAttemptDiagnostic lets the
    sole-writer retry policy know whether a tool may have run before the attempt
    failed. ``None`` for non-streaming raises (dropped from the diagnostic dict).
    """
    tele = getattr(exc, "attempt_telemetry", None)
    if isinstance(tele, dict):
        state = tele.get("side_effect_state")
        if isinstance(state, str):
            return state
    return None


def _tool_wait_evidence(exc: BaseException) -> dict[str, Any]:
    """Read the VALIDATED tool-phase / progress-age evidence off a raise.

    Exactly two scalars, each through the shared validator in ``diagnostics``.
    The reader's ``attempt_telemetry`` snapshot is never copied wholesale, so a
    tool name, a tool argument, a tool identity, a prompt, a provider error
    string or a credential cannot reach the persisted run read along this path
    — a key this function does not name simply does not travel. Unknown or
    malformed evidence is omitted, leaving the failure class unchanged.
    """
    tele = getattr(exc, "attempt_telemetry", None)
    if not isinstance(tele, dict):
        return {}
    out: dict[str, Any] = {}
    phase = admitted_tool_phase(tele.get("tool_phase"))
    if phase is not None:
        out["tool_phase"] = phase
    age = finite_progress_age_ms(tele.get("last_progress_age_ms"))
    if age is not None:
        out["last_progress_age_ms"] = age
    return out


# Sync graph nodes call async provider routing through this bounded pool.
# Keep it above 1 so an unrelated slow provider call does not serialize all
# other sync callers behind one shared worker.
#
# It also acts as a second ceiling on concurrent provider subprocesses, which is a
# property worth knowing about rather than relying on: admission
# (`TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS`, default 6) is the bound sized against
# memory and observable through `get_status.provider_admission`. Raising THIS number to
# unlock concurrency would raise memory pressure with nothing reporting it — I proposed
# exactly that and the arithmetic behind it was wrong, so it stays where it is until a
# real turn's high-water is measured.
_SYNC_CALL_MAX_WORKERS: int = 8

# NOTE: `_provider_slot` (imported above) bounds concurrent provider SUBPROCESSES.
# _SYNC_CALL_MAX_WORKERS bounds threads, which are cheap; a subprocess is ~77 MB.


def _is_nested(universe_context) -> bool:
    """Is this call spawned BY a turn that already holds a slot?

    `run_graph` child calls carry a typed `provider_invocation` carrier, so the answer
    is available exactly where it is needed. Nested work draws on the reserve, because
    otherwise a served turn holding a slot starves the children it created and both
    fail (Codex round 3).
    """
    return bool(getattr(universe_context, "provider_invocation", None))


@asynccontextmanager
async def _routable_authorization(inner, selection):
    """Turn a sign-in refusal raised while AUTHORIZING into a routable outcome.

    The pre-launch credential refresh runs inside this context manager, so a stored
    sign-in the source has finished with is discovered BEFORE ``_call_routed`` is
    entered -- which means the provider loop's ``except ProviderAuthenticationError``
    never sees it and the exception escapes the turn entirely. Measured: the founder's
    spent-refresh-token path ended with the turn ``abandoned``, no fallback and no
    reply, which is the outage this exists to fix.

    So it is re-raised as the aggregate the loop would have produced had the provider
    itself refused: one ``auth_invalid`` attempt, which both turn-coordinator fallback
    handlers require. ``side_effect_state`` is ``none`` as a FACT here, not an
    attestation -- nothing was launched, because authorization did not finish -- and
    that is stronger evidence than any adapter can offer about a run that did start.

    Only ``ProviderAuthenticationError`` is converted. A held authority, a permission
    refusal or a storage fault keeps travelling as itself; turning those into
    "exhausted" would report a provider that was never asked as having failed.
    """
    try:
        async with inner as authority:
            yield authority
    except ProviderAuthenticationError as exc:
        raise AllProvidersExhaustedError(
            "the stored sign-in for this source is no longer accepted",
            attempts=[ProviderAttemptDiagnostic(
                provider=getattr(selection, "connection_id", "") or "",
                status="failed", skip_class="auth_invalid",
                detail=redacted_failure_detail(str(exc)),
                failure_class="auth_invalid", side_effect_state="none",
            )],
            failure_class="auth_invalid",
        ) from None


class ProviderRouter:
    """Routes one universe's owner-authorized LLM call, with quota tracking.

    Parameters
    ----------
    providers : dict[str, BaseProvider]
        Map from provider name to provider instance.  Only providers
        present in this dict are reachable.
    quota : QuotaTracker | None
        Shared quota tracker.  A default is created if not supplied.
    chain_drain_empty_threshold, auth_health
        Accepted for caller compatibility and never used. The first detected
        a drained platform chain falling back to the host's local model; the
        second probed the HOST's subscription login -- the codex probe being
        itself a real ``codex exec`` on the host's credentials. Hard Rule 15
        removed the chain, so neither has anything to act on.
    """

    def __init__(
        self,
        providers: dict[str, BaseProvider] | None = None,
        quota: QuotaTracker | None = None,
        chain_drain_empty_threshold: int | None = None,
        auth_health: Callable[[str], dict[str, str]] | None = None,
    ) -> None:
        # ``chain_drain_empty_threshold`` and ``auth_health`` are accepted for
        # caller compatibility and never used: both served the retired platform
        # fallback chain (local-model drain detection, host-login pruning).
        del chain_drain_empty_threshold, auth_health
        self._providers: dict[str, BaseProvider] = providers or {}
        self._quota = quota or QuotaTracker()

    # ------------------------------------------------------------------
    # Shared health
    # ------------------------------------------------------------------

    def _cool(
        self, cfg: Any, provider_name: str, seconds: int, *, daily_detail: str = "",
        reason: str = "", owner: str = "",
    ) -> bool:
        """The ONE place an in-flight attempt writes the shared cooldown map.

        Returns whether it was written. A ``ModelConfig.secondary_call`` is
        refused: a call the founder never made must not decide what their next
        turn may do (see ``ModelConfig.secondary_call`` for the live incident).
        Skipping the write is strictly LESS restrictive than writing it, so this
        can only ever make the router try an authorized source MORE -- it admits
        no model, widens no grant and raises no ceiling.

        Reading the gate is unchanged: a secondary call still skips a source
        already cooling, so it never hammers one.
        """
        if getattr(cfg, "secondary_call", False):
            logger.info(
                "Not cooling %s for %ds: secondary call, the founder's next turn "
                "keeps this source", provider_name, seconds,
            )
            return False
        # One write site (test_every_router_cooldown_write_goes_through_the_guard).
        # The owner comes from admitted authority, never an executor's name.
        extra = {"daily_detail": daily_detail} if daily_detail else {}
        self._quota.cooldown(provider_name, seconds, owner=owner, **extra)
        # Why, kept beside the map: a later call skipped by this cooldown says
        # what it is waiting out. Live 2026-10-01 a run skipped a cooled source
        # and reported only "account scope", which read as a spent allowance.
        self.__dict__.setdefault("_cool_reasons", {})[(owner, provider_name)] = reason
        return True

    def cooldown_reason(self, provider_name: str, *, owner: str = "") -> str:
        """The failure class that put ``provider_name`` in cooldown, or ``""``."""
        return self.__dict__.get("_cool_reasons", {}).get((owner, provider_name), "")

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------

    def register(self, provider: BaseProvider) -> None:
        """Add or replace a provider in the registry."""
        self._providers[provider.name] = provider

    @property
    def available_providers(self) -> list[str]:
        return list(self._providers)

    def effective_chain(
        self,
        chain: list[str],
    ) -> tuple[list[str], list[ProviderAttemptDiagnostic]]:
        """Return registered providers from *chain* plus explicit exclusions.

        ``FALLBACK_CHAINS`` records preference order, but runtime routing must
        only advertise and iterate providers that were actually registered at
        startup. Missing CLI-backed providers, such as ``claude-code`` in the
        cloud image, are reported as exclusions rather than silent phantom
        entries at the front of the live chain.
        """
        effective: list[str] = []
        excluded: list[ProviderAttemptDiagnostic] = []
        for provider_name in chain:
            if provider_name in self._providers:
                effective.append(provider_name)
                continue
            excluded.append(ProviderAttemptDiagnostic(
                provider=provider_name,
                status="skipped",
                skip_class="not_in_registry",
                detail="provider name not registered with daemon",
            ))
        return effective, excluded

    # ------------------------------------------------------------------
    # Core routing
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_allowlist(
        chain: list[str], allowlist: list[str] | None,
    ) -> list[str]:
        """Filter *chain* down to providers in *allowlist*.

        ``allowlist=None`` is a no-op (returns chain unchanged). An empty list
        filters everything out — the caller is responsible for hard-failing
        with ``AllProvidersExhaustedError`` so the policy block is visible.
        """
        if allowlist is None:
            return chain
        return [p for p in chain if p in allowlist]

    def cool_source(
        self, provider: str, *, owner: str, retry_after_s=None, reason: str = "",
    ) -> int:
        """Put a source in cooldown after the fact. Restrictive only.

        Cooling can only ever make this router try a source LESS, so this is
        safe to expose: it admits no model, widens no grant, raises no ceiling,
        and cannot make an ineligible source eligible.

        It exists because the capacity handler withholds a zero-cost source's
        cooldown to leave a sibling attempt possible, and only the turn
        coordinator knows whether one actually followed. Honours the source's
        own ``Retry-After`` when it supplied one; returns the seconds applied.
        """
        if type(provider) is not str or not provider:
            raise ValueError("cooling a source requires its provider name")
        if not isinstance(owner, str) or not owner:
            raise ValueError("cooling a source requires its owner")
        seconds = _retry_after_cooldown_s(retry_after_s)
        # Through the one guarded door (``_cool``), so the secondary-call rule
        # cannot be bypassed by a future caller of this seam. ``None`` for the
        # config is the honest value: this is an AFTER-THE-FACT cooling by the
        # turn coordinator, which only ever follows a founder-facing turn, so it
        # is never secondary -- and saying so beats leaving a second unguarded
        # write of the shared map.
        self._cool(None, provider, seconds, owner=owner, reason=reason or "capacity refusal")
        return seconds

    def selected_agent_execution_kind(self, selection) -> str:
        """Advisory installed capability; actual dispatch rechecks the resolved executor."""
        provider = self._providers.get(selection.connection_id)
        kind = getattr(provider, "agent_execution_kind", None)
        if kind not in ("native_agent", "engine_inference"):
            raise ProviderAuthorityHeldError("selected provider has no installed agent executor")
        return kind

    async def call(
        self,
        role: str,
        prompt: str,
        system: str,
        config: ModelConfig | None = None,
        *,
        operation: str | None = None,
        universe_context: UniverseContext | None = None,
        _agent_observer=None,
        _agent_execution_kind=None,
    ) -> ProviderResponse:
        """Route a call, fencing founder-facing served turns before launch."""
        # Hard Rule 15: no universe-owner authority, no provider. Before any
        # provider is resolved, probed or configured.
        require_owner_bound_context(universe_context, operation=operation)
        from tinyassets.providers.agent_inference import AgentInferenceRequest

        ordinary_text = (
            config is not None and type(config.agent_request) is AgentInferenceRequest
            and config.agent_request.text_only
        )
        work_agent = (
            universe_context is not None
            and type(universe_context.provider_invocation) is ProviderInvocationCarrier
            and universe_context.provider_request is None
            and universe_context.served_provider is None
            and operation in {"run_graph", "background_branch_run"}
            and operation == universe_context.provider_invocation.operation
            and callable(_agent_observer)
        )
        if _agent_execution_kind is not None and (
            _agent_execution_kind not in ("native_agent", "engine_inference")
            or role != "writer" or (operation != "converse" and not work_agent)
            or config is None or not (config.engine_mcp_enabled or ordinary_text)
            or universe_context is None or universe_context.model_selection is None
            or (universe_context.provider_invocation is not None and not work_agent)
        ):
            raise PermissionError("agent step requires the selected served writer")

        # A context that ALREADY carries an authorized ServedProviderAuthority (the
        # daemon-owned background consumer fences its own authority per call in
        # background_served_provider._authorize_launch and injects it here) must
        # NOT be re-authorized as a founder-facing served turn: that path demands a
        # live provider_request the background has none of, so it raised
        # ProviderAuthorityHeldError and the real background path could never
        # launch (Codex REJECT #1, PR #2528). Only an UN-authorized context is
        # fenced here.
        # REVERTED (Codex REJECT #4 on #2531): an earlier head honoured a pre-set
        # ``served_provider`` on the context so the background consumer could inject
        # its authority. ServedProviderAuthority is a plain dataclass, and no
        # in-process provenance scheme (registry, sentinel fence) survived review:
        # 'if arbitrary in-process Python is the adversary, no underscore, sentinel,
        # or same-process secret suffices'. The correct route for background is the
        # server-minted, pid-bound, ONE-USE ProviderInvocationCarrier (the
        # ``provider_invocation`` branch below) - the next lane. Until then a context
        # without a carrier is re-fenced as a served turn: fail closed.
        if universe_context is not None and universe_context.provider_invocation is None:
            from tinyassets.provider_assignment import authorize_served_provider_call

            if (
                universe_context.universe_dir is None
                or universe_context.provider_request is None
                or not operation
            ):
                raise ProviderAuthorityHeldError(_CONNECT_PROVIDER_MESSAGE)
            universe_dir = universe_context.universe_dir
            if universe_context.model_selection is not None:
                from tinyassets.provider_assignment import authorize_served_provider_call_async

                agent_turn = (_agent_execution_kind is not None
                              or config is not None and config.agent_request is not None)
                async with _routable_authorization(
                    authorize_served_provider_call_async(
                        universe_dir.parent,
                        universe_dir=universe_dir,
                        request_carrier=universe_context.provider_request,
                        role=role, operation=operation,
                        model_selection=universe_context.model_selection,
                        **({"agent_turn": True} if agent_turn and not ordinary_text else {}),
                    ),
                    universe_context.model_selection,
                ) as authority:
                    if _agent_observer is not None:
                        if not agent_turn or not callable(_agent_observer):
                            raise PermissionError("agent observer requires an agent inference")
                        authority = replace(authority, after_provider_claim=_agent_observer)
                    return await self._call_routed(
                        role, prompt, system, config, operation=operation,
                        universe_context=replace(universe_context, served_provider=authority),
                        _agent_execution_kind=_agent_execution_kind,
                    )
            with authorize_served_provider_call(
                universe_dir.parent,
                universe_dir=universe_dir,
                request_carrier=universe_context.provider_request,
                role=role,
                operation=operation,
            ) as authority:
                authorized_context = replace(
                    universe_context,
                    served_provider=authority,
                )
                return await self._call_routed(
                    role,
                    prompt,
                    system,
                    config,
                    operation=operation,
                    universe_context=authorized_context,
                )
        return await self._call_routed(
            role,
            prompt,
            system,
            config,
            operation=operation,
            universe_context=universe_context,
            **({"_agent_execution_kind": _agent_execution_kind,
                "_work_agent_observer": _agent_observer} if work_agent else {}),
        )

    async def _call_routed(self, role, prompt, system, config=None, **kwargs):
        """A standalone authorized call owns a root; nested calls must borrow it."""
        from tinyassets.request_budget import (
            FREE_TURN_ATTEMPTS,
            TEXT_TURN_ATTEMPTS,
            TurnRequestBudget,
            current_request_budget,
            request_budget_scope,
        )

        context = kwargs.get("universe_context")
        cfg = config or _default_config(_resolve_universe_config(context))
        if cfg.request_budget is not None or current_request_budget() is not None:
            return await self._call_routed_attempts(role, prompt, system, cfg, **kwargs)
        authority = context.served_provider if context is not None else None
        carrier = context.provider_invocation if context is not None else None
        if authority is not None:
            owner = authority.owner_user_id
        elif type(carrier) is ProviderInvocationCarrier:
            owner = carrier._receipt.principal_id
        else:
            # Existing admission code owns the exact refusal and creates no root.
            return await self._call_routed_attempts(role, prompt, system, cfg, **kwargs)
        limit = FREE_TURN_ATTEMPTS if cfg.engine_mcp_enabled else TEXT_TURN_ATTEMPTS
        budget = TurnRequestBudget(owner, context.universe_dir.name,
                                   free_limit=limit, free_pool_limit=limit)
        with request_budget_scope(budget):
            response = await self._call_routed_attempts(
                role, prompt, system, replace(cfg, request_budget=budget), **kwargs,
            )
        return replace(response, request_receipt=budget.receipt())

    async def _call_routed_attempts(
        self,
        role: str,
        prompt: str,
        system: str,
        config: ModelConfig | None = None,
        *,
        operation: str | None = None,
        universe_context: UniverseContext | None = None,
        _agent_execution_kind=None,
        _work_agent_observer=None,
    ) -> ProviderResponse:
        """Route a single call through the fallback chain for *role*.

        Returns a :class:`ProviderResponse` on success.  For judge role,
        returns a degraded sentinel when all providers are exhausted.
        For other roles, raises :class:`AllProvidersExhaustedError`.

        ``universe_context``, when supplied, resolves this call's engine
        preference + allowlist + vault-backed auth from an EXPLICIT argument
        instead of the process globals — the multi-universe seam.
        """
        invocation_carrier = _provider_invocation_carrier(
            universe_context,
            role=role,
            operation=operation,
        )
        carrier_settled = False
        router_settles_carrier = (
            invocation_carrier is not None
            and invocation_carrier.settlement_owner
            is ProviderInvocationSettlementOwner.ROUTER
        )

        def settle_carrier(
            state: ProviderInvocationReservationState,
            *,
            input_tokens: int | None = None,
            output_tokens: int | None = None,
            cost_microunits: int | None = None,
        ) -> None:
            nonlocal carrier_settled
            if not router_settles_carrier or carrier_settled:
                return
            try:
                invocation_carrier.settle(
                    state,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_microunits=cost_microunits,
                )
            except Exception as exc:
                # Name the CAUSE. The bare message sent a founder in circles for
                # two days on 2026-08-27: every prompt-template run failed with
                # "provider invocation usage could not be settled" and neither
                # the universe nor its founder could tell a budget exhaustion
                # from a carrier-lifecycle error from a storage failure. The
                # `settle()` path alone has four distinct PermissionError exits.
                #
                # `__cause__` was always attached; nothing surfaced it, because
                # the message the user sees is built from this string. Keeping
                # the wrapper type (callers branch on it) and appending the
                # cause costs nothing and makes the failure actionable.
                raise ProviderAuthorityHeldError(
                    "provider invocation usage could not be settled: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            carrier_settled = True
        served_authority = universe_context.served_provider if universe_context else None
        resolved_config = _resolve_universe_config(universe_context)
        universe_dir = universe_context.universe_dir if universe_context else None
        cfg = config or _default_config(resolved_config)
        from tinyassets.request_budget import (
            TurnRequestBudget,
            current_request_budget,
            metered_free_source,
        )

        parent_budget = current_request_budget()
        request_budget = cfg.request_budget or parent_budget
        if request_budget is not None and (
            type(request_budget) is not TurnRequestBudget
            or parent_budget is not None and parent_budget is not request_budget
        ):
            raise ProviderAuthorityHeldError("invalid parent request budget")
        cfg = replace(cfg, request_budget=request_budget, request_attempt=None,
                      invocation_owner_user_id="")
        if _work_agent_observer is not None:
            if (type(invocation_carrier) is not ProviderInvocationCarrier
                    or not callable(_work_agent_observer) or _agent_execution_kind is None):
                raise PermissionError("work agent observer requires admitted inference")
            # Inert caller configuration cannot select another tool identity.
            cfg = replace(cfg, engine_mcp_actor_id=invocation_carrier._receipt.principal_id,
                          engine_mcp_graph_id=invocation_carrier._receipt.universe_id)
        # Selection is a validated per-attempt fact, never an ordinary caller's
        # ModelConfig preference. Preserve legacy calls by clearing any injected
        # selection when there is no selected-model serving authority.
        quota_owner = (served_authority.owner_user_id if served_authority is not None
                       else invocation_carrier._receipt.principal_id
                       if invocation_carrier is not None else "")
        model_authority = served_authority or invocation_carrier
        cfg = replace(cfg, selected_model=getattr(model_authority, "selected_model", None))
        from tinyassets.providers.native_model_selection import NativeSelection

        native_selection = getattr(model_authority, "native_selection", None)
        if native_selection is not None and (
            type(native_selection) is not NativeSelection
            or native_selection.provider != model_authority.provider
        ):
            raise PermissionError("native selection does not match serving authority")
        cfg = replace(cfg, native_model_id=(
            None if model_authority is None else
            native_selection.requested_model_id if native_selection is not None else ""
        ))
        # Effort rides the same validated authority as the model id, for the
        # same reason: an ordinary caller's ModelConfig must not be able to
        # raise the effort of a served turn.
        #
        # Only an ENUMERATED selection speaks for effort, and it speaks
        # absolutely -- including with "" , which means the owner saved no level
        # and the executor's default applies. An owner-DECLARED selection has no
        # advertised level list, so it could not have validated one and has
        # nothing to say here; overwriting from it would erase a workflow node's
        # own declared reasoning_effort (branch definitions set it, and a node
        # running on a native source with an explicit model id would silently
        # drop from `minimal` to the executor default).
        if native_selection is not None and native_selection.basis == "executor_enumerated":
            cfg = replace(cfg, reasoning_effort=native_selection.effort)
        if _agent_execution_kind == "native_agent" and (
            cfg.agent_request is not None or cfg.selected_model is not None
        ):
            raise PermissionError("native agent cannot use HTTP inference facts")
        if _agent_execution_kind == "engine_inference" and cfg.agent_request is None:
            raise PermissionError("engine inference requires its structured request")
        from tinyassets.providers.agent_inference import (
            context_tokens,
            input_size,
            output_for_settlement,
        )

        if cfg.agent_request is not None and (
            cfg.selected_model is None or not (
                cfg.engine_mcp_enabled or cfg.agent_request.text_only
            )
            or role != "writer" or (
                operation != "converse" and not (
                    _work_agent_observer is not None
                    and operation == invocation_carrier.operation
                    and cfg.selected_model is not None and cfg.selected_model.supports_tools
                )
            )
        ):
            raise PermissionError("agent inference requires the selected served writer")
        if cfg.selected_model is not None:
            if cfg.selected_model.provider != model_authority.provider:
                raise PermissionError("selected model does not match serving authority")
            if cfg.engine_mcp_enabled and cfg.agent_request is None:
                raise PermissionError("selected HTTP agent tool execution is not implemented yet")
        if served_authority is not None:
            if (
                operation != served_authority.operation
                or role not in served_authority.allowed_roles
            ):
                raise PermissionError("provider role or operation is outside authority")
            if served_authority.max_tokens < 1:
                raise PermissionError("served provider authority has no token budget")
            if served_authority.max_cost_microunits < 1:
                raise PermissionError("served provider authority has no cost budget")
            if cfg.max_tokens is None:
                # Reserve a BOUNDED per-call output, not the whole aggregate
                # ceiling — otherwise the first turn reserves the entire budget
                # and the second concurrent turn bricks (Codex 2026-08-22). Cap
                # to the ceiling so a small binding still validates.
                output_limit = min(served_authority.max_tokens, _SERVED_PER_CALL_MAX_TOKENS)
                if cfg.selected_model is not None:
                    # The chosen output limit is itself part of the encoded
                    # agent request. Measure with that field present; otherwise
                    # adding it can overflow an exactly filled context afterward.
                    required_input = context_tokens(
                        prompt, system, replace(cfg, max_tokens=output_limit),
                    )
                    output_limit = min(
                        output_limit, cfg.selected_model.context_tokens - required_input,
                    )
                    if output_limit < 1:
                        raise SelectedModelContextError(
                            "selected model cannot fit this inference context",
                            required_tokens=required_input + 1,
                        )
                cfg = replace(cfg, max_tokens=output_limit)
            elif (
                isinstance(cfg.max_tokens, bool)
                or not isinstance(cfg.max_tokens, int)
                or cfg.max_tokens < 0
                or cfg.max_tokens > served_authority.max_tokens
            ):
                raise PermissionError("provider call exceeds served token ceiling")
            cfg = replace(
                cfg,
                credential_snapshot_dir=served_authority.credential_snapshot_dir,
            )
            chain = [served_authority.provider]
        elif invocation_carrier is not None:
            if invocation_carrier.max_tokens < 1:
                raise PermissionError("armed provider invocation has no positive token budget")
            if invocation_carrier.max_cost_microunits < 1:
                raise PermissionError("armed provider invocation has no positive cost budget")
            if cfg.max_tokens is None:
                output_limit = invocation_carrier.max_tokens
                if cfg.selected_model is not None:
                    required_input = context_tokens(
                        prompt, system, replace(cfg, max_tokens=output_limit),
                    )
                    output_limit = min(
                        output_limit, cfg.selected_model.context_tokens - required_input,
                    )
                    if output_limit < 1:
                        # Our own measurement, before any launch: this call's
                        # reservation charged nothing, so it must not hold the
                        # run's aggregate budget the next model needs.
                        settle_carrier(
                            ProviderInvocationReservationState.CANCELLED_BEFORE_LAUNCH,
                            input_tokens=0, output_tokens=0, cost_microunits=0,
                        )
                        raise SelectedModelContextError(
                            "selected model cannot fit this workflow context",
                            required_tokens=required_input + 1,
                        )
                cfg = replace(cfg, max_tokens=output_limit)
            elif (
                isinstance(cfg.max_tokens, bool)
                or not isinstance(cfg.max_tokens, int)
                or cfg.max_tokens < 0
                or cfg.max_tokens > invocation_carrier.max_tokens
            ):
                raise PermissionError("provider call exceeds armed token ceiling")
            chain = [invocation_carrier.provider]
        else:
            # Unreachable past ``call``'s entry gate; kept as a hard stop so a
            # future internal caller cannot rebuild a platform fallback chain.
            require_owner_bound_dispatch(
                "", universe_dir=universe_dir,
                served_authority=None, invocation_carrier=None,
            )

        if cfg.selected_model is not None:
            if invocation_carrier is not None and cfg.selected_model.cost_upper_bound(
                cfg.max_tokens,
            ) > invocation_carrier.max_cost_microunits:
                raise PermissionError("selected model exceeds this workflow cost allowance")
            # A conservative TOKEN estimate against the window (the reservation
            # below keeps the byte measure). The selected catalogue's context
            # limit is not a permission to truncate.
            required_context = context_tokens(prompt, system, cfg)
            if (
                cfg.max_tokens is None
                or required_context + cfg.max_tokens > cfg.selected_model.context_tokens
            ):
                if invocation_carrier is not None:
                    settle_carrier(
                        ProviderInvocationReservationState.CANCELLED_BEFORE_LAUNCH,
                        input_tokens=0, output_tokens=0, cost_microunits=0,
                    )
                raise SelectedModelContextError(
                    "selected model cannot fit this inference context",
                    required_tokens=required_context + (cfg.max_tokens or 1),
                )

        # Hard Rule 15: the owner's binding alone names the provider. There is
        # no host pin (``TINYASSETS_PIN_WRITER`` is retired: a host env var must
        # never choose, narrow or probe the provider serving a universe) and no
        # preference over a platform fallback chain, because there is no chain.

        # Q6.3 — apply per-universe allowlist (privacy primitive).
        if served_authority is not None:
            # The served authority is the explicitly-bound provider, but it must ALSO
            # sit within the universe's privacy allowlist — a minted served authority
            # must NOT bypass allowed_providers (Codex serve-open-compute review #2).
            # No ceiling set = allow (backwards-compatible); a ceiling that excludes the
            # served provider empties the chain -> fail closed below.
            _served_ceiling = _effective_universe_provider_ceiling(
                universe_context,
                resolved_config,
                carrier_armed=False,
            )
            allowlist = (
                [served_authority.provider]
                if _served_ceiling is None
                or served_authority.provider in _served_ceiling
                else []
            )
        else:
            allowlist = _effective_universe_provider_ceiling(
                universe_context,
                resolved_config,
                carrier_armed=invocation_carrier is not None,
            )
        if allowlist is not None:
            filtered = self._apply_allowlist(chain, allowlist)
            if not filtered:
                logger.warning(
                    "Q6.3 allowlist empties chain for role=%s: chain=%s "
                    "filtered against allowed_providers=%s; hard-failing.",
                    role, chain, allowlist,
                )
                raise AllProvidersExhaustedError(
                    f"All providers for role={role!r} are blocked by the "
                    f"command center's allowed_providers={allowlist!r}. Daemon will "
                    f"not silently fall back to a disallowed provider."
                )
            chain = filtered

        # FEAT-006 / BUG-025: collect per-provider skip/failure diagnostics so
        # the final AllProvidersExhaustedError can carry structured detail.
        attempts: list[ProviderAttemptDiagnostic] = []
        native_proofs = {}

        for provider_name in chain:
            # Hard Rule 15, at the launch site: only the provider the owner's
            # authority names, resolving the universe's credentials, never a
            # host-credential built-in. Settled as never launched on refusal.
            provider = self._providers.get(provider_name)
            try:
                require_owner_bound_dispatch(
                    provider_name,
                    provider=provider,
                    universe_dir=universe_dir,
                    served_authority=served_authority,
                    invocation_carrier=invocation_carrier,
                )
            except ProviderAuthorityHeldError:
                if invocation_carrier is not None:
                    settle_carrier(
                        ProviderInvocationReservationState.CANCELLED_BEFORE_LAUNCH,
                        input_tokens=0, output_tokens=0, cost_microunits=0,
                    )
                raise
            if (
                (
                    served_authority is not None
                    and provider_name == served_authority.provider
                )
                or (
                    invocation_carrier is not None
                    and provider_name == invocation_carrier.provider
                )
            ) and provider_name.startswith("api_key_http:"):
                # Do NOT trust the mutable registry for an authorized OPEN provider.
                # A substituted same-name object could receive the prompt without
                # using the definition's current grant or credential-blind proxy.
                # Served and foreground-run authority therefore share the same
                # fresh, content-addressed definition -> executor resolution.
                try:
                    from tinyassets.providers.definition import get_definition
                    from tinyassets.providers.provider_resolver import (
                        provider_for_definition,
                    )

                    if universe_dir is not None:
                        _uid = universe_dir.name
                    elif served_authority is not None:
                        _uid = served_authority.universe_id
                    else:
                        raise PermissionError(
                            "open provider invocation requires a command center context"
                        )
                    _def_id = provider_name.split("api_key_http:", 1)[-1]
                    _definition = get_definition(_uid, _def_id)
                    if _definition is None:
                        raise PermissionError(
                            "open provider definition is absent"
                        )
                    provider = provider_for_definition(_definition)
                except Exception as exc:
                    if invocation_carrier is not None:
                        settle_carrier(
                            ProviderInvocationReservationState.CANCELLED_BEFORE_LAUNCH,
                            input_tokens=0,
                            output_tokens=0,
                            cost_microunits=0,
                        )
                        raise ProviderAuthorityHeldError(
                            _CONNECT_PROVIDER_MESSAGE
                        ) from exc
                    if isinstance(exc, PermissionError):
                        raise
                    raise PermissionError(
                        "open served provider could not be authenticated"
                    ) from exc
            if provider is None:
                logger.info("Provider %s not in registry, skipping", provider_name)
                attempts.append(ProviderAttemptDiagnostic(
                    provider=provider_name, status="skipped",
                    skip_class="not_in_registry",
                    detail="provider name not registered with daemon",
                ))
                continue
            try:
                # After fresh executor resolution, before quota or launch. A
                # refusal must not enter capacity fallback or lose the mode.
                BaseProvider.require_text_only_support(provider, cfg)
            except ProviderAuthorityHeldError:
                if invocation_carrier is not None:
                    settle_carrier(
                        ProviderInvocationReservationState.CANCELLED_BEFORE_LAUNCH,
                        input_tokens=0, output_tokens=0, cost_microunits=0,
                    )
                raise
            if _agent_execution_kind is not None and (
                getattr(provider, "agent_execution_kind", None) != _agent_execution_kind
            ):
                raise PermissionError("selected agent executor changed before dispatch")
            if not self._quota.available(provider_name, owner=quota_owner):
                logger.info("Skipping %s (quota/cooldown)", provider_name)
                cd = self._quota.cooldown_remaining(provider_name, owner=quota_owner)
                daily = self._quota.daily_detail(provider_name, owner=quota_owner)
                reason = self.cooldown_reason(provider_name, owner=quota_owner)
                attempts.append(ProviderAttemptDiagnostic(
                    provider=provider_name, status="skipped",
                    skip_class="quota_or_cooldown",
                    detail=daily or (
                        f"provider cooldown gate (after {reason})"
                        if reason else "provider cooldown gate"
                    ),
                    failure_class="provider_daily_quota" if daily else None,
                    capacity_scope="account" if daily else None,
                    cooldown_remaining_s=cd if cd > 0 else None,
                ))
                continue

            logger.info("Trying provider %s for role=%s", provider_name, role)
            try:
                budget_reservation = None
                # EVERY served authority — founder-facing served turns
                # ("served_request") AND daemon-owned background attempts
                # ("background_attempt") — goes THROUGH this reserve-before-launch /
                # finalize-actuals-after machinery. It used to admit only
                # "served_request", so background calls reserved nothing and
                # created no actual-usage row, which made the consumer's rolling
                # cap blind to real spend (Codex REJECT #2/#6, PR #2528). The one
                # genuinely served-ONLY step is consuming a live request
                # capability, which background does not carry — skipped below when
                # absent; reservation + finalization are common to both.
                if served_authority is not None and served_authority.budget_owner not in (
                    "served_request",
                    "background_attempt",
                ):
                    # An authority with an UNKNOWN budget owner must never reach a
                    # provider: it would skip the reservation block below and launch
                    # with no budget at all (Codex REJECT #3 reproduced exactly this
                    # with a forged authority). Fail closed before routing.
                    raise ProviderAuthorityHeldError(_CONNECT_PROVIDER_MESSAGE)
                if served_authority is not None and served_authority.budget_owner in (
                    "served_request",
                    "background_attempt",
                ):
                    from tinyassets.auth.middleware import (
                        consume_provider_request_invocation,
                    )
                    from tinyassets.provider_assignment import (
                        abandon_served_provider_budget,
                        finalize_served_provider_budget,
                        release_served_provider_budget,
                        reserve_served_provider_budget,
                    )

                    estimated_input_tokens = max(1, input_size(prompt, system, cfg))
                    budget_reservation = reserve_served_provider_budget(
                        universe_dir.parent,
                        universe_dir=universe_dir,
                        authority=served_authority,
                        role=role,
                        requested_output_tokens=cfg.max_tokens,
                        estimated_input_tokens=estimated_input_tokens,
                        call_timeout_s=getattr(cfg, "timeout", None),
                    )
                    cfg = replace(cfg, max_tokens=budget_reservation.output_tokens)
                provider_started = False
                request_attempt = None
                request_dispatched = False
                try:
                    # Bound concurrent provider SUBPROCESSES (~77 MB PSS each,
                    # measured). ASYNC form: a blocking acquire here stalls the event
                    # loop, and `call_judge_ensemble` gathers admission-taking tasks on
                    # one loop, so the bound would refuse work it was itself holding up.
                    #
                    # Acquired BEFORE `before_provider_launch` on purpose. With the
                    # order reversed, a busy refusal charged the launch, abandoned the
                    # budget reservation as INDETERMINATE and cooled a provider that had
                    # never started — the caller then saw AllProvidersExhaustedError
                    # instead of "busy, retry" (Codex reproduced this).
                    async with _provider_slot(
                        nested=_is_nested(universe_context),
                        parent_slot=blocking_parent_slot(),
                    ):
                        live_turn = _stoppable_native_turn(operation, provider)
                        if live_turn is not None and live_turn.requested():
                            raise asyncio.CancelledError()  # Stop: nothing launched
                        before_launch = getattr(
                            served_authority, "before_provider_launch", None
                        ) if served_authority is not None else None
                        if callable(before_launch):
                            before_launch()
                        if (
                            served_authority is not None
                            and served_authority.request_capability is not None
                        ):
                            try:
                                consume_provider_request_invocation(
                                    served_authority.request_capability,
                                    limit=served_authority.request_max_invocations,
                                )
                            except PermissionError as exc:
                                raise ProviderAuthorityHeldError(
                                    _CONNECT_PROVIDER_MESSAGE
                                ) from exc
                        if request_budget is not None:
                            from tinyassets.providers.api_key_http_provider import (
                                ApiKeyHttpProvider,
                            )
                            from tinyassets.providers.model_policy import ModelRef

                            owner = (served_authority.owner_user_id if served_authority is not None
                                     else invocation_carrier._receipt.principal_id)
                            selected = cfg.selected_model
                            selected_id = (selected.model_id if selected is not None
                                           else provider.model
                                           if isinstance(provider, ApiKeyHttpProvider)
                                           else cfg.native_model_id or "provider-default")
                            accounting_context = replace(universe_context, model_selection=ModelRef(
                                provider_name, selected_id,
                            ))
                            request_budget.check_scope(owner, universe_dir.name)
                            request_budget.persist(universe_dir.parent)
                            request_attempt = request_budget.reserve(
                                owner=owner, universe=universe_dir.name,
                                source_ref=provider_name, model=selected_id,
                                free=metered_free_source(accounting_context, selected, owner=owner),
                                purpose="learning" if cfg.secondary_call else cfg.request_purpose,
                            )
                            cfg = replace(cfg, request_attempt=request_attempt)
                        after_claim = getattr(served_authority, "after_provider_claim", None)
                        if after_claim is not None:
                            if not callable(after_claim) or budget_reservation is None:
                                raise PermissionError("invalid agent pre-dispatch observer")
                            after_claim(served_authority, budget_reservation, cfg)
                        if _work_agent_observer is not None:
                            _work_agent_observer(invocation_carrier, None, cfg)
                        # The owning universe for every process this call
                        # launches; the shared spawn point jails to it, or
                        # refuses a launch with none (provider_jail).
                        cfg = replace(cfg, invocation_owner_user_id=(
                            served_authority.owner_user_id if served_authority is not None
                            else invocation_carrier._receipt.principal_id
                            if invocation_carrier is not None else ""
                        ))
                        with provider_launch_scope(
                            universe_dir, credential_dir=cfg.credential_snapshot_dir,
                            engine_route=_engine_route(cfg),
                        ):
                            if request_attempt is not None:
                                from tinyassets.providers.api_key_http_provider import (
                                    ApiKeyHttpProvider,
                                )

                                if not isinstance(provider, ApiKeyHttpProvider):
                                    request_budget.dispatched(request_attempt)
                                request_dispatched = True
                            provider_started = True
                            dispatch = provider.complete(
                                prompt, system, cfg, universe_dir=universe_dir,
                            )
                            if live_turn is not None:
                                resp = await live_turn.cancel_on_stop(dispatch)
                            else:
                                resp = await dispatch
                except _ProviderBusy:
                    # Not a provider failure: nothing launched, so the reservation is
                    # released untouched, no cooldown is applied, and the actionable
                    # message reaches the caller instead of being flattened into
                    # "all providers exhausted".
                    if invocation_carrier is not None:
                        settle_carrier(
                            ProviderInvocationReservationState.CANCELLED_BEFORE_LAUNCH,
                            input_tokens=0, output_tokens=0, cost_microunits=0,
                        )
                    if budget_reservation is not None:
                        # RELEASED, not abandoned: nothing launched, so no tokens were
                        # spent and charging the binding would exhaust a budget that
                        # still has capacity — the same reasoning the
                        # ProviderUnavailableError branch below applies.
                        try:
                            release_served_provider_budget(
                                universe_dir.parent, budget_reservation,
                            )
                        except Exception:
                            # Logged, not suppressed silently: a failed release leaves a
                            # binding charged for a turn that never ran, and swallowing
                            # it is how that becomes an unexplained "budget exhausted".
                            logger.exception(
                                "failed to release reservation after admission refusal"
                            )
                    raise
                except BaseException as exc:
                    if request_attempt is not None:
                        dispatched_count = request_budget.settle_invocation(
                            request_attempt,
                            "unknown" if isinstance(exc, (asyncio.CancelledError,
                                                           ProviderTimeoutError, TimeoutError))
                            else "failed",
                        )
                        if dispatched_count == 0:
                            provider_started = False
                    if budget_reservation is not None:
                        # A provider that never became available produced no
                        # tokens, so its reservation must be RELEASED, not
                        # conservatively consumed forever. Abandoning it
                        # (`indeterminate`) permanently charges the binding for a
                        # turn that spent nothing — so a flaky provider exhausts
                        # its own budget one failed turn at a time and then reads
                        # as "budget exhausted" while actually having capacity.
                        # Only a failure AFTER the call began (genuinely unknown
                        # usage) is conservatively consumed.
                        if not provider_started or isinstance(
                            exc, (ProviderUnavailableError, ProviderConfinementError),
                        ):
                            release_served_provider_budget(
                                universe_dir.parent,
                                budget_reservation,
                            )
                        else:
                            abandon_served_provider_budget(
                                universe_dir.parent,
                                budget_reservation,
                            )
                    if invocation_carrier is not None:
                        # A confinement refusal is raised before any process
                        # exists, so it launched nothing either.
                        if not provider_started or isinstance(exc, ProviderConfinementError):
                            settle_carrier(
                                ProviderInvocationReservationState.CANCELLED_BEFORE_LAUNCH,
                                input_tokens=0, output_tokens=0, cost_microunits=0,
                            )
                        elif isinstance(
                            exc,
                            (
                                ProviderRateLimitedError,
                                ProviderOverloadedError,
                                ProviderUnavailableError,
                            ),
                        ):
                            # Native no-effects evidence controls safe retry,
                            # not usage: the CLI may already have spent tokens.
                            # Without totals, keep its conservative reservation.
                            native_unmetered = (
                                _work_agent_observer is not None
                                and _agent_execution_kind == "native_agent"
                                and isinstance(
                                    exc, (ProviderRateLimitedError, ProviderOverloadedError),
                                )
                            )
                            if native_unmetered:
                                settle_carrier(ProviderInvocationReservationState.INDETERMINATE)
                            else:
                                settle_carrier(
                                    ProviderInvocationReservationState.FAILED,
                                    input_tokens=0, output_tokens=0, cost_microunits=0,
                                )
                        else:
                            settle_carrier(
                                ProviderInvocationReservationState.INDETERMINATE
                            )
                    raise
                if request_dispatched:
                    request_budget.settle_invocation(request_attempt, "succeeded")
                    resp = replace(resp, request_receipt=request_budget.receipt())
                if budget_reservation is not None:
                    finalize_served_provider_budget(
                        universe_dir.parent,
                        authority=served_authority,
                        reservation=budget_reservation,
                        input_tokens=resp.input_tokens,
                        output_tokens=resp.output_tokens,
                        cost_microunits=resp.cost_microunits,
                        fallback_output=output_for_settlement(resp),
                    )
                # A SUCCEEDED settlement must carry KNOWN usage: settle_invocation
                # rejects anything that is not an int. But usage is optional on
                # ProviderResponse by design -- "every existing construction site
                # and non-streaming provider stays a valid terminal
                # ProviderResponse" (providers/base.py) -- and codex_provider only
                # populates it when machine accounting is on
                # (`machine_accounting = bool(config.sandbox_workspace)`,
                # codex_provider.py:350). A plain prompt-template node has no
                # sandbox workspace, so a perfectly successful call arrived here
                # with all three fields None and the settlement destroyed it:
                # every prompt-template run in the founder's universe failed with
                # "provider invocation usage could not be settled" on 2026-08-27
                # while effect-only branches, which take no carrier, kept working.
                #
                # INDETERMINATE is the state that already means exactly this, and
                # its budget treatment is the conservative one (consume the
                # reservation rather than report a free call). Settling zeros
                # instead would report the call as free and leave the budget
                # undrainable.
                if (
                    resp.input_tokens is None
                    or resp.output_tokens is None
                    or resp.cost_microunits is None
                ):
                    settle_carrier(ProviderInvocationReservationState.INDETERMINATE)
                else:
                    settle_carrier(
                        ProviderInvocationReservationState.SUCCEEDED,
                        input_tokens=resp.input_tokens,
                        output_tokens=resp.output_tokens,
                        cost_microunits=resp.cost_microunits,
                    )
                if served_authority is not None:
                    from tinyassets.providers.source_health import SOURCE_HEALTH, source_key

                    SOURCE_HEALTH.succeeded(source_key(
                        universe_context.universe_dir.parent, served_authority.owner_user_id,
                        served_authority.universe_id, served_authority,
                    ))
            except _ProviderBusy:
                # Nothing launched: not a provider failure, so no cooldown and no
                # "exhausted" verdict about a provider that was never asked. Codex
                # reproduced the alternative — `provider_calls=0`, cooldown 29s,
                # AllProvidersExhaustedError — where the inner re-raise was swallowed by
                # this outer classifier.
                raise
            except ProviderAuthorityHeldError:
                raise
            except ProviderAuthenticationError as exc:
                from tinyassets.providers.agent_capacity_boundary import NativeCompletionEvidence
                from tinyassets.providers.source_health import SOURCE_HEALTH, source_key

                if served_authority is not None:
                    # A secondary call writes no shared health at all: a
                    # reconnect mark removes the source from the founder's next
                    # turn's plan (``served_model_plan``), which is the same
                    # lockout the cooldown gate was causing, and the reply that
                    # just succeeded already cleared this key.
                    if not getattr(cfg, "secondary_call", False):
                        SOURCE_HEALTH.authentication_failed(source_key(
                            universe_context.universe_dir.parent,
                            served_authority.owner_user_id,
                            served_authority.universe_id, served_authority,
                        ))
                    else:
                        logger.info(
                            "Not marking %s for reconnect: secondary call",
                            provider_name,
                        )
                else:
                    # Preserve legacy host routing. Owned serving failures must
                    # not quarantine another owner's credential on this host.
                    self._cool(cfg, provider_name, COOLDOWN_OTHER,
                               owner=quota_owner, reason="auth_invalid")
                proof = getattr(exc, "native_evidence", None)
                if type(proof) is NativeCompletionEvidence and proof.provider == provider_name:
                    native_proofs[len(attempts)] = proof
                attempts.append(ProviderAttemptDiagnostic(
                    provider=provider_name, status="failed", skip_class="auth_invalid",
                    detail="Provider reported a sign-in failure",
                    failure_class=exc.failure_class, side_effect_state=_side_effect_from(exc),
                    **_tool_wait_evidence(exc),
                ))
                continue
            except SelectedModelCapacityError as exc:
                from tinyassets.providers.model_capacity import free_sibling_retry

                # One model's capacity is not evidence its whole connection is
                # unhealthy. Shared/unknown scope keeps the conservative cooldown
                # -- EXCEPT where cooling the whole connection would also skip
                # the sibling model the turn is about to try, which is the dead
                # end itself (live 2026-09-25).
                # ...and not when the source named a wait longer than a whole
                # turn may live, where waiting IS the answer. Whether a sibling
                # attempt actually follows is known only to the turn coordinator,
                # which cools the source itself once it concludes none will.
                # The owner's ceilings are NOT consulted: they price every
                # attempt anyway, and reading them here made the same refusal
                # mean different things to a free and a paid account.
                #
                # Withholding buys exactly one thing -- a sibling on this same
                # grant -- so it is only for a caller that HAS one and settles
                # the cooldown once its order runs out. An agent turn does; a
                # workflow node says so with `ModelConfig.owns_capacity_siblings`
                # (`_ForegroundRunProviderSession._cool_abandoned_sources`).
                #
                # Everyone ELSE is cooled exactly as before. The capacity
                # decoder now runs for every call rather than only agent rounds,
                # so this handler is newly reachable from callers that hold no
                # order at all -- a post-reply learning extraction, say -- and
                # for them a withheld window is never spent and never settled.
                # That is the DAILY-cap case `free_sibling_retry` warns about:
                # every later turn pays the full order again, forever.
                owns_siblings = (
                    cfg.agent_request is not None
                    or getattr(cfg, "owns_capacity_siblings", False)
                )
                if not owns_siblings or (
                    exc.signal.scope != "model" and not free_sibling_retry(
                        scope=exc.signal.scope, failure_class=exc.failure_class,
                        retry_after_s=exc.retry_after,
                        turn_budget_s=cfg.stream_timeout_profile().absolute_cap_s,
                    )
                ):
                    daily = exc.failure_class == "provider_daily_quota"
                    self._cool(
                        cfg, provider_name,
                        (_retry_after_cooldown_s(exc.retry_after) if exc.retry_after is not None
                         else MAX_COOLDOWN_S) if daily else _rate_limit_cooldown_s(exc),
                        daily_detail=redacted_failure_detail(str(exc)) if daily else "",
                        owner=quota_owner, reason=exc.failure_class,
                    )
                attempts.append(ProviderAttemptDiagnostic(
                    provider=provider_name, status="failed", skip_class="quota_or_cooldown",
                    detail=redacted_failure_detail(str(exc)), failure_class=exc.failure_class,
                    retry_after_s=exc.retry_after, capacity_scope=exc.signal.scope,
                    # The RAISER now declares whether anything was generated for
                    # the one class that reaches here -- a whole-response HTTP
                    # status (`api_key_http_provider._pre_generation`) -- so
                    # `_side_effect_from` answers "none" on its own and this
                    # conditional is a second definition of the same fact.
                    # Left in place deliberately: collapsing it is a no-op for
                    # every reachable caller and not worth touching the chat
                    # path for in the same change. See the PR's follow-up note.
                    side_effect_state=(
                        "none" if cfg.agent_request is not None
                        and getattr(provider, "agent_execution_kind", None) == "engine_inference"
                        else _side_effect_from(exc)
                    ),
                    **_tool_wait_evidence(exc),
                ))
                continue
            except ProviderModelRefusedError as exc:
                # The source refused THIS model (403/404/410) before generating.
                # That says nothing about the connection's health, and cooling it
                # would skip the sibling models the turn coordinator may move to
                # next -- the dead end of 2026-09-28. Nothing was generated, so no
                # effect: the refusal is a whole-response HTTP status.
                attempts.append(ProviderAttemptDiagnostic(
                    provider=provider_name, status="failed", skip_class="provider_error",
                    detail=redacted_failure_detail(str(exc)), failure_class=exc.failure_class,
                    side_effect_state="none",
                ))
                continue
            except (ProviderRateLimitedError, ProviderOverloadedError) as exc:
                from tinyassets.providers.agent_capacity_boundary import NativeCompletionEvidence

                proof = getattr(exc, "native_evidence", None)
                if (type(proof) is NativeCompletionEvidence
                        and proof.provider == provider_name):
                    native_proofs[len(attempts)] = proof
                # A genuine rate-limit / overload IS real capacity: cool the
                # provider until its own retry-after (+margin), keeping fallback
                # forbidden for the sole served writer.
                cd = _rate_limit_cooldown_s(exc)
                if self._cool(cfg, provider_name, cd,
                              owner=quota_owner, reason=exc.failure_class or ""):
                    logger.warning(
                        "Provider %s rate-limited/overloaded (%s), cooldown %ds",
                        provider_name, exc.failure_class, cd,
                    )
                attempts.append(ProviderAttemptDiagnostic(
                    provider=provider_name, status="failed",
                    skip_class=classify_unavailable(exc),
                    detail=redacted_failure_detail(str(exc)),
                    failure_class=exc.failure_class,
                    retry_after_s=getattr(exc, "retry_after", None),
                    side_effect_state=_side_effect_from(exc),
                    **_tool_wait_evidence(exc),
                ))
                continue
            except (
                ProviderIdleTimeoutError, InteractiveDeadlineError, ProviderReplyTimeoutError,
            ) as exc:
                # A transient attempt timeout is NOT proof the credential is
                # down. Do NOT cool the sole served writer — the next turn stays
                # eligible. The process was already killed by the provider.
                logger.warning(
                    "Provider %s ended on %s (no provider cooldown)",
                    provider_name, exc.failure_class,
                )
                attempts.append(ProviderAttemptDiagnostic(
                    provider=provider_name, status="failed",
                    skip_class="timed_out",
                    detail=redacted_failure_detail(str(exc)),
                    failure_class=exc.failure_class,
                    side_effect_state=_side_effect_from(exc),
                    **_tool_wait_evidence(exc),
                ))
                continue
            except ProviderProtocolError as exc:
                # An agent round's unreadable or in-band-error reply is about one
                # MODEL's answer, not the connection: cooling it skipped every
                # sibling model on the same key (live 2026-10-02, the free-only
                # account's whole OpenRouter pool), and made the owner's very next
                # "continue" a cooldown refusal. The turn coordinator bounds its
                # own retries (``AgentTurnCoordinator._next_after_bad_reply``).
                if cfg.agent_request is None and self._cool(
                    cfg, provider_name, COOLDOWN_OTHER,
                    owner=quota_owner, reason="provider_protocol_error",
                ):
                    logger.warning(
                        "Provider %s protocol error, cooldown %ds",
                        provider_name, COOLDOWN_OTHER,
                    )
                attempts.append(ProviderAttemptDiagnostic(
                    provider=provider_name, status="failed",
                    skip_class="provider_error",
                    detail=redacted_failure_detail(str(exc)),
                    failure_class=exc.failure_class,
                    side_effect_state=_side_effect_from(exc),
                    partial_text=getattr(exc, "partial_text", None) or None,
                    **_tool_wait_evidence(exc),
                ))
                continue
            except ProviderUnavailableError as exc:
                if self._cool(cfg, provider_name, COOLDOWN_UNAVAILABLE,
                              owner=quota_owner, reason="provider_unavailable"):
                    logger.warning(
                        "Provider %s unavailable, cooldown %ds",
                        provider_name, COOLDOWN_UNAVAILABLE,
                    )
                attempts.append(ProviderAttemptDiagnostic(
                    provider=provider_name, status="failed",
                    skip_class=classify_unavailable(exc),
                    detail=redacted_failure_detail(str(exc)),
                ))
                continue
            except ProviderTimeoutError as exc:
                if self._cool(cfg, provider_name, COOLDOWN_TIMEOUT,
                              owner=quota_owner, reason="timed_out"):
                    logger.warning(
                        "Provider %s timed out, cooldown %ds",
                        provider_name, COOLDOWN_TIMEOUT,
                    )
                attempts.append(ProviderAttemptDiagnostic(
                    provider=provider_name, status="failed",
                    skip_class="timed_out",
                    detail=redacted_failure_detail(str(exc)),
                ))
                continue
            except ProviderError as exc:
                if self._cool(cfg, provider_name, COOLDOWN_OTHER,
                              owner=quota_owner, reason="provider_error"):
                    logger.warning(
                        "Provider %s error, cooldown %ds: %s",
                        provider_name, COOLDOWN_OTHER, exc,
                    )
                attempts.append(ProviderAttemptDiagnostic(
                    provider=provider_name, status="failed",
                    skip_class="provider_error",
                    detail=redacted_failure_detail(str(exc)),
                    side_effect_state=_side_effect_from(exc),
                    **_tool_wait_evidence(exc),
                ))
                continue
            except _ProviderBusy:
                # Nothing launched: not a provider failure, so no cooldown and no
                # "exhausted" verdict about a provider that was never asked. Codex
                # reproduced the alternative — `provider_calls=0`, cooldown 29s,
                # AllProvidersExhaustedError — where the inner re-raise was swallowed by
                # this outer classifier.
                raise
            except Exception as exc:
                self._cool(cfg, provider_name, COOLDOWN_OTHER, owner=quota_owner, reason="unknown")
                logger.exception("Unexpected error from %s", provider_name)
                attempts.append(ProviderAttemptDiagnostic(
                    provider=provider_name, status="failed",
                    skip_class="unknown",
                    detail=f"{type(exc).__name__}: {redacted_failure_detail(str(exc), limit=160)}",
                ))
                continue

            # Decorate the earned answer with the owner's own name for the
            # source that produced it, so the app's "Answered by" line can say
            # "OpenRouter" instead of `api_key_http:provdef_ed0169c8...`. Display
            # only, resolved from the definition's grant and connection; a miss
            # returns "" and the renderer keeps the routing identity.
            if not resp.provider_display and universe_dir is not None:
                from tinyassets.providers.source_display import source_display_name

                label = source_display_name(
                    base=universe_dir.parent,
                    universe_id=universe_dir.name,
                    provider=provider_name,
                )
                if label:
                    resp = replace(resp, provider_display=label)
            return resp

        # All providers exhausted.
        if served_authority is not None:
            raise AllProvidersExhaustedError(
                f"Served provider {served_authority.provider!r} exhausted; "
                f"command center {AllProvidersExhaustedError.NO_WIDENING_MESSAGE}.",
                attempts=attempts,
                failure_class=dominant_failure_class(attempts),
                retry_after=dominant_retry_after_s(attempts),
                capacity_scope=dominant_capacity_scope(attempts),
                native_evidence=tuple(native_proofs.get(i) for i in range(len(attempts))),
            )
        if invocation_carrier is not None:
            settle_carrier(
                ProviderInvocationReservationState.CANCELLED_BEFORE_LAUNCH,
                input_tokens=0,
                output_tokens=0,
                cost_microunits=0,
            )
            # The run's ONLY authorized source failed. Carry the same structured
            # snapshot the unpinned chain carries: the compiler persists it on
            # the failed event and the run error, and without it the run read
            # named no cause at all (live 2026-09-21, run 07c1611916cc4eb4).
            raise AllProvidersExhaustedError(
                f"Armed provider {invocation_carrier.provider!r} exhausted; "
                f"provider {AllProvidersExhaustedError.NO_WIDENING_MESSAGE}.",
                attempts=attempts,
                chain_state=build_chain_state(
                    role=role,
                    chain=chain,
                    attempts=attempts,
                    api_key_providers_enabled=api_key_providers_enabled(),
                    allowlist=allowlist,
                ),
                failure_class=dominant_failure_class(attempts),
                retry_after=dominant_retry_after_s(attempts),
                capacity_scope=dominant_capacity_scope(attempts),
                native_evidence=tuple(native_proofs.get(i) for i in range(len(attempts))),
            )
        # Unreachable: the entry and dispatch gates guarantee exactly one owner
        # authority, and both branches above raise. There is no platform chain
        # left to drain, no local fallback and no degraded judge (Hard Rule 15).
        raise AllProvidersExhaustedError(
            f"All providers exhausted for role={role}.",
            attempts=attempts,
            failure_class=dominant_failure_class(attempts),
            retry_after=dominant_retry_after_s(attempts),
        )

    # ------------------------------------------------------------------
    # Policy-aware routing (per-node llm_policy override)
    # ------------------------------------------------------------------

    @staticmethod
    def _call_meta(resp, attempts: int) -> dict:
        """Telemetry for one routed call: model identity, latency, attempts.

        Persisted onto run receipts (runs.provider_used/model columns and the
        per-run ``provider_calls`` event) so receipts can answer "which model
        produced this, how long did it take, after how many tries" — spec
        §11.3 model-stamp requirement.
        """
        from tinyassets.providers.execution_receipt import WriterExecutionReceipt

        receipt = WriterExecutionReceipt()
        receipt.observe(resp)
        return {
            "model": getattr(resp, "model", "") or "",
            "execution": receipt.projection(),
            "family": getattr(resp, "family", "") or "",
            "latency_ms": getattr(resp, "latency_ms", None),
            "degraded": bool(getattr(resp, "degraded", False)),
            "attempts": attempts,
        }

    async def call_with_policy(
        self,
        role: str,
        prompt: str,
        system: str,
        policy: dict | None,
        config: ModelConfig | None = None,
        difficulty: str = "",
        *,
        operation: str | None = None,
        universe_context: UniverseContext | None = None,
    ) -> tuple[str, str, dict]:
        """Route a node's ``llm_policy`` call through the owner's authority.

        Returns ``(response_text, provider_name_used, call_meta)`` where
        ``call_meta`` is :meth:`_call_meta` telemetry for the winning call.

        The owner's binding names exactly one provider, so a policy's
        ``preferred`` / ``fallback_chain`` / ``difficulty_override`` entries no
        longer choose among host-registered providers (Hard Rule 15: there is
        no platform chain to choose from). Carrier-bound callers that must
        honour a policy check it against their authority before calling here
        (``cloud_automation_continuation``, ``foreground_run_provider``).
        """
        del policy, difficulty
        require_owner_bound_context(universe_context, operation=operation)
        response = await self.call(
            role,
            prompt,
            system,
            config,
            operation=operation,
            universe_context=universe_context,
        )
        return response.text, response.provider, self._call_meta(response, attempts=1)


    @blocking_provider_child()
    def call_with_policy_sync(
        self,
        role: str,
        prompt: str,
        system: str,
        policy: dict | None,
        config: ModelConfig | None = None,
        difficulty: str = "",
        *,
        operation: str | None = None,
        universe_context: UniverseContext | None = None,
    ) -> tuple[str, str, dict]:
        """Synchronous wrapper for :meth:`call_with_policy`."""
        cfg = config or _default_config(_resolve_universe_config(universe_context))
        # See call_sync (blocker L): enforce the timeout INSIDE the async task so
        # a timeout cancels the coroutine and kills the streaming subprocess, and
        # never fire below the stream absolute cap.
        inner_timeout = _sync_call_timeout_s(cfg)
        # One absolute monotonic deadline, armed HERE — on the caller's thread,
        # BEFORE the queue. See call_sync for why the anchor has to be submit
        # time and not worker pickup.
        queued_at = time.monotonic()
        node_budget_s = _caller_deadline_budget_s(cfg)
        drain_deadline = queued_at + inner_timeout

        # Capture universe_context in the closure so it survives the hop into
        # the ThreadPoolExecutor worker thread (no ContextVar — a ContextVar
        # set here would NOT propagate to the pool's worker thread).
        def _run() -> tuple[str, str, dict]:
            waited = time.monotonic() - queued_at
            if node_budget_s is not None and waited >= node_budget_s:
                raise ProviderTimeoutError(
                    f"call_with_policy_sync refused role={role} before launch: "
                    f"its {node_budget_s:.3g}s deadline passed while it waited "
                    f"{waited:.3g}s in the provider-sync queue"
                )
            run_cfg = _queue_adjusted_config(cfg, node_budget_s, waited)
            run_timeout = max(
                _MIN_POSITIVE_PROVIDER_CAP_S, drain_deadline - time.monotonic(),
            )
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    asyncio.wait_for(
                        self.call_with_policy(
                            role, prompt, system, policy, run_cfg, difficulty,
                            operation=operation,
                            universe_context=universe_context,
                        ),
                        timeout=run_timeout,
                    )
                )
            except asyncio.TimeoutError:
                raise ProviderTimeoutError(
                    f"call_with_policy_sync exceeded {run_timeout:.0f}s for "
                    f"role={role} (subprocess cancelled/killed)"
                )
            finally:
                loop.close()

        # A synchronous nested caller is blocked until this worker settles.
        # Copy its explicit loan, not just the universe carrier. Nested work
        # cannot queue behind blocked ancestors in the fixed sync-worker pool.
        call_context = contextvars.copy_context()
        nested_pool = (
            concurrent.futures.ThreadPoolExecutor(max_workers=1)
            if blocking_parent_slot() is not None else None
        )
        pool = nested_pool or self._thread_pool
        future = pool.submit(call_context.run, _run)
        if nested_pool is not None:
            nested_pool.shutdown(wait=False)
        try:
            return future.result(timeout=inner_timeout + 30)
        except concurrent.futures.TimeoutError:
            logger.warning(
                "call_with_policy_sync backstop fired after %.0fs for role=%s",
                inner_timeout + 30, role,
            )
            raise ProviderTimeoutError(
                f"call_with_policy_sync exceeded {inner_timeout + 30:.0f}s "
                f"backstop for role={role}"
            )

    # ------------------------------------------------------------------
    # Synchronous wrapper (for use from sync graph nodes)
    # ------------------------------------------------------------------

    _thread_pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=_SYNC_CALL_MAX_WORKERS,
        thread_name_prefix="tinyassets-provider-sync",
    )

    @blocking_provider_child()
    def call_sync(
        self,
        role: str,
        prompt: str,
        system: str,
        config: ModelConfig | None = None,
        *,
        operation: str | None = None,
        universe_context: UniverseContext | None = None,
    ) -> ProviderResponse:
        """Synchronous version of :meth:`call` for use from sync code.

        Runs the async ``call`` in a dedicated thread with its own event
        loop, avoiding the "loop already running" problem that blocks
        ``loop.run_until_complete`` inside LangGraph nodes.

        ``universe_context`` is captured in the submitted closure so it survives
        the hop into the ThreadPoolExecutor worker thread — a ContextVar set in
        the caller's thread would NOT propagate into the pool worker, so the
        per-universe routing state is threaded EXPLICITLY, not via ContextVar.
        """
        cfg = config or _default_config(_resolve_universe_config(universe_context))
        # The streaming served path has its OWN absolute cap (default 600s); the
        # sync wrapper MUST NOT fire below it (blocker L) or it would return
        # failure while the subprocess keeps streaming (possible side effects).
        # Enforce the cap INSIDE the async task via ``asyncio.wait_for`` so a
        # timeout CANCELS ``call`` → ``complete`` → ``_read_stream``'s finally,
        # which kills the subprocess. ``future.result`` keeps a slightly larger
        # backstop only for a wedged event loop.
        inner_timeout = _sync_call_timeout_s(cfg)
        # ONE absolute monotonic deadline for this call, armed HERE — on the
        # caller's thread, BEFORE the queue. This pool is bounded
        # (_SYNC_CALL_MAX_WORKERS), so an item can sit in it for the whole of
        # the caller's budget, while the clock ``asyncio.wait_for`` arms below
        # only starts at worker pickup. Anchoring the window at submit is what
        # makes the queue wait DEDUCTED instead of silently re-granted, and it
        # is the same absolute deadline the compiler's own worker guard applies
        # one hop earlier (``graph_compiler._run_with_timeout``).
        queued_at = time.monotonic()
        node_budget_s = _caller_deadline_budget_s(cfg)
        drain_deadline = queued_at + inner_timeout
        # Read on the CALLER's thread and carried into the pool worker, which
        # does not inherit context variables. Set only inside a served converse
        # turn; the dispatch site alone decides what a stop may cancel (only a
        # native ``converse`` dispatch), so nothing else is stoppable from here.
        from tinyassets.turn_interrupt import TurnInterrupted, bound

        live_turn = _live_interactive_turn()

        def _run() -> ProviderResponse:
            waited = time.monotonic() - queued_at
            if node_budget_s is not None and waited >= node_budget_s:
                # Refused BEFORE any provider launch: the deadline that
                # authorised this call has already passed, so starting one now
                # burns a worker (and a subprocess) on a result nobody awaits.
                # Nothing in flight is touched — this check sits ahead of the
                # call, never inside it, so work that got past it settles.
                raise ProviderTimeoutError(
                    f"call_sync refused role={role} before launch: its "
                    f"{node_budget_s:.3g}s deadline passed while it waited "
                    f"{waited:.3g}s in the provider-sync queue"
                )
            run_cfg = _queue_adjusted_config(cfg, node_budget_s, waited)
            # Still max(legacy, absolute cap) + 30s of reader-drain margin
            # (blocker L) — just measured from submit, so the queue wait comes
            # out of it rather than extending it.
            run_timeout = max(
                _MIN_POSITIVE_PROVIDER_CAP_S, drain_deadline - time.monotonic(),
            )
            loop = asyncio.new_event_loop()
            try:
                with bound(live_turn):
                    try:
                        return loop.run_until_complete(
                            asyncio.wait_for(
                                self.call(
                                    role, prompt, system, run_cfg,
                                    operation=operation,
                                    universe_context=universe_context,
                                ),
                                timeout=run_timeout,
                            )
                        )
                    except asyncio.CancelledError:
                        # Only the owner's Stop cancels from inside this loop.
                        if live_turn is not None and live_turn.requested():
                            raise TurnInterrupted("the owner stopped this turn") from None
                        raise
            except asyncio.TimeoutError:
                # wait_for already cancelled the coroutine (subprocess killed).
                raise ProviderTimeoutError(
                    f"call_sync exceeded {run_timeout:.0f}s for role={role} "
                    "(subprocess cancelled/killed)"
                )
            finally:
                loop.close()

        # A synchronous nested caller is blocked until this worker settles.
        # Copy its explicit loan, not just the universe carrier. Nested work
        # cannot queue behind blocked ancestors in the fixed sync-worker pool.
        call_context = contextvars.copy_context()
        nested_pool = (
            concurrent.futures.ThreadPoolExecutor(max_workers=1)
            if blocking_parent_slot() is not None else None
        )
        pool = nested_pool or self._thread_pool
        future = pool.submit(call_context.run, _run)
        if nested_pool is not None:
            nested_pool.shutdown(wait=False)
        try:
            return future.result(timeout=inner_timeout + 30)
        except concurrent.futures.TimeoutError:
            logger.warning(
                "call_sync backstop fired after %.0fs for role=%s (event loop "
                "wedged)", inner_timeout + 30, role,
            )
            raise ProviderTimeoutError(
                f"call_sync exceeded {inner_timeout + 30:.0f}s hard backstop "
                f"for role={role}"
            )

    # ------------------------------------------------------------------
    # Judge ensemble (model family diversity)
    # ------------------------------------------------------------------

    async def call_judge_ensemble(
        self,
        prompt: str,
        system: str,
        config: ModelConfig | None = None,
        *,
        operation: str | None = None,
        universe_context: UniverseContext | None = None,
    ) -> list[ProviderResponse]:
        """Judge through the owner's authority: exactly one response.

        The ensemble used to fan out to every host-registered judge provider.
        There is no platform provider pool to fan out to (Hard Rule 15), so the
        ensemble is the owner's one authorized judge.
        """
        require_owner_bound_context(universe_context, operation=operation)
        return [
            await self.call(
                "judge",
                prompt,
                system,
                config,
                operation=operation,
                universe_context=universe_context,
            )
        ]
