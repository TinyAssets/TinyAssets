"""One-shot stopped-cutover repair. Delete with box_layout_migration after cutover.

Reads one mounted owner volume and the stopped broker's grant ledger. No network,
catalogue, credential probe, or runtime caller; absence alone removes membership.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import replace
from pathlib import Path


def repair(owner_root: Path, ledger: Path):
    from tinyassets.provider_assignment import (
        load_provider_assignment_in_transaction,
        provider_assignment_digest,
        store_provider_assignment_in_transaction,
    )
    from tinyassets.provider_assignment_manifest import manifest_digest
    from tinyassets.provider_work_authority import ProviderWorkBindingState
    from tinyassets.providers.definition import _verified_definition
    from tinyassets.storage import db_path
    from tinyassets.storage.provider_work_authority import _payload, _record

    changes = []
    # mode=rw/ro refuses a misspelled path rather than inventing an empty ledger.
    with sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True) as grants:
        grant_ids = set(grants.execute(
            "SELECT owner_user_id,grant_id FROM outbound_connection_grants"))
    with sqlite3.connect(db_path(owner_root).resolve().as_uri() + "?mode=rw", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        centers = conn.execute("SELECT universe_id FROM provider_assignments").fetchall()
        for row in centers:
            uid = row[0]
            if not uid or Path(uid).name != uid or uid in {".", ".."}:
                raise ValueError("invalid center id")
            assignment = load_provider_assignment_in_transaction(conn, universe_id=uid)
            if not assignment.candidates:
                continue
            path = owner_root / uid / "provider_definitions.json"
            documents = json.loads(path.read_text("utf-8")) if path.exists() else []
            definitions = {d.id: d for d in (
                _verified_definition(doc, expect_universe=uid) for doc in documents)}

            def exists(member):
                method, ident = member.provider.split(":", 1)
                definition = definitions.get(ident)
                if definition is None:
                    return False
                if (definition.owner_user_id != assignment.owner_user_id
                        or definition.access_method != method):
                    raise ValueError("assignment definition identity mismatch")
                return method != "api_key_http" or (
                    assignment.owner_user_id, definition.ref) in grant_ids

            kept = tuple(m for m in assignment.candidates if exists(m))
            if kept == assignment.candidates:
                continue
            removed = [m.provider for m in assignment.candidates if m not in kept]
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
            changes.append({"universe_id": uid, "removed": removed,
                            "old_root": assignment.provider,
                            "root": updated.provider if kept else None})
        conn.commit()
        # Projections are non-authorizing files. Refresh on every invocation so
        # rerunning after interruption between the DB commit and file write repairs them too.
        from tinyassets.config import write_provider_assignment_projection

        for row in centers:
            assignment = load_provider_assignment_in_transaction(conn, universe_id=row[0])
            write_provider_assignment_projection(owner_root / row[0], state=assignment.state,
                generation=assignment.generation, provider=assignment.provider,
                binding={"binding_id": assignment.binding_id,
                         "generation": assignment.binding_generation,
                         "binding_digest": assignment.binding_digest,
                         "assignment_digest": assignment.assignment_digest},
                assignment_candidates=(assignment.candidates
                    if assignment.state == "ready" and assignment.candidates else None))
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-root", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--stopped", action="store_true", required=True)
    args = parser.parse_args()
    print(json.dumps(repair(args.owner_root, args.ledger), sort_keys=True))


if __name__ == "__main__":
    main()
