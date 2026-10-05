"""Production-image foundation/egress probes; synthetic data, no live service.

This is deliberately not the full per-class launcher acceptance matrix. The
runner prints its image identity and grants only the planned seven entry caps.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import pwd
import runpy
import sqlite3
import stat
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
CAP_FIELDS = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")


def status():
    return dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())


def retire(uid, groups):
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.prctl(38, 1, 0, 0, 0) == 0
    assert libc.prctl(8, 0, 0, 0, 0) == 0
    assert libc.prctl(47, 4, 0, 0, 0) == 0
    for capability in range(int(Path("/proc/sys/kernel/cap_last_cap").read_text()) + 1):
        assert libc.prctl(24, capability, 0, 0, 0) == 0
    os.setgroups(groups)
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)
    assert os.getresuid() == (uid, uid, uid)
    assert os.getresgid() == (uid, uid, uid)
    assert os.getgroups() == groups
    fields = status()
    assert all(int(fields[key], 16) == 0 for key in CAP_FIELDS)
    assert int(fields["NoNewPrivs"]) == 1
    os.umask(0o077)
    print(f"identity uid={uid} groups={groups} caps=all-zero nnp=1", flush=True)


def child(uid, groups, operation):
    sys.stdout.flush()
    pid = os.fork()
    if pid == 0:
        try:
            retire(uid, groups)
            operation()
            sys.stdout.flush()
        except BaseException:
            traceback.print_exc()
            os._exit(1)
        os._exit(0)
    assert os.waitpid(pid, 0)[1] == 0


def snapshot(root):
    result = {}
    for path in (root, *sorted(root.rglob("*"))):
        info = path.lstat()
        content = (hashlib.sha256(path.read_bytes()).hexdigest()
                   if stat.S_ISREG(info.st_mode) else None)
        result[str(path.relative_to(root))] = (
            info.st_uid, info.st_gid, info.st_mode, info.st_mtime_ns, content,
        )
    return result


def fixture():
    from tinyassets.storage.outbound_connections import ConnectionLedger

    root = Path(tempfile.mkdtemp(prefix="uid-egress-"))
    root.chmod(0o755)
    os.chown(root, 1001, 1001)
    (root / ".layout.lock").touch(mode=0o666)
    (root / ".layout.json").write_text(json.dumps({
        "layout": 2, "state": "stable",
        "moves": {"consents_outside_command_centers": "done"},
    }))
    ConnectionLedger(root / "outbound.db")
    pid = os.fork()
    if pid == 0:
        connection = sqlite3.connect(root / "outbound.db")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE sentinel(value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('retained')")
        connection.commit()
        # Simulate the prior image dying with committed, uncheckpointed WAL.
        os._exit(0)
    assert os.waitpid(pid, 0)[1] == 0
    assert (root / "outbound.db-wal").stat().st_size > 0
    proxy = root / ".outbound-proxy"
    proxy.mkdir()
    (proxy / "audit.jsonl").write_text('"retained proxy bytes"\n')
    return root


def insert(path, name):
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ConnectionLedger(path).create_connection(
        connection_id=name, owner_user_id="alice", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("POST",), provider="http",
        destination="compute:synthetic", credential_ref="vault://http/synthetic",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/v1/chat",
                            "methods": ["POST"]}],
    )


def main():
    assert os.getuid() == 0
    expected = sum(1 << cap for cap in (0, 1, 3, 5, 6, 7, 8))
    for field in ("CapPrm", "CapEff", "CapBnd"):
        assert int(status()[field], 16) == expected, field
    assert all(int(status()[field], 16) == 0 for field in ("CapInh", "CapAmb"))
    chain = runpy.run_path("/usr/local/libexec/ta-chain.py")
    chain["main"]()
    protected = Path("/opt/uid-chain-probe")
    protected.mkdir(mode=0o755)
    module = protected / "module.py"
    module.write_text("pass\n")
    for owner, mode in ((1001, 0o444), (0, 0o666)):
        os.chown(module, owner, owner)
        module.chmod(mode)
        try:
            chain["verify_paths"]([], [str(protected)])
        except chain["UnsafeChain"]:
            pass
        else:
            raise AssertionError("unsafe module passed import-chain gate")
    os.chown(module, 0, 0)
    module.chmod(0o444)
    chain["verify_paths"]([], [str(protected)])
    print("non-root/writable descendant module chain refusal: PASS", flush=True)
    for uid in (1001, 1002, 1003):
        assert pwd.getpwuid(uid).pw_gid == uid
        assert os.getgrouplist(pwd.getpwuid(uid).pw_name, uid) == [uid]
    assert not Path("/opt/venv/bin/python").is_symlink()

    def immutable():
        for path in ("/app", "/app/tinyassets", "/usr/local/libexec/ta-entry.sh"):
            assert not os.access(path, os.W_OK), path
        home = Path("/home/tinyassets")
        (home / "write-proof").write_text("ok")
        subprocess.run(["bwrap", "--unshare-all", "--ro-bind", "/usr", "/usr",
                        "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
                        "--symlink", "usr/lib64", "/lib64", "--proc", "/proc",
                        "--dev", "/dev", "--", "/bin/true"], check=True)
    child(1001, [], immutable)
    print("image accounts, immutable paths, writable HOME, unprivileged bwrap: PASS", flush=True)
    migration = runpy.run_path("/usr/local/libexec/ta-egress-migration.py")
    relocate, refused = migration["relocate"], migration["MigrationRefused"]
    for consent_state in (None, "migrating"):
        blocked = fixture()
        marker = {"layout": 2, "state": "migrating",
                  "moves": {"consents_outside_command_centers": consent_state}}
        (blocked / ".layout.json").write_text(json.dumps(marker))
        before = snapshot(blocked)
        try:
            relocate(blocked)
        except refused:
            pass
        else:
            raise AssertionError("overlapping consent migration admitted")
        assert snapshot(blocked) == before
    print("overlapping consent migration refused without mutation: PASS", flush=True)
    root = fixture()
    before = snapshot(root)
    assert relocate(root, dry_run=True)
    assert snapshot(root) == before
    relocate(root)
    before = snapshot(root)
    assert relocate(root) == []
    assert snapshot(root) == before
    assert json.loads((root / ".layout.json").read_text())["state"] == "migrating"
    from tinyassets.storage_layout import LayoutRefused, check
    try:
        check(root)
    except LayoutRefused:
        pass
    else:
        raise AssertionError("normal daemon layout admission reopened relocated state")
    print("forward dry-run, apply, repeat; service remains unadmitted: PASS", flush=True)

    def broker():
        insert(root / ".broker/outbound.db", "existing")
        insert(root / ".broker/fresh.db", "fresh")
        (root / ".broker/.outbound-proxy/new-grant").mkdir(mode=0o700)
    child(1002, [1102], broker)
    print("broker actual ConnectionLedger existing/fresh writes and proxy mkdir: PASS", flush=True)

    def denied():
        for name in (".broker", ".broker/outbound.db", ".broker/outbound.db-wal",
                     ".broker/outbound.db-shm", ".broker/.outbound-proxy"):
            try:
                fd = os.open(root / name, os.O_RDONLY)
            except PermissionError:
                continue
            else:
                os.close(fd)
                raise AssertionError(f"private access unexpectedly permitted: {name}")
    child(1001, [1100, 1101, 1102], denied)
    child(1003, [1100], denied)
    print("direct daemon/engine-identity private path denials: PASS "
          "(not class acceptance)", flush=True)
    before = snapshot(root)
    relocate(root, reverse=True, dry_run=True)
    assert snapshot(root) == before
    relocate(root, reverse=True)
    before = snapshot(root)
    assert relocate(root, reverse=True) == []
    assert snapshot(root) == before

    def old_identity():
        insert(root / "outbound.db", "restored")
        with sqlite3.connect(root / "outbound.db") as connection:
            assert connection.execute("SELECT value FROM sentinel").fetchone() == ("retained",)
        audit = root / ".outbound-proxy/audit.jsonl"
        assert audit.read_text() == '"retained proxy bytes"\n'
        with audit.open("a") as handle:
            handle.write("restored write\n")
    child(1001, [], old_identity)
    print("reverse dry-run/apply/repeat and uid-1001 old-location writes: PASS", flush=True)

    for reverse in (False, True):
        for interruption in ("checkpoint", "outbound.db", ".outbound-proxy"):
            root = fixture()
            if reverse:
                relocate(root)

            def interrupt(step):
                if step == interruption:
                    os._exit(97)
            pid = os.fork()
            if pid == 0:
                relocate(root, reverse=reverse, after_step=interrupt)
                os._exit(98)
            assert os.waitpid(pid, 0)[1] == 97 << 8, "fault injection did not execute"
            relocate(root, reverse=reverse)
            path = root / "outbound.db" if reverse else root / ".broker/outbound.db"
            with sqlite3.connect(path) as connection:
                assert connection.execute("SELECT value FROM sentinel").fetchone() == ("retained",)
    print("forward/reverse abrupt-exit checkpoint and rename recovery: PASS "
          "(6 boundaries)", flush=True)

    for attack in ("symlink", "hardlink", "fifo", "conflict"):
        root = fixture()
        if attack == "conflict":
            (root / ".broker").mkdir()
            (root / ".broker/outbound.db").write_bytes(b"do not overwrite")
        else:
            target = root / ".outbound-proxy/planted"
            if attack == "symlink":
                target.symlink_to(root / "outbound.db")
            elif attack == "hardlink":
                os.link(root / "outbound.db", target)
            else:
                os.mkfifo(target)
        before = snapshot(root)
        try:
            relocate(root)
        except refused:
            pass
        else:
            raise AssertionError(f"migration accepted {attack}")
        assert snapshot(root) == before
    print("symlink/hardlink/FIFO/conflicting-copy refusal without mutation: PASS", flush=True)
    print("FOUNDATION/EGRESS SUBSTEP ONLY: launcher, IPC, real engine classes, "
          "full rollback pending")


if __name__ == "__main__":
    main()
