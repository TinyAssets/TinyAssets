"""Deterministic policy and route tests for provider-neutral Realtime voice."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from tinyassets.onboarding import realtime_voice as rv

_CONNECTION_ID = "voice-connection"
_GRANT_ID = "voice-grant"
_SESSION_URL = "https://bridge.example/v1/session"
_OFFER_SDP = "v=0\r\no=browser 1 1 IN IP4 127.0.0.1\r\n"
_ANSWER_SDP = "v=0\r\no=bridge 1 1 IN IP4 203.0.113.10\r\n"


#: There is no per-user rate bucket to clear between tests any more -- the voice
#: session/status limiters were account limits and are gone (founder 2026-09-30).


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("TINYASSETS_REALTIME_VOICE_ENABLED", "1")
    monkeypatch.setenv("TINYASSETS_ALLOW_REALTIME_VOICE_API", "1")
    monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")


def _owner(sub: str = "user_owner"):
    from tinyassets.auth.provider import Identity

    return Identity(
        user_id=sub,
        username=sub,
        display_name=sub,
        capabilities=["write"],
        metadata={},
    )


def _seed_binding(
    base: Path,
    monkeypatch,
    *,
    owner: str = "user_owner",
    universe_id: str = "u-owner-home",
) -> Path:
    import tinyassets.daemon_server as daemon_server
    import tinyassets.provider_serving_binding as serving
    from tinyassets.provider_serving_binding import CurrentServingProviderAuthority
    from tinyassets.storage.outbound_connections import ConnectionLedger

    universe = base / universe_id
    universe.mkdir(parents=True, exist_ok=True)
    ledger = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base)
    ledger.create_connection(
        connection_id=_CONNECTION_ID,
        owner_user_id=owner,
        connection_class="http",
        scopes=("POST",),
        provider="user-defined",
        destination="bridge.example",
        credential_ref="vault://http/voice-bridge",
        connection_type="http",
        auth_scheme="bearer",
        allowed_endpoints=[
            {
                "host": "bridge.example",
                "path_template": "/v1/session",
                "methods": ["POST"],
            },
            {
                "host": "bridge.example",
                "path_template": "/v1/session-2",
                "methods": ["POST"],
            },
        ],
    )
    ledger.grant_connection(
        grant_id=_GRANT_ID,
        connection_id=_CONNECTION_ID,
        owner_user_id=owner,
        universe_id=universe_id,
    )
    ledger.configure_capability(
        connection_id=_CONNECTION_ID,
        capability_kind="realtime_voice",
        enabled=True,
        descriptor={
            "protocol": rv.VOICE_PROTOCOL,
            "session_url": _SESSION_URL,
            "service_name": "Owner Voice Bridge",
            "privacy_url": "https://bridge.example/privacy",
        },
    )
    monkeypatch.setattr(
        daemon_server,
        "get_founder_home",
        lambda _base, actor: universe_id if actor == owner else "",
    )
    monkeypatch.setattr(
        daemon_server,
        "list_universe_acl",
        lambda _base, *, universe_id: [
            {"actor_id": owner, "permission": "admin"}
        ],
    )

    def current(_base, *, universe_dir, universe_id, owner_user_id):
        if owner_user_id != owner:
            raise PermissionError("not owner")
        return CurrentServingProviderAuthority(
            provider="api_key_http:def-voice",
            access_method="api_key_http",
            connection_id=_CONNECTION_ID,
            grant_id=_GRANT_ID,
        )

    monkeypatch.setattr(serving, "resolve_current_serving_provider_authority", current)
    return universe


class _FakeProxy:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def request(self, verb: str, request: dict[str, Any]) -> Any:
        self.calls.append((verb, request))
        return self.response

    def close(self) -> None:
        self.closed = True


def _proxy_factory(proxy: _FakeProxy):
    def factory(_universe: Path, _owner_id: str, _binding: rv.VoiceBinding):
        return proxy

    return factory


def _drive(
    body: dict,
    *,
    identity=None,
    origin: str = "https://tinyassets.test",
) -> tuple[int, dict, dict]:
    from starlette.requests import Request

    from tinyassets.auth.middleware import identity_context
    from tinyassets.onboarding import onboarding_routes

    route = next(r for r in onboarding_routes() if r.path == "/app/voice/session")
    raw = json.dumps(body).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(raw)).encode()),
        (b"host", b"tinyassets.test"),
    ]
    if origin:
        headers.append((b"origin", origin.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/app/voice/session",
        "headers": headers,
        "query_string": b"",
    }

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    async def run():
        request = Request(scope, receive)
        with identity_context(identity):
            return await route.endpoint(request)

    response = asyncio.run(run())
    return response.status_code, json.loads(response.body or b"{}"), dict(response.headers)


def _drive_status(*, identity=None) -> tuple[int, dict, dict]:
    from starlette.requests import Request

    from tinyassets.auth.middleware import identity_context
    from tinyassets.onboarding import onboarding_routes

    route = next(r for r in onboarding_routes() if r.path == "/app/voice/status")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/app/voice/status",
            "headers": [(b"host", b"tinyassets.test")],
            "query_string": b"",
        }
    )

    async def run():
        with identity_context(identity):
            return await route.endpoint(request)

    response = asyncio.run(run())
    return response.status_code, json.loads(response.body or b"{}"), dict(response.headers)


def test_voice_transport_uses_only_the_generic_outbound_gate(monkeypatch):
    for app_flag, api_flag, outbound_flag, expected in (
        ("", "", "", False),
        ("", "", "1", True),
        ("0", "0", "1", True),
        ("1", "1", "", False),
        ("yes", "true", "on", True),
    ):
        monkeypatch.setenv("TINYASSETS_REALTIME_VOICE_ENABLED", app_flag)
        monkeypatch.setenv("TINYASSETS_ALLOW_REALTIME_VOICE_API", api_flag)
        monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", outbound_flag)
        assert rv.realtime_voice_enabled() is expected


def test_there_is_no_per_user_voice_rate_limiter():
    """The owner is never rate-limited for talking to their own universe.

    ``VOICE_SESSIONS_PER_WINDOW = 10`` and ``VOICE_STATUS_CHECKS_PER_WINDOW = 60``
    answered 429 to the authenticated account holder. Founder, 2026-09-30: an
    account has exactly two limits, cloud bytes and concurrent agent seats, and
    neither is a count of microphone presses. The limiters, their buckets and
    their window constants are all gone.
    """
    for gone in (
        "VOICE_SESSIONS_PER_WINDOW",
        "VOICE_SESSION_WINDOW_SECONDS",
        "VOICE_STATUS_CHECKS_PER_WINDOW",
        "VOICE_STATUS_WINDOW_SECONDS",
        "allow_voice_session",
        "allow_voice_status",
        "_session_buckets",
        "_status_buckets",
    ):
        assert not hasattr(rv, gone), f"{gone} came back"
    # The provider's own session length is not an account limit and stays.
    assert rv.VOICE_SESSION_MAX_SECONDS == 30 * 60


def test_public_config_contains_only_protocol_and_limits(monkeypatch):
    _enable(monkeypatch)
    config = rv.public_voice_config()
    assert config == {
        "enabled": True,
        "protocol": rv.VOICE_PROTOCOL,
        "disclosure_version": 3,
        "max_session_seconds": 1800,
    }


def test_session_policy_requires_only_the_converse_tool():
    request = rv.session_request(_OFFER_SDP)
    assert request["protocol"] == rv.VOICE_PROTOCOL
    assert request["offer_sdp"] == _OFFER_SDP
    session = request["session"]
    assert session["tool"]["name"] == "converse"
    assert session["turn_detection"] == {
        "mode": "semantic",
        "eagerness": "medium",
        "interrupt_output": True,
    }
    assert session["output"] == {
        "mode": "audio",
        "source": "tool_result",
        "verbatim": True,
    }


def test_capability_is_unpowered_without_current_provider(monkeypatch, tmp_path):
    _enable(monkeypatch)
    universe = tmp_path / "u-owner"
    universe.mkdir()
    import tinyassets.daemon_server as daemon_server
    import tinyassets.provider_serving_binding as serving

    monkeypatch.setattr(daemon_server, "get_founder_home", lambda *_args: "u-owner")
    monkeypatch.setattr(
        daemon_server,
        "list_universe_acl",
        lambda *_args, **_kwargs: [{"actor_id": "owner", "permission": "admin"}],
    )
    monkeypatch.setattr(
        serving,
        "resolve_current_serving_provider_authority",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            serving.NoServingProvider("not configured")
        ),
    )
    assert rv.voice_capability(universe, "owner") == {
        "available": False,
        "state": "unpowered",
        "reason": "provider_not_configured",
        "remediation": "existing_connection_surface",
    }


def test_unpowered_session_returns_stable_closed_error(monkeypatch, tmp_path):
    _enable(monkeypatch)
    universe = tmp_path / "u-owner"
    universe.mkdir()
    import tinyassets.daemon_server as daemon_server
    import tinyassets.provider_serving_binding as serving

    monkeypatch.setattr(daemon_server, "get_founder_home", lambda *_args: "u-owner")
    monkeypatch.setattr(
        daemon_server,
        "list_universe_acl",
        lambda *_args, **_kwargs: [{"actor_id": "owner", "permission": "admin"}],
    )
    monkeypatch.setattr(
        serving,
        "resolve_current_serving_provider_authority",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            serving.NoServingProvider("not configured")
        ),
    )

    with pytest.raises(rv.RealtimeVoiceError) as caught:
        asyncio.run(rv.create_voice_session(universe, "owner", _OFFER_SDP))

    assert caught.value.code == "provider_not_configured"
    assert caught.value.status == 409


def test_capability_is_ready_only_for_exact_owner_and_universe(monkeypatch, tmp_path):
    _enable(monkeypatch)
    universe = _seed_binding(tmp_path, monkeypatch)
    capability = rv.voice_capability(universe, "user_owner")
    disclosure_id = sha256(
        "\0".join(
            (
                _CONNECTION_ID,
                rv.VOICE_PROTOCOL,
                _SESSION_URL,
                "Owner Voice Bridge",
                "https://bridge.example/privacy",
            )
        ).encode("utf-8")
    ).hexdigest()
    assert capability == {
        "available": True,
        "state": "ready",
        "remediation": "none",
        "resource": "user_bound_voice_connection",
        "disclosure_id": disclosure_id,
        "service_name": "Owner Voice Bridge",
        "privacy_url": "https://bridge.example/privacy",
    }
    assert len(capability["disclosure_id"]) == 64
    assert capability["disclosure_id"].isalnum()
    assert rv.voice_capability(universe, "someone-else")["available"] is False


def test_subscription_provider_routes_to_existing_connection_surface(
    monkeypatch, tmp_path
):
    _enable(monkeypatch)
    universe = _seed_binding(tmp_path, monkeypatch)
    import tinyassets.provider_serving_binding as serving

    monkeypatch.setattr(
        serving,
        "resolve_current_serving_provider_authority",
        lambda *_args, **_kwargs: serving.CurrentServingProviderAuthority(
            provider="codex", access_method="subscription_cli"
        ),
    )

    assert rv.voice_capability(universe, "user_owner") == {
        "available": False,
        "state": "incompatible",
        "reason": "provider_voice_unsupported",
        "remediation": "existing_connection_surface",
    }


def test_unsupported_provider_remains_actionable_without_legacy_voice_flags(
    monkeypatch, tmp_path
):
    universe = _seed_binding(tmp_path, monkeypatch)
    import tinyassets.provider_serving_binding as serving

    monkeypatch.setattr(
        serving,
        "resolve_current_serving_provider_authority",
        lambda *_args, **_kwargs: serving.CurrentServingProviderAuthority(
            provider="codex", access_method="subscription_cli"
        ),
    )
    monkeypatch.delenv("TINYASSETS_REALTIME_VOICE_ENABLED", raising=False)
    monkeypatch.delenv("TINYASSETS_ALLOW_REALTIME_VOICE_API", raising=False)

    assert rv.voice_capability(universe, "user_owner") == {
        "available": False,
        "state": "incompatible",
        "reason": "provider_voice_unsupported",
        "remediation": "existing_connection_surface",
    }


def test_ready_capability_is_not_hidden_by_legacy_voice_flags(monkeypatch, tmp_path):
    universe = _seed_binding(tmp_path, monkeypatch)
    monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")
    monkeypatch.delenv("TINYASSETS_REALTIME_VOICE_ENABLED", raising=False)
    monkeypatch.delenv("TINYASSETS_ALLOW_REALTIME_VOICE_API", raising=False)

    assert rv.voice_capability(universe, "user_owner") == {
        "available": True,
        "state": "ready",
        "remediation": "none",
        "resource": "user_bound_voice_connection",
        "disclosure_id": sha256(
            (
                f"{_CONNECTION_ID}\0tinyassets.voice.v1\0{_SESSION_URL}\0"
                "Owner Voice Bridge\0https://bridge.example/privacy"
            ).encode("utf-8")
        ).hexdigest(),
        "service_name": "Owner Voice Bridge",
        "privacy_url": "https://bridge.example/privacy",
    }


def test_capability_revoke_is_seen_on_next_status_check(monkeypatch, tmp_path):
    _enable(monkeypatch)
    universe = _seed_binding(tmp_path, monkeypatch)
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    ledger.configure_capability(
        connection_id=_CONNECTION_ID,
        capability_kind="realtime_voice",
        enabled=False,
    )

    assert rv.voice_capability(universe, "user_owner") == {
        "available": False,
        "state": "incompatible",
        "reason": "capability_not_declared",
        "remediation": "existing_connection_surface",
    }


def test_grant_revoke_fails_closed_before_session(monkeypatch, tmp_path):
    _enable(monkeypatch)
    universe = _seed_binding(tmp_path, monkeypatch)
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    ledger.revoke_grant(_GRANT_ID)
    proxy = _FakeProxy({})
    with pytest.raises(rv.RealtimeVoiceError) as caught:
        asyncio.run(
            rv.create_voice_session(
                universe,
                "user_owner",
                _OFFER_SDP,
                proxy_factory=_proxy_factory(proxy),
            )
        )
    assert caught.value.code == "voice_authority_invalid"
    assert proxy.calls == []


def test_capability_disclosure_changes_when_bound_service_changes(monkeypatch, tmp_path):
    _enable(monkeypatch)
    universe = _seed_binding(tmp_path, monkeypatch)
    first = rv.voice_capability(universe, "user_owner")["disclosure_id"]
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    ledger.configure_capability(
        connection_id=_CONNECTION_ID,
        capability_kind="realtime_voice",
        enabled=True,
        descriptor={
            "protocol": rv.VOICE_PROTOCOL,
            "session_url": "https://bridge.example/v1/session-2",
            "service_name": "Replacement Voice Bridge",
            "privacy_url": "https://bridge.example/privacy",
        },
    )
    second = rv.voice_capability(universe, "user_owner")["disclosure_id"]
    assert second != first


def test_capability_refuses_session_url_outside_connection_policy(monkeypatch, tmp_path):
    _enable(monkeypatch)
    universe = _seed_binding(tmp_path, monkeypatch)
    with sqlite3.connect(tmp_path / "outbound.db") as raw:
        raw.execute(
            "UPDATE connection_capabilities SET descriptor_json = ?",
            (json.dumps({
                "protocol": rv.VOICE_PROTOCOL,
                "session_url": "https://bridge.example/not-allowed",
                "service_name": "Owner Voice Bridge",
            }),),
        )
    assert rv.voice_capability(universe, "user_owner") == {
        "available": False,
        "state": "incompatible",
        "reason": "voice_capability_invalid",
        "remediation": "existing_connection_surface",
    }


def test_invalid_stored_capability_fails_closed(monkeypatch, tmp_path):
    _enable(monkeypatch)
    universe = _seed_binding(tmp_path, monkeypatch)
    with sqlite3.connect(tmp_path / "outbound.db") as raw:
        raw.execute(
            "UPDATE connection_capabilities SET descriptor_json = '{broken'"
        )
    capability = rv.voice_capability(universe, "user_owner")
    assert capability["available"] is False
    assert capability["reason"] == "voice_capability_invalid"


def test_session_uses_generic_scoped_proxy_and_returns_only_answer_fields(
    monkeypatch, tmp_path
):
    _enable(monkeypatch)
    monkeypatch.delenv("TINYASSETS_REALTIME_VOICE_ENABLED", raising=False)
    monkeypatch.delenv("TINYASSETS_ALLOW_REALTIME_VOICE_API", raising=False)
    universe = _seed_binding(tmp_path, monkeypatch)
    proxy = _FakeProxy(
        {
            "status": 200,
            "body": {
                "protocol": rv.VOICE_PROTOCOL,
                "answer_sdp": _ANSWER_SDP,
                "expires_at": 1234567890,
                "max_session_seconds": 9999,
                "internal": "must-not-cross-boundary",
            },
        }
    )
    result = asyncio.run(
        rv.create_voice_session(
            universe,
            "user_owner",
            _OFFER_SDP,
            proxy_factory=_proxy_factory(proxy),
        )
    )
    assert proxy.calls == [
        (
            "POST",
            {
                "url": _SESSION_URL,
                "headers": {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                "body": rv.session_request(_OFFER_SDP),
            },
        )
    ]
    assert proxy.closed is True
    assert result == {
        "protocol": rv.VOICE_PROTOCOL,
        "answer_sdp": _ANSWER_SDP,
        "expires_at": 1234567890,
        "max_session_seconds": 1800,
    }
    assert "must-not-cross-boundary" not in json.dumps(result)


def test_session_offloads_binding_and_ledger_preflight(monkeypatch, tmp_path):
    _enable(monkeypatch)
    universe = _seed_binding(tmp_path, monkeypatch)
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []
    original_resolve = rv._resolve_voice_binding

    def resolve(path, owner_id):
        worker_threads.append(threading.get_ident())
        return original_resolve(path, owner_id)

    monkeypatch.setattr(rv, "_resolve_voice_binding", resolve)
    proxy = _FakeProxy(
        {
            "status": 200,
            "body": {
                "protocol": rv.VOICE_PROTOCOL,
                "answer_sdp": _ANSWER_SDP,
                "max_session_seconds": 300,
            },
        }
    )
    asyncio.run(
        rv.create_voice_session(
            universe,
            "user_owner",
            _OFFER_SDP,
            proxy_factory=_proxy_factory(proxy),
        )
    )
    assert len(worker_threads) == 1
    assert all(thread_id != caller_thread for thread_id in worker_threads)


def test_session_refuses_invalid_offer_before_opening_proxy(monkeypatch, tmp_path):
    _enable(monkeypatch)
    universe = _seed_binding(tmp_path, monkeypatch)
    proxy = _FakeProxy({})
    with pytest.raises(rv.RealtimeVoiceError) as caught:
        asyncio.run(
            rv.create_voice_session(
                universe,
                "user_owner",
                "not-sdp",
                proxy_factory=_proxy_factory(proxy),
            )
        )
    assert (caught.value.code, caught.value.status) == (
        "voice_session_offer_invalid",
        400,
    )
    assert proxy.calls == []


@pytest.mark.parametrize(
    ("upstream", "code", "status"),
    [
        (401, "voice_resource_rejected", 409),
        (403, "voice_resource_rejected", 409),
        (429, "voice_resource_rate_limited", 503),
        (500, "voice_resource_failed", 502),
    ],
)
def test_upstream_statuses_become_stable_secret_free_errors(
    monkeypatch, tmp_path, upstream: int, code: str, status: int
):
    _enable(monkeypatch)
    universe = _seed_binding(tmp_path, monkeypatch)
    proxy = _FakeProxy({"status": upstream, "body": {"error": "hidden-secret"}})
    with pytest.raises(rv.RealtimeVoiceError) as caught:
        asyncio.run(
            rv.create_voice_session(
                universe,
                "user_owner",
                _OFFER_SDP,
                proxy_factory=_proxy_factory(proxy),
            )
        )
    assert (caught.value.code, caught.value.status) == (code, status)
    assert "hidden-secret" not in repr(caught.value)


def test_route_is_disabled_before_home_or_network(monkeypatch):
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.delenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", raising=False)
    status, body, headers = _drive({}, identity=_owner())
    assert (status, body) == (404, {"error": "voice_disabled"})
    assert headers["cache-control"] == "no-store"


def test_route_requires_identity_and_same_origin(monkeypatch):
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    _enable(monkeypatch)
    assert _drive({})[:2] == (401, {"error": "authentication_required"})
    assert _drive({}, identity=_owner(), origin="https://evil.example")[:2] == (
        403,
        {"error": "same_origin_required"},
    )


def test_route_rejects_caller_selected_universe_before_session(monkeypatch):
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    _enable(monkeypatch)
    status, body, _headers = _drive(
        {"universe_id": "u-someone-else"}, identity=_owner()
    )
    assert (status, body) == (
        400,
        {"error": "voice_session_fields_not_allowed"},
    )


def test_route_scopes_distinct_identities_to_distinct_home_universes(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _enable(monkeypatch)
    import tinyassets.onboarding as onboarding

    monkeypatch.setattr(onboarding, "_read_home", lambda identity: f"u-{identity.user_id}")
    seen = []

    async def create(path, owner_id, offer_sdp):
        seen.append((path, owner_id, offer_sdp))
        return {
            "answer_sdp": _ANSWER_SDP,
            "expires_at": 123,
            "protocol": rv.VOICE_PROTOCOL,
            "max_session_seconds": 1800,
        }

    monkeypatch.setattr(rv, "create_voice_session", create)
    assert _drive({"offer_sdp": _OFFER_SDP}, identity=_owner("owner-a"))[0] == 200
    assert _drive({"offer_sdp": _OFFER_SDP}, identity=_owner("owner-b"))[0] == 200
    assert seen == [
        ((tmp_path / "u-owner-a").resolve(), "owner-a", _OFFER_SDP),
        ((tmp_path / "u-owner-b").resolve(), "owner-b", _OFFER_SDP),
    ]


def test_the_session_route_answers_no_rate_refusal(monkeypatch):
    """Twenty back-to-back session requests, twice the old window of 10.

    None is a 429. The route reaches the real home/authority checks every time,
    which is what a removed rate gate has to mean: not "a bigger number" but
    "the request is evaluated on its merits".
    """
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    _enable(monkeypatch)

    for _ in range(20):
        status, payload = _drive({"offer_sdp": _OFFER_SDP}, identity=_owner())[:2]
        assert status != 429, payload
        assert "rate_limited" not in str(payload)


def test_route_resolves_home_owner_and_returns_no_store(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _enable(monkeypatch)
    monkeypatch.delenv("TINYASSETS_REALTIME_VOICE_ENABLED", raising=False)
    monkeypatch.delenv("TINYASSETS_ALLOW_REALTIME_VOICE_API", raising=False)
    import tinyassets.onboarding as onboarding

    monkeypatch.setattr(onboarding, "_read_home", lambda _identity: "u-owner-home")
    seen = {}

    async def create(path, owner_id, offer_sdp):
        seen.update(path=path, owner_id=owner_id, offer_sdp=offer_sdp)
        return {
            "answer_sdp": _ANSWER_SDP,
            "expires_at": 123,
            "protocol": rv.VOICE_PROTOCOL,
            "max_session_seconds": 1800,
        }

    monkeypatch.setattr(rv, "create_voice_session", create)
    status, body, headers = _drive(
        {"offer_sdp": _OFFER_SDP}, identity=_owner("user_exact")
    )
    assert status == 200
    assert body["answer_sdp"] == _ANSWER_SDP
    assert seen == {
        "path": (tmp_path / "u-owner-home").resolve(),
        "owner_id": "user_exact",
        "offer_sdp": _OFFER_SDP,
    }
    assert headers["cache-control"] == "no-store"


def test_route_missing_home_is_actionable(monkeypatch):
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    _enable(monkeypatch)
    import tinyassets.onboarding as onboarding

    monkeypatch.setattr(onboarding, "_read_home", lambda _identity: "")
    assert _drive({"offer_sdp": _OFFER_SDP}, identity=_owner())[:2] == (
        409,
        {"error": "no_home_universe"},
    )


def test_status_route_is_authenticated_secret_free_and_unpowered(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _enable(monkeypatch)
    import tinyassets.onboarding as onboarding

    monkeypatch.setattr(onboarding, "_read_home", lambda _identity: "u-owner-home")
    monkeypatch.setattr(
        rv,
        "voice_capability",
        lambda *_args: {
            "available": False,
            "state": "unpowered",
            "reason": "provider_not_configured",
            "remediation": "existing_connection_surface",
        },
    )
    assert _drive_status()[:2] == (401, {"error": "authentication_required"})
    status, body, headers = _drive_status(identity=_owner())
    assert status == 200
    assert body == {
        "available": False,
        "state": "unpowered",
        "reason": "provider_not_configured",
        "remediation": "existing_connection_surface",
    }
    assert headers["cache-control"] == "no-store"


def test_the_status_route_answers_no_rate_refusal(monkeypatch):
    """A hundred preflight checks, well past the old 60-per-minute window."""
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")

    for _ in range(100):
        status, payload = _drive_status(identity=_owner())[:2]
        assert status != 429, payload
        assert "rate_limited" not in str(payload)


def test_status_route_reports_ready_without_returning_connection_secret(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _enable(monkeypatch)
    _seed_binding(tmp_path, monkeypatch)
    import tinyassets.onboarding as onboarding

    monkeypatch.setattr(onboarding, "_read_home", lambda _identity: "u-owner-home")
    status, body, _headers = _drive_status(identity=_owner())
    assert status == 200
    disclosure_id = sha256(
        "\0".join(
            (
                _CONNECTION_ID,
                rv.VOICE_PROTOCOL,
                _SESSION_URL,
                "Owner Voice Bridge",
                "https://bridge.example/privacy",
            )
        ).encode("utf-8")
    ).hexdigest()
    assert body == {
        "available": True,
        "state": "ready",
        "remediation": "none",
        "resource": "user_bound_voice_connection",
        "disclosure_id": disclosure_id,
        "service_name": "Owner Voice Bridge",
        "privacy_url": "https://bridge.example/privacy",
    }
    assert len(body["disclosure_id"]) == 64
    assert "voice-bridge" not in json.dumps(body)
