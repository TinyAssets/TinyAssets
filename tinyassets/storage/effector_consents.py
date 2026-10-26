"""Per-destination effector consents: a read and narrow facade over the broker (N10).

The consents themselves are broker-held, per owner (``broker/policy.py``), and the
broker's OPEN enforces them for every non-read call that crosses it. Here a box
reads them for its advisory checks and its inventory, and takes them back.
Granting is an owner act in the protected view, served by the router
(``/app/api/consents``); nothing in a box can grant one.

Rows are ``{"sink", "destination", "granted_at", "granted_by", "revoked_at"}``.
Matching is exact and case-sensitive; there are no wildcard consents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: The sink whose consent destinations embed the connection id
#: (``"<op>:<connection_id>:<host>/<owner>/<name>"``, per
#: ``workspace_consent_destination``).
_CONNECTION_KEYED_SINK = "workspace"
#: The sink whose consent destination is the connection's DESTINATION LABEL
#: with no id in it at all -- so it cannot be found by scanning for the id, and
#: survived removal to be inherited by the next deposit under the same name.
_DESTINATION_KEYED_SINK = "authenticated_external_call"


def _call(universe_dir: str | Path, action: str, **fields: Any) -> dict[str, Any]:
    from tinyassets.broker.client import owner_policy_operation
    from tinyassets.owner_centers import admitted_owner

    home = Path(universe_dir)
    owner = admitted_owner(home.parent, center=home.name)
    return owner_policy_operation(home.parent, owner, action, **fields)


def list_consents(
    universe_dir: str | Path,
    *,
    sink: str | None = None,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """The owner's consent rows, newest first. By default only active grants."""
    return _call(universe_dir, "consents_read", sink=sink or "",
                 active_only=bool(active_only))["consents"]


def is_consent_active(universe_dir: str | Path, *, sink: str, destination: str) -> bool:
    """Whether an active grant exists for exactly ``(sink, destination)``."""
    if not sink or not destination:
        return False
    return any(row["destination"] == destination
               for row in list_consents(universe_dir, sink=sink))


def revoke_consent(universe_dir: str | Path, *, sink: str, destination: str) -> bool:
    """Take one consent back. True when an active grant was revoked."""
    if not sink or not destination:
        return False
    return bool(_call(universe_dir, "consent_revoke", sink=sink,
                      destination=destination)["revoked"])


def _keyed_on_connection(
    sink: str,
    destination: str,
    connection_id: str,
    connection_destination: str,
) -> bool:
    """Whether this consent row belongs to THIS connection.

    Per sink, because the two sinks key differently and a single positional
    guess was wrong in both directions (Codex code review round 1): scanning
    every sink for ``split(":")[1]`` swept in a foreign row whose destination
    merely LOOKED like ``x:<id>:y``, and missed the connection's own outbound
    consent, which carries the destination label and no id.
    """
    if sink == _CONNECTION_KEYED_SINK:
        return destination.split(":")[1:2] == [connection_id]
    if sink == _DESTINATION_KEYED_SINK:
        return bool(connection_destination) and destination == connection_destination
    return False


def revoke_consents_for_connection(
    universe_dir: str | Path,
    *,
    connection_id: str,
    destination: str = "",
) -> list[str]:
    """Revoke every consent keyed on ``connection_id``; return their destinations.

    A connection id is deterministic per ``(owner, destination)``, so a removal
    that left these active would let a re-deposit of the same destination
    silently inherit the old consents. Taking a key back takes back everything
    it authorized (full-channel-access D6).
    """
    token = str(connection_id or "").strip()
    label = str(destination or "").strip()
    if not token:
        return []
    hits = [row for row in list_consents(universe_dir)
            if _keyed_on_connection(row["sink"], row["destination"], token, label)]
    for row in hits:
        revoke_consent(universe_dir, sink=row["sink"], destination=row["destination"])
    return sorted(row["destination"] for row in hits)


__all__ = [
    "is_consent_active",
    "list_consents",
    "revoke_consent",
    "revoke_consents_for_connection",
]
