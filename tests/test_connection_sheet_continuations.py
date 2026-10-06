"""A real connection answer commits the wake with the protected request."""

import json
from contextlib import closing

import pytest

from tests.test_pending_requests import _CRED, _ask, _login, _make_universe, _owner_answer
from tests.test_pending_requests import _reset_auth as _auth_fixture
from tests.test_pending_requests import base as _base_fixture
from tinyassets import bound_requests, request_continuations, turn_interrupt
from tinyassets.storage import pending_requests

base = _base_fixture
_reset_auth = _auth_fixture


@pytest.mark.parametrize("deny", [False, True])
def test_agent_connection_answer_commits_one_sanitized_wake(base, deny):
    home = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    with turn_interrupt.interactive_turn("alice", "u-1"):
        ask = _ask("u-1", **_CRED)
    assert ask.get("server_continuation") is True, ask
    assert pending_requests.get_request(home, ask["request_id"])["server_continuation"]
    if deny:
        result = _owner_answer("u-1", request_id=ask["request_id"], decision="declined")
    else:
        result = _owner_answer("u-1", request_id=ask["request_id"],
                               values={"secret": "private-key"})
    assert not result.get("error"), result
    with closing(bound_requests.connect(home)) as conn:
        events = conn.execute(
            "SELECT payload_json FROM activity_events WHERE wake_required=1"
        ).fetchall()
        assert len(events) == 1
        payload = json.loads(events[0][0])
        assert payload["owner"] == "alice"
        assert payload["agent"] == "main"
        assert payload["task_id"]
        assert "private-key" not in events[0][0]
    resumed = []
    assert (
        request_continuations.recover(
            home, run=lambda _, outcome: resumed.append(outcome) or {"reply": "Continued"}
        )
        == 1
    )
    assert request_continuations.recover(home, run=lambda *_: pytest.fail("duplicate wake")) == 0
    assert len(resumed) == 1


def test_settings_connection_does_not_invent_an_agent_continuation(base):
    home = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    ask = _ask("u-1", **_CRED)
    assert not ask.get("server_continuation")
    result = _owner_answer("u-1", request_id=ask["request_id"], values={"secret": "private-key"})
    assert not result.get("error"), result
    with closing(bound_requests.connect(home)) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM activity_events WHERE wake_required=1").fetchone()[0]
            == 0
        )


def test_wake_failure_rolls_back_answer_and_never_reports_success(base, monkeypatch):
    from tinyassets import connection_continuations

    home = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    with turn_interrupt.interactive_turn("alice", "u-1"):
        ask = _ask("u-1", **_CRED)
    original = connection_continuations.answered

    def crash(*args):
        raise RuntimeError("simulated wake write failure")

    monkeypatch.setattr(connection_continuations, "answered", crash)
    result = _owner_answer("u-1", request_id=ask["request_id"], values={"secret": "private-key"})
    assert result["error"] == "request_storage_unavailable"
    assert pending_requests.get_request(home, ask["request_id"])["status"] == "pending"
    monkeypatch.setattr(connection_continuations, "answered", original)
    result = _owner_answer("u-1", request_id=ask["request_id"], values={"secret": "private-key"})
    assert result["status"] == "answered"
    with closing(bound_requests.connect(home)) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM activity_events WHERE wake_required=1").fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("failure", [RuntimeError("lock busy"),
                                    bound_requests.RequestRefused("The initiating task ended.")])
def test_binding_failure_still_notifies_owner_and_returns_saved_ask(base, monkeypatch, failure):
    from tinyassets import connection_continuations
    from tinyassets.api import pending_requests as api

    home = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    notifications = []
    monkeypatch.setattr(
        api, "_notify_owner", lambda uid, row: notifications.append((uid, row.copy())))

    def fail(*args):
        raise failure

    monkeypatch.setattr(connection_continuations, "bind", fail)
    with turn_interrupt.interactive_turn("alice", "u-1"):
        ask = _ask("u-1", **_CRED)
        duplicate = _ask("u-1", **_CRED)
    assert not ask.get("error")
    assert ask["server_continuation"] is False
    assert ask["continuation_status"] == "unavailable"
    assert pending_requests.get_request(home, ask["request_id"])["status"] == "pending"
    assert duplicate["request_id"] == ask["request_id"]
    assert len(notifications) == 1
    assert notifications[0][0] == "u-1"
    assert notifications[0][1]["request_id"] == ask["request_id"]
