"""Accounting source binding reuses broker authority without a local ledger."""
import socket
import sys
from types import SimpleNamespace

import pytest

from tests.support.broker_ipc import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.broker.owner_sources import publish
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.providers.definition import ProviderDefinition, _definition_id
from tinyassets.request_budget import TurnRequestBudget
from tinyassets.storage.agent_request_usage import UsageStore

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
                                reason="Unix broker peer credentials")


@pytest.fixture
def source(discovery):  # noqa: F811
    fields = dict(universe_id="cc-alice", owner_user_id="alice", access_method="api_key_http",
                  protocol="openai_chat", ref="grant-a", model="default-model")
    definition_id = _definition_id(**fields)
    definition = ProviderDefinition(**fields, id=definition_id, visibility="private",
                                    created_at="2026-10-05T00:00:00Z")
    publish(discovery.ledger, "alice", "cc-alice", definition.as_dict())
    discovery.arguments = dict(scope=("alice", "cc-alice", "usage"),
                               attempt=SimpleNamespace(source_ref="api_key_http:" + definition_id,
                                                       model="chosen-model"),
                               grant_id="grant-a", connection_id="conn-a", verb="POST",
                               request={"body": {"model": "chosen-model"}})
    discovery.store = UsageStore(discovery.root)
    return discovery


def test_installed_source_binding_uses_live_broker_grant(source):
    store = UsageStore(source.root, broker_ledger=source.ledger)
    store._validate_source(**source.arguments)
    assert not (source.root / "outbound.db").exists()
    assert not (source.root / ".tinyassets.db").exists()
    assert not (source.root / "cc-alice").exists()


@pytest.mark.parametrize("change", ["owner", "center", "revoked_resource"])
def test_source_grant_query_itself_enforces_broker_scope(source, change):
    scope = ("alice", "cc-alice", "usage")
    if change == "owner":
        scope = ("bob", "cc-alice", "usage")
    elif change == "center":
        scope = ("alice", "cc-bob", "usage")
    else:
        source.ledger.revoke_connection("conn-a")
    if change == "center":
        assert source.store._validate_source_grant(scope, "grant-a", "conn-a") is None
    else:
        with pytest.raises(ProviderAuthorityHeldError):
            source.store._validate_source_grant(scope, "grant-a", "conn-a")
    assert not (source.root / "outbound.db").exists()


@pytest.mark.parametrize("change", ["owner", "center", "connection", "model", "revoked",
                                    "outage", "fence"])
def test_source_binding_refuses_changed_or_unavailable_authority(source, change, monkeypatch):
    from tinyassets.broker.ops import new_op_id

    budget = TurnRequestBudget("alice", "cc-alice")
    budget.persist(source.root)
    ordinal = budget.reserve(owner="alice", universe="cc-alice",
                             source_ref=source.arguments["attempt"].source_ref,
                             model="chosen-model", free=True)
    args = dict(source.arguments)
    args.pop("attempt")
    args["scope"] = budget._scope
    if change == "owner":
        args["scope"] = ("bob", "cc-alice", budget.usage_id)
    elif change == "center":
        args["scope"] = ("alice", "cc-bob", budget.usage_id)
    elif change == "connection":
        args["connection_id"] = "foreign-connection"
    elif change == "model":
        args["request"] = {"body": {"model": "different-model"}}
    elif change == "revoked":
        source.ledger.revoke_grant("grant-a")
    elif change == "outage":
        from tinyassets.broker import supervisor
        monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    else:
        source.broker.state["token"] = "stale"
    try:
        if change == "center":
            assert source.store.issue_reference(ordinal=ordinal, operation_id=new_op_id(), **args)
        else:
            with pytest.raises(ProviderAuthorityHeldError):
                source.store.issue_reference(ordinal=ordinal, operation_id=new_op_id(), **args)
    finally:
        budget._lease.close()
    assert not (source.root / "outbound.db").exists()
    assert not (source.root / ".tinyassets.db").exists()
