"""The stopped, snapshotted Activities cutover and its restore boundary."""
import hashlib
import json
import shutil
import sqlite3
import tarfile
from pathlib import Path

import pytest

from tinyassets import agent_activities as activities
from tinyassets import box_layout_migration as migration
from tinyassets import runs
from tinyassets.api import activities as activity_api
from tinyassets.automations import _failure_pause_reason
from tinyassets.branches import BranchDefinition
from tinyassets.control_plane.session import _activity_instructions


def _legacy(data: Path, center: str, owner: str, *, run_owner: str | None = None):
    root = data / center
    root.mkdir()
    with sqlite3.connect(data / ".tinyassets.db") as db:
        db.execute("CREATE TABLE IF NOT EXISTS universe_owner "
                   "(universe_id TEXT, owner_id TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS branch_definitions "
                   "(branch_def_id TEXT, author TEXT, node_defs_json TEXT, graph_json TEXT)")
        db.execute("INSERT INTO universe_owner VALUES (?, ?)", (center, owner))
        db.execute("INSERT INTO branch_definitions VALUES (?, ?, ?, ?)", (
            f"activities::{center}", owner,
            json.dumps([{"prompt_template": "Owner's exact text: {title}\n{brief}\n",
                         "tools_allowed": ["agent", "read", "bash"]}]),
            json.dumps({"nodes": [{"id": "agent"}]}),
        ))
    record = activities.create(root, owner_principal=owner, title="Do it", brief="Details",
                               origin_kind="ask")
    generation = activities.claim(root, record["activity_id"], replaceable=lambda _: False)
    assert generation == 1
    runs.initialize_runs_db(data)
    with sqlite3.connect(data / ".runs.db") as db:
        db.execute("INSERT INTO runs "
                   "(run_id,branch_def_id,thread_id,status,actor,owner_user_id,"
                   "queue_universe_id,started_at) "
                   "VALUES (?, ?, ?, 'queued', ?, ?, ?, 1)",
                   (f"run-{center}", f"activities::{center}", f"activity:{center}",
                    f"universe:{center}", run_owner or owner, center))
    assert activities.bind_run(root, record["activity_id"], 1, f"run-{center}")
    return record["activity_id"]


def _snapshot(data: Path, backup: Path):
    with tarfile.open(backup, "w:gz") as archive:
        archive.add(data, arcname="data")
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    backup.with_name(backup.name + ".sha256").write_text(
        f"{digest}  {backup.name}\n", encoding="ascii")


def _boxed(data: Path, store: Path, owners: dict[str, tuple[str, int]]):
    (data / ".broker" / "state").mkdir(parents=True)
    with sqlite3.connect(data / ".broker" / "state" / "owner-identities.db") as db:
        db.execute("CREATE TABLE owner_identities (principal TEXT, machine_id INTEGER)")
        db.execute("CREATE TABLE box_records (principal TEXT, machine INTEGER)")
        for center, (owner, uid) in owners.items():
            db.execute("INSERT INTO owner_identities VALUES (?, ?)", (owner, uid))
            db.execute("INSERT INTO box_records VALUES (?, ?)", (owner, uid))
            target = store / str(uid) / center
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(data / center, target)
    (data / ".layout.lock").touch()
    (data / ".layout.json").write_text(json.dumps({
        "layout": 2, "state": "stable", "box_layout": "owner-box",
    }))


def _apply_boxed(data: Path, store: Path, monkeypatch):
    monkeypatch.setattr(migration.os, "geteuid", lambda: 0)
    monkeypatch.setattr(migration, "_verified_pair", lambda *_: None)
    monkeypatch.setattr(migration, "_mounted", lambda *_: None)
    migration.apply(data, store, Path("unused-data-backup"), Path("unused-box-backup"))


def _run_row(data: Path, run_id: str) -> tuple:
    with sqlite3.connect(data / ".runs.db") as db:
        return db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()


