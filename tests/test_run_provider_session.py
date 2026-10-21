from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from tests.support.http_model import HTTP_PROVIDER
from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.platform_runtime_provenance import (
    CLOUD,
    RuntimeProvenance,
)
from tinyassets.providers.base import BaseProvider, ModelConfig, ProviderResponse
from tinyassets.providers.router import ProviderRouter
from tinyassets.storage.provider_work_authority import db_path as authority_db_path

#: One admitted verdict for this module, built from the production dataclass.
_ADMITTED_PROVENANCE = RuntimeProvenance(
    verdict=CLOUD,
    reason="instance_match",
    metadata_reachable=True,
    expected_identity_prepared=True,
)




class _CountingProvider(BaseProvider):
    supports_text_only = True

    def __init__(self, name: str = HTTP_PROVIDER, after_call=None) -> None:
        self.name = name
        self.family = name
        self.calls: list[ModelConfig] = []
        self.after_call = after_call

    async def complete(
        self,
        prompt: str,
        system: str,
        config: ModelConfig,
        *,
        universe_dir: Path | None = None,
    ) -> ProviderResponse:
        self.calls.append(config)
        if self.after_call is not None:
            self.after_call(self.name, len(self.calls))
        return ProviderResponse(
            text="foreground-ok",
            provider=self.name,
            model="test-model",
            family=self.family,
            latency_ms=1.0,
            input_tokens=70,
            output_tokens=30,
            cost_microunits=5,
        )




def _branch(
    *,
    node_count: int,
    author: str = "acct_alice",
    provider: str = HTTP_PROVIDER,
) -> BranchDefinition:
    nodes = [
        NodeDefinition(
            node_id=f"n{index}",
            display_name=f"Writer {index}",
            prompt_template=f"Write foreground result {index}.",
            output_keys=[f"answer_{index}"],
            model_hint="writer",
            llm_policy={"preferred": {"provider": provider}},
        )
        for index in range(1, node_count + 1)
    ]
    graph_nodes = [GraphNodeRef(id=node.node_id, node_def_id=node.node_id) for node in nodes]
    edges = [EdgeDefinition(from_node="START", to_node="n1")]
    edges.extend(
        EdgeDefinition(from_node=f"n{index}", to_node=f"n{index + 1}")
        for index in range(1, node_count)
    )
    edges.append(EdgeDefinition(from_node=f"n{node_count}", to_node="END"))
    return BranchDefinition(
        branch_def_id=f"branch_foreground_{node_count}",
        name="Foreground provider authority",
        author=author,
        visibility="private",
        graph_nodes=graph_nodes,
        edges=edges,
        entry_point="n1",
        node_defs=nodes,
        state_schema=[
            {"name": f"answer_{index}", "type": "str", "default": ""}
            for index in range(1, node_count + 1)
        ],
    )


def _seed_open_serving_assignment(base_path, monkeypatch, **kwargs):
    from tests.support.http_model import seed_http_assignment
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base_path))
    return seed_http_assignment(base_path, **kwargs)


