"""Caller and owner on one published app request.

The router authenticates the signed-in account and, for a published slug,
forwards that account into the app owner's box. Private pages, graphs and
settings stay on the caller's own box. A visitor who does arrive may touch
only the published app document and its room.
"""

from __future__ import annotations

import re

CALLER_HEADER = b"x-tinyassets-caller-user-id"
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
# First segments of the product /app routes. A published slug cannot take one.
RESERVED = frozenset({
    "account", "api", "api-keys", "approval-handoff", "approvals", "apps",
    "billing", "client-error", "connection-discovery",
    "connections", "devices", "files", "live", "m", "me", "memory",
    "model-callback", "model-connect", "model-discovery", "models",
    "native-sign-in", "notify", "oauth", "outside-clients", "owner",
    "owner-sign-in", "profile", "run", "serving", "session", "soul",
    "subscription-connect", "sw.js", "token", "trace", "turn", "ui-frame",
    "ui-prefs", "unread", "wakes",
})
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,80}")


def validate_slug(slug: object) -> str:
    if not isinstance(slug, str) or slug in RESERVED or _SLUG.fullmatch(slug) is None:
        raise ValueError("invalid app slug")
    return slug


def validate_name(name: object) -> str:
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise ValueError("invalid app name")
    return name


def app_slug(path: str) -> str | None:
    """The published app named by this path, or None when it is a product route."""
    if not path.startswith("/app/"):
        return None
    parts = path[len("/app/"):].split("/")
    if not parts or parts[0] in RESERVED or _SLUG.fullmatch(parts[0]) is None:
        return None
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2 and parts[1] in {"state", "publish"}:
        return parts[0]
    return None


def visitor_surface(path: str, method: str) -> bool:
    """Paths a visiting player may use on the app owner's box."""
    slug = app_slug(path)
    if slug is None:
        return False
    parts = path[len("/app/"):].split("/")
    if len(parts) == 1:
        return method in {"GET", "HEAD"}
    return len(parts) == 2 and parts[1] == "state" and method in {"GET", "POST"}


def private_tool_refusal() -> str | None:
    """JSON refusal when this box is serving a visiting player a private tool."""
    import json

    from tinyassets.auth.middleware import current_identity_or_none
    from tinyassets.owner_runtime import current

    try:
        principal = current().principal
    except AttributeError:
        return None
    identity = current_identity_or_none()
    if identity is None:
        return None
    caller = (identity.metadata or {}).get("caller_user_id")
    if not isinstance(caller, str) or caller == principal:
        return None
    return json.dumps({"error": "private_surface", "outcome": "not_sent"})
