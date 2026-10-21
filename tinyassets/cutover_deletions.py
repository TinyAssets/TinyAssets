"""One-shot cutover reader for old deletion progress (T4 exception).

The collector is memory-only. New durable progress belongs to deletion jobs.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from tinyassets.broker.admission import digest as principal_digest

NAME = ".erasure-ledger.sqlite3"

PHASES = ("broker_channels", "owner_tree", "broker_egress", "billing", "credentials", "identity")


class LegacyFacts:
    def __init__(self, source):
        self.root = source
        self.events = []

    def contains(self, owner):
        return principal_digest(owner) in _project(self.events)

    def entries(self):
        return list(_project(self.events).values())

    def execute(self, operation, args):
        key = args["digest"]
        if operation == "add":
            payload = {"principal": args.get("principal"), "uids": args.get("uids", [])}
            receipt = args.get("receipt", {})
            payload.update(receipt=receipt, done=receipt.get("completed_phases", []))
            if key not in _project(self.events):
                payload["tasks"] = {phase: phase for phase in PHASES}
        elif operation == "work":
            validate_target(args["target"])
            task = uuid.uuid4().hex
            payload = {"work": {task: args["target"]}, "tasks": {task: "owner_tree"}}
        elif operation == "finish":
            payload = {"done": args["ids"]}
        else:
            raise ValueError("invalid legacy import operation")
        self.events.append((uuid.uuid4().hex, key, len(self.events), json.dumps(payload)))


def collect(source, *, store_root):
    from tinyassets.restore_recovery import _rows

    facts = LegacyFacts(source)
    # Read legacy input only. No progress is ever written back to this file.
    rows = _rows(source, NAME, "events")
    facts.events = [(r["id"], r["digest"], r["at"], r["payload"])
                    for r in sorted(rows, key=lambda r: (r["at"], r["id"]))]
    seed_legacy(source, facts, store_root=store_root)
    return facts


def _project(events):
    entries = {}
    for _, digest, _, raw in events:
        event = json.loads(raw)
        entry = entries.setdefault(digest, {"digest": digest, "principal": None,
                                           "uids": [], "tasks": {}, "work": {},
                                           "done": set(), "receipt": {}, "local_done": False})
        if event.get("principal"):
            entry["principal"] = event["principal"]
        entry["uids"] = sorted(set(entry["uids"]) | set(event.get("uids", [])))
        entry["tasks"].update(event.get("tasks", {}))
        entry["work"].update(event.get("work", {}))
        entry["done"].update(event.get("done", []))
        receipt = event.get("receipt", {})
        entry["local_done"] |= receipt.get("local_cleanup") == "done"
        cleanup = {**entry["receipt"].get("billing_cleanup", {}),
                   **receipt.get("billing_cleanup", {})}
        entry["receipt"].update(receipt)
        if cleanup:
            entry["receipt"]["billing_cleanup"] = cleanup
    for entry in entries.values():
        done = entry.pop("done")
        entry["tasks"] = {key: phase for key, phase in entry["tasks"].items() if key not in done}
        entry["work"] = {key: target for key, target in entry["work"].items() if key not in done}
        pending = set(entry["tasks"].values())
        entry["receipt"].update(
            unfinished_phases=[phase for phase in PHASES if phase in pending],
            completed_phases=[phase for phase in PHASES if phase not in pending],
            home_removed="owner_tree" not in pending,
            quarantines=[target["quarantine"] for target in entry["work"].values()])
        if entry.pop("local_done"):
            entry["receipt"]["local_cleanup"] = "done"
        entry["receipt"].pop("restore_pending", None)
    return entries


def validate_target(target):
    if type(target["uid"]) is not int or not 300001 <= target["uid"] <= 399999:
        raise ValueError("invalid erasure UID")
    for key in ("store", "quarantine"):
        path = Path(target[key])
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("erasure targets must be store-relative")
    if Path(target["quarantine"]).name != str(target["uid"]):
        raise ValueError("quarantine differs from erasure UID")


def relative_target(target, store_root):
    """Normalize old obligations only within the one-shot migration."""
    result = dict(target)
    for key in ("store", "quarantine"):
        path = Path(result[key])
        if path.is_absolute():
            for base in (store_root, Path("/box-store"), Path("/var/lib/boxhostd/owners")):
                if path.is_relative_to(base):
                    path = path.relative_to(base)
                    break
            else:
                raise ValueError("legacy erasure target is outside the configured store")
        result[key] = str(path)
    validate_target(result)
    return result


def seed_legacy(source, ledger, *, store_root):
    """Import source and staged pre-cutover facts before retiring their formats."""
    from tinyassets.restore_recovery import _rows

    roots = list(dict.fromkeys((source, ledger.root)))
    for root in roots:
        _seed_legacy_fences(root, ledger, store_root)
    # Resolve digest-only facts after BOTH roots have contributed their fences.
    for root in roots:
        for row in _rows(root, ".broker/state/owner-identities.db", "owner_identities"):
            if ledger.contains(row["principal"]):
                ledger.execute("add", {"digest": principal_digest(row["principal"]),
                                       "principal": row["principal"], "uids": [row["machine_id"]]})
    for root in roots:
        _seed_legacy_work(root, ledger, store_root)


def _seed_legacy_fences(source, ledger, store_root):
    from tinyassets.restore_recovery import _rows, _safe

    for digest in _legacy_main_digests(source):
        ledger.execute("add", {"digest": digest})
    sources = ((".router-deleted-owners.sqlite3", "deleted_owners"),
               (".broker/credential-custody.db", "deleted_principals"),
               (".boxhost/boxhost.db", "owner_destroys"),
               (".broker/state/owner-identities.db", "owner_box_revocations"))
    for relative, table in sources:
        for row in _rows(source, relative, table):
            if table == "owner_box_revocations" and not row["deleted"]:
                continue
            owner = row.get("owner", row.get("owner_id", row.get("principal")))
            digest = row["digest"] if table == "deleted_principals" else principal_digest(owner)
            receipt = json.loads(row.get("receipt") or "null") or {}
            ledger.execute("add", {"digest": digest, "principal": owner, "receipt": receipt,
                                   "uids": [row["uid"]] if table == "owner_destroys" else []})
            for path in receipt.get("quarantines", []):
                destination = Path(path)
                ledger.execute("work", {"digest": digest, "target": relative_target({
                    "uid": int(destination.name), "store": str(destination.parent),
                    "quarantine": str(destination)}, store_root)})
    # Policy erasure removes policy.db but leaves this digest-named fence.
    for marker in (source / ".broker/accounts").glob("*/.deleted"):
        _safe(source, marker.relative_to(source))
        ledger.execute("add", {"digest": marker.parent.name})


def _legacy_main_digests(source):
    """Repair and verify the private staged copy before reading legacy facts."""
    from tinyassets.restore_recovery import _safe
    from tinyassets.sqlite_integrity import check
    from tinyassets.sqlite_readonly import readonly_db

    path = _safe(source, ".tinyassets.db")
    for suffix in ("-wal", "-shm", "-journal"):
        _safe(source, ".tinyassets.db" + suffix)
    if not path.exists():
        return []
    with readonly_db(path) as db:
        check(db, repair=True)
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                          "AND name='deleted_principals'").fetchone():
            return []
        return [row[0] for row in db.execute(
            "SELECT founder_sub FROM deleted_principals NOT INDEXED")]


# Recovery reports can exceed the ordinary 8 MiB universe-file limit. This
# explicit bound applies equally to live, staged and externally located roots.
LEGACY_REPORT_MAX_BYTES = 64 * 1024 * 1024


def _seed_legacy_work(source, ledger, store_root):
    from tinyassets.restore_recovery import _safe

    report_path = _safe(source, ".disaster-restore.json")
    from tinyassets.universe_files import read_universe_file

    try:
        raw = read_universe_file(report_path.parent, report_path.name,
                                 max_bytes=LEGACY_REPORT_MAX_BYTES)
    except FileNotFoundError:
        report = {}
    else:
        report = json.loads(raw)
    for target in report.get("pending_erasures", []) + report.get("erasure_failures", []):
        matches = [entry for entry in ledger.entries() if target["uid"] in entry["uids"]]
        if len(matches) != 1:
            raise ValueError("legacy quarantine has no unique erased UID reservation")
        ledger.execute("work", {"digest": matches[0]["digest"], "target": relative_target({
            "uid": target["uid"], "quarantine": target["quarantine"],
            "store": target.get("store", str(Path(target["quarantine"]).parent))}, store_root)})
    for entry in ledger.entries():
        for key, target in entry["work"].items():
            relative = relative_target(target, store_root)
            if relative != target:
                ledger.execute("work", {"digest": entry["digest"], "target": relative})
                ledger.execute("finish", {"digest": entry["digest"], "ids": [key]})



def finish(facts, shared, store, records):
    """Complete legacy obligations before publication, or refuse the cutover.

    Only this one-shot migration sees old paths. Job receipts contain obligation
    digests and counts, never a legacy owner path for a running service to use.
    A rebuild replays effects against its fresh stage even if a prior attempt
    already wrote a receipt; all external effects must remain idempotent.
    """
    import hashlib
    import sqlite3
    import time
    from contextlib import closing

    from tinyassets.box_layout_migration import _remove_tree
    from tinyassets.broker.deletion_jobs import STEPS, new_job

    previous = {job["id"]: job for job in records.records()}
    completed = 0
    for entry in facts.entries():
        key, owner = entry["digest"], entry["principal"]
        job = previous.get(key) or new_job(owner, None, key=key)
        job.update(origin="cutover", billing_cleanup=entry["receipt"].get("billing_cleanup", {}))
        job["completed_steps"] = []
        records.save(job)
        pending = entry["receipt"]["unfinished_phases"]
        try:
            # Cutover is stopped, so there can be no old runtime/stream to close.
            # The publication gate remains closed throughout this transaction.
            targets = {str(store / str(uid)) for uid in entry["uids"]}
            targets.update(str(store / target["quarantine"]) for target in entry["work"].values())
            for target in sorted(targets):
                _remove_tree(Path(target), store)
                if Path(target).exists():
                    raise RuntimeError("legacy path cleanup remains incomplete")
            job["results"]["legacy_paths"] = [hashlib.sha256(target.encode()).hexdigest()
                                               for target in sorted(targets)]
            # Copying a source ledger is allowed only as a one-shot input. Its
            # completed receipts are preserved, never converted into new retries.
            if pending:
                if not owner:
                    raise RuntimeError("legacy deletion lacks identity for pending cleanup")
                if "broker_egress" in pending:
                    from tinyassets.broker.account_erasure import ACCOUNT_SCOPE, local_erase
                    from tinyassets.broker.process import _Dispatchers

                    local_erase(_Dispatchers(shared, allow_test_fixtures=False).ledger_for(owner),
                                principal=owner, command_center=ACCOUNT_SCOPE)
                    _remove_tree(shared / ".broker/accounts" / key, shared)
                if "billing" in pending:
                    from tinyassets.billing.stripe_adapter import cancel_account_billing
                    from tinyassets.broker.owner_reads import Plans

                    def receipt(receipt_key, value):
                        job["billing_cleanup"][receipt_key] = value
                        records.save(job)

                    result = cancel_account_billing(shared, owner, plans=Plans(shared),
                                                    record_cleanup=receipt)
                    if result not in {"none", "cancelled", "attempt_expired"}:
                        raise RuntimeError("legacy billing cleanup remains pending")
                if "credentials" in pending:
                    # Cutover invalidates pre-cutover sessions/keys. Reset these
                    # shared bearer tables, never traverse an owner home.
                    from tinyassets.restore_recovery import RESET_TABLES, _quote, _read

                    for relative, tables in RESET_TABLES.items():
                        if relative.startswith(".broker/"):
                            continue
                        present = _read(shared, relative)
                        if not present:
                            continue
                        with closing(sqlite3.connect(shared / relative)) as db, db:
                            for table in tables:
                                if table in present:
                                    db.execute(f"DELETE FROM {_quote(table)}")
                if "identity" in pending:
                    from tinyassets.router_account_deletion import delete_workos_user

                    if delete_workos_user(owner) not in {"deleted", "not_applicable"}:
                        raise RuntimeError("legacy identity cleanup remains pending")
            job["results"]["legacy_receipt"] = {
                k: v for k, v in entry["receipt"].items()
                if k not in {"quarantines", "erasure_failures", "unfinished_phases"}}
            job["results"]["receipt"] = {"deleted": True, "completed_at": time.time()}
            job.update(completed_steps=list(STEPS), failed_step=None, retry_at=0)
            records.save(job)
            completed += 1
        except Exception as error:
            job.update(failed_step="legacy_cutover", retry_at=time.time())
            job["attempts"]["legacy_cutover"] = job["attempts"].get("legacy_cutover", 0) + 1
            records.save(job)
            raise RuntimeError("legacy deletion incomplete; cutover refused") from error
    return completed


def census_count(source):
    """Read-only count of legacy deletion records and pending path obligations."""
    from tinyassets.restore_recovery import _rows, _safe
    from tinyassets.universe_files import read_universe_file

    events = sorted(_rows(source, NAME, "events"), key=lambda row: (row["at"], row["id"]))
    facts = LegacyFacts(source)
    facts.events = [(r["id"], r["digest"], r["at"], r["payload"]) for r in events]
    paths = {target["quarantine"] for entry in facts.entries()
             for target in entry["work"].values()}
    for key in _legacy_main_digests(source):
        facts.execute("add", {"digest": key})
    for marker in (source / ".broker/accounts").glob("*/.deleted"):
        _safe(source, marker.relative_to(source))
        facts.execute("add", {"digest": marker.parent.name})
    for relative, table in ((".router-deleted-owners.sqlite3", "deleted_owners"),
                            (".broker/credential-custody.db", "deleted_principals"),
                            (".boxhost/boxhost.db", "owner_destroys"),
                            (".broker/state/owner-identities.db", "owner_box_revocations")):
        for row in _rows(source, relative, table):
            if table == "owner_box_revocations" and not row["deleted"]:
                continue
            owner = row.get("owner", row.get("owner_id", row.get("principal")))
            key = row["digest"] if table == "deleted_principals" else principal_digest(owner)
            receipt = json.loads(row.get("receipt") or "null") or {}
            facts.execute("add", {"digest": key, "principal": owner, "receipt": receipt})
            paths.update(receipt.get("quarantines", []))
    pending = {entry["digest"] for entry in facts.entries()
               if entry["receipt"]["unfinished_phases"] or entry["work"]}
    path = _safe(source, ".disaster-restore.json")
    try:
        report = json.loads(read_universe_file(path.parent, path.name,
                                              max_bytes=LEGACY_REPORT_MAX_BYTES))
    except FileNotFoundError:
        report = {}
    paths.update(target["quarantine"] for target in
                 report.get("pending_erasures", []) + report.get("erasure_failures", []))
    return {"pending_deletions": len(pending), "pending_paths": len(paths)}
