"""Router installation for the owner-bound model call service."""

from __future__ import annotations

import logging
from pathlib import Path

from tinyassets.providers.router import ProviderRouter

logger = logging.getLogger(__name__)
_router: ProviderRouter | None = ProviderRouter()


def set_provider_router(router: ProviderRouter | None) -> None:
    global _router
    _router = router


def get_provider_router() -> ProviderRouter | None:
    return _router


def get_last_provider() -> str:
    from tinyassets.providers.model_call import last_provider

    return last_provider()


def register_open_providers_for(universe_context: object) -> None:
    """Make the owner's open definitions available to the installed router."""
    if _router is None or universe_context is None:
        return
    universe_dir = getattr(universe_context, "universe_dir", None)
    if universe_dir is None:
        return
    from tinyassets.providers.provider_resolver import register_universe_open_providers

    register_universe_open_providers(_router, Path(universe_dir).name)
