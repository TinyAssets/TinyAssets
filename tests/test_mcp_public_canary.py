"""PR-178: mcp_public_canary --assert-handles drift guard (offline)."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "mcp_public_canary",
    Path(__file__).resolve().parents[1] / "scripts" / "mcp_public_canary.py",
)
canary = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(canary)

_TOKEN = "t" * 40


@pytest.fixture(autouse=True)
def _canary_token(monkeypatch):
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", _TOKEN)


def _scripted_post(
    tool_names,
    status_payload=None,
    *,
    structured_status=False,
    converse_status=200,
    converse_challenge=(
        'Bearer resource_metadata="https://example/'
        '.well-known/oauth-protected-resource/mcp" error="invalid_token" '
        'error_description="Sign in required"'
    ),
    converse_is_error=True,
    canary_converse_status=403,
    anonymous_initialize_status=401,
    server_name="tinyassets",
    calls=None,
):
    """Return a fake _post that replays an MCP handshake advertising tool_names."""
    if status_payload is None:
        status_payload = {
            "active_host": {"llm_endpoint_bound": True},
            "release_state": {"git_sha": "abc123"},
            "request_identity": {"principal_fingerprint": "v1:abc"},
            "identity_evidence": {"status": "available"},
        }

    def _post(
        url,
        payload,
        timeout,
        session_id=None,
        accepted_http_statuses=frozenset(),
        bearer=None,
    ):
        if calls is not None:
            calls.append({"payload": payload, "bearer": bearer})
        method = payload.get("method")
        if method == "initialize":
            if bearer is None:
                return anonymous_initialize_status, {
                    "www-authenticate": (
                        'Bearer resource_metadata="https://example/'
                        '.well-known/oauth-protected-resource/mcp"'
                    ),
                }, (
                    b'{"error":"authentication_required",'
                    b'"error_description":"Sign in to TinyAssets to continue."}'
                )
            body = json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": server_name, "version": "0.1.0"},
                },
            }).encode()
            return 200, {"mcp-session-id": "sess-1"}, body
        if method == "notifications/initialized":
            return 202, {}, b""
        if method == "tools/list":
            body = json.dumps({
                "jsonrpc": "2.0", "id": 2,
                "result": {"tools": [{"name": n} for n in tool_names]},
            }).encode()
            return 200, {}, body
        if method == "tools/call":
            if payload["params"]["name"] == "converse":
                assert payload["params"] == {
                    "name": "converse",
                    "arguments": {"message": "mcp-public-canary auth boundary probe"},
                }
                expected = 403 if bearer else 200
                assert expected in accepted_http_statuses
                if bearer:
                    return canary_converse_status, {}, b'{"error":"forbidden"}'
                if converse_status == 200 and converse_challenge is not None:
                    return 200, {}, json.dumps({
                        "jsonrpc": "2.0",
                        "id": 3,
                        "result": {
                            "content": [{"type": "text", "text": "Sign in required"}],
                            "isError": converse_is_error,
                            "_meta": {
                                "mcp/www_authenticate": [converse_challenge],
                            },
                        },
                    }).encode()
                if converse_status == 401:
                    return 401, {"www-authenticate": converse_challenge}, (
                        b'{"error":"authentication_required",'
                        b'"error_description":"Sign in to TinyAssets to continue."}'
                    )
                body = json.dumps({
                    "jsonrpc": "2.0",
                    "id": 3,
                    "error": {"code": -32602, "message": "Resource not found"},
                }).encode()
                return converse_status, {}, body
            assert payload["params"] == {"name": "get_status", "arguments": {}}
            result = {
                "content": [
                    {"type": "text", "text": json.dumps(status_payload)}
                ]
            }
            if structured_status:
                result["content"][0]["text"] = (
                    '{"active_host":\n... [truncated; full payload in structuredContent]'
                )
                result["structuredContent"] = status_payload
            body = json.dumps({
                "jsonrpc": "2.0",
                "id": 3,
                "result": result,
            }).encode()
            return 200, {}, body
        raise AssertionError(f"unexpected method {method}")

    return _post


_CANONICAL_PLUS_STATUS = [
    "read_graph", "write_graph", "run_graph", "read_page", "write_page",
    "converse", "get_status",
]


def test_assert_handles_passes_on_exact_surface(monkeypatch):
    monkeypatch.setattr(canary, "_post", _scripted_post(_CANONICAL_PLUS_STATUS))
    # No exception == green.
    canary.assert_canonical_handles("https://example/mcp", 5.0)


def test_assert_handles_fails_on_legacy_leak(monkeypatch):
    leaky = _CANONICAL_PLUS_STATUS + ["universe", "extensions"]
    monkeypatch.setattr(canary, "_post", _scripted_post(leaky))
    with pytest.raises(canary.CanaryError) as exc:
        canary.assert_canonical_handles("https://example/mcp", 5.0)
    assert exc.value.code == 4
    assert "universe" in exc.value.msg


@pytest.mark.parametrize(
    "extra_handle",
    ["contribution", "dataset", "manifest", "forge", "promotion"],
)
def test_assert_handles_rejects_data_commons_catalog_handles(
    monkeypatch, extra_handle
):
    monkeypatch.setattr(
        canary,
        "_post",
        _scripted_post(_CANONICAL_PLUS_STATUS + [extra_handle]),
    )
    with pytest.raises(canary.CanaryError) as exc:
        canary.assert_canonical_handles("https://example/mcp", 5.0)
    assert exc.value.code == 4
    assert extra_handle in exc.value.msg


def test_assert_handles_fails_on_missing_handle(monkeypatch):
    short = [n for n in _CANONICAL_PLUS_STATUS if n != "run_graph"]
    monkeypatch.setattr(canary, "_post", _scripted_post(short))
    with pytest.raises(canary.CanaryError) as exc:
        canary.assert_canonical_handles("https://example/mcp", 5.0)
    assert exc.value.code == 4
    assert "run_graph" in exc.value.msg


def test_assert_handles_fails_when_get_status_is_missing(monkeypatch):
    short = [n for n in _CANONICAL_PLUS_STATUS if n != "get_status"]
    monkeypatch.setattr(canary, "_post", _scripted_post(short))
    with pytest.raises(canary.CanaryError) as exc:
        canary.assert_canonical_handles("https://example/mcp", 5.0)
    assert exc.value.code == 4
    assert "get_status" in exc.value.msg


def test_advertised_tool_names_round_trips(monkeypatch):
    monkeypatch.setattr(canary, "_post", _scripted_post(_CANONICAL_PLUS_STATUS))
    names = canary.advertised_tool_names("https://example/mcp", 5.0)
    assert names == set(_CANONICAL_PLUS_STATUS)


def test_canary_metadata_url_matches_the_challenge_helper():
    from tinyassets.auth.wellknown import resource_metadata_url

    resource = "https://tinyassets.io/mcp"
    advertised, legacy = canary.resource_metadata_urls(resource)
    assert advertised == resource_metadata_url(resource)
    assert advertised == (
        "https://tinyassets.io/.well-known/oauth-protected-resource/mcp"
    )
    assert legacy == (
        "https://tinyassets.io/mcp/.well-known/oauth-protected-resource"
    )


def test_converse_auth_gate_reaches_canonical_bearer_challenge(monkeypatch):
    monkeypatch.setattr(canary, "_post", _scripted_post(_CANONICAL_PLUS_STATUS))

    requested_urls = []

    class MetadataResponse:
        status = 200
        headers = {"content-type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({
                "resource": "https://example/mcp",
                "authorization_servers": list(
                    canary.EXPECTED_AUTHORIZATION_SERVERS
                ),
            }).encode()

    def urlopen(request, **kwargs):
        requested_urls.append(request.full_url)
        return MetadataResponse()

    monkeypatch.setattr(canary.urllib.request, "urlopen", urlopen)

    canary.assert_converse_auth_gate("https://example/mcp", 5.0)

    assert requested_urls == [
        "https://example/.well-known/oauth-protected-resource/mcp",
        "https://example/mcp/.well-known/oauth-protected-resource",
    ]


def test_converse_auth_gate_rejects_protected_resource_document_drift(
    monkeypatch,
):
    monkeypatch.setattr(canary, "_post", _scripted_post(_CANONICAL_PLUS_STATUS))

    class MetadataResponse:
        status = 200
        headers = {"content-type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({
                "resource": "https://wrong.example/mcp",
                "authorization_servers": [],
            }).encode()

    monkeypatch.setattr(
        canary.urllib.request,
        "urlopen",
        lambda request, **kwargs: MetadataResponse(),
    )

    with pytest.raises(canary.CanaryError) as exc:
        canary.assert_converse_auth_gate("https://example/mcp", 5.0)

    assert exc.value.code == 6
    assert "protected resource document drift" in exc.value.msg


def test_converse_auth_gate_rejects_authorization_server_drift(monkeypatch):
    monkeypatch.setattr(canary, "_post", _scripted_post(_CANONICAL_PLUS_STATUS))
    monkeypatch.setattr(
        canary,
        "_get_json",
        lambda url, timeout: {
            "resource": "https://example/mcp",
            "authorization_servers": ["https://wrong.example"],
        },
    )

    with pytest.raises(canary.CanaryError) as exc:
        canary.assert_converse_auth_gate("https://example/mcp", 5.0)

    assert exc.value.code == 6
    assert "protected resource document drift" in exc.value.msg


def test_converse_auth_gate_rejects_resource_not_found(monkeypatch):
    monkeypatch.setattr(
        canary,
        "_post",
        _scripted_post(
            _CANONICAL_PLUS_STATUS,
            converse_status=200,
            converse_challenge=None,
        ),
    )

    with pytest.raises(canary.CanaryError) as exc:
        canary.assert_converse_auth_gate("https://example/mcp", 5.0)

    assert exc.value.code == 6
    assert "unexpected converse linking result" in exc.value.msg


def test_converse_auth_gate_rejects_transport_only_challenge(monkeypatch):
    monkeypatch.setattr(
        canary,
        "_post",
        _scripted_post(_CANONICAL_PLUS_STATUS, converse_status=401),
    )

    with pytest.raises(canary.CanaryError) as exc:
        canary.assert_converse_auth_gate("https://example/mcp", 5.0)

    assert exc.value.code == 6
    assert "expected hosted linking result HTTP 200" in exc.value.msg


def test_converse_auth_gate_rejects_anonymous_dispatch_result(monkeypatch):
    monkeypatch.setattr(
        canary,
        "_post",
        _scripted_post(_CANONICAL_PLUS_STATUS, converse_is_error=False),
    )

    with pytest.raises(canary.CanaryError) as exc:
        canary.assert_converse_auth_gate("https://example/mcp", 5.0)

    assert exc.value.code == 6
    assert "unexpected converse linking result" in exc.value.msg


def test_converse_auth_gate_rejects_protected_resource_metadata_drift(
    monkeypatch,
):
    monkeypatch.setattr(
        canary,
        "_post",
        _scripted_post(
            _CANONICAL_PLUS_STATUS,
            converse_challenge=(
                'Bearer resource_metadata="https://wrong.example/'
                '.well-known/oauth-protected-resource"'
            ),
        ),
    )

    with pytest.raises(canary.CanaryError) as exc:
        canary.assert_converse_auth_gate("https://example/mcp", 5.0)

    assert exc.value.code == 6
    assert "resource metadata drift" in exc.value.msg


def test_status_surface_assertion_calls_get_status_and_checks_uptime_fields(
    monkeypatch,
):
    monkeypatch.setattr(canary, "_post", _scripted_post(_CANONICAL_PLUS_STATUS))

    identity_state = canary.assert_status_surface("https://example/mcp", 5.0)

    assert identity_state == "available"


def test_status_surface_assertion_accepts_explicit_identity_degradation(
    monkeypatch,
):
    status_payload = {
        "active_host": {"llm_endpoint_bound": False},
        "release_state": {"git_sha": "abc123"},
        "request_identity": {"principal_fingerprint": None},
        "identity_evidence": {
            "status": "unavailable",
            "reason": "key_not_provisioned",
        },
    }
    monkeypatch.setattr(
        canary,
        "_post",
        _scripted_post(_CANONICAL_PLUS_STATUS, status_payload),
    )

    identity_state = canary.assert_status_surface("https://example/mcp", 5.0)

    assert identity_state == "unavailable:key_not_provisioned"


def test_status_surface_prefers_full_structured_content_over_truncated_text(
    monkeypatch,
):
    monkeypatch.setattr(
        canary,
        "_post",
        _scripted_post(_CANONICAL_PLUS_STATUS, structured_status=True),
    )

    identity_state = canary.assert_status_surface("https://example/mcp", 5.0)

    assert identity_state == "available"


@pytest.mark.parametrize("missing_field", ["active_host", "release_state"])
def test_status_surface_assertion_fails_when_uptime_field_is_missing(
    monkeypatch,
    missing_field,
):
    status_payload = {
        "active_host": {"llm_endpoint_bound": True},
        "release_state": {"git_sha": "abc123"},
        "request_identity": {"principal_fingerprint": None},
        "identity_evidence": {
            "status": "unavailable",
            "reason": "key_not_provisioned",
        },
    }
    status_payload.pop(missing_field)
    monkeypatch.setattr(
        canary,
        "_post",
        _scripted_post(_CANONICAL_PLUS_STATUS, status_payload),
    )

    with pytest.raises(canary.CanaryError) as exc:
        canary.assert_status_surface("https://example/mcp", 5.0)

    assert exc.value.code == 5
    assert missing_field in exc.value.msg


def test_assert_handles_retry_includes_get_status_uptime_assertion(monkeypatch):
    status_stub = {
        "identity_evidence": {
            "status": "unavailable",
            "reason": "key_not_provisioned",
        },
        "request_identity": {"principal_fingerprint": None},
    }
    monkeypatch.setattr(
        canary,
        "_post",
        _scripted_post(_CANONICAL_PLUS_STATUS, status_stub),
    )
    monkeypatch.setattr(
        canary,
        "_get_json",
        lambda url, timeout: {
            "resource": "https://example/mcp",
            "authorization_servers": list(canary.EXPECTED_AUTHORIZATION_SERVERS),
        },
    )

    with pytest.raises(canary.CanaryError) as exc:
        canary.assert_canonical_handles_with_retry(
            "https://example/mcp",
            5.0,
            retries=1,
            delay=0.0,
            _sleep=lambda _: None,
        )

    assert exc.value.code == 5
    assert "active_host" in exc.value.msg
    assert "release_state" in exc.value.msg


def test_retry_recovers_from_transient_blip(monkeypatch):
    """A transient failure that clears on a later attempt must NOT fail."""
    good = _scripted_post(_CANONICAL_PLUS_STATUS)
    calls = {"n": 0}

    def flaky(
        url,
        payload,
        timeout,
        session_id=None,
        accepted_http_statuses=frozenset(),
        bearer=None,
    ):
        if payload.get("method") == "initialize" and bearer:
            calls["n"] += 1
            if calls["n"] == 1:
                raise canary.CanaryError(2, "transient unreachable")
        return good(
            url, payload, timeout, session_id, accepted_http_statuses, bearer,
        )

    monkeypatch.setattr(canary, "_post", flaky)
    monkeypatch.setattr(
        canary,
        "_get_json",
        lambda url, timeout: {
            "resource": "https://example/mcp",
            "authorization_servers": list(canary.EXPECTED_AUTHORIZATION_SERVERS),
        },
    )
    # Should pass on the 2nd attempt; no real sleeping.
    canary.assert_canonical_handles_with_retry(
        "https://example/mcp", 5.0, retries=3, delay=0.0, _sleep=lambda _: None
    )


def test_retry_propagates_persistent_drift(monkeypatch):
    """A genuine, persistent regression still fails after retries exhaust."""
    monkeypatch.setattr(
        canary, "_post", _scripted_post(_CANONICAL_PLUS_STATUS + ["universe"])
    )
    with pytest.raises(canary.CanaryError) as exc:
        canary.assert_canonical_handles_with_retry(
            "https://example/mcp", 5.0, retries=3, delay=0.0, _sleep=lambda _: None
        )
    assert exc.value.code == 4


def _initialize_urlopen(server_name: str):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": server_name, "version": "0.1.0"},
                    },
                }
            ).encode()

    return lambda *args, **kwargs: Response()


def test_probe_result_accepts_exact_expected_public_name(monkeypatch):
    monkeypatch.setattr(
        canary,
        "_post",
        _scripted_post(_CANONICAL_PLUS_STATUS, server_name="TinyAssets"),
    )

    canary.probe_result(
        "https://example/mcp",
        5.0,
        expected_name="TinyAssets",
    )


def test_probe_result_rejects_case_drift_in_public_name(monkeypatch):
    monkeypatch.setattr(
        canary,
        "_post",
        _scripted_post(_CANONICAL_PLUS_STATUS, server_name="tinyassets"),
    )

    with pytest.raises(canary.CanaryError) as exc:
        canary.probe_result(
            "https://example/mcp",
            5.0,
            expected_name="TinyAssets",
        )

    assert exc.value.code == 1
    assert "expected 'TinyAssets'" in exc.value.msg
    assert "got 'tinyassets'" in exc.value.msg


def test_probe_result_rejects_anonymous_initialize_200(monkeypatch):
    monkeypatch.setattr(
        canary,
        "_post",
        _scripted_post(
            _CANONICAL_PLUS_STATUS,
            anonymous_initialize_status=200,
        ),
    )
    with pytest.raises(canary.CanaryError) as exc:
        canary.probe_result("https://example/mcp", 5.0)
    assert exc.value.code == 6
    assert "admitted an anonymous initialize" in exc.value.msg


def test_canary_bearer_converse_200_is_exit_6(monkeypatch):
    monkeypatch.setattr(
        canary,
        "_post",
        _scripted_post(
            _CANONICAL_PLUS_STATUS,
            canary_converse_status=200,
        ),
    )
    monkeypatch.setattr(
        canary,
        "_get_json",
        lambda url, timeout: {
            "resource": "https://example/mcp",
            "authorization_servers": list(canary.EXPECTED_AUTHORIZATION_SERVERS),
        },
    )
    with pytest.raises(canary.CanaryError) as exc:
        canary.assert_converse_auth_gate("https://example/mcp", 5.0)
    assert exc.value.code == 6


def test_main_requires_token_before_network(monkeypatch, capsys):
    calls = []
    monkeypatch.delenv("TINYASSETS_WIKI_CANARY_TOKEN", raising=False)
    monkeypatch.setattr(canary, "_post", lambda *a, **k: calls.append((a, k)))
    with pytest.raises(SystemExit) as exc:
        canary.main([])
    assert exc.value.code == 2
    assert not calls
    assert "TINYASSETS_WIKI_CANARY_TOKEN" in capsys.readouterr().err


def test_pulse_only_sends_canary_bearer(monkeypatch):
    import urllib.request

    seen = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, _limit):
            return b'{"git_sha":"abc123"}'

    def urlopen(request, timeout):
        seen.append((request, timeout))
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert canary._pulse_only("https://example/mcp", 5.0) == 0
    assert seen[0][0].full_url == "https://example/mcp/pulse"
    assert seen[0][0].get_header("Authorization") == f"Bearer {_TOKEN}"


def test_authenticated_handle_posts_all_carry_canary_bearer(monkeypatch):
    calls = []
    monkeypatch.setattr(
        canary,
        "_post",
        _scripted_post(_CANONICAL_PLUS_STATUS, calls=calls),
    )
    canary.assert_canonical_handles("https://example/mcp", 5.0)
    assert calls
    assert all(call["bearer"] == _TOKEN for call in calls)


def test_the_healthcheck_invocation_does_not_authenticate_its_anonymous_probe(monkeypatch):
    """The exact shape `deploy/compose.yml` runs inside the container.

    Codex found this red: the "anonymous" initialize omitted its bearer
    argument, the transport defaulted to reading the environment, the request
    went out authenticated, the daemon answered 200 -- and the canary reported
    "surface admitted an anonymous initialize", exit 6. Deterministically, on
    the first deploy, with a rollback as the outcome.

    So this drives the REAL transport and asserts on the header it put on the
    wire, per call, rather than on a fake whose defaults differ from
    production's.
    """
    import urllib.request

    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", _TOKEN)
    sent: list[tuple[str, str | None]] = []

    class _Resp:
        def __init__(self, status, headers, body):
            self.status = status
            self.headers = headers
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(request, timeout=None, context=None):
        method = json.loads(request.data)["method"]
        sent.append((method, request.get_header("Authorization")))
        if request.get_header("Authorization") is None:
            # The new daemon: no bearer, no session.
            raise urllib.error.HTTPError(
                request.full_url, 401,
                "Unauthorized",
                {"www-authenticate": 'Bearer resource_metadata="https://example/.well-known/oauth-protected-resource/mcp"'},
                io.BytesIO(
                    b'{"error":"authentication_required",'
                    b'"error_description":"Sign in to TinyAssets to continue."}'
                ),
            )
        return _Resp(
            200,
            {"mcp-session-id": "s-1"},
            json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "result": {"protocolVersion": "2024-11-05",
                           "serverInfo": {"name": "TinyAssets", "version": "1"}},
            }).encode(),
        )

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    canary.probe_result("https://example/mcp", 5.0, bearer=_TOKEN)

    assert [auth for _, auth in sent] == [None, f"Bearer {_TOKEN}"], sent
