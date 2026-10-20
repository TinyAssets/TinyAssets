"""The cutover audit explains dispositions without changing its input tree."""

import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

from tinyassets.box_layout_migration import census


def _sql(path, statements):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
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
    from tests.support.admission import Plans

    Plans(root).set_plan("alice", "paid", event_created=42,
                         customer_id="cus_legacy", subscription_id="sub_legacy")
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
        {"dropped_cli": 1, "dropped_http": 1, "dropped_other": 0},
        {"dropped_cli": 1, "dropped_http": 1, "dropped_other": 0},
    ]
    assert _fingerprint(source) == before
    serialized = json.dumps(report)
    for private in ("alice", "bob", "secret-must-not-appear", "retired-cli-secret"):
        assert private not in serialized


@pytest.mark.parametrize("database,table", [
    (".tinyassets.db", "branches"),
    (".tinyassets.db", "background_branch_bindings"),
    (".tinyassets.db", "branch_tasks_v2"),
    (".automations.db", "automations"),
])
@pytest.mark.parametrize("case", [
    "missing", "other_owner", "public_other_owner", "private", "published_copy",
    "retired", "empty_retired_at", "active_resolved", "null", "empty", "no_column",
])
def test_census_predicts_apply_workflow_resolution(source, tmp_path, database, table, case):
    from tinyassets.box_layout_migration import _build_databases, _verify_workflow_definitions

    _sql(source / ".tinyassets.db", """
        CREATE TABLE branch_definitions
            (branch_def_id TEXT, author TEXT, published INTEGER, visibility TEXT);
    """)
    if case in {"other_owner", "public_other_owner", "private", "published_copy",
                "active_resolved"}:
        author = "bob" if case in {"other_owner", "public_other_owner"} else "alice"
        published = int(case in {"published_copy", "public_other_owner"})
        visibility = "public" if published else "private"
        _sql(source / ".tinyassets.db", "INSERT INTO branch_definitions VALUES "
             f"('definition','{author}',{published},'{visibility}')")
    if table == "branches":
        _sql(source / database, "DROP TABLE branch_heads; DROP TABLE branches;")
    definition_column = "" if case == "no_column" else ", branch_def_id TEXT"
    _sql(source / database, f"CREATE TABLE {table} (universe_id TEXT, retired_at TEXT"
         f"{definition_column})")
    retired = ("'2026-09-01T00:00:00Z'" if case == "retired" else
               "''" if case in {"empty_retired_at", "active_resolved", "null", "empty"}
               else "NULL")
    definition = "NULL" if case == "null" else "''" if case == "empty" else "'definition'"
    values = f"'a1',{retired}" + ("" if case == "no_column" else f",{definition}")
    _sql(source / database, f"INSERT INTO {table} VALUES ({values})")
    before = _fingerprint(source)
    inventory = {}
    report = census(source, _inventory=inventory)
    unresolved = (case in {"missing", "other_owner", "public_other_owner", "empty_retired_at"}
                  or (case in {"null", "empty"} and table != "branches"))
    assert report["ready"] is not unresolved, report["refusals"]
    assert report["workflow_instances"]["account-0001"][
        "unresolved_workflow_instance"] == int(unresolved)
    assert report["refusals"].get("unresolved_workflow_instance", 0) == int(unresolved)
    store = tmp_path / "boxes"
    _build_databases(source, store, tmp_path / "shared", inventory)
    if unresolved:
        with pytest.raises(RuntimeError, match="instance has no local definition"):
            _verify_workflow_definitions(store / "300001")
    else:
        _verify_workflow_definitions(store / "300001")
    _verify_workflow_definitions(store / "300002")
    with closing(sqlite3.connect(store / "300001" / database)) as db:
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (1,)
    assert report["workflow_history"]["account-0001"].get(
        "automations with non-empty retired_at kept as history", 0) == int(
            table == "automations" and case == "retired")
    if case == "active_resolved":
        assert report["workflow_instances"]["account-0001"]["resolved"] == 1
    assert _fingerprint(source) == before


