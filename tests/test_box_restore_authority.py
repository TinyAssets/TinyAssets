"""Exact pre-traffic rollback and acknowledged disaster safety proofs."""

import importlib.util
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from tests.test_owner_cutover_census import source as source
from tinyassets.boxes.layout import OWNER_BOX_MIGRATION
from tinyassets.boxes.state import BoxHostState

SPEC = importlib.util.spec_from_file_location(
    "box_store_quotas", Path(__file__).resolve().parents[1] / "deploy/box-store-quotas.py")
restore = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restore)


def seed(root):
    (root / ".broker/state").mkdir(parents=True)
    from tinyassets.erasure_ledger import ErasureLedger

    ErasureLedger(root).initialize()
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




def paired(tmp_path):
    from tinyassets.owner_lease import lease_db
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
    for root in (current, tree):
        with lease_db(root):
            pass
    shutil.copytree(current, staged)
    shutil.copytree(live, trees)
    return current, staged, live, trees


@pytest.mark.parametrize("caller", ["seed", "rollback"])
def test_non_index_fault_after_110_damaged_indexes_refuses_before_erasure(source, tmp_path, caller):
    from tests.test_cutover_index_integrity import damage_many_indexes
    from tinyassets.erasure_ledger import ErasureLedger, seed_legacy
    from tinyassets.restore_recovery import normal_preflight

    damage_many_indexes(source / ".tinyassets.db", corrupt_table=True)
    before = fingerprint(source)
    if caller == "seed":
        ledger_root = tmp_path / "ledger"
        ledger_root.mkdir()
        ledger = ErasureLedger(ledger_root)
        ledger.initialize()
        with pytest.raises(ValueError, match="not index-only"):
            seed_legacy(source, ledger, store_root=tmp_path)
        assert ledger.entries() == []
    else:
        current, _, live, _ = paired(tmp_path / "pair")
        live_before = fingerprint(current), fingerprint(live)
        with pytest.raises(ValueError, match="not index-only"):
            normal_preflight(current, source, live, backup_time=1)
        assert (fingerprint(current), fingerprint(live)) == live_before
    assert fingerprint(source) == before


@pytest.mark.parametrize("many", [False, True])
def test_rollback_repairs_index_only_drift(source, tmp_path, many):
    from contextlib import closing

    from tests.test_cutover_index_integrity import corrupt_index, damage_many_indexes
    from tinyassets.restore_recovery import normal_preflight
    from tinyassets.sqlite_integrity import check

    current, _, live, _ = paired(tmp_path / "pair")
    live_before = fingerprint(current), fingerprint(live)
    path = source / ".tinyassets.db"
    if many:
        damage_many_indexes(path, corrupt_table=False)
    else:
        corrupt_index(path)
    normal_preflight(current, source, live, backup_time=1)
    with closing(sqlite3.connect(path)) as db:
        assert check(db) == []
        assert db.execute("SELECT count(*) FROM agent_turns").fetchone() == (4,)
    assert (fingerprint(current), fingerprint(live)) == live_before


