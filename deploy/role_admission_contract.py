"""DA7 admission-generation restart contract (owner-dynamic-admission).

Stdlib only: loaded by the stopped-volume startup coordinator before PID1
retires. The volume journal records the admission-log generation it last
reconciled and the centers held missing; at restart the coordinator accepts
exactly the principal-set change the broker log explains since then. This
replaces D216's "new principals remain a loud refusal" for completed phases.

Founder decision F1 = (b) (2026-10-06): a center the log admits whose tree is
missing with no deletion to explain it does not stop startup. It stays
unbound on ``missing``, raises a loud alarm and a concern record, and is
re-checked at every restart until restored or retired.

Root never opens the broker-private database: reads and appends go through a
retired broker child (D61, as role_volume_migration._allocate does).
"""

from __future__ import annotations

import errno
import json
import os
import re
import signal
import stat
import struct
import sys
import time

STAGING = ".role-admission"
CONCERNS = "admission-concerns"  # inside the root-private migration state directory
ACCESS, DEFAULT = "system.posix_acl_access", "system.posix_acl_default"
_CENTER = re.compile(r"[A-Za-z0-9_-]{1,128}")


class ContractRefused(RuntimeError):
    """A principal-set change the admission log does not explain."""


# Canonical migrated root label (role_owner_migration._permissions, root kind).
def canonical_root_acl(machine):
    undefined = 0xFFFFFFFF
    entries = [(1, 7, undefined), (2, 1, 1002), (2, 5, machine), (4, 0, undefined),
               (16, 5, undefined), (32, 0, undefined)]
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *e) for e in entries)


def canonical_label(machine):
    return (1001, machine, 0o750, canonical_root_acl(machine), None)


def _xattr(fd, name):
    try:
        return os.getxattr(fd, name)
    except OSError as exc:
        if exc.errno != errno.ENODATA:
            raise
        return None


def read_label(fd):
    info = os.fstat(fd)
    return (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode),
            _xattr(fd, ACCESS), _xattr(fd, DEFAULT))


def clear_staging(root):
    """Remove ``.role-admission/`` with writers stopped, before the inventory.

    Everything in it is an unpublished admission remnant (DA4 "before step 3").
    """
    try:
        info = os.stat(STAGING, dir_fd=root, follow_symlinks=False)
    except FileNotFoundError:
        return 0
    if not stat.S_ISDIR(info.st_mode):
        raise ContractRefused("admission staging is not a directory")
    return _remove(root, STAGING)


def _remove(parent, name):
    removed = 0
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                 dir_fd=parent)
    try:
        for entry in os.listdir(fd):
            info = os.stat(entry, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                removed += _remove(fd, entry)
            else:
                os.unlink(entry, dir_fd=fd)
                removed += 1
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent)
    os.fsync(parent)
    return removed + 1


def _rows(rows, after):
    ordered = sorted(rows, key=lambda row: row["generation"])
    for row in ordered:
        if (set(row) != {"generation", "event", "principal", "center", "machine"}
                or type(row["generation"]) is not int or row["generation"] <= after
                or row["event"] not in ("admit", "retire")
                or not isinstance(row["center"], str) or not _CENTER.fullmatch(row["center"])):
            raise ContractRefused("invalid admission log delta")
    return ordered


def expected(principals, missing, rows):
    """E: journal principals plus missing, then the log delta in order."""
    centers = {**missing, **principals}
    for row in rows:
        if row["event"] == "admit":
            if centers.get(row["center"], row["principal"]) != row["principal"]:
                raise ContractRefused(f"log admits a journaled center to another owner: "
                                      f"{row['center']}")
            centers[row["center"]] = row["principal"]
        else:
            centers.pop(row["center"], None)
    return centers


def reconcile(*, journal, discovered, rows, pending=(), adoptable=lambda center, owner: False):
    """The DA7 plan for one startup; refuses before any mutation.

    ``journal``: the volume journal (or None). ``discovered``: inventory
    center -> principal. ``rows``: every log row with a generation above the
    journal's (all rows when there is no stable forward journal). ``pending``:
    centers with a durable deletion intent. ``adoptable``: the caller's check
    that an unlogged root's label matches its owner's durable reservation and
    the center was never admitted (DA4 orphan); the broker's own append
    invariants refuse a retired or foreign name regardless.

    Returns ``principals`` (the journal's next map, every bindable tree),
    ``missing``, ``generation``, ``adopt`` and ``seed`` (admit rows to append
    through the retired broker child, never a retire) and ``alarms``.
    """
    pending = set(pending)
    after = journal.get("generation", 0) if journal else 0
    rows = _rows(rows, after if journal and _forward_stable(journal) else 0)
    high = max([after, *(row["generation"] for row in rows)])
    if journal and journal.get("state") != "stable":
        # Interrupted: exact configuration (D216 unchanged); writers were stopped.
        if journal["principals"] != discovered:
            raise ContractRefused("full migration authority changed")
        return dict(principals=dict(discovered), missing=dict(journal.get("missing", {})),
                    generation=after, adopt=[], seed=[], alarms=[])
    if not journal or not _forward_stable(journal):
        # First volume, or forward after a stable reverse: seed every inventoried
        # center lacking a row; retired or reassigned names refuse; never retire.
        state = {}
        for row in rows:
            state.setdefault(row["center"], {})[row["event"]] = row["principal"]
        seed = []
        for center, principal in sorted(discovered.items()):
            events = state.get(center, {})
            if "retire" in events and center not in pending:
                raise ContractRefused(f"a retired center has a tree: {center}")
            if events.get("admit", principal) != principal:
                raise ContractRefused(f"center owner differs from its log row: {center}")
            if "admit" not in events:
                seed.append((principal, center))
        return dict(principals=dict(discovered), missing={}, generation=high, adopt=[],
                    seed=seed, alarms=[])
    centers = expected(journal["principals"], journal.get("missing", {}), rows)
    adopt = []
    for center, principal in sorted(discovered.items()):
        if center in centers:
            if centers[center] != principal:
                raise ContractRefused(f"center owner changed: {center}")
        elif center in pending:
            continue  # D218 resume reruns pass one on its bound tree
        elif adoptable(center, principal):
            adopt.append((principal, center))
        else:
            raise ContractRefused(f"unexplained center tree: {center}")
    missing, alarms = {}, []
    for center, principal in sorted(centers.items()):
        if center in discovered or center in pending:
            continue
        missing[center] = principal  # F1 (b): start everyone else
        alarms.append(dict(center=center, principal=principal))
    return dict(principals=dict(discovered), missing=missing, generation=high, adopt=adopt,
                seed=[], alarms=alarms)


