"""The platform has no LLM (AGENTS.md Hard Rule 15) -- the one enforcement point.

Only a powered universe calls an LLM, with the credentials its owner connected,
for that universe alone. The platform never makes, needs or brokers a model call
for its own operation (onboarding, selection, ranking, moderation, investigation,
maintenance, monitoring), and no platform, host, maintainer or shared credential
ever serves one, including as a fallback.

Every provider launch in :class:`tinyassets.providers.router.ProviderRouter`
passes through the two checks below, and the router is the only code that calls
``BaseProvider.complete``. So this module is where the rule is enforced; nothing
else needs to remember it.

* :func:`require_owner_bound_context` runs when a call ENTERS the router. A call
  with no universe, or with a universe but no owner authority (neither a
  server-minted ``ProviderInvocationCarrier`` for that universe nor a live
  ``provider_request`` the router will authorize against the owner's serving
  binding), is refused before any provider is looked at, probed or configured.
* :func:`require_owner_bound_dispatch` runs immediately before a provider is
  launched. The launched provider must be the one the owner's authority names,
  it must resolve credentials from the universe (never the host), and it must
  not be an executor whose only credential source is the host process (a host
  environment API key, or the host's own local model server). Such executors
  declare ``credential_source = HOST_PROCESS_CREDENTIALS``. An owner who wants
  one of those sources connects it as their own open provider
  (``api_key_http:<definition>``), which carries their own credential.

A refusal raises :class:`~tinyassets.exceptions.PlatformLLMCallRefusedError`, a
``ProviderAuthorityHeldError``: every caller already propagates that class
instead of swallowing it as a provider fault, so the refusal is loud (Hard
Rule 8) and never degrades into a fallback string.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tinyassets.exceptions import PlatformLLMCallRefusedError

#: ``credential_source`` value declared by an executor whose only credential
#: source is the host process. It can never carry an owner's connection, so it
#: may never serve a universe.
HOST_PROCESS_CREDENTIALS = "host_process"


def is_host_credential_provider(provider: Any) -> bool:
    """Whether *provider* can only authenticate with the host's own credentials."""
    return getattr(provider, "credential_source", None) == HOST_PROCESS_CREDENTIALS


_NO_UNIVERSE = (
    "The platform has no LLM: a model call must come from a powered command center "
    "using its owner's own connected credentials. This call names no command center, "
    "so it was refused. No platform, host or shared credential serves it."
)
#: The owner-facing refusal. Run-failure taxonomy keys on "connect your
#: provider" (``tinyassets/api/runs.py``), so a universe that has no owner
#: authority reads as the actionable fix, not as a platform fault.
#:
#: The one definition. ``foreground_run_provider`` and ``provider_assignment``
#: each held a byte-identical copy of this string; the taxonomy, the canary and
#: six tests keyed on the literal, so three copies were three chances for the
#: sentence and its meaning to drift apart.
CONNECT_PROVIDER_MESSAGE = (
    "Connect your provider before running this command center. TinyAssets will not "
    "borrow platform credentials or start a metered trial."
)
#: Lead-in for a held run whose universe DOES have a provider connected.
#:
#: ``CONNECT_PROVIDER_MESSAGE`` is the right sentence for a universe with no
#: owner authority. It was ALSO the sentence for every other refusal on the run
#: lane, because `except Exception` handlers wrapped all of them in it. Live
#: 2026-09-30 (universe ``u-01ky3zh1arr8qth8jee7zx63pq``, runs
#: ``61184d8f21724915`` / ``4828ae18e2414e77``): an owner whose ``api_key_http``
#: source was connected, ready and serving their chat was told to connect a
#: provider -- the wrong cause, the wrong fix, and no other clue in the record.
#: A refusal with words of its own keeps them behind this lead-in, and the
#: taxonomy keys on the lead-in so the failure class is unchanged.
AUTHORITY_HELD_DETAIL = (
    "This command center's connected provider could not authorize this run: "
)
#: The same lead-in as errors stored before the universe -> command center rename
#: wrote it; readers of stored errors match either.
LEGACY_AUTHORITY_HELD_DETAIL = (
    "This universe's connected provider could not authorize this run: "
)
_NO_OWNER_AUTHORITY = (
    f"{CONNECT_PROVIDER_MESSAGE} (The platform has no LLM: this call names a "
    "command center but carries no owner authority, so it was refused rather than "
    "served from a platform or host credential.)"
)


def _carrier_universe_id(carrier: Any) -> str:
    receipt = getattr(carrier, "_receipt", None)
    return str(getattr(receipt, "universe_id", "") or "")


def require_owner_bound_context(universe_context: Any, *, operation: str | None) -> None:
    """Refuse a router call that is not bound to one universe's owner authority."""
    from tinyassets.provider_work_authority import ProviderInvocationCarrier
    from tinyassets.providers.base import UniverseContext

    if universe_context is None:
        raise PlatformLLMCallRefusedError(_NO_UNIVERSE)
    if not isinstance(universe_context, UniverseContext):
        raise PlatformLLMCallRefusedError(
            "The platform has no LLM: the call's command center context is not a "
            "UniverseContext, so it cannot name an owner."
        )
    universe_dir = universe_context.universe_dir
    if universe_dir is None:
        # A context with no universe directory is the retired single-universe
        # daemon shape: it would resolve config and credentials from process
        # globals. It names no owner, so it gets the owner-facing refusal.
        raise PlatformLLMCallRefusedError(_NO_OWNER_AUTHORITY)
    carrier = universe_context.provider_invocation
    if carrier is not None:
        # A carrier that is not the exact server-minted type is refused by the
        # router's own carrier validation (PermissionError) before any access.
        if (
            type(carrier) is ProviderInvocationCarrier
            and _carrier_universe_id(carrier) != Path(universe_dir).name
        ):
            raise PlatformLLMCallRefusedError(
                "The platform has no LLM: the provider invocation carrier belongs "
                "to a different command center than the one this call names."
            )
        return
    if universe_context.provider_request is None or not operation:
        raise PlatformLLMCallRefusedError(_NO_OWNER_AUTHORITY)


def require_owner_bound_dispatch(
    provider_name: str,
    *,
    provider: Any = None,
    universe_dir: Path | None,
    served_authority: Any = None,
    invocation_carrier: Any = None,
) -> None:
    """Refuse a provider launch the owner's authority does not name."""
    if universe_dir is None:
        raise PlatformLLMCallRefusedError(_NO_UNIVERSE)
    authority = served_authority if served_authority is not None else invocation_carrier
    if authority is None:
        raise PlatformLLMCallRefusedError(_NO_OWNER_AUTHORITY)
    if provider_name != getattr(authority, "provider", None):
        raise PlatformLLMCallRefusedError(
            f"The platform has no LLM: provider {provider_name!r} is not the "
            "provider the owner's binding names, so it may not serve this call."
        )
    if is_host_credential_provider(provider):
        raise PlatformLLMCallRefusedError(
            f"The platform has no LLM: {provider_name!r} can only use the host's "
            "credentials or the host's own model server, never the owner's. "
            "Connect this source to the command center as your own provider instead."
        )


__all__ = [
    "AUTHORITY_HELD_DETAIL",
    "CONNECT_PROVIDER_MESSAGE",
    "HOST_PROCESS_CREDENTIALS",
    "is_host_credential_provider",
    "require_owner_bound_context",
    "require_owner_bound_dispatch",
]
