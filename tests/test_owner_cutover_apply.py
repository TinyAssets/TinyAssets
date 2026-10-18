"""Synthetic real-file cutover and crash resume; no external data fixtures."""

import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from contextlib import closing

import pytest

from tests.test_box_layout_migration import _archive
from tests.test_broker_server import broker  # noqa: F401
from tests.test_owner_cutover_census import _fingerprint, _round6_files, _sql
from tests.test_owner_cutover_census import source as source
from tinyassets import box_layout_migration as migration


def test_cutover_preserves_cancellation_mapping_and_resumes_payable_checkout(cutover, monkeypatch):
    from tests.test_cutover_billing_deletion import attempt, legacy_attempt
    from tinyassets.billing.plan_action import _start_checkout
    from tinyassets.broker.owner_reads import Plans

    data, store, *_ = cutover
    original = attempt()
    legacy_attempt(data, original)
    migration.apply(*cutover)
    plan = Plans(data)
    assert plan.read("alice")["customer_id"] == "cus_legacy"
    assert plan.read("alice")["subscription_id"] == "sub_legacy"
    assert json.loads(plan.read("bob")["attempt"]) == original
    assert not (data / "b1/.subscription_state.db").exists()
    assert not (store / "300002/b1/.subscription_state.db").exists()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(data))
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_synthetic")
    # Use the actual shared writer through the test broker transport.
    assert _start_checkout(owner="bob", origin="https://tinyassets.io") == {
        "url": original["url"], "resumed": True}


def test_root_vectors_never_inherit_knowledge_owner(cutover):
    data, store, *_ = cutover
    _sql(data / "knowledge.db", "CREATE TABLE facts (universe_id TEXT, text TEXT); "
         "INSERT INTO facts VALUES ('a1','owned knowledge');")
    for owner in ("alice", "bob"):
        path = data / "lancedb" / owner / "vectors.lance"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"synthetic mixed root vectors")
    report = migration.census(data)
    assert report["ready"], report["refusals"]
    loss = report["loss_summary"]["unattributed"]["unattributed_root_vectors"]["knowledge stores"]
    assert loss["files"] == 2
    assert loss["bytes"] == 2 * len(b"synthetic mixed root vectors")
    migration.apply(*cutover)
    for uid in (300001, 300002):
        assert not (store / str(uid) / "lancedb").exists()
    assert (store / "300001/knowledge.db").exists()


def test_cutover_repairs_only_named_indexes_and_records_verified_repair(cutover):
    from tests.test_cutover_index_integrity import corrupt_index

    data, store, *_ = cutover
    corrupt_index(data / ".tinyassets.db")
    assert migration.census(data)["ready"]
    migration.apply(*cutover)
    receipt = json.loads((data / ".owner-cutover.json").read_text())
    assert receipt["report"]["reindexed"] == ["reindexed: .tinyassets.db/drift"]
    for uid in (300001, 300002):
        with sqlite3.connect(store / str(uid) / ".tinyassets.db") as db:
            assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert db.execute("SELECT COUNT(*) FROM agent_turns INDEXED BY drift"
                              ).fetchone() == (2,)


def _published_workflow(data):
    # Full production definition schema, including all current migrated columns.
    import tempfile
    from pathlib import Path

    from tinyassets import commons_catalog, daemon_server
    from tinyassets.branches import BranchDefinition

    with tempfile.TemporaryDirectory() as directory:
        daemon_server.initialize_author_server(directory)
        with sqlite3.connect(Path(directory) / ".tinyassets.db") as db:
            ddl = db.execute("SELECT sql FROM sqlite_master WHERE "
                             "name='branch_definitions'").fetchone()[0]
        with sqlite3.connect(data / ".tinyassets.db") as db:
            db.execute(ddl)
    definition = BranchDefinition(branch_def_id="workflow", name="Published workflow",
                                  author="alice", visibility="public", published=True).to_dict()
    # The fixture's unrelated legacy tables are intentionally minimal; avoid
    # schema initialization of those tables while using the actual row encoder.
    _, values = daemon_server._branch_definition_insert(definition)
    with sqlite3.connect(data / ".tinyassets.db") as db:
        db.execute(daemon_server._BRANCH_DEFINITION_INSERT_SQL, values)
        db.execute("CREATE TABLE background_branch_bindings (binding_id TEXT, "
                   "universe_id TEXT, branch_def_id TEXT)")
        db.execute("INSERT INTO background_branch_bindings VALUES ('instance','a1','workflow')")
    published = commons_catalog.publish_definition(data, author_id="alice", payload={
        "schema_version": 1, "name": "Published workflow", "bundle_id": "bundle_" + "a" * 32,
        "components": {"identity": {"kind": "soul", "config": {"workflow": definition}}}})
    return published


def test_published_workflow_keeps_local_definition_and_readable_commons(cutover):
    from tinyassets import daemon_server
    from tinyassets.broker.owner_reads import read

    data, store, *_ = cutover
    published = _published_workflow(data)
    report = migration.census(data)
    assert report["ready"], report["refusals"]
    assert report["tables"][".tinyassets.db/branch_definitions"]["published_per_owner"] == {
        "account-0001": 1}
    migration.apply(*cutover)
    migration.verify(data, store)
    tree = store / "300001"
    with sqlite3.connect(tree / ".tinyassets.db") as db:
        definition_id = db.execute(
            "SELECT branch_def_id FROM background_branch_bindings").fetchone()[0]
        assert db.execute("SELECT COUNT(*) FROM branch_definitions").fetchone() == (1,)
    local = daemon_server.get_branch_definition(tree, branch_def_id=definition_id)
    assert local["name"] == "Published workflow" and local["published"]
    public = read(data, "bob", {"op": "BOX_READ", "query": "definition", "args": {
        "definition_id": published["agent_definition_id"], "include_catalogue": False}})
    assert public["components"]["identity"]["config"]["workflow"]["branch_def_id"] == definition_id
    with sqlite3.connect(data / ".published/.tinyassets.db") as db:
        assert db.execute("SELECT branch_def_id FROM branch_definitions").fetchall() == [
            (definition_id,)]
    receipt = json.loads((data / ".owner-cutover.json").read_text())
    assert receipt["report"]["workflow_definitions"] == {
        "account-0001": {"instances": 1, "resolved": 1},
        "account-0002": {"instances": 0, "resolved": 0}}


