"""C11 real-store center lifecycle and stopped cutover proofs."""

import hashlib
import json
import sqlite3

from tests.support.migration_volumes import contents
from tests.test_owner_cutover_apply import cutover  # noqa: F401
from tests.test_owner_cutover_census import _fingerprint, source  # noqa: F401
from tinyassets import box_layout_migration as migration
from tinyassets.boxes.lifecycle import admit_center, retire_center
from tinyassets.broker.policy import _canonical, _digest
from tinyassets.owner_centers import Centers, admitted_owner


def test_center_operations_change_only_the_box(tmp_path, monkeypatch):
    from tests.support.admission import Plans, drive, projection
    from tinyassets.broker.owner_identities import OwnerIdentities
    from tinyassets.broker.policy import PolicyStore
    from tinyassets.storage.outbound_connections import ConnectionLedger

    shared, box = tmp_path / "shared", tmp_path / "box"
    shared.mkdir()
    box.mkdir()
    state = shared / ".broker/state"
    state.mkdir(parents=True, mode=0o700)
    state.parent.chmod(0o700)
    identities = OwnerIdentities(state / "owner-identities.db", initialize=True,
                                 plans=Plans(shared))
    identities.resolve("alice", allocate=True)
    drive(identities.admit("alice", routing=projection(identities.path)))
    policy = PolicyStore(shared, identities)
    policy.bootstrap()
    assert policy.read("alice").generation == 1
    ledger = ConnectionLedger(shared / ".broker/outbound.db", data_root=shared)
    ledger.create_connection(connection_id="connection", owner_user_id="alice",
        connection_class="http", connection_type="http", auth_scheme="none",
        scopes=("GET",), provider="http", destination="synthetic",
        credential_ref="vault://http/synthetic", allowed_endpoints=[{
            "host": "service.invalid", "path_template": "/", "methods": ["GET"]}])
    ledger.grant_connection(grant_id="grant", connection_id="connection", owner_user_id="alice")
    before = _fingerprint(shared), sorted(p.relative_to(shared) for p in shared.rglob("*"))
    import os
    from types import SimpleNamespace

    from tinyassets import owner_runtime

    monkeypatch.setattr(owner_runtime, "_runtime", SimpleNamespace(
        pid=os.getpid(), root=box, principal="alice"))
    centers = Centers(box)
    assert centers.active() == []
    assert admitted_owner(box, center="one") == "alice"
    for name in ("one", "two", "three"):
        admit_center(box, principal="alice", center=name)
    assert centers.active() == ["one", "three", "two"]
    retire_center("two")
    assert Centers(box).active() == ["one", "three"]
    assert admitted_owner(box, center="two") == "alice"
    after = _fingerprint(shared), sorted(p.relative_to(shared) for p in shared.rglob("*"))
    assert after == before


def test_cutover_drops_custody_and_retains_account_policy(cutover):  # noqa: F811
    data, store, *_ = cutover
    account = hashlib.sha256(b"alice").hexdigest()
    policy_path = data / ".broker/accounts" / account / "policy.db"
    policy_path.parent.mkdir(parents=True)
    document = {
        "entries": [
            {"scope": "account", "table": "rules", "values": {
                "action_class": "money.move", "behaviour": "hand_off"}},
            {"scope": "a1", "table": "rules", "values": {
                "action_class": "app.write", "behaviour": "ask_first"}},
        ],
        "egress": {"mode": "allowlist", "allowlist": ["service.invalid"]},
        "spend": {"daily": 100, "monthly": 200, "transaction": 10,
                  "ask_above": 5, "source_ceilings": {"source": 8}},
    }
    old_digest = _digest(document)
    with sqlite3.connect(policy_path) as db:
        db.executescript("""
            CREATE TABLE policy(singleton INTEGER PRIMARY KEY, generation INTEGER,
                                digest TEXT, document TEXT);
            CREATE TABLE receipts(id INTEGER PRIMARY KEY, at REAL, kind TEXT, owner TEXT,
                session_id TEXT, old_digest TEXT, new_digest TEXT, request_digest TEXT,
                generation INTEGER, rule TEXT, decision TEXT, outcome TEXT);
            CREATE TABLE once_approvals(token_hash TEXT);
            INSERT INTO once_approvals VALUES ('discard-this-approval');
            CREATE TABLE spend_reservations(request_id TEXT, amount INTEGER);
            INSERT INTO spend_reservations VALUES ('retained-reservation', 7);
        """)
        db.execute("INSERT INTO policy VALUES (1,4,?,?)", (old_digest, _canonical(document)))
    with sqlite3.connect(data / "outbound.db") as db:
        db.execute("ALTER TABLE outbound_connection_grants ADD COLUMN universe_id TEXT")
        db.execute("INSERT INTO outbound_connection_grants "
                   "(grant_id,connection_id,owner_user_id,universe_id,granted_at) "
                   "VALUES (?,?,?,?,?)", ("old-grant", "alice-legacy", "alice", "a1", 1))
    before = migration.census(data)
    assert before["ready"], before["refusals"]
    drops = before["center_custody_drops"]
    assert {key: drops[key] for key in ("credentials", "connections", "grants",
                                      "policy_scopes", "policy_approvals")} == {
        "credentials": 4, "connections": 2, "grants": 1,
        "policy_scopes": 1, "policy_approvals": 1,
    }
    migration.apply(*cutover)
    migration.verify(data, store)
    assert not (data / ".platform/credential-vaults").exists()
    assert not (data / ".broker/credential-custody.db").exists()
    for relative in (".broker/outbound.db",):
        with sqlite3.connect(data / relative) as db:
            tables = [row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for table in tables:
                assert db.execute(f'SELECT count(*) FROM "{table}"').fetchone() == (0,)
    assert Centers(contents(store) / "300001").active() == ["a1", "a2"]
    assert Centers(contents(store) / "300002").active() == ["b1", "b2"]
    with sqlite3.connect(policy_path) as db:
        generation, digest, raw = db.execute(
            "SELECT generation,digest,document FROM policy").fetchone()
        expected = {**document, "entries": document["entries"][:1]}
        assert (generation, digest, json.loads(raw)) == (5, _digest(expected), expected)
        assert db.execute("SELECT kind,owner,old_digest,new_digest,generation,outcome "
                          "FROM receipts").fetchall() == [
            ("migration", "alice", old_digest, digest, 5, "committed")]
        assert db.execute("SELECT * FROM spend_reservations").fetchall() == [
            ("retained-reservation", 7)]
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name='once_approvals'").fetchall()
    assert json.loads((data / ".owner-cutover.json").read_text())["report"][
        "center_custody_drops"] == drops
