import json
import time

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_automations import OWNER, UNIVERSE, _seed_branch, _seed_owner
from tests.test_mcp_events import SECRET, params, rows
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.mcp_events import EventStore
from tinyassets.runs import _connect, create_run, deliver_terminal_events, update_run_status

pytestmark = pytest.mark.usefixtures("cloud_runtime")
FOLLOWED = "storage-workflow"


@pytest.fixture(name="home")
def event_home(tmp_path, monkeypatch):
    # Events need durable runs/ACLs, not a model credential or an execution assignment.
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _seed_owner(tmp_path)
    from tinyassets.daemon_server import grant_universe_ownership

    grant_universe_ownership(tmp_path, universe_id=UNIVERSE, owner_id=OWNER)
    _seed_branch(tmp_path, branch_def_id=FOLLOWED, visibility="private")
    return tmp_path


def test_real_terminal_outbox_survives_failed_fanout_then_deduplicates(home, monkeypatch):
    from tinyassets import mcp_events, runs

    sent = []

    def sender(url, body, headers):
        data = json.loads(body)
        sent.append(data)
        return 200, json.dumps({"challenge": data.get("challenge")}).encode()

    store = EventStore(home, key_provider=lambda: b"k" * 32, sender=sender)
    document = params()
    document["arguments"].update(command_center_id=UNIVERSE, branch_def_id=FOLLOWED)
    store.subscribe(OWNER, document)
    monkeypatch.setattr(mcp_events, "EventStore", lambda base: store)
    with identity_context(Identity(OWNER, OWNER)):
        run_id = create_run(
            home,
            branch_def_id=FOLLOWED,
            thread_id="t",
            actor=OWNER,
            queue_universe_id=UNIVERSE,
            inputs={"operation": "save", "text": "private context"},
        )
    update_run_status(home, run_id, status="running")
    real_capture = store.capture
    local_events = []
    local_emit = runs._emit_run_completed

    def observe_local(*args, **kwargs):
        local_events.append(args[1])
        return local_emit(*args, **kwargs)

    monkeypatch.setattr(runs, "_emit_run_completed", observe_local)

    def failed(**kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(store, "capture", failed)
    update_run_status(home, run_id, status="completed", output={"saved": True})
    assert local_events == [run_id]
    with _connect(home) as conn:
        assert (
            conn.execute(
                "SELECT delivered_at FROM run_terminal_outbox WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            is None
        )
    monkeypatch.setattr(store, "capture", real_capture)
    assert deliver_terminal_events(home, older_than=0) == 1
    assert len(rows(store, "deliveries")) == 1
    # Crash after fanout but before source acknowledgment: repeat the same source row.
    with _connect(home) as conn:
        conn.execute("UPDATE run_terminal_outbox SET delivered_at=NULL WHERE run_id=?", (run_id,))
    assert deliver_terminal_events(home, older_than=0) == 1
    assert len(rows(store, "deliveries")) == 1
    store.pump()
    assert sent[-1]["data"]["run_id"] == run_id
    assert "private context" not in json.dumps(sent)


@pytest.mark.parametrize("status", ["failed", "cancelled", "interrupted"])
def test_terminal_failure_does_not_emit(home, monkeypatch, status):
    from tinyassets import mcp_events

    store = EventStore(
        home,
        key_provider=lambda: b"k" * 32,
        sender=lambda url, body, headers: (
            200,
            json.dumps({"challenge": json.loads(body)["challenge"]}).encode(),
        ),
    )
    document = params()
    document["arguments"].update(command_center_id=UNIVERSE, branch_def_id=FOLLOWED)
    store.subscribe(OWNER, document)
    monkeypatch.setattr(mcp_events, "EventStore", lambda base: store)
    with identity_context(Identity(OWNER, OWNER)):
        run = create_run(
            home,
            branch_def_id=FOLLOWED,
            thread_id="t",
            actor=OWNER,
            queue_universe_id=UNIVERSE,
            inputs={"operation": "save"},
        )
    update_run_status(home, run, status="running")
    update_run_status(home, run, status=status, output={"saved": True})
    assert rows(store, "deliveries") == []


def test_persistent_seal_key_restart_and_ephemeral_refusal(tmp_path, monkeypatch):
    from tinyassets.onboarding import session_store

    monkeypatch.setenv(session_store.SEAL_KEY_ENV, "ab" * 32)
    session_store._reset_for_tests()
    def sender(url, body, headers):
        return 200, json.dumps({"challenge": json.loads(body)["challenge"]}).encode()
    try:
        store = EventStore(tmp_path, authorizer=lambda *a: None, sender=sender)
        store.subscribe("alice", params())
        first = rows(store, "subscriptions")[0]
        assert store._open(first)["secret"] == SECRET
        monkeypatch.setenv(session_store.SEAL_KEY_ENV, "ab" * 32)
        session_store._reset_for_tests()
        assert EventStore(tmp_path)._open(first)["secret"] == SECRET
        monkeypatch.delenv(session_store.SEAL_KEY_ENV, raising=False)
        session_store._reset_for_tests()
        with pytest.raises(session_store.SessionStoreUnavailable):
            store.subscribe("alice", params())
    finally:
        session_store._reset_for_tests()


def test_saved_outside_authority_is_rechecked(home, monkeypatch):
    from tinyassets.outside_authority import OutsideClientAuthority

    outside = OutsideClientAuthority(home)
    bound = {"client": "test-client", "family": "family", "authenticated_at": time.time()}
    outside.observe(OWNER, bound)
    outside.set_enabled(True)
    outside.change(
        OWNER,
        "test-client",
        expected_generation=0,
        family="family",
        scopes=[{"universe": UNIVERSE, "agent": "main", "capability": "read_graph"}],
    )
    bound = outside.admit(OWNER, bound)
    store = EventStore(
        home,
        key_provider=lambda: b"k" * 32,
        sender=lambda url, body, headers: (
            200,
            json.dumps({"challenge": json.loads(body)["challenge"]}).encode(),
        ),
    )
    document = params()
    document["arguments"].update(command_center_id=UNIVERSE, branch_def_id=FOLLOWED)
    store.subscribe(OWNER, document, authority=bound)
    store.capture(
        owner=OWNER,
        universe=UNIVERSE,
        branch=FOLLOWED,
        run_id="r",
        event_id="r#1",
        occurred=time.time(),
        inputs={"operation": "save"},
        outputs={"saved": True},
    )
    outside.change(
        OWNER, "test-client", expected_generation=1, family="family", scopes=[], revoke=True
    )
    store.pump()
    assert rows(store, "subscriptions")[0]["sealed"] == b""
    assert rows(store, "deliveries")[0]["state"] == "stopped"


@pytest.mark.parametrize("fault", ["nonfinite", "corrupt", "key_unavailable"])
def test_poisoned_capture_has_bounded_outbox_lifetime(home, monkeypatch, fault):
    from tinyassets import mcp_events
    from tinyassets.onboarding.session_store import SessionStoreUnavailable

    clock = [time.time()]
    store = EventStore(home, key_provider=lambda: b"k" * 32, clock=lambda: clock[0],
                       sender=lambda url, body, headers: (
                           200, json.dumps({"challenge": json.loads(body)["challenge"]}).encode()))
    document = params()
    document["arguments"].update(command_center_id=UNIVERSE, branch_def_id=FOLLOWED)
    store.subscribe(OWNER, document)
    monkeypatch.setattr(mcp_events, "EventStore", lambda base: store)
    with identity_context(Identity(OWNER, OWNER)):
        rid = create_run(home, branch_def_id=FOLLOWED, thread_id="t", actor=OWNER,
                         queue_universe_id=UNIVERSE, inputs={"operation": "save"})
    update_run_status(home, rid, status="running")
    if fault == "corrupt":
        with store.db() as conn:
            conn.execute("UPDATE subscriptions SET sealed=?", (b"invalid",))
    elif fault == "key_unavailable":
        def unavailable():
            raise SessionStoreUnavailable("test-only unavailable key")

        store.key_provider = unavailable
    update_run_status(home, rid, status="completed", output={
        "saved": float("nan") if fault == "nonfinite" else True})
    with _connect(home) as conn:
        marked = conn.execute(
            "SELECT delivered_at FROM run_terminal_outbox WHERE run_id=?", (rid,)).fetchone()[0]
    assert (marked is None) == (fault == "key_unavailable")
    if fault == "key_unavailable":
        clock[0] += mcp_events.DELIVERY_TTL + 10
        assert deliver_terminal_events(home, older_than=0) == 1
    assert rows(store, "deliveries") == []
