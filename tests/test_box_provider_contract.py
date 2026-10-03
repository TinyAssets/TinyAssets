"""The driver-agnostic `BoxProvider` contract (target architecture D2).

Every driver must pass this suite. Today the parametrised driver list holds the
local (dev/test) driver. The gVisor and Firecracker drivers join the list when
they land, and nothing here changes. A driver that has no hard disk bound
reports ``bound_bytes is None``. The bound test runs only for drivers that
declare a bound, so the local driver cannot quietly pass it.

POSIX only: drivers are Linux. On Windows the parametrised cases skip with the
reason stated, and CI (Linux) runs every case.
"""

from __future__ import annotations

import io
import os
import sys
import tarfile
import time
from pathlib import Path

import pytest

from tinyassets.boxes import (
    BOX_ROOT,
    BoxAuthError,
    BoxError,
    BoxNotFound,
    BoxPathError,
    ExecLimits,
    ExecState,
    ExportProfile,
    OpIdReuse,
    StaleHandle,
    WriteConflict,
    WriteMode,
    box_relpath,
)

POSIX = os.name == "posix" and sys.platform != "win32"
needs_posix = pytest.mark.skipif(not POSIX, reason="box drivers need POSIX openat semantics")

OWNERS = {"cc-a": "acct-a", "cc-b": "acct-b"}


def _local(tmp_path: Path):
    from tinyassets.boxes.local import LocalBoxProvider

    return LocalBoxProvider(
        boxes_root=tmp_path / "boxes", state_dir=tmp_path / "state",
        owner_of=OWNERS.get, allow_unisolated=True,
    )


DRIVERS = {"local": _local}


@pytest.fixture(params=sorted(DRIVERS))
def provider(request, tmp_path):
    if not POSIX:
        pytest.skip("box drivers need POSIX openat semantics")
    driver = DRIVERS[request.param](tmp_path)
    yield driver
    driver.close()


def _drain(provider, handle, exec_id, timeout=15.0):
    out = b""
    exit_event = None
    for event in provider.stream(handle, exec_id, timeout=timeout):
        if event.kind == "output":
            out += event.data
        else:
            exit_event = event
    assert exit_event is not None, "exec did not finish within the test timeout"
    return out, exit_event


# -- paths -------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "cc/x", "/c/x", "/cc/../x", "/cc//x", "/cc/./x",
                                 "/cc/a/..", "/cc/x\0y", "/cc\\x", "relative"])
def test_box_paths_outside_cc_are_refused(bad):
    with pytest.raises(BoxPathError):
        box_relpath(bad)


def test_box_root_and_nested_paths_normalize():
    assert box_relpath(BOX_ROOT) == ""
    assert box_relpath("/cc/a/b.txt") == "a/b.txt"
    assert box_relpath("/cc/a/") == "a"


# -- authentication --------------------------------------------------------------------------


@needs_posix
def test_bind_refuses_an_account_that_does_not_own_the_command_center(provider):
    with pytest.raises(BoxAuthError):
        provider.bind("cc-a", account_id="acct-b")


@needs_posix
def test_a_valid_handle_presented_for_another_owner_fails_every_operation(provider):
    good = provider.bind("cc-a", account_id="acct-a")
    forged = type(good)("cc-a", "acct-b", good.epoch)
    with pytest.raises(BoxAuthError):
        provider.read(forged, "/cc/x", max_bytes=10)
    with pytest.raises(BoxAuthError):
        provider.start_exec(forged, "op1", ["true"])


