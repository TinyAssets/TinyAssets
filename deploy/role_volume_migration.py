"""Stopped-volume role migration coordinator; never starts normal service.

The verified startup caller supplies immutable helper namespaces and stops all
writers before entry. Forward and reverse hold one layout lock throughout.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import stat
import time


def _allocate(data_root, principals, launch, owner, root, contract):
    """Only a fully retired broker child imports the application allocator."""
    with owner["_directory"](root, ".broker") as broker:
        if owner["_stat"](broker, "state") is None:
            os.mkdir("state", 0o700, dir_fd=broker)
            os.fsync(broker)
        with owner["_directory"](broker, "state") as state:
            info = os.fstat(state)
            if info.st_uid not in {0, 1002}:
                raise owner["MigrationRefused"]("unexpected identity state owner")
            os.fchown(state, 1002, 1101)
            os.fchmod(state, 0o700)
            os.fsync(state)
    child = os.fork()
    if child == 0:
        try:
            launch["close_descriptors"]({0, 1, 2})
            launch["retire_child"]("broker")
            code = (
                "import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[3]); "
                "from tinyassets.broker.owner_identities import OwnerIdentities; "
                "db=OwnerIdentities(Path(sys.argv[1])/'.broker/state/owner-identities.db',"
                "initialize=True); "
                "[db.resolve(p,allocate=True) for p in json.loads(sys.argv[2])]"
            )
            os.execve(contract["PYTHON"], ["python", "-I", "-B", "-c", code, str(data_root),
                                           json.dumps(principals), contract["APP"]],
                      {"PATH": "/opt/venv/bin:/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        except BaseException:
            os._exit(126)
    deadline = time.monotonic() + 60
    while True:
        waited, status = os.waitpid(child, os.WNOHANG)
        if waited:
            if status:
                raise owner["MigrationRefused"]("retired broker identity allocation failed")
            return
        if time.monotonic() >= deadline:
            os.kill(child, signal.SIGKILL)
            os.waitpid(child, 0)
            raise owner["MigrationRefused"]("retired broker identity allocation timed out")
        time.sleep(0.01)



def _accounting_preflight(root, owner, egress, reverse):
    """Check both physical stores before relocation or any other mutation."""
    from contextlib import ExitStack

    with ExitStack() as stack:
        broker = None
        if owner["_stat"](root, ".broker") is not None:
            broker = stack.enter_context(owner["_directory"](root, ".broker"))
        old = egress["_database_entries"](root, "outbound.db")
        new = egress["_database_entries"](broker, "outbound.db") if broker is not None else {}
        if old["outbound.db"] is not None and new.get("outbound.db") is not None:
            raise owner["MigrationRefused"]("conflicting ledger locations")
        ledger = broker if new.get("outbound.db") is not None else root
        daemon_db = stack.enter_context(egress["_database_snapshot"](root, ".tinyassets.db"))
        ledger_db = stack.enter_context(egress["_database_snapshot"](ledger, "outbound.db"))
        daemon_facts = egress["_accounting_facts"](daemon_db)
        broker_facts = egress["_accounting_facts"](ledger_db)
        layout = owner["_read"](root, ".layout.json")
        progress = layout.get("roles", {}).get("accounting", {})
        if progress.get("state") == "migrating":
            manifest = progress["manifest"]
            if (daemon_facts not in ({}, manifest) or broker_facts not in ({}, manifest)
                    or manifest and not daemon_facts and not broker_facts):
                raise owner["MigrationRefused"]("accounting copies diverged before full migration")
        elif daemon_facts and broker_facts:
            raise owner["MigrationRefused"]("conflicting accounting destinations")
        return {"daemon": daemon_facts, "broker": broker_facts, "reverse": reverse}

DELETION_INTENTS = ".role-owner-delete"  # tinyassets.role_owner_tree_deletion.INTENT_DIR


def _deletion_intents(root, owner):
    """Centers with a durable two-pass deletion intent; dot names are temporaries."""
    info = owner["_stat"](root, DELETION_INTENTS)
    if info is None:
        return set()
    if not stat.S_ISDIR(info.st_mode):
        raise owner["MigrationRefused"]("deletion intent store is not a directory")
    with owner["_directory"](root, DELETION_INTENTS) as intents:
        names = [name for name in os.listdir(intents) if not name.startswith(".")]
    if any(not name.endswith(".json") for name in names):
        raise owner["MigrationRefused"]("unexpected entry in the deletion intent store")
    return {name[:-5] for name in names}


def _identity_database(root, owner):
    """Whether the broker's identity map, and so its admission log, exists yet."""
    if owner["_stat"](root, ".broker") is None:
        return False
    with owner["_directory"](root, ".broker") as broker:
        if owner["_stat"](broker, "state") is None:
            return False
        with owner["_directory"](broker, "state") as state:
            return owner["_stat"](state, "owner-identities.db") is not None


