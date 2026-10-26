"""Explicit membership departure inside an owner's assignment transaction."""
from contextlib import ExitStack, contextmanager
from dataclasses import replace

_ANCHOR = (
    "provider", "binding_id", "binding_generation", "binding_digest",
    "credential_reference_id", "credential_reference_generation",
    "credential_reference_digest",
)


def _rewrite_members(conn, assignment, kept, anchor, *, state):
    """Store `kept` under `anchor` and rebuild digests plus the agent pointer.

    `anchor` is a member of `kept`, or None when the center is unassigned.
    The caller chooses the root. This writer does not promote another member.
    """
    from tinyassets.provider_assignment import (
        provider_assignment_digest,
        store_provider_assignment_in_transaction,
    )
    from tinyassets.provider_assignment_manifest import manifest_digest
    from tinyassets.provider_work_authority import ProviderWorkBindingState
    from tinyassets.storage.provider_work_authority import _payload, _record

    if anchor is not None and anchor.provider not in {m.provider for m in kept}:
        raise ValueError("assignment root is not a member")
    if anchor is None and kept:
        raise ValueError("assignment members require their root")
    uid = assignment.universe_id
    updated = replace(
        assignment, generation=assignment.generation + 1, candidates=kept, state=state,
    )
    if anchor is not None:
        updated = replace(updated, **{name: getattr(anchor, name) for name in _ANCHOR})
    updated = replace(updated, manifest_digest=(
        manifest_digest(updated.provider, kept) if anchor is not None else ""))
    updated = replace(updated, assignment_digest=provider_assignment_digest(**{
        name: getattr(updated, name) for name in (
            "owner_user_id", "universe_id", "provider", "generation", "binding_id",
            "credential_reference_id", "credential_reference_generation",
            "credential_reference_digest", "manifest_digest")}))
    wanted = {member.binding_id: member for member in kept}
    incoming = [
        member for member in kept
        if all(member.binding_id != old.binding_id for old in assignment.candidates)
    ]
    members = []
    for member in (*assignment.candidates, *incoming):
        row = conn.execute(
            "SELECT * FROM provider_work_bindings WHERE binding_id=?",
            (member.binding_id,),
        ).fetchone()
        seated = member.binding_id in wanted
        if row is None:
            if seated:
                raise ValueError("surviving member has no work binding")
            continue
        binding = _record(row)
        if seated:
            # An active binding cannot carry a revocation generation. Re-adding a
            # member the repair revoked clears it; a member that stayed active
            # already has zero.
            binding = replace(
                binding, generation=binding.generation + 1,
                state=ProviderWorkBindingState.ACTIVE, revocation_generation=0,
                assignment_generation=updated.generation,
                assignment_digest=updated.assignment_digest,
            )
        else:
            binding = replace(
                binding, generation=binding.generation + 1,
                state=ProviderWorkBindingState.REVOKED,
                revocation_generation=binding.revocation_generation + 1,
            )
        binding = replace(binding, binding_digest=binding.expected_digest())
        conn.execute(
            "INSERT OR REPLACE INTO provider_work_bindings VALUES (?,?,?,?,?,?,?,?)",
            _payload(binding),
        )
        if seated:
            source = wanted[member.binding_id]
            members.append(replace(
                source, binding_generation=binding.generation,
                binding_digest=binding.binding_digest,
            ))
    updated = replace(updated, candidates=tuple(members))
    if anchor is not None:
        anchor = next(m for m in members if m.provider == updated.provider)
        updated = replace(
            updated, binding_generation=anchor.binding_generation,
            binding_digest=anchor.binding_digest,
        )
    store_provider_assignment_in_transaction(conn, updated)
    conn.execute(
        "DELETE FROM provider_assignment_candidates WHERE universe_id=? "
        "AND assignment_generation<>?",
        (uid, updated.generation),
    )
    if anchor is None:
        conn.execute(
            "DELETE FROM provider_assignment_candidates WHERE universe_id=?", (uid,),
        )
    # Keep the user's agent content and serving intent; only its authority pointer changes.
    conn.execute(
        "UPDATE agent_bindings SET configuration_json=json_set("
        "configuration_json,'$.provider_ref',?),revision=revision+1 "
        "WHERE universe_id=? AND created_by=? "
        "AND json_extract(configuration_json,'$.provider_ref')=?",
        (updated.binding_id, uid, assignment.owner_user_id, assignment.binding_id),
    )
    return updated


