"""Resolve an owner definition to the broker-backed HTTP executor."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tinyassets.providers.base import BaseProvider
from tinyassets.providers.definition import ProviderDefinition

if TYPE_CHECKING:
    from tinyassets.providers.router import ProviderRouter

def provider_for_definition(definition: ProviderDefinition) -> BaseProvider:
    """Return the broker-backed HTTP executor for a registered definition."""
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider

    return ApiKeyHttpProvider(definition)


def register_universe_open_providers(
    router: "ProviderRouter", universe_id: str
) -> list[str]:
    """Resolve + register a universe's registered ProviderDefinitions into `router`.

    The additive bridge from the open registry to live routing: once a universe's
    open providers are registered (by name — ``api_key_http:<def-id>``), the router chain
    logic
    (``effective_chain`` / ``call`` with the universe's ``allowed_providers`` +
    preference) can route to them — no router rewrite. Idempotent: ``router.register``
    replaces by name, and definition ids are stable, so repeated calls converge.

    Isolation is preserved even though the global router accumulates providers across
    universes: an ``ApiKeyHttpProvider`` re-checks, per call, that the grant is bound
    to the RUNNING universe (from ``universe_dir``), so a provider registered for
    universe A cannot serve universe B. Malformed definitions are skipped defensively
    rather than aborting the whole registration.

    Returns the registered provider names.
    """
    from tinyassets.providers.definition import list_definitions

    names: list[str] = []
    for definition in list_definitions(universe_id):
        try:
            provider = provider_for_definition(definition)
        except (ValueError, KeyError):
            continue
        router.register(provider)
        names.append(provider.name)
    return names
