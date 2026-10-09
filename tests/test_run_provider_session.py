from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

import tinyassets.platform_runtime_provenance as platform_runtime_provenance
from tests.inference_usage_helpers import accounting_resolver
from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.platform_runtime_provenance import (
    CLOUD,
    ProcessProvenanceObservation,
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


@pytest.fixture(autouse=True)
def _module_local_cloud_admission(monkeypatch):
    """Admit this module's process, through the real observation seam.

    Every test in this file drives the cloud provider lane, which now requires
    an admitted cloud runtime (`cloud-only-runtime-admission`, enforcement site
    (C)). With no verdict these processes are unobserved, and unobserved is a
    refusal -- so without this each test would be asserting the admission gate
    instead of the behaviour it was written for.

    Deliberately module-local and NOT in `tests/conftest.py`: a suite-wide
    autouse admission would silently admit the negative regressions in
    `tests/test_cloud_only_admission_regressions.py` and
    `tests/test_cloud_only_provider_admission_regressions.py` as well, and
    those tests are the whole point. Those files bind their own verdict per
    test through the same seam.

    The seam is the production one -- a `ProcessProvenanceObservation` with an
    injected resolver -- not an env var, not a flag and not a tests-only branch
    in production code. No metadata socket is opened, and a green run here
    establishes no cloud fact and no production authority.
    """
    observation = ProcessProvenanceObservation(resolver=lambda: _ADMITTED_PROVENANCE)
    observation.observe()
    monkeypatch.setattr(
        platform_runtime_provenance, "_PROCESS_OBSERVATION", observation
    )
    return observation



class _CountingProvider(BaseProvider):
    def __init__(self, name: str = "codex", after_call=None) -> None:
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


class _OpenProxy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def close(self) -> None:
        pass

    def request(self, verb: str, wire: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((verb, wire))
        return {
            "status": 200,
            "body": json.dumps(
                {
                    "choices": [{"message": {"content": "foreground-open-ok"}}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 4},
                }
            ),
        }


@pytest.fixture
def available_native_executors(monkeypatch):
    """Only the executor is synthetic; readiness and custody stay real."""
    router = ProviderRouter({name: _CountingProvider(name)
                             for name in ("codex", "claude-code")})
    monkeypatch.setattr("tinyassets.providers.call.get_provider_router", lambda: router)
    return router


def _branch(
    *,
    node_count: int,
    author: str = "acct_alice",
    provider: str = "codex",
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


def _seed_serving_assignment(
    base_path: Path,
    *,
    owner_user_id: str = "acct_alice",
    universe_id: str = "universe_alice",
    model_access=None,
    services=("codex",),
) -> None:
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.custom_agents import create_binding, publish_definition
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving

    universe_dir = base_path / universe_id
    universe_dir.mkdir(exist_ok=True)
    (universe_dir / "config.yaml").write_text(
        "preferred_writer: codex\nallowed_providers:\n  - codex\n",
        encoding="utf-8",
    )
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
        owner_user_id=owner_user_id,
        universe_id=universe_id,
    )
    definition = publish_definition(
        base_path,
        author_id=owner_user_id,
        payload={
            "schema_version": 1,
            "name": "Foreground agent",
            "description": "Serves foreground Branch runs.",
            "tags": ["test"],
            "components": {"identity": {"kind": "soul", "config": {}}},
        },
    )
    agent = create_binding(
        base_path,
        universe_id=universe_id,
        definition_id=definition["agent_definition_id"],
        created_by=owner_user_id,
        payload={"schema_version": 1, "name": "Foreground agent", "role": "writer"},
    )
    connected = bind_serving_provider(
        base_path=base_path,
        universe_dir=universe_dir,
        owner_user_id=owner_user_id,
        universe_id=universe_id,
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=1,
        provider="codex",
        model_access=model_access,
    )
    set_serving(
        base_path=base_path,
        universe_dir=universe_dir,
        owner_user_id=owner_user_id,
        universe_id=universe_id,
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=connected["agent_binding"]["revision"],
        enabled=True,
    )


def _seed_open_serving_assignment(
    base_path: Path,
    monkeypatch,
    *,
    owner_user_id: str = "acct_alice",
    universe_id: str = "universe_alice",
    select_for_serving: bool = True,
    model_access=None,
    model="synthetic-model",
    host="api.example.com",
) -> str:
    """Select one synthetic owner-bound HTTP provider for foreground runs."""
    from tinyassets.custom_agents import create_binding, publish_definition
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving
    from tinyassets.providers.definition import register_definition
    from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base_path))
    universe_dir = base_path / universe_id
    universe_dir.mkdir(exist_ok=True)
    connection_id = "http_" + "b" * 32
    grant_id = "http_grant_" + "a" * 32
    ledger = ConnectionLedger(
        base_path / ".broker" / "outbound.db", data_root=base_path,
        verify_authenticated_principal=lambda: owner_user_id,
    )
    ledger.create_connection(
        connection_id=connection_id,
        owner_user_id=owner_user_id,
        connection_class="http",
        connection_type="http",
        auth_scheme="bearer",
        scopes=("http",) if model_access is None else ("GET", "POST"),
        provider="http",
        destination="compute:synthetic",
        credential_ref="vault://http/compute:synthetic",
        allowed_endpoints=[{
            "host": host,
            "path_template": "/v1/chat/completions",
            "methods": ["POST"],
        }] + ([{
            "host": host, "path_template": "/api/v1/models/user", "methods": ["GET"],
            "allowed_query": ["output_modalities"], "required_query": ["output_modalities"],
            "query_patterns": {"output_modalities": "^all$"},
        }] if model_access is not None else []),
    )
    ledger.grant_connection(
        grant_id=grant_id,
        connection_id=connection_id,
        owner_user_id=owner_user_id,
        universe_id=universe_id,
        unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )
    definition = register_definition(
        universe_id=universe_id,
        owner_user_id=owner_user_id,
        access_method="api_key_http",
        protocol="openai_chat",
        model=model,
        ref=grant_id,
    )
    if model_access is not None:
        ledger.configure_capability(
            connection_id=connection_id, capability_kind="model_discovery", enabled=True,
            descriptor={"protocol": "openrouter_user_models_v1",
                        "catalogue_url": "https://api.example.com/api/v1/models/user?output_modalities=all",
                        "benchmark_url": ""},
            expected_grant=ledger.get_grant(grant_id),
        )
    published = publish_definition(
        base_path,
        author_id=owner_user_id,
        payload={
            "schema_version": 1,
            "name": "Foreground open-provider agent",
            "description": "Synthetic foreground-run authority fixture.",
            "tags": ["test"],
            "components": {"identity": {"kind": "soul", "config": {}}},
        },
    )
    agent = create_binding(
        base_path,
        universe_id=universe_id,
        definition_id=published["agent_definition_id"],
        created_by=owner_user_id,
        payload={
            "schema_version": 1,
            "name": "Foreground open-provider agent",
            "role": "writer",
        },
    )
    if select_for_serving:
        connected = bind_serving_provider(
            base_path=base_path,
            universe_dir=universe_dir,
            owner_user_id=owner_user_id,
            universe_id=universe_id,
            agent_binding_id=agent["agent_binding_id"],
            expected_revision=1,
            provider=definition.id,
            model_access=None if model_access is None else {definition.id: model_access},
        )
        set_serving(
            base_path=base_path,
            universe_dir=universe_dir,
            owner_user_id=owner_user_id,
            universe_id=universe_id,
            agent_binding_id=agent["agent_binding_id"],
            expected_revision=connected["agent_binding"]["revision"],
            enabled=True,
        )
    return f"api_key_http:{definition.id}"


