"""Exact pre-traffic rollback and acknowledged disaster safety proofs."""

import importlib.util
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from tinyassets.boxes.state import BoxHostState

SPEC = importlib.util.spec_from_file_location(
    "box_store_quotas", Path(__file__).resolve().parents[1] / "deploy/box-store-quotas.py")
restore = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restore)


def seed(root):
    (root / ".broker/state").mkdir(parents=True)
    (root / ".boxhost").mkdir(mode=0o700)
    (root / ".layout.json").write_text(json.dumps({"box_layout": "owner-box", "state": "stable"}))
    with sqlite3.connect(root / ".broker/state/owner-identities.db") as db:
        db.execute("CREATE TABLE owner_identities (principal TEXT, machine_id INT)")
        db.execute("INSERT INTO owner_identities VALUES ('alice', 300001)")
        db.execute("CREATE TABLE center_admissions (event TEXT, principal TEXT, center TEXT)")
        db.execute("INSERT INTO center_admissions VALUES ('admit', 'alice', 'a')")
        db.execute("CREATE TABLE box_records (machine INT, project_id INT, bhard_bytes INT)")
        db.execute("INSERT INTO box_records VALUES (300001,300001,1048576)")
        db.execute("CREATE TABLE owner_box_revocations (principal TEXT PRIMARY KEY, deleted INT)")
    with sqlite3.connect(root / ".broker/credential-custody.db") as db:
        db.execute("CREATE TABLE deleted_principals (digest TEXT PRIMARY KEY)")
    state = BoxHostState(root / ".boxhost/boxhost.db")
    state.begin("alice", "before-snapshot", "write", "digest1")
    state.finish("alice", "before-snapshot", {"response": "before"})
    state.bump_generation("alice")
    state.close()



def paired(tmp_path):
    from tinyassets.router_traffic import initialize
    current, staged = tmp_path / "current", tmp_path / "staged"
    live, trees = tmp_path / "live", tmp_path / "trees"
    seed(current)
    initialize(current)
    tree = live / "300001"
    tree.mkdir(parents=True)
    (tree / "note").write_text("backup")
    journal = BoxHostState(tree / ".owner-boxd/boxhost.db")
    journal.epoch("alice")
    journal.close()
    shutil.copytree(current, staged)
    shutil.copytree(live, trees)
    return current, staged, live, trees


