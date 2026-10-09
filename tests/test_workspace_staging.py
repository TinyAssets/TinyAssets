"""Workspace staging: removed on every exit, swept only when its owner is dead.

Production held 334 leaked staging directories (2.8 GiB, possibly credentialed
clones) in one universe until the boot sweep removed them on 2026-09-30.
The sweep that removes them must never touch a directory a live checkout owns --
proven here with a REAL second process holding one.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tinyassets import process_liveness
from tinyassets import workspace_staging as ws


@pytest.fixture(autouse=True)
def _isolated_queue():
    """The pending-removal queue is per process; keep one test's out of the next."""
    ws._PENDING.clear()
    yield
    ws._PENDING.clear()


def _entries(root: Path) -> list[str]:
    return sorted(e for e in os.listdir(root) if e != process_liveness.LIVENESS_DIR)


def _fill(path: Path, n: int = 3, size: int = 1000) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "sub").mkdir(exist_ok=True)
    for i in range(n):
        (path / "sub" / f"f{i}").write_bytes(b"s" * size)
    (path / "credential-ish").write_text("token", encoding="utf-8")


def _dead_token(root: Path) -> str:
    """A token whose liveness file exists and whose owner is gone -- what a
    killed process leaves (the kernel drops its lock, the file stays)."""
    token = "proc_" + os.urandom(12).hex()
    lock = process_liveness.liveness_path(root, token)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_bytes(b"")
    assert process_liveness.owner_state(root, token) == process_liveness.DEAD
    return token


# --------------------------------------------------------------------------- #
# Every exit removes it
# --------------------------------------------------------------------------- #


class TestEveryExitRemovesIt:
    def test_success(self, tmp_path):
        with ws.staging(tmp_path, "run", "node") as path:
            _fill(path)
        assert not path.exists()

    def test_exception(self, tmp_path):
        with pytest.raises(RuntimeError), ws.staging(tmp_path, "run", "node") as path:
            _fill(path)
            raise RuntimeError("checkout failed")
        assert not path.exists()

    def test_cancellation(self, tmp_path):
        class Cancelled(BaseException):
            pass

        with pytest.raises(Cancelled), ws.staging(tmp_path, "run", "node") as path:
            _fill(path)
            raise Cancelled()
        assert not path.exists()

    def test_nothing_is_left_at_any_name(self, tmp_path):
        with ws.staging(tmp_path, "run", "node") as path:
            _fill(path)
        root = ws.staging_root(tmp_path)
        # Empty parent directories of this live process may remain; no FILE
        # (the credential-bearing part) survives anywhere, and no trash does.
        left = [
            p for p in root.rglob("*")
            if p.is_file() and process_liveness.LIVENESS_DIR not in p.parts
        ]
        assert left == []
        assert not [e for e in _entries(root) if e.startswith(".trash-")]

    def test_a_removal_failure_never_masks_the_original_error(self, tmp_path, monkeypatch):
        def _broken(*_a):
            raise OSError("removal broke")

        monkeypatch.setattr(ws, "_remove_completely", _broken)
        with pytest.raises(RuntimeError, match="the real error"), ws.staging(tmp_path, "r", "n"):
            raise RuntimeError("the real error")

    def test_a_parts_segment_cannot_escape_the_root(self, tmp_path):
        with pytest.raises(ValueError):
            ws.create(tmp_path, "..", "x")


# --------------------------------------------------------------------------- #
# The sweep: owner liveness decides, never age alone
# --------------------------------------------------------------------------- #