def fingerprint(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_normal_restore_carries_erasure_only(tmp_path):
    current, staged, live, trees = paired(tmp_path)
    from tinyassets.erasure_ledger import ErasureLedger

    ErasureLedger(current).add("alice")
    before = fingerprint(staged), fingerprint(trees)
    restore.fence_restore(current, staged, cutover_store=live, restored_store=trees)
    from tinyassets.erasure_ledger import NAME, ErasureLedger

    assert ErasureLedger(staged).contains("alice")
    assert ({k: v for k, v in fingerprint(staged).items() if k != NAME},
            fingerprint(trees)) == ({k: v for k, v in before[0].items() if k != NAME}, before[1])


@pytest.mark.parametrize("layout", [None, {"split": "owner-split", "state": "stable"}])
def test_pre_cutover_pair_is_not_a_disaster_source(tmp_path, layout):
    current, staged, live, trees = paired(tmp_path)
    marker = staged / ".layout.json"
    marker.unlink() if layout is None else marker.write_text(json.dumps(layout))
    with pytest.raises(ValueError, match="pre-cutover backup.*requires forward migration"):
        restore.fence_restore(current, staged, cutover_store=live, restored_store=trees,
                              disaster=True)


def test_clear_retained_quotas_needs_no_registry_for_post_backup_owner(tmp_path, monkeypatch):
    current, staged, live, trees = paired(tmp_path)
    (live / "300002").mkdir()
    commands = []
    monkeypatch.setattr(restore.subprocess, "run", lambda args, **kw: commands.append(args))
    monkeypatch.setattr(restore, "verify_tree", lambda *args: None)
    # Even a missing live registry cannot turn retained content into authority.
    restore.reapply(live, live, tmp_path / "absent-registry", clear=True)
    assert {command[3].split()[-1] for command in commands} == {"300001", "300002"}


def test_disaster_erases_post_backup_identity(tmp_path, monkeypatch):
    from tinyassets.erasure_ledger import ErasureLedger

    current, staged, live, trees = paired(tmp_path)
    with sqlite3.connect(current / ".broker/state/owner-identities.db") as db:
        db.execute("INSERT INTO owner_identities VALUES ('later',300002)")
    report = disaster(current, staged, trees, monkeypatch)
    assert ErasureLedger(staged).contains("later")
    assert report["erased_owners"] == 1
    assert report["owners_reconciled"] == 1
    with sqlite3.connect(staged / ".broker/state/owner-identities.db") as db:
        assert db.execute("SELECT machine_id FROM owner_identities WHERE principal='later'"
                          ).fetchone() == (300002,)


def test_completed_restore_never_rewrites_later_deletion_receipts(tmp_path, monkeypatch):
    from tinyassets.erasure_ledger import ErasureLedger
    from tinyassets.restore_recovery import finish_erasures

    current, staged, live, trees = paired(tmp_path)
    disaster(current, staged, trees, monkeypatch)
    finish_erasures(staged, live)
    deleted = ErasureLedger(staged)
    receipt = {"home_removed": False, "unfinished_phases": ["owner_tree"],
               "completed_phases": ["billing", "identity"]}
    deleted.add("alice", receipt)
    receipt = deleted.receipt("alice")
    before = fingerprint(staged)
    finish_erasures(staged, live)
    assert deleted.receipt("alice") == receipt
    assert fingerprint(staged) == before


def test_missing_owner_store_does_not_complete_published_erasures(tmp_path, monkeypatch):
    from tinyassets.erasure_ledger import ErasureLedger
    from tinyassets.restore_recovery import finish_erasures

    current, staged, live, trees = paired(tmp_path)
    ErasureLedger(current).add("alice", {})
    disaster(current, staged, trees, monkeypatch)
    detached = tmp_path / "detached-store"
    live.rename(detached)
    before = fingerprint(staged), fingerprint(detached)
    finish_erasures(staged, live)
    assert not live.exists()
    assert fingerprint(detached) == before[1]
    assert ErasureLedger(staged).receipt("alice")["erasure_failures"]
    assert any(entry["work"] for entry in ErasureLedger(staged).pending())


def test_disaster_reports_unreadable_live_identity_source(tmp_path, monkeypatch):
    current, staged, live, trees = paired(tmp_path)
    (current / ".broker/state/owner-identities.db").write_bytes(b"damaged platform registry")
    report = disaster(current, staged, trees, monkeypatch)
    assert "live owner identities" in report["unavailable_erasure_sources"]
    assert report["owners_reconciled"] == 1
    assert (trees / "300001/note").read_text() == "backup"


@pytest.mark.parametrize("damage", ["traffic", "missing_marker", "corrupt_marker"])
def test_normal_refuses_before_touching_staging(tmp_path, damage):
    from tinyassets.router_traffic import open_traffic
    current, staged, live, trees = paired(tmp_path)
    if damage == "traffic":
        open_traffic(current)
    else:
        path = current / ".router-traffic.sqlite3"
        if damage.startswith("corrupt"):
            path.write_bytes(b"unreadable")
        else:
            path.unlink()
    before = fingerprint(staged), fingerprint(trees)
    with pytest.raises((ValueError, OSError, sqlite3.DatabaseError)):
        restore.fence_restore(current, staged, cutover_store=live, restored_store=trees)
    assert (fingerprint(staged), fingerprint(trees)) == before


@pytest.mark.parametrize("marker", ["missing", "corrupt"])
@pytest.mark.parametrize("layout", ["missing", "corrupt", "unfinished"])
def test_normal_marker_requires_unfinished_layout(tmp_path, marker, layout):
    current, staged, live, trees = paired(tmp_path)
    traffic = current / ".router-traffic.sqlite3"
    traffic.unlink() if marker == "missing" else traffic.write_bytes(b"corrupt")
    path = current / ".layout.json"
    if layout == "missing":
        path.unlink()
    elif layout == "corrupt":
        path.write_text("corrupt")
    else:
        path.write_text(json.dumps({"state": "migrating", "migration": OWNER_BOX_MIGRATION,
                                    "box_layout": "owner-split"}))
    if layout == "unfinished":
        restore.fence_restore(current, staged, cutover_store=live, restored_store=trees)
    else:
        with pytest.raises(ValueError, match="disaster path"):
            restore.fence_restore(current, staged, cutover_store=live, restored_store=trees)


@pytest.mark.parametrize("opened,credentials", [(1, 0), (3, 0), (None, 1)])
def test_normal_refuses_traffic_regardless_of_backup_age(tmp_path, opened, credentials):
    current, staged, live, trees = paired(tmp_path)
    with sqlite3.connect(current / ".router-traffic.sqlite3") as db:
        db.execute("UPDATE traffic SET traffic_opened_at=?,credentials_after=?",
                   (opened, credentials))
    with pytest.raises(ValueError, match="disaster path"):
        restore.fence_restore(current, staged, cutover_store=live, restored_store=trees,
                              backup_time=2)


OWNER_CONTENT = ["arbitrary/deep/planted.db", ".owner_leases.db", ".tinyassets.db",
                 ".owner-boxd/boxhost.db", ".remote-ta-receipts.sqlite3",
                 ".external_write_receipts.db", "a/.external_write_receipts.db",
                 "a/.agent-workspace/.tmp/project/.owner_leases.db"]


def test_normal_never_opens_owner_or_sidecar_content(tmp_path, monkeypatch):
    from tinyassets.restore_recovery import normal_preflight

    current, staged, live, trees = paired(tmp_path)
    for root, relatives in ((live / "300001", OWNER_CONTENT), (current, [
            ".agent-sessions/a/planted.db", ".published/planted.db",
            ".universe-sidecars/a/starter-seeds.sqlite3", ".owner_leases.db"])):
        for relative in relatives:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"planted corrupt database")
    real_open, real_connect = Path.open, sqlite3.connect

    def only_marker(path, *args, **kwargs):
        from urllib.parse import unquote, urlsplit

        opened = Path(unquote(urlsplit(str(path)).path))
        if opened.is_relative_to(staged):
            return real_connect(path, *args, **kwargs)
        assert opened.name in {
            ".router-traffic.sqlite3", "credential-custody.db", "owner-identities.db",
            "boxhost.db", ".erasure-ledger.sqlite3"}
        return real_connect(path, *args, **kwargs)

    def only_layout(path, *args, **kwargs):
        if path.is_relative_to(staged):
            # The extracted stage must be checked/repaired before publication.
            return real_open(path, *args, **kwargs)
        if path.is_relative_to(tmp_path):
            assert any(path in {root / name for name in (
                ".layout.json", ".router-traffic.sqlite3", ".broker/credential-custody.db",
                ".broker/state/owner-identities.db", ".boxhost/boxhost.db")}
                for root in (current, staged))
        else:
            assert path.name in {".router-traffic.sqlite3", "credential-custody.db",
                                 "owner-identities.db", "boxhost.db"}
            assert path.parent.name.startswith("tinyassets-sqlite-read-")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", only_marker)
    monkeypatch.setattr(Path, "open", only_layout)
    monkeypatch.setattr(Path, "iterdir", lambda path: pytest.fail("live enumeration"))
    normal_preflight(current, staged, live, backup_time=1, restored_owners=trees)


