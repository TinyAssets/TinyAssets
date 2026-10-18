"""Regression proofs for erasure authority loss, live boxes and missing mounts."""
import asyncio
import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from tests.support.recovery_daemons import recovery_daemons
from tinyassets.auth.provider import Identity
from tinyassets.erasure_ledger import ErasureLedger, fresh_install, principal_digest


@pytest.mark.parametrize("damage", ["missing", "empty", "corrupt", "header", "permissions"])
def test_authentication_refuses_damaged_initialized_ledger(tmp_path, caplog, damage):
    ledger = ErasureLedger(tmp_path)
    ledger.initialize()
    ledger.add("alice")
    if damage == "missing":
        ledger.path.unlink()
    elif damage == "empty":
        ledger.path.write_bytes(b"")
    elif damage == "corrupt":
        ledger.path.write_bytes(b"corrupt database")
    elif damage == "header":
        with sqlite3.connect(ledger.path) as db:
            db.execute("DROP TABLE initialized")
    else:
        ledger.path.chmod(0o644)
    before = ledger.path.read_bytes() if ledger.path.exists() else None
    assert ledger.contains("alice")
    assert ledger.contains("never-fenced")
    assert "refusing every authentication" in caplog.text
    assert (ledger.path.read_bytes() if ledger.path.exists() else None) == before


def test_fresh_install_explicitly_initializes_and_auth_reads_are_readonly(tmp_path, monkeypatch):
    ledger = ErasureLedger(tmp_path)
    assert ledger.contains("alice")
    assert not ledger.path.exists()
    fresh_install(tmp_path)
    assert ledger.path.stat().st_uid == 0
    assert ledger.path.stat().st_mode & 0o777 == 0o600
    connect = sqlite3.connect
    statements = []

    def readonly(database, **kw):
        assert database.endswith("?mode=ro") and kw["uri"]
        db = connect(database, **kw)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(sqlite3, "connect", readonly)
    assert not ledger.contains("alice")
    assert not any(s.startswith(("CREATE", "INSERT", "UPDATE", "DELETE")) for s in statements)
    with pytest.raises(RuntimeError, match="empty data"):
        fresh_install(tmp_path)


@pytest.mark.parametrize("failure", ["resolve", "http", "unadmitted"])
def test_box_failure_does_not_block_shared_phases_and_retries(tmp_path, monkeypatch, failure):
    from starlette.responses import JSONResponse

    from tinyassets import owner_router

    ledger = ErasureLedger(tmp_path)
    ledger.initialize()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    calls = []
    failing = [True]

    def resolve(*a, **kw):
        calls.append("resolve")
        if failing[0] and failure == "resolve":
            raise RuntimeError("box unavailable")
        if failure == "unadmitted":
            raise LookupError("no live admission")
        return object()

    async def local(*a):
        return JSONResponse({}, status_code=503 if failing[0] and failure == "http" else 200)

    def retire(*a, **kw):
        calls.append("tree")
        if failing[0]:
            raise RuntimeError("store unavailable; no absence proof")

    monkeypatch.setattr(owner_router, "resolve_box", resolve)
    monkeypatch.setattr(owner_router, "delete_box_data", local)
    monkeypatch.setattr("tinyassets.broker.owner_identities.retire_owner", retire)
    for name, target, result in (
        ("billing", "tinyassets.billing.stripe_adapter.cancel_account_billing", "cancelled"),
        ("identity", "tinyassets.router_account_deletion.delete_workos_user", "deleted"),
        ("broker_channels", "tinyassets.broker.account_erasure.close_channels", None),
        ("broker_egress", "tinyassets.broker.account_erasure.erase_account", None),
        ("credentials", "tinyassets.router_credentials.revoke_account", None),
    ):
        monkeypatch.setattr(target, lambda *a, _name=name, _result=result, **kw:
                            calls.append(_name) or _result)
    identity = Identity(user_id="alice", username="alice")
    assert asyncio.run(owner_router.delete_owner_account(identity)).status_code == 503
    assert ledger.receipt("alice")["unfinished_phases"] == ["owner_tree", "identity"]
    assert all(name in ledger.receipt("alice")["completed_phases"] for name in (
        "billing", "credentials", "broker_egress"))
    assert "identity" not in calls
    failing[0] = False
    assert asyncio.run(owner_router.delete_owner_account(identity)).status_code == 200
    assert ledger.receipt("alice")["unfinished_phases"] == []
    assert calls.count("resolve") == 2
    assert calls.count("billing") == calls.count("identity") == 1


