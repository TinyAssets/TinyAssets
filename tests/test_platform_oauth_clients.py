"""Registered clients remain provider data and keep credentials in the daemon."""
# ruff: noqa: F811 -- imported pytest fixtures are dependencies of this module
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit

import pytest

from tests.test_generic_oauth_connections import (
    API,
    AUTH,
    CHALLENGE,
    OTHER,
    OTHER_UID,
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
from tinyassets.connection_oauth import directory, discovery, service, tokens
from tinyassets.connection_oauth.transport import OAuthError

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


def test_directory_precedes_discovery_and_request_client_id(configured, monkeypatch):
    monkeypatch.setattr(discovery, "_metadata_for", lambda *_: pytest.fail("discovery ran"))
    offer, reason = discovery.resolve_offer({"client_id": "agent-id"}, [API])
    assert not reason and offer["source"] == "directory"
    assert offer["client_id"] == CLIENT and offer["scopes"] == ["tasks.write"]
    assert offer["provider_id"] == "unforeseen"
    assert SECRET not in json.dumps(offer) and SECRET_NAME not in json.dumps(offer)


@pytest.mark.parametrize("host", [
    "googleapis.com", "www.googleapis.com", "calendar.googleapis.com",
    "regional.googleapis.com",
])
def test_google_api_connect_ask_uses_directory_scopes(monkeypatch, universes, host):
    monkeypatch.delenv(directory.CONFIG_ENV, raising=False)
    monkeypatch.setenv("TINYASSETS_OAUTH_GOOGLE_CLIENT_ID", "google-test-client")
    monkeypatch.setenv("TINYASSETS_OAUTH_GOOGLE_CLIENT_SECRET", "google-test-secret")
    monkeypatch.setattr(discovery, "_metadata_for", lambda *_: pytest.fail("discovery ran"))
    scopes = ["https://www.googleapis.com/auth/calendar.readonly"]
    with _as(OWNER):
        asked = _ask(fields=[], action={
            **TASKS_ASK, "destination": "google-calendar", "host": host,
            "path_template": "/calendar/v3/calendars/primary/events", "methods": ["GET"],
            "oauth": {"scopes": scopes},
        })
    assert asked["primary"] == "sign_in"
    offer = asked["action"]["oauth"]
    assert offer["source"] == "directory" and offer["provider_id"] == "google"
    assert offer["issuer"] == "https://accounts.google.com"
    assert offer["scopes"] == scopes
    assert asked["fields"] == []


def test_uncovered_api_host_still_discovers(configured):
    row, config, _ = configured
    row["hosts"] = ["unrelated.example.com"]
    row["host_uses"] = {}
    config.write_text(json.dumps({"providers": [row]}), encoding="utf-8")
    offer, reason = discovery.resolve_offer({"scopes": ["tasks.write"]}, [API])
    assert not reason and offer["source"] == "discovered"
    assert offer["scopes"] == ["tasks.write"]


@pytest.mark.parametrize("hosts,covered", [
    (["api.service.example"], True),
    (["nested.api.service.example"], True),
    (["API.SERVICE.EXAMPLE"], True),
    (["service.example"], False),
    (["evilservice.example"], False),
    (["api.service.example.evil.com"], False),
    (["www.googleapis.com"], False),
    (["api.service.example", "evil.example.com"], False),
])
def test_other_directory_row_covers_only_declared_patterns(configured, hosts, covered):
    row, config, _ = configured
    row["hosts"] = ["*.service.example"]
    row["host_uses"] = {}
    config.write_text(json.dumps({"providers": [row]}), encoding="utf-8")
    offer = directory.resolve({"scopes": ["tasks.write"]}, hosts)
    if covered:
        assert offer["provider_id"] == "unforeseen"
        assert offer["scopes"] == ["tasks.write"]
    else:
        assert offer is None


@pytest.mark.parametrize("pattern", ["*", "*.com", "api.*.example.com", "*example.com"])
def test_directory_rejects_non_dns_wildcard_patterns(configured, pattern):
    row, config, _ = configured
    row["hosts"] = [pattern]
    row["host_uses"] = {}
    config.write_text(json.dumps({"providers": [row]}), encoding="utf-8")
    with pytest.raises(OAuthError, match="^oauth_directory_invalid$"):
        directory.entries()


@pytest.mark.parametrize("unset", [SECRET_NAME, CLIENT_NAME])
def test_missing_configuration_falls_back_to_discovery(configured, monkeypatch, unset):
    monkeypatch.delenv(unset)
    offer, reason = discovery.resolve_offer({}, [API])
    assert not reason and offer["source"] == "discovered"


def test_missing_configuration_retains_key_paste(configured, monkeypatch, provider, universes):
    monkeypatch.delenv(SECRET_NAME)
    provider.advertise = False
    with _as(OWNER):
        result = _ask()
    assert result.get("oauth_unavailable")


def test_directory_matches_all_hosts_exactly_and_selects_scope_sets(configured):
    assert directory.resolve({}, ["evil." + API]) is None
    assert directory.resolve({}, [API, "evil.example.com"]) is None
    offer = directory.resolve({"use": "tasks"}, [API])
    assert offer["scopes"] == ["tasks.write"]
    assert directory.resolve({"use": "unknown"}, [API]) is None
    assert directory.resolve({"scopes": ["tasks.write"]}, [API])["scopes"] == ["tasks.write"]
    assert directory.resolve({"scopes": ["tasks.read"]}, [API]) is None


@pytest.mark.parametrize("field,value", [
    ("client_secret_env", "WORKOS_API_KEY"),
    ("client_id_env", SECRET_NAME),
    ("token_endpoint", "http://example.com/token"),
    ("extra_auth_params", {"state": "overridden"}),
    ("extra_auth_params", {"client_secret": SECRET}),
])
def test_invalid_directory_is_fixed_error_without_contents(configured, field, value):
    row, config, _ = configured
    row[field] = value
    config.write_text(json.dumps({"providers": [row]}), encoding="utf-8")
    with pytest.raises(OAuthError) as caught:
        directory.entries()
    assert str(caught.value) == "oauth_directory_invalid"
    assert SECRET not in str(caught.value.__dict__)


def test_directory_override_cannot_depend_on_working_directory(configured, monkeypatch):
    _, config, _ = configured
    monkeypatch.chdir(config.parent)
    monkeypatch.setenv(directory.CONFIG_ENV, config.name)
    with pytest.raises(OAuthError, match="^oauth_directory_invalid$"):
        directory.entries()


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


def test_transport_exception_does_not_escape(configured, monkeypatch, caplog):
    from tinyassets.storage import outbound_connections as oc

    def fail(**_):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(oc, "_SsrfHardenedHttpDriver", lambda **_: fail)
    with pytest.raises(OAuthError) as caught:
        tokens.refresh(tokens.TokenBundle("access", f"https://{TOKEN}/token", CLIENT,
                                          refresh_token="refresh", provider_id="unforeseen"))
    assert caught.value.code == "oauth_server_unreachable"
    assert SECRET not in str(caught.value.__dict__) + caplog.text


@pytest.mark.parametrize("change", [{"token_url": "https://evil.example.com/token"},
                                     {"client_id": "other-client"}, {"provider_id": "absent"}])
def test_refresh_cannot_redirect_the_platform_secret(configured, monkeypatch, change):
    bundle = tokens.TokenBundle("a", f"https://{TOKEN}/token", CLIENT,
                                refresh_token="r", provider_id="unforeseen")
    monkeypatch.setattr(tokens, "request_json", lambda *_a, **_k: pytest.fail("network reached"))
    with pytest.raises(OAuthError, match="platform_client_unavailable"):
        tokens.refresh(replace(bundle, **change))


def test_scoped_daemon_refresh_works_without_secret_in_child(configured, provider, app):
    with _as(OWNER):
        ask = _ask()
    _, _, done = _sign_in(provider, ask["request_id"])
    assert done.status_code == 200
    before = _vault_bundle(app)
    config = service.client_config(app / UID, OWNER)
    assert SECRET_NAME not in os.environ  # removed even before multiprocessing spawn
    from tinyassets.platform_secrets import child_env

    env = child_env(os.environ)
    env[service.ENV] = json.dumps(config)
    script = """
import json, os
from tinyassets.connection_oauth.discovery import resolve_offer
from tinyassets.connection_oauth.tokens import ConnectionTokens, decode
from tinyassets.connection_oauth import directory
assert not directory._secrets
assert not any(n.endswith('_SECRET') and n.startswith('TINYASSETS_OAUTH_') for n in os.environ)
offer, reason = resolve_offer({}, ['api.tasklark.io'])
assert not reason and offer['source'] == 'directory'
t = ConnectionTokens(universe_dir=os.environ['TEST_UNIVERSE'], owner_user_id='owner-1')
old = t._read('tasklark')
new = t.current('tasklark', old, rejected=decode(old).access_token)
assert new.access_token != decode(old).access_token
print('ok')
"""
    env["TEST_UNIVERSE"] = str(app / UID)
    child = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                           text=True, timeout=30)
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == "ok"
    assert SECRET not in child.stdout + child.stderr
    assert _vault_bundle(app).access_token != before.access_token


