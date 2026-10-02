"""General LLM-call bridge for the engine.

Routes all provider calls through the shared :class:`ProviderRouter`
(synchronous ``call_sync``), with a deterministic mock path for tests and an
explicit fallback when providers are exhausted. This is the engine's single,
domain-agnostic LLM-call primitive — engine code must reach the LLM only
through this module, never through a domain package. (It was previously hosted
inside the fantasy domain; the relocation is the de-fantasy audit's Tier A:
``docs/audits/2026-06-24-fantasy-architecture-residue-audit.md``.)

The router is injectable: a long-running host (e.g. a daemon) builds its own
fully-configured :class:`ProviderRouter` and installs it via
:func:`set_provider_router`. A bare import builds a router that REGISTERS the
provider executors present on this machine. Registration is not permission:
the router serves a call only with one universe's owner authority and that
universe's own credentials, and refuses every other call before any provider
is touched (AGENTS.md Hard Rule 15, ``tinyassets/providers/owner_binding.py``).
A call with no ``universe_context`` is therefore refused loudly, never served
from the host's CLI login or environment and never degraded to
``fallback_response``.

Use the accessors (:func:`get_last_provider`, :func:`is_force_mock`) rather than
``from ... import last_provider`` / ``_FORCE_MOCK``: the import-the-name pattern
binds an import-time snapshot that never reflects later reassignment (the latent
bug at ``ingestion/extractors.py``, where ``model=last_provider`` always wrote
an empty string).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

if TYPE_CHECKING:
    from tinyassets.providers.base import ProviderResponse
    from tinyassets.providers.router import ProviderRouter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mutable module state (use the accessors below; do not import these names)
# ---------------------------------------------------------------------------

# Skip real provider calls and return mock/fallback output. Tests set this via
# set_force_mock() in conftest.
_force_mock = False

# The router call_provider() routes through. None until a router is built or
# injected. A daemon overwrites it via set_provider_router().
_real_router: "Optional[ProviderRouter]" = None

# Provider name used by the most recent call. Read via get_last_provider().
_last_provider: str = ""


def set_force_mock(value: bool) -> None:
    """Enable/disable the mock path (tests)."""
    global _force_mock
    _force_mock = bool(value)


def is_force_mock() -> bool:
    """Whether call_provider() short-circuits to mock/fallback output."""
    return _force_mock


def set_provider_router(router: "Optional[ProviderRouter]") -> None:
    """Install the router call_provider() routes through.

    This is the daemon-injection seam: ``DaemonController`` builds a fully
    configured router and installs it here so the engine's LLM calls use the
    host's provider configuration instead of the import-time fallback.
    """
    global _real_router
    _real_router = router


def get_provider_router() -> "Optional[ProviderRouter]":
    """The router currently installed (fallback or daemon-injected)."""
    return _real_router


def get_last_provider() -> str:
    """Provider name used by the most recent call_provider() — live, not a snapshot."""
    return _last_provider


def make_interactive_agent_turn(*, prompt, system, universe_context, config):
    """Create a selected agent coordinator; no mock or alternate credential route."""
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.interactive_http_agent import InteractiveHttpAgentTurn

    if _force_mock or _real_router is None:
        raise ProviderAuthorityHeldError("interactive agent requires a real provider router")
    _register_open_providers_for(universe_context)
    from tinyassets.agent_loop.served_chat import ThinLoopChatAdapter, thin_loop_selected

    # Only the thin loop names an adapter; unselected, the call is today's.
    thin = {"adapter": ThinLoopChatAdapter()} if thin_loop_selected() else {}
    return InteractiveHttpAgentTurn(
        router=_real_router, prompt=prompt, system=system,
        universe_context=universe_context, config=config, **thin,
    )


def call_interactive_agent_turn(turn, *, response_observer=None) -> str:
    """Run on this claiming worker; only the final answer earns a writer receipt."""
    import asyncio

    global _last_provider
    result = asyncio.run(turn.run())
    _last_provider = result.provider
    if response_observer is not None:
        try:
            response_observer(result)
        except Exception:
            logger.warning("Provider response receipt could not be recorded")
    return result.text


@dataclass(frozen=True, slots=True)
class UniverseBoundProviderCall:
    """Callable that carries one exact universe context across graph workers."""

    provider_call: Any
    universe_context: Any
    operation: str

    def __call__(self, prompt: str, system: str = "", **kwargs: Any) -> str:
        supplied = kwargs.pop("universe_context", None)
        if supplied is not None and supplied is not self.universe_context:
            raise PermissionError("provider call cannot substitute command center context")
        supplied_operation = kwargs.pop("operation", None)
        if supplied_operation is not None and supplied_operation != self.operation:
            raise PermissionError("provider call cannot substitute its bound operation")
        return self.provider_call(
            prompt,
            system,
            universe_context=self.universe_context,
            operation=self.operation,
            **kwargs,
        )

    def call_with_policy_sync(
        self,
        role: str,
        prompt: str,
        system: str,
        policy: dict | None,
        config: Any = None,
        difficulty: str = "",
        *,
        universe_context: Any = None,
    ) -> tuple[str, str, dict]:
        supplied = universe_context
        if supplied is not None and supplied is not self.universe_context:
            raise PermissionError("provider policy cannot substitute command center context")
        delegated = getattr(self.provider_call, "call_with_policy_sync", None)
        if callable(delegated):
            return delegated(
                role,
                prompt,
                system,
                policy,
                config,
                difficulty,
            )
        if _real_router is None:
            from tinyassets.exceptions import AllProvidersExhaustedError

            raise AllProvidersExhaustedError("No provider router is available.")
        global _last_provider
        _register_open_providers_for(self.universe_context)
        result = _real_router.call_with_policy_sync(
            role,
            prompt,
            system,
            policy,
            config,
            difficulty,
            operation=self.operation,
            universe_context=self.universe_context,
        )
        _last_provider = result[1]
        return result


def bind_universe_provider_call(
    provider_call: Any,
    universe_context: Any,
    *,
    operation: str,
) -> UniverseBoundProviderCall:
    """Bind an explicit universe context to direct and policy graph calls."""
    if universe_context is None:
        raise ValueError("universe_context is required")
    normalized_operation = (operation or "").strip()
    if not normalized_operation:
        raise ValueError("operation is required for a universe-bound provider call")
    return UniverseBoundProviderCall(provider_call, universe_context, normalized_operation)


# ---------------------------------------------------------------------------
# Fallback router for standalone / script / test usage
# ---------------------------------------------------------------------------


def _build_fallback_router() -> "Optional[ProviderRouter]":
    """Router registering whatever provider executors are installed.

    Registration only makes an executor addressable by name. Whether it may
    run is decided per call by the owner's authority (Hard Rule 15); the
    host-credential built-ins registered here (Ollama, Gemini, Groq, Grok) are
    refused at dispatch for every universe. Each provider import is
    independently guarded so a missing optional dependency never breaks the
    bridge.
    """
    try:
        from tinyassets.providers.base import subscription_auth_health
        from tinyassets.providers.router import ProviderRouter
    except ImportError:
        logger.info("Real ProviderRouter not available; using mock-only provider")
        return None

    router = ProviderRouter(auth_health=subscription_auth_health)

    try:
        from tinyassets.providers.claude_provider import ClaudeProvider
        if ClaudeProvider.is_available():
            router.register(ClaudeProvider())
            logger.info("Registered ClaudeProvider")
        else:
            logger.debug("claude binary not found - ClaudeProvider skipped")
    except Exception:
        logger.debug("ClaudeProvider not available")

    try:
        from tinyassets.providers.codex_provider import CodexProvider
        if CodexProvider.is_available():
            router.register(CodexProvider())
            logger.info("Registered CodexProvider")
        else:
            logger.debug("codex binary not found - CodexProvider skipped")
    except Exception:
        logger.debug("CodexProvider not available")

    try:
        from tinyassets.providers.ollama_provider import OllamaProvider
        router.register(OllamaProvider())
        logger.info("Registered OllamaProvider")
    except Exception:
        logger.debug("OllamaProvider not available")

    logger.info(
        "ProviderRouter ready with providers: %s",
        router.available_providers,
    )
    return router


_real_router = _build_fallback_router()


# ---------------------------------------------------------------------------
# Public call primitive
# ---------------------------------------------------------------------------


def _register_open_providers_for(universe_context: Any) -> None:
    """Register the calling universe's open compute providers into the live router.

    The additive bridge from the open registry (``connect_compute`` /
    ``ProviderDefinition``) to routing: after this, the universe's open providers are
    in ``router.available_providers`` by name, so the existing chain routes to any the
    universe config's ``allowed_providers`` + preference select (set via the
    ``open_provider`` engine mode). Defensive: never breaks a provider call — a missing
    context, absent registry, or resolution error is a no-op. Isolation is preserved by
    ``ApiKeyHttpProvider``'s per-call grant-universe gate, so a provider registered here
    for one universe cannot serve another.
    """
    if _real_router is None or universe_context is None:
        return
    try:
        universe_dir = getattr(universe_context, "universe_dir", None)
        if universe_dir is None:
            return
        from pathlib import Path as _Path

        from tinyassets.providers.provider_resolver import (
            register_universe_open_providers,
        )

        register_universe_open_providers(_real_router, _Path(universe_dir).name)
    except Exception:
        logger.debug("open-provider registration skipped", exc_info=True)


def _call_router_with_retry(
    role: str,
    prompt: str,
    system: str,
    config: Any = None,
    universe_context: Any = None,
    operation: str | None = None,
    retry_on_exhaustion: bool = True,
    response_observer: Callable[[ProviderResponse], None] | None = None,
) -> str:
    """Call the installed router, optionally retrying on transient exhaustion.

    When ``retry_on_exhaustion`` is True (default, batch/graph callers) it
    retries up to 3 times with exponential backoff (2s, 4s, 8s) as rate-limit
    cooldowns expire between attempts. The INTERACTIVE served path passes
    ``retry_on_exhaustion=False``: it must NEVER sleep while holding the inbound
    request or a turn-worker slot (design.md § "Router health/cooldown model");
    its sole-writer retry policy lives in ``universe_intelligence._call_writer``.
    """
    from tinyassets.exceptions import AllProvidersExhaustedError

    def _once() -> str:
        global _last_provider
        # Make the universe's open compute providers routable before the call.
        _register_open_providers_for(universe_context)
        # Only forward config / universe_context when set, so existing
        # routers/stubs with the 3-arg call_sync signature keep working
        # (backward-compat).
        kwargs: dict[str, Any] = {}
        if universe_context is not None:
            kwargs["universe_context"] = universe_context
        if operation is not None:
            kwargs["operation"] = operation
        if config is not None:
            result = _real_router.call_sync(role, prompt, system, config, **kwargs)
        else:
            result = _real_router.call_sync(role, prompt, system, **kwargs)
        _last_provider = result.provider
        if response_observer is not None:
            try:
                response_observer(result)
            except Exception:  # telemetry failure cannot replay an earned answer
                logger.warning("Provider response receipt could not be recorded")
        return result.text

    # A context carrying a ProviderInvocationCarrier is NOT retryable, whatever
    # the caller asked for. The carrier is single use by design
    # (``provider_work_authority.validate_for_call`` removes it from the active
    # set), so attempt 2 re-enters the router with a spent carrier, raises
    # "provider invocation carrier is already consumed", and -- because Tenacity
    # reraises the LAST error -- throws away the real reason attempt 1 failed.
    #
    # That masking is the worse half. The founder's universe was diagnosed as a
    # quota problem and then as missing API keys, and was neither; the true
    # first failure had been overwritten by this retry every time
    # (Codex root-cause 2026-09-01, C1/C4).
    #
    # Re-minting a carrier per attempt would "fix" the error and destroy the
    # authority property, which exists so one invocation is budgeted and settled
    # exactly once. Retrying a spent authority is not a retry, it is a second
    # invocation, and that would have to be authorised above this boundary.
    carrier_armed = getattr(universe_context, "provider_invocation", None) is not None
    if not retry_on_exhaustion or carrier_armed:
        return _once()

    @retry(
        retry=retry_if_exception_type(AllProvidersExhaustedError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        reraise=True,
    )
    def _attempt() -> str:
        return _once()

    return _attempt()


def call_provider(
    prompt: str,
    system: str = "",
    *,
    role: str = "writer",
    fallback_response: str | None = None,
    config: Any = None,
    universe_context: Any = None,
    operation: str | None = None,
    retry_on_exhaustion: bool = True,
    response_observer: Callable[[ProviderResponse], None] | None = None,
) -> str:
    """Call an LLM provider with automatic fallback.

    Routes through the installed :class:`ProviderRouter`'s synchronous
    ``call_sync`` (which runs the async fallback chain in a dedicated thread).
    On transient exhaustion, retries up to 3 times with exponential backoff.
    Falls back to mock/``fallback_response`` only when forced or exhausted.

    Parameters
    ----------
    prompt:
        The user prompt.
    system:
        System prompt.
    role:
        Routing role (writer, judge, extract).
    fallback_response:
        Returned if all providers fail. If ``None`` in production, provider
        exhaustion surfaces as the real error rather than masquerading as an
        empty LLM response downstream.
    universe_context:
        Optional per-universe routing context (:class:`~tinyassets.providers.
        base.UniverseContext`) threaded through to ``call_sync`` so engine
        preference + vault auth resolve for the given universe instead of the
        process globals.
    operation:
        Optional server-owned operation bound to a provider authority carrier.
        Omit for ordinary unarmed calls.
    retry_on_exhaustion:
        When True (default) the router call is wrapped in a tenacity backoff
        that sleeps between retries on transient ``AllProvidersExhaustedError``.
        The interactive served path passes ``False`` so the inbound request is
        never blocked on a synchronous sleep.
    response_observer:
        Optional internal observer of a completed real response, before reducing
        it to text. Never called for mocked, skipped or failed attempts. Observer
        failures cannot discard the response or trigger another inference.
    """
    governed = operation is not None or universe_context is not None
    if _force_mock:
        if governed:
            raise PermissionError("governed provider calls cannot use force-mock output")
        if fallback_response is not None:
            return fallback_response
        # Preserve the exact legacy string (callers/tests may assert on it).
        return "[Mock response -- _FORCE_MOCK is True]"

    provider_error: Exception | None = None

    if _real_router is not None:
        try:
            observe = {} if response_observer is None else {"response_observer": response_observer}
            return _call_router_with_retry(
                role, prompt, system, config, universe_context, operation,
                retry_on_exhaustion,
                **observe,
            )
        except Exception as e:
            from tinyassets.exceptions import ProviderAuthorityHeldError

            if isinstance(e, ProviderAuthorityHeldError):
                raise
            provider_error = e
            logger.error(
                "All providers exhausted for role=%s after retries: %s", role, e,
            )

    if governed and provider_error is not None:
        raise provider_error
    if fallback_response is not None and not governed:
        logger.warning(
            "Using fallback response for role=%s (%d chars)",
            role, len(fallback_response),
        )
        return fallback_response
    if provider_error is not None:
        raise provider_error

    from tinyassets.exceptions import AllProvidersExhaustedError

    raise AllProvidersExhaustedError(
        f"No provider router available for role={role!r} and no fallback_response "
        "was provided."
    )
