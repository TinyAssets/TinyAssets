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


def accounting_probes(migration):
    """D29 table custody only; actual accounting IPC/old-image still required."""
    from tinyassets.storage.agent_request_usage import _SCHEMA

    transfer, relocate = migration["transfer_accounting"], migration["relocate"]
    facts, refused = migration["_accounting_facts"], migration["MigrationRefused"]

    def seed():
        root = fixture()
        pid = os.fork()
        if pid == 0:
            conn = sqlite3.connect(root / ".tinyassets.db")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA wal_autocheckpoint=0")
            for sql in _SCHEMA:
                conn.execute(sql)
            conn.execute("CREATE TABLE unrelated(value TEXT)")
            conn.execute("INSERT INTO unrelated VALUES ('daemon state retained')")
            conn.execute("INSERT INTO agent_request_usage VALUES "
                         "('alice','alice','usage','{}','{}','owner','parent',0,'today')")
            conn.execute("INSERT INTO agent_request_attempts VALUES "
                         "('alice','alice','usage',1,'{}',NULL,'source','reserved')")
            conn.execute("INSERT INTO agent_request_usage_links VALUES "
                         "('alice','alice','usage','turn','turn-one')")
            conn.execute("INSERT INTO agent_request_dispatches VALUES "
                         "('hash','alice','alice','usage',1,1,'grant','conn','digest','op',1,1)")
            conn.commit()
            os._exit(0)  # Committed usage rows deliberately remain in WAL.
        assert os.waitpid(pid, 0)[1] == 0
        assert (root / ".tinyassets.db-wal").stat().st_size > 0
        os.chown(root / ".tinyassets.db", 1001, 1001)
        relocate(root)
        return root

    def read(path):
        conn = sqlite3.connect(path)
        try:
            return facts(conn)
        finally:
            conn.close()

    root = seed()
    before = snapshot(root)
    assert len(transfer(root, dry_run=True)) == 4
    assert snapshot(root) == before, "accounting dry-run mutated live DB/WAL/SHM"
    transfer(root)
    saved = read(root / ".broker/outbound.db")
    assert len(saved) == 4 and all(row["rows"] == 1 for row in saved.values())
    assert read(root / ".tinyassets.db") == {}
    conn = sqlite3.connect(root / ".tinyassets.db")
    assert conn.execute("SELECT value FROM unrelated").fetchone() == ("daemon state retained",)
    conn.close()
    stable = snapshot(root)
    assert transfer(root) == [] and snapshot(root) == stable
    try:
        relocate(root, reverse=True)
    except refused:
        pass
    else:
        raise AssertionError("egress rollback bypassed accounting rollback")

    def broker_write():
        conn = sqlite3.connect(root / ".broker/outbound.db")
        assert facts(conn) == saved
        conn.execute("UPDATE agent_request_attempts SET state='dispatched'")
        conn.commit()
        conn.close()

    child(1002, [1102], broker_write)
    changed = read(root / ".broker/outbound.db")
    assert changed != saved
    before = snapshot(root)
    assert len(transfer(root, reverse=True, dry_run=True)) == 4
    assert snapshot(root) == before
    transfer(root, reverse=True)
    assert read(root / ".tinyassets.db") == changed
    assert read(root / ".broker/outbound.db") == {}
    stable = snapshot(root)
    assert transfer(root, reverse=True) == [] and snapshot(root) == stable
    relocate(root, reverse=True)
    stable = snapshot(root)
    assert transfer(root, reverse=True) == [] and snapshot(root) == stable
    print("D29 accounting forward/reverse dry-run/apply/repeat, broker writes and "
          "unrelated daemon-table preservation: PASS (not accounting IPC or old-image)", flush=True)

    for reverse in (False, True):
        for boundary in ("accounting-manifest", "accounting-copy", "accounting-verified",
                         "accounting-drop"):
            root = seed()
            if reverse:
                transfer(root)
            source = root / (".broker/outbound.db" if reverse else ".tinyassets.db")
            target = root / (".tinyassets.db" if reverse else ".broker/outbound.db")
            wanted = read(source)
            pid = os.fork()
            if pid == 0:
                transfer(root, reverse=reverse,
                         after_step=lambda name: os._exit(79) if name == boundary else None)
                os._exit(1)
            assert os.waitpid(pid, 0)[1] == 79 << 8
            try:
                relocate(root, reverse=True)
            except refused:
                pass
            else:
                raise AssertionError("egress moved incomplete accounting transfer")
            before = snapshot(root)
            transfer(root, reverse=reverse, dry_run=True)
            assert snapshot(root) == before
            transfer(root, reverse=reverse)
            assert read(target) == wanted and read(source) == {}
    print("D29 accounting abrupt-exit resume: PASS (8 forward/reverse boundaries)", flush=True)

    for boundary in ("checkpoint", "outbound.db", ".outbound-proxy"):
        root = seed()
        transfer(root)
        transfer(root, reverse=True)
        wanted = read(root / ".tinyassets.db")
        pid = os.fork()
        if pid == 0:
            relocate(root, reverse=True,
                     after_step=lambda name: os._exit(79) if name == boundary else None)
            os._exit(1)
        assert os.waitpid(pid, 0)[1] == 79 << 8
        before = snapshot(root)
        assert transfer(root, reverse=True) == [] and snapshot(root) == before
        relocate(root, reverse=True)
        assert read(root / ".tinyassets.db") == wanted
    print("D29 accounting rollback resumes interrupted reverse egress: PASS (3 boundaries)",
          flush=True)

    for attack in ("symlink", "hardlink", "fifo", "conflict", "schema", "diverged-copy",
                   "case-collision", "index-collision"):
        root = seed()
        if attack in {"symlink", "hardlink", "fifo"}:
            # Keep the synthetic original in place; attack a sidecar SQLite would open.
            path = root / ".tinyassets.db-journal"
            if attack == "symlink":
                path.symlink_to(root / ".tinyassets.db")
            elif attack == "hardlink":
                os.link(root / ".tinyassets.db", path)
            else:
                os.mkfifo(path)
        elif attack == "schema":
            conn = sqlite3.connect(root / ".tinyassets.db")
            conn.execute("ALTER TABLE agent_request_usage ADD COLUMN unexpected TEXT")
            conn.close()
        elif attack in {"case-collision", "index-collision"}:
            conn = sqlite3.connect(root / ".broker/outbound.db")
            name = ("AGENT_REQUEST_USAGE" if attack == "case-collision"
                    else "agent_request_attempt_day")
            conn.execute(f"CREATE TABLE {name} (value TEXT)")
            conn.close()
        else:
            if attack == "diverged-copy":
                pid = os.fork()
                if pid == 0:
                    transfer(root, after_step=lambda name: os._exit(79)
                             if name == "accounting-copy" else None)
                    os._exit(1)
                assert os.waitpid(pid, 0)[1] == 79 << 8
            conn = sqlite3.connect(root / ".broker/outbound.db")
            if attack == "conflict":
                for sql in _SCHEMA:
                    conn.execute(sql)
            else:
                conn.execute("UPDATE agent_request_usage SET owner='foreign'")
            conn.commit()
            conn.close()
        before = snapshot(root)
        for dry in (True, False):
            try:
                transfer(root, dry_run=dry)
            except refused:
                pass
            else:
                raise AssertionError(f"accounting migration accepted {attack}")
            assert snapshot(root) == before
    print("D29 accounting link/FIFO/conflict/schema/diverged-copy refusal without mutation: PASS",
          flush=True)


