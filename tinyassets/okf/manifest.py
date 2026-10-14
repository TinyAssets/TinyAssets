"""The shared manifest writer for published packages and private exports."""

from __future__ import annotations

import hashlib


def build_manifest(*, profile, name, description, files, agents, workflows, ui,
                   automations, model, connections, roster=None):
    result = {
        "format_version": 1,
        "profile": profile,
        "name": name,
        "description": description,
        "agents": agents,
        "files": [
            {"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for path, data in sorted(files.items())
        ],
        "workflows": workflows,
        "ui": ui,
        "automations": automations,
        "needs": {"model": model, "connections": connections},
    }
    if roster is not None:
        result["roster"] = roster
    return result
