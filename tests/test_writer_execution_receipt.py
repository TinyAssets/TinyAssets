"""Request-local answer observation; no provider calls or account authority."""

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tinyassets.providers import call as calls
from tinyassets.providers.base import ProviderResponse
from tinyassets.providers.execution_receipt import WriterExecutionReceipt


def response(provider="owned", reported="actual"):
    return ProviderResponse(
        "reply", provider, "requested-alias", "family", 1.0, reported_model=reported
    )


@pytest.fixture
def router(monkeypatch):
    history = []

    def call_sync(role, prompt, system, **kwargs):
        history.append(prompt)
        return response(provider=prompt, reported=f"actual-{prompt}")

    router = SimpleNamespace(call_sync=call_sync)
    monkeypatch.setattr(calls, "_force_mock", False)
    monkeypatch.setattr(calls, "_real_router", router)
    monkeypatch.setattr(calls, "_register_open_providers_for", lambda _: None)
    return router, history


def test_alias_never_substitutes_for_reported_model():
    receipt = WriterExecutionReceipt()
    receipt.observe(response(reported=""))
    assert receipt.projection() == {"provider": "owned", "model": "", "model_status": "unknown"}
    assert ProviderResponse("a", "p", "legacy-model", "f", 1).reported_model == ""


def test_request_rides_beside_unknown_model_and_survives_storage():
    """A request is named apart from `model`; it never makes the status reported."""
    from tinyassets.providers.execution_receipt import (
        ExecutionReceipt,
        normalize_execution_receipt,
    )

    receipt = WriterExecutionReceipt()
    receipt.observe(replace(response(reported=""), requested_model="picked"))
    projected = receipt.projection()
    assert projected == {"provider": "owned", "model": "", "model_status": "unknown",
                         "requested_model": "picked"}
    # The stored row round-trips through the conversation store's dataclass.
    assert normalize_execution_receipt(projected) == projected
    assert normalize_execution_receipt(ExecutionReceipt(**projected)) == projected
    # Legacy rows without the key still normalize; a blank or malformed request
    # is a refusal, never a rendered blank.
    legacy = {"provider": "owned", "model": "", "model_status": "unknown"}
    assert normalize_execution_receipt(legacy) == legacy
    for bad in ("", "bad\nlabel", "a" * 201, 42):
        assert normalize_execution_receipt({**legacy, "requested_model": bad}) is None


@pytest.mark.parametrize("bad", [None, True, 123, "", " ", "bad\nlabel", "a" * 201])
def test_unusable_model_evidence_stays_unknown(bad):
    receipt = WriterExecutionReceipt()
    receipt.observe(response(reported=bad))
    assert receipt.projection()["model_status"] == "unknown"


def test_projection_is_detached_and_first_success_cannot_be_overwritten():
    receipt = WriterExecutionReceipt()
    assert receipt.projection() is None
    receipt.observe(response())
    receipt.projection()["provider"] = "tampered"
    receipt.observe(response("learning-provider", "learning-model"))
    assert receipt.projection() == {
        "provider": "owned",
        "model": "actual",
        "model_status": "reported",
    }


@pytest.mark.parametrize(
    "item",
    [
        None,
        {},
        response(""),
        response("bad\nprovider"),
        replace(response(), degraded=True),
        replace(response(), failure_class="provider_rate_limited"),
    ],
)
def test_non_success_is_not_an_answer_receipt(item):
    receipt = WriterExecutionReceipt()
    receipt.observe(item)
    assert receipt.projection() is None


def test_observer_failure_never_retries_or_discards_answer(router):
    def broken(_):
        raise RuntimeError("private callback detail must not be logged")

    assert calls.call_provider("writer", operation="converse", response_observer=broken) == "reply"
    assert router[1] == ["writer"]


def test_failed_call_cannot_reuse_prior_receipt(router):
    first, second = WriterExecutionReceipt(), WriterExecutionReceipt()
    calls.call_provider("writer", response_observer=first.observe)

    def fail(*args, **kwargs):
        raise RuntimeError("failed")

    router[0].call_sync = fail
    with pytest.raises(RuntimeError, match="failed"):
        calls.call_provider("second", operation="converse", response_observer=second.observe)
    assert second.projection() is None
    assert first.projection()["provider"] == "writer"


def test_mock_has_no_actual_provider_receipt(router, monkeypatch):
    monkeypatch.setattr(calls, "_force_mock", True)
    receipt = WriterExecutionReceipt()
    assert (
        calls.call_provider("test", fallback_response="mock", response_observer=receipt.observe)
        == "mock"
    )
    assert receipt.projection() is None
    assert router[1] == []