@pytest.mark.parametrize("missing", ["source", "extracted"])
def test_apply_refuses_workflow_instance_without_local_definition(cutover, monkeypatch, missing):
    data, store, *_ = cutover
    _published_workflow(data)
    if missing == "source":
        with sqlite3.connect(data / ".tinyassets.db") as db:
            db.execute("DELETE FROM branch_definitions")
    else:
        original = migration._build_databases

        def lose_definition(*args):
            result = original(*args)
            with sqlite3.connect(store / "300001/.tinyassets.db") as db:
                db.execute("DELETE FROM branch_definitions")
            return result

        monkeypatch.setattr(migration, "_build_databases", lose_definition)
    message = ("unresolved_workflow_instance" if missing == "source"
               else "instance has no local definition")
    with pytest.raises(RuntimeError, match=message):
        migration.apply(*cutover)
    if missing == "extracted":
        assert json.loads((data / ".owner-cutover.json").read_text())["phase"] == "build"
    else:
        assert not (data / ".owner-cutover.json").exists()
    assert (data / "a1/notes.txt").is_file()


@pytest.mark.parametrize("retired_at", ["2026-09-01T00:00:00Z", "", None])
@pytest.mark.parametrize("definition", ["missing", None, ""])
def test_automation_missing_definition_is_history_only_when_retired(
        cutover, retired_at, definition):
    data, store, *_ = cutover
    _sql(data / ".automations.db", """
        CREATE TABLE automations (automation_id TEXT, universe_id TEXT,
                                  branch_def_id TEXT, retired_at TEXT);
        CREATE TABLE automation_attempts (automation_id TEXT, result TEXT);
        INSERT INTO automation_attempts VALUES ('old', 'synthetic run history');
    """)
    with sqlite3.connect(data / ".automations.db") as db:
        db.execute("INSERT INTO automations VALUES ('old','a1',?,?)",
                   (definition, retired_at))
    retired = bool(retired_at)
    report = migration.census(data)
    assert report["ready"] is retired
    if not retired:
        assert report["refusals"]["unresolved_workflow_instance"] == 1
        with pytest.raises(RuntimeError, match="unresolved_workflow_instance"):
            migration.apply(*cutover)
        return
    assert report["workflow_history"]["account-0001"][
        "automations with non-empty retired_at kept as history"] == 1
    migration.apply(*cutover)
    migration.verify(data, store)
    with sqlite3.connect(store / "300001/.automations.db") as db:
        assert db.execute("SELECT * FROM automations").fetchall() == [
            ("old", "a1", definition, retired_at)]
        assert db.execute("SELECT * FROM automation_attempts").fetchall() == [
            ("old", "synthetic run history")]


@pytest.fixture
def cutover(source, tmp_path, monkeypatch):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip(
            "requires linux_oracle.py --as-root; owner=c6-owner-box runs-in=linux_oracle.py"
        )
    from tinyassets.storage.outbound_connections import _SCHEMA

    with closing(sqlite3.connect(source / "outbound.db")) as db, db:
        records = db.execute("SELECT destination,owner_user_id FROM outbound_connections"
                             ).fetchall()
        db.execute("DROP TABLE outbound_connections")
        db.executescript(_SCHEMA)
        for destination, owner in records:
            db.execute("INSERT INTO outbound_connections (connection_id,owner_user_id,"
                       "connection_class,scopes_json,provider,destination,credential_ref) "
                       "VALUES (?,?,'http','[]','synthetic',?,'synthetic')",
                       (owner + "-legacy", owner, destination))
    for center in ('a1', 'a2', 'b1', 'b2'):
        os.chown(source / center, 1001, 300001 if center[0] == 'a' else 300002)
    destination = tmp_path / 'volume/_data'
    destination.parent.mkdir()
    source.rename(destination)
    source = destination
    store = tmp_path / 'owners'
    store.mkdir()
    # XFS mount/quota only: SQLite, rsync, permissions, journal and publication
    # are the real implementations. Real-mount proof belongs to the lead.
    monkeypatch.setattr(migration, '_mounted', lambda *_: None)
    monkeypatch.setattr(migration, '_xfs_quota', lambda *_: 'synthetic quota venue')
    monkeypatch.setattr(migration, 'verify_tree', lambda *_: None)
    monkeypatch.setattr(migration, 'verify_owner', lambda *_: None)
    data_backup = tmp_path / 'tinyassets-snapshot-2026-10-17T00-00-00Z.tar.gz'
    box_backup = tmp_path / 'tinyassets-box-store-2026-10-17T00-00-00Z.tar.gz'
    for path, tree, name in ((data_backup, source, '_data'), (box_backup, store, 'owners')):
        with tarfile.open(path, 'w:gz') as archive:
            archive.add(tree, arcname=name)
        _archive(path, path.read_bytes())
    return source, store, data_backup, box_backup


