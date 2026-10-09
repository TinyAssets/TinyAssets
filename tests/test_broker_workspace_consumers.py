"""Workspace admission carries trusted run scope through real broker IPC."""
# ruff: noqa: F811 -- imported fixtures
import socket
import sys
from types import SimpleNamespace

import pytest

from tests.test_broker_discovery_http import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.effectors import EffectChain, EffectFailedError, workspace
from tinyassets.graph_compiler import BranchExecutionContext, _wrap_with_effects

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"), reason="Unix peer credentials")


@pytest.fixture
def rig(discovery):
    from tinyassets.storage.effector_consents import grant_consent
    from tinyassets.storage.workspace_authority import workspace_consent_destination

    base = discovery.root / "cc-alice"
    base.mkdir()
    with discovery.ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET scopes_json=?", ('["git_read:owner/repo"]',))
    grant_consent(base, sink="workspace", destination=workspace_consent_destination(
        "workspace_checkout", "owner/repo", connection_id="conn-a", host="models.example.com"),
        granted_by="alice")
    discovery.base = base
    return discovery


def invoke(rig, *, owner="alice", center="cc-alice", context=True):
    node = SimpleNamespace(node_id="checkout", effects=["workspace"], output_keys=["packet"],
                           input_keys=[], timeout_seconds=0)
    packet = dict(sink="workspace", op="checkout", repo="owner/repo", connection_id="conn-a",
                  grant_id="grant-a", owner_user_id="alice", principal="alice")
    chain = EffectChain(base_path=rig.base, run_id="fixture", dry_run=True)
    wrapped = _wrap_with_effects(
        lambda state: {"packet": packet}, node, chain, [], None,
        execution_context=BranchExecutionContext(owner_user_id=owner, universe_id=center)
        if context else None)
    wrapped({})
    return chain.evidence["checkout"]["workspace"]


def test_compiler_dispatch_workspace_admission_uses_broker_without_local_ledger(rig):
    assert invoke(rig)["dry_run"] is True
    mount = SimpleNamespace(connection_id="conn-a", grant_id="grant-a")
    resource = workspace._connection_for_mount(rig.base, mount, fallback=None, principal="alice")
    assert resource.connection_id == "conn-a"
    assert not (rig.root / "outbound.db").exists()


@pytest.mark.parametrize("kwargs,kind", [
    ({"owner": "bob"}, "connection_authority_unavailable"),
    ({"center": "cc-bob"}, "execution_context_mismatch"),
    ({"context": False}, "no_universe_authority"),
])
def test_packet_owner_never_supplies_workspace_authority(rig, kwargs, kind):
    from tinyassets.auth.middleware import identity_context

    with identity_context(None), pytest.raises(EffectFailedError) as caught:
        invoke(rig, **kwargs)
    assert kind in str(caught.value) or caught.value.error_kind == kind
    assert not (rig.root / "outbound.db").exists()


def test_mount_revalidation_refuses_revocation_and_foreign_owner(rig):
    mount = SimpleNamespace(connection_id="conn-a", grant_id="grant-a")
    for owner in ("bob", ""):
        with pytest.raises(workspace._Refused):
            workspace._connection_for_mount(rig.base, mount, fallback=object(), principal=owner)
    rig.ledger.revoke_grant("grant-a")
    with pytest.raises(workspace._Refused, match="authority refused"):
        workspace._connection_for_mount(rig.base, mount, fallback=object(), principal="alice")


def test_workspace_broker_outage_has_no_local_fallback(rig, monkeypatch):
    from tinyassets.broker import supervisor
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    with pytest.raises(EffectFailedError, match="broker unavailable"):
        invoke(rig)
    assert not (rig.root / "outbound.db").exists()