@pytest.mark.parametrize("mutation,reason", [
    ("unknown_table", "unknown_table"),
    ("unknown_database", "unknown_table"),
    ("unknown_owner", "unadmitted_principal"),
    ("conflicting_owner", "unattributed_owner_rows"),
    ("ambiguous_parent", "unattributed_owner_rows"),
    ("unknown_file", "unattributed_files"),
    ("paid_orphan", "unattributed_paid_tier"),
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
    before = _fingerprint(source)
    report = census(source)
    if mutation == "unknown_owner":
        assert report["ready"], report["refusals"]
        table = report["tables"][".tinyassets.db/agent_turns"]
        assert table["reasons"] == {}
        assert table["dropped"] == 0
        assert table["per_owner"] == {"account-0001": 3, "account-0002": 2}
    else:
        assert not report["ready"]
        assert report["refusals"][reason] > 0
    assert _fingerprint(source) == before


@pytest.mark.parametrize("rows", [0, 2])
def test_a_retired_store_drops_counted_while_any_other_unknown_table_refuses(source, rows):
    store = source / ".source_channel_policy.db"
    _sql(store, "CREATE TABLE source_channel_policy "
                "(universe_id TEXT, channel_type TEXT, mode TEXT)")
    for index in range(rows):
        _sql(store, f"INSERT INTO source_channel_policy VALUES ('a1','source_code','m{index}')")
    before = _fingerprint(source)
    report = census(source)
    assert report["ready"], report["refusals"]
    table = report["tables"][".source_channel_policy.db/source_channel_policy"]
    assert table["class"] == "dropped" and table["total"] == rows
    assert table["dropped"] == rows and table["reasons"] == {"retired_store": rows}
    if rows:
        assert report["loss_summary"]["unattributed"]["retired_store"]["retired store"][
            "rows"] == rows
    assert _fingerprint(source) == before
    # The exemption is the named store only: an unknown table beside it refuses.
    _sql(store, "CREATE TABLE newly_added (owner_user_id TEXT)")
    report = census(source)
    assert not report["ready"] and report["refusals"]["unknown_table"] == 1


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


def test_census_counts_dropped_custody_even_without_a_center_tree(source):
    vault = source / ".platform/credential-vaults/orphan/.credential-vault.json"
    vault.parent.mkdir(parents=True)
    vault.write_text(json.dumps({"credentials": [{
        "credential_type": "http", "destination": "https://unowned.invalid",
        "token": "never-reported",
    }]}))
    before = _fingerprint(source)
    report = census(source)
    assert report["ready"], report["refusals"]
    assert report["credentials"]["unattributed"] == {
        "dropped_http": 1, "dropped_cli": 0, "dropped_other": 0}
    assert report["center_custody_drops"]["credentials"] == 5
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
    report = census(source)
    assert report["ready"], report["refusals"]
    table = report["tables"][".runs.db/branch_versions"]
    assert table["reasons"] == {"unadmitted_principal": 1}
    assert table["dropped"] == 1
    assert table["published"] == 1
    assert table["per_owner"] == {"account-0001": 1, "account-0002": 1}


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
            ('once_approvals', 'receipts', 'spend_reservations'),
        '.broker/outbound.db': ('agent_model_launches', 'agent_request_attempts',
            'agent_request_dispatches', 'agent_request_usage', 'agent_request_usage_links'),
        '.runtime/owner-sessions.db': ('approval_handoffs', 'inline_model_flows'),
    }.items():
        _sql(source / database, ''.join(
            f'CREATE TABLE {name} (value INT); INSERT INTO {name} VALUES (1);'
            for name in names))
    from tinyassets.broker.policy import _canonical, _digest

    policy = {"entries": [], "egress": {"mode": "allowlist", "allowlist": []},
              "spend": {"daily": 1}}
    with sqlite3.connect(source / '.broker/accounts/synthetic-account/policy.db') as db:
        db.execute('CREATE TABLE policy(generation INTEGER, digest TEXT, document TEXT)')
        db.execute('INSERT INTO policy VALUES (1,?,?)', (_digest(policy), _canonical(policy)))
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
    assert report['files']['dropped'] == 5
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


def test_unclassified_orphan_is_explicitly_dropped_by_reason(source):
    _sql(source / '.tinyassets.db', "INSERT INTO agent_turns VALUES (NULL,NULL,'orphan')")
    report = census(source)
    assert report['ready'], report['refusals']
    table = report['tables']['.tinyassets.db/agent_turns']
    assert table['reasons'] == {'no_owner_key': 1}
    assert table['dropped'] == 1
    assert table['per_owner'] == {'account-0001': 2, 'account-0002': 2}


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
        INSERT INTO plans (owner,tier,event_created) VALUES ('unknown','paid',0);
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
        '.broker/plans.db': ["UPDATE plans SET owner='dropped' WHERE owner='alice'"],
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


def test_reviewed_legacy_keys_resolve_before_drop_without_default_owner(source):
    # Use the retired principal spelling only as synthetic historical input.
    _sql(source / '.tinyassets.db', """
        CREATE TABLE branch_definitions (branch_def_id TEXT, author TEXT);
        INSERT INTO branch_definitions VALUES ('owned','universe:a2'), ('old','anonymous');
        CREATE TABLE goals (author TEXT, visibility TEXT);
        INSERT INTO goals VALUES ('universe:b1','private'), ('anonymous','private');
        CREATE TABLE provider_work_bindings (binding_id TEXT, owner_user_id TEXT, universe_id TEXT);
        INSERT INTO provider_work_bindings VALUES ('a','anonymous','a1'), ('old','anonymous','');
        CREATE TABLE served_provider_budget_reservations (binding_id TEXT);
        INSERT INTO served_provider_budget_reservations VALUES ('a'), ('old'), ('missing');
        CREATE TABLE agent_definitions (agent_definition_id TEXT, author_id TEXT);
        INSERT INTO agent_definitions VALUES ('public-definition','bob');
        CREATE TABLE agent_import_stages
            (stage_id TEXT, actor_id TEXT, published_definition_id TEXT);
        INSERT INTO agent_import_stages VALUES
            ('stage','universe:b2',NULL), ('old','anonymous',NULL),
            ('published-stage','anonymous','public-definition');
        CREATE TABLE agent_conversion_receipts (receipt_digest TEXT, actor_id TEXT, stage_id TEXT);
        INSERT INTO agent_conversion_receipts VALUES ('receipt','anonymous','stage'),
                                                     ('old','anonymous','old');
        CREATE TABLE agent_conversion_receipt_links
            (stage_id TEXT, receipt_digest TEXT, actor_id TEXT);
        INSERT INTO agent_conversion_receipt_links VALUES ('stage','receipt','anonymous'),
                                                          ('old','old','anonymous');
        CREATE TABLE agent_conversion_receipt_owners (receipt_digest TEXT, actor_id TEXT);
        INSERT INTO agent_conversion_receipt_owners VALUES
            ('receipt','anonymous'),('old','anonymous');
        CREATE TABLE agent_interchange_idempotency
            (actor_id TEXT, operation TEXT, resource_id TEXT);
        INSERT INTO agent_interchange_idempotency VALUES
            ('anonymous','stage_import','stage'), ('anonymous','convert_export','receipt'),
            ('anonymous','publish_stage','public-definition'), ('anonymous','stage_import','old');
        CREATE TABLE owner_ui_prefs (owner_user_id TEXT);
        INSERT INTO owner_ui_prefs VALUES ('alice'), ('anonymous');
    """)
    _sql(source / '.runs.db', """
        CREATE TABLE branch_versions (branch_def_id TEXT, publisher TEXT, public INT);
        INSERT INTO branch_versions VALUES ('owned','anonymous',0), ('old','anonymous',0);
        CREATE TABLE branch_schedules (branch_def_id TEXT, owner_actor TEXT, universe_id TEXT);
        INSERT INTO branch_schedules VALUES ('owned','anonymous',''), ('old','anonymous','');
    """)
    _sql(source / '.owner_devices.db', """
        CREATE TABLE owner_devices (device_id TEXT, owner_user_id TEXT);
        INSERT INTO owner_devices VALUES ('device-a','alice'), ('device-old','anonymous');
    """)
    _sql(source / '.outside-client-authority.sqlite3', """
        CREATE TABLE authenticated_connections (owner TEXT, connection_id TEXT);
        INSERT INTO authenticated_connections VALUES ('bob','receipt'), ('anonymous','unlinked');
        CREATE TABLE outside_switch (id INTEGER PRIMARY KEY, enabled INTEGER);
        INSERT INTO outside_switch VALUES (1,1);
    """)
    before = _fingerprint(source)
    report = census(source)
    assert report['ready'], report['refusals']
    expected = {
        '.tinyassets.db/branch_definitions': ('account-0001', 1, 1),
        '.tinyassets.db/goals': ('account-0002', 1, 1),
        '.tinyassets.db/provider_work_bindings': ('account-0001', 1, 1),
        '.tinyassets.db/served_provider_budget_reservations': ('account-0001', 1, 2),
        '.tinyassets.db/agent_import_stages': ('account-0002', 2, 1),
        '.tinyassets.db/agent_conversion_receipts': ('account-0002', 1, 1),
        '.tinyassets.db/agent_conversion_receipt_links': ('account-0002', 1, 1),
        '.tinyassets.db/agent_conversion_receipt_owners': ('account-0002', 1, 1),
        '.tinyassets.db/agent_interchange_idempotency': ('account-0002', 3, 1),
        '.tinyassets.db/owner_ui_prefs': ('account-0001', 1, 1),
        '.runs.db/branch_versions': ('account-0001', 1, 1),
        '.runs.db/branch_schedules': ('account-0001', 1, 1),
        '.owner_devices.db/owner_devices': ('account-0001', 1, 1),
        '.outside-client-authority.sqlite3/authenticated_connections': ('account-0002', 1, 1),
    }
    for table, (label, carried, dropped) in expected.items():
        assert report['tables'][table]['per_owner'] == {label: carried}, table
        assert report['tables'][table]['dropped'] == dropped, table
        assert report['losses']['unattributed']['rows'][table] == dropped
    assert report['tables']['.outside-client-authority.sqlite3/outside_switch']['class'] == 'router'
    assert _fingerprint(source) == before


@pytest.mark.parametrize('actor', ['bob', 'unknown-account'])
def test_real_parent_never_repairs_conflicting_or_unknown_actor(source, actor):
    """Publisher identity stays unchanged; the admitted author owns its history."""
    _sql(source / '.tinyassets.db', """
        CREATE TABLE branch_definitions (branch_def_id TEXT, author TEXT);
        INSERT INTO branch_definitions VALUES ('owned','alice');
    """)
    _sql(source / '.runs.db', f"""
        CREATE TABLE branch_versions (branch_def_id TEXT, publisher TEXT, public INT);
        INSERT INTO branch_versions VALUES ('owned','{actor}',0);
    """)
    report = census(source)
    table = report['tables']['.runs.db/branch_versions']
    if actor == 'bob':
        assert report['refusals']['unattributed_owner_rows'] == 1
        assert table['dropped'] == 0
        assert table['reasons'] == {'conflicting_owners': 1}
    else:
        assert report['ready'], report['refusals']
        assert table['dropped'] == 0
        assert table['reasons'] == {}
        assert table['per_owner'] == {'account-0001': 1}


def test_files_have_masked_patterns_and_legacy_vaults_are_unopened_drops(source):
    paths = {
        'a1/.credential-vault.json': 'not JSON; must not be opened',
        '.agent-sessions/a2/private-session/history.db': 'synthetic session',
        '.agent-sessions/unadmitted-private/private-id/history.db': 'old session',
        '.runtime/private-process/private-name.log': 'runtime',
        '.universe-sidecars/a1/.effector_consents.db': 'synthetic consent',
        'lancedb/private-table.lance/private-index': 'index',
        'a1/lancedb/private-table/private-index': 'index',
        '.run-execution-locks/private-id': 'stale lock',
        'scratch/private-name/cache': 'retired scratch',
        'activity.log': 'log',
    }
    for name, content in paths.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    before = _fingerprint(source)
    report = census(source)
    assert report['ready'], report['refusals']
    assert report['credential_custody']['outside_broker_files_dropped'] == 1
    assert report['files']['regenerated'] == 2
    assert report['files']['dropped'] == 7
    assert sum(sum(group.values()) for group in report['file_patterns'].values()) == sum(
        report['files'].values())
    assert report['file_patterns']['dropped']['<id>/<id>/<file>'] == 1
    assert report['file_patterns']['owner']['<center>/<id>/<id>/<file>'] == 1
    assert report['knowledge_stores']['account-0001'] == {
        'description': 'knowledge stores', 'files': 1, 'bytes': len('index')}
    dropped = report['loss_summary']['unattributed']['unattributed_root_vectors']
    assert dropped['knowledge stores'] == {
        'class': 'dropped', 'rows': 0, 'files': 1, 'bytes': len('index')}
    assert report['file_patterns']['owner'][
        '.universe-sidecars/<center>/.effector_consents.db'] == 1
    assert report['losses']['account-0001']['files']['<center>/.credential-vault.json'] == 1
    serialized = json.dumps(report)
    assert 'private-' not in serialized
    assert 'unadmitted-private' not in serialized
    assert _fingerprint(source) == before
    (source / '.unknown-private-root').mkdir()
    (source / '.unknown-private-root/secret-filename').write_text('unknown')
    unknown = census(source)
    assert unknown['ready'], unknown['refusals']
    assert unknown['files']['dropped'] == report['files']['dropped'] + 1
    assert unknown['file_pattern_kinds']['dropped']['<id>/<file>'] == {'unknown_dir': 1}
    assert unknown['loss_summary']['unattributed']['unknown_dir']['unknown directory files'] == {
        'class': 'dropped', 'rows': 0, 'files': 1,
    }
    assert 'secret-filename' not in json.dumps(unknown)


def test_zero_carried_sources_is_explicit_for_every_account(source):
    for path in (source / '.platform/credential-vaults').glob('*/.credential-vault.json'):
        path.write_text(json.dumps({'credentials': [{'credential_type': 'llm_subscription'}]}))
    report = census(source)
    assert report['ready'], report['refusals']
    assert report['account_model_source_summary'] == 'every account reconnects a model source'
    assert report['credentials'] == {
        'account-0001': {'dropped_cli': 1, 'dropped_http': 0, 'dropped_other': 0},
        'account-0002': {'dropped_cli': 1, 'dropped_http': 0, 'dropped_other': 0},
    }
    assert 'post-cutover owner writes' in report['restore_write_loss']


def test_checkpoint_thread_requires_all_runs_to_agree(source):
    _sql(source / '.runs.db', """
        CREATE TABLE runs (run_id TEXT, thread_id TEXT, owner_user_id TEXT, queue_universe_id TEXT);
        INSERT INTO runs VALUES ('r1','thread','alice','a1'), ('r2','thread','alice','a2');
    """)
    _sql(source / '.langgraph_runs.db', """
        CREATE TABLE checkpoints (thread_id TEXT, payload TEXT);
        INSERT INTO checkpoints VALUES ('thread','synthetic checkpoint');
    """)
    report = census(source)
    assert report['ready'], report['refusals']
    assert report['tables']['.langgraph_runs.db/checkpoints']['per_owner'] == {'account-0001': 1}
    _sql(source / '.runs.db', "INSERT INTO runs VALUES ('r3','thread','bob','b1')")
    report = census(source)
    assert report['refusals']['unattributed_owner_rows'] == 1
    assert report['tables']['.langgraph_runs.db/checkpoints']['dropped'] == 0


@pytest.mark.parametrize('reason,values,target', [
    ('no_owner_key', (None, None, None), 'dropped'),
    ('orphan_reference', (None, None, 'deleted-definition'), 'dropped'),
    ('unadmitted_principal', ('deleted-account', None, None), 'dropped'),
    ('unknown_center', (None, 'missing-center', None), 'dropped'),
    ('conflicting_owners', ('bob', 'a1', None), 'unattributed'),
    # Conflict wins over another broken key; missing reference wins over actor.
    ('conflicting_owners', ('bob', 'a1', 'deleted-definition'), 'unattributed'),
    ('orphan_reference', ('deleted-account', 'missing-center', 'deleted'), 'dropped'),
    (None, ('alice', 'missing-center', None), ('owner', 'alice')),
])
def test_one_row_reason_drives_inventory_and_count(source, reason, values, target):
    _sql(source / '.runs.db', '''
        CREATE TABLE branch_versions (publisher TEXT, universe_id TEXT, branch_def_id TEXT);
    ''')
    with closing(sqlite3.connect(source / '.runs.db')) as db, db:
        db.execute('INSERT INTO branch_versions VALUES (?,?,?)', values)
    before = _fingerprint(source)
    inventory = {}
    report = census(source, _inventory=inventory)
    table = report['tables']['.runs.db/branch_versions']
    assert table['reasons'] == ({reason: 1} if reason else {})
    assert table['dropped'] == int(target == 'dropped')
    assert inventory['assignments']['.runs.db', 'branch_versions'] == [target]
    assert report['ready'] == (target != 'unattributed')
    assert report['refusals'].get('unattributed_owner_rows', 0) == int(target == 'unattributed')
    assert sum(table['per_owner'].values()) + table['dropped'] == table['total'] == 1
    assert _fingerprint(source) == before
    assert 'deleted-account' not in json.dumps(report)
    assert 'missing-center' not in json.dumps(report)


@pytest.mark.parametrize('parent_values,reason', [
    ("('binding','deleted-account',NULL)", 'unadmitted_principal'),
    ("('binding',NULL,'unknown-center')", 'unknown_center'),
    ("('binding',NULL,NULL)", 'no_owner_key'),
    ("('binding','alice','b1')", 'conflicting_owners'),
    ("('binding','alice','a1'),('binding','bob','b1')", 'conflicting_owners'),
])
def test_budget_parent_reason_propagates_without_losing_conflicts(source, parent_values, reason):
    _sql(source / '.tinyassets.db', f'''
        CREATE TABLE provider_work_bindings (binding_id TEXT, owner_user_id TEXT, universe_id TEXT);
        INSERT INTO provider_work_bindings VALUES {parent_values};
        CREATE TABLE served_provider_budget_reservations (binding_id TEXT);
        INSERT INTO served_provider_budget_reservations VALUES ('binding'), ('deleted-binding');
    ''')
    inventory = {}
    report = census(source, _inventory=inventory)
    table = report['tables']['.tinyassets.db/served_provider_budget_reservations']
    assert table['reasons'] == {reason: 1, 'orphan_reference': 1}
    assert table['dropped'] == (1 if reason == 'conflicting_owners' else 2)
    assert inventory['assignments']['.tinyassets.db', 'served_provider_budget_reservations'] == [
        'unattributed' if reason == 'conflicting_owners' else 'dropped', 'dropped',
    ]
    assert report['ready'] == (reason != 'conflicting_owners')
    assert report['loss_summary']['unattributed']['orphan_reference'][
        'usage reservation history'] == {'class': 'dropped', 'rows': 1, 'files': 0}


@pytest.mark.parametrize('relative,kind,placement', [
    ('a1/private-file', 'admitted_center', 'owner'),
    ('never-admitted/private-file', 'unadmitted_center', 'dropped'),
    ('unknown-directory/nested/private-file', 'unknown_dir', 'dropped'),
    ('a' * 64 + '/private-file', 'account_hash', 'unattributed'),
    ('.broker/accounts/private-account/private-file', 'broker_account', 'unattributed'),
    ('.broker/state/private-file', 'platform_store', 'unattributed'),
    ('.platform/private-file', 'platform_store', 'unattributed'),
    ('private-file', 'root_file', 'unattributed'),
    ('.broker/accounts/private-account/.deleted', 'broker_account', 'regenerated'),
    ('.broker/state/fence.json', 'platform_store', 'broker'),
    ('release-state.json', 'root_file', 'router'),
    ('.run_recovery.lock', 'root_file', 'regenerated'),
    ('.platform/credential-vaults/a1/.custody.lock', 'platform_store', 'regenerated'),
    ('.platform/credential-vaults/a1/.oauth-refresh/' + 'a' * 32 + '.lock',
     'platform_store', 'regenerated'),
    # A key in an undocumented location is not assumed to be safe custody.
    ('.platform/private-key.key', 'platform_store', 'unattributed'),
])
def test_file_kind_and_disposition_are_counted_without_names(source, relative, kind, placement):
    _sql(source / '.tinyassets.db', '''
        CREATE TABLE universes (id TEXT);
        INSERT INTO universes VALUES ('a1'), ('never-admitted');
    ''')
    baseline = census(source)
    path = source / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('synthetic content, never reported')
    before = _fingerprint(source)
    inventory = {}
    report = census(source, _inventory=inventory)
    assert inventory['file_targets'][relative] == (
        ('owner', 'alice') if placement == 'owner' else placement)
    kinds = report['file_pattern_kinds'].get(placement, {})
    before_kinds = baseline['file_pattern_kinds'].get(placement, {})
    assert sum(group.get(kind, 0) for group in kinds.values()) == 1 + sum(
        group.get(kind, 0) for group in before_kinds.values())
    assert report['ready'] == (placement != 'unattributed')
    assert report['refusals'].get('unattributed_files', 0) == int(placement == 'unattributed')
    for group, patterns in report['file_patterns'].items():
        assert {pattern: sum(counts.values()) for pattern, counts in
                report['file_pattern_kinds'][group].items()} == patterns
    assert _fingerprint(source) == before
    serialized = json.dumps(report)
    for private in ('private-file', 'private-account', 'private-key', 'never-admitted/',
                    'unknown-directory', 'a' * 64, 'synthetic content'):
        assert private not in serialized


def test_founder_loss_summary_counts_cli_files_per_account(source):
    for relative in ('a1/.credentials/deep/login', 'a2/.credentials/session',
                     'b1/.credentials/state', '.platform/owners/300001/agent-home/login'):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic private state')
    report = census(source)
    assert report['ready'], report['refusals']
    for label, count in (('account-0001', 3), ('account-0002', 1)):
        assert report['loss_summary'][label]['retired_cli_state']['CLI login state'] == {
            'class': 'dropped', 'rows': 0, 'files': count,
        }
    assert report['files']['dropped'] == 6
    summary = json.dumps(report['loss_summary'])
    assert '/' not in summary
    assert '300001' not in summary
    assert 'alice' not in summary


def test_conflicting_run_file_still_refuses(source):
    _sql(source / '.runs.db', '''
        CREATE TABLE runs (run_id TEXT, owner_user_id TEXT, queue_universe_id TEXT);
        INSERT INTO runs VALUES ('private-run','alice','b1');
    ''')
    (source / 'runs').mkdir()
    (source / 'runs/private-run.jsonl').write_text('synthetic transcript')
    inventory = {}
    report = census(source, _inventory=inventory)
    assert report['refusals']['unattributed_owner_rows'] == 1
    assert report['refusals']['unattributed_files'] == 1
    assert report['tables']['.runs.db/runs']['reasons'] == {'conflicting_owners': 1}
    assert inventory['file_targets']['runs/private-run.jsonl'] == 'unattributed'
    assert report['file_pattern_kinds']['unattributed'] == {
        'runs/<file>.jsonl': {'platform_store': 1},
    }
    assert 'private-run' not in json.dumps(report)


def test_orphan_foreign_key_drop_is_applied_but_retained_violation_refuses(source, tmp_path):
    from tinyassets.box_layout_migration import _build_databases

    _sql(source / '.tinyassets.db', '''
        CREATE TABLE provider_work_bindings (binding_id TEXT PRIMARY KEY, owner_user_id TEXT);
        INSERT INTO provider_work_bindings VALUES ('live','alice');
        CREATE TABLE served_provider_budget_reservations (
            binding_id TEXT REFERENCES provider_work_bindings(binding_id), payload TEXT);
        INSERT INTO served_provider_budget_reservations VALUES ('deleted','lost'), ('live','kept');
    ''')
    before = _fingerprint(source)
    inventory = {}
    report = census(source, _inventory=inventory)
    assert report['ready'], report['refusals']
    assert report['tables']['.tinyassets.db/served_provider_budget_reservations'][
        'reasons'] == {'orphan_reference': 1}
    assert all('payload' not in row for row in inventory['tables'][
        '.tinyassets.db', 'served_provider_budget_reservations'])
    store, shared = tmp_path / 'boxes', tmp_path / 'shared'
    _build_databases(source, store, shared, inventory)
    for uid, expected in ((300001, [('live', 'kept')]), (300002, [])):
        with closing(sqlite3.connect(store / str(uid) / '.tinyassets.db')) as db:
            rows = db.execute('SELECT * FROM served_provider_budget_reservations').fetchall()
            assert rows == expected
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    assert _fingerprint(source) == before
    # An unrelated FK in a retained table is still a structural refusal.
    _sql(source / '.tinyassets.db', '''
        CREATE TABLE owner_ui_prefs (owner_user_id TEXT,
            preference TEXT REFERENCES provider_work_bindings(binding_id));
        INSERT INTO owner_ui_prefs VALUES ('alice','missing');
    ''')
    report = census(source)
    assert report['refusals']['database_foreign_keys'] == 1
    assert report['tables']['.tinyassets.db/owner_ui_prefs']['per_owner'] == {'account-0001': 1}
    assert report['tables']['.tinyassets.db/owner_ui_prefs']['dropped'] == 0


def test_admitted_center_wins_through_definitions_and_reports_orphan_workflow_history(source):
    _sql(source / '.tinyassets.db', '''
        CREATE TABLE branch_definitions (branch_def_id TEXT, author TEXT, universe_id TEXT);
        INSERT INTO branch_definitions VALUES ('owned','universe:a1',NULL),
            ('ownerless','anonymous',NULL), ('unknown','universe:unknown-center',NULL);
        CREATE TABLE provider_work_bindings (binding_id TEXT, owner_user_id TEXT, universe_id TEXT);
        INSERT INTO provider_work_bindings VALUES ('lost','earlier-identity',NULL),
                                                  ('kept','earlier-identity','a1');
        CREATE TABLE served_provider_budget_reservations (binding_id TEXT);
        INSERT INTO served_provider_budget_reservations VALUES ('lost'), ('kept');
    ''')
    _sql(source / '.runs.db', '''
        CREATE TABLE branch_versions (branch_def_id TEXT, publisher TEXT, universe_id TEXT);
        INSERT INTO branch_versions VALUES ('owned','earlier-identity',NULL),
            ('owned','system',NULL), ('ownerless','earlier-identity','a1'),
            ('deleted','earlier-identity','a1'), (NULL,'earlier-identity','a1'),
            ('unknown','earlier-identity','a1');
        CREATE TABLE branch_schedules (branch_def_id TEXT, owner_actor TEXT, universe_id TEXT);
        INSERT INTO branch_schedules VALUES ('owned','earlier-identity',NULL),
            ('deleted','earlier-identity','a1');
        CREATE TABLE runs (run_id TEXT, owner_user_id TEXT, queue_universe_id TEXT);
        INSERT INTO runs VALUES ('run','earlier-identity','a1');
        CREATE TABLE run_events (run_id TEXT);
        INSERT INTO run_events VALUES ('run');
    ''')
    before = _fingerprint(source)
    inventory = {}
    report = census(source, _inventory=inventory)
    assert report['ready'], report['refusals']
    for key, count in (('.tinyassets.db/branch_definitions', 1),
                       ('.runs.db/branch_versions', 5), ('.runs.db/branch_schedules', 1),
                       ('.tinyassets.db/provider_work_bindings', 1),
                       ('.tinyassets.db/served_provider_budget_reservations', 1),
                       ('.runs.db/runs', 1), ('.runs.db/run_events', 1)):
        assert report['tables'][key]['per_owner'] == {'account-0001': count}
    for table in ('branch_versions', 'branch_schedules'):
        details = report['tables'][f'.runs.db/{table}']
        assert details['reasons'] == {'orphan_reference': 1}
        assert details['dropped'] == 1
        assert inventory['assignments']['.runs.db', table].count('dropped') == 1
    assert report['loss_summary']['account-0001']['orphan_reference']['workflow history'] == {
        'class': 'dropped', 'rows': 2, 'files': 0, 'versions': 1, 'schedules': 1,
    }
    for table, description in (
        ('provider_work_bindings', 'provider bindings'),
        ('served_provider_budget_reservations', 'usage reservation history'),
    ):
        assert report['tables'][f'.tinyassets.db/{table}']['dropped'] == 1
        assert report['loss_summary']['unattributed']['unadmitted_principal'][description] == {
            'class': 'dropped', 'rows': 1, 'files': 0,
        }
    assert _fingerprint(source) == before
    assert 'earlier-identity' not in json.dumps(report)


@pytest.mark.parametrize('relative,placement', [
    ('.broker/state/owner-identities.db-wal', 'regenerated'),
    ('.broker/state/ops.db-shm', 'regenerated'),
    ('.broker/state/next-wake.db-journal', 'regenerated'),
    ('.broker/plans.db-wal', 'regenerated'),
    ('.broker/credential-custody.db-shm', 'regenerated'),
    ('.broker/outbound.db-wal', 'regenerated'),
    ('.broker/accounts/' + 'a' * 64 + '/policy.db-shm', 'regenerated'),
    ('.broker/accounts/' + 'a' * 64 + '/policy.db', 'broker'),
    ('.broker/browser-vault/' + 'a' * 64 + '.key', 'broker'),
    ('browser-vault/' + 'a' * 64 + '.key', 'broker'),
    ('.auth.db-shm', 'regenerated'),
    ('.api-keys.sqlite3-wal', 'regenerated'),
    ('.broker/plans.db', 'database_files'),
    ('platform-expected-instance.json', 'router'),
    ('.runtime_status.json', 'regenerated'),
    ('.worker_supervisor.synthetic-consumer.json', 'regenerated'),
    ('.consumer_liveness/synthetic.lock', 'regenerated'),
    ('.owner_tree/synthetic/1-synthetic.lock', 'regenerated'),
    ('.run_recovery.lock.pid', 'regenerated'),
    ('.layout.json.synthetic.tmp', 'regenerated'),
    ('.broker/state/.fence.synthetic', 'regenerated'),
    ('.platform/credential-vaults/a1/.vault-synthetic.tmp', 'regenerated'),
])
def test_platform_writer_families_have_explicit_dispositions(source, relative, placement):
    path = source / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text('synthetic platform bytes')
    before = _fingerprint(source)
    inventory = {}
    report = census(source, _inventory=inventory)
    assert report['ready'], report['refusals']
    assert inventory['file_targets'][relative] == placement
    assert _fingerprint(source) == before
    assert 'synthetic platform bytes' not in json.dumps(report)
    assert 'a' * 64 not in json.dumps(report)


@pytest.mark.parametrize('relative,pattern', [
    ('private-personal-name.json', '<file>.json'),
    ('.broker/state/private-personal-name.db-wal', '.broker/<id>/<file>.db-wal'),
    ('.platform/private-personal-name.key', '.platform/<file>.key'),
    ('private-personal-name', '<file>'),
])
def test_unknown_files_refuse_with_only_masked_filename_patterns(source, relative, pattern):
    path = source / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('private contents')
    before = _fingerprint(source)
    report = census(source)
    assert not report['ready']
    assert report['refusals']['unattributed_files'] == 1
    assert report['file_patterns']['unattributed'] == {pattern: 1}
    assert 'private-personal-name' not in json.dumps(report)
    assert 'private contents' not in json.dumps(report)
    assert _fingerprint(source) == before


def test_workflow_history_author_then_publisher_and_version_schedules(source, tmp_path):
    from tinyassets.box_layout_migration import _build_databases

    _sql(source / '.tinyassets.db', '''
        CREATE TABLE branch_definitions (branch_def_id TEXT, author TEXT);
        INSERT INTO branch_definitions VALUES ('alice-definition','alice'),
            ('bob-definition','bob'), ('unadmitted-definition','platform-actor');
    ''')
    _sql(source / '.runs.db', '''
        CREATE TABLE branch_versions (branch_version_id TEXT, branch_def_id TEXT,
                                      publisher TEXT, snapshot_json TEXT);
        INSERT INTO branch_versions VALUES
            ('author','alice-definition','platform-actor','{"synthetic":"author"}'),
            ('both','alice-definition','alice','{"synthetic":"both"}'),
            ('publisher','missing','bob','{"synthetic":"publisher"}'),
            ('fallback','unadmitted-definition','bob','{"synthetic":"fallback"}'),
            ('drop','missing','platform-actor','{"synthetic":"drop"}');
        CREATE TABLE branch_schedules (branch_def_id TEXT, branch_version_id TEXT,
                                       owner_actor TEXT);
        INSERT INTO branch_schedules VALUES ('alice-definition',NULL,'platform-actor'),
                                             ('missing',NULL,'platform-actor');
    ''')
    before = _fingerprint(source)
    inventory = {}
    report = census(source, _inventory=inventory)
    assert report['ready'], report['refusals']
    assert report['workflow_history'] == {
        'account-0001': {'versions': 2, 'schedules': 1},
        'account-0002': {'versions': 2, 'schedules': 1},
    }
    versions = report['tables']['.runs.db/branch_versions']
    assert versions['dropped'] == 1
    assert versions['reasons'] == {'orphan_reference': 1}
    assert report['loss_summary']['unattributed']['orphan_reference']['workflow history'] == {
        'class': 'dropped', 'rows': 1, 'files': 0, 'versions': 1,
    }
    store, shared = tmp_path / 'boxes', tmp_path / 'shared'
    _build_databases(source, store, shared, inventory)
    for uid, ids in ((300001, ['author', 'both']), (300002, ['publisher', 'fallback'])):
        with closing(sqlite3.connect(store / str(uid) / '.runs.db')) as db:
            rows = db.execute(
                'SELECT branch_version_id,snapshot_json FROM branch_versions').fetchall()
            assert [row[0] for row in rows] == ids
            assert all(json.loads(row[1])['synthetic'] == row[0] for row in rows)
            assert db.execute('SELECT COUNT(*) FROM branch_schedules').fetchone()[0] == 1
    assert _fingerprint(source) == before
    _sql(source / '.runs.db', '''
        INSERT INTO branch_versions VALUES ('conflict','alice-definition','bob','{}');
        INSERT INTO branch_schedules VALUES ('alice-definition','publisher','platform-actor');
    ''')
    report = census(source)
    assert not report['ready']
    assert report['tables']['.runs.db/branch_versions']['reasons']['conflicting_owners'] == 1
    assert report['tables']['.runs.db/branch_schedules']['reasons']['conflicting_owners'] == 2


def _round6_files(source, *, drop_blob=False):
    """Exactly the 16 identified files; all content and identifiers are synthetic."""
    _sql(source / '.tinyassets.db', '''
        CREATE TABLE universes (id TEXT);
        INSERT INTO universes VALUES ('never');
    ''')
    placements = {
        '.broker/.outbound-proxy/account-a/audit.jsonl': 'broker',
        '.broker/.outbound-proxy/account-b/audit.jsonl': 'broker',
        '.broker/state/architecture-signing.json': 'broker',
        '.connect/client_id': 'router', '.connect/client_secret': 'router',
        '.connect/session_secret': 'router',
        '.engine_mcp_http_routes.json': 'regenerated',
        '.scoped-reset.barrier': 'regenerated',
        '.run-file-custody/.blob-root.lock': 'regenerated',
        '.tinyassets.db.prebudgetrepair.bak-shm': 'dropped',
        '.tinyassets.db.prebudgetrepair.bak-wal': 'dropped',
        '.executors/synthetic/.codex/config.toml': 'dropped',
        '.platform/external-agent-refresh-' + 'a' * 32: 'dropped',
        '.run-file-custody/synthetic.body': 'dropped' if drop_blob else ('owner', 'alice'),
        '.scheduler_fires.jsonl': 'split', 'ledger.json': 'split',
    }
    for relative in placements:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic-private-' + path.name)
    _sql(source / '.runs.db', '''
        CREATE TABLE run_file_operations (operation_id TEXT, owner_id TEXT, universe_id TEXT);
        INSERT INTO run_file_operations VALUES ('operation','alice','a1');
        CREATE TABLE run_file_objects (file_id TEXT, operation_id TEXT, owner_id TEXT,
                                       universe_id TEXT, storage_key TEXT);
        INSERT INTO run_file_objects VALUES ('synthetic','operation','alice','a1','synthetic');
    ''')
    if drop_blob:
        _sql(source / '.runs.db', "UPDATE run_file_objects SET operation_id='missing'")
    fires = [dict(branch_def_id='private-definition', result='ok', run_name='private-run',
                  ts=42, universe_id=center) for center in ('a1', 'b1', 'a2', 'never', 'unknown')]
    (source / '.scheduler_fires.jsonl').write_text(
        ''.join(json.dumps(entry) + '\n' for entry in fires))
    ledger = [dict(action='synthetic', actor=actor, target=target, summary='private-summary',
                   timestamp='synthetic-time') for actor, target in (
                       ('alice', 'bob'), ('platform-actor', 'bob'), ('', 'alice'),
                       ('platform-actor', 'unknown'))]
    (source / 'ledger.json').write_text(json.dumps(ledger))
    return placements, fires, ledger


@pytest.mark.parametrize('drop_blob', [False, True])
def test_round6_file_classes_and_history_counts_are_private_and_readonly(source, drop_blob):
    placements, _, _ = _round6_files(source, drop_blob=drop_blob)
    before = _fingerprint(source)
    inventory = {}
    report = census(source, _inventory=inventory)
    assert report['ready'], report['refusals']
    assert {name: inventory['file_targets'][name] for name in placements} == placements
    assert inventory['dropped_centers'] == {'never'}
    assert report['history_splits'] == {
        '.scheduler_fires.jsonl': {'description': 'scheduled-run history', 'total': 5,
                                 'per_owner': {'account-0001': 2, 'account-0002': 1}, 'dropped': 2},
        'ledger.json': {'description': 'activity ledger', 'total': 4,
                        'per_owner': {'account-0001': 2, 'account-0002': 1}, 'dropped': 1},
    }
    losses = report['loss_summary']['unattributed']
    assert losses['retired_cli_state']['CLI login state']['files'] == 2
    assert losses['no_admitted_owner'] == {
        'scheduled-run history': {'class': 'dropped', 'entries': 2},
        'activity ledger': {'class': 'dropped', 'entries': 1},
    }
    if drop_blob:
        assert losses['platform_store']['run-file uploads']['files'] == 1
    assert len(report['accepted_user_costs']) == 5
    serialized = json.dumps(report)
    for private in ('alice', 'bob', '.connect', 'client_secret', 'session_secret',
                    'synthetic-private', 'private-summary', 'private-run', 'private-definition'):
        assert private not in serialized
    assert _fingerprint(source) == before


@pytest.mark.parametrize('mutation', ['source', 'owner_count', 'dropped_count', 'output'])
def test_history_split_verification_refuses_changed_counts_or_content(source, tmp_path,
                                                                    monkeypatch, mutation):
    from tinyassets import box_layout_migration as migration

    _round6_files(source)
    inventory = {}
    assert census(source, _inventory=inventory)['ready']
    store = tmp_path / 'boxes'
    for uid in inventory['owners'].values():
        (store / str(uid)).mkdir(parents=True)
    if mutation == 'source':
        (source / 'ledger.json').write_text('[]')
    elif mutation == 'owner_count':
        inventory['histories']['ledger.json']['report']['per_owner']['account-0001'] += 1
    elif mutation == 'dropped_count':
        inventory['histories']['ledger.json']['report']['dropped'] += 1
    else:
        original = migration._history_entries

        def corrupt(path):
            return original(path) if path.parent == source else []

        monkeypatch.setattr(migration, '_history_entries', corrupt)
    with pytest.raises(RuntimeError, match='history'):
        migration._split_histories(source, store, inventory)


def test_discarded_http_slots_need_no_connection_ownership_join(source):
    _sql(source / "outbound.db", "DELETE FROM outbound_connections")
    before = _fingerprint(source)
    report = census(source)
    assert report["ready"], report["refusals"]
    assert report["center_custody_drops"]["credentials"] == 4
    assert report["center_custody_drops"]["connections"] == 0
    assert _fingerprint(source) == before