@pytest.mark.parametrize("legacy_scope", ["a1", "missing", None])
def test_knowledge_stores_move_verbatim_with_counted_legacy_disposition(cutover, legacy_scope):
    data, store, *_ = cutover
    originals = {}
    for relative in ("a1/knowledge.db", "a1/private/knowledge.sqlite3"):
        _sql(data / relative, "CREATE TABLE facts (body BLOB); "
             "INSERT INTO facts VALUES (X'001122ff');")
        originals[relative] = (data / relative).read_bytes()
    for relative in ("a1/lancedb/facts.lance/data/fragment.lance",
                     "a1/lancedb/facts.lance/_versions/1.manifest"):
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic vector bytes\x00\xff")
        originals[relative] = path.read_bytes()
    _sql(data / "knowledge.db", "CREATE TABLE facts (universe_id TEXT, text TEXT);")
    with sqlite3.connect(data / "knowledge.db") as db:
        db.execute("INSERT INTO facts VALUES (?, 'legacy fact')", (legacy_scope,))
    legacy = (data / "knowledge.db").read_bytes()
    report = migration.census(data)
    assert report["ready"], report["refusals"]
    if legacy_scope == "a1":
        originals["knowledge.db"] = legacy
    else:
        assert report["loss_summary"]["unattributed"]["no_owner_key"]["knowledge stores"] == {
            "class": "dropped", "rows": 0, "files": 1, "bytes": len(legacy)}
    assert report["knowledge_stores"]["account-0001"] == {
        "description": "knowledge stores", "files": len(originals),
        "bytes": sum(map(len, originals.values()))}
    migration.apply(*cutover)
    migration.verify(data, store)
    for relative, content in originals.items():
        assert (store / "300001" / relative).read_bytes() == content
        assert not (store / "300002" / relative).exists()
    assert not (data / "knowledge.db").exists()
    if legacy_scope != "a1":
        assert not (store / "300001/knowledge.db").exists()


@pytest.mark.parametrize('drop_blob', [False, True])
def test_round6_apply_resumes_hard_crash_between_verified_history_splits(cutover, drop_blob):
    data, store, *_ = cutover
    placements, fires, ledger = _round6_files(data, drop_blob=drop_blob)
    report = migration.census(data)
    assert report['ready'], report['refusals']
    before = _fingerprint(data)
    child = os.fork()
    if child == 0:
        os.environ['CUTOVER_CRASH_AT'] = 'after_scheduled_history_split'
        migration.apply(*cutover)
        os._exit(0)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 137
    assert (store / '300001/.scheduler_fires.jsonl').is_file()
    assert not (store / '300001/ledger.json').exists()
    assert json.loads((data / '.owner-cutover.json').read_text())['phase'] == 'build'
    for relative in placements:
        assert _fingerprint(data)[relative] == before[relative]
    migration.apply(*cutover)
    migration.verify(data, store)
    receipt = json.loads((data / '.owner-cutover.json').read_text())
    assert receipt['report']['history_splits'] == report['history_splits']
    for uid, fire_indices, ledger_indices in ((300001, (0, 2), (0, 2)), (300002, (1,), (1,))):
        tree = store / str(uid)
        assert [json.loads(line) for line in (tree / '.scheduler_fires.jsonl').read_text(
            ).splitlines()] == [fires[i] for i in fire_indices]
        assert json.loads((tree / 'ledger.json').read_text()) == [ledger[i] for i in ledger_indices]
        for name in ('.scheduler_fires.jsonl', 'ledger.json'):
            assert (tree / name).stat().st_uid == uid
        blob = tree / '.run-file-custody/synthetic.body'
        assert blob.exists() == (uid == 300001 and not drop_blob)
        if blob.exists():
            assert blob.read_text() == 'synthetic-private-synthetic.body'
        for relative, placement in placements.items():
            if placement in {'broker', 'router', 'regenerated', 'dropped'}:
                assert not (tree / relative).exists()
    for relative, placement in placements.items():
        if placement in {'broker', 'router'}:
            assert (data / relative).read_bytes() == ('synthetic-private-' +
                                                    relative.rsplit('/', 1)[-1]).encode()
            assert (data / relative).stat().st_uid == (1002 if placement == 'broker' else 1001)
            assert (data / relative).stat().st_mode & 0o777 == 0o600
        else:
            assert not (data / relative).exists()
    for name, summary in report['history_splits'].items():
        actual = sum(len(migration._history_entries(store / str(uid) / name))
                     for uid in (300001, 300002))
        assert actual + summary['dropped'] == summary['total']
    published = (_fingerprint(data), _fingerprint(store))
    migration.apply(*cutover)
    assert (_fingerprint(data), _fingerprint(store)) == published


