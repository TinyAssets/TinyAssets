"""Profile-bound refresh provenance and async coordination, no live account IO."""

from datetime import datetime, timezone

import pytest

from tests import test_model_discovery_capability as profile_tests
from tinyassets.providers import discovery_snapshot as snapshots

NOW = datetime(2026, 9, 9, 22, 0, tzinfo=timezone.utc)
rig = profile_tests.rig


def _model(model_id="new-company/new-model"):
    return {
        "id": model_id,
        "canonical_slug": model_id,
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        "supported_parameters": ["tools"],
        "context_length": 32000,
        "pricing": {"prompt": "0", "completion": "0", "image": "0", "request": "0"},
    }


@pytest.fixture
def reader(rig, monkeypatch):
    rig.publish()
    monkeypatch.setattr(snapshots, "_now", lambda: NOW)
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        assert kwargs["owner_user_id"] == "owner" and kwargs["universe_id"] == "u-models"
        assert kwargs["definition"].ref == "grant-models"
        if "benchmarks" in kwargs["url"]:
            return {
                "meta": {"as_of": NOW.isoformat()},
                "data": [
                    {
                        "source": "artificial-analysis",
                        "model_permaslug": "new-company/new-model",
                        "agentic_index": 52,
                        "intelligence_index": 64,
                    }
                ],
            }
        return {
            "owner_filtered": False,
            "executor_tools": True,
            "account_id": "remote-claim",
            "data": [_model()],
        }

    monkeypatch.setattr(snapshots, "read_http_discovery_document", read)
    return calls, read


def _refresh(rig):
    return snapshots.refresh_model_discovery(
        owner_user_id="owner", universe_id="u-models", definition_id=rig.definition.id
    )
























