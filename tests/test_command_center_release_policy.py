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
    from tinyassets.custom_agents import get_app_ui, save_app_ui

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


def test_explicit_release_chain_has_summaries_without_private_identity_map(published):
    from tests.test_command_center_packages import _as

    first = published["first"]
    second = published["publish"](
        parent=first, edit=lambda ui: ui.update(style="main { color: teal; }")
    )
    assert second["sequence"] == 2 and second["parent_release_id"] == first["release_id"]
    with _as(published["owner"]):
        listing = releases.list_releases(universe_id=published["uid"], series_id=first["series_id"])
    assert [r["summary"] for r in listing["releases"]] == [
        "First explicit release",
        "Presentation refresh",
    ]
    assert all(r["available"] for r in listing["releases"])
    assert all(
        "identity_hashes" not in r and "publisher_home" not in r for r in listing["releases"]
    )


def test_policy_default_off_and_only_explicit_consent_enables(published):
    from tests.test_command_center_packages import _as

    target = published["publish"](
        parent=published["first"], edit=lambda ui: ui.update(style="main { color: teal; }")
    )
    assert eligible(published, target)["status"] == "disabled"
    request = enable(published, accepted=False)
    assert eligible(published, target)["status"] == "disabled"
    with _as(published["owner"]):
        declined = policy.commit_policy(
            universe_id=published["uid"],
            request_id=request["request_id"],
            plan_digest=request["plan_digest"],
            decision="declined",
        )
    assert declined["changed"] is False and eligible(published, target)["status"] == "disabled"
    assert enable(published)["enabled"]
    outcome = eligible(published, target)
    assert outcome["eligible"] and not outcome["applied"]
    assert outcome["policy"] == "presentation-updates-v1"


@pytest.mark.parametrize(
    "edit",
    [
        lambda ui: ui.update(script=ui["script"] + "\nconsole.log('changed execution')"),
        lambda ui: ui.update(markup=ui["markup"] + "<button>New action</button>"),
        lambda ui: ui.update(style="main { background: url(https://example.invalid/image); }"),
        lambda ui: ui.update(style="@import 'extra.css';"),
    ],
)
def test_opt_in_does_not_accept_executable_or_resource_changes(published, edit):
    enable(published)
    target = published["publish"](parent=published["first"], edit=edit)
    outcome = eligible(published, target)
    assert (
        not outcome["eligible"]
        and outcome["status"] == "requires_decision"
        and not outcome["applied"]
    )


def test_same_name_different_series_is_not_lineage(published):
    enable(published)
    target = published["publish"](edit=lambda ui: ui.update(name="New presentation"))
    assert target["series_id"] != published["first"]["series_id"]
    outcome = eligible(published, target)
    assert outcome["status"] == "requires_decision" and not outcome["eligible"]


def test_component_selection_reorder_does_not_reuse_stable_keys(published):
    with pytest.raises(ValueError, match="stable component keys"):
        published["publish"](
            parent=published["first"], action_edit=lambda a: a["branch_ids"].reverse()
        )


def test_stale_parent_cannot_append_after_another_release(published):
    first = published["first"]
    published["publish"](parent=first, edit=lambda ui: ui.update(name="Second"))
    with pytest.raises(ValueError, match="parent head changed"):
        published["publish"](parent=first, edit=lambda ui: ui.update(name="Stale third"))


def test_private_edit_after_policy_preview_invalidates_consent(published):
    from tests.test_command_center_packages import _as
    from tinyassets.custom_agents import get_app_ui, save_app_ui

    request = enable(published, accepted=False)
    row = get_app_ui(
        published["base"], owner_user_id=published["owner"], universe_id=published["uid"]
    )
    library = row["ui_library"]
    library[0]["markup"] = "Private recipient content"
    save_app_ui(
        published["base"],
        owner_user_id=published["owner"],
        universe_id=published["uid"],
        expected_revision=row["revision"],
        changes={"ui_library": library},
    )
    with _as(published["owner"]), pytest.raises(ValueError, match="edited or deleted"):
        policy.commit_policy(
            universe_id=published["uid"],
            request_id=request["request_id"],
            plan_digest=request["plan_digest"],
            decision="accepted",
        )