def _run_branch(
    tmp_path: Path,
    monkeypatch,
    authenticate_request,
    branch: BranchDefinition,
    *,
    authority_case: str = "active",
    mock_provider: bool = False,
    open_provider: bool = False,
    open_model: str = "synthetic-model",
    open_host: str = "api.example.com",
    open_router_resolution_refusal: bool = False,
    model_access=None,
    services=("codex",),
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
    selected_provider = "codex"
    providers = {
        name: _CountingProvider(name, after_provider_call)
        for name in ("claude-code" if service == "claude" else service for service in services)
    }
    provider_router = ProviderRouter(providers)
    # Readiness must see the same simulated executors used by the run, not
    # whichever native CLIs happen to be installed on the test host.
    monkeypatch.setattr(call_module, "get_provider_router", lambda: provider_router)
    if authority_case != "missing":
        if open_provider:
            selected_provider = _seed_open_serving_assignment(
                tmp_path,
                monkeypatch,
                select_for_serving=authority_case != "registered_only",
                model_access=model_access, model=open_model, host=open_host,
            )
        else:
            _seed_serving_assignment(tmp_path, model_access=model_access, services=services)
    provider_names = (
        [selected_provider] if open_provider
        else ["claude-code" if service == "claude" else service for service in services]
    )
    (universe_dir / "config.yaml").write_text(
        f"preferred_writer: {selected_provider}\n"
        "allowed_providers:\n" + "".join(f"  - {name}\n" for name in provider_names),
        encoding="utf-8",
    )
    if open_provider:
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

    if open_provider:
        providers = {name: _CountingProvider(name, after_provider_call) for name in provider_names}
        provider_router = ProviderRouter(providers)
    provider = providers[selected_provider]
    captured["providers"] = providers

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

    injected_provider_call = (
        (lambda prompt, _system="", **_kwargs: f"fixture:{prompt}")
        if mock_provider
        else governed_provider_call
    )
    monkeypatch.setattr(call_module, "call_provider", injected_provider_call)
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
    wait_for(response["run_id"], timeout=10)
    record = get_run(tmp_path, response["run_id"])
    assert record is not None
    response["terminal_status"] = record["status"]
    response["terminal_error"] = record["error"]
    return response, provider, captured


def test_mock_foreground_run_needs_no_serving_binding_or_run_receipt(
    tmp_path: Path,
    monkeypatch,
    authenticate_request,
) -> None:
    response, provider, captured = _run_branch(
        tmp_path,
        monkeypatch,
        authenticate_request,
        _branch(node_count=1),
        authority_case="missing",
        mock_provider=True,
    )

    assert response["terminal_status"] == "completed", response["terminal_error"]
    assert provider.calls == []
    assert len(captured["effects"]) == 1
    db = authority_db_path(tmp_path)
    if db.exists():
        with sqlite3.connect(db) as conn:
            receipt_table = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'table' AND name = 'provider_work_receipts'"
            ).fetchone()[0]
            assert receipt_table == 0 or conn.execute(
                "SELECT COUNT(*) FROM provider_work_receipts"
            ).fetchone()[0] == 0


def test_foreground_run_launches_active_serving_provider_and_settles_once(
    tmp_path: Path,
    monkeypatch,
    authenticate_request,
) -> None:
    response, provider, _captured = _run_branch(
        tmp_path,
        monkeypatch,
        authenticate_request,
        _branch(node_count=1),
    )

    assert response["terminal_status"] == "completed", response["terminal_error"]
    assert len(provider.calls) == 1
    conn = sqlite3.connect(authority_db_path(tmp_path))
    conn.row_factory = sqlite3.Row
    reservation = conn.execute(
        "SELECT state, actual_total_tokens, actual_cost_microunits "
        "FROM provider_invocation_reservations"
    ).fetchone()
    receipt = conn.execute(
        "SELECT work_item_kind, work_item_id FROM provider_work_receipts"
    ).fetchone()
    claim = conn.execute(
        "SELECT state FROM provider_work_execution_claims"
    ).fetchone()
    conn.close()

    assert dict(reservation) == {
        "state": "succeeded",
        "actual_total_tokens": 100,
        "actual_cost_microunits": 5,
    }
    assert dict(receipt) == {
        "work_item_kind": "run",
        "work_item_id": response["run_id"],
    }
    assert claim["state"] == "released"


