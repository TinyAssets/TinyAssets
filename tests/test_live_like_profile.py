from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

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
