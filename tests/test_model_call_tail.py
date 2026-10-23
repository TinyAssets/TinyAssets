"""Owner helpers and declared fallbacks cross the same allocation boundary."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_selected_model_authority import reader, rig, served  # noqa: F401
from tinyassets.exceptions import AllProvidersExhaustedError, ProviderAuthorityHeldError
from tinyassets.providers import model_call
from tinyassets.providers.base import ProviderResponse, UniverseContext
from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic
from tinyassets.providers.model_policy import ModelRef

pytestmark = pytest.mark.verifies("shape-convergence:C3")


@pytest.mark.parametrize("sync", [False, True])
def test_real_router_returns_owner_allocation_receipt(served, sync):  # noqa: F811
    context = served.context
    request = model_call.ModelCall(context.model_selection, (), model_call.ModelContext(
        prompt="reply", universe_context=context, operation="converse", router=served.router))
    parent = model_call.Parent("owner", context.universe_dir.name)
    result = (model_call.call_sync(request, parent) if sync
              else asyncio.run(model_call.call(request, parent)))
    assert result.text == "selected answer"
    assert result.receipt.requested_model == context.model_selection.model_id
    assert result.receipt.reported_model == "actual-answer-model"
    assert result.receipt.parent_usage_id
    assert result.response.execution_receipt == result.receipt
    assert len(served.wire) == 1


@pytest.fixture
def transport(tmp_path, monkeypatch):
    calls = []
    failures = {}

    def complete(role, prompt, system, config, *, operation, universe_context, **kwargs):
        selection = universe_context.model_selection
        calls.append((selection, config.request_budget, operation, universe_context))
        if selection in failures:
            raise failures[selection]
        return ProviderResponse(text='{}', provider=selection.connection_id,
                                model=selection.model_id, reported_model=selection.model_id,
                                family="test", latency_ms=0)

    async def call(*args, **kwargs):
        return complete(*args, **kwargs)

    cooled = []
    router = SimpleNamespace(call=call, call_sync=complete,
                             cool_source=lambda *args, **kwargs: cooled.append((args, kwargs)),
                             selected_agent_execution_kind=lambda ref: "engine_inference")
    from tinyassets.providers import call as bridge

    monkeypatch.setattr(bridge, "get_provider_router", lambda: router)
    monkeypatch.setattr(bridge, "register_open_providers_for", lambda context: None)
    context = UniverseContext(
        universe_dir=tmp_path / "center",
        served_provider=SimpleNamespace(owner_user_id="owner"),
        model_selection=ModelRef("source-a", "first"),
    )
    return context, calls, failures, cooled


@pytest.fixture
def request_transport(transport, monkeypatch):
    from tinyassets.auth import middleware

    context, calls, failures, cooled = transport
    carrier = SimpleNamespace(agent_binding_id="binding", binding_revision=1)
    monkeypatch.setattr(middleware, "validate_provider_request_carrier",
                        lambda *args, **kwargs: SimpleNamespace(principal_id="owner"))
    return replace(context, served_provider=None, provider_request=carrier), calls, failures, cooled


def _failed_attempt(ref, *, state="none", failure_class="provider_rate_limited"):
    return ProviderAttemptDiagnostic(
        provider=ref.connection_id, status="failed", skip_class="quota_or_cooldown",
        side_effect_state=state, failure_class=failure_class, capacity_scope="unknown",
    )


@pytest.mark.parametrize("sync", [False, True])
def test_declared_fallbacks_share_allocation_and_return_receipt(request_transport, sync):
    context, calls, failures, cooled = request_transport
    primary = context.model_selection
    fallback = ModelRef("source-b", "second")
    failures[primary] = AllProvidersExhaustedError(
        "unavailable", attempts=[_failed_attempt(primary)])
    request = model_call.ModelCall(primary, (fallback,), model_call.ModelContext(
        prompt="review", universe_context=context, operation="converse"))
    parent = model_call.Parent("owner", "center")
    result = (model_call.call_sync(request, parent) if sync
              else asyncio.run(model_call.call(request, parent)))
    assert [item[0] for item in calls] == [primary, fallback]
    assert calls[0][1] is calls[1][1]
    assert calls[0][1].owner == "owner"
    assert result.receipt.requested_model == primary.model_id
    assert result.receipt.resolved_model == fallback.model_id
    assert result.receipt.reported_model == fallback.model_id
    assert result.receipt.parent_usage_id == calls[0][1].usage_id
    assert cooled == [((primary.connection_id,), {
        "owner": "owner", "retry_after_s": None, "reason": "provider_rate_limited",
    })]


@pytest.mark.parametrize("failure_class", ["provider_refused", "auth_invalid"])
def test_request_fallback_accepts_known_unsent_refusal(request_transport, failure_class):
    context, calls, failures, cooled = request_transport
    primary = context.model_selection
    fallback = ModelRef("source-b", "second")
    failures[primary] = AllProvidersExhaustedError(
        "unavailable", attempts=[_failed_attempt(primary, failure_class=failure_class)])
    request = model_call.ModelCall(primary, (fallback,), model_call.ModelContext(
        prompt="review", universe_context=context, operation="converse"))
    assert model_call.call_sync(request, model_call.Parent("owner", "center")).text == "{}"
    assert [item[0] for item in calls] == [primary, fallback]
    assert not cooled


@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("failure", [PermissionError("authority ended"),
                                    AllProvidersExhaustedError("unavailable")])
def test_no_implicit_fallback_or_authority_widening(transport, sync, failure):
    context, calls, failures, _ = transport
    primary = context.model_selection
    failures[primary] = failure
    fallbacks = (ModelRef("source-b", "second"),) if isinstance(failure, PermissionError) else ()
    request = model_call.ModelCall(primary, fallbacks, model_call.ModelContext(
        prompt="review", universe_context=context, operation="converse"))
    parent = model_call.Parent("owner", "center")
    with pytest.raises(type(failure)):
        if sync:
            model_call.call_sync(request, parent)
        else:
            asyncio.run(model_call.call(request, parent))
    assert len(calls) == 1


@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("authority", ["served", "invocation"])
def test_bound_authority_does_not_enter_declared_fallback(transport, sync, authority):
    context, calls, failures, _ = transport
    primary = context.model_selection
    if authority == "invocation":
        context = replace(context, served_provider=None,
                          provider_invocation=SimpleNamespace(
                              _receipt=SimpleNamespace(principal_id="owner")))
    failure = AllProvidersExhaustedError(
        "unavailable", attempts=[_failed_attempt(primary)])
    failures[primary] = failure
    request = model_call.ModelCall(primary, (ModelRef("source-b", "second"),),
                                   model_call.ModelContext(
                                       prompt="review", universe_context=context,
                                       operation="converse"))
    with pytest.raises(AllProvidersExhaustedError) as caught:
        if sync:
            model_call.call_sync(request, model_call.Parent("owner", "center"))
        else:
            asyncio.run(model_call.call(request, model_call.Parent("owner", "center")))
    assert caught.value is failure
    assert len(calls) == 1


def test_workflow_policy_uses_run_carrier_only(transport):
    context, calls, failures, _ = transport
    primary = context.model_selection
    context = replace(context, served_provider=None,
                      provider_invocation=SimpleNamespace(
                          _receipt=SimpleNamespace(principal_id="owner")))
    failure = AllProvidersExhaustedError(
        "unavailable", attempts=[_failed_attempt(primary)])
    failures[primary] = failure
    with pytest.raises(AllProvidersExhaustedError) as caught:
        model_call.text_sync(
            "review", universe_context=context, operation="run_graph",
            policy={"preferred": {"provider": primary.connection_id,
                                  "model_id": primary.model_id},
                    "fallback_chain": [{"provider": "source-b", "model_id": "second"}]},
        )
    assert caught.value is failure
    assert [item[0] for item in calls] == [primary]


@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("state,failure_class", [
    ("possible", "provider_rate_limited"), (None, "provider_rate_limited"),
    ("none", "provider_idle_timeout"),
])
def test_request_fallback_requires_proven_unsent_known_failure(
    request_transport, sync, state, failure_class,
):
    context, calls, failures, cooled = request_transport
    primary = context.model_selection
    failure = AllProvidersExhaustedError(
        "unavailable", attempts=[_failed_attempt(
            primary, state=state, failure_class=failure_class)])
    failures[primary] = failure
    request = model_call.ModelCall(primary, (ModelRef("source-b", "second"),),
                                   model_call.ModelContext(
                                       prompt="review", universe_context=context,
                                       operation="converse"))
    with pytest.raises(AllProvidersExhaustedError) as caught:
        if sync:
            model_call.call_sync(request, model_call.Parent("owner", "center"))
        else:
            asyncio.run(model_call.call(request, model_call.Parent("owner", "center")))
    assert caught.value is failure
    assert len(calls) == 1
    assert not cooled


def test_request_fallback_requires_every_attempt_unsent(request_transport):
    context, calls, failures, cooled = request_transport
    primary = context.model_selection
    failure = AllProvidersExhaustedError("unavailable", attempts=[
        _failed_attempt(primary), _failed_attempt(primary, state="committed"),
    ])
    failures[primary] = failure
    request = model_call.ModelCall(primary, (ModelRef("source-b", "second"),),
                                   model_call.ModelContext(
                                       prompt="review", universe_context=context,
                                       operation="converse"))
    with pytest.raises(AllProvidersExhaustedError) as caught:
        model_call.call_sync(request, model_call.Parent("owner", "center"))
    assert caught.value is failure
    assert len(calls) == 1
    assert not cooled


def test_final_request_candidate_cools_withheld_source(request_transport):
    context, calls, failures, cooled = request_transport
    primary = context.model_selection
    failure = AllProvidersExhaustedError(
        "unavailable", attempts=[_failed_attempt(primary)])
    failures[primary] = failure
    request = model_call.ModelCall(primary, (), model_call.ModelContext(
        prompt="review", universe_context=context, operation="converse"))
    with pytest.raises(AllProvidersExhaustedError):
        model_call.call_sync(request, model_call.Parent("owner", "center"))
    assert len(calls) == 1
    assert cooled == [((primary.connection_id,), {
        "owner": "owner", "retry_after_s": None, "reason": "provider_rate_limited",
    })]


@pytest.mark.parametrize("helper", ["critique", "reflection", "raptor", "search", "learning"])
def test_each_helper_requires_owner_authority(transport, helper, tmp_path, monkeypatch):
    context, calls, _, _ = transport

    def invoke(ctx):
        if helper in {"critique", "reflection"}:
            from tinyassets.memory.reflexion import ReflexionEngine

            if helper == "critique":
                return ReflexionEngine._llm_critique({}, [], "feedback")
            return ReflexionEngine._llm_reflection("critique", {})
        if helper == "search":
            from tinyassets.retrieval.agentic_search import _build_provider_call

            return asyncio.run(_build_provider_call()("query", "decompose", "writer"))
        if helper == "learning":
            from tinyassets.universe_intelligence import extract_learning

            return extract_learning("remember this", "reply", ctx)
        from tinyassets.knowledge import raptor

        # Exercise the production summarizer binding without clustering noise.
        async def build(**kwargs):
            await kwargs["provider_call"]("paragraphs", "summarize", "writer")
            return SimpleNamespace(nodes=[], depth=0)

        monkeypatch.setattr(raptor, "build_raptor_tree", build)
        monkeypatch.setattr(raptor, "_read_canon_paragraphs", lambda _: ["a", "b"])
        return raptor.rebuild_raptor_from_canon(str(tmp_path), embed_fn=lambda _: [1., 0.])

    with pytest.raises(ProviderAuthorityHeldError):
        invoke(replace(context, served_provider=None))
    assert not calls
    bound = model_call.bind_model_call(model_call.text_sync, context, operation="converse")
    with model_call.owner_model_scope(bound):
        invoke(context)
    assert len(calls) == 1
    assert calls[0][1].owner == "owner"
    assert calls[0][2] == "converse"
    assert calls[0][3].universe_dir == context.universe_dir
    with pytest.raises(ProviderAuthorityHeldError):
        invoke(replace(context, served_provider=None))
    assert len(calls) == 1
