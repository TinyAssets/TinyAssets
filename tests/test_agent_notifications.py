"""Notification delivery traverses real owner checks, storage and device ledger."""

import pytest

from tests.test_owner_notifications import _fake, _home, _register
from tinyassets.api.agent_notifications import notify
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.storage.pending_requests import list_pending


@pytest.fixture
def notification_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    home = _home(tmp_path, "u-a", "actor-a")
    _home(tmp_path, "u-b", "actor-b")
    owned = {_register(tmp_path, "actor-a", "phone-a"),
             _register(tmp_path, "actor-a", "browser-a", platform="web")}
    _register(tmp_path, "actor-b", "phone-b")
    recorder, transports = _fake()
    recorder.owned_ids = owned
    monkeypatch.setattr("tinyassets.owner_notifications.resolve_transports", lambda: transports)
    return home, recorder


def test_notification_delivers_and_deduplicates_without_an_answer(notification_home):
    from tinyassets.api.pending_requests import answer_request, withdraw_request

    home, transport = notification_home
    with identity_context(Identity(user_id="actor-a", username="a", capabilities=[])):
        payload = {"title": "Report ready", "body": "The tests passed.",
                   "item_id": "turn_1", "attachment_ref": "file_report"}
        first = notify(universe_id="u-a", payload=payload)
        assert first["created"] and first["delivery"]["sent"] == 2, first
        second = notify(universe_id="u-a", payload=payload)
        assert not second["created"] and second["request_id"] == first["request_id"]
        assert second["delivery"] == {"skipped": "duplicate"}
        row, = list_pending(home)
        assert row["informational"] and not row["requires_answer"]
        assert row["fields"] == row["items"] == []
        assert row["action"]["attachment_ref"] == "file_report"
        assert answer_request(universe_id="u-a", payload={
            "request_id": row["request_id"], "values": {},
        })["error"] == "not_answerable"
        assert len(transport.calls) == 2
        for device, message in transport.calls:
            assert device["device_id"] in transport.owned_ids
            assert message.title.endswith(" updates")
            assert "The tests passed." in message.body
            assert message.data["request_id"] == row["request_id"]
        assert not withdraw_request(universe_id="u-a", payload={
            "request_id": row["request_id"], "reason": "Read",
        }).get("error")
        assert not list_pending(home)


@pytest.mark.parametrize("actor,universe,extra", [
    ("actor-b", "u-a", {}),
    ("actor-a", "u-b", {}),
    ("actor-a", "u-a", {"owner": "actor-b"}),
    ("actor-a", "u-a", {"agent": "someone-else"}),
    ("actor-a", "u-a", {"universe_id": "u-b"}),
    ("actor-a", "u-a", {"link_to_thread": "foreign-agent"}),
])
def test_notification_never_accepts_foreign_authority(notification_home, actor, universe, extra):
    home, transport = notification_home
    with identity_context(Identity(user_id=actor, username=actor, capabilities=[])):
        result = notify(universe_id=universe, payload={"title": "Hi", "body": "Update", **extra})
    assert result.get("error"), result
    assert transport.calls == []
    assert not list_pending(home)
    assert not list_pending(home.parent / "u-b")


def test_custom_agent_source_and_dedupe_are_server_bound(notification_home, monkeypatch):
    home, transport = notification_home
    monkeypatch.setattr("tinyassets.effectors.authenticated_external_call._initiating_agent",
                        lambda _: "social-manager")
    with identity_context(Identity(user_id="actor-a", username="a", capabilities=[])):
        row = notify(universe_id="u-a", payload={"title": "Ready", "body": "Draft ready"})
    assert row["agent"] == "social-manager"
    assert list_pending(home)[0]["agent"] == "social-manager"
    assert len(transport.calls) == 2


def test_notification_reports_no_device_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _home(tmp_path, "u-a", "actor-a")
    with identity_context(Identity(user_id="actor-a", username="a", capabilities=[])):
        row = notify(universe_id="u-a", payload={"title": "Ready", "body": "Draft ready"})
    assert row["created"]
    assert row["delivery"].get("sent", 0) == 0


def test_capability_handbook_and_seed_are_editable(tmp_path):
    from tinyassets import engine_mcp_server as engine
    from tinyassets.starter_skills import CAPABILITIES_SKILL_PATH, capabilities_skill
    from tinyassets.universe_bundle import seed_okf_bundle

    text = capabilities_skill()
    assert engine.SERVED_TOOL_CHAPTERS["write_graph"]["capabilities"] == text
    for phrase in ("persistent box", "git", "egress", "python -m pytest",
                   'operation="notify"', 'invoke_mcp_action("notify"', "deliver files"):
        assert phrase in text
    seed_okf_bundle(tmp_path, purpose="", loop_branch_def_id="")
    path = tmp_path / CAPABILITIES_SKILL_PATH
    assert path.read_text(encoding="utf-8") == text
    path.write_text("My preferences", encoding="utf-8")
    seed_okf_bundle(tmp_path, purpose="", loop_branch_def_id="")
    assert path.read_text(encoding="utf-8") == "My preferences"
