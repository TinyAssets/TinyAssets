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


def publish_assignment(center_dir, assignment):
    """Publish the non-authorizing projection. The authority record does not need PyYAML."""
    from tinyassets.config import write_provider_assignment_projection
    from tinyassets.provider_authority import write_record

    ready = assignment.state == "ready" and bool(assignment.candidates)
    members = {member.provider: member for member in assignment.candidates} if ready else {}
    allowed = sorted(members)
    record = {
        "allowed_providers": allowed if ready else [],
        "engine_assignment_generation": assignment.generation,
        "engine_assignment_state": assignment.state,
        "provider_authority_bindings": {
            name: {
                "binding_id": members[name].binding_id,
                "generation": members[name].binding_generation,
                "binding_digest": members[name].binding_digest,
                "assignment_digest": assignment.assignment_digest,
            }
            for name in allowed
        },
    }
    try:
        write_provider_assignment_projection(
            center_dir, state=assignment.state, generation=assignment.generation,
            provider=assignment.provider,
            binding={
                "binding_id": assignment.binding_id,
                "generation": assignment.binding_generation,
                "binding_digest": assignment.binding_digest,
                "assignment_digest": assignment.assignment_digest,
            },
            assignment_candidates=assignment.candidates if ready else None,
        )
    except ImportError:
        write_record(center_dir, record)


def apply_member_decision(db_path, center, decision):
    """Seat one planned set. Returns the stored assignment, or None when nothing changed."""
    import sqlite3

    from tinyassets.provider_assignment import load_provider_assignment_in_transaction

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        live = load_provider_assignment_in_transaction(conn, universe_id=center)
        updated = add_members(conn, live, decision["members"], root=decision["root"])
        if updated.generation == live.generation:
            conn.rollback()
            return None
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return updated
    finally:
        conn.close()


def _member_counts():
    return {
        "members_readded": 0,
        "skipped_no_grant": 0,
        "skipped_no_definition": 0,
        "refused_mismatch": 0,
        "centers_changed": 0,
        "skipped_no_binding": 0,
        "volumes_detached": 0,
    }


def _http_candidate(binding, *, owner, center, connection_id, credential_ref, grant_id, model):
    """The candidate serve-time custody recomputes for this live grant."""
    import hashlib

    from tinyassets.credential_vault import (
        _connection_custody_service,
        _connection_grant_record_digest,
        _custody_reference_digest,
    )
    from tinyassets.provider_assignment_manifest import AssignmentCandidate, ModelAccess

    reference_id = "llm_credential_" + hashlib.sha256(
        f"{owner}:{center}:{connection_id}".encode("utf-8"),
    ).hexdigest()[:32]
    record = _connection_grant_record_digest(
        grant_id=grant_id, connection_id=connection_id, credential_ref=credential_ref,
        owner_user_id=owner, universe_id=center,
    )
    digest = _custody_reference_digest(
        reference_id=reference_id, owner_user_id=owner, universe_id=center,
        service=_connection_custody_service(connection_id), generation=1,
        record_digest=record,
    )
    return AssignmentCandidate(
        binding.provider, binding.binding_id, binding.generation, binding.binding_digest,
        reference_id, 1, digest, ModelAccess("explicit", (model,), None),
    )


