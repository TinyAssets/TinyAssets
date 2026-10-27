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

    server = SimpleNamespace(_owner_identities=identities, _fence=Fence(), _policy_store=None,
                             ledger=(None, Conversations(root, identities)))

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
                        body='{"lane":"todo"}', expected="", private=False)

    def move(name, lane):
        start.wait()
        try:
            outcomes[name] = store.write("alice", caller=name, collection="board",
                                         row_id="task-1", body=f'{{"lane":"{lane}"}}',
                                         expected=first["etag"], private=False)
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
                      expected="", private=False)
    store.write("alice", caller="alice", collection="room:chess:main", row_id="x", body="{}",
                expected="", private=False)
    two = store.write("alice", caller="alice", collection="board", row_id="t2", body="[]",
                      expected="", private=False)
    page = store.pull("alice", caller="bob", collection="board", after=0, limit=1)
    assert page == {"cursor": 1, "rows": [one]} and one["body"] == body
    assert store.pull("alice", caller="bob", collection="board", after=1, limit=10) == {
        "cursor": 2, "rows": [two]}
    assert store.pull("alice", caller="bob", collection="board", after=2, limit=10) == {
        "cursor": 2, "rows": []}
    with pytest.raises(Conflict) as stale:
        store.write("alice", caller="bob", collection="board", row_id="t1", body="{}",
                    expected="", private=False)
    assert stale.value.current == one


def test_a_private_row_is_its_writers_own(broker):
    store = broker.store
    mine = store.write("alice", caller="bob", collection="room:chess:main", row_id="p",
                       body="secret", expected="", private=True)
    assert store.pull("alice", caller="cara", collection="room:chess:main", after=0,
                      limit=10) == {"cursor": 1, "rows": []}
    assert store.pull("alice", caller="bob", collection="room:chess:main", after=0,
                      limit=10)["rows"] == [mine]
    with pytest.raises(PermissionError):
        store.write("alice", caller="cara", collection="room:chess:main", row_id="p",
                    body="x", expected=mine["etag"], private=True)
    with pytest.raises(ValueError):
        store.write("alice", caller="bob", collection="room:chess:main", row_id="p",
                    body="x", expected=mine["etag"], private=False)
    assert _rows(broker.root)[0][-1] == "secret"


def test_a_full_collection_refuses_a_new_row(broker, monkeypatch):
    from tinyassets.broker import conversations

    monkeypatch.setattr(conversations, "MAX_ROWS", 1)
    store = broker.store
    row = store.write("alice", caller="alice", collection="board", row_id="a", body="1",
                      expected="", private=False)
    with pytest.raises(PermissionError, match="full"):
        store.write("alice", caller="alice", collection="board", row_id="b", body="1",
                    expected="", private=False)
    assert store.write("alice", caller="alice", collection="board", row_id="a", body="2",
                       expected=row["etag"], private=False)["revision"] == 2
    assert MAX_ROWS == 1000


def test_an_owners_collections_are_bounded_in_rows_and_bytes(broker, monkeypatch):
    """Across every collection of one owner; another owner's store is untouched."""
    from tinyassets.broker import conversations

    store = broker.store
    monkeypatch.setattr(conversations, "MAX_OWNER_ROWS", 2)
    monkeypatch.setattr(conversations, "MAX_OWNER_BYTES", 10)
    a = store.write("alice", caller="alice", collection="room:a:main", row_id="x", body="1234",
                    expected="", private=False)
    store.write("alice", caller="alice", collection="room:b:main", row_id="x", body="1234",
                expected="", private=False)
    with pytest.raises(PermissionError, match="owner's collections are full"):
        store.write("alice", caller="alice", collection="room:c:main", row_id="x", body="",
                    expected="", private=False)
    with pytest.raises(PermissionError, match="owner's collections are full"):
        store.write("alice", caller="alice", collection="room:a:main", row_id="x",
                    body="caféé", expected=a["etag"], private=False)  # 7 bytes
    assert store.write("alice", caller="alice", collection="room:a:main", row_id="x",
                       body="123456", expected=a["etag"], private=False)["revision"] == 2
    assert len(_rows(broker.root)) == 2
    assert store.write("bob", caller="bob", collection="room:c:main", row_id="x", body="1",
                       expected="", private=False)["revision"] == 1


def test_the_owner_channel_carries_the_router_caller_and_a_conflict(broker):
    """The router's verified caller is the writer; a lost swap is an answer, not a refusal."""
    from tinyassets.auth.provider import Identity
    from tinyassets.broker.client import BrokerRefused, CollectionConflict, collection_request

    bob = Identity(user_id="bob", username="bob")
    row = collection_request(broker.root, bob, "write", owner="alice", collection="board",
                             row_id="t", body="1", expected="", private=False)["row"]
    assert row["writer"] == "bob" and _rows(broker.root)[0][4] == "bob"
    with pytest.raises(CollectionConflict) as lost:
        collection_request(broker.root, bob, "write", owner="alice", collection="board",
                           row_id="t", body="2", expected="", private=False)
    assert lost.value.current == row and lost.value.etag == row["etag"]
    with pytest.raises(BrokerRefused, match="invalid collection request"):
        collection_request(broker.root, bob, "write", owner="alice", collection="board",
                           row_id="t", body="2", expected="", private=False, writer="alice")
    with pytest.raises(BrokerRefused, match="no admission"):
        collection_request(broker.root, bob, "pull", owner="cara", collection="board",
                           after=0, limit=1)
