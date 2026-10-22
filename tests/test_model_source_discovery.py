"""An unfamiliar local OAuth/model server through the real ingress and broker."""
# ruff: noqa: F811 -- imported pytest fixtures
import time
from urllib.parse import parse_qsl, urlsplit

import pytest

from tests.test_generic_oauth_connections import (
    API,
    AUTH,
    CHALLENGE,
    OWNER,
    UID,
    VERIFIER,
    FakeProvider,
    _as,
    _broker,
    _post,
    app,  # noqa: F401 - shared fixtures
    universes,  # noqa: F401 - pytest fixture
)


class ModelProvider(FakeProvider):
    model_api = True
    advertised = False
    changed_host = False

    def route(self, method, host, path, headers, body):
        if host == API and path == "/v1/models" and self.model_api:
            return 200, {"data": [{"id": "test-chat"}]}
        if host == API and path == "/v1/chat/completions":
            token = headers.get("Authorization", "").removeprefix("Bearer ")
            self.api_calls.append(token)
            if self.access.get(token, 0) <= time.time():
                return 401, {"error": "invalid_token"}
            return 200, {"choices": [{"message": {"role": "assistant", "content": "Hello"}}]}
        status, doc = super().route(method, host, path, headers, body)
        if host == AUTH and path == "/.well-known/oauth-authorization-server" and self.changed_host:
            doc["token_endpoint"] = "https://changed.example/token"
        if host == API and path == "/.well-known/oauth-protected-resource" and self.advertised:
            doc.update(models_endpoint=f"https://{API}/v1/models",
                       chat_completions_endpoint=f"https://{API}/v1/chat/completions")
        return status, doc


@pytest.fixture
def provider(monkeypatch):
    from tests.test_authenticated_external_call_effector import _install_loopback_driver
    from tinyassets.connection_oauth import discovery

    fake = ModelProvider()
    _install_loopback_driver(monkeypatch, fake.port)
    monkeypatch.setattr(discovery, "DISCOVERY_ENABLED", True)
    yield fake
    fake.stop()


def begin(address=API):
    from tests.owner_answer import session_cookie

    return _post("discover", {"address": address, "code_challenge": CHALLENGE},
                 cookie=session_cookie())


def test_connect_and_model_call(provider, app):
    with _as(OWNER):
        begun = begin()
        assert begun.status_code == 200, begun.text
        query = dict(parse_qsl(urlsplit(provider.authorize(begun.json()["authorize_url"])).query))
        # Changing discovery mid-flight cannot redirect the authorization code.
        provider.changed_host = True
        done = _post("oauth_exchange", {"flow": query["state"], "code": query["code"],
                                       "code_verifier": VERIFIER})
        assert done.status_code == 200, done.text
        result = done.json()
        assert result["status"] == "provisioned"
        assert result["model_confirmation"]["grant_id"] == result["grant_id"]
        assert "access_token" not in done.text
        dispatch = _broker(app, UID, OWNER, result["grant_id"], None)
        response = dispatch(result["grant_id"], "POST", {
            "url": f"https://{API}/v1/chat/completions",
            "body": {"model": "test-chat", "messages": [{"role": "user", "content": "Hi"}]}})
        assert response["status"] == 200
        assert provider.api_calls[-1] in provider.access
        from tinyassets.onboarding.source_connect import offer_connection_access
        from tinyassets.providers.definition import list_definitions

        confirmation = offer_connection_access(base=app, uid=UID, owner=OWNER,
                                                grant_id=result["grant_id"])
        assert confirmation["status"] == "confirmation_required"
        definitions = [d for d in list_definitions(UID) if d.ref == result["grant_id"]]
        assert len(definitions) == 1
        assert definitions[0].access_method == "api_key_http"
        assert "cannot measure" in confirmation["request"]["body"]
        assert len(provider.clients) == 1
        assert begin().status_code == 200
        assert len(provider.clients) == 1


def test_no_oauth_offers_discovered_key(provider, app):
    provider.advertise = False
    with _as(OWNER):
        result = begin()
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "api_key_required"
    assert result.json()["endpoint"] == f"https://{API}/v1/chat/completions"
    # The same connect path continues: deposit the key, then a model call.
    key = "sk-synthetic-" + "k" * 24
    provider.access[key] = time.time() + 3600
    with _as(OWNER):
        from tests.owner_answer import session_cookie

        saved = _post("deposit", {"definition": result.json()["definition"], "secret": key},
                      cookie=session_cookie())
    assert saved.status_code == 200, saved.text
    assert key not in saved.text
    grant = saved.json()["grant_id"]
    response = _broker(app, UID, OWNER, grant, None)(grant, "POST", {
        "url": f"https://{API}/v1/chat/completions",
        "body": {"model": "test-chat", "messages": [{"role": "user", "content": "Hi"}]}})
    assert response["status"] == 200
    assert provider.api_calls[-1] == key


def test_no_model_api_does_not_guess(provider, app):
    provider.model_api = False
    with _as(OWNER):
        result = begin()
    assert result.status_code == 409
    assert result.json()["error"] == "no_model_api_found"


def test_https_required(provider, app):
    with _as(OWNER):
        result = begin("http://" + API)
    assert result.status_code == 409
    assert result.json()["error"] == "invalid_oauth_url"
    assert not provider.clients


def test_advertised_private_catalogue(provider, app):
    provider.model_api = False
    provider.advertised = True
    with _as(OWNER):
        result = begin()
    assert result.status_code == 200, result.text
    assert result.json()["authorize_host"] == AUTH


def test_requires_owner_session(provider, app):
    with _as(OWNER):
        result = _post("discover", {"address": API, "code_challenge": CHALLENGE}, cookie="")
    assert result.status_code == 403


def test_changed_issuer_refused(provider, app):
    with _as(OWNER):
        begun = begin()
        query = dict(parse_qsl(urlsplit(provider.authorize(begun.json()["authorize_url"])).query))
        done = _post("oauth_exchange", {"flow": query["state"], "code": query["code"],
            "code_verifier": VERIFIER, "iss": "https://changed.example"})
    assert done.status_code == 409
    assert done.json()["error"] == "issuer_mismatch"
    assert not provider.access


def test_token_redirect_is_not_followed(provider, app):
    original = provider.route
    contacted = []

    def redirect(method, host, path, headers, body):
        contacted.append(host)
        if path == "/token":
            provider.response_headers = {"Location": "https://changed.example/token"}
            return 302, {}
        return original(method, host, path, headers, body)

    with _as(OWNER):
        begun = begin()
        query = dict(parse_qsl(urlsplit(provider.authorize(begun.json()["authorize_url"])).query))
        provider.route = redirect
        done = _post("oauth_exchange", {"flow": query["state"], "code": query["code"],
                                       "code_verifier": VERIFIER})
    assert done.status_code == 502
    assert "changed.example" not in contacted
    assert not provider.access


def test_advertised_api_rejects_wildcards_and_split_hosts():
    from tinyassets.connection_oauth.model_discovery import definition
    from tinyassets.connection_oauth.transport import OAuthError

    with pytest.raises(OAuthError):
        definition("https://api.example/models", "https://api.example/*", "https://api.example")
    with pytest.raises(OAuthError, match="model_api_hosts_unsupported"):
        definition("https://catalogue.example/models", "https://api.example/chat",
                   "https://api.example")
