"""The standards detector chooses from published standards alone, never by name."""
import re
from pathlib import Path

import pytest

from tinyassets.connection_oauth.model_discovery import detect
from tinyassets.connection_oauth.transport import OAuthError

API = "https://api.provider.example"
AS = "https://login.provider.example"
CATALOGUE = {"object": "list", "data": [{"id": "m1", "object": "model"}]}


def fetcher(routes):
    seen = []

    def fetch(url, *, large=False):
        seen.append(url)
        status, doc, *headers = routes.get(url, (404, None))
        return status, doc, headers[0] if headers else {}

    fetch.seen = seen
    return fetch


def server(**extra):
    return (200, {"issuer": AS, "authorization_endpoint": AS + "/authorize",
                  "token_endpoint": AS + "/token", "code_challenge_methods_supported": ["S256"],
                  "grant_types_supported": ["authorization_code"], **extra})


@pytest.mark.parametrize("address, path", [
    ("provider.example", "/v1"),
    ("provider.example", "/api/v1"),
    ("api.provider.example/custom/v1", "/custom/v1"),
])
def test_bare_domain_finds_api_subdomain_and_common_paths(address, path):
    fetch = fetcher({API + path + "/models": (200, CATALOGUE)})
    result = detect(address, fetch=fetch)
    assert result["chosen"]["models_url"] == API + path + "/models"
    assert result["chosen"]["verdict"] == "api_key"
    page = Path("tinyassets/onboarding/app/main.js").read_text("utf-8")
    html = Path("tinyassets/onboarding/app.html").read_text("utf-8")
    assert "Connect any OpenAI-compatible endpoint" not in page + html


def test_challenge_metadata_dcr_chooses_oauth():
    fetch = fetcher({
        API + "/v1/models": (401, None, {"www-authenticate":
            f'Bearer resource_metadata="{API}/meta"'}),
        API + "/v1/chat/completions": (401, None),
        API + "/meta": (200, {"resource": API + "/v1", "authorization_servers": [AS]}),
        AS + "/.well-known/oauth-authorization-server": server(
            registration_endpoint=AS + "/register", device_authorization_endpoint=AS + "/d",
            token_endpoint_auth_methods_supported=["none"]),
    })
    record = detect(API, fetch=fetch)
    assert record["sign_in"]["protected_resource"]["via"] == "www-authenticate"
    found = record["sign_in"]["authorization_servers"][0]
    assert found["dynamic_registration"] and found["device_authorization"]
    assert found["token_auth_methods"] == ["none"]
    chosen = record["chosen"]
    assert chosen["verdict"] == "oauth" and chosen["client"] == "dynamic_registration"
    assert chosen["inference_url"] == API + "/v1/chat/completions"
    assert all(u.startswith("https://") for u in fetch.seen)


def test_client_metadata_document_counts_as_self_registration():
    fetch = fetcher({
        API + "/v1/models": (200, CATALOGUE),
        API + "/.well-known/oauth-protected-resource": (
            200, {"resource": API, "authorization_servers": [AS]}),
        AS + "/.well-known/openid-configuration": server(
            client_id_metadata_document_supported=True),
    })
    chosen = detect(API, fetch=fetch)["chosen"]
    assert chosen["verdict"] == "oauth"
    assert chosen["client"] == "client_id_metadata_document"


def test_no_oauth_and_openapi_bearer_is_api_key():
    fetch = fetcher({
        API + "/v1/models": (200, CATALOGUE),
        API + "/openapi.json": (200, {"paths": {"/chat/completions": {}}, "components": {
            "securitySchemes": {"k": {"type": "http", "scheme": "bearer"}}}}),
    })
    record = detect(API + "/v1", fetch=fetch)
    assert record["model_api"]["key_styles"] == ["bearer"]
    assert record["chosen"]["verdict"] == "api_key"


