"""Public universe-action wrappers for daemon mini-brain memory."""

from __future__ import annotations

import json
from pathlib import Path

from tinyassets.api import universe as universe_api
from tinyassets.daemon_registry import create_daemon


def _create_daemon(base: Path) -> dict:
    return create_daemon(
        base,
        display_name="Review Daemon",
        created_by="pytest",
        soul_mode="soul",
        soul_text="Review Daemon keeps careful operational memory.",
    )


def test_daemon_memory_actions_capture_search_review_and_promote(
    canonical_daemon, tmp_path: Path,
    monkeypatch,
) -> None:
    daemon = _create_daemon(tmp_path)
    source = canonical_daemon(tmp_path)
    monkeypatch.setattr(universe_api, "_daemon_memory_source", lambda _: source)
    monkeypatch.setattr(universe_api, "_base_path", lambda: tmp_path)

    capture = json.loads(universe_api._universe_impl(
        action="daemon_memory_capture",
        inputs_json=json.dumps({
            "request_id": "daemon-test-1",
            "daemon_id": daemon["daemon_id"],
            "content": "Always verify provider_call reaches child prompt nodes.",
            "memory_kind": "failure_mode",
            "source_type": "pytest",
            "source_id": "test-daemon-memory-actions",
            "reliability": "test_observed",
            "temporal_bounds": {"valid_from": "2026-05-02"},
            "language_type": "policy",
            "importance": 0.9,
            "confidence": 0.85,
        }),
    ))
    assert capture["entry"]["daemon_id"] == daemon["daemon_id"]
    entry_id = capture["entry"]["entry_id"]
    assert capture["entry"]["promotion_state"] == "candidate"

    search = json.loads(universe_api._universe_impl(
        action="daemon_memory_search",
        text="provider_call child prompt",
        inputs_json=json.dumps({"daemon_id": daemon["daemon_id"]}),
    ))
    assert search["count"] == 1
    assert search["entries"][0]["entry_id"] == entry_id

    accepted = json.loads(universe_api._universe_impl(
        action="daemon_memory_review",
        inputs_json=json.dumps({
            "request_id": "daemon-test-4",
            "daemon_id": daemon["daemon_id"],
            "entry_id": entry_id,
            "decision": "accept",
            "note": "Validated by focused MCP action wrapper test.",
        }),
    ))
    assert accepted["entry"]["promotion_state"] == "accepted"
    assert accepted["entry"]["metadata"]["last_review"]["decision"] == "accepted"

    listed = json.loads(universe_api._universe_impl(
        action="daemon_memory_list",
        inputs_json=json.dumps({"daemon_id": daemon["daemon_id"], "limit": 5}),
    ))
    assert [entry["entry_id"] for entry in listed["entries"]] == [entry_id]

    promoted = json.loads(universe_api._universe_impl(
        action="daemon_memory_promote",
        text="Provider-call propagation is a stable child-run lesson.",
        inputs_json=json.dumps({
            "request_id": "daemon-test-6",
            "daemon_id": daemon["daemon_id"],
            "entry_ids": [entry_id],
        }),
    ))
    assert promoted["promoted_count"] == 1
    assert promoted["entry_ids"] == [entry_id]

    status = json.loads(universe_api._universe_impl(
        action="daemon_memory_status",
        inputs_json=json.dumps({"daemon_id": daemon["daemon_id"]}),
    ))
    assert status["entry_count"] == 1
    assert status["promotion_count"] == 1
    assert status["promotion_states"]["promoted"] == 1
    assert status["registry"]["default_memory_kind"] == "semantic"
    assert "failure_mode" in {
        item["kind"] for item in status["registry"]["memory_kinds"]
    }


def test_daemon_memory_review_can_supersede_entries(
    canonical_daemon, tmp_path: Path,
    monkeypatch,
) -> None:
    daemon = _create_daemon(tmp_path)
    source = canonical_daemon(tmp_path)
    monkeypatch.setattr(universe_api, "_daemon_memory_source", lambda _: source)
    monkeypatch.setattr(universe_api, "_base_path", lambda: tmp_path)

    first = json.loads(universe_api._universe_impl(
        action="daemon_memory_capture",
        inputs_json=json.dumps({
            "request_id": "daemon-test-2",
            "daemon_id": daemon["daemon_id"],
            "content": "Old lesson: child runs always use mock providers.",
            "memory_kind": "failure_mode",
            "source_type": "pytest",
            "source_id": "old",
            "reliability": "test_observed",
            "language_type": "claim",
        }),
    ))["entry"]
    second = json.loads(universe_api._universe_impl(
        action="daemon_memory_capture",
        inputs_json=json.dumps({
            "request_id": "daemon-test-3",
            "daemon_id": daemon["daemon_id"],
            "content": "New lesson: child runs use threaded provider_call.",
            "memory_kind": "failure_mode",
            "source_type": "pytest",
            "source_id": "new",
            "reliability": "test_observed",
            "language_type": "claim",
        }),
    ))["entry"]

    reviewed = json.loads(universe_api._universe_impl(
        action="daemon_memory_review",
        inputs_json=json.dumps({
            "request_id": "daemon-test-5",
            "daemon_id": daemon["daemon_id"],
            "entry_id": first["entry_id"],
            "decision": "supersede",
            "superseded_by_entry_id": second["entry_id"],
            "note": "Provider propagation landed.",
        }),
    ))

    assert reviewed["entry"]["promotion_state"] == "superseded"
    assert reviewed["entry"]["superseded_by_entry_id"] == second["entry_id"]
    assert reviewed["entry"]["metadata"]["last_review"]["decision"] == "superseded"
