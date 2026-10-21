"""Unfamiliar wire layouts through the shared interpreter; not live authority."""

from datetime import datetime, timezone

from tinyassets.providers.model_policy import ConnectionModels

CATALOGUE = {
    "rows": "/inventory/items", "count": "/inventory/count", "next_page": "/page/next",
    "model_id": "/identity/key", "join_key": "/identity/evaluation",
    "inputs": "/features/in", "outputs": "/features/out", "tools": "/features/functions",
    "contexts": ["/limits/0", "/limits/1"], "prices": "/tariff",
    "default_model": "/preferred",
}
PRICES = {
    "fields": {
        "input": {"component": "input_million_tokens_usd", "scale": 10**6},
        "output": {"component": "output_million_tokens_usd", "scale": 10**6},
        "call": {"component": "request_usd", "scale": 10**6},
    },
    "required": ["input", "output", "call"], "overrides": "/tiers",
    "metadata": ["tiers"], "conditions": ["when"],
}
BENCHMARK = {
    "rows": "/evaluations", "source": "independent-lab/schema-v3",
    "source_field": "/measurement/source", "model_id": "/subject",
    "timestamp": "/measured_at", "agentic": "/measurement/agent_score",
    "general": "/measurement/reason_score", "scale": 1000,
}
CONNECTION = ConnectionModels("owned-source", "custom-http", "http", "fresh", False, False, ())
NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


def payload():
    return {
        "inventory": {"count": 1, "items": [{
            "identity": {"key": "unseen:model-v7", "evaluation": "evaluation/7"},
            "features": {"in": ["text"], "out": ["text"], "functions": True},
            "limits": [12345, 8000],
            "tariff": {"input": "1.234567", "output": "2", "call": "0",
                       "tiers": [{"input": "1.5", "when": "peak"}]},
        }]},
        "page": {"next": None}, "preferred": "unseen:model-v7",
        "owner_filtered": True, "executor_tools": True,
        "authenticated_account_id": "invented", "source_kind": "subscription",
    }
