from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import pytest


def _component(kind: str, **config: object) -> dict[str, object]:
    return {"kind": kind, "config": config}


def _definition(
    name: str,
    *,
    components: dict[str, dict[str, object]] | None = None,
    lineage: dict[str, list[dict[str, object]]] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "name": name,
        "description": f"{name} definition",
        "tags": ["agent", "test"],
        "components": components or {"identity": _component("soul", instructions="Be useful.")},
    }
    if lineage is not None:
        payload["lineage"] = lineage
    return payload


def test_publish_is_immutable_extensible_and_idempotent(tmp_path) -> None:
    from tinyassets.commons_catalog import get_definition, publish_definition

    first_payload = _definition(
        "Coding partner",
        components={
            "identity": _component("soul", instructions="Pair carefully."),
            "experimental_context_fabric": _component(
                "third_party.v9",
                knobs={"novel": True},
            ),
        },
    )
    first = publish_definition(
        tmp_path,
        author_id="alice",
        payload=first_payload,
        idempotency_key="publish-coding-partner-v1",
    )
    retry = publish_definition(
        tmp_path,
        author_id="alice",
        payload=copy.deepcopy(first_payload),
        idempotency_key="publish-coding-partner-v1",
    )

    successor_payload = copy.deepcopy(first_payload)
    successor_payload["description"] = "A separately published successor"
    successor = publish_definition(
        tmp_path,
        author_id="alice",
        payload=successor_payload,
        idempotency_key="publish-coding-partner-v2",
    )

    assert first["agent_definition_id"].startswith("agent_")
    assert retry["agent_definition_id"] == first["agent_definition_id"]
    assert retry["content_fingerprint"] == first["content_fingerprint"]
    assert successor["agent_definition_id"] != first["agent_definition_id"]
    assert (
        get_definition(tmp_path, first["agent_definition_id"])["description"]
        == "Coding partner definition"
    )
    assert first["components"]["experimental_context_fabric"]["kind"] == "third_party.v9"


@pytest.mark.parametrize(
    ("payload", "path_fragment"),
    [
        (
            _definition(
                "Secret agent",
                components={
                    "provider": _component(
                        "provider",
                        nested={"api_key": "do-not-store"},
                    )
                },
            ),
            "components.provider.config.nested.api_key",
        ),
        (
            _definition(
                "Bad key",
                components={"Not A Slug": _component("custom")},
            ),
            "components.Not A Slug",
        ),
        (
            _definition(
                "Token agent",
                components={
                    "identity": _component(
                        "soul",
                        token="ghp_sensitive_value_1234567890",
                    )
                },
            ),
            "components.identity.config.token",
        ),
        (
            _definition(
                "Runtime-state agent",
                components={
                    "identity": _component(
                        "soul",
                        runtime_state={"messages": ["private"]},
                    )
                },
            ),
            "components.identity.config.runtime_state",
        ),
        (
            {
                **_definition("Credential-shaped description"),
                "description": (
                    "prefix ghp_sensitive_value_1234567890 suffix; "
                    "Authorization: Bearer abc"
                ),
            },
            "description",
        ),
        (
            _definition(
                "Bad component",
                components={"identity": "not-an-object"},  # type: ignore[dict-item]
            ),
            "components.identity",
        ),
    ],
)
def test_invalid_definition_is_rejected_without_a_partial_write(
    tmp_path,
    payload,
    path_fragment,
) -> None:
    from tinyassets.commons_catalog import (
        AgentValidationError,
        list_definitions,
        publish_definition,
    )

    with pytest.raises(AgentValidationError, match=path_fragment):
        publish_definition(tmp_path, author_id="alice", payload=payload)

    assert list_definitions(tmp_path) == []


def test_native_definition_rejects_credential_shaped_key_without_echoing_it(
    tmp_path,
) -> None:
    from tinyassets.commons_catalog import AgentValidationError, publish_definition

    credential_key = "ghp_abcdefghijklmnop"
    payload = _definition(
        "Credential-shaped object key",
        components={
            "identity": _component(
                "soul",
                **{credential_key: "safe-looking-value"},
            )
        },
    )

    with pytest.raises(AgentValidationError) as exc_info:
        publish_definition(tmp_path, author_id="alice", payload=payload)

    assert "credential-shaped-key" in str(exc_info.value)
    assert credential_key not in str(exc_info.value)


