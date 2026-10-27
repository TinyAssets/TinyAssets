"""S34: the generic collection, the repo's one revision primitive, in the broker store."""
from __future__ import annotations

import asyncio
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from tests.test_owner_policy import admissions
from tinyassets import rpc_frames as rf
from tinyassets.broker.conversations import MAX_ROWS, Conflict, Conversations
from tinyassets.broker.policy import PolicyStore, account_path

pytestmark = pytest.mark.verifies("trusted-message-ledger:One revision primitive")


def owner_channel(root, identities):
    """``_owner_request`` as the router's owner channel reaches the real broker op."""
    from tinyassets.broker.server import OWNER, _Connection

    class Fence:
        def admits(self, generation, token):
            return (generation, token) == (7, "fence")

    from tinyassets.broker.message_ledger import MessageLedger

    server = SimpleNamespace(_owner_identities=identities, _fence=Fence(),
                             _policy_store=PolicyStore(root, identities),
                             ledger=(MessageLedger(root, identities),
                                     Conversations(root, identities)))

    def request(data_root, doc):
        async def call():
            connection = _Connection(server, None, OWNER)
            await connection._connection_op(doc["op"], {**doc, "generation": 7,
                                                        "token": "fence"})
            return rf.Decoder().feed(connection._queue.get_nowait())[0].control()
        return asyncio.run(call())
    return request


@pytest.fixture
def broker(tmp_path, monkeypatch):
    """Alice and bob admitted with their policy bootstrapped; the router reaches the broker."""
    from tinyassets.broker import owner_identities

    identities = admissions(tmp_path)
    PolicyStore(tmp_path, identities).bootstrap()
    monkeypatch.setattr(owner_identities, "_owner_request", owner_channel(tmp_path, identities))
    return SimpleNamespace(root=tmp_path, store=Conversations(tmp_path, identities))


def _rows(root, owner="alice"):
    with sqlite3.connect(account_path(root, owner, "conversations.db")) as db:
        return db.execute("SELECT collection, row_id, revision, seq, writer, private, body "
                          "FROM collection_rows ORDER BY seq").fetchall()


