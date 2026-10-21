"""Accounting source binding reuses broker authority without a local ledger."""
import socket
import sys
from types import SimpleNamespace

import pytest

from tests.support.broker_ipc import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.broker.owner_sources import publish
from tinyassets.providers.definition import ProviderDefinition, _definition_id
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
