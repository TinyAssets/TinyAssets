"""Connect requests follow current universe authority, not a surviving binding row."""

import sqlite3
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


def test_manifest_connection_makes_only_its_owners_connect_request_optional(manifest_connection):
    from tinyassets.api.pending_requests import list_requests
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    base, _, _ = manifest_connection
    assert _serving_llm_bound(base, "u-owner", "owner-1") is True
    assert _serving_llm_bound(base, "u-owner", "someone-else") is False
    assert _serving_llm_bound(base, "u-other", "owner-1") is False
    with identity_context(Identity(user_id="owner-1", username="owner", capabilities=["read"])):
        entry = list_requests(universe_id="u-owner")["pending"][0]
        assert entry["title"] == "Connect another LLM"
        assert entry["sticky"] is False


@pytest.mark.parametrize("change", ["custody_lost", "paused"])
def test_manifest_connection_revalidates_current_state(manifest_connection, change):
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.storage import db_path

    base, universe, agent = manifest_connection
    assert _serving_llm_bound(base, "u-owner", "owner-1") is True
    if change == "custody_lost":
        write_credential_vault(universe, [], owner_user_id="owner-1", universe_id="u-owner")
    else:
        # Fixture corruption exercises a surviving enrollment, not production edits.
        with sqlite3.connect(db_path(base)) as conn:
            conn.execute("UPDATE agent_bindings SET status = 'configured' "
                         "WHERE agent_binding_id = ?",
                         (agent["agent_binding_id"],))
    assert _serving_llm_bound(base, "u-owner", "owner-1") is False


def test_manifest_connection_uses_live_source_not_saved_provider_ref(manifest_connection):
    from tinyassets.storage import db_path

    base, _, agent = manifest_connection
    with sqlite3.connect(db_path(base)) as conn:
        conn.execute("UPDATE agent_bindings SET configuration_json = '{}' "
                     "WHERE agent_binding_id = ?", (agent["agent_binding_id"],))
    assert _serving_llm_bound(base, "u-owner", "owner-1") is True


@pytest.mark.parametrize("manifest_connection", [("codex", "claude-code")], indirect=True)
def test_manifest_readiness_accepts_live_member_without_anchor(manifest_connection):
    from tinyassets.credential_vault import write_credential_vault

    base, universe, _ = manifest_connection
    write_credential_vault(universe, [
        {"credential_type": "llm_subscription", "service": "claude",
         "claude_config_dir": str(universe / "claude-config")},
    ], owner_user_id="owner-1", universe_id="u-owner")
    assert _serving_llm_bound(base, "u-owner", "owner-1") is True
    write_credential_vault(universe, [], owner_user_id="owner-1", universe_id="u-owner")
    assert _serving_llm_bound(base, "u-owner", "owner-1") is False


def test_manifest_readiness_resolves_live_connected_source(manifest_connection):
    from tinyassets.provider_serving_binding import resolve_current_serving_provider_authority

    base, universe, _ = manifest_connection
    assert _serving_llm_bound(base, "u-owner", "owner-1") is True
    authority = resolve_current_serving_provider_authority(
        base, universe_dir=universe, universe_id="u-owner", owner_user_id="owner-1",
    )
    assert authority.provider == "codex"
    assert authority.access_method == "subscription_cli"


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


def test_subscription_custody_loss_restores_the_actual_request_rail(tmp_path, monkeypatch):
    from tests.test_provider_serving_binding import _seed_universe
    from tinyassets.api.pending_requests import list_requests
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.daemon_server import grant_universe_access, set_founder_home
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    udir, agent = _seed_universe(tmp_path)
    set_founder_home(
        tmp_path, founder_sub="owner-1", universe_id="u-owner", platform_generated=True,
    )
    grant_universe_access(
        tmp_path, universe_id="u-owner", actor_id="owner-1",
        permission="admin", granted_by="owner-1",
    )
    _install_native_executor(monkeypatch)
    connected = bind_serving_provider(
        base_path=tmp_path, universe_dir=udir, owner_user_id="owner-1",
        universe_id="u-owner", agent_binding_id=agent["agent_binding_id"],
        expected_revision=1, provider="codex",
    )
    set_serving(
        base_path=tmp_path, universe_dir=udir, owner_user_id="owner-1",
        universe_id="u-owner", agent_binding_id=agent["agent_binding_id"],
        expected_revision=connected["agent_binding"]["revision"], enabled=True,
    )
    identity = Identity(user_id="owner-1", username="owner-1", capabilities=["read"])
    with identity_context(identity):
        assert list_requests(universe_id="u-owner")["pending"][0]["sticky"] is False
        # Test-only vault contents; no real user credential or provider call.
        write_credential_vault(udir, [], owner_user_id="owner-1", universe_id="u-owner")
        rail = list_requests(universe_id="u-owner")
        again = list_requests(universe_id="u-owner")
    assert rail["pending"][0]["request_id"] == "sys_connect_llm"
    assert rail["pending"][0]["sticky"] is True
    assert rail["pending"] == again["pending"], "polling must not create duplicate asks"
