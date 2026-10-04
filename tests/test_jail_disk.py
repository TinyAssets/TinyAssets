"""The disk budget a jailed process runs under (`tinyassets.jail_disk`).

Concern 2026-10-01-no-per-universe-disk-budget-in-jails: bytes a jail writes
bypass every gated write path. These drive the real ledger and the real walk
against a temp data root (quota shrunk through the real env override, as in
tests/test_storage_accounting.py); only the volume's statvfs is faked, because a
test cannot make the real disk fill or grow on cue. The real-jail proofs live in
tests/test_universe_tools_jail.py and tests/test_provider_universe_jail.py.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets import jail_disk
from tinyassets import storage_accounting as sa
from tinyassets.daemon_server import grant_universe_ownership, initialize_author_server
from tinyassets.providers.provider_jail import JailMount, UniverseView, jail_argv

A = "workos|alice"
KIB = 1024


class _Volume:
    """A fake volume: ``used`` bytes in use, ``free`` bytes and ``inodes`` left."""

    def __init__(self, used: int = 10 * 1024**3, free: int = 20 * 1024**3, inodes: int = 10**6):
        self.used, self.free, self.inodes = used, free, inodes

    def statvfs(self, _path):
        return SimpleNamespace(
            f_frsize=1, f_blocks=self.used + self.free, f_bfree=self.free,
            f_bavail=self.free, f_favail=self.inodes, f_files=10**7,
        )


@pytest.fixture
def volume(monkeypatch: pytest.MonkeyPatch) -> _Volume:
    vol = _Volume()
    monkeypatch.setattr(jail_disk, "_statvfs", vol.statvfs)
    return vol


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    # 100 KiB free quota: the real override, in its real unit.
    monkeypatch.setenv("TINYASSETS_FREE_STORAGE_GIB", str(100 * KIB / 1024**3))
    initialize_author_server(root)
    # Shrunk so the numbers stay in kilobytes; the logic is unchanged.
    monkeypatch.setattr(jail_disk, "LAUNCH_BYTES_CAP", 50 * KIB)
    monkeypatch.setattr(jail_disk, "GRACE_BYTES", 4 * KIB)
    # Preserve these legacy allocation/lease assertions at their 100 KiB scale.
    # Production headroom plus startup recovery is covered in
    # test_jail_reservation_headroom.py with the real 16 MiB constants.
    monkeypatch.setattr(jail_disk, "WRITE_HEADROOM_BYTES", 0)
    return root


def _universe(base: Path, uid: str, owner: str | None = A) -> Path:
    if owner is not None:
        grant_universe_ownership(base, universe_id=uid, owner_id=owner)
    udir = base / uid
    udir.mkdir(exist_ok=True)
    return udir


def _write(udir: Path, name: str, size: int) -> None:
    (udir / name).write_bytes(b"x" * size)


# ── the private /tmp and every view tmpfs are sized ─────────────────────────


@pytest.mark.skipif(os.name != "posix", reason="jail views are POSIX paths")
def test_every_tmpfs_in_the_jail_argv_is_sized(tmp_path: Path):
    universe = tmp_path / "u-one"
    universe.mkdir()
    view = UniverseView(
        universe_dir=universe,
        mounts=(
            JailMount("bind", str(universe), universe),
            JailMount("tmpfs", str(universe / ".hidden")),
        ),
    )
    argv = jail_argv(["true"], view, bwrap_path="/usr/bin/bwrap")
    tmpfs_at = [i for i, arg in enumerate(argv) if arg == "--tmpfs"]
    assert len(tmpfs_at) == 2
    for at in tmpfs_at:
        assert argv[at - 2] == "--size", argv
    sizes = {argv[at + 1]: int(argv[at - 1]) for at in tmpfs_at}
    assert sizes == {
        "/tmp": jail_disk.TMP_BYTES,
        str(universe / ".hidden"): jail_disk.MASK_TMPFS_BYTES,
    }


# ── at launch ───────────────────────────────────────────────────────────────


def test_a_launch_below_the_volume_floor_is_refused(base, volume):
    udir = _universe(base, "u-one")
    volume.free = jail_disk.MIN_FREE_DISK_BYTES - 1
    with pytest.raises(jail_disk.DiskFloorRefused, match="nearly full"):
        jail_disk.open_budget(udir)
    volume.free, volume.inodes = 20 * 1024**3, jail_disk.MIN_FREE_INODES - 1
    with pytest.raises(jail_disk.DiskFloorRefused, match="inodes"):
        jail_disk.open_budget(udir)


def test_the_bound_is_the_accounts_fresh_headroom_and_is_reserved(base, volume):
    udir = _universe(base, "u-one")
    _write(udir, "a.bin", 30 * KIB)
    other = _universe(base, "u-two")
    _write(other, "b.bin", 20 * KIB)

    first = jail_disk.open_budget(udir)
    # 100 KiB quota - 50 KiB on disk (measured at launch, not cached).
    assert first.bound == 50 * KIB and first.notice == ""
    assert first.start_bytes == 30 * KIB
    # The headroom is pending while the first launch runs: a concurrent launch
    # of the same account cannot spend it again.
    second = jail_disk.open_budget(other)
    assert second.bound == jail_disk.GRACE_BYTES and second.notice
    first.settle()
    second.settle()
    third = jail_disk.open_budget(other)
    assert third.bound == 50 * KIB
    third.settle()


def test_the_launch_measures_its_universe_fresh(base, volume):
    udir = _universe(base, "u-one")
    jail_disk.open_budget(udir).settle()
    # A jailed write lands; nothing gated it, so only a fresh walk sees it.
    _write(udir, "jail-wrote.bin", 100 * KIB)
    budget = jail_disk.open_budget(udir)
    assert budget.bound == jail_disk.GRACE_BYTES
    assert "out of cloud storage" in budget.notice
    budget.settle()


def test_a_full_account_still_launches_on_the_grace_budget(base, volume):
    udir = _universe(base, "u-one")
    _write(udir, "full.bin", 100 * KIB)
    budget = jail_disk.open_budget(udir)
    assert budget.bound == jail_disk.GRACE_BYTES
    assert budget.reservation is None
    # Never the owner's numbers: a collaborator may be the caller.
    assert "KiB" not in budget.notice.split("at most")[0]
    budget.settle()


def test_an_unattributed_universe_gets_the_launch_cap(base, volume):
    udir = _universe(base, "u-nobody", owner=None)
    budget = jail_disk.open_budget(udir)
    assert budget.bound == jail_disk.LAUNCH_BYTES_CAP and budget.notice == ""
    budget.settle()


def test_settle_releases_the_reservation_and_marks_the_files_dirty(base, volume):
    udir = _universe(base, "u-one")
    budget = jail_disk.open_budget(udir)
    budget.settle()
    budget.settle()  # idempotent
    conn = sa._connect(base)
    try:
        pending = conn.execute("SELECT COUNT(*) FROM pending").fetchone()[0]
        dirty = conn.execute(
            "SELECT dirty FROM measurements WHERE scope_id = 'u-one' AND store = 'universe_files'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert pending == 0 and dirty == 1


# ── while it runs ───────────────────────────────────────────────────────────


def test_growth_of_its_own_universe_past_the_bound_is_a_storage_limit(base, volume):
    udir = _universe(base, "u-one")
    budget = jail_disk.open_budget(udir)
    assert budget.breach() is None
    _write(udir, "fill.bin", budget.bound + 1)
    volume.used += budget.bound + 1
    assert budget.breach() == jail_disk.STORAGE_LIMIT
    budget.settle()


def test_many_small_files_count_the_same_as_one_big_one(base, volume):
    udir = _universe(base, "u-one")
    budget = jail_disk.open_budget(udir)
    (udir / "many").mkdir()
    for index in range(budget.bound // KIB + 1):
        _write(udir / "many", f"f{index}", KIB)
    volume.used += budget.bound + KIB
    assert budget.breach() == jail_disk.STORAGE_LIMIT
    budget.settle()


def test_another_users_writes_never_kill_this_launch(base, volume):
    udir = _universe(base, "u-one")
    budget = jail_disk.open_budget(udir)
    # The volume grows by far more than the bound -- none of it here.
    volume.used += 10 * budget.bound
    assert budget.breach() is None
    # Re-armed from the new level: this universe's own growth still counts.
    _write(udir, "mine.bin", budget.bound + 1)
    volume.used += budget.bound + 1
    assert budget.breach() == jail_disk.STORAGE_LIMIT
    budget.settle()


def test_a_deleting_launch_on_a_full_account_is_not_stopped(base, volume):
    udir = _universe(base, "u-one")
    _write(udir, "full.bin", 100 * KIB)
    budget = jail_disk.open_budget(udir)
    (udir / "full.bin").unlink()
    _write(udir, "small.bin", 2 * KIB)
    volume.used += 10 * budget.bound  # someone else, again
    assert budget.breach() is None
    budget.settle()


def test_growth_masked_by_another_users_deletes_is_caught_by_the_timed_walk(
    base, volume, monkeypatch,
):
    udir = _universe(base, "u-one")
    budget = jail_disk.open_budget(udir)
    _write(udir, "fill.bin", budget.bound + 1)
    # Someone else deleted as much as this universe wrote: the volume is flat.
    assert budget.breach() is None
    monkeypatch.setattr(jail_disk, "WALK_SECONDS", 0.0)
    assert budget.breach() == jail_disk.STORAGE_LIMIT
    budget.settle()


def test_a_nearly_full_account_gets_no_less_than_the_grace_budget(base, volume):
    udir = _universe(base, "u-one")
    _write(udir, "almost.bin", 99 * KIB)  # 1 KiB of headroom, below the grace
    budget = jail_disk.open_budget(udir)
    assert budget.bound == jail_disk.GRACE_BYTES
    budget.settle()


def test_crossing_the_volume_floor_mid_run_is_a_disk_limit(base, volume):
    udir = _universe(base, "u-one")
    budget = jail_disk.open_budget(udir)
    volume.free = jail_disk.MIN_FREE_DISK_BYTES - 1
    assert budget.breach() == jail_disk.DISK_LIMIT
    budget.settle()


def test_an_adapters_exit_error_says_the_disk_budget_stopped_it():
    from tinyassets.providers.owned_process import disk_stop_note

    assert "cloud storage" in disk_stop_note(SimpleNamespace(disk_killed="storage_limit"))
    assert "nearly full" in disk_stop_note(SimpleNamespace(disk_killed="disk_limit"))
    assert disk_stop_note(SimpleNamespace(disk_killed=None)) == ""
    assert disk_stop_note(object()) == ""


@pytest.mark.parametrize("where", [".runtime", "workspaces"])
def test_writes_outside_the_charged_store_still_count_against_the_launch(
    base, volume, monkeypatch, where,
):
    """The provider jail can write ``.runtime`` and ``workspaces``, which the
    account's ``universe_files`` store leaves out; the launch's walk does not."""
    udir = _universe(base, "u-one")
    budget = jail_disk.open_budget(udir)
    (udir / where).mkdir()
    _write(udir / where, "fill.bin", budget.bound + 1)
    monkeypatch.setattr(jail_disk, "WALK_SECONDS", 0.0)
    assert budget.breach() == jail_disk.STORAGE_LIMIT
    budget.settle()


def test_a_long_launch_keeps_its_reservation_past_the_ledger_ttl(base, volume, monkeypatch):
    udir = _universe(base, "u-one")
    _write(udir, "half.bin", 50 * KIB)  # the other 50 KiB is this launch's
    budget = jail_disk.open_budget(udir)
    assert budget.bound == 50 * KIB
    conn = sa._connect(base)
    try:
        conn.execute("UPDATE pending SET created_at = created_at - ?", (sa.RESERVED_TTL_S + 1,))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(jail_disk, "RENEW_SECONDS", 0.0)
    assert budget.breach() is None  # renews
    # A later measurement must not drop it as a crashed writer's...
    sa.measure(base, "u-one", "universe_files")
    # ...so a concurrent launch of the same account still finds the headroom spent.
    other = jail_disk.open_budget(_universe(base, "u-two"))
    assert other.bound == jail_disk.GRACE_BYTES
    other.settle()
    budget.settle()


@pytest.mark.parametrize("loss", ["release", "expiry", "commit"])
def test_a_lost_lease_stops_without_reclaiming_reallocated_capacity(
    base, volume, monkeypatch, loss,
):
    udir = _universe(base, "u-one")
    budget = jail_disk.open_budget(udir)
    original = budget.reservation
    if loss == "release":
        sa.release(original)
    elif loss == "expiry":
        with sa._txn(base) as conn:
            conn.execute(
                "UPDATE pending SET created_at = created_at - ? WHERE id = ?",
                (sa.RESERVED_TTL_S + 1, original.id),
            )
        sa.measure(base, udir.name, "universe_files")
    else:
        sa.commit(original)
    replacement = jail_disk.open_budget(_universe(base, "u-two"))
    other_owner = jail_disk.open_budget(_universe(base, "u-other", "workos|bob"))
    before = sa.usage(base, A).used_bytes
    monkeypatch.setattr(jail_disk, "RENEW_SECONDS", 0.0)
    assert budget.breach() == jail_disk.STORAGE_LIMIT
    assert replacement.breach() is None
    assert other_owner.breach() is None
    assert sa.usage(base, A).used_bytes == before
    assert not sa.renew_checked(original)
    assert replacement.reservation.id != original.id
    # The failure stays latched even if future renewal calls would succeed.
    monkeypatch.setattr(sa, "renew_checked", lambda reservation: True)
    assert budget.breach() == jail_disk.STORAGE_LIMIT
    replacement.settle()
    other_owner.settle()
    budget.settle()


@pytest.mark.parametrize("field,value", [("account_id", "workos|bob"), ("bytes", 1)])
def test_checked_renewal_rejects_a_mismatched_handle(base, volume, field, value):
    from dataclasses import replace

    budget = jail_disk.open_budget(_universe(base, "u-one"))
    handle = replace(budget.reservation, **{field: value})
    assert not sa.renew_checked(handle)
    assert sa.renew_checked(budget.reservation)
    budget.settle()


def test_checked_renewal_supports_zero_byte_and_unattributed_handles(base, volume):
    _universe(base, "u-one")
    reservation = sa.reserve(
        base, account_id=A, scope_id="u-one", store="universe_files", nbytes=0,
    )
    assert sa.renew_checked(reservation)
    assert sa.renew(reservation) is None
    assert sa.renew_checked(sa.Reservation(base, None, None, 0))
    sa.release(reservation)
    assert not sa.renew_checked(reservation)


@pytest.mark.parametrize("message", ["database is locked", "disk I/O error"])
def test_a_ledger_error_stops_the_budget_and_does_not_advance_renewal_clock(
    base, volume, monkeypatch, message,
):
    import sqlite3

    budget = jail_disk.open_budget(_universe(base, "u-one"))
    previous = budget._last_renew
    monkeypatch.setattr(jail_disk, "RENEW_SECONDS", 0.0)
    with monkeypatch.context() as patch:
        def fail_connect(_base):
            raise sqlite3.OperationalError(message)

        patch.setattr(sa, "_connect", fail_connect)
        assert budget.breach() == jail_disk.STORAGE_LIMIT
        assert budget._last_renew == previous
        assert sa.renew(budget.reservation) is None
    assert sa.renew_checked(budget.reservation)
    assert budget.breach() == jail_disk.STORAGE_LIMIT
    budget.settle()


@pytest.mark.parametrize("owner", [A, None])
def test_a_settled_budget_cannot_authorize_more_execution(base, volume, owner):
    budget = jail_disk.open_budget(_universe(base, "u-one", owner))
    budget.settle()
    budget.settle()
    assert budget.breach() == jail_disk.STORAGE_LIMIT


def test_a_full_account_preserves_bounded_provider_recovery(base, volume, monkeypatch):
    """Session setup must survive a poll BEFORE the agent can delete a file.

    Extends the existing exact-grace contract with startup-before-cleanup ordering.
    The provider's writable runtime is not billed, but the jail still bounds it.
    """
    udir = _universe(base, "u-one")
    _write(udir, "full.bin", 100 * KIB)
    budget = jail_disk.open_budget(udir)
    try:
        (udir / ".runtime").mkdir()
        _write(udir / ".runtime", "session.bin", KIB)
        monkeypatch.setattr(jail_disk, "WALK_SECONDS", 0.0)
        assert jail_disk._jail_writable_bytes(udir) == 101 * KIB
        assert sa.measure(base, "u-one", "universe_files") == 100 * KIB
        assert budget.breach() is None, "session setup must reach the cleanup step"
        # Original protected contract assertions, unchanged.
        assert budget.bound == jail_disk.GRACE_BYTES
        assert budget.reservation is None
        # Never the owner's numbers: a collaborator may be the caller.
        assert "KiB" not in budget.notice.split("at most")[0]
        assert (udir / "full.bin").read_bytes() == b"x" * (100 * KIB)
        (udir / "full.bin").unlink()
        assert budget.breach() is None
        assert sa.measure(base, "u-one", "universe_files") == 0
        # Recovery cannot bypass the shared-volume safety floor.
        volume.free = jail_disk.MIN_FREE_DISK_BYTES - 1
        assert budget.breach() == jail_disk.DISK_LIMIT
    finally:
        budget.settle()


def test_a_nearly_full_account_preserves_bounded_provider_recovery(base, volume, monkeypatch):
    udir = _universe(base, "u-one")
    _write(udir, "almost.bin", 99 * KIB)  # 1 KiB of headroom, below the grace
    budget = jail_disk.open_budget(udir)
    try:
        (udir / ".runtime").mkdir()
        _write(udir / ".runtime", "session.bin", KIB)
        monkeypatch.setattr(jail_disk, "WALK_SECONDS", 0.0)
        assert jail_disk._jail_writable_bytes(udir) == 100 * KIB
        assert sa.measure(base, "u-one", "universe_files") == 99 * KIB
        assert budget.breach() is None, "session setup must reach the cleanup step"
        # Original grace-bound assertion, unchanged; also preserve admission.
        assert budget.bound == jail_disk.GRACE_BYTES
        assert budget.reservation.bytes == KIB
        assert (udir / "almost.bin").read_bytes() == b"x" * (99 * KIB)
        (udir / "almost.bin").unlink()
        assert budget.breach() is None
        assert sa.measure(base, "u-one", "universe_files") == 0
        # Excluding runtime from billing must not make its allocation unbounded.
        _write(udir / ".runtime", "overflow.bin", 99 * KIB + budget.bound + 1)
        assert budget.breach() == jail_disk.STORAGE_LIMIT
    finally:
        budget.settle()
