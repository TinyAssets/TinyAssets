"""HTTP compute over real authenticated broker IPC; scripted upstream only."""
import asyncio
import json
import socket
import sys

import pytest

from tests.test_broker_discovery_http import discovery  # noqa: F401
from tests.test_broker_server import Script, broker  # noqa: F401
from tinyassets.broker.client import BrokerClient
from tinyassets.broker.ledger_queries import AUTHORIZED_CONNECTION
from tinyassets.exceptions import ProviderUnavailableError
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.providers.base import ModelConfig
from tinyassets.providers.definition import ProviderDefinition

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
    reason="the broker authenticates Unix socket peers")


@pytest.fixture
def inference(discovery):  # noqa: F811
    # Synthetic setup remains in the fixture, never the daemon consumer.
    with discovery.ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET scopes_json=?, allowed_endpoints_json=?",
                     (json.dumps(["POST"]), json.dumps([{
                         "host": "models.example.com", "path_template": "/chat",
                         "methods": ["POST"]}])))
    response = b'{"choices":[{"message":{"content":"scoped reply"}}]}'
    discovery.broker.upstreams["next"] = lambda: Script([response[:12], response[12:]])
    definition = ProviderDefinition(
        id="fixture", universe_id="cc-alice", owner_user_id="alice",
        access_method="api_key_http", protocol="chat_messages", model="fixture",
        ref="grant-a", visibility="private", created_at="2026-10-04T00:00:00Z")
    discovery.provider = ApiKeyHttpProvider(definition)
    discovery.config = ModelConfig(invocation_owner_user_id="alice")

    def complete():
        return asyncio.run(discovery.provider.complete(
            "hello", "", discovery.config, universe_dir=discovery.root / "cc-alice"))

    discovery.complete = complete
    return discovery


def test_compute_real_ipc_queries_then_streams_scripted_reply(inference):
    assert inference.complete().text == "scoped reply"
    assert len(inference.broker.sent) == 1
    principal, grant, verb, request = inference.broker.sent[0]
    assert (principal, grant, verb) == ("alice", "grant-a", "POST")
    assert request["url"] == "https://models.example.com/chat"
    assert not (inference.root / "outbound.db").exists()


def test_compute_real_ipc_rechecks_revocation_at_stream_admission(inference, monkeypatch):
    query = BrokerClient.ledger_query

    def revoke_after_acquire(client, **kwargs):
        result = query(client, **kwargs)
        if kwargs["query"] == AUTHORIZED_CONNECTION:
            inference.ledger.revoke_grant("grant-a")
        return result

    monkeypatch.setattr(BrokerClient, "ledger_query", revoke_after_acquire)
    with pytest.raises(ProviderUnavailableError, match="grant resolution failed"):
        inference.complete()
    assert not inference.broker.sent
    assert not (inference.root / "outbound.db").exists()


def test_compute_real_ipc_rejects_stale_fence_without_fallback(inference):
    inference.broker.state["token"] = "stale"
    with pytest.raises(ProviderUnavailableError, match="broker admission unavailable"):
        inference.complete()
    assert not inference.broker.sent
    assert not (inference.root / "outbound.db").exists()
