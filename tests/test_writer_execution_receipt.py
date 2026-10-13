"""The call receipt distinguishes choice, dispatch and observed answer."""

from dataclasses import replace

import pytest

from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.providers.base import ModelConfig, ProviderResponse, UniverseContext
from tinyassets.providers.call import get_provider_router, set_provider_router
from tinyassets.providers.execution_receipt import (
    normalize_execution_receipt,
    receipt_from_response,
)
from tinyassets.providers.model_call import (
    ModelCall,
    ModelContext,
    Parent,
    call_sync,
    open_allocation,
)
from tinyassets.providers.model_policy import ModelRef


def test_receipt_never_promotes_configured_identity_or_unknown_usage():
    response = ProviderResponse(
        "answer", "owned", "configured-alias", "gpt", 1,
        requested_model="requested-alias", reported_model="",
    )
    receipt = normalize_execution_receipt(receipt_from_response(response))
    assert receipt["requested_model"] == "requested-alias"
    assert receipt["resolved_model"] == "configured-alias"
    assert receipt["model_status"] == "unknown"
    assert "reported_model" not in receipt
    assert receipt["usage_provenance"] == "unknown"
    assert "input_tokens" not in receipt and "cost_microunits" not in receipt


def test_reported_model_and_zero_provider_reported_tokens_are_distinct():
    response = ProviderResponse(
        "answer", "owned", "configured-alias", "gpt", 1,
        reported_model="actual-gpt", input_tokens=0, output_tokens=3,
    )
    receipt = normalize_execution_receipt(receipt_from_response(response))
    assert receipt["reported_model"] == "actual-gpt"
    assert receipt["model"] == "actual-gpt"
    assert receipt["usage_provenance"] == "provider_reported"
    assert receipt["input_tokens"] == 0 and receipt["output_tokens"] == 3


def test_single_call_returns_response_and_receipt_with_explicit_selection(
    tmp_path, monkeypatch,
):
    from tinyassets.providers import call as registry

    seen = []

    class Router:
        def call_sync(self, role, prompt, system, config, **kwargs):
            seen.append(kwargs["universe_context"].model_selection)
            return ProviderResponse(
                "answer", "owned", "resolved-gpt", "gpt", 1,
                reported_model="reported-gpt", input_tokens=4,
            )

    previous = get_provider_router()
    set_provider_router(Router())
    monkeypatch.setattr(registry, "register_open_providers_for", lambda _: None)
    budget = open_allocation("owner", tmp_path / "center")
    try:
        selected = ModelRef("owned", "requested-gpt")
        request = ModelCall(
            model=selected, fallbacks=(),
            context=ModelContext(
                prompt="review", config=ModelConfig(request_budget=budget),
                universe_context=UniverseContext(
                    universe_dir=tmp_path / "center",
                ), operation="run_graph",
            ),
        )
        result = call_sync(request, Parent(owner="owner", command_center="center"))
        assert result.text == "answer"
        assert seen == [selected]
        receipt = normalize_execution_receipt(result.receipt)
        assert receipt["requested_model"] == "requested-gpt"
        assert receipt["resolved_model"] == "resolved-gpt"
        assert receipt["reported_model"] == "reported-gpt"
    finally:
        budget.close()
        set_provider_router(previous)


def test_call_refuses_an_unbound_or_cross_center_parent(tmp_path):
    context = ModelContext(prompt="review", universe_context=UniverseContext(
        universe_dir=tmp_path / "center",
    ))
    request = ModelCall(model=None, fallbacks=(), context=context)
    with pytest.raises(PermissionError, match="command center"):
        call_sync(request, Parent(owner="owner", command_center="other"))
    with pytest.raises(ProviderAuthorityHeldError, match="owner-bound"):
        call_sync(replace(request, context=ModelContext(prompt="review")),
                  Parent(owner="owner", command_center="center"))


def test_failed_call_does_not_replay_a_single_use_parent(tmp_path, monkeypatch):
    from tinyassets.providers import call as registry

    class RefusingRouter:
        attempts = 0

        def call_sync(self, *args, **kwargs):
            self.attempts += 1
            raise RuntimeError("the original refusal")

    router = RefusingRouter()
    previous = get_provider_router()
    set_provider_router(router)
    monkeypatch.setattr(registry, "register_open_providers_for", lambda _: None)
    budget = open_allocation("owner", tmp_path / "center")
    try:
        request = ModelCall(
            model=None, fallbacks=(),
            context=ModelContext(
                prompt="review", config=ModelConfig(request_budget=budget),
                universe_context=UniverseContext(
                    universe_dir=tmp_path / "center",
                ), operation="run_graph",
            ),
        )
        with pytest.raises(RuntimeError, match="original refusal"):
            call_sync(request, Parent(owner="owner", command_center="center"))
        assert router.attempts == 1
    finally:
        budget.close()
        set_provider_router(previous)
