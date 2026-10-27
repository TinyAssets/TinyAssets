"""S5b: the task board on the one revision primitive, answered on the trusted side (W8)."""
# ruff: noqa: F811 -- imported pytest fixtures
from __future__ import annotations

import ast
import asyncio
import json
import socket
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tests.test_mailbox_door import call, door, served, sse  # noqa: F401
from tests.test_owner_policy import admissions
from tests.test_revision_primitive import _PERSISTS, _REVISION_CAS, BOX_PATHS, primitives
from tinyassets import board_door, owner_router
from tinyassets import rpc_frames as rf
from tinyassets.auth.provider import Identity
from tinyassets.board_door import Conflict, Refused
from tinyassets.broker.conversations import MAX_ROW, Conversations
from tinyassets.broker.message_ledger import MessageLedger
from tinyassets.broker.policy import PolicyStore, account_path

pytestmark = pytest.mark.verifies("trusted-message-ledger:The board")
MUSE = {"client": "a" * 64, "family": "b" * 64}
DOT = {"client": "c" * 64, "family": "d" * 64}
OWNER = Identity("alice", "alice")


def _agent(origin):
    return Identity("alice", "alice", metadata={"outside_origin": dict(origin)})


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """The real store, ledger and broker dispatch on the router's owner channel."""
    from tinyassets.broker import owner_identities
    from tinyassets.broker.server import OWNER as OWNER_ROLE
    from tinyassets.broker.server import _Connection

    class Fence:
        def admits(self, generation, token):
            return True

    identities = admissions(tmp_path)
    PolicyStore(tmp_path, identities).bootstrap()
    ledger, revoked = MessageLedger(tmp_path, identities), []
    callbacks = SimpleNamespace(revoke_agent=lambda owner, agent: revoked.append((owner, agent)))
    server = SimpleNamespace(_owner_identities=identities, _fence=Fence(),
                             _policy_store=PolicyStore(tmp_path, identities),
                             ledger=(ledger, Conversations(tmp_path, identities)),
                             mcp_events=SimpleNamespace(kick=lambda: None, store=callbacks))

    def request(data_root, doc):
        async def exchange():
            connection = _Connection(server, None, OWNER_ROLE)
            await connection._connection_op(doc["op"], dict(doc))
            return rf.Decoder().feed(connection._queue.get_nowait())[0].control()
        return asyncio.run(exchange())

    monkeypatch.setattr(owner_identities, "_owner_request", request)
    monkeypatch.setattr("tinyassets.storage.data_dir", lambda: tmp_path)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    clock = [1_000_000.0]
    monkeypatch.setattr(board_door, "time", SimpleNamespace(time=lambda: clock[0]))

    def register(origin, name, delivery="pull"):
        return ledger.register("alice", client=origin["client"], family=origin["family"],
                               command_center_id="cc-" + name, source="mcp", source_ref="conn",
                               display_name=name, delivery_method=delivery,
                               recipe_id=None)["agent_id"]

    def db(name):
        conn = sqlite3.connect(account_path(tmp_path, "alice", name))
        conn.row_factory = sqlite3.Row
        return conn

    def stored(task_id):
        with db("conversations.db") as conn:
            return json.loads(conn.execute("SELECT body FROM collection_rows WHERE "
                                           "collection='board' AND row_id=?",
                                           (task_id,)).fetchone()[0])

    return SimpleNamespace(root=tmp_path, ledger=ledger, revoked=revoked, clock=clock, db=db,
                           stored=stored,
                           muse=register(MUSE, "Muse"), dot=register(DOT, "Dot", "mcp_event"))


def _create(task_id="t1", **patch):
    return board_door.write(OWNER, task_id=task_id, expected_revision=0,
                            patch={"title": "Ship it", **patch})["task"]


