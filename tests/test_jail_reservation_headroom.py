"""Ordinary writes and bounded provider recovery share an owner's quota."""

from pathlib import Path

import pytest

from tinyassets import jail_disk
from tinyassets import storage_accounting as sa
from tinyassets.daemon_server import grant_universe_ownership, initialize_author_server

A = "workos|alice"
MIB = 1024**2


@pytest.fixture
def base(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setenv("TINYASSETS_FREE_STORAGE_GIB", "2")
    initialize_author_server(root)
    return root


def universe(base: Path, uid: str) -> Path:
    grant_universe_ownership(base, universe_id=uid, owner_id=A)
    root = base / uid
    root.mkdir(exist_ok=True)
    return root


def test_overlapping_launches_leave_room_for_an_ordinary_ui_save(base):
    first_root = universe(base, "u-first")
    second_root = universe(base, "u-second")
    first = jail_disk.open_budget(first_root)
    second = jail_disk.open_budget(second_root)
    try:
        # Real 2 GiB quota, launch cap, ledger, measurement, and write gate.
        # Main used to reserve 1 GiB twice and refuse this 49,101-byte save.
        with sa.admitted(
            base, account_id=A, scope_id=first_root.name,
            store="universe_files", nbytes=49_101,
        ):
            (first_root / "ui.html").write_bytes(b"x" * 49_101)
        assert (first_root / "ui.html").stat().st_size == 49_101
        assert first.reservation.bytes + second.reservation.bytes == 2 * 1024**3 - 16 * MIB
    finally:
        second.settle()
        first.settle()


@pytest.mark.parametrize("remaining", [0, 1024, 16 * MIB, 16 * MIB + 1024, 32 * MIB])
def test_write_headroom_preserves_startup_before_provider_cleanup(base, monkeypatch, remaining):
    monkeypatch.setenv("TINYASSETS_FREE_STORAGE_GIB", str(64 * MIB / 1024**3))
    monkeypatch.setattr(jail_disk, "WALK_SECONDS", 0.0)
    root = universe(base, "u-recovery")
    retained = root / "retained.bin"
    with retained.open("wb") as handle:
        handle.truncate(64 * MIB - remaining)
    budget = jail_disk.open_budget(root)
    try:
        fitted = max(0, remaining - jail_disk.WRITE_HEADROOM_BYTES)
        assert budget.bound == max(fitted, jail_disk.GRACE_BYTES)
        if fitted:
            assert budget.reservation.bytes == fitted
        else:
            assert budget.reservation is None
            assert budget.notice
        (root / ".runtime").mkdir()
        (root / ".runtime" / "session.bin").write_bytes(b"x" * 1024)
        assert budget.breach() is None, "provider setup must reach cleanup"
        if remaining:
            with sa.admitted(
                base, account_id=A, scope_id=root.name, store="universe_files", nbytes=1024,
            ):
                (root / "ui.html").write_bytes(b"x" * 1024)
        retained.unlink()
        assert budget.breach() is None
        with (root / ".runtime" / "overflow.bin").open("wb") as handle:
            handle.truncate(budget.start_bytes + budget.bound + 1)
        assert budget.breach() == jail_disk.STORAGE_LIMIT
    finally:
        budget.settle()


def test_concurrent_launch_reservations_preserve_one_shared_write_allowance(base):
    from concurrent.futures import ThreadPoolExecutor

    roots = [universe(base, f"u-{index}") for index in range(6)]
    # Warm measurements; the race exercises atomic fit/insert, not initialization.
    for root in roots:
        jail_disk.open_budget(root).settle()
    with ThreadPoolExecutor(max_workers=len(roots)) as executor:
        budgets = list(executor.map(jail_disk.open_budget, roots))
    try:
        reserved = sum(b.reservation.bytes for b in budgets if b.reservation)
        assert reserved == 2 * 1024**3 - jail_disk.WRITE_HEADROOM_BYTES
        assert all(b.bound >= jail_disk.GRACE_BYTES for b in budgets)
        # Ordinary writes may use every byte protected from speculation.
        reservation = sa.reserve(
            base, account_id=A, scope_id=roots[0].name, store="universe_files",
            nbytes=jail_disk.WRITE_HEADROOM_BYTES,
        )
        sa.release(reservation)
    finally:
        for budget in budgets:
            budget.settle()