@pytest.mark.parametrize("pin_provider", [False, True], ids=["default", "pinned"])
def test_accepted_native_model_manifest_preserves_foreground_execution(
    tmp_path: Path,
    monkeypatch,
    authenticate_request,
    pin_provider: bool,
) -> None:
    """Real bind/enable/run admission must compose, not just interactive chat."""
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_assignment_manifest import ModelAccess

    branch = _branch(node_count=1)
    if not pin_provider:
        branch.node_defs[0].llm_policy = None
    before = branch.to_dict()
    response, provider, _captured = _run_branch(
        tmp_path,
        monkeypatch,
        authenticate_request,
        branch,
        model_access={"codex": ModelAccess("explicit", ("",))},
    )
    assignment = load_provider_assignment(tmp_path, universe_id="universe_alice")
    assert assignment is not None and assignment.manifest_digest
    assert assignment.state == "ready"
    assert branch.to_dict() == before
    assert response["terminal_status"] == "completed", response["terminal_error"]
    assert len(provider.calls) == 1
    with sqlite3.connect(authority_db_path(tmp_path)) as conn:
        assert conn.execute(
            "SELECT state FROM provider_invocation_reservations"
        ).fetchall() == [("succeeded",)]


@pytest.mark.parametrize("rotated", [None, "codex", "claude-code"])
def test_manifest_run_uses_independent_members_under_one_receipt(
    tmp_path, monkeypatch, authenticate_request, rotated,
):
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_assignment_manifest import ModelAccess

    monkeypatch.setenv("TINYASSETS_ALLOW_CLAUDE_SERVING", "1")
    branch = _branch(node_count=2)
    branch.node_defs[1].llm_policy = {"preferred": {"provider": "claude-code"}}
    before = branch.to_dict()

    def after_call(provider, count):
        if rotated and provider == "codex" and count == 1:
            write_credential_vault(
                tmp_path / "universe_alice",
                [
                    {"credential_type": "llm_subscription", "service": "codex",
                     "auth_json_b64": "eyJyb3RhdGVkIjp0cnVlfQ==" if rotated == "codex" else "e30="},
                    {"credential_type": "llm_subscription", "service": "claude",
                     "oauth_token": "rotated-test-only" if rotated == "claude-code"
                     else "synthetic-claude-test-only"},
                ], owner_user_id="acct_alice", universe_id="universe_alice",
            )

    response, _provider, captured = _run_branch(
        tmp_path, monkeypatch, authenticate_request, branch,
        model_access={name: ModelAccess("explicit", ("",)) for name in ("codex", "claude-code")},
        services=("codex", "claude"), after_provider_call=after_call,
    )
    expected_status = "failed" if rotated == "claude-code" else "completed"
    assert response["terminal_status"] == expected_status, response["terminal_error"]
    assert branch.to_dict() == before
    assert {name: len(p.calls) for name, p in captured["providers"].items()} == {
        "codex": 1, "claude-code": int(rotated != "claude-code"),
    }
    assignment = load_provider_assignment(tmp_path, universe_id="universe_alice")
    assert assignment.provider == "codex"  # Work choice never rewrites the main choice.
    with sqlite3.connect(authority_db_path(tmp_path)) as conn:
        receipts = conn.execute("SELECT record_json FROM provider_work_receipts").fetchall()
        assert len(receipts) == 1
        receipt = json.loads(receipts[0][0])
        assert receipt["authority_scope"] == "manifest"
        assert receipt["binding_id"] is None and receipt["provider"] is None
        records = [json.loads(row[0]) for row in conn.execute(
            "SELECT record_json FROM provider_invocation_reservations ORDER BY ordinal"
        )]
    assert {r["receipt_id"] for r in records} == {receipt["receipt_id"]}
    expected_providers = ["codex"] if rotated == "claude-code" else ["codex", "claude-code"]
    assert [r["selection"]["provider"] for r in records] == expected_providers
    assert all(r["schema_version"] == 3 and r["state"] == "succeeded" for r in records)


@pytest.mark.parametrize("authority_case", ["revoked", "stale", "home_rebound"])
def test_manifest_run_still_refuses_invalid_owner_or_authority(
    tmp_path, monkeypatch, authenticate_request, authority_case,
):
    from tinyassets.provider_assignment_manifest import ModelAccess

    response, provider, _captured = _run_branch(
        tmp_path, monkeypatch, authenticate_request, _branch(node_count=1),
        authority_case=authority_case, model_access={"codex": ModelAccess("explicit", ("",))},
    )
    assert response["terminal_status"] == "failed"
    assert provider.calls == []


def test_manifest_work_model_pin_is_not_silently_replaced_by_native_default(
    tmp_path, monkeypatch, authenticate_request,
):
    from tinyassets.provider_assignment_manifest import ModelAccess

    branch = _branch(node_count=1)
    branch.node_defs[0].llm_policy["preferred"]["model"] = "unaccepted-model"
    response, provider, _captured = _run_branch(
        tmp_path, monkeypatch, authenticate_request, branch,
        model_access={"codex": ModelAccess("explicit", ("",))},
    )
    assert response["terminal_status"] == "failed"
    assert provider.calls == []