def fingerprint(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_normal_restore_is_exact_and_does_not_carry_rows(tmp_path):
    current, staged, live, trees = paired(tmp_path)
    with sqlite3.connect(current / ".broker/state/owner-identities.db") as db:
        db.execute("INSERT INTO owner_box_revocations VALUES ('alice',1)")
    before = fingerprint(staged), fingerprint(trees)
    restore.fence_restore(current, staged, cutover_store=live, restored_store=trees)
    assert (fingerprint(staged), fingerprint(trees)) == before


@pytest.mark.parametrize("damage", ["traffic", "missing_marker", "corrupt_marker",
                                   "missing_store", "corrupt_store", "missing_tree",
                                   "missing_journal", "corrupt_journal", "empty_journal"])
def test_normal_refuses_before_touching_staging(tmp_path, damage):
    from tinyassets.router_traffic import open_traffic
    current, staged, live, trees = paired(tmp_path)
    if damage == "traffic":
        open_traffic(current)
    else:
        path = {"marker": current / ".router-traffic.sqlite3",
                "store": current / ".broker/credential-custody.db",
                "tree": live / "300001",
                "journal": live / "300001/.owner-boxd/boxhost.db"}[damage.split("_")[1]]
        if damage.startswith("empty"):
            path.write_bytes(b"")
        elif damage.startswith("corrupt"):
            path.write_bytes(b"unreadable")
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    before = fingerprint(staged), fingerprint(trees)
    with pytest.raises((ValueError, OSError, sqlite3.DatabaseError)):
        restore.fence_restore(current, staged, cutover_store=live, restored_store=trees)
    assert (fingerprint(staged), fingerprint(trees)) == before


def disaster(current, staged, trees, monkeypatch, **kwargs):
    from tinyassets.restore_recovery import disaster_restore
    monkeypatch.setattr("os.chown", lambda *a, **k: None)
    return disaster_restore(current, staged, trees, backup_time=1, now=2000000000,
                            identity_exists=lambda owner: True,
                            billing_sync=lambda root, now: {"fixture": True}, **kwargs)


def test_disaster_stale_key_session_hook_and_secret_all_fail(tmp_path, monkeypatch):
    import hashlib

    from tinyassets.api_keys import ApiKeyStore
    from tinyassets.onboarding import owner_sessions
    from tinyassets.router_credentials import HookOwners
    current, staged, live, trees = paired(tmp_path)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(staged))
    key = ApiKeyStore(staged).mint("alice", name="fixture", scopes=[], authority=None)["key"]
    HookOwners(staged).publish(hashlib.sha256(b"hook").hexdigest(), "alice")
    with owner_sessions.store() as db:
        db.execute("INSERT INTO owner_sessions VALUES (?,?,?)",
                   (owner_sessions.hashed("session"), json.dumps({"user_id": "alice"}), 3000000000))
    with sqlite3.connect(staged / ".broker/state/owner-identities.db") as db:
        db.execute("CREATE TABLE owner_box_secrets (principal TEXT PRIMARY KEY, secret TEXT)")
        db.execute("INSERT INTO owner_box_secrets VALUES ('alice','old-secret')")
    assert ApiKeyStore(staged).resolve(key) is not None
    assert HookOwners(staged).resolve("hook") == "alice"
    assert owner_sessions.lookup("session") is not None
    report = disaster(current, staged, trees, monkeypatch)
    assert ApiKeyStore(staged).resolve(key) is None
    assert HookOwners(staged).resolve("hook") is None
    assert owner_sessions.lookup("session") is None
    with sqlite3.connect(staged / ".broker/state/owner-identities.db") as db:
        assert db.execute("SELECT secret FROM owner_box_secrets").fetchone()[0] != "old-secret"
    assert report["credential_roots_reset"] and report["operation_fences_reset"]
    assert report["spend_blocked_until"] > report["restore_time"]


def test_disaster_refuses_write_bash_and_broker_outbound_replay(tmp_path, monkeypatch):
    from tinyassets.boxes.boxd import OwnerBoxd
    from tinyassets.boxes.provider import BoxOperationRefused, StaleHandle
    from tinyassets.broker.ops import OpStore, new_op_id
    current, staged, live, trees = paired(tmp_path)
    core = OwnerBoxd(owner_id="alice", owner_root=live / "300001",
                     state_dir=live / "300001/.owner-boxd")
    old = core.bind("alice")
    core.write(old, "after-snapshot", "/cc/note", b"live", max_bytes=100)
    core.consume_operation(old, "old-bash", "bash", {"command": "echo once"})
    core.close()
    old_outbound = new_op_id(lambda: 1999999999)
    disaster(current, staged, trees, monkeypatch)
    core = OwnerBoxd(owner_id="alice", owner_root=trees / "300001",
                     state_dir=trees / "300001/.owner-boxd")
    try:
        with pytest.raises(StaleHandle):
            core.write(old, "after-snapshot", "/cc/note", b"bad", max_bytes=100)
        handle = core.bind("alice")
        with pytest.raises(BoxOperationRefused, match="not re-run"):
            core.write(handle, "after-snapshot", "/cc/note", b"bad", max_bytes=100)
        with pytest.raises(BoxOperationRefused, match="not re-run"):
            core.consume_operation(handle, "old-bash", "bash", {"command": "echo once"})
        assert (trees / "300001/note").read_text() == "backup"
        core.write(handle, f"{handle.epoch}:fresh", "/cc/note", b"fresh", max_bytes=100)
    finally:
        core.close()
    ops = OpStore(staged / ".broker/ops.db", clock=lambda: 2000000001)
    assert ops.admit("outbound", old_outbound, "digest").kind == "expired"
    assert ops.admit("outbound", new_op_id(lambda: 2000000301), "digest").kind == "new"