def _add_http_members(live, definitions, foreign, bindings, grant_for, center, counts):
    """Granted http sources missing from `live`, keeping every access already seated."""
    from tinyassets.provider_work_authority import ProviderWorkBindingState

    chosen = list(live.candidates)
    seen = {member.provider for member in chosen}
    added = []
    owner = live.owner_user_id
    known = set(definitions) | set(foreign)
    for ident in foreign:
        if ident not in definitions:
            counts["refused_mismatch"] += 1
    for ident in sorted(definitions):
        document = definitions[ident]
        if not isinstance(document, dict):
            counts["refused_mismatch"] += 1
            continue
        if document.get("access_method") != "api_key_http":
            continue
        provider = "api_key_http:" + ident
        if provider in seen:
            continue
        model, ref = document.get("model"), document.get("ref")
        if not isinstance(model, str) or not model or not isinstance(ref, str) or not ref:
            counts["skipped_no_definition"] += 1
            continue
        try:
            grant = grant_for(center, ref)
        except (OSError, ValueError, RuntimeError):
            grant = None
        if grant is None:
            counts["skipped_no_grant"] += 1
            continue
        connection_id, credential_ref = grant
        usable = [
            binding for binding in bindings.get(provider, ())
            if binding.owner_user_id == owner and binding.universe_id == center
            and binding.provider == provider
            and binding.state in {
                ProviderWorkBindingState.ACTIVE, ProviderWorkBindingState.REVOKED,
            }
        ]
        if len(usable) != 1:
            counts["skipped_no_binding" if not usable else "refused_mismatch"] += 1
            continue
        try:
            candidate = _http_candidate(
                usable[0], owner=owner, center=center, connection_id=connection_id,
                credential_ref=credential_ref, grant_id=ref, model=model,
            )
        except (TypeError, ValueError):
            counts["refused_mismatch"] += 1
            continue
        chosen.append(candidate)
        seen.add(provider)
        added.append(provider)
    for provider, rows in bindings.items():
        if not provider.startswith("api_key_http:") or provider in seen:
            continue
        ident = provider.split(":", 1)[1]
        if ident in known or not rows:
            continue
        counts["skipped_no_definition"] += 1
        seen.add(provider)
    if not added:
        return None
    providers = {member.provider for member in chosen}
    root = live.provider if live.provider in providers else None
    if root is None:
        http = sorted(provider for provider in providers if provider.startswith("api_key_http:"))
        root = http[0] if http else None
    if root is None:
        return None
    return {"members": tuple(chosen), "root": root, "added": len(added)}


def reseat_volume(root, owner, grant_for, definitions_for, *, write):
    """Seat this mounted owner's granted http sources. A missing database is detached.

    `grant_for(center, ref)` returns `(connection_id, credential_ref)` or None.
    `definitions_for(center)` returns `(id -> document, foreign ids)` or `(None, …)`
    when the center's definition file cannot be trusted. Dry-run writes a disposable
    copy. Already seated access is left as it is.
    """
    import sqlite3
    import tempfile
    from pathlib import Path

    from tinyassets.provider_assignment import load_provider_assignment_in_transaction
    from tinyassets.sqlite_readonly import readonly_db
    from tinyassets.storage.provider_work_authority import _record

    counts = _member_counts()
    root = Path(root)
    db_path = root / ".tinyassets.db"
    if (root.is_symlink() or not root.is_dir() or db_path.is_symlink()
            or not db_path.is_file()):
        counts["volumes_detached"] = 1
        return counts
    try:
        with readonly_db(db_path) as source:
            source.row_factory = sqlite3.Row
            source.execute("BEGIN")
            centers = [
                row[0] for row in source.execute(
                    "SELECT universe_id FROM provider_assignments",
                )
            ]
    except sqlite3.Error:
        counts["refused_mismatch"] += 1
        return counts
    decisions = []
    for center in centers:
        if not isinstance(center, str) or not center or center in {".", ".."}:
            counts["refused_mismatch"] += 1
            continue
        if Path(center).name != center:
            counts["refused_mismatch"] += 1
            continue
        try:
            definitions, foreign = definitions_for(center)
        except (OSError, ValueError, RuntimeError):
            counts["refused_mismatch"] += 1
            continue
        if definitions is None:
            counts["refused_mismatch"] += 1
            continue
        try:
            with readonly_db(db_path) as source:
                source.row_factory = sqlite3.Row
                source.execute("BEGIN")
                live = load_provider_assignment_in_transaction(source, universe_id=center)
                rows = source.execute(
                    "SELECT * FROM provider_work_bindings "
                    "WHERE owner_user_id=? AND universe_id=?",
                    (owner, center),
                ).fetchall()
                bindings = {}
                for row in rows:
                    binding = _record(row)
                    bindings.setdefault(binding.provider, []).append(binding)
        except (sqlite3.Error, RuntimeError, ValueError):
            counts["refused_mismatch"] += 1
            continue
        if live is None:
            counts["skipped_no_binding"] += 1
            continue
        if live.owner_user_id != owner:
            counts["refused_mismatch"] += max(1, len(live.candidates))
            continue
        decision = _add_http_members(
            live, definitions, foreign, bindings, grant_for, center, counts,
        )
        if decision is not None:
            decisions.append((center, decision))
    if not decisions:
        return counts
    disposable = None
    target_root = root
    target_db = db_path
    try:
        if not write:
            disposable = tempfile.TemporaryDirectory(prefix="ta-assign-")
            target_root = Path(disposable.name)
            target_db = target_root / ".tinyassets.db"
            copied = sqlite3.connect(target_db)
            try:
                with readonly_db(db_path) as src:
                    src.backup(copied)
            finally:
                copied.close()
        for center, decision in decisions:
            try:
                updated = apply_member_decision(target_db, center, decision)
            except (sqlite3.Error, OSError, RuntimeError, ValueError):
                counts["refused_mismatch"] += decision["added"] or 1
                continue
            if updated is None:
                continue
            center_dir = target_root / center
            center_dir.mkdir(parents=True, exist_ok=True)
            try:
                publish_assignment(center_dir, updated)
            except (OSError, RuntimeError, ValueError):
                pass
            counts["members_readded"] += decision["added"]
            counts["centers_changed"] += 1
    finally:
        if disposable is not None:
            disposable.cleanup()
    return counts