def test_parallel_manifest_members_share_one_work_receipt(
    tmp_path, monkeypatch, authenticate_request,
):
    from tinyassets.provider_assignment_manifest import ModelAccess

    monkeypatch.setenv("TINYASSETS_ALLOW_CLAUDE_SERVING", "1")
    branch = _branch(node_count=2)
    branch.node_defs[1].llm_policy = {"preferred": {"provider": "claude-code"}}
    branch.edges = [
        EdgeDefinition(from_node="START", to_node="n1"),
        EdgeDefinition(from_node="START", to_node="n2"),
        EdgeDefinition(from_node="n1", to_node="END"),
        EdgeDefinition(from_node="n2", to_node="END"),
    ]
    both_launched = threading.Barrier(2)
    response, _provider, captured = _run_branch(
        tmp_path, monkeypatch, authenticate_request, branch,
        model_access={name: ModelAccess("explicit", ("",)) for name in ("codex", "claude-code")},
        services=("codex", "claude"),
        after_provider_call=lambda _provider, _count: both_launched.wait(timeout=3),
    )
    assert response["terminal_status"] == "completed", response["terminal_error"]
    assert all(len(provider.calls) == 1 for provider in captured["providers"].values())
    with sqlite3.connect(authority_db_path(tmp_path)) as conn:
        assert conn.execute("SELECT count(*) FROM provider_work_receipts").fetchone() == (1,)
        assert conn.execute(
            "SELECT count(DISTINCT receipt_id), count(*), sum(actual_total_tokens) "
            "FROM provider_invocation_reservations WHERE state = 'succeeded'"
        ).fetchone() == (1, 2, 200)


@pytest.mark.parametrize("manifest", [False, True], ids=["legacy", "model-access"])
@pytest.mark.parametrize("invalidate", ["custody", "pause"])
def test_enabled_model_access_universe_remains_visible_to_background_scheduler(
    tmp_path: Path,
    authenticate_request,
    available_native_executors,
    manifest: bool,
    invalidate: str,
) -> None:
    """Successful enable cannot silently remove the universe from polling."""
    from tinyassets.daemon_server import set_founder_home
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.provider_serving_binding import (
        list_serving_universes,
        resolve_serving_agent_binding,
    )

    authenticate_request("acct_alice")
    set_founder_home(
        tmp_path, founder_sub="acct_alice", universe_id="universe_alice",
        platform_generated=True,
    )
    _seed_serving_assignment(
        tmp_path,
        model_access={"codex": ModelAccess("explicit", ("",))} if manifest else None,
    )
    agent = resolve_serving_agent_binding(
        tmp_path, universe_id="universe_alice", owner_user_id="acct_alice",
    )
    assert agent["status"] == "serving"
    assert list_serving_universes(tmp_path) == ["universe_alice"]

    # Inventory cannot become a launch grant or mint a work receipt.
    with sqlite3.connect(authority_db_path(tmp_path)) as conn:
        assert conn.execute("SELECT count(*) FROM provider_work_receipts").fetchone() == (0,)

    if invalidate == "pause":
        from tinyassets.provider_serving_binding import set_serving

        set_serving(
            base_path=tmp_path, universe_dir=tmp_path / "universe_alice",
            owner_user_id="acct_alice", universe_id="universe_alice",
            agent_binding_id=agent["agent_binding_id"],
            expected_revision=agent["revision"], enabled=False,
        )
        assert list_serving_universes(tmp_path) == []
        return

    # A credential rotation invalidates the accepted member until re-bound.
    from tinyassets.credential_vault import write_credential_vault

    write_credential_vault(
        tmp_path / "universe_alice",
        [{"credential_type": "llm_subscription", "service": "codex",
          "auth_json_b64": "eyJyb3RhdGVkIjp0cnVlfQ=="}],
        owner_user_id="acct_alice", universe_id="universe_alice",
    )
    assert list_serving_universes(tmp_path) == []


def test_manifest_enable_still_refuses_missing_native_executor(
    tmp_path: Path, authenticate_request, monkeypatch,
) -> None:
    from tinyassets.daemon_server import set_founder_home
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.provider_serving_binding import list_serving_universes

    authenticate_request("acct_alice")
    set_founder_home(
        tmp_path, founder_sub="acct_alice", universe_id="universe_alice",
        platform_generated=True,
    )
    monkeypatch.setattr(
        "tinyassets.providers.call.get_provider_router", lambda: ProviderRouter({}),
    )
    with pytest.raises(PermissionError, match="no eligible model"):
        _seed_serving_assignment(
            tmp_path, model_access={"codex": ModelAccess("explicit", ("",))},
        )
    assert list_serving_universes(tmp_path) == []


def test_scheduler_inventory_keeps_independent_member_after_anchor_rotation(
    tmp_path: Path, authenticate_request, monkeypatch, available_native_executors,
) -> None:
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.daemon_server import set_founder_home
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.provider_serving_binding import list_serving_universes

    authenticate_request("acct_alice")
    monkeypatch.setenv("TINYASSETS_ALLOW_CLAUDE_SERVING", "1")
    set_founder_home(
        tmp_path, founder_sub="acct_alice", universe_id="universe_alice",
        platform_generated=True,
    )
    _seed_serving_assignment(
        tmp_path, services=("codex", "claude"),
        model_access={name: ModelAccess("explicit", ("",)) for name in ("codex", "claude-code")},
    )
    assert list_serving_universes(tmp_path) == ["universe_alice"]
    write_credential_vault(
        tmp_path / "universe_alice",
        [
            {"credential_type": "llm_subscription", "service": "codex",
             "auth_json_b64": "eyJyb3RhdGVkIjp0cnVlfQ=="},
            {"credential_type": "llm_subscription", "service": "claude",
             "oauth_token": "synthetic-claude-test-only"},
        ],
        owner_user_id="acct_alice", universe_id="universe_alice",
    )
    assert list_serving_universes(tmp_path) == ["universe_alice"]


