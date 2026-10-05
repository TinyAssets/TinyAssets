"""The disk budget a jailed process runs under (`tinyassets.jail_disk`).

Concern 2026-10-01-no-per-universe-disk-budget-in-jails: bytes a jail writes
bypass every gated write path. These drive the real ledger and the real walk
against a temp data root (quota shrunk through the real env override, as in
tests/test_storage_accounting.py); only the volume's statvfs is faked, because a
test cannot make the real disk fill or grow on cue. The real-jail proofs live in
tests/test_universe_tools_jail.py and tests/test_provider_universe_jail.py.

Legacy test identities are retained for hygiene. Cases formerly asserting a
per-launch cap, reservation or recovery grace now assert account totals; ledger
lease tests still exercise the unchanged reservation API for gated writes.
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
    monkeypatch.setattr(jail_disk, "ACCOUNT_CHECK_SECONDS", 0)
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
    other = _universe(base, "u-two")
    _write(udir, "a.bin", 30 * KIB)
    _write(other, "b.bin", 20 * KIB)
    first, second = jail_disk.open_budget(udir), jail_disk.open_budget(other)
    assert first.bound == second.bound == 50 * KIB
    assert first.notice == second.notice == ""
    assert sa.usage(base, A).reserved_bytes == 0
    _write(other, "c.bin", 51 * KIB)
    assert first.breach(force=True) == second.breach(force=True) == jail_disk.STORAGE_LIMIT
    first.settle()
    second.settle()


def test_the_launch_measures_its_universe_fresh(base, volume):
    udir = _universe(base, "u-one")
    jail_disk.open_budget(udir).settle()
    # A jailed write lands; nothing gated it, so only a fresh walk sees it.
    _write(udir, "jail-wrote.bin", 100 * KIB)
    budget = jail_disk.open_budget(udir)
    assert budget.bound == 0
    assert "out of cloud storage" in budget.notice
    budget.settle()


def test_a_full_account_still_launches_on_the_grace_budget(base, volume):
    udir = _universe(base, "u-one")
    _write(udir, "full.bin", 100 * KIB)
    budget = jail_disk.open_budget(udir)
    assert budget.bound == 0
    assert sa.usage(base, A).reserved_bytes == 0
    # Never the owner's numbers: a collaborator may be the caller.
    assert "KiB" not in budget.notice.split("at most")[0]
    budget.settle()


def test_large_protected_provider_cache_does_not_warn_or_charge_owner(base, volume):
    udir = _universe(base, "u-one")
    for directory in (".credentials/claude/projects", ".credentials/codex/plugins"):
        path = udir / directory
        path.mkdir(parents=True)
        _write(path, "runtime.bin", 112 * KIB)
    _write(udir, "mine.bin", 36 * KIB)
    budget = jail_disk.open_budget(udir)
    try:
        current = sa.usage(base, A)
        assert current.measured_bytes == 36 * KIB
        assert current.quota_bytes == 100 * KIB
        assert current.reserved_bytes == 0
        assert budget.notice == ""
    finally:
        budget.settle()
    _write(udir, "mine.bin", 101 * KIB)
    over = jail_disk.open_budget(udir)
    try:
        assert "out of cloud storage" in over.notice
        assert sa.usage(base, A).measured_bytes == 101 * KIB
    finally:
        over.settle()


def test_reservations_and_headroom_do_not_claim_user_storage_is_full(base, volume, monkeypatch):
    udir = _universe(base, "u-one")
    _write(udir, "mine.bin", 36 * KIB)
    first, second = jail_disk.open_budget(udir), jail_disk.open_budget(udir)
    try:
        assert first.bound == second.bound == 64 * KIB
        assert not first.notice and not second.notice
        assert sa.usage(base, A).measured_bytes == 36 * KIB
        assert sa.usage(base, A).reserved_bytes == 0
    finally:
        first.settle()
        second.settle()


@pytest.mark.parametrize("committed", [False, True])
def test_nonfull_account_notice_does_not_invent_active_calls(base, volume, monkeypatch, committed):
    udir = _universe(base, "u-one")
    _write(udir, "mine.bin", 95 * KIB)
    if committed:
        sa.commit(sa.reserve(
            base, account_id=A, scope_id=A, store="ui_library", nbytes=5 * KIB,
        ))
    budget = jail_disk.open_budget(udir)
    try:
        # Committed ordinary writes retain their accounting until remeasured.
        assert not budget.notice
        assert budget.bound == (0 if committed else 5 * KIB)
        assert sa.usage(base, A).committed_bytes == (5 * KIB if committed else 0)
    finally:
        budget.settle()


def test_an_unattributed_universe_gets_the_launch_cap(base, volume):
    udir = _universe(base, "u-nobody", owner=None)
    budget = jail_disk.open_budget(udir)
    assert budget.account is None and budget.notice == ""
    assert budget.bound == volume.free
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
    _write(udir, "almost.bin", 99 * KIB)
    budget = jail_disk.open_budget(udir)
    assert budget.bound == KIB
    _write(udir, "extra.bin", KIB + 1)
    assert budget.breach(force=True) == jail_disk.STORAGE_LIMIT
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
    _write(udir, "half.bin", 50 * KIB)
    budget = jail_disk.open_budget(udir)
    # Jails no longer reserve future capacity; ordinary writer leases retain
    # their existing checked-renewal contract independently of jail lifetime.
    lease = sa.reserve(base, account_id=A, scope_id="u-one", store="universe_files", nbytes=KIB)
    with sa._txn(base) as conn:
        conn.execute("UPDATE pending SET created_at = created_at - ?", (sa.RESERVED_TTL_S + 1,))
    assert sa.renew_checked(lease)
    sa.measure(base, "u-one", "universe_files")
    assert sa.usage(base, A).reserved_bytes == KIB
    assert budget.breach(force=True) is None
    assert budget.bound == 49 * KIB
    sa.release(lease)
    budget.settle()


@pytest.mark.parametrize("loss", ["release", "expiry", "commit"])
def test_a_lost_lease_stops_without_reclaiming_reallocated_capacity(
    base, volume, monkeypatch, loss,
):
    _universe(base, "u-one")
    original = sa.reserve(
        base, account_id=A, scope_id="u-one", store="universe_files", nbytes=50 * KIB,
    )
    if loss == "release":
        sa.release(original)
    elif loss == "expiry":
        with sa._txn(base) as conn:
            conn.execute("UPDATE pending SET created_at = created_at - ? WHERE id = ?",
                         (sa.RESERVED_TTL_S + 1, original.id))
        sa.measure(base, "u-one", "universe_files")
    else:
        sa.commit(original)
    assert not sa.renew_checked(original)
    replacement = sa.reserve(
        base, account_id=A, scope_id="u-one", store="universe_files", nbytes=KIB,
    )
    before = sa.usage(base, A).used_bytes
    assert sa.renew_checked(replacement)
    assert not sa.renew_checked(original)
    assert sa.usage(base, A).used_bytes == before
    assert replacement.id != original.id
    sa.release(replacement)


@pytest.mark.parametrize("field,value", [("account_id", "workos|bob"), ("bytes", 1)])
def test_checked_renewal_rejects_a_mismatched_handle(base, volume, field, value):
    from dataclasses import replace

    _universe(base, "u-one")
    lease = sa.reserve(base, account_id=A, scope_id="u-one", store="universe_files", nbytes=KIB)
    handle = replace(lease, **{field: value})
    assert not sa.renew_checked(handle)
    assert sa.renew_checked(lease)
    sa.release(lease)


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
    with monkeypatch.context() as patch:
        def fail_connect(_base):
            raise sqlite3.OperationalError(message)

        patch.setattr(sa, "_connect", fail_connect)
        assert budget.breach(force=True) == jail_disk.STORAGE_LIMIT
    assert budget.breach(force=True) == jail_disk.STORAGE_LIMIT
    budget.settle()


@pytest.mark.parametrize("owner", [A, None])
def test_a_settled_budget_cannot_authorize_more_execution(base, volume, owner):
    budget = jail_disk.open_budget(_universe(base, "u-one", owner))
    budget.settle()
    budget.settle()
    assert budget.breach() == jail_disk.STORAGE_LIMIT


def test_a_full_account_preserves_bounded_provider_recovery(base, volume, monkeypatch):
    udir = _universe(base, "u-one")
    _write(udir, "full.bin", 100 * KIB)
    budget = jail_disk.open_budget(udir)
    try:
        assert budget.bound == 0
        assert sa.usage(base, A).reserved_bytes == 0
        assert "KiB" not in budget.notice
        assert (udir / "full.bin").read_bytes() == b"x" * (100 * KIB)
        (udir / "full.bin").unlink()
        assert budget.breach(force=True) is None
        assert sa.measure(base, "u-one", "universe_files") == 0
        volume.free = jail_disk.MIN_FREE_DISK_BYTES - 1
        assert budget.breach() == jail_disk.DISK_LIMIT
    finally:
        budget.settle()


def test_a_nearly_full_account_preserves_bounded_provider_recovery(base, volume, monkeypatch):
    udir = _universe(base, "u-one")
    _write(udir, "almost.bin", 99 * KIB)
    budget = jail_disk.open_budget(udir)
    try:
        (udir / ".runtime").mkdir()
        _write(udir / ".runtime", "session.bin", KIB)
        assert budget.breach(force=True) is None
        assert sa.measure(base, "u-one", "universe_files") == 100 * KIB
        assert budget.bound == 0
        assert (udir / "almost.bin").read_bytes() == b"x" * (99 * KIB)
        (udir / "almost.bin").unlink()
        assert budget.breach(force=True) is None
        assert sa.measure(base, "u-one", "universe_files") == KIB
        _write(udir / ".runtime", "overflow.bin", 99 * KIB + 1)
        assert budget.breach(force=True) == jail_disk.STORAGE_LIMIT
    finally:
        budget.settle()


def test_ordinary_write_reservations_still_reduce_total_storage(base, volume):
    udir = _universe(base, "u-one")
    lease = sa.reserve(
        base, account_id=A, scope_id="u-one", store="universe_files", nbytes=90 * KIB,
    )
    budget = jail_disk.open_budget(udir)
    try:
        assert budget.bound == 10 * KIB
        assert not budget.notice  # held capacity is not a full-data claim
        _write(udir, "jail.bin", 11 * KIB)
        assert budget.breach(force=True) == jail_disk.STORAGE_LIMIT
        assert sa.usage(base, A).reserved_bytes == 90 * KIB
    finally:
        sa.release(lease)
        budget.settle()


def test_unavailable_accounting_refuses_admission_without_a_write_allowance(
    base, volume, monkeypatch,
):
    import sqlite3

    udir = _universe(base, "u-one")
    def unavailable(*_args):
        raise sqlite3.OperationalError("unavailable")

    monkeypatch.setattr(sa, "measure", unavailable)
    with pytest.raises(jail_disk.DiskFloorRefused, match="could not be measured"):
        jail_disk.open_budget(udir)


def test_full_account_cleanup_does_not_mint_new_growth_on_next_call(base, volume):
    udir = _universe(base, "u-one")
    _write(udir, "full.bin", 110 * KIB)
    first = jail_disk.open_budget(udir)
    assert first.bound == 0
    _write(udir, "full.bin", 105 * KIB)
    assert first.breach(force=True) is None
    _write(udir, "extra.bin", 1)
    assert first.breach(force=True) == jail_disk.STORAGE_LIMIT
    first.settle()
    second = jail_disk.open_budget(udir)
    assert second.bound == 0
    _write(udir, "extra.bin", 2)
    assert second.breach(force=True) == jail_disk.STORAGE_LIMIT
    second.settle()


def test_nested_supervisors_coalesce_account_walks(base, volume, monkeypatch):
    udir = _universe(base, "u-one")
    first, second = jail_disk.open_budget(udir), jail_disk.open_budget(udir)
    monkeypatch.setattr(jail_disk, "ACCOUNT_CHECK_SECONDS", 0.5)
    monkeypatch.setattr(jail_disk, "WALK_SECONDS", 0)
    measured = []
    original = sa.measure
    def measure(*args, **kwargs):
        measured.append(args[1:])
        return original(*args, **kwargs)

    monkeypatch.setattr(sa, "measure", measure)
    assert first.breach() is None
    assert second.breach() is None
    assert measured == []
    assert second.breach(force=True) is None
    assert set(measured) == {("u-one", "universe_files"), ("u-one", "workspaces")}
    first.settle()
    second.settle()