def remove_members(conn, assignment, kept):
    anchor = next(
        (member for member in kept if member.provider == assignment.provider),
        kept[0] if kept else None,
    )
    return _rewrite_members(
        conn, assignment, tuple(kept), anchor,
        state=assignment.state if kept else "unassigned",
    )


def add_members(conn, assignment, members, *, root):
    """Seat `members` with their own access under `root`, or leave the row untouched.

    `members` is the full set (members already seated, plus ones being restored).
    `root` must already be one of them. A second call with the same set and root
    does not bump the generation.
    """
    seated = tuple(members)
    providers = {member.provider for member in seated}
    if len(providers) != len(seated) or not root or root not in providers:
        raise ValueError("assignment root is not a member")
    same = (
        assignment.state == "ready"
        and assignment.provider == root
        and {member.provider: member.access.document() for member in assignment.candidates}
        == {member.provider: member.access.document() for member in seated}
    )
    if same:
        return assignment
    anchor = next(member for member in seated if member.provider == root)
    return _rewrite_members(conn, assignment, seated, anchor, state="ready")


@contextmanager
def disconnect_subscription(owner, source_id):
    """An explicit owner departure; never called by runtime failure handling."""
    import sqlite3

    from tinyassets.config import write_provider_assignment_projection
    from tinyassets.owner_centers import Centers
    from tinyassets.provider_assignment import (
        ensure_provider_assignment_schema,
        load_provider_assignment_in_transaction,
        provider_assignment_admission,
    )
    from tinyassets.providers.definition import get_definition, remove_definitions
    from tinyassets.storage import db_path

    with sqlite3.connect(db_path(owner.root)) as conn:
        ensure_provider_assignment_schema(conn)
        assigned = {row[0] for row in conn.execute("SELECT universe_id FROM provider_assignments")}
    # A candidate registered but never assigned has no assignment row, and its
    # definition still has to go, so every active center is visited.
    centers = sorted(assigned | set(Centers(owner.root).active()))
    with ExitStack() as locks:
        for uid in centers:
            locks.enter_context(provider_assignment_admission().exclusive(owner.root / uid))
        for uid in centers:
            universe = owner.root / uid
            updated = None
            with sqlite3.connect(db_path(owner.root)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN IMMEDIATE")
                assignment = load_provider_assignment_in_transaction(conn, universe_id=uid)
                if assignment is not None and assignment.owner_user_id != owner.principal:
                    raise PermissionError("owned assignment required")

                def departing(member):
                    method, ident = member.provider.split(":", 1)
                    if method != "subscription_cli":
                        return False
                    definition = get_definition(uid, ident)
                    return definition is not None and definition.ref == source_id

                if assignment is not None:
                    kept = tuple(m for m in assignment.candidates if not departing(m))
                    if kept != assignment.candidates:
                        updated = remove_members(conn, assignment, kept)
            if updated is not None:
                write_provider_assignment_projection(universe, state=updated.state,
                    generation=updated.generation, provider=updated.provider,
                    binding={"binding_id": updated.binding_id,
                             "generation": updated.binding_generation,
                             "binding_digest": updated.binding_digest,
                             "assignment_digest": updated.assignment_digest},
                    assignment_candidates=(updated.candidates or None)
                    if updated.state == "ready" else None)
            # After membership, never before: departing() resolves the member
            # through the definition this removes.
            remove_definitions(uid, owner_user_id=owner.principal,
                               access_method="subscription_cli", ref=source_id)

        yield
