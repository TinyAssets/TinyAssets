"""Bootstrap readers preserve recovery display without treating it as authority."""
# ruff: noqa: F811 -- imported pytest fixtures
import json
import socket
import sys

import pytest

from tests.test_broker_discovery_http import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.broker.ledger_queries import BOOTSTRAP_RECOVERY, query_ledger
from tinyassets.onboarding.hosted_model_auth import HostedAuthError, load_preset
from tinyassets.onboarding.model_bootstrap import _pending_confirmation, endpoint_policy
from tinyassets.onboarding.model_bootstrap_candidate import prepare_candidate

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"), reason="Unix peer credentials")


@pytest.fixture
def setup(discovery, monkeypatch):
    from tinyassets.daemon_server import grant_universe_access, set_founder_home
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.providers.definition import register_definition
    from tinyassets.storage.pending_requests import create_request

    root = discovery.root
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    universe = root / "cc-alice"
    universe.mkdir()
    set_founder_home(root, founder_sub="alice", universe_id="cc-alice", platform_generated=True)
    grant_universe_access(root, universe_id="cc-alice", actor_id="alice", permission="admin")
    preset = load_preset("openrouter_user_models_v1")
    with discovery.ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET destination=?, allowed_endpoints_json=?",
                     ("model:" + preset.id, json.dumps(endpoint_policy(preset))))
    descriptor = {"protocol": preset.id, "catalogue_url": preset.catalogue_url,
                  "benchmark_url": preset.benchmark_url}
    discovery.ledger.configure_capability(connection_id="conn-a", capability_kind="model_discovery",
                                          descriptor=descriptor, enabled=True,
                                          expected_grant=discovery.ledger.get_grant("grant-a"))
    definition = register_definition(universe_id="cc-alice", owner_user_id="alice",
                                     access_method="api_key_http", protocol="chat_messages",
                                     model="fixture", ref="grant-a")
    access = {definition.id: ModelAccess("discovered").document()}
    request = create_request(universe, kind="Models", title="Fixture", body="Fixture", fields=[],
                             action={"type": "bind_model_access", "provider": definition.id,
                                     "model_access": access, "previous_membership": {},
                                     "proposed_membership": access}, dedupe_key="fixture")
    assert request is not None
    discovery.preset, discovery.definition = preset, definition
    discovery.pending = lambda: _pending_confirmation(
        root, universe=universe, uid="cc-alice", owner="alice", preset=preset)
    discovery.prepare = lambda: prepare_candidate(
        base=root, uid="cc-alice", owner="alice", grant_id="grant-a", preset=preset)
    return discovery


def test_bootstrap_candidate_and_pending_confirmation_read_over_ipc(setup):
    assert setup.prepare() == setup.definition.id
    assert setup.pending()["status"] == "confirmation_required"
    assert not (setup.root / "outbound.db").exists()
    assert not setup.broker.sent


@pytest.mark.parametrize("revoke", ["grant", "connection"])
def test_revoked_bootstrap_can_be_displayed_but_cannot_prepare(setup, revoke):
    if revoke == "grant":
        setup.ledger.revoke_grant("grant-a")
    else:
        setup.ledger.revoke_connection("conn-a")
    assert setup.pending()["status"] == "confirmation_required"
    with pytest.raises(HostedAuthError, match="model_connection_requires_recovery"):
        setup.prepare()
    assert not (setup.root / "outbound.db").exists()


@pytest.mark.parametrize("scope", [
    {"principal": "bob"}, {"command_center": "cc-bob"}, {"grant_id": "foreign"},
    {"connection_id": "foreign"}])
def test_bootstrap_recovery_query_never_returns_foreign_metadata(setup, scope):
    args = dict(query=BOOTSTRAP_RECOVERY, principal="alice", command_center="cc-alice",
                grant_id="grant-a") | scope
    assert query_ledger(setup.root, **args) == {"recovery": None}


def test_bootstrap_recovery_projection_contains_no_credential_or_authority(setup):
    result = query_ledger(setup.root, query=BOOTSTRAP_RECOVERY, principal="alice",
                          command_center="cc-alice", grant_id="grant-a")
    assert set(result["recovery"]) == {"destination", "descriptor"}
    assert "vault://" not in json.dumps(result)


def test_bootstrap_outage_is_explicit_without_daemon_ledger(setup, monkeypatch):
    from tinyassets.broker import supervisor
    from tinyassets.storage.outbound_connections import ProxyRequestError

    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    for consumer in (setup.pending, setup.prepare):
        with pytest.raises(ProxyRequestError):
            consumer()
    assert not (setup.root / "outbound.db").exists()
