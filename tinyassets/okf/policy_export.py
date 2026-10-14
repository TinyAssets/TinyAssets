"""Data-only export of broker policy, never another policy authority."""
from __future__ import annotations

import json
import re

from tinyassets.broker.policy import BEHAVIOURS, DEFAULT_RULES
from tinyassets.okf.bundle import InvalidBundle


def preview_agents(paths):
    agents = {"main"} if "AGENTS.md" in paths else set()
    agents.update(path.split("/")[1] for path in paths
                  if re.fullmatch(r"agents/[^/]+/AGENTS\.md", path))
    return sorted(agents)


def portable_rules(projection, *, owner, center, agents):
    """Strip source authority; qualified rules become conservative class limits.

    A connection/operation-specific rule cannot be rebound to an arbitrary
    local command. Applying the strictest behavior across all qualifiers avoids
    dropping a restriction. Preapproval is never transferable permission.
    """
    if (not isinstance(projection, dict) or projection.get("owner") != owner
            or type(projection.get("generation")) is not int
            or projection["generation"] < 1
            or not isinstance(projection.get("digest"), str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", projection["digest"])):
        raise InvalidBundle("Invalid owner policy projection")
    document = projection.get("policy")
    if not isinstance(document, dict) or not isinstance(document.get("entries"), list):
        raise InvalidBundle("Invalid owner policy entries")
    matches = {agent: {} for agent in agents}
    for entry in document["entries"]:
        if not isinstance(entry, dict):
            raise InvalidBundle("Invalid owner policy entry")
        if entry.get("scope") not in ("account", center) or entry.get("table") != "rules":
            continue
        values = entry.get("values")
        if not isinstance(values, dict):
            raise InvalidBundle("Invalid owner policy rule")
        if values.get("record_kind", "behavior") != "behavior":
            continue
        targets = [slug for slug, identity in agents.items()
                   if identity == values.get("agent", "main")]
        if not targets:
            continue
        action, behavior = values.get("action_class"), values.get("behaviour")
        if action not in DEFAULT_RULES or behavior not in BEHAVIOURS:
            raise InvalidBundle("Unsupported owner policy behavior")
        for agent in targets:
            matches[agent].setdefault(action, []).append(behavior)
            if values.get("connection") or values.get("operation"):
                # Outside the qualified request the broker still uses its default.
                matches[agent][action].append(DEFAULT_RULES[action])
    files = {}
    for agent, rules in matches.items():
        behaviors = {action: max(rules.get(action, [default]), key=BEHAVIOURS.index)
                     for action, default in DEFAULT_RULES.items()}
        files[f"agents/{agent}/rules.json"] = json.dumps({
            "schema_version": 1, "behaviours": behaviors,
        }, sort_keys=True, indent=2).encode() + b"\n"
    return files