@pytest.mark.parametrize("op", ["BOX_ADMISSION", "BOX_CHANNEL", "BOX_SHARED",
                                "BOX_LIFECYCLE", "BOX_CREDENTIAL", "BOX_IDENTITY",
                                "BOX_PING", "BOX_PUBLISH", "BOX_READ", "BOX_FUTURE"])
def test_all_box_operations_refuse_ledger_fenced_live_owner(tmp_path, op):
    from tinyassets import rpc_frames as rf
    from tinyassets.broker.server import BOX, BOX_HOST, _Connection

    ledger = ErasureLedger(tmp_path)
    ledger.initialize()
    ledger.add("alice")

    async def exercise():
        # Both an already-admitted, still-running box and new host channels.
        for role in (BOX, BOX_HOST):
            server = SimpleNamespace(_data_root=tmp_path)
            channel = _Connection(server, None, role)
            channel._box_owner, channel._box_center = "alice", "a"
            await channel._connection_op(op, {"op": op, "principal": "alice", "center": "a"})
            assert channel._queue.get_nowait() == rf.control(rf.CONNECTION, {"op": "BOX_REFUSED"})
            assert channel._role == role

    asyncio.run(exercise())


def test_public_egress_refuses_ledger_fenced_owner_with_live_broker_admission(tmp_path):
    from tests.test_box_public_egress import exchange
    from tinyassets.broker.owner_identities import OwnerIdentities
    from tinyassets.broker.public_egress import PublicEgress

    ledger = ErasureLedger(tmp_path)
    ledger.initialize()
    state = tmp_path / "broker"
    state.mkdir(mode=0o700)
    identities = OwnerIdentities(state / "identities.db", initialize=True)
    uid = identities.resolve("alice", allocate=True).uid
    identities.admission("admit", "alice", "a")
    identities.plans.apply("alice", tier="paid", event_created=1)
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal, machine, project_id, bhard_bytes)")
        db.execute("INSERT INTO box_records VALUES (?,?,?,?)", ("alice", uid, uid, 1048576))
    ledger.add("alice")
    assert identities.box_route("alice")[0] == uid  # broker state still admits it
    proxy = PublicEgress(identities, tmp_path)
    assert asyncio.run(exchange(proxy, b"GET http://example.test/ HTTP/1.0\r\n\r\n")) == b"REFUSED"
    assert not proxy.active


def test_unmounted_store_keeps_restore_tree_phase_pending(tmp_path, monkeypatch):
    from tinyassets.boxes.destroy import tree
    from tinyassets.boxes.provider import BoxError
    from tinyassets.restore_recovery import finish_erasures

    data, store = tmp_path / "data", tmp_path / "unmounted"
    data.mkdir()
    store.mkdir()
    ledger = ErasureLedger(data)
    ledger.initialize()
    ledger.add("alice")
    ledger.execute("work", {"digest": principal_digest("alice"), "target": {
        "store": ".", "uid": 300001,
        "quarantine": ".quarantine/300001"}})
    with pytest.raises(BoxError, match="mount"):
        tree(store, 300001, erase=True)
    finish_erasures(data, store, daemons=recovery_daemons(store, monkeypatch))
    assert "owner_tree" in ledger.receipt("alice")["unfinished_phases"]
    assert ledger.entries()[0]["work"]
    assert not (store / ".quarantine").exists()


