"""Owner-volume census and the deploy image hold.

The census is read-only. It must not report zero owners when the identity
database cannot be read, and it must not print a principal, a snapshot hash,
or a credential path.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "owner_volume_audit.py"
sys.path.insert(0, str(REPO / "scripts"))

import owner_volume_audit as audit  # noqa: E402

pytestmark = pytest.mark.verifies("owner-daemon-in-box:C2")

PRINCIPAL = "leak-principal-9f3a@example.invalid"
SNAPSHOT_SHA = "ab" * 32
LEAK_TOKEN = "LEAK-TOKEN-9f3a"


def _db(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE owner_identities (principal TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE owner_volumes ("
            "principal TEXT PRIMARY KEY, uid INTEGER NOT NULL, "
            "volume_generation INTEGER NOT NULL, snapshot_id TEXT, "
            "snapshot_sha256 TEXT, work TEXT, local INTEGER NOT NULL)")
        for principal, uid, generation, snapshot, work, local in rows:
            conn.execute("INSERT INTO owner_identities (principal) VALUES (?)", (principal,))
            conn.execute(
                "INSERT INTO owner_volumes VALUES (?,?,?,?,?,?,?)",
                (principal, uid, generation, snapshot, SNAPSHOT_SHA if snapshot else None,
                 work, local))


def _image(store: Path, uid: int, payload: bytes = b"x" * 8192) -> Path:
    store.mkdir(parents=True, exist_ok=True)
    path = store / f"{uid}.img"
    path.write_bytes(payload)
    return path


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, check=False)


def _assert_private(text: str) -> None:
    assert PRINCIPAL not in text
    assert SNAPSHOT_SHA not in text
    assert LEAK_TOKEN not in text


def test_audit_counts_owners_and_hides_principals(tmp_path: Path):
    data = tmp_path / "data"
    store = tmp_path / "owners"
    _db(data / ".broker" / "state" / "owner-identities.db", [
        (PRINCIPAL, 300001, 4, "a" * 32,
         json.dumps({"kind": "running", "restore_snapshot_id": LEAK_TOKEN}), 1),
        ("other-hidden@example.invalid", 300002, 1, None, None, 1),
    ])
    # The first row is a restore, so it is affected. The second is the live image.
    _image(store, 300001)
    _image(store, 300002)
    accounts = data / ".broker" / "accounts" / ("c" * 64) / "credentials"
    accounts.mkdir(parents=True)
    (accounts / ".subscription-state.json").write_bytes(b'{"state":"hidden-bytes"}')
    ledger = data / ".broker" / "outbound.db"
    with sqlite3.connect(ledger) as conn:
        conn.execute(
            "CREATE TABLE outbound_connection_grants "
            "(grant_id TEXT PRIMARY KEY, revoked_at REAL)")
        conn.execute("INSERT INTO outbound_connection_grants VALUES ('g1', NULL)")
        conn.execute("INSERT INTO outbound_connection_grants VALUES ('g2', 1)")
    before = (store / "300002.img").stat()
    result = _run([
        "audit", "--store", str(store), "--data-root", str(data), "--skip-mounts"])
    _assert_private(result.stdout + result.stderr)
    assert "c" * 64 not in result.stdout
    assert result.returncode == 2, result.stderr
    doc = json.loads(result.stdout)
    assert doc["owners"] == 2
    assert doc["identity_rows"] == 2
    assert doc["affected"] == 1
    assert doc["grants"] == 1
    assert doc["subscription_files"] == 1
    assert "restore_or_erase_pending" in doc["failures"]
    live = next(row for row in doc["rows"] if row["uid"] == 300002)
    assert live["disposition"] == "serving_local_image"
    assert live["action"] == "none"
    assert (store / "300002.img").stat().st_mtime_ns == before.st_mtime_ns
    again = _run([
        "audit", "--store", str(store), "--data-root", str(data), "--skip-mounts"])
    assert json.loads(again.stdout) == doc


def test_missing_identity_database_is_not_zero_owners(tmp_path: Path):
    store = tmp_path / "owners"
    store.mkdir()
    result = _run([
        "audit", "--store", str(store), "--data-root", str(tmp_path / "missing"),
        "--skip-mounts"])
    assert result.returncode == 2
    doc = json.loads(result.stdout)
    assert doc["owners"] is None
    assert doc["owners"] != 0
    assert doc["failures"] == ["identity_unreadable"]


def test_dispositions_that_must_not_be_woken(tmp_path: Path):
    data = tmp_path / "data"
    store = tmp_path / "owners"
    _db(data / ".broker" / "state" / "owner-identities.db", [
        ("a@example.invalid", 300010, 1, None, json.dumps({"kind": "create"}), 0),
        ("b@example.invalid", 300011, 2, "b" * 32, None, 0),
        ("c@example.invalid", 300012, 1, None, None, 1),
    ])
    _image(store, 300010)
    _image(store, 300011)
    result = _run([
        "audit", "--store", str(store), "--data-root", str(data), "--skip-mounts"])
    assert result.returncode == 2
    doc = json.loads(result.stdout)
    by_uid = {row["uid"]: row for row in doc["rows"]}
    assert by_uid[300010]["disposition"] == "format_on_next_wake"
    assert by_uid[300010]["action"].startswith("do not wake")
    assert by_uid[300011]["disposition"] == "published_snapshot_on_next_wake"
    assert "volumes.restore" in by_uid[300011]["action"]
    assert by_uid[300012]["disposition"] == "missing_image"
    assert doc["affected"] == 3


def test_image_hold_is_idempotent_and_refuses_replace_or_shrink(tmp_path: Path):
    store = tmp_path / "owners"
    image = _image(store, 300021)
    baseline = tmp_path / "hold.json"
    common = ["--store", str(store), "--baseline", str(baseline),
              "--images-only", "--skip-mounts"]
    assert _run(["record", *common]).returncode == 0
    first = json.loads(baseline.read_text())
    assert _run(["check", *common]).returncode == 0
    assert json.loads(baseline.read_text()) == first
    image.write_bytes(image.read_bytes() + b"y")
    assert _run(["check", *common]).returncode == 0
    image.write_bytes(b"")
    shrunk = _run(["check", *common])
    assert shrunk.returncode == 1
    assert "image_shrunk" in shrunk.stderr
    image.write_bytes(b"x" * 8192)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"x" * 8192)
    image.unlink()
    replacement.replace(image)
    replaced = _run(["check", *common])
    assert replaced.returncode == 1
    assert "image_replaced" in replaced.stderr


def test_images_only_check_ignores_a_grant_drop(tmp_path: Path):
    data = tmp_path / "data"
    store = tmp_path / "owners"
    _image(store, 300031)
    _db(data / ".broker" / "state" / "owner-identities.db", [
        ("a@example.invalid", 300031, 1, None, None, 1),
    ])
    ledger = data / ".broker" / "outbound.db"
    with sqlite3.connect(ledger) as conn:
        conn.execute(
            "CREATE TABLE outbound_connection_grants "
            "(grant_id TEXT PRIMARY KEY, revoked_at REAL)")
        conn.execute("INSERT INTO outbound_connection_grants VALUES ('g1', NULL)")
    baseline = tmp_path / "hold.json"
    assert _run([
        "record", "--store", str(store), "--data-root", str(data),
        "--baseline", str(baseline), "--skip-mounts"]).returncode == 0
    with sqlite3.connect(ledger) as conn:
        conn.execute("DELETE FROM outbound_connection_grants")
    images_only = _run([
        "check", "--store", str(store), "--data-root", str(data),
        "--baseline", str(baseline), "--images-only", "--skip-mounts"])
    assert images_only.returncode == 0, images_only.stderr
    compared = _run([
        "audit", "--store", str(store), "--data-root", str(data),
        "--baseline", str(baseline), "--skip-mounts"])
    assert compared.returncode == 3
    doc = json.loads(compared.stdout)
    assert "grants_dropped" in doc["failures"]
    assert doc["baseline"] == "mismatch"
    _assert_private(compared.stdout)


def test_mounted_tree_counts_runs_and_serving_bindings(tmp_path: Path):
    root = tmp_path / "300041"
    root.mkdir()
    (root / ".langgraph_runs.db").write_bytes(b"m" * 300)
    child = root / "u-center"
    child.mkdir()
    (child / "checkpoints.db").write_bytes(b"c" * 20)
    (child / "activity.log").write_bytes(b"")
    runs = root / ".runs.db"
    with sqlite3.connect(runs) as conn:
        conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO runs VALUES ('r1')")
        conn.execute("INSERT INTO runs VALUES ('r2')")
    tiny = root / ".tinyassets.db"
    with sqlite3.connect(tiny) as conn:
        conn.execute(
            "CREATE TABLE agent_bindings (status TEXT, configuration_json TEXT)")
        conn.execute("INSERT INTO agent_bindings VALUES ('serving', 'secret-config')")
        conn.execute("INSERT INTO agent_bindings VALUES ('configured', 'other')")
    marker = root / ".ta-cli" / "claude-code"
    marker.mkdir(parents=True)
    (marker / "connection").write_text("marker-bytes", encoding="utf-8")
    measured = audit.measure_tree(root)
    assert measured["mounted"] is True
    assert measured["runs_rows"] == 2
    assert measured["serving_bindings"] == 1
    assert measured["subscription_markers"] == 1
    assert measured["checkpoints_bytes"] == 20
    assert measured["run_store_bytes"] >= 300
    blob = json.dumps(measured)
    assert "secret-config" not in blob
    same = {"device": 1, "inode": 1, "size": 1, "blocks": 1}
    dropped = audit.compare_hold(
        {"images": {"300041": {**same, "runs_rows": 2, "serving_bindings": 1,
                               "subscription_markers": 1, "run_store_bytes": 300}},
         "grants": 1, "subscription_files": 1, "subscription_bytes": 10},
        {"images": {"300041": {**same, "runs_rows": 0, "serving_bindings": 0,
                               "subscription_markers": 0, "run_store_bytes": 0}},
         "grants": 0, "subscription_files": 0, "subscription_bytes": 0},
        images_only=False)
    assert "runs_dropped" in dropped
    assert "serving_bindings_dropped" in dropped
    assert "markers_dropped" in dropped
    assert "grants_dropped" in dropped
    asleep = audit.compare_hold(
        {"images": {"300041": {"device": 1, "inode": 1, "size": 1, "blocks": 1,
                               "runs_rows": 2}}, "grants": None},
        {"images": {"300041": {"device": 1, "inode": 1, "size": 1, "blocks": 1,
                               "runs_rows": None}}, "grants": None},
        images_only=False)
    assert asleep == []


def test_hold_calls_bracket_the_boxhostd_restart():
    script = (REPO / "deploy" / "deploy_fail_safe.sh").read_text(encoding="utf-8")
    for marker in (
            'log "box layout: already owner-box; no move"',
            'echo "cutover_admission_pending=true"',
    ):
        block = script.split(marker, 1)[1].split(";;", 1)[0]
        assert block.index("owner_image_hold record") < block.index(
            "systemctl restart boxhostd.service")
        assert block.index("systemctl restart boxhostd.service") < block.index(
            "owner_image_hold check")
    start = script.index("owner_image_hold() {")
    end = script.index("\nrestart_stack() {", start)
    function = script[start:end]
    # A store that does not exist is a no-op, which is what deploy tests hit.
    prelude = "log() { printf '%s\\n' \"$*\"; }\nerr() { printf '%s\\n' \"$*\" >&2; }\n"
    completed = subprocess.run(
        ["bash", "-c", prelude + function + "\nowner_image_hold record"],
        capture_output=True, text=True, check=False,
        env={**os.environ, "BOX_STORE_DIR": "/tmp/tinyassets-no-such-owner-store",
             "BUNDLE_STATE_DIR": "/tmp"})
    assert completed.returncode == 0, completed.stderr
    assert "nothing to hold" in completed.stdout


def test_audit_dispatch_skips_the_image_swap():
    text = (REPO / ".github" / "workflows" / "deploy-prod.yml").read_text(encoding="utf-8")
    assert "owner_volume_audit_only" in text
    assert "Does not swap the image" in text
    assert "scripts/owner_volume_audit.py" in text
    assert "OWNER_VOLUME_AUDIT=/tmp/owner_volume_audit.py" in text
    assert "sudo python3 - audit --baseline /var/lib/tinyassets-deploy/owner-image-hold.json" in text
    deploy = text.split("\n  deploy:\n", 1)[1].split("\n    runs-on:", 1)[0]
    assert "inputs.owner_volume_audit_only != true" in deploy
    assert "github.event.workflow_run.conclusion == 'success'" in deploy
    audit_job = text.split("\n  owner-volume-audit:\n", 1)[1].split("\n  deploy:\n", 1)[0]
    assert "inputs.owner_volume_audit_only" in audit_job
    assert "environment: production" in audit_job


def test_measure_tree_does_not_follow_a_run_store_symlink(tmp_path: Path):
    root = tmp_path / "tree"
    root.mkdir()
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"z" * 50)
    (root / ".runs.db").symlink_to(outside)
    measured = audit.measure_tree(root)
    assert measured["runs_rows"] is None
    assert measured["run_store_bytes"] == 0
    assert not (root / ".runs.db-wal").exists()
