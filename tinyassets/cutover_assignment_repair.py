"""One-shot stopped-cutover repair. Delete with box_layout_migration after cutover.

Reads one mounted owner volume and the stopped broker's grant ledger. No catalogue,
credential probe, or runtime caller; absence alone removes membership. ``--dry-run``
computes the same plan with everything open read-only and writes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
from pathlib import Path

from tinyassets.boxes.bootstrap import RUNTIME

BROKER_SOCKET = RUNTIME / "broker/broker.sock"
PROC_ROOT = Path("/proc")

# A row in this owner's center store that names another center or another owner is
# not this owner's definition. It is reported by reason code and count -- never by
# id or content -- and membership pointing at it is removed as a missing
# definition. The foreign row itself is never rewritten, and one such row never
# aborts the rest of this owner's repair.
FOREIGN_CENTER = "definition_center_mismatch"
FOREIGN_OWNER = "definition_owner_mismatch"


def require_stopped(owner_root):
    # A successful connect is enough: do not send any broker operation.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(1)
        try:
            channel.connect(str(BROKER_SOCKET))
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        except OSError as exc:
            raise RuntimeError("cannot establish that the broker is stopped") from exc
        else:
            raise RuntimeError("broker is still answering; stop it before repair")
    # boxhostd/gofer attachments live in private mount namespaces. Checking
    # only our mount table would miss exactly the live volume we must refuse.
    device = owner_root.stat().st_dev
    device_id = f"{os.major(device)}:{os.minor(device)}"
    seen = {(PROC_ROOT / "self/mountinfo").read_text()}
    for process in PROC_ROOT.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            mounts = (process / "mountinfo").read_text()
            if mounts in seen:
                continue
        except (FileNotFoundError, ProcessLookupError):
            continue  # Process exited during the census.
        except OSError as exc:
            raise RuntimeError("cannot establish that owner volume is detached") from exc
        seen.add(mounts)
        if any(line.split()[2] == device_id for line in mounts.splitlines()):
            raise RuntimeError("owner volume is attached in another process namespace")


def _center_ids(conn):
    ids = []
    for row in conn.execute("SELECT universe_id FROM provider_assignments").fetchall():
        uid = row[0]
        if not uid or Path(uid).name != uid or uid in {".", ".."}:
            raise ValueError("invalid center id")
        ids.append(uid)
    return ids


def _owned_definitions(owner_root: Path, uid: str, owner_user_id: str):
    """This owner's definitions in this center, and foreign-row counts by reason.

    A tampered row -- one whose id is not the content address of its own fields --
    still raises: that is corruption of this owner's store, not another owner's row.
    """
    from tinyassets.providers.definition import _verified_definition

    path = owner_root / uid / "provider_definitions.json"
    documents = json.loads(path.read_text("utf-8")) if path.exists() else []
    definitions, foreign = {}, {}
    for document in documents:
        definition = _verified_definition(document)
        reason = (FOREIGN_CENTER if definition.universe_id != uid else
                  FOREIGN_OWNER if definition.owner_user_id != owner_user_id else None)
        if reason is not None:
            foreign[reason] = foreign.get(reason, 0) + 1
            continue
        definitions[definition.id] = definition
    return definitions, foreign


def _survives(member, definitions, owner_user_id, grant_ids):
    method, ident = member.provider.split(":", 1)
    definition = definitions.get(ident)
    if definition is None:
        return False
    if definition.access_method != method:
        raise ValueError("assignment definition identity mismatch")
    return method != "api_key_http" or (owner_user_id, definition.ref) in grant_ids


def _root_after(assignment, kept):
    """The root ``remove_members`` anchors on: the current root when it survives,
    else the first survivor; no survivor leaves the center unassigned."""
    if not kept:
        return None
    return next((m.provider for m in kept if m.provider == assignment.provider),
                kept[0].provider)


def _plan(conn, owner_root: Path, centers, grant_ids):
    """Read only. The dry run prints this plan; the real run applies exactly it."""
    from tinyassets.provider_assignment import load_provider_assignment_in_transaction

    planned = []
    for uid in centers:
        assignment = load_provider_assignment_in_transaction(conn, universe_id=uid)
        if not assignment.candidates:
            continue
        definitions, foreign = _owned_definitions(owner_root, uid, assignment.owner_user_id)
        kept = tuple(m for m in assignment.candidates
                     if _survives(m, definitions, assignment.owner_user_id, grant_ids))
        if kept == assignment.candidates and not foreign:
            continue
        change = {"universe_id": uid,
                  "removed": [m.provider for m in assignment.candidates if m not in kept],
                  "old_root": assignment.provider,
                  "root": _root_after(assignment, kept)}
        if foreign:
            change["foreign_definitions"] = dict(sorted(foreign.items()))
        planned.append((assignment, kept, change))
    return planned


def _preview(owner_db: str, owner_root: Path, grant_ids):
    """The real run's plan, with every byte of the volume opened read-only."""
    with sqlite3.connect(owner_db + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")  # Deferred: a consistent read that takes no write lock.
        try:
            return [c for _, _, c in _plan(conn, owner_root, _center_ids(conn), grant_ids)]
        except sqlite3.OperationalError as exc:
            if "readonly" not in str(exc):
                raise
            raise RuntimeError("cannot plan this volume read-only: its assignment "
                               "schema predates the tables the repair run creates") from exc
        finally:
            conn.rollback()


def repair(owner_root: Path, ledger: Path, *, dry_run: bool = False):
    from tinyassets.provider_assignment import load_provider_assignment_in_transaction
    from tinyassets.provider_assignment_departure import remove_members
    from tinyassets.storage import DB_FILENAME, db_path

    require_stopped(owner_root)
    changes = []
    # mode=rw/ro refuses a misspelled path rather than inventing an empty ledger.
    with sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True) as grants:
        grant_ids = set(grants.execute(
            "SELECT owner_user_id,grant_id FROM outbound_connection_grants"))
    # db_path promotes a legacy filename by renaming it, which is a write: a dry run
    # reads the canonical name and mode=ro refuses when it is absent.
    owner_db = (owner_root / DB_FILENAME if dry_run else db_path(owner_root)).resolve().as_uri()
    if dry_run:
        return _preview(owner_db, owner_root, grant_ids)
    with sqlite3.connect(owner_db + "?mode=rw", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        centers = _center_ids(conn)
        for assignment, kept, change in _plan(conn, owner_root, centers, grant_ids):
            changes.append(change)
            if kept == assignment.candidates:
                continue  # Foreign rows only: reported, nothing of this owner's to change.
            updated = remove_members(conn, assignment, kept)
            if (updated.provider if kept else None) != change["root"]:
                raise RuntimeError("repaired root differs from the planned root")
        conn.commit()
        # Projections are non-authorizing files. Refresh on every invocation so
        # rerunning after interruption between the DB commit and file write repairs them too.
        from tinyassets.config import write_provider_assignment_projection

        for uid in centers:
            assignment = load_provider_assignment_in_transaction(conn, universe_id=uid)
            write_provider_assignment_projection(owner_root / uid, state=assignment.state,
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
    parser.add_argument("--dry-run", action="store_true",
                        help="plan read-only and print the same summary without writing")
    args = parser.parse_args()
    print(json.dumps(repair(args.owner_root, args.ledger, dry_run=args.dry_run), sort_keys=True))


if __name__ == "__main__":
    main()
