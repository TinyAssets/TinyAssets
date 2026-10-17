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


def test_run_records_are_charged_to_the_recorded_owner(base):
    _add_run(base, "r-a", owner=A, size=30 * KIB)
    _add_run(base, "r-b", owner=B, size=5 * KIB)
    assert _measured(base, A, "run_records") >= 30 * KIB
    assert _measured(base, B, "run_records") < 10 * KIB


def test_an_ownerless_run_is_charged_to_its_universes_owner(base):
    _add_run(base, "r-legacy", owner="", universe="u-a", size=20 * KIB)
    assert _measured(base, A, "run_records") >= 20 * KIB
    assert _measured(base, B, "run_records") == 0


def test_run_events_follow_their_run(base):
    _add_run(base, "r-a", owner=A)
    with _runs_db(base) as conn:
        conn.execute(
            "INSERT INTO run_events (run_id, step_index, node_id, status, started_at, "
            "detail_json) VALUES ('r-a', 0, 'n', 'ok', 0, ?)",
            ("e" * (25 * KIB),),
        )
    assert _measured(base, A, "run_records") >= 25 * KIB


def test_checkpoints_are_charged_through_the_runs_thread(base):
    from langgraph.checkpoint.sqlite import SqliteSaver

    _add_run(base, "r-a", owner=A, thread="t-a")
    _add_run(base, "r-b", owner=B, thread="t-b")
    conn = sqlite3.connect(base / ".langgraph_runs.db", check_same_thread=False)
    SqliteSaver(conn).setup()
    for thread, size in (("t-a", 40 * KIB), ("t-b", 1 * KIB)):
        conn.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, type, "
            "checkpoint, metadata) VALUES (?, '', ?, 'x', ?, ?)",
            (thread, thread, b"c" * size, b"{}"),
        )
    conn.commit()
    conn.close()

    assert _measured(base, A, "checkpoints") >= 40 * KIB
    assert _measured(base, B, "checkpoints") < 5 * KIB


def test_uploads_are_charged_to_their_owner_at_their_exact_size(base):
    from tinyassets.storage import run_files

    with _runs_db(base) as conn:
        run_files.ensure_schema(conn)
        conn.execute(
            "INSERT INTO run_file_operations (operation_id, owner_id, universe_id, "
            "request_sha256, max_bytes, physical_root_id, state) "
            "VALUES ('op', ?, 'u-a', 'h', 0, 'r', 'committed')",
            (A,),
        )
        for file_id, owner, size, state in (
            ("f1", A, 50 * KIB, "ready"), ("f2", A, 99 * KIB, "released"),
            ("f3", B, 7 * KIB, "ready"),
        ):
            conn.execute(
                "INSERT INTO run_file_objects (file_id, operation_id, owner_id, universe_id, "
                "storage_key, sha256, size_bytes, filename, media_type, state) "
                "VALUES (?, 'op', ?, 'u', ?, 's', ?, 'f', 'text/plain', ?)",
                (file_id, owner, file_id, size, state),
            )
    assert _measured(base, A, "uploads") == 50 * KIB  # released files are gone
    assert _measured(base, B, "uploads") == 7 * KIB


def test_branches_are_charged_to_author_and_publisher(base):
    from tinyassets.branch_versions import initialize_branch_versions_db
    from tinyassets.storage import _connect as author_connection

    with author_connection(base) as conn:
        conn.execute(
            "INSERT INTO branch_definitions (branch_def_id, name, author, graph_json, "
            "created_at, updated_at) VALUES ('bd', 'n', ?, ?, 0, 0)",
            (A, "g" * (15 * KIB)),
        )
    initialize_branch_versions_db(base)
    with _runs_db(base) as conn:
        conn.execute(
            "INSERT INTO branch_versions (branch_version_id, branch_def_id, content_hash, "
            "snapshot_json, publisher, published_at) VALUES ('bv', 'bd', 'h', ?, ?, 'now')",
            ("s" * (12 * KIB), A),
        )
    assert _measured(base, A, "branches") >= 27 * KIB
    assert _measured(base, B, "branches") == 0


