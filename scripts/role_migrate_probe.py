"""Root oracle for deploy/role_migrate.py: kill at every step, rerun, compare.

    python scripts/role_migrate_probe.py            # from the dev box (Docker)
    python scripts/role_migrate_probe.py --inside /data --app /src   # in the container

From the dev box it runs itself in the Linux oracle image as root with exactly
the migration's capabilities (CHOWN, FOWNER, DAC_OVERRIDE), no network, and a
named Docker volume at /data, which is ext4 like production. Inside it:

1. builds a production-shaped fixture, migrates it once and takes the manifest
   (path, type, uid, gid, mode, ACLs, sha256);
2. a converged rerun changes nothing and ``--check`` reports zero diffs;
3. for every mutating operation N of a full run (each chown, chmod, xattr,
   rename, mkdir, unlink, write-open, and each SQLite statement), rebuilds the
   fixture, kills the migration at N (``os._exit``, no cleanup), reruns it and
   requires the reference manifest byte for byte;
4. every precondition refuses with exit 2 and leaves the volume unchanged;
5. in a second container as full root (like the host), the runbook's snapshot
   tar and restore lines round-trip the migrated volume to the same manifest.

No fault seam ships in the migration: the kill comes from a Python audit hook
and a SQLite trace callback installed by this probe's child wrapper.
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import socket
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

VOLUME, OUT_VOLUME, OUT = "ta-migrate-probe", "ta-migrate-probe-out", "/out"
CAPS = ("CHOWN", "FOWNER", "DAC_OVERRIDE")
SNAPSHOT = ["--snapshot", "probe-snapshot", "--snapshot-bytes", "1"]

# Runs the migration in a child; argv[1] is the kill point (0 = never, -1 = count).
_CHILD = r"""
import os, runpy, sys
kill = int(sys.argv[1]); script = sys.argv[2]
seen = [0]
WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
MUTATING = {"os.chown", "os.chmod", "os.setxattr", "os.removexattr", "os.rename",
            "os.mkdir", "os.remove", "os.rmdir", "os.link", "os.symlink", "os.truncate"}
def step():
    seen[0] += 1
    if seen[0] == kill:
        os._exit(137)
def statement(sql):
    step()
def hook(event, args):
    if event in MUTATING:
        step()
    elif event == "open" and args[0] is not None and (
            (isinstance(args[1], str) and any(c in args[1] for c in "wax+"))
            or (isinstance(args[2], int) and args[2] & WRITE)):
        if not str(args[0]).startswith("/tmp/"):
            step()
sys.addaudithook(hook)
import sqlite3
_connect = sqlite3.connect
def connect(database, *args, **kwargs):
    connection = _connect(database, *args, **kwargs)
    if not str(database).startswith("/tmp/"):  # private read-only copies
        connection.set_trace_callback(statement)
    return connection
sqlite3.connect = connect
sys.argv = [script, *sys.argv[3:]]
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as exc:
    code = exc.code
else:
    code = 0
if kill == -1:
    sys.stderr.write(f"STEPS {seen[0]}\n")
