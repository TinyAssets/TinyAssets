"""Workspace admission carries trusted run scope through real broker IPC."""
# ruff: noqa: F811 -- imported fixtures
import socket
import sys
from types import SimpleNamespace

import pytest

from tests.support.broker_ipc import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.effectors import EffectChain
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








