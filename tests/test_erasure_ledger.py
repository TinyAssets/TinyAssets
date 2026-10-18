"""Erasure authority is monotonic across restores and independent of identity rows."""
import asyncio
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.support.recovery_daemons import recovery_daemons
from tests.test_box_restore_authority import disaster, paired
from tests.test_workos_provider import _provider, _sign
from tinyassets.erasure_ledger import (
    NAME,
    PHASES,
    ErasureLedger,
    carry_forward,
    principal_digest,
    seed_legacy,
)
from tinyassets.restore_recovery import erasures, finish_erasures, normal_preflight


@pytest.fixture(autouse=True)
def initialized_ledger(tmp_path, monkeypatch):
    ErasureLedger(tmp_path).initialize()
    # Restore mechanics use disposable directories; mount proof is tested separately.
    monkeypatch.setattr("tinyassets.boxes.destroy.verify_store", lambda *a: None)


def test_two_disaster_restores_keep_earlier_quarantine(tmp_path, monkeypatch):
    from tinyassets.boxes import destroy
    from tinyassets.boxes.provider import BoxError

    current, backup, live, trees = paired(tmp_path)
    original = tmp_path / "original"
    shutil.copytree(backup, original)
    ErasureLedger(current).add("alice")
    (live / "300001").chmod(0o700)
    os.chown(live / "300001", 300001, 300001)
    real = destroy.tree
    blocked = [True]

    def tree(store, uid, *, erase):
        if blocked[0] and (store / str(uid)).exists():
            raise BoxError("injected mount refusal")
        return real(store, uid, erase=erase)

    monkeypatch.setattr(destroy, "tree", tree)
    disaster(current, backup, trees, monkeypatch)
    finish_erasures(backup, live, daemons=recovery_daemons(live, monkeypatch))
    entry = ErasureLedger(backup).pending()[0]
    earlier = live / next(iter(entry["work"].values()))["quarantine"]
    assert earlier.exists()
    assert not entry["receipt"]["home_removed"]
    # Two more restores from the original backup must carry this old path.
    for index in range(2):
        staged = tmp_path / f"restore-{index}"
        shutil.copytree(original, staged)
        disaster(backup, staged, trees, monkeypatch)
        finish_erasures(staged, live, daemons=recovery_daemons(live, monkeypatch))
        assert earlier.exists()
        assert not ErasureLedger(staged).receipt("alice")["home_removed"]
        backup = staged
    blocked[0] = False
    finish_erasures(backup, live, daemons=recovery_daemons(live, monkeypatch))
    assert not earlier.exists()
    assert ErasureLedger(backup).receipt("alice")["home_removed"]
    assert all(not entry["work"] for entry in ErasureLedger(backup).entries())


def test_unmapped_ledger_digest_refuses_fresh_workos_login(tmp_path, monkeypatch):
    current, staged, _, _ = paired(tmp_path)
    owner = "post-backup-owner"
    ErasureLedger(current).execute("add", {"digest": principal_digest(owner)})
    (current / ".broker/state/owner-identities.db").write_bytes(b"damaged")
    (current / ".router-deleted-owners.sqlite3").write_bytes(b"damaged")
    erasures(current, staged, identity_exists=lambda _: True)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(staged))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert _provider(key).resolve_token(_sign(key, sub=owner, sid="fresh-session")) is None
    entry = ErasureLedger(staged).pending()[0]
    assert entry["principal"] is None
    assert entry["digest"] == principal_digest(owner)


