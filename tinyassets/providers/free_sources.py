"""Compatibility projections of source records for existing HTTP model connections.

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

_DAILY_CAPS = json.loads(Path(__file__).with_name("daily_cap_offers.json").read_text("utf-8"))


def _cards():
    from tinyassets.providers.source_record import records

    return [{**deepcopy(r.invoke["http"]["card"]), "id": r.id, "name": r.display_name,
             "base_url": r.invoke["http"]["base_url"], "models": [m["id"] for m in r.models]}
            for r in records().values() if "card" in r.invoke.get("http", {})]


def source_cards():
    """Key-paste cards. A source completed by signing in is never one of them."""
    return deepcopy([row for row in _cards()
                     if row.get("available", True) and "sign_in" not in row])


def source_preset(source_id):
    return next((row for row in source_cards() if row["id"] == source_id), None)


def sign_in_cards():
    """Display data for sources the owner connects by signing in; no endpoints."""
    return [{"id": row["id"], "name": row["name"], "offer": row["offer"],
             "label": row["sign_in"]["label"], "billing_note": row["billing_note"],
             "daily_cap": deepcopy(row.get("daily_cap"))}
            for row in _cards() if row.get("available", True) and "sign_in" in row]


def sign_in_preset(source_id):
    return next((deepcopy(row) for row in _cards()
                 if row["id"] == source_id and row.get("available", True)
                 and "sign_in" in row), None)


def model_uses_metered_free_offer(host, model):
    """Recognize a model tier only when the installed source declares it."""
    suffix = (_DAILY_CAPS.get(host) or {}).get("free_model_suffix")
    return (isinstance(suffix, str) and bool(suffix) and isinstance(model, str)
            and len(model) > len(suffix) and model.endswith(suffix))


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
    """Public daily-limit copy; internal model classification stays installed-only."""
    fields = ("name", "free_requests_per_day", "credit_requests_per_day",
              "credit_amount", "credit_url", "reset_timezone")
    return [{"host": host, **{key: deepcopy(offer[key]) for key in fields if key in offer}}
            for host, offer in sorted(_DAILY_CAPS.items())]


def source_for_host(host):
    source = next((row for row in _cards() if urlsplit(row["base_url"]).netloc == host), None)
    if source is not None:
        return deepcopy(source)
    from tinyassets.providers.source_record import records

    presets = {r.id: r.acquisition_document for r in records().values()
               if r.sign_in.get("oauth", {}).get("protocol") == "pkce_user_key_v1"}
    return next((row for row in presets.values()
                 if urlsplit(row["inference_url"]).netloc == host), {})


def billing_url_for_host(host):
    # Existing hosted acquisition presets also supply recovery links as data.
    source = source_for_host(host)
    if source:
        return source["billing_url"]
    from tinyassets.providers.source_record import records

    presets = {r.id: r.acquisition_document for r in records().values()
               if r.sign_in.get("oauth", {}).get("protocol") == "pkce_user_key_v1"}
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