def _run_branch(
    tmp_path: Path,
    monkeypatch,
    authenticate_request,
    branch: BranchDefinition,
    *,
    authority_case: str = "active",
    open_provider: bool = False,
    open_model: str = "synthetic-model",
    open_host: str = "api.example.com",
    open_router_resolution_refusal: bool = False,
    model_access=None,
    after_provider_call=None,
    on_active_session=None,
) -> tuple[dict[str, Any], _CountingProvider, dict[str, Any]]:
    from tinyassets.api import runs as api_runs
    from tinyassets.daemon_server import save_branch_definition, set_founder_home
    from tinyassets.providers import call as call_module
    from tinyassets.runs import execute_branch_async, get_run, wait_for

    authenticate_request("acct_alice")
    set_founder_home(
        tmp_path,
        founder_sub="acct_alice",
        universe_id=(
            "universe_other" if authority_case == "cross_universe" else "universe_alice"
        ),
        platform_generated=True,
    )
    universe_dir = tmp_path / "universe_alice"
    universe_dir.mkdir(exist_ok=True)
    selected_provider = HTTP_PROVIDER
    provider_router = ProviderRouter({})
    monkeypatch.setattr(call_module, "get_provider_router", lambda: provider_router)
    if authority_case != "missing":
        selected_provider = _seed_open_serving_assignment(
            tmp_path, monkeypatch,
            select_for_serving=authority_case != "registered_only",
            model_access=model_access, model=open_model, host=open_host,
        )
    provider_names = [selected_provider]
    (universe_dir / "config.yaml").write_text(
        f"preferred_writer: {selected_provider}\n"
        "allowed_providers:\n" + "".join(f"  - {name}\n" for name in provider_names),
        encoding="utf-8",
    )
    for node in branch.node_defs:
        node.llm_policy = dict(node.llm_policy or {})
        node.llm_policy["preferred"] = dict(node.llm_policy.get("preferred") or {})
        node.llm_policy["preferred"]["provider"] = selected_provider
    save_branch_definition(tmp_path, branch_def=branch.to_dict())
    if authority_case == "home_rebound":
        set_founder_home(
            tmp_path, founder_sub="acct_alice", universe_id="universe_other",
            platform_generated=True,
        )
    if authority_case in {"revoked", "stale"}:
        conn = sqlite3.connect(authority_db_path(tmp_path))
        if authority_case == "revoked":
            conn.execute(
                "UPDATE provider_work_bindings SET state = 'revoked' "
                "WHERE record_json LIKE '%\"allowed_operations\":[\"converse\"]%'"
            )
        else:
            conn.execute(
                "UPDATE provider_assignments SET generation = generation + 1 "
                "WHERE universe_id = 'universe_alice'"
            )
        conn.commit()
        conn.close()

    captured: dict[str, Any] = {}
    captured["effects"] = []

    def capture_execute(*args: Any, provider_call=None, **kwargs: Any):
        captured["provider_call"] = provider_call
        # A FORWARDING wrapper: `kwargs` already carries the caller's actor,
        # and naming one here passed it twice.
        return execute_branch_async(
            *args,
            provider_call=provider_call,
            **kwargs,
        )

    monkeypatch.setattr(api_runs, "_ensure_runs_recovery", lambda: None)
    monkeypatch.setattr(api_runs, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(
        api_runs,
        "_universe_dir",
        lambda _uid: tmp_path / "universe_alice",
    )
    monkeypatch.setattr(
        api_runs,
        "_request_universe",
        lambda uid="": uid or "universe_alice",
    )
    monkeypatch.setattr("tinyassets.runs.execute_branch_async", capture_execute)
    # Since change `sandboxed-code-node` effects fire at node time; the
    # post-run dispatcher this helper used to capture is gone. The completion
    # path still passes exactly one point, once per COMPLETED run and never on
    # a refused launch: the quarantine of branch-authored effect keys that
    # precedes reading the run's effect chain. That is the "effects path was
    # reached" signal these tests count.
    monkeypatch.setattr(
        "tinyassets.runs._quarantine_branch_authored_external_write_keys",
        lambda output: captured["effects"].append((output,)),
    )

    providers = {name: _CountingProvider(name, after_provider_call) for name in provider_names}
    provider_router = ProviderRouter(providers)
    provider = providers[selected_provider]
    captured["providers"] = providers
    captured["router"] = provider_router
    if not open_provider:
        monkeypatch.setattr("tinyassets.providers.provider_resolver.provider_for_definition",
                            lambda definition: providers["api_key_http:" + definition.id])

    def governed_provider_call(
        prompt,
        system="",
        *,
        role="writer",
        config=None,
        universe_context=None,
        operation=None,
        **_kwargs,
    ):
        from dataclasses import replace

        from tinyassets.providers.model_call import _policy_models

        preferred, _ = _policy_models(_kwargs.get("policy"))
        if (universe_context is not None and universe_context.model_selection is None
                and preferred is not None):
            universe_context = replace(universe_context, model_selection=preferred)
        if open_router_resolution_refusal and getattr(
            universe_context, "provider_invocation", None
        ) is not None:
            def refuse_resolution(_definition):
                raise ValueError("synthetic resolver refusal")

            monkeypatch.setattr(
                "tinyassets.providers.provider_resolver.provider_for_definition",
                refuse_resolution,
            )
        if on_active_session is not None:
            on_active_session(captured["provider_call"])
        return provider_router.call_sync(
            role,
            prompt,
            system,
            config,
            universe_context=universe_context,
            operation=operation,
        ).text

    injected_provider_call = governed_provider_call
    monkeypatch.setattr("tinyassets.providers.model_call.text_sync", injected_provider_call)
    bind_run_provider = api_runs._bind_run_provider_call
    monkeypatch.setattr(
        api_runs,
        "_bind_run_provider_call",
        lambda _ambient_provider_call, universe_id: bind_run_provider(
            injected_provider_call,
            universe_id,
        ),
    )
    response = json.loads(
        api_runs._action_run_branch(
            {
                "branch_def_id": branch.branch_def_id,
                "universe_id": "universe_alice",
            }
        )
    )
    assert "run_id" in response, response
    wait_for(response["run_id"], timeout=60)
    record = get_run(tmp_path, response["run_id"])
    assert record is not None
    response["terminal_status"] = record["status"]
    response["terminal_error"] = record["error"]
    return response, provider, captured






#: What each held case must SAY, not merely that it was held. Every one of
#: these used to read "Connect your provider before running this command center",
#: including on a universe that had -- live 2026-09-30, where the owner of a
#: connected, serving `api_key_http` source was sent to connect a provider
#: (tests/test_free_account_run_provider_parity.py). Only the two cases that
#: genuinely have no serving binding keep that sentence.
_HELD_CASE_REASON = {"missing": "no connected model source for this owner"}