@pytest.mark.parametrize("drift", [None, "unmounted", "uuid", "type", "descriptor", "proof"])
def test_store_identity_and_absent_tree(tmp_path, monkeypatch, drift):
    from pathlib import Path

    from tinyassets.boxes import destroy
    from tinyassets.boxes.provider import BoxError

    mount = tmp_path / "owners"
    mount.mkdir()
    (mount.parent / ".owners-identity").write_text("xfs setup-uuid\n")
    (mount.parent / ".owners-identity").chmod(0o600)
    fd_stat = os.fstat
    path_stat = Path.stat
    device = mount.stat().st_dev
    parent_device = device if drift == "unmounted" else device + 1

    def stat_path(path, **kw):
        info = path_stat(path, **kw)
        if path == mount.parent:
            return SimpleNamespace(st_dev=parent_device)
        return info

    def stat_fd(fd):
        info = fd_stat(fd)
        if drift == "descriptor":
            return SimpleNamespace(st_dev=device + 2)
        return info

    read_text = Path.read_text

    def read_mounts(path, **kw):
        if str(path) == "/proc/self/mountinfo":
            return f"42 1 0:1 / {mount} rw - xfs fixture rw\n"
        return read_text(path, **kw)

    monkeypatch.setattr(Path, "read_text", read_mounts)
    monkeypatch.setattr(destroy, "_mount", lambda fd: "42")
    monkeypatch.setattr(Path, "stat", stat_path)
    monkeypatch.setattr(os, "fstat", stat_fd)
    if drift == "proof":
        (mount.parent / ".owners-identity").chmod(0o644)
    document = {"filesystems": [{"target": str(mount),
        "fstype": "tmpfs" if drift == "type" else "xfs",
        "uuid": "other" if drift == "uuid" else "setup-uuid"}]}
    monkeypatch.setattr(destroy.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(stdout=json.dumps(document)))
    if drift is None:
        assert destroy.tree(mount, 300001, erase=True) == 0
        from tinyassets.restore_recovery import finish_erasures

        data = tmp_path / "data"
        data.mkdir()
        ledger = ErasureLedger(data)
        ledger.initialize()
        ledger.add("alice")
        ledger.execute("work", {"digest": principal_digest("alice"), "target": {
            "store": ".", "uid": 300001,
            "quarantine": ".quarantine/300001"}})
        finish_erasures(data, mount, daemons=recovery_daemons(mount, monkeypatch))
        assert ledger.receipt("alice")["home_removed"]
        assert not ledger.entries()[0]["work"]
    else:
        with pytest.raises(BoxError):
            destroy.tree(mount, 300001, erase=True)


@pytest.mark.parametrize("damage", ["fenced", "missing", "empty"])
def test_established_box_stream_and_send_checkpoint_recheck_erasure(tmp_path, damage):
    from tinyassets import rpc_frames as rf
    from tinyassets.broker.server import BOX, _Connection

    ledger = ErasureLedger(tmp_path)
    ledger.initialize()
    server = SimpleNamespace(_data_root=tmp_path,
                             _owner_identities=SimpleNamespace(admitted_owner=lambda _: "alice"),
                             _fence=SimpleNamespace(generation=1, token="live"))

    async def exercise():
        channel = _Connection(server, None, BOX)
        channel._box_owner, channel._box_center = "alice", "a"
        assert channel._box_scope({})["principal"] == "alice"
        if damage == "fenced":
            ledger.add("alice")
        elif damage == "missing":
            ledger.path.unlink()
        else:
            ledger.path.write_bytes(b"")
        with pytest.raises(rf.FrameError, match="erased"):
            await channel._open(1, {"op": "OPEN"})
        with pytest.raises(rf.FrameError, match="erased"):
            channel._checkpoint(SimpleNamespace(cancelled=False))

    asyncio.run(exercise())


