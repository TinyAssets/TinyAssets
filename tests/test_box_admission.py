"""C6 record-only admission, custody and startup fail closed."""

import fcntl
import json
import os
import sqlite3

import pytest

from tests.support.admission import Plans, async_call, drive, projection, suspend
from tinyassets.boxes.layout import hold
from tinyassets.broker.admission import local_mutate, read, require_admitted
from tinyassets.broker.box_admission import owner_record, resolve_owner
from tinyassets.broker.owner_identities import OwnerIdentities


@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


@pytest.fixture
def owner_box(tmp_path):
    tmp_path.chmod(0o700)
    identities = OwnerIdentities(tmp_path / "identities.db", initialize=True)
    uid = identities.resolve("alice", allocate=True).uid
    from tinyassets.broker.admission import local_mutate

    drive(local_mutate(identities.path, "alice", "admit", routing=projection(identities.path)))
    identities.plans.apply("alice", tier="paid", event_created=1)
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT, machine INT, "
                   "project_id INT, bhard_bytes INT)")
        db.execute("INSERT INTO box_records VALUES (?,?,?,?)", ("alice", uid, uid, 1024**3))
    return identities, uid


@pytest.mark.verifies('owner-daemon-in-box:C11', 'owner-daemon-in-box:T5')
def test_owner_public_admission_needs_no_center_and_refuses_center_fields(owner_box):
    identities, uid = owner_box
    assert resolve_owner(identities, {"op": "BOX_PUBLIC", "principal": "alice"}) == {
        "op": "BOX_ADMITTED", "uid": uid, "quota": 1024**3}
    with sqlite3.connect(identities.path) as db:
        assert db.execute("SELECT name FROM sqlite_master "
                          "WHERE name='center_admissions'").fetchall() == []
        assert db.execute("SELECT COUNT(*) FROM owner_box_secrets").fetchone() == (0,)
    with pytest.raises(ValueError):
        resolve_owner(identities, {"op": "BOX_PUBLIC", "principal": "alice", "center": "a"})


@pytest.mark.verifies('owner-daemon-in-box:T5')
@pytest.mark.parametrize("column,value", [
    ("machine", 300002), ("project_id", 300002), ("machine", None),
    ("bhard_bytes", 0), ("bhard_bytes", -1024), ("bhard_bytes", 1025),
    ("bhard_bytes", 1024.5), ("bhard_bytes", "unverified"),
])
def test_owner_public_admission_refuses_unverified_box_record(owner_box, column, value):
    identities, _ = owner_box
    with sqlite3.connect(identities.path) as db:
        db.execute(f"UPDATE box_records SET {column}=?", (value,))
    with pytest.raises(PermissionError, match="verified box registry record"):
        resolve_owner(identities, {"op": "BOX_PUBLIC", "principal": "alice"})


@pytest.mark.verifies('owner-daemon-in-box:C11', 'owner-daemon-in-box:T5')
@pytest.mark.parametrize("document", [
    {"op": "BOX_PUBLIC", "principal": "alice", "center": "a"},
    {"op": "BOX_PUBLIC", "principal": "alice", "uid": 300001},
    {"op": "BOX_ADMISSION", "principal": "alice"},
    {"op": "BOX_PUBLIC"},
    {"op": "BOX_PUBLIC", "principal": ""},
])
def test_owner_public_admission_has_an_exact_owner_keyed_request(owner_box, document):
    identities, _ = owner_box
    with pytest.raises(ValueError):
        resolve_owner(identities, document)


@pytest.mark.verifies('owner-daemon-in-box:T5')
def test_owner_public_admission_refuses_missing_identity_plan_or_schema(owner_box):
    identities, _ = owner_box
    request = {"op": "BOX_PUBLIC", "principal": "alice"}
    with pytest.raises(PermissionError):
        resolve_owner(None, request)
    with sqlite3.connect(identities.plans.path) as db:
        db.execute("DELETE FROM plans")
    # Billing metadata is no longer an admission authority.
    assert resolve_owner(identities, request)["uid"] == identities.owner_machine("alice")
    with sqlite3.connect(identities.path) as db:
        db.execute("DELETE FROM owner_admission")
    with pytest.raises(LookupError):
        resolve_owner(identities, request)
    from tinyassets.broker.admission import local_mutate

    drive(local_mutate(identities.path, "alice", "admit", routing=projection(identities.path)))
    with sqlite3.connect(identities.path) as db:
        db.execute("DROP TABLE box_records")
    with pytest.raises(sqlite3.OperationalError):
        resolve_owner(identities, request)