def test_daemon_capability_cannot_refresh_another_owners_or_connections_tokens(
    configured, provider, app,
):
    for owner, uid in [(OWNER, UID), (OTHER, OTHER_UID)]:
        with _as(owner):
            ask = _ask(uid=uid)
        assert _sign_in(provider, ask["request_id"], owner=owner)[2].status_code == 200
    original = _vault_bundle(app)
    foreign = _vault_bundle(app, OTHER_UID)
    config = service.client_config(app / UID, OWNER)
    result = service.call(config, {"op": "refresh", "destination": "tasklark",
                                   "rejected": original.access_token,
                                   "owner": OTHER, "universe": OTHER_UID})
    assert result == {"ok": True}
    assert _vault_bundle(app).access_token != original.access_token
    assert _vault_bundle(app, OTHER_UID) == foreign
    with pytest.raises(OAuthError):
        service.call(config, {"op": "refresh", "destination": "different-connection"})
    wrong_owner = service.client_config(app / OTHER_UID, OWNER)
    with pytest.raises(OAuthError):
        service.call(wrong_owner, {"op": "refresh", "destination": "tasklark"})
    with pytest.raises(OAuthError):
        service.call({**config, "token": "wrong"}, {"op": "refresh", "destination": "tasklark"})


def test_two_connections_of_one_owner_keep_distinct_tokens(configured, provider, app):
    from tinyassets.credential_vault import load_credential_vault

    for destination in ("tasklark", "second"):
        with _as(OWNER):
            ask = _ask(action={**TASKS_ASK, "destination": destination})
        assert _sign_in(provider, ask["request_id"])[2].status_code == 200
    before = {r["destination"]: r["token"] for r in load_credential_vault(app / UID)}
    config = service.client_config(app / UID, OWNER)
    service.call(config, {"op": "refresh", "destination": "second",
                          "rejected": tokens.decode(before["second"]).access_token})
    after = {r["destination"]: r["token"] for r in load_credential_vault(app / UID)}
    assert before["tasklark"] == after["tasklark"]
    assert before["second"] != after["second"]


