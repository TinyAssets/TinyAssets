"""An existing pending-requests database must survive a schema addition.

``CREATE TABLE IF NOT EXISTS`` leaves an older table exactly as it was, so a
column added to ``_SCHEMA`` never reaches a database that already exists — and
every live universe has one.

On 2026-08-28 that took the request rail's front door down in production. PR
#2636 added ``decision`` and ``answer_json`` to ``request_suppressions`` with no
migration, so on the founder's own universe every ``create_request`` raised
``sqlite3.OperationalError: no such column: decision``. The catch-all in
``create_request`` swallowed it and returned ``None``, which the API layer turned
into the generic ``request_storage_unavailable`` — so the agent was told only
that storage was unavailable, tried twice, and could not raise a single request.
Nothing in the message named the column, the table, or the migration.

These tests build a database with the PRE-#2636 schema and prove the current
code repairs it in place.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from tinyassets.storage import pending_requests as pr

# The request_suppressions table exactly as it shipped before #2636.
_OLD_SUPPRESSIONS = """
CREATE TABLE IF NOT EXISTS request_suppressions (
    dedupe_key  TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    feedback    TEXT,
    created_at  REAL NOT NULL
);
"""


@pytest.fixture()
def legacy_universe(tmp_path: Path) -> Path:
    """A universe whose database predates the `decision` column."""
    udir = tmp_path / "u-legacy"
    udir.mkdir()
    conn = sqlite3.connect(str(udir / pr._DB_NAME))
    # Build the CURRENT schema, then put back the one table's older shape. Doing
    # it by dropping and recreating keeps the other tables honest: an earlier
    # version of this fixture edited the schema text and stripped `answer_json`
    # from `pending_requests` too, where that column is original — which failed
    # the test for a reason that had nothing to do with the migration.
    conn.executescript(pr._SCHEMA)
    conn.executescript("DROP TABLE request_suppressions;")
    conn.executescript(_OLD_SUPPRESSIONS)
    conn.execute(
        "INSERT INTO request_suppressions (dedupe_key, kind, title, feedback, "
        "created_at) VALUES (?,?,?,?,?)",
        ("legacy-key", "API", "an older mute", "no thanks", time.time()),
    )
    conn.commit()
    conn.close()
    return udir


def _columns(udir: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(udir / pr._DB_NAME))
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_the_legacy_fixture_really_is_missing_the_column(legacy_universe: Path):
    """Guard the guard: if the fixture were already current, nothing is tested."""
    assert "decision" not in _columns(legacy_universe, "request_suppressions")


def test_opening_an_older_database_adds_the_missing_columns(legacy_universe: Path):
    with pr._db(legacy_universe):
        pass
    columns = _columns(legacy_universe, "request_suppressions")
    assert "decision" in columns
    assert "answer_json" in columns


def test_an_older_database_can_still_raise_a_request(legacy_universe: Path):
    """The live symptom: every ask failed with request_storage_unavailable."""
    row = pr.create_request(
        legacy_universe,
        kind="API",
        title="widen the github grant",
        body="one more endpoint",
        fields=[],
        action={"type": "answer"},
        dedupe_key="fresh-key",
    )
    assert row is not None, "create_request still fails on a pre-existing database"
    assert not row.get("error"), row
    assert row["status"] == "pending"


def test_a_mute_recorded_before_the_column_existed_reads_as_declined(
    legacy_universe: Path,
):
    """Existing rows must survive the migration with a sane value.

    'declined' is the right default: the column was added because storing only
    the silence lost the answer, and a mute recorded before there was anything
    to store cannot be evidence of a standing yes.
    """
    settled = pr.create_request(
        legacy_universe,
        kind="API",
        title="an older mute",
        body="",
        fields=[],
        action={"type": "answer"},
        dedupe_key="legacy-key",
    )
    assert settled["settled"] is True
    assert settled["decision"] == "declined"
    assert settled["feedback"] == "no thanks"


def test_the_migration_is_idempotent(legacy_universe: Path):
    for _ in range(3):
        with pr._db(legacy_universe):
            pass
    columns = _columns(legacy_universe, "request_suppressions")
    assert sorted(c for c in columns if c in {"decision", "answer_json"}) == [
        "answer_json",
        "decision",
    ]


def test_every_schema_column_addition_is_registered():
    """A column in _SCHEMA that is not in _ADDED_COLUMNS repeats the outage.

    This is the tripwire, not the migration: the migration only runs for columns
    somebody remembered to list. `request_suppressions` is pinned because it is
    the table that broke; the assertion fails loudly if it grows again.
    """
    body = pr._SCHEMA.split("CREATE TABLE IF NOT EXISTS request_suppressions", 1)[1]
    body = body.split(");", 1)[0]
    declared = {
        line.strip().split()[0]
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith(("(", "--"))
    }
    declared.discard("")
    original = {"dedupe_key", "kind", "title", "feedback", "created_at"}
    registered = {c for t, c, _ in pr._ADDED_COLUMNS if t == "request_suppressions"}
    missing = declared - original - registered
    assert not missing, (
        f"columns {sorted(missing)} were added to request_suppressions without a "
        "row in _ADDED_COLUMNS — an existing database will never get them, which "
        "is exactly the 2026-08-28 outage"
    )


def test_a_storage_fault_reports_why(tmp_path: Path, monkeypatch):
    """The generic error is what made the outage take an hour to name.

    `create_request` swallowed the exception and returned None, which the API
    layer rendered as a bare `request_storage_unavailable`. The agent retried the
    identical call, failed identically, and stopped — while the one-line cause
    sat in a container log nobody was tailing.
    """
    udir = tmp_path / "u-broken"
    udir.mkdir()

    def boom(_universe_dir):
        raise sqlite3.OperationalError("no such column: decision")

    monkeypatch.setattr(pr, "_db", boom)
    out = pr.create_request(
        udir, kind="API", title="t", body="", fields=[],
        action={"type": "answer"}, dedupe_key="k",
    )
    assert out is not None
    assert out["error"] == "request_storage_unavailable"
    assert "no such column: decision" in out["detail"], (
        "the reason was dropped again; a schema fault is not sensitive"
    )


def test_each_agent_asks_and_is_muted_on_its_own(tmp_path: Path):
    """Harness §4.18: an agent's dedupe and "don't ask again" are its own; the
    seeded agent's keys are unchanged, so nothing it was muted on returns."""
    universe = tmp_path / "u-alpha"
    universe.mkdir()

    def ask(agent):
        return pr.create_request(universe, kind="API", title="connect github", body="b",
                                 fields=[], action={"type": "answer"},
                                 dedupe_key="github", agent=agent)

    main, researcher = ask("main"), ask("researcher")
    assert main["created"] and researcher["created"]
    assert main["request_id"] != researcher["request_id"]
    assert main["dedupe_key"] == "github"
    assert researcher["dedupe_key"] == "agent:researcher:github"
    assert researcher.get("agent") == "researcher" and main.get("agent") == "main"
    assert ask("researcher")["created"] is False, "same agent, same ask: one tab"


def test_a_non_main_agents_request_still_reproduces_what_was_shown(tmp_path: Path):
    """The execute pin re-derives the key from the row; an agent's prefix must
    not make its own request refuse to run."""
    from tinyassets.api.pending_requests import displayed_row_matches

    universe = tmp_path / "u-alpha"
    universe.mkdir()
    import json as _json
    key = _json.dumps(["API", "t", "b", [], {"type": "answer"}], sort_keys=True,
                      separators=(",", ":"))
    row = pr.create_request(universe, kind="API", title="t", body="b", fields=[],
                            action={"type": "answer"}, dedupe_key=key, agent="researcher")
    assert displayed_row_matches(row)
    assert not displayed_row_matches({**row, "agent": "main"})