def _forward_stable(journal):
    return journal.get("state") == "stable" and journal.get("direction") == "forward"


def journal_fields(plan, appended):
    """``volume.json`` fields after the adopt/seed rows were appended (DA7)."""
    generation = max([plan["generation"], *(row["generation"] for row in appended)])
    return dict(principals=plan["principals"], missing=plan["missing"], generation=generation)


def phase_explained(recorded, previous, current):
    """A completed phase journal's bindings may lag one reconciled generation.

    Accept exactly the configuration the previous volume journal admitted, or
    the newly reconciled one; anything else is still D216's refusal.
    """
    return recorded == current or (previous is not None and recorded == previous)


def raise_alarms(state, alarms, *, now=None):
    """F1 (b): a loud alarm on stderr and one concern record per missing center.

    Re-raised at every restart while the center stays on ``missing``. The
    record uses the docs/concerns shape so the host can file it verbatim.
    """
    if not alarms:
        return []
    stamp = time.strftime("%Y-%m-%d", time.gmtime(now))
    try:
        os.mkdir(CONCERNS, 0o700, dir_fd=state)
    except FileExistsError:
        pass
    concerns = os.open(CONCERNS, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                       dir_fd=state)
    written = []
    try:
        for alarm in alarms:
            center, principal = alarm["center"], alarm["principal"]
            sys.stderr.write(f"ROLE ADMISSION ALARM: command center {center} is admitted "
                             f"but its tree is missing with no deletion to explain it; "
                             f"it stays unbound and every other owner starts (F1 b)\n")
            name = f"{stamp}-missing-center-{center}.md"
            body = (f"# Admitted command center {center} is missing\n\n"
                    f"Startup found no tree for `{center}` (principal `{principal}`). The "
                    f"admission log admits it, no `retire` row or deletion intent explains "
                    f"the loss, so it is held on `volume.json` `missing`, unbound, and its "
                    f"requests refuse. Every other owner started.\n\n"
                    f"Resolve: restore the tree (the next restart binds it if its label and "
                    f"row still match), or delete the account/center so D218 writes its "
                    f"`retire` row. Re-checked at every restart.\n")
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
                         | os.O_CLOEXEC, 0o600, dir_fd=concerns)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            written.append(name)
        os.fsync(concerns)
    finally:
        os.close(concerns)
    sys.stderr.flush()
    return written


_BROKER_LOG = (
    "import sys,json; from pathlib import Path; sys.path.insert(0,'/app'); "
    "from tinyassets.broker.owner_identities import OwnerIdentities; "
    "db=OwnerIdentities(Path(sys.argv[1])/'.broker/state/owner-identities.db'); "
    "req=json.loads(sys.argv[2]); "
    "[db.admission('admit',p,c) for p,c in req['append']]; "
    "rows=[r.__dict__ for r in db.admissions_after(req['after'])]; "
    "sys.stdout.write(json.dumps(rows))"
)


def broker_log(data_root, launch, *, after=0, append=(), timeout=60):
    """Append admit rows, then read the delta, as a fully retired broker child.

    Appends are idempotent (DA1): an identical row is returned, a conflicting
    one refuses the whole startup. Returns the rows above ``after``.
    """
    reader, writer = os.pipe()
    child = os.fork()
    if child == 0:
        try:
            os.close(reader)
            os.dup2(writer, 1)
            launch["close_descriptors"]({0, 1, 2})
            launch["retire_child"]("broker")
            request = json.dumps({"after": after, "append": [list(p) for p in append]})
            os.execve("/opt/venv/bin/python",
                      ["python", "-I", "-B", "-c", _BROKER_LOG, str(data_root), request],
                      {"PATH": "/opt/venv/bin:/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        except BaseException:
            os._exit(126)
    os.close(writer)
    output = b""
    deadline = time.monotonic() + timeout
    try:
        while True:
            chunk = os.read(reader, 65536)
            if not chunk:
                break
            output += chunk
            if time.monotonic() >= deadline or len(output) > 64 * 1024 * 1024:
                raise ContractRefused("retired broker log read exceeded its bound")
    finally:
        os.close(reader)
        if time.monotonic() >= deadline:
            os.kill(child, signal.SIGKILL)
        _, status = os.waitpid(child, 0)
    if status:
        raise ContractRefused("retired broker admission log access failed")
    return _rows(json.loads(output), after)
