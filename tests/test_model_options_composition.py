"""Use the real owned catalogue producer without an inference or serving mutation."""

import json

import pytest

from tests import test_served_model_preferences as integration
from tinyassets.custom_agents import get_binding
from tinyassets.providers.model_options import model_options_document
from tinyassets.providers.served_model_plan import prepare_owned_model_plan

rig = integration.rig
reader = integration.reader
configured = integration.configured


@pytest.mark.parametrize("configured", ["http", "mixed"], indirect=True)
def test_actual_owned_catalog_projects_without_enabling_serving(configured, monkeypatch):
    # The native-first expectation below depends on a known finite HTTP cap.
    monkeypatch.setattr("tinyassets.request_budget.requests_today", lambda *a, **kw: (0, 0))
    base = configured.rig.base
    before = get_binding(base, universe_id="u-models",
                         binding_id=configured.binding["agent_binding_id"])
    prepared = prepare_owned_model_plan(
        base=base, universe=base / "u-models", owner="owner", agent=before,
    )
    result = model_options_document(prepared.catalog, prepared.plan, prepared.ineligible)
    assert result["kind"] == "advisory_model_options"
    assert result["policy_source"] == "automatic"
    assert result["generation"] == 0
    assert result["options"] and result["order"]
    assert get_binding(base, universe_id="u-models",
                       binding_id=before["agent_binding_id"]) == before
    if configured.native is not None:
        assert result["order"][0] == {"provider_ref": "codex", "model_id": ""}
        assert configured.native.calls == 0
    encoded = json.dumps(result)
    assert "grant-models" not in encoded
    assert str(base) not in encoded
    assert "source_digest" not in encoded
    assert "authenticated_account_id" not in encoded
