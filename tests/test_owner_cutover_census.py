"""The cutover audit refuses lost ownership without changing its input tree."""

import hashlib
import json
import sqlite3
import subprocess
import sys

import pytest

from tinyassets.box_layout_migration import census


def _sql(path, statements):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(statements)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "owner-split"
    root.mkdir()
    (root / ".layout.json").write_text(json.dumps({
        "layout": 2, "split": "owner-split", "state": "stable",
    }))
    (root / ".layout.lock").touch()
    for center in ("a1", "a2", "b1", "b2"):
        (root / center).mkdir()
        (root / center / "notes.txt").write_text(f"private history for {center}")
    _sql(root / ".broker/state/owner-identities.db", """
        CREATE TABLE owner_identities (principal TEXT PRIMARY KEY, machine_id INTEGER);
        INSERT INTO owner_identities VALUES ('alice', 300001), ('bob', 300002);
        CREATE TABLE center_admissions (generation INTEGER, event TEXT, principal TEXT,
                                        center TEXT, machine INTEGER);
        INSERT INTO center_admissions VALUES
            (1, 'admit', 'alice', 'a1', 300001), (2, 'admit', 'alice', 'a2', 300001),
            (3, 'admit', 'bob', 'b1', 300002), (4, 'admit', 'bob', 'b2', 300002);
    """)
    _sql(root / ".tinyassets.db", """
        CREATE TABLE universe_owner (universe_id TEXT, owner_id TEXT);
        INSERT INTO universe_owner VALUES ('a1','alice'),('a2','alice'),('b1','bob'),('b2','bob');
        CREATE TABLE agent_turns (owner_user_id TEXT, universe_id TEXT, turn_id TEXT);
        INSERT INTO agent_turns VALUES ('alice','a1','1'),('alice','a2','2'),
                                      ('bob','b1','3'),('bob','b2','4');
        CREATE TABLE branch_heads (branch_id TEXT, snapshot_id TEXT);
        CREATE TABLE branches (branch_id TEXT, universe_id TEXT);
        INSERT INTO branches VALUES ('branch-a','a2'), ('branch-b','b2');
        INSERT INTO branch_heads VALUES ('branch-a','snapshot-a'), ('branch-b','snapshot-b');
        CREATE TABLE webhook_hooks (token_hash TEXT, universe_id TEXT, owner_principal_id TEXT);
        INSERT INTO webhook_hooks VALUES ('hash-a','a1','alice'), ('hash-b','b1','bob');
        CREATE TABLE llm_credential_deposit_owners (universe_id TEXT, owner_user_id TEXT);
        INSERT INTO llm_credential_deposit_owners VALUES ('a1','alice'),('b1','bob');
    """)
    _sql(root / "a2/.subscription_state.db", """
        CREATE TABLE subscription_meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO subscription_meta VALUES ('tier','paid'), ('tier_event_at','42');
    """)
    _sql(root / "outbound.db", """
        CREATE TABLE outbound_connections (destination TEXT, owner_user_id TEXT);
        INSERT INTO outbound_connections VALUES ('https://service.invalid/a', 'alice'),
                                                ('https://service.invalid/b', 'bob');
    """)
    for center, suffix in (("a1", "a"), ("b1", "b")):
        (root / center / ".credential-vault.json").write_text(json.dumps({
            "schema_version": 1, "credentials": [
                {"credential_type": "http", "destination": f"https://service.invalid/{suffix}",
                 "token": "secret-must-not-appear"},
                {"credential_type": "llm_subscription", "token": "retired-cli-secret"},
            ],
        }))
    return root