def test_future_secret_names_are_filtered_without_a_python_patch(monkeypatch):
    from tinyassets.platform_secrets import child_env

    assert child_env({SECRET_NAME: SECRET, CLIENT_NAME: CLIENT,
                      service.ENV: "owner capability", "KEEP": "yes"}) == {"KEEP": "yes"}
    from tinyassets.node_sandbox import BwrapLauncher, PlainSubprocessLauncher

    monkeypatch.setenv(SECRET_NAME, SECRET)
    monkeypatch.setenv(service.ENV, "owner capability")
    for launcher in (BwrapLauncher(), PlainSubprocessLauncher()):
        env = launcher.env("/tmp/private-home")
        assert SECRET_NAME not in env and service.ENV not in env
        assert SECRET not in str(env)


def test_concurrent_daemon_refresh_spends_once(configured, provider, app):
    with _as(OWNER):
        ask = _ask()
    assert _sign_in(provider, ask["request_id"])[2].status_code == 200
    original = _vault_bundle(app)
    provider.refresh_delay = 0.1
    with ThreadPoolExecutor(max_workers=4) as pool:
        configs = list(pool.map(lambda _: service.client_config(app / UID, OWNER), range(4)))
        replies = list(pool.map(lambda config: service.call(config, {
            "op": "refresh", "destination": "tasklark", "rejected": original.access_token,
        }), configs))
    assert all(reply == {"ok": True} for reply in replies)
    assert provider.refresh_calls == 1 and provider.reused == 0


