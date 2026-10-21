"""Decoder evidence only; no live-account discovery or inference is exercised."""

from datetime import datetime, timedelta, timezone


from tests.support.catalog_decoders import (
    decode_openrouter_benchmarks,
    decode_openrouter_models,
)
from tinyassets.providers.model_policy import (
    Catalog,
    ConnectionModels,
    Interaction,
    ModelPolicy,
    order_models,
)

CONNECTION = ConnectionModels(
    "owned-connection", "declared-provider", "http", "fresh", True, True, ()
)
NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)
COMPONENTS = frozenset(
    {
        "input_million_tokens_usd",
        "output_million_tokens_usd",
        "request_usd",
        "image_usd",
    }
)
NEEDS = Interaction(True, frozenset({"text"}), COMPONENTS)


def row(model_id="brand/opaque-v99", **overrides):
    return {
        "id": model_id,
        "canonical_slug": model_id,
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        "supported_parameters": ["tools", "temperature"],
        "context_length": 100000,
        "top_provider": {"context_length": 32000},
        "pricing": {"prompt": "0", "completion": "0", "request": "0", "image": "0"},
        **overrides,
    }


def decode(*rows, connection=CONNECTION, **kwargs):
    return decode_openrouter_models({"data": list(rows)}, connection=connection, **kwargs)


def order(connection):
    return order_models(
        Catalog("owner", "universe", (connection,)),
        ModelPolicy(1, "automatic", (), ranking_source="artificial-analysis"),
        NEEDS,
        owner_id="owner",
        universe_id="universe",
    )


def benchmark(*rows, as_of=NOW.isoformat(), **kwargs):
    return decode_openrouter_benchmarks(
        {"data": list(rows), "meta": {"as_of": as_of}},
        now=NOW,
        max_age=timedelta(days=1),
        **kwargs,
    )


def score(key="brand/opaque-v99", **overrides):
    return {
        "source": "artificial-analysis",
        "model_permaslug": key,
        "agentic_index": 58.3,
        "intelligence_index": 71.2,
        **overrides,
    }
