def _daemon_definitions(center, owner):
    from tinyassets.providers.definition import ProviderDefinitionError, list_definitions

    try:
        rows = list_definitions(center)
    except (ProviderDefinitionError, OSError, ValueError):
        return None, set()
    found, foreign = {}, set()
    for item in rows:
        if item.access_method not in {"api_key_http", "subscription_cli"}:
            continue
        if item.owner_user_id != owner or item.universe_id != center:
            foreign.add(item.id)
            continue
        found[item.id] = {
            "id": item.id, "model": item.model, "ref": item.ref,
            "access_method": item.access_method,
        }
    return found, foreign


def _broker_grant(owner, center, ref):
    from tinyassets.broker.client import BrokerRefused
    from tinyassets.broker.ledger_queries import (
        HAS_MODEL_SOURCE,
        granted_resource_row,
        query_ledger,
    )
    from tinyassets.owner_runtime import current
    from tinyassets.storage.outbound_connections import (
        GrantResolutionError,
        ProxyRequestError,
    )

    runtime = current()
    if runtime is None or not isinstance(ref, str) or not ref:
        return None
    try:
        facts = query_ledger(
            runtime.root, query=HAS_MODEL_SOURCE, principal=owner,
            command_center=center, grant_id=ref,
        )
        if facts.get("model_source") is not True:
            return None
        row = granted_resource_row(
            runtime.root, principal=owner, command_center=center, grant_id=ref,
        )
    except (BrokerRefused, GrantResolutionError, ProxyRequestError, OSError, ValueError):
        return None
    connection_id = row.get("connection_id")
    credential_ref = row.get("credential_ref")
    if (not isinstance(connection_id, str) or not connection_id
            or connection_id.strip() != connection_id
            or not isinstance(credential_ref, str) or not credential_ref
            or credential_ref.strip() != credential_ref):
        return None
    return connection_id, credential_ref


def reseat_awake_owner():
    """Seat this box's granted http sources. The volume is mounted before startup."""
    import logging

    from tinyassets.owner_runtime import current

    runtime = current()
    if runtime is None:
        return None
    owner = runtime.principal
    try:
        return reseat_volume(
            runtime.root, owner,
            lambda center, ref: _broker_grant(owner, center, ref),
            lambda center: _daemon_definitions(center, owner),
            write=True,
        )
    except Exception as exc:
        logging.getLogger("tinyassets.owner_daemon").warning(
            "assignment reseat failed: %s", type(exc).__name__,
        )
        return None


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