def test_conversion_is_per_owner_idempotent_and_restoreable(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    good = _legacy(data, "one", "owner-one")
    refused = _legacy(data, "two", "owner-two", run_owner="owner-one")
    store = tmp_path / "box-store"
    _boxed(data, store, {"one": ("owner-one", 300001), "two": ("owner-two", 300002)})
    backup = tmp_path / "tinyassets-snapshot-test.tar.gz"
    _snapshot(data, backup)
    box_backup = tmp_path / "tinyassets-box-store-test.tar.gz"
    _snapshot(store, box_backup)
    foreign_before = _run_row(data, "run-two")

    assert migration.check(data) == 10
    _apply_boxed(data, store, monkeypatch)
    assert json.loads((data / ".layout.json").read_text())["state"] == "stable"
    assert json.loads((data / ".layout.json").read_text())["activity_shape"] == "v1"
    assert activities.get(data / "one", good)["status"] == activities.SCHEDULED
    denied = activities.get(data / "two", refused)
    assert denied["status"] == activities.FAILED
    assert "cross-owner" in denied["result_summary"]
    assert "cross-owner" in activities.events_page(data / "two", refused)["events"][-1]["line"]
    assert _run_row(data, "run-two") == foreign_before
    prompt, node = _activity_instructions(
        store / "300001" / "one", activities.get(data / "one", good))
    assert prompt == "Owner's exact text: Do it\nDetails\n"
    assert node == {"tools_allowed": ["agent", "read", "bash"]}
    with sqlite3.connect(data / ".runs.db") as db:
        status = db.execute("SELECT status FROM runs WHERE run_id='run-one'").fetchone()[0]
        assert status == "failed"
    assert migration.check(data) == 0

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    with tarfile.open(backup, "r:gz") as archive:
        archive.extractall(restore_dir, filter="data")
    restored = restore_dir / "data"
    assert migration.check(restored) == 10
    assert activities.get(restored / "one", good)["status"] == activities.IN_PROGRESS
    with tarfile.open(box_backup, "r:gz") as archive:
        archive.extractall(restore_dir / "box", filter="data")
    assert not (restore_dir / "box" / "data" / "300001" / "one" / "Activities.md").exists()


def test_missing_owner_box_record_is_reported_and_retried(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    data.mkdir()
    good = _legacy(data, "one", "owner-one")
    delayed = _legacy(data, "two", "owner-two")
    store = tmp_path / "box-store"
    _boxed(data, store, {"one": ("owner-one", 300001), "two": ("owner-two", 300002)})
    with sqlite3.connect(data / ".broker" / "state" / "owner-identities.db") as db:
        db.execute("DELETE FROM box_records WHERE principal='owner-two'")
    delayed_activity = activities.get(data / "two", delayed)
    delayed_run = _run_row(data, "run-two")

    _apply_boxed(data, store, monkeypatch)

    assert activities.get(data / "one", good)["status"] == activities.SCHEDULED
    assert activities.get(data / "two", delayed) == delayed_activity
    assert _run_row(data, "run-two") == delayed_run
    marker = json.loads((data / ".layout.json").read_text())
    assert marker["state"] == "stable"
    assert "activity_shape" not in marker
    assert "no box record" in marker["activity_shape_failures"]["two"]
    assert "no box record" in capsys.readouterr().err
    assert "no box record" in activity_api.read(data, universe_id="two")["migration_error"]
    assert "migration_error" not in activity_api.read(data, universe_id="one")
    assert migration.check(data) == migration.PENDING

    with sqlite3.connect(data / ".broker" / "state" / "owner-identities.db") as db:
        db.execute("INSERT INTO box_records VALUES ('owner-two', 300002)")
    _apply_boxed(data, store, monkeypatch)

    assert activities.get(data / "two", delayed)["status"] == activities.SCHEDULED
    marker = json.loads((data / ".layout.json").read_text())
    assert marker["activity_shape"] == "v1"
    assert marker["activity_shape_failures"] == {}
    assert "migration_error" not in activity_api.read(data, universe_id="two")
    assert migration.check(data) == 0


def test_cross_owner_definition_is_not_copied(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    activity_id = _legacy(data, "one", "owner-one")
    with sqlite3.connect(data / ".tinyassets.db") as db:
        db.execute("UPDATE branch_definitions SET author='owner-two'")
    store = tmp_path / "box-store"
    _boxed(data, store, {"one": ("owner-one", 300001)})

    _apply_boxed(data, store, monkeypatch)
    assert activities.get(data / "one", activity_id)["status"] == activities.FAILED
    assert not (store / "300001" / "one" / "Activities.md").exists()
    assert "different owner" in (store / "300001" / "one" / "Activities.error.txt").read_text()


def test_foreign_activity_row_is_untouched(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    _legacy(data, "one", "owner-one")
    foreign = activities.create(data / "one", owner_principal="owner-two",
                                title="Foreign", brief="Other owner's work", origin_kind="ask")
    before = activities.get(data / "one", foreign["activity_id"])
    store = tmp_path / "box-store"
    _boxed(data, store, {"one": ("owner-one", 300001)})

    _apply_boxed(data, store, monkeypatch)

    assert activities.get(data / "one", foreign["activity_id"]) == before


def test_retired_activities_branch_cannot_be_started_as_a_workflow(tmp_path):
    with pytest.raises(PermissionError, match="Activities workflow was retired"):
        runs._prepare_run(
            tmp_path, branch=BranchDefinition(branch_def_id="activities::one"),
            inputs={}, run_name="", actor="owner-one",
        )
    assert not (tmp_path / ".runs.db").exists()
    assert _failure_pause_reason("The Activities workflow was retired") == (
        "run_admission_refused")