def liveness_probes():
    """Real cross-uid read-only kernel proof, independent parent lifetime."""
    from tinyassets.process_liveness import ALIVE, DEAD, UNKNOWN, hold_liveness, owner_state
    from tinyassets.singleton_lock import _unlock_fd

    root = Path(tempfile.mkdtemp(prefix="uid-liveness-"))
    os.chown(root, 1001, 1102)
    root.chmod(0o750)
    ready_read, ready_write = os.pipe()
    command_read, command_write = os.pipe()
    holder = os.fork()
    if holder == 0:
        try:
            os.close(ready_read)
            os.close(command_write)
            retire(1001, [1100, 1101, 1102])
            daemon = hold_liveness(root, "daemon", broker_readable=True)
            parent = hold_liveness(root, "parent", broker_readable=True)
            os.write(ready_write, b"1")
            assert os.read(command_read, 1) == b"r"
            _unlock_fd(parent.fd)
            os.write(ready_write, b"2")
            assert os.read(command_read, 1) == b"q"
            assert daemon.fd is not None
            os._exit(0)  # Kernel releases daemon lock; retain file as dead proof.
        except BaseException:
            traceback.print_exc()
            os._exit(1)
    os.close(ready_write)
    os.close(command_read)
    try:
        assert os.read(ready_read, 1) == b"1"

        def probe(parent_state, daemon_state):
            assert owner_state(root, "parent") == parent_state
            assert owner_state(root, "daemon") == daemon_state
            assert owner_state(root, "missing") == UNKNOWN
            for path in (root / ".consumer_liveness").glob("*.lock"):
                try:
                    fd = os.open(path, os.O_WRONLY)
                except PermissionError:
                    pass
                else:
                    os.close(fd)
                    raise AssertionError("broker can write daemon liveness proof")

        child(1002, [1102], lambda: probe(ALIVE, ALIVE))
        child(1003, [1100], lambda: (
            assert_unknown_liveness(root, owner_state, UNKNOWN)))
        os.write(command_write, b"r")
        assert os.read(ready_read, 1) == b"2"
        child(1002, [1102], lambda: probe(DEAD, ALIVE))
        os.write(command_write, b"q")
        assert os.waitpid(holder, 0)[1] == 0
        holder = None
        child(1002, [1102], lambda: probe(DEAD, DEAD))
    finally:
        os.close(ready_read)
        os.close(command_write)
        if holder is not None:
            import signal
            os.kill(holder, signal.SIGKILL)
            os.waitpid(holder, 0)
    print("D42/D45 runtime-created read-only daemon/parent kernel liveness, independent parent "
          "close, daemon death, engine denial: PASS", flush=True)