def test_component_remix_records_verified_multi_parent_lineage_atomically(
    tmp_path,
) -> None:
    from tinyassets.commons_catalog import (
        AgentValidationError,
        list_definitions,
        publish_definition,
    )

    identity_parent = publish_definition(
        tmp_path,
        author_id="alice",
        payload=_definition(
            "Identity parent",
            components={"identity": _component("soul", voice="direct")},
        ),
    )
    workflow_parent = publish_definition(
        tmp_path,
        author_id="bob",
        payload=_definition(
            "Workflow parent",
            components={"workflow": _component("branch_set", refs=["branch-a"])},
        ),
    )
    child_payload = _definition(
        "Blended agent",
        components={
            "identity": _component("soul", voice="direct but warmer"),
            "workflow": _component("branch_set", refs=["branch-a", "branch-b"]),
        },
        lineage={
            "identity": [
                {
                    "definition_id": identity_parent["agent_definition_id"],
                    "component_key": "identity",
                    "credit_share": 0.75,
                }
            ],
            "workflow": [
                {
                    "definition_id": workflow_parent["agent_definition_id"],
                    "component_key": "workflow",
                    "credit_share": 0.5,
                },
                {
                    "definition_id": identity_parent["agent_definition_id"],
                    "component_key": "identity",
                    "credit_share": 0.25,
                },
            ],
        },
    )

    child = publish_definition(
        tmp_path,
        author_id="carol",
        payload=child_payload,
    )

    assert len(child["lineage"]) == 3
    assert {
        (edge["child_component_key"], edge["parent_definition_id"]) for edge in child["lineage"]
    } == {
        ("identity", identity_parent["agent_definition_id"]),
        ("workflow", workflow_parent["agent_definition_id"]),
        ("workflow", identity_parent["agent_definition_id"]),
    }
    assert {edge["generation_depth"] for edge in child["lineage"]} == {1}

    invalid = copy.deepcopy(child_payload)
    invalid["name"] = "Invalid blend"
    invalid["lineage"]["workflow"][0]["credit_share"] = 0.8  # type: ignore[index]
    invalid["lineage"]["workflow"][1]["credit_share"] = 0.3  # type: ignore[index]
    before = len(list_definitions(tmp_path))
    with pytest.raises(AgentValidationError, match="credit shares"):
        publish_definition(tmp_path, author_id="mallory", payload=invalid)
    assert len(list_definitions(tmp_path)) == before


def test_missing_parent_component_and_depth_over_50_are_rejected(tmp_path) -> None:
    from tinyassets.commons_catalog import AgentValidationError, publish_definition

    missing = _definition(
        "Missing parent",
        lineage={
            "identity": [
                {
                    "definition_id": "agent_missing",
                    "component_key": "identity",
                    "credit_share": 1.0,
                }
            ]
        },
    )
    with pytest.raises(AgentValidationError, match="parent component"):
        publish_definition(tmp_path, author_id="alice", payload=missing)

    current = publish_definition(
        tmp_path,
        author_id="alice",
        payload=_definition("Generation zero"),
    )
    for generation in range(1, 51):
        current = publish_definition(
            tmp_path,
            author_id="alice",
            payload=_definition(
                f"Generation {generation}",
                lineage={
                    "identity": [
                        {
                            "definition_id": current["agent_definition_id"],
                            "component_key": "identity",
                            "credit_share": 1.0,
                        }
                    ]
                },
            ),
        )
    assert current["lineage"][0]["generation_depth"] == 50

    with pytest.raises(AgentValidationError, match="50 generations"):
        publish_definition(
            tmp_path,
            author_id="alice",
            payload=_definition(
                "Generation 51",
                lineage={
                    "identity": [
                        {
                            "definition_id": current["agent_definition_id"],
                            "component_key": "identity",
                            "credit_share": 1.0,
                        }
                    ]
                },
            ),
        )


def _binding(name: str = "My coding agent") -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": name,
        "role": "Own the test-and-iterate loop",
        "goals": ["ship reliable automations"],
        "component_configuration": {
            "workflow": {"branch_refs": ["branch-a"]},
        },
        "authority": {"capability_refs": ["branch.write", "run.execute"]},
        "resources": {"github": {"resource_binding_id": "resource-github-1"}},
        "provider": {"provider_policy_id": "provider-policy-1"},
        "channels": {
            "slack": {
                "adapter_ref": "commons:slack",
                "address_ref": "channel-address-1",
            }
        },
    }


