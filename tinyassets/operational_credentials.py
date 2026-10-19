"""The single current source of operational door credentials (C3)."""
import json


def credential(root, name):
    try:
        value = json.loads((root / ".router-credentials.json").read_text())[name]
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    return value if isinstance(value, str) else ""