@needs_posix
def test_destroy_makes_every_earlier_handle_stale(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    provider.write(handle, "w1", "/cc/f.txt", b"hi", max_bytes=10)
    receipt = provider.destroy(handle, "d1")
    assert receipt.files_removed == 1 and receipt.new_epoch == handle.epoch + 1
    with pytest.raises(StaleHandle):
        provider.read(handle, "/cc/f.txt", max_bytes=10)
    fresh = provider.bind("cc-a", account_id="acct-a")
    with pytest.raises(BoxNotFound):
        provider.read(fresh, "/cc/f.txt", max_bytes=10)


# -- files and generations -----------------------------------------------------------------


@needs_posix
def test_write_read_and_generation_without_waking(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    g0 = provider.committed_generation(handle)
    wrote = provider.write(handle, "w1", "/cc/notes/a.md", b"hello", max_bytes=100)
    assert wrote.generation == g0 + 1
    got = provider.read(handle, "/cc/notes/a.md", max_bytes=100)
    assert got.data == b"hello" and got.size == 5 and got.generation == wrote.generation
    assert provider.committed_generation(handle) == wrote.generation


@needs_posix
def test_a_retried_write_with_the_same_op_id_runs_once(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    first = provider.write(handle, "w1", "/cc/a.txt", b"one", max_bytes=10)
    again = provider.write(handle, "w1", "/cc/a.txt", b"one", max_bytes=10)
    assert again == first
    assert provider.committed_generation(handle) == first.generation
    with pytest.raises(OpIdReuse):
        provider.write(handle, "w1", "/cc/a.txt", b"two", max_bytes=10)


@needs_posix
def test_create_and_cas_conflicts_change_nothing(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    w = provider.write(handle, "w1", "/cc/a.txt", b"one", max_bytes=10)
    with pytest.raises(WriteConflict):
        provider.write(handle, "w2", "/cc/a.txt", b"x", max_bytes=10, mode=WriteMode.CREATE)
    with pytest.raises(WriteConflict):
        provider.write(handle, "w3", "/cc/a.txt", b"x", max_bytes=10, mode=WriteMode.CAS,
                       expect_generation=w.generation - 1)
    ok = provider.write(handle, "w4", "/cc/a.txt", b"two", max_bytes=10, mode=WriteMode.CAS,
                        expect_generation=w.generation)
    assert provider.read(handle, "/cc/a.txt", max_bytes=10).data == b"two"
    assert ok.generation == w.generation + 1
    # a refused op id is free again for a corrected retry
    provider.write(handle, "w2", "/cc/b.txt", b"x", max_bytes=10, mode=WriteMode.CREATE)


@needs_posix
def test_writes_over_their_bound_are_refused(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    with pytest.raises(BoxError):
        provider.write(handle, "w1", "/cc/a.txt", b"x" * 11, max_bytes=10)
    assert provider.stat(handle, "/cc/a.txt") is None


@needs_posix
def test_read_many_returns_one_generation_and_names_missing_files(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    provider.write(handle, "w1", "/cc/soul.md", b"S", max_bytes=10)
    provider.write(handle, "w2", "/cc/skills/x.md", b"X", max_bytes=10)
    snap = provider.read_many(handle, ["/cc/soul.md", "/cc/skills/x.md", "/cc/nope.md"],
                              max_total=100)
    assert snap.files == {"/cc/soul.md": b"S", "/cc/skills/x.md": b"X"}
    assert snap.missing == ("/cc/nope.md",)
    assert snap.generation == provider.committed_generation(handle)


@needs_posix
def test_listing_is_paginated_by_cursor(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    for i in range(5):
        provider.write(handle, f"w{i}", f"/cc/d/f{i}.txt", b"x", max_bytes=10)
    page = provider.list(handle, "/cc/d", limit=2)
    names = [e.name for e in page.entries]
    while page.next_cursor:
        page = provider.list(handle, "/cc/d", cursor=page.next_cursor, limit=2)
        names += [e.name for e in page.entries]
    assert names == [f"f{i}.txt" for i in range(5)]


@needs_posix
def test_download_streams_a_regular_file(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    provider.write(handle, "w1", "/cc/big.bin", b"a" * 1000, max_bytes=2000)
    assert b"".join(provider.download(handle, "/cc/big.bin", chunk_bytes=64)) == b"a" * 1000


# -- the planted-link class (#4244) ---------------------------------------------------------


@needs_posix
def test_a_planted_link_to_another_box_is_never_followed(provider, tmp_path):
    victim = provider.bind("cc-b", account_id="acct-b")
    provider.write(victim, "w1", "/cc/founder.md", b"VICTIM SECRET", max_bytes=100)
    attacker = provider.bind("cc-a", account_id="acct-a")
    provider.ensure_awake(attacker, reason="test")
    # The agent plants links inside its own box: to an absolute host path, and upward.
    victim_file = tmp_path / "boxes" / "cc-b" / "founder.md"
    exec_id = provider.start_exec(attacker, "e1", [
        "sh", "-c", f"ln -s {victim_file} founder.md && ln -s ../cc-b up && mkdir -p d"])
    _out, done = _drain(provider, attacker, exec_id)
    assert done.exit_code == 0
    for path in ("/cc/founder.md", "/cc/up/founder.md"):
        with pytest.raises(BoxPathError):
            provider.read(attacker, path, max_bytes=100)
        with pytest.raises(BoxPathError):
            provider.read_many(attacker, [path], max_total=100)
        with pytest.raises(BoxPathError):
            provider.download(attacker, path)
    with pytest.raises(BoxPathError):
        provider.list(attacker, "/cc/up")
    assert provider.stat(attacker, "/cc/founder.md").kind == "link"
    kinds = {e.name: e.kind for e in provider.list(attacker, "/cc").entries}
    assert kinds["founder.md"] == "link" and kinds["up"] == "link"
    with pytest.raises(BoxPathError):
        provider.write(attacker, "w2", "/cc/up/founder.md", b"overwrite", max_bytes=100)
    # removing the link removes the link, never its target
    provider.remove(attacker, "r1", "/cc/founder.md")
    assert provider.read(victim, "/cc/founder.md", max_bytes=100).data == b"VICTIM SECRET"


# -- execution lifecycle ----------------------------------------------------------------------


@needs_posix
def test_exec_streams_output_and_exit_code(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    exec_id = provider.start_exec(handle, "e1", ["sh", "-c", "echo out; echo err >&2; exit 3"])
    out, done = _drain(provider, handle, exec_id)
    assert b"out" in out and b"err" in out
    assert done.exit_code == 3 and done.killed is None
    status = provider.exec_status(handle, "e1")
    assert status.state is ExecState.EXITED and status.exit_code == 3


@needs_posix
def test_a_retried_exec_with_the_same_op_id_never_runs_twice(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    argv = ["sh", "-c", "echo line >> log.txt"]
    first = provider.start_exec(handle, "e1", argv)
    _drain(provider, handle, first)
    second = provider.start_exec(handle, "e1", argv)
    assert second == first
    assert provider.read(handle, "/cc/log.txt", max_bytes=100).data == b"line\n"
    with pytest.raises(OpIdReuse):
        provider.start_exec(handle, "e1", ["sh", "-c", "echo other"])


@needs_posix
def test_cancel_kills_the_whole_process_tree(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    exec_id = provider.start_exec(
        handle, "e1", ["sh", "-c", "sleep 60 & echo $! > child.pid; wait"],
        limits=ExecLimits(wall_seconds=60))
    deadline = time.monotonic() + 10
    while provider.stat(handle, "/cc/child.pid") is None or not provider.read(
            handle, "/cc/child.pid", max_bytes=20).data.strip():
        assert time.monotonic() < deadline, "child never started"
        time.sleep(0.05)
    child = int(provider.read(handle, "/cc/child.pid", max_bytes=20).data)
    provider.cancel(handle, exec_id)
    _out, done = _drain(provider, handle, exec_id)
    assert done.killed == "cancelled"
    time.sleep(0.2)
    assert Path("/proc/self/stat").exists()
    stat_file = Path(f"/proc/{child}/stat")
    # gone, or a zombie awaiting a reaper (the container's pid 1 may not reap)
    assert not stat_file.exists() or stat_file.read_text().split(") ")[1][0] == "Z"


@needs_posix
def test_wall_clock_and_output_limits_kill_the_exec(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    slow = provider.start_exec(handle, "e1", ["sleep", "30"],
                               limits=ExecLimits(wall_seconds=0.5))
    assert _drain(provider, handle, slow)[1].killed == "timeout"
    loud = provider.start_exec(handle, "e2", ["head", "-c", "1000000", "/dev/zero"],
                               limits=ExecLimits(wall_seconds=30, output_bytes=1000))
    out, done = _drain(provider, handle, loud)
    assert done.killed == "output_limit" and len(out) <= 1000


@needs_posix
def test_stdin_a_child_never_reads_cannot_stall_the_wall_clock(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    started = time.monotonic()
    exec_id = provider.start_exec(handle, "e1", ["sleep", "30"], stdin=b"x" * (4 << 20),
                                  limits=ExecLimits(wall_seconds=0.5))
    _out, done = _drain(provider, handle, exec_id)
    assert done.killed == "timeout" and time.monotonic() - started < 10


@needs_posix
def test_the_exec_ends_with_its_leader_and_takes_its_group_with_it(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    exec_id = provider.start_exec(handle, "e1", ["sh", "-c", "sleep 60 & echo $! > child.pid"])
    _out, done = _drain(provider, handle, exec_id)
    assert done.exit_code == 0
    child = int(provider.read(handle, "/cc/child.pid", max_bytes=20).data)
    time.sleep(0.2)
    stat_file = Path(f"/proc/{child}/stat")
    assert not stat_file.exists() or stat_file.read_text().split(") ")[1][0] == "Z"


@needs_posix
def test_destroy_stops_running_execs_before_it_returns(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    provider.start_exec(handle, "e1", ["sh", "-c", "echo $$ > leader.pid; sleep 60"],
                        limits=ExecLimits(wall_seconds=60))
    deadline = time.monotonic() + 10
    while provider.stat(handle, "/cc/leader.pid") is None or not provider.read(
            handle, "/cc/leader.pid", max_bytes=20).data.strip():
        assert time.monotonic() < deadline, "exec never started"
        time.sleep(0.05)
    leader = int(provider.read(handle, "/cc/leader.pid", max_bytes=20).data)
    receipt = provider.destroy(handle, "d1")
    stat_file = Path(f"/proc/{leader}/stat")
    assert not stat_file.exists() or stat_file.read_text().split(") ")[1][0] == "Z"
    # replaying the same destroy through the original handle returns the same receipt
    assert provider.destroy(handle, "d1") == receipt


# -- whole box --------------------------------------------------------------------------------


@needs_posix
def test_usage_counts_logical_bytes_once_per_inode(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    provider.write(handle, "w1", "/cc/a.bin", b"x" * 100, max_bytes=200)
    _drain(provider, handle, provider.start_exec(handle, "e1", ["ln", "a.bin", "b.bin"]))
    usage = provider.usage(handle)
    assert usage.logical_bytes == 100
    if usage.bound_bytes is not None:
        assert usage.bound_bytes >= usage.logical_bytes


@needs_posix
def test_export_import_round_trip_leaves_links_out(provider, tmp_path):
    src = provider.bind("cc-a", account_id="acct-a")
    provider.write(src, "w1", "/cc/notes/a.md", b"A", max_bytes=10)
    _drain(provider, src, provider.start_exec(src, "e1", ["ln", "-s", "/etc/passwd", "link"]))
    bundle = b"".join(provider.export(src, profile=ExportProfile.MIGRATION))
    names = tarfile.open(fileobj=io.BytesIO(bundle)).getnames()
    assert "notes/a.md" in names and "link" not in names
    dst = provider.bind("cc-b", account_id="acct-b")
    report = provider.import_bundle(dst, "i1", [bundle], profile=ExportProfile.MIGRATION)
    assert report.files == 1
    assert provider.read(dst, "/cc/notes/a.md", max_bytes=10).data == b"A"


@needs_posix
def test_import_refuses_traversal_and_link_members(provider):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, kind in (("../escape.txt", tarfile.REGTYPE), ("ok.txt", tarfile.REGTYPE),
                           ("evil", tarfile.SYMTYPE)):
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.SYMTYPE:
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = 2
                tar.addfile(info, io.BytesIO(b"hi"))
    handle = provider.bind("cc-a", account_id="acct-a")
    report = provider.import_bundle(handle, "i1", [buf.getvalue()],
                                    profile=ExportProfile.MIGRATION)
    assert report.files == 1
    assert set(report.refused) == {"../escape.txt", "evil"}
    assert provider.stat(handle, "/cc/evil") is None


@needs_posix
def test_box_disk_bound_contains_a_full_box(provider):
    handle = provider.bind("cc-a", account_id="acct-a")
    if provider.usage(handle).bound_bytes is None:
        pytest.skip("this driver declares no disk bound (local dev driver) owner=Jonnyton runs-in=gvisor-driver")
    bound = provider.usage(handle).bound_bytes
    exec_id = provider.start_exec(handle, "e1", ["sh", "-c", f"head -c {bound + 4096} "
                                                 "/dev/zero > fill.bin"])
    assert _drain(provider, handle, exec_id)[1].exit_code != 0
    neighbour = provider.bind("cc-b", account_id="acct-b")
    provider.write(neighbour, "w1", "/cc/ok.txt", b"still fine", max_bytes=100)


# -- the local driver's own guard ---------------------------------------------------------------


@needs_posix
def test_local_driver_refuses_to_start_without_the_unisolated_acknowledgement(tmp_path):
    from tinyassets.boxes.local import LocalBoxProvider

    with pytest.raises(BoxError):
        LocalBoxProvider(boxes_root=tmp_path / "b", state_dir=tmp_path / "s",
                         owner_of=OWNERS.get)
