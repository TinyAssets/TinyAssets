"""Durable acceptance crash boundaries, principal isolation and runtime import."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace

import pytest

from tinyassets.storage.ingress_journal import (
    Conflict,
    Expired,
    IngressJournal,
    Scope,
    import_in_transaction,
    initialize,
    initialize_imports,
)

KEY = "4cc03eb5-0f17-4911-9fa5-3c9517610ad6"
OWNER = Scope("user-a", "center-a", "principal:user-a")


@pytest.fixture
def journal(tmp_path):
    root = tmp_path / "ingress"
    initialize(root)
    revoked = set()
    policies = []

    @contextmanager
    def authority(scope):
        if scope.principal_id in revoked:
            raise PermissionError("revoked")
        yield

    store = IngressJournal(root, authority=authority,
                           admission_policy=lambda s, p: policies.append((s, p)))
    store.revoked = revoked
    store.policies = policies
    return store


def test_accept_ack_loss_reopen_and_exact_upload_bytes(journal):
    payload = b' {"message":"verbatim"}\r\n\x00\xff'
    first = journal.accept(OWNER, KEY, payload)
    reopened = IngressJournal(journal.path.parent, authority=journal.authority,
                              admission_policy=journal.admission_policy)
    retry = reopened.accept(OWNER, KEY, payload)
    assert retry == first
    assert retry.payload == payload
    assert retry.client_send_id == KEY
    assert journal.policies == [(OWNER, payload)]
    assert reopened.pending(OWNER) == [first]


def test_racing_accepts_commit_one_identity(journal):
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda _: journal.accept(OWNER, KEY, b"same"), range(8)))
    assert len({r.ingress_id for r in records}) == 1
    assert len(journal.pending(OWNER)) == 1
    with pytest.raises(Conflict):
        journal.accept(OWNER, KEY, b"different")
    assert journal.receipt(OWNER, KEY).payload == b"same"


@pytest.mark.parametrize("field", ["principal_id", "command_center_id", "thread_id", "operation"])
def test_each_scope_dimension_isolated(journal, field):
    first = journal.accept(OWNER, KEY, b"private")
    other = replace(OWNER, **{field: "other"})
    with pytest.raises(LookupError):
        journal.receipt(other, KEY)
    with pytest.raises(LookupError):
        journal.events(other, KEY)
    with pytest.raises(LookupError):
        journal.replay(other, KEY, lambda _: pytest.fail("foreign import"))
    second = journal.accept(other, KEY, b"other person's bytes")
    assert first.ingress_id != second.ingress_id
    assert journal.receipt(OWNER, KEY).payload == b"private"


def test_revocation_blocks_accept_import_and_result_reads(journal):
    journal.accept(OWNER, KEY, b"private")
    journal.revoked.add(OWNER.principal_id)
    actions = [lambda: journal.accept(OWNER, KEY, b"private"),
               lambda: journal.pending(OWNER), lambda: journal.receipt(OWNER, KEY),
               lambda: journal.events(OWNER, KEY),
               lambda: journal.replay(OWNER, KEY, lambda _: pytest.fail("revoked import")),
               lambda: journal.append(OWNER, KEY, 1, b"secret")]
    for action in actions:
        with pytest.raises(PermissionError, match="revoked"):
            action()


def test_no_acceptance_on_policy_or_commit_failure(journal, monkeypatch):
    def deny(*_args):
        raise PermissionError("quota")

    monkeypatch.setattr(journal, "admission_policy", deny)
    with pytest.raises(PermissionError, match="quota"):
        journal.accept(OWNER, KEY, b"input")
    assert journal.pending(OWNER) == []
    monkeypatch.setattr(journal, "admission_policy", lambda *_: None)
    connect = sqlite3.connect

    class NoCommit(sqlite3.Connection):
        def commit(self):
            raise sqlite3.OperationalError("injected fsync failure")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, **kw, factory=NoCommit))
        with pytest.raises(sqlite3.OperationalError, match="fsync"):
            journal.accept(OWNER, KEY, b"input")
    assert journal.pending(OWNER) == []


@pytest.mark.parametrize("key", ["", "a/b", "x" * 129, "\u00e9", None])
def test_invalid_client_send_key_refused(journal, key):
    with pytest.raises(ValueError):
        journal.accept(OWNER, key, b"input")
    assert journal.pending(OWNER) == []


def test_event_ack_loss_cursor_conflict_terminal_and_retention(journal):
    journal.accept(OWNER, KEY, b"input")
    assert not journal.expire(OWNER, KEY, terminal_before=float("inf"))
    journal.replay(OWNER, KEY, lambda _: "already-committed-admission")
    journal.append(OWNER, KEY, 1, b"partial")
    journal.append(OWNER, KEY, 1, b"partial")  # lost publication ack
    with pytest.raises(Conflict):
        journal.append(OWNER, KEY, 1, b"changed")
    with pytest.raises(Conflict):
        journal.append(OWNER, KEY, 3, b"gap")
    journal.append(OWNER, KEY, 2, b"final", terminal=True)
    journal.append(OWNER, KEY, 2, b"final", terminal=True)
    assert journal.events(OWNER, KEY, after=1) == [
        {"sequence": 2, "payload": b"final", "terminal": 1}]
    with pytest.raises(Conflict):
        journal.append(OWNER, KEY, 3, b"after terminal")
    assert not journal.expire(OWNER, KEY, terminal_before=0)
    assert journal.expire(OWNER, KEY, terminal_before=float("inf"))
    with pytest.raises(Expired):
        journal.accept(OWNER, KEY, b"input")
    with pytest.raises(Expired):
        journal.events(OWNER, KEY)
    with sqlite3.connect(journal.path) as conn:
        assert conn.execute("SELECT payload FROM requests").fetchone() == (None,)
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_import_loss_uses_real_atomic_conversation_admission(journal, tmp_path):
    from tinyassets.daemon_server import grant_universe_access, set_founder_home
    from tinyassets.runs import runs_db_path
    from tinyassets.storage import conversation_run_admissions as cr

    base = tmp_path / "runtime"
    (base / OWNER.command_center_id).mkdir(parents=True)
    set_founder_home(base, founder_sub=OWNER.principal_id,
                     universe_id=OWNER.command_center_id, platform_generated=True)
    grant_universe_access(base, universe_id=OWNER.command_center_id, actor_id=OWNER.principal_id,
                          permission="admin", granted_by=OWNER.principal_id)
    cr.initialize(base)
    with sqlite3.connect(runs_db_path(base)) as conn:
        initialize_imports(conn)
    accepted = journal.accept(OWNER, KEY, b"exact message")

    def importer(envelope, *, lose_ack=False, rollback=False):
        with cr.authorized_scope(base, owner=envelope.scope.principal_id,
                                 universe=envelope.scope.command_center_id) as scope:
            with cr.runs_transaction(scope) as conn:
                def reserve(writer, item):
                    assert writer is conn
                    return cr.reserve_in_transaction(writer, scope, request_key=item.client_send_id,
                        intent={"version": 1, "message": item.payload.decode(),
                                "input_method": "typed",
                                "model_choice": None, "binding_id": "b", "binding_revision": 1},
                        context={"version": 1, "history": []},
                        selection={"version": 1, "branch_def_id": "fixture", "reply_key": "reply"},
                        inputs={"message": item.payload.decode()})["admission_id"]
                result = import_in_transaction(conn, scope, envelope, reserve)
                if rollback:
                    raise RuntimeError("before admission commit")
        if lose_ack:
            raise RuntimeError("after admission commit")
        return result

    with pytest.raises(RuntimeError, match="before"):
        journal.replay(OWNER, KEY, lambda e: importer(e, rollback=True))
    with sqlite3.connect(runs_db_path(base)) as conn:
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM ingress_imports").fetchone()[0] == 0
    with pytest.raises(RuntimeError, match="after"):
        journal.replay(OWNER, KEY, lambda e: importer(e, lose_ack=True))
    assert journal.receipt(OWNER, KEY).state == "pending"
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: journal.replay(OWNER, KEY, importer), range(2)))
    assert ids[0] == ids[1]
    assert journal.receipt(OWNER, KEY).state == "imported"
    with sqlite3.connect(runs_db_path(base)) as conn:
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM ingress_imports").fetchone()[0] == 1
    with (cr.authorized_scope(base, owner=OWNER.principal_id,
                              universe=OWNER.command_center_id) as scope,
          cr.runs_transaction(scope) as conn):
        with pytest.raises(PermissionError):
            import_in_transaction(conn, scope,
                                  replace(accepted, scope=replace(OWNER, principal_id="other")),
                                  lambda *_: pytest.fail("cross-owner reservation"))
        for field in ("command_center_id", "thread_id", "operation"):
            with pytest.raises(PermissionError):
                import_in_transaction(conn, scope,
                                      replace(accepted, scope=replace(OWNER, **{field: "other"})),
                                      lambda *_: pytest.fail("aliased runtime scope"))
        with pytest.raises(Conflict):
            import_in_transaction(conn, scope, replace(accepted, client_send_id="changed"),
                                  lambda *_: pytest.fail("aliased client key"))
        with pytest.raises(Conflict):
            import_in_transaction(conn, scope, replace(accepted, payload=b"tampered"),
                                  lambda *_: pytest.fail("tampered reservation"))


def test_importer_cannot_return_another_principals_admission(journal, tmp_path):
    from tinyassets.daemon_server import grant_universe_access, set_founder_home
    from tinyassets.runs import runs_db_path
    from tinyassets.storage import conversation_run_admissions as cr

    base = tmp_path / "runtime"
    for owner, center in [(OWNER.principal_id, OWNER.command_center_id), ("user-b", "center-b")]:
        (base / center).mkdir(parents=True)
        set_founder_home(base, founder_sub=owner, universe_id=center, platform_generated=True)
        grant_universe_access(base, universe_id=center, actor_id=owner,
                              permission="admin", granted_by=owner)
    cr.initialize(base)
    with sqlite3.connect(runs_db_path(base)) as conn:
        initialize_imports(conn)
    with (cr.authorized_scope(base, owner="user-b", universe="center-b") as foreign,
          cr.runs_transaction(foreign) as conn):
        other = cr.reserve_in_transaction(conn, foreign, request_key=KEY,
            intent={"version": 1, "message": "private", "input_method": "typed",
                    "model_choice": None, "binding_id": "b", "binding_revision": 1},
            context={"version": 1, "history": []},
            selection={"version": 1, "branch_def_id": "fixture", "reply_key": "reply"},
            inputs={"message": "private"})
    accepted = journal.accept(OWNER, KEY, b"own input")
    with (cr.authorized_scope(base, owner=OWNER.principal_id,
                              universe=OWNER.command_center_id) as scope,
          cr.runs_transaction(scope) as conn):
        with pytest.raises(PermissionError, match="unavailable"):
            import_in_transaction(conn, scope, accepted, lambda *_: other["admission_id"])
        assert conn.execute("SELECT count(*) FROM ingress_imports").fetchone()[0] == 0
    assert journal.receipt(OWNER, KEY).state == "pending"


def test_unprovisioned_or_incompatible_schema_refused(journal, tmp_path):
    unopened = IngressJournal(tmp_path, authority=journal.authority,
                             admission_policy=journal.admission_policy)
    with pytest.raises(sqlite3.OperationalError):
        unopened.accept(OWNER, KEY, b"input")
    with sqlite3.connect(journal.path) as conn:
        conn.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="incompatible"):
        journal.accept(OWNER, KEY, b"input")
