"""ApiKeyHttpProvider — compute over a user-registered http provider via the
credential-blind outbound proxy (compute-agnostic slice 2.3b).

Covers: happy-path openai + anthropic via an INJECTED proxy (wire assembly is
correct + carries no secret; response decodes into a ProviderResponse); the
universe-isolation gate (a grant bound to another universe is refused BEFORE any
dispatch); absent/revoked grant refusal; HTTP status mapping (429/5xx/4xx ->
typed provider errors); malformed body fails loud; constructor validation.

The real broker worker (SSRF, credential application) is exercised by
integration/dogfood, not here — these tests inject a fake proxy for the dispatch
seam and use a REAL ConnectionLedger for the grant/isolation gate.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tinyassets.exceptions import (
    ProviderModelRefusedError,
    ProviderOverloadedError,
    ProviderProtocolError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.providers.definition import ProviderDefinition

_CONN_ID = "http_" + "b" * 32
_GRANT_ID = "http_grant_" + "a" * 32
_HOST = "api.example.com"


class _FakeProxy:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def request(self, verb: str, wire: dict[str, Any]) -> Any:
        self.calls.append((verb, wire))
        return self.response


def _seed(base: Path, *, owner: str = "founder", universe: str = "u-x") -> None:
    """Create a real http connection + grant bound to `universe` in outbound.db."""
    from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger

    ledger = ConnectionLedger(
        base / ".broker" / "outbound.db", data_root=base,
        verify_authenticated_principal=lambda: owner
    )
    ledger.create_connection(
        connection_id=_CONN_ID,
        owner_user_id=owner,
        connection_class="http",
        connection_type="http",
        auth_scheme="bearer",
        scopes=("http",),
        provider="http",
        destination="compute:test",
        credential_ref="vault://http/compute:test",
        allowed_endpoints=[
            {"host": _HOST, "path_template": "/v1/chat/completions", "methods": ["POST"]},
            {"host": _HOST, "path_template": "/v1/messages", "methods": ["POST"]},
        ],
    )
    ledger.grant_connection(
        grant_id=_GRANT_ID,
        connection_id=_CONN_ID,
        owner_user_id=owner,
        universe_id=universe,
        unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )


def _definition(protocol: str = "openai_chat", *, ref: str = _GRANT_ID) -> ProviderDefinition:
    return ProviderDefinition(
        id="provdef_" + "c" * 32,
        universe_id="u-x",
        owner_user_id="founder",
        access_method="api_key_http",
        protocol=protocol,
        model="moonshotai/kimi-k2",
        ref=ref,
        visibility="private",
        created_at="2026-01-01T00:00:00+00:00",
    )


def _config() -> Any:
    return SimpleNamespace(temperature=0.2, timeout=60, max_tokens=1024,
                           invocation_owner_user_id="founder")


def _run(provider: ApiKeyHttpProvider, universe_dir: Path) -> Any:
    return asyncio.run(
        provider.complete("hello", "be terse", _config(), universe_dir=universe_dir)
    )


@pytest.fixture
def base(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    (root / "u-x").mkdir(parents=True)
    (root / "u-y").mkdir(parents=True)
    return root


# --------------------------------------------------------------------------- #
# Happy path + wire assembly.
# --------------------------------------------------------------------------- #


def test_openai_happy_path_and_wire_assembly(base: Path) -> None:
    _seed(base)
    proxy = _FakeProxy(
        {
            "status": 200,
            "body": json.dumps(
                {
                    "choices": [{"message": {"content": "the answer"}}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 4},
                }
            ),
        }
    )
    provider = ApiKeyHttpProvider(_definition(), proxy_override=proxy)
    resp = _run(provider, base / "u-x")

    assert resp.text == "the answer"
    assert resp.input_tokens == 9 and resp.output_tokens == 4
    assert resp.family == "api:openai_chat"
    # No model was reported: the requested alias is not execution evidence.
    assert resp.model == ""
    assert resp.reported_model == ""

    # Wire assembly: POST to the exact allowlisted URL, correct body, NO secret.
    verb, wire = proxy.calls[0]
    assert verb == "POST"
    assert wire["url"] == "https://api.example.com/v1/chat/completions"
    assert wire["body"]["model"] == "moonshotai/kimi-k2"
    blob = json.dumps(wire).lower()
    assert "authorization" not in blob and "bearer" not in blob


def test_anthropic_happy_path(base: Path) -> None:
    _seed(base)
    proxy = _FakeProxy(
        {
            "status": 200,
            "body": json.dumps(
                {"content": [{"type": "text", "text": "hi there"}],
                 "usage": {"input_tokens": 2, "output_tokens": 3}}
            ),
        }
    )
    provider = ApiKeyHttpProvider(_definition("anthropic_messages"), proxy_override=proxy)
    resp = _run(provider, base / "u-x")
    assert resp.text == "hi there"
    wire = proxy.calls[0][1]
    assert wire["url"] == "https://api.example.com/v1/messages"
    # The Anthropic Messages API REQUIRES anthropic-version or it 400s — the Claude
    # compute node must send it (the api key rides the connection's x-api-key auth,
    # never these static headers).
    assert wire["headers"]["anthropic-version"] == "2023-06-01"
    blob = json.dumps(wire).lower()
    assert "x-api-key" not in blob and "authorization" not in blob  # no cred here


@pytest.mark.parametrize("protocol", ["openai_chat", "anthropic_messages"])
def test_receipt_reports_answering_model_without_mutating_selection(
    base: Path, protocol: str,
) -> None:
    _seed(base)
    body = (
        {"choices": [{"message": {"content": "answer"}}]}
        if protocol == "openai_chat"
        else {"content": [{"type": "text", "text": "answer"}]}
    )
    body["model"] = "future-provider/actual-model-2099"
    proxy = _FakeProxy({"status": 200, "body": json.dumps(body)})
    provider = ApiKeyHttpProvider(_definition(protocol), proxy_override=proxy)
    response = _run(provider, base / "u-x")
    assert response.model == body["model"]
    assert response.reported_model == body["model"]
    assert provider.model == "moonshotai/kimi-k2"
    assert proxy.calls[0][1]["body"]["model"] == provider.model
    assert response.provider == provider.name  # remote metadata grants no identity
    from tinyassets.providers.router import ProviderRouter

    assert ProviderRouter._call_meta(response, 1)["model"] == body["model"]


@pytest.mark.parametrize("reported", [None, "", "  ", 12, True, {}, [], "x\ny", "x" * 201])
def test_unusable_model_metadata_is_unknown_without_discarding_answer(
    base: Path, reported: Any,
) -> None:
    _seed(base)
    proxy = _FakeProxy({"status": 200, "body": json.dumps({
        "model": reported, "choices": [{"message": {"content": "answer"}}],
    })})
    response = _run(ApiKeyHttpProvider(_definition(), proxy_override=proxy), base / "u-x")
    assert response.text == "answer"
    assert response.model == ""
    assert response.reported_model == ""


def test_openai_sends_no_static_headers(base: Path) -> None:
    _seed(base)
    proxy = _FakeProxy(
        {"status": 200, "body": json.dumps(
            {"choices": [{"message": {"content": "ok"}}]})}
    )
    provider = ApiKeyHttpProvider(_definition("openai_chat"), proxy_override=proxy)
    _run(provider, base / "u-x")
    # openai_chat has no protocol-static headers; the executor omits the key.
    assert "headers" not in proxy.calls[0][1]


# --------------------------------------------------------------------------- #
# Isolation + grant gates (real ledger).
# --------------------------------------------------------------------------- #


def test_grant_bound_to_other_universe_refused(base: Path) -> None:
    _seed(base, universe="u-x")  # grant bound to u-x
    proxy = _FakeProxy({"status": 200, "body": "{}"})
    provider = ApiKeyHttpProvider(_definition(), proxy_override=proxy)
    # Running as u-y must refuse BEFORE any dispatch — cross-universe isolation.
    with pytest.raises(ProviderUnavailableError):
        _run(provider, base / "u-y")
    assert proxy.calls == []  # never dispatched


def test_absent_grant_refused(base: Path) -> None:
    # No seed → the grant does not exist.
    provider = ApiKeyHttpProvider(_definition(ref="http_grant_" + "z" * 32))
    with pytest.raises(ProviderUnavailableError):
        _run(provider, base / "u-x")


def test_missing_universe_dir_refused(base: Path) -> None:
    _seed(base)
    provider = ApiKeyHttpProvider(_definition())
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(provider.complete("p", "s", _config(), universe_dir=None))


# --------------------------------------------------------------------------- #
# Status mapping + malformed body (fail loud).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "status,exc",
    [
        (429, ProviderRateLimitedError),
        (503, ProviderOverloadedError),
        (500, ProviderOverloadedError),
        (400, ProviderProtocolError),
        # A refusal of the model is not an unreadable reply (live 2026-09-28).
        (403, ProviderModelRefusedError),
        (404, ProviderModelRefusedError),
        (410, ProviderModelRefusedError),
    ],
)
def test_http_status_maps_to_provider_error(base: Path, status: int, exc: type) -> None:
    _seed(base)
    proxy = _FakeProxy({"status": status, "body": "{}"})
    provider = ApiKeyHttpProvider(_definition(), proxy_override=proxy)
    with pytest.raises(exc):
        _run(provider, base / "u-x")


def test_malformed_body_fails_loud(base: Path) -> None:
    _seed(base)
    proxy = _FakeProxy({"status": 200, "body": "not json at all"})
    provider = ApiKeyHttpProvider(_definition(), proxy_override=proxy)
    with pytest.raises(ProviderProtocolError):
        _run(provider, base / "u-x")


def test_error_envelope_without_status_fails_loud(base: Path) -> None:
    _seed(base)
    proxy = _FakeProxy({"reason": "connect timeout"})  # no status
    provider = ApiKeyHttpProvider(_definition(), proxy_override=proxy)
    with pytest.raises(ProviderUnavailableError):
        _run(provider, base / "u-x")


def test_non_integer_status_rejected(base: Path) -> None:
    # A malformed float status must NOT be truncated to 200 and pass as success.
    _seed(base)
    proxy = _FakeProxy({"status": 200.9, "body": '{"choices":[{"message":{"content":"x"}}]}'})
    provider = ApiKeyHttpProvider(_definition(), proxy_override=proxy)
    with pytest.raises(ProviderUnavailableError):
        _run(provider, base / "u-x")


# --------------------------------------------------------------------------- #
# Constructor validation.
# --------------------------------------------------------------------------- #


def test_constructor_rejects_non_api_key_http() -> None:
    bad = ProviderDefinition(
        id="provdef_x", universe_id="u", owner_user_id="o",
        access_method="subscription_cli", protocol="cli:codex", model="gpt-5",
        ref="codex", visibility="private", created_at="2026-01-01T00:00:00+00:00",
    )
    with pytest.raises(ValueError):
        ApiKeyHttpProvider(bad)


# --------------------------------------------------------------------------- #
# The user's own endpoint path.
#
# A compute connection registered through the app's "connect any LLM" pane
# allowlists exactly the URL the user typed. Calling the protocol's canonical
# path instead makes the broker refuse every endpoint whose path is not the
# canonical one -- registerable, never servable (Codex, connect-any-llm P1).
# --------------------------------------------------------------------------- #


def _seed_single_path(base: Path, path: str, *, owner: str = "founder",
                      universe: str = "u-x", read_paths: tuple[str, ...] = ()) -> None:
    from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger

    ledger = ConnectionLedger(
        base / ".broker" / "outbound.db", data_root=base,
        verify_authenticated_principal=lambda: owner
    )
    ledger.create_connection(
        connection_id=_CONN_ID,
        owner_user_id=owner,
        connection_class="http",
        connection_type="http",
        auth_scheme="bearer",
        scopes=("http",),
        provider="http",
        destination="compute:test",
        credential_ref="vault://http/compute:test",
        allowed_endpoints=[
            {"host": _HOST, "path_template": path, "methods": ["POST"]},
            *({"host": _HOST, "path_template": read_path, "methods": ["GET"]}
              for read_path in read_paths),
        ],
    )
    ledger.grant_connection(
        grant_id=_GRANT_ID,
        connection_id=_CONN_ID,
        owner_user_id=owner,
        universe_id=universe,
        unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )


def _ok_proxy() -> "_FakeProxy":
    return _FakeProxy({
        "status": 200,
        "body": json.dumps({
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }),
    })


def test_a_custom_granted_path_is_the_path_called(base: Path) -> None:
    _seed_single_path(base, "/custom/chat")
    proxy = _ok_proxy()
    _run(ApiKeyHttpProvider(_definition(), proxy_override=proxy), base / "u-x")

    _verb, wire = proxy.calls[0]
    assert wire["url"] == "https://api.example.com/custom/chat", (
        "the user's granted path was ignored in favour of the protocol default"
    )


@pytest.mark.parametrize("path", ["/custom/chat", "/api/v1/chat/completions"])
def test_discovery_reads_do_not_change_inference_path(base: Path, path: str) -> None:
    _seed_single_path(base, path, read_paths=("/api/v1/models/user", "/api/v1/key"))
    proxy = _ok_proxy()
    _run(ApiKeyHttpProvider(_definition(), proxy_override=proxy), base / "u-x")

    assert len(proxy.calls) == 1
    verb, wire = proxy.calls[0]
    assert verb == "POST"
    assert wire["url"] == f"https://{_HOST}{path}"


def test_a_templated_path_is_not_treated_as_concrete() -> None:
    """A placeholder is not a concrete path; guessing a substitution would be worse.

    The ledger refuses a placeholder without `param_patterns`, so this guards the
    selector directly rather than through a connection it cannot seed.
    """
    from tinyassets.providers.api_key_http_provider import _declared_path

    templated = SimpleNamespace(allowed_endpoints=[
        SimpleNamespace(host=_HOST, path_template="/v1/{model}/chat", methods=("POST",)),
    ])
    assert _declared_path(templated) == ""

    concrete = SimpleNamespace(allowed_endpoints=[
        SimpleNamespace(host=_HOST, path_template="/custom/chat", methods=("POST",)),
    ])
    assert _declared_path(concrete) == "/custom/chat"

    none_declared = SimpleNamespace(allowed_endpoints=[])
    assert _declared_path(none_declared) == ""


@pytest.mark.parametrize("methods", [("GET",), (), ("DELETE",)])
def test_non_inference_endpoint_is_never_selected(methods) -> None:
    from tinyassets.providers.api_key_http_provider import _declared_path

    view = SimpleNamespace(allowed_endpoints=[
        SimpleNamespace(host=_HOST, path_template="/models", methods=methods),
    ])
    assert _declared_path(view) == ""


def test_several_declared_paths_fall_back_to_the_protocol_path(base: Path) -> None:
    """The two-endpoint connection the other tests use must not change behaviour."""
    _seed(base)
    proxy = _ok_proxy()
    _run(ApiKeyHttpProvider(_definition(), proxy_override=proxy), base / "u-x")

    _verb, wire = proxy.calls[0]
    assert wire["url"] == "https://api.example.com/v1/chat/completions"