def test_packaged_example_is_inactive_until_both_env_names_are_set(monkeypatch):
    monkeypatch.delenv(directory.CONFIG_ENV, raising=False)
    monkeypatch.setattr(directory, "_secrets", {})
    row = directory.entries()[0]
    monkeypatch.delenv(row["client_id_env"], raising=False)
    monkeypatch.delenv(row["client_secret_env"], raising=False)
    assert directory.resolve({}, ["gmail.googleapis.com"]) is None
    monkeypatch.setenv(row["client_id_env"], CLIENT)
    assert directory.resolve({}, ["gmail.googleapis.com"]) is None
    monkeypatch.setenv(row["client_secret_env"], SECRET)
    assert (directory.resolve({}, ["gmail.googleapis.com"])["scopes"]
            == row["default_scopes"]["gmail"])
    assert (directory.resolve({"use": "calendar"}, ["www.googleapis.com"])["scopes"]
            == row["default_scopes"]["calendar"])
    assert directory.resolve({"scopes": ["https://mail.google.com/"]},
                             ["www.googleapis.com"]) is None


@pytest.mark.parametrize("failure", ["missing", "invalid", "rpc_timeout", "acl", "hosts"])
@pytest.mark.parametrize("advertise", [True, False])
def test_unavailable_directory_preserves_discovery_and_key_paste(
    configured, app, monkeypatch, provider, caplog, failure, advertise,
):
    row, path, _ = configured
    hosts = [API]
    if failure == "missing":
        monkeypatch.setenv(directory.CONFIG_ENV, str(path.with_name("absent.json")))
    elif failure == "invalid":
        path.write_text(SECRET, encoding="utf-8")
    else:
        owner = OTHER if failure == "acl" else OWNER
        config = service.client_config(app / UID, owner)
        monkeypatch.setenv(service.ENV, json.dumps(config))
        if failure == "hosts":
            hosts *= 9
        if failure == "rpc_timeout":
            def timeout(*_args, **_kwargs):
                raise TimeoutError(SECRET)
            monkeypatch.setattr(service.http.client, "HTTPConnection", timeout)
    provider.advertise = advertise
    offer, reason = discovery.resolve_offer({}, hosts)
    if advertise:
        assert not reason and offer["source"] == "discovered"
    else:
        assert offer is None and reason
    assert "Optional OAuth directory unavailable" in caplog.text
    assert SECRET not in caplog.text


def test_unexpected_directory_errors_are_not_hidden(configured, monkeypatch):
    def fail(*_args):
        raise OAuthError("unexpected_failure")
    monkeypatch.setattr(directory, "resolve", fail)
    with pytest.raises(OAuthError, match="unexpected_failure"):
        discovery.resolve_offer({}, [API])


def test_invalid_directory_does_not_block_engine_restart(configured, app, monkeypatch):
    from tinyassets.engine_mcp_http import _EngineServer

    configured[1].write_text(SECRET, encoding="utf-8")
    directory.prepare_children()
    before = set(service._bindings)
    class Thread:
        def __init__(self, **kwargs):
            self.live = False
        def start(self):
            self.live = True
        def is_alive(self):
            return self.live
        def join(self, timeout):
            self.live = False
    monkeypatch.setattr("tinyassets.engine_mcp_http.threading.Thread", Thread)
    engine = _EngineServer(UID, OWNER, 8790, str(app))
    assert engine.start()
    first = engine.endpoint
    engine.stop()
    assert first.module.__name__ not in sys.modules
    assert engine.start()
    assert engine.endpoint is not first
    second = engine.endpoint
    assert second.module.__name__ in sys.modules
    engine.stop()
    assert second.module.__name__ not in sys.modules
    assert set(service._bindings) == before
    assert SECRET_NAME not in os.environ and directory.secret(SECRET_NAME) == SECRET


