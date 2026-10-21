"""Capability authority is checked again in the transaction that uses it."""
import pytest

from tinyassets.storage.outbound_connections import ConnectionLedger


@pytest.fixture
def ledger(tmp_path):
    """The ledger where it actually lives: only the broker opens this path."""
    value = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    value.create_connection(
        connection_id="conn-a", owner_user_id="alice", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("GET", "POST"),
        provider="http", destination="fixture", credential_ref="vault://http/fixture",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/catalogue",
                            "methods": ["GET", "POST"]}])
    value.grant_connection(grant_id="grant-a", connection_id="conn-a",
                           owner_user_id="alice")
    return value


def document(**changes):
    return dict(action="configure", grant_id="grant-a", connection_id="conn-a",
                capability_kind="constant_headers", descriptor={"headers": {"X-Fixture": "yes"}},
                enabled=True, preview=False) | changes