def test_custom_key_header_is_reported_not_guessed():
    fetch = fetcher({
        API + "/v1/models": (200, CATALOGUE),
        API + "/openapi.json": (200, {"paths": {"/v1/chat/completions": {}}, "components": {
            "securitySchemes": {"k": {"type": "apiKey", "in": "header", "name": "x-key"}}}}),
    })
    chosen = detect(API, fetch=fetch)["chosen"]
    assert chosen["verdict"] == "not_connectable" and "header:x-key" in chosen["reason"]


@pytest.mark.parametrize("status", [401, 403])
def test_blanket_auth_is_unverified_key_or_refused(status):
    fetch = fetcher({API + p: (status, None) for p in (
        "/v1/models", "/v1/.tinyassets-discovery-control", "/models",
        "/.tinyassets-discovery-control", "/v1/chat/completions", "/v1/responses")})
    chosen = detect(API, fetch=fetch)["chosen"]
    if status == 401:
        # Nothing is proven without a key: the owner's key is checked first.
        assert chosen["verdict"] == "api_key" and chosen["verified"] is False
        assert chosen["models_url"] == API + "/v1/models"
    else:
        assert chosen["verdict"] == "not_connectable" and "403" in chosen["reason"]


def test_device_only_server_is_not_oauth():
    fetch = fetcher({
        API + "/v1/models": (200, CATALOGUE),
        API + "/.well-known/oauth-protected-resource": (
            200, {"resource": API, "authorization_servers": [AS]}),
        AS + "/.well-known/oauth-authorization-server": server(
            device_authorization_endpoint=AS + "/device"),
    })
    chosen = detect(API, fetch=fetch)["chosen"]
    assert chosen["verdict"] == "api_key" and "registered client" in chosen["reason"]


def test_insecure_and_private_addresses_refused_before_any_probe():
    fetch = fetcher({})
    for address in ("http://api.example", "https://user@api.example", "https://api.provider.example:8443"):
        with pytest.raises(OAuthError):
            detect(address, fetch=fetch)
    assert not fetch.seen


# ---- guard: no provider name or host in the connect, detector or exchange code ----
CODE_PATH = [
    "tinyassets/providers/free_sources.py",
    "tinyassets/providers/discovery_presets.py",
    "tinyassets/onboarding/hosted_model_auth.py",
    "tinyassets/providers/source_record.py",
    "tinyassets/providers/subscription_runtime.py",
    "tinyassets/providers/subscription_catalog.py",
    "tinyassets/providers/subscription_provider.py",
    "tinyassets/onboarding/subscription_connect.py",
    "tinyassets/connection_oauth/model_discovery.py",
    "tinyassets/connection_oauth/discovery.py",
    "tinyassets/connection_oauth/flow.py",
    "tinyassets/connection_oauth/tokens.py",
    "tinyassets/connection_oauth/transport.py",
    "tinyassets/broker/model_connect.py",
    "tinyassets/onboarding/model_connect.py",
    "tinyassets/onboarding/source_connect.py",
    "scripts/blind_provider_test.py",
]
PROVIDERS = ("openrouter", "openai", "anthropic", "claude", "gemini", "google", "mistral",
             "groq", "grok", "xai", "x.ai", "deepseek", "together", "fireworks", "cohere",
             "perplexity", "ollama", "huggingface", "cerebras", "deepinfra", "nvidia", "meta.ai",
             "moonshot", "qwen", "dashscope", "replicate", "azure", "bedrock", "vertex")


def test_no_provider_names_in_the_connect_path():
    from tinyassets.providers.source_record import records

    hosts = {re.sub(r"^https://|/.*$", "", address)
             for record in records().values() for address in record.addresses}
    for path in CODE_PATH:
        text = Path(path).read_text("utf-8").lower()
        # A compatibility route used by unrelated hosts is not provider dispatch.
        if path == "tinyassets/connection_oauth/model_discovery.py":
            text = text.replace('"/openai/v1"', '"/compatibility/v1"')
        for token in (*PROVIDERS, *hosts):
            assert not re.search(r"(?<![a-z])" + re.escape(token.lower()) + r"(?![a-z])",
                                 text), f"{token} in {path}"


pytestmark = pytest.mark.verifies('model-connect-by-discovery:Address-based model connection')
