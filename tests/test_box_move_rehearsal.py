"""Entire archive rehearsal on synthetic bytes and a real XFS quota mount."""

import io
import json
import os
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


def test_synthetic_archive_rehearses_final_cutover(source, tmp_path, monkeypatch, capsys):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip(
            "requires linux_oracle.py --as-root; owner=c6-owner-box runs-in=linux_oracle.py"
        )
    base = os.environ["TA_REHEARSAL_BASE_IMAGE"]  # requires --docker-engine
    from tinyassets import box_layout_migration as migration
    from tinyassets.conversation_store import record_turn
    from tinyassets.daemon_server import grant_universe_ownership, set_founder_home
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
    _sql(
        source / ".runs.db",
        """
        CREATE TABLE runs (run_id TEXT,owner_user_id TEXT,queue_universe_id TEXT,thread_id TEXT);
        INSERT INTO runs VALUES ('owned','alice','a1','thread-a'),
          ('old',NULL,NULL,'thread-old'), ('unknown','unadmitted',NULL,'thread-u'),
          ('center',NULL,'absent','thread-c');
        CREATE TABLE run_events (run_id TEXT,event TEXT);
        INSERT INTO run_events VALUES ('missing','orphan');
    """,
    )
    _sql(
        source / ".owner_leases.db",
        """
        CREATE TABLE owner_lease (fence INTEGER);
        INSERT INTO owner_lease VALUES (93);
    """,
    )
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
            f"FROM {base}\nWORKDIR /app\nCOPY tinyassets /app/tinyassets\n"
            "COPY deploy/box_project_readback.py /app/deploy/box_project_readback.py\n"
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
            "BOX_REHEARSAL_INTERRUPT"
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
        assert result["serving"]["owners"] == 2
        assert result["serving"]["centers"] == 4
        output = capsys.readouterr().out + json.dumps(result)
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
