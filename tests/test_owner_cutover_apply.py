"""Synthetic real-file cutover and crash resume; no external data fixtures."""

import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from contextlib import closing
from pathlib import Path

import pytest

from tests.support.migration_volumes import contents, install
from tests.test_box_layout_migration import _archive
from tests.test_broker_server import broker  # noqa: F401
from tests.test_owner_cutover_census import _fingerprint, _round6_files, _sql
from tests.test_owner_cutover_census import source as source
from tinyassets import box_layout_migration as migration


def test_only_deployment_can_change_the_published_admission_gate(cutover):
    from tinyassets.router_traffic import NAME

    data, *_ = cutover
    migration.apply(*cutover)
    gate = data / NAME
    assert not (data / '.restore-epoch').exists()
    assert gate.stat().st_uid == 0
    assert gate.stat().st_mode & 0o777 == 0o644
    assert data.stat().st_uid == 0 and data.stat().st_mode & 0o1000
    descriptor = os.open(data, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for uid in (1001, 1002):
            result = subprocess.run([sys.executable, '-c', """
import os, sys
directory, name = int(sys.argv[1]), sys.argv[2]
fd = os.open('.service-test', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=directory)
os.close(fd)
os.unlink('.service-test', dir_fd=directory)
for operation in (lambda: os.open(name, os.O_WRONLY | os.O_TRUNC, dir_fd=directory),
                  lambda: os.unlink(name, dir_fd=directory)):
    try:
        operation()
    except PermissionError:
        pass
    else:
        raise AssertionError('service changed deployment admission')
""", str(descriptor), NAME], user=uid, group=uid, extra_groups=[1001],
                                    pass_fds=(descriptor,), capture_output=True, text=True)
            assert result.returncode == 0, result.stderr
    finally:
        os.close(descriptor)
    assert json.loads(gate.read_text()) == {"ordinary": False}


def _admission_transport(data, broker, monkeypatch):  # noqa: F811
    """Serve migrated authority in the same-UID protocol venue."""
    from types import SimpleNamespace

    from tests.support.admission import Plans
    from tinyassets.broker import supervisor
    from tinyassets.broker.owner_identities import OwnerIdentities

    path = data / ".broker/state/owner-identities.db"
    # Production custody was asserted by apply. This fixture's broker is root.
    os.chown(path.parent, os.getuid(), os.getgid())
    os.chown(path, os.getuid(), os.getgid())
    broker.server._owner_identities = OwnerIdentities(path, plans=Plans(data))
    broker.server._data_root = data
    live = SimpleNamespace(socket_path=broker.path, fence=lambda: (
        broker.state["generation"], broker.state["token"]), verify_broker=lambda sock: None)
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: live)


def test_cutover_preserves_cancellation_mapping_and_resumes_payable_checkout(
    cutover, broker, monkeypatch,  # noqa: F811
):
    from tests.support.admission import Plans
    from tests.test_cutover_billing_deletion import attempt, legacy_attempt
    from tinyassets.billing.plan_action import _start_checkout

    data, store, *_ = cutover
    original = attempt()
    legacy_attempt(data, original)
    migration.apply(*cutover)
    plan = Plans(data)
    assert plan.read("alice")["customer_id"] == "cus_legacy"
    assert plan.read("alice")["subscription_id"] == "sub_legacy"
    assert json.loads(plan.read("bob")["attempt"]) == original
    assert not (data / "b1/.subscription_state.db").exists()
    assert not (contents(store) / "300002/b1/.subscription_state.db").exists()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(data))
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_synthetic")
    # Use the actual shared writer through the test broker transport.
    _admission_transport(data, broker, monkeypatch)
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
        assert not (contents(store) / str(uid) / "lancedb").exists()
    assert (contents(store) / "300001/knowledge.db").exists()


