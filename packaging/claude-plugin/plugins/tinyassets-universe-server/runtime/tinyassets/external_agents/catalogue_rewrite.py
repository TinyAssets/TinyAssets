"""Prepare a shipped CLI's bundled catalogue with only manifest-declared tools."""
from __future__ import annotations

import copy
import json
from pathlib import Path


def rewrite(raw: bytes, rule: dict, model: str | None) -> bytes:
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("external-agent bundled catalogue is oversized")
    catalogue = json.loads(raw)
    models = catalogue.get("models") if isinstance(catalogue, dict) else None
    if not isinstance(models, list) or not models:
        raise ValueError("external-agent bundled catalogue is empty")
    for entry in models:
        if not isinstance(entry, dict):
            raise ValueError("external-agent bundled model row is invalid")
        for field in rule.get("remove_fields", []):
            entry.pop(field, None)
        entry.update(copy.deepcopy(rule["clear_fields"]))
    field = rule["model_field"]
    if model and not any(entry.get(field) == model for entry in models):
        clone = copy.deepcopy(models[0])
        clone[field] = model
        if "display_name" in clone:
            clone["display_name"] = model
        models.append(clone)
    return json.dumps(catalogue, separators=(",", ":")).encode()


def from_install(root: Path, rule: dict, model: str | None) -> bytes:
    path = root / rule["source"]
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("external-agent bundled catalogue is absent")
    with path.open("rb") as source:
        raw = source.read(8 * 1024 * 1024 + 1)
    return rewrite(raw, rule, model)