def liveness_migration_probes(migration):
    modes = runpy.run_path("/app/tinyassets/role_modes.py")
    migrate, refused = migration["migrate_liveness"], migration["MigrationRefused"]

    def seed():
        root = fixture()
        directory = root / ".consumer_liveness"
        directory.mkdir(mode=0o700)
        os.chown(directory, 1001, 1001)
        path = directory / "retained.lock"
        path.write_bytes(b"retained proof bytes")
        os.chown(path, 1001, 1001)
        path.chmod(0o600)
        return root

    root = seed()
    for reverse in (False, True):
        before = snapshot(root)
        assert migrate(root, modes=modes, reverse=reverse, dry_run=True)
        assert snapshot(root) == before
        migrate(root, modes=modes, reverse=reverse)
        stable = snapshot(root)
        assert migrate(root, modes=modes, reverse=reverse) == []
        assert snapshot(root) == stable
        assert (root / ".consumer_liveness/retained.lock").read_bytes() == b"retained proof bytes"
    for reverse in (False, True):
        for boundary in ("liveness-marker", "liveness-entry"):
            root = seed()
            if reverse:
                migrate(root, modes=modes)
            pid = os.fork()
            if pid == 0:
                migrate(root, modes=modes, reverse=reverse,
                        after_step=lambda step: os._exit(79) if step == boundary else None)
                os._exit(1)
            assert os.waitpid(pid, 0)[1] == 79 << 8
            migrate(root, modes=modes, reverse=reverse)
            proof = root / ".consumer_liveness/retained.lock"
            assert proof.read_bytes() == b"retained proof bytes"
            assert migrate(root, modes=modes, reverse=reverse) == []
    for kind in ("symlink", "hardlink", "fifo", "foreign_owner"):
        root = seed()
        target = root / ".consumer_liveness/attack.lock"
        outside = root / "unrelated"
        outside.write_bytes(b"retained outside")
        if kind == "symlink":
            target.symlink_to(outside)
        elif kind == "hardlink":
            os.link(outside, target)
        elif kind == "fifo":
            os.mkfifo(target)
        else:
            target.touch()
            os.chown(target, 1003, 1100)
        before = snapshot(root)
        try:
            migrate(root, modes=modes)
        except refused:
            pass
        else:
            raise AssertionError("hostile liveness migration accepted")
        assert snapshot(root) == before
    print("D45 liveness forward/reverse dry-run/apply/repeat, four crash boundaries, "
          "hostile aliases and foreign owner refused without mutation: PASS", flush=True)


def assert_unknown_liveness(root, owner_state, unknown):
    assert owner_state(root, "daemon") == unknown
    assert owner_state(root, "parent") == unknown


