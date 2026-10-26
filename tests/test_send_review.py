"""Regression proofs for the durable-send review, with no provider access."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.verifies("owner-daemon-in-box:One durable owner send intake (S24)")

ROOT = Path(__file__).resolve().parents[1]


def test_modern_mcp_post_survives_deleted_ingress():
    from tinyassets.mcp_events_http import VERSION, MCPEventsMiddleware

    async def run():
        from starlette.responses import JSONResponse

        async def app(scope, receive, send):
            await JSONResponse({"jsonrpc": "2.0", "id": 1, "result": {}})(scope, receive, send)

        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "ping",
                "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": VERSION}},
            }
        ).encode()
        scope = {
            "type": "http",
            "path": "/mcp",
            "method": "POST",
            "headers": [(b"mcp-protocol-version", VERSION.encode()), (b"mcp-method", b"ping")],
        }

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        output = []

        async def send(message):
            output.append(message)

        from tinyassets.auth.middleware import identity_context
        from tinyassets.auth.provider import Identity

        with identity_context(Identity(user_id="owner", username="owner", capabilities=["write"])):
            await MCPEventsMiddleware(app, app)(scope, receive, send)
        assert output[0]["status"] == 200, output

    asyncio.run(run())


def test_obsolete_ingress_artifacts_removed():
    from scripts.check_background_authority_inventory import validate_inventory

    assert validate_inventory(ROOT) == []
    assert not (ROOT / "deploy/app-ingress-env.template").exists()
    assert not (ROOT / "tinyassets/storage/ingress_journal.py").exists()
    assert 'app-ingress.env' not in (ROOT / 'deploy/compose.yml').read_text()


@pytest.fixture
def converse_rig(tmp_path, monkeypatch):
    from tinyassets import addressed_agents, consumer_runtime
    from tinyassets import universe_server as server
    from tinyassets.api import helpers, interlocutor, permissions

    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path.parent)
    monkeypatch.setattr(helpers, "_universe_dir", lambda uid: tmp_path)
    monkeypatch.setattr(helpers, "_request_universe", lambda uid: uid)
    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: True)
    monkeypatch.setattr(permissions, "current_actor_id", lambda: "owner")
    monkeypatch.setattr(permissions, "universe_access_allows", lambda *a, **k: True)
    monkeypatch.setattr(
        interlocutor,
        "authorize_conversation_turn",
        lambda uid: SimpleNamespace(
            permitted=True, interlocutor=SimpleNamespace(tier=interlocutor.FOUNDER)
        ),
    )
    monkeypatch.setattr(addressed_agents, "resolve", lambda *a, **k: None)
    monkeypatch.setattr(consumer_runtime, "converse_turn", lambda *a, **k: None)
    monkeypatch.setattr(server, "_announce_owner_message", lambda *a: None)
    return server


def test_replay_precedes_changed_selection(converse_rig, tmp_path, monkeypatch):
    from tinyassets import consumer_runtime

    server = converse_rig
    monkeypatch.setattr(server, "_dispatch_owner_send", lambda *a: json.dumps({"reply": "done"}))
    first = json.loads(server.converse("hello", graph_id="home", client_send_id="same"))
    monkeypatch.setattr(
        consumer_runtime, "converse_turn", lambda *a, **k: {"error": "consumer_request_required"}
    )
    again = json.loads(server.converse("hello", graph_id="home", client_send_id="same"))
    assert again == first
    # A browser that already negotiated a different selection repairs the same ID.
    conflict = json.loads(server.converse('hello', graph_id='home', client_send_id='same',
                                         consumer_request={'request_key': 'new-selection'}))
    assert conflict == first


def test_no_effect_connection_failure_remains_retryable(converse_rig, monkeypatch):
    from tinyassets.api import pending_requests, universe
    from tinyassets.control_plane import session

    server = converse_rig

    def fail(*a, **k):
        raise RuntimeError("connection unavailable")

    monkeypatch.setattr(session, "start", fail)
    monkeypatch.setattr(
        universe,
        "engine_setup_required_payload",
        lambda *a: {"status": "held", "reason": "setup_required"},
    )
    monkeypatch.setattr(
        server, "_served_failure_record", lambda *a, **k: SimpleNamespace(effects="none")
    )
    monkeypatch.setattr(pending_requests, "_model_connect_request", fail)
    monkeypatch.setattr(server, "_with_unsettled_steering", lambda value, *a: value)
    first = json.loads(server.converse("hello", graph_id="home", client_send_id="same"))
    assert first.get("retryable_setup") is True
    again = json.loads(server.converse("hello", graph_id="home", client_send_id="same"))
    assert again.get("retryable_setup") is True


def test_start_recovery_uses_box_identity_and_drains_on_boot(converse_rig, tmp_path, monkeypatch):
    import time
    from threading import Event

    from tinyassets import conversation_store as store
    from tinyassets.owner_centers import Centers
    from tinyassets.owner_door.send import start_recovery

    Centers(tmp_path.parent).create(tmp_path.name)
    store.admit_owner_send(
        tmp_path,
        "principal:owner",
        "queued",
        "before restart",
        {"input_method": "typed", "model_choice": None, "consumer_request": None},
    )
    called, stop = Event(), Event()
    calls = []

    def dispatch(*args):
        from tinyassets.auth.middleware import current_identity, provider_request_capability

        capability = provider_request_capability()
        assert capability is not None
        assert capability.principal_id == 'owner'
        assert capability.request_id == 'queued'
        calls.append(current_identity().user_id)
        called.set()
        return json.dumps({"reply": "resumed"})

    monkeypatch.setattr(converse_rig, "_dispatch_owner_send", dispatch)
    worker = start_recovery(SimpleNamespace(root=tmp_path.parent, principal="owner"), stop)
    try:
        assert called.wait(5)
        deadline = time.monotonic() + 5
        while store.owner_send_state(tmp_path, 'principal:owner', 'queued')[0] != 'completed':
            assert time.monotonic() < deadline
            time.sleep(.01)
    finally:
        stop.set()
        worker.join(5)
    assert calls == ["owner"]


def test_projection_cannot_create_an_unadmitted_owner_message(tmp_path):
    from tinyassets.storage.conversation_run_admissions import TerminalUnavailable, _write_pair

    row = {
        "admission_id": "a" * 32,
        "projection_state": "pending",
        "intent_json": json.dumps({"message": "not admitted"}),
        "context_json": "{}",
        "terminal_json": json.dumps(
            {"speaker": "universe", "content": "reply", "execution": None, "failure": None}
        ),
    }
    with pytest.raises(TerminalUnavailable):
        _write_pair(
            SimpleNamespace(home=tmp_path, session="principal:owner", check=lambda: None), row
        )


def test_cutover_projects_only_preexisting_consumer_run_once(tmp_path):
    import sqlite3

    from tinyassets import conversation_store as store
    from tinyassets.storage import conversation_run_admissions as admissions

    path = tmp_path / 'runs.db'
    context = json.dumps({'version': 1})
    with sqlite3.connect(path) as conn:
        conn.executescript(admissions._SCHEMA)
        conn.execute(
            "INSERT INTO conversation_run_admissions "
            "(admission_id,owner_user_id,universe_id,session_id,request_key_hash,"
            "intent_digest,intent_json,context_json,selection_json,run_id,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ('a' * 32, 'owner', 'home', 'principal:owner', 'key', 'digest',
             json.dumps({'message': 'legacy message'}), context, '{}', 'run', 1, 1),
        )
        conn.commit()
        admissions._mark_legacy_projections(conn)
        query = 'SELECT context_json FROM conversation_run_admissions'
        legacy_context = conn.execute(query).fetchone()[0]
        admissions._mark_legacy_projections(conn)
        assert conn.execute(query).fetchone()[0] == legacy_context
        conn.execute(
            "INSERT INTO conversation_run_admissions "
            "(admission_id,owner_user_id,universe_id,session_id,request_key_hash,"
            "intent_digest,intent_json,context_json,selection_json,run_id,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ('b' * 32, 'owner', 'home', 'principal:owner', 'new-key', 'new-digest',
             json.dumps({'message': 'new message'}), context, '{}', 'new-run', 2, 2),
        )
        conn.commit()
        admissions._mark_legacy_projections(conn)
        assert conn.execute(query + ' WHERE admission_id=?', ('b' * 32,)).fetchone()[0] == context
    scope = SimpleNamespace(home=tmp_path, session='principal:owner', check=lambda: None)
    row = {'admission_id': 'a' * 32, 'projection_state': 'pending',
           'intent_json': json.dumps({'message': 'legacy message'}),
           'context_json': legacy_context,
           'terminal_json': json.dumps({'speaker': 'universe', 'content': 'legacy reply',
                                        'execution': None, 'failure': None})}
    first = admissions._write_pair(scope, row)
    assert admissions._write_pair(scope, row) == first
    assert [(turn.speaker, turn.text) for turn in store.load_recent(tmp_path, scope.session)] == [
        ('founder', 'legacy message'), ('universe', 'legacy reply')]
    new_row = {**row, 'admission_id': 'b' * 32, 'context_json': context}
    with pytest.raises(admissions.TerminalUnavailable):
        admissions._write_pair(scope, new_row)


def test_pair_cannot_create_an_unadmitted_owner_message(tmp_path):
    from tinyassets.conversation_store import record_exchange_turns

    assert record_exchange_turns(tmp_path, "principal:owner", "not admitted", "reply") is None


def test_drain_finishes_work_admitted_before_snapshot():
    from tinyassets.owner_activity import Activity

    gate = Activity()

    async def exercise():
        release = asyncio.Event()
        started = asyncio.Event()

        async def admitted():
            with gate.enter(keep_awake=False):
                gate.status(snapshot=True)
                started.set()
                with gate.enter(keep_awake=False):
                    assert gate.active == 2
                await release.wait()

        async def other_turn():
            await started.wait()
            with pytest.raises(RuntimeError, match='draining'):
                with gate.enter(keep_awake=False):
                    pass
            release.set()

        await asyncio.gather(admitted(), other_turn())

    asyncio.run(exercise())
    with pytest.raises(RuntimeError, match='draining'):
        with gate.enter(keep_awake=False):
            pass