def test_commons_pages_are_charged_to_their_last_writer(base):
    page = base / "wiki" / "pages" / "notes" / "p.md"
    page.parent.mkdir(parents=True)
    a_text = "a" * (8 * KIB)
    page.write_text(a_text, encoding="utf-8")
    sa.record_commons_writer(base, page, A, a_text)
    assert _measured(base, A, "commons_pages") == 8 * KIB

    b_text = "b" * (9 * KIB)
    page.write_text(b_text, encoding="utf-8")  # B edits it: B owns it now
    sa.record_commons_writer(base, page, B, b_text)
    assert _measured(base, A, "commons_pages") == 0
    assert _measured(base, B, "commons_pages") == 9 * KIB


def test_an_interleaved_write_never_bills_someone_who_did_not_write_it(base):
    """gpt-6-astra PR #4166: B writes, A writes, A records, B records. The page
    holds A's bytes; B's record names B's content, so B is NOT charged."""
    page = base / "wiki" / "pages" / "notes" / "race.md"
    page.parent.mkdir(parents=True)
    a_text, b_text = "a" * (8 * KIB), "b" * (8 * KIB)
    page.write_text(a_text, encoding="utf-8")  # A's write landed last
    sa.record_commons_writer(base, page, A, a_text)
    sa.record_commons_writer(base, page, B, b_text)  # B's record arrives late

    assert _measured(base, B, "commons_pages") == 0


def test_a_page_outside_the_wiki_or_without_a_writer_records_nothing(base):
    outside = base / "u-a" / "x.md"
    outside.write_text("x", encoding="utf-8")
    sa.record_commons_writer(base, outside, A, "x")
    sa.record_commons_writer(base, base / "wiki" / "p.md", "", "x")
    assert _measured(base, A, "commons_pages") == 0


def test_commons_log_lines_are_charged_to_their_writer(base):
    sa.record_commons_log(base, A, 3 * KIB)
    sa.record_commons_log(base, A, 2 * KIB)
    sa.record_commons_log(base, "", 50 * KIB)  # no actor: the platform's
    assert _measured(base, A, "commons_pages") == 5 * KIB
    assert _measured(base, B, "commons_pages") == 0


def test_automation_inputs_are_charged_to_their_owner(base):
    from tinyassets.automations import automations_db_path

    db = automations_db_path(base)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE automations (automation_id TEXT, owner_principal_id TEXT, "
        "name TEXT, cron_expr TEXT, inputs_json TEXT)"
    )
    conn.execute(
        "INSERT INTO automations VALUES ('a1', ?, 'n', '', ?)", (A, "i" * (20 * KIB)),
    )
    conn.commit()
    conn.close()
    assert _measured(base, A, "automations") >= 20 * KIB
    assert _measured(base, B, "automations") == 0


def test_the_wiki_write_records_its_writer(base, signed_in, monkeypatch):
    """The real write path, not the helper: `_wiki_write` records the actor."""
    from tinyassets.api import wiki as api_wiki

    # The configured wiki store is provisioned before an owner-local write.
    # A write cannot create an unadmitted top-level command center.
    (base / "wiki").mkdir()
    signed_in(A)
    out = api_wiki._wiki_write(
        category="notes", filename="from-a.md", content="# A\n" + "a" * (6 * KIB),
    )
    assert "error" not in out, out
    assert _measured(base, A, "commons_pages") >= 6 * KIB


def test_registered_stores_cover_the_account(base):
    """`usage` sums every registered store, so a new one is charged the moment
    it is registered."""
    _add_run(base, "r-a", owner=A, size=10 * KIB)
    sa.commit(sa.reserve(base, account_id=A, scope_id="u-a", store="universe_files", nbytes=0))
    stores = {store for _, store, _ in sa.usage(base, A).breakdown}
    assert "run_records" in stores