@pytest.mark.parametrize("protected", [False, True])
@pytest.mark.parametrize("wal", [False, True])
def test_cutover_repairs_only_named_indexes_and_records_verified_repair(cutover, protected, wal):
    from tests.test_cutover_index_integrity import corrupt_index

    data, store, *_ = cutover
    corrupt_index(data / ".tinyassets.db")
    if wal:
        # A stopped writer can leave committed WAL bytes and shared memory.
        subprocess.run([sys.executable, '-c', """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA journal_mode=WAL')
db.execute('PRAGMA user_version=1')
db.commit()
os._exit(0)
""", str(data / '.tinyassets.db')], check=True)
        assert (data / '.tinyassets.db-wal').stat().st_size > 0
        for suffix in ('-wal', '-shm'):
            os.chown(data / ('.tinyassets.db' + suffix), 1001, 1001)
    if protected:
        from tinyassets.cutover_control import protect_root

        assert Path('/proc/sys/fs/protected_regular').read_text().strip() == '2'
        os.chown(data / '.tinyassets.db', 1001, 1001)
        protect_root(data)
    assert migration.census(data)["ready"]
    migration.apply(*cutover)
    receipt = json.loads((data / ".owner-cutover.json").read_text())
    assert receipt["report"]["reindexed"] == ["reindexed: .tinyassets.db/drift"]
    for uid in (300001, 300002):
        with sqlite3.connect(contents(store) / str(uid) / ".tinyassets.db") as db:
            assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert db.execute("SELECT COUNT(*) FROM agent_turns INDEXED BY drift"
                              ).fetchone() == (2,)


