"""Connect in the app must make the universe SERVE, not just hold a credential.

Live phone test 2026-08-21: the OpenAI credential was deposited, yet every turn
still failed with "exactly one founder serving binding is required". These run
the REAL custom-agent + serving modules against a temp base path (same chain
the served-router tests use) — no mocks of the authority layer.
"""

from __future__ import annotations

from types import SimpleNamespace

from tinyassets.onboarding import serving as sv


def _grant_admin(tmp_path, owner="owner-1", uid="u-owner"):
    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(
        tmp_path, universe_id=uid, actor_id=owner, permission="admin", granted_by=owner
    )


def _seed(tmp_path, service="codex", owner="owner-1", uid="u-owner", admin=True):
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.daemon_server import set_founder_home

    udir = tmp_path / uid
    udir.mkdir(exist_ok=True)
    if admin:
        _grant_admin(tmp_path, owner, uid)
    set_founder_home(tmp_path, founder_sub=owner, universe_id=uid,
                     platform_generated=True)
    cred = (
        {"credential_type": "llm_subscription", "service": "codex", "auth_json_b64": "e30="}
        if service == "codex"
        else {"credential_type": "llm_subscription", "service": "claude", "oauth_token": "sk-ant-x"}
    )
    write_credential_vault(udir, [cred], owner_user_id=owner, universe_id=uid)
    return udir


def _serving_binding(tmp_path, uid="u-owner", owner="owner-1"):
    from tinyassets.provider_serving_binding import resolve_serving_agent_binding

    return resolve_serving_agent_binding(tmp_path, universe_id=uid, owner_user_id=owner)


def _manifest_setup(tmp_path, monkeypatch, *, custom=False, http=False):
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.daemon_server import set_founder_home
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving
    from tinyassets.providers.router import ProviderRouter

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    router = ProviderRouter({name: SimpleNamespace(is_available=lambda: True)
                             for name in ("codex", "claude-code")})
    monkeypatch.setattr("tinyassets.providers.call.get_provider_router", lambda: router)
    universe = _seed(tmp_path)
    set_founder_home(
        tmp_path, founder_sub="owner-1", universe_id="u-owner", platform_generated=True,
    )
    credentials = [
        {"credential_type": "llm_subscription", "service": "codex", "auth_json_b64": "e30="},
        {"credential_type": "llm_subscription", "service": "claude",
         "oauth_token": "sk-ant-fixture"},
    ]
    write_credential_vault(universe, credentials, owner_user_id="owner-1", universe_id="u-owner")
    if custom:
        from tinyassets.commons_catalog import create_binding, publish_definition

        definition = publish_definition(
            tmp_path, author_id="owner-1",
            payload={**sv._PLATFORM_DEFINITION, "name": "Owner's own design"},
        )
        first = create_binding(
            tmp_path, universe_id="u-owner", created_by="owner-1",
            definition_id=definition["agent_definition_id"],
            payload={**sv._BINDING_PAYLOAD, "persona": "Keep my exact content"},
        )
    else:
        first = sv.ensure_founder_serving(
            base_path=tmp_path, universe_dir=universe, owner_user_id="owner-1",
            universe_id="u-owner", service="codex",
        )
        assert first["status"] == "serving", first
    access = {
        "codex": ModelAccess("discovered"),
        "claude-code": ModelAccess("explicit", ("", "sonnet"), (("prompt_tokens", 17),)),
    }
    if http:
        from tinyassets.providers.definition import register_definition
        from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger

        ledger = ConnectionLedger(
            tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
            verify_authenticated_principal=lambda: "owner-1",
        )
        connection_id, grant_id = "http_" + "c" * 32, "http_grant_" + "d" * 32
        ledger.create_connection(
            connection_id=connection_id, owner_user_id="owner-1", connection_class="http",
            connection_type="http", auth_scheme="bearer", scopes=("http",), provider="http",
            destination="compute:fixture", credential_ref="vault://http/compute:fixture",
            allowed_endpoints=[{"host": "api.example.com", "methods": ["POST"],
                                "path_template": "/v1/chat/completions"}],
        )
        ledger.grant_connection(
            grant_id=grant_id, connection_id=connection_id, owner_user_id="owner-1",
            universe_id="u-owner", unprompted_action_cap=ActionCap("http_requests", 10, "requests"),
        )
        definition = register_definition(
            universe_id="u-owner", owner_user_id="owner-1", access_method="api_key_http",
            protocol="openai_chat", model="opaque-fixture-model", ref=grant_id,
        )
        access[definition.id] = ModelAccess("discovered")
    manifest = bind_serving_provider(
        base_path=tmp_path, universe_dir=universe, owner_user_id="owner-1",
        universe_id="u-owner", agent_binding_id=first["agent_binding_id"],
        expected_revision=first["revision"], provider="codex",
        model_access=access,
    )
    bound = manifest["agent_binding"]
    set_serving(
        base_path=tmp_path, universe_dir=universe, owner_user_id="owner-1",
        universe_id="u-owner", agent_binding_id=bound["agent_binding_id"],
        expected_revision=bound["revision"], enabled=True,
    )
    before = load_provider_assignment(tmp_path, universe_id="u-owner")
    return universe, credentials, first, before












def test_anonymous_or_missing_universe_is_held_not_raised(tmp_path):
    assert (
        sv.ensure_founder_serving(
            base_path=tmp_path,
            universe_dir=tmp_path,
            owner_user_id="anonymous",
            universe_id="u-owner",
            service="codex",
        )["status"]
        == "held"
    )
    # A name that is neither alias nor registered connection is HELD, but for a
    # real authority reason -- not because the platform keeps a vendor
    # allowlist. It used to answer `unsupported_service` for anything but
    # claude/codex, which refused every user with their own endpoint (founder,
    # 2026-09-03: "we shouldnt have a chatgpt spacific path").
    held = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=tmp_path,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="gemini",
    )
    assert held["status"] == "held"
    assert held["reason"] != "unsupported_service", held
    assert held.get("detail"), "a refusal has to say why"


def test_an_unnamed_service_is_held_rather_than_guessed(tmp_path):
    """Opening the name up does not mean accepting an empty one."""
    held = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=tmp_path,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="   ",
    )
    assert held == {"status": "held", "reason": "no_service_named"}
















