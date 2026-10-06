"""Switched role-split container startup and healthcheck (default OFF).

Installed root-owned 0555 at /usr/local/libexec/ta-role-start.py. The image
ENTRYPOINT, CMD and the compose healthcheck are unchanged, so a default deploy
never reaches this file. Only the deploy/compose.role-split.yml overlay selects
it, and every mode also refuses unless TINYASSETS_ROLE_SPLIT is exactly "1".

    start    container PID1, root with the seven entry capabilities: forward
             migration under the layout lock, then D60's bootstrap (broker and
             mapper forked, PID1 retires to the daemon), then the server.
    reverse  D10's startup reverse migration in the same privileged window;
             exits before any service, so forward cannot undo it.
    health   the healthcheck exec: retire to 1001 with zero capability and
             NNP, then exec the unchanged ``ta-op pulse``.

Stdlib only until PID1 has retired; the application is imported after that.
"""
from __future__ import annotations

import os
import runpy
import stat
import sys
from pathlib import Path

SWITCH = "TINYASSETS_ROLE_SPLIT"
LIBEXEC = Path("/usr/local/libexec")
DATA_ROOT = Path("/data")
RUN_ROOT = Path("/run/tinyassets-roles")
MODES = Path("/app/tinyassets/role_modes.py")
REFUSE = 78


def enabled(environ=None):
    """Exactly "1" selects the split; unset, empty or anything else is OFF."""
    return (os.environ if environ is None else environ).get(SWITCH) == "1"


def _trusted(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise RuntimeError(f"untrusted startup path: {item}")
    return str(path)


def _load(name):
    return runpy.run_path(_trusted(LIBEXEC / f"ta-{name}.py"))


def _helpers(launch):
    return dict(owner=_load("owner-migration"), egress=_load("egress-migration"),
                metadata=_load("metadata-migration"), inventory=_load("volume-inventory"),
                modes=runpy.run_path(_trusted(MODES)), launch=launch)


def _entry(launch):
    if os.getresuid() != (0, 0, 0) or os.getresgid() != (0, 0, 0):
        raise RuntimeError("role startup requires the root entry identity")
    launch["_assert_caps"](launch["ENTRY_CAPS"])
    launch["verify_chain"]()


def reverse(launch):
    _entry(launch)
    helpers = _helpers(launch)
    report = _load("volume-migration")["migrate"](DATA_ROOT, reverse=True, **helpers)
    print(f"role reverse migration complete: {report['direction']}", flush=True)
    return 0


def bindings(launch, helpers):
    """Startup admissions {(principal, center): machine} from the stable inventory."""
    facts = helpers["inventory"]["inventory"](
        DATA_ROOT, owner=helpers["owner"], egress=helpers["egress"])
    if facts["unallocated"]:
        raise RuntimeError("forward migration left unallocated owners")
    return {(facts["principals"][center], center): machine
            for center, machine in facts["bindings"].items()}


def _run_root():
    """Root-owned /run parents and the setgid broker socket directory D6 needs."""
    for path in (RUN_ROOT.parent, RUN_ROOT):
        if not path.exists():
            os.mkdir(path, 0o755)
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022):
            raise RuntimeError(f"unsafe startup run root: {path}")
    broker = RUN_ROOT / "broker"
    if not broker.exists():
        os.mkdir(broker, 0o700)
    fd = os.open(broker, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchown(fd, 1002, 1101)
        # Without FSETID the kernel clears S_ISGID unless the caller is in the
        # group (D17); SETGID is already an entry capability.
        os.setegid(1101)
        try:
            os.fchmod(fd, 0o2750)
        finally:
            os.setegid(0)
        info = os.fstat(fd)
        if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (1002, 1101, 0o2750):
            raise RuntimeError("broker socket directory readback failed")
    finally:
        os.close(fd)


def start(launch):
    if os.getpid() != 1:
        raise RuntimeError("role startup must be container PID1")
    _entry(launch)
    helpers = _helpers(launch)
    _load("volume-migration")["migrate"](DATA_ROOT, **helpers)
    admitted = bindings(launch, helpers)
    _run_root()
    # Returns in PID1 as the capability-free daemon, or exits the container 78.
    _load("owner-launch")["bootstrap_services"](DATA_ROOT, RUN_ROOT, admitted, launch)
    os.environ["TINYASSETS_DATA_DIR"] = str(DATA_ROOT)
    os.environ["TINYASSETS_CREDENTIAL_BROKER"] = "process"
    from tinyassets.sqlite_floor import require_sqlite_floor

    require_sqlite_floor()
    from tinyassets.universe_server import main as serve

    serve()
    return 0


def health(launch):
    launch["_retire_identity"](1001, ())
    launch["close_descriptors"]()
    os.execve(str(LIBEXEC / "ta-op"), ["ta-op", "pulse"], dict(os.environ))


def main(argv=None, environ=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in {"start", "reverse", "health"}:
        print("usage: ta-role-start.py start|reverse|health", file=sys.stderr)
        return REFUSE
    if not enabled(environ):
        print(f"role split startup is OFF ({SWITCH} is not 1); refusing", file=sys.stderr)
        return REFUSE
    # The chain checker (run again inside bootstrap) reads sys.argv[1:] as its
    # artifact list, and the server takes no arguments: drop the mode here.
    sys.argv = sys.argv[:1]
    launch = _load("launch")
    try:
        return {"start": start, "reverse": reverse, "health": health}[argv[0]](launch)
    except Exception as exc:
        # Before PID1 retires, a caught failure must never keep host authority.
        print(f"role startup {argv[0]} refused: {exc}", file=sys.stderr, flush=True)
        os._exit(REFUSE)


if __name__ == "__main__":
    sys.exit(main())
