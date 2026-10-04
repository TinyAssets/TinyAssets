"""Owner surface and completed-copy receipt integration, with real stores."""
import json

from tests.test_command_center_packages import BOB, BOB_UNIVERSE, _answer, _as
from tests.test_command_center_system_copy import _legacy, _preview
from tests.test_command_center_update_adapter import (
    document,
    replacement,
)
from tests.test_command_center_update_adapter import installed as installed
from tinyassets.api.command_center_update_surface import read_updates, write_update
from tinyassets.api.graph_reads import read_graph
from tinyassets.custom_agents import get_definition
from tinyassets.universe_server import write_graph


def test_completed_copy_registers_actual_provenance_automatically(installed):
    done = installed["done"]
    assert done["installed"] and done["update_registration"] == "recorded"
    assert done["adoption"] == installed["adoption"]
    with _as(BOB):
        rows = json.loads(read_graph(target="command_center_updates", graph_id=BOB_UNIVERSE))
    assert len(rows["adoptions"]) == 1
    assert rows["adoptions"][0]["ui_definition_id"] == installed["source_id"]
    assert not rows["adoptions"][0]["automatic_updates"]


def test_owner_route_previews_then_commits_exact_selected_screen(installed):
    target = replacement(installed)
    original = document(installed)
    source = get_definition(installed["base"], installed["source_id"])
    with _as(BOB):
        choices = read_updates(universe_id=BOB_UNIVERSE)
        [adoption] = choices["adoptions"]
        assert target in {item["agent_definition_id"] for item in adoption["candidates"]}
        ask = json.loads(write_graph(target="connection", operation="preview_center_update",
                                    graph_id=BOB_UNIVERSE, payload_json=json.dumps({
                                        "adoption_id": adoption["adoption_id"],
                                        "agent_definition_id": target})))
        assert ask["requires_explicit_consent"] and not ask["applied"]
        assert document(installed) == original
        done = json.loads(write_graph(target="connection", operation="answer_center_update",
                                     graph_id=BOB_UNIVERSE, payload_json=json.dumps({
                                         "request_id": ask["request_id"],
                                         "plan_digest": ask["plan_digest"],
                                         "decision": "accepted"})))
    assert done["applied"] and done["adoption"]["ui_definition_id"] == target
    assert done["adoption"]["retained_definition_id"] == installed["source_id"]
    assert document(installed)["revision"] == original["revision"] + 1
    assert get_definition(installed["base"], installed["source_id"]) == source


def test_update_controls_refuse_other_owner_and_client_supplied_content(installed):
    original = document(installed)
    with _as(installed["publisher"]):
        assert read_updates(universe_id=BOB_UNIVERSE).get("error")
        assert write_update(universe_id=BOB_UNIVERSE, operation="answer_center_update",
                            payload={"request_id": "x", "plan_digest": "x",
                                     "decision": "accepted"}).get("error")
    with _as(BOB):
        assert write_update(universe_id=BOB_UNIVERSE, operation="preview_center_update",
                            payload={"adoption_id": installed["adoption"]["adoption_id"],
                                     "agent_definition_id": replacement(installed),
                                     "ui": {"markup": "client replacement"}}).get("error")
    assert document(installed) == original


def test_history_failure_keeps_successful_install_receipt(installed, monkeypatch):
    from tinyassets.api import command_center_updates

    target = _legacy(installed["base"])
    ask = _preview(target)

    def unavailable(**kwargs):
        raise OSError("synthetic registry failure")

    monkeypatch.setattr(command_center_updates, "record_install", unavailable)
    done = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert done["installed"] and done["status"] == "answered"
    assert done["update_registration"] == "unavailable"
    assert "Your copy is installed" in done["update_registration_detail"]


def test_engine_cannot_answer_the_owner_update_consent(installed, monkeypatch):
    from tinyassets import engine_mcp_server
    from tinyassets.api import command_center_updates

    calls = []
    monkeypatch.setattr(command_center_updates, "commit_update", lambda **kw: calls.append(kw))
    with _as(BOB):
        result = json.loads(engine_mcp_server.write_graph(
            target="connection", operation="answer_center_update", payload_json=json.dumps({
                "request_id": "x", "plan_digest": "x", "decision": "accepted"})))
    assert result.get("error") and calls == []


def test_earlier_copy_recovers_only_exact_receipt_and_preserves_private_edits(installed):
    from tests.test_command_center_update_adapter import edit_recipient
    from tinyassets import command_center_update_registry as registry

    with registry.connect(installed["base"]) as conn:
        conn.execute("DELETE FROM command_center_adoptions")
    with _as(BOB):
        rows = read_updates(universe_id=BOB_UNIVERSE)
        assert rows["adoptions"] == []
        assert rows["earlier_copies"][0]["request_id"] == installed["request_id"]
        again = read_updates(universe_id=BOB_UNIVERSE)
        assert again["adoptions"] == []  # reading does not backfill provenance
    edit_recipient(installed, lambda entries: entries[0].update(markup="My private edit"))
    before = document(installed)
    with _as(BOB):
        refused = write_update(universe_id=BOB_UNIVERSE, operation="register_center_copy",
                               payload={"request_id": installed["request_id"]})
    assert refused.get("error") and "edited" in refused["detail"]
    assert document(installed) == before


def test_unchanged_earlier_copy_can_be_verified_explicitly(installed):
    from tinyassets import command_center_update_registry as registry

    with registry.connect(installed["base"]) as conn:
        conn.execute("DELETE FROM command_center_adoptions")
    before = document(installed)
    with _as(BOB):
        verified = write_update(universe_id=BOB_UNIVERSE, operation="register_center_copy",
                                payload={"request_id": installed["request_id"]})
        rows = read_updates(universe_id=BOB_UNIVERSE)
    assert verified["registered"] and verified["adoption"] == installed["adoption"]
    assert len(rows["adoptions"]) == 1 and rows["earlier_copies"] == []
    assert document(installed) == before