def test_board_race_has_one_winner_and_one_conflict_carrying_the_current_row(broker):
    """Two writers present the same etag at once: one commits, the other gets the row."""
    store, start, outcomes = broker.store, threading.Barrier(2), {}
    first = store.write("alice", caller="alice", collection="board", row_id="task-1",
                        body='{"lane":"todo"}', expected_etag="", private=False)

    def move(name, lane):
        start.wait()
        try:
            outcomes[name] = store.write("alice", caller=name, collection="board",
                                         row_id="task-1", body=f'{{"lane":"{lane}"}}',
                                         expected_etag=first["etag"], private=False)
        except Conflict as exc:
            outcomes[name] = exc

    threads = [threading.Thread(target=move, args=args)
               for args in (("alice", "doing"), ("muse", "done"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    [winner] = [row for row in outcomes.values() if isinstance(row, dict)]
    [lost] = [exc for exc in outcomes.values() if isinstance(exc, Conflict)]
    assert lost.current == winner
    assert (winner["revision"], winner["seq"]) == (2, 2)
    assert _rows(broker.root) == [("board", "task-1", 2, 2, winner["writer"], 0,
                                   winner["body"])]


def test_payloads_are_verbatim_and_pull_follows_the_sequence(broker):
    store = broker.store
    body = '{ "b": 1,  "a": "café" }'
    one = store.write("alice", caller="alice", collection="board", row_id="t1", body=body,
                      expected_etag="", private=False)
    store.write("alice", caller="alice", collection="room:chess:main", row_id="x", body="{}",
                expected_etag="", private=False)
    two = store.write("alice", caller="alice", collection="board", row_id="t2", body="[]",
                      expected_etag="", private=False)
    page = store.pull("alice", caller="bob", collection="board", after=0, limit=1)
    assert page == {"cursor": 1, "rows": [one]} and one["body"] == body
    assert store.pull("alice", caller="bob", collection="board", after=1, limit=10) == {
        "cursor": 2, "rows": [two]}
    assert store.pull("alice", caller="bob", collection="board", after=2, limit=10) == {
        "cursor": 2, "rows": []}
    with pytest.raises(Conflict) as stale:
        store.write("alice", caller="bob", collection="board", row_id="t1", body="{}",
                    expected_etag="", private=False)
    assert stale.value.current == one


def test_a_private_row_is_its_writers_own(broker):
    store = broker.store
    mine = store.write("alice", caller="bob", collection="room:chess:main", row_id="p",
                       body="secret", expected_etag="", private=True)
    assert store.pull("alice", caller="cara", collection="room:chess:main", after=0,
                      limit=10) == {"cursor": 1, "rows": []}
    assert store.pull("alice", caller="bob", collection="room:chess:main", after=0,
                      limit=10)["rows"] == [mine]
    with pytest.raises(PermissionError):
        store.write("alice", caller="cara", collection="room:chess:main", row_id="p",
                    body="x", expected_etag=mine["etag"], private=True)
    with pytest.raises(ValueError):
        store.write("alice", caller="bob", collection="room:chess:main", row_id="p",
                    body="x", expected_etag=mine["etag"], private=False)
    assert _rows(broker.root)[0][-1] == "secret"


def test_get_reads_one_row_by_key_as_pull_would_show_it(broker):
    """A keyed read, not a second swap: a private row is its writer's, the caller the router's."""
    from tinyassets.auth.provider import Identity
    from tinyassets.broker.client import BrokerRefused, collection_request

    store, room = broker.store, "room:chess:main"
    mine = store.write("alice", caller="bob", collection=room, row_id="p", body="secret",
                       expected_etag="", private=True)
    shared = store.write("alice", caller="bob", collection=room, row_id="s", body="turn",
                         expected_etag="", private=False)
    assert store.get("alice", caller="bob", collection=room, row_id="p") == {"row": mine}
    assert store.get("alice", caller="cara", collection=room, row_id="p") == {"row": None}
    assert store.get("alice", caller="cara", collection=room, row_id="s") == {"row": shared}
    assert store.get("alice", caller="bob", collection=room, row_id="none") == {"row": None}
    assert store.get("alice", caller="bob", collection="board", row_id="s") == {"row": None}
    cara = Identity(user_id="cara", username="cara")
    over = collection_request(broker.root, cara, "get", owner="alice", collection=room,
                              row_id="s")
    assert over == {"row": shared}
    assert collection_request(broker.root, cara, "get", owner="alice", collection=room,
                              row_id="p") == {"row": None}
    with pytest.raises(BrokerRefused, match="invalid collection request"):
        collection_request(broker.root, cara, "get", owner="alice", collection=room,
                           row_id="p", writer="bob")
    with pytest.raises(BrokerRefused):
        collection_request(broker.root, cara, "get", owner="alice", collection=room,
                           row_id="")
    assert [row[1] for row in _rows(broker.root)] == ["p", "s"]  # a get writes nothing


def test_a_full_collection_refuses_a_new_row(broker, monkeypatch):
    from tinyassets.broker import conversations

    monkeypatch.setattr(conversations, "MAX_ROWS", 1)
    store = broker.store
    row = store.write("alice", caller="alice", collection="board", row_id="a", body="1",
                      expected_etag="", private=False)
    with pytest.raises(PermissionError, match="full"):
        store.write("alice", caller="alice", collection="board", row_id="b", body="1",
                    expected_etag="", private=False)
    assert store.write("alice", caller="alice", collection="board", row_id="a", body="2",
                       expected_etag=row["etag"], private=False)["revision"] == 2
    assert MAX_ROWS == 1000


def test_an_owners_collections_are_bounded_in_rows_and_bytes(broker, monkeypatch):
    """Across every collection of one owner; another owner's store is untouched."""
    from tinyassets.broker import conversations

    store = broker.store
    monkeypatch.setattr(conversations, "MAX_OWNER_ROWS", 2)
    monkeypatch.setattr(conversations, "MAX_OWNER_BYTES", 10)
    a = store.write("alice", caller="alice", collection="room:a:main", row_id="x", body="1234",
                    expected_etag="", private=False)
    store.write("alice", caller="alice", collection="room:b:main", row_id="x", body="1234",
                expected_etag="", private=False)
    with pytest.raises(PermissionError, match="owner's collections are full"):
        store.write("alice", caller="alice", collection="room:c:main", row_id="x", body="",
                    expected_etag="", private=False)
    with pytest.raises(PermissionError, match="owner's collections are full"):
        store.write("alice", caller="alice", collection="room:a:main", row_id="x",
                    body="caféé", expected_etag=a["etag"], private=False)  # 7 bytes
    assert store.write("alice", caller="alice", collection="room:a:main", row_id="x",
                       body="123456", expected_etag=a["etag"], private=False)["revision"] == 2
    assert len(_rows(broker.root)) == 2
    assert store.write("bob", caller="bob", collection="room:c:main", row_id="x", body="1",
                       expected_etag="", private=False)["revision"] == 1


def test_the_owner_channel_carries_the_router_caller_and_a_conflict(broker):
    """The router's verified caller is the writer; a lost swap is an answer, not a refusal."""
    from tinyassets.auth.provider import Identity
    from tinyassets.broker.client import BrokerRefused, CollectionConflict, collection_request

    bob = Identity(user_id="bob", username="bob")
    row = collection_request(broker.root, bob, "write", owner="alice", collection="board",
                             row_id="t", body="1", expected_etag="", private=False)["row"]
    assert row["writer"] == "bob" and _rows(broker.root)[0][4] == "bob"
    with pytest.raises(CollectionConflict) as lost:
        collection_request(broker.root, bob, "write", owner="alice", collection="board",
                           row_id="t", body="2", expected_etag="", private=False)
    assert lost.value.current == row and lost.value.etag == row["etag"]
    with pytest.raises(BrokerRefused, match="invalid collection request"):
        collection_request(broker.root, bob, "write", owner="alice", collection="board",
                           row_id="t", body="2", expected_etag="", private=False, writer="alice")
    with pytest.raises(BrokerRefused, match="no admission"):
        collection_request(broker.root, bob, "pull", owner="cara", collection="board",
                           after=0, limit=1)


def test_a_player_at_its_row_share_still_takes_a_turn_on_a_shared_row(broker, monkeypatch):
    """Only a creation counts against the share; a takeover charges the row's bytes."""
    from tinyassets.broker import conversations

    store, room = broker.store, "room:go:main"
    monkeypatch.setattr(conversations, "MAX_WRITER_ROWS", 2)

    def put(caller, row_id, expected_etag=""):
        return store.write("alice", caller=caller, collection=room, row_id=row_id,
                           body="stone", expected_etag=expected_etag, private=False)

    cell0 = put("bob", "cell0")
    put("cara", "cell1")
    put("cara", "cell2")
    with pytest.raises(PermissionError, match="this writer's share"):
        put("cara", "cell3")
    assert put("cara", "cell0", cell0["etag"])["writer"] == "cara"
    monkeypatch.setattr(conversations, "MAX_WRITER_BYTES", 4)
    with pytest.raises(PermissionError, match="this writer's share"):
        put("bob", "cell1", _rows_by_id(broker.root)["cell1"])  # takes over all 5 bytes


def test_two_accounts_cannot_launder_a_row_share(broker, monkeypatch):
    """Rows count against their creator: a takeover by a second account frees nothing."""
    from tinyassets.auth.provider import Identity
    from tinyassets.broker import conversations
    from tinyassets.broker.client import collection_request

    store, room = broker.store, "room:go:main"
    monkeypatch.setattr(conversations, "MAX_WRITER_ROWS", 2)

    def put(caller, row_id, expected_etag="", body="xx"):
        return store.write("alice", caller=caller, collection=room, row_id=row_id,
                           body=body, expected_etag=expected_etag, private=False)

    made = [put("m1", f"r{n}") for n in range(2)]
    for row in made:
        put("m2", row["row_id"], row["etag"], body="")  # m2 takes both over
    with pytest.raises(PermissionError, match="this writer's share"):
        put("m1", "r2")  # m1's rows still exist, under m2's name
    put("honest", "h0")
    put("honest", "h1")
    # The owner's clear-by-writer matches creator or writer: m1's rows go, though m2
    # last wrote them, and m1 may create again.
    alice = Identity(user_id="alice", username="alice")
    assert collection_request(broker.root, alice, "clear", owner="alice", collection=room,
                              writer="m1") == {"deleted": 2}
    put("m1", "r2")
    assert sorted(_rows_by_id(broker.root)) == ["h0", "h1", "r2"]


def test_a_row_from_before_creators_is_its_writers(broker):
    path = account_path(broker.root, "alice", "conversations.db")
    path.touch(mode=0o600)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE collection_rows (collection TEXT NOT NULL, row_id TEXT NOT "
                   "NULL, revision INTEGER NOT NULL, seq INTEGER NOT NULL, etag TEXT NOT NULL, "
                   "writer TEXT NOT NULL, private INTEGER NOT NULL, body TEXT NOT NULL, "
                   "updated_at REAL NOT NULL, PRIMARY KEY(collection, row_id), "
                   "UNIQUE(collection, seq))")
        db.execute("INSERT INTO collection_rows VALUES ('board','a',1,1,'e','bob',0,'x',0)")
    broker.store.pull("alice", caller="bob", collection="board", after=0, limit=1)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT creator FROM collection_rows").fetchall() == [("bob",)]


def _rows_by_id(root):
    with sqlite3.connect(account_path(root, "alice", "conversations.db")) as db:
        return dict(db.execute("SELECT row_id, etag FROM collection_rows"))


def test_a_seq_collision_fails_loudly_and_deletes_nothing(broker, monkeypatch):
    """A missing high-water mark still numbers past the rows; a clash never replaces one."""
    store = broker.store

    def put(row_id):
        return store.write("alice", caller="alice", collection="board", row_id=row_id,
                           body=row_id, expected_etag="", private=False)

    assert put("a")["seq"] == 1
    with sqlite3.connect(account_path(broker.root, "alice", "conversations.db")) as db:
        db.execute("DELETE FROM collection_seqs")  # as a store written before the table
    assert put("b")["seq"] == 2
    monkeypatch.setattr(Conversations, "_top", staticmethod(lambda db, collection: 0))
    with pytest.raises(sqlite3.IntegrityError):  # seq 1 is row a's
        put("c")
    assert sorted(_rows_by_id(broker.root)) == ["a", "b"]


def test_one_visitor_fills_only_its_own_share_and_the_owner_clears(broker, monkeypatch):
    """A visitor stops at its share; another still writes; one room cannot starve another."""
    from tinyassets.auth.provider import Identity
    from tinyassets.broker import conversations
    from tinyassets.broker.client import BrokerRefused, collection_request

    store, room = broker.store, "room:chess:main"
    monkeypatch.setattr(conversations, "MAX_WRITER_ROWS", 2)
    monkeypatch.setattr(conversations, "MAX_COLLECTION_BYTES", 6)

    def put(caller, row_id, body="1", collection=room):
        return store.write("alice", caller=caller, collection=collection, row_id=row_id,
                           body=body, expected_etag="", private=True)

    put("mallory", "a")
    put("mallory", "b")
    with pytest.raises(PermissionError, match="this writer's share"):
        put("mallory", "c")
    put("cara", "c1")
    with pytest.raises(PermissionError, match="the collection is full"):
        put("alice", "big", body="1234")  # 3 used + 4 > 6
    put("alice", "big", body="1234", collection="room:go:main")
    alice = Identity(user_id="alice", username="alice")
    with pytest.raises(BrokerRefused, match="only the owner"):
        collection_request(broker.root, Identity("mallory", "mallory"), "clear",
                           owner="alice", collection=room, writer=None)
    assert collection_request(broker.root, alice, "clear", owner="alice", collection=room,
                              writer="mallory") == {"deleted": 2}
    put("mallory", "c")
    assert collection_request(broker.root, alice, "clear", owner="alice", collection=room,
                              writer=None) == {"deleted": 2}
    # A clear never moves a reader's cursor back: the next row is past every old seq.
    assert put("cara", "z")["seq"] == 5
    assert store.pull("alice", caller="cara", collection=room, after=4, limit=10)[
        "cursor"] == 5