def test_interleaved_requests_capture_only_their_own_response(router):
    ready = threading.Barrier(2)

    def overlap(role, prompt, system, **kwargs):
        ready.wait(timeout=5)
        return response(provider=prompt, reported=f"actual-{prompt}")

    router[0].call_sync = overlap

    def invoke(identity):
        receipt = WriterExecutionReceipt()
        text = calls.call_provider(
            identity, operation="converse", response_observer=receipt.observe
        )
        return text, receipt.projection()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(invoke, ["owner-a", "owner-b"]))
    for identity, (text, receipt) in zip(["owner-a", "owner-b"], results):
        assert text == "reply"
        assert receipt["provider"] == identity
        assert receipt["model"] == f"actual-{identity}"


def test_usage_projection_includes_failures_and_internal_work_without_an_answer(tmp_path):
    from tinyassets.providers.execution_receipt import ExecutionReceipt, normalize_execution_receipt
    from tinyassets.request_budget import TurnRequestBudget
    from tinyassets.storage.agent_request_usage import UsageStore

    budget = TurnRequestBudget("owner", "home")
    budget.persist(tmp_path)
    for source, purpose, outcome in (("source-a", "reply", "failed"),
                                     ("source-b", "review", "unknown")):
        ordinal = budget.reserve(owner="owner", universe="home", source_ref=source,
                                 model="model", free=True, purpose=purpose)
        budget.dispatched(ordinal)
        budget.settle(ordinal, outcome)
    collector = WriterExecutionReceipt()
    collector.observe(replace(response(), degraded=True, request_receipt=budget.receipt()))
    projection = collector.projection()
    assert set(projection) == {"usage"}
    assert projection["usage"]["dispatched"] == 2
    assert projection["usage"]["quota_authoritative"] is False
    assert [(source["source_ref"], source["purpose"], source["failed"], source["unknown"])
            for source in projection["usage"]["sources"]] == [
        ("source-a", "reply", 1, 0), ("source-b", "review", 0, 1),
    ]
    assert normalize_execution_receipt(ExecutionReceipt(**projection)) == projection
    projection["usage"]["sources"][0]["failed"] = 123
    assert collector.projection()["usage"]["sources"][0]["failed"] == 1
    durable = UsageStore(tmp_path).receipt(budget._scope)
    assert durable["usage_id"] == collector.projection()["usage"]["usage_id"]
    assert len(durable["attempts"]) == 2
    budget.close()


def test_answer_labels_survive_usage_and_later_learning_cannot_replace_them():
    from tinyassets.request_budget import TurnRequestBudget

    budget = TurnRequestBudget("owner", "home")
    ordinal = budget.reserve(owner="owner", universe="home", source_ref="local",
                             model="model", free=False)
    budget.dispatched(ordinal)
    budget.settle(ordinal, "succeeded")
    collector = WriterExecutionReceipt()
    collector.observe(replace(response(), request_receipt=budget.receipt()))
    collector.observe(response("learning", "learning-model"))
    projected = collector.projection()
    assert (projected["provider"], projected["model"]) == ("owned", "actual")
    assert projected["usage"]["dispatched"] == 1


def test_large_paid_usage_preserves_totals_without_overflowing_history_metadata():
    import json

    from tinyassets.providers.execution_receipt import normalize_execution_receipt
    from tinyassets.request_budget import TurnRequestBudget

    budget = TurnRequestBudget("owner", "home")
    for number in range(100):
        ordinal = budget.reserve(owner="owner", universe="home", source_ref=f"paid-{number}",
                                 model="model", free=False)
        budget.dispatched(ordinal)
        budget.settle(ordinal, "succeeded")
    collector = WriterExecutionReceipt()
    collector.observe(replace(response(), request_receipt=budget.receipt()))
    projected = collector.projection()
    assert projected["usage"]["dispatched"] == 100
    assert projected["usage"]["sources_omitted"] > 0
    assert len(projected["usage"]["sources"]) + projected["usage"]["sources_omitted"] == 100
    assert len(json.dumps(projected)) < 4096
    assert normalize_execution_receipt(projected) == projected


@pytest.mark.parametrize("change", [
    {"quota_authoritative": True}, {"dispatched": True}, {"dispatched": -1},
    {"sources": [{"prompt": "must not retain"}]}, {"usage_id": "wrong"},
])
def test_usage_projection_rejects_malformed_optional_evidence(change):
    from tinyassets.providers.execution_receipt import normalize_execution_receipt

    usage = dict(reserved=0, dispatched=0, closed=True, sources=[], sources_omitted=0,
                 quota_authoritative=False, count_basis="local_provider_dispatch")
    assert normalize_execution_receipt({"usage": {**usage, **change}}) is None
