"""Unassigned namespaces remain outside all owner projections and exports."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest

from tests.test_okf_canonical_bundle import bundle  # noqa: F401
from tinyassets.okf.bundle import Entry, InvalidBundle
from tinyassets.okf.migration import commit_migration
from tinyassets.okf.namespace_census import (
    NamespaceSource,
    ReviewedBinding,
    census_sources,
    prepare_reviewed_migration,
    retain_census,
)
from tinyassets.okf.records import SourceRecords
from tinyassets.okf.restore import restore_copy


def fixture_source(tmp_path, *, empty=False):
    path = tmp_path / "projects.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE notes(project_id TEXT, id TEXT PRIMARY KEY, "
            "content TEXT, updated_by TEXT)"
        )
        if not empty:
            db.executemany(
                "INSERT INTO notes VALUES (?,?,?,?)",
                [
                    ("opaque-a", "a1", "A prose", "writer-b"),
                    ("opaque-b", "b1", "B prose", "writer-a"),
                    (None, "null1", "Unattributed prose", "owner-a"),
                ],
            )
    return NamespaceSource(
        path, "projects", hashlib.sha256(path.read_bytes()).hexdigest(), {"notes": "project_id"}, {}
    )


def binding(source, namespace="opaque-a", **kwargs):
    return replace(
        ReviewedBinding(
            "projects",
            "notes",
            namespace,
            "owner-a",
            "a",
            source.expected_sha256,
            "reviewer",
            "review/123",
            "f" * 64,
        ),
        **kwargs,
    )


def authority(owner, center):
    return {"a": "owner-a", "b": "owner-b"}.get(center) == owner


def test_partial_census_retains_ambiguous_rows_and_projects_only_reviewed_namespace(
    bundle, tmp_path  # noqa: F811
):  # noqa: F811
    source = fixture_source(tmp_path)
    census = census_sources([source], [binding(source)], verify_authority=authority)
    assert census.report["unresolved_rows"] == 2
    assert not census.report["ownership_complete"]
    assert not census.report["source_deletion_permitted"]
    with pytest.raises(InvalidBundle, match="custody"):
        prepare_reviewed_migration(
            bundle, census, operation_id="initial", verify_authority=authority
        )
    custody = tmp_path / "operator-custody"
    custody.mkdir(mode=0o700)
    receipt = retain_census(census, custody)
    doc = json.loads(__import__("pathlib").Path(receipt["path"]).read_bytes())
    retained = [Entry.decode(base64.b64decode(raw)) for raw in doc["unresolved"]]
    assert {e.body for e in retained} == {"B prose", "Unattributed prose"}
    assert __import__("pathlib").Path(receipt["path"]).stat().st_mode & 0o777 == 0o600
    plan = prepare_reviewed_migration(
        bundle, census, operation_id="initial", verify_authority=authority, custody_receipt=receipt
    )
    commit_migration(bundle, plan)
    assert [e.body for e in bundle.snapshot().entries.values()] == ["A prose"]
    assert bundle.search("Unattributed") == []
    assert (
        SourceRecords(bundle, "projects")
        .snapshot()
        .manifest["migration"][0]["tables"]["notes"]["reviewed_bindings"][0]["review_ref"]
        == "review/123"
    )
    target = tmp_path / "restored.db"
    restore_copy(bundle.snapshot(), plan.sources[0], target)
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT count(*) FROM notes").fetchone() == (3,)
    assert hashlib.sha256(source.path.read_bytes()).hexdigest() == source.expected_sha256


def test_unbound_writer_and_null_scopes_never_infer_owner_and_zero_rows_clear_blocker(tmp_path):
    source = fixture_source(tmp_path)
    census = census_sources([source], [], verify_authority=authority)
    assert census.report["unresolved_rows"] == 3
    assert all(
        n["binding"] is None for n in census.report["sources"][0]["tables"]["notes"]["namespaces"]
    )
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    empty = census_sources([fixture_source(empty_root, empty=True)], [], verify_authority=authority)
    assert empty.report["ownership_complete"] is True
    assert empty.report["sources"][0]["tables"]["notes"]["rows"] == 0
    assert empty.report["source_deletion_permitted"] is False


def test_binding_provenance_conflicts_current_admission_and_changed_backup_refuse(bundle, tmp_path):  # noqa: F811
    source = fixture_source(tmp_path)
    good = binding(source)
    for bad in [
        replace(good, review_ref=""),
        replace(good, snapshot_sha256="0" * 64),
        replace(good, owner="owner-b"),
        replace(good, evidence_sha256="missing"),
    ]:
        with pytest.raises(InvalidBundle):
            census_sources([source], [bad], verify_authority=authority)
    with pytest.raises(InvalidBundle, match="duplicate"):
        census_sources([source], [good, good], verify_authority=authority)
    census = census_sources([source], [good], verify_authority=authority)
    # A new deletion/admission fence rejects a plan made from the older backup.
    with pytest.raises(InvalidBundle, match="admission"):
        prepare_reviewed_migration(
            bundle, census, operation_id="initial", verify_authority=lambda owner, center: False
        )
    with sqlite3.connect(source.path) as db:
        db.execute("UPDATE notes SET content='changed'")
    with pytest.raises(InvalidBundle, match="checksum"):
        census_sources([source], [good], verify_authority=authority)
    assert not bundle.snapshot().entries


def test_custody_refuses_public_directory_symlink_and_corrupt_receipt(bundle, tmp_path):  # noqa: F811
    source = fixture_source(tmp_path)
    census = census_sources([source], [binding(source)], verify_authority=authority)
    custody = tmp_path / "custody"
    custody.mkdir(mode=0o755)
    custody.chmod(0o755)
    with pytest.raises(InvalidBundle, match="0700"):
        retain_census(census, custody)
    custody.chmod(0o700)
    link = tmp_path / "link"
    link.symlink_to(custody)
    with pytest.raises(OSError):
        retain_census(census, link)
    receipt = retain_census(census, custody)
    with pytest.raises(InvalidBundle, match="differs"):
        prepare_reviewed_migration(
            bundle,
            census,
            operation_id="initial",
            verify_authority=authority,
            custody_receipt={**receipt, "sha256": "0" * 64},
        )
    assert not bundle.snapshot().entries


def test_sql_collation_cannot_broaden_reviewed_namespace(bundle, tmp_path):  # noqa: F811
    source = fixture_source(tmp_path, empty=True)
    with sqlite3.connect(source.path) as db:
        db.execute("DROP TABLE notes")
        db.execute(
            "CREATE TABLE notes(project_id TEXT COLLATE NOCASE, id TEXT PRIMARY KEY, content TEXT)"
        )
        db.executemany(
            "INSERT INTO notes VALUES (?,?,?)",
            [("opaque-a", "a", "Reviewed"), ("OPAQUE-A", "b", "Unassigned peer")],
        )
    source = replace(source, expected_sha256=hashlib.sha256(source.path.read_bytes()).hexdigest())
    census = census_sources([source], [binding(source)], verify_authority=authority)
    assert census.report["unresolved_rows"] == 1
    custody = tmp_path / "custody"
    custody.mkdir(mode=0o700)
    receipt = retain_census(census, custody)
    with pytest.raises(InvalidBundle, match="collation"):
        prepare_reviewed_migration(
            bundle,
            census,
            operation_id="initial",
            verify_authority=authority,
            custody_receipt=receipt,
        )
    assert not bundle.snapshot().entries
