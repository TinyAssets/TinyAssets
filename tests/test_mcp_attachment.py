"""MCP metadata never supplies authority or revives an old incarnation."""
# ruff: noqa: F811
from dataclasses import asdict

import pytest

from tests.test_broker_ledger_queries import ledger  # noqa: F401
from tinyassets.broker.capabilities import local_operation
from tinyassets.connection_oauth.transport import OAuthError
from tinyassets.mcp_attachment import Attachment
from tinyassets.storage.outbound_connections import GrantResolutionError


def operation(ledger, *, value=None, expected=None, **scope):
    return local_operation(ledger, **(dict(principal="alice", command_center="cc-alice") | scope),
        document=dict(action="configure" if value else "read", grant_id="grant-alice",
            connection_id="conn-alice", capability_kind="mcp", enabled=value is not None,
            preview=False, descriptor=dict(incarnation=ledger.incarnation("conn-alice"),
                                           expected=expected, value=value)))


def draft(**changes):
    return asdict(Attachment("https://models.example.com/v1/chat", "Example")) | changes


def test_roundtrip_preserves_http_rows_and_cleanup(ledger):
    before = ledger._get_connection_resource("conn-alice")
    assert operation(ledger) == {"descriptor": None}
    assert operation(ledger, value=draft()) == {"descriptor": draft()}
    assert operation(ledger) == {"descriptor": draft()}
    assert ledger._get_connection_resource("conn-alice") == before
    revoked = draft(revision=2, state="revoked")
    operation(ledger, value=revoked, expected=draft())
    with pytest.raises(GrantResolutionError):
        operation(ledger, value=draft(revision=3), expected=revoked)
    assert ledger._get_connection_resource("conn-alice") == before


@pytest.mark.parametrize("scope", [{"principal": "bob"}, {"command_center": "cc-bob"}])
@pytest.mark.parametrize("write", [False, True])
def test_foreign_owner_and_center_cannot_read_or_write(ledger, scope, write):
    operation(ledger, value=draft())
    with pytest.raises(GrantResolutionError):
        operation(ledger, value=draft() if write else None, **scope)


def test_revoke_and_reincarnate_fence_metadata(ledger):
    operation(ledger, value=draft())
    ledger.revoke_connection("conn-alice")
    with pytest.raises(GrantResolutionError):
        operation(ledger)
    with ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET revoked_at=NULL, incarnation='new' "
                     "WHERE connection_id='conn-alice'")
    assert operation(ledger) == {"descriptor": None}


def test_cas_rejects_stale_revision(ledger):
    operation(ledger, value=draft())
    with pytest.raises(GrantResolutionError):
        operation(ledger, value=draft(revision=2, state="connecting"))


def test_metadata_api_cannot_claim_activation_or_change_a_connecting_endpoint(ledger):
    operation(ledger, value=draft())
    with pytest.raises(PermissionError, match="coordinator"):
        operation(ledger, value=draft(revision=2, state="active"), expected=draft())
    connecting = draft(revision=2, state="connecting")
    operation(ledger, value=connecting, expected=draft())
    with pytest.raises(GrantResolutionError):
        operation(ledger, value=draft(revision=3), expected=connecting)
    with pytest.raises(PermissionError, match="endpoint"):
        operation(ledger, value=draft(revision=3, state="connecting",
                                     endpoint="https://models.example.com/other"),
                  expected=connecting)


@pytest.mark.parametrize("changes", [
    {"endpoint": "https://elsewhere.example.com/mcp"},
    {"endpoint": "https://models.example.com/v1/chat?token=secret"},
    {"schema_version": 2}, {"transport": "stdio"}, {"session_id": "private"},
])
def test_invalid_or_ungranted_metadata_is_not_persisted(ledger, changes):
    with pytest.raises((ValueError, PermissionError, RuntimeError, OAuthError)):
        operation(ledger, value=draft(**changes))
    assert operation(ledger) == {"descriptor": None}


def test_unknown_stored_version_fails_without_deletion(ledger):
    operation(ledger, value=draft())
    with ledger._connect() as conn:
        conn.execute("UPDATE mcp_attachments SET descriptor_json="
                     "replace(descriptor_json, '\"schema_version\": 1', '\"schema_version\": 2')")
    with pytest.raises(ValueError, match="unsupported"):
        operation(ledger)
    with ledger._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM mcp_attachments").fetchone()[0] == 1


def test_rollback_cleanup_can_remove_unknown_metadata_version(ledger):
    operation(ledger, value=draft())
    with ledger._connect() as conn:
        conn.execute("UPDATE mcp_attachments SET descriptor_json='{}'")
    assert ledger.delete_connection("conn-alice")
    with ledger._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM mcp_attachments").fetchone()[0] == 0
    assert ledger._get_connection_resource("conn-bob") is not None
