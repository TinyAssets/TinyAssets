"""Entire archive rehearsal on synthetic bytes and a real XFS quota mount."""

import io
import json
import os
import re
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path

import pytest

from scripts.archive_rehearsal import archive_rehearsal
from scripts.rehearse_box_move import validate_archive
from tests.test_owner_cutover_census import _sql
from tests.test_owner_cutover_census import source as source

ROOT = Path(__file__).resolve().parents[1]


def test_missing_image_refuses_before_setup(monkeypatch):
    from scripts import archive_rehearsal as harness

    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(harness, "docker", docker)
    venue = harness.Venue("missing:synthetic", 1024**3)
    with pytest.raises(RuntimeError, match="image not found: missing:synthetic"):
        venue.setup("unused")
    venue.close()
    assert calls == [("image", "inspect", "missing:synthetic")]
    assert not venue.volumes


@pytest.mark.parametrize("residue", [False, "mount", "loop", "propagated"])
def test_cleanup_unmounts_all_aliases_before_detach_and_asserts_no_residue(monkeypatch, residue):
    from scripts import archive_rehearsal as harness

    prefix = "ta-s6-012345abcdef"
    volume = "docker/volumes/" + prefix + "-store/_data"
    targets = ["/var/lib/" + volume,
               "/mnt/docker-desktop-disk/data/" + volume,
               "/parent-distro/mnt/docker-desktop-disk/data/" + volume,
               "/run/desktop/mnt/docker-desktop-disk/data/" + volume]
    # Three covered mounts at the shallow alias require all three passes.
    mounted = dict.fromkeys(targets, 1)
    mounted[targets[0]] = 3
    attached, calls = {"/dev/loop77"}, []
    backing = "/var/lib/docker/volumes/" + prefix + "-image/_data/store.img"

    def mount_text():
        return "\n".join(f"/dev/loop77 {target} xfs rw 0 0" for target in sorted(mounted))

    def run(args, **kwargs):
        assert args[:8] == ["nsenter", "-t", "1", "-m", "sh", "-c", 'exec "$@"', "sh"]
        args = args[8:]
        calls.append(args)
        code, output = 0, ""
        if args == ["cat", "/proc/mounts"]:
            output = mount_text()
        elif args[:2] == ["losetup", "--list"]:
            output = json.dumps({"loopdevices": [
                {"name": device, "back-file": backing} for device in attached] + [
                    {"name": "/dev/loop99", "back-file": "/unrelated/store.img"}]})
        elif args[0] == "umount":
            assert len(args) == 2
            if residue == "mount":
                code = 1
            else:
                if residue == "propagated":
                    mounted.clear()
                    attached.clear()  # Last mount autoclears its loop.
                else:
                    mounted[args[-1]] -= 1
                    if not mounted[args[-1]]:
                        del mounted[args[-1]]
        elif args[:2] == ["losetup", "-d"]:
            assert not mounted or residue == "mount"
            if residue != "loop":
                attached.remove(args[-1])
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, code, output)

    monkeypatch.setattr(harness.subprocess, "run", run)

    def docker(*args, **kwargs):
        if args[0] == "run":
            assert "--privileged" in args and "--pid=host" in args
            entry = args.index("--entrypoint")
            assert args[entry + 1:entry + 4] == ("python", "synthetic", "-c")
            # Execute the actual helper payload as the fake namespace runner.
            with monkeypatch.context() as patch:
                patch.setattr(sys, "argv", ["-c", prefix])
                try:
                    exec(args[entry + 4], {})
                except SystemExit:
                    paths = sorted(mounted) if residue == "mount" else [backing]
                    return subprocess.CompletedProcess(args, 1,
                                                       "cleanup residue: " + ", ".join(paths))
            return subprocess.CompletedProcess(args, 0, "")
        assert args[:2] == ("volume", "rm")
        assert not mounted and not attached
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "")

    monkeypatch.setattr(harness, "docker", docker)
    venue = harness.Venue("synthetic", 1)
    venue.prefix = prefix
    venue.volumes = [prefix + "-image", prefix + "-store"]
    if residue in {"mount", "loop"}:
        with pytest.raises(RuntimeError, match="volumes retained.*" +
                           re.escape(targets[0] if residue == "mount" else backing)):
            venue.close()
        assert not any(args[:2] == ["volume", "rm"] for args in calls)
    else:
        venue.close()
        assert not mounted and not attached
    normal = [args[1] for args in calls if args[0] == "umount" and len(args) == 2]
    ordered = sorted(targets, key=lambda p: (p.count("/"), len(p), p), reverse=True)
    expected = (ordered[:1] if residue == "propagated" else
                ordered * 3 if residue == "mount" else ordered + [targets[0]] * 2)
    assert normal == expected
    if residue is False:
        detach = calls.index(["losetup", "-d", "/dev/loop77"])
        assert all(index > detach for index, args in enumerate(calls)
                   if args[:2] == ["volume", "rm"])


