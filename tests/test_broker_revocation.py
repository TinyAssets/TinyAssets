"""RFC 7009 is broker-local, fenced, and never prevents local custody erasure."""
import json
from urllib.parse import parse_qs

import pytest

from tests.support.broker_ipc import broker, discovery  # noqa: F401
from tests.test_broker_disconnect import operation, removal  # noqa: F401
from tinyassets.connection_oauth.tokens import TokenBundle, encode


@pytest.fixture
def oauth(removal, monkeypatch, tmp_path):  # noqa: F811
    from tinyassets.broker.vault import write_credential_vault
    from tinyassets.credential_vault import http_credential_record
    from tinyassets.storage import outbound_connections as oc

    entry = {"id": "fixture", "hosts": ["service.example.com"],
             "authorization_endpoint": "https://service.example.com/authorize",
             "token_endpoint": "https://service.example.com/token",
             "revocation_endpoint": "https://service.example.com/revoke",
             "client_id_env": "TINYASSETS_OAUTH_FIXTURE_ID",
             "client_secret_env": "TINYASSETS_OAUTH_FIXTURE_SECRET",
             "token_endpoint_auth_method": "client_secret_post"}
    path = tmp_path / "directory.json"
    path.write_text(json.dumps({"providers": [entry]}))
    monkeypatch.setenv("TINYASSETS_OAUTH_DIRECTORY", str(path))
    monkeypatch.setenv("TINYASSETS_OAUTH_FIXTURE_ID", "public-client")
    monkeypatch.setenv("TINYASSETS_OAUTH_FIXTURE_SECRET", "registered-secret")
    calls = []
    reply = {"status": 200, "body": ""}

    def driver(**kwargs):
        assert removal.ledger._get_connection_resource(removal.connection).revoked_at is not None
        calls.append(kwargs)
        if reply.get("raise"):
            raise OSError("transport echoed refresh-value registered-secret")
        return reply

    monkeypatch.setattr(oc, "_SsrfHardenedHttpDriver", lambda **kwargs: driver)

    def deposit(*, registered=False, refresh=True):
        bundle = TokenBundle(access_token="access-value", refresh_token="refresh-value" if refresh
                             else "", token_url=entry["token_endpoint"], client_id="public-client",
                             provider_id="fixture" if registered else "")
        write_credential_vault(removal.root / "cc-alice",
            [http_credential_record(destination="fixture", token=encode(bundle))],
            owner_user_id="alice", universe_id="cc-alice")
        with removal.ledger._connect() as db:
            db.execute("UPDATE outbound_connections SET auth_scheme='oauth2' WHERE connection_id=?",
                       (removal.connection,))

    return removal, deposit, calls, reply, path, entry