def test_two_agents_race_for_one_claim_one_wins_and_the_other_sees_it(rig, monkeypatch):
    """Two vendors' agents present one revision at once: the store's etag decides."""
    created = _create(assignees=["everyone"])
    real, gate, outcomes = board_door._store, threading.Barrier(2), {}

    def store(identity, actor, action, **fields):
        if action == "write":  # both read the same row and etag before either writes
            gate.wait(timeout=10)
        return real(identity, actor, action, **fields)

    monkeypatch.setattr(board_door, "_store", store)

    def claim(name, origin):
        try:
            outcomes[name] = board_door.write(_agent(origin), task_id="t1",
                                              expected_revision=created["revision"],
                                              patch={"state": "claimed"})
        except Conflict as exc:
            outcomes[name] = exc

    threads = [threading.Thread(target=claim, args=args)
               for args in (("muse", MUSE), ("dot", DOT))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    [won] = [o for o in outcomes.values() if isinstance(o, dict)]
    [lost] = [o for o in outcomes.values() if isinstance(o, Conflict)]
    winner = won["task"]["claim"]["agent_id"]
    assert winner in {rig.muse, rig.dot} and won["task"]["state"] == "claimed"
    assert (lost.status, lost.reason) == (409, "the task changed while this write was made")
    assert lost.task["claim"]["agent_id"] == winner and lost.task["revision"] == 2
    with rig.db("message_ledger.db") as conn:
        assert [tuple(r) for r in conn.execute(
            "SELECT agent_id, kind, ref FROM ledger_receipts WHERE kind='claim'")] == [
            (winner, "claim", "t1")]


def test_a_stale_revision_returns_the_current_task_and_changes_nothing(rig):
    from tinyassets.untrusted import envelope

    created = _create()
    board_door.write(OWNER, task_id="t1", expected_revision=1, patch={"body": "v2"})
    with pytest.raises(Conflict) as stale:
        board_door.write(_agent(MUSE), task_id="t1", expected_revision=created["revision"],
                         patch={"state": "claimed"})
    assert stale.value.body()["error"] == "conflict"
    current = stale.value.task
    assert (current["revision"], current["state"], current["claim"]) == (2, "open", None)
    # The owner's words reach an outside agent enveloped, as mailbox bodies do.
    assert current["body"] == envelope("board:owner", "v2")
    with pytest.raises(Conflict) as missing:  # a write naming a task that does not exist
        board_door.write(OWNER, task_id="t9", expected_revision=3, patch={"title": "x"})
    assert missing.value.task is None
    board_door.write(_agent(MUSE), task_id="t1", expected_revision=2,
                     patch={"state": "claimed"})
    with pytest.raises(Conflict, match="not open") as taken:  # read after the claim landed
        board_door.write(_agent(DOT), task_id="t1", expected_revision=3,
                         patch={"state": "claimed"})
    assert taken.value.task["claim"]["agent_id"] == rig.muse
    assert rig.stored("t1")["claim"]["agent_id"] == rig.muse


def test_an_expired_lease_reopens_on_read_and_persists_on_the_next_write(rig):
    _create()
    claimed = board_door.write(_agent(DOT), task_id="t1", expected_revision=1,
                               patch={"state": "claimed"})["task"]
    # A live (mcp_event) agent's lease is minutes; a check-in (pull) agent's is hours.
    expires = claimed["claim"]["expires_at"]
    assert expires - rig.clock[0] == board_door.LIVE_LEASE_S
    rig.clock[0] += 60  # progress renews the lease
    moved = board_door.write(_agent(DOT), task_id="t1", expected_revision=2,
                             patch={"state": "in_progress"})["task"]
    assert moved["claim"]["expires_at"] == rig.clock[0] + board_door.LIVE_LEASE_S
    rig.clock[0] = moved["claim"]["expires_at"] + 1
    read = board_door.task(OWNER, task_id="t1")["task"]
    lapse = {"at": moved["claim"]["expires_at"], "actor": "board", "op": "lease_expired",
             "from_state": "in_progress", "to_state": "open"}
    assert (read["state"], read["claim"], read["history"][-1]) == ("open", None, lapse)
    assert rig.stored("t1")["state"] == "in_progress"  # a read writes nothing (ADR-026)
    assert board_door.board(OWNER)["tasks"][0]["state"] == "open"
    # The next write persists the reopen and its history entry, then its own change.
    taken = board_door.write(_agent(MUSE), task_id="t1", expected_revision=3,
                             patch={"state": "claimed"})["task"]
    assert taken["claim"] == {"agent_id": rig.muse,
                              "expires_at": rig.clock[0] + board_door.CHECKIN_LEASE_S}
    assert [(e["actor"], e["op"], e["from_state"], e["to_state"])
            for e in rig.stored("t1")["change"]] == [
        ("board", "lease_expired", "in_progress", "open"),
        (rig.muse, "claim", "open", "claimed")]


def test_a_claim_with_open_dependencies_is_refused(rig):
    _create("t1")
    _create("t2", depends_on=["t1"])
    with pytest.raises(Refused) as refused:
        board_door.write(_agent(MUSE), task_id="t2", expected_revision=1,
                         patch={"state": "claimed"})
    assert (refused.value.status, refused.value.reason) == (409, board_door.DEPS_OPEN)
    board_door.write(OWNER, task_id="t1", expected_revision=1, patch={"state": "claimed"})
    done = board_door.write(OWNER, task_id="t1", expected_revision=2, patch={"state": "done"})
    assert done["task"]["claim"] is None
    claimed = board_door.write(_agent(MUSE), task_id="t2", expected_revision=1,
                               patch={"state": "claimed"})
    assert claimed["task"]["state"] == "claimed"
    with rig.db("message_ledger.db") as conn:
        assert [tuple(r) for r in conn.execute(
            "SELECT agent_id, kind, ref FROM ledger_receipts WHERE kind IN "
            "('claim','complete') ORDER BY seq")] == [
            ("owner", "claim", "t1"), ("owner", "complete", "t1"), (rig.muse, "claim", "t2")]


def test_a_receipt_naming_an_agent_revokes_nothing_only_a_registry_change_does(rig):
    """The broker revokes callbacks on an OUTSIDE_AGENTS change, never on another result."""
    from tests.test_message_ledger import COOKIE, PROOF
    from tests.test_owner_policy import _session
    from tinyassets.broker.client import ledger_request

    _create()
    claimed = board_door.write(_agent(MUSE), task_id="t1", expected_revision=1,
                               patch={"state": "claimed"})
    assert "receipt" not in claimed  # the receipt result carries agent_id and was recorded
    receipt = ledger_request(rig.root, _agent(MUSE), "MAILBOX_RECEIPT", "board", task_id="t1")
    assert receipt == {"task_id": "t1", "recorded": []} and rig.revoked == []
    _session(rig.root, "alice", COOKIE)
    ledger_request(rig.root, OWNER, "OUTSIDE_AGENTS", "disable", session_proof=PROOF,
                   agent_id=rig.muse, reason="done")
    assert rig.revoked == [("alice", rig.muse)]


def test_only_the_owner_assigns_and_only_the_claimant_or_owner_moves_a_task(rig):
    agent = _agent(MUSE)
    with pytest.raises(Refused, match="only the owner assigns"):
        board_door.write(agent, task_id="t1", expected_revision=0,
                         patch={"title": "mine", "assignees": [rig.muse]})
    _create(assignees=[rig.dot])
    with pytest.raises(Refused, match="assigned to other agents"):
        board_door.write(agent, task_id="t1", expected_revision=1, patch={"state": "claimed"})
    board_door.write(_agent(DOT), task_id="t1", expected_revision=1, patch={"state": "claimed"})
    for patch in ({"state": "done"}, {"title": "renamed"}, {"state": "open"}):
        with pytest.raises(Refused, match="only the claimant or the owner"):
            board_door.write(agent, task_id="t1", expected_revision=2, patch=patch)
    with pytest.raises(Refused, match="enabled agent ids or everyone"):
        board_door.write(OWNER, task_id="t1", expected_revision=2,
                         patch={"assignees": ["agent_nobody"]})
    assert board_door.write(OWNER, task_id="t1", expected_revision=2,
                            patch={"state": "done"})["task"]["state"] == "done"


def test_history_names_the_verified_actor_never_a_patch_field(rig):
    from tinyassets.untrusted import envelope

    agent = _agent(MUSE)
    for patch in ({"title": "x", "actor": "owner"}, {"title": "x", "history": []},
                  {"title": "x", "claim": {"agent_id": "owner"}}, ["title"]):
        with pytest.raises(Refused, match="patch takes"):
            board_door.write(agent, task_id="t1", expected_revision=0, patch=patch)
    with pytest.raises(Refused, match="payload_json takes task_id and patch"):
        board_door.tool(agent, "write_graph", "board_task", {"payload_json": json.dumps(
            {"task_id": "t1", "patch": {"title": "x"}, "actor": "owner"})})
    board_door.tool(agent, "write_graph", "board_task", {"payload_json": json.dumps(
        {"task_id": "t1", "patch": {"title": "From the agent", "body": "do X"}})})
    made = board_door.tool(agent, "read_graph", "board_task", {"query": "t1"})["task"]
    assert [(e["actor"], e["op"]) for e in made["history"]] == [(rig.muse, "create")]
    # The agent's words reach the owner enveloped, and every word reaches an agent so.
    mine = board_door.task(OWNER, task_id="t1")["task"]
    assert mine["title"] == envelope(f"board:{rig.muse}", "From the agent")
    assert board_door.tool(_agent(DOT), "read_graph", "board_task", {"query": "t1"})[
        "task"]["body"] == envelope(f"board:{rig.muse}", "do X")


def test_assigning_rings_each_new_assignee_in_its_dm_room(rig):
    made = board_door.write(OWNER, task_id="t1", expected_revision=0, patch={
        "title": "Secret title", "assignees": [rig.muse]})
    assert made["notified"] == [rig.muse]
    again = board_door.write(OWNER, task_id="t1", expected_revision=1,
                             patch={"assignees": [rig.muse, "everyone"]})
    assert again["notified"] == sorted([rig.muse, rig.dot])
    with rig.db("message_ledger.db") as conn:
        rows = conn.execute("SELECT room_id, sender_id FROM ledger_messages ORDER BY seq")
        assert [tuple(r) for r in rows] == [
            (f"dm:{rig.muse}", "owner"), *sorted((f"dm:{a}", "owner")
                                                 for a in (rig.muse, rig.dot))]
    with rig.db("conversations.db") as conn:
        bodies = [r[0] for r in conn.execute("SELECT body FROM message_bodies")]
    # A doorbell names the task, never its words.
    assert all("t1" in body and "Secret" not in body for body in bodies) and len(bodies) == 3


def test_a_board_read_pages_under_the_ceiling_and_bounds_its_fields(rig, monkeypatch):
    from tinyassets.engine_result_bounds import CEILING_ENV

    monkeypatch.setenv(CEILING_ENV, "4096")
    for n in range(12):
        _create(f"t{n}", title="x" * board_door.MAX_TITLE)
    seen, cursor = [], 0
    while True:
        page = board_door.board(OWNER, since=cursor, limit=100)
        seen += [task["task_id"] for task in page["tasks"]]
        assert len(json.dumps(page).encode()) < 4096
        if not page["more"]:
            break
        cursor = page["next_cursor"]
    assert seen == [f"t{n}" for n in range(12)]
    for patch in ({"title": "x" * (board_door.MAX_TITLE + 1)},
                  {"body": "x" * (board_door.MAX_TEXT + 1)},
                  {"depends_on": [f"d{n}" for n in range(board_door.MAX_IDS + 1)]},
                  {"result_refs": ["r" * (board_door.MAX_REF + 1)]}):
        with pytest.raises(Refused) as refused:
            board_door.write(OWNER, task_id="t0", expected_revision=1, patch=patch)
        assert refused.value.status == 400


def test_a_disabled_agent_reads_nothing(rig):
    _create()
    rig.ledger.disable("alice", agent_id=rig.muse, reason="done")
    for read in (lambda: board_door.board(_agent(MUSE)),
                 lambda: board_door.task(_agent(MUSE), task_id="t1")):
        with pytest.raises(Refused, match="not one enabled agent"):
            read()


def test_an_agent_writes_as_its_own_bounded_writer_and_the_owner_clears_it(rig, monkeypatch):
    """An agent is ``agent:<id>`` in the store: its share binds it, never the owner."""
    from tinyassets.broker import conversations
    from tinyassets.broker.client import collection_request

    monkeypatch.setattr(conversations, "MAX_WRITER_ROWS", 2)
    muse = _agent(MUSE)
    for n in range(2):
        board_door.write(muse, task_id=f"m{n}", expected_revision=0, patch={"title": "mine"})
    with pytest.raises(Refused, match="this writer's share") as full:
        board_door.write(muse, task_id="m2", expected_revision=0, patch={"title": "mine"})
    assert full.value.status == 403
    for n in range(3):  # the owner is never a bounded writer
        _create(f"o{n}")
    with rig.db("conversations.db") as conn:
        writers = dict(conn.execute("SELECT row_id, writer FROM collection_rows"))
        history = {tuple(r) for r in conn.execute("SELECT key, writer FROM collection_history")}
    assert writers == {"m0": f"agent:{rig.muse}", "m1": f"agent:{rig.muse}",
                       "o0": "alice", "o1": "alice", "o2": "alice"}
    assert ("m0", f"agent:{rig.muse}") in history
    # The owner clears one agent's rows by its writer id; the agent may create again.
    assert collection_request(rig.root, OWNER, "clear", owner="alice", collection="board",
                              writer=f"agent:{rig.muse}") == {"deleted": 2}
    board_door.write(muse, task_id="m2", expected_revision=0, patch={"title": "mine"})
    assert [t["task_id"] for t in board_door.board(OWNER)["tasks"]] == ["o0", "o1", "o2", "m2"]


def test_only_the_owner_deletes_a_task_at_the_revision_it_read(rig):
    from tinyassets.broker.client import BrokerRefused, CollectionConflict, collection_request

    _create()
    board_door.write(OWNER, task_id="t1", expected_revision=1, patch={"body": "v2"})
    for refused in (lambda: board_door.delete(_agent(MUSE), task_id="t1", expected_revision=2),
                    lambda: board_door.tool(_agent(MUSE), "write_graph", "board_task", {
                        "operation": "delete", "payload_json": '{"task_id": "t1"}',
                        "expected_revision": 2})):
        with pytest.raises(Refused, match="only the owner deletes") as agent:
            refused()
        assert agent.value.status == 403
    row = board_door._row(OWNER, "owner", "t1")
    with pytest.raises(BrokerRefused, match="only the owner"):  # nor at the store
        collection_request(rig.root, _agent(MUSE), "delete", owner="alice", agent_id=rig.muse,
                           collection="board", row_id="t1", expected_etag=row["etag"])
    with pytest.raises(Conflict) as stale:
        board_door.delete(OWNER, task_id="t1", expected_revision=1)
    assert stale.value.task["revision"] == 2
    with pytest.raises(CollectionConflict) as lost:  # a stale etag loses the swap
        collection_request(rig.root, OWNER, "delete", owner="alice", collection="board",
                           row_id="t1", expected_etag="0" * 64)
    assert lost.value.current["etag"] == row["etag"]
    cursor = board_door.board(OWNER)["next_cursor"]
    assert board_door.delete(OWNER, task_id="t1", expected_revision=2) == {
        "deleted": "t1", "revision": 2}
    with pytest.raises(Refused, match="no board task"):
        board_door.task(OWNER, task_id="t1")
    # No tombstone reaches a pull, and the cursor never goes back; the id made again
    # goes on past the delete's revision, so no stale revision matches it.
    assert board_door.board(OWNER, since=cursor)["tasks"] == []
    again = _create()
    assert (again["revision"], again["seq"]) == (4, cursor + 1)
    assert [e["op"] for e in board_door.task(OWNER, task_id="t1")["task"]["history"]] == [
        "create"]


def test_an_escape_heavy_task_is_refused_before_the_store(rig):
    patch = {"title": '"' * board_door.MAX_TITLE, "body": "\x00" * board_door.MAX_TEXT,
             "result_refs": ['"' * board_door.MAX_REF] * board_door.MAX_IDS,
             "depends_on": [f"{n:02d}" + "d" * 62 for n in range(board_door.MAX_IDS)]}
    with pytest.raises(Refused) as refused:
        board_door.write(OWNER, task_id="t1", expected_revision=0, patch=patch)
    assert (refused.value.status, refused.value.reason) == (400, board_door.TOO_LARGE)
    assert board_door.MAX_TASK == MAX_ROW
    assert board_door.board(OWNER)["tasks"] == []


def test_a_receipt_that_did_not_land_is_mirrored_on_the_next_write_once(rig, monkeypatch):
    real, down = board_door._ledger, [True]

    def ledger(identity, op, action, **fields):
        if op == "MAILBOX_RECEIPT" and down[0]:
            raise Refused(503, "ledger_unavailable")
        return real(identity, op, action, **fields)

    monkeypatch.setattr(board_door, "_ledger", ledger)
    _create()
    claimed = board_door.write(_agent(MUSE), task_id="t1", expected_revision=1,
                               patch={"state": "claimed"})
    assert claimed["receipt"] == "unrecorded"

    def receipts():
        with rig.db("message_ledger.db") as conn:
            return [(r[0], r[1], json.loads(r[2])["revision"]) for r in conn.execute(
                "SELECT agent_id, kind, detail_json FROM ledger_receipts WHERE ref='t1' "
                "ORDER BY seq")]

    assert receipts() == []
    down[0] = False  # the history is the truth: the next write, by anyone, mirrors it
    board_door.write(OWNER, task_id="t1", expected_revision=2, patch={"body": "note"})
    assert receipts() == [(rig.muse, "claim", 2)]
    board_door.write(_agent(MUSE), task_id="t1", expected_revision=3, patch={"state": "done"})
    assert receipts() == [(rig.muse, "claim", 2), (rig.muse, "complete", 4)]


def test_a_claim_whose_holder_is_disabled_lapses_on_the_next_read(rig):
    _create()
    board_door.write(_agent(MUSE), task_id="t1", expected_revision=1,
                     patch={"state": "claimed"})
    rig.clock[0] += 60
    rig.ledger.disable("alice", agent_id=rig.muse, reason="gone")
    read = board_door.task(OWNER, task_id="t1")["task"]
    assert (read["state"], read["claim"]) == ("open", None)
    assert read["history"][-1] == {"at": rig.clock[0], "actor": "board",
                                   "op": "lease_expired", "from_state": "claimed",
                                   "to_state": "open"}
    taken = board_door.write(_agent(DOT), task_id="t1", expected_revision=2,
                             patch={"state": "claimed"})["task"]
    assert taken["claim"]["agent_id"] == rig.dot


def test_an_assignment_with_a_state_change_is_in_its_history_entry(rig):
    _create()
    board_door.write(OWNER, task_id="t1", expected_revision=1,
                     patch={"assignees": [rig.muse], "state": "claimed"})
    [entry] = rig.stored("t1")["change"]
    assert (entry["op"], entry["assigned"]) == ("claim", [rig.muse])
    with pytest.raises(Refused, match="cursor is an integer"):
        board_door.tool(OWNER, "read_graph", "board", {"cursor": -1})
    with pytest.raises(Refused, match="query is 1 to 64"):
        board_door.tool(OWNER, "read_graph", "board_task", {"query": ""})


# -- The doors: registered on the router, never a box -----------------------------


def test_the_board_is_rows_in_the_one_primitive_and_its_door_names_no_way_into_a_box():
    """The door compares a revision but persists nothing: the store's etag is the swap."""
    source = Path(board_door.__file__).read_text(encoding="utf-8")
    assert _REVISION_CAS.search(source) and not _PERSISTS.search(source)
    assert "tinyassets/board_door.py" not in primitives()
    names = {node.id if isinstance(node, ast.Name) else node.attr
             for node in ast.walk(ast.parse(source))
             if isinstance(node, (ast.Name, ast.Attribute))}
    assert not names & BOX_PATHS
    keys = {key for key in owner_router.TRUSTED.mcp_routes if key[2] in board_door.READS}
    assert keys == {("tools/call", "read_graph", "board"),
                    ("tools/call", "read_graph", "board_task"),
                    ("tools/call", "write_graph", "board_task")}
    for path, method in (("/app/api/board", "GET"), ("/app/api/board", "POST"),
                         ("/app/api/board", "DELETE"), ("/api/board", "GET"),
                         ("/api/board/task", "POST")):
        assert not owner_router.forwarded_path(path, method)


def test_the_box_answers_served_on_trusted_side():
    from tinyassets import universe_server

    for tool, target in ((universe_server.read_graph, " Board "),
                         (universe_server.read_graph, "board_task"),
                         (universe_server.write_graph, "BOARD_TASK")):
        assert json.loads(tool(target=target)) == {
            "error": "served_on_trusted_side", "target": target.strip().lower()}


def _collection_calls(monkeypatch):
    from tinyassets.broker import client

    seen = []
    monkeypatch.setattr(client, "collection_request", lambda *a, **k: seen.append(a))
    return seen


@pytest.mark.parametrize("switch", ["deny_env", "durable_off"])
def test_the_outside_kill_switches_close_the_board_mcp_doors(door, monkeypatch, switch):
    post, boxed, calls = door
    stored = _collection_calls(monkeypatch)
    if switch == "deny_env":
        monkeypatch.setenv("TINYASSETS_OUTSIDE_DENY", "1")
    else:
        post.authority.set_enabled(False)
    for body in (call("read_graph", "board"), call("read_graph", "board_task", query="t1"),
                 call("write_graph", "board_task", payload_json='{"task_id": "t1"}')):
        refused = sse(post(body))
        assert refused["error"]["code"] == -32024
        assert refused["error"]["data"] == {"error": "outside_access_off"}
    assert calls == stored == boxed == []


def test_a_board_caller_without_an_outside_origin_is_refused(door, monkeypatch):
    post, boxed, calls = door
    stored = _collection_calls(monkeypatch)
    for body in (call("read_graph", "board"), call("write_graph", "board_task")):
        assert sse(post(body, origin=None))["error"] == {
            "code": -32024, "message": "outside agent identity is not configured yet",
            "data": {"reason": "outside_identity_unconfigured"}}
    assert calls == stored == boxed == []


@pytest.mark.skipif(sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
                    reason="broker owner channel uses Unix peer credentials")
def test_an_api_key_agent_claims_and_completes_a_task_the_owner_assigned(served):
    request, (agent_key, _) = served
    agent = request("POST", "/app/api/outside-agents", bearer="alice-app", session=True,
                    json={"action": "register", "key_id": agent_key["key_id"],
                          "display_name": "Night Agent",
                          "command_center_id": "cc-alice"}).json()["agent_id"]
    bare = request("GET", "/app/api/board", bearer="alice-app")
    assert (bare.status_code, bare.json()) == (403, {
        "error": "interactive_owner_session_required"})
    made = request("POST", "/app/api/board", bearer="alice-app", session=True, json={
        "task_id": "t1", "expected_revision": 0,
        "patch": {"title": "Overnight digest", "assignees": [agent]}}).json()
    assert made["notified"] == [agent] and made["task"]["revision"] == 1
    key = agent_key["key"]
    [bell] = request("GET", "/api/mailbox", bearer=key).json()["messages"]
    assert "t1" in bell["body"]["content"] and "Overnight" not in bell["body"]["content"]
    [listed] = request("GET", "/api/board", bearer=key).json()["tasks"]
    assert (listed["task_id"], listed["state"]) == ("t1", "open")
    claim = {"task_id": "t1", "expected_revision": 1, "patch": {"state": "claimed"}}
    assert request("POST", "/api/board/task", bearer=key, json={
        **claim, "actor": "owner"}).status_code == 400
    claimed = request("POST", "/api/board/task", bearer=key, json=claim)
    assert claimed.json()["task"]["claim"]["agent_id"] == agent
    stale = request("POST", "/api/board/task", bearer=key, json=claim)
    assert stale.status_code == 409 and stale.json()["task"]["revision"] == 2
    done = request("POST", "/api/board/task", bearer=key, json={
        "task_id": "t1", "expected_revision": 2,
        "patch": {"state": "done", "result_refs": ["file:digest.md"]}}).json()["task"]
    assert done["state"] == "done"
    one = request("GET", "/api/board/task?task_id=t1", bearer=key).json()["task"]
    assert one["revision"] == 3
    mine = request("GET", "/app/api/board?task_id=t1", bearer="alice-app",
                   session=True).json()["task"]
    assert [(e["actor"], e["op"]) for e in mine["history"]] == [
        ("owner", "create"), (agent, "claim"), (agent, "complete")]
    assert mine["result_refs"]["untrusted"] is True
    assert mine["result_refs"]["content"] == ["file:digest.md"]
    assert request("DELETE", "/api/board/task", bearer=key, json={
        "task_id": "t1", "expected_revision": 3}).status_code == 405
    gone = request("DELETE", "/app/api/board", bearer="alice-app", session=True,
                   json={"task_id": "t1", "expected_revision": 3})
    assert gone.json() == {"deleted": "t1", "revision": 3}
    assert request("GET", "/api/board", bearer=key).json()["tasks"] == []
