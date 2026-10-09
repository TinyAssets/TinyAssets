"""Container PID1: the owner-split startup, then the daemon (stdlib until retired).

Installed root-owned 0555 at /usr/local/libexec/ta-launch.py and run as the
image CMD under ``compose.yml``'s ``user: "0:0"`` with only the bootstrap's
capabilities. In order:

1. refuse unless ``/data/.layout.json`` carries ``"split": "owner-split"``,
   which only ``deploy/role_migrate.py`` writes (nothing here migrates);
2. read the startup admissions from the broker's append-only log through a
   child fully retired to the broker identity;
3. D60's bootstrap: fork the broker and the bounded mapper, then retire PID1
   to the capability-free daemon;
4. import the application and serve.

Any failure before retirement exits 78 without serving.
"""
from __future__ import annotations

import ctypes
import json
import os
import runpy
import stat
import sys
from pathlib import Path

LIBEXEC = Path("/usr/local/libexec")
DATA_ROOT = Path("/data")
RUN_ROOT = Path("/run/tinyassets-roles")
SPLIT = "owner-split"
REFUSE = 78
# The D70 bootstrap's whole set: KILL, SETGID, SETUID, SETPCAP. The migration's
# CHOWN, FOWNER and DAC_OVERRIDE are never held by the service image.
ENTRY_CAPS = sum(1 << cap for cap in (5, 6, 7, 8))
CAP_FIELDS = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
ROLE_IDS = {"daemon": (1001, (1100, 1101, 1102)), "broker": (1002, (1102,))}


class Refused(RuntimeError):
    """A startup precondition failed; never fall back to an unsplit service."""


class _Header(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _Data(ctypes.Structure):
    _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32),
                ("inheritable", ctypes.c_uint32)]


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def _checked(result, operation):
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, f"{operation}: {os.strerror(code)}")


def status():
    return dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())


def _assert_caps(mask):
    fields = status()
    expected = {"CapInh": 0, "CapAmb": 0, "CapEff": mask,
                "CapPrm": mask, "CapBnd": mask}
    if any(int(fields[key], 16) != value for key, value in expected.items()):
        raise Refused("unexpected capability sets")
    if int(fields["NoNewPrivs"]) != 1:
        raise Refused("no-new-privileges is absent")


def _capset(mask):
    data = (_Data * 2)()
    for index in range(2):
        data[index].effective = data[index].permitted = (mask >> (32 * index)) & 0xffffffff
    _checked(_libc().capset(ctypes.byref(_Header(0x20080522, 0)), data), "capset")


def retire_child(role):
    """Called in a single-threaded fork child, before any application import."""
    uid, groups = ROLE_IDS[role]
    _retire_identity(uid, groups)


def _retire_identity(uid, groups):
    libc = _libc()
    _checked(libc.prctl(38, 1, 0, 0, 0), "no-new-privileges")
    _checked(libc.prctl(8, 0, 0, 0, 0), "clear keepcaps")
    _checked(libc.prctl(47, 4, 0, 0, 0), "clear ambient")
    for cap in range(int(Path("/proc/sys/kernel/cap_last_cap").read_text()) + 1):
        _checked(libc.prctl(24, cap, 0, 0, 0), "drop child bounding capability")
    os.setgroups(groups)
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)
    _capset(0)
    fields = status()
    if any([int(value) for value in fields[key].split()] != [uid] * 4
           for key in ("Uid", "Gid")) or os.getgroups() != list(groups):
        raise Refused("child identity readback failed")
    _assert_caps(0)
    os.umask(0o007)


def close_descriptors(keep=()):
    """No directory, listener or namespace handle leaks through role exec."""
    retained = {0, 1, 2, *keep}
    for name in os.listdir("/proc/self/fd"):
        fd = int(name)
        if fd not in retained:
            try:
                os.close(fd)
            except OSError as exc:
                if exc.errno != 9:  # the descriptor used by listdir has closed
                    raise


