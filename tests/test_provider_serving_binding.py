from __future__ import annotations

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
    from tinyassets.commons_catalog import create_binding, publish_definition
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.daemon_server import set_founder_home

    universe_dir = tmp_path / "u-owner"
    universe_dir.mkdir()
    set_founder_home(
        tmp_path, founder_sub="owner-1", universe_id="u-owner", platform_generated=True,
    )
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

    monkeypatch.setattr(server, "_commons_catalog_impl", _dispatch)
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
