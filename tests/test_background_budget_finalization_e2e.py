"""Shared seeds for background-run tests: a serving assignment, a branch, a provider.

The carrier tests that used to live here claimed fleet-era cloud-automation slices
through the consumer's epoch-2 claim pass. That pass went with the pump that fed it
(dark-code deletion plan C2); these seeds are what the live automation tests import.
"""

from __future__ import annotations

from pathlib import Path

from tests.support.http_model import HTTP_PROVIDER
from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.providers.base import BaseProvider, ModelConfig, ProviderResponse


class _CountingProvider(BaseProvider):
    supports_text_only = True

    def __init__(self, on_call=None, *, name=HTTP_PROVIDER) -> None:
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


def _seed_serving_assignment(tmp_path: Path, *, model_access=None) -> str:
    from tests.support.http_model import seed_http_assignment
    return seed_http_assignment(tmp_path, model_access=model_access)
