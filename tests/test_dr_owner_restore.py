"""The DR drill accepts only a verified platform manifest's actual map binding."""
import hashlib
import json
import re
import runpy
import sqlite3
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def recovery_point(tmp_path):
    database = tmp_path / "map.db"
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE owner_identities (principal TEXT, machine_id INT);
            CREATE TABLE box_records (principal TEXT, machine INT);
            CREATE TABLE owner_volumes (principal TEXT, uid INT, snapshot_id TEXT,
                                        snapshot_sha256 TEXT, volume_generation INT);
            INSERT INTO owner_identities VALUES ('alice',300001),('bob',300002);
            INSERT INTO box_records VALUES ('alice',300001);
        """)
        db.execute("INSERT INTO owner_volumes VALUES ('alice',300001,?,?,1)",
                   ("a" * 32, "b" * 64))
    archive = tmp_path / "tinyassets-data-2026-10-19T00-00-00Z.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(database, arcname="_data/.broker/state/owner-identities.db")
        link = tarfile.TarInfo("_data/shared-link")
        link.type, link.linkname = tarfile.SYMTYPE, ".broker/state/owner-identities.db"
        bundle.addfile(link)
    manifest = tmp_path / "tinyassets-platform-2026-10-19T00-00-00Z.json"
    document = {"version": 1, "archive": archive.name,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "snapshots": [{"uid": 300001, "snapshot_id": "a" * 32, "sha256": "b" * 64}]}
    manifest.write_text(json.dumps(document))
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    return manifest, archive, work, document


def test_drill_selects_only_the_manifest_and_map_reference(recovery_point):
    manifest, archive, work, _ = recovery_point
    select = runpy.run_path(str(ROOT / "deploy/dr-owner-restore.py"))["select"]
    selected = select(manifest, archive, work)
    assert selected[:5] == ("alice", 300001, "a" * 32, "b" * 64, 1)


def test_workflow_preflight_accepts_c3_preserved_shared_symlink(
        recovery_point, monkeypatch, capsys):
    _, archive, _, document = recovery_point
    workflow = yaml.safe_load((ROOT / ".github/workflows/dr-drill.yml").read_text())
    step = next(step for job in workflow["jobs"].values() for step in job.get("steps", [])
                if step.get("name") == "Validate selected backup")
    program = re.search(r"<<'PY'\n(.*?)\nPY", step["run"], re.S)[1]
    monkeypatch.setattr(sys, "argv", ["-", str(archive), str(archive.parent)])
    monkeypatch.setattr(sys, "path", list(sys.path))
    exec(compile(program, "dr-preflight", "exec"), {"__name__": "__main__"})
    assert json.loads(capsys.readouterr().out)["archive_sha256"] == document["sha256"]


@pytest.mark.parametrize("corrupt", ["archive", "uid", "snapshot", "digest", "empty"])
def test_drill_refuses_unverified_or_substituted_recovery_reference(recovery_point, corrupt):
    manifest, archive, work, document = recovery_point
    if corrupt == "archive":
        with archive.open("ab") as stream:
            stream.write(b"modified")
    elif corrupt == "empty":
        document["snapshots"] = []
    else:
        key, value = {"uid": ("uid", 300002), "snapshot": ("snapshot_id", "c" * 32),
                      "digest": ("sha256", "d" * 64)}[corrupt]
        document["snapshots"][0][key] = value
    manifest.write_text(json.dumps(document))
    select = runpy.run_path(str(ROOT / "deploy/dr-owner-restore.py"))["select"]
    with pytest.raises(ValueError, match="digest mismatch|map-referenced snapshot"):
        select(manifest, archive, work)
