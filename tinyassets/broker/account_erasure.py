"""The daemon's account-deletion phase for broker-owned ledger rows.

Only a principal crosses this interface. No table, path, SQL or home supplied
by a caller can expand the deletion. The daemon has already tombstoned the
account; filesystem deletion and external identity cleanup remain its phases.
"""
from __future__ import annotations

import os
import socket

from tinyassets import rpc_frames as rf

ACCOUNT_SCOPE = "account-deletion"
TABLES = frozenset({
    "outbound_connections", "outbound_connection_grants", "connection_capabilities",
    "outbound_connector_artifacts", "outbound_connector_artifact_edges",
    "agent_request_usage", "agent_request_attempts", "agent_request_usage_links",
    "agent_request_dispatches", "browser_vault",
})


def validate(principal, command_center):
    if (not isinstance(principal, str) or not principal.strip()
            or principal != principal.strip() or len(principal) > 512
            or not principal.isprintable() or "|" in principal
            or command_center != ACCOUNT_SCOPE):
        raise ValueError("invalid account erasure scope")


def local_erase(ledger, *, principal, command_center):
    validate(principal, command_center)
    owned_connections = "SELECT connection_id FROM outbound_connections WHERE owner_user_id=?"
    owned_artifacts = "SELECT artifact_id FROM outbound_connector_artifacts WHERE owner_user_id=?"
    targets = [
        ('browser_vault', 'owner=?', (principal,)),
        ("outbound_connector_artifact_edges",
         f"parent_artifact_id IN ({owned_artifacts}) OR child_artifact_id IN ({owned_artifacts}) "
         "OR remixed_by_user_id=?", (principal, principal, principal)),
        ("connection_capabilities", f"connection_id IN ({owned_connections})", (principal,)),
        ("outbound_connection_grants", "owner_user_id=?", (principal,)),
        ("outbound_connector_artifacts", "owner_user_id=?", (principal,)),
        ("outbound_connections", "owner_user_id=?", (principal,)),
        *[(table, "owner=?", (principal,)) for table in (
            "agent_request_dispatches", "agent_request_usage_links",
            "agent_request_attempts", "agent_request_usage")],
    ]
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # A corrupt foreign grant must block the transaction, never be erased
        # as a child of somebody else's connection (even with FK checks off).
        if conn.execute(
            "SELECT 1 FROM outbound_connection_grants "
            f"WHERE connection_id IN ({owned_connections}) "
            "AND owner_user_id<>? LIMIT 1", (principal, principal),
        ).fetchone():
            raise ValueError("foreign grant prevents account erasure")
        live = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {}
        for table, where, params in targets:
            # Accounting may never have been initialized on an empty ledger.
            if table not in live and (table.startswith("agent_request_")
                                      or table == 'browser_vault'):
                continue
            count = conn.execute(f"DELETE FROM {table} WHERE {where}", params).rowcount
            if count:
                counts[table] = count
    from tinyassets.broker.browser_vault import key_path

    key_path(ledger, principal).unlink(missing_ok=True)
    return counts


def erase_account(data_root, *, principal):
    from tinyassets.broker.supervisor import get_supervisor
    from tinyassets.storage.outbound_connections import ProxyRequestError

    validate(principal, ACCOUNT_SCOPE)
    supervisor = get_supervisor(data_root)
    if supervisor is None:
        raise ProxyRequestError("the credential broker is not running")
    generation, token = supervisor.fence()
    document = {"op": "ERASE_ACCOUNT", "principal": principal,
                "command_center": ACCOUNT_SCOPE, "generation": generation, "token": token}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(30)
        try:
            channel.connect(os.fspath(supervisor.socket_path))
            supervisor.verify_broker(channel)
            channel.sendall(rf.control(rf.CONNECTION, document))
            frame = rf.read_frame_blocking(channel)
            if frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION:
                raise rf.FrameError("invalid account erasure reply")
            answer = frame.control()
        except (OSError, rf.FrameError):
            # No automatic replay of an ambiguous mutation. A fresh explicit
            # deletion retry may safely find zero remaining rows.
            raise ProxyRequestError("broker account erasure outcome unavailable") from None
    if set(answer) != {"op", "counts"} or answer["op"] != "ACCOUNT_ERASED":
        raise ProxyRequestError("broker account erasure refused")
    counts = answer["counts"]
    if (not isinstance(counts, dict) or not counts.keys() <= TABLES
            or any(type(n) is not int or n < 1 for n in counts.values())):
        raise ProxyRequestError("invalid broker account erasure counts")
    return counts
