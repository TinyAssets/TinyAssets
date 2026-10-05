"""Typed MCP metadata on the broker-owned HTTP connection authority.

This module runs in the broker for storage operations. Session identifiers and
credentials are deliberately absent; the daemon keeps protocol sessions in memory.
"""
from __future__ import annotations

import json
import threading
import weakref
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from tinyassets.connection_oauth.transport import validate_https_url

_locks = weakref.WeakValueDictionary()
_locks_guard = threading.Lock()


def attachment_lock(ledger, owner, connection):
    key = (str(ledger._db_path), owner, connection)
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_attachments (
    owner_id TEXT NOT NULL,
    connection_id TEXT NOT NULL REFERENCES outbound_connections(connection_id) ON DELETE CASCADE,
    incarnation TEXT NOT NULL,
    descriptor_json TEXT NOT NULL,
    PRIMARY KEY (owner_id, connection_id, incarnation)
);
"""


@dataclass(frozen=True)
class Attachment:
    endpoint: str
    display_name: str
    revision: int = 1
    state: str = "draft"
    protocol_version: str = ""
    catalog_hash: str = ""
    schema_version: int = 1
    transport: str = "http"

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict):
            raise ValueError("invalid MCP metadata")
        try:
            item = cls(**value)
        except TypeError:
            raise ValueError("invalid MCP metadata fields") from None
        if (type(item.schema_version) is not int or item.schema_version != 1
                or item.transport != "http" or type(item.revision) is not int
                or item.revision < 1 or item.state not in {
                    "draft", "connecting", "active", "failed", "revoked", "expired"}
                or not isinstance(item.display_name, str)
                or not 1 <= len(item.display_name) <= 200
                or not isinstance(item.protocol_version, str)
                or item.protocol_version not in {"", "2025-03-26", "2025-06-18"}
                or not isinstance(item.catalog_hash, str)
                or (item.catalog_hash and (len(item.catalog_hash) != 64
                    or any(c not in "0123456789abcdef" for c in item.catalog_hash)))):
            raise ValueError("unsupported MCP metadata")
        validate_https_url(item.endpoint)
        return item


def validate_operation(document):
    payload = document["descriptor"]
    if (not isinstance(payload, dict) or set(payload) != {"incarnation", "expected", "value"}
            or not isinstance(payload["incarnation"], str) or not payload["incarnation"]
            or len(payload["incarnation"]) > 128 or document["preview"]):
        raise ValueError("MCP operation requires an incarnation and metadata CAS")
    for key in ("expected", "value"):
        if payload[key] is not None:
            Attachment.parse(payload[key])
    if document["action"] == "read":
        if payload["expected"] is not None or payload["value"] is not None or document["enabled"]:
            raise ValueError("invalid MCP read")
    elif not document["enabled"] or payload["value"] is None:
        raise ValueError("MCP removal uses a revoked metadata revision")


def local_operation(ledger, *, principal, command_center, document):
    from tinyassets.storage.outbound_connections import (
        GrantResolutionError,
        _enforce_endpoint_allowlist,
        _parse_canonical_https_url,
        _resource_from_row,
        _verb_within_scopes,
    )

    validate_operation(document)
    payload = document["descriptor"]
    with attachment_lock(ledger, principal, document["connection_id"]), ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT c.* FROM outbound_connections c JOIN outbound_connection_grants g "
            "ON g.connection_id=c.connection_id WHERE c.owner_user_id=? "
            "AND g.owner_user_id=? AND g.universe_id=? AND g.grant_id=? "
            "AND c.connection_id=? AND c.incarnation=? "
            "AND c.revoked_at IS NULL AND g.revoked_at IS NULL",
            (principal, principal, command_center, document["grant_id"],
             document["connection_id"], payload["incarnation"]),
        ).fetchone()
        if row is None:
            raise GrantResolutionError("MCP backing connection authority changed")
        resource = _resource_from_row(row)
        key = (principal, document["connection_id"], payload["incarnation"])
        stored = conn.execute(
            "SELECT descriptor_json FROM mcp_attachments "
            "WHERE owner_id=? AND connection_id=? AND incarnation=?", key,
        ).fetchone()
        current = asdict(Attachment.parse(json.loads(stored[0]))) if stored else None
        if document["action"] == "read":
            return {"descriptor": current}
        expected = payload["expected"]
        if expected is not None:
            expected = asdict(Attachment.parse(expected))
        if current != expected:
            raise GrantResolutionError("MCP metadata revision changed")
        item = Attachment.parse(payload["value"])
        if item.state == "active":
            raise PermissionError("MCP activation requires the connection coordinator")
        if (current is not None and current["state"] != "draft"
                and item.endpoint != current["endpoint"]):
            raise PermissionError("MCP endpoint changed after draft")
        if current is not None:
            if (current["state"] in {"revoked", "expired"}
                    or (current["state"] != "draft" and item.state == "draft")
                    or item.revision != current["revision"] + 1):
                raise GrantResolutionError("MCP metadata revision changed")
        elif item.revision != 1 or item.state != "draft":
            raise ValueError("MCP attachment must start as a draft")
        if (resource.connection_type != "http"
                or not _verb_within_scopes("POST", resource.scopes, resource.access_mode)):
            raise PermissionError("MCP requires HTTP POST authority")
        _enforce_endpoint_allowlist(
            _parse_canonical_https_url(item.endpoint, allowed_ports=frozenset({443})),
            "POST", resource.allowed_endpoints, resource.access_mode,
        )
        result = asdict(item)
        conn.execute(
            "INSERT INTO mcp_attachments VALUES (?, ?, ?, ?) "
            "ON CONFLICT(owner_id, connection_id, incarnation) DO UPDATE SET "
            "descriptor_json=excluded.descriptor_json", (*key, json.dumps(result, sort_keys=True)),
        )
        return {"descriptor": result}


def metadata(data_root, *, principal, command_center, grant_id, connection_id,
             incarnation, value=None, expected=None):
    from tinyassets.broker.capabilities import capability_operation

    return capability_operation(
        data_root, principal=principal, command_center=command_center,
        grant_id=grant_id, connection_id=connection_id, capability_kind="mcp",
        action="read" if value is None else "configure", enabled=value is not None,
        descriptor={"incarnation": incarnation, "expected": expected, "value": value},
    )


@contextmanager
def bound_send(ledger, *, principal, command_center, grant_id, connection_id,
               binding, request):
    """Fence a network write against attachment removal/reconfiguration.

    A broker-local attachment lock serializes metadata revocation with sends.
    The backing HTTP authority is independently checked by the egress driver.
    """
    from tinyassets.storage.outbound_connections import GrantResolutionError

    if not isinstance(binding, dict) or set(binding) != {"incarnation", "revision"}:
        raise GrantResolutionError("invalid MCP stream binding")
    with attachment_lock(ledger, principal, connection_id):
        with ledger._connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT a.descriptor_json FROM mcp_attachments a "
                "JOIN outbound_connections c ON c.connection_id=a.connection_id "
                "AND c.incarnation=a.incarnation AND c.owner_user_id=a.owner_id "
                "JOIN outbound_connection_grants g ON g.connection_id=c.connection_id "
                "WHERE a.owner_id=? AND g.owner_user_id=? AND g.universe_id=? "
                "AND g.grant_id=? AND a.connection_id=? AND a.incarnation=? "
                "AND c.revoked_at IS NULL AND g.revoked_at IS NULL",
                (principal, principal, command_center, grant_id, connection_id,
                 binding["incarnation"]),
            ).fetchone()
            item = Attachment.parse(json.loads(row[0])) if row else None
            if (item is None or item.revision != binding["revision"]
                    or item.state not in {"connecting", "active"}
                    or request.get("url") != item.endpoint or request.get("query")
                    or request.get("path")):
                raise GrantResolutionError("MCP attachment authority changed")
            if item.state == "connecting":
                try:
                    method = json.loads(request.get("body", "")).get("method")
                except (ValueError, TypeError, AttributeError):
                    method = None
                if method not in {"initialize", "notifications/initialized", "tools/list"}:
                    raise GrantResolutionError("MCP tools require completed activation")
        yield
