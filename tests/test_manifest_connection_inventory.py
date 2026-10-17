"""One unavailable HTTP definition must not hide a different live member."""

import pytest

from tests import test_served_model_preferences as integration
from tinyassets.api.pending_requests import _serving_llm_bound
from tinyassets.provider_serving_binding import list_serving_universes

rig = integration.rig
reader = integration.reader
configured = integration.configured


@pytest.mark.parametrize("change", ["remove_definition", "revoke_grant"])
def test_readiness_and_inventory_check_independent_http_members(configured, monkeypatch, change):
    from tinyassets.providers.definition import _store_path

    integration.enable(configured)
    base = configured.rig.base
    from tinyassets.provider_assignment import load_provider_assignment

    assert load_provider_assignment(base, universe_id="u-models").candidates[0].provider.startswith(
        "api_key_http:"
    )
    assert _serving_llm_bound(base, "u-models", "owner") is True
    assert list_serving_universes(base) == ["u-models"]
    # Fixture-owned records only; no real credentials, provider or network.
    definition_path = _store_path("u-models")
    definition_bytes = definition_path.read_bytes()
    if change == "remove_definition":
        _store_path("u-models").unlink()
    else:
        configured.rig.ledger.revoke_grant("grant-models")
    monkeypatch.setattr("tinyassets.providers.call.get_provider_router",
                        lambda: pytest.fail("readiness must not inspect executor availability"))
    assert _serving_llm_bound(base, "u-models", "owner") is False
    assert list_serving_universes(base) == []
    assert _serving_llm_bound(base, "u-models", "another-owner") is False
    # Repair only the unavailable member; readiness must follow live broker state.
    if change == "remove_definition":
        definition_path.write_bytes(definition_bytes)
    else:
        # Restore the fixture's original row; public regrant creates a new grant ID.
        with configured.rig.ledger._connect() as db:
            db.execute("UPDATE outbound_connection_grants SET revoked_at=NULL WHERE grant_id=?",
                       ("grant-models",))
    assert _serving_llm_bound(base, "u-models", "owner") is True
    assert list_serving_universes(base) == ["u-models"]