def relay_permission_probes():
    """Actual identities and live sockets; exact cell mounts remain separate."""
    import socket

    root = Path(tempfile.mkdtemp(prefix="uid-relays-"))
    root.chmod(0o755)
    os.chown(root, 1001, 1001)
    ready_read, ready_write = os.pipe()
    stop_read, stop_write = os.pipe()
    sys.stdout.flush()
    pid = os.fork()
    if pid == 0:
        os.close(ready_read)
        os.close(stop_write)
        try:
            retire(1001, [1100, 1101, 1102])
            os.environ["TINYASSETS_CREDENTIAL_BROKER"] = "process"
            from tinyassets import universe_egress

            (root / "alice").mkdir()
            universe_egress._route_port = lambda *args: 12345
            paths = [universe_egress.ensure_proxy(root / "alice"),
                     universe_egress.ensure_engine_relay(
                         root / "alice", actor_id="alice", graph_id="g")[0]]
            for path in paths:
                info = path.stat()
                assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (1001, 1100, 0o660)
                assert stat.S_IMODE(path.parent.stat().st_mode) == 0o2710
            os.write(ready_write, json.dumps([str(path) for path in paths]).encode())
            os.close(ready_write)
            assert os.read(stop_read, 1) == b"x"
        except BaseException:
            traceback.print_exc()
            os._exit(1)
        os._exit(0)
    os.close(ready_write)
    os.close(stop_read)
    try:
        paths = json.loads(os.read(ready_read, 4096))

        def engine():
            for index, path in enumerate(paths):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(2)
                    client.connect(path)
                    if index == 0:
                        client.sendall(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n")
                        assert b"403 Forbidden" in client.recv(4096)
                try:
                    os.listdir(Path(path).parent)
                except PermissionError:
                    pass
                else:
                    raise AssertionError("engine listed relay directory")

        def broker():
            for path in paths:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    try:
                        client.connect(path)
                    except PermissionError:
                        pass
                    else:
                        raise AssertionError("broker reached engine relay")

        child(1003, [1100], engine)
        child(1002, [1102], broker)
    finally:
        os.close(ready_read)
        os.write(stop_write, b"x")
        os.close(stop_write)
        assert os.waitpid(pid, 0)[1] == 0
    print("D55 actual daemon egress/engine relay 1001:1100/0660; engine connects, "
          "directory listing and broker denied: PASS (socket prerequisite, "
          "not class acceptance)", flush=True)


def snapshot_permission_probes():
    root = Path(tempfile.mkdtemp(prefix="uid-snapshots-"))
    root.chmod(0o755)
    os.chown(root, 1001, 1001)
    universe = root / "snapshot-owner"
    universe.mkdir(mode=0o711)
    os.chown(universe, 1001, 1100)

    def create():
        import base64

        from tinyassets import credential_vault as vault
        from tinyassets.storage import db_path

        os.environ["TINYASSETS_CREDENTIAL_BROKER"] = "process"
        vault.write_credential_vault(universe, [{"credential_type": "llm_subscription",
            "service": "codex", "auth_json_b64": base64.b64encode(
                b'{"token":"snapshot-fixture"}').decode()}],
            owner_user_id="snapshot-owner", universe_id=universe.name)
        with sqlite3.connect(db_path(root)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            custody = vault.adopt_llm_subscription_custody(
                conn, universe_dir=universe, owner_user_id="snapshot-owner",
                universe_id=universe.name, service="codex")
        made = vault.snapshot_llm_subscription_credential(universe_dir=universe, custody=custody)
        vault._prepare_snapshot_root(universe)
        for directory in (made.directory, made.directory.parent, made.directory.parent.parent):
            info = directory.stat()
            assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (1001, 1100, 0o2750)
        for path in made.directory.iterdir():
            info = path.stat()
            assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (1001, 1100, 0o440)

    child(1001, [1100, 1101, 1102], create)
    directory = next((universe / ".runtime/provider-launch-credentials").iterdir())

    def engine():
        assert (directory / "auth.json").read_bytes() == b'{"token":"snapshot-fixture"}'
        with open(directory / ".lock", "rb") as handle:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for path in (directory / "auth.json", universe / ".credential-vault.json"):
            try:
                fd = os.open(path, os.O_WRONLY)
            except PermissionError:
                pass
            else:
                os.close(fd)
                raise AssertionError("engine wrote a sealed credential")
        version = subprocess.run(["/usr/local/bin/codex", "--version"],
                                 env={"PATH": "/usr/local/bin:/usr/bin:/bin",
                                      "HOME": "/tmp", "CODEX_HOME": str(directory)},
                                 cwd=directory, capture_output=True, timeout=15)
        assert version.returncode == 0, version.stderr.decode()

    child(1003, [1100], engine)

    def broker():
        try:
            (directory / "auth.json").read_bytes()
        except PermissionError:
            pass
        else:
            raise AssertionError("broker acquired work-group snapshot access")
        assert (universe / ".credential-vault.json").read_bytes()

    child(1002, [1102], broker)
    print("D54 actual daemon snapshot creation/reprepare: 1001:1100 2750/0440; "
          "engine read and installed CLI lock/version, engine write and broker snapshot denial: "
          "PASS (permissions prerequisite, not launcher class acceptance)", flush=True)


def main():
    assert os.getuid() == 0
    expected = sum(1 << cap for cap in (0, 1, 3, 5, 6, 7, 8))
    for field in ("CapPrm", "CapEff", "CapBnd"):
        assert int(status()[field], 16) == expected, field
    assert all(int(status()[field], 16) == 0 for field in ("CapInh", "CapAmb"))
    liveness_probes()
    snapshot_permission_probes()
    relay_permission_probes()
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
    for private in (root / ".broker", root / ".broker/.outbound-proxy"):
        info = private.stat()
        assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (1002, 1101, 0o2700)
    print("broker private directory ownership/setgid readbacks without FSETID: PASS", flush=True)
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
    accounting_probes(migration)
    liveness_migration_probes(migration)
    runpy.run_path("/app/scripts/role_launcher_oracle.py")["main"]()
    print("FOUNDATION/EGRESS SUBSTEP ONLY: launcher, IPC, real engine classes, "
          "full rollback pending")


if __name__ == "__main__":
    main()
