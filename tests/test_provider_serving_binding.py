from __future__ import annotations

import os
import sqlite3
import subprocess

import pytest


def _definition() -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": "Serving agent",
        "description": "A private serving-binding fixture",
        "tags": ["test"],
        "components": {
            "identity": {"kind": "soul", "config": {"voice": "direct"}},
        },
    }


def _binding() -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": "Serving agent",
        "role": "writer",
    }


def _seed_universe(tmp_path):
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.custom_agents import create_binding, publish_definition

    universe_dir = tmp_path / "u-owner"
    universe_dir.mkdir()
    write_credential_vault(
        universe_dir,
        [{
            "credential_type": "llm_subscription",
            "service": "codex",
            "auth_json_b64": "e30=",
        }],
        owner_user_id="owner-1",
        universe_id="u-owner",
    )
    definition = publish_definition(
        tmp_path,
        author_id="owner-1",
        payload=_definition(),
    )
    binding = create_binding(
        tmp_path,
        universe_id="u-owner",
        definition_id=definition["agent_definition_id"],
        created_by="owner-1",
        payload=_binding(),
    )
    return universe_dir, binding


def test_credential_vault_owns_opaque_generation_safe_llm_reference(tmp_path):
    from tinyassets.credential_vault import (
        adopt_llm_subscription_custody,
        write_credential_vault,
    )
    from tinyassets.storage import db_path

    universe_dir, _ = _seed_universe(tmp_path)
    conn = sqlite3.connect(db_path(tmp_path), isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    first = adopt_llm_subscription_custody(
        conn,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="codex",
    )
    conn.commit()

    assert first.reference_id.startswith("llm_credential_")
    assert first.generation == 1
    assert "auth_json" not in repr(first)
    assert "e30=" not in repr(first)

    write_credential_vault(
        universe_dir,
        [{
            "credential_type": "llm_subscription",
            "service": "codex",
            "auth_json_b64": "eyJyb3RhdGVkIjp0cnVlfQ==",
        }],
    )
    conn.execute("BEGIN IMMEDIATE")
    rotated = adopt_llm_subscription_custody(
        conn,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="codex",
    )
    conn.commit()
    conn.close()

    assert rotated.reference_id == first.reference_id
    assert rotated.generation == 2
    assert rotated.reference_digest != first.reference_digest


def test_custody_adoption_requires_server_recorded_depositor_ownership(tmp_path):
    from tinyassets.credential_vault import (
        adopt_llm_subscription_custody,
        write_credential_vault,
    )
    from tinyassets.storage import db_path

    universe_dir = tmp_path / "u-owner"
    universe_dir.mkdir()
    write_credential_vault(
        universe_dir,
        [{
            "credential_type": "llm_subscription",
            "service": "codex",
            "auth_json_b64": "e30=",
        }],
    )
    conn = sqlite3.connect(db_path(tmp_path), isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(PermissionError, match="credential owner"):
        adopt_llm_subscription_custody(
            conn,
            universe_dir=universe_dir,
            owner_user_id="collaborator-1",
            universe_id="u-owner",
            service="codex",
        )
    conn.rollback()
    conn.close()


def test_path_backed_codex_rotation_changes_custody_generation_and_digest(tmp_path):
    from tinyassets.credential_vault import (
        adopt_llm_subscription_custody,
        current_llm_subscription_custody,
        write_credential_vault,
    )
    from tinyassets.storage import db_path

    universe_dir = tmp_path / "u-owner"
    auth_home = universe_dir / "codex-auth"
    auth_home.mkdir(parents=True)
    auth_file = auth_home / "auth.json"
    auth_file.write_bytes(b'{"tokens":{"access_token":"first"}}')
    write_credential_vault(
        universe_dir,
        [{
            "credential_type": "llm_subscription",
            "service": "codex",
            "codex_home": str(auth_home),
        }],
        owner_user_id="owner-1",
        universe_id="u-owner",
    )
    conn = sqlite3.connect(db_path(tmp_path), isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    first = adopt_llm_subscription_custody(
        conn,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="codex",
    )
    conn.commit()

    auth_file.write_bytes(b'{"tokens":{"access_token":"rotated"}}')
    conn.execute("BEGIN")
    assert current_llm_subscription_custody(
        conn,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="codex",
    ) is None
    conn.rollback()

    conn.execute("BEGIN IMMEDIATE")
    rotated = adopt_llm_subscription_custody(
        conn,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        service="codex",
    )
    conn.commit()
    conn.close()

    assert rotated.reference_id == first.reference_id
    assert rotated.generation == first.generation + 1
    assert rotated.reference_digest != first.reference_digest


def test_custody_refuses_duplicate_and_path_escaping_subscription_records(tmp_path):
    from tinyassets.credential_vault import (
        adopt_llm_subscription_custody,
        write_credential_vault,
    )
    from tinyassets.storage import db_path

    universe_dir, _ = _seed_universe(tmp_path)
    write_credential_vault(
        universe_dir,
        [
            {
                "credential_type": "llm_subscription",
                "service": "codex",
                "auth_json_b64": "e30=",
            },
            {
                "credential_type": "llm_subscription",
                "service": "codex",
                "codex_home": str(tmp_path / "outside"),
            },
        ],
        owner_user_id="owner-1",
        universe_id="u-owner",
    )
    conn = sqlite3.connect(db_path(tmp_path), isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(PermissionError, match="exactly one usable"):
        adopt_llm_subscription_custody(
            conn,
            universe_dir=universe_dir,
            owner_user_id="owner-1",
            universe_id="u-owner",
            service="codex",
        )
    conn.rollback()

    write_credential_vault(
        universe_dir,
        [{
            "credential_type": "llm_subscription",
            "service": "codex",
            "codex_home": str(tmp_path / "outside"),
        }],
        owner_user_id="owner-1",
        universe_id="u-owner",
    )
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(PermissionError, match="exactly one usable"):
        adopt_llm_subscription_custody(
            conn,
            universe_dir=universe_dir,
            owner_user_id="owner-1",
            universe_id="u-owner",
            service="codex",
        )
    conn.rollback()
    conn.close()


def test_custody_refuses_symlinked_subscription_path_components(tmp_path):
    from tinyassets.credential_vault import (
        adopt_llm_subscription_custody,
        write_credential_vault,
    )
    from tinyassets.storage import db_path

    universe_dir, _ = _seed_universe(tmp_path)
    real_home = universe_dir / "real-codex-home"
    real_home.mkdir()
    (real_home / "auth.json").write_text("{}", encoding="utf-8")
    linked_home = universe_dir / "linked-codex-home"
    try:
        linked_home.symlink_to(real_home, target_is_directory=True)
    except OSError as exc:
        if os.name != "nt":
            pytest.skip(f"directory symlinks unavailable: {exc}")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(linked_home), str(real_home)],
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode:
            pytest.skip(
                "directory symlinks/junctions unavailable: "
                f"{exc}; {result.stderr.strip()}"
            )
    write_credential_vault(
        universe_dir,
        [{
            "credential_type": "llm_subscription",
            "service": "codex",
            "codex_home": str(linked_home),
        }],
        owner_user_id="owner-1",
        universe_id="u-owner",
    )

    conn = sqlite3.connect(db_path(tmp_path), isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(PermissionError, match="exactly one usable"):
        adopt_llm_subscription_custody(
            conn,
            universe_dir=universe_dir,
            owner_user_id="owner-1",
            universe_id="u-owner",
            service="codex",
        )
    conn.rollback()
    conn.close()


def test_bind_serving_provider_derives_authority_and_wires_one_generation(tmp_path):
    from tinyassets.custom_agents import get_binding
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_serving_binding import bind_serving_provider
    from tinyassets.storage.provider_work_authority import (
        SQLiteProviderWorkAuthorityStore,
    )

    universe_dir, agent = _seed_universe(tmp_path)
    result = bind_serving_provider(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=1,
        provider="codex",
    )

    stored_agent = get_binding(
        tmp_path,
        universe_id="u-owner",
        binding_id=agent["agent_binding_id"],
    )
    provider_binding = SQLiteProviderWorkAuthorityStore(tmp_path).get(
        result["provider_binding"]["binding_id"]
    )
    assignment = load_provider_assignment(tmp_path, universe_id="u-owner")

    assert stored_agent["configuration"]["provider_ref"] == provider_binding.binding_id
    assert stored_agent["revision"] == 2
    assert provider_binding.owner_user_id == "owner-1"
    assert provider_binding.universe_id == "u-owner"
    assert provider_binding.allowed_operations == ("converse",)
    assert provider_binding.allowed_roles == ("writer",)
    assert assignment.state == "ready"
    assert assignment.binding_id == provider_binding.binding_id
    assert assignment.assignment_digest == provider_binding.assignment_digest
    assert "credential_reference_digest" not in result["provider_binding"]


def test_bind_replays_only_while_custody_is_still_current(tmp_path):
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.provider_serving_binding import bind_serving_provider

    universe_dir, agent = _seed_universe(tmp_path)
    first = bind_serving_provider(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=1,
        provider="codex",
    )
    replay = bind_serving_provider(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=first["agent_binding"]["revision"],
        provider="codex",
    )
    assert replay["replayed"] is True

    write_credential_vault(
        universe_dir,
        [{
            "credential_type": "llm_subscription",
            "service": "codex",
            "auth_json_b64": "eyJyb3RhdGVkIjp0cnVlfQ==",
        }],
        owner_user_id="owner-1",
        universe_id="u-owner",
    )
    rebound = bind_serving_provider(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=first["agent_binding"]["revision"],
        provider="codex",
    )
    assert rebound["replayed"] is False
    assert rebound["assignment_generation"] == 2
    assert rebound["agent_binding"]["revision"] == 3


def test_generic_binding_writes_cannot_inject_provider_ref(tmp_path):
    from tinyassets.custom_agents import AgentValidationError, update_binding

    _, agent = _seed_universe(tmp_path)
    hostile = _binding()
    hostile["provider_ref"] = "pwb_copied_from_someone_else"

    with pytest.raises(AgentValidationError, match="provider_ref"):
        update_binding(
            tmp_path,
            universe_id="u-owner",
            binding_id=agent["agent_binding_id"],
            expected_revision=1,
            updated_by="owner-1",
            payload=hostile,
        )


def test_set_serving_requires_current_server_authority_and_is_reversible(tmp_path):
    from tinyassets.custom_agents import get_binding
    from tinyassets.provider_serving_binding import (
        bind_serving_provider,
        set_serving,
    )

    universe_dir, agent = _seed_universe(tmp_path)
    with pytest.raises(PermissionError, match="connect your provider"):
        set_serving(
            base_path=tmp_path,
            universe_dir=universe_dir,
            owner_user_id="owner-1",
            universe_id="u-owner",
            agent_binding_id=agent["agent_binding_id"],
            expected_revision=1,
            enabled=True,
        )

    connected = bind_serving_provider(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=1,
        provider="codex",
    )
    configured_revision = connected["agent_binding"]["revision"]
    enabled = set_serving(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=configured_revision,
        enabled=True,
    )
    assert enabled["agent_binding"]["status"] == "serving"
    # Serving intent is a server-owned switch, not portable configuration. It
    # preserves the binding revision so existing signed Slack routes remain exact.
    assert enabled["agent_binding"]["revision"] == configured_revision

    disabled = set_serving(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=configured_revision,
        enabled=False,
    )
    assert disabled["agent_binding"]["status"] == "configured"
    assert get_binding(
        tmp_path,
        universe_id="u-owner",
        binding_id=agent["agent_binding_id"],
    )["status"] == "configured"


def test_set_serving_fences_the_exact_approved_assignment(tmp_path):
    from tinyassets.custom_agents import get_binding
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving

    universe, agent = _seed_universe(tmp_path)
    scope = dict(base_path=tmp_path, universe_dir=universe, owner_user_id="owner-1",
                 universe_id="u-owner", agent_binding_id=agent["agent_binding_id"])
    bound = bind_serving_provider(**scope, expected_revision=1, provider="codex")
    revision = bound["agent_binding"]["revision"]
    assignment = load_provider_assignment(tmp_path, universe_id="u-owner")
    with pytest.raises(PermissionError, match="assignment changed since approval"):
        set_serving(**scope, expected_revision=revision, enabled=True,
                    expected_assignment_digest="not-the-approved-assignment")
    assert get_binding(tmp_path, universe_id="u-owner",
                       binding_id=agent["agent_binding_id"])["status"] == "configured"
    enabled = set_serving(**scope, expected_revision=revision, enabled=True,
                          expected_assignment_digest=assignment.assignment_digest)
    assert enabled["status"] == "serving"
    assert enabled["agent_binding"]["revision"] == revision  # status is not a revision bump
    # The transaction-level fence also protects an explicitly pinned disable.
    with pytest.raises(PermissionError, match="assignment changed since approval"):
        set_serving(**scope, expected_revision=revision, enabled=False,
                    expected_assignment_digest="stale")
    assert get_binding(tmp_path, universe_id="u-owner",
                       binding_id=agent["agent_binding_id"])["status"] == "serving"


def test_served_binding_selection_is_owner_scoped_and_unambiguous(tmp_path):
    from tinyassets.custom_agents import create_binding, get_definition, serving_binding_candidates
    from tinyassets.provider_serving_binding import (
        bind_serving_provider,
        resolve_current_serving_provider_authority,
        resolve_serving_agent_binding,
        set_serving,
    )

    universe_dir, agent = _seed_universe(tmp_path)
    assert serving_binding_candidates(
        tmp_path, universe_id="u-owner", owner_user_id="owner-1",
    ) == []
    connected = bind_serving_provider(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=1,
        provider="codex",
    )
    set_serving(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=connected["agent_binding"]["revision"],
        enabled=True,
    )
    # The general list returns one page, newest first. Inactive bindings outside
    # the page must not conceal this older serving binding.
    for _ in range(105):
        create_binding(
            tmp_path, universe_id="u-owner", definition_id=agent["agent_definition_id"],
            created_by="owner-1", payload=_binding(),
        )
    from tinyassets.storage import db_path

    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("UPDATE agent_bindings SET updated_at = 946684800.0 "
                     "WHERE status = 'serving'")
    assert serving_binding_candidates(
        tmp_path, universe_id="u-owner", owner_user_id="other-owner",
    ) == []
    assert serving_binding_candidates(
        tmp_path, universe_id="other-universe", owner_user_id="owner-1",
    ) == []
    selected = resolve_serving_agent_binding(
        tmp_path,
        universe_id="u-owner",
        owner_user_id="owner-1",
    )
    assert selected["agent_binding_id"] == agent["agent_binding_id"]
    assert resolve_current_serving_provider_authority(
        tmp_path, universe_dir=universe_dir, universe_id="u-owner", owner_user_id="owner-1",
    ).provider == "codex"

    definition_id = agent["agent_definition_id"]
    assert get_definition(tmp_path, definition_id) is not None
    second = create_binding(
        tmp_path,
        universe_id="u-owner",
        definition_id=definition_id,
        created_by="owner-1",
        payload=_binding(),
    )
    second_connected = bind_serving_provider(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=second["agent_binding_id"],
        expected_revision=1,
        provider="codex",
    )
    set_serving(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=second["agent_binding_id"],
        expected_revision=second_connected["agent_binding"]["revision"],
        enabled=True,
    )
    with pytest.raises(PermissionError, match="exactly one"):
        resolve_serving_agent_binding(
            tmp_path,
            universe_id="u-owner",
            owner_user_id="owner-1",
        )
    with pytest.raises(PermissionError, match="exactly one"):
        resolve_current_serving_provider_authority(
            tmp_path, universe_dir=universe_dir, universe_id="u-owner", owner_user_id="owner-1",
        )


def test_agent_binding_api_exposes_exact_bind_and_set_serving_operations(
    tmp_path,
    monkeypatch,
):
    from tinyassets.api import permissions
    from tinyassets.api.custom_agents import custom_agents
    from tinyassets.custom_agents import create_binding
    from tinyassets.daemon_server import grant_universe_access

    universe_dir, agent = _seed_universe(tmp_path)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: True)
    monkeypatch.setattr(permissions, "current_actor_id", lambda: "owner-1")
    grant_universe_access(
        tmp_path,
        universe_id="u-owner",
        actor_id="owner-1",
        permission="admin",
        granted_by="owner-1",
    )

    connected = custom_agents(
        action="bind_serving_provider",
        universe_id="u-owner",
        binding_id=agent["agent_binding_id"],
        expected_revision=1,
        payload={"provider": "codex"},
    )
    assert connected["status"] == "ready"
    assert connected["provider_binding"]["allowed_operations"] == ["converse"]
    assert "credential_reference_digest" not in connected["provider_binding"]

    enabled = custom_agents(
        action="set_serving",
        universe_id="u-owner",
        binding_id=agent["agent_binding_id"],
        expected_revision=connected["agent_binding"]["revision"],
        payload={"enabled": True},
    )
    assert enabled["status"] == "serving"
    assert enabled["agent_binding"]["status"] == "serving"

    malformed = custom_agents(
        action="set_serving",
        universe_id="u-owner",
        binding_id=agent["agent_binding_id"],
        expected_revision=connected["agent_binding"]["revision"],
        payload={"enabled": True, "provider": "codex"},
    )
    assert malformed["error"] == "agent_validation_error"

    # An ACL collaborator still cannot mint spend authority for someone else's
    # private binding: creator/founder is the bounded slice-1 acceptance scope.
    grant_universe_access(
        tmp_path,
        universe_id="u-owner",
        actor_id="collaborator-1",
        permission="admin",
        granted_by="owner-1",
    )
    monkeypatch.setattr(permissions, "current_actor_id", lambda: "collaborator-1")
    denied = custom_agents(
        action="set_serving",
        universe_id="u-owner",
        binding_id=agent["agent_binding_id"],
        expected_revision=connected["agent_binding"]["revision"],
        payload={"enabled": False},
    )
    assert denied["error"] == "provider_authority_denied"

    collaborator_binding = create_binding(
        tmp_path,
        universe_id="u-owner",
        definition_id=agent["agent_definition_id"],
        created_by="collaborator-1",
        payload=_binding(),
    )
    forged = custom_agents(
        action="bind_serving_provider",
        universe_id="u-owner",
        binding_id=collaborator_binding["agent_binding_id"],
        expected_revision=1,
        payload={"provider": "codex"},
    )
    assert forged["error"] == "provider_authority_denied"
    assert universe_dir.is_dir()


@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        ("bind_serving_provider", '{"provider":"codex"}'),
        ("set_serving", '{"enabled":true}'),
    ],
)
def test_write_graph_routes_first_class_serving_operations(
    monkeypatch,
    operation,
    payload,
):
    import json

    import tinyassets.universe_server as server

    seen: dict[str, object] = {}

    def _dispatch(**kwargs):
        seen.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(server, "_custom_agents_impl", _dispatch)
    response = json.loads(
        server.write_graph(
            target="agent_binding",
            operation=operation,
            graph_id="u-owner",
            agent_binding_id="agent-binding-1",
            expected_revision=4,
            payload_json=payload,
        )
    )

    assert response == {"status": "ok"}
    assert seen["action"] == operation
    assert seen["universe_id"] == "u-owner"
    assert seen["binding_id"] == "agent-binding-1"
    assert seen["expected_revision"] == 4
    assert seen["payload"] == payload


@pytest.mark.parametrize("operation", ["bind", "enable"])
def test_absent_binding_has_a_dedicated_exception(tmp_path, operation):
    from tinyassets import provider_serving_binding as serving

    args = dict(base_path=tmp_path, universe_dir=tmp_path / "missing-home",
                universe_id="missing-home", owner_user_id="owner-1",
                agent_binding_id="missing-binding", expected_revision=1)
    with pytest.raises(serving.AgentBindingNotFound, match="agent binding was not found"):
        if operation == "bind":
            serving.bind_serving_provider(**args, provider="codex")
        else:
            serving.set_serving(**args, enabled=True)
