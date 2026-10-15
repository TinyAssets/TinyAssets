"""The stopped, snapshotted Activities cutover and its restore boundary."""
import hashlib
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from tinyassets import agent_activities as activities
from tinyassets import runs
from tinyassets.activity_shape_migration import apply, check
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


def test_conversion_is_per_owner_idempotent_and_restoreable(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    good = _legacy(data, "one", "owner-one")
    refused = _legacy(data, "two", "owner-two", run_owner="owner-one")
    backup = tmp_path / "tinyassets-snapshot-test.tar.gz"
    _snapshot(data, backup)

    assert check(data) == 10
    report = apply(data, backup)
    assert json.loads((data / ".layout.json").read_text())["state"] == "stable"
    assert report["one"]["converted"] == 1
    assert report["two"]["failed"] == 1
    assert activities.get(data / "one", good)["status"] == activities.SCHEDULED
    denied = activities.get(data / "two", refused)
    assert denied["status"] == activities.FAILED
    assert "cross-owner" in denied["result_summary"]
    assert "cross-owner" in activities.events_page(data / "two", refused)["events"][-1]["line"]
    prompt, node = _activity_instructions(data / "one", activities.get(data / "one", good))
    assert prompt == "Owner's exact text: Do it\nDetails\n"
    assert node == {"tools_allowed": ["agent", "read", "bash"]}
    with sqlite3.connect(data / ".runs.db") as db:
        status = db.execute("SELECT status FROM runs WHERE run_id='run-one'").fetchone()[0]
        assert status == "failed"
    assert check(data) == 0
    assert apply(data, backup) == report

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    with tarfile.open(backup, "r:gz") as archive:
        archive.extractall(restore_dir, filter="data")
    restored = restore_dir / "data"
    assert check(restored) == 10
    assert activities.get(restored / "one", good)["status"] == activities.IN_PROGRESS
    assert not (restored / "one" / "Activities.md").exists()
    assert not (restored / ".layout.json").exists()


def test_cross_owner_definition_is_not_copied(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    activity_id = _legacy(data, "one", "owner-one")
    with sqlite3.connect(data / ".tinyassets.db") as db:
        db.execute("UPDATE branch_definitions SET author='owner-two'")
    backup = tmp_path / "tinyassets-snapshot-test.tar.gz"
    _snapshot(data, backup)

    apply(data, backup)
    assert activities.get(data / "one", activity_id)["status"] == activities.FAILED
    assert not (data / "one" / "Activities.md").exists()
    assert "different owner" in (data / "one" / "Activities.error.txt").read_text()


def test_retired_activities_branch_cannot_be_started_as_a_workflow(tmp_path):
    with pytest.raises(PermissionError, match="Activities workflow was retired"):
        runs._prepare_run(
            tmp_path, branch=BranchDefinition(branch_def_id="activities::one"),
            inputs={}, run_name="", actor="owner-one",
        )
    assert not (tmp_path / ".runs.db").exists()
    assert _failure_pause_reason("The Activities workflow was retired") == (
        "run_admission_refused")
