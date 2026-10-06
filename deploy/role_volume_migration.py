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


def _allocate(data_root, principals, launch, owner, root):
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
                "import sys,json; from pathlib import Path; sys.path.insert(0,'/app'); "
                "from tinyassets.broker.owner_identities import OwnerIdentities; "
                "db=OwnerIdentities(Path(sys.argv[1])/'.broker/state/owner-identities.db',"
                "initialize=True); "
                "[db.resolve(p,allocate=True) for p in json.loads(sys.argv[2])]"
            )
            os.execve("/opt/venv/bin/python", ["python", "-I", "-B", "-c", code,
                                              str(data_root), json.dumps(principals)],
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


def _deletion_pending(root, owner):
    """Any durable two-pass deletion intent; dot names are unreplaced temporaries."""
    info = owner["_stat"](root, DELETION_INTENTS)
    if info is None:
        return False
    if not stat.S_ISDIR(info.st_mode):
        raise owner["MigrationRefused"]("deletion intent store is not a directory")
    with owner["_directory"](root, DELETION_INTENTS) as intents:
        return any(not name.startswith(".") for name in os.listdir(intents))


def migrate(data_root, *, owner, egress, metadata, inventory, modes, launch,
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
            if reverse and _deletion_pending(root, owner):
                # A partly deleted owner tree must not reach the old image;
                # forward stays allowed so the daemon can resume the deletion.
                raise refused("finish pending owner deletion before reversing")
            facts = inventory["inventory"](data_root, owner=owner, egress=egress)
            work = inventory["classify"](root, facts["principals"], owner)
            journal = None
            if owner["_stat"](root, owner["STATE"]) is not None:
                with owner["_directory"](root, owner["STATE"]) as state:
                    if owner["_stat"](state, "volume.json") is not None:
                        journal = owner["_read"](state, "volume.json", private=True)
            if journal and journal["state"] != "stable" and journal["direction"] != direction:
                raise refused("finish interrupted full migration before reversing")
            if journal and journal["principals"] != facts["principals"]:
                raise refused("full migration authority changed")
            if reverse and facts["unallocated"]:
                raise refused("reverse cannot allocate missing owner identities")
            bindings = facts["bindings"]
            if facts["unallocated"]:
                # Planning labels are never persisted or returned as reservations.
                available = iter(n for n in range(300001, 400000) if n not in bindings.values())
                planned = {p: next(available) for p in facts["unallocated"]}
                bindings = {c: bindings.get(c, planned.get(p))
                            for c, p in facts["principals"].items()}
            common = dict(bindings=bindings, work=work, reverse=reverse, layout_lock=lock,
                          reconcile_work=True)
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
                          unallocated=facts["unallocated"])
            if dry_run:
                return report
            owner["_mkdirs"](root, owner["STATE"])
            with owner["_directory"](root, owner["STATE"]) as state:
                current = dict(direction=direction, state="migrating",
                               principals=facts["principals"])
                if journal != {**current, "state": "stable"}:
                    owner["_write"](state, "volume.json", current)
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
                    _allocate(data_root, facts["unallocated"], launch, owner, root)
                    facts = inventory["inventory"](data_root, owner=owner, egress=egress)
                    if facts["unallocated"]:
                        raise refused("broker did not reserve every owner identity")
                checkpoint("volume-reservations")
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
                complete = {**current, "state": "stable"}
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
