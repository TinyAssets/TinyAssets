"""Shared seeds for background-run tests: a serving assignment, a branch, a provider.

The carrier tests that used to live here claimed fleet-era cloud-automation slices
through the consumer's epoch-2 claim pass. That pass went with the pump that fed it
(dark-code deletion plan C2); these seeds are what the live automation tests import.
"""

from __future__ import annotations

from pathlib import Path

from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.providers.base import BaseProvider, ModelConfig, ProviderResponse


class _CountingProvider(BaseProvider):
    def __init__(self, on_call=None, *, name="codex") -> None:
        self.name = name
        self.family = name
        self.calls: list[ModelConfig] = []
        self.on_call = on_call

    async def complete(self, prompt, system, config: ModelConfig, *, universe_dir=None):
        if self.on_call is not None:
            self.on_call()
        self.calls.append(config)
        return ProviderResponse(
            text="routed-ok",
            provider=self.name,
            model="fake",
            family=self.family,
            latency_ms=0.0,
            input_tokens=700,
            output_tokens=300,
            cost_microunits=50,
        )


def _seed_branch_version(tmp_path: Path, *, policy=None, agent=False):
    from tinyassets.branch_versions import publish_branch_version
    from tinyassets.daemon_server import initialize_author_server, save_branch_definition

    nodes = [NodeDefinition(
        node_id=f"n{index + 1}",
        display_name="Background writer",
        prompt_template="Complete the assigned background task.",
        llm_policy=node_policy,
        tools_allowed=["universe_self"] if agent else [],
    ) for index, node_policy in enumerate(policy if isinstance(policy, list) else [policy])]
    branch = BranchDefinition(
        branch_def_id="branch_repo_spec_loop",
        name="Repository spec loop",
        author="acct_alice",
        visibility="private",
        graph_nodes=[GraphNodeRef(id=n.node_id, node_def_id=n.node_id) for n in nodes],
        edges=[EdgeDefinition(from_node=n.node_id, to_node=(
            nodes[index + 1].node_id if index + 1 < len(nodes) else "END"
        )) for index, n in enumerate(nodes)],
        entry_point="n1",
        node_defs=nodes,
        state_schema=[],
    )
    initialize_author_server(tmp_path)
    save_branch_definition(tmp_path, branch_def=branch.to_dict())
    return publish_branch_version(tmp_path, branch.to_dict(), publisher="acct_alice")


def _seed_serving_assignment(tmp_path: Path, *, model_access=None, services=("codex",)) -> None:
    from tinyassets.commons_catalog import create_binding, publish_definition
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.daemon_server import set_founder_home
    from tinyassets.provider_serving_binding import (
        bind_serving_provider,
        list_serving_universes,
        set_serving,
    )

    universe_dir = tmp_path / "universe_alice"
    universe_dir.mkdir(exist_ok=True)
    set_founder_home(tmp_path, founder_sub="acct_alice", universe_id="universe_alice",
                     platform_generated=True)
    write_credential_vault(
        universe_dir,
        [
            {
                "credential_type": "llm_subscription",
                "service": service,
                **({"auth_json_b64": "e30="} if service == "codex"
                   else {"oauth_token": "synthetic-claude-test-only"}),
            }
            for service in services
        ],
        owner_user_id="acct_alice",
        universe_id="universe_alice",
    )
    definition = publish_definition(
        tmp_path,
        author_id="acct_alice",
        payload={
            "schema_version": 1,
            "name": "Background agent",
            "description": "Runs one assigned background Branch.",
            "tags": ["test"],
            "components": {"identity": {"kind": "soul", "config": {}}},
        },
    )
    agent = create_binding(
        tmp_path,
        universe_id="universe_alice",
        definition_id=definition["agent_definition_id"],
        created_by="acct_alice",
        payload={"schema_version": 1, "name": "Background agent", "role": "writer"},
    )
    connected = bind_serving_provider(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="acct_alice",
        universe_id="universe_alice",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=1,
        provider="codex",
        model_access=model_access,
    )
    set_serving(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="acct_alice",
        universe_id="universe_alice",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=connected["agent_binding"]["revision"],
        enabled=True,
    )
    assert list_serving_universes(tmp_path) == ["universe_alice"]