sys.exit(code)
"""


# ---------------------------------------------------------------------------
# Fixture: the shapes production carries (2026-10-02 census), at small scale
# ---------------------------------------------------------------------------


def _write(path, data=b"x", mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)


def build(data: Path, *, extra=None):
    for entry in sorted(data.iterdir(), reverse=True):
        subprocess.run(["rm", "-rf", "--one-file-system", str(entry)], check=True)
    (data / ".layout.json").write_text(json.dumps(
        {"layout": 2, "state": "stable",
         "moves": {"consents_outside_command_centers": "done"}}))
    (data / ".layout.lock").touch()
    with closing(sqlite3.connect(data / ".tinyassets.db")) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE founder_home (universe_id TEXT, founder_sub TEXT)")
        db.execute("CREATE TABLE universe_acl (universe_id TEXT, actor_id TEXT, "
                   "permission TEXT)")
        db.executemany("INSERT INTO founder_home VALUES (?, ?)",
                       [("u-alice", "alice"), ("u-bob", "bob")])
        db.executemany("INSERT INTO universe_acl VALUES (?, ?, ?)",
                       [("legacy-one", "carol", "admin"), ("legacy-one", "dave", "read")])
        migration = runpy.run_path(str(Path(__file__).resolve().parents[1]
                                       / "deploy" / "role_migrate.py"))
        for schema in migration["ACCOUNTING_SCHEMA"]:
            db.execute(schema)
        db.execute(migration["ACCOUNTING_INDEX"])
        db.execute("INSERT INTO agent_request_usage VALUES "
                   "('alice','u-alice','r1','{}','[]','t','p',0,'2026-10-01')")
        db.execute("INSERT INTO agent_request_attempts VALUES "
                   "('alice','u-alice','r1',1,'{}',NULL,'src','open')")
    with closing(sqlite3.connect(data / "outbound.db")) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE grants (id TEXT)")
        db.execute("INSERT INTO grants VALUES ('g1')")
    _write(data / ".outbound-proxy" / "state.json", b"{}", 0o600)
    _write(data / ".consumer_liveness" / "daemon.lock", b"", 0o600)
    _write(data / ".universe-sidecars" / "u-alice" / "relay.json", b"{}", 0o600)
    relay = socket.socket(socket.AF_UNIX)
    relay.bind(str(data / ".universe-sidecars" / "u-alice" / "egress-12.sock"))
    relay.close()
    _write(data / ".broker" / "owner.json", b"{}", 0o600)
    _write(data / ".role-admission" / "stage-1" / "partial", b"p")
    _write(data / "community-pool" / "post.md", b"pool")
    for center, files in (("u-alice", 3), ("u-bob", 1), ("legacy-one", 1)):
        root = data / center
        _write(root / "universe.json", b"{}")
        for index in range(files):
            _write(root / "notes" / f"n{index}.md", f"{center} {index}".encode())
        _write(root / "bin" / "tool.sh", b"#!/bin/sh\n", 0o755)
        _write(root / ".agent-workspace" / "AGENTS.md", b"agent")
        _write(root / ".credentials" / "codex.json", b"secret", 0o600)
        _write(root / ".credential-vault.json", b"vault", 0o600)
        _write(root / "provider_definitions.json", b"[]")
        _write(root / ".runtime" / "provider-launch-credentials" / "snap" / "auth.json",
               b"sealed", 0o600)
        _write(root / ".runtime" / "cache" / "a", b"cache", 0o600)
        os.chmod(root / "notes", 0o700)
    # Five-link shape: same-owner multi-link inodes inside one owner's .runtime.
    os.link(data / "u-alice/.runtime/cache/a", data / "u-alice/.runtime/cache/b")
    os.symlink("notes", data / "u-alice" / "notes-link")
    os.symlink("../cache/a", data / "u-alice" / ".runtime" / "cache" / "scratch-link")
    if extra:
        extra(data)
    for path in (data, *data.rglob("*")):
        os.lchown(path, 1001, 1001)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def run(script, app, data, args, *, kill=0):
    result = subprocess.run([sys.executable, "-I", "-B", "-c", _CHILD, str(kill), script,
                             "--data-root", str(data), "--app", app, *args],
                            capture_output=True, text=True)
    return result


def manifest(script, app, data):
    result = run(script, app, data, ["--manifest"])
    if result.returncode != 0:
        raise SystemExit(f"manifest failed: {result.stderr}")
    return result.stdout


def _difference(got, want):
    got, want = set(got.splitlines()), set(want.splitlines())
    return f"extra {sorted(got - want)[:4]} missing {sorted(want - got)[:4]}"


def expect(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"ok   {message}", flush=True)


def inside(data: Path, app: str):
    script = str(Path(app) / "deploy" / "role_migrate.py")
    build(data)
    before = run(script, app, data, ["--check"])
    expect(before.returncode == 1 and "reserve identity: alice" in before.stdout,
           f"--check lists the pending work ({len(before.stdout.splitlines())} diffs)"
           + ("" if before.returncode == 1 else before.stderr[-2000:]))
    counted = run(script, app, data, SNAPSHOT, kill=-1)
    expect(counted.returncode == 0, f"full migration: {counted.stdout.strip()}"
           + ("" if counted.returncode == 0 else counted.stderr))
    steps = int(counted.stderr.rsplit("STEPS ", 1)[1].split()[0])
    reference = manifest(script, app, data)
    lines = reference.splitlines()
    expect(any(line.startswith("u-alice\t") and "\t1001\t300001\t0750\t" in line
               for line in lines), "owner root labelled 1001:<owner gid> 0750")
    expect(any(line.startswith(".broker/outbound.db\t") for line in lines)
           and not any(line.startswith("outbound.db\t") for line in lines),
           "ledger relocated into .broker")
    expect(not any(".role-admission" in line or "egress-12.sock" in line
                   or line.startswith(".broker/owner.json") for line in lines),
           "staging, stale sockets and the broker owner record removed")
    rerun = run(script, app, data, SNAPSHOT)
    expect(rerun.returncode == 0 and json.loads(rerun.stdout)["changed"] == 0
           and not json.loads(rerun.stdout)["marked"], "converged rerun is a no-op")
    expect(manifest(script, app, data) == reference, "converged rerun leaves the manifest")
    after = run(script, app, data, ["--check"])
    expect(after.returncode == 0 and after.stdout == "", "--check after the apply: 0 diffs")
    print(f"kill at each of {steps} mutating steps, then rerun", flush=True)
    for point in range(1, steps + 1):
        build(data)
        killed = run(script, app, data, SNAPSHOT, kill=point)
        if killed.returncode != 137:
            raise SystemExit(f"FAIL: kill point {point} did not fire: {killed.returncode} "
                             f"{killed.stderr}")
        healed = run(script, app, data, SNAPSHOT)
        if healed.returncode != 0:
            raise SystemExit(f"FAIL: rerun after kill {point}: {healed.stderr}")
        if manifest(script, app, data) != reference:
            got = set(manifest(script, app, data).splitlines())
            want = set(reference.splitlines())
            raise SystemExit(f"FAIL: manifest after kill {point} differs:\n"
                             f"  extra {sorted(got - want)[:5]}\n"
                             f"  missing {sorted(want - got)[:5]}")
    expect(True, f"every kill point ({steps}) reran to the byte-identical manifest")
    refusals(script, app, data)
    # Leave a migrated volume and its manifest for the restore round-trip.
    build(data)
    run(script, app, data, SNAPSHOT)
    Path(OUT, "reference.tsv").write_text(manifest(script, app, data))


def roundtrip(data: Path, app: str):
    """The runbook's snapshot and restore lines, as the host runs them (full root)."""
    script = str(Path(app) / "deploy" / "role_migrate.py")
    reference = Path(OUT, "reference.tsv").read_text()
    archive = "/tmp/probe-snapshot.tar.gz"
    subprocess.run(["tar", "-czf", archive, "--numeric-owner", "--acls", "--xattrs",
                    "-C", str(data), "."], check=True)
    subprocess.run(["find", str(data), "-mindepth", "1", "-delete"], check=True)
    subprocess.run(["tar", "-xzpf", archive, "-C", str(data), "--numeric-owner", "--acls",
                    "--xattrs", "--same-owner"], check=True)
    restored = manifest(script, app, data)
    expect(restored == reference,
           "snapshot restore round-trip keeps numeric owners, modes, setgid and ACLs"
           + ("" if restored == reference else f": {_difference(restored, reference)}"))


