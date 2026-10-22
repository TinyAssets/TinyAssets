"""The synthetic workload must reach the real owner-split census."""

import json
import os
import socket
import sqlite3
import stat
import subprocess
from pathlib import Path

import pytest

from scripts.live_like.fixture import build
from tinyassets.box_layout_migration import census
from tinyassets.storage import platform_state_move as psm

pytestmark = pytest.mark.verifies("live-like-deploy-gate:G5")


def test_fixture_is_production_shaped_and_cutover_ready(tmp_path):
    summary = build(tmp_path)
    assert summary["owners"] >= 3
    assert summary["centers"] >= 5
    assert summary["workspace_files"] >= 3000
    assert summary["sqlite_stores"] >= 5
    marker = json.loads((tmp_path / ".layout.json").read_text())
    assert marker[psm.MOVES] == {psm.CONSENTS: psm.DONE}
    for relative in (".tinyassets.db", ".runs.db", ".automations.db",
                     ".engine_run_admissions.db", ".broker/state/owner-identities.db"):
        with sqlite3.connect(tmp_path / relative) as db:
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    report = census(tmp_path)
    assert report["ready"], report["refusals"]
    assert report["owners"] == 3
    assert report["admitted_centers"] == 5
    broker_state = tmp_path / ".broker/state"
    identity_db = broker_state / "owner-identities.db"
    assert (broker_state.stat().st_uid, stat.S_IMODE(broker_state.stat().st_mode)) == (1002, 0o700)
    assert (identity_db.stat().st_uid, stat.S_IMODE(identity_db.stat().st_mode)) == (1002, 0o600)
    assert (tmp_path / "a1").stat().st_gid == 300001
    assert (tmp_path / "b1").stat().st_gid == 300002
    assert (tmp_path / "c1").stat().st_gid == 300003


def test_workload_shells_parse():
    root = Path(__file__).resolve().parents[1]
    for name in ("s3_remote.sh", "make_live.sh"):
        subprocess.run(["bash", "-n", str(root / "scripts/live_like" / name)], check=True)


def test_built_fixture_passes_snapshot_inventory_with_live_socket(tmp_path):
    root = tmp_path / "_data"
    root.mkdir()
    build(root)
    sidecar = root / ".universe-sidecars/a1" / f"egress-{os.getpid()}.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(sidecar))
        listener.listen(1)
        assert stat.S_ISSOCK(sidecar.stat().st_mode)

        archive = tmp_path / "snapshot.tar.gz"
        subprocess.run([
            "tar", "-czf", str(archive), "--numeric-owner", "--acls", "--xattrs",
            "--exclude=_data/.broker/browser-vault", "-C", str(tmp_path), "_data",
        ], check=True)
        backup = (Path(__file__).resolve().parents[1] / "deploy/backup.sh").read_text()
        inventory = backup.split("<<'INVENTORY_PY'\n", 1)[1].split("\nINVENTORY_PY", 1)[0]
        result = subprocess.run(
            ["python3", "-c", inventory, str(root), str(archive)],
            capture_output=True, text=True, check=True,
        )
        assert int(result.stdout.strip()) > 0