@pytest.mark.parametrize('crash', ['none', 'copy', 'publish', 'cleanup'])
def test_apply_splits_databases_seeds_stores_and_resumes(cutover, monkeypatch, crash):
    data, store, *_ = cutover
    _sql(data / '.owner_leases.db', """
        CREATE TABLE owner_lease (fence INTEGER);
        INSERT INTO owner_lease VALUES (93);
    """)
    _sql(data / '.tinyassets.db', """
        CREATE TABLE universes (id TEXT);
        INSERT INTO universes VALUES ('a1'), ('never-admitted');
        CREATE TABLE external_agent_job_receipts (owner_user_id TEXT);
        INSERT INTO external_agent_job_receipts VALUES ('alice');
        CREATE TABLE author_definitions (id TEXT);
        INSERT INTO author_definitions VALUES ('public-definition');
        CREATE TABLE user_accounts (id TEXT);
        INSERT INTO user_accounts VALUES ('router-identity');
    """)
    (data / 'never-admitted').mkdir()
    (data / 'never-admitted/old').write_text('synthetic discarded file')
    _sql(data / '.runs.db', """
        CREATE TABLE runs (run_id TEXT, owner_user_id TEXT, queue_universe_id TEXT, thread_id TEXT);
        INSERT INTO runs VALUES ('run-a','alice','a1','thread-a'),
            ('run-b','bob','b1','thread-b'), ('legacy',NULL,NULL,'thread-old'),
            ('unadmitted',NULL,'never-admitted','thread-unadmitted');
        CREATE TABLE run_events (run_id TEXT, event TEXT);
        INSERT INTO run_events VALUES ('run-a','history-a'), ('run-b','history-b'),
                                      ('legacy','old'), ('unadmitted','old');
    """)
    _sql(data / '.langgraph_runs.db', """
        CREATE TABLE checkpoints (thread_id TEXT, payload TEXT);
        INSERT INTO checkpoints VALUES ('thread-a','a'), ('thread-b','b'), ('thread-old','old');
    """)
    _sql(data / '.runtime/owner-sessions.db', """
        CREATE TABLE approval_handoffs (id TEXT);
        INSERT INTO approval_handoffs VALUES ('router-handoff');
    """)
    (data / 'runs').mkdir()
    (data / 'runs/run-a.jsonl').write_text('owned transcript')
    (data / 'runs/legacy.jsonl').write_text('dropped transcript')
    home = data / '.platform/owners/300001/agent-home'
    home.mkdir(parents=True)
    (home / 'session').write_text('retired cli home')
    for relative in ('a1/.credential-vault.json',
                     'lancedb/index', '.runtime/stale-pid'):
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('legacy-secret-or-runtime-must-not-copy')
    vector = data / 'a1/lancedb/index'
    vector.parent.mkdir(parents=True, exist_ok=True)
    vector.write_bytes(b'owner-vector-must-copy-verbatim')
    _sql(data / '.outside-client-authority.sqlite3', """
        CREATE TABLE outside_switch (id INTEGER PRIMARY KEY, enabled INTEGER);
        INSERT INTO outside_switch VALUES (1,1);
    """)
    before = migration.census(data)
    assert before['ready'], before['refusals']
    if crash != 'none':
        name = {'copy': '_copy', 'publish': '_publish_cutover', 'cleanup': '_remove_tree'}[crash]
        original = getattr(migration, name)
        fired = False

        def interrupt(*args, **kwargs):
            nonlocal fired
            result = original(*args, **kwargs)
            if not fired and (crash != 'cleanup' or args[0] == data / 'a1'):
                fired = True
                raise OSError('synthetic interruption')
            return result

        monkeypatch.setattr(migration, name, interrupt)
        with pytest.raises(OSError, match='synthetic interruption'):
            migration.apply(*cutover)
        assert fired
        monkeypatch.setattr(migration, name, original)
        if crash == 'copy':
            # Residue from a hard process death, beyond Python exception cleanup.
            (store / '300001/a1/.interrupted-copy').write_text('partial output')
            (store / '300001/.tinyassets.db-journal').write_bytes(b'incomplete journal')
            for filename in ('.layout.json.99999.tmp', '.owner-cutover.json.99999.tmp'):
                residue = data / filename
                residue.write_text('{')
                residue.chmod(0o600)
    migration.apply(*cutover)
    assert not list(data.glob('*.99999.tmp'))
    assert not (store / '300001/a1/.interrupted-copy').exists()
    marker = json.loads((data / '.layout.json').read_text())
    assert marker['owner_daemon'] == 1
    assert marker['state'] == 'stable'
    assert marker['box_layout'] == 'owner-box'
    identity = data / '.broker/state/owner-identities.db'
    assert identity.stat().st_uid == 1002
    assert identity.stat().st_mode & 0o777 == 0o600
    assert identity.parent.stat().st_mode & 0o777 == 0o700
    with closing(sqlite3.connect(data / '.broker/state/owner-identities.db')) as db:
        assert db.execute('SELECT * FROM owner_box_secrets').fetchall() == []
        assert db.execute('SELECT * FROM owner_box_revocations').fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match='permanent'):
            db.execute("UPDATE owner_identities SET machine_id=300009 WHERE principal='alice'")
    for owner, uid in [('alice', 300001), ('bob', 300002)]:
        root = store / str(uid)
        assert root.stat().st_uid == uid
        with closing(sqlite3.connect(root / '.tinyassets.db')) as db:
            assert db.execute('SELECT DISTINCT owner_user_id FROM agent_turns').fetchall() == [
                (owner,)]
            assert db.execute('SELECT COUNT(*) FROM agent_turns').fetchone()[0] == 2
            assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        with closing(sqlite3.connect(root / '.owner_leases.db')) as db:
            assert db.execute('SELECT COUNT(*) FROM owner_lease').fetchone()[0] == 0
        with closing(sqlite3.connect(root / '.runs.db')) as db:
            assert db.execute('SELECT owner_user_id FROM runs').fetchall() == [(owner,)]
            assert db.execute('SELECT COUNT(*) FROM run_events').fetchone()[0] == 1
        with closing(sqlite3.connect(root / '.langgraph_runs.db')) as db:
            assert db.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0] == 1
        assert len(list(root.glob('*/notes.txt'))) == 2
        assert not (root / '.agent-home').exists()
        assert all(b'secret-must-not-appear' not in p.read_bytes()
                   for p in root.rglob('*') if p.is_file())
    for center in ('a1', 'a2', 'b1', 'b2', 'never-admitted'):
        assert not (data / center).exists()
    with closing(sqlite3.connect(data / '.tinyassets.db')) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [
            ('user_accounts',)]
        assert db.execute('SELECT id FROM user_accounts').fetchall() == [('router-identity',)]
    assert (store / '300001/runs/run-a.jsonl').read_text() == 'owned transcript'
    assert not (data / 'runs').exists()
    assert not home.exists()
    assert before['losses']['account-0001']['files'][
        '.platform/owners/<owner>/agent-home/<file>'] == 1
    assert before['credential_custody']['outside_broker_files_dropped'] == 1
    for tree in (data, store):
        assert all(b'legacy-secret-or-runtime-must-not-copy' not in p.read_bytes()
                   for p in tree.rglob('*') if p.is_file())
    assert (store / '300001/a1/lancedb/index').read_bytes() == b'owner-vector-must-copy-verbatim'
    with closing(sqlite3.connect(data / '.outside-client-authority.sqlite3')) as db:
        assert db.execute('SELECT * FROM outside_switch').fetchall() == [(1, 1)]
    assert before['tables']['.runs.db/runs']['dropped'] == 2
    assert before['tables']['.langgraph_runs.db/checkpoints']['dropped'] == 1
    from tinyassets.broker.owner_reads import Plans

    assert Plans(data).read('alice')['tier'] == 'paid'
    assert Plans(data).read('bob')['tier'] == 'free'
    with closing(sqlite3.connect(data / '.hook-owners.sqlite3')) as db:
        assert db.execute('SELECT * FROM hook_owners ORDER BY owner').fetchall() == [
            ('hash-a', 'alice'), ('hash-b', 'bob')]
    with closing(sqlite3.connect(data / '.published/.tinyassets.db')) as db:
        assert db.execute('SELECT id FROM author_definitions').fetchall() == [
            ('public-definition',)]
    for path in (data / '.platform/credential-vaults').rglob('.credential-vault.json'):
        assert path.stat().st_uid == 1002
        assert path.stat().st_mode & 0o777 == 0o600
        records = json.loads(path.read_text())['credentials']
        assert [r['credential_type'] for r in records] == ['http']
    after = _fingerprint(data), _fingerprint(store)
    migration.apply(*cutover)
    assert (_fingerprint(data), _fingerprint(store)) == after