def test_broker_ledger_reads_work_while_lifecycle_slots_are_full(tmp_path):
    import subprocess
    import sys
    import tempfile
    import threading
    import time
    from pathlib import Path

    from tinyassets.boxes.host_service import HostService

    ledger = ErasureLedger(tmp_path)
    ledger.initialize()
    ledger.add("alice")
    service = HostService(SimpleNamespace(data_root=tmp_path), None, None, None)
    for _ in range(4):
        assert service._broker_slots.acquire(blocking=False)
    with tempfile.TemporaryDirectory(prefix="ta-fence-") as directory:
        runtime = Path(directory)
        runtime.chmod(0o755)
        path = runtime / "erasure.sock"
        stop = threading.Event()
        server = threading.Thread(target=service.serve,
                                  args=(path, 1102, service.erasure, stop))
        server.start()
        try:
            deadline = time.monotonic() + 5
            while not path.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            result = subprocess.run([sys.executable, "-c", """
from pathlib import Path
from tinyassets.erasure_ledger import ErasureLedger
ledger = ErasureLedger(Path('/not-readable-by-broker'))
assert ledger.contains('alice')
assert not ledger.contains('bob')
assert ledger.fenced(['alice', 'bob']) == ['alice']
try:
    ledger.add('bob')
except RuntimeError:
    pass
else:
    raise AssertionError('broker wrote erasure authority')
"""], user=1002, group=1002, extra_groups=[1102], capture_output=True, text=True,
                env={**os.environ, "TINYASSETS_BOXES_RUN": directory}, timeout=10)
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            stop.set()
            server.join(5)
    assert not ledger.contains("bob")


def test_channel_closure_is_first_independent_retryable_ledger_phase(tmp_path, monkeypatch):
    from starlette.responses import JSONResponse

    from tinyassets import owner_router

    ledger = ErasureLedger(tmp_path)
    ledger.initialize()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    calls = []
    failing = [True]

    def close(*a, **kw):
        assert ledger.contains("alice")
        calls.append("channels")
        if failing[0]:
            raise OSError("broker temporarily unavailable")

    def resolve(*a, **kw):
        calls.append("resolve")
        raise LookupError("no box")

    monkeypatch.setattr("tinyassets.broker.account_erasure.close_channels", close)
    monkeypatch.setattr(owner_router, "resolve_box", resolve)
    for target, result in (
        ("tinyassets.broker.owner_identities.retire_owner", None),
        ("tinyassets.broker.account_erasure.erase_account", None),
        ("tinyassets.billing.stripe_adapter.cancel_account_billing", "cancelled"),
        ("tinyassets.router_credentials.revoke_account", None),
        ("tinyassets.router_account_deletion.delete_workos_user", "deleted"),
    ):
        monkeypatch.setattr(target, lambda *a, _result=result, **kw: _result)
    identity = Identity(user_id="alice", username="alice")
    response = asyncio.run(owner_router.delete_owner_account(identity))
    assert isinstance(response, JSONResponse) and response.status_code == 503
    assert calls == ["channels", "resolve"]
    assert ledger.receipt("alice")["unfinished_phases"] == ["broker_channels", "identity"]
    failing[0] = False
    assert asyncio.run(owner_router.delete_owner_account(identity)).status_code == 200
    assert calls == ["channels", "resolve", "channels"]
    assert not ledger.receipt("alice")["unfinished_phases"]


def test_relative_cleanup_relocation_continues_after_failure(tmp_path, monkeypatch):
    from tinyassets.restore_recovery import _schedule_trees, finish_erasures

    monkeypatch.setattr("tinyassets.boxes.destroy.verify_store", lambda *a: None)
    ledger = ErasureLedger(tmp_path)
    ledger.initialize()
    container = tmp_path / "box-store"
    host = tmp_path / "var/lib/boxhostd/owners"
    host.parent.mkdir(parents=True)
    container.mkdir()
    for uid, principal in ((300001, "failed"), (300002, "erased")):
        ledger.execute("add", {"digest": principal_digest(principal), "principal": principal,
                               "uids": [uid]})
        tree = container / str(uid)
        tree.mkdir(mode=0o700)
        (tree / "note").write_text("private")
        if principal == "erased":
            os.chown(tree, uid, uid)
    _schedule_trees(ledger, [container])
    assert all(work["store"] == "." and not work["quarantine"].startswith("/")
               for entry in ledger.entries() for work in entry["work"].values())
    container.rename(host)
    finish_erasures(tmp_path, host, daemons=recovery_daemons(host, monkeypatch))
    assert not (host / "300002").exists()
    assert ledger.receipt("erased")["home_removed"]
    assert not ledger.receipt("failed")["home_removed"]
    assert ledger.receipt("failed")["erasure_failures"]
    assert ledger.contains("failed") and not ledger.contains("other-owner")


