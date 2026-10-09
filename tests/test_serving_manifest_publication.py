"""Real SQLite publication of accepted connections, not model-execution proof."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from tests.test_open_serving_bind import _CONN_ID, _GRANT_ID, _setup
from tinyassets.custom_agents import get_binding
from tinyassets.provider_assignment import load_provider_assignment
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.provider_serving_binding import (
    _current_bound_member_authority,
    bind_serving_provider,
    set_serving,
)
from tinyassets.providers.definition import get_definition, register_definition
from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore


@pytest.fixture
def scene(tmp_path, monkeypatch):
    universe, agent, first = _setup(tmp_path, monkeypatch)
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: "owner-1",
    )
    ledger.create_connection(
        connection_id="http_" + "c" * 32,
        owner_user_id="owner-1",
        connection_class="http",
        connection_type="http",
        auth_scheme="bearer",
        scopes=("http",),
        provider="http",
        destination="compute:y",
        credential_ref="vault://http/compute:y",
        allowed_endpoints=[
            {
                "host": "other.example.com",
                "path_template": "/custom/inference",
                "methods": ["POST"],
            }
        ],
    )
    ledger.grant_connection(
        grant_id="http_grant_" + "d" * 32,
        connection_id="http_" + "c" * 32,
        owner_user_id="owner-1",
        universe_id="u-owner",
        unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )
    second = register_definition(
        universe_id="u-owner",
        owner_user_id="owner-1",
        access_method="api_key_http",
        protocol="openai_chat",
        model="unchanged-legacy-model",
        ref="http_grant_" + "d" * 32,
    )
    kwargs = dict(
        base_path=tmp_path,
        universe_dir=universe,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=1,
        provider=first.id,
    )
    return tmp_path, universe, ledger, first, second, kwargs


def _publish(scene, **overrides):
    _, _, _, first, second, kwargs = scene
    return bind_serving_provider(
        **{
            **kwargs,
            "model_access": {
                first.id: ModelAccess("discovered"),
                second.id: ModelAccess("explicit", ("selected-model",)),
            },
            **overrides,
        }
    )


def _root(scene):
    return load_provider_assignment(scene[0], universe_id="u-owner")


def _check_member(scene, root, member, **overrides):
    store = SQLiteProviderWorkAuthorityStore(scene[0])
    with store.connection() as conn:
        conn.execute("BEGIN")
        return _current_bound_member_authority(
            conn,
            **{
                "store": store,
                "universe_dir": scene[1],
                "base_path": scene[0],
                "owner_user_id": "owner-1",
                "universe_id": "u-owner",
                "assignment": root,
                "member": member,
                **overrides,
            },
        )


def test_all_sources_publish_under_one_assignment_and_keep_definitions(scene):
    _, _, _, first, second, _ = scene
    result = _publish(scene)
    root = _root(scene)
    assert result["replayed"] is False
    assert root.state == "ready" and root.manifest_digest
    assert root.provider == f"api_key_http:{first.id}"
    assert {member.provider for member in root.candidates} == {
        f"api_key_http:{first.id}",
        f"api_key_http:{second.id}",
    }
    assert len(result["provider_candidates"]) == 2
    for member in root.candidates:
        same_root, binding, custody = _check_member(scene, root, member)
        assert same_root == root
        assert binding.assignment_digest == root.assignment_digest
        assert binding.assignment_generation == root.generation
        assert binding.binding_digest == member.binding_digest
        assert custody.reference_digest == member.credential_reference_digest
    assert get_definition("u-owner", first.id) == first
    assert get_definition("u-owner", second.id) == second


def test_reorder_and_equivalent_model_set_replay_without_rebinding(scene):
    first, second = scene[3:5]
    original = _publish(
        scene,
        model_access={
            first.id: ModelAccess("explicit", ("x", "y")),
            second.id: ModelAccess("discovered"),
        },
    )
    before = _root(scene)
    result = _publish(
        scene,
        expected_revision=original["agent_binding"]["revision"],
        model_access={
            second.id: ModelAccess("discovered"),
            first.id: ModelAccess("explicit", ("y", "x")),
        },
    )
    assert result["replayed"] is True
    assert result["agent_binding"] == original["agent_binding"]
    assert _root(scene) == before


@pytest.mark.parametrize("change", ["scope", "model", "cost", "remove", "anchor"])
def test_authority_change_advances_generation_and_reissues_members(scene, change):
    first, second = scene[3:5]
    original = _publish(scene)
    before = _root(scene)
    access = {
        first.id: ModelAccess("discovered"),
        second.id: ModelAccess("explicit", ("selected-model",)),
    }
    overrides = {"expected_revision": original["agent_binding"]["revision"]}
    if change == "scope":
        access[second.id] = ModelAccess("discovered")
    elif change == "model":
        access[second.id] = ModelAccess("explicit", ("new-model",))
    elif change == "cost":
        access[second.id] = ModelAccess(
            "explicit", ("selected-model",), (("input_million_usd", 2),)
        )
    elif change == "remove":
        del access[second.id]
    else:
        overrides["provider"] = second.id
    result = _publish(scene, model_access=access, **overrides)
    after = _root(scene)
    assert result["replayed"] is False
    assert after.generation == before.generation + 1
    assert after.assignment_digest != before.assignment_digest
    prior = {member.provider: member for member in before.candidates}
    for member in after.candidates:
        assert member.binding_generation == prior[member.provider].binding_generation + 1
        _check_member(scene, after, member)
    if change == "remove":
        removed = next(m for m in before.candidates if m.provider == f"api_key_http:{second.id}")
        with pytest.raises(PermissionError, match="not in the current"):
            _check_member(scene, after, removed)


@pytest.mark.parametrize(
    "ceiling", ["_MAX_TOKENS", "_MAX_COST_MICROUNITS", "_MAX_BINDING_INVOCATIONS"]
)
def test_replay_checks_every_members_current_ceiling(scene, monkeypatch, ceiling):
    import tinyassets.provider_serving_binding as module

    result = _publish(scene)
    before = _root(scene)
    monkeypatch.setattr(module, ceiling, getattr(module, ceiling) - 1)
    result = _publish(scene, expected_revision=result["agent_binding"]["revision"])
    after = _root(scene)
    assert result["replayed"] is False
    assert after.generation == before.generation + 1
    field = {
        "_MAX_TOKENS": "max_tokens",
        "_MAX_COST_MICROUNITS": "max_cost_microunits",
        "_MAX_BINDING_INVOCATIONS": "max_invocations",
    }[ceiling]
    for member in after.candidates:
        assert getattr(_check_member(scene, after, member)[1], field) == getattr(module, ceiling)


def test_revoked_anchor_does_not_poison_independent_member_validation(scene):
    _publish(scene)
    root = _root(scene)
    scene[2].revoke_grant(_GRANT_ID)
    for member in root.candidates:
        if member.provider == root.provider:
            with pytest.raises(PermissionError):
                _check_member(scene, root, member)
        else:
            assert _check_member(scene, root, member)[1].provider == member.provider


@pytest.mark.parametrize("mutation", ["rotate", "revoke"])
def test_member_live_custody_rechecked(scene, mutation):
    _publish(scene)
    root = _root(scene)
    if mutation == "revoke":
        scene[2].revoke_grant(_GRANT_ID)
    else:
        with sqlite3.connect(scene[0] / "outbound.db") as conn:
            conn.execute(
                "UPDATE outbound_connections SET credential_ref = ? WHERE connection_id = ?",
                ("vault://http/rotated", _CONN_ID),
            )
    anchor = next(m for m in root.candidates if m.provider == root.provider)
    with pytest.raises(PermissionError):
        _check_member(scene, root, anchor)


@pytest.mark.parametrize("state", ["pending", "failed", "unassigned"])
def test_nonready_root_refuses_every_member(scene, state):
    _publish(scene)
    root = replace(_root(scene), state=state)
    for member in root.candidates:
        with pytest.raises(PermissionError):
            _check_member(scene, root, member)


@pytest.mark.parametrize("scope", ["owner_user_id", "universe_id"])
def test_other_identity_cannot_validate_any_member(scene, scope):
    _publish(scene)
    root = _root(scene)
    for member in root.candidates:
        with pytest.raises(PermissionError):
            _check_member(scene, root, member, **{scope: "somebody-else"})


@pytest.mark.parametrize("unsorted", [False, True])
def test_failed_second_binding_rolls_back_all_and_keeps_deny_all_root(scene, monkeypatch, unsorted):
    import tinyassets.provider_serving_binding as module

    initial = bind_serving_provider(**scene[5])
    previous = _root(scene)
    store = SQLiteProviderWorkAuthorityStore(scene[0])
    old_binding = store.get(previous.binding_id)
    original_issue = module.ProviderWorkBindingService.issue_in_transaction

    def fail_second(service, conn, root):
        if root.provider != previous.provider:
            raise RuntimeError("second-connection-failed")
        return original_issue(service, conn, root)

    monkeypatch.setattr(module.ProviderWorkBindingService, "issue_in_transaction", fail_second)
    overrides = {}
    if unsorted:
        overrides["model_access"] = {
            source.id: ModelAccess("explicit", ("z", "a"), (("z_units", 2), ("a_units", 1)))
            for source in scene[3:5]
        }
    with pytest.raises(RuntimeError, match="second-connection-failed"):
        _publish(scene, expected_revision=initial["agent_binding"]["revision"], **overrides)
    failed = _root(scene)
    assert failed.state == "failed" and len(failed.candidates) == 2
    assert failed.generation == previous.generation + 1
    assert store.get(previous.binding_id) == old_binding
    assert (
        get_binding(scene[0], universe_id="u-owner", binding_id=scene[5]["agent_binding_id"])
        == initial["agent_binding"]
    )
    for member in failed.candidates:
        with pytest.raises(PermissionError):
            _check_member(scene, failed, member)


def test_grant_revoked_between_phases_cannot_publish_ready(scene, monkeypatch):
    import tinyassets.provider_serving_binding as module

    original = module.write_provider_assignment_projection

    def revoke_after_pending(*args, **kwargs):
        original(*args, **kwargs)
        if kwargs["state"] == "pending":
            pending = _root(scene)
            assert pending.state == "pending" and len(pending.candidates) == 2
            scene[2].revoke_grant(_GRANT_ID)

    monkeypatch.setattr(module, "write_provider_assignment_projection", revoke_after_pending)
    with pytest.raises(PermissionError):
        _publish(scene)
    assert _root(scene).state == "failed"
    store = SQLiteProviderWorkAuthorityStore(scene[0])
    assert all(store.get(m.binding_id) is None for m in _root(scene).candidates)


@pytest.mark.parametrize("invalid", ["empty", "missing-anchor", "alias-duplicate", "bad-access"])
def test_invalid_membership_changes_nothing(scene, invalid):
    first, second = scene[3:5]
    access = {
        "empty": {},
        "missing-anchor": {second.id: ModelAccess()},
        "alias-duplicate": {first.id: ModelAccess(), f"api_key_http:{first.id}": ModelAccess()},
        "bad-access": {first.id: "discovered"},
    }[invalid]
    with pytest.raises(ValueError):
        _publish(scene, model_access=access)
    assert _root(scene) is None


def test_manifest_without_model_discovery_cannot_enable_serving(scene):
    from tinyassets.daemon_server import set_founder_home

    set_founder_home(
        scene[0], founder_sub="owner-1", universe_id="u-owner", platform_generated=True,
    )
    result = _publish(scene)
    before = _root(scene)
    with pytest.raises(PermissionError, match="no eligible model"):
        set_serving(
            base_path=scene[0],
            universe_dir=scene[1],
            owner_user_id="owner-1",
            universe_id="u-owner",
            agent_binding_id=scene[5]["agent_binding_id"],
            expected_revision=result["agent_binding"]["revision"],
            enabled=True,
        )
    assert _root(scene) == before
    assert get_binding(
        scene[0], universe_id="u-owner", binding_id=scene[5]["agent_binding_id"],
    ) == result["agent_binding"]


def test_manifest_to_legacy_cannot_replay_stale_members(scene):
    result = _publish(scene)
    before = _root(scene)
    result = bind_serving_provider(
        **{**scene[5], "expected_revision": result["agent_binding"]["revision"]}
    )
    after = _root(scene)
    assert result["replayed"] is False
    assert after.generation == before.generation + 1
    assert not after.manifest_digest and not after.candidates
    for old in before.candidates:
        with pytest.raises(PermissionError):
            _check_member(scene, after, old)
    assert _check_member(scene, after, None)[0] == after


def test_subscription_and_http_share_one_manifest_without_ambient_credentials(scene):
    from tinyassets.credential_vault import write_credential_vault

    write_credential_vault(
        scene[1],
        [
            {
                "credential_type": "llm_subscription",
                "service": "codex",
                "auth_json_b64": "e30=",
            }
        ],
        owner_user_id="owner-1",
        universe_id="u-owner",
    )
    _publish(
        scene,
        provider="codex",
        model_access={
            "codex": ModelAccess("explicit", ("",)),
            scene[3].id: ModelAccess("discovered"),
        },
    )
    root = _root(scene)
    assert root.provider == "codex" and len(root.candidates) == 2
    for member in root.candidates:
        _check_member(scene, root, member)


def test_stale_failure_recovery_cannot_overwrite_another_transition(scene, monkeypatch):
    import tinyassets.provider_serving_binding as module
    from tinyassets.provider_assignment import store_provider_assignment_in_transaction

    original = module.write_provider_assignment_projection
    replacement = None

    def change_root_after_pending(*args, **kwargs):
        nonlocal replacement
        original(*args, **kwargs)
        if kwargs["state"] == "pending":
            replacement = replace(_root(scene), state="unassigned", updated_at="newer-transition")
            with SQLiteProviderWorkAuthorityStore(scene[0]).connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                store_provider_assignment_in_transaction(conn, replacement)
                conn.commit()

    monkeypatch.setattr(module, "write_provider_assignment_projection", change_root_after_pending)
    with pytest.raises(PermissionError, match="changed before publication"):
        _publish(scene)
    assert replacement is not None and _root(scene) == replacement


def test_prepublication_invalid_connection_preserves_existing_assignment(scene):
    first_result = bind_serving_provider(**scene[5])
    original = _root(scene)
    scene[2].revoke_grant("http_grant_" + "d" * 32)
    with pytest.raises(PermissionError):
        _publish(scene, expected_revision=first_result["agent_binding"]["revision"])
    assert _root(scene) == original


def test_new_connection_is_not_accepted_without_its_own_custody(scene):
    with pytest.raises(PermissionError):
        _publish(
            scene, model_access={scene[3].id: ModelAccess("discovered"), "codex": ModelAccess()}
        )
    assert _root(scene) is None


def test_first_publish_with_unsorted_model_ids_and_caps_is_ready(scene):
    first, second = scene[3:5]
    access = ModelAccess("explicit", ("z", "a"), (("z_units", 2), ("a_units", 1)))
    result = _publish(scene, model_access={second.id: access, first.id: access})
    root = _root(scene)
    assert result["status"] == "ready" and root.state == "ready"
    for member in root.candidates:
        assert member.access == access
        assert member.access.model_ids == ("a", "z")
        assert member.access.cost_caps == (("a_units", 1), ("z_units", 2))
        _check_member(scene, root, member)
