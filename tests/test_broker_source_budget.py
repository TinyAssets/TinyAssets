"""Source classification uses authenticated IPC, never an unavailable local ledger."""
# ruff: noqa: F811 -- imported pytest fixtures
import json
import socket
import sys
from types import SimpleNamespace

import pytest

from tests.support.broker_ipc import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.request_budget import _source_budget_facts, metered_free_source

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"), reason="Unix peer credentials")


@pytest.fixture
def source(discovery, monkeypatch):
    from tinyassets.providers import definition

    installed = SimpleNamespace(owner_user_id="alice", ref="grant-a")
    monkeypatch.setattr(definition, "get_definition", lambda *a: installed)
    with discovery.ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET allowed_endpoints_json=?", (json.dumps([
            {"host": "openrouter.ai", "path_template": "/chat", "methods": ["POST"]}]),))
    discovery.context = SimpleNamespace(
        universe_dir=discovery.root / "cc-alice",
        model_selection=SimpleNamespace(connection_id="api_key_http:fixture", model_id="m:free"))
    return discovery


def test_source_budget_uses_broker_facts_without_local_ledger(source):
    owner, preset = _source_budget_facts(source.context, owner="alice")
    assert owner == "alice" and preset["requests_per_day"] > 0
    assert metered_free_source(source.context, None, owner="alice")
    assert not (source.root / "outbound.db").exists()


@pytest.mark.parametrize("change", [
    "owner", "missing-owner", "center", "revoked", "fence", "outage"])
def test_unavailable_source_never_becomes_unmetered(source, monkeypatch, change):
    from tinyassets.broker import supervisor

    owner = "alice"
    if change == "owner":
        owner = "bob"
    elif change == "missing-owner":
        owner = None
    elif change == "center":
        source.context.universe_dir = source.root / "cc-bob"
    elif change == "revoked":
        source.ledger.revoke_grant("grant-a")
    elif change == "fence":
        source.broker.state["token"] = "stale"
    else:
        monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    with pytest.raises(ProviderAuthorityHeldError):
        metered_free_source(source.context, None, owner=owner)
    assert not (source.root / "outbound.db").exists()
    assert not source.broker.sent


def test_source_budget_rejects_malformed_broker_projection(source, monkeypatch):
    from tinyassets.broker.client import BrokerClient

    query = BrokerClient.ledger_query

    def malformed(client, **kwargs):
        result = query(client, **kwargs)
        result["resource"]["allowed_endpoints_json"] = "not-json"
        return result

    monkeypatch.setattr(BrokerClient, "ledger_query", malformed)
    with pytest.raises(ProviderAuthorityHeldError):
        metered_free_source(source.context, None, owner="alice")
    assert not (source.root / "outbound.db").exists()
