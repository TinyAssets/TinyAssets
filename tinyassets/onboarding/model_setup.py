"""Credential-blind app routing: empty setup is not failed provider recovery."""

from __future__ import annotations

from pathlib import Path


def model_setup_state(base: Path, *, universe: Path, uid: str, owner: str) -> str:
    """Read local readiness and setup presence; never discover, bind or enable.

    Exceptions remain unavailable to the caller, never evidence of empty state.
    This does not prove upstream sign-in, account quota or model eligibility.
    """
    from tinyassets.api.universe import _config_yaml_is_parseable
    from tinyassets.commons_catalog import list_bindings
    from tinyassets.config import load_universe_config
    from tinyassets.credential_vault import load_credential_vault
    from tinyassets.onboarding.serving import _require_current_admin
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_serving_binding import (
        UnknownServingProvider,
        serving_connection_is_current,
    )
    from tinyassets.providers.definition import list_definitions

    _require_current_admin(base, universe_id=uid, owner=owner)
    try:
        if serving_connection_is_current(
            base, universe_dir=universe, universe_id=uid, owner_user_id=owner,
        ):
            return "connected"
    except (PermissionError, UnknownServingProvider):
        # A revoked/rotated source is existing setup, not a first-time connection.
        pass
    from tinyassets.providers.connection_lifecycle import intentionally_disconnected

    if intentionally_disconnected(base, owner=owner, uid=uid):
        records = load_credential_vault(universe)
        if not any(r.get("credential_type") in {"llm_subscription", "llm_api_key"}
                   for r in records):
            return "disconnected"
    if load_provider_assignment(base, universe_id=uid) is not None:
        return "recovery"
    if list_definitions(uid) or list_bindings(base, universe_id=uid, limit=1):
        return "recovery"
    records = load_credential_vault(universe)
    if any(record.get("credential_type") in {"llm_subscription", "llm_api_key"}
           for record in records):
        return "recovery"
    # The generic hosted-model bootstrap uses an explicit destination namespace;
    # a partially registered key is not permission to start another OAuth flow.
    if any(record.get("credential_type") == "http"
           and str(record.get("service", "")).startswith("model:") for record in records):
        return "recovery"
    config_file = universe / "config.yaml"
    if config_file.exists():
        if not _config_yaml_is_parseable(config_file):
            raise ValueError("model setup configuration unavailable")
        if str(load_universe_config(universe).engine_source or "").strip() != "byo_api_key":
            return "recovery"
    return "empty"
