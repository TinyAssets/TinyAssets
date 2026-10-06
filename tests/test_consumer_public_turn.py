"""Existing canonical handles route selected turns without default-writer replay."""

import json
import sqlite3
import uuid

import pytest

from tests.test_consumer_run_envelope import setup
from tests.test_consumer_selection import HOME, OWNER
from tests.test_consumer_selection import store as store


@pytest.fixture
def public(store, monkeypatch, authenticate_request):
    from tinyassets import universe_server
    from tinyassets.api import helpers, permissions

    authenticate_request(OWNER)
    monkeypatch.setattr(helpers, "_base_path", lambda: store)
    monkeypatch.setattr(permissions, "_base_path", lambda: store)
    monkeypatch.setattr(helpers, "_universe_dir", lambda _: store / HOME)
    monkeypatch.setattr("tinyassets.universe_intelligence.converse",
                        lambda *a, **k: pytest.fail("default writer must not run"))
    return universe_server


def test_selected_converse_requires_key_without_starting_default_writer(public, store):
    intent = setup(store)
    result = json.loads(public.converse(message="hello", graph_id=HOME))
    assert result["error"] == "consumer_request_required"
    assert result["consumer_selection"]["binding_id"] == intent["binding_id"]


def test_scoped_status_missing_is_uniform_not_found(public):
    assert json.loads(public.read_graph(target="conversation_turn", graph_id=HOME,
                                       request_key=str(uuid.uuid4()))) == {"error": "not_found"}


def test_replay_after_disable_keeps_original_turn_and_conflicting_body_refuses(
    public, store, monkeypatch,
):
    from tests.test_consumer_run_envelope import reserve
    from tests.test_conversation_run_admissions import complete
    from tinyassets.storage import db_path

    intent = setup(store, reply_key="reply")
    key = str(uuid.uuid4())
    row = reserve(store, key, intent)
    complete(store, row["run_id"])
    with sqlite3.connect(db_path(store)) as conn:
        conn.execute("UPDATE agent_bindings SET configuration_json='{}'")
    monkeypatch.setattr("tinyassets.consumer_runtime._dispatch",
                        lambda *a, **k: None)  # Terminal projection contract, not worker coverage.
    request = {"version": 1, "request_key": key, "binding_id": intent["binding_id"],
               "binding_revision": intent["binding_revision"]}
    result = json.loads(public.converse(intent["message"], HOME, "typed", None, request))
    assert result["reply"] == "answer"
    assert result["consumer_turn"]["run_id"] == row["run_id"]
    assert json.loads(public.converse("changed", HOME, "typed", None, request)) == {
        "error": "consumer_request_conflict",
    }


def test_wrong_owner_never_observes_private_correlation(public, store, authenticate_request):
    from tests.test_consumer_run_envelope import reserve

    key = str(uuid.uuid4())
    reserve(store, key, setup(store))
    authenticate_request("other")
    assert json.loads(public.read_graph(target="conversation_turn", graph_id=HOME,
                                       request_key=key)) == {"error": "not_found"}


def test_no_selection_preserves_legacy_default_conversation(public, store, monkeypatch):
    monkeypatch.setattr("tinyassets.universe_intelligence.converse", lambda *a, **k: "legacy reply")
    assert json.loads(public.converse("hello", HOME))["reply"] == "legacy reply"


def test_foreign_executable_install_requires_real_remix_before_run_reservation(public, store):
    from tinyassets.runs import runs_db_path

    intent = setup(store)
    request = {"version": 1, "request_key": str(uuid.uuid4()),
               "binding_id": intent["binding_id"], "binding_revision": intent["binding_revision"]}
    result = json.loads(public.converse("hello", HOME, "typed", None, request))
    assert result["error"] == "consumer_remix_required"
    assert result["next_action"]["operation"] == "remix"
    assert result["next_action"]["fork_from"] == result["source_version_id"]
    with sqlite3.connect(runs_db_path(store)) as conn:
        assert conn.execute("SELECT count(*) FROM runs").fetchone() == (0,)


