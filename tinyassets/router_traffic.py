"""Read deployment's ordinary-admission decision; no restore authority."""
import json
import os

NAME = ".ordinary-admission.json"


def admits(root, owner):
    try:
        gate = json.loads((root / NAME).read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(gate, dict) or set(gate) != {"ordinary"}:
        return False
    if gate["ordinary"] is True:
        return True
    return gate["ordinary"] is False and bool(owner) and owner in {
        "canary", os.environ.get("TINYASSETS_CANARY_USER_ID"),
    }