def test_private_edit_blocks_eligibility_but_does_not_block_opt_out(published):
    from tests.test_command_center_packages import _as
    from tinyassets.custom_agents import get_app_ui, save_app_ui

    enable(published)
    target = published["publish"](
        parent=published["first"], edit=lambda ui: ui.update(style="main { color: teal; }")
    )
    row = get_app_ui(
        published["base"], owner_user_id=published["owner"], universe_id=published["uid"]
    )
    row["ui_library"][0]["markup"] = "My private work"
    save_app_ui(
        published["base"],
        owner_user_id=published["owner"],
        universe_id=published["uid"],
        expected_revision=row["revision"],
        changes={"ui_library": row["ui_library"]},
    )
    assert eligible(published, target)["status"] == "requires_decision"
    with _as(published["owner"]):
        request = policy.preview_policy(
            universe_id=published["uid"],
            adoption_id=published["adoption"]["adoption_id"],
            enabled=False,
        )
        result = policy.commit_policy(
            universe_id=published["uid"],
            request_id=request["request_id"],
            plan_digest=request["plan_digest"],
            decision="accepted",
        )
    assert not result["enabled"] and eligible(published, target)["status"] == "disabled"


def test_publication_hook_requires_activated_pin_and_pinned_release_display(published, monkeypatch):
    from tests.test_command_center_packages import _as
    from tinyassets import command_center_packages
    from tinyassets.custom_agents import _agent_connect

    with _agent_connect(published["base"]) as conn:
        row = conn.execute(
            "SELECT request_id FROM command_center_releases WHERE release_id=?",
            (published["first"]["release_id"],),
        ).fetchone()
    actual = command_center_packages.pin_for_request
    mutations = [
        lambda pin: pin.update(state="pinned"),
        lambda pin: pin["record"]["action"].pop("release_link"),
        lambda pin: pin["record"]["tab"].update(body="Publish this design only"),
        lambda pin: pin["record"]["action"]["release_link"].update(summary="Unshown summary"),
    ]
    for mutate in mutations:

        def tampered(*args, **kwargs):
            pin = copy.deepcopy(actual(*args, **kwargs))
            mutate(pin)
            return pin

        monkeypatch.setattr(command_center_packages, "pin_for_request", tampered)
        with _as(published["publisher"]), pytest.raises(ValueError):
            releases.record_release(universe_id=published["publisher_home"], request_id=row[0])
    monkeypatch.setattr(command_center_packages, "pin_for_request", actual)
    with _as(published["publisher"]):
        assert (
            releases.record_release(universe_id=published["publisher_home"], request_id=row[0])
            == published["first"]
        )


def test_parent_head_compare_and_set_survives_two_completed_publications(published):
    from tests.test_command_center_packages import _as

    first = published["first"]
    left = published["publish"](parent=first, edit=lambda ui: ui.update(name="Left"), record=False)
    right = published["publish"](
        parent=first, edit=lambda ui: ui.update(name="Right"), record=False
    )
    with _as(published["publisher"]):
        winner = releases.record_release(
            universe_id=published["publisher_home"], request_id=right["request_id"]
        )
        with pytest.raises(ValueError, match="parent head changed"):
            releases.record_release(
                universe_id=published["publisher_home"], request_id=left["request_id"]
            )
    assert winner["sequence"] == 2
    from tinyassets.custom_agents import get_definition

    assert get_definition(
        published["base"], left["definition_id"]
    )  # Actual publish remains truthfully public.


def test_wrong_owner_home_or_digest_cannot_enable(published):
    from tests.test_command_center_packages import _as, _seed_owner

    request = enable(published, accepted=False)
    with _as(published["publisher"]), pytest.raises(PermissionError):
        policy.commit_policy(
            universe_id=published["uid"],
            request_id=request["request_id"],
            plan_digest=request["plan_digest"],
            decision="accepted",
        )
    _seed_owner(published["base"], universe_id="another-home", owner=published["owner"])
    (published["base"] / "another-home").mkdir(exist_ok=True)
    with _as(published["owner"]):
        with pytest.raises(LookupError):
            policy.commit_policy(
                universe_id="another-home",
                request_id=request["request_id"],
                plan_digest=request["plan_digest"],
                decision="accepted",
            )
        with pytest.raises(ValueError, match="digest mismatch"):
            policy.commit_policy(
                universe_id=published["uid"],
                request_id=request["request_id"],
                plan_digest="0" * 64,
                decision="accepted",
            )
        assert not policy.inspect_policy(
            universe_id=published["uid"], adoption_id=published["adoption"]["adoption_id"]
        )["enabled"]


