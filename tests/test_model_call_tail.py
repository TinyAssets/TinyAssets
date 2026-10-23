"""Owner helpers and declared fallbacks cross the same allocation boundary."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_selected_model_authority import reader, rig, served  # noqa: F401
from tinyassets.exceptions import AllProvidersExhaustedError, ProviderAuthorityHeldError
from tinyassets.providers import model_call
from tinyassets.providers.base import ProviderResponse, UniverseContext
from tinyassets.providers.model_policy import ModelRef


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

    router = SimpleNamespace(call=call, call_sync=complete)
    from tinyassets.providers import call as bridge

    monkeypatch.setattr(bridge, "get_provider_router", lambda: router)
    monkeypatch.setattr(bridge, "register_open_providers_for", lambda context: None)
    context = UniverseContext(
        universe_dir=tmp_path / "center",
        served_provider=SimpleNamespace(owner_user_id="owner"),
        model_selection=ModelRef("source-a", "first"),
    )
    return context, calls, failures


@pytest.mark.parametrize("sync", [False, True])
def test_declared_fallbacks_share_allocation_and_return_receipt(transport, sync):
    context, calls, failures = transport
    primary = context.model_selection
    fallback = ModelRef("source-b", "second")
    failures[primary] = AllProvidersExhaustedError("unavailable")
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


@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("failure", [PermissionError("authority ended"),
                                    AllProvidersExhaustedError("unavailable")])
def test_no_implicit_fallback_or_authority_widening(transport, sync, failure):
    context, calls, failures = transport
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


@pytest.mark.parametrize("helper", ["critique", "reflection", "raptor", "search", "learning"])
def test_each_helper_requires_owner_authority(transport, helper, tmp_path, monkeypatch):
    context, calls, _ = transport

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