@pytest.mark.parametrize('kind', ['regular', 'missing', 'symlink', 'hardlink', 'fifo'])
def test_sqlite_sidecar_custody_preserves_bytes_and_refuses_aliases(cutover, tmp_path, kind):
    data, *_ = cutover
    path = data / '.tinyassets.db'
    os.chown(path, 1001, 1001)
    wal, shm = data / '.tinyassets.db-wal', data / '.tinyassets.db-shm'
    wal.write_bytes(b'committed WAL bytes')
    os.chown(wal, 1001, 1001)
    wal.chmod(0o640)
    external = tmp_path / 'external'
    external.write_bytes(b'external bytes')
    os.chown(external, 1001, 1001)
    if kind == 'regular':
        shm.write_bytes(b'shared memory')
        os.chown(shm, 1001, 1001)
    elif kind == 'symlink':
        shm.symlink_to(external)
    elif kind == 'hardlink':
        os.link(external, shm)
    elif kind == 'fifo':
        os.mkfifo(shm)
    if kind in {'regular', 'missing'}:
        migration._claim_sqlite_sidecars(path)
        assert wal.stat().st_uid == 0
        if kind == 'regular':
            assert shm.stat().st_uid == 0
            assert shm.read_bytes() == b'shared memory'
    else:
        with pytest.raises((OSError, RuntimeError)):
            migration._claim_sqlite_sidecars(path)
        assert wal.stat().st_uid == 1001  # Validate both before changing either.
    assert wal.read_bytes() == b'committed WAL bytes'
    assert wal.stat().st_mode & 0o777 == 0o640
    assert path.stat().st_uid == external.stat().st_uid == 1001
    assert external.read_bytes() == b'external bytes'


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
    tree = contents(store) / "300001"
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
            with sqlite3.connect(contents(store) / "300001/.tinyassets.db") as db:
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
    with sqlite3.connect(contents(store) / "300001/.automations.db") as db:
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
    install(monkeypatch)
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
        assert (contents(store) / "300001" / relative).read_bytes() == content
        assert not (contents(store) / "300002" / relative).exists()
    assert not (data / "knowledge.db").exists()
    if legacy_scope != "a1":
        assert not (contents(store) / "300001/knowledge.db").exists()


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
    assert (contents(store) / '300001/.scheduler_fires.jsonl').is_file()
    assert not (contents(store) / '300001/ledger.json').exists()
    assert json.loads((data / '.owner-cutover.json').read_text())['phase'] == 'build'
    for relative in placements:
        assert _fingerprint(data)[relative] == before[relative]
    migration.apply(*cutover)
    migration.verify(data, store)
    receipt = json.loads((data / '.owner-cutover.json').read_text())
    assert receipt['report']['history_splits'] == report['history_splits']
    for uid, fire_indices, ledger_indices in ((300001, (0, 2), (0, 2)), (300002, (1,), (1,))):
        tree = contents(store) / str(uid)
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
        actual = sum(len(migration._history_entries(contents(store) / str(uid) / name))
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
            (contents(store) / '300001/a1/.interrupted-copy').write_text('partial output')
            (contents(store) / '300001/.tinyassets.db-journal').write_bytes(b'incomplete journal')
            for filename in ('.layout.json.99999.tmp', '.owner-cutover.json.99999.tmp'):
                residue = data / filename
                residue.write_text('{')
                residue.chmod(0o600)
    migration.apply(*cutover)
    assert not list(data.glob('*.99999.tmp'))
    assert not (contents(store) / '300001/a1/.interrupted-copy').exists()
    marker = json.loads((data / '.layout.json').read_text())
    assert marker['owner_daemon'] == 1
    assert marker['state'] == 'stable'
    assert marker['box_layout'] == 'owner-box'
    identity = data / '.broker/state/owner-identities.db'
    assert identity.stat().st_uid == 1002
    assert identity.stat().st_mode & 0o777 == 0o600
    assert identity.parent.stat().st_mode & 0o777 == 0o700
    with closing(sqlite3.connect(data / '.broker/state/owner-identities.db')) as db:
        secrets = dict(db.execute('SELECT * FROM owner_box_secrets'))
        assert set(secrets) == {'alice', 'bob'}
        assert len(set(secrets.values())) == 2
        assert all(len(secret) == 43 for secret in secrets.values())
        assert db.execute("SELECT name FROM sqlite_master "
                          "WHERE name='owner_box_revocations'").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match='permanent'):
            db.execute("UPDATE owner_identities SET machine_id=300009 WHERE principal='alice'")
    for owner, uid in [('alice', 300001), ('bob', 300002)]:
        root = contents(store) / str(uid)
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
    assert (contents(store) / '300001/runs/run-a.jsonl').read_text() == 'owned transcript'
    assert not (data / 'runs').exists()
    assert not home.exists()
    assert before['losses']['account-0001']['files'][
        '.platform/owners/<owner>/agent-home/<file>'] == 1
    assert before['credential_custody']['outside_broker_files_dropped'] == 1
    for tree in (data, store):
        assert all(b'legacy-secret-or-runtime-must-not-copy' not in p.read_bytes()
                   for p in tree.rglob('*') if p.is_file())
    assert (contents(store) / '300001/a1/lancedb/index').read_bytes() == (
        b'owner-vector-must-copy-verbatim')
    with closing(sqlite3.connect(data / '.outside-client-authority.sqlite3')) as db:
        assert db.execute('SELECT * FROM outside_switch').fetchall() == [(1, 1)]
    assert before['tables']['.runs.db/runs']['dropped'] == 2
    assert before['tables']['.langgraph_runs.db/checkpoints']['dropped'] == 1
    from tests.support.admission import Plans

    assert Plans(data).read('alice')['tier'] == 'paid'
    assert Plans(data).read('bob')['tier'] == 'free'
    with closing(sqlite3.connect(data / '.hook-owners.sqlite3')) as db:
        assert db.execute('SELECT * FROM hook_owners ORDER BY owner').fetchall() == [
            ('hash-a', 'alice'), ('hash-b', 'bob')]
    with closing(sqlite3.connect(data / '.published/.tinyassets.db')) as db:
        assert db.execute('SELECT id FROM author_definitions').fetchall() == [
            ('public-definition',)]
    assert not (data / '.platform/credential-vaults').exists()
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
        (contents(store) / '300001/a1/notes.txt').write_text('changed owner output')
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
        (data / '.boxhost').mkdir()
        # A missing legacy journal is irrelevant after cutover.
    elif journal == 'corrupt':
        (data / '.boxhost').mkdir()
        (data / '.boxhost/boxhost.db').write_bytes(b'corrupt live journal')
    result = _restore_old_layout(cutover, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert {k: v for k, v in _fingerprint(data).items()
            if k != '.erasure-ledger.sqlite3'} == before
    assert (store / '300001.img').exists()
    assert (contents(store) / '300001/a1/notes.txt').read_text() == 'private history for a1'






def test_actual_interrupted_migration_allows_normal_rollback(cutover, tmp_path, monkeypatch):
    before = _fingerprint(cutover[0])

    def crash(*args):
        raise OSError("crash after migration marker")

    monkeypatch.setattr(migration, "_copy", crash)
    with pytest.raises(OSError, match="crash after migration marker"):
        migration.apply(*cutover)
    result = _restore_old_layout(cutover, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert {k: v for k, v in _fingerprint(cutover[0]).items()
            if k != '.erasure-ledger.sqlite3'} == before


def test_owner_content_links_survive_migration_and_rollback(cutover, tmp_path):
    data, store, backup, _ = cutover
    home = data / "a1"
    (home / "shortcut").symlink_to("notes.txt")
    os.link(home / "notes.txt", home / "notes-copy.txt")
    with tarfile.open(backup, "w:gz") as archive:
        archive.add(data, arcname="_data")
    _archive(backup, backup.read_bytes())

    def assert_links(home):
        assert (home / "shortcut").readlink() == Path("notes.txt")
        assert (home / "shortcut").read_text() == "private history for a1"
        assert (home / "notes.txt").samefile(home / "notes-copy.txt")
        assert (home / "notes.txt").stat().st_nlink == 2

    migration.apply(*cutover)
    assert_links(contents(store) / "300001/a1")
    result = _restore_old_layout(cutover, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert_links(data / "a1")


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
    (contents(cutover[1]) / '300001/a1/notes.txt').write_text('changed after verification')
    with pytest.raises(RuntimeError, match='owner manifest changed'):
        migration.apply(*cutover)
    assert (cutover[0] / 'a1/notes.txt').read_text() == 'private history for a1'
    assert json.loads((cutover[0] / '.layout.json').read_text())['state'] == 'migrating'


def test_shared_doors_authenticate_migrated_keys_and_hooks(
    cutover, broker, monkeypatch,  # noqa: F811
):
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
    _admission_transport(data, broker, monkeypatch)
    # This functional reader runs as root; production's writer is UID1001.
    # Match this test writer for SQLite's journal ownership in the sticky root.
    os.chown(data / '.api-keys.sqlite3', os.getuid(), os.getgid())
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
    from tinyassets.cutover_restore import restore_old_layout
    data, store, archive, _ = cutover
    before = _fingerprint(data)
    migration.apply(*cutover)
    if mutation == 'shared':
        _sql(data / '.broker/plans.db', "UPDATE plans SET tier='paid' WHERE owner='bob'")
    elif mutation == 'owner':
        (contents(store) / '300001/a1/notes.txt').write_text('post-cutover owner write')
    staged = restore_old_layout(archive, tmp_path / 'restored')
    assert (staged / 'a1/notes.txt').read_text() == 'private history for a1'
    assert _fingerprint(staged) == before
    assert (contents(store) / '300001/a1').is_dir()
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
    # A digest-only fence cannot prove pending external cleanup. Supply the
    # historical completion receipt, which the cutover must preserve off-host.
    from tinyassets.cutover_deletions import PHASES

    _sql(data / '.router-deleted-owners.sqlite3', """
        CREATE TABLE deleted_owners (owner TEXT PRIMARY KEY, receipt TEXT);
    """)
    with sqlite3.connect(data / '.router-deleted-owners.sqlite3') as db:
        db.execute("INSERT INTO deleted_owners VALUES (?,?)",
                   ('retired-account', json.dumps({'completed_phases': list(PHASES)})))
    migration.apply(*cutover)
    assert not (data / '.tinyassets.db').exists()
    with closing(metadata_connection(data)) as db:
        assert db.execute("SELECT name FROM sqlite_master "
                          "WHERE name='deleted_principals'").fetchall() == []
        from tinyassets.broker.admission import local_read

        assert local_read(data, 'retired-account').state == 'erased'
        with pytest.raises(PermissionError, match='deleted'):
            check_not_deleted(data, 'retired-account')
        check_not_deleted(data, 'bob')




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
    # Seeding uses the production initializers in this process. Release their
    # cyclic SQLite handles before the census reads the stopped-writer tree, so
    # a collected handle cannot remove a WAL file mid-read.
    gc.collect()
    report = migration.census(data)
    assert report['ready'], report['refusals']
    # The paired backup must contain the actual seeded pre-cutover generation.
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
    roots = {300001: contents(store) / '300001', 300002: contents(store) / '300002'}
    with tempfile.TemporaryDirectory(prefix='ta-cutover-') as directory:
        run = Path(directory)
        relays = OwnerRelays(run / 'relay', SimpleNamespace(broker_socket=broker.path))
        runtime = ProcessRuntime(roots, module='tests.support.owner_cutover_process')
        try:
            for owner, uid in [('alice', 300001), ('bob', 300002)]:
                app = run / str(uid)
                app.mkdir()
                secret = app / 'secret'
                secret.write_text(identities.box_route(owner)[1])
                secret.chmod(0o400)
                runtime.start(uid, uid, relay=relays.bind(owner, uid), app_dir=app,
                              secret_file=secret, memory_bytes=1024**3,
                              volume_mount={"source": str(roots[uid])}, volume_generation=1,
                              centers=("a1", "a2") if owner == "alice" else ("b1", "b2"))
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
                                  headers={'x-owner': owner,
                                           'x-box-secret': identities.box_route(owner)[1],
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
    assert _fingerprint(contents(store) / '300001') != json.loads(
        (data / '.owner-cutover.json').read_text())['owner_manifests']['300001']
    from tinyassets.cutover_control import admit, initialize

    initialize(tmp_path / "deployment", backup=cutover[2], old_image="old-image")
    admit(tmp_path / "deployment", data)
    live_before = _fingerprint(data), _fingerprint(store)
    from tinyassets.cutover_restore import restore_old_layout
    from tinyassets.restore_recovery import RecoveryUnavailable, prepare_platform
    staged = restore_old_layout(cutover[2], tmp_path / "old-layout")
    with pytest.raises(RecoveryUnavailable, match="shared-layer backup") as refused:
        prepare_platform(staged, backup_time=1)
    assert "shared-layer backup" in str(refused.value)
    assert (staged / "a1/notes.txt").read_text() == "private history for a1"
    assert (_fingerprint(data), _fingerprint(store)) == live_before




def test_cutover_imports_legacy_facts_and_refuses_unfinishable_obligations(cutover):
    from tinyassets.broker.admission import digest as principal_digest
    from tinyassets.cutover_deletions import collect

    data, store, *_ = cutover
    _sql(data / ".router-deleted-owners.sqlite3", "CREATE TABLE deleted_owners "
         "(owner TEXT PRIMARY KEY, deleted_at REAL, receipt TEXT);"
         "INSERT INTO deleted_owners VALUES ('router-only',1,NULL);")
    custody = data / ".broker/credential-custody.db"
    custody.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(custody) as db:
        db.execute("CREATE TABLE deleted_principals (digest TEXT PRIMARY KEY)")
        db.execute("INSERT INTO deleted_principals VALUES (?)", (principal_digest("custody-only"),))
    (data / ".boxhost").mkdir(exist_ok=True)
    with sqlite3.connect(data / ".boxhost/boxhost.db") as db:
        db.execute("CREATE TABLE owner_destroys (owner_id TEXT, uid INTEGER)")
        db.execute("INSERT INTO owner_destroys VALUES (?,?)", ("host-only", 300003))
    (data / ".disaster-restore.json").write_text(json.dumps({
        "erasure_failures": [{"uid": 300003, "store": "/box-store",
                              "quarantine": "/box-store/.old/300003"}]}))
    report = migration.census(data)
    assert report["ready"], report["refusals"]
    facts = collect(data, store_root=store)
    assert all(facts.contains(owner) for owner in ("router-only", "custody-only", "host-only"))
    assert any(work["quarantine"] == ".old/300003" for entry in facts.entries()
               for work in entry["work"].values())
    assert report["legacy_deletions"]["pending_deletions"] >= 2
    # A digest alone cannot complete unknown identity/billing obligations.
    with pytest.raises(RuntimeError, match="legacy deletion incomplete"):
        migration.apply(*cutover)
    assert json.loads((data / ".owner-cutover.json").read_text())["phase"] == "build"
    assert (data / ".router-deleted-owners.sqlite3").exists()


def test_cutover_publishes_legacy_receipt_only_after_path_cleanup(cutover):
    from tinyassets.broker.admission import digest, local_read
    from tinyassets.broker.deletion_jobs import STEPS
    from tinyassets.broker.deletion_records import RemoteRecords
    from tinyassets.cutover_deletions import PHASES

    data, store, *_ = cutover
    target = store / ".old/300003"
    target.mkdir(parents=True)
    (target / "private").write_bytes(b"synthetic erased bytes")
    (data / ".boxhost").mkdir(exist_ok=True)
    with sqlite3.connect(data / ".boxhost/boxhost.db") as db:
        db.execute("CREATE TABLE owner_destroys (owner_id TEXT, uid INTEGER, receipt TEXT)")
        db.execute("INSERT INTO owner_destroys VALUES (?,?,?)", ("host-only", 300003, json.dumps({
            "completed_phases": [p for p in PHASES if p != "owner_tree"],
            "quarantines": [".old/300003"]})))
    migration.apply(*cutover)
    assert not target.exists()
    assert not (data / ".boxhost").exists()
    records = RemoteRecords(migration._volumes(store).remote)
    job = records.load(digest("host-only"))
    assert job["completed_steps"] == list(STEPS)
    assert job["results"]["receipt"]["deleted"]
    with sqlite3.connect(data / ".broker/state/owner-identities.db") as db:
        local, = db.execute("SELECT record FROM deletion_jobs WHERE id=?", (job["id"],)).fetchone()
    assert json.loads(local) == job
    assert local_read(data, "host-only").state == "erased"
    assert not (data / ".erasure-ledger.sqlite3").exists()


@pytest.mark.parametrize("relative", ["knowledge.db", "a1/local.db"])
@pytest.mark.parametrize("corrupt_table", [False, True])
def test_whole_copied_database_uses_same_integrity_verdict(cutover, relative, corrupt_table):
    data, store, *_ = cutover
    _sql(data / relative, """
        CREATE TABLE facts (universe_id TEXT, value CHECK(value > 0));
        INSERT INTO facts VALUES ('a1',1);
        CREATE INDEX facts_drift ON facts(universe_id) WHERE 0;
        PRAGMA writable_schema=ON;
        UPDATE sqlite_master SET sql='CREATE INDEX facts_drift ON facts(universe_id)'
          WHERE name='facts_drift';
    """)
    if corrupt_table:
        _sql(data / relative, "PRAGMA ignore_check_constraints=ON; "
                             "INSERT INTO facts VALUES ('a1',-1);")
    before = _fingerprint(data)
    report = migration.census(data)
    assert _fingerprint(data) == before
    if corrupt_table:
        assert report["refusals"]["database_integrity"] == 1
        with pytest.raises(RuntimeError, match="census refused"):
            migration.apply(*cutover)
    else:
        assert report["ready"], report["refusals"]
        assert report["repairable_indexes"] == [f"repairable index: {relative}/facts_drift"]
        migration.apply(*cutover)
        with closing(sqlite3.connect(contents(store) / "300001" / relative)) as db:
            assert db.execute("PRAGMA integrity_check(1000000000)").fetchall() == [("ok",)]
            rows = db.execute("SELECT universe_id FROM facts INDEXED BY facts_drift").fetchall()
            assert rows == [("a1",)]


def test_migration_publishes_detached_owner_snapshots_and_preserves_binding(cutover):
    data, store, *_ = cutover
    migration.apply(*cutover)
    volumes = migration._volumes(store)
    assert not volumes.attached
    assert not (store / "300001").exists()
    assert not (store / "300002").exists()
    with sqlite3.connect(data / ".broker/state/owner-identities.db") as db:
        rows = db.execute("SELECT principal,uid,volume_generation,snapshot_id,"
                          "snapshot_sha256,work FROM owner_volumes ORDER BY uid").fetchall()
        assert len(rows) == 2
        for owner, uid, generation, reference, digest, work in rows:
            assert generation == 1 and work is None
            assert len(reference) == 32 and len(digest) == 64
            assert db.execute("SELECT principal,snapshot_sha256 FROM owner_snapshots "
                              "WHERE snapshot_id=?", (reference,)).fetchone() == (owner, digest)
            allowance = db.execute("SELECT bhard_bytes FROM box_records WHERE principal=?",
                                   (owner,)).fetchone()[0]
            assert volumes.size(uid) == allowance
            events = [event for event, event_uid in volumes.events if event_uid == uid]
            assert events[events.index("snapshot") - 1] == "detach"
    migration.verify(data, store)
    assert not volumes.attached


def test_snapshot_failure_keeps_cutover_closed_and_resumes_same_reference(cutover, monkeypatch):
    data, store, *_ = cutover
    volumes = migration._volumes(store)
    snapshot = volumes.snapshot
    attempts = []

    def fail(uid, snapshot_id):
        attempts.append(snapshot_id)
        raise OSError("synthetic remote unavailable")

    monkeypatch.setattr(volumes, "snapshot", fail)
    with pytest.raises(OSError, match="remote unavailable"):
        migration.apply(*cutover)
    assert not volumes.attached
    journal = json.loads((data / ".owner-cutover.json").read_text())
    assert journal["phase"] == "build"
    assert json.loads((data / ".layout.json").read_text())["state"] == "migrating"
    assert (data / "a1").is_dir()
    assert journal["snapshots"]["300001"] == attempts[0]
    monkeypatch.setattr(volumes, "snapshot", snapshot)
    migration.apply(*cutover)
    migration.verify(data, store)
    with sqlite3.connect(data / ".broker/state/owner-identities.db") as db:
        assert db.execute("SELECT snapshot_id FROM owner_volumes WHERE uid=300001"
                          ).fetchone() == (attempts[0],)


def test_migration_uses_existing_box_allowance(cutover):
    data, store, *_ = cutover
    allowance = 3 * 1024**3
    _sql(data / ".broker/state/owner-identities.db", """
        CREATE TABLE box_records (principal TEXT PRIMARY KEY, machine INT,
                                  project_id INT, bhard_bytes INT);
        INSERT INTO box_records VALUES ('alice',300001,300001,3221225472);
    """)
    assert migration._owner_plan(data)[0].quota == allowance
    migration.apply(*cutover)
    assert migration._volumes(store).size(300001) == allowance
    migration.verify(data, store)


def _restore_old_layout(cutover, tmp_path):
    # Test installation only: eligibility and production installation are C4's
    # separate deployment-control lane. This unit restores the old bytes.
    import shutil

    from tinyassets.cutover_restore import restore_old_layout
    data, _, archive, _ = cutover
    restored = restore_old_layout(archive, tmp_path / "rollback-stage")
    data.rename(tmp_path / "retained-shared")
    shutil.move(restored, data)
    return subprocess.CompletedProcess([], 0, "", "")


def test_cutover_converts_and_removes_legacy_erasure_ledger(cutover):
    from tinyassets.broker.admission import digest, local_read
    from tinyassets.cutover_deletions import PHASES

    data, store, *_ = cutover
    ledger = data / ".erasure-ledger.sqlite3"
    with sqlite3.connect(ledger) as db:
        db.execute("CREATE TABLE initialized (version INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO initialized VALUES (1)")
        db.execute("CREATE TABLE events (id TEXT PRIMARY KEY, digest TEXT, at REAL, payload TEXT)")
        db.execute("INSERT INTO events VALUES (?,?,?,?)", (
            "historical", digest("ledger-only"), 1,
            json.dumps({"principal": "ledger-only", "uids": [],
                        "receipt": {"completed_phases": list(PHASES)}})))
    migration.apply(*cutover)
    assert local_read(data, "ledger-only").state == "erased"
    assert not ledger.exists()
    assert not list(data.glob(".erasure-ledger.sqlite3-*"))
    with sqlite3.connect(data / ".broker/state/owner-identities.db") as db:
        assert db.execute("SELECT name FROM sqlite_master "
                          "WHERE name='owner_box_revocations'").fetchall() == []
    from tinyassets.broker.deletion_jobs import STEPS
    from tinyassets.broker.deletion_records import RemoteRecords

    job = RemoteRecords(migration._volumes(store).remote).load(digest("ledger-only"))
    assert job["completed_steps"] == list(STEPS)
    assert job["results"]["receipt"]["deleted"]
