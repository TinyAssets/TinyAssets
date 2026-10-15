"""Bridge existing owner bindings to the package runner."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from tinyassets.exceptions import ExternalAgentUnavailable
from tinyassets.external_agents import receipts
from tinyassets.external_agents.home import box_custody
from tinyassets.external_agents.registry import resolve
from tinyassets.providers.base import BaseProvider, ModelConfig, ProviderResponse


class ExternalAgentProvider(BaseProvider):
    agent_execution_kind = "native_agent"

    def __init__(self, agent: str):
        package = resolve(agent)
        self.name = agent
        self.family = package.row["family"]
        self.native_credential_service = package.row["auth"]["service"]
        self.native_launch_auth = dict(package.row["auth"])

    async def complete(self, prompt: str, system: str, config: ModelConfig,
                       *, universe_dir: Path | None = None) -> ProviderResponse:
        self.require_text_only_support(config)
        model = config.native_model_id or ""
        owner = config.invocation_owner_user_id
        if universe_dir is None or not owner:
            raise ExternalAgentUnavailable(self.name, model, "job_failed:custody")
        universe = Path(os.path.abspath(universe_dir))
        from tinyassets.control_plane.binding import current_binding

        binding = current_binding()
        if binding is None:
            raise ExternalAgentUnavailable(self.name, model, "job_failed:custody")
        custody = box_custody(universe, owner, self.native_credential_service, binding)
        if custody is None:
            raise ExternalAgentUnavailable(self.name, model, "signed_out")
        from tinyassets.control_plane.native_launch import launcher
        from tinyassets.external_agents.runner import run_job

        budget = config.request_budget
        if budget is None or not budget.usage_id or type(config.request_attempt) is not int:
            raise ExternalAgentUnavailable(self.name, model, "job_failed:custody")
        budget.check_scope(owner, universe.name)
        logical_key = "job:" + hashlib.sha256("\0".join((
            self.name, budget.usage_id, model, prompt, system,
            config.request_purpose,
        )).encode()).hexdigest()
        operation_id = receipts.claim_cli(
            universe.parent, owner=owner, universe_id=universe.name,
            logical_key=logical_key,
            custody_digest=custody.reference.reference_digest,
        )

        from tinyassets.broker.ledger_queries import selected_git_connections

        connections = selected_git_connections(
            universe.parent, principal=owner, command_center=universe.name)
        launch = launcher(custody, binding=binding, operation_id=operation_id,
                          connections=connections, agent=config.agent_node_id or "main")
        return await run_job(
            self.name, launch,
            custody, prompt=prompt, system=system,
            config=config,
        )
