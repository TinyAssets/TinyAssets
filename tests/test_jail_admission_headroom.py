"""Ordinary writes share account storage with active jails; no launch holds it.

Sparse files model retained logical bytes without allocating GiB on the test
host. These tests do not claim headroom survives subsequent jail growth or
remeasurement, or that the polling budget is a hard filesystem quota.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tinyassets import custom_agents
from tinyassets import jail_disk as jd
from tinyassets import storage_accounting as sa
from tinyassets.daemon_server import grant_universe_ownership, initialize_author_server

OWNER = "workos|alice"
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


def _universe(base, uid, retained=0, owner=OWNER):
    grant_universe_ownership(base, universe_id=uid, owner_id=owner)
    root = base / uid
    root.mkdir()
    with (root / "retained.bin").open("wb") as out:
        out.truncate(retained)
    return root


def _launch(root):
    return jd.open_budget(root, min_free_bytes=0, min_free_inodes=0)


@pytest.mark.parametrize("retained", [0, 100 * MIB])
@pytest.mark.parametrize("same_root", [False, True])
def test_two_new_launches_leave_room_for_a_real_ui_save(base, retained, same_root):
    root = _universe(base, "u-one", retained)
    other = root if same_root else _universe(base, "u-two")
    budgets = []
    try:
        budgets.append(_launch(root))
        budgets.append(_launch(other))
        library = [{"ui_id": "example", "html": "x" * 48 * 1024}]
        saved = custom_agents.save_app_ui(
            base, owner_user_id=OWNER, universe_id="u-one", expected_revision=0,
            changes={"ui_library": library},
        )
        assert saved["ui_library"] == library
        assert sa.usage(base, OWNER).reserved_bytes == 0
        assert all(b.bound == 2 * GIB - retained for b in budgets)
    finally:
        for budget in budgets:
            budget.settle()


def test_concurrent_fits_leave_headroom_in_the_same_admission_transaction(base):
    roots = [_universe(base, "u-one"), _universe(base, "u-two")]
    # Establish measurements before the concurrent admission boundary.
    for scope, store in sa._scopes(base, OWNER):
        sa.measure(base, scope, store)
    ready = Barrier(2)

    def reserve(root):
        ready.wait(timeout=10)
        return sa.reserve_fitted(
            base, account_id=OWNER, scope_id=root.name, store="universe_files",
            cap=GIB, minimum=1, headroom=16 * MIB,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, roots))
    try:
        assert sum(bound for _, bound in results) == 2 * GIB - 16 * MIB
        assert sa.usage(base, OWNER).used_bytes == 2 * GIB - 16 * MIB
    finally:
        for reservation, _ in results:
            sa.release(reservation)


def test_full_account_keeps_grace_for_startup_and_cleanup(base, monkeypatch):
    root = _universe(base, "u-one", 2 * GIB)
    budget = _launch(root)
    try:
        assert budget.bound == 0
        assert sa.usage(base, OWNER).reserved_bytes == 0
        (root / "session.bin").write_bytes(b"x" * 4096)
        monkeypatch.setattr(jd, "WALK_SECONDS", 0)
        assert budget.breach(force=True) == jd.STORAGE_LIMIT
        (root / "retained.bin").unlink()
        assert budget.breach(force=True) is None
    finally:
        budget.settle()


def test_another_owners_reservations_do_not_take_this_owners_headroom(base):
    root = _universe(base, "u-one")
    other = _universe(base, "u-other", owner="workos|bob")
    first, second = _launch(root), _launch(other)
    try:
        assert first.bound == second.bound == 2 * GIB
        assert first.account != second.account
    finally:
        first.settle()
        second.settle()
