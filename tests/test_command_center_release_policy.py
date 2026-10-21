"""Real publication/install pins with proposed hooks; no live owner or scheduled work."""

import copy

import pytest

from tinyassets import command_center_release_series as releases
from tinyassets import command_center_update_policy as policy
from tinyassets.api import command_center_updates as updates


@pytest.fixture
def published(tmp_path, monkeypatch):
    from tests.cloud_runtime_fixture import cloud_runtime
    from tests.test_command_center_packages import (
        BOB,
        BOB_UNIVERSE,
        OWNER,
        SCOUT,
        SCRIBE,
        UI,
        UNIVERSE,
        _answer,
        _as,
        _ask,
        _automations,
        _publish_action,
        home,
    )
    from tests.test_command_center_system_copy import _preview
    from tinyassets.api import publish_requests
    from tinyassets.app_ui_store import get_app_ui, save_app_ui

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    cloud_runtime.__wrapped__(monkeypatch)
    home.__wrapped__(tmp_path, monkeypatch)
    ui = {**UI, "workflow_refs": {"scout": SCOUT, "scribe": SCRIBE}}
    row = get_app_ui(tmp_path, owner_user_id=OWNER, universe_id=UNIVERSE)
    save_app_ui(
        tmp_path,
        owner_user_id=OWNER,
        universe_id=UNIVERSE,
        expected_revision=row["revision"],
        changes={"ui_library": [ui]},
    )
    beat, follow = _automations(tmp_path)
    action = _publish_action()
    del action["package"]
    action["automation_ids"] = [beat.automation_id, follow.automation_id]
    options = {"summary": "First explicit release"}
    capture, tab = publish_requests.capture_action, publish_requests.tab_text

    def capture_hook(uid, request_action):
        captured = capture(uid, request_action)
        link = releases.capture_release_link(universe_id=uid, action=request_action, **options)
        return {**captured, "release_link": link["release_link"]}

    def tab_hook(captured):
        kind, title, body = tab(captured)
        return kind, title, body + "\n\n" + releases.consent_text(captured["release_link"])

    monkeypatch.setattr(publish_requests, "capture_action", capture_hook)
    monkeypatch.setattr(publish_requests, "tab_text", tab_hook)

    def publish(
        *, summary="Presentation refresh", parent=None, edit=None, action_edit=None, record=True
    ):
        options.clear()
        options.update(summary=summary)
        if parent:
            options.update(series_id=parent["series_id"], parent_release_id=parent["release_id"])
        if edit:
            row = get_app_ui(tmp_path, owner_user_id=OWNER, universe_id=UNIVERSE)
            library = copy.deepcopy(row["ui_library"])
            edit(library[0])
            save_app_ui(
                tmp_path,
                owner_user_id=OWNER,
                universe_id=UNIVERSE,
                expected_revision=row["revision"],
                changes={"ui_library": library},
            )
        requested = copy.deepcopy(action)
        if action_edit:
            action_edit(requested)
        ask = _ask(OWNER, UNIVERSE, requested)
        if "request_id" not in ask:
            raise ValueError(str(ask))
        done = _answer(OWNER, UNIVERSE, ask["request_id"])
        assert done.get("published"), done
        if not record:
            return {"request_id": ask["request_id"], "definition_id": done["agent_definition_id"]}
        with _as(OWNER):
            return releases.record_release(universe_id=UNIVERSE, request_id=ask["request_id"])

    first = publish(summary="First explicit release")
    ask = _preview(first["definition_id"])
    done = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert done.get("installed"), done
    with _as(BOB):
        adoption = updates.record_install(universe_id=BOB_UNIVERSE, request_id=ask["request_id"])
    return {
        "base": tmp_path,
        "owner": BOB,
        "uid": BOB_UNIVERSE,
        "publisher": OWNER,
        "publisher_home": UNIVERSE,
        "first": first,
        "adoption": adoption,
        "publish": publish,
        "source_action": action,
    }


def enable(published, *, accepted=True):
    from tests.test_command_center_packages import _as

    with _as(published["owner"]):
        request = policy.preview_policy(
            universe_id=published["uid"],
            adoption_id=published["adoption"]["adoption_id"],
            enabled=True,
            series_id=published["first"]["series_id"],
            release_id=published["first"]["release_id"],
        )
        if accepted:
            return policy.commit_policy(
                universe_id=published["uid"],
                request_id=request["request_id"],
                plan_digest=request["plan_digest"],
                decision="accepted",
            )
        return request


def eligible(published, target):
    from tests.test_command_center_packages import _as

    with _as(published["owner"]):
        return policy.check_eligibility(
            universe_id=published["uid"],
            adoption_id=published["adoption"]["adoption_id"],
            target_release_id=target["release_id"],
        )










































