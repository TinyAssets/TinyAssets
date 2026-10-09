"""Real foreground admission and HTTP execution with synthetic network only."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.inference_usage_helpers import accounting_resolver
from tests.test_discovery_snapshot import _model
from tests.test_run_provider_session import _branch, _run_branch
from tinyassets.foreground_run_provider import _ForegroundRunProviderSession
from tinyassets.provider_assignment import provider_assignment_admission
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.providers import discovery_snapshot
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.storage.provider_work_authority import db_path

pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture
def http_wire(tmp_path, monkeypatch):
    from tinyassets.background_served_provider import _BackgroundAssignedProviderSession

    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    reads, writes, errors = [], [], []
    original_call = _ForegroundRunProviderSession._call

    def call(*args, **kwargs):
        try:
            return original_call(*args, **kwargs)
        except Exception as exc:
            import traceback

            errors.append("".join(traceback.format_exception(exc)))
            raise

    monkeypatch.setattr(_ForegroundRunProviderSession, "_call", call)
    original_background = _BackgroundAssignedProviderSession._call

    def background_call(*args, **kwargs):
        try:
            return original_background(*args, **kwargs)
        except Exception as exc:
            import traceback

            errors.append("".join(traceback.format_exception(exc)))
            raise

    monkeypatch.setattr(_BackgroundAssignedProviderSession, "_call", background_call)

    def check_unlocked():
        with provider_assignment_admission().exclusive(tmp_path / "universe_alice"):
            with sqlite3.connect(db_path(tmp_path), timeout=0.1) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.rollback()

    def read(**kwargs):
        # A second thread can enter both fences while remote discovery runs.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(check_unlocked).result(timeout=2)
        reads.append(kwargs)
        return {"data": [_model("synthetic-model"), _model("future-company/new-choice")]}

    class Proxy:
        def close(self):
            pass

        def request(self, verb, document):
            writes.append((verb, document))
            return {
                "status": 200,
                "body": json.dumps(
                    {
                        "model": "observed-answer-model",
                        "choices": [{"message": {"content": "selected workflow answer"}}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "cost": 0},
                    }
                ),
            }

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", read)
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy",
                        accounting_resolver(lambda *a, **k: Proxy()))
    return reads, writes, errors


@pytest.mark.parametrize("model", [None, "future-company/new-choice"])
def test_foreground_http_selection_reaches_exact_model(
    tmp_path,
    monkeypatch,
    authenticate_request,
    http_wire,
    model,
):
    from tinyassets.provider_work_authority import ProviderInvocationCarrier

    provenance = []
    original_validate = ProviderInvocationCarrier.validate_for_call

    def capture(carrier, **kwargs):
        selected = carrier._reservation.selection
        assert selected is not None
        assert carrier._receipt.binding_id is None  # Aggregate is not the member.
        assert carrier.binding_id == selected.binding_id
        assert carrier.binding_generation == selected.binding_generation
        assert carrier.binding_digest == selected.binding_digest
        provenance.append((carrier.work_receipt_id, carrier.reservation_id))
        return original_validate(carrier, **kwargs)

    monkeypatch.setattr(ProviderInvocationCarrier, "validate_for_call", capture)
    branch = _branch(node_count=2)
    for node in branch.node_defs:
        node.llm_policy = {
            "preferred": {} if model is None else {"model": model},
            "fallback_chain": [],
        }
    result, _, _ = _run_branch(
        tmp_path,
        monkeypatch,
        authenticate_request,
        branch,
        open_provider=True,
        model_access=ModelAccess("discovered"),
    )
    reads, writes, errors = http_wire
    assert result["terminal_status"] == "completed", (result["terminal_error"], errors)
    # Serving readiness, the run's own captured model order, then one refresh
    # per actual attempt. The order's read is what makes an unpinned node
    # resolve from the account's FRESH catalogue instead of the source's
    # declared default (tests/test_free_account_run_provider_parity.py).
    assert len(reads) == 4
    assert len(writes) == 2
    assert all(verb == "POST" for verb, _ in writes)
    assert [doc["body"]["model"] for _, doc in writes] == [model or "synthetic-model"] * 2
    with sqlite3.connect(db_path(tmp_path)) as conn:
        receipts = [
            json.loads(row[0])
            for row in conn.execute("SELECT record_json FROM provider_work_receipts")
        ]
        reservations = [
            json.loads(row[0])
            for row in conn.execute(
                "SELECT record_json FROM provider_invocation_reservations ORDER BY ordinal",
            )
        ]
    assert len(receipts) == 1
    assert len(reservations) == 2
    assert provenance == [(receipts[0]["receipt_id"], r["reservation_id"]) for r in reservations]
    assert all(r["state"] == "succeeded" for r in reservations)
    assert all(r["selection"]["model_id"] == (model or "synthetic-model") for r in reservations)
    assert all(r["selection"]["executor_id"] == "openai_chat" for r in reservations)
    assert all(r["selection"]["model_evidence"]["context_tokens"] == 32000 for r in reservations)
    assert sum(r["actual_total_tokens"] for r in reservations) == 14


@pytest.mark.parametrize("failure", ["missing_model", "stale_catalogue", "revoked_source", "paid"])
def test_http_work_refuses_changed_or_unavailable_selection(
    tmp_path, monkeypatch, authenticate_request, http_wire, failure,
):
    from dataclasses import replace
    from datetime import timedelta

    from tinyassets.providers import work_model_selection
    from tinyassets.storage.outbound_connections import ConnectionLedger

    original_prepare = work_model_selection.prepare_work_model_snapshot

    def prepare(**kwargs):
        snapshot = original_prepare(**kwargs)
        if failure == "stale_catalogue":
            snapshot = replace(snapshot, observed_at=snapshot.observed_at - timedelta(minutes=6))
        elif failure == "revoked_source":
            ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
            ledger.revoke_grant("http_grant_" + "a" * 32)
        return snapshot

    monkeypatch.setattr(work_model_selection, "prepare_work_model_snapshot", prepare)
    if failure == "paid":
        original_read = discovery_snapshot.read_http_discovery_document

        def read(**kwargs):
            result = original_read(**kwargs)
            if len(http_wire[0]) > 1:
                for item in result["data"]:
                    item["pricing"]["prompt"] = "1"
            return result

        monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", read)
    branch = _branch(node_count=1)
    branch.node_defs[0].llm_policy = {
        "preferred": {
            "model": "absent" if failure == "missing_model" else "future-company/new-choice",
        },
        "fallback_chain": [],
    }
    result, _, _ = _run_branch(
        tmp_path, monkeypatch, authenticate_request, branch,
        open_provider=True, model_access=ModelAccess("discovered"),
    )
    assert result["terminal_status"] == "failed"
    assert http_wire[1] == []
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM provider_invocation_reservations",
        ).fetchone() == (0,)


def test_work_model_output_is_bounded_before_launch():
    from dataclasses import replace

    from tests.test_provider_invocation_selection import http_selection
    from tinyassets.provider_work_authority import _canonical_json
    from tinyassets.providers.work_model_selection import (
        bound_work_model_tokens,
        selected_work_model,
    )

    selection = http_selection()
    evidence = selection.model_evidence()
    evidence["cost_caps"]["input_million_tokens_usd"] = 1_000_000
    evidence["cost_caps"]["output_million_tokens_usd"] = 2_000_000
    selection = replace(selection, model_evidence_json=_canonical_json(evidence))
    # 32,000 context-input micros plus 2 micros per permitted output token.
    assert bound_work_model_tokens(selection, 2000, 34_000) == 1000
    assert bound_work_model_tokens(selection, 100, 34_000) == 100
    with pytest.raises(PermissionError, match="cost allowance"):
        bound_work_model_tokens(selection, 2000, 31_999)
    assert selected_work_model(selection).model_id == selection.model_id
    assert bound_work_model_tokens(None, 2000, 34_000) == 2000
