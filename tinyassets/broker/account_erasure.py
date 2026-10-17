"""The daemon's account-deletion phase for broker-owned ledger rows.

Only a principal crosses this interface. No table, path, SQL or home supplied
by a caller can expand the deletion. The daemon has already tombstoned the
account; filesystem deletion and external identity cleanup remain its phases.
"""
from __future__ import annotations

ACCOUNT_SCOPE = "account-deletion"
TABLES = frozenset({
    "outbound_connections", "outbound_connection_grants", "connection_capabilities",
    "outbound_connector_artifacts", "outbound_connector_artifact_edges",
    "agent_request_usage", "agent_request_attempts", "agent_request_usage_links",
    "agent_request_dispatches", "browser_vault", "owner_policy",
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
    from tinyassets.broker.vault import erase_owner

    erase_owner(ledger._data_root, principal)
    from tinyassets.broker.browser_vault import key_path

    path = key_path(ledger, principal)
    path.unlink(missing_ok=True)
    (ledger._db_path.parent / 'browser-vault' / path.name).unlink(missing_ok=True)
    return counts