def test_future_dated_consumed_operation_cannot_replay_after_restore(tmp_path, monkeypatch):
    from tinyassets.broker.ops import MAX_FUTURE_SKEW_S, OpStore, new_op_id
    from tinyassets.restore_recovery import fence_ops

    current, staged, live, trees = paired(tmp_path)
    monkeypatch.setattr("os.chown", lambda *args: None)
    start = 2000000000
    op_id = new_op_id(lambda: start + 240)
    live_ops = OpStore(current / ".broker/ops.db", clock=lambda: start)
    assert live_ops.admit("outbound", op_id, "digest").kind == "new"
    live_ops.mark_may_have_sent("outbound", op_id)
    live_ops.finish("outbound", op_id, "completed")
    # The snapshot predates the receipt, so only the restored cutoff can refuse it.
    fence_ops(staged, trees, start + 60)
    restored = OpStore(staged / ".broker/ops.db", clock=lambda: start + 60)
    assert restored.admit("outbound", op_id, "digest").kind == "expired"
    boundary = new_op_id(lambda: start + 60 + MAX_FUTURE_SKEW_S)
    assert restored.admit("outbound", boundary, "digest").kind == "expired"


@pytest.mark.parametrize("damage", ["missing", "corrupt", "empty", "integrity"])
@pytest.mark.parametrize("placement", ["router", "owner"])
def test_normal_checks_live_catalog_stores_absent_from_backup(tmp_path, damage, placement):
    from tinyassets.owner_lease import register_store

    current, staged, live, trees = paired(tmp_path)
    root = current if placement == "router" else live / "300001"
    path = root / "new.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE records (value TEXT CHECK(length(value)>0))")
        db.execute("INSERT INTO records VALUES ('valid')")
    register_store(root, path, "agent_turn_journal")
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_bytes(b"corrupt")
    elif damage == "empty":
        path.write_bytes(b"")
    else:
        with sqlite3.connect(path) as db:
            db.execute("CREATE INDEX drift ON records(value) WHERE 0")
            db.execute("PRAGMA writable_schema=ON")
            db.execute("UPDATE sqlite_master SET sql='CREATE INDEX drift ON records(value)' "
                       "WHERE name='drift'")
    before = fingerprint(staged), fingerprint(trees)
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        restore.fence_restore(current, staged, cutover_store=live, restored_store=trees)
    assert (fingerprint(staged), fingerprint(trees)) == before


@pytest.mark.parametrize("placement", ["router", "owner"])
def test_normal_resolves_registered_runtime_mount_paths(tmp_path, placement):
    from tinyassets.owner_lease import lease_db

    current, staged, live, trees = paired(tmp_path)
    root = current if placement == "router" else live / "300001"
    with sqlite3.connect(root / "new.db") as db:
        db.execute("CREATE TABLE records (value TEXT)")
    mount = "/data" if placement == "router" else "/home/owner"
    with lease_db(root) as db:
        db.execute("INSERT INTO owner_store_catalog VALUES (?,?,?)",
                   (mount + "/new.db", "agent_turn_journal", "now"))
    before = fingerprint(staged), fingerprint(trees)
    restore.fence_restore(current, staged, cutover_store=live, restored_store=trees)
    assert (fingerprint(staged), fingerprint(trees)) == before


def test_normal_preserves_uploaded_files_with_database_suffixes(tmp_path):
    current, staged, live, trees = paired(tmp_path)
    for root in (live, trees):
        (root / "300001/upload.db").write_bytes(b"verbatim uploaded file")
    before = fingerprint(staged), fingerprint(trees)
    restore.fence_restore(current, staged, cutover_store=live, restored_store=trees)
    assert (fingerprint(staged), fingerprint(trees)) == before