def test_foreground_run_refreshes_a_stale_run_binding_after_serving_rebind(
    tmp_path: Path,
    monkeypatch,
    authenticate_request,
) -> None:
    """A real credential rotation refreshes once, then exact children replay."""
    first, provider, _captured = _run_branch(
        tmp_path,
        monkeypatch,
        authenticate_request,
        _branch(node_count=1),
    )
    assert first["terminal_status"] == "completed", first["terminal_error"]

    from tinyassets.api import runs as api_runs
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.custom_agents import list_bindings
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving
    from tinyassets.provider_work_authority import provider_work_binding_id
    from tinyassets.runs import get_run, wait_for
    from tinyassets.storage.provider_work_authority import (
        SQLiteProviderWorkAuthorityStore,
    )

    binding_id = provider_work_binding_id(
        owner_user_id="acct_alice",
        universe_id="universe_alice",
        provider="codex",
        binding_class="run_graph",
    )
    store = SQLiteProviderWorkAuthorityStore(tmp_path)
    original_child = store.get(binding_id)
    assert original_child is not None

    # Rotate the actual deposited record, then drive the same public serving
    # rebind path that reconnecting the app uses.  This advances custody,
    # assignment, and parent-binding generations while leaving the deterministic
    # run-class child from the first run in place.
    write_credential_vault(
        tmp_path / "universe_alice",
        [{
            "credential_type": "llm_subscription",
            "service": "codex",
            "auth_json_b64": "eyJyb3RhdGVkIjp0cnVlfQ==",
        }],
        owner_user_id="acct_alice",
        universe_id="universe_alice",
    )
    serving = next(
        row
        for row in list_bindings(
            tmp_path, universe_id="universe_alice", limit=100,
        )
        if row["status"] == "serving" and row["created_by"] == "acct_alice"
    )
    rebound = bind_serving_provider(
        base_path=tmp_path,
        universe_dir=tmp_path / "universe_alice",
        owner_user_id="acct_alice",
        universe_id="universe_alice",
        agent_binding_id=serving["agent_binding_id"],
        expected_revision=int(serving["revision"]),
        provider="codex",
    )
    configured = rebound["agent_binding"]
    set_serving(
        base_path=tmp_path,
        universe_dir=tmp_path / "universe_alice",
        owner_user_id="acct_alice",
        universe_id="universe_alice",
        agent_binding_id=configured["agent_binding_id"],
        expected_revision=int(configured["revision"]),
        enabled=True,
    )
    assignment = load_provider_assignment(tmp_path, universe_id="universe_alice")
    assert assignment is not None
    assert assignment.generation > original_child.assignment_generation
    assert store.get(binding_id) == original_child, "serving rebind leaves the child stale"

    def run_again() -> dict[str, Any]:
        response = json.loads(api_runs._action_run_branch({
            "branch_def_id": "branch_foreground_1",
            "universe_id": "universe_alice",
        }))
        wait_for(response["run_id"], timeout=10)
        record = get_run(tmp_path, response["run_id"])
        assert record is not None
        return record

    second = run_again()
    assert second["status"] == "completed", second["error"]
    refreshed_child = store.get(binding_id)
    assert refreshed_child is not None
    assert refreshed_child.generation == original_child.generation + 1
    assert refreshed_child.assignment_generation == assignment.generation
    assert refreshed_child.assignment_digest == assignment.assignment_digest

    # A third admission sees an exact child. It must reuse it without another
    # rebind, otherwise a normal concurrent run would fence the prior receipt.
    third = run_again()
    assert third["status"] == "completed", third["error"]
    assert store.get(binding_id) == refreshed_child
    assert len(provider.calls) == 3


def test_foreground_run_launches_selected_open_provider_and_settles_once(
    tmp_path: Path,
    monkeypatch,
    authenticate_request,
) -> None:
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider

    proxy = _OpenProxy()
    snapshot_calls: list[object] = []
    monkeypatch.setattr(
        ApiKeyHttpProvider,
        "_resolve_proxy",
        accounting_resolver(lambda _self, **_kwargs: proxy),
    )
    monkeypatch.setattr(
        "tinyassets.credential_vault.snapshot_llm_subscription_credential",
        lambda **kwargs: snapshot_calls.append(kwargs),
    )
    response, substituted_provider, _captured = _run_branch(
        tmp_path,
        monkeypatch,
        authenticate_request,
        _branch(node_count=1),
        open_provider=True,
    )

    assert response["terminal_status"] == "completed", response["terminal_error"]
    assert substituted_provider.calls == []
    assert snapshot_calls == []
    assert len(proxy.calls) == 1
    verb, wire = proxy.calls[0]
    assert verb == "POST"
    assert wire["url"] == "https://api.example.com/v1/chat/completions"
    assert "authorization" not in {
        key.lower() for key in wire.get("headers", {})
    }
    with sqlite3.connect(authority_db_path(tmp_path)) as conn:
        reservation = conn.execute(
            "SELECT state FROM provider_invocation_reservations"
        ).fetchone()
    assert reservation == ("indeterminate",)


