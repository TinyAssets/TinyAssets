"""Synthetic metadata fixtures; POSIX paths must run in the Linux proof."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tinyassets import workspace_pool as wp
from tinyassets.api import resource_usage as usage
from tinyassets.api import storage_observations as so
from tinyassets.ttl_memo import TTLMemo

POSIX = pytest.mark.skipif(os.name != "posix", reason="requires real POSIX descriptor traversal")
UID = "owner-universe"


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(so, "_memo", TTLMemo(max_entries=128))
    monkeypatch.setattr(so, "_scan_slots", threading.BoundedSemaphore(2))
    monkeypatch.setattr(so, "SCAN_SECONDS", 5.0)
    monkeypatch.setenv("TINYASSETS_STORAGE_SNAPSHOT_TTL_S", "0")


@pytest.fixture
def home(tmp_path):
    (tmp_path / UID).mkdir()
    conn = sqlite3.connect(tmp_path / UID / ".runs.db")
    wp.ensure_schema(conn)
    conn.commit()
    conn.close()
    return tmp_path


def observe(root):
    return so.observe(root, UID, readonly=usage._readonly)


def put(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def lease(root, name="own-lease", uid=UID, state=wp.STATE_ACTIVE, generation=1):
    with sqlite3.connect(root / UID / ".runs.db") as conn:
        conn.execute(
            "INSERT INTO workspace_leases "
            "(lease_id, universe_id, connection_id, repo_key, storage_class, "
            "generation, state, reserved_bytes, measured_bytes, run_id, "
            "path, quarantine_path, created_at, updated_at) "
            "VALUES (?,?, 'connection', 'repo', 'scratch', "
            "?, ?, 999999, 888888, 'run', 'ignored-foreign-path', 'ignored-quarantine', 1, 1)",
            (name, uid, generation, state),
        )
    conn.close()


@POSIX
def test_categories_measure_files_not_transport_and_never_read_contents(home, monkeypatch):
    put(home, f"{UID}/workspaces/project/one", b"abc")
    put(home, f"{UID}/workspaces/.quarantine/old/two", b"12345")
    put(home, f"{UID}/.runtime/provider-child/config", b"1234567")
    put(home, f"{UID}/note", b"hello-world")
    put(home, "scratch/own-lease/file", b"1234567890123")
    put(home, "scratch/.quarantine/own-lease.1/file", b"12345678901234567")
    put(home, "scratch/foreign-lease/private", b"x" * 1000)
    lease(home)
    lease(home, name="foreign-lease", uid="foreign-universe")
    db_bytes = (home / UID / ".runs.db").stat().st_size
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    monkeypatch.setattr(os, "read", lambda *a: pytest.fail("must not read file contents"))
    result = observe(home)
    assert result["availability"] == "observed"
    assert result["categories"] == {
        "permanent_workspaces": {"observed_logical_bytes": 8, "files_observed": 2},
        "provider_runtime": {"observed_logical_bytes": 7, "files_observed": 1},
        "other_universe_files": {"observed_logical_bytes": db_bytes + 11, "files_observed": 2},
        "scratch": {"observed_logical_bytes": 30, "files_observed": 2},
    }
    assert result["observed_logical_bytes"] == db_bytes + 56
    assert result["not_atomic_snapshot"] is True
    assert {p: p.read_bytes() for p in before} == before
    serialized = json.dumps(result)
    for private in ("foreign", "own-lease", "ignored-", str(home), UID):
        assert private not in serialized


@POSIX
def test_released_history_and_absent_derived_paths_are_not_partial(home):
    lease(home, state=wp.STATE_AVAILABLE)
    lease(home, name="not-created-yet")
    result = observe(home)
    assert result["availability"] == "observed"
    assert result["categories"]["scratch"]["observed_logical_bytes"] == 0


@POSIX
def test_credential_runtime_is_separate_from_user_files_in_status(home):
    put(home, f"{UID}/.credentials/claude/projects/cache", b"x" * 112_000)
    put(home, f"{UID}/notes/mine", b"x" * 36_000)
    put(home, f"{UID}/.runtime/agent-sessions/transcript", b"history")
    db_bytes = (home / UID / ".runs.db").stat().st_size
    result = observe(home)
    assert result["categories"]["provider_runtime"]["observed_logical_bytes"] == 112_000
    user_bytes = result["categories"]["other_universe_files"]["observed_logical_bytes"]
    assert user_bytes == 36_000 + db_bytes + 7
    assert "not account usage" in result["accounting_note"]


@POSIX
def test_hard_links_deduplicate_in_fixed_category_order(home):
    file = put(home, f"{UID}/workspaces/project/file", b"abcde")
    (home / UID / ".runtime").mkdir()
    os.link(file, home / UID / ".runtime" / "duplicate")
    os.link(file, home / UID / "duplicate")
    result = observe(home)
    assert result["categories"]["permanent_workspaces"]["observed_logical_bytes"] == 5
    assert result["categories"]["provider_runtime"]["observed_logical_bytes"] == 0
    assert result["categories"]["other_universe_files"]["files_observed"] == 1


@POSIX
def test_sparse_file_is_logical_not_allocated_bytes(home):
    path = home / UID / "sparse"
    with path.open("wb") as file:
        file.truncate(1024 * 1024)
    result = observe(home)
    assert result["categories"]["other_universe_files"]["observed_logical_bytes"] == (
        1024 * 1024 + (home / UID / ".runs.db").stat().st_size
    )
    assert result["unit"] == "logical_regular_file_bytes"
    assert "allocated_disk_blocks" in result["exclusions"]


@POSIX
def test_links_and_fifo_are_not_followed_or_opened(home):
    foreign = put(home, "foreign/secret", b"x" * 10000)
    os.symlink(foreign, home / UID / "link")
    os.symlink(foreign.parent, home / UID / "dir-link", target_is_directory=True)
    os.mkfifo(home / UID / "fifo")
    result = observe(home)
    assert result["availability"] == "partial"
    assert result["reasons"] == ["links_or_special_files"]
    assert result["categories"]["other_universe_files"]["files_observed"] == 1


@POSIX
def test_swapped_directory_is_not_walked(home, monkeypatch):
    put(home, f"{UID}/workspaces/a", b"old")
    replacement = home / "replacement"
    replacement.mkdir()
    (replacement / "secret").write_bytes(b"x" * 9999)
    original = so.fs.open_subdir_nofollow

    def swapped(parent, name):
        if name == "workspaces":
            (home / UID / name).rename(home / "old-workspace")
            replacement.rename(home / UID / name)
        return original(parent, name)

    monkeypatch.setattr(so.fs, "open_subdir_nofollow", swapped)
    result = observe(home)
    assert "directory_changed" in result["reasons"]
    assert result["categories"]["permanent_workspaces"]["files_observed"] == 0


@POSIX
@pytest.mark.parametrize("error,reason", [(PermissionError, "entry_unreadable"),
                                        (FileNotFoundError, "entry_changed")])
def test_entry_errors_are_partial_and_sanitized(home, monkeypatch, error, reason):
    put(home, f"{UID}/private-entry", b"xx")
    original = os.stat

    def fail(path, *args, **kwargs):
        if path == "private-entry":
            raise error("/foreign/secret inode=12345")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", fail)
    result = observe(home)
    assert result["availability"] == "partial"
    assert reason in result["reasons"]
    assert all("/" not in r and "private" not in r and "12345" not in r for r in result["reasons"])


@POSIX
@pytest.mark.parametrize("damage", ["missing", "corrupt", "legacy"])
def test_lease_store_failure_does_not_initialize_or_invent_coverage(home, damage):
    db = home / UID / ".runs.db"
    db.unlink()
    if damage == "corrupt":
        db.write_bytes(b"not sqlite")
    if damage == "legacy":
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE old (v)")
        conn.close()
    before = db.read_bytes() if db.exists() else None
    result = observe(home)
    assert "lease_records_unavailable" in result["reasons"]
    assert (db.read_bytes() if db.exists() else None) == before


@POSIX
@pytest.mark.parametrize("name,generation", [("../foreign", 1), ("safe", -1), ("safe", 1.5),
                                           ("safe", "9" * 4301), ("x" * 256, 1)])
def test_invalid_lease_identity_never_becomes_a_path(home, name, generation):
    lease(home, name=name, generation=generation)
    result = observe(home)
    assert result["reasons"] == ["invalid_lease_record"]
    assert result["categories"]["scratch"]["files_observed"] == 0


@POSIX
def test_live_wal_files_are_part_of_local_footprint(home):
    conn = sqlite3.connect(home / UID / ".runs.db")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE live (v)")
        conn.execute("INSERT INTO live VALUES (?)", ("x" * 10000,))
        conn.commit()
        expected = sum(p.stat().st_size for p in (home / UID).iterdir())
        result = observe(home)
        assert result["categories"]["other_universe_files"]["observed_logical_bytes"] == expected
        assert conn.execute("SELECT length(v) FROM live").fetchone()[0] == 10000
    finally:
        conn.close()


@POSIX
@pytest.mark.parametrize("bound,value,reason", [("MAX_ENTRIES", 1, "entry_bound"),
                                              ("MAX_DEPTH", 0, "depth_bound"),
                                              ("SCAN_SECONDS", 0, "time_bound"),
                                              ("MAX_LEASE_ROWS", 0, "lease_row_bound")])
def test_work_bounds_report_partial(home, monkeypatch, bound, value, reason):
    put(home, f"{UID}/workspaces/a/file", b"abc")
    lease(home)
    monkeypatch.setattr(so, bound, value)
    result = observe(home)
    assert result["availability"] == "partial"
    assert reason in result["reasons"]


def test_unsupported_descriptor_host_is_explicit(home, monkeypatch):
    def unavailable(*args):
        raise NotImplementedError("private detail")
    monkeypatch.setattr(so.fs, "open_dir_nofollow", unavailable)
    result = observe(home)
    assert result["availability"] == "unavailable"
    assert result["reasons"] == ["unsupported_safe_traversal"]
    assert "observed_logical_bytes" not in result


def test_cache_retains_capture_time_and_is_bounded(home, monkeypatch):
    monkeypatch.setenv("TINYASSETS_STORAGE_SNAPSHOT_TTL_S", "99999999")
    calls = []
    monkeypatch.setattr(so, "_measure", lambda *a: calls.append(1) or so._unavailable("fixture"))
    first, second = observe(home), observe(home)
    assert first["observed_at"] == second["observed_at"]
    assert second["cache_max_age_seconds"] == 60
    assert len(calls) == 1
    second["reasons"].append("mutated")
    assert "mutated" not in observe(home)["reasons"]


def test_cache_directory_identity_prevents_replacement_reuse(home, monkeypatch):
    monkeypatch.setenv("TINYASSETS_STORAGE_SNAPSHOT_TTL_S", "60")
    calls = []
    monkeypatch.setattr(so, "_measure", lambda *a: calls.append(1) or so._unavailable("fixture"))
    observe(home)
    (home / UID).rename(home / "previous")
    (home / UID).mkdir()
    observe(home)
    assert len(calls) == 2


def test_same_scope_requests_share_one_scan(home, monkeypatch):
    calls, start = [], threading.Barrier(8)
    def measure(*args):
        calls.append(1)
        time.sleep(0.05)
        return so._unavailable("fixture")
    monkeypatch.setattr(so, "_measure", measure)
    def request(_):
        start.wait()
        return observe(home)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(request, range(8)))
    assert len(calls) == 1
    assert len({r["observed_at"] for r in results}) == 1


def test_scan_capacity_contention_is_unavailable(home):
    assert so._scan_slots.acquire(blocking=False)
    assert so._scan_slots.acquire(blocking=False)
    try:
        result = observe(home)
        assert result["reasons"] == ["contention"]
        assert result["availability"] == "unavailable"
    finally:
        so._scan_slots.release()
        so._scan_slots.release()


def test_timed_out_memo_waiter_is_unavailable(home, monkeypatch):
    started, release = threading.Event(), threading.Event()
    def blocked(*args):
        started.set()
        assert release.wait(5)
        return so._unavailable("fixture")
    monkeypatch.setattr(so, "_measure", blocked)
    with ThreadPoolExecutor(max_workers=1) as executor:
        leader = executor.submit(observe, home)
        try:
            assert started.wait(5)
            flight = next(iter(so._memo._inflight.values()))
            monkeypatch.setattr(flight.done, "wait", lambda timeout: False)
            assert observe(home)["reasons"] == ["contention"]
        finally:
            release.set()
        assert leader.result()["reasons"] == ["fixture"]
