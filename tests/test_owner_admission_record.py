"""C6/T5/T9 single-authority proof, including the temporary b1 equivalence."""

import sqlite3

import pytest

from tinyassets.broker.admission import admission, local_mutate, read, require_admitted
from tinyassets.broker.box_admission import owner_record, resolve, resolve_owner
from tinyassets.broker.owner_identities import OwnerIdentities
from tinyassets.broker.owner_reads import Plans


@pytest.fixture
def authority(tmp_path):
    state = tmp_path / ".broker/state"
    state.mkdir(parents=True, mode=0o700)
    identities = OwnerIdentities(state / "owner-identities.db", initialize=True,
                                 plans=Plans(tmp_path))
    uid = identities.resolve("alice", allocate=True).uid
    identities.admission("admit", "alice", "center")
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


# lane-b2: delete with the old authority; replace with record-only tests.
@pytest.mark.parametrize("state", [
    "admitted", "billing", "abuse", "both", "center-retired", "account-retired",
    "erased", "revoked", "absent",
])
def test_old_writer_equivalence_for_every_work_reader(authority, state, tmp_path):
    identities = authority
    owner = "missing" if state == "absent" else "alice"
    if state in {"billing", "both"}:
        identities.plans.set_suspension(owner, reason="billing", suspended=True)
    if state in {"abuse", "both"}:
        identities.plans.set_suspension(owner, reason="abuse", suspended=True)
    if state == "center-retired":
        identities.admission("retire", owner, "center")
    if state == "account-retired":
        identities.retire_owner(owner)
    if state == "revoked":
        identities.revoke_box(owner)
    if state == "erased":
        from tinyassets.erasure_ledger import ErasureLedger

        ledger = ErasureLedger(tmp_path)
        ledger.initialize()
        ledger.add(owner)
    # Old combined authority, with C6's explicit center-retirement correction.
    with sqlite3.connect(identities.path) as db:
        reserved = db.execute("SELECT 1 FROM owner_identities WHERE principal=?",
                              (owner,)).fetchone() is not None
        revoked = db.execute("SELECT 1 FROM owner_box_revocations WHERE principal=?",
                             (owner,)).fetchone() is not None
    plan = identities.plans.read("alice")
    old = reserved and not (revoked or plan["billing_suspended"] or plan["abuse_suspended"]
                           or state == "erased")
    readers = {
        "admission": lambda: require_admitted(identities, owner),
        "router": lambda: identities.box_route(owner),
        "boxhostd": lambda: owner_record(identities, owner, rotate=False),
        "channel-admission": lambda: resolve(identities, {"op": "BOX_ADMISSION",
                                                         "principal": owner, "center": "center"}),
        "public-egress": lambda: resolve_owner(identities, {"op": "BOX_PUBLIC",
                                                           "principal": owner}),
    }
    assert {question: allowed(call) for question, call in readers.items()} == dict.fromkeys(
        readers, bool(old))
    from types import SimpleNamespace

    from tinyassets.broker.next_wake import NextWakeIndex
    from tinyassets.broker.server import _Connection
    from tinyassets.broker.waker import Waker
    from tinyassets.router_credentials import active_owner

    index = NextWakeIndex(identities)
    assert (owner in index.due(now=0)) == bool(old)
    woke = []
    Waker(index, identities.plans, wake=lambda owner, **kw: woke.append(owner))._wake(owner)
    assert woke == ([owner] if old else [])
    channel = SimpleNamespace(_box_owner=owner, _box_center="center", _server=SimpleNamespace(
        _owner_identities=identities, _fence=SimpleNamespace(generation=1, token="test")))
    from tinyassets.rpc_frames import FrameError

    try:
        scope = _Connection._box_scope(channel, {})
    except FrameError:
        assert not old
    else:
        assert old and scope["principal"] == owner and scope["command_center"] == "center"
    record = read(identities, owner)
    assert active_owner(owner, tmp_path) == (record.state != "erased")
    assert (record.state == "admitted") == bool(old)
    assert (record.state == "erased") == (state in {"account-retired", "erased", "revoked"})


