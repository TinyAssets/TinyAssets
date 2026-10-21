"""Shared unpowered model catalogue: actual ownership/storage, synthetic HTTP."""

import json

import pytest

from tests import test_served_model_preferences as integration
from tinyassets import daemon_server
from tinyassets.api import permissions
from tinyassets.providers import discovery_snapshot
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

rig = integration.rig
reader = integration.reader
configured = integration.configured


@pytest.fixture
def catalogue(configured):
    base = configured.rig.base
    (base / "u-models" / "soul.md").write_text("# Fixture home", encoding="utf-8")
    daemon_server.grant_universe_access(base, universe_id="u-models", actor_id="owner",
                                       permission="admin")
    return configured


def read(**kwargs):
    """The complete catalogue: the shared domain read the owner door serves.

    The connector (the model door) projects it; see
    `test_full_catalogue_survives_read_limit_and_adapter`."""
    from tinyassets.api.graph_reads import read_graph

    return json.loads(read_graph(target="model_options", **kwargs))














def test_foreign_registered_source_is_not_listed(catalogue):
    from tests.support.broker_double import seed_unchecked_definition

    foreign = seed_unchecked_definition(
        universe_id="u-models", owner_user_id="different-owner", access_method="api_key_http",
        protocol="openai_chat", model="private-model", ref="private-grant",
    )
    assert foreign.id not in json.dumps(read())




@pytest.mark.parametrize("case", ["anonymous", "other_actor", "non_admin", "foreign_graph",
                                 "no_home", "incomplete_home"])
def test_scope_refuses_before_discovery(catalogue, reader, monkeypatch, case):
    if case == "anonymous":
        monkeypatch.setattr(permissions, "is_authenticated_request", lambda: False)
    elif case == "other_actor":
        monkeypatch.setattr(permissions, "current_actor_id", lambda: "other-owner")
    elif case == "non_admin":
        daemon_server.grant_universe_access(catalogue.rig.base, universe_id="u-models",
                                           actor_id="owner", permission="read")
    elif case == "no_home":
        with SQLiteProviderWorkAuthorityStore(catalogue.rig.base).connection() as conn:
            conn.execute("DELETE FROM founder_home")
    elif case == "incomplete_home":
        (catalogue.rig.base / "u-models" / "soul.md").unlink()
    result = read(graph_id="foreign-home" if case == "foreign_graph" else "")
    assert "error" in result and "options" not in result
    assert reader[0] == []




def test_scope_change_during_refresh_refuses_entire_result(catalogue, reader, monkeypatch):
    def rebind(**kwargs):
        result = reader[1](**kwargs)
        daemon_server.set_founder_home(catalogue.rig.base, founder_sub="owner",
                                       universe_id="other-home", platform_generated=True)
        return result

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", rebind)
    assert read() == {"error": "not_found", "resource": "model_options"}


def test_catalogue_does_not_serialize_private_source_metadata(catalogue):
    result = read()
    encoded = json.dumps(result)
    for secret in ("grant-models", "conn-models", "vault://", "owned.example",
                   str(catalogue.rig.base), "source_digest", "authenticated_account_id"):
        assert secret not in encoded
    assert result["sources"][0]["observed_at"] and result["sources"][0]["expires_at"]