def test_refused_census_makes_no_cutover_writes(cutover):
    data, store, *_ = cutover
    _sql(data / 'orphan/.subscription_state.db', """
        CREATE TABLE subscription_meta (key TEXT, value TEXT);
        INSERT INTO subscription_meta VALUES ('tier','paid');
    """)
    before = _fingerprint(data), _fingerprint(store)
    with pytest.raises(RuntimeError, match='census refused'):
        migration.apply(*cutover)
    assert (_fingerprint(data), _fingerprint(store)) == before


@pytest.mark.parametrize('changed', ['owner', 'shared', 'marker'])
def test_final_verify_refuses_changed_committed_output(cutover, changed):
    data, store, *_ = cutover
    migration.apply(*cutover)
    migration.verify(data, store)
    if changed == 'owner':
        (store / '300001/a1/notes.txt').write_text('changed owner output')
    elif changed == 'shared':
        _sql(data / '.broker/plans.db', "UPDATE plans SET tier='paid' WHERE owner='bob'")
    else:
        marker = json.loads((data / '.layout.json').read_text())
        marker['owner_daemon'] = 0
        (data / '.layout.json').write_text(json.dumps(marker))
    before = _fingerprint(data), _fingerprint(store)
    with pytest.raises(RuntimeError, match='manifest changed|not complete'):
        migration.verify(data, store)
    assert (_fingerprint(data), _fingerprint(store)) == before


