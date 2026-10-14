"""The blank command center and its public, health-checked package offers."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BUILD_PROMPT = (
    "Help me design my own command center: ask me what I want it to do, then build it."
)

# The built-in fallback reads the same card bytes an owner may install as an
# ordinary extension. Package metadata grants no trusted-shell capabilities;
# extension projection replaces this reserved fallback id with its revision id.
PLATFORM_DEFAULT_UI = json.loads(
    (Path(__file__).parent / "starter" / "default-layout" / "layout.json").read_text("utf-8")
)


def working_packages() -> list[dict]:
    """Latest per author/name, newest first; unhealthy publications never offer a Try."""
    from tinyassets.api import package_requests
    from tinyassets.api.helpers import _base_path
    from tinyassets.api.publish_requests import BRANCH_REF_KIND
    from tinyassets.branch_versions import branch_version_is_public, get_branch_version

    try:
        rows = package_requests.list_packages(limit=100)
        rows.sort(key=lambda row: (row.get("created_at") or "",
                                   row["agent_definition_id"]), reverse=True)
        base = _base_path()
    except Exception:
        logger.warning("Could not list command-center packages", exc_info=True)
        return []
    latest = {}
    for row in rows:
        try:
            key = (row["author_id"], row["name"])
            if key not in latest or row["version"] > latest[key]["version"]:
                latest[key] = row
        except Exception:
            logger.warning("Skipping invalid package listing", exc_info=True)
    result = []
    for row in rows:
        try:
            if latest.get((row["author_id"], row["name"])) is not row:
                continue
            definition, _, _, _ = package_requests._load(row)
            for component in definition["components"].values():
                if component.get("kind") != BRANCH_REF_KIND:
                    continue
                version = component.get("published_version_id")
                if (not version or not branch_version_is_public(base, version)
                        or get_branch_version(base, version) is None):
                    raise ValueError("package workflow version is missing or not public")
            result.append({key: row[key] for key in (
                "agent_definition_id", "name", "description", "author_id", "version",
                "size", "file_count", "needs",
            )})
            if len(result) == 12:
                break
        except Exception:
            logger.warning("Skipping unhealthy command-center package %s",
                           row.get("agent_definition_id"), exc_info=True)
    return result


def read_packages(*, universe_id: str = "") -> dict:
    from tinyassets.api.pending_requests import _owner_gate

    _, _, denial = _owner_gate(universe_id)
    if denial is not None:
        return denial
    packages = working_packages()
    systems = working_systems()
    return {"packages": packages, "systems": systems, "build_prompt": BUILD_PROMPT,
            "can_try": bool(packages or any(row["available"] for row in systems))}


def working_systems() -> list[dict]:
    from tinyassets.api.system_copy_requests import list_systems

    return list_systems()
