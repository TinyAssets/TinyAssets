"""Event wakes and self-set wakes of a universe's background agent, end to end.

The owner's new message wakes an ``owner_message`` subscription -- once per
burst, never on the universe's own reply -- and the agent can set its own next
wake through its served ``write_graph``. Same rig as ``test_automation_events``:
real emitters, the real ``register_automation``, the real store and the real
consumer pump; the only substitute is the ``_execute`` seam that stands in for
the graph a wake runs.
"""

# ruff: noqa: F811 -- `home` is the imported pytest fixture the tests request

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tinyassets.automations as automations_module
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_automation_events import (  # noqa: F401 - the shared fixture
    BOB,
    BOB_UNIVERSE,
    FOLLOWER,
    _Graph,
    _poll,
    _subscribe,
    _wakes,
    home,
)
from tests.test_automations import OWNER, UNIVERSE
from tinyassets.automation_events import emit_app_event, emit_owner_message
from tinyassets.automations import AutomationStore, due_automations

pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture(autouse=True)
def _pin_data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


def _message(base: Path, principal: str = OWNER, universe: str = UNIVERSE) -> list[str]:
    return emit_owner_message(base / universe, principal_id=principal)


# -- owner_message --------------------------------------------------------------


def test_a_burst_of_owner_messages_is_one_wake_through_the_pump(
    home: Path, monkeypatch,
) -> None:
    sub = _subscribe(home, "owner_message")
    first = _message(home)
    assert _message(home) == first and _message(home) == first
    [wake] = _wakes(home)
    assert wake.automation_id == first[0]
    assert wake.inputs == {
        "why": "followed",
        "event": {"type": "owner_message", "subscription_id": sub.automation_id},
    }

    graph = _Graph()
    monkeypatch.setattr(automations_module, "_execute", graph)
    _poll(home)
    assert graph.calls == [(FOLLOWER, wake.inputs)]
    assert _wakes(home) == []

    # The wake ran; the next message is news again and wakes it again.
    [second] = _message(home)
    assert second != wake.automation_id
    _poll(home)
    assert len(graph.calls) == 2


def test_a_message_while_the_wake_waits_adds_no_second_wake(home: Path) -> None:
    """Coalescing holds until the wake STARTS, not just until it is stored."""
    _subscribe(home, "owner_message")
    [wake] = _message(home)
    assert _message(home) == [wake]
    assert [row.automation_id for row in _wakes(home)] == [wake]


def test_two_messages_stored_at_once_store_one_wake(home: Path, monkeypatch) -> None:
    """Both deliveries read the subscription before either fired: one key."""
    _subscribe(home, "owner_message")
    before = AutomationStore(home).list(universe_id=UNIVERSE)
    real_list = AutomationStore.list
    monkeypatch.setattr(AutomationStore, "list", lambda self, **kw: list(before))
    first = _message(home)
    assert _message(home) == first
    monkeypatch.setattr(AutomationStore, "list", real_list)
    assert [row.automation_id for row in _wakes(home)] == first


def test_the_universes_own_activity_wakes_nothing(home: Path) -> None:
    """A universe principal is the agent itself: answering cannot wake it."""
    _subscribe(home, "owner_message")
    assert _message(home, principal=f"universe:{UNIVERSE}") == []
    assert _message(home, principal="") == []
    assert _wakes(home) == []


def test_another_users_message_wakes_nothing_of_the_owners(home: Path) -> None:
    _subscribe(home, "owner_message")
    # Bob talking in Alice's universe is not Alice's message.
    assert _message(home, principal=BOB) == []
    assert _wakes(home) == []
    # Alice's message names her home; sent "into" Bob's universe it wakes nothing.
    assert _message(home, universe=BOB_UNIVERSE) == []
    assert _wakes(home) == [] and _wakes(home, BOB_UNIVERSE) == []
    assert len(_message(home)) == 1


def test_converse_announces_each_stored_owner_message_once(tmp_path: Path, monkeypatch) -> None:
    """The served path: one event per message the store kept, as the caller."""
    import tinyassets.automation_events as events
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us
    from tests.test_converse_handle import _founder_auth
    from tinyassets import conversation_store

    _founder_auth(monkeypatch, base=tmp_path)
    announced: list[tuple[str, str]] = []
    monkeypatch.setattr(
        events, "emit_owner_message",
        lambda udir, *, principal_id: announced.append((Path(udir).name, principal_id)),
    )
    monkeypatch.setattr(ui, "converse", lambda uid, msg, **kw: "the universe's reply")
    assert json.loads(us.converse(message="hello", graph_id="u-x"))["reply"]
    assert len(announced) == 1 and announced[0][0] == "u-x" and announced[0][1]

    def fail(*a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ui, "converse", fail)
    assert json.loads(us.converse(message="still there?", graph_id="u-x"))["history_saved"]
    assert len(announced) == 2
    rows, _ = conversation_store.read_history_page(
        tmp_path / "u-x", "principal:founder-1", limit=30)
    assert [row.text for row in rows if row.speaker == "founder"] == ["hello", "still there?"]

    # Nothing stored, nothing to read: no event.
    monkeypatch.setattr(conversation_store, "record_failure", lambda *a, **kw: False)
    monkeypatch.setattr(conversation_store, "record_turn", lambda *a, **kw: 0)
    assert json.loads(us.converse(message="lost", graph_id="u-x"))["history_saved"] is False
    assert len(announced) == 2
    remaining, _ = conversation_store.read_history_page(
        tmp_path / "u-x", "principal:founder-1", limit=30)
    assert remaining == rows