def _admission_rows(data_root, root, journal, contract, launch, owner):
    """The log rows the contract reconciles, read through a retired broker child.

    Only the delta above a stable forward journal's generation; every row
    otherwise (first volume, forward after reverse). Also returns the names the
    log and journal admit, which D64 discovery must include (DA7).
    """
    forward = (journal is not None and journal["state"] == "stable"
               and journal["direction"] == "forward")
    after = journal.get("generation", 0) if forward else 0
    try:
        rows = (contract["broker_log"](data_root, launch, after=after)
                if _identity_database(root, owner) else [])
    except contract["ContractRefused"] as exc:
        raise owner["MigrationRefused"](str(exc)) from exc
    admitted = set(journal.get("principals", {}) if journal else ())
    admitted |= set(journal.get("missing", {}) if journal else ())
    for row in rows:
        (admitted.add if row["event"] == "admit" else admitted.discard)(row["center"])
    return rows, admitted


def _admission_plan(root, journal, facts, rows, pending, contract, owner):
    """DA7: the principal-set change the admission log explains, before any mutation.

    Adds ``admits`` (rows to append; an interrupted journal carries its own) and
    ``previous`` (the last stable principals, which explain a completed phase
    journal that lags this reconciliation) to the contract's plan.
    """
    logged = {row["center"] for row in rows}

    def adoptable(center, principal):
        # DA4 orphan: published under its owner's canonical label and never
        # logged; the broker's own append refuses a retired or foreign name.
        machine = facts["bindings"].get(center)
        if machine is None or center in logged:
            return False
        with owner["_directory"](root, center) as directory:
            return contract["read_label"](directory) == contract["canonical_label"](machine)

    try:
        plan = contract["reconcile"](journal=journal, discovered=facts["principals"],
                                     rows=rows, pending=pending, adoptable=adoptable)
    except contract["ContractRefused"] as exc:
        raise owner["MigrationRefused"](str(exc)) from exc
    if journal and journal["state"] != "stable":
        plan["admits"] = [tuple(pair) for pair in journal.get("admits", [])]
        plan["previous"] = journal.get("previous")
    else:
        plan["admits"] = plan["adopt"] + plan["seed"]
        plan["previous"] = journal["principals"] if journal else None
    return plan


