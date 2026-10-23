"""One-shot stopped-cutover repair. Delete with box_layout_migration after cutover.

Reads one mounted owner volume and the stopped broker's grant ledger. No network,
catalogue, credential probe, or runtime caller; absence alone removes membership.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def repair(owner_root: Path, ledger: Path):
    from tinyassets.provider_assignment import load_provider_assignment_in_transaction
    from tinyassets.provider_assignment_departure import remove_members
    from tinyassets.providers.definition import _verified_definition
    from tinyassets.storage import db_path

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
            updated = remove_members(conn, assignment, kept)
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