@pytest.mark.verifies('owner-daemon-in-box:T3')
def test_broker_admission_requires_permanent_owner_mapping_and_migrated_quota(tmp_path):
    tmp_path.chmod(0o700)
    identities = OwnerIdentities(tmp_path / "identities.db", initialize=True)
    identity = identities.resolve("alice", allocate=True)
    drive(identities.admit("alice", routing=projection(identities.path)))
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT, machine INT, "
                   "project_id INT, bhard_bytes INT)")
        db.execute("INSERT INTO box_records VALUES (?,?,?,?)",
                   ("alice", identity.uid, identity.uid, 1024**3))
    request = {"op": "BOX_PUBLIC", "principal": "alice"}
    assert resolve_owner(identities, request) == {"op": "BOX_ADMITTED", "uid": identity.uid,
                                           "quota": 1024**3}
    with pytest.raises(PermissionError):
        resolve_owner(identities, {**request, "principal": "bob"})
    with pytest.raises(ValueError):
        resolve_owner(identities, {**request, "uid": identity.uid})
    with sqlite3.connect(identities.path) as db:
        db.execute("UPDATE box_records SET project_id=project_id+1")
    with pytest.raises(PermissionError):
        resolve_owner(identities, request)
    with sqlite3.connect(identities.path) as db:
        db.execute("UPDATE box_records SET project_id=machine")
    assert resolve_owner(identities, request)["uid"] == identity.uid




@pytest.mark.verifies('owner-daemon-in-box:T4')
def test_box_layout_lock_excludes_migration_for_service_lifetime(tmp_path):
    lock = tmp_path / ".layout.lock"
    lock.touch()
    marker = tmp_path / ".layout.json"
    marker.write_text(json.dumps({"layout": 2, "state": "stable", "box_layout": "owner-box"}))
    other = os.open(lock, os.O_RDONLY)
    try:
        with hold(tmp_path):
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(other)
    marker.write_text('{"layout":2,"state":"migrating","box_layout":"owner-box"}')
    with pytest.raises(RuntimeError, match="committed"):
        with hold(tmp_path):
            pytest.fail("incomplete layout admitted")


@pytest.mark.verifies('owner-daemon-in-box:T3')
def test_new_owner_box_reservation_is_canonical_idempotent_and_fail_closed(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tinyassets.broker.box_admission import provision

    tmp_path.chmod(0o700)
    state = tmp_path / ".broker"
    state.mkdir(mode=0o700)
    identities = OwnerIdentities(state / "owner-identities.db", initialize=True)
    uid = identities.resolve("alice", allocate=True).uid
    drive(identities.admit("alice", routing=projection(identities.path)))
    def no_owner_read(*args):
        raise AssertionError("broker provisioning must read only shared plans")

    monkeypatch.setattr("tinyassets.universe_owner.account_type_of", no_owner_read)
    monkeypatch.setattr("tinyassets.usage_policy.limits_for",
                        lambda tier: SimpleNamespace(storage_bytes=1024**3))
    # No startup schema creation, even after authenticated admission.
    with pytest.raises(sqlite3.OperationalError):
        provision(identities, tmp_path, "alice")
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT PRIMARY KEY, machine INT UNIQUE, "
                   "project_id INT UNIQUE, bhard_bytes INT)")
    provision(identities, tmp_path, "alice")
    monkeypatch.setattr("tinyassets.usage_policy.limits_for",
                        lambda tier: SimpleNamespace(storage_bytes=1024))
    provision(identities, tmp_path, "alice")
    assert resolve_owner(identities, {"op": "BOX_PUBLIC", "principal": "alice"}) == {
        "op": "BOX_ADMITTED", "uid": uid, "quota": 1024**3}
    with pytest.raises(PermissionError):
        provision(identities, tmp_path, "bob")
    with sqlite3.connect(identities.path) as db:
        db.execute("UPDATE box_records SET project_id=project_id+1")
    with pytest.raises(PermissionError, match="reservation changed"):
        provision(identities, tmp_path, "alice")


@pytest.fixture
def authority(tmp_path):
    state = tmp_path / ".broker/state"
    state.mkdir(parents=True, mode=0o700)
    state.parent.chmod(0o700)
    identities = OwnerIdentities(state / "owner-identities.db", initialize=True,
                                 plans=Plans(tmp_path))
    uid = identities.resolve("alice", allocate=True).uid
    drive(identities.admit("alice", routing=projection(identities.path)))
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT PRIMARY KEY, machine INT, "
                   "project_id INT, bhard_bytes INT)")
        db.execute("INSERT INTO box_records VALUES (?,?,?,?)", ("alice", uid, uid, 1024**3))
        from tinyassets.broker.owner_volumes import provision

        provision(db, "alice", uid)
    return identities