def test_unrelated_same_author_manual_replacement_is_not_auto_linked(published):
    from tests.test_command_center_packages import _as
    from tests.test_command_center_update_adapter import replacement

    target = replacement(
        {
            "base": published["base"],
            "source_id": published["first"]["definition_id"],
            "publisher": published["publisher"],
        }
    )
    with _as(published["owner"]):
        ask = updates.preview_update(
            universe_id=published["uid"],
            adoption_id=published["adoption"]["adoption_id"],
            definition_id=target,
        )
        result = updates.commit_update(
            universe_id=published["uid"],
            request_id=ask["request_id"],
            plan_digest=ask["plan_digest"],
            decision="accepted",
        )
    assert not result["adoption"]["automatic_updates"]
    with pytest.raises(ValueError, match="has not adopted this exact"):
        enable(published)


@pytest.mark.parametrize("mutation", ["retire", "config", "withdraw"])
def test_dependency_or_source_change_blocks_policy_and_eligibility(published, mutation):
    from tests.test_command_center_update_adapter import _change_retained_automation
    from tinyassets.branch_versions import mark_versions_public
    from tinyassets.custom_agents import get_definition

    enable(published)
    target = published["publish"](
        parent=published["first"], edit=lambda ui: ui.update(name="Refreshed")
    )
    if mutation == "withdraw":
        source = get_definition(published["base"], published["first"]["definition_id"])
        ids = [
            c["published_version_id"]
            for c in source["components"].values()
            if c["kind"] == "tinyassets.branch-ref.v1"
        ]
        mark_versions_public(published["base"], ids, public=False)
    else:
        _change_retained_automation(published, mutation)
    outcome = eligible(published, target)
    assert outcome["status"] == "requires_decision" and not outcome["eligible"]


def test_added_component_requires_decision_not_partial_auto_apply(published):
    from tests.test_command_center_packages import _seed_branch

    enable(published)
    _seed_branch(published["base"], branch_def_id="new-source-workflow", visibility="private")
    target = published["publish"](
        parent=published["first"],
        action_edit=lambda a: a["branch_ids"].append("new-source-workflow"),
    )
    outcome = eligible(published, target)
    assert outcome["status"] == "requires_decision" and not outcome["applied"]


def test_capability_surface_changes_are_not_presentation_updates():
    original = {
        "components": {
            "ui": {
                "kind": "tinyassets.app-ui.v1",
                "name": "Screen",
                "style": "",
                "script": "",
                "markup": "<main></main>",
            }
        },
        "external_origins": [],
    }
    for key, value in [
        ("libraries", ["extra"]),
        ("assets", {"new": "blob"}),
        ("workflow_refs", {"new": "workflow"}),
        ("agent_refs", {"new": "agent"}),
    ]:
        updated = copy.deepcopy(original)
        updated["components"]["ui"][key] = value
        assert policy.presentation_decisions(original, updated) == [
            "executable_ui_or_capability_changed"
        ]
    updated = copy.deepcopy(original)
    updated["external_origins"] = ["https://new-permission.invalid"]
    assert policy.presentation_decisions(original, updated) == ["external_origins_changed"]


