"""Bounded proposal documents; no caller-selected request or action fields."""
from __future__ import annotations

import hashlib

LIMITS = {"action": 200, "why": 1000, "evidence": 2000}


def validate(document: dict) -> dict[str, str]:
    extra = document.keys() - LIMITS.keys()
    if extra:
        raise ValueError(f"unexpected proposal field: {sorted(extra)[0]}")
    for field, limit in LIMITS.items():
        value = document.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a nonempty string")
        if len(value) > limit:
            raise ValueError(f"{field} must be at most {limit} characters")
    if document["action"].splitlines() != [document["action"]]:
        raise ValueError("action must be one line")
    return {field: document[field].strip() for field in LIMITS}


def dedupe_key(action: str) -> str:
    return "proposal:" + hashlib.sha256(action.encode("utf-8")).hexdigest()


def displayed_row_matches(row: dict) -> bool:
    action = row.get("action") or {}
    title, brief = action.get("title"), action.get("brief")
    return (
        set(action) == {"type", "title", "brief"}
        and action["type"] == "start_activity"
        and isinstance(title, str) and 0 < len(title) <= LIMITS["action"]
        and len(title.splitlines()) == 1
        and isinstance(brief, str) and 0 < len(brief) <= 3002
        and row.get("title") == title and row.get("body") == brief
        and row.get("fields") == [] and not row.get("items")
        and row.get("dedupe_key") == dedupe_key(title)
    )