@pytest.mark.parametrize("step", ["runtime", "admission", "quota", "relay", "socket", "secret"])
def test_recovery_never_finishes_failed_destroy_after_tree_success(tmp_path, monkeypatch, step):
    from pathlib import Path

    from tinyassets.boxes import destroy
    from tinyassets.restore_recovery import finish_erasures

    root, store = tmp_path / "data", tmp_path / "store"
    root.mkdir()
    store.mkdir()
    uid = 300001
    tree = store / str(uid)
    tree.mkdir(mode=0o700)
    os.chown(tree, uid, uid)
    (tree / "private").write_text("erase")
    monkeypatch.setattr(destroy, "verify_store", lambda *a: None)
    ledger = ErasureLedger(root)
    ledger.initialize()
    ledger.execute("add", {"digest": principal_digest("alice"),
                           "principal": "alice", "uids": [uid]})
    ledger.execute("work", {"digest": principal_digest("alice"), "target": {
        "store": ".", "uid": uid, "quarantine": ".quarantine/300001"}})
    daemons = recovery_daemons(store, monkeypatch)
    secrets = daemons.directory / "secrets"
    secrets.mkdir()
    secret = secrets / str(uid)
    secret.write_text("secret")
    failing = [True]
    target, method = {"runtime": (daemons.runtime, "stop"),
                      "admission": (daemons.admission, "retire"),
                      "quota": (daemons.quota, "destroy"),
                      "relay": (daemons.relays, "retire"),
                      "socket": (destroy, "socket_tree"),
                      "secret": (Path, "unlink")}[step]
    original = getattr(target, method)

    def fail(*args, **kw):
        if failing[0] and (step != "secret" or args[0] == secret):
            raise OSError("injected destroy failure")
        return original(*args, **kw)

    monkeypatch.setattr(target, method, fail)
    for _ in range(2):
        finish_erasures(root, store, daemons=daemons)
        assert not tree.exists()  # A later absent-tree success is insufficient.
        assert not ledger.receipt("alice")["home_removed"]
        assert ledger.entries()[0]["work"]
    failing[0] = False
    finish_erasures(root, store, daemons=daemons)
    assert ledger.receipt("alice")["home_removed"]
    assert not ledger.entries()[0]["work"]
    assert not secret.exists()


def test_root_admission_retirement_requires_fence_and_exact_reservation(tmp_path):
    from tinyassets.broker.owner_identities import OwnerIdentities
    from tinyassets.broker.server import BOX_HOST, _Connection

    ledger = ErasureLedger(tmp_path)
    ledger.initialize()
    state = tmp_path / "broker"
    state.mkdir(mode=0o700)
    identities = OwnerIdentities(state / "identities.db", initialize=True)
    alice = identities.resolve("alice", allocate=True).uid
    bob = identities.resolve("bob", allocate=True).uid
    identities.admission("admit", "alice", "a")
    server = SimpleNamespace(_data_root=tmp_path, _owner_identities=identities)

    async def exercise():
        channel = _Connection(server, None, BOX_HOST)

        async def call(owner, uid):
            await channel._connection_op("BOX_RETIRE_ADMISSION", {
                "op": "BOX_RETIRE_ADMISSION", "principal": owner, "uid": uid})
            from tinyassets import rpc_frames as rf

            return channel._queue.get_nowait() == rf.control(
                rf.CONNECTION, {"op": "BOX_ADMISSION_RETIRED"})

        assert not await call("alice", alice)
        ledger.add("alice")
        assert not await call("alice", bob)
        assert await call("alice", alice)
        assert identities.center_state("a") == "retired"
        assert await call("alice", alice)
        ledger.add("historical")
        assert not await call("historical", bob)
        assert await call("historical", 399998)

    asyncio.run(exercise())
