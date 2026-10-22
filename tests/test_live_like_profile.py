from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.live_like import fixture, shape_profile
from tinyassets.box_layout_migration import census


def test_redaction_drops_emails_ids_and_user_paths(tmp_path: Path) -> None:
    owner = "pat@example.com"
    center = "private-center-12345"
    identity = tmp_path / ".broker/state/owner-identities.db"
    identity.parent.mkdir(parents=True)
    with sqlite3.connect(identity) as db:
        db.executescript(
            "CREATE TABLE owner_identities (principal TEXT, machine_id INTEGER);"
            "CREATE TABLE center_admissions (generation INTEGER, event TEXT, "
            "principal TEXT, center TEXT, machine INTEGER);"
        )
        db.execute("INSERT INTO owner_identities VALUES (?,?)", (owner, 300001))
        db.execute("INSERT INTO center_admissions VALUES (1,'admit',?,?,300001)", (owner, center))
    private = tmp_path / center / "my-secret-id-98765" / "email-pat@example.com.txt"
    private.parent.mkdir(parents=True)
    private.write_text("pat@example.com\nsecret", encoding="utf-8")
    result = shape_profile.profile(tmp_path)
    shape_profile.check_redaction(result)
    payload = json.dumps(result)
    assert owner not in payload
    assert center not in payload
    assert "my-secret-id-98765" not in payload
    assert "email-pat" not in payload
    assert "300001" not in payload


def test_realistic_variable_segments_normalize(tmp_path: Path) -> None:
    owner = "user_privateowner42"
    center = "u-privatecenter42"
    identity = tmp_path / ".broker/state/owner-identities.db"
    identity.parent.mkdir(parents=True)
    with sqlite3.connect(identity) as db:
        db.executescript(
            "CREATE TABLE owner_identities (principal TEXT, machine_id INTEGER);"
            "CREATE TABLE center_admissions (generation INTEGER, event TEXT, "
            "principal TEXT, center TEXT, machine INTEGER);"
        )
        db.execute("INSERT INTO owner_identities VALUES (?,?)", (owner, 300001))
        db.execute("INSERT INTO center_admissions VALUES (1,'admit',?,?,300001)", (owner, center))
    account_hash = "a1" * 32
    account = tmp_path / ".broker/accounts" / account_hash / "policy.db"
    account.parent.mkdir(parents=True)
    with sqlite3.connect(account) as db:
        db.execute(
            "CREATE TABLE policy "
            "(singleton INTEGER, generation INTEGER, digest TEXT, document TEXT)"
        )
    names = [
        f"{center}/550e8400-e29b-41d4-a716-446655440000/1730000000/12345/note.txt",
        f"{account_hash}/secret.json",
        f".owner_tree/{owner}/tree.json",
        "_removed_20261021/secret.txt",
        "_backup_subject_20261021/secret.txt",
        "scratch/private/scratch.txt",
        f".platform/owners/300001/agent-home/workspace/{center}/work/private-note.md",
        f".platform/owners/300001/agent-home/workspace/{center}/policy.db",
    ]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic", encoding="utf-8")
    result = shape_profile.profile(tmp_path)
    shape_profile.check_redaction(result)
    patterns = {item["pattern"] for item in result["files"]}
    assert any(pattern.endswith("/agent-home/<id>/<id>/<id>/<file>.md") for pattern in patterns)
    assert any(pattern.endswith("/agent-home/<id>/<id>/<file>.db") for pattern in patterns)
    payload = json.dumps(result)
    private_values = (
        owner, center, account_hash, "550e8400", "1730000000", "12345",
        "private-note", "_removed_20261021", "_backup_subject_20261021",
    )
    for value in private_values:
        assert value not in payload


def test_unknown_path_vocabulary_reports_class_only(capsys: pytest.CaptureFixture[str]) -> None:
    document = json.loads(shape_profile.DEFAULT.read_text(encoding="utf-8"))
    item = next(item for item in document["files"] if item["pattern"] == ".layout.json")
    item["pattern"] = "private-diary-amber.txt"
    with pytest.raises(ValueError, match="unapproved path vocabulary"):
        shape_profile.check_redaction(document)
    err = capsys.readouterr().err
    assert err == "path class=broker segment=0 chars=slug length=17-32 store=none table=none\n"
    assert "private-diary" not in err


def test_fixture_build_covers_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "posix":
        pytest.skip("special inodes need POSIX")
    profile = json.loads(shape_profile.DEFAULT.read_text(encoding="utf-8"))
    shape_profile.check_redaction(profile)
    assert fixture.fixture_coverage(profile) == []
    monkeypatch.setattr(os, "chown", lambda *args, **kwargs: None)
    fixture.build(tmp_path, profile)
    for name, store in profile["stores"].items():
        path = fixture._materialize_pattern(tmp_path, name)
        assert path.is_file(), name
        with sqlite3.connect(path) as db:
            for table, spec in store["tables"].items():
                columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
                assert set(spec["columns"]) <= columns, (name, table)
    for item in profile["files"]:
        if item["class"] != "database_files":
            matcher = shape_profile._pattern_regex(item["pattern"])
            assert any(
                matcher.fullmatch(path.relative_to(tmp_path).as_posix())
                for path in tmp_path.rglob("*")
            ), item["pattern"]
    fresh = shape_profile.profile(tmp_path)
    shape_profile.check_redaction(fresh)
    gaps = shape_profile.diff(profile, fresh)
    assert gaps == [], "\n".join(gaps)
    report = census(tmp_path)
    assert report["ready"], report["refusals"]


