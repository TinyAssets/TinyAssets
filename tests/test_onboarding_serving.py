"""Connect in the app must make the universe SERVE, not just hold a credential.

Live phone test 2026-08-21: the OpenAI credential was deposited, yet every turn
still failed with "exactly one founder serving binding is required". These run
the REAL custom-agent + serving modules against a temp base path (same chain
the served-router tests use) — no mocks of the authority layer.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from tinyassets.onboarding import serving as sv


def _grant_admin(tmp_path, owner="owner-1", uid="u-owner"):
    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(
        tmp_path, universe_id=uid, actor_id=owner, permission="admin", granted_by=owner
    )


def _seed(tmp_path, service="codex", owner="owner-1", uid="u-owner", admin=True):
    from tinyassets.credential_vault import write_credential_vault

    udir = tmp_path / uid
    udir.mkdir(exist_ok=True)
    if admin:
        _grant_admin(tmp_path, owner, uid)
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
    monkeypatch.setenv("TINYASSETS_ALLOW_CLAUDE_SERVING", "1")
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
        from tinyassets.custom_agents import create_binding, publish_definition

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


@pytest.mark.parametrize("service", ["codex", "claude"])
@pytest.mark.parametrize("rotate", [False, True])
def test_reconnect_preserves_accepted_models_and_root(tmp_path, monkeypatch, service, rotate):
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.custom_agents import get_binding
    from tinyassets.provider_assignment import load_provider_assignment

    universe, credentials, first, before = _manifest_setup(tmp_path, monkeypatch)
    if rotate:
        # Synthetic owner-supplied credential only; no provider or network call.
        if service == "claude":
            credentials[1]["oauth_token"] = "sk-ant-replacement-fixture"
        else:
            credentials[0]["auth_json_b64"] = base64.b64encode(
                b'{"tokens":{"access_token":"replacement-fixture"}}'
            ).decode()
        write_credential_vault(
            universe, credentials, owner_user_id="owner-1", universe_id="u-owner",
        )
    result = sv.ensure_founder_serving(
        base_path=tmp_path, universe_dir=universe, owner_user_id="owner-1",
        universe_id="u-owner", service=service,
    )
    assert result["status"] == "serving", result
    after = load_provider_assignment(tmp_path, universe_id="u-owner")
    assert after.provider == before.provider
    assert after.manifest_digest
    assert {m.provider: m.access for m in after.candidates} == {
        m.provider: m.access for m in before.candidates
    }
    assert result["agent_binding_id"] == first["agent_binding_id"]
    current = get_binding(tmp_path, universe_id="u-owner", binding_id=first["agent_binding_id"])
    assert current["status"] == "serving"
    assert after.generation == before.generation + int(rotate)
    assert result["replayed"] is (not rotate)


@pytest.mark.parametrize("failure", [
    "unrelated_credential_removed", "collaborator_edited", "ambiguous",
    "home_changed", "admin_revoked", "unaccepted", "failed", "pending", "host_opt_out",
    "last_writer_changed",
])
def test_manifest_reconnect_holds_without_mutation(tmp_path, monkeypatch, failure):
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.custom_agents import create_binding, get_binding, list_bindings, update_binding
    from tinyassets.daemon_server import revoke_universe_access, set_founder_home
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    universe, credentials, first, assignment = _manifest_setup(tmp_path, monkeypatch)
    current = get_binding(tmp_path, universe_id="u-owner", binding_id=first["agent_binding_id"])
    service = "codex"
    if failure == "unrelated_credential_removed":
        write_credential_vault(
            universe, [], owner_user_id="owner-1", universe_id="u-owner",
        )
        write_credential_vault(
            universe, credentials[:1], owner_user_id="owner-1", universe_id="u-owner",
        )
    elif failure == "collaborator_edited":
        update_binding(
            tmp_path, universe_id="u-owner", binding_id=current["agent_binding_id"],
            expected_revision=current["revision"], updated_by="collaborator",
            payload={"schema_version": 1, "role": "writer", "name": "Changed by collaborator"},
        )
    elif failure == "ambiguous":
        other = create_binding(
            tmp_path, universe_id="u-owner", created_by="owner-1",
            definition_id=current["agent_definition_id"], payload=sv._BINDING_PAYLOAD,
        )
        with SQLiteProviderWorkAuthorityStore(tmp_path).connection() as conn:
            conn.execute("UPDATE agent_bindings SET status='serving' WHERE agent_binding_id=?",
                         (other["agent_binding_id"],))
            conn.commit()
    elif failure == "home_changed":
        set_founder_home(
            tmp_path, founder_sub="owner-1", universe_id="another-home", platform_generated=True,
        )
    elif failure == "admin_revoked":
        revoke_universe_access(tmp_path, universe_id="u-owner", actor_id="owner-1")
    elif failure == "last_writer_changed":
        with SQLiteProviderWorkAuthorityStore(tmp_path).connection() as conn:
            conn.execute("UPDATE agent_bindings SET updated_by='collaborator' "
                         "WHERE agent_binding_id=?", (current["agent_binding_id"],))
            conn.commit()
    elif failure == "unaccepted":
        service = "unaccepted-connection"
    elif failure in {"pending", "failed"}:
        with SQLiteProviderWorkAuthorityStore(tmp_path).connection() as conn:
            conn.execute("UPDATE provider_assignments SET state=? WHERE universe_id=?",
                         (failure, "u-owner"))
            conn.commit()
    elif failure == "host_opt_out":
        monkeypatch.delenv("TINYASSETS_ALLOW_CLAUDE_SERVING")
    before = load_provider_assignment(tmp_path, universe_id="u-owner")
    bindings_before = list_bindings(tmp_path, universe_id="u-owner", limit=100)
    result = sv.ensure_founder_serving(
        base_path=tmp_path, universe_dir=universe, owner_user_id="owner-1",
        universe_id="u-owner", service=service,
    )
    assert result["status"] == "held", result
    assert load_provider_assignment(tmp_path, universe_id="u-owner") == before
    assert list_bindings(tmp_path, universe_id="u-owner", limit=100) == bindings_before


@pytest.mark.parametrize("http", [False, True])
@pytest.mark.parametrize("configured", [False, True])
def test_reconnect_preserves_custom_agent_preferences_and_full_membership(
    tmp_path, monkeypatch, http, configured,
):
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.custom_agents import create_binding, get_binding
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.providers.model_preferences import ModelPreferences
    from tinyassets.storage.model_preferences import ModelPreferenceStore
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    universe, credentials, first, before = _manifest_setup(
        tmp_path, monkeypatch, custom=True, http=http,
    )
    binding = get_binding(tmp_path, universe_id="u-owner", binding_id=first["agent_binding_id"])
    store = ModelPreferenceStore(tmp_path)
    saved = store.save("owner-1", "u-owner", expected_generation=0,
                       policy=ModelPreferences("automatic", None, ()))
    # A full inventory page of newer inactive agents cannot hide the selected one.
    for index in range(101):
        create_binding(
            tmp_path, universe_id="u-owner", created_by="owner-1",
            definition_id=binding["agent_definition_id"],
            payload={**sv._BINDING_PAYLOAD, "name": f"Other agent {index}"},
        )
    if configured:
        with SQLiteProviderWorkAuthorityStore(tmp_path).connection() as conn:
            conn.execute("UPDATE agent_bindings SET status='configured' WHERE agent_binding_id=?",
                         (binding["agent_binding_id"],))
            conn.commit()
    credentials[1]["oauth_token"] = "sk-ant-renewal-fixture"
    write_credential_vault(universe, credentials, owner_user_id="owner-1", universe_id="u-owner")
    result = sv.ensure_founder_serving(
        base_path=tmp_path, universe_dir=universe, owner_user_id="owner-1",
        universe_id="u-owner", service="claude",
    )
    assert result["status"] == "serving", result
    after = load_provider_assignment(tmp_path, universe_id="u-owner")
    assert after.provider == before.provider
    assert after.generation == before.generation + 1
    assert {m.provider: m.access for m in after.candidates} == {
        m.provider: m.access for m in before.candidates
    }
    current = get_binding(tmp_path, universe_id="u-owner", binding_id=result["agent_binding_id"])
    assert current["agent_binding_id"] == binding["agent_binding_id"]
    assert current["configuration"] == binding["configuration"]
    assert current["agent_definition_id"] == binding["agent_definition_id"]
    assert store.get("owner-1", "u-owner") == saved


def test_fresh_universe_gets_definition_binding_and_serving(tmp_path):
    udir = _seed(tmp_path)
    out = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=udir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="codex",
    )
    assert out["status"] == "serving" and out["provider"] == "codex"
    # The exact check converse performs now passes.
    b = _serving_binding(tmp_path)
    assert b["agent_binding_id"] == out["agent_binding_id"]
    assert b["created_by"] == "owner-1" and b["status"] == "serving"


def test_idempotent_and_reuses_the_founders_binding(tmp_path):
    from tinyassets.custom_agents import list_bindings, list_definitions

    udir = _seed(tmp_path)
    first = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=udir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="codex",
    )
    second = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=udir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="codex",
    )
    assert second["status"] == "serving"
    assert second["agent_binding_id"] == first["agent_binding_id"]
    assert len(list_bindings(tmp_path, universe_id="u-owner", limit=100)) == 1
    # exactly one platform definition, however many founders connect
    defs = [
        d
        for d in list_definitions(tmp_path, limit=100)
        if d.get("author_id") == sv.PLATFORM_DEFINITION_AUTHOR
    ]
    assert len(defs) == 1
    assert _serving_binding(tmp_path)["status"] == "serving"


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


def test_a_provider_that_is_not_yours_is_refused_by_ownership(tmp_path, monkeypatch):
    """The gate is OWNERSHIP, which is why the name gate could go.

    Two REAL owners, two real universes, a real connection ledger. The previous
    version of this test monkeypatched `_open_serving_context` to raise, so it
    would have stayed green with the ownership comparison deleted (Codex, on the
    connect-any-llm lane).
    """
    from tinyassets.providers.definition import register_definition
    from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    udir = _seed(tmp_path)                      # owner-1 / u-owner, admin
    (tmp_path / "u-other").mkdir(exist_ok=True)

    # owner-2's connection, granted to owner-2's universe. Nothing about it is
    # owner-1's, but owner-1 names its definition.
    grant_id = "http_grant_" + "b" * 32
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: "owner-2"
    )
    ledger.create_connection(
        connection_id="conn_" + "b" * 32,
        owner_user_id="owner-2",
        connection_class="http",
        connection_type="http",
        auth_scheme="bearer",
        scopes=("http",),
        provider="http",
        destination="compute:not-yours",
        credential_ref="vault://http/compute:not-yours",
        allowed_endpoints=[
            {"host": "api.example.com", "path_template": "/v1/chat/completions",
             "methods": ["POST"]},
        ],
    )
    ledger.grant_connection(
        grant_id=grant_id,
        connection_id="conn_" + "b" * 32,
        owner_user_id="owner-2",
        universe_id="u-other",
        unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )
    # Registered inside owner-1's universe, pointing at owner-2's grant: the
    # confused-deputy shape the ownership check exists to refuse.
    foreign = register_definition(
        universe_id="u-owner", owner_user_id="owner-1", access_method="api_key_http",
        protocol="openai_chat", model="some-model", ref=grant_id,
    )

    held = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=udir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service=foreign.id,
    )
    assert held["status"] == "held", held
    assert held["reason"] == "provider_not_yours", held
    # And nothing was bound as a side effect of the refusal.
    with pytest.raises(PermissionError):
        _serving_binding(tmp_path)


def test_a_name_that_is_no_provider_here_reads_as_unknown(tmp_path, monkeypatch):
    """Missing and foreign must stay indistinguishable: `get_definition` is
    universe-scoped, so another universe's id lands on the same answer as a
    typo. That is what keeps the bind surface from being an existence oracle."""
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    udir = _seed(tmp_path)

    held = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=udir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="provdef_" + "9" * 32,
    )
    assert held["status"] == "held" and held["reason"] == "unknown_provider", held


def test_claude_serving_refusal_is_reported_not_raised(tmp_path, monkeypatch):
    """Claude serving is held by default (operator opt-in). The deposit must
    still be reported as a success with the serving outcome alongside."""
    monkeypatch.delenv("TINYASSETS_ALLOW_CLAUDE_SERVING", raising=False)
    udir = _seed(tmp_path, service="claude")
    out = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=udir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="claude",
    )
    assert out["status"] == "held" and out["reason"] in (
        "provider_authority_denied",
        "binding_invalid",
    )
    with pytest.raises(PermissionError):
        _serving_binding(tmp_path)


def test_other_users_binding_is_not_hijacked(tmp_path):
    """A collaborator's binding in the same universe is never re-pointed; the
    founder gets their own."""
    from tinyassets.custom_agents import create_binding, publish_definition

    udir = _seed(tmp_path)
    d = publish_definition(
        tmp_path,
        author_id="someone-else",
        payload={
            "schema_version": 1,
            "name": "Other",
            "description": "",
            "tags": [],
            "components": {"identity": {"kind": "soul", "config": {}}},
        },
    )
    other = create_binding(
        tmp_path,
        universe_id="u-owner",
        definition_id=d["agent_definition_id"],
        created_by="someone-else",
        payload={"schema_version": 1, "name": "Other", "role": "writer"},
    )
    out = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=udir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="codex",
    )
    assert out["status"] == "serving" and out["agent_binding_id"] != other["agent_binding_id"]


def test_no_current_admin_acl_means_zero_mutation(tmp_path):
    """Codex #1: a valid bearer with a stale home mapping but no CURRENT admin
    ACL must not create or enable anything."""
    from tinyassets.custom_agents import list_bindings, list_definitions

    udir = _seed(tmp_path, admin=False)
    out = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=udir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="codex",
    )
    assert out["status"] == "held" and out["reason"] == "provider_authority_denied"
    assert list_bindings(tmp_path, universe_id="u-owner", limit=100) == []
    assert [d for d in list_definitions(tmp_path, limit=100)] == []


def test_collaborator_tampered_binding_is_reset_not_adopted(tmp_path):
    """Codex #2 (confused deputy): a write collaborator edits the founder's
    platform binding; connect must NOT bind the founder's credential under the
    collaborator's content. The binding is reset to canonical at an exact
    revision, and a binding on a different definition is never selected."""
    from tinyassets.custom_agents import (
        get_binding,
        list_bindings,
        publish_definition,
        update_binding,
    )

    udir = _seed(tmp_path)
    first = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=udir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="codex",
    )
    bid = first["agent_binding_id"]
    # A write collaborator rewrites the founder's platform binding (allowed by
    # the custom-agent ACL model) and also swaps it onto THEIR definition.
    theirs = publish_definition(
        tmp_path,
        author_id="collab",
        payload={
            "schema_version": 1,
            "name": "Evil",
            "description": "",
            "tags": [],
            "components": {"identity": {"kind": "soul", "config": {}}},
        },
    )
    cur = get_binding(tmp_path, universe_id="u-owner", binding_id=bid)
    update_binding(
        tmp_path,
        universe_id="u-owner",
        binding_id=bid,
        expected_revision=int(cur["revision"]),
        updated_by="collab",
        payload={"schema_version": 1, "name": "Evil", "role": "writer", "instructions": "exfil"},
        definition_id=theirs["agent_definition_id"],
    )
    again = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=udir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="codex",
    )
    assert again["status"] == "serving"
    # The tampered binding (now on the collaborator's definition) was NOT
    # selected; the founder got a fresh canonical platform binding, and it is
    # the only one serving.
    assert again["agent_binding_id"] != bid
    served = _serving_binding(tmp_path)
    assert served["agent_binding_id"] == again["agent_binding_id"]
    assert served["configuration"].get("name") == "Your agent"
    assert "instructions" not in served["configuration"]
    # and the tampered one is no longer serving
    statuses = {
        b["agent_binding_id"]: b["status"]
        for b in list_bindings(tmp_path, universe_id="u-owner", limit=100)
    }
    assert statuses[bid] != "serving"


def test_drifted_config_on_platform_definition_is_reset_at_exact_revision(tmp_path):
    from tinyassets.custom_agents import get_binding, update_binding

    udir = _seed(tmp_path)
    first = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=udir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="codex",
    )
    bid = first["agent_binding_id"]
    cur = get_binding(tmp_path, universe_id="u-owner", binding_id=bid)
    update_binding(
        tmp_path,
        universe_id="u-owner",
        binding_id=bid,
        expected_revision=int(cur["revision"]),
        updated_by="collab",
        payload={"schema_version": 1, "name": "Your agent", "role": "writer", "persona": "evil"},
    )
    again = sv.ensure_founder_serving(
        base_path=tmp_path,
        universe_dir=udir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="codex",
    )
    assert again["status"] == "serving" and again["agent_binding_id"] == bid
    cfg = _serving_binding(tmp_path)["configuration"]
    assert "persona" not in cfg and cfg["name"] == "Your agent"


def test_a_binding_on_the_retired_default_definition_is_used_as_is(tmp_path):
    """The rename republished the default agent under a new id (the old one is
    immutable). A pre-rename binding is still the founder's platform binding:
    used untouched, provider_ref intact, never silently replaced by a second
    binding; the cutover migration re-points it (gpt-6-astra, C1)."""
    from tinyassets.custom_agents import create_binding, publish_definition

    _seed(tmp_path)
    retired = publish_definition(
        tmp_path,
        author_id=sv.RETIRED_PLATFORM_DEFINITION_AUTHOR,
        payload={
            "schema_version": 1, "name": "Your universe",
            "description": "The default voice of a founder's own universe.",
            "tags": ["platform", "default"],
            "components": {"identity": {"kind": "soul", "config": {}}},
        },
        idempotency_key="universe-default-v1",
    )
    legacy = create_binding(
        tmp_path, universe_id="u-owner",
        definition_id=retired["agent_definition_id"], created_by="owner-1",
        payload=dict(sv.RETIRED_BINDING_PAYLOAD),
    )
    found = sv._platform_binding(tmp_path, universe_id="u-owner", owner="owner-1")
    assert found["agent_binding_id"] == legacy["agent_binding_id"]
    assert found["revision"] == legacy["revision"]
    assert found["agent_definition_id"] == retired["agent_definition_id"]