def test_disaster_revokes_every_surviving_owner_and_fails_closed(tmp_path, monkeypatch):
    from tinyassets.router_account_deletion import AccountDeletionError

    current, staged, live, trees = paired(tmp_path)
    calls = []
    def revoke(owner):
        calls.append(owner)
        raise AccountDeletionError("provider unavailable")
    with pytest.raises(AccountDeletionError):
        disaster(current, staged, trees, monkeypatch, revoke_sessions=revoke)
    assert calls == ["alice"]
    assert not (staged / ".disaster-restore.json").exists()
    report = disaster(current, staged, trees, monkeypatch, revoke_sessions=calls.append)
    assert calls == ["alice", "alice"]
    assert report["owners_reconciled"] == 1


@pytest.mark.parametrize("failure", [None, "list", "revoke"])
def test_workos_revokes_all_sessions_through_paginated_provider_api(monkeypatch, failure):
    import io
    import urllib.error

    from tinyassets.router_account_deletion import AccountDeletionError, revoke_workos_sessions

    monkeypatch.setenv("WORKOS_API_KEY", "synthetic")
    revoked = []
    def request(req, timeout):
        assert timeout == 20
        if (failure == "list" and req.get_method() == "GET"
                or failure == "revoke" and req.get_method() == "POST"):
            raise urllib.error.URLError("unavailable")
        if req.get_method() == "POST":
            revoked.append(json.loads(req.data)["session_id"])
            response = io.BytesIO(b"")
            response.status = 204
        else:
            assert "/users/user_fixture/sessions?" in req.full_url
            last = "after=session_first" in req.full_url
            response = io.BytesIO(json.dumps({
                "data": [{"id": "session_second" if last else "session_first"}],
                "list_metadata": {"after": None if last else "session_first"}}).encode())
            response.status = 200
        return response
    if failure:
        with pytest.raises(AccountDeletionError):
            revoke_workos_sessions("user_fixture", request=request)
    else:
        revoke_workos_sessions("user_fixture", request=request)
        assert revoked == ["session_first", "session_second"]


@pytest.mark.parametrize("source", ["router", "broker", "custody", "host", "workos"])
def test_disaster_erasure_union_and_identity_reconciliation(tmp_path, monkeypatch, source):
    monkeypatch.setattr("os.chown", lambda *args, **kwargs: None)
    import hashlib

    from tinyassets.restore_recovery import erasures
    from tinyassets.router_credentials import DeletedOwners
    current, staged, live, trees = paired(tmp_path)
    if source == "router":
        DeletedOwners(current).add("alice", {})
    elif source == "broker":
        with sqlite3.connect(current / ".broker/state/owner-identities.db") as db:
            db.execute("INSERT INTO owner_box_revocations VALUES ('alice',1)")
    elif source == "custody":
        with sqlite3.connect(current / ".broker/credential-custody.db") as db:
            db.execute("INSERT INTO deleted_principals VALUES (?)",
                       (hashlib.sha256(b"alice").hexdigest(),))
    elif source == "host":
        state = BoxHostState(current / ".boxhost/boxhost.db")
        state.begin_owner_destroy("alice", "erase", 300001, "a")
        state.close()
    assert erasures(current, staged, identity_exists=lambda owner: source != "workos") == {300001}
    assert DeletedOwners(staged).contains("alice")


def test_workos_seam_distinguishes_absent_from_unavailable(monkeypatch):
    import urllib.error

    from tinyassets.router_account_deletion import AccountDeletionError, workos_user_exists
    monkeypatch.setenv("WORKOS_API_KEY", "synthetic")
    def missing(req, **kwargs):
        raise urllib.error.HTTPError(req.full_url, 404, "missing", {}, None)
    def unavailable(req, **kwargs):
        raise TimeoutError()
    assert not workos_user_exists("user_fixture", request=missing)
    with pytest.raises(AccountDeletionError):
        workos_user_exists("user_fixture", request=unavailable)