def test_binding_create_and_revision_guard_are_atomic(tmp_path) -> None:
    from tinyassets.commons_catalog import (
        AgentConflictError,
        create_binding,
        get_binding,
        publish_definition,
        update_binding,
    )

    definition = publish_definition(
        tmp_path,
        author_id="alice",
        payload=_definition("Bound agent"),
    )
    binding = create_binding(
        tmp_path,
        universe_id="universe-a",
        definition_id=definition["agent_definition_id"],
        created_by="alice",
        payload=_binding(),
    )

    assert binding["revision"] == 1
    assert binding["status"] == "configured"
    assert binding["configuration"]["channels"]["slack"]["adapter_ref"] == ("commons:slack")

    updated_payload = _binding("Updated coding agent")
    updated = update_binding(
        tmp_path,
        universe_id="universe-a",
        binding_id=binding["agent_binding_id"],
        expected_revision=1,
        updated_by="alice",
        payload=updated_payload,
    )
    assert updated["revision"] == 2
    assert updated["configuration"]["name"] == "Updated coding agent"

    with pytest.raises(AgentConflictError, match="revision conflict"):
        update_binding(
            tmp_path,
            universe_id="universe-a",
            binding_id=binding["agent_binding_id"],
            expected_revision=1,
            updated_by="alice",
            payload=_binding("Stale overwrite"),
        )
    assert (
        get_binding(
            tmp_path,
            universe_id="universe-a",
            binding_id=binding["agent_binding_id"],
        )["configuration"]["name"]
        == "Updated coding agent"
    )


def test_binding_rejects_raw_credentials_and_cross_universe_reads(tmp_path) -> None:
    from tinyassets.commons_catalog import (
        AgentValidationError,
        create_binding,
        get_binding,
        list_bindings,
        publish_definition,
    )

    definition = publish_definition(
        tmp_path,
        author_id="alice",
        payload=_definition("Private bound agent"),
    )
    invalid = _binding()
    invalid["resources"]["github"]["access_token"] = "raw-secret"  # type: ignore[index]

    with pytest.raises(
        AgentValidationError,
        match=r"resources\.github\.access_token",
    ):
        create_binding(
            tmp_path,
            universe_id="universe-a",
            definition_id=definition["agent_definition_id"],
            created_by="alice",
            payload=invalid,
        )
    assert list_bindings(tmp_path, universe_id="universe-a") == []

    binding = create_binding(
        tmp_path,
        universe_id="universe-a",
        definition_id=definition["agent_definition_id"],
        created_by="alice",
        payload=_binding(),
    )
    assert (
        get_binding(
            tmp_path,
            universe_id="universe-b",
            binding_id=binding["agent_binding_id"],
        )
        is None
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("conversations", [{"role": "user", "content": "private"}]),
        ("message_history", ["private turn"]),
        ("effect_payload", {"destination": "external-system"}),
        ("runtime_state", {"status": "running"}),
    ],
)
def test_binding_rejects_private_operational_content_on_create_and_update(
    tmp_path,
    field,
    value,
) -> None:
    from tinyassets.commons_catalog import (
        AgentValidationError,
        create_binding,
        get_binding,
        list_bindings,
        publish_definition,
        update_binding,
    )

    definition = publish_definition(
        tmp_path,
        author_id="alice",
        payload=_definition("Configuration-only binding"),
    )
    invalid_create = _binding()
    invalid_create["nested"] = {field: value}
    with pytest.raises(AgentValidationError, match=field):
        create_binding(
            tmp_path,
            universe_id="universe-a",
            definition_id=definition["agent_definition_id"],
            created_by="alice",
            payload=invalid_create,
        )
    assert list_bindings(tmp_path, universe_id="universe-a") == []

    binding = create_binding(
        tmp_path,
        universe_id="universe-a",
        definition_id=definition["agent_definition_id"],
        created_by="alice",
        payload=_binding(),
    )
    invalid_update = _binding()
    invalid_update["nested"] = {field: value}
    with pytest.raises(AgentValidationError, match=field):
        update_binding(
            tmp_path,
            universe_id="universe-a",
            binding_id=binding["agent_binding_id"],
            expected_revision=1,
            updated_by="alice",
            payload=invalid_update,
        )
    stored = get_binding(
        tmp_path,
        universe_id="universe-a",
        binding_id=binding["agent_binding_id"],
    )
    assert stored["revision"] == 1
    assert "nested" not in stored["configuration"]


