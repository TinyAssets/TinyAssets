"""D25 serving custody reads use the caller's admitted owner over broker IPC."""
from types import SimpleNamespace

import pytest

from tinyassets.broker import ledger_queries, supervisor
from tinyassets.providers.definition import register_definition
from tinyassets.storage.outbound_connections import ConnectionLedger


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path)
    definitions = {}
    for owner in ("alice", "bob"):
        ledger.create_connection(
            connection_id=f"conn-{owner}", owner_user_id=owner, connection_class="http",
            connection_type="http", auth_scheme="bearer", scopes=("POST",), provider="http",
            destination=f"compute:{owner}", credential_ref=f"vault://http/{owner}",
            allowed_endpoints=[{"host": "models.example.com", "path_template": "/chat",
                                "methods": ["POST"]}])
        ledger.grant_connection(grant_id=f"grant-{owner}", connection_id=f"conn-{owner}",
                                owner_user_id=owner)
        ledger.configure_capability(
            connection_id=f"conn-{owner}", capability_kind="model_use", enabled=True,
            descriptor={"wire": "openai_chat", "billing": "free", "models": [
                {"id": f"{owner}-fixture", "tools": True, "context": 8192},
            ]},
        )
        definitions[owner] = register_definition(
            universe_id="cc-alice", owner_user_id=owner, access_method="api_key_http",
            protocol="openai_chat", model=f"{owner}-fixture", ref=f"grant-{owner}")
    from tests.support.broker_double import seed_unchecked_definition

    # Simulate legacy/tampered metadata: broker registration now refuses this
    # foreign ref, and runtime reads must still reject an already-stored row.
    fields = dict(universe_id="cc-alice", owner_user_id="alice", access_method="api_key_http",
                  protocol="chat_messages", model="foreign-fixture", ref="grant-bob")
    definitions["foreign"] = seed_unchecked_definition(**fields)
    calls = []

    class Client:
        def __init__(self, path, *, principal, command_center, **kwargs):
            self.principal, self.center = principal, command_center

        def ledger_query(self, **kwargs):
            calls.append((self.principal, self.center, kwargs))
            return ledger_queries.local_query(ledger, principal=self.principal,
                                              command_center=self.center, **kwargs)

    monkeypatch.setattr(supervisor, "get_supervisor", lambda _: SimpleNamespace(
        socket_path=tmp_path / "broker.sock", fence=lambda: (1, "synthetic"), verify_broker=None))
    monkeypatch.setattr("tinyassets.broker.client.BrokerClient", Client)

    def no_local(*args, **kwargs):
        raise AssertionError("daemon opened a local ledger")

    monkeypatch.setattr("tinyassets.storage.outbound_connections.ConnectionLedger", no_local)
    return SimpleNamespace(root=tmp_path, ledger=ledger, definitions=definitions, calls=calls)