@pytest.mark.parametrize("registered", [False, True], ids=["public", "client_secret_post"])
@pytest.mark.parametrize("refresh", [True, False], ids=["refresh_token", "access_token"])
def test_disconnect_revokes_current_token_then_erases(oauth, registered, refresh):
    from tinyassets.credential_vault import load_credential_vault

    rig, deposit, calls, _, _, _ = oauth
    deposit(registered=registered, refresh=refresh)
    result = rig.remove()
    assert result["connection_removed"] and result["secrets_removed"] == 1
    assert len(calls) == 1
    assert calls[0]["url"] == "https://service.example.com/revoke"
    assert calls[0]["auth_scheme"] == "none"
    form = parse_qs(calls[0]["body"])
    assert form == {"token": ["refresh-value" if refresh else "access-value"],
                    "token_type_hint": ["refresh_token" if refresh else "access_token"],
                    **({"client_id": ["public-client"], "client_secret": ["registered-secret"]}
                       if registered else {})}
    assert load_credential_vault(rig.root / "cc-alice") == []
    assert rig.ledger.get_connection(rig.connection) is None
    from tinyassets.storage.outbound_connections import GrantResolutionError, _broker_channel

    with pytest.raises(GrantResolutionError):
        _broker_channel(rig.root, principal="alice", command_center="cc-alice",
                        grant_id=rig.grant, connection_id=rig.connection).request(
                            "GET", {"url": "https://models.example.com/catalogue"})
    rig.remove()
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["http", "network", "directory"])
@pytest.mark.parametrize("surface", ["box", "door"])
def test_revocation_failure_is_recorded_without_secrets_and_local_erase_wins(
        oauth, failure, surface, monkeypatch):
    from tinyassets.broker.http_connect import connect_operation
    from tinyassets.credential_vault import load_credential_vault

    rig, deposit, calls, reply, path, _ = oauth
    deposit(registered=True)
    if failure == "http":
        reply.update(status=503, body='{"error_description":"refresh-value registered-secret"}')
    elif failure == "network":
        reply["raise"] = True
    else:
        path.write_text("invalid")
    remove = rig.remove if surface == "box" else lambda: _door_remove(rig, monkeypatch)
    result = remove()
    assert result["status"] == "removed_locally_revoke_failed"
    assert result["recovery"] == "revoke_at_provider"
    assert "provider-side revocation failed" in result["message"]
    assert "revoke it at fixture (https://service.example.com)" in result["message"]
    assert "cannot retry" in result["message"]
    assert result["connection_removed"] and result["secrets_removed"] == 1
    assert load_credential_vault(rig.root / "cc-alice") == []
    assert rig.ledger.get_grant(rig.grant) is None
    with rig.ledger._connect() as db:
        row = dict(db.execute("SELECT * FROM outbound_connections WHERE connection_id=?",
                              (rig.connection,)).fetchone())
    assert row["revocation_error"] == "provider_revocation_failed"
    assert row["custody_erased"] == 1 and row["revoked_at"]
    assert "refresh-value" not in json.dumps(row) and "registered-secret" not in json.dumps(row)
    assert operation(rig)["resource"] is None
    count = len(calls)
    repeated = remove()
    assert repeated["connection_removed"] is False
    assert repeated["status"] == "removed_locally_revoke_failed"
    assert repeated["message"] == result["message"]
    assert len(calls) == count
    args = dict(principal="alice", command_center="cc-alice", destination="fixture",
                policy={"auth_scheme": "bearer", "scopes": ["GET"], "git_host": "",
                        "access_mode": "exact", "endpoints": [{"host": "models.example.com",
                        "path_template": "/catalogue", "methods": ["GET"]}]})
    prepared = connect_operation(rig.root, **args, action="prepare")
    restored = connect_operation(rig.root, **args, action="commit", secret="new-credential",
                                 expected=prepared["revision"])
    assert restored["projection"]["status"] == "provisioned"
    assert rig.ledger.incarnation(rig.connection) != row["incarnation"]


def test_directory_without_revocation_endpoint_only_erases(oauth):
    rig, deposit, calls, _, path, entry = oauth
    deposit(registered=True)
    del entry["revocation_endpoint"]
    path.write_text(json.dumps({"providers": [entry]}))
    assert rig.remove()["secrets_removed"] == 1
    assert calls == []


def test_stale_or_unfenced_disconnect_never_calls_provider(oauth):
    from tinyassets.storage.outbound_connections import GrantResolutionError

    rig, deposit, calls, _, _, _ = oauth
    deposit()
    for incarnation in ("stale", rig.incarnation):
        with pytest.raises(GrantResolutionError):
            operation(rig, action="erase", incarnation=incarnation)
    assert calls == []


def _door_remove(rig, monkeypatch):
    import asyncio

    from starlette.requests import Request

    from tinyassets import onboarding
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.onboarding.connections import handle_connections

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://tinyassets.io/mcp"})
    raw = json.dumps({"universe_id": "cc-alice", "destination": "fixture",
                      "incarnation": rig.incarnation}).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    request = Request({"type": "http", "method": "POST", "path": "/app/connections",
        "query_string": b"", "headers": [(b"origin", b"https://tinyassets.io"),
        (b"content-type", b"application/json"), (b"host", b"tinyassets.io")]}, receive)
    with identity_context(Identity(user_id="alice", username="alice", capabilities=["write"])):
        response = asyncio.run(handle_connections(request))
    assert response.status_code == 200, response.body
    return json.loads(response.body)
