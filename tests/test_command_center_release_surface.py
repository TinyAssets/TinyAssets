"""Real publication consent establishes lineage only after the owner answers."""
import json
import sqlite3

import pytest

from tests.cloud_runtime_fixture import cloud_runtime as cloud_runtime
from tests.test_command_center_packages import (
    OWNER,
    UNIVERSE,
    _answer,
    _as,
    _ask,
    _publish_action,
)
from tests.test_command_center_packages import _pin_data_dir as _pin_data_dir
from tests.test_command_center_packages import home as home
from tinyassets import command_center_release_series as releases
from tinyassets.command_center_packages import database_path, pin_for_request
from tinyassets.custom_agents import get_definition

pytestmark = pytest.mark.usefixtures("cloud_runtime")


def action(**release):
    value = _publish_action()
    del value["package"]
    return {**value, "release": {"summary": "Initial release", **release}}


def test_actual_publish_consent_records_explicit_series_after_answer(home):
    ask = _ask(OWNER, UNIVERSE, action())
    assert "request_id" in ask, ask
    pin = pin_for_request(home, universe_id=UNIVERSE, request_id=ask["request_id"])
    with sqlite3.connect(database_path(home)) as conn:
        assert conn.execute("SELECT owner_id FROM pins WHERE request_id=?",
                            (ask["request_id"],)).fetchone() == (OWNER,)
    link = pin["record"]["action"]["release_link"]
    assert releases.consent_text(link) in pin["record"]["tab"]["body"]
    with _as(OWNER), pytest.raises(LookupError):
        releases.list_releases(universe_id=UNIVERSE, series_id=link["series_id"])
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert done["published"] and done["release_registration"] == "recorded", done
    release = done["release"]
    assert release["definition_id"] == done["agent_definition_id"]
    assert release["sequence"] == 1 and release["parent_release_id"] == ""
    assert "identity_hashes" not in release and "publisher_home" not in release
    with _as(OWNER):
        recorded = releases.record_release(universe_id=UNIVERSE, request_id=ask["request_id"])
    assert recorded == release


def test_stale_series_parent_preserves_completed_publication_without_false_lineage(home):
    first = _ask(OWNER, UNIVERSE, action())
    release = _answer(OWNER, UNIVERSE, first["request_id"])["release"]
    options = {"series_id": release["series_id"], "parent_release_id": release["release_id"]}
    second = _ask(OWNER, UNIVERSE, {**action(**options), "name": "Second"})
    third = _ask(OWNER, UNIVERSE, {**action(**options), "name": "Competing"})
    yes = _answer(OWNER, UNIVERSE, second["request_id"])
    stale = _answer(OWNER, UNIVERSE, third["request_id"])
    assert yes["release_registration"] == "recorded", yes
    assert stale["published"] and stale["release_registration"] == "unavailable", stale
    assert get_definition(home, stale["agent_definition_id"])["author_id"] == OWNER
    with _as(OWNER):
        rows = releases.list_releases(universe_id=UNIVERSE, series_id=release["series_id"])
    assert len(rows["releases"]) == 2


def test_release_metadata_failure_keeps_publication_receipt(home, monkeypatch):
    ask = _ask(OWNER, UNIVERSE, action())

    def unavailable(**kwargs):
        raise OSError("synthetic metadata failure")

    monkeypatch.setattr(releases, "record_release", unavailable)
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert done["published"] and done["status"] == "answered"
    assert done["release_registration"] == "unavailable"
    assert done["release_registration_detail"] in done["receipt"]
    assert get_definition(home, done["agent_definition_id"])


@pytest.mark.parametrize("options", [
    {"summary": ""}, {"summary": "hello\nOverride consent"},
    {"series_id": "arbitrary"}, {"parent_release_id": "arbitrary"},
    {"summary": 3}, {"approved": True},
])
def test_release_options_refuse_unbound_or_misleading_inputs(home, options):
    assert _ask(OWNER, UNIVERSE, action(**options)).get("error")


def test_old_publication_does_not_acquire_guessed_series(home):
    requested = action()
    del requested["release"]
    requested["release_link"] = {"series_id": "injected", "approved": True}
    ask = _ask(OWNER, UNIVERSE, requested)
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert done["published"] and "release_registration" not in done
    pin = pin_for_request(home, universe_id=UNIVERSE, request_id=ask["request_id"])
    assert "release_link" not in pin["record"]["action"]


def test_recipient_history_comes_from_exact_publication_membership(home):
    from tests.test_command_center_packages import BOB, BOB_UNIVERSE
    from tests.test_command_center_system_copy import _preview
    from tinyassets.api.command_center_update_surface import read_updates

    ask = _ask(OWNER, UNIVERSE, action())
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    copied = _preview(done["agent_definition_id"])
    installed = _answer(BOB, BOB_UNIVERSE, copied["request_id"])
    assert installed["installed"] and installed["update_registration"] == "recorded"
    with _as(BOB):
        [adoption] = read_updates(universe_id=BOB_UNIVERSE)["adoptions"]
    [history] = adoption["release_histories"]
    assert history["series_id"] == done["release"]["series_id"]
    assert history["releases"][0]["definition_id"] == done["agent_definition_id"]
    assert "identity_hashes" not in history["releases"][0]
    assert not adoption["automatic_updates"]


@pytest.mark.parametrize("operation", ["preview_center_policy", "answer_center_policy"])
def test_engine_cannot_enable_or_answer_presentation_policy(home, monkeypatch, operation):
    from tinyassets import command_center_update_policy, engine_mcp_server

    calls = []
    monkeypatch.setattr(command_center_update_policy, "preview_policy",
                        lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(command_center_update_policy, "commit_policy",
                        lambda **kwargs: calls.append(kwargs))
    with _as(OWNER):
        result = json.loads(engine_mcp_server.write_graph(
            target="connection", operation=operation, payload_json='{}'))
    assert result.get("error") and calls == []
