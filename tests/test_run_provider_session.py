from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import tinyassets.platform_runtime_provenance as platform_runtime_provenance
from tests.inference_usage_helpers import accounting_resolver
from tests.support.http_model import HTTP_PROVIDER
from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
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
    open_provider: bool = True,
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


def test_foreground_run_launches_selected_open_provider_and_settles_once(
    tmp_path: Path,
    monkeypatch,
    authenticate_request,
) -> None:
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider

    proxy = _OpenProxy()
    monkeypatch.setattr(
        ApiKeyHttpProvider,
        "_resolve_proxy",
        accounting_resolver(lambda _self, **_kwargs: proxy),
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


#: What each held case must SAY, not merely that it was held. Every one of
#: these used to read "Connect your provider before running this command center",
#: including on a universe that had -- live 2026-09-30, where the owner of a
#: connected, serving `api_key_http` source was sent to connect a provider
#: (tests/test_free_account_run_provider_parity.py). Only the two cases that
#: genuinely have no serving binding keep that sentence.
_HELD_CASE_REASON = {"missing": "no connected model source for this owner"}


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

    The docstring said "the wrapper is a `BoundModelCall`, which
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
    from tinyassets.providers.model_call import BoundModelCall

    parent = object.__new__(frp._ForegroundRunProviderSession)
    child = object.__new__(frp._ForegroundRunProviderSession)
    sentinel = object()
    wrapper = BoundModelCall(
        runner=parent, universe_context=sentinel, operation="run_graph"
    )

    rebound = frp._rebind(wrapper, child)
    assert type(rebound) is BoundModelCall
    assert rebound.runner is child
    # The whole point: swapping the session must not swap the binding.
    assert rebound.universe_context is sentinel
    assert rebound.operation == "run_graph"


@pytest.mark.parametrize("host,model,expected_calls", [
    ("openrouter.ai", "synthetic-model:free", 8),
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
    assert len(proxy.calls) == expected_calls, response
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
