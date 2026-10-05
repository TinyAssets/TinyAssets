"""Metadata readers preserve owner checks without daemon ledger access."""
# ruff: noqa: F811 -- imported fixtures
import socket
import sys

import pytest

from tests.test_broker_disconnect import removal  # noqa: F401
from tests.test_broker_discovery_http import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.broker.owner_metadata import names, view

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"), reason="Unix peer credentials")


def ask(connection):
    from tinyassets.api.pending_requests import request_from_user
    return request_from_user(universe_id="cc-alice", payload={
        "kind": "Approval", "title": "Checkout", "body": "Checkout fixture", "fields": [],
        "action": {"type": "grant_workspace_consent", "connection_id": connection,
                   "repo": "owner/repo", "consents": ["workspace_checkout"]}})


def test_actual_consent_capture_and_answer_with_broker_metadata(removal):
    from tinyassets.api.pending_requests import answer_request
    from tinyassets.storage.effector_consents import list_consents
    with identity_context(Identity(user_id="alice", username="alice", capabilities=["write"])):
        request = ask(removal.connection)
        assert request.get("status") == "pending", request
        result = answer_request(universe_id="cc-alice", payload={
            "request_id": request["request_id"], "values": {}})
        assert result.get("status") == "answered", result
    consents = list_consents(removal.root / "cc-alice", sink="workspace")
    assert len(consents) == 1 and consents[0]["granted_by"] == "alice"
    assert "models.example.com/owner/repo" in consents[0]["destination"]
    assert not (removal.root / "outbound.db").exists()


@pytest.mark.parametrize("change", ["owner", "host", "revoked"])
def test_consent_answer_rechecks_current_metadata(removal, change):
    from tinyassets.api.pending_requests import answer_request
    from tinyassets.storage.effector_consents import list_consents
    with identity_context(Identity(user_id="alice", username="alice", capabilities=["write"])):
        request = ask(removal.connection)
        assert request.get("status") == "pending", request
        with removal.ledger._connect() as conn:
            statement = {
                "owner": "UPDATE outbound_connections SET owner_user_id='bob'",
                "host": "UPDATE outbound_connections SET git_host='other.example.com'",
                "revoked": "UPDATE outbound_connections SET revoked_at='revoked'",
            }[change]
            conn.execute(statement)
        result = answer_request(universe_id="cc-alice", payload={
            "request_id": request["request_id"], "values": {}})
        assert result.get("error") == (
            "connection_conflict" if change == "host" else "not_found"), result
    assert list_consents(removal.root / "cc-alice", sink="workspace") == []


def test_owner_metadata_is_redacted_and_foreign_or_revoked_is_absent(removal):
    scope = dict(principal="alice", command_center="cc-alice", connection_id=removal.connection)
    result = view(removal.root, **scope)
    assert "credential_ref" not in result.as_dict() and "vault://" not in str(result)
    assert view(removal.root, **(scope | {"principal": "bob"})) is None
    removal.ledger.revoke_connection(removal.connection)
    assert view(removal.root, **scope) is None


def test_package_owner_names_page_without_requiring_grants(removal):
    from tinyassets.api.package_requests import _connections_you_have
    for i in range(70):
        removal.ledger.create_connection(
            connection_id=f"owner-name-{i:03d}", owner_user_id="alice", connection_class="http",
            connection_type="http", auth_scheme="bearer", scopes=("GET",), provider="http",
            destination=f"owner-name-{i}", credential_ref=f"vault://http/owner-name-{i}",
            allowed_endpoints=[{"host": "models.example.com", "path_template": "/",
                                "methods": ["GET"]}])
    result = _connections_you_have("alice", "cc-alice")
    assert all(f"owner-name-{i}" in result for i in range(70))
    assert not any(name.startswith("owner-name-") for name in names(
        removal.root, principal="bob", command_center="cc-bob"))
    assert not (removal.root / "outbound.db").exists()


def test_outage_returns_unknown_preview_and_no_consent_or_fallback(removal, monkeypatch):
    from tinyassets.api.package_requests import _connections_you_have
    from tinyassets.broker import supervisor
    from tinyassets.storage.effector_consents import list_consents
    from tinyassets.storage.outbound_connections import ProxyRequestError
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    assert _connections_you_have("alice", "cc-alice") is None
    with identity_context(Identity(user_id="alice", username="alice", capabilities=["write"])):
        with pytest.raises(ProxyRequestError):
            ask(removal.connection)
    assert list_consents(removal.root / "cc-alice", sink="workspace") == []
    assert not (removal.root / "outbound.db").exists()