def test_retry_without_identity_row_finishes_billing_and_identity(tmp_path, monkeypatch):
    from tinyassets.owner_router import resume_deleted_accounts

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    ledger = ErasureLedger(tmp_path)
    ledger.add("alice", {"completed_phases": ["owner_tree", "broker_egress", "credentials"],
                         "restore_pending": True})
    calls = []
    outcomes = iter(("error", "cancelled"))
    monkeypatch.setattr("tinyassets.billing.stripe_adapter.cancel_account_billing",
                        lambda *a: calls.append("billing") or next(outcomes))
    monkeypatch.setattr("tinyassets.router_account_deletion.delete_workos_user",
                        lambda *a: calls.append("identity") or "deleted")
    assert not (tmp_path / ".broker/state/owner-identities.db").exists()
    asyncio.run(resume_deleted_accounts(tmp_path))
    assert ledger.pending()[0]["receipt"]["unfinished_phases"] == ["billing", "identity"]
    assert calls == ["billing"]
    asyncio.run(resume_deleted_accounts(tmp_path))
    assert calls == ["billing", "billing", "identity"]
    assert ledger.pending() == []


def test_normal_rollback_keeps_post_backup_ledger_entry(tmp_path):
    current, staged, live, trees = paired(tmp_path)
    ErasureLedger(current).add("post-backup-owner")
    normal_preflight(current, staged, live, backup_time=1, restored_owners=trees)
    assert ErasureLedger(staged).contains("post-backup-owner")
    assert ErasureLedger(staged).pending()


def test_cutover_seeds_all_legacy_sources(tmp_path):
    current, staged, _, _ = paired(tmp_path)
    with sqlite3.connect(current / ".router-deleted-owners.sqlite3") as db:
        db.execute("CREATE TABLE deleted_owners (owner TEXT, receipt TEXT)")
        db.execute("INSERT INTO deleted_owners VALUES ('router-only',NULL)")
    with sqlite3.connect(current / ".broker/credential-custody.db") as db:
        db.execute("INSERT INTO deleted_principals VALUES (?)", (principal_digest("custody-only"),))

    (current / ".boxhost").mkdir(exist_ok=True)
    with sqlite3.connect(current / ".boxhost/boxhost.db") as db:
        db.execute("CREATE TABLE owner_destroys (owner_id TEXT, uid INTEGER)")
        db.execute("INSERT INTO owner_destroys VALUES (?,?)", ("host-only", 300002))
    ledger = carry_forward(current, staged)
    seed_legacy(current, ledger, store_root=tmp_path)
    assert all(ledger.contains(owner) for owner in ("router-only", "custody-only", "host-only"))
    assert {entry["digest"] for entry in ledger.pending()} == {
        principal_digest(owner) for owner in ("router-only", "custody-only", "host-only")}
    assert (staged / NAME).stat().st_uid == 0
    assert (staged / NAME).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("location", ["source", "staging"])
@pytest.mark.parametrize("kind", ["main", "main-index-drift", "custody", "router", "host",
                                 "revocation", "policy"])
