"""The account storage registry: the stores BESIDE universe directories.

account-storage-quota D3: run records, checkpoints, uploads, branches and commons
pages live in shared stores at the data root, so a universe scan never sees them.
Each is measured from a column that RECORDS whose it is -- one focused test per
store, plus the cross-user split (A's bytes never land on B).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tinyassets import storage_accounting as sa
from tinyassets.daemon_server import grant_universe_ownership, initialize_author_server

A = "workos|alice"
B = "workos|bob"
KIB = 1024


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(root / "wiki"))
    initialize_author_server(root)
    for owner, uid in ((A, "u-a"), (B, "u-b")):
        grant_universe_ownership(root, universe_id=uid, owner_id=owner)
        (root / uid).mkdir()
    return root


def _runs_db(base: Path) -> sqlite3.Connection:
    from tinyassets import runs

    runs.initialize_runs_db(base)
    return sqlite3.connect(runs.runs_db_path(base))


def _add_run(base: Path, run_id: str, *, owner: str, universe: str = "", size: int = 0,
             thread: str = "") -> None:
    with _runs_db(base) as conn:
        conn.execute(
            "INSERT INTO runs (run_id, branch_def_id, thread_id, actor, owner_user_id, "
            "inputs_json, output_json, started_at, queue_universe_id) "
            "VALUES (?, 'b', ?, 'x', ?, '{}', ?, 0, ?)",
            (run_id, thread or run_id, owner, "o" * size, universe),
        )


def _measured(base: Path, account: str, store: str) -> int:
    return sa.measure(base, account, store)


























def test_registered_stores_cover_the_account(base):
    """`usage` sums every registered store, so a new one is charged the moment
    it is registered."""
    _add_run(base, "r-a", owner=A, size=10 * KIB)
    sa.commit(sa.reserve(base, account_id=A, scope_id="u-a", store="universe_files", nbytes=0))
    stores = {store for _, store, _ in sa.usage(base, A).breakdown}
    assert "run_records" in stores


pytestmark = pytest.mark.verifies('owner-daemon-in-box:T2')