def test_invalid_directory_does_not_block_shared_broker_channel(configured, tmp_path):
    from tests.test_outbound_connection_ledger import _grant_github_connection
    from tinyassets.storage.outbound_connections import ConnectionLedger

    configured[1].write_text("invalid", encoding="utf-8")
    before = set(service._bindings)
    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path,
                              verify_authenticated_principal=lambda: "user-1")
    _grant_github_connection(ledger)
    proxy = ledger.resolve_exact_scoped_proxy(
        universe_id="universe-1", grant_id="grant-github", connection_id="conn-github")
    proxy.close()
    assert set(service._bindings) == before


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


def test_fork_guard_protects_inherited_memory(configured, monkeypatch):
    directory.prepare_children()
    monkeypatch.setattr(directory, "_pid", os.getpid() + 1)
    with pytest.raises(OAuthError, match="platform_client_unavailable"):
        directory.secret(SECRET_NAME)


def test_founder_home_without_acl_can_resolve_and_refresh(configured, provider, app):
    from tinyassets import daemon_server

    with _as(OWNER):
        ask = _ask()
    assert _sign_in(provider, ask["request_id"])[2].status_code == 200
    before = _vault_bundle(app)
    daemon_server.set_founder_home(app, founder_sub=OWNER, universe_id=UID)
    with daemon_server._connect(app) as conn:
        conn.execute("DELETE FROM universe_acl WHERE universe_id = ?", (UID,))
    assert daemon_server.list_universe_acl(app, universe_id=UID) == []
    config = service.client_config(app / UID, OWNER)
    assert service.call(config, {"op": "resolve", "hosts": [API]})["offer"]["source"] == "directory"
    assert service.call(config, {"op": "refresh", "destination": "tasklark",
                                 "rejected": before.access_token}) == {"ok": True}
    assert _vault_bundle(app).access_token != before.access_token
    stranger = service.client_config(app / UID, OTHER)
    with pytest.raises(OAuthError):
        service.call(stranger, {"op": "refresh", "destination": "tasklark"})


def test_released_capability_cannot_refresh_but_other_launch_can(configured, provider, app):
    with _as(OWNER):
        ask = _ask()
    assert _sign_in(provider, ask["request_id"])[2].status_code == 200
    first = service.client_config(app / UID, OWNER)
    second = service.client_config(app / UID, OWNER)
    service.release_client(first)
    with pytest.raises(OAuthError):
        service.call(first, {"op": "refresh", "destination": "tasklark"})
    assert service.call(second, {"op": "refresh", "destination": "tasklark"}) == {"ok": True}


def test_invalid_directory_connect_ask_keeps_key_paste(configured, provider, universes):
    configured[1].write_text("invalid", encoding="utf-8")
    provider.advertise = False
    with _as(OWNER):
        assert _ask().get("oauth_unavailable")


def test_failed_engine_endpoint_mints_no_child_oauth_capability(configured, app, monkeypatch):
    from tinyassets.engine_mcp_http import _EngineServer

    before = set(service._bindings)
    def fail(*_args, **_kwargs):
        raise OSError("endpoint failed")
    monkeypatch.setattr("tinyassets.engine_endpoint.EngineEndpoint", fail)
    with pytest.raises(OSError, match="endpoint failed"):
        _EngineServer(UID, OWNER, 8790, str(app)).start()
    assert set(service._bindings) == before


def test_unavailable_broker_mints_no_child_oauth_capability(configured, tmp_path, monkeypatch):
    from tests.test_outbound_connection_ledger import _grant_github_connection
    from tinyassets.broker import supervisor
    from tinyassets.storage.outbound_connections import ConnectionLedger, ProxyRequestError

    before = set(service._bindings)
    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path,
                              verify_authenticated_principal=lambda: "user-1")
    _grant_github_connection(ledger)
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    with pytest.raises(ProxyRequestError, match="credential broker is not running"):
        ledger.resolve_exact_scoped_proxy(
            universe_id="universe-1", grant_id="grant-github", connection_id="conn-github")
    assert set(service._bindings) == before