def test_history_dedupe_identity_requires_real_consumer_projection(store):
    from tests.test_consumer_run_envelope import reserve
    from tests.test_conversation_run_admissions import complete
    from tinyassets import conversation_store
    from tinyassets.storage import conversation_run_admissions as canonical

    row = reserve(store, str(uuid.uuid4()), setup(store, reply_key="reply"))
    complete(store, row["run_id"])
    with canonical.authorized_scope(store, owner=OWNER, universe=HOME) as scope:
        canonical.project_terminal(scope, row["admission_id"])
    conversation_store.record_turn(store / HOME, f"principal:{OWNER}", "universe", "Unrelated",
                                   ext_id="consumer:" + "a" * 32 + ":reply")
    messages = conversation_store.load_recent_readonly(store / HOME, f"principal:{OWNER}")
    assert [msg.consumer_turn_id for msg in messages] == [row["admission_id"],
                                                         row["admission_id"], None]


def test_status_repairs_lost_terminal_callback_without_starting_work(public, store, monkeypatch):
    from tests.test_consumer_run_envelope import reserve
    from tests.test_conversation_run_admissions import complete
    from tinyassets import conversation_store

    key = str(uuid.uuid4())
    intent = setup(store, reply_key="reply")
    row = reserve(store, key, intent)
    complete(store, row["run_id"])
    monkeypatch.setattr("tinyassets.consumer_runtime._dispatch",
                        lambda *a, **k: pytest.fail("status cannot dispatch work"))
    monkeypatch.setattr("tinyassets.providers.call.call_provider",
                        lambda *a, **k: pytest.fail("status cannot invoke a provider"))
    for _ in range(2):
        result = json.loads(public.read_graph(target="conversation_turn", graph_id=HOME,
                                             request_key=key))
        assert result["consumer_turn"]["state"] == "completed"
        assert result["consumer_turn"]["projection"] == "committed"
        assert result["reply"] == "answer"
    messages = conversation_store.load_recent_readonly(store / HOME, f"principal:{OWNER}")
    assert [message.text for message in messages] == [intent["message"], "answer"]


def test_a_projected_turn_announces_the_owners_message_once(public, store, monkeypatch):
    """The selected-consumer thread wakes owner_message subscriptions too, once.

    Only the observation that COMMITS the pair announces it; a second status read
    of the same turn finds it already projected and announces nothing.
    """
    from tests.test_consumer_run_envelope import reserve
    from tests.test_conversation_run_admissions import complete

    announced = []
    monkeypatch.setattr(
        "tinyassets.automation_events.emit_owner_message",
        lambda udir, *, principal_id: announced.append((udir.name, principal_id)),
    )
    key = str(uuid.uuid4())
    row = reserve(store, key, setup(store, reply_key="reply"))
    complete(store, row["run_id"])
    for _ in range(2):
        result = json.loads(public.read_graph(target="conversation_turn", graph_id=HOME,
                                             request_key=key))
        assert result["consumer_turn"]["projection"] == "committed"
    assert announced == [(HOME, OWNER)]


def test_settlement_projects_and_announces_once_before_any_status_read(store, monkeypatch):
    from tests.test_consumer_run_envelope import reserve
    from tests.test_conversation_run_admissions import complete
    from tinyassets.consumer_runtime import settle_admitted_consumer

    announced = []
    monkeypatch.setattr(
        "tinyassets.automation_events.emit_owner_message",
        lambda udir, *, principal_id: announced.append((udir.name, principal_id)),
    )
    row = reserve(store, str(uuid.uuid4()), setup(store, reply_key="reply"))
    complete(store, row["run_id"])
    for _ in range(2):
        result = settle_admitted_consumer(store, row["run_id"])
        assert result["consumer_turn"]["projection"] == "committed"
        assert result["reply"] == "answer"
    assert announced == [(HOME, OWNER)]