def allowed(call):
    try:
        call()
    except (LookupError, PermissionError):
        return False
    return True


@pytest.mark.verifies('owner-daemon-in-box:C6', 'owner-daemon-in-box:T5')
@pytest.mark.parametrize("state", [
    "admitted", "billing", "abuse", "both", "center-retired", "account-retired",
    "erased", "policy-erased", "vault-erased", "absent",
])
def test_record_only_for_every_work_reader(authority, state, tmp_path):
    identities = authority
    owner = "missing" if state == "absent" else "alice"
    if state in {"billing", "both"}:
        suspend(identities.plans, owner, reason="billing", suspended=True)
    if state in {"abuse", "both"}:
        suspend(identities.plans, owner, reason="abuse", suspended=True)
    if state == "center-retired":
        from tinyassets.owner_centers import Centers

        box = tmp_path / "box"
        box.mkdir()
        Centers(box).create("center")
        Centers(box).retire("center")
    if state == "account-retired":
        drive(local_mutate(identities.path, owner, "erase",
      routing=projection(identities.path)))
    if state == "erased":
        drive(local_mutate(identities.path, owner, "erase",
      routing=projection(identities.path)))
    if state == "policy-erased":
        from tinyassets.broker.policy import PolicyStore

        # C14: step 1 withdraws admission before custody cleanup starts.
        drive(local_mutate(identities.path, owner, "erase",
      routing=projection(identities.path)))
        PolicyStore(tmp_path, identities).erase(owner)
    if state == "vault-erased":
        from tinyassets.broker.vault import erase_owner

        drive(local_mutate(identities.path, owner, "erase",
      routing=projection(identities.path)))
        erase_owner(tmp_path, owner)
    terminal = state in {"account-retired", "erased", "policy-erased", "vault-erased"}
    expected = state in {"admitted", "center-retired"}
    readers = {
        "admission": lambda: require_admitted(identities, owner),
        "router": lambda: identities.box_route(owner),
        "boxhostd": lambda: owner_record(identities, owner),
        "channel-admission": lambda: resolve_owner(identities, {"op": "BOX_PUBLIC",
                                                         "principal": owner}),
        "public-egress": lambda: resolve_owner(identities, {"op": "BOX_PUBLIC",
                                                           "principal": owner}),
    }
    assert {question: allowed(call) for question, call in readers.items()} == dict.fromkeys(
        readers, expected)
    from types import SimpleNamespace

    from tinyassets.broker.next_wake import NextWakeIndex
    from tinyassets.broker.server import _Connection
    from tinyassets.broker.waker import Waker
    from tinyassets.router_credentials import active_owner

    index = NextWakeIndex(identities)
    assert (owner in index.due(now=0)) == expected
    woke = []
    drive(Waker(index, identities.plans,
                wake=async_call(lambda owner, **kw: woke.append(owner)))._wake(owner))
    assert woke == ([owner] if expected else [])
    channel = SimpleNamespace(_box_owner=owner, _server=SimpleNamespace(
        _owner_identities=identities, _fence=SimpleNamespace(generation=1, token="test")))
    from tinyassets.rpc_frames import FrameError

    try:
        scope = _Connection._box_scope(channel, {})
    except FrameError:
        assert not expected
    else:
        assert expected and scope == {"principal": owner, "generation": 1, "token": "test"}
    record = read(identities, owner)
    assert active_owner(owner, tmp_path)  # Authentication has no erasure-history read.
    assert (record.state == "admitted") == expected
    assert (record.state == "erased") == terminal


@pytest.mark.verifies('owner-daemon-in-box:C6', 'owner-daemon-in-box:T9')
def test_sources_cannot_clear_each_other_or_terminal_erasure(authority):
    plans = authority.plans
    for reason in ("billing", "abuse"):
        suspend(plans, "alice", reason=reason, suspended=True)
    suspend(plans, "alice", reason="billing", suspended=False)
    assert read(authority, "alice").reasons == {"abuse"}
    drive(local_mutate(authority.path, "alice", "erase", routing=projection(authority.path)))
    for reason in ("billing", "abuse"):
        suspend(plans, "alice", reason=reason, suspended=False)
        assert read(authority, "alice").state == "erased"
    with pytest.raises(PermissionError):
        drive(local_mutate(authority.path, "alice", "admit",
       routing=projection(authority.path)))