def test_disaster_retains_unmapped_erasure_digests_despite_unreadable_live_source(
    tmp_path, monkeypatch,
):
    import hashlib

    from tinyassets.restore_recovery import erasures
    current, staged, live, trees = paired(tmp_path)
    digest = hashlib.sha256(b"owner-created-after-backup").hexdigest()
    with sqlite3.connect(current / ".broker/credential-custody.db") as db:
        db.execute("INSERT INTO deleted_principals VALUES (?)", (digest,))
    (current / ".router-deleted-owners.sqlite3").write_bytes(b"damaged")
    monkeypatch.setattr("os.chown", lambda *args: None)
    assert erasures(current, staged, identity_exists=lambda owner: True) == set()
    with sqlite3.connect(staged / ".broker/credential-custody.db") as db:
        assert db.execute("SELECT digest FROM deleted_principals").fetchall() == [(digest,)]


def test_only_root_can_open_router_traffic(tmp_path, monkeypatch):
    import asyncio

    from tinyassets import router_credentials, rpc_frames
    from tinyassets.router_traffic import initialize, state
    initialize(tmp_path)
    caller = [1002]
    monkeypatch.setattr("tinyassets.broker.server.peer_uid", lambda sock: caller[0])

    async def run():
        async with router_credentials.serving(tmp_path):
            for uid in (1002, 0):
                caller[0] = uid
                reader, writer = await asyncio.open_unix_connection(
                    str(router_credentials.credential_socket(tmp_path)))
                writer.write(rpc_frames.control(rpc_frames.CONNECTION, {"op": "OPEN_TRAFFIC"}))
                await writer.drain()
                answer = (await rpc_frames.read_frame(reader)).control()
                if uid:
                    assert answer == {"op": "ROUTER_CREDENTIAL_REFUSED"}
                    assert state(tmp_path)[0] is None
                else:
                    assert answer == {"op": "TRAFFIC_OPENED", "traffic_opened": True}
                    assert state(tmp_path)[0] is not None
                writer.close()
                await writer.wait_closed()
    asyncio.run(run())


def test_router_cutover_gate_and_external_token_floor(tmp_path, monkeypatch):
    from tinyassets.router_traffic import accepts_issued_at, admits, initialize, open_traffic, state
    initialize(tmp_path)
    monkeypatch.setenv("TINYASSETS_CANARY_USER_ID", "canary")
    assert admits(tmp_path, "canary")
    assert not admits(tmp_path, "alice")
    assert state(tmp_path)[0] is None
    open_traffic(tmp_path)
    first = state(tmp_path)[0]
    assert admits(tmp_path, "alice")
    open_traffic(tmp_path)
    assert state(tmp_path)[0] == first
    with sqlite3.connect(tmp_path / ".router-traffic.sqlite3") as db:
        db.execute("UPDATE traffic SET credentials_after=100")
    assert not accepts_issued_at(tmp_path, 99)
    assert not accepts_issued_at(tmp_path, None)
    assert accepts_issued_at(tmp_path, 101)


@pytest.mark.parametrize("member", ["directory", "database", "sidecar"])
def test_normal_refuses_linked_journal_members(tmp_path, member):
    current, staged, live, trees = paired(tmp_path)
    directory = live / "300001/.owner-boxd"
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    if member == "directory":
        directory.rename(directory.with_name("saved"))
        directory.symlink_to(directory.with_name("saved"), target_is_directory=True)
    else:
        path = directory / ("boxhost.db" if member == "database" else "boxhost.db-wal")
        path.unlink(missing_ok=True)
        path.symlink_to(outside)
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        restore.fence_restore(current, staged, cutover_store=live, restored_store=trees)
    assert outside.read_bytes() == b"outside"