def test_policy_replay_and_opt_out_do_not_write_ui_or_run_models(published, monkeypatch):
    from tests.test_command_center_packages import _as
    from tinyassets.custom_agents import get_app_ui
    from tinyassets.providers.router import ProviderRouter

    def forbidden(*args, **kwargs):
        raise AssertionError("policy/eligibility cannot call a model")

    monkeypatch.setattr(ProviderRouter, "call_sync", forbidden)
    monkeypatch.setattr(ProviderRouter, "call_with_policy_sync", forbidden)
    before = get_app_ui(
        published["base"], owner_user_id=published["owner"], universe_id=published["uid"]
    )
    ask = enable(published, accepted=False)
    with _as(published["owner"]):
        first = policy.commit_policy(
            universe_id=published["uid"],
            request_id=ask["request_id"],
            plan_digest=ask["plan_digest"],
            decision="accepted",
        )
        replay = policy.commit_policy(
            universe_id=published["uid"],
            request_id=ask["request_id"],
            plan_digest=ask["plan_digest"],
            decision="accepted",
        )
    assert first["revision"] == replay["revision"] == 1 and replay["already_applied"]
    target = published["publish"](
        parent=published["first"], edit=lambda ui: ui.update(name="New look")
    )
    assert eligible(published, target)["eligible"]
    assert (
        get_app_ui(
            published["base"], owner_user_id=published["owner"], universe_id=published["uid"]
        )
        == before
    )


def test_removed_component_key_cannot_be_reassigned_in_a_later_release(published):
    from tests.test_command_center_packages import _automations

    first = published["first"]
    removed = published["publish"](parent=first, action_edit=lambda a: a.update(automation_ids=[]))
    new_beat, new_follow = _automations(published["base"])
    with pytest.raises(ValueError, match="stable component keys"):
        published["publish"](
            parent=removed,
            action_edit=lambda a: a.update(
                automation_ids=[new_beat.automation_id, new_follow.automation_id]
            ),
        )


def test_completed_opt_out_cannot_be_undone_by_replaying_old_opt_in(published):
    from tests.test_command_center_packages import _as

    enable_request = enable(published, accepted=False)
    with _as(published["owner"]):
        policy.commit_policy(
            universe_id=published["uid"],
            request_id=enable_request["request_id"],
            plan_digest=enable_request["plan_digest"],
            decision="accepted",
        )
        disable_request = policy.preview_policy(
            universe_id=published["uid"],
            adoption_id=published["adoption"]["adoption_id"],
            enabled=False,
        )
        policy.commit_policy(
            universe_id=published["uid"],
            request_id=disable_request["request_id"],
            plan_digest=disable_request["plan_digest"],
            decision="accepted",
        )
        with pytest.raises(LookupError):
            policy.commit_policy(
                universe_id=published["uid"],
                request_id=enable_request["request_id"],
                plan_digest=enable_request["plan_digest"],
                decision="accepted",
            )
        assert not policy.inspect_policy(
            universe_id=published["uid"], adoption_id=published["adoption"]["adoption_id"]
        )["enabled"]


def test_release_integrity_tampering_is_not_treated_as_a_valid_series(published):
    import json

    from tests.test_command_center_packages import _as
    from tinyassets.custom_agents import _agent_connect

    with _agent_connect(published["base"]) as conn:
        row = conn.execute(
            "SELECT record_json FROM command_center_releases WHERE release_id=?",
            (published["first"]["release_id"],),
        ).fetchone()
        record = json.loads(row[0])
        record["author_id"] = "wrong-author"
        conn.execute(
            "UPDATE command_center_releases SET record_json=? WHERE release_id=?",
            (json.dumps(record), published["first"]["release_id"]),
        )
    with _as(published["owner"]), pytest.raises(ValueError, match="integrity mismatch"):
        releases.list_releases(
            universe_id=published["uid"], series_id=published["first"]["series_id"]
        )


def test_publish_release_hook_checks_author_even_with_home_admin_access(published):
    from tests.test_command_center_packages import _as
    from tinyassets.custom_agents import _agent_connect

    with _agent_connect(published["base"]) as conn:
        request = conn.execute(
            "SELECT request_id FROM command_center_releases WHERE release_id=?",
            (published["first"]["release_id"],),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO universe_acl (universe_id,actor_id,permission,granted_at,granted_by) "
            "VALUES (?,?, 'admin',0,'test')",
            (published["publisher_home"], published["owner"]),
        )
    with _as(published["owner"]), pytest.raises(ValueError, match="owner or pinned display"):
        releases.record_release(universe_id=published["publisher_home"], request_id=request)


def test_release_summary_uses_existing_publication_secret_scan(published):
    from tests.test_command_center_packages import SECRET_KEY

    with pytest.raises(ValueError, match="credential-shaped"):
        published["publish"](parent=published["first"], summary="New connection " + SECRET_KEY)
