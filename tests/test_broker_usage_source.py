"""Accounting source binding reuses broker authority without a local ledger."""
import json
import socket
import sys
from types import SimpleNamespace

import pytest

from tests.support.broker_ipc import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.providers.definition import _definition_id
from tinyassets.storage.agent_request_usage import UsageStore

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
                                reason="Unix broker peer credentials")


@pytest.fixture
def source(discovery):  # noqa: F811
    fields = dict(universe_id="cc-alice", owner_user_id="alice", access_method="api_key_http",
                  protocol="openai_chat", ref="grant-a", model="default-model")
    definition_id = _definition_id(**fields)
    directory = discovery.root / "cc-alice"
    directory.mkdir()
    (directory / "provider_definitions.json").write_text(json.dumps([
        dict(fields, id=definition_id, visibility="private", created_at="2026-10-05T00:00:00Z")]))
    discovery.arguments = dict(scope=("alice", "cc-alice", "usage"),
                               attempt=SimpleNamespace(source_ref="api_key_http:" + definition_id,
                                                       model="chosen-model"),
                               grant_id="grant-a", connection_id="conn-a", verb="POST",
                               request={"body": {"model": "chosen-model"}})
    discovery.store = UsageStore(discovery.root)
    return discovery


def test_installed_source_binding_uses_live_broker_grant(source):
    source.store._validate_source(**source.arguments)
    assert not (source.root / "outbound.db").exists()
    assert not (source.root / ".tinyassets.db").exists()


@pytest.mark.parametrize("change", ["owner", "center", "revoked_resource"])
def test_source_grant_query_itself_enforces_broker_scope(source, change):
    scope = ("alice", "cc-alice", "usage")
    if change == "owner":
        scope = ("bob", "cc-alice", "usage")
    elif change == "center":
        scope = ("alice", "cc-bob", "usage")
    else:
        source.ledger.revoke_connection("conn-a")
    with pytest.raises(ProviderAuthorityHeldError):
        source.store._validate_source_grant(scope, "grant-a", "conn-a")
    assert not (source.root / "outbound.db").exists()


@pytest.mark.parametrize("change", ["owner", "center", "connection", "model", "revoked",
                                    "outage", "fence"])
def test_source_binding_refuses_changed_or_unavailable_authority(source, change, monkeypatch):
    args = dict(source.arguments)
    if change == "owner":
        args["scope"] = ("bob", "cc-alice", "usage")
    elif change == "center":
        args["scope"] = ("alice", "cc-bob", "usage")
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
    with pytest.raises(ProviderAuthorityHeldError):
        source.store._validate_source(**args)
    assert not (source.root / "outbound.db").exists()
    assert not (source.root / ".tinyassets.db").exists()