def broker_environment(data_root):
    # This exact static set is checked against CHILD_FORBIDDEN_ENV in tests.
    # Never import application code into the privileged parent to filter env.
    result = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
              "HOME": "/var/lib/ta-broker", "PYTHONDONTWRITEBYTECODE": "1",
              "TINYASSETS_DATA_DIR": str(data_root)}
    if "TZ" in os.environ:
        result["TZ"] = os.environ["TZ"]
    http_flag = "TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED"
    if http_flag in os.environ:
        # Preserve the deployment's existing opt-in, never arbitrary values.
        result[http_flag] = "1" if os.environ[http_flag].strip().lower() in (
            "1", "true", "yes", "on") else "0"
    return result


def verify_chain():
    # Verify the checker before executing it; its immutable parent/install are
    # also checked by the image gate. No site or application import runs here.
    checker = Path("/usr/local/libexec/ta-chain.py")
    for path in (checker, *checker.parents):
        info = path.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise Refused(f"unsafe chain checker: {path}")
    runpy.run_path(str(checker))["main"]()


def _trusted(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise Refused(f"untrusted startup path: {item}")
    return str(path)


def _load(name):
    return runpy.run_path(_trusted(LIBEXEC / f"ta-{name}.py"))


def require_split(data_root=DATA_ROOT):
    """Fact 8: a volume the migration has not finished is never served."""
    try:
        document = json.loads((Path(data_root) / ".layout.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"no readable layout marker: {exc}") from None
    if not isinstance(document, dict) or document.get("split") != SPLIT:
        raise Refused("the data volume is not migrated to owner-split; run "
                      "deploy/role_migrate.py (docs/ops/owner-split-cutover-runbook.md)")
    return document


def admissions(rows, data_root=DATA_ROOT):
    """{(principal, center): machine} for every admitted, unretired center with a
    tree, and the log's high-water generation (the mapper binds rows above it).

    An admitted center whose tree is missing stays unbound and alarms; every
    other owner starts (F1 b).
    """
    admitted, generation = {}, 0
    for row in rows:
        generation = max(generation, row["generation"])
        if row["event"] == "admit":
            admitted[row["center"]] = (row["principal"], row["machine"])
        else:
            admitted.pop(row["center"], None)
    bindings = {}
    for center, (principal, machine) in sorted(admitted.items()):
        try:
            info = (Path(data_root) / center).lstat()
        except FileNotFoundError:
            sys.stderr.write(f"ALARM: admitted command center {center} has no tree; "
                             "it stays unbound\n")
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise Refused(f"admitted center root is not a directory: {center}")
        bindings[(principal, center)] = machine
    return bindings, generation


def boot(launch):
    """Returns in PID1 as the daemon with (bindings, supervisor, client)."""
    if os.getpid() != 1:
        raise Refused("startup must be container PID1")
    if os.getresuid() != (0, 0, 0) or os.getresgid() != (0, 0, 0):
        raise Refused("startup requires the root entry identity")
    _assert_caps(ENTRY_CAPS)
    verify_chain()
    require_split()
    contract = _load("admission-contract")
    bindings, generation = admissions(contract["broker_log"](DATA_ROOT, launch), DATA_ROOT)
    services = _load("owner-launch")["bootstrap_services"](
        DATA_ROOT, RUN_ROOT, bindings, launch, generation=generation)
    return bindings, services


def main():
    # The chain checker reads sys.argv[1:] as its artifact list: keep it empty.
    sys.argv = sys.argv[:1]
    try:
        boot(globals())  # the launch namespace the bootstrap module calls into
    except Exception as exc:
        # Before PID1 retires, a caught failure must never keep host authority.
        print(f"startup refused: {exc}", file=sys.stderr, flush=True)
        os._exit(REFUSE)
    os.environ["TINYASSETS_DATA_DIR"] = str(DATA_ROOT)
    from tinyassets.sqlite_floor import require_sqlite_floor

    require_sqlite_floor()
    from tinyassets.universe_server import main as serve

    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