@pytest.mark.parametrize("relative", OWNER_CONTENT)
def test_disaster_ignores_corrupt_owner_content_and_fences_replay(tmp_path, monkeypatch, relative):
    from tinyassets.boxes.boxd import OwnerBoxd
    from tinyassets.boxes.provider import BoxOperationRefused

    current, staged, live, trees = paired(tmp_path)
    core = OwnerBoxd(owner_id="alice", owner_root=trees / "300001",
                     state_dir=trees / "300001/.owner-boxd")
    old_id = core.operation_prefix() + "old-operation"
    for root in (live, trees):
        path = root / "300001" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"planted corrupt database")
    before = fingerprint(trees)
    report = disaster(current, staged, trees, monkeypatch)
    assert report["operation_fences_reset"]  # the shell's completed exit-6 preparation
    assert fingerprint(trees) == before
    core._restore_epoch = (staged / ".restore-epoch").read_text()
    try:
        with pytest.raises(BoxOperationRefused, match="not re-run"):
            core._begin("alice", old_id, "write", {})
    finally:
        core.close()


def disaster(current, staged, trees, monkeypatch, **kwargs):
    from tinyassets.restore_recovery import disaster_restore
    monkeypatch.setattr("os.chown", lambda *a, **k: None)
    return disaster_restore(current, staged, trees, backup_time=1, now=2000000000,
                            live_trees=trees.parent / "live",
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
    from tinyassets.boxes.provider import BoxOperationRefused
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
                     state_dir=trees / "300001/.owner-boxd",
                     restore_epoch=(staged / ".restore-epoch").read_text())
    try:
        with pytest.raises(BoxOperationRefused):
            core.write(old, "after-snapshot", "/cc/note", b"bad", max_bytes=100)
        handle = core.bind("alice")
        with pytest.raises(BoxOperationRefused, match="not re-run"):
            core.write(handle, "after-snapshot", "/cc/note", b"bad", max_bytes=100)
        with pytest.raises(BoxOperationRefused, match="not re-run"):
            core.consume_operation(handle, "old-bash", "bash", {"command": "echo once"})
        assert (trees / "300001/note").read_text() == "backup"
        core.write(handle, core.operation_prefix() + "fresh", "/cc/note", b"fresh", max_bytes=100)
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