@pytest.mark.parametrize("journal", ["intact", "missing", "corrupt"])
def test_completed_apply_restores_paired_owner_split(cutover, tmp_path, journal):
    data, store, *_ = cutover
    before = _fingerprint(data)
    migration.apply(*cutover)
    assert not (data / 'a1').exists()
    if journal == 'missing':
        (data / '.boxhost/boxhost.db').unlink()
    elif journal == 'corrupt':
        (data / '.boxhost/boxhost.db').write_bytes(b'corrupt live journal')
    result = _restore_paired(cutover, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _fingerprint(data) == before
    assert not any(path.name.isdecimal() for path in store.iterdir())
    assert list(store.glob('.tinyassets-box-restore-old.*/300001/a1/notes.txt'))


@pytest.mark.parametrize("authority", ["router", "revocation"])
def test_disaster_restore_keeps_erasure_and_destroys_restored_tree(
    cutover, tmp_path, authority,
):
    import runpy

    from tests.test_owner_cutover_restore import REPO
    from tinyassets.erasure_ledger import ErasureLedger

    data, store, data_archive, box_archive = cutover
    migration.apply(*cutover)
    for path, tree, name in ((data_archive, data, "_data"), (box_archive, store, "owners")):
        with tarfile.open(path, "w:gz") as archive:
            archive.add(tree, arcname=name)
        _archive(path, path.read_bytes())
    runpy.run_path(str(REPO / "deploy/backup-pair.py"))["create"](
        data_archive, box_archive, tmp_path / "tinyassets-pair-2026-10-17T00-00-00Z.json")
    # Local sweep succeeded, broker retirement/custody is still unavailable.
    (store / "300001/a1/notes.txt").unlink()
    if authority == "router":
        ErasureLedger(data).add("alice", {"unfinished_phases": ["owner_tree", "broker_egress"]})
    else:
        _sql(data / ".broker/state/owner-identities.db",
             "INSERT INTO owner_box_revocations VALUES ('alice',1)")
    result = _restore_paired(cutover, tmp_path, disaster=True)
    assert result.returncode == 6, result.stdout + result.stderr
    assert not (store / "300001").exists()
    assert (store / "300002/b1/notes.txt").exists()
    with sqlite3.connect(data / ".broker/state/owner-identities.db") as db:
        assert db.execute("SELECT deleted FROM owner_box_revocations WHERE principal='alice'"
                          ).fetchone() == (1,)
    if authority == "router":
        assert ErasureLedger(data).contains("alice")
    assert list(store.glob(".tinyassets-box-restore-old.*"))


def _restore_paired(cutover, tmp_path, *, disaster=False):
    from tests.test_owner_cutover_restore import REPO, _shim

    data, store, data_archive, box_archive = cutover
    binary = tmp_path / 'bin'
    binary.mkdir()
    _shim(binary, 'docker', 'case "$1 $2" in\n"volume inspect") echo "$TEST_VOLUME" ;;\n'
          '"ps -q") : ;;\n*) exit 79 ;;\nesac\n')
    _shim(binary, 'systemctl', ':\n')
    _shim(binary, 'findmnt', 'if [[ "${@: -1}" == FSTYPE ]]; then echo xfs; '
          'else echo rw,prjquota,nodiscard; fi\n')
    _shim(binary, 'xfs_quota', 'exit 79\n')
    # Only the absent host runtime is modeled: the real restore census still
    # checks all receipt identities, tables and modes before asking for its list.
    wrapper = binary / 'python3'
    wrapper.write_text(f"#!{sys.executable}\n" + """import os, runpy, subprocess, sys
if len(sys.argv) > 1 and sys.argv[1].endswith('/box-store-quotas.py'):
    sys.path.insert(0, os.path.dirname(os.path.dirname(sys.argv[1])))
    import deploy.box_project_readback as readback
    readback.verify_owner = lambda *args: None
    readback.verify_tree = lambda *args: None
    real_run = subprocess.run
    def run(argv, **kwargs):
        if argv[0] == 'xfs_quota':
            return subprocess.CompletedProcess(argv, 0, '', '')
        if argv[0] == '/opt/tinyassets/boxhostd/current/runsc':
            assert argv[-3:] == ['list', '--format=json'] or argv[-2:] == ['list', '--format=json']
            return subprocess.CompletedProcess(argv, 0, '[]', '')
        return real_run(argv, **kwargs)
    subprocess.run = run
    sys.argv = sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name='__main__')
else:
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
""")
    wrapper.chmod(0o755)
    result = subprocess.run(
        ['bash', str(REPO / 'deploy/backup-restore.sh'),
         *(['--disaster-restore', '--acknowledge-unrecoverable-facts'] if disaster else [])],
        env={**os.environ, 'PATH': f"{binary}:{os.environ['PATH']}",
             'BACKUP_FILE': str(data_archive), 'BOX_BACKUP_FILE': str(box_archive),
             'BACKUP_VOLUME': 'synthetic-cutover', 'BOX_STORE_DIR': str(store),
             'BACKUP_LOG': str(tmp_path / 'restore.log'), 'TEST_VOLUME': str(data)},
        text=True, capture_output=True, check=False)
    return result


def test_actual_interrupted_migration_allows_normal_rollback(cutover, tmp_path, monkeypatch):
    before = _fingerprint(cutover[0])

    def crash(*args):
        raise OSError("crash after migration marker")

    monkeypatch.setattr(migration, "_copy", crash)
    with pytest.raises(OSError, match="crash after migration marker"):
        migration.apply(*cutover)
    result = _restore_paired(cutover, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _fingerprint(cutover[0]) == before


def test_crash_resume_rejects_a_different_snapshot_pair(cutover, monkeypatch):
    original = migration._copy
    def crash(*args):
        original(*args)
        raise OSError('interrupted copy')
    monkeypatch.setattr(migration, '_copy', crash)
    with pytest.raises(OSError, match='interrupted copy'):
        migration.apply(*cutover)
    _archive(cutover[2], b'a different synthetic snapshot')
    monkeypatch.setattr(migration, '_copy', original)
    with pytest.raises(RuntimeError, match='resume journal'):
        migration.apply(*cutover)
    assert json.loads((cutover[0] / '.layout.json').read_text())['state'] == 'migrating'
    assert (cutover[0] / 'a1').is_dir()


def test_publication_refuses_changed_verified_owner_tree(cutover, monkeypatch):
    publish = migration._publish_cutover
    def crash(*args):
        raise OSError('before publication')
    monkeypatch.setattr(migration, '_publish_cutover', crash)
    with pytest.raises(OSError, match='before publication'):
        migration.apply(*cutover)
    monkeypatch.setattr(migration, '_publish_cutover', publish)
    (cutover[1] / '300001/a1/notes.txt').write_text('changed after verification')
    with pytest.raises(RuntimeError, match='owner manifest changed'):
        migration.apply(*cutover)
    assert (cutover[0] / 'a1/notes.txt').read_text() == 'private history for a1'
    assert json.loads((cutover[0] / '.layout.json').read_text())['state'] == 'migrating'


def test_shared_doors_authenticate_migrated_keys_and_hooks(cutover, monkeypatch):
    import hashlib

    from tinyassets import api_keys
    from tinyassets.auth.middleware import router_authentication
    from tinyassets.router_credentials import HookOwners

    data = cutover[0]
    keys = {owner: api_keys.ApiKeyStore(data).mint(
        owner, name='synthetic migration key', scopes=[], authority=None)['key']
        for owner in ('alice', 'bob')}
    hooks = {'alice': 'synthetic-hook-a', 'bob': 'synthetic-hook-b'}
    with closing(sqlite3.connect(data / '.tinyassets.db')) as db, db:
        db.execute('ALTER TABLE webhook_hooks ADD COLUMN revoked_at REAL')
        db.execute('INSERT INTO webhook_hooks VALUES (?,?,?,0)',
                   (hashlib.sha256(b'revoked-hook').hexdigest(), 'a1', 'alice'))
        for owner, token in hooks.items():
            db.execute('UPDATE webhook_hooks SET token_hash=? WHERE owner_principal_id=? '
                       'AND revoked_at IS NULL',
                       (hashlib.sha256(token.encode()).hexdigest(), owner))
    migration.apply(*cutover)
    monkeypatch.setattr(api_keys, 'current_store', lambda: api_keys.ApiKeyStore(data))
    context = router_authentication.set(True)
    try:
        for owner, key in keys.items():
            assert api_keys.resolve_identity(key).user_id == owner
            assert HookOwners(data).resolve(hooks[owner]) == owner
        assert api_keys.resolve_identity('invalid-key') is None
        assert HookOwners(data).resolve('invalid-hook') is None
        assert HookOwners(data).resolve('revoked-hook') is None
    finally:
        router_authentication.reset(context)


@pytest.mark.parametrize('mutation', ['shared', 'owner'])
def test_pretraffic_restore_ignores_migration_and_canary_writes(cutover, tmp_path, mutation):
    import runpy

    from tests.test_owner_cutover_restore import REPO

    data, store, archive, _ = cutover
    restored = tmp_path / 'restored'
    restored.mkdir()
    with tarfile.open(archive, 'r:gz') as source_archive:
        source_archive.extractall(restored, filter='fully_trusted')
    staged = restored / '_data'
    migration.apply(*cutover)
    helper = runpy.run_path(str(REPO / 'deploy/box-store-quotas.py'))
    fence = helper['fence_restore']
    restored_trees = tmp_path / 'restored-trees'
    restored_trees.mkdir()
    if mutation == 'shared':
        _sql(data / '.broker/plans.db', "UPDATE plans SET tier='paid' WHERE owner='bob'")
    elif mutation == 'owner':
        (store / '300001/a1/notes.txt').write_text('post-cutover owner write')
    fence(data, staged, cutover_store=store, restored_store=restored_trees)
    assert (staged / 'a1/notes.txt').read_text() == 'private history for a1'
    assert list(restored_trees.iterdir()) == []
    assert (store / '300001/a1').is_dir()
    assert not (data / 'a1').exists()


def test_seeded_tombstones_block_broker_custody_without_old_metadata(cutover):
    import hashlib

    from tinyassets.broker.vault import check_not_deleted, metadata_connection

    data = cutover[0]
    digest = hashlib.sha256(b'retired-account').hexdigest()
    _sql(data / '.tinyassets.db', f"""
        CREATE TABLE deleted_principals (founder_sub TEXT PRIMARY KEY, deleted_at REAL);
        INSERT INTO deleted_principals VALUES ('{digest}', 123);
    """)
    migration.apply(*cutover)
    assert not (data / '.tinyassets.db').exists()
    with closing(metadata_connection(data)) as db:
        assert [row[1] for row in db.execute('PRAGMA table_info(deleted_principals)')] == ['digest']
        assert db.execute('SELECT digest FROM deleted_principals').fetchall() == [(digest,)]
        with pytest.raises(PermissionError, match='deleted'):
            check_not_deleted(db, 'retired-account')
        check_not_deleted(db, 'bob')


def test_normal_restore_refuses_after_traffic(cutover, tmp_path):
    import runpy

    from tests.test_owner_cutover_restore import REPO

    data, store, archive, _ = cutover
    staged_parent = tmp_path / 'staged'
    staged_parent.mkdir()
    with tarfile.open(archive, 'r:gz') as backup:
        backup.extractall(staged_parent, filter='fully_trusted')
    staged = staged_parent / '_data'
    migration.apply(*cutover)
    (store / '300001/a1/notes.txt').write_text('accepted owner write loss')
    from tinyassets.router_traffic import open_traffic

    open_traffic(data)
    before = _fingerprint(staged)
    helper = runpy.run_path(str(REPO / 'deploy/box-store-quotas.py'))
    restored_trees = tmp_path / 'restored-trees'
    restored_trees.mkdir()
    with pytest.raises(ValueError):
        helper['fence_restore'](data, staged, cutover_store=store,
                                restored_store=restored_trees)
    assert _fingerprint(staged) == before
    assert list(restored_trees.iterdir()) == []


def test_migrated_owner_processes_serve_history_requests_and_pause(
    cutover, monkeypatch, broker, tmp_path,  # noqa: F811
):
    import base64
    import gc
    import tempfile
    import time
    from pathlib import Path
    from types import SimpleNamespace

    import httpx

    from tests.support.owner_daemon_launcher import ProcessRuntime
    from tinyassets.boxes import relay as relay_module
    from tinyassets.boxes.relay import OwnerRelays
    from tinyassets.broker.owner_identities import OwnerIdentities
    from tinyassets.broker.server import BOX_HOST
    from tinyassets.conversation_store import record_turn
    from tinyassets.daemon_server import grant_universe_ownership, set_founder_home
    from tinyassets.storage.pending_requests import create_request

    data, store, *_ = cutover
    identity_path = data / '.broker/state/owner-identities.db'
    identity_path.parent.chmod(0o700)
    identity_path.chmod(0o600)
    identities = OwnerIdentities(identity_path)
    # Replace only this synthetic fixture's abbreviated main schema with the
    # application's actual schema before creating history and requests.
    (data / '.tinyassets.db').unlink()
    monkeypatch.setenv('TINYASSETS_DATA_DIR', str(data))
    requests = {}
    for owner, centers in [('alice', ('a1', 'a2')), ('bob', ('b1', 'b2'))]:
        for center in centers:
            grant_universe_ownership(data, universe_id=center, owner_id=owner)
            folder = data / center
            (folder / 'soul.md').write_text('# Synthetic owner home')
            (folder / '.pause').write_text('paused before cutover')
            record_turn(folder, f'principal:{owner}', 'founder', f'{owner} history in {center}')
            request = create_request(folder, kind='Question', title=f'{owner} question in {center}',
                                     body='Synthetic pending question', fields=[],
                                     action={'type': 'answer'}, dedupe_key=f'ask-{center}')
            assert request is not None
            requests[center] = request['request_id']
        set_founder_home(data, founder_sub=owner, universe_id=centers[0], platform_generated=True)
    report = migration.census(data)
    assert report['ready'], report['refusals']
    # The paired backup must contain the actual seeded pre-cutover generation.
    # Seeding uses the production initializers in this process. Release their
    # cyclic SQLite handles before modeling the stopped-writer snapshot.
    gc.collect()
    before = _fingerprint(data)
    with tarfile.open(cutover[2], 'w:gz') as archive:
        archive.add(data, arcname='_data')
    _archive(cutover[2], cutover[2].read_bytes())
    migration.apply(*cutover)
    assert json.loads((data / '.owner-cutover.json').read_text())['source_manifest'] == before
    # The real broker and relays run under the rig's UID; production pins the
    # peer to broker UID 1002. The migrated identity and plan stores are real.
    broker.server._roles = {os.getuid(): BOX_HOST}
    broker.server._data_root = data
    broker.server._owner_identities = identities
    monkeypatch.setattr(relay_module, 'peer', lambda sock: (os.getpid(), 1002, 1002))
    roots = {300001: store / '300001', 300002: store / '300002'}
    with tempfile.TemporaryDirectory(prefix='ta-cutover-') as directory:
        run = Path(directory)
        relays = OwnerRelays(run / 'relay', SimpleNamespace(broker_socket=broker.path))
        runtime = ProcessRuntime(roots, module='tests.support.owner_cutover_process')
        try:
            for owner, uid in [('alice', 300001), ('bob', 300002)]:
                app = run / str(uid)
                app.mkdir()
                secret = app / 'secret'
                secret.write_text('s' * 43)
                secret.chmod(0o400)
                runtime.start(uid, uid, relay=relays.bind(owner, uid), app_dir=app,
                              secret_file=secret, memory_bytes=1024**3)
            for owner, uid, centers in [('alice', 300001, ('a1', 'a2')),
                                        ('bob', 300002, ('b1', 'b2'))]:
                socket = run / str(uid) / 'app.sock'
                deadline = time.monotonic() + 45
                while not socket.exists() and runtime.alive(uid) and time.monotonic() < deadline:
                    time.sleep(.05)
                if not socket.exists():
                    for log in runtime.logs:
                        log.seek(0)
                        print(log.read().decode())
                    pytest.fail('migrated owner daemon did not start')
                identity = base64.urlsafe_b64encode(json.dumps({
                    'user_id': owner, 'username': owner,
                    'capabilities': ['tinyassets.universe.write'],
                }).encode()).decode()
                with httpx.Client(transport=httpx.HTTPTransport(uds=str(socket)),
                                  base_url='http://localhost', timeout=30,
                                  headers={'x-owner': owner, 'x-box-secret': 's' * 43,
                                           'x-owner-identity': identity}) as client:
                    for center in centers:
                        status = client.post('/app/api/status', json={
                            'universe_id': center, 'include_conversation': True})
                        assert status.status_code == 200, status.text
                        document = status.json()
                        turns = document['recent_conversation']['turns']
                        assert [turn['text'] for turn in turns] == [
                            f'{owner} history in {center}']
                        pending = client.post('/app/api/read', json={
                            'target': 'pending_requests', 'graph_id': center})
                        assert pending.status_code == 200, pending.text
                        asks = pending.json()['pending']
                        assert requests[center] in [row['request_id'] for row in asks]
                        written = client.post('/app/test-cutover-write', json={'center': center})
                        assert written.status_code == 200, written.text
                        new_request = written.json()['request_id']
                        status = client.post('/app/api/status', json={
                            'universe_id': center, 'include_conversation': True})
                        assert status.status_code == 200, status.text
                        assert 'post-cutover chat' in [turn['text'] for turn in
                            status.json()['recent_conversation']['turns']]
                        pending = client.post('/app/api/read', json={
                            'target': 'pending_requests', 'graph_id': center})
                        assert pending.status_code == 200, pending.text
                        assert new_request in [row['request_id']
                                               for row in pending.json()['pending']]
                        pause = roots[uid] / center / '.pause'
                        assert pause.read_text() == 'paused before cutover'
                        actions = [('status', True), ('resume', False), ('pause', True)]
                        for action, paused in actions:
                            response = client.post('/app/test-cutover-control', json={
                                'center': center, 'action': action})
                            assert response.status_code == 200, response.text
                            result = response.json()
                            assert 'error' not in result['control'], result
                            expected_tier = 'paid' if owner == 'alice' else 'free'
                            assert result['plan']['tier'] == expected_tier
                            if action == 'status':
                                assert result['control']['is_paused'] is True
                            assert pause.exists() is paused
                    foreign = 'b1' if owner == 'alice' else 'a1'
                    denied = client.post('/app/api/status', json={
                        'universe_id': foreign, 'include_conversation': True})
                    assert 'history in ' + foreign not in denied.text
                    assert denied.json()['universe_exists'] is False
                    assert 'recent_conversation' not in denied.json()
            assert runtime.processes[300001].pid != runtime.processes[300002].pid
        finally:
            runtime.close()
            relays.close()
    assert _fingerprint(store / '300001') != json.loads(
        (data / '.owner-cutover.json').read_text())['owner_manifests']['300001']
    from tinyassets.router_traffic import open_traffic

    open_traffic(data)
    live_before = _fingerprint(data), _fingerprint(store)
    result = _restore_paired(cutover, tmp_path)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "traffic_opened_at" in result.stderr
    assert (_fingerprint(data), _fingerprint(store)) == live_before


def test_forward_cutover_preserves_ledger_and_reopens_copied_home_work(cutover):
    from tinyassets.erasure_ledger import PHASES, ErasureLedger
    from tinyassets.restore_recovery import finish_erasures

    data, store, *_ = cutover
    ledger = ErasureLedger(data)
    ledger.add("alice")
    ledger.save_receipt("alice", {"completed_phases": list(PHASES)})
    assert ledger.receipt("alice")["home_removed"]
    assert migration.census(data)["ready"]
    migration.apply(*cutover)
    ledger = ErasureLedger(data)
    assert ledger.contains("alice")
    assert not ledger.receipt("alice")["home_removed"]
    assert (store / "300001").exists()
    finish_erasures(data)  # The actual boxhost startup boundary, before admission.
    assert not (store / "300001").exists()
    assert ledger.receipt("alice")["home_removed"]
    assert (store / "300002/b1/notes.txt").exists()


def test_cutover_imports_router_custody_and_host_erasure_facts(cutover):
    from tinyassets.boxes.state import BoxHostState
    from tinyassets.erasure_ledger import ErasureLedger, principal_digest

    data, *_ = cutover
    _sql(data / ".router-deleted-owners.sqlite3", "CREATE TABLE deleted_owners "
         "(owner TEXT PRIMARY KEY, deleted_at REAL, receipt TEXT);"
         "INSERT INTO deleted_owners VALUES ('router-only',1,NULL);")
    custody = data / ".broker/credential-custody.db"
    custody.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(custody) as db:
        db.execute("CREATE TABLE deleted_principals (digest TEXT PRIMARY KEY)")
        db.execute("INSERT INTO deleted_principals VALUES (?)", (principal_digest("custody-only"),))
    host = BoxHostState(data / ".boxhost/boxhost.db")
    host.begin_owner_destroy("host-only", "erase", 300003, "c")
    host.close()
    report = migration.census(data)
    assert report["ready"], report["refusals"]
    migration.apply(*cutover)
    ledger = ErasureLedger(data)
    assert all(ledger.contains(owner) for owner in ("router-only", "custody-only", "host-only"))
    assert not (data / ".router-deleted-owners.sqlite3").exists()
