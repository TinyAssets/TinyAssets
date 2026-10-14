"""Public requests must never promise work without a claiming worker."""

from __future__ import annotations

import json

from tinyassets.api import universe


def test_both_public_request_entries_refuse_without_persisting(tmp_path, monkeypatch):
    monkeypatch.setattr(universe, "_base_path", lambda: tmp_path)
    action = json.loads(universe.UNIVERSE_ACTIONS["submit_request"](
        graph_id="missing", text="Please do this",
    ))
    writer = json.loads(universe.admit_request_v2(
        idempotency_key="request-key-0001", graph_id="missing",
        text="Please do this",
    ))
    assert action == writer == {
        "error": "request_execution_unavailable",
        "message": "Request submission is unavailable: no worker starts admitted requests.",
    }
    assert list(tmp_path.iterdir()) == []