def _fingerprint(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def test_two_owner_census_preserves_totals_and_source_bytes(source):
    before = _fingerprint(source)
    report = census(source)
    assert report["ready"], report["refusals"]
    assert report["owners"] == 2
    assert report["admitted_centers"] == 4
    assert report["tables"][".tinyassets.db/agent_turns"]["per_owner"] == {
        "account-0001": 2, "account-0002": 2,
    }
    for table in report["tables"].values():
        if table["class"] == "owner":
            assert sum(table["per_owner"].values()) + table["published"] == table["total"]
    assert report["plans"] == {
        "account-0001": {"tier": "paid", "paid_sources": 1},
        "account-0002": {"tier": "free", "paid_sources": 0},
    }
    assert list(report["credentials"].values()) == [
        {"dropped_cli": 1, "carried_http": 1}, {"dropped_cli": 1, "carried_http": 1},
    ]
    assert _fingerprint(source) == before
    serialized = json.dumps(report)
    for private in ("alice", "bob", "secret-must-not-appear", "retired-cli-secret"):
        assert private not in serialized


@pytest.mark.parametrize("mutation,reason", [
    ("unknown_table", "unknown_table"),
    ("unknown_database", "unknown_table"),
    ("unknown_owner", "unattributed_owner_rows"),
    ("conflicting_owner", "unattributed_owner_rows"),
    ("ambiguous_parent", "unattributed_owner_rows"),
    ("unknown_file", "unattributed_files"),
    ("paid_orphan", "unattributed_paid_tier"),
    ("http_orphan", "http_slot_without_unique_owner_connection"),
])
def test_census_refuses_without_guessing_or_mutating(source, mutation, reason):
    if mutation == "unknown_table":
        _sql(source / ".tinyassets.db", "CREATE TABLE newly_added (owner_user_id TEXT)")
    elif mutation == "unknown_database":
        _sql(source / ".unknown-store", "CREATE TABLE agent_turns (owner_user_id TEXT)")
    elif mutation in {"unknown_owner", "conflicting_owner"}:
        owner = "missing" if mutation == "unknown_owner" else "bob"
        _sql(source / ".tinyassets.db",
             f"INSERT INTO agent_turns VALUES ('{owner}','a1','bad')")
    elif mutation == "ambiguous_parent":
        _sql(source / ".tinyassets.db", "INSERT INTO branches VALUES ('branch-a','b2')")
    elif mutation == "unknown_file":
        (source / ".unrecognized").write_text("private orphan")
    elif mutation == "paid_orphan":
        _sql(source / "orphan/.subscription_state.db", """
            CREATE TABLE subscription_meta (key TEXT, value TEXT);
            INSERT INTO subscription_meta VALUES ('tier','paid');
        """)
    elif mutation == "http_orphan":
        _sql(source / "outbound.db", "DELETE FROM outbound_connections")
    before = _fingerprint(source)
    report = census(source)
    assert not report["ready"]
    assert report["refusals"][reason] > 0
    assert _fingerprint(source) == before


def test_dry_run_never_enters_apply_even_when_apply_is_requested(source):
    before = _fingerprint(source)
    result = subprocess.run(
        [sys.executable, "-B", "-m", "tinyassets.box_layout_migration",
         "apply", str(source), "--dry-run"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ready"]
    assert _fingerprint(source) == before


def test_census_checks_custody_even_without_a_center_tree(source):
    vault = source / ".platform/credential-vaults/orphan/.credential-vault.json"
    vault.parent.mkdir(parents=True)
    vault.write_text(json.dumps({"credentials": [{
        "credential_type": "http", "destination": "https://unowned.invalid",
        "token": "never-reported",
    }]}))
    before = _fingerprint(source)
    report = census(source)
    assert report["refusals"]["unattributed_credential"] == 1
    assert _fingerprint(source) == before
    assert "never-reported" not in json.dumps(report)


def test_census_refuses_mismatched_router_key_owner(source):
    _sql(source / ".api-keys.sqlite3", """
        CREATE TABLE api_keys (key_id TEXT, owner TEXT, revoked_at REAL);
        INSERT INTO api_keys VALUES ('live', 'unknown', NULL), ('revoked', 'unknown', 1);
    """)
    report = census(source)
    assert report["refusals"]["router_credential_without_owner"] == 1


def test_census_does_not_write_live_wal_read_marks(source):
    writer = sqlite3.connect(source / ".tinyassets.db")
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO agent_turns VALUES ('alice','a1','live-wal')")
        writer.commit()
        before = _fingerprint(source)
        report = census(source)
        assert report["ready"], report["refusals"]
        assert report["tables"][".tinyassets.db/agent_turns"]["total"] == 5
        assert _fingerprint(source) == before
    finally:
        writer.close()


def test_branch_versions_require_an_explicit_publication_mark(source):
    _sql(source / ".runs.db", """
        CREATE TABLE branch_versions (branch_version_id TEXT, publisher TEXT, public INTEGER);
        INSERT INTO branch_versions VALUES ('private-a','alice',0), ('private-b','bob',0),
                                           ('published','unowned-publisher',1);
    """)
    report = census(source)
    table = report["tables"][".runs.db/branch_versions"]
    assert report["ready"], report["refusals"]
    assert table["per_owner"] == {"account-0001": 1, "account-0002": 1}
    assert table["published"] == 1
    assert table["total"] == 3
    _sql(source / ".runs.db",
         "INSERT INTO branch_versions VALUES ('private-orphan','unowned-publisher',0)")
    assert census(source)["refusals"]["unattributed_owner_rows"] == 1
