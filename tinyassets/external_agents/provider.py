"""Bridge existing owner bindings to the package runner."""
from __future__ import annotations

import hashlib
from pathlib import Path

from tinyassets.exceptions import ExternalAgentUnavailable
from tinyassets.external_agents import receipts
from tinyassets.external_agents.home import Custody
from tinyassets.external_agents.registry import resolve
from tinyassets.providers.base import BaseProvider, ModelConfig, ProviderResponse
from tinyassets.providers.native_discovery import _custody


class ExternalAgentProvider(BaseProvider):
    agent_execution_kind = "native_agent"

    def __init__(self, agent: str):
        package = resolve(agent)
        self.name = agent
        self.family = package.row["family"]
        self.native_credential_service = package.row["auth"]["service"]

    async def complete(self, prompt: str, system: str, config: ModelConfig,
                       *, universe_dir: Path | None = None) -> ProviderResponse:
        self.require_text_only_support(config)
        model = config.native_model_id or ""
        owner = config.invocation_owner_user_id
        if universe_dir is None or not owner:
            raise ExternalAgentUnavailable(self.name, model, "job_failed:custody")
        universe = Path(universe_dir).absolute()
        reference = _custody(universe.parent, universe, owner,
                             self.native_credential_service)
        if reference is None:
            raise ExternalAgentUnavailable(self.name, model, "signed_out")
        from tinyassets.control_plane.native_launch import launcher
        from tinyassets.external_agents.runner import run_job

        custody = Custody(universe, owner, reference)
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
            logical_key=logical_key, custody_digest=reference.reference_digest,
        )

        from tinyassets.agent_loop.served_chat import configured_box_provider
        from tinyassets.control_plane.binding import bind_session

        box_provider, limits = configured_box_provider()
        binding = (bind_session(provider=box_provider, limits=limits,
                                universe_dir=universe, owner=owner,
                                execution_id=operation_id)
                   if box_provider is not None else None)

        return await run_job(
            self.name, launcher(custody, operation_id=operation_id, binding=binding),
            custody, prompt=prompt, system=system,
            config=config,
        )
