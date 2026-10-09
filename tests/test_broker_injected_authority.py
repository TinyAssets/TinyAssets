"""Canonical injected consumers work through scoped broker authority."""
# ruff: noqa: F811 -- imported fixtures
import socket
import sys
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_broker_discovery_http import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tests.test_user_owned_cloud_automation import _cloud_authority_fixture
from tinyassets.broker.connection_authority import (
    BrokerConnectionAuthority,
    read_authority,
    require_connection_authority,
)
from tinyassets.storage.outbound_connections import ConnectionLedger, GrantResolutionError
from tinyassets.user_owned_cloud_automation import (
    AutomationAdmissionError,
    resolve_inactive_cloud_authority,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"), reason="Unix peer credentials")


@pytest.fixture
def cloud(discovery):
    definition, provider, ledger = _cloud_authority_fixture(discovery.root / "private-cloud")
    path = discovery.root / "private-cloud/outbound.db"
    daemon = threading.get_ident()

    def ledger_for(principal):
        assert threading.get_ident() != daemon
        return ConnectionLedger(path, data_root=discovery.root,
                                verify_authenticated_principal=lambda: principal)

    discovery.broker.server._ledger_for = ledger_for
    discovery.definition, discovery.provider, discovery.cloud_ledger = definition, provider, ledger
    discovery.authority = BrokerConnectionAuthority(
        discovery.root, "universe_alice", lambda: "acct_alice")
    return discovery


def test_actual_cloud_resolver_uses_scoped_snapshot(cloud):
    result = resolve_inactive_cloud_authority(
        cloud.definition, provider_store=cloud.provider, connection_ledger=cloud.authority)
    assert result.destination_grant_id == cloud.definition.destination_grant_id
    assert result.authority_source == "requester_owned"
    assert not (cloud.root / "outbound.db").exists()


@pytest.mark.parametrize("change", ["owner", "center", "revoked", "outage"])
def test_cloud_cannot_infer_scope_from_definition_or_fallback(cloud, change, monkeypatch):
    authority = cloud.authority
    if change == "owner":
        authority = replace(authority, verify_authenticated_principal=lambda: "bob")
    elif change == "center":
        authority = replace(authority, command_center="universe_bob")
    elif change == "revoked":
        cloud.cloud_ledger.revoke_grant(cloud.definition.destination_grant_id)
    else:
        from tinyassets.broker import supervisor
        monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    with pytest.raises(AutomationAdmissionError, match="destination_grant_unavailable"):
        resolve_inactive_cloud_authority(
            cloud.definition, provider_store=cloud.provider, connection_ledger=authority)
    assert not (cloud.root / "outbound.db").exists()


def test_closed_interface_rejects_ducks_and_local_ledger_in_selected_mode(cloud):
    for candidate in (cloud.cloud_ledger, SimpleNamespace(snapshot=lambda grant: None)):
        with pytest.raises(ValueError, match="canonical connection authority"):
            require_connection_authority(candidate)


def test_actual_cap_boundary_holds_then_revalidates_revocation(cloud):
    from tinyassets.effectors.outbound_boundary import execute_capped_action

    class Proxy:
        grant_id = cloud.definition.destination_grant_id

        def execute(self, *args, **kwargs):
            raise AssertionError("above-cap action must not send")

    args = dict(universe_dir=cloud.root / "universe_alice", ledger=cloud.authority,
                grant_id=cloud.definition.destination_grant_id, proxy=Proxy(), tool_authorized=True,
                action_value=2, action_unit="pull_requests", effect_key="held-probe",
                sink="pull_request", run_id="run", verb="POST", request={"title": "fixture"})
    result = execute_capped_action(**args)
    assert result["status"] == "held"
    cloud.cloud_ledger.revoke_grant(cloud.definition.destination_grant_id)
    with pytest.raises(PermissionError, match="current grant"):
        execute_capped_action(**args)


def test_snapshot_never_exposes_custody_and_empty_owner_refuses(cloud):
    principal, grant, view = read_authority(cloud.authority, cloud.definition.destination_grant_id)
    assert principal == grant.owner_user_id == view.owner_user_id == "acct_alice"
    assert "credential_ref" not in view.as_dict()
    with pytest.raises(PermissionError, match="directory differs"):
        read_authority(cloud.authority, cloud.definition.destination_grant_id,
                       universe_dir=cloud.root / "universe_bob")
    with pytest.raises(PermissionError):
        read_authority(replace(cloud.authority, verify_authenticated_principal=lambda: ""),
                       cloud.definition.destination_grant_id)
    with pytest.raises(GrantResolutionError):
        read_authority(cloud.authority, "missing")