def test_cleanup_never_removes_volumes_after_failed_residue_assertion(monkeypatch):
    from scripts import archive_rehearsal as harness

    calls = []
    monkeypatch.setattr(harness, "docker", lambda *args, **kwargs: (
        calls.append(args) or subprocess.CompletedProcess(args, 1)))
    venue = harness.Venue("synthetic", 1)
    venue.volumes = [venue.names["image"], venue.names["store"]]
    with pytest.raises(RuntimeError, match="residue assertion failed; volumes retained"):
        venue.close()
    assert all(args[:2] != ("volume", "rm") for args in calls)


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


@pytest.mark.parametrize("failure", ["step", "exception", "interrupt", "sigterm"])
def test_cleanup_runs_before_step_failure_or_interrupt_escapes(monkeypatch, failure):
    from scripts import archive_rehearsal as harness

    calls = []

    class FailedVenue:
        def __init__(self, *_):
            pass

        def setup(self, _):
            calls.append("setup")

        def migration(self, step):
            calls.append(step)
            if failure == "exception":
                raise ValueError("synthetic failure")
            if failure == "interrupt":
                raise KeyboardInterrupt
            if failure == "sigterm":
                handlers[harness.signal.SIGTERM]()
            return subprocess.CompletedProcess([], 1, "")

        def close(self):
            calls.append("cleanup")

    monkeypatch.setattr(harness, "Venue", FailedVenue)
    if failure == "sigterm":
        handlers = {}
        monkeypatch.setattr(harness.signal, "signal", lambda sig, handler: handlers.update(
            {sig: handler}))
        monkeypatch.setattr(sys, "argv", ["rehearsal", "--archive", "synthetic", "--image", "test"])
        from scripts import rehearse_box_move

        monkeypatch.setattr(rehearse_box_move, "validate_archive", lambda _: 1)
        with pytest.raises(SystemExit):
            harness.main()
    else:
        error = {"step": RuntimeError, "exception": ValueError, "interrupt": KeyboardInterrupt}
        with pytest.raises(error[failure]):
            harness.archive_rehearsal("synthetic", "unused", store_bytes=1)
    assert calls == ["setup", "preflight", "cleanup"]


def test_container_removal_failure_still_cleans_mounts(monkeypatch):
    from scripts import archive_rehearsal as harness

    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, int(args[0] == "rm"), "", "busy")

    monkeypatch.setattr(harness, "docker", docker)
    venue = harness.Venue("synthetic", 1)
    venue.volumes = [venue.names["image"]]
    venue.containers = [venue.prefix + "-run-0"]
    with pytest.raises(RuntimeError, match="could not remove venue containers"):
        venue.close()
    assert [args[0] for args in calls] == ["rm", "run"]


@pytest.mark.parametrize("sig", ["SIGTERM", "SIGINT"])
@pytest.mark.parametrize("phase", ["rm", "run", "volume"])
def test_cleanup_defers_signals_until_all_resources_are_removed(monkeypatch, sig, phase):
    from scripts import archive_rehearsal as harness

    calls = []

    def terminate(*_):
        raise KeyboardInterrupt

    original = {harness.signal.SIGTERM: terminate, harness.signal.SIGINT: terminate}
    handlers = original.copy()

    def install(sig, handler):
        previous = handlers[sig]
        handlers[sig] = handler
        return previous

    def docker(*args, **kwargs):
        calls.append(args[0])
        if args[0] == phase:
            handlers[getattr(harness.signal, sig)]()
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(harness.signal, "signal", install)
    monkeypatch.setattr(harness, "docker", docker)
    venue = harness.Venue("synthetic", 1)
    venue.containers = [venue.prefix + "-run-0"]
    venue.volumes = [venue.names["image"], venue.names["store"]]
    with pytest.raises(KeyboardInterrupt):
        venue.close()
    assert calls == ["rm", "run", "volume", "volume"]
    assert handlers == original


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
    venues = []
    original_venue = harness.Venue

    def tracked_venue(*args):
        venue = original_venue(*args)
        venues.append(venue)
        return venue

    monkeypatch.setattr(harness, "Venue", tracked_venue)
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
            "normal_restore_after_traffic_refuses",
            "disaster_restore_after_traffic",
            "cleanup",
        }
        assert all(step["status"] == "PASS" for step in result.values())
        assert len(venues) == 1
        # close's real engine-namespace mount/loop assertions must succeed
        # before these volumes can disappear, including the backing loop file.
        assert harness.docker("volume", "ls", "--filter", "name=" + venues[0].prefix,
                              "--format", "{{.Name}}").stdout.strip() == ""
        assert result["crash_resume"]["interruptions"] == 2
        assert result["crash_resume"]["comparisons"] == 2
        assert result["serving"] == {
            "status": "PASS", "owners": 2, "centers": 4, "history": 4,
            "history_turns": 4, "pending": 4, "pending_saved": 4,
            "controls": 8, "plans": 8, "writes": 4, "reconnect": 2,
        }
        assert result["restore_before_write"]["comparisons"] == 1
        assert result["normal_restore_after_traffic_refuses"] == {"status": "PASS", "refusals": 1}
        assert result["disaster_restore_after_traffic"] == {
            "status": "PASS", "comparisons": 1, "replay_refusals": 7, "fenced_owners": 2,
            "stale_credentials": 3, "erased_owners": 1,
        }
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