def test_new_create_table_fails_drift(tmp_path: Path) -> None:
    source = tmp_path / "tinyassets"
    source.mkdir()
    (source / "new_store.py").write_text(
        'PATH = ".newly_added.db"\n'
        'DDL = "CREATE TABLE IF NOT EXISTS newly_added_table (id TEXT)"\n',
        encoding="utf-8",
    )
    committed = json.loads(shape_profile.DEFAULT.read_text(encoding="utf-8"))
    fresh = {
        **committed,
        "code_tables": sorted(shape_profile.source_schema(source)),
        "code_stores": shape_profile.source_stores(source),
    }
    errors = shape_profile.diff(committed, fresh)
    assert any("new CREATE TABLE missing from profile: newly_added_table" in e for e in errors)
    assert any("new SQLite store missing from profile: .newly_added.db" in e for e in errors)


def test_profile_reports_all_drift_without_leaking_it(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / ".automations.db") as db:
        db.executescript(
            "CREATE TABLE universe_leases (universe_id TEXT, holder TEXT, "
            "expires_at TEXT, run_id TEXT, secret_extra TEXT, another_secret INTEGER);"
            "CREATE TABLE private_surprise (private_token TEXT);"
        )
    with sqlite3.connect(tmp_path / ".tinyassets.db") as db:
        db.executescript("CREATE TABLE second_surprise (private_key TEXT);")
    output = tmp_path.parent / "redacted-profile.json"
    run = subprocess.run(
        [
            sys.executable,
            str(shape_profile.REPO / "scripts/live_like/shape_profile.py"),
            "profile",
            "--data",
            str(tmp_path),
            "--out",
            str(output),
            "--check-redaction",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode != 0
    assert run.stdout == ""
    assert run.stderr.splitlines() == [
        ".automations.db/private_surprise: 1 unreviewed columns",
        ".automations.db/private_surprise: unreviewed table",
        ".automations.db/universe_leases: 2 unreviewed columns",
        ".tinyassets.db/second_surprise: 1 unreviewed columns",
        ".tinyassets.db/second_surprise: unreviewed table",
    ]
    payload = output.read_text(encoding="utf-8")
    for secret in ("private_surprise", "second_surprise", "secret_extra", "another_secret"):
        assert secret not in payload
    shape_profile.check_redaction(json.loads(payload))


def test_universe_leases_migration_is_reviewed(tmp_path: Path) -> None:
    assert shape_profile.source_schema()["universe_leases"]["run_id"] == "TEXT"
    with sqlite3.connect(tmp_path / ".automations.db") as db:
        db.executescript(
            "CREATE TABLE universe_leases (universe_id TEXT, holder TEXT, "
            "expires_at TEXT, run_id TEXT);"
        )
    result = shape_profile.profile(tmp_path)
    assert "run_id" in result["stores"][".automations.db"]["tables"]["universe_leases"]["columns"]
    shape_profile.check_redaction(result)


def test_fresh_code_owned_store_columns_are_in_vocabulary(tmp_path: Path) -> None:
    """Exercise real initializers, including migrations that add columns later."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    from tinyassets import engine_admissions, runs, workspace_pool
    from tinyassets.connection_oauth import pkce
    from tinyassets.storage.agent_request_usage import UsageStore
    from tinyassets.storage.agent_turn_journal import ensure_schema as turns_schema
    from tinyassets.storage.outbound_connections import ConnectionLedger

    db_paths: list[Path] = []
    common = tmp_path / "common.db"
    with closing(sqlite3.connect(common)) as db:
        workspace_pool.ensure_schema(db)
        engine_admissions._ensure_schema(db)
        pkce._ensure_schema(db)
        db.commit()
        turns_schema(db)
    db_paths.append(common)

    db_paths.append(runs.initialize_runs_db(tmp_path))
    outbound = tmp_path / "outbound.db"
    ConnectionLedger(outbound)
    db_paths.append(outbound)
    usage = tmp_path / "usage.db"
    store = UsageStore(tmp_path, broker_ledger=SimpleNamespace(_db_path=usage))
    with store._connection():
        pass
    db_paths.append(usage)
    checkpoint = tmp_path / "checkpoint.db"
    with closing(sqlite3.connect(checkpoint)) as db:
        SqliteSaver(db).setup()
    db_paths.append(checkpoint)

    vocabulary = shape_profile.column_vocabulary()
    for path in db_paths:
        with closing(sqlite3.connect(path)) as db:
            tables = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            for (table,) in tables:
                actual = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
                assert table in vocabulary, (path.name, table)
                assert actual <= set(vocabulary[table]), (
                    path.name, table, sorted(actual - set(vocabulary[table]))
                )


def test_literal_schema_builders_and_migrations_are_extracted(tmp_path: Path) -> None:
    source = tmp_path / "tinyassets"
    source.mkdir()
    (source / "store.py").write_text(
        'DDL = "CREATE TABLE IF NOT EXISTS built (" "id TEXT, \\"key\\" INTEGER)"\n'
        'def ensure(conn):\n'
        '    conn.execute(DDL)\n'
        '    for column, kind in (("after", "TEXT"), ("count", "INTEGER")):\n'
        '        conn.execute(f"ALTER TABLE built ADD COLUMN {column} {kind}")\n'
        '    _ensure_column(conn, "built", "helper", "REAL")\n',
        encoding="utf-8",
    )
    assert shape_profile.source_schema(source)["built"] == {
        "id": "TEXT",
        "key": "INTEGER",
        "after": "TEXT",
        "count": "INTEGER",
        "helper": "REAL",
    }