def test_disaster_fences_external_receipts_and_daily_spend(tmp_path, monkeypatch):
    from tinyassets.broker.policy import PolicyRefused, PolicyStore
    from tinyassets.restore_recovery import fence_ops
    from tinyassets.storage.external_write_receipts import try_reserve_receipt
    current, staged, live, trees = paired(tmp_path)
    center = trees / "300001/a"
    center.mkdir()
    assert try_reserve_receipt(center, idempotency_hint="old", sink="fixture",
                               run_id="opaque-before")["status"] == "reserved"
    monkeypatch.setattr("os.chown", lambda *a, **k: None)
    fence_ops(staged, trees, 2000000000)
    with pytest.raises(ValueError, match="restore epoch"):
        try_reserve_receipt(center, idempotency_hint="absent-from-backup", sink="fixture",
                            run_id="opaque-after-backup")
    assert try_reserve_receipt(center, idempotency_hint="new", sink="fixture",
                               run_id="2000000000001:new")["status"] == "reserved"
    hold = staged / ".broker/restore-spend-until"
    hold.write_text("2000073600")
    policy = PolicyStore(staged, None)
    monkeypatch.setattr("tinyassets.broker.policy.time.time", lambda: 2000000000)
    with pytest.raises(PolicyRefused, match="daily spend"):
        policy.decide("alice", center="a", action_class="spend", amount=1, request_id="new")
    assert policy.spend_blocked()
    monkeypatch.setattr("tinyassets.broker.policy.time.time", lambda: 2000073600)
    assert not policy.spend_blocked()


def test_disaster_drops_provider_custody_and_outbound_authority(tmp_path, monkeypatch):
    current, staged, live, trees = paired(tmp_path)
    with sqlite3.connect(staged / ".broker/credential-custody.db") as db:
        db.execute("CREATE TABLE llm_credential_custody (secret TEXT)")
        db.execute("INSERT INTO llm_credential_custody VALUES ('external-refresh-token')")
    with sqlite3.connect(staged / ".broker/outbound.db") as db:
        for table in ("outbound_connections", "outbound_connection_grants",
                      "connection_capabilities", "browser_vault"):
            db.execute(f"CREATE TABLE {table} (authority TEXT)")
            db.execute(f"INSERT INTO {table} VALUES ('external-authority')")
    disaster(current, staged, trees, monkeypatch)
    with sqlite3.connect(staged / ".broker/credential-custody.db") as db:
        assert db.execute("SELECT * FROM llm_credential_custody").fetchall() == []
    with sqlite3.connect(staged / ".broker/outbound.db") as db:
        for table in ("outbound_connections", "outbound_connection_grants",
                      "connection_capabilities", "browser_vault"):
            assert db.execute(f"SELECT * FROM {table}").fetchall() == []


@pytest.mark.parametrize("relative", [".router-traffic.sqlite3", ".broker/ops.db",
                                     ".broker/plans.db", ".broker/restore-spend-until",
                                     ".disaster-restore.json"])
def test_disaster_never_follows_staged_authority_links(tmp_path, monkeypatch, relative):
    current, staged, live, trees = paired(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    path = staged / relative
    path.unlink(missing_ok=True)
    path.symlink_to(outside)
    before = fingerprint(current), fingerprint(live)
    with pytest.raises(ValueError, match="symlink"):
        disaster(current, staged, trees, monkeypatch)
    assert outside.read_bytes() == b"untouched"
    assert (fingerprint(current), fingerprint(live)) == before


@pytest.mark.parametrize("path,method,status", [
    ("/app/token", "POST", 503), ("/app/owner-sign-in", "GET", 503),
    ("/app/billing/webhook", "POST", 503), ("/authorize", "GET", 503),
    ("/token", "POST", 503), ("/app", "POST", 503),
    ("/app", "GET", 200), ("/app/m/build/main.js", "GET", 200),
    ("/app/canary-sign-in", "POST", 200),
])
def test_closed_admission_blocks_anonymous_writers(tmp_path, monkeypatch, path, method, status):
    import asyncio

    import httpx
    from starlette.responses import JSONResponse

    from tinyassets.owner_router import OwnerRouter
    from tinyassets.router_traffic import initialize, open_traffic, state

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TINYASSETS_CANARY_USER_ID", raising=False)
    initialize(tmp_path)
    entered = []

    async def shared(scope, receive, send):
        entered.append(scope["path"])
        await JSONResponse({"ok": True})(scope, receive, send)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(OwnerRouter(shared)),
                                     base_url="http://test") as client:
            response = await client.request(method, path)
            assert response.status_code == status
            assert entered == ([path] if status == 200 else [])
            assert state(tmp_path)[0] is None
            open_traffic(tmp_path)
            assert (await client.request(method, path)).status_code == 200

    asyncio.run(exercise())
