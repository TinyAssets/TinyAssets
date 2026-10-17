"""Connect requests follow current universe authority, not a surviving binding row."""

from datetime import datetime, timezone

import pytest

from tests.test_open_serving_bind import _CONN_ID, _GRANT_ID, _bound_and_serving
from tinyassets.api.pending_requests import _serving_llm_bound


def _install_native_executor(monkeypatch):
    """Give source readiness owned metadata without a host-installed CLI."""
    from types import SimpleNamespace

    from tinyassets.providers.native_catalogue import NativeCatalogue, NativeModel

    class Executor:
        name = "codex"
        native_credential_service = "codex"

        def is_available(self):
            return True

        async def enumerate_models(self, *, universe_dir, credential_snapshot_dir):
            assert universe_dir.is_dir()
            assert credential_snapshot_dir.is_dir()
            return NativeCatalogue(
                (NativeModel("fixture-model", frozenset({"text"})),),
                "fixture-model", datetime.now(timezone.utc),
            )

    monkeypatch.setattr("tinyassets.external_agents.registry.PACKAGES", {})
    monkeypatch.setattr(
        "tinyassets.providers.call.get_provider_router",
        lambda: SimpleNamespace(_providers={"codex": Executor()}),
    )


@pytest.fixture
def manifest_connection(tmp_path, monkeypatch, request):
    from types import SimpleNamespace

    from tests.test_provider_serving_binding import _seed_universe
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.daemon_server import grant_universe_access, set_founder_home
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    universe, agent = _seed_universe(tmp_path)
    providers = getattr(request, "param", ("codex",))
    if "claude-code" in providers:
        from tinyassets.credential_vault import _prepare_credential_root

        # Path-backed material lives in the platform custody root outside the
        # owner's box; the record names it by its center-relative path.
        config_dir = _prepare_credential_root(universe) / "claude-config"
        config_dir.mkdir()
        (config_dir / ".credentials.json").write_text("{}", encoding="utf-8")
        write_credential_vault(universe, [
            {"credential_type": "llm_subscription", "service": "codex", "auth_json_b64": "e30="},
            {"credential_type": "llm_subscription", "service": "claude",
             "claude_config_dir": str(universe / "claude-config")},
        ], owner_user_id="owner-1", universe_id="u-owner")
    set_founder_home(tmp_path, founder_sub="owner-1", universe_id="u-owner",
                     platform_generated=True)
    grant_universe_access(tmp_path, universe_id="u-owner", actor_id="owner-1",
                          permission="admin")
    monkeypatch.setattr("tinyassets.providers.call.get_provider_router", lambda: SimpleNamespace(
        _providers={name: SimpleNamespace(is_available=lambda: True) for name in providers},
    ))
    connected = bind_serving_provider(
        base_path=tmp_path, universe_dir=universe, owner_user_id="owner-1",
        universe_id="u-owner", agent_binding_id=agent["agent_binding_id"],
        expected_revision=1, provider="codex",
        model_access={name: ModelAccess("explicit", ("",)) for name in providers},
    )
    set_serving(
        base_path=tmp_path, universe_dir=universe, owner_user_id="owner-1",
        universe_id="u-owner", agent_binding_id=agent["agent_binding_id"],
        expected_revision=connected["agent_binding"]["revision"], enabled=True,
    )
    # Readiness must be local: no catalogue, executor availability or inference.
    monkeypatch.setattr("tinyassets.providers.call.get_provider_router",
                        lambda: pytest.fail("request polling consulted the executor"))
    return tmp_path, universe, agent












def test_current_open_connection_satisfies_the_connect_request(tmp_path, monkeypatch):
    _bound_and_serving(tmp_path, monkeypatch)
    assert _serving_llm_bound(tmp_path, "u-owner", "owner-1") is True
    assert _serving_llm_bound(tmp_path, "u-owner", "someone-else") is False


@pytest.mark.parametrize("change", ["revoke_grant", "revoke_connection"])
def test_a_surviving_binding_does_not_hide_a_broken_connection_request(
    tmp_path, monkeypatch, change
):
    from tinyassets.provider_serving_binding import resolve_serving_agent_binding
    from tinyassets.storage.outbound_connections import ConnectionLedger

    _bound_and_serving(tmp_path, monkeypatch)
    assert _serving_llm_bound(tmp_path, "u-owner", "owner-1") is True
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: "owner-1"
    )
    if change == "revoke_grant":
        ledger.revoke_grant(_GRANT_ID)
    else:
        ledger.revoke_connection(_CONN_ID)
    # Reproduce the exact old premise: the serving row still exists.
    assert resolve_serving_agent_binding(
        tmp_path, universe_id="u-owner", owner_user_id="owner-1"
    )
    assert _serving_llm_bound(tmp_path, "u-owner", "owner-1") is False


def test_connect_detection_uses_explicit_universe_authority(tmp_path, monkeypatch):
    calls = []

    def resolve(base, **kwargs):
        calls.append((base, kwargs))
        return True

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.serving_connection_is_current",
        resolve,
    )
    assert _serving_llm_bound(tmp_path, "u-owner", "owner-1") is True
    assert calls == [(tmp_path, {
        "universe_dir": tmp_path / "u-owner",
        "universe_id": "u-owner",
        "owner_user_id": "owner-1",
    })]