def migrate(data_root, *, owner, egress, metadata, inventory, modes, launch, contract,
            reverse=False, dry_run=False, after_step=None):
    refused = owner["MigrationRefused"]
    if os.geteuid() != 0:
        raise refused("full migration requires the pre-drop window")
    direction = "reverse" if reverse else "forward"

    def checkpoint(step):
        if after_step:
            after_step(step)

    with owner["_root"](data_root) as root:
        lock = egress["_lock_descriptor"](root, None)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            pending = _deletion_intents(root, owner)
            if reverse and pending:
                # A partly deleted owner tree must not reach the old image;
                # forward stays allowed so the daemon can resume the deletion.
                raise refused("finish pending owner deletion before reversing")
            swept = 0
            if not dry_run:
                try:
                    # DA4: unpublished admission remnants, with writers stopped.
                    swept = contract["clear_staging"](root)
                except contract["ContractRefused"] as exc:
                    raise refused(str(exc)) from exc
            journal = None
            if owner["_stat"](root, owner["STATE"]) is not None:
                with owner["_directory"](root, owner["STATE"]) as state:
                    if owner["_stat"](state, "volume.json") is not None:
                        journal = owner["_read"](state, "volume.json", private=True)
            if journal and journal["state"] != "stable" and journal["direction"] != direction:
                raise refused("finish interrupted full migration before reversing")
            rows, admitted = _admission_rows(data_root, root, journal, contract, launch, owner)
            facts = inventory["inventory"](data_root, owner=owner, egress=egress,
                                           admitted=admitted)
            work = inventory["classify"](root, facts["principals"], owner)
            # DA7 replaces D216's exact principal set: accept exactly the change
            # the admission log explains; an interrupted journal stays exact.
            plan = _admission_plan(root, journal, facts, rows, pending, contract, owner)
            if reverse and facts["unallocated"]:
                raise refused("reverse cannot allocate missing owner identities")
            bindings = facts["bindings"]
            if facts["unallocated"]:
                # Planning labels are never persisted or returned as reservations.
                available = iter(n for n in range(300001, 400000) if n not in bindings.values())
                planned = {p: next(available) for p in facts["unallocated"]}
                bindings = {c: bindings.get(c, planned.get(p))
                            for c, p in facts["principals"].items()}
            # Reservations are permanent, so a retired center's owner still maps.
            previous = None if plan["previous"] is None else {
                center: facts["reservations"].get(principal)
                for center, principal in plan["previous"].items()}
            common = dict(bindings=bindings, work=work, reverse=reverse, layout_lock=lock,
                          reconcile_work=True, previous_bindings=previous,
                          explained=contract["phase_explained"])
            # Validate every owner inode and protected/shared entry before the
            # first mutation. Other phases preserve their own crash manifests.
            owner_plan = owner["migrate"](data_root, dry_run=True, **common)
            metadata_plan = metadata["migrate"](
                data_root, owner=owner, modes=modes, dry_run=True, **common)
            accounting_plan = _accounting_preflight(root, owner, egress, reverse)
            liveness_plan = egress["migrate_liveness"](
                data_root, modes=modes, reverse=reverse, dry_run=True, layout_lock=lock)
            report = dict(direction=direction, owners=owner_plan, metadata=metadata_plan,
                          liveness=liveness_plan, accounting=accounting_plan,
                          unallocated=facts["unallocated"], generation=plan["generation"],
                          missing=plan["missing"], alarms=plan["alarms"],
                          admits=[list(pair) for pair in plan["admits"]], swept=swept,
                          principals=facts["principals"], bindings=facts["bindings"])
            if dry_run:
                return report
            owner["_mkdirs"](root, owner["STATE"])
            with owner["_directory"](root, owner["STATE"]) as state:
                fields = contract["journal_fields"](plan, [])
                if (journal != dict(direction=direction, state="stable", **fields)
                        or plan["admits"]):
                    owner["_write"](state, "volume.json", dict(
                        direction=direction, state="migrating", **fields,
                        admits=[list(pair) for pair in plan["admits"]],
                        previous=plan["previous"]))
                    checkpoint("volume-journal")
                layout = owner["_read"](root, ".layout.json")
                accounting = layout.get("roles", {}).get("accounting", {})
                if reverse or accounting.get("state") == "migrating":
                    egress["transfer_accounting"](
                        data_root, reverse=reverse, layout_lock=lock, after_step=after_step)
                egress["relocate"](
                    data_root, reverse=reverse, layout_lock=lock, after_step=after_step)
                if not reverse:
                    egress["transfer_accounting"](
                        data_root, layout_lock=lock, after_step=after_step)
                checkpoint("volume-egress")
                if facts["unallocated"]:
                    _allocate(data_root, facts["unallocated"], launch, owner, root, contract)
                    facts = inventory["inventory"](data_root, owner=owner, egress=egress,
                                                   admitted=admitted)
                    if facts["unallocated"]:
                        raise refused("broker did not reserve every owner identity")
                checkpoint("volume-reservations")
                # Reverse appends nothing: the old image never reads the log and
                # the next forward seeds whatever it lacks. Appends are idempotent.
                appended = [] if reverse or not plan["admits"] else contract["broker_log"](
                    data_root, launch, after=plan["generation"], append=plan["admits"])
                checkpoint("volume-admissions")
                contract["raise_alarms"](state, plan["alarms"])
                fields = contract["journal_fields"](plan, appended)
                report.update(generation=fields["generation"], missing=fields["missing"],
                              unallocated=facts["unallocated"],
                              principals=facts["principals"], bindings=facts["bindings"])
                egress["migrate_liveness"](data_root, modes=modes, reverse=reverse,
                                           layout_lock=lock, after_step=after_step)
                if not reverse:
                    for center in facts["bindings"]:
                        with owner["_directory"](root, center) as directory:
                            if owner["_stat"](directory, "previews") is None:
                                os.mkdir("previews", 0o700, dir_fd=directory)
                                with owner["_directory"](directory, "previews") as preview:
                                    os.fchown(preview, 1001, 1001)
                                    os.fsync(preview)
                                os.fsync(directory)
                    work = inventory["classify"](root, facts["principals"], owner)
                    common["work"] = work
                common["bindings"] = facts["bindings"]
                report["metadata"] = metadata["migrate"](
                    data_root, owner=owner, modes=modes, after_step=after_step, **common)
                report["owners"] = owner["migrate"](data_root, after_step=after_step, **common)
                checkpoint("volume-owners")
                complete = dict(direction=direction, state="stable", **fields)
                if journal != complete:
                    owner["_write"](state, "volume.json", complete)
                    checkpoint("volume-complete")
                layout = owner["_read"](root, ".layout.json")
                if layout["state"] != "stable" or layout["roles"]["state"] != "stable":
                    layout["state"] = layout["roles"]["state"] = "stable"
                    owner["_write"](root, ".layout.json", layout, uid=1001)
                    checkpoint("volume-admit")
                return report
        finally:
            os.close(lock)
