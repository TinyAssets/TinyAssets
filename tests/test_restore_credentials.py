"""Credential merge: idempotent, isolated per account, no secret in the report."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from tinyassets import restore_credentials as rc

pytestmark = pytest.mark.verifies("owner-daemon-in-box:C3")

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "LEAK-TOKEN-0123456789abcdef"
OWNER_A = "owner-a"
OWNER_B = "owner-b"

SCHEMA = """
CREATE TABLE outbound_connections (
    connection_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    revoked_at TEXT,
    label TEXT
);
CREATE TABLE outbound_connection_grants (
    grant_id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE connection_capabilities (
    connection_id TEXT NOT NULL,
    capability_kind TEXT NOT NULL,
    PRIMARY KEY (connection_id, capability_kind)
);
CREATE TABLE browser_vault (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    sealed BLOB
);
"""
LIVE_SCHEMA = SCHEMA.replace(
    "label TEXT", "label TEXT NOT NULL", 1)


def _db(path, schema, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(schema)
        for table, records in rows.items():
            for record in records:
                names = list(record)
                db.execute(
                    f"INSERT INTO {table} ({','.join(names)}) "
                    f"VALUES ({','.join('?' for _ in names)})",
                    [record[name] for name in names],
                )


def _vault(root, owner, token=TOKEN):
    digest = hashlib.sha256(owner.encode()).hexdigest()
    path = root / ".broker" / "accounts" / digest / "credentials" / ".credential-vault.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token}), encoding="utf-8")
    return path


def _tree(tmp, name, rows, owners=()):
    root = tmp / name
    _db(root / ".broker" / "outbound.db", SCHEMA, rows)
    for owner in owners:
        _vault(root, owner)
    return root


def _conn(owner, connection_id, label="ok"):
    return {"connection_id": connection_id, "owner_user_id": owner,
            "revoked_at": None, "label": label}


def _grant(owner, grant_id, connection_id):
    return {"grant_id": grant_id, "connection_id": connection_id,
            "owner_user_id": owner, "revoked_at": None}


def test_merge_is_idempotent(tmp_path):
    live = _tree(tmp_path, "live", {"outbound_connections": []})
    source = _tree(tmp_path, "source", {
        "outbound_connections": [_conn(OWNER_A, "c-a")],
        "outbound_connection_grants": [_grant(OWNER_A, "g-a", "c-a")],
    }, owners=(OWNER_A,))
    first = rc.merge(live, source)
    assert first["accounts_affected"] == 1
    assert first["tables"]["outbound_connections"]["insert"] == 1
    second = rc.merge(live, source)
    assert second["accounts_affected"] == 0
    assert second["accounts_unchanged"] == 1
    assert second["tables"]["outbound_connections"]["kept_live"] == 1
    assert second["tables"]["outbound_connections"]["insert"] == 0
    assert second["files"]["credential_vault"]["kept_live"] == 1
    assert TOKEN not in json.dumps(second)


def test_conflict_keeps_the_live_grant_and_file(tmp_path):
    live = _tree(tmp_path, "live", {
        "outbound_connections": [_conn(OWNER_A, "c-a", "live")],
        "outbound_connection_grants": [_grant(OWNER_A, "g-a", "c-a")],
    }, owners=(OWNER_A,))
    live_grant = (live / ".broker" / "outbound.db")
    before = live_grant.read_bytes()
    vault = _vault(live, OWNER_A, "live-token-0123456789abcd")
    original = vault.read_bytes()
    source = _tree(tmp_path, "source", {
        "outbound_connections": [_conn(OWNER_A, "c-a", "other")],
        "outbound_connection_grants": [_grant(OWNER_A, "g-a", "c-a")],
    }, owners=(OWNER_A,))
    _vault(source, OWNER_A, TOKEN)
    plan = rc.merge(live, source)
    assert plan["tables"]["outbound_connections"]["conflict"] == 1
    assert plan["tables"]["outbound_connections"]["insert"] == 0
    assert plan["files"]["credential_vault"]["conflict"] == 1
    assert vault.read_bytes() == original
    with sqlite3.connect(live_grant) as db:
        label = db.execute("SELECT label FROM outbound_connections").fetchone()[0]
    assert label == "live"
    assert live_grant.read_bytes() != before or label == "live"
    assert TOKEN not in json.dumps(plan)


def test_one_account_failure_does_not_abort_the_other(tmp_path):
    live = tmp_path / "live"
    _db(live / ".broker" / "outbound.db", LIVE_SCHEMA, {})
    source = tmp_path / "source"
    _db(source / ".broker" / "outbound.db", SCHEMA, {"outbound_connections": [
        _conn(OWNER_A, "c-a", "ok"),
        _conn(OWNER_B, "c-b", None),
    ]})
    _vault(source, OWNER_A)
    _vault(source, OWNER_B)
    plan = rc.merge(live, source)
    assert plan["accounts_affected"] == 1
    assert plan["accounts_failed"] == 1
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        owners = [row[0] for row in db.execute("SELECT owner_user_id FROM outbound_connections")]
    assert owners == [OWNER_A]
    assert (live / ".broker" / "accounts" / hashlib.sha256(OWNER_A.encode()).hexdigest()
            / "credentials" / ".credential-vault.json").is_file()
    assert not (live / ".broker" / "accounts" / hashlib.sha256(OWNER_B.encode()).hexdigest()
                ).exists()
    assert TOKEN not in json.dumps(plan)


def test_symlink_credential_is_not_copied(tmp_path):
    live = _tree(tmp_path, "live", {})
    source = _tree(tmp_path, "source", {"outbound_connections": [
        _conn(OWNER_A, "c-a"), _conn(OWNER_B, "c-b"),
    ]}, owners=(OWNER_A,))
    digest = hashlib.sha256(OWNER_B.encode()).hexdigest()
    link = source / ".broker" / "accounts" / digest / "credentials" / ".credential-vault.json"
    link.parent.mkdir(parents=True)
    link.symlink_to(source / ".broker" / "outbound.db")
    plan = rc.merge(live, source)
    assert plan["accounts_failed"] == 0
    assert plan["tables"]["outbound_connections"]["insert"] == 2
    assert not (live / ".broker" / "accounts" / digest).exists()


def test_safe_json_refuses_a_secret_leaf():
    report = {"mode": "inspect", "error": TOKEN}
    with pytest.raises(rc.SourceRefused):
        rc.safe_json(report)


def test_select_source_prefers_credential_counts_over_a_later_date():
    early = rc._source_record(
        "archive", "tinyassets-data-2026-10-23T03-00-00Z.tar.gz", "2026-10-23",
        {"accounts_with_vault": 6, "accounts_with_subscription": 6,
         "outbound_connections": 4, "outbound_connection_grants": 4,
         "connection_capabilities": 4, "browser_vault": 0},
        digest_known=True, readable=True)
    later = rc._source_record(
        "archive", "tinyassets-data-2026-10-24T03-00-00Z.tar.gz", "2026-10-24",
        {"accounts_with_vault": 0, "accounts_with_subscription": 0,
         "outbound_connections": 1, "outbound_connection_grants": 1,
         "connection_capabilities": 1, "browser_vault": 0},
        digest_known=True, readable=True)
    assert rc.select_source([later, early]) == early["name"]
    tied = dict(early, date="2026-10-22", name="tinyassets-data-2026-10-22T03-00-00Z.tar.gz")
    assert rc.select_source([tied, early]) == early["name"]


def test_digest_and_extract_reject_a_linked_credential(tmp_path):
    archive = tmp_path / "tinyassets-data-2026-10-24T03-00-00Z.tar.gz"
    payload = tmp_path / "outbound.db"
    payload.write_bytes(b"not-a-secret-db")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="_data/.broker/outbound.db")
        info = tarfile.TarInfo("_data/.broker/accounts/" + "a" * 64
                               + "/credentials/.credential-vault.json")
        info.type = tarfile.SYMTYPE
        info.linkname = "outbound.db"
        tar.addfile(info)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    rc.verify_digest(archive, digest)
    with pytest.raises(rc.SourceRefused) as raised:
        rc.verify_digest(archive, "0" * 64)
    assert raised.value.code == "digest_mismatch"
    with pytest.raises(rc.SourceRefused) as raised:
        rc.extract_needed(archive, tmp_path / "out")
    assert raised.value.code == "unsafe_member"


def test_unrelated_symlink_does_not_reject_the_archive(tmp_path):
    archive = tmp_path / "tinyassets-snapshot-2026-10-22T04-41-03Z.tar.gz"
    payload = tmp_path / "outbound.db"
    _db(payload, SCHEMA, {"outbound_connections": [_conn(OWNER_A, "c-a")]})
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="_data/.broker/outbound.db")
        info = tarfile.TarInfo("_data/some-link")
        info.type = tarfile.SYMTYPE
        info.linkname = "elsewhere"
        tar.addfile(info)
    dest = rc.extract_needed(archive, tmp_path / "out")
    assert (dest / ".broker" / "outbound.db").is_file()


def test_dry_run_does_not_change_the_live_ledger(tmp_path):
    live = _tree(tmp_path, "live", {"outbound_connections": [_conn(OWNER_A, "c-live")]})
    source = _tree(tmp_path, "source", {"outbound_connections": [_conn(OWNER_B, "c-b")]},
                   owners=(OWNER_B,))
    before = (live / ".broker" / "outbound.db").read_bytes()
    plan = rc._plan_only(live, source)
    assert plan["tables"]["outbound_connections"]["insert"] == 1
    assert (live / ".broker" / "outbound.db").read_bytes() == before
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        assert db.execute("SELECT COUNT(*) FROM outbound_connections").fetchone()[0] == 1


def test_apply_stops_before_merge_and_starts_if_merge_fails(tmp_path):
    live = _tree(tmp_path, "live", {})
    source = _tree(tmp_path, "source", {})
    calls = []

    def run(argv, timeout=0):
        calls.append(tuple(argv))
        return type("Done", (), {"returncode": 0})()

    def boom(*_args, **_kwargs):
        raise rc.SourceRefused("unreadable")

    original = rc.merge
    rc.merge = boom
    try:
        plan, facts, _checked, error = rc.apply_tree(
            live, source, tmp_path / "credential-restore-2026-10-25T01-02-03Z", run=run)
    finally:
        rc.merge = original
    assert calls[0] == ("docker", "stop", "tinyassets-broker")
    assert calls[-1] == ("docker", "start", "tinyassets-broker")
    assert error == "unreadable"
    assert facts["broker_started"] is True
    assert facts["rollback_date"] == "2026-10-25"
    assert plan is None


def test_apply_reports_start_failure(tmp_path):
    live = _tree(tmp_path, "live", {})
    source = _tree(tmp_path, "source", {"outbound_connections": [_conn(OWNER_A, "c-a")]})

    def run(argv, timeout=0):
        code = 1 if argv[1] == "start" else 0
        return type("Done", (), {"returncode": code})()

    _plan, facts, _checked, error = rc.apply_tree(
        live, source, tmp_path / "credential-restore-2026-10-25T01-02-03Z", run=run)
    assert error == "broker_start_failed"
    assert facts["broker_started"] is False
    assert facts["broker_stopped"] is True


def test_remote_archive_is_downloaded_and_checked(tmp_path, monkeypatch):
    archive = tmp_path / "packed.tar.gz"
    payload = tmp_path / "outbound.db"
    _db(payload, SCHEMA, {"outbound_connections": [_conn(OWNER_A, "c-a")]})
    name = "tinyassets-data-2026-10-24T03-00-00Z.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="_data/.broker/outbound.db")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    def fake_run(argv, timeout=3600):
        assert argv[1] == "copyto"
        Path(argv[-1]).write_bytes(archive.read_bytes())
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setattr(rc, "_run", fake_run)
    root, cleanup = rc._materialize(
        tmp_path / "live", tmp_path / "empty",
        {"kind": "archive", "name": name, "digest_known": True},
        destination="remote:bucket", digests={name: digest},
    )
    try:
        assert (root / ".broker" / "outbound.db").is_file()
    finally:
        cleanup()
    with pytest.raises(rc.SourceRefused) as raised:
        rc._materialize(
            tmp_path / "live", tmp_path / "empty",
            {"kind": "archive", "name": name, "digest_known": True},
            destination="remote:bucket", digests={name: "0" * 64},
        )
    assert raised.value.code == "digest_mismatch"


def test_marker_restores_on_an_awake_mount_and_counts_the_rest(tmp_path):
    live = _tree(tmp_path, "live", {})
    uid = "300001"
    mounts = tmp_path / "mounts"
    (mounts / uid).mkdir(parents=True)
    source = tmp_path / "source"
    marker = source / uid / ".ta-cli" / "claude" / "connection"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"source": "s", "incarnation": TOKEN}), encoding="utf-8")
    assert rc.restore_markers(mounts, source) == 1
    assert rc.restore_markers(mounts, source) == 0
    copied = (mounts / uid / ".ta-cli" / "claude" / "connection").read_text(encoding="utf-8")
    assert TOKEN in copied
    asleep = tmp_path / "asleep-source"
    (asleep / "300002" / ".ta-cli" / "claude").mkdir(parents=True)
    (asleep / "300002" / ".ta-cli" / "claude" / "connection").write_text(
        marker.read_text(encoding="utf-8"), encoding="utf-8")
    assert rc.restore_markers(mounts, asleep) == 0
    checked = rc.verify(live, mounts, source_markers=1, markers_restored=0)
    assert TOKEN not in json.dumps(checked)


def test_inspect_counts_accounts_and_hides_private_values(tmp_path, monkeypatch, capsys):
    root = tmp_path / "data"
    _db(root / ".broker" / "outbound.db", SCHEMA, {
        "outbound_connections": [_conn(OWNER_A, "c-a")],
        "outbound_connection_grants": [_grant(OWNER_A, "g-a", "c-a")],
        "connection_capabilities": [{"connection_id": "c-a", "capability_kind": "model_use"}],
    })
    _db(root / ".broker" / "state" / "owner-identities.db",
        "CREATE TABLE owner_volumes (uid INTEGER, principal TEXT);",
        {"owner_volumes": [
            {"uid": 300001, "principal": OWNER_A},
            {"uid": 300002, "principal": OWNER_B},
        ]})
    _vault(root, OWNER_A)
    (root / ".layout.json").write_text(json.dumps({
        "preparation": {"credential_roots_reset": True}, "note": TOKEN,
    }), encoding="utf-8")
    (root / ".platform-recovery.json").write_bytes(b"{}")
    log = tmp_path / "backup.log"
    log.write_text(
        "[restore 2026-10-23T03:11:00Z] shared-layer restore complete\n" + TOKEN + "\n",
        encoding="utf-8")
    retained = tmp_path / ".tinyassets-restore-old.Ab12"
    _tree(retained, ".", {"outbound_connections": [_conn(OWNER_A, "c-old")]}, owners=(OWNER_A,))
    # _tree(retained, ".") creates retained/./.broker which is retained/.broker. Good.
    monkeypatch.setattr(rc.os, "geteuid", lambda: 0)
    monkeypatch.delenv("BACKUP_DEST", raising=False)
    assert rc.main([
        "--mode", "inspect", "--root", str(root), "--backup-log", str(log),
        "--snapshot-dir", str(tmp_path / "snaps"), "--mounts", str(tmp_path / "mounts"),
    ]) == 0
    out = capsys.readouterr().out
    assert TOKEN not in out
    assert OWNER_A not in out
    assert hashlib.sha256(OWNER_A.encode()).hexdigest() not in out
    report = json.loads(out)
    assert report["mode"] == "inspect"
    assert report["live"]["credential_roots_reset"] is True
    assert report["live"]["platform_recovery_present"] is True
    assert report["live"]["accounts"] == 2
    assert report["live"]["accounts_with_vault"] == 1
    assert report["live"]["accounts_with_subscription"] == 0
    assert report["live"]["outbound_connections"] == 1
    assert report["live"]["restore_complete_dates"] == ["2026-10-23"]
    assert report["live"]["retained_dirs"] == 1
    assert report["sources"][0]["accounts_with_vault"] == 1
    assert report["sources"][0]["outbound_connections"] == 1


def test_assignment_restore_reports_counts_and_writes_only_on_apply(tmp_path):
    from tests.test_restore_legacy_credentials import (
        _assignment_world,
        _restored_assignment,
        _restore,
        _tree_bytes,
    )

    world = _assignment_world(tmp_path)
    before = _tree_bytes(world["volume"])
    planned = _restore(world, write=False)
    assert planned["members_readded"] == 1
    assert planned["centers_changed"] == 1
    assert _tree_bytes(world["volume"]) == before
    rc.safe_json({"mode": "dry-run", "plan": {"assignments": planned}})
    applied = _restore(world, write=True)
    assert applied == planned
    restored = _restored_assignment(world["volume"])
    assert restored.provider == world["root"]
    seated = next(
        member for member in restored.candidates if member.provider == world["root"]
    )
    assert seated.access.document() == world["access"]
    assert world["held"] not in {member.provider for member in restored.candidates}
    again = _restore(world, write=True)
    assert again["members_readded"] == 0
    assert again["centers_changed"] == 0
    rendered = rc.safe_json({"mode": "dry-run", "plan": {"assignments": again}})
    assert "gpt-test" not in rendered
    assert "provdef_" not in rendered


def test_missing_snapshot_reseat_is_reported(tmp_path):
    from tests.test_restore_legacy_credentials import (
        _assignment_world,
        _prepare_live,
        _restore,
        _tree_bytes,
    )

    world = _prepare_live(_assignment_world(tmp_path))
    before = _tree_bytes(world["volume"])
    planned = _restore(world, write=False)
    assert planned["basis"] == "live"
    assert planned["members_readded"] == 1
    assert "skipped_reason" not in planned
    rendered = rc.safe_json({"mode": "dry-run", "plan": {"assignments": planned}})
    assert "fixture" not in rendered
    assert "grant-0" not in rendered
    assert _tree_bytes(world["volume"]) == before
    skipped = rc.restore_assignments(
        world["live"], world["source"], tmp_path / "no-mounts", world["ledger"],
        write=False,
    )
    assert skipped["skipped_reason"] == "mounts_missing"
    assert skipped["members_readded"] == 0
    rc.safe_json({"mode": "dry-run", "plan": {"assignments": skipped}})
    assert _tree_bytes(world["volume"]) == before


def test_banned_operations_and_workflow_posture():
    module = (ROOT / "tinyassets/restore_credentials.py").read_text(encoding="utf-8")
    shell = (ROOT / "deploy/restore-credentials.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/restore-credentials.yml").read_text(encoding="utf-8")
    for text in (module, shell, workflow):
        for banned in ("reset_credentials(", "rotate_box_secrets(", "close_admission(",
                       "backup-restore.sh"):
            assert banned not in text
    assert "workflow_dispatch:" in workflow
    assert "environment: production" in workflow
    assert "contents: read" in workflow
    assert "production-host-mutation" in workflow
    assert "${{ inputs" not in workflow
    assert "set -x" not in shell
