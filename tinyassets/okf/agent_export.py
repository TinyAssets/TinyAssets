"""Portable agent configuration through the existing bounded settings schema."""

from __future__ import annotations

import json
import re

import yaml

from tinyassets.agent_definition import SettingsError, parse_settings
from tinyassets.okf.bundle import InvalidBundle


def settings_path(path: str) -> bool:
    return path == "settings.yaml" or bool(re.fullmatch(r"agents/[^/]+/settings\.yaml", path))


def portable_settings(raw: bytes, *, number: int) -> bytes:
    """Replace authority/connection identifiers before exposing a content preview.

    Only these schema fields are replaced. Arbitrary agent configuration and
    behavior strings still pass through the transfer credential/contact screen.
    """
    try:
        parse_settings(raw)  # byte, alias, nesting, duplicate and typed-field bounds
        document = yaml.safe_load(raw.decode("utf-8"))
        if "model" in document:
            previous = document["model"]
            model = {"id": f"${{AGENT_{number}_MODEL_NAME}}"}
            if isinstance(previous, dict):
                if "effort" in previous:
                    model["effort"] = previous["effort"]
                if "connection" in previous:
                    model["connection"] = f"connection-{number}"
            document["model"] = model
            document["schema_version"] = 1
        if "agent" in document:
            document["agent"].pop("owner", None)
        return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2,
                          allow_nan=False).encode("utf-8")
    except (SettingsError, ValueError, UnicodeError, TypeError, yaml.YAMLError) as exc:
        raise InvalidBundle("Invalid agent settings; export cannot use defaults") from exc


def roster(files, agents):
    """Derive the portable roster solely from included, canonical settings bytes."""
    if any(settings_path(path) and (len(path.split("/")) != 3 or path.split("/")[1] not in agents)
           for path in files):
        raise InvalidBundle("Select an agent's instructions with its settings")
    result = []
    for agent in agents:
        path = f"agents/{agent}/settings.yaml"
        entry = {"agent": agent, "instructions": f"agents/{agent}/AGENTS.md",
                 "settings": path if path in files else None, "model_env": ""}
        if path in files:
            try:
                settings = parse_settings(files[path])
            except SettingsError as exc:
                raise InvalidBundle("Invalid roster settings") from exc
            if settings.model is not None:
                match = re.fullmatch(r"\$\{(AGENT_[1-9][0-9]{0,3})_MODEL_NAME\}",
                                     settings.model.id)
                if match is None:
                    raise InvalidBundle("Exported model must be a local environment reference")
                entry["model_env"] = match.group(1)
        result.append(entry)
    groups = [entry["model_env"] for entry in result if entry["model_env"]]
    if len(groups) != len(set(groups)):
        raise InvalidBundle("Agents must have distinct local model references")
    return result


def model_variables(roster):
    return [f"{entry['model_env']}_{suffix}" for entry in roster if entry["model_env"]
            for suffix in ("MODEL_BASE_URL", "MODEL_NAME", "MODEL_API_KEY")]