@pytest.mark.verifies('owner-daemon-in-box:C6')
def test_center_retirement_keeps_record_and_secret_and_zero_center_wake(authority, tmp_path):
    before = authority.box_route("alice")
    from tinyassets.owner_centers import Centers

    box = tmp_path / "box"
    box.mkdir()
    Centers(box).create("center")
    Centers(box).retire("center")
    assert Centers(box).active() == []
    assert authority.box_route("alice") == before
    assert "centers" not in owner_record(authority, "alice")
    from tinyassets.broker.next_wake import NextWakeIndex

    assert NextWakeIndex(authority).due(now=1) == ["alice"]


@pytest.mark.verifies('owner-daemon-in-box:C6')
def test_retirement_writes_only_the_terminal_record(authority):
    with sqlite3.connect(authority.path) as db:
        before = {table: db.execute(f"SELECT * FROM {table}").fetchall() for table in
                  ("box_records", "owner_identities", "owner_box_secrets")}
        assert db.execute("SELECT name FROM sqlite_master "
                          "WHERE name='owner_box_revocations'").fetchall() == []
    assert set(authority.plans.read("alice")) == {
        "owner", "tier", "event_created", "ends_at", "customer_id", "subscription_id", "attempt"}
    drive(local_mutate(authority.path, "alice", "erase", routing=projection(authority.path)))
    assert read(authority, "alice").state == "erased"
    assert not allowed(lambda: authority.box_route("alice"))
    with sqlite3.connect(authority.path) as db:
        assert {table: db.execute(f"SELECT * FROM {table}").fetchall()
                for table in before} == before


@pytest.mark.verifies('owner-daemon-in-box:C6', 'owner-daemon-in-box:T5')
def test_absent_record_is_not_created_by_read(authority):
    with sqlite3.connect(authority.path) as db:
        db.execute("DELETE FROM owner_admission")
    assert not allowed(lambda: authority.box_route("alice"))
    assert not allowed(lambda: owner_record(authority, "alice"))
    assert not allowed(lambda: resolve_owner(authority, {"op": "BOX_PUBLIC", "principal": "alice"}))


@pytest.mark.verifies('owner-daemon-in-box:C6')
def test_runtime_restart_does_not_recreate_missing_admission_schema(authority):
    with sqlite3.connect(authority.path) as db:
        db.execute("DROP TABLE owner_admission")
    restarted = OwnerIdentities(authority.path, plans=authority.plans)
    with pytest.raises(sqlite3.OperationalError):
        read(restarted, "alice")


@pytest.mark.verifies('owner-daemon-in-box:C6')
@pytest.mark.parametrize("damage", ["missing", "empty", "corrupt", "schema"])
def test_damaged_record_authority_denies_authentication_and_work(authority, tmp_path, damage):
    from tinyassets.api_keys import ApiKeyStore
    from tinyassets.router_credentials import active_owner
    from tinyassets.routing_snapshot import RoutingSnapshot, _snapshots

    keys = ApiKeyStore(tmp_path)
    key = keys.mint("alice", name="fixture", scopes=[], authority=None)["key"]
    assert keys.resolve(key) is not None  # Start the fixture broker before authority loss.
    if damage == "missing":
        authority.path.unlink()
    elif damage in {"empty", "corrupt"}:
        authority.path.write_bytes(b"" if damage == "empty" else b"corrupt admission")
    else:
        with sqlite3.connect(authority.path) as db:
            db.execute("DROP TABLE owner_admission")
    before = authority.path.read_bytes() if authority.path.exists() else None
    # C5: a restarted router has no current projection. An already installed
    # healthy projection instead survives the control-plane outage.
    _snapshots[tmp_path] = RoutingSnapshot()
    assert keys.resolve(key) is None
    assert not active_owner("alice", tmp_path)
    assert not active_owner("bob", tmp_path)
    with pytest.raises(sqlite3.Error):
        require_admitted(authority, "alice")
    assert (authority.path.read_bytes() if authority.path.exists() else None) == before


@pytest.mark.verifies('owner-daemon-in-box:C6', 'owner-daemon-in-box:T9')
def test_admission_mutation_has_one_runtime_writer():
    import ast
    import re
    from pathlib import Path

    import tinyassets

    writers = []
    root = Path(tinyassets.__file__).parent
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and any(
                isinstance(value, ast.Constant) and isinstance(value.value, str)
                and re.search(r"(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+owner_admission\b",
                              value.value, re.IGNORECASE)
                for value in ast.walk(node)
            ):
                writers.append((path.relative_to(root).as_posix(), node.name))
    assert writers == [("broker/admission.py", "mutate")]


