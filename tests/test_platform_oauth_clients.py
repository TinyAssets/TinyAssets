"""Registered clients remain provider data and keep credentials in the daemon."""
# ruff: noqa: F811 -- imported pytest fixtures are dependencies of this module
from __future__ import annotations

import base64
import json
import os
from dataclasses import replace
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit

import pytest

from tests.test_generic_oauth_connections import (
    API,
    AUTH,
    CHALLENGE,
    OWNER,
    REDIRECT,
    TASKS_ASK,
    TOKEN,
    UID,
    VERIFIER,
    _as,
    _ask,
    _broker,
    _call,
    _sign_in,
    _vault_bundle,
    app,  # noqa: F401
    provider,  # noqa: F401
    universes,  # noqa: F401
)
from tinyassets.connection_oauth import directory, tokens
from tinyassets.connection_oauth.transport import OAuthError
from tinyassets.credential_vault import custody_namespace

SECRET_NAME = "TINYASSETS_OAUTH_UNFORESEEN_CLIENT_SECRET"
CLIENT_NAME = "TINYASSETS_OAUTH_UNFORESEEN_CLIENT_ID"
SECRET = "private-platform-client+secret/&?"
CLIENT = "registered-client"


@pytest.fixture
def configured(tmp_path, monkeypatch, provider):
    monkeypatch.setattr(directory, "_secrets", {})
    row = {
        "id": "unforeseen", "hosts": [API], "issuer": f"https://{AUTH}",
        "authorization_endpoint": f"https://{AUTH}/authorize",
        "token_endpoint": f"https://{TOKEN}/token",
        "client_id_env": CLIENT_NAME, "client_secret_env": SECRET_NAME,
        "token_endpoint_auth_method": "client_secret_post",
        "default_scopes": {"tasks": ["tasks.write"]}, "host_uses": {API: "tasks"},
        "extra_auth_params": {"access_type": "offline", "prompt": "consent"},
    }
    config = tmp_path / "providers.json"
    config.write_text(json.dumps({"providers": [row]}), encoding="utf-8")
    monkeypatch.setenv(directory.CONFIG_ENV, str(config))
    monkeypatch.setenv(CLIENT_NAME, CLIENT)
    monkeypatch.setenv(SECRET_NAME, SECRET)
    provider.clients[CLIENT] = [REDIRECT]
    original = provider.route
    traffic = []

    def route(method, host, path, headers, body):
        traffic.append((method, host, path, headers, body.decode()))
        if host == TOKEN:
            form = dict(parse_qsl(body.decode()))
            if "Authorization" in headers:
                expected = base64.b64encode(
                    f"{quote_plus(CLIENT)}:{quote_plus(SECRET)}".encode()).decode()
                assert headers["Authorization"] == "Basic " + expected
                assert "client_secret" not in form
                form["client_id"] = CLIENT
                body = urlencode(form).encode()
            else:
                assert form["client_secret"] == SECRET
        else:
            assert SECRET not in str((headers, body))
            assert quote_plus(SECRET) not in str((headers, body))
        return original(method, host, path, headers, body)

    monkeypatch.setattr(provider, "route", route)
    return row, config, traffic


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize("method", ["client_secret_post", "client_secret_basic"])
def test_confidential_flow_and_refresh_at_only_token_endpoint(
    configured, provider, app, tmp_path, caplog, method,
):
    row, config, traffic = configured
    row["token_endpoint_auth_method"] = method
    config.write_text(json.dumps({"providers": [row]}), encoding="utf-8")
    with _as(OWNER):
        ask = _ask()
    begun, _, done = _sign_in(provider, ask["request_id"])
    assert done.status_code == 200, done.text
    query = dict(parse_qsl(urlsplit(begun.json()["authorize_url"]).query))
    assert query["prompt"] == "consent" and query["access_type"] == "offline"
    assert query["code_challenge"] == CHALLENGE
    original = _vault_bundle(app)
    assert original.provider_id == "unforeseen"
    assert SECRET not in tokens.encode(original)
    broker = _broker(app, UID, OWNER, done.json()["grant_id"], tmp_path / "runtime")
    provider.revoke_access()
    result = _call(broker, done.json()["grant_id"])
    assert result["status"] == 200
    assert provider.refresh_calls == 1
    assert _vault_bundle(app).access_token != original.access_token
    assert not any(path.endswith("register") or "well-known" in path
                   for _, _, path, _, _ in traffic)
    public = json.dumps([ask, begun.json(), done.json(), result]) + caplog.text
    assert SECRET not in public and quote_plus(SECRET) not in public


