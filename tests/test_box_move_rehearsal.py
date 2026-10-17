"""Entire archive rehearsal on synthetic bytes and a real XFS quota mount."""

import io
import json
import os
import re
import subprocess
import tarfile
import uuid
from pathlib import Path

import pytest

from scripts.archive_rehearsal import archive_rehearsal
from scripts.rehearse_box_move import validate_archive
from tests.test_owner_cutover_census import _sql
from tests.test_owner_cutover_census import source as source

ROOT = Path(__file__).resolve().parents[1]


def test_report_failure_does_not_disclose_diagnostics(monkeypatch, capsys):
    from scripts import archive_rehearsal as harness

    class FailedVenue:
        def __init__(self, *_):
            pass

        def setup(self, _):
            raise RuntimeError("private-owner /private/archive synthetic-private-content")

        def close(self):
            pass

    monkeypatch.setattr(harness, "Venue", FailedVenue)
    with pytest.raises(RuntimeError):
        harness.archive_rehearsal("unused", "unused", store_bytes=1)
    captured = capsys.readouterr()
    assert captured.out == "setup FAIL\ncleanup PASS\n"
    assert captured.err == ""


@pytest.mark.parametrize("mode", ["deploy", "--restore-bundle"])
def test_deploy_refuses_crash_input_before_any_host_action(mode):
    import shutil

    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    result = subprocess.run(
        [bash, str(ROOT / "deploy/deploy_fail_safe.sh"), mode],
        env={**os.environ, "CUTOVER_CRASH_AT": "after_seeding",
             "BOX_MIGRATION_DISPOSABLE": "1"},
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.strip() == "deploy refused: rehearsal fault injection is forbidden"


def test_disposable_runner_refuses_fault_on_arbitrary_paths():
    import shutil

    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    result = subprocess.run(
        [bash, str(ROOT / "deploy/deploy_fail_safe.sh"), "--box-migration-container",
         "unapproved-data", "/unapproved-store", "/dev/loop0", "/unapproved-backup",
         "unused-image", "apply", "/data"],
        env={**os.environ, "CUTOVER_CRASH_AT": "after_seeding"},
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.strip() == "rehearsal fault requires disposable volumes"


def test_migration_container_overrides_image_fault_default(tmp_path):
    import shutil

    fake = tmp_path / "docker"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n', encoding="utf-8")
    fake.chmod(0o755)
    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    result = subprocess.run(
        [bash, str(ROOT / "deploy/deploy_fail_safe.sh"), "--box-migration-container",
         "disposable-data", "/disposable-store", "/dev/loop0", "/disposable-backup",
         "unused-image", "check", "/data"],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
             "CUTOVER_CRASH_AT": "", "MSYS_NO_PATHCONV": "1"},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    args = result.stdout.splitlines()
    assert args[args.index("CUTOVER_CRASH_AT=") - 1] == "-e"
    assert args[-5:] == ["-B", "-m", "tinyassets.box_layout_migration", "check", "/data"]


def test_synthetic_archive_rehearses_final_cutover(source, tmp_path, monkeypatch, capsys):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip(
            "requires linux_oracle.py --as-root; owner=c6-owner-box runs-in=linux_oracle.py"
        )
    base = os.environ["TA_REHEARSAL_BASE_IMAGE"]  # requires --docker-engine
    from tinyassets import box_layout_migration as migration
    from tinyassets.conversation_store import record_turn
    from tinyassets.daemon_server import grant_universe_ownership, set_founder_home
    from tinyassets.owner_lease import lease_db
    from tinyassets.runs import initialize_runs_db
    from tinyassets.storage.outbound_connections import ConnectionLedger
    from tinyassets.storage.pending_requests import create_request

    # Use full production schemas for the serving proof; the abbreviated fixture
    # above supplies admissions, plans and HTTP custody, never real account data.
    (source / ".tinyassets.db").unlink()
    (source / "outbound.db").unlink()
    import shutil

    shutil.rmtree(source / ".platform")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(source))
    ConnectionLedger(
        source / "outbound.db", data_root=source, verify_authenticated_principal=lambda: "alice"
    )
    for owner, centers, uid in (("alice", ("a1", "a2"), 300001), ("bob", ("b1", "b2"), 300002)):
        for center in centers:
            folder = source / center
            os.chown(folder, 1001, uid)
            grant_universe_ownership(source, universe_id=center, owner_id=owner)
            (folder / "soul.md").write_text("Synthetic owner home")
            record_turn(folder, "principal:" + owner, "founder", "synthetic history")
            assert create_request(
                folder,
                kind="Question",
                title="Synthetic request",
                body="synthetic question",
                fields=[],
                action={"type": "answer"},
                dedupe_key="synthetic",
            )
        set_founder_home(source, founder_sub=owner, universe_id=centers[0], platform_generated=True)
    initialize_runs_db(source)
    _sql(
        source / ".runs.db",
        """
        INSERT INTO runs
          (run_id,owner_user_id,queue_universe_id,thread_id,branch_def_id,actor,started_at,status)
        VALUES ('owned','alice','a1','thread-a','branch-a','alice',1,'completed'),
          ('old','',NULL,'thread-old','old','',1,'completed'),
          ('unknown','unadmitted',NULL,'thread-u','unknown','unadmitted',1,'completed'),
          ('center','','absent','thread-c','center','',1,'completed');
        INSERT INTO run_events (run_id,step_index,node_id,status,started_at)
        VALUES ('missing',0,'orphan','completed',1);
    """,
    )
    with lease_db(source) as db:
        db.execute("INSERT INTO owner_lease VALUES "
                   "('cc:a1',93,'retired-tree','synthetic-proof','released','then','later')")
    _sql(
        source / ".tinyassets.db",
        """
        INSERT INTO author_definitions
          (author_id,display_name,soul_text,soul_hash,created_at)
          VALUES ('published-synthetic','Synthetic','Synthetic','hash',1);
        CREATE TABLE external_agent_job_receipts (owner_user_id TEXT);
        INSERT INTO external_agent_job_receipts VALUES ('alice');
    """,
    )
    _sql(
        source / ".outside-client-authority.sqlite3",
        """
        CREATE TABLE outside_switch (id INTEGER PRIMARY KEY,enabled INTEGER);
        INSERT INTO outside_switch VALUES (1,1);
    """,
    )
    home = source / ".platform/owners/300001/agent-home"
    home.mkdir(parents=True)
    (home / "session").write_text("synthetic retired CLI state")
    report = migration.census(source)
    assert report["ready"], report["refusals"]
    assert {table["class"] for table in report["tables"].values() if table["total"]} == {
        "owner",
        "router",
        "broker",
        "published",
        "regenerated",
        "dropped",
    }
    assert sum(t["dropped"] for t in report["tables"].values()) >= 4
    assert {reason for table in report["tables"].values() for reason in table["reasons"]} == {
        "no_owner_key",
        "unadmitted_principal",
        "unknown_center",
        "orphan_reference",
    }
    import gc

    gc.collect()
    archive = tmp_path / "tinyassets-data-2026-10-17T00-00-00Z.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(source, arcname="_data")
    assert validate_archive(archive) > 0
    image = "tinyassets-s6-synthetic:" + uuid.uuid4().hex[:12]
    context = io.BytesIO()
    with tarfile.open(fileobj=context, mode="w") as stream:
        payload = (
            f"FROM {base}\nWORKDIR /app\nENV PYTHONPATH=/app\n"
            "COPY tinyassets /app/tinyassets\n"
            "COPY deploy/box_project_readback.py /app/deploy/box_project_readback.py\n"
            "COPY data/world_rules.lp /app/data/world_rules.lp\n"
        ).encode()
        member = tarfile.TarInfo("Dockerfile")
        member.size = len(payload)
        stream.addfile(member, io.BytesIO(payload))
        stream.add(
            ROOT / "tinyassets",
            arcname="tinyassets",
            filter=lambda m: None if "__pycache__" in m.name else m,
        )
        stream.add(
            ROOT / "deploy/box_project_readback.py", arcname="deploy/box_project_readback.py"
        )
        stream.add(ROOT / "data/world_rules.lp", arcname="data/world_rules.lp")
    built = subprocess.run(
        ["docker", "build", "--network=none", "-q", "-t", image, "-"],
        input=context.getvalue(),
        capture_output=True,
    )
    assert built.returncode == 0, built.stderr.decode()
    from scripts import archive_rehearsal as harness

    diagnostics = []
    original_run = harness.run

    def diagnosed(*args, **kwargs):
        result = original_run(*args, **{**kwargs, "check": False})
        if result.returncode not in {0, 137} or kwargs.get("env", {}).get(
            "CUTOVER_CRASH_AT"
        ):
            diagnostics.append(result.stdout + result.stderr)
        if kwargs.get("check", True) and result.returncode:
            raise RuntimeError("synthetic operation failed")
        return result

    monkeypatch.setattr(harness, "run", diagnosed)
    try:
        try:
            result = archive_rehearsal(image, archive, store_bytes=1024**3)
        except Exception as error:
            raise AssertionError(str(error) + "\n" + "\n".join(diagnostics)) from error
        assert set(result) == {
            "setup",
            "preflight",
            "census",
            "apply",
            "verify",
            "restore_before_write",
            "crash_resume",
            "serving",
            "restore_after_write",
            "cleanup",
        }
        assert all(step["status"] == "PASS" for step in result.values())
        assert result["crash_resume"]["interruptions"] == 2
        assert result["crash_resume"]["comparisons"] == 2
        assert result["serving"] == {
            "status": "PASS", "owners": 2, "centers": 4, "history": 4,
            "history_turns": 4, "pending": 4, "pending_saved": 4,
            "controls": 8, "plans": 8, "writes": 4, "reconnect": 2,
        }
        assert result["restore_before_write"]["comparisons"] == 1
        assert result["restore_after_write"]["comparisons"] == 1
        captured = capsys.readouterr()
        assert captured.err == ""
        assert len(captured.out.splitlines()) == len(result)
        for line in captured.out.splitlines():
            assert re.fullmatch(r"[a-z_]+ PASS(?: [a-z_]+=\d+)*", line)
        for step in result.values():
            assert all(type(value) is int and value >= 0
                       for key, value in step.items() if key != "status")
        output = captured.out + json.dumps(result)
        for private in (
            "alice",
            "bob",
            "a1",
            "a2",
            "b1",
            "b2",
            "300001",
            "300002",
            "synthetic history",
            str(tmp_path),
            "/data",
            "/box-store",
            str(archive),
        ):
            assert private not in output
    finally:
        subprocess.run(["docker", "image", "rm", image], capture_output=True, check=True)