@pytest.mark.verifies('owner-daemon-in-box:C6')
def test_suspension_write_does_not_recreate_a_missing_identity_database(authority):
    authority.path.unlink()
    with pytest.raises(sqlite3.OperationalError):
        suspend(authority.plans, "alice", reason="billing", suspended=True)
    assert not authority.path.exists()
    assert "billing_suspended" not in authority.plans.read("alice")



@pytest.mark.verifies('owner-daemon-in-box:C6')
def test_suspended_owner_authenticates_but_cannot_work(authority, tmp_path):
    from tinyassets.api_keys import ApiKeyStore
    from tinyassets.router_credentials import revoke_account

    keys = ApiKeyStore(tmp_path)
    key = keys.mint("alice", name="fixture", scopes=[], authority=None)["key"]
    suspend(authority.plans, "alice", reason="abuse", suspended=True)
    assert keys.resolve(key) is not None
    assert not allowed(lambda: authority.box_route("alice"))
    drive(local_mutate(authority.path, "alice", "erase", routing=projection(authority.path)))
    revoke_account(tmp_path, "alice")
    assert keys.resolve(key) is None
    assert not allowed(lambda: authority.box_route("alice"))


@pytest.mark.verifies('owner-daemon-in-box:C6', 'owner-daemon-in-box:T9')
def test_concurrent_sources_keep_both_reasons(authority):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(suspend, authority.plans, "alice", reason=reason,
                               suspended=True) for reason in ("billing", "abuse")]
        for future in futures:
            future.result()
    assert read(authority, "alice").reasons == {"billing", "abuse"}


@pytest.mark.verifies('owner-daemon-in-box:C6')
def test_cutover_drops_center_revocation_and_seeds_terminal_and_source_reasons(authority, tmp_path):
    from tinyassets.box_layout_migration import _seed_owner_admission
    from tinyassets.broker.admission import digest
    from tinyassets.cutover_deletions import LegacyFacts

    with sqlite3.connect(authority.plans.path) as db:
        for reason in ("billing", "abuse"):
            db.execute(f"ALTER TABLE plans ADD COLUMN {reason}_suspended INTEGER NOT NULL "
                       f"DEFAULT 0 CHECK ({reason}_suspended IN (0,1))")
        db.execute("UPDATE plans SET billing_suspended=1,abuse_suspended=1 WHERE owner='alice'")
    with sqlite3.connect(authority.path) as db:
        db.execute("DELETE FROM owner_admission")
        db.execute("CREATE TABLE owner_box_revocations "
                   "(principal TEXT PRIMARY KEY, deleted INTEGER)")
        db.execute("INSERT INTO owner_box_revocations VALUES ('alice',0),('revoked',1)")
        db.execute("DELETE FROM owner_box_secrets")
    legacy = LegacyFacts(tmp_path)
    legacy.execute("add", {"digest": digest("erased-before-cutover")})
    _seed_owner_admission(tmp_path, legacy)
    assert read(authority, "alice").reasons == {"billing", "abuse"}
    for owner in ("erased-before-cutover", "revoked"):
        assert read(authority, owner).state == "erased"
    with sqlite3.connect(authority.path) as db:
        assert db.execute("SELECT name FROM sqlite_master "
                          "WHERE name='owner_box_revocations'").fetchall() == []
    assert not {"billing_suspended", "abuse_suspended"} & authority.plans.read("alice").keys()
    for reason in ("billing", "abuse"):
        suspend(authority.plans, "alice", reason=reason, suspended=False)
    assert len(authority.box_route("alice")[1]) == 43
    drive(local_mutate(authority.path, "alice", "erase", routing=projection(authority.path)))
    _seed_owner_admission(tmp_path, legacy)
    assert read(authority, "alice").state == "erased"


@pytest.mark.verifies('owner-daemon-in-box:C6')
@pytest.mark.parametrize("owner,uid", [("alice", 300002), ("unknown", 300001),
                                      ("unknown", 399999)])
def test_deletion_resume_requires_positive_permanent_binding(authority, owner, uid):
    from types import SimpleNamespace

    from tinyassets.broker.deletion_jobs import DeletionJobs, new_job

    class Remote:
        def save(self, job):
            pytest.fail("unbound job was written")
    jobs = DeletionJobs(SimpleNamespace(_owner_identities=authority), records=Remote())
    with pytest.raises((LookupError, PermissionError)):
        drive(jobs.resume_loaded_job(new_job(owner, uid)))
    assert read(authority, "alice").state == "admitted"
    assert authority.owner_machine("unknown") is None
