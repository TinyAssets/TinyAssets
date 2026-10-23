"""Explicit membership departure inside an owner's assignment transaction."""
from contextlib import ExitStack, contextmanager
from dataclasses import replace


def remove_members(conn, assignment, kept):
    from tinyassets.provider_assignment import (
        provider_assignment_digest,
        store_provider_assignment_in_transaction,
    )
    from tinyassets.provider_assignment_manifest import manifest_digest
    from tinyassets.provider_work_authority import ProviderWorkBindingState
    from tinyassets.storage.provider_work_authority import _payload, _record

    uid = assignment.universe_id
    anchor = next((m for m in kept if m.provider == assignment.provider),
                  kept[0] if kept else None)
    updated = replace(assignment, generation=assignment.generation + 1,
                      candidates=kept, state=assignment.state if kept else "unassigned")
    if anchor:
        updated = replace(updated, **{name: getattr(anchor, name) for name in (
            "provider", "binding_id", "binding_generation", "binding_digest",
            "credential_reference_id", "credential_reference_generation",
            "credential_reference_digest")})
    updated = replace(updated, manifest_digest=(
        manifest_digest(updated.provider, kept) if kept else ""))
    updated = replace(updated, assignment_digest=provider_assignment_digest(**{
        name: getattr(updated, name) for name in (
            "owner_user_id", "universe_id", "provider", "generation", "binding_id",
            "credential_reference_id", "credential_reference_generation",
            "credential_reference_digest", "manifest_digest")}))
    members = []
    for member in assignment.candidates:
        row = conn.execute("SELECT * FROM provider_work_bindings WHERE binding_id=?",
                           (member.binding_id,)).fetchone()
        if row is None:
            if member in kept:
                raise ValueError("surviving member has no work binding")
            continue
        binding = _record(row)
        if member in kept:
            binding = replace(binding, generation=binding.generation + 1,
                              assignment_generation=updated.generation,
                              assignment_digest=updated.assignment_digest)
        else:
            binding = replace(binding, generation=binding.generation + 1,
                              state=ProviderWorkBindingState.REVOKED,
                              revocation_generation=binding.revocation_generation + 1)
        binding = replace(binding, binding_digest=binding.expected_digest())
        conn.execute("INSERT OR REPLACE INTO provider_work_bindings VALUES "
                     "(?,?,?,?,?,?,?,?)", _payload(binding))
        if member in kept:
            members.append(replace(member, binding_generation=binding.generation,
                                   binding_digest=binding.binding_digest))
    updated = replace(updated, candidates=tuple(members))
    if anchor:
        anchor = next(m for m in members if m.provider == updated.provider)
        updated = replace(updated, binding_generation=anchor.binding_generation,
                          binding_digest=anchor.binding_digest)
    store_provider_assignment_in_transaction(conn, updated)
    conn.execute("DELETE FROM provider_assignment_candidates WHERE universe_id=? "
                 "AND assignment_generation<>?", (uid, updated.generation))
    if not kept:
        conn.execute("DELETE FROM provider_assignment_candidates WHERE universe_id=?",
                     (uid,))
    # Keep the user's agent content and serving intent; only its authority pointer changes.
    conn.execute("UPDATE agent_bindings SET configuration_json=json_set("
                 "configuration_json,'$.provider_ref',?),revision=revision+1 "
                 "WHERE universe_id=? AND created_by=? "
                 "AND json_extract(configuration_json,'$.provider_ref')=?",
                 (updated.binding_id, uid, assignment.owner_user_id,
                  assignment.binding_id))
    return updated


@contextmanager
def disconnect_subscription(owner, source_id):
    """An explicit owner departure; never called by runtime failure handling."""
    import sqlite3

    from tinyassets.config import write_provider_assignment_projection
    from tinyassets.provider_assignment import (
        ensure_provider_assignment_schema,
        load_provider_assignment_in_transaction,
        provider_assignment_admission,
    )
    from tinyassets.providers.definition import get_definition
    from tinyassets.storage import db_path

    with sqlite3.connect(db_path(owner.root)) as conn:
        ensure_provider_assignment_schema(conn)
        centers = [row[0] for row in conn.execute("SELECT universe_id FROM provider_assignments")]
    with ExitStack() as locks:
        for uid in sorted(centers):
            locks.enter_context(provider_assignment_admission().exclusive(owner.root / uid))
        for uid in centers:
            universe = owner.root / uid
            with sqlite3.connect(db_path(owner.root)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN IMMEDIATE")
                assignment = load_provider_assignment_in_transaction(conn, universe_id=uid)
                if assignment.owner_user_id != owner.principal:
                    raise PermissionError("owned assignment required")

                def departing(member):
                    method, ident = member.provider.split(":", 1)
                    if method != "subscription_cli":
                        return False
                    definition = get_definition(uid, ident)
                    return definition is not None and definition.ref == source_id

                kept = tuple(m for m in assignment.candidates if not departing(m))
                if kept == assignment.candidates:
                    continue
                updated = remove_members(conn, assignment, kept)
            write_provider_assignment_projection(universe, state=updated.state,
                generation=updated.generation, provider=updated.provider,
                binding={"binding_id": updated.binding_id,
                         "generation": updated.binding_generation,
                         "binding_digest": updated.binding_digest,
                         "assignment_digest": updated.assignment_digest},
                assignment_candidates=(updated.candidates or None)
                if updated.state == "ready" else None)

        yield
