"""The synthetic workload must reach the real owner-split census."""

import sqlite3
import subprocess
from pathlib import Path

from scripts.live_like.fixture import build
from tinyassets.box_layout_migration import census


def test_fixture_is_production_shaped_and_cutover_ready(tmp_path):
    summary = build(tmp_path)
    assert summary["owners"] >= 3
    assert summary["centers"] >= 5
    assert summary["workspace_files"] >= 3000
    assert summary["sqlite_stores"] >= 5
    for relative in (".tinyassets.db", ".runs.db", ".automations.db",
                     ".engine_run_admissions.db", ".broker/state/owner-identities.db"):
        with sqlite3.connect(tmp_path / relative) as db:
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    report = census(tmp_path)
    assert report["ready"], report["refusals"]
    assert report["owners"] == 3
    assert report["admitted_centers"] == 5


def test_workload_shells_parse():
    root = Path(__file__).resolve().parents[1]
    for name in ("s3_remote.sh", "make_live.sh"):
        subprocess.run(["bash", "-n", str(root / "scripts/live_like" / name)], check=True)