def test_each_legacy_fence_refuses_auth_and_survives_disaster(
    tmp_path, monkeypatch, kind, location,
):
    current, staged, live, trees = paired(tmp_path)
    restored = tmp_path / "restored"
    shutil.copytree(current, restored)  # A recovery point predating the fence.
    root = current if location == "source" else staged
    digest = principal_digest("alice")
    if kind == "policy":
        marker = root / ".broker/accounts" / digest / ".deleted"
        marker.parent.mkdir(parents=True)
        marker.touch()
    else:
        path, ddl, insert, values = {
            "main": (".tinyassets.db", "CREATE TABLE deleted_principals "
                     "(founder_sub TEXT, deleted_at REAL)",
                     "INSERT INTO deleted_principals VALUES (?,1)", (digest,)),
            "custody": (".broker/credential-custody.db", "",
                        "INSERT INTO deleted_principals VALUES (?)", (digest,)),
            "router": (".router-deleted-owners.sqlite3",
                       "CREATE TABLE deleted_owners (owner TEXT, receipt TEXT)",
                       "INSERT INTO deleted_owners VALUES (?,NULL)", ("alice",)),
            "host": (".boxhost/boxhost.db", "CREATE TABLE owner_destroys "
                     "(owner_id TEXT, uid INTEGER)",
                     "INSERT INTO owner_destroys VALUES (?,?)", ("alice", 300001)),
            "revocation": (".broker/state/owner-identities.db", "",
                           "INSERT INTO owner_box_revocations VALUES (?,1)", ("alice",)),
        }["main" if kind == "main-index-drift" else kind]
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(root / path) as db:
            if ddl:
                db.execute(ddl)
            db.execute(insert, values)
            if kind == "main-index-drift":
                db.executescript("""
                    CREATE INDEX tombstone_index ON deleted_principals(founder_sub) WHERE 0;
                    PRAGMA writable_schema=ON;
                    UPDATE sqlite_master SET sql=
                        'CREATE INDEX tombstone_index ON deleted_principals(founder_sub)'
                        WHERE name='tombstone_index';
                """)
    main_before = (root / ".tinyassets.db").read_bytes() if kind.startswith("main") else None
    ledger = ErasureLedger(staged)
    seed_legacy(current, ledger, store_root=live)
    if main_before is not None:
        assert (root / ".tinyassets.db").read_bytes() == main_before
    entry, = ledger.entries()
    assert entry["digest"] == digest and entry["uids"] == [300001]
    assert entry["receipt"]["unfinished_phases"] == list(PHASES)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(staged))
    assert _provider(key).resolve_token(_sign(key, sub="alice", sid="fresh-session")) is None
    # Retired formats cannot participate in the owner-box disaster path.
    for data in (staged, restored):
        (data / ".tinyassets.db").unlink(missing_ok=True)
        for name in (".router-deleted-owners.sqlite3", ".boxhost/boxhost.db",
                     ".broker/credential-custody.db", ".disaster-restore.json"):
            (data / name).parent.mkdir(parents=True, exist_ok=True)
            (data / name).write_bytes(b"retired")
        with sqlite3.connect(data / ".broker/state/owner-identities.db") as db:
            db.execute("DELETE FROM owner_box_revocations")
        shutil.rmtree(data / ".broker/accounts", ignore_errors=True)
    # Disaster recovery writes a local custody fence, but never reads one.
    (restored / ".broker/credential-custody.db").unlink()
    for tree in (live / "300001", trees / "300001"):
        os.chown(tree, 300001, 300001)
        tree.chmod(0o700)
    report = disaster(staged, restored, trees, monkeypatch)
    assert report["erased_owners"] == 1
    recovered = ErasureLedger(restored)
    assert recovered.contains("alice") and recovered.pending()[0]["work"]
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(restored))
    assert _provider(key).resolve_token(_sign(key, sub="alice", sid="after-restore")) is None
    finish_erasures(restored, live, daemons=recovery_daemons(live, monkeypatch))
    assert not (live / "300001").exists()
    assert recovered.receipt("alice")["home_removed"]


def test_cutover_does_not_turn_box_retirement_into_account_erasure(tmp_path):
    current, staged, live, _ = paired(tmp_path)
    with sqlite3.connect(current / ".broker/state/owner-identities.db") as db:
        db.execute("INSERT INTO owner_box_revocations VALUES ('alice',0)")
    seed_legacy(current, ErasureLedger(staged), store_root=live)
    assert not ErasureLedger(staged).contains("alice")


def test_cutover_refuses_corrupt_legacy_main_database(tmp_path):
    current, staged, live, _ = paired(tmp_path)
    (current / ".tinyassets.db").write_bytes(b"corrupt table data")
    with pytest.raises(sqlite3.DatabaseError):
        seed_legacy(current, ErasureLedger(staged), store_root=live)


