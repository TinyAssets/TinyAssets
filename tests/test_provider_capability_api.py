from __future__ import annotations

import inspect
import json

from tinyassets.api import permissions
from tinyassets.api.provider_capability import configure_provider_capability
from tinyassets.provider_serving_binding import CurrentServingProviderAuthority
from tinyassets.storage.outbound_connections import ConnectionLedger


def _arrange(monkeypatch, tmp_path, *, connection_owner="founder-1"):
    universe_id = "u-home"
    (tmp_path / universe_id).mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: True)
    monkeypatch.setattr(permissions, "current_actor_id", lambda: "founder-1")

    import tinyassets.daemon_server as daemon_server
    import tinyassets.provider_serving_binding as serving

    monkeypatch.setattr(
        daemon_server, "get_founder_home", lambda _base, _actor: universe_id
    )
    monkeypatch.setattr(
        daemon_server,
        "list_universe_acl",
        lambda _base, *, universe_id: [
            {"actor_id": "founder-1", "permission": "admin"}
        ],
    )
    monkeypatch.setattr(
        serving,
        "resolve_current_serving_provider_authority",
        lambda *_args, **_kwargs: CurrentServingProviderAuthority(
            provider="api_key_http:def-1",
            access_method="api_key_http",
            connection_id="conn-voice",
            grant_id="grant-voice",
        ),
    )
    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    ledger.create_connection(
        connection_id="conn-voice",
        owner_user_id=connection_owner,
        connection_class="outbound-http",
        scopes=("POST",),
        provider="http",
        destination="bridge.example",
        credential_ref="vault://http/ref",
        connection_type="http",
        auth_scheme="bearer",
        allowed_endpoints=[{
            "host": "bridge.example",
            "path_template": "/session",
            "methods": ["POST"],
        }],
    )
    ledger.grant_connection(
        grant_id="grant-voice",
        connection_id="conn-voice",
        owner_user_id=connection_owner,
        universe_id=universe_id,
    )
    return ledger


def _payload(*, enabled=True):
    result = {"capability_kind": "realtime_voice", "enabled": enabled}
    if enabled:
        result["descriptor"] = {
            "protocol": "tinyassets.voice.v1",
            "session_url": "https://bridge.example/session",
            "service_name": "Example Voice",
            "privacy_url": "https://bridge.example/privacy",
        }
    return result


def test_configure_derives_current_connection_and_returns_no_authority_secret(
    monkeypatch, tmp_path
):
    ledger = _arrange(monkeypatch, tmp_path)

    result = configure_provider_capability(universe_id="u-home", payload=_payload())

    assert result == {
        "status": "configured",
        "capability_kind": "realtime_voice",
        "provider": "api_key_http:def-1",
        "descriptor": _payload()["descriptor"],
    }
    assert ledger.get_connection_capability(
        "conn-voice", "realtime_voice"
    ) is not None
    assert "grant" not in str(result).lower()
    assert "credential" not in str(result).lower()


def test_configure_refuses_graph_id_that_disagrees_with_derived_home(
    monkeypatch, tmp_path
):
    ledger = _arrange(monkeypatch, tmp_path)

    result = configure_provider_capability(universe_id="u-other", payload=_payload())

    assert result == {"error": "not_found", "resource": "connection"}
    assert ledger.get_connection_capability(
        "conn-voice", "realtime_voice"
    ) is None


def test_configure_requires_exact_grant_owner_even_for_admin(monkeypatch, tmp_path):
    ledger = _arrange(monkeypatch, tmp_path, connection_owner="other-founder")

    result = configure_provider_capability(universe_id="u-home", payload=_payload())

    assert result == {"error": "not_found", "resource": "connection"}
    assert ledger.get_connection_capability(
        "conn-voice", "realtime_voice"
    ) is None


def test_configure_rejects_caller_selected_authority_fields(monkeypatch, tmp_path):
    ledger = _arrange(monkeypatch, tmp_path)
    payload = _payload()
    payload["connection_id"] = "conn-other"

    result = configure_provider_capability(universe_id="u-home", payload=payload)

    assert result["error"] == "provider_capability_invalid"
    assert ledger.get_connection_capability(
        "conn-voice", "realtime_voice"
    ) is None


def test_configure_refuses_subscription_provider_without_secondary_credential_flow(
    monkeypatch, tmp_path
):
    _arrange(monkeypatch, tmp_path)
    import tinyassets.provider_serving_binding as serving

    monkeypatch.setattr(
        serving,
        "resolve_current_serving_provider_authority",
        lambda *_args, **_kwargs: CurrentServingProviderAuthority(
            provider="codex", access_method="subscription_cli"
        ),
    )

    result = configure_provider_capability(universe_id="u-home", payload=_payload())

    assert result == {
        "error": "provider_voice_unsupported",
        "resource": "provider_capability",
    }


def test_configure_revoke_is_idempotent(monkeypatch, tmp_path):
    ledger = _arrange(monkeypatch, tmp_path)
    assert configure_provider_capability(
        universe_id="u-home", payload=_payload()
    )["status"] == "configured"

    first = configure_provider_capability(
        universe_id="u-home", payload=_payload(enabled=False)
    )
    second = configure_provider_capability(
        universe_id="u-home", payload=_payload(enabled=False)
    )

    assert first["status"] == second["status"] == "revoked"
    assert ledger.get_connection_capability("conn-voice", "realtime_voice") is None


def test_write_graph_routes_without_adding_a_handle(monkeypatch, tmp_path):
    _arrange(monkeypatch, tmp_path)
    from tinyassets import universe_server
    from tinyassets.api import provider_capability

    seen = {}

    def _configure(*, universe_id, payload):
        seen.update(universe_id=universe_id, payload=payload)
        return {"status": "configured"}

    monkeypatch.setattr(provider_capability, "configure_provider_capability", _configure)
    payload = _payload()

    result = json.loads(
        universe_server.write_graph(
            target="connection",
            operation="configure_provider_capability",
            graph_id="u-home",
            payload_json=json.dumps(payload),
        )
    )

    assert result == {"status": "configured"}
    assert seen == {"universe_id": "u-home", "payload": json.dumps(payload)}
    assert "configure_provider_capability" in (inspect.getdoc(universe_server.write_graph) or "")