class TestSweep:
    def test_a_dead_owners_staging_is_removed_completely(self, tmp_path):
        root = ws.staging_root(tmp_path)
        root.mkdir()
        token = _dead_token(root)
        _fill(root / token / "run" / "node")

        report = ws.sweep(tmp_path)

        assert report.removed == 1
        assert report.removed_bytes == 3 * 1000 + len("token")
        assert _entries(root) == []
        # The dead owner's liveness file goes too.
        assert process_liveness.owner_state(root, token) == process_liveness.UNKNOWN

    def test_this_live_processs_staging_is_never_swept(self, tmp_path):
        path = ws.create(tmp_path, "run", "node")
        _fill(path)

        report = ws.sweep(tmp_path)

        assert report.removed == 0 and report.kept_live == 1
        assert (path / "credential-ish").is_file()
        ws.remove(path)

    def test_an_unprovable_owner_is_never_swept(self, tmp_path):
        """A token-shaped directory with no liveness file is UNKNOWN, and unknown
        is never treated as dead."""
        root = ws.staging_root(tmp_path)
        _fill(root / ("proc_" + "a" * 24) / "run")

        report = ws.sweep(tmp_path)

        assert report.removed == 0 and report.kept_unknown == 1

    def test_the_sweep_is_idempotent(self, tmp_path):
        root = ws.staging_root(tmp_path)
        root.mkdir()
        _fill(root / _dead_token(root) / "run")
        live = ws.create(tmp_path, "run", "live")

        first = ws.sweep(tmp_path)
        second = ws.sweep(tmp_path)

        assert first.removed == 1
        assert second.removed == 0 and second.removed_bytes == 0
        assert live.is_dir()
        ws.remove(live)

    def test_an_interrupted_removal_is_finished_by_the_next_sweep(self, tmp_path, monkeypatch):
        root = ws.staging_root(tmp_path)
        root.mkdir()
        _fill(root / _dead_token(root) / "run")
        real = ws._rmtree
        calls = []

        def _fails_once(path):
            calls.append(path)
            if len(calls) == 1:
                raise OSError("interrupted mid-delete")
            real(path)

        monkeypatch.setattr(ws, "_rmtree", _fails_once)
        first = ws.sweep(tmp_path)
        # Never left under a usable name: only as trash.
        assert first.failed == 1
        assert all(e.startswith(".trash-") for e in _entries(root))

        second = ws.sweep(tmp_path)
        assert second.removed == 1
        assert _entries(root) == []

    def test_a_legacy_entry_is_removed_only_if_untouched_since_the_container_started(
        self, tmp_path, monkeypatch,
    ):
        started = time.time() - 60
        monkeypatch.setattr(ws, "_namespace_started_at", lambda: started)
        root = ws.staging_root(tmp_path)
        old = root / "006c7c18cd467df3"
        fresh = root / "0091c4beef94a001"
        _fill(old / "n1-aaaa")
        _fill(fresh / "n1-bbbb")
        for p in [old, *old.rglob("*")]:
            os.utime(p, (started - 3600, started - 3600))
        # Touched after the container started: a process in it could be using it.

        report = ws.sweep(tmp_path)

        assert report.removed == 1
        assert _entries(root) == ["0091c4beef94a001"]

    def test_a_legacy_entry_with_one_fresh_file_is_kept(self, tmp_path, monkeypatch):
        """Newest-in-tree, not the directory's own mtime."""
        started = time.time() - 60
        monkeypatch.setattr(ws, "_namespace_started_at", lambda: started)
        root = ws.staging_root(tmp_path)
        legacy = root / "fb2c2816ebe798fa"
        _fill(legacy / "n1-cccc")
        for p in [legacy, *legacy.rglob("*")]:
            os.utime(p, (started - 3600, started - 3600))
        os.utime(legacy / "n1-cccc" / "sub" / "f0", None)  # now

        assert ws.sweep(tmp_path).removed == 0

    def test_legacy_entries_are_kept_where_the_container_start_is_unknown(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(ws, "_namespace_started_at", lambda: None)
        root = ws.staging_root(tmp_path)
        legacy = root / "006c7c18cd467df3"
        _fill(legacy)
        for p in [legacy, *legacy.rglob("*")]:
            os.utime(p, (0, 0))

        report = ws.sweep(tmp_path)

        assert report.removed == 0 and report.kept_unknown == 1

    @pytest.mark.skipif(os.name != "posix", reason="reads /proc")
    def test_the_namespace_start_is_in_the_past(self):
        started = ws._namespace_started_at()
        assert started is not None and 0 < started <= time.time()

    def test_a_linked_root_is_not_walked(self, tmp_path):
        outside = tmp_path / "outside"
        _fill(outside / "x")
        base = tmp_path / "u"
        base.mkdir()
        try:
            os.symlink(outside, ws.staging_root(base), target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable here")

        assert ws.sweep(base).removed == 0
        assert (outside / "x" / "credential-ish").is_file()

    def test_the_data_root_sweep_covers_every_universe_and_logs_it(self, tmp_path, caplog):
        for uid in ("u-one", "u-two"):
            root = ws.staging_root(tmp_path / uid)
            root.mkdir(parents=True)
            _fill(root / _dead_token(root) / "run")

        with caplog.at_level("WARNING", logger="tinyassets.workspace_staging"):
            report = ws.sweep_data_root(tmp_path)

        assert report.removed == 2
        assert "removed 2 dir(s)" in caplog.text


_POSIX_ONLY = pytest.mark.skipif(
    os.name != "posix", reason="shared advisory locks (flock) are POSIX; production is Linux"
)


def _hold_share_in_subprocess(tree: Path, ready: Path) -> subprocess.Popen:
    """A separate process holding the tree's in-use share -- a worker or a git
    that outlived the parent that created the staging."""
    script = textwrap.dedent(
        f"""
        import time
        from pathlib import Path
        from tinyassets import workspace_staging as ws
        fd = ws.hold_in_use(Path({str(tree)!r}))
        Path({str(ready)!r}).write_text("held")
        time.sleep(120)
        """
    )
    proc = subprocess.Popen([sys.executable, "-c", script])
    deadline = time.monotonic() + 60
    while not ready.exists():
        assert proc.poll() is None, "helper process died"
        assert time.monotonic() < deadline, "helper never became ready"
        time.sleep(0.05)
    return proc


@_POSIX_ONLY
def test_a_dead_owners_tree_still_used_by_its_worker_is_kept(tmp_path):
    """gpt-6-astra round 1: the parent was killed, its worker was not. Owner
    DEAD is not enough; the worker's share keeps the tree."""
    root = ws.staging_root(tmp_path)
    root.mkdir()
    tree = root / _dead_token(root) / "run" / "node"
    _fill(tree)
    proc = _hold_share_in_subprocess(tree, tmp_path / "ready")
    try:
        report = ws.sweep(tmp_path)
        assert report.removed == 0 and report.kept_live == 1
        assert (tree / "credential-ish").is_file()
    finally:
        proc.kill()
        proc.wait(timeout=30)

    assert ws.sweep(tmp_path).removed == 1
    assert _entries(root) == []


@_POSIX_ONLY
def test_the_owners_own_removal_waits_for_a_user_then_queues_it(tmp_path):
    """Never removed under another process's share; queued, and the sweeper's
    retry finishes it once the share is gone (round 1, P2)."""
    path = ws.create(tmp_path, "run", "node")
    _fill(path)
    proc = _hold_share_in_subprocess(path, tmp_path / "ready")
    try:
        assert ws.remove(path, wait_s=0.2) is False
        assert (path / "credential-ish").is_file()
        assert str(path) in ws._PENDING
    finally:
        proc.kill()
        proc.wait(timeout=30)

    report = ws.sweep_data_root(tmp_path)
    assert report.removed >= 1
    assert not path.exists()
    assert str(path) not in ws._PENDING


@_POSIX_ONLY
def test_the_worker_hands_its_share_to_every_git(tmp_path, monkeypatch):
    from tinyassets import workspace_git, workspace_worker

    monkeypatch.setattr(workspace_git, "_INHERITED_FDS", ())
    path = ws.create(tmp_path, "run", "node")
    workspace_worker._mark_staging_in_use({"op": "checkout", "staging_dir": str(path)})
    (fd,) = workspace_git._INHERITED_FDS
    seen = {}

    def _launcher(command, **kwargs):
        seen.update(kwargs)

        class _Done:
            returncode, stdout, stderr = 0, b"", b""

        return _Done()

    home = tmp_path / "git-home"
    home.mkdir()
    workspace_git.run_git(
        ["--version"], cwd=path, home_dir=home, path="/usr/bin",
        timeout_s=5, launcher=_launcher,
    )
    assert fd in seen["pass_fds"]
    os.close(fd)
    ws.remove(path)


@_POSIX_ONLY
def test_an_uninspectable_lock_keeps_the_tree(tmp_path, monkeypatch):
    """Fail closed (round 2): an .inuse we cannot open may be a live worker's."""
    root = ws.staging_root(tmp_path)
    root.mkdir()
    tree = root / _dead_token(root) / "run" / "node"
    _fill(tree)
    (tree / ws.INUSE_NAME).write_bytes(b"")
    real_open = os.open

    def _denied(path, *args, **kwargs):
        if str(path).endswith(ws.INUSE_NAME):
            raise PermissionError("denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(ws.os, "open", _denied)
    report = ws.sweep(tmp_path)
    monkeypatch.undo()

    # Kept -- but reported as a failure, not passed off as "in use" (round 3).
    assert report.removed == 0 and report.failed == 1 and report.kept_live == 0
    assert (tree / "credential-ish").is_file()


@_POSIX_ONLY
def test_an_uninspectable_tree_is_logged_every_pass(tmp_path, monkeypatch, caplog):
    root = ws.staging_root(tmp_path)
    root.mkdir()
    tree = root / _dead_token(root) / "run"
    _fill(tree)

    def _unwalkable(*_a, **_k):
        raise ws.Uninspectable(f"{tree}: could not walk: denied")

    monkeypatch.setattr(ws, "_lock_tree_exclusive", _unwalkable)
    with caplog.at_level("WARNING", logger="tinyassets.workspace_staging"):
        first = ws.sweep_data_root(tmp_path)
        second = ws.sweep_data_root(tmp_path)

    assert first.failed == 1 and second.failed == 1
    assert caplog.text.count("locks uninspectable") == 2


def test_a_scoped_inheritance_reaches_only_gits_inside_the_block(tmp_path, monkeypatch):
    from tinyassets import workspace_git

    monkeypatch.setattr(workspace_git, "_INHERITED_FDS", ())
    fd = os.open(str(tmp_path / "share"), os.O_RDWR | os.O_CREAT)
    home = tmp_path / "git-home"
    home.mkdir()
    seen: list = []

    def _launcher(command, **kwargs):
        seen.append(kwargs.get("pass_fds", ()))

        class _Done:
            returncode, stdout, stderr = 0, b"", b""

        return _Done()

    def _git():
        workspace_git.run_git(
            ["--version"], cwd=tmp_path, home_dir=home, path="/usr/bin",
            timeout_s=5, launcher=_launcher,
        )

    try:
        with workspace_git.inheriting(fd):
            _git()
        _git()
    finally:
        os.close(fd)
    if os.name == "posix":
        assert fd in seen[0]
    assert fd not in seen[1], "a scoped share must not leak into later gits"


@_POSIX_ONLY
def test_the_parent_populate_git_inherits_the_staging_share(tmp_path, monkeypatch):
    """Round 3: the PARENT runs populate's git against staging, in its own
    session; it must carry the share too."""
    import tinyassets.workspace_git as wg
    from tests.test_workspace_effector import _packet, _run, _setup
    from tinyassets.effectors import EffectChain

    _root, universe_dir = _setup(tmp_path)
    chain = EffectChain(run_id="run-1", base_path=str(tmp_path), universe_id="universe-1")
    captured = {}

    def _populate(bundle, dest, ref_name, checkout_ref, *, home_dir, **_kw):
        staging = Path(bundle).parent
        captured["scoped"] = wg._SCOPED_FDS.get()
        captured["held"] = ws.in_use_fd(staging)
        Path(dest).mkdir(parents=True, exist_ok=True)
        return "c" * 40

    monkeypatch.setattr(wg, "populate_workspace_from_bundle", _populate)
    monkeypatch.setattr("tinyassets.effectors.workspace._git_path", lambda: "/usr/bin")
    _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)

    assert captured["held"] is not None
    assert captured["held"] in captured["scoped"]


@_POSIX_ONLY
def test_an_unwalkable_tree_is_kept(tmp_path, monkeypatch):
    root = ws.staging_root(tmp_path)
    root.mkdir()
    tree = root / _dead_token(root) / "run"
    _fill(tree)
    real_walk = os.walk

    def _walk_with_error(top, **kwargs):
        kwargs["onerror"](PermissionError("unreadable subdir"))
        yield from real_walk(top, **{k: v for k, v in kwargs.items() if k != "onerror"})

    monkeypatch.setattr(ws.os, "walk", _walk_with_error)
    report = ws.sweep(tmp_path)
    monkeypatch.undo()

    assert report.removed == 0
    assert (tree / "credential-ish").is_file()


def test_the_boot_sweeper_sweeps_at_once_and_runs_once(tmp_path):
    """What removes the production leftovers after deploy: the serving startup
    starts it, it sweeps immediately, and a second start is a no-op."""
    from tinyassets.universe_server import (
        start_staging_sweeper_for_serving,
        stop_workspace_sweepers_for_serving,
    )

    root = ws.staging_root(tmp_path / "u-one")
    root.mkdir(parents=True)
    leaked = root / _dead_token(root)
    _fill(leaked / "run")
    try:
        start_staging_sweeper_for_serving(tmp_path)
        assert ws.start_sweeper(tmp_path) is False
        deadline = time.monotonic() + 30
        while leaked.exists():
            assert time.monotonic() < deadline, "the boot sweep never ran"
            time.sleep(0.05)
    finally:
        stop_workspace_sweepers_for_serving()
    assert ws.stop_sweeper() is True


# --------------------------------------------------------------------------- #
# A live checkout in ANOTHER process is never swept
# --------------------------------------------------------------------------- #


def test_another_live_processs_staging_survives_until_it_dies(tmp_path):
    ready = tmp_path / "ready"
    script = textwrap.dedent(
        f"""
        import sys, time
        from pathlib import Path
        from tinyassets import workspace_staging as ws
        path = ws.create(Path({str(tmp_path)!r}), "run", "live-checkout")
        (path / "credential-ish").write_text("token")
        Path({str(ready)!r}).write_text(str(path))
        time.sleep(120)
        """
    )
    proc = subprocess.Popen([sys.executable, "-c", script])
    try:
        deadline = time.monotonic() + 60
        while not ready.exists():
            assert proc.poll() is None, "helper process died"
            assert time.monotonic() < deadline, "helper never became ready"
            time.sleep(0.05)
        live = Path(ready.read_text())

        report = ws.sweep(tmp_path)

        assert report.removed == 0 and report.kept_live == 1
        assert (live / "credential-ish").is_file()
    finally:
        proc.kill()
        proc.wait(timeout=30)

    report = ws.sweep(tmp_path)
    assert report.removed == 1
    assert not live.exists()
