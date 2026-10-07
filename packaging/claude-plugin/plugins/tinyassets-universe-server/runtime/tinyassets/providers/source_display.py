"""The owner's own name for a compute connection. Display only, never authority.

A served reply's ``provider`` is the routing identity the router needs
(``api_key_http:provdef_ed0169c8...``), and on 2026-09-25 that is what the app's
"Answered by" line showed a founder about their own universe. It names nothing
they ever typed.

The name they DID see when they connected is recoverable from data that is
already stored, with no hardcoded vendor anywhere:

    provider name -> ProviderDefinition.ref (a grant id)
                  -> ConnectionGrant.connection_id
                  -> ConnectionView.destination

The guided model sign-in deposits its connection as ``model:<preset id>``
(``onboarding.model_bootstrap``), and the installed preset carries the
``display_name`` the founder clicked ("OpenRouter"). Anything else is a
connection they named themselves, so the destination IS their name for it.

Nothing here reads a credential, and none of it can widen routing: an unresolved
source returns "" and the renderer falls back to the routing identity rather
than inventing a label.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PREFIX = "api_key_http:"
#: Destinations the guided sign-in writes: ``model:<acquisition preset id>``.
_PRESET_PREFIX = "model:"
#: The renderer already bounds what it will print; this only bounds what we read.
_MAX_LABEL_CHARS = 200


def _preset_display_name(preset_id: str) -> str:
    """The installed preset's own display name, or "" when it is not installed."""
    from tinyassets.onboarding.hosted_model_auth import HostedAuthError, load_preset

    try:
        return load_preset(preset_id).display_name
    except (HostedAuthError, OSError, ValueError, KeyError):
        return ""


def source_display_name(*, base: Path | str, universe_id: str, provider: str) -> str:
    """The display label for *provider* in *universe_id*, or "" when unresolved.

    Defensive by construction: this decorates a reply that has already been
    earned, so every miss (a CLI provider name, a retired definition, a revoked
    grant, an uninstalled preset, an unreadable ledger) returns "" rather than
    raising into the response path.
    """
    if not isinstance(provider, str) or not provider.startswith(_PREFIX):
        return ""  # a subscription CLI name is already the owner-facing word
    definition_id = provider[len(_PREFIX):]
    if not definition_id or not universe_id:
        return ""
    try:
        from tinyassets.providers.definition import get_definition
        from tinyassets.storage.outbound_connections import ConnectionLedger

        definition = get_definition(universe_id, definition_id)
        if definition is None:
            return ""
        from tinyassets.broker.supervisor import broker_selected

        if broker_selected():
            from tinyassets.broker.ledger_queries import granted_resource_row

            row = granted_resource_row(Path(base), principal=definition.owner_user_id,
                                       command_center=universe_id, grant_id=definition.ref)
            destination = str(row.get("destination") or "")
        else:
            ledger = ConnectionLedger(Path(base) / "outbound.db")
            grant = ledger.get_grant(definition.ref)
            if grant is None or grant.universe_id != universe_id:
                # Never label a source with another universe's connection.
                return ""
            connection = ledger.get_connection(grant.connection_id)
            destination = "" if connection is None else str(connection.destination or "")
    except Exception:  # noqa: BLE001 - a label is never worth failing a reply over
        logger.debug("source display name unresolved for %s", provider, exc_info=True)
        return ""
    if not destination or len(destination) > _MAX_LABEL_CHARS:
        return ""
    if destination.startswith(_PRESET_PREFIX):
        return _preset_display_name(destination[len(_PRESET_PREFIX):])
    return destination