@pytest.mark.parametrize("source", ["ledger", "workos"])
def test_disaster_erasure_union_and_identity_reconciliation(tmp_path, source):
    from tinyassets.erasure_ledger import ErasureLedger
    from tinyassets.restore_recovery import erasures

    current, staged, live, trees = paired(tmp_path)
    if source == "ledger":
        ErasureLedger(current).add("alice")
    assert erasures(current, staged, identity_exists=lambda owner: source != "workos") == {300001}
    assert ErasureLedger(staged).contains("alice")


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
    from tinyassets.erasure_ledger import ErasureLedger

    ErasureLedger(current).execute("add", {"digest": digest})
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


@pytest.mark.parametrize("placement", ["root", "center"])
def test_disaster_fences_external_receipts_and_daily_spend(tmp_path, monkeypatch, placement):
    from tinyassets.broker.policy import PolicyRefused, PolicyStore
    from tinyassets.restore_recovery import fence_ops
    from tinyassets.storage.external_write_receipts import try_reserve_receipt
    current, staged, live, trees = paired(tmp_path)
    workspace = trees / "300001/a/.agent-workspace/.tmp/project"
    workspace.mkdir(parents=True)
    for name in (".remote-ta-receipts.sqlite3", ".external_write_receipts.db"):
        (workspace / name).write_bytes(b"user content, not receipt authority")
    before = fingerprint(workspace)
    center = trees / ("300001/a" if placement == "center" else "300001")
    assert try_reserve_receipt(center, idempotency_hint="old", sink="fixture",
                               run_id="opaque-before")["status"] == "reserved"
    monkeypatch.setattr("os.chown", lambda *a, **k: None)
    fence_ops(staged, trees, 2000000000)
    assert fingerprint(workspace) == before
    import os

    from tinyassets.boxes.boxd import OwnerBoxd
    from tinyassets.owner_runtime import OwnerRuntime

    core = OwnerBoxd(owner_id="alice", owner_root=trees / "300001",
                     state_dir=trees / "300001/.owner-boxd",
                     restore_epoch=(staged / ".restore-epoch").read_text())
    monkeypatch.setattr("tinyassets.owner_runtime._runtime",
                        OwnerRuntime("alice", 300001, trees / "300001", os.getpid(), core))
    with pytest.raises(ValueError, match="restore epoch"):
        try_reserve_receipt(center, idempotency_hint="absent-from-backup", sink="fixture",
                            run_id="opaque-after-backup")
    assert try_reserve_receipt(center, idempotency_hint="new", sink="fixture",
                               run_id=core.operation_prefix() + "new")["status"] == "reserved"
    core.close()
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
                                     ".disaster-restore.json", ".restore-epoch"])
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


