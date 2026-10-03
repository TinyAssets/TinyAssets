"""Installed connection-card data; all sources use api_key_http / openai_chat.

A source with a ``sign_in`` block is connected by OAuth (the generic
``connect`` ask), never by a pasted key, so it is not a key card.

Verified 2026-09-30. No key, account tier or runtime vendor adapter lives here.
Models are an agent-capable allowlist intersected with the owner's /models
response at connection time, not an invented catalogue or a claim of access.
"""

import json
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

# Endpoint, keys, free eligibility and model docs:
# https://ai.google.dev/gemini-api/docs/openai
# https://ai.google.dev/gemini-api/docs/api-key
# https://ai.google.dev/gemini-api/docs/pricing
# https://console.groq.com/docs/openai
# https://console.groq.com/docs/quickstart
# https://console.groq.com/docs/rate-limits
# https://inference-docs.cerebras.ai/resources/openai
# https://inference-docs.cerebras.ai/api-reference/models/list-models
# https://inference-docs.cerebras.ai/support/rate-limits
# https://docs.mistral.ai/getting-started/quickstarts/studio/activate-and-generate-api-key
# https://docs.mistral.ai/api/endpoint/models
# https://docs.mistral.ai/admin/billing-usage/usage-limits
# https://huggingface.co/docs/hub/oauth (inference-api scope, PKCE, metadata docs)
# https://huggingface.co/docs/inference-providers/pricing
_SOURCES = json.loads(Path(__file__).with_name("free_source_presets.json").read_text("utf-8"))
# What a provider's own free daily limit is, and what its credit buys, keyed by
# inference host: https://openrouter.ai/docs/api/reference/limits (2026-10-01).
_DAILY_CAPS = json.loads(Path(__file__).with_name("daily_cap_offers.json").read_text("utf-8"))


def source_cards():
    """Key-paste cards. A source completed by signing in is never one of them."""
    return deepcopy([row for row in _SOURCES
                     if row.get("available", True) and "sign_in" not in row])


def subscription_cards():
    """Installed display copy for the existing device sign-in offer."""
    return json.loads(Path(__file__).with_name("subscription_cards.json").read_text("utf-8"))


def source_preset(source_id):
    return next((row for row in source_cards() if row["id"] == source_id), None)


def sign_in_cards():
    """Display data for sources the owner connects by signing in; no endpoints."""
    return [{"id": row["id"], "name": row["name"], "offer": row["offer"],
             "label": row["sign_in"]["label"], "billing_note": row["billing_note"],
             "daily_cap": deepcopy(row.get("daily_cap"))}
            for row in _SOURCES if row.get("available", True) and "sign_in" in row]


def sign_in_preset(source_id):
    return next((deepcopy(row) for row in _SOURCES
                 if row["id"] == source_id and row.get("available", True)
                 and "sign_in" in row), None)


def daily_cap_for_host(host):
    """The ONE reader of a source's daily limit, by inference host, or None.

    Reads both installed files: the provider-stated credit tier for hosts in
    ``daily_cap_offers.json`` and the per-card ``daily_cap``. None values mean
    unconfirmed, never zero.
    """
    offer = _DAILY_CAPS.get(host)
    if offer is not None:
        return {"requests_per_day": offer.get("free_requests_per_day"),
                "credit_requests_per_day": offer.get("credit_requests_per_day"),
                "credit_amount": offer.get("credit_amount"),
                "reset_timezone": offer.get("reset_timezone"), "name": offer["name"],
                "credit_url": offer.get("credit_url")}
    row = source_for_host(host)
    cap = row.get("daily_cap") if row else None
    if not isinstance(cap, dict):
        return None
    return {"requests_per_day": cap.get("requests_per_day"), "credit_requests_per_day": None,
            "reset_timezone": cap.get("reset_timezone"), "name": row["name"],
            "credit_url": row.get("billing_url")}


def daily_cap_offers():
    """Installed daily-limit facts the app words its daily-cap card from."""
    return [{"host": host, **offer} for host, offer in sorted(_DAILY_CAPS.items())]


def source_for_host(host):
    source = next((row for row in _SOURCES if urlsplit(row["base_url"]).netloc == host), None)
    if source is not None:
        return deepcopy(source)
    presets = json.loads(Path(__file__).with_name("acquisition_presets.json").read_text("utf-8"))
    return next((row for row in presets.values()
                 if urlsplit(row["inference_url"]).netloc == host), {})


def billing_url_for_host(host):
    # Existing hosted acquisition presets also supply recovery links as data.
    source = source_for_host(host)
    if source:
        return source["billing_url"]
    presets = json.loads(Path(__file__).with_name("acquisition_presets.json").read_text("utf-8"))
    return next((row.get("billing_url", row["manage_url"]) for row in presets.values()
                 if urlsplit(row["inference_url"]).netloc == host), "")


def discovered_agent_models(preset, payload):
    """Bound supported chat/tool ids by the authenticated catalogue's membership.

    These APIs don't all report tools/context in /models. The preset allowlist
    establishes tool support; 32768 is a conservative local admission bound.
    No catalogue price is fabricated and no fetched URL or code is executed.
    """
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("model catalogue is unavailable")
    ids = {row.get("id") for row in rows if isinstance(row, dict)
           and isinstance(row.get("id"), str) and row.get("active", True) is not False}
    models = [{"id": mid, "tools": True, "context": 32768}
              for mid in preset["models"] if mid in ids]
    if not models:
        raise ValueError("this key offers no supported agent model; check its model access")
    return models