def test_union_cannot_hide_new_work_with_an_old_completion(tmp_path, monkeypatch):
    current, staged, _, _ = paired(tmp_path)
    first = ErasureLedger(current)
    first.add("alice", {"local_cleanup": "pending"})
    first.save_receipt("alice", {"completed_phases": list(PHASES)})
    carry_forward(current, staged)
    second = ErasureLedger(staged)
    monkeypatch.setattr("tinyassets.erasure_ledger.time.time", lambda: 1)
    second.execute("restart", {"digest": principal_digest("alice")})
    carry_forward(current, staged)
    assert second.receipt("alice")["unfinished_phases"] == list(PHASES)
    assert second.receipt("alice")["local_cleanup"] == "done"
    with sqlite3.connect(staged / NAME) as db:
        for statement in ("DELETE FROM events", "UPDATE events SET digest='erased'"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(statement)


def test_ledger_control_refuses_owner_and_broker_writes(tmp_path):
    from types import SimpleNamespace

    from tinyassets.boxes.host_service import HostService

    host = HostService(SimpleNamespace(data_root=tmp_path), None, None, None)
    doc = {"lifecycle": "erasure", "ledger_op": "add", "arguments": {
        "digest": principal_digest("alice"), "principal": "alice"}}
    for uid in (300001, 1002):
        assert "error" in host.lifecycle(doc, uid)
    assert not ErasureLedger(tmp_path).contains("alice")
    assert host.lifecycle(doc, 1001) == {"result": None}
    assert ErasureLedger(tmp_path).contains("alice")


@pytest.mark.parametrize("disaster_path", [False, True])
def test_ledger_only_erased_uid_stays_reserved(tmp_path, disaster_path):
    current, staged, live, trees = paired(tmp_path)
    ledger = ErasureLedger(current)
    ledger.execute("add", {"digest": principal_digest("later"), "principal": "later",
                           "uids": [300002]})
    (current / ".broker/state/owner-identities.db").write_bytes(b"lost identity rows")
    if disaster_path:
        erasures(current, staged, identity_exists=lambda _: True)
    else:
        normal_preflight(current, staged, live, backup_time=1)
    with sqlite3.connect(staged / ".broker/state/owner-identities.db") as db:
        assert db.execute("SELECT machine_id FROM owner_identities WHERE principal='later'"
                          ).fetchone() == (300002,)
        assert db.execute("SELECT MAX(machine_id)+1 FROM owner_identities").fetchone() == (300003,)


def test_normal_restore_tracks_retained_and_restored_trees(tmp_path, monkeypatch):
    current, staged, live, trees = paired(tmp_path)
    ErasureLedger(current).add("alice")
    retained = live / ".retained"
    retained.mkdir()
    normal_preflight(current, staged, live, backup_time=1, retained_owners=retained)
    (live / "300001").rename(retained / "300001")
    (trees / "300001").rename(live / "300001")
    for store in (live, retained):
        os.chown(store / "300001", 300001, 300001)
        (store / "300001").chmod(0o700)
    assert not ErasureLedger(staged).receipt("alice")["home_removed"]
    finish_erasures(staged, live, daemons=recovery_daemons(live, monkeypatch))
    assert not (live / "300001").exists()
    assert not (retained / "300001").exists()
    assert ErasureLedger(staged).receipt("alice")["home_removed"]


def test_root_ledger_cannot_be_replaced_by_router(tmp_path):
    ledger = ErasureLedger(tmp_path)
    ledger.add("alice")
    tmp_path.parent.chmod(0o755)
    child = os.fork()
    if child == 0:
        try:
            os.setgid(1001)
            os.setuid(1001)
            (tmp_path / "router-file").touch()
            try:
                ledger.path.unlink()
            except PermissionError:
                os._exit(0)
            os._exit(1)
        except BaseException:
            os._exit(2)
    assert os.waitpid(child, 0)[1] == 0
    assert ledger.contains("alice")
    sidecar = tmp_path / (NAME + "-journal")
    sidecar.write_bytes(b"hostile sidecar")
    os.chown(sidecar, 1001, 1001)
    assert ledger.contains("alice")
    assert ledger.contains("unfenced")


def test_legacy_report_quarantine_is_seeded_without_identity_store(tmp_path):
    import json

    current, staged, _, _ = paired(tmp_path)

    (current / ".boxhost").mkdir(exist_ok=True)
    with sqlite3.connect(current / ".boxhost/boxhost.db") as db:
        db.execute("CREATE TABLE owner_destroys (owner_id TEXT, uid INTEGER)")
        db.execute("INSERT INTO owner_destroys VALUES (?,?)", ("later", 300002))
    (current / ".broker/state/owner-identities.db").unlink()
    path = tmp_path / "old-quarantine" / "300002"
    (current / ".disaster-restore.json").write_text(json.dumps({
        "pending_erasures": [], "erasure_failures": [{"uid": 300002, "quarantine": str(path)}]}))
    ledger = carry_forward(current, staged)
    seed_legacy(current, ledger, store_root=tmp_path)
    assert any(work["quarantine"] == str(path.relative_to(tmp_path)) for entry in ledger.entries()
               for work in entry["work"].values())


def test_router_uses_root_control_socket_and_paginates_pending(tmp_path):
    import subprocess
    import sys
    import tempfile
    import threading
    import time
    from types import SimpleNamespace

    from tinyassets.boxes.host_service import HostService

    ledger = ErasureLedger(tmp_path)
    for index in range(130):
        ledger.add(f"owner-{index}")
    host = HostService(SimpleNamespace(data_root=tmp_path), None, None, None)
    with tempfile.TemporaryDirectory(prefix="ta-ledger-") as directory:
        runtime = Path(directory)
        runtime.chmod(0o755)
        path = runtime / "erasure.sock"
        stop = threading.Event()
        server = threading.Thread(target=host.serve, args=(path, 1102, host.erasure, stop))
        server.start()
        try:
            deadline = time.monotonic() + 5
            while not path.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            code = '''
from pathlib import Path
from tinyassets.erasure_ledger import ErasureLedger, PHASES
ledger = ErasureLedger(Path('/not-a-router-readable-ledger'))
assert len(ledger.pending()) == 130
ledger.add('router-new')
assert ledger.contains('router-new')
assert ledger.receipt('router-new')['unfinished_phases'] == list(PHASES)
ledger.save_receipt('router-new', {'completed_phases': list(PHASES)})
assert len(ledger.pending()) == 130
'''
            result = subprocess.run([sys.executable, "-c", code], user=1001, group=1001,
                                    extra_groups=[1102], capture_output=True, text=True,
                                    env={**os.environ, "TINYASSETS_BOXES_RUN": str(runtime)},
                                    timeout=20)
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            stop.set()
            server.join(5)
    assert ledger.contains("router-new")


def test_owner_box_restore_ignores_retired_stores_and_reports(tmp_path):
    current, staged, live, _ = paired(tmp_path)
    ledger = ErasureLedger(current)
    ledger.execute("add", {"digest": principal_digest("alice"), "principal": "alice",
                           "uids": [300001]})
    for root in (current, staged):
        (root / ".boxhost").mkdir(exist_ok=True)
        for name in (".boxhost/boxhost.db", ".router-deleted-owners.sqlite3",
                     ".disaster-restore.json", ".broker/credential-custody.db"):
            (root / name).write_bytes(b"retired format must never be consulted")
    normal_preflight(current, staged, live, backup_time=1)
    assert ErasureLedger(staged).contains("alice")


def test_normal_legacy_pair_seeds_erasure_before_forward_migration(tmp_path):
    import json

    current, staged, live, _ = paired(tmp_path)
    (staged / NAME).unlink()
    (staged / ".layout.json").write_text(json.dumps({"split": "owner-split", "state": "stable"}))
    with sqlite3.connect(staged / ".router-deleted-owners.sqlite3") as db:
        db.execute("CREATE TABLE deleted_owners (owner TEXT, receipt TEXT)")
        db.execute("INSERT INTO deleted_owners VALUES ('alice',NULL)")
    normal_preflight(current, staged, live, backup_time=1)
    entry, = ErasureLedger(staged).entries()
    assert entry["uids"] == [300001] and entry["work"]
    assert not (staged / ".router-deleted-owners.sqlite3").exists()