def test_the_connector_creates_an_owner_message_subscription(home: Path) -> None:
    from tinyassets.api.automations import automations
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    with identity_context(Identity(
        user_id=OWNER, username=OWNER,
        capabilities=["tinyassets.universe.write", "tinyassets.universe.admin"],
    )):
        out = automations(action="create", universe_id=UNIVERSE, payload=json.dumps({
            "name": "listen", "branch_def_id": FOLLOWER, "event_type": "owner_message",
        }))
        filtered = automations(action="create", universe_id=UNIVERSE, payload=json.dumps({
            "name": "x", "branch_def_id": FOLLOWER, "event_type": "owner_message",
            "event_filter": {"name": "anything"},
        }))
    assert out["status"] == "automation_created", out
    assert out["automation"]["trigger"]["event_type"] == "owner_message"
    assert filtered["reason"] == "event_filter_invalid"


# -- app_event, through the pump -------------------------------------------------


def test_an_app_event_wakes_the_subscribed_agent_through_the_pump(
    home: Path, monkeypatch,
) -> None:
    _subscribe(home, "app_event", {"name": "tap"})
    assert emit_app_event(home, universe_id=UNIVERSE, principal_id=OWNER,
                          name="other", data={}) == []
    [wake_id] = emit_app_event(home, universe_id=UNIVERSE, principal_id=OWNER,
                               name="tap", data={"x": 1})
    graph = _Graph()
    monkeypatch.setattr(automations_module, "_execute", graph)
    _poll(home)
    assert [(branch, inputs["event"]["name"]) for branch, inputs in graph.calls] == [
        (FOLLOWER, "tap"),
    ]
    assert AutomationStore(home).get(wake_id).retired_at


# -- a wake the agent sets for itself -------------------------------------------


@pytest.fixture
def served(home: Path, monkeypatch):
    """The served engine surface, pinned to the owner's universe."""
    from tests.engine_authority_helpers import mock_engine_admission
    from tinyassets import engine_mcp_server as s

    monkeypatch.setattr(s, "_ACTOR_ID", OWNER)
    monkeypatch.setattr(s, "_GRAPH_ID", UNIVERSE)
    mock_engine_admission(monkeypatch, {UNIVERSE})
    return s


def _create(served, document: dict) -> dict:
    return json.loads(served.write_graph(
        target="automation", operation="create", payload_json=json.dumps(document),
    ))


def test_the_agent_sets_its_own_next_wake_and_it_fires_at_its_time(
    home: Path, served, monkeypatch,
) -> None:
    before = datetime.now(timezone.utc).replace(microsecond=0)  # stored to the second
    out = _create(served, {"name": "check back", "branch_def_id": FOLLOWER,
                           "delay_seconds": 3600, "inputs": {"why": "later"}})
    assert out["status"] == "automation_created", out
    wake_id = out["automation"]["automation_id"]
    due_at = datetime.fromisoformat(out["automation"]["next_due_at"])
    assert before + timedelta(seconds=3600) <= due_at <= before + timedelta(seconds=3660)

    def due(at: datetime) -> list[str]:
        return [row.automation_id for row, _ in
                due_automations(home, universe_id=UNIVERSE, now=at)]

    assert wake_id not in due(before + timedelta(seconds=60))
    assert wake_id in due(due_at + timedelta(seconds=1))


def test_a_wake_set_for_now_runs_on_the_next_poll(home: Path, served, monkeypatch) -> None:
    at = datetime.now(timezone.utc).isoformat()
    out = _create(served, {"name": "now", "branch_def_id": FOLLOWER, "not_before": at})
    assert out["status"] == "automation_created", out
    graph = _Graph()
    monkeypatch.setattr(automations_module, "_execute", graph)
    _poll(home)
    assert [branch for branch, _ in graph.calls] == [FOLLOWER]
    assert AutomationStore(home).get(out["automation"]["automation_id"]).retired_at


@pytest.mark.parametrize("document, detail", [
    ({"delay_seconds": -1}, "delay_seconds must be a number >= 0"),
    ({"delay_seconds": True}, "delay_seconds must be a number >= 0"),
    ({"delay_seconds": 5, "not_before": "2030-01-01T00:00:00+00:00"},
     "give not_before or delay_seconds, not both"),
    ({"not_before": 5}, "not_before must be an ISO-8601 timestamp string"),
])
def test_a_malformed_self_set_wake_is_refused_with_its_reason(
    home: Path, served, document: dict, detail: str,
) -> None:
    out = _create(served, {"name": "x", "branch_def_id": FOLLOWER, **document})
    assert out == {"error": "automation_payload_invalid", "detail": detail}


def test_a_self_set_wake_with_a_cadence_is_two_triggers(home: Path, served) -> None:
    out = _create(served, {"name": "x", "branch_def_id": FOLLOWER,
                           "delay_seconds": 5, "interval_seconds": 60})
    assert out["reason"] == "trigger_invalid"
