"""Accepted messages remain observable before execution and across projection gaps."""

import json
import sqlite3
import uuid

import pytest

from tests.test_conversation_failure_readers import _authenticate
from tests.test_conversation_run_admissions import HOME, OWNER, complete, project, reserve
from tests.test_conversation_run_admissions import store as store
from tinyassets.conversation_pending import read_pending
from tinyassets.runs import runs_db_path


def test_connector_pending_to_pair_and_crash_gap(store, monkeypatch):
    from tinyassets import universe_server
    from tinyassets.api.status import get_status
    from tinyassets.storage import conversation_run_admissions as cr

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(store))
    _authenticate(monkeypatch, OWNER)
    row = reserve(store, str(uuid.uuid4()), message="accepted before reply")
    with sqlite3.connect(runs_db_path(store)) as conn:
        conn.execute("UPDATE runs SET status='running' WHERE run_id=?", (row["run_id"],))
    before = runs_db_path(store).read_bytes()
    page = json.loads(universe_server.read_graph(target="conversation", graph_id=HOME))
    status = json.loads(get_status(universe_id=HOME, include_conversation=True))[
        "recent_conversation"
    ]
    for result in (page, status):
        assert result["content_is_untrusted"] is True
        assert result["fence"] == "BEGIN_UNTRUSTED_TRANSCRIPT"
        assert result["pending"][0]["text"] == "accepted before reply"
        assert result["pending"][0]["state"] == "running"
        assert result["pending"][0]["consumer_turn_id"] == row["admission_id"]
    assert runs_db_path(store).read_bytes() == before
    assert read_pending(store / HOME, "principal:someone-else") == []
    assert read_pending(store / HOME, f"agent:other:principal:{OWNER}") == []
    complete(store, row["run_id"])
    original = cr._mark_projected
    monkeypatch.setattr(
        cr, "_mark_projected", lambda *a: (_ for _ in ()).throw(RuntimeError("crash"))
    )
    with pytest.raises(RuntimeError, match="crash"):
        project(store, row["admission_id"])
    assert read_pending(store / HOME, f"principal:{OWNER}") == []
    monkeypatch.setattr(cr, "_mark_projected", original)
    project(store, row["admission_id"])
    status = json.loads(get_status(universe_id=HOME, include_conversation=True))[
        "recent_conversation"
    ]
    assert status["pending"] == []
    assert [t["text"] for t in status["turns"]] == ["accepted before reply", "answer"]
    assert len({t["consumer_turn_id"] for t in status["turns"]}) == 1
    _authenticate(monkeypatch, "someone-else")
    assert "accepted before reply" not in universe_server.read_graph(
        target="conversation", graph_id=HOME
    )


def test_legacy_pending_is_readonly_and_agent_scoped(store):
    from tinyassets import agent_steering, conversation_store

    root = store / HOME
    session = f"principal:{OWNER}"
    agent_steering.open_turn(
        root, f"thread:{session}", "live", message="legacy request", client_send_id="send-one"
    )
    rows = read_pending(root, session)
    assert [(r["text"], r["client_send_id"]) for r in rows] == [("legacy request", "send-one")]
    assert read_pending(root, "principal:other") == []
    assert conversation_store.read_history_page(root, session, limit=10) == ([], False)
    agent_steering.settle(root, f"thread:{session}", "live")
    assert read_pending(root, session) == []


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("conversation_run_admissions", "owner_user_id", "another-owner"),
        ("conversation_run_admissions", "universe_id", "another-home"),
        ("conversation_run_admissions", "session_id", "principal:another-owner"),
        ("runs", "owner_user_id", "another-owner"),
        ("runs", "queue_universe_id", "another-home"),
        ("runs", "actor", "universe:another-home"),
    ],
)
def test_pending_requires_matching_admission_and_run_scope(store, table, column, value):
    reserve(store, str(uuid.uuid4()), message="private accepted intent")
    with sqlite3.connect(runs_db_path(store)) as conn:
        conn.execute(f"UPDATE {table} SET {column}=?", (value,))
    assert read_pending(store / HOME, f"principal:{OWNER}") == []


def test_completion_between_pending_and_catalogue_reads_has_no_duplicate(store, monkeypatch):
    from tinyassets import conversation_retrieval
    from tinyassets.api.graph_reads import read_graph

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(store))
    _authenticate(monkeypatch, OWNER)
    row = reserve(store, str(uuid.uuid4()), message="racing request")
    original = conversation_retrieval.read_conversation_page

    def completing_read(*args, **kwargs):
        complete(store, row["run_id"])
        project(store, row["admission_id"])
        return original(*args, **kwargs)

    monkeypatch.setattr(conversation_retrieval, "read_conversation_page", completing_read)
    result = json.loads(read_graph(target="conversation", graph_id=HOME))
    assert result["pending"] == []
    assert len(result["messages"]) == 2
    assert result["content_is_untrusted"] is True
