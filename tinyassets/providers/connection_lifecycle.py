"""Withdraw a connection's model authority without changing user-authored agents."""

from dataclasses import replace
from pathlib import Path


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS connection_disconnections (
        universe_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
        connection_id TEXT NOT NULL, grant_id TEXT NOT NULL, incarnation TEXT NOT NULL,
        destination TEXT NOT NULL, model_source INTEGER NOT NULL,
        completed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(universe_id, owner_user_id, connection_id))""")


def _digest(assignment):
    from tinyassets.provider_assignment import provider_assignment_digest

    return provider_assignment_digest(
        **{
            name: getattr(assignment, name)
            for name in (
                "owner_user_id",
                "universe_id",
                "provider",
                "generation",
                "binding_id",
                "credential_reference_id",
                "credential_reference_generation",
                "credential_reference_digest",
                "manifest_digest",
            )
        }
    )


def fence_connection(
    base, universe, *, owner, uid, connection_id, grant_id, incarnation, destination
):
    """Commit denial before custody deletion. Caller serializes connection gestures."""
    from tinyassets.custom_agents import (
        set_binding_provider_ref_in_transaction,
        set_binding_serving_in_transaction,
    )
    from tinyassets.provider_assignment import (
        load_provider_assignment_in_transaction,
        provider_assignment_admission,
        store_provider_assignment_in_transaction,
    )
    from tinyassets.provider_assignment_manifest import manifest_digest
    from tinyassets.provider_serving_binding import _ServingResolver
    from tinyassets.provider_work_authority import (
        ProviderWorkAuthorityWriteOutcome,
        ProviderWorkBindingFence,
        ProviderWorkBindingRoot,
        ProviderWorkBindingSeed,
        ProviderWorkBindingService,
        ProviderWorkBindingState,
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
            assignment = load_provider_assignment_in_transaction(conn, universe_id=uid)
            if assignment is not None and assignment.owner_user_id != owner:
                raise PermissionError("connection assignment owner changed")
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
            if assignment and (
                assignment.provider in affected
                or any(member.provider in affected for member in assignment.candidates)
            ):
                remaining = tuple(
                    m
                    for m in assignment.candidates
                    if m.provider not in affected
                    and (prior := store.get_binding_in_transaction(conn, binding_id=m.binding_id))
                    and prior.state is ProviderWorkBindingState.ACTIVE
                    and prior.generation == m.binding_generation
                    and prior.binding_digest == m.binding_digest
                )
                if remaining:
                    anchor = next(
                        (m for m in remaining if m.provider == assignment.provider), remaining[0]
                    )
                    changed = replace(
                        assignment,
                        generation=assignment.generation + 1,
                        provider=anchor.provider,
                        binding_id=anchor.binding_id,
                        binding_generation=anchor.binding_generation,
                        binding_digest=anchor.binding_digest,
                        credential_reference_id=anchor.credential_reference_id,
                        credential_reference_generation=anchor.credential_reference_generation,
                        credential_reference_digest=anchor.credential_reference_digest,
                        candidates=remaining,
                        manifest_digest=manifest_digest(anchor.provider, remaining),
                        updated_at=store.timestamp(),
                    )
                    changed = replace(changed, assignment_digest=_digest(changed))
                    members = []
                    for member in remaining:
                        prior = store.get_binding_in_transaction(conn, binding_id=member.binding_id)
                        if prior is None or prior.generation != member.binding_generation:
                            raise PermissionError("remaining model authority changed")
                        seed = ProviderWorkBindingSeed(
                            owner_user_id=owner,
                            universe_id=uid,
                            provider=prior.provider,
                            credential_reference_digest=prior.credential_reference_digest,
                            allowed_operations=prior.allowed_operations,
                            allowed_roles=prior.allowed_roles,
                            assignment_generation=changed.generation,
                            assignment_digest=changed.assignment_digest,
                            max_invocations=prior.max_invocations,
                            max_tokens=prior.max_tokens,
                            max_cost_microunits=prior.max_cost_microunits,
                            expires_at=prior.expires_at,
                        )
                        result = ProviderWorkBindingService(
                            store, _ServingResolver(seed)
                        ).rebind_in_transaction(
                            conn,
                            ProviderWorkBindingFence(prior),
                            ProviderWorkBindingRoot(owner, uid, prior.provider),
                        )
                        if result.record is None or result.outcome not in {
                            ProviderWorkAuthorityWriteOutcome.APPLIED,
                            ProviderWorkAuthorityWriteOutcome.REPLAYED,
                        }:
                            raise PermissionError(
                                "remaining model authority could not be preserved"
                            )
                        members.append(
                            replace(
                                member,
                                binding_generation=result.record.generation,
                                binding_digest=result.record.binding_digest,
                            )
                        )
                    anchor = next(m for m in members if m.provider == changed.provider)
                    changed = replace(
                        changed,
                        candidates=tuple(members),
                        binding_generation=anchor.binding_generation,
                        binding_digest=anchor.binding_digest,
                    )
                else:
                    changed = replace(
                        assignment,
                        state="unassigned",
                        generation=assignment.generation + 1,
                        updated_at=store.timestamp(),
                    )
                    changed = replace(changed, assignment_digest=_digest(changed))
                store_provider_assignment_in_transaction(conn, changed)
                if not remaining:
                    rows = conn.execute(
                        "SELECT agent_binding_id,revision FROM agent_bindings "
                        "WHERE universe_id=? AND created_by=? AND status='serving' AND "
                        "json_extract(configuration_json,'$.provider_ref')=?",
                        (uid, owner, assignment.binding_id),
                    ).fetchall()
                    for row in rows:
                        # This is runtime intent, not user content. The canonical
                        # helper preserves configuration and revision unchanged.
                        set_binding_serving_in_transaction(
                            conn,
                            universe_id=uid,
                            binding_id=row[0],
                            expected_revision=row[1],
                            owner_user_id=owner,
                            enabled=False,
                        )
                # Only the internal provider pointer changes on re-anchor. Content stays verbatim.
                if remaining and changed.binding_id != assignment.binding_id:
                    rows = conn.execute(
                        "SELECT agent_binding_id,revision,status FROM agent_bindings "
                        "WHERE universe_id=? AND created_by=? AND "
                        "json_extract(configuration_json,'$.provider_ref')=?",
                        (uid, owner, assignment.binding_id),
                    ).fetchall()
                    for row in rows:
                        updated = set_binding_provider_ref_in_transaction(
                            conn,
                            universe_id=uid,
                            binding_id=row[0],
                            expected_revision=row[1],
                            owner_user_id=owner,
                            provider_ref=changed.binding_id,
                        )
                        if row[2] == "serving":
                            set_binding_serving_in_transaction(
                                conn,
                                universe_id=uid,
                                binding_id=row[0],
                                expected_revision=updated["revision"],
                                owner_user_id=owner,
                                enabled=True,
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