def test_sources_cannot_clear_each_other_or_terminal_erasure(authority):
    plans = authority.plans
    for reason in ("billing", "abuse"):
        plans.set_suspension("alice", reason=reason, suspended=True)
    plans.set_suspension("alice", reason="billing", suspended=False)
    assert read(authority, "alice").reasons == {"abuse"}
    authority.revoke_box("alice")
    plans.set_suspension("alice", reason="abuse", suspended=False)
    assert read(authority, "alice").state == "erased"
    with pytest.raises(PermissionError):
        local_mutate(authority.path, "alice", "admit")


def test_center_retirement_keeps_record_and_secret_and_zero_center_wake(authority):
    before = authority.box_route("alice")
    authority.admission("retire", "alice", "center")
    assert authority.box_route("alice") == before
    assert owner_record(authority, "alice", rotate=False)["centers"] == []
    from tinyassets.broker.next_wake import NextWakeIndex

    assert NextWakeIndex(authority).due(now=1) == ["alice"]


def test_only_the_record_decides_after_legacy_authorities_change(authority):
    with sqlite3.connect(authority.path) as db:
        db.execute("INSERT INTO owner_box_revocations VALUES ('alice',1)")
        assert admission(db, "alice").state == "admitted"
    assert authority.box_route("alice")[0] == authority.owner_machine("alice")
    local_mutate(authority.path, "alice", "erase")
    with sqlite3.connect(authority.path) as db:
        db.execute("DELETE FROM owner_box_revocations")
    assert not allowed(lambda: authority.box_route("alice"))


def test_absent_record_is_not_created_by_read(authority):
    with sqlite3.connect(authority.path) as db:
        db.execute("DELETE FROM owner_admission")
    assert not allowed(lambda: authority.box_route("alice"))
    assert not allowed(lambda: owner_record(authority, "alice", rotate=False))
    assert not allowed(lambda: resolve_owner(authority, {"op": "BOX_PUBLIC", "principal": "alice"}))


def test_runtime_restart_does_not_recreate_missing_admission_schema(authority):
    with sqlite3.connect(authority.path) as db:
        db.execute("DROP TABLE owner_admission")
    restarted = OwnerIdentities(authority.path, plans=authority.plans)
    with pytest.raises(sqlite3.OperationalError):
        read(restarted, "alice")


def test_suspended_owner_authenticates_but_cannot_work(authority, tmp_path):
    from tinyassets.router_credentials import active_owner

    authority.plans.set_suspension("alice", reason="abuse", suspended=True)
    assert active_owner("alice", tmp_path)
    assert not allowed(lambda: authority.box_route("alice"))
    authority.revoke_box("alice")
    assert not active_owner("alice", tmp_path)


def test_concurrent_sources_keep_both_reasons(authority):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(authority.plans.set_suspension, "alice", reason=reason,
                               suspended=True) for reason in ("billing", "abuse")]
        for future in futures:
            future.result()
    assert read(authority, "alice").reasons == {"billing", "abuse"}


def test_cutover_drops_center_revocation_and_seeds_terminal_and_source_reasons(authority, tmp_path):
    from tinyassets.box_layout_migration import _seed_owner_admission
    from tinyassets.erasure_ledger import ErasureLedger

    authority.plans.set_suspension("alice", reason="billing", suspended=True)
    authority.plans.set_suspension("alice", reason="abuse", suspended=True)
    with sqlite3.connect(authority.path) as db:
        db.execute("DELETE FROM owner_admission")
        db.execute("INSERT INTO owner_box_revocations VALUES ('alice',0)")
        db.execute("DELETE FROM owner_box_secrets")
    ledger = ErasureLedger(tmp_path)
    ledger.initialize()
    from tinyassets.erasure_ledger import principal_digest

    ledger.execute("add", {"digest": principal_digest("erased-before-cutover")})
    _seed_owner_admission(tmp_path, ledger)
    assert read(authority, "alice").reasons == {"billing", "abuse"}
    assert read(authority, "erased-before-cutover").state == "erased"
    with sqlite3.connect(authority.path) as db:
        assert db.execute("SELECT * FROM owner_box_revocations").fetchall() == []
    for reason in ("billing", "abuse"):
        authority.plans.set_suspension("alice", reason=reason, suspended=False)
    assert len(authority.box_route("alice", rotate=True)[1]) == 43
    authority.retire_owner("alice")
    _seed_owner_admission(tmp_path, ledger)
    assert read(authority, "alice").state == "erased"