def test_foreground_open_provider_settles_fresh_resolution_refusal_before_launch(
    tmp_path: Path,
    monkeypatch,
    authenticate_request,
) -> None:
    response, substituted_provider, captured = _run_branch(
        tmp_path,
        monkeypatch,
        authenticate_request,
        _branch(node_count=1),
        open_provider=True,
        open_router_resolution_refusal=True,
    )

    assert response["terminal_status"] == "failed"
    assert "Connect your provider before running this command center" in response[
        "terminal_error"
    ]
    assert substituted_provider.calls == []
    assert captured["effects"] == []
    with sqlite3.connect(authority_db_path(tmp_path)) as conn:
        reservation = conn.execute(
            "SELECT state FROM provider_invocation_reservations"
        ).fetchone()
    assert reservation == ("cancelled_before_launch",)


def test_foreground_run_mints_one_carrier_per_node_and_refuses_n_plus_one(
    tmp_path: Path,
    monkeypatch,
    authenticate_request,
) -> None:
    response, provider, captured = _run_branch(
        tmp_path,
        monkeypatch,
        authenticate_request,
        _branch(node_count=2),
    )

    assert response["terminal_status"] == "completed", response["terminal_error"]
    assert len(provider.calls) == 2
    with pytest.raises(ProviderAuthorityHeldError):
        captured["provider_call"]("one call too many")
    with pytest.raises(PermissionError, match="operation"):
        captured["provider_call"]("wrong operation", operation="converse")

    conn = sqlite3.connect(authority_db_path(tmp_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT reservation_id, invocation_key, ordinal, state "
        "FROM provider_invocation_reservations ORDER BY ordinal"
    ).fetchall()
    conn.close()
    assert [row["ordinal"] for row in rows] == [1, 2]
    assert len({row["reservation_id"] for row in rows}) == 2
    assert len({row["invocation_key"] for row in rows}) == 2
    assert [row["state"] for row in rows] == ["succeeded", "succeeded"]


#: What each held case must SAY, not merely that it was held. Every one of
#: these used to read "Connect your provider before running this command center",
#: including on a universe that had -- live 2026-09-30, where the owner of a
#: connected, serving `api_key_http` source was sent to connect a provider
#: (tests/test_free_account_run_provider_parity.py). Only the two cases that
#: genuinely have no serving binding keep that sentence.
_HELD_CASE_REASON = {
    "missing": None,
    "registered_only": None,
    "stale": "provider assignment digest is invalid",
    "revoked": "connect your provider before enabling serving",
    "cross_universe": "foreground run is not the principal's own command center",
}


@pytest.mark.parametrize("authority_case", sorted(_HELD_CASE_REASON))
def test_foreground_run_authority_mismatch_launches_nothing_and_runs_no_effects(
    authority_case: str,
    tmp_path: Path,
    monkeypatch,
    authenticate_request,
) -> None:
    from tinyassets.providers.owner_binding import (
        AUTHORITY_HELD_DETAIL,
        CONNECT_PROVIDER_MESSAGE,
    )

    response, provider, captured = _run_branch(
        tmp_path,
        monkeypatch,
        authenticate_request,
        _branch(node_count=1),
        authority_case=authority_case,
        open_provider=authority_case == "registered_only",
    )

    assert response["terminal_status"] == "failed"
    error = response["terminal_error"]
    reason = _HELD_CASE_REASON[authority_case]
    if reason is None:
        assert CONNECT_PROVIDER_MESSAGE in error, error
    else:
        assert AUTHORITY_HELD_DETAIL + reason in error, error
        assert CONNECT_PROVIDER_MESSAGE not in error, error
    assert provider.calls == []
    assert captured["effects"] == []


def test_foreground_run_rejects_policy_outside_active_provider_before_effects(
    tmp_path: Path,
    monkeypatch,
    authenticate_request,
) -> None:
    branch = _branch(node_count=1)
    branch.node_defs[0].llm_policy = {
        "preferred": {"provider": "claude-code"},
    }
    response, provider, captured = _run_branch(
        tmp_path,
        monkeypatch,
        authenticate_request,
        branch,
    )

    assert response["terminal_status"] == "failed"
    assert provider.calls == []
    assert captured["effects"] == []


def test_foreground_run_rejects_branch_not_authored_by_authenticated_principal(
    tmp_path: Path,
    monkeypatch,
    authenticate_request,
) -> None:
    """Now refused at RESOLUTION rather than as a failed run.

    This used to create a run and let it terminate `failed`, which meant a branch the
    caller may not read was still loaded and a run row still written under their
    universe. The refusal now happens before the load, so there is nothing to write --
    and it reads as "not found", because saying "exists but not yours" is itself a
    disclosure.

    The property the test was written for is unchanged and stronger: the branch is not
    executed and no provider is called.

    This asserts only that no run is created -- via the helper's own `run_id` check,
    which is indirect and could in principle trip for another reason. The DIRECT
    assertion on the refusal body lives in
    `tests/test_branch_run_read_check.py::test_an_unreadable_branch_is_reported_as_not_found`,
    which is the canonical coverage; this one is kept so the original scenario stays
    represented at the provider-session layer.
    """
    with pytest.raises(AssertionError, match="run_id"):
        _run_branch(
            tmp_path,
            monkeypatch,
            authenticate_request,
            _branch(node_count=1, author="acct_bob"),
        )


def test_foreground_run_rejects_unsupported_provider_role_before_launch(
    tmp_path: Path,
    monkeypatch,
    authenticate_request,
) -> None:
    branch = _branch(node_count=1)
    branch.node_defs[0].model_hint = "reader"
    response, provider, captured = _run_branch(
        tmp_path,
        monkeypatch,
        authenticate_request,
        branch,
    )

    assert response["terminal_status"] == "failed"
    assert provider.calls == []
    assert captured["effects"] == []


def test_settlement_failure_names_its_cause() -> None:
    """A settlement failure must say WHY, not just that it happened.

    On 2026-08-27 every prompt-template run failed with the bare
    "provider invocation usage could not be settled". `settle()` alone has four
    distinct `PermissionError` exits -- carrier not server-owned, carrier has
    not launched, settlement is consumer-owned, no durable settler -- plus
    whatever the durable settler itself raises. The founder and the universe
    both spent two days unable to tell those apart.

    `__cause__` was always attached; the user-visible message is built from the
    string, so nothing surfaced it.
    """
    import inspect

    from tinyassets.providers import router as router_module

    source = inspect.getsource(router_module)
    marker = (
        'raise ProviderAuthorityHeldError(\n'
        '                    "provider invocation usage could not be settled: "'
    )
    assert marker in source, (
        "the settlement wrapper must append the cause to its message; a bare "
        "'could not be settled' is undiagnosable from the surface that shows it"
    )
    assert "f\"{type(exc).__name__}: {exc}\"" in source, (
        "the cause must include the exception TYPE -- four different "
        "PermissionErrors reach this path and only the message tells them apart"
    )


def test_async_sub_branch_gets_its_own_session_not_the_parents(
    tmp_path: Path, monkeypatch, authenticate_request
) -> None:
    """A child run must not be refused because the parent holds the session.

    `graph_compiler` passes the parent's ALREADY-PREPARED `provider_call`
    straight into `execute_branch_async` for an async sub-branch. That reaches
    `prepare()`'s "already bound" guard, and before this fix the child run was
    created FAILED before executing a single node.

    The guard is right and stays -- one session must never serve two runs,
    because its receipt and claim are minted against one run id. The fix is to
    mint a SECOND session for the child.

    Found by cross-family review of PR #2559 *after* it merged and deployed. It
    shipped because no test exercised an async sub-branch through a provider
    session -- this is that test.
    """
    from tinyassets.daemon_server import save_branch_definition
    from tinyassets.foreground_run_provider import (
        _session_from_provider_call,
        prepare_foreground_run_provider,
    )

    active_checks = []

    def check_active_parent(parent_wrapper):
        check_index = len(active_checks)
        active_checks.append(False)
        parent_session = _session_from_provider_call(parent_wrapper)
        assert parent_session is not None, "fixture did not produce a real bound session"

        # A real child run row: the child must validate against ITS OWN run, so a
        # made-up id proves nothing (and correctly fails "run record is missing").
        from tinyassets.runs import create_run, update_run_status

        child_branch = _branch(node_count=1)
        save_branch_definition(tmp_path, branch_def=child_branch.to_dict())
        child_run_id = create_run(
            tmp_path,
            branch_def_id=child_branch.branch_def_id,
            thread_id="thread-child",
            inputs={},
            actor="universe:universe_alice",
        )
        update_run_status(tmp_path, child_run_id, status="running")

        child_wrapper = prepare_foreground_run_provider(
            parent_wrapper,
            run_id=child_run_id,
            branch=child_branch,
            branch_version_id=None,
            allowed_statuses={"running", "queued"},
        )

        child_session = _session_from_provider_call(child_wrapper)
        assert child_session is not None, "child run got no session at all"
        assert child_session is not parent_session, (
            "the child reused the PARENT's session; its receipt and claim are minted "
            "against the parent's run id"
        )
        # The child must carry no authority inherited from the parent.
        assert child_session._receipt is None, "child inherited the parent's receipt"
        assert child_session._claim is None, "child inherited the parent's claim"
        # And the parent must be left intact for its own remaining nodes.
        assert _session_from_provider_call(parent_wrapper) is parent_session
        child_session.close()
        active_checks[check_index] = True

    response, _, captured = _run_branch(
        tmp_path, monkeypatch, authenticate_request, _branch(node_count=1),
        on_active_session=check_active_parent,
    )
    assert response["terminal_status"] == "completed", response
    assert active_checks and all(active_checks), "every callback must complete its assertions"
    parent_session = _session_from_provider_call(captured["provider_call"])
    with pytest.raises(ProviderAuthorityHeldError):
        parent_session._request_allocation.child()


def test_a_session_hidden_behind_an_extra_wrapper_is_refused_not_passed_through() -> None:
    """A wrapped wrapper must not silently hand the child the parent's session.

    `_session_from_provider_call` looked exactly one `.provider_call` deep. Add
    one forwarding wrapper and it found nothing, so `prepare_...` returned the
    call UNCHANGED -- and the child run then executed on the PARENT's prepared
    session, which is the authority bleed the sibling mint exists to prevent.
    Reachable by adding a single decorator.

    Nothing builds that shape today (api/runs.py constructs the wrapper
    directly), which is exactly why this is a test and not a bug report:
    refusing keeps it true. Cross-family review 2026-08-27, finding (d).
    """
    from tinyassets import foreground_run_provider as frp

    class _Forwarding:
        """One extra layer -- the shape the old lookup could not see past."""

        def __init__(self, inner):
            self.provider_call = inner

    session = object.__new__(frp._ForegroundRunProviderSession)
    direct = _Forwarding(session)
    hidden = _Forwarding(direct)

    # Depth 1 is the supported shape and still resolves.
    assert frp._locate_session(direct) == (session, 1)
    # Depth 2 is found, and reported as found -- not silently missed.
    assert frp._locate_session(hidden) == (session, 2)
    # A call with no session anywhere still passes through untouched.
    assert frp._locate_session(_Forwarding(_Forwarding(object()))) == (None, 0)

    with pytest.raises(PermissionError, match="unrecognised wrapper chain"):
        frp.prepare_foreground_run_provider(
            hidden,
            run_id="child-run",
            branch=None,
            branch_version_id=None,
            allowed_statuses={"running"},
        )


def test_an_ordinary_provider_call_is_still_left_alone() -> None:
    """Fail-closed must not become fail-on-everything."""
    from tinyassets import foreground_run_provider as frp

    plain = object()
    assert frp.prepare_foreground_run_provider(
        plain,
        run_id="r",
        branch=None,
        branch_version_id=None,
        allowed_statuses={"running"},
    ) is plain


def test_a_forged_outer_wrapper_cannot_be_rebound() -> None:
    """`_rebind` asserted a wrapper shape it never checked.

    The docstring said "the wrapper is a `UniverseBoundProviderCall`, which
    enforces one exact universe context and operation" -- but `replace()` was
    called on whatever arrived. Any dataclass exposing a real session as
    `.provider_call` passed depth 1 and was rebound, carrying whatever
    universe/operation semantics that type happened to have.

    It needed possession of a real session and the child still revalidated
    owner/run/branch, so it was never a demonstrated cross-tenant mint. It was
    an invariant the code asserted and did not enforce, which is its own bug.
    Cross-family review 2026-08-27, finding (c).
    """
    from dataclasses import dataclass

    from tinyassets import foreground_run_provider as frp

    session = object.__new__(frp._ForegroundRunProviderSession)

    @dataclass
    class _ForgedWrapper:
        """Right shape, wrong type -- and no universe binding to preserve."""

        provider_call: object
        universe_context: object = None
        operation: str = "run_graph"

    forged = _ForgedWrapper(provider_call=session)
    # It still looks like the supported shape to the locator...
    assert frp._locate_session(forged) == (session, 1)
    # ...and is refused anyway, on type.
    with pytest.raises(PermissionError, match="only an exact"):
        frp._rebind(forged, session)


def test_the_real_wrapper_still_rebinds_and_keeps_its_binding() -> None:
    """Fail-closed must not break the one shape that is supposed to work."""
    from tinyassets import foreground_run_provider as frp
    from tinyassets.providers.call import UniverseBoundProviderCall

    parent = object.__new__(frp._ForegroundRunProviderSession)
    child = object.__new__(frp._ForegroundRunProviderSession)
    sentinel = object()
    wrapper = UniverseBoundProviderCall(
        provider_call=parent, universe_context=sentinel, operation="run_graph"
    )

    rebound = frp._rebind(wrapper, child)
    assert type(rebound) is UniverseBoundProviderCall
    assert rebound.provider_call is child
    # The whole point: swapping the session must not swap the binding.
    assert rebound.universe_context is sentinel
    assert rebound.operation == "run_graph"


def test_foreground_claude_node_runs_in_its_universe_without_host_tools(
    tmp_path, monkeypatch, authenticate_request,
):
    """A workflow node's claude call is pinned to its owner's universe.

    Production 2026-09-24: prompt nodes launched `claude -p` in the daemon's
    cwd (`/app`) with the CLI's default builtins, and about half of a parallel
    probe's short nodes explored the platform source for 100-300s. Driven
    through the real foreground run path; the codex node shows the scope.
    """
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.providers.base import HOST_REACH_TOOLS
    from tinyassets.providers.claude_provider import _sandbox_cli_args

    monkeypatch.setenv("TINYASSETS_ALLOW_CLAUDE_SERVING", "1")
    branch = _branch(node_count=2)
    branch.node_defs[1].llm_policy = {"preferred": {"provider": "claude-code"}}
    response, _provider, captured = _run_branch(
        tmp_path, monkeypatch, authenticate_request, branch,
        model_access={name: ModelAccess("explicit", ("",)) for name in ("codex", "claude-code")},
        services=("codex", "claude"),
    )
    assert response["terminal_status"] == "completed", response["terminal_error"]

    [claude_config] = captured["providers"]["claude-code"].calls
    [codex_config] = captured["providers"]["codex"].calls
    # Both node calls are marked; each provider confines in its own way.
    assert claude_config.workflow_node is True
    assert codex_config.workflow_node is True

    universe_dir = tmp_path / "universe_alice"
    flags, run_cwd = _sandbox_cli_args(claude_config, universe_dir)
    assert run_cwd == str(universe_dir)
    assert flags[flags.index("--setting-sources") + 1] == ""
    denied = flags[flags.index("--disallowedTools") + 1:]
    assert set(HOST_REACH_TOOLS) <= set(denied)
    for tool in ("Bash", "Read", "Glob", "Grep"):
        assert tool in denied
    # Only host reach is removed: web tools and subagents are not the host's.
    for kept in ("WebSearch", "WebFetch", "Agent"):
        assert kept not in denied


@pytest.mark.parametrize("host,model,expected_calls", [
    ("openrouter.ai", "synthetic-model:free", 6),
    ("openrouter.ai", "synthetic-model", 8),
    ("api.example.com", "synthetic-model:free", 8),
])
def test_legacy_run_free_policy_uses_owned_source_and_exact_model(
    tmp_path, monkeypatch, authenticate_request, host, model, expected_calls,
):
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
    from tinyassets.storage.agent_request_usage import UsageStore

    proxy = _OpenProxy()
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy",
                        accounting_resolver(lambda _self, **_kwargs: proxy))
    response, substituted, captured = _run_branch(
        tmp_path, monkeypatch, authenticate_request, _branch(node_count=8),
        open_provider=True, open_model=model, open_host=host,
    )
    assert len(proxy.calls) == expected_calls
    assert not substituted.calls
    if expected_calls == 6:
        assert response["terminal_status"] == "failed"
        assert "request budget" in response["terminal_error"]
        assert captured["effects"] == []
    else:
        assert response["terminal_status"] == "completed", response["terminal_error"]
    stored = UsageStore(tmp_path).for_subject(
        "acct_alice", "universe_alice", "run", response["run_id"],
    )
    assert len(stored) == 1 and stored[0]["dispatched"] == expected_calls
    assert all(a["free"] is (expected_calls == 6) for a in stored[0]["attempts"])
