"""Bridge existing owner bindings to the package runner."""
from __future__ import annotations

from pathlib import Path

from tinyassets.exceptions import ExternalAgentUnavailable
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
        universe = Path(universe_dir).resolve(strict=True)
        reference = _custody(universe.parent, universe, owner,
                             self.native_credential_service)
        if reference is None:
            raise ExternalAgentUnavailable(self.name, model, "signed_out")
        from tinyassets.external_agents.runner import run_job
        from tinyassets.role_external_agent import CellLauncher

        return await run_job(
            self.name, CellLauncher(universe_dir=universe, owner_user_id=owner),
            Custody(universe, owner, reference), prompt=prompt, system=system,
            config=config,
        )
