"""Real ledger/UI regression proofs; sparse retained files, temp roots only.

Overlapping open_budget calls model independently supervised launches. These
are not claims that NodeSandbox reserves or that a completed Bash still holds
its budget. No providers, accounts, network, or user workflows are invoked.
"""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tinyassets import custom_agents
from tinyassets import jail_disk as jd
from tinyassets import storage_accounting as sa
from tinyassets.daemon_server import grant_universe_ownership, initialize_author_server

A = "workos|alice"
B = "workos|bob"
MIB = 1024**2
GIB = 1024**3


@pytest.fixture
def base(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setenv("TINYASSETS_FREE_STORAGE_GIB", "2")
    initialize_author_server(root)
    return root


def universe(base, uid="u-one", owner=A, retained=0):
    grant_universe_ownership(base, universe_id=uid, owner_id=owner)
    root = base / uid
    root.mkdir()
    # Logical bytes drive accounting. Sparse files avoid allocating GiB in CI.
    with (root / "retained.bin").open("wb") as out:
        out.truncate(retained)
    return root


def launch(root):
    return jd.open_budget(root, min_free_bytes=0, min_free_inodes=0)


def ui_write(base, *, owner=A, uid="u-one"):
    library = [{"ui_id": "example", "html": "x" * 48 * 1024}]
    saved = custom_agents.save_app_ui(
        base, owner_user_id=owner, universe_id=uid, expected_revision=0,
        changes={"ui_library": library},
    )
    assert saved["ui_library"] == library
    return saved


def ui_reserve(base, nbytes, owner=A):
    return sa.reserve(
        base, account_id=owner, scope_id=owner, store="ui_library", nbytes=nbytes,
    )


@pytest.mark.parametrize("retained,launches", [(0, 2), (100 * MIB, 2), (1500 * MIB, 1)])
@pytest.mark.parametrize("same_root", [True, False])
def test_speculative_launches_leave_room_for_real_ui_save(base, retained, launches, same_root):
    root = universe(base, retained=retained)
    other = root if same_root else universe(base, "u-two")
    budgets = [launch(root)]
    try:
        if launches == 2:
            budgets.append(launch(other))
        assert sum(b.bound for b in budgets) + retained == 2 * GIB - jd.WRITE_HEADROOM_BYTES
        assert all(b.bound == b.reservation.bytes for b in budgets)
        # Remeasuring the real stores still retains all running reservations.
        for scope, store in sa._scopes(base, A):
            sa.measure(base, scope, store)
        assert sa.usage(base, A).used_bytes == 2 * GIB - jd.WRITE_HEADROOM_BYTES
        ui_write(base)
        sa.measure(base, A, "ui_library")
        usage = sa.usage(base, A)
        assert usage.used_bytes <= usage.quota_bytes
        assert any(store == "ui_library" for _, store, _ in usage.breakdown)
    finally:
        for budget in budgets:
            budget.settle()


def test_completed_sequential_tool_budget_is_not_a_second_reservation(base):
    root = universe(base, retained=100 * MIB)
    provider = launch(root)
    tool = launch(root)
    tool.settle()
    tool.settle()
    assert sa.usage(base, A).used_bytes == 100 * MIB + provider.bound
    ui_write(base)
    provider.settle()


def test_same_owner_concurrent_launches_fit_without_oversubscribing_reservations(base):
    roots = [universe(base, f"u-{i}") for i in range(6)]
    barrier = threading.Barrier(len(roots))

    def start(root):
        barrier.wait()
        return launch(root)

    with ThreadPoolExecutor(max_workers=len(roots)) as pool:
        budgets = list(pool.map(start, roots))
    try:
        assert sum(b.bound for b in budgets) == 2 * GIB - jd.WRITE_HEADROOM_BYTES
        assert all(b.reservation is not None and b.bound == b.reservation.bytes for b in budgets)
        assert sorted(b.bound for b in budgets) == [0] * 4 + [GIB - jd.WRITE_HEADROOM_BYTES, GIB]
        write = ui_reserve(base, jd.WRITE_HEADROOM_BYTES)
        assert sa.usage(base, A).used_bytes == 2 * GIB
        with pytest.raises(sa.StorageRefused):
            ui_reserve(base, 1)
        sa.release(write)
    finally:
        for budget in budgets:
            budget.settle()


def test_different_owners_do_not_share_reservations_or_headroom(base):
    alice = universe(base)
    bob = universe(base, "u-bob", B)
    budgets = [launch(alice), launch(alice), launch(bob)]
    try:
        assert sa.usage(base, A).used_bytes == 2 * GIB - jd.WRITE_HEADROOM_BYTES
        assert sa.usage(base, B).used_bytes == GIB
        ui_write(base, owner=B, uid="u-bob")
        ui_write(base)
    finally:
        for budget in budgets:
            budget.settle()


@pytest.mark.parametrize(
    "remaining", [0, 1, MIB, jd.WRITE_HEADROOM_BYTES, jd.WRITE_HEADROOM_BYTES + 1],
)
def test_near_full_actual_storage_never_expands_a_launch_grant(base, remaining, monkeypatch):
    root = universe(base, retained=2 * GIB - remaining)
    budget = launch(root)
    try:
        assert budget.bound == budget.reservation.bytes
        assert budget.bound == max(0, remaining - jd.WRITE_HEADROOM_BYTES)
        ordinary = ui_reserve(base, remaining - budget.bound)
        with pytest.raises(sa.StorageRefused):
            ui_reserve(base, 1)
        sa.release(ordinary)
        (root / "growth.bin").write_bytes(b"x" * (budget.bound + 1))
        monkeypatch.setattr(jd, "WALK_SECONDS", 0)
        assert budget.breach() == jd.STORAGE_LIMIT
    finally:
        budget.settle()


def test_over_quota_actual_bytes_never_receive_unreserved_grace(base, monkeypatch):
    root = universe(base, retained=2 * GIB + 1)
    budget = launch(root)
    try:
        assert budget.bound == 0 and budget.reservation is None
        assert "no additional cloud storage allocation" in budget.notice
        assert sa.usage(base, A).used_bytes == 2 * GIB + 1
        with pytest.raises(sa.StorageRefused):
            ui_reserve(base, 1)
        # Read/delete recovery remains possible with no grant expansion.
        (root / "retained.bin").unlink()
        monkeypatch.setattr(jd, "WALK_SECONDS", 0)
        assert budget.breach() is None
    finally:
        budget.settle()


def test_zero_quota_and_zero_actual_data_grant_zero_growth(base, monkeypatch):
    root = universe(base)
    monkeypatch.setattr(sa, "_quota", lambda *_: (0, "free"))
    budget = launch(root)
    assert budget.bound == budget.reservation.bytes == 0
    with pytest.raises(sa.StorageRefused):
        ui_reserve(base, 1)
    budget.settle()


def test_a_crashed_stopped_writer_is_remeasured_before_its_lease_expires(base):
    root = universe(base)
    old = launch(root)
    (root / "landed.bin").write_bytes(b"x" * 1024)
    # Model a stopped writer and a restarted observer using a fresh connection;
    # this deliberately does not claim an orphan can safely keep writing.
    with sa._txn(base) as conn:
        conn.execute("UPDATE pending SET created_at = 0 WHERE id = ?", (old.reservation.id,))
    fresh = launch(root)
    try:
        assert sa.usage(base, A).used_bytes == 1024 + fresh.bound
        # Stale settlement cannot delete a replacement reservation (unique IDs).
        old.settle()
        old.settle()
        assert sa.usage(base, A).used_bytes >= fresh.bound
    finally:
        fresh.settle()


def test_zero_growth_lease_expiry_does_not_release_capacity(base):
    root = universe(base, retained=2 * GIB - 1)
    budget = launch(root)
    assert budget.reservation.bytes == 0
    before = sa.usage(base, A).used_bytes
    with sa._txn(base) as conn:
        conn.execute("UPDATE pending SET created_at = 0")
    sa.measure(base, "u-one", "universe_files")
    assert sa.usage(base, A).used_bytes == before
    budget.settle()


def test_failed_launch_measurement_releases_its_reservation(base, monkeypatch):
    root = universe(base)

    def failed(_):
        raise OSError("test walk failure")

    monkeypatch.setattr(jd, "_jail_writable_bytes", failed)
    with pytest.raises(jd.DiskFloorRefused):
        launch(root)
    assert sa.usage(base, A).used_bytes == 0


def test_cancelled_known_size_write_releases_only_its_own_reservation(base):
    import asyncio

    root = universe(base)
    provider = launch(root)
    with pytest.raises(asyncio.CancelledError), sa.charged(
        base, account_id=A, store="ui_library", nbytes=49101,
    ):
        raise asyncio.CancelledError()
    assert sa.usage(base, A).used_bytes == provider.bound
    provider.settle()


def test_ledger_failure_keeps_existing_bounded_fallback(base, monkeypatch):
    root = universe(base)

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("test unavailable")

    monkeypatch.setattr(sa, "measure", unavailable)
    budget = launch(root)
    assert budget.bound == jd.GRACE_BYTES and budget.reservation is None
    assert "unavailable" in budget.notice
    budget.settle()


def test_fitted_admission_validates_owner_and_nonnegative_headroom(base):
    universe(base)
    universe(base, "u-bob", B)
    with pytest.raises(ValueError, match="not part of this account"):
        sa.reserve_fitted(base, account_id=A, scope_id="u-bob", store="universe_files", cap=GIB)
    with pytest.raises(ValueError, match=">= 0"):
        sa.reserve_fitted(
            base, account_id=A, scope_id="u-one", store="universe_files", cap=GIB, headroom=-1,
        )
