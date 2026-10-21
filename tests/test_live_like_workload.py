"""The synthetic workload must reach the real owner-split census."""

import json
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