def test_reapply_repairs_index_drift_on_its_private_registry_copy(tmp_path, monkeypatch):
    # A failed swap's cleanup trap reapplies quotas from the untouched live
    # registry; index-only drift there must never leave trees unquotaed.
    from tests.test_cutover_index_integrity import damage_many_indexes

    trees = tmp_path / "trees"
    (trees / "300001").mkdir(parents=True)
    registry = tmp_path / "owner-identities.db"
    with sqlite3.connect(registry) as db:
        db.execute("CREATE TABLE box_records (machine INTEGER, project_id INTEGER,"
                   " bhard_bytes INTEGER)")
        db.execute("INSERT INTO box_records VALUES (300001, 300001, 1048576)")
    damage_many_indexes(registry, corrupt_table=False)
    before = registry.read_bytes()
    commands = []
    monkeypatch.setattr(restore.subprocess, "run", lambda args, **kw: commands.append(args))
    monkeypatch.setattr(restore, "verify_owner", lambda *args: None)
    monkeypatch.setattr(restore.os, "chown", lambda *args: None)
    restore.reapply(tmp_path, trees, registry)
    assert [c[3] for c in commands] == [f"project -s -p {trees / '300001'} 300001",
                                       "limit -p bhard=1024k 300001"]
    assert registry.read_bytes() == before


def test_staged_repair_survives_sidecars_removed_by_repairing_their_database(tmp_path):
    # Real owner trees hold WAL databases (e.g. a pre-migrate backup) with
    # -wal/-shm on disk; repairing and closing one removes its sidecars.
    from tinyassets.restore_recovery import _repair_staged_databases

    live = tmp_path / "live.db"
    stage = tmp_path / "stage"
    stage.mkdir()
    keep = sqlite3.connect(live)
    keep.execute("PRAGMA journal_mode=WAL")
    keep.execute("PRAGMA wal_autocheckpoint=0")
    keep.execute("CREATE TABLE notes (body)")
    keep.execute("INSERT INTO notes VALUES ('kept')")
    keep.commit()
    for suffix in ("", "-wal", "-shm"):
        shutil.copyfile(f"{live}{suffix}", stage / f"memory.db.bak{suffix}")
    keep.close()
    _repair_staged_databases(stage)
    with sqlite3.connect(stage / "memory.db.bak") as db:
        assert db.execute("SELECT body FROM notes").fetchall() == [("kept",)]


@pytest.mark.parametrize("name", ["notes-wal", "ledger-shm", "ledger-journal"])
def test_staged_repair_inspects_a_sidecar_named_database_with_no_verified_base(tmp_path, name):
    # Only a verified database's own sidecars are skipped; a database merely
    # named like one still gets link refusal and the integrity verdict.
    from contextlib import closing

    from tests.test_cutover_index_integrity import damage_many_indexes
    from tinyassets.restore_recovery import _repair_staged_databases

    stage = tmp_path / "stage"
    stage.mkdir()
    # A base that merely exists is not a verified database.
    (stage / name[:name.rindex("-")]).write_text("not a database")
    with closing(sqlite3.connect(stage / name)) as db, db:
        db.execute("CREATE TABLE notes (body)")
    (stage / "alias").hardlink_to(stage / name)
    with pytest.raises(ValueError, match="private regular file"):
        _repair_staged_databases(stage)
    (stage / "alias").unlink()
    damage_many_indexes(stage / name, corrupt_table=True)
    with pytest.raises(ValueError, match="not index-only"):
        _repair_staged_databases(stage)