def test_custom_agent_api_hides_private_bindings_without_universe_access(
    tmp_path,
    monkeypatch,
) -> None:
    from tinyassets.api import permissions
    from tinyassets.api.commons_catalog import commons_catalog
    from tinyassets.commons_catalog import create_binding, publish_definition

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    definition = publish_definition(
        tmp_path,
        author_id="alice",
        payload=_definition("API agent"),
    )
    binding = create_binding(
        tmp_path,
        universe_id="universe-a",
        definition_id=definition["agent_definition_id"],
        created_by="alice",
        payload=_binding(),
    )

    public = commons_catalog(
        action="get_agent",
        definition_id=definition["agent_definition_id"],
    )
    assert public["agent"]["agent_definition_id"] == (definition["agent_definition_id"])

    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: False)
    monkeypatch.setattr(permissions, "current_actor_id", lambda: "anonymous")
    monkeypatch.setattr(
        permissions,
        "universe_access_allows",
        # Public universe readability is intentionally insufficient for
        # private agent-binding reads.
        lambda universe_id, *, write=False: True,
    )
    hidden = commons_catalog(
        action="get_binding",
        universe_id="universe-a",
        binding_id=binding["agent_binding_id"],
    )
    assert hidden == {"error": "not_found", "resource": "agent_binding"}

    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: True)
    monkeypatch.setattr(permissions, "current_actor_id", lambda: "alice")
    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id="alice",
        permission="read",
        granted_by="alice",
    )
    monkeypatch.setattr(
        permissions,
        "universe_access_allows",
        lambda universe_id, *, write=False: universe_id == "universe-a",
    )
    visible = commons_catalog(
        action="get_binding",
        universe_id="universe-a",
        binding_id=binding["agent_binding_id"],
    )
    assert visible["binding"]["configuration"]["role"].startswith("Own the")


def test_custom_agent_api_requires_auth_and_write_access_for_mutations(
    tmp_path,
    monkeypatch,
) -> None:
    from tinyassets.api import permissions
    from tinyassets.api.commons_catalog import commons_catalog

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: False)
    monkeypatch.setattr(permissions, "current_actor_id", lambda: "anonymous")

    anonymous = commons_catalog(
        action="publish_agent",
        payload=_definition("Anonymous agent"),
    )
    assert anonymous["error"] == "authentication_required"

    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: True)
    monkeypatch.setattr(permissions, "current_actor_id", lambda: "alice")
    published = commons_catalog(
        action="publish_agent",
        payload=_definition("Authorized agent"),
    )
    definition_id = published["agent"]["agent_definition_id"]

    monkeypatch.setattr(
        permissions,
        "universe_access_allows",
        lambda universe_id, *, write=False: not write,
    )
    denied = commons_catalog(
        action="create_binding",
        universe_id="universe-a",
        definition_id=definition_id,
        payload=_binding(),
    )
    assert denied["error"] == "universe_access_denied"

    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id="alice",
        permission="write",
        granted_by="alice",
    )
    monkeypatch.setattr(
        permissions,
        "universe_access_allows",
        lambda universe_id, *, write=False: universe_id == "universe-a",
    )
    created = commons_catalog(
        action="create_binding",
        universe_id="universe-a",
        definition_id=definition_id,
        payload=_binding(),
    )
    assert created["binding"]["revision"] == 1


def test_concurrent_definition_retry_creates_exactly_one_artifact(tmp_path) -> None:
    from tinyassets.commons_catalog import list_definitions, publish_definition

    payload = _definition("Concurrent publish")

    def publish_once(_: int) -> str:
        result = publish_definition(
            tmp_path,
            author_id="alice",
            payload=payload,
            idempotency_key="concurrent-publish-request",
        )
        return result["agent_definition_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        definition_ids = list(pool.map(publish_once, range(16)))

    assert len(set(definition_ids)) == 1
    assert len(list_definitions(tmp_path)) == 1


def test_concurrent_binding_compare_and_swap_has_one_winner(tmp_path) -> None:
    from tinyassets.commons_catalog import (
        AgentConflictError,
        create_binding,
        get_binding,
        publish_definition,
        update_binding,
    )

    definition = publish_definition(
        tmp_path,
        author_id="alice",
        payload=_definition("Concurrent binding"),
    )
    binding = create_binding(
        tmp_path,
        universe_id="universe-a",
        definition_id=definition["agent_definition_id"],
        created_by="alice",
        payload=_binding(),
    )

    def update_once(index: int) -> str:
        try:
            update_binding(
                tmp_path,
                universe_id="universe-a",
                binding_id=binding["agent_binding_id"],
                expected_revision=1,
                updated_by=f"writer-{index}",
                payload=_binding(f"Candidate {index}"),
            )
            return "updated"
        except AgentConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(update_once, range(8)))

    assert outcomes.count("updated") == 1
    assert outcomes.count("conflict") == 7
    final = get_binding(
        tmp_path,
        universe_id="universe-a",
        binding_id=binding["agent_binding_id"],
    )
    assert final["revision"] == 2