def refusals(script, app, data):
    def refused(name, extra=None, args=SNAPSHOT, root=data, setup=None):
        build(root, extra=extra) if root == data else None
        if setup:
            setup()
        snapshot = manifest(script, app, root)
        result = run(script, app, root, args)
        expect(result.returncode == 2 and "refused" in result.stderr
               and manifest(script, app, root) == snapshot,
               f"refuses, unchanged: {name} ({result.stderr.strip().splitlines()[-1:]})")

    refused("an entry owned by another owner",
            extra=lambda d: _write(d / "u-bob" / "notes" / "planted.md", b"b"),
            setup=lambda: os.lchown(data / "u-bob/notes/planted.md", 300001, 300001))
    refused("a cross-owner hardlink",
            extra=lambda d: os.link(d / "u-alice/notes/n0.md", d / "u-bob/notes/alias.md"))
    refused("a FIFO", extra=lambda d: os.mkfifo(d / "u-bob" / "notes" / "pipe"))
    refused("egress state in both places",
            extra=lambda d: _write(d / ".broker" / "outbound.db", b""))
    refused("an unstable layout marker", extra=lambda d: (d / ".layout.json").write_text(
        json.dumps({"layout": 2, "state": "migrating"})))
    refused("an owner tree with no authority row",
            extra=lambda d: _write(d / "u-zed" / "universe.json", b"{}"))
    refused("free space below the snapshot size",
            args=["--snapshot", "s", "--snapshot-bytes", str(1 << 60)])
    holder = []

    def hold():
        import fcntl

        holder.append(os.open(data / ".layout.lock", os.O_RDONLY))
        fcntl.flock(holder[-1], fcntl.LOCK_SH)
    refused("a process holding the volume", setup=hold)
    os.close(holder.pop())
    overlay = Path("/tmp/overlay-data")
    overlay.mkdir(exist_ok=True)
    build(overlay)
    refused("a non-ext4 volume", root=overlay)


def outside():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import linux_oracle

    root = linux_oracle._repo_root()
    tag = linux_oracle._image_tag(root)
    if not linux_oracle._image_exists(tag):
        linux_oracle._build(root, tag)
    volumes = (VOLUME, OUT_VOLUME)
    subprocess.run(["docker", "volume", "rm", "-f", *volumes], capture_output=True)
    common = ["--network", "none", "-v", f"{VOLUME}:/data", "-v", f"{OUT_VOLUME}:{OUT}",
              "-v", f"{linux_oracle._docker_path(root)}:/src:ro", tag,
              "python", "-I", "-B", "/src/scripts/role_migrate_probe.py", "--app", "/src"]
    # The migration's own posture, then a host-like root for the restore.
    migration = ["docker", "run", "--rm", "--user", "0", "--cap-drop", "ALL",
                 *[flag for cap in CAPS for flag in ("--cap-add", cap)], *common,
                 "--inside", "/data"]
    restore = ["docker", "run", "--rm", "--user", "0", *common, "--roundtrip", "/data"]
    try:
        return subprocess.run(migration).returncode or subprocess.run(restore).returncode
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", *volumes], capture_output=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inside")
    parser.add_argument("--roundtrip")
    parser.add_argument("--app", default="/app")
    args = parser.parse_args()
    if args.roundtrip:
        roundtrip(Path(args.roundtrip), args.app)
        print("role_migrate probe: PASS")
        return 0
    if args.inside is None:
        return outside()
    inside(Path(args.inside), args.app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
