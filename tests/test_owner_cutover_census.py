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
        vault = root / '.platform/credential-vaults' / center / '.credential-vault.json'
        vault.parent.mkdir(parents=True)
        vault.write_text(json.dumps({
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


def test_explicit_classes_cover_legacy_rows_and_never_admitted_tree(source):
    _sql(source / ".tinyassets.db", """
        CREATE TABLE universes (id TEXT PRIMARY KEY);
        INSERT INTO universes VALUES ('a1'), ('never-admitted');
        INSERT INTO branches VALUES ('legacy-branch','never-admitted');
        INSERT INTO branch_heads VALUES ('legacy-branch','old');
        CREATE TABLE external_agent_cli_invocations (owner_user_id TEXT);
        INSERT INTO external_agent_cli_invocations VALUES ('alice'), (NULL);
        CREATE TABLE owner_fence (fence INT);
        INSERT INTO owner_fence VALUES (90);
        CREATE TABLE app_event_admissions (universe_id TEXT);
        INSERT INTO app_event_admissions VALUES ('a1'), (NULL);
        CREATE TABLE webhook_deliveries (dedupe_key TEXT);
        INSERT INTO webhook_deliveries VALUES ('old');
    """)
    _sql(source / ".runs.db", """
        CREATE TABLE runs (run_id TEXT, thread_id TEXT, owner_user_id TEXT, queue_universe_id TEXT);
        INSERT INTO runs VALUES ('legacy','thread-old',NULL,NULL),
                                ('center-owned','thread-a',NULL,'a2');
        CREATE TABLE run_events (run_id TEXT);
        INSERT INTO run_events VALUES ('legacy'), ('center-owned');
        CREATE TABLE run_lineage (run_id TEXT);
        INSERT INTO run_lineage VALUES ('legacy');
        CREATE TABLE contribution_events (source_run_id TEXT, owner_user_id TEXT);
        INSERT INTO contribution_events VALUES ('legacy',NULL);
        CREATE TABLE branch_versions_migrations (version INT);
        INSERT INTO branch_versions_migrations VALUES (1);
        CREATE TABLE workspace_push_intents (run_id TEXT, universe_id TEXT);
        INSERT INTO workspace_push_intents VALUES ('center-owned','a2');
    """)
    _sql(source / ".langgraph_runs.db", """
        CREATE TABLE checkpoints (thread_id TEXT);
        INSERT INTO checkpoints VALUES ('thread-old'), ('thread-a');
        CREATE TABLE writes (thread_id TEXT);
        INSERT INTO writes VALUES ('thread-old');
    """)
    for database, names in {
        '.owner_leases.db': ('fence_high_water', 'owner_lease', 'owner_store_catalog'),
        '.storage_accounting.db': ('commons_log_bytes', 'counter', 'measurements'),
        '.broker/accounts/synthetic-account/policy.db':
            ('once_approvals', 'policy', 'receipts', 'spend_reservations'),
        '.broker/outbound.db': ('agent_model_launches', 'agent_request_attempts',
            'agent_request_dispatches', 'agent_request_usage', 'agent_request_usage_links'),
        '.runtime/owner-sessions.db': ('approval_handoffs', 'inline_model_flows'),
    }.items():
        _sql(source / database, ''.join(
            f'CREATE TABLE {name} (value INT); INSERT INTO {name} VALUES (1);'
            for name in names))
    _sql(source / '.outside-client-authority.sqlite3', """
        CREATE TABLE authenticated_connections (owner TEXT);
        INSERT INTO authenticated_connections VALUES ('alice'), ('bob');
    """)
    (source / 'never-admitted').mkdir()
    (source / 'never-admitted/history').write_text('synthetic dropped history')
    (source / '.tinyassets.db.prebudgetrepair.bak').write_bytes(b'not opened as a database')
    (source / 'runs').mkdir()
    (source / 'runs/legacy.jsonl').write_text('synthetic legacy transcript')
    (source / 'runs/center-owned.jsonl').write_text('synthetic owned transcript')
    before = _fingerprint(source)
    report = census(source)
    assert report['ready'], report['refusals']
    assert report['files']['dropped'] == 3
    assert report['row_totals']['regenerated'] == 8
    assert report['tables']['.runs.db/runs']['dropped'] == 1
    assert report['tables']['.runs.db/runs']['per_owner'] == {'account-0001': 1}
    assert report['tables']['.langgraph_runs.db/checkpoints']['dropped'] == 1
    assert report['tables']['.tinyassets.db/universes']['dropped'] == 1
    assert report['tables']['.tinyassets.db/branch_heads']['dropped'] == 1
    assert report['tables']['.tinyassets.db/app_event_admissions']['dropped'] == 1
    assert report['tables']['.tinyassets.db/webhook_deliveries']['dropped'] == 1
    assert report['tables']['.outside-client-authority.sqlite3/authenticated_connections'][
        'per_owner'] == {'account-0001': 1, 'account-0002': 1}
    assert report['credentials']['account-0001']['dropped_cli'] == 2
    assert sum(report['row_totals'].values()) == sum(t['total'] for t in report['tables'].values())
    for table in report['tables'].values():
        if table['class'] == 'owner':
            assert (sum(table['per_owner'].values()) + table['published'] + table['dropped']
                    == table['total'])
    assert _fingerprint(source) == before


def test_binding_never_substitutes_for_broker_admission(source):
    _sql(source / '.broker/state/owner-identities.db', 'DELETE FROM center_admissions')
    report = census(source)
    assert not report['ready']
    assert report['admitted_centers'] == 0
    assert report['refusals']['ownership_not_live_admitted'] == 4


def test_unclassified_orphan_is_not_dropped(source):
    _sql(source / '.tinyassets.db', "INSERT INTO agent_turns VALUES (NULL,NULL,'orphan')")
    report = census(source)
    assert not report['ready']
    assert report['refusals']['unattributed_owner_rows'] == 1


def test_null_owner_uses_center_and_an_unknown_paid_broker_plan_refuses(source):
    _sql(source / '.tinyassets.db', """
        CREATE TABLE owner_ui_prefs (owner_user_id TEXT, universe_id TEXT);
        INSERT INTO owner_ui_prefs VALUES (NULL,'a1'), (NULL,NULL);
    """)
    report = census(source)
    assert report['ready'], report['refusals']
    table = report['tables']['.tinyassets.db/owner_ui_prefs']
    assert table['per_owner'] == {'account-0001': 1}
    assert table['dropped'] == 1
    _sql(source / '.broker/plans.db', """
        CREATE TABLE plans (owner TEXT, tier TEXT);
        INSERT INTO plans VALUES ('unknown','paid');
    """)
    assert census(source)['refusals']['unattributed_paid_tier'] == 1


def test_owner_name_cannot_collide_with_a_placement_class(source):
    for path, statements in {
        '.broker/state/owner-identities.db': [
            "UPDATE owner_identities SET principal='dropped' WHERE principal='alice'",
            "UPDATE center_admissions SET principal='dropped' WHERE principal='alice'"],
        '.tinyassets.db': [
            "UPDATE universe_owner SET owner_id='dropped' WHERE owner_id='alice'",
            "UPDATE agent_turns SET owner_user_id='dropped' WHERE owner_user_id='alice'",
            "UPDATE webhook_hooks SET owner_principal_id='dropped' "
            "WHERE owner_principal_id='alice'"],
        'outbound.db': [
            "UPDATE outbound_connections SET owner_user_id='dropped' WHERE owner_user_id='alice'"],
    }.items():
        _sql(source / path, ';'.join(statements))
    report = census(source)
    assert report['ready'], report['refusals']
    table = report['tables']['.tinyassets.db/agent_turns']
    assert table['dropped'] == 0
    assert sorted(table['per_owner'].values()) == [2, 2]


def test_transcript_key_preserves_dots_and_census_never_materializes_payloads(source):
    _sql(source / '.runs.db', """
        CREATE TABLE runs (run_id TEXT, owner_user_id TEXT, queue_universe_id TEXT, payload TEXT);
        INSERT INTO runs VALUES ('run.with.dot','alice','a1','private-payload'),
                                ('run','bob','b1','other-private-payload');
    """)
    (source / 'runs').mkdir()
    (source / 'runs/run.with.dot.jsonl').write_text('private transcript')
    inventory = {}
    report = census(source, _inventory=inventory)
    assert report['ready'], report['refusals']
    assert inventory['file_targets']['runs/run.with.dot.jsonl'] == ('owner', 'alice')
    assert all('payload' not in row for row in inventory['tables']['.runs.db', 'runs'])