@pytest.mark.verifies('owner-daemon-in-box:T6')
@pytest.mark.parametrize("field", [
    "error_description", "token_type", "scope", "access_token", "refresh_token",
])
def test_echoed_secret_never_enters_response_error_or_log(configured, monkeypatch, caplog, field):
    from tinyassets.storage import outbound_connections as oc

    response = {"access_token": "safe-token", field: SECRET}
    status = 400 if field == "error_description" else 200
    monkeypatch.setattr(oc, "_SsrfHardenedHttpDriver", lambda **_: lambda **kw: {
        "status": status, "body": json.dumps(response),
    })
    with pytest.raises(OAuthError) as caught:
        tokens.exchange_code(token_url=f"https://{TOKEN}/token", client_id=CLIENT,
                             provider_id="unforeseen", code="code", verifier=VERIFIER,
                             redirect_uri=REDIRECT)
    assert SECRET not in str(caught.value.__dict__) + str(caught.value) + caplog.text


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize("change", [{"token_url": "https://evil.example.com/token"},
                                     {"client_id": "other-client"}, {"provider_id": "absent"}])
def test_refresh_cannot_redirect_the_platform_secret(configured, monkeypatch, change):
    bundle = tokens.TokenBundle("a", f"https://{TOKEN}/token", CLIENT,
                                refresh_token="r", provider_id="unforeseen")
    monkeypatch.setattr(tokens, "request_json", lambda *_a, **_k: pytest.fail("network reached"))
    with pytest.raises(OAuthError, match="platform_client_unavailable"):
        tokens.refresh(replace(bundle, **change))


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_two_connections_of_one_owner_keep_distinct_tokens(configured, provider, app):
    from tinyassets.credential_vault import load_credential_vault

    for destination in ("tasklark", "second"):
        with _as(OWNER):
            ask = _ask(action={**TASKS_ASK, "destination": destination})
        assert _sign_in(provider, ask["request_id"])[2].status_code == 200
    before = {r["destination"]: r["token"]
              for r in load_credential_vault(custody_namespace(app, OWNER))}
    from tinyassets.broker.tokens import ConnectionTokens

    keeper = ConnectionTokens(universe_dir=custody_namespace(app, OWNER), owner_user_id=OWNER)
    keeper.current("second", before["second"],
                   rejected=tokens.decode(before["second"]).access_token)
    after = {r["destination"]: r["token"]
             for r in load_credential_vault(custody_namespace(app, OWNER))}
    assert before["tasklark"] == after["tasklark"]
    assert before["second"] != after["second"]


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_startup_scrubs_oauth_secrets_before_storage_or_children(configured, monkeypatch):
    from tinyassets import storage_layout, universe_server

    class StartupChecked(Exception):
        pass

    def check():
        assert SECRET_NAME not in os.environ
        assert directory.secret(SECRET_NAME) == SECRET
        raise StartupChecked

    configured[1].write_text("invalid", encoding="utf-8")
    monkeypatch.setattr(storage_layout, "require_layout", check)
    with pytest.raises(StartupChecked):
        universe_server.main()


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_fork_guard_protects_inherited_memory(configured, monkeypatch):
    directory.prepare_children()
    monkeypatch.setattr(directory, "_pid", os.getpid() + 1)
    with pytest.raises(OAuthError, match="platform_client_unavailable"):
        directory.secret(SECRET_NAME)


from tests.support.egress import admitted_egress  # noqa: E402, F401
