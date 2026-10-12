"""Withdraw a connection's model authority without changing user-authored agents."""

from pathlib import Path


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS connection_disconnections (
        universe_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
        connection_id TEXT NOT NULL, grant_id TEXT NOT NULL, incarnation TEXT NOT NULL,
        destination TEXT NOT NULL, model_source INTEGER NOT NULL,
        completed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(universe_id, owner_user_id, connection_id))""")


def fence_connection(
    base, universe, *, owner, uid, connection_id, grant_id, incarnation, destination
):
    """Commit denial before custody deletion. Caller serializes connection gestures."""
    from tinyassets.provider_assignment import provider_assignment_admission
    from tinyassets.provider_work_authority import (
        ProviderWorkBindingFence,
        ProviderWorkBindingService,
    )
    from tinyassets.providers.definition import list_definitions
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    affected = {
        "api_key_http:" + d.id
        for d in list_definitions(uid)
        if d.owner_user_id == owner and d.access_method == "api_key_http" and d.ref == grant_id
    }
    store = SQLiteProviderWorkAuthorityStore(base)
    with provider_assignment_admission().exclusive(universe):
        with store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _schema(conn)
            conn.execute(
                """INSERT INTO connection_disconnections
                (universe_id,owner_user_id,connection_id,grant_id,incarnation,destination,model_source)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(universe_id,owner_user_id,connection_id)
                DO UPDATE SET incarnation=excluded.incarnation, grant_id=excluded.grant_id,
                    destination=excluded.destination, model_source=excluded.model_source,
                    completed=0""",
                (
                    uid,
                    owner,
                    connection_id,
                    grant_id,
                    incarnation,
                    destination,
                    int(bool(affected) or destination.startswith("model:")),
                ),
            )
            records = conn.execute(
                "SELECT binding_id FROM provider_work_bindings WHERE owner_user_id=? "
                "AND universe_id=? AND state='active'",
                (owner, uid),
            ).fetchall()
            for row in records:
                record = store.get_binding_in_transaction(conn, binding_id=row[0])
                if record.provider in affected:
                    ProviderWorkBindingService(store).revoke_in_transaction(
                        conn, ProviderWorkBindingFence(record)
                    )
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_credential_custody'"
            ).fetchone():
                conn.execute(
                    "DELETE FROM llm_credential_custody WHERE owner_user_id=? "
                    "AND universe_id=? AND service=?",
                    (owner, uid, "connection:" + connection_id),
                )
            # A bind can race this gesture using exclusive admission, not the
            # app gesture lock. Revoke egress before releasing admission so it
            # cannot adopt the old grant in the gap before vault deletion.
            from tinyassets.broker.disconnect import disconnect

            disconnect(base, principal=owner, command_center=uid, destination=destination,
                       action="fence", incarnation=incarnation)
            conn.commit()
        from tinyassets.storage.pending_requests import list_pending, resolve_request

        aliases = affected | {name.removeprefix("api_key_http:") for name in affected}
        for request in list_pending(Path(universe)):
            action = request.get("action") or {}
            if action.get("type") == "bind_model_access" and aliases.intersection(
                action.get("model_access") or {}
            ):
                resolve_request(
                    Path(universe),
                    request["request_id"],
                    status="dismissed",
                    feedback="Connection disconnected; review fresh access after reconnecting.",
                )


def complete_disconnect(base, *, owner, uid, connection_id):
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
        _schema(conn)
        conn.execute(
            "UPDATE connection_disconnections SET completed=1 WHERE universe_id=? "
            "AND owner_user_id=? AND connection_id=?",
            (uid, owner, connection_id),
        )
        conn.commit()


def unfinished_disconnections(base, *, owner, uid):
    """Keep failed cleanup reachable even after its ledger row was erased."""
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
        _schema(conn)
        rows = conn.execute(
            "SELECT connection_id,destination,incarnation FROM connection_disconnections "
            "WHERE universe_id=? AND owner_user_id=? AND completed=0",
            (uid, owner),
        ).fetchall()
    return [
        {
            "connection_id": row[0],
            "destination": row[1],
            "incarnation": row[2],
            "cleanup_pending": True,
        }
        for row in rows
    ]


def intentionally_disconnected(base, *, owner, uid):
    """No credential IO. A deliberate completed model removal is not failed auth."""
    from tinyassets.provider_assignment import load_provider_assignment_in_transaction
    from tinyassets.providers.definition import list_definitions
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='connection_disconnections'"
        ).fetchone():
            return False
        conn.execute("BEGIN")
        assignment = load_provider_assignment_in_transaction(conn, universe_id=uid)
        if assignment and (assignment.owner_user_id != owner or assignment.state != "unassigned"):
            return False
        rows = conn.execute(
            "SELECT connection_id,destination FROM connection_disconnections WHERE "
            "universe_id=? AND owner_user_id=? AND model_source=1 AND completed=1",
            (uid, owner),
        ).fetchall()
    from tinyassets.api.http_connection import _ids
    from tinyassets.broker.disconnect import disconnect
    from tinyassets.broker.ledger_queries import granted_resource_row
    from tinyassets.storage.outbound_connections import GrantResolutionError

    for definition in list_definitions(uid):
        if definition.owner_user_id != owner or definition.access_method != "api_key_http":
            continue
        try:
            granted_resource_row(base, principal=owner, command_center=uid,
                                 grant_id=definition.ref)
        except GrantResolutionError:
            continue
        return False
    for row in rows:
        # Completed records are daemon state, but still validate the exact
        # identity before using the destination-based broker recovery read.
        if _ids(universe_id=uid, destination=row[1])[0] != row[0]:
            return False
        try:
            snapshot = disconnect(base, principal=owner, command_center=uid,
                                  destination=row[1])
        except GrantResolutionError:
            return False
        if snapshot["resource"] is not None:
            return False
    return bool(rows)
