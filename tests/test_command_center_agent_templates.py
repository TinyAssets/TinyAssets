"""Public instruction templates never carry private binding authority."""

from __future__ import annotations

import json

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_command_center_packages import (
    BOB,
    BOB_UNIVERSE,
    OWNER,
    SCOUT,
    UI,
    UNIVERSE,
    _answer,
    _as,
    _ask,
    _bobs_branches,  # noqa: F401
    _pin_data_dir,  # noqa: F401
    _publish_action,
)
from tests.test_command_center_packages import (
    home as package_home,
)
from tinyassets import command_center_agent_templates as templates
from tinyassets.addressed_agents import AgentNotAddressable, resolve, roster
from tinyassets.api.pending_requests import try_package
from tinyassets.custom_agents import (
    create_binding,
    get_app_ui,
    get_binding,
    get_definition,
    list_bindings,
    publish_definition,
    save_app_ui,
    update_binding,
)

home = package_home
pytestmark = pytest.mark.usefixtures("cloud_runtime")


def _agent(base, owner=OWNER, uid=UNIVERSE, name="Scout", config=None):
    definition = publish_definition(
        base,
        author_id=owner,
        payload={
            "schema_version": 1,
            "name": name,
            "description": "Public instructions",
            "tags": [],
            "components": {
                "identity": {
                    "kind": "instructions",
                    "config": {"instructions": "Help prepare a task; ask before sending anything."},
                }
            },
        },
    )
    binding = create_binding(
        base,
        universe_id=uid,
        created_by=owner,
        definition_id=definition["agent_definition_id"],
        payload={"schema_version": 1, "name": name, **(config or {})},
    )
    return definition, binding


def _publish(base, *, package=False):
    definition, binding = _agent(
        base, config={"role": "scout", "provider_policy_id": "private-choice"}
    )
    row = get_app_ui(base, owner_user_id=OWNER, universe_id=UNIVERSE)
    ui = {**UI, "agent_refs": {"scout": binding["agent_binding_id"]}}
    save_app_ui(
        base,
        owner_user_id=OWNER,
        universe_id=UNIVERSE,
        expected_revision=row["revision"],
        changes={"ui_library": [ui]},
    )
    action = _publish_action()
    if not package:
        del action["package"]
    action["agent_templates"] = {"village-scout": binding["agent_binding_id"]}
    ask = _ask(OWNER, UNIVERSE, action)
    assert "request_id" in ask, ask
    published = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert published.get("published"), published
    return published["agent_definition_id"], definition, binding


def _copy_request(definition_id):
    with _as(BOB):
        asked = try_package(
            universe_id=BOB_UNIVERSE, payload={"agent_definition_id": definition_id}
        )
    assert "request_id" in asked, asked
    return asked["request_id"]


@pytest.mark.parametrize("package", [False, True])
def test_copy_agents_maps_references_preserves_existing_and_source(home, package):
    source, definition, original = _publish(home, package=package)
    _own_definition, existing = _agent(home, BOB, BOB_UNIVERSE, "My existing Scout")
    public = get_definition(home, source)
    component = public["components"]["village-scout"]
    assert component == {
        "kind": templates.AGENT_REF_KIND,
        "name": "Scout",
        "agent_definition_id": definition["agent_definition_id"],
        "content_fingerprint": definition["content_fingerprint"],
    }
    assert original["agent_binding_id"] not in json.dumps(public)
    assert "private-choice" not in json.dumps(public)
    assert public["components"]["ui"]["agent_refs"] == {"scout": "village-scout"}
    before = list_bindings(home, universe_id=BOB_UNIVERSE, limit=None)
    request_id = _copy_request(source)
    assert list_bindings(home, universe_id=BOB_UNIVERSE, limit=None) == before
    result = _answer(BOB, BOB_UNIVERSE, request_id)
    assert result.get("installed"), result
    target = result["agents"]["village-scout"]
    assert target != original["agent_binding_id"]
    installed = get_binding(home, universe_id=BOB_UNIVERSE, binding_id=target)
    assert installed["configuration"] == {"schema_version": 1, "name": "Scout"}
    assert installed["status"] == "configured" and installed["created_by"] == BOB
    assert (
        get_binding(home, universe_id=BOB_UNIVERSE, binding_id=existing["agent_binding_id"])
        == existing
    )
    assert (
        get_binding(home, universe_id=UNIVERSE, binding_id=original["agent_binding_id"]) == original
    )
    assert get_definition(home, source) == public
    copied_ui = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"][-1]
    assert copied_ui["agent_refs"] == {"scout": target}
    assert copied_ui["script"] == public["components"]["ui"]["script"]
    assert {row["agent_id"] for row in roster(home, universe_id=BOB_UNIVERSE, owner=BOB)} >= {
        target,
        existing["agent_binding_id"],
    }
    agent = resolve(home, universe_id=BOB_UNIVERSE, owner=BOB, agent_id=target)
    assert agent.instructions == (
        ("identity", "instructions", "Help prepare a task; ask before sending anything."),
    )
    with pytest.raises(AgentNotAddressable):
        resolve(home, universe_id=BOB_UNIVERSE, owner=OWNER, agent_id=target)


@pytest.mark.parametrize("package", [False, True])
def test_screen_without_published_agents_reports_empty_roster_before_copy(home, package):
    from tinyassets.storage.pending_requests import get_request

    _definition, original = _agent(home)
    row = get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
    # A roster-driven screen works for its publisher without declaring any
    # portable agent_refs, as in the published Village/House designs.
    save_app_ui(
        home, owner_user_id=OWNER, universe_id=UNIVERSE,
        expected_revision=row["revision"],
        changes={"ui_library": [{**UI, "script": "tinyassets.call('agents.list', {})"}]},
    )
    action = _publish_action()
    if not package:
        del action["package"]
    asked = _ask(OWNER, UNIVERSE, action)
    published = _answer(OWNER, UNIVERSE, asked["request_id"])
    source = published["agent_definition_id"]
    before = list_bindings(home, universe_id=BOB_UNIVERSE, limit=None)
    request_id = _copy_request(source)
    body = get_request(home / BOB_UNIVERSE, request_id)["body"]
    result = _answer(BOB, BOB_UNIVERSE, request_id)
    # Reproduce the original symptom through the real handlers and stores:
    # working screen, no copied chat bindings, even with public instructions
    # behind a binding in the publisher's command center.
    assert result.get("installed"), result
    assert get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"]
    assert list_bindings(home, universe_id=BOB_UNIVERSE, limit=None) == before
    assert get_binding(home, universe_id=UNIVERSE,
                       binding_id=original["agent_binding_id"]) == original
    assert "no chat agents will be copied" in body
    assert body.count("No public chat-agent templates are included") == 1
    assert "The screen's agent list uses your own roster and will be empty" in body
    assert "empty" in body and "republish" in body
    assert original["agent_binding_id"] not in body


@pytest.mark.parametrize("has_screen", [False, True])
def test_agent_consent_screen_line_is_conditional_and_domain_neutral(has_screen):
    lines = templates.consent_lines([], has_screen=has_screen)
    body = "\n".join(lines)
    assert ("screen" in body) == has_screen
    assert "village" not in body.lower() and "house" not in body.lower()
    assert body.count("No public chat-agent templates are included") == 1
    if has_screen:
        assert "add your own agents or the publisher includes them" in body


def test_package_without_screen_omits_screen_consent(home):
    from tinyassets.storage.pending_requests import get_request

    action = _publish_action()
    del action["ui_id"]
    asked = _ask(OWNER, UNIVERSE, action)
    published = _answer(OWNER, UNIVERSE, asked["request_id"])
    request_id = _copy_request(published["agent_definition_id"])
    request = get_request(home / BOB_UNIVERSE, request_id)
    assert not request["action"]["plan"]["ui"]
    assert "screen" not in request["body"].lower()
    assert "no chat agents will be copied" in request["body"]


@pytest.mark.parametrize("package", [False, True])
def test_publish_screen_without_templates_warns_agents_are_not_included(home, package):
    from tinyassets.storage.pending_requests import get_request

    _agent(home)
    action = _publish_action()
    if not package:
        del action["package"]
    asked = _ask(OWNER, UNIVERSE, action)
    body = get_request(home / UNIVERSE, asked["request_id"])["body"]
    assert "No chat agents are included" in body
    assert "select the agents" in body
    assert "empty" in body


@pytest.mark.parametrize("package", [False, True])
def test_missing_declared_agents_are_named_before_any_copy(home, package):
    source, _definition, _binding = _publish(home, package=package)
    public = get_definition(home, source)
    components = dict(public["components"])
    components["ui"] = {
        **components["ui"],
        "agent_refs": {"scout": "village-scout", "housemate": "missing-cat",
                       "villager": "missing-baker"},
    }
    broken = publish_definition(home, author_id=OWNER, payload={
        "schema_version": 1, "name": "Missing residents", "description": "",
        "tags": public["tags"], "components": components,
    })
    before = list_bindings(home, universe_id=BOB_UNIVERSE, limit=None)
    result = _ask(BOB, BOB_UNIVERSE, {
        "type": "install", "agent_definition_id": broken["agent_definition_id"],
    })
    assert result.get("error"), result
    detail = json.dumps(result)
    assert "housemate" in detail and "villager" in detail
    assert "not included" in detail and "republish" in detail
    if not package:
        from tinyassets.api.system_copy_requests import list_systems

        card = next(row for row in list_systems()
                    if row["agent_definition_id"] == broken["agent_definition_id"])
        assert not card["available"]
        assert "housemate" in card["unavailable_reason"]
    assert list_bindings(home, universe_id=BOB_UNIVERSE, limit=None) == before
    assert _bobs_branches(home) == []
    assert not get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"]


def test_export_refuses_wrong_owner_serving_and_consumer_bindings(home):
    _definition, binding = _agent(home)
    with pytest.raises(ValueError, match="not one of your"):
        templates.export_templates(home, UNIVERSE, BOB, {"scout": binding["agent_binding_id"]})
    for config in ({"role": "app_experience"}, {"turn_consumer": {}}):
        _definition, invalid = _agent(home, config=config)
        with pytest.raises(ValueError, match="not one of your"):
            templates.export_templates(
                home, UNIVERSE, OWNER, {"scout": invalid["agent_binding_id"]}
            )
    serving = next(
        b
        for b in list_bindings(home, universe_id=BOB_UNIVERSE, limit=None)
        if b["status"] == "serving"
    )
    with pytest.raises(ValueError, match="not one of your"):
        templates.export_templates(home, BOB_UNIVERSE, BOB, {"scout": serving["agent_binding_id"]})


def test_replay_is_atomic_and_recipient_edits_are_never_overwritten(home):
    _definition, original = _agent(home)
    component = templates.export_templates(
        home, UNIVERSE, OWNER, {"scout": original["agent_binding_id"]}
    )["scout"]
    template = {"key": "scout", **component}
    target = templates.install(home, BOB_UNIVERSE, BOB, "pin-1", template)
    assert templates.install(home, BOB_UNIVERSE, BOB, "pin-1", template) == target
    assert templates.install(home, UNIVERSE, OWNER, "pin-1", template) != target
    edited = update_binding(
        home,
        universe_id=BOB_UNIVERSE,
        binding_id=target,
        updated_by=BOB,
        expected_revision=1,
        payload={"schema_version": 1, "name": "My edits"},
    )
    with pytest.raises(ValueError, match="changed or its identity conflicts"):
        templates.install(home, BOB_UNIVERSE, BOB, "pin-1", template)
    assert get_binding(home, universe_id=BOB_UNIVERSE, binding_id=target) == edited


def test_invalid_public_reference_fails_before_any_recipient_effect(home):
    source, _definition, _original = _publish(home)
    public = get_definition(home, source)
    components = dict(public["components"])
    components["village-scout"] = {**components["village-scout"], "content_fingerprint": "0" * 64}
    corrupt = publish_definition(
        home,
        author_id=OWNER,
        payload={
            "schema_version": 1,
            "name": "Invalid template",
            "description": "",
            "tags": public["tags"],
            "components": components,
        },
    )
    before = list_bindings(home, universe_id=BOB_UNIVERSE, limit=None)
    with _as(BOB):
        result = try_package(
            universe_id=BOB_UNIVERSE,
            payload={"agent_definition_id": corrupt["agent_definition_id"]},
        )
    assert result.get("error"), result
    assert list_bindings(home, universe_id=BOB_UNIVERSE, limit=None) == before
    assert _bobs_branches(home) == []
    assert not get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"]


@pytest.mark.parametrize(
    "component",
    [
        {"kind": "instructions", "config": {"instructions": "Hi", "tools": ["send"]}},
        {"kind": "workflow", "branch_def_id": SCOUT},
        {"kind": "instructions", "config": {}},
    ],
)
def test_unsupported_definition_contract_never_silently_drops_fields(home, component):
    definition = publish_definition(
        home,
        author_id=OWNER,
        payload={
            "schema_version": 1,
            "name": "Unsupported",
            "description": "",
            "tags": [],
            "components": {"identity": component},
        },
    )
    binding = create_binding(
        home,
        universe_id=UNIVERSE,
        created_by=OWNER,
        definition_id=definition["agent_definition_id"],
        payload={"schema_version": 1, "name": "Unsupported"},
    )
    with pytest.raises(ValueError, match="instruction-only"):
        templates.export_templates(home, UNIVERSE, OWNER, {"scout": binding["agent_binding_id"]})


def test_crash_after_binding_insert_replays_without_duplicate(home, monkeypatch):
    source, _definition, _original = _publish(home)
    request_id = _copy_request(source)
    real_install = templates.install
    inserted = []

    def interrupted(*args):
        target = real_install(*args)
        inserted.append(target)
        raise OSError("simulated crash before saving progress")

    monkeypatch.setattr(templates, "install", interrupted)
    first = _answer(BOB, BOB_UNIVERSE, request_id)
    assert first.get("error"), first
    assert len(inserted) == 1
    monkeypatch.setattr(templates, "install", real_install)
    resumed = _answer(BOB, BOB_UNIVERSE, request_id)
    assert resumed.get("installed"), resumed
    assert resumed["agents"] == {"village-scout": inserted[0]}
    assert (
        sum(
            row["agent_binding_id"] == inserted[0]
            for row in list_bindings(home, universe_id=BOB_UNIVERSE, limit=None)
        )
        == 1
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"created_by": "other-owner"},
        {"universe_id": "other-home"},
        {"status": "serving"},
        {"revision": 2},
    ],
)
def test_binding_identity_collision_refuses_without_repairing_existing(home, changed):
    from tinyassets.custom_agents import _agent_connect

    _definition, original = _agent(home)
    component = templates.export_templates(
        home, UNIVERSE, OWNER, {"scout": original["agent_binding_id"]}
    )["scout"]
    template = {"key": "scout", **component}
    target = templates.install(home, BOB_UNIVERSE, BOB, "pin-collision", template)
    with _agent_connect(home) as conn:
        for field, value in changed.items():
            conn.execute(
                f"UPDATE agent_bindings SET {field} = ? WHERE agent_binding_id = ?", (value, target)
            )
        before = dict(
            conn.execute(
                "SELECT * FROM agent_bindings WHERE agent_binding_id = ?", (target,)
            ).fetchone()
        )
    with pytest.raises(ValueError, match="identity conflicts"):
        templates.install(home, BOB_UNIVERSE, BOB, "pin-collision", template)
    with _agent_connect(home) as conn:
        assert (
            dict(
                conn.execute(
                    "SELECT * FROM agent_bindings WHERE agent_binding_id = ?", (target,)
                ).fetchone()
            )
            == before
        )


def test_publisher_agent_change_after_consent_preview_requires_new_request(home):
    _definition, binding = _agent(home)
    action = _publish_action()
    del action["package"]
    action["agent_templates"] = {"scout": binding["agent_binding_id"]}
    request = _ask(OWNER, UNIVERSE, action)
    update_binding(
        home,
        universe_id=UNIVERSE,
        binding_id=binding["agent_binding_id"],
        updated_by=OWNER,
        expected_revision=1,
        payload={"schema_version": 1, "name": "Changed name"},
    )
    result = _answer(OWNER, UNIVERSE, request["request_id"])
    assert result.get("error") and not result.get("published"), result


def test_unresolved_alias_refuses_before_publication(home):
    row = get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
    save_app_ui(
        home,
        owner_user_id=OWNER,
        universe_id=UNIVERSE,
        expected_revision=row["revision"],
        changes={"ui_library": [{**UI, "agent_refs": {"scout": "unselected-binding"}}]},
    )
    result = _ask(OWNER, UNIVERSE, _publish_action())
    assert result.get("error") and "request_id" not in result, result


@pytest.mark.parametrize("field,target_field", [
    ("invoke_branch_spec", "branch_def_id"),
    ("invoke_branch_version_spec", "branch_version_id"),
])
@pytest.mark.parametrize("included", [False, True])
def test_nested_workflow_dependency_is_refused_before_copy(home, field, target_field, included):
    from tests.test_command_center_system_copy import _legacy
    from tinyassets.branch_versions import publish_branch_version
    from tinyassets.daemon_server import get_branch_definition, save_branch_definition

    source = _legacy(home)
    public = get_definition(home, source)
    branch = get_branch_definition(home, branch_def_id=SCOUT)
    node = branch["node_defs"][0]
    node["prompt_template"] = ""
    node["source_code"] = ""
    child = public["components"]["workflow-2"]["published_version_id"]
    if not included:
        child = "missing-child@01234567"
    if target_field == "branch_def_id":
        child = child.split("@", 1)[0]
    node[field] = {target_field: child, "inputs_mapping": {}, "output_mapping": {}}
    save_branch_definition(home, branch_def=branch)
    version = publish_branch_version(home, branch, publisher=OWNER, public=True)
    components = dict(public["components"])
    components["workflow-1"] = {
        **components["workflow-1"],
        "published_version_id": version.branch_version_id,
    }
    bad = publish_definition(
        home,
        author_id=OWNER,
        payload={
            "schema_version": 1,
            "name": "Nested dependency",
            "description": "",
            "tags": public["tags"],
            "components": components,
        },
    )
    with _as(BOB):
        result = try_package(
            universe_id=BOB_UNIVERSE, payload={"agent_definition_id": bad["agent_definition_id"]}
        )
    assert result.get("error"), result
    assert _bobs_branches(home) == []
    assert not get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"]


@pytest.mark.parametrize("field", ["invoke_branch_spec", "invoke_branch_version_spec"])
def test_declared_empty_nested_workflow_spec_is_also_unsupported(field):
    with pytest.raises(ValueError, match="nested workflow dependencies"):
        templates.reject_nested_workflows({"node_defs": [{field: {}}]})
    templates.reject_nested_workflows({"node_defs": [{field: None}]})


def test_unpowered_recipient_installs_without_model_or_router_calls(monkeypatch, request):
    import tests.test_command_center_packages as fixture_module
    from tinyassets.providers.router import ProviderRouter

    monkeypatch.setattr(fixture_module, "_seed_bob_serving", lambda base: None)
    base = request.getfixturevalue("home")
    assert list_bindings(base, universe_id=BOB_UNIVERSE, limit=None) == []
    assert not (base / BOB_UNIVERSE / ".credentials.json").exists()
    source, _definition, _binding = _publish(base)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("installation must not request model inference")

    monkeypatch.setattr(ProviderRouter, "available_providers", lambda self: [])
    for method in (
        "call",
        "call_sync",
        "call_with_policy",
        "call_with_policy_sync",
        "call_judge_ensemble",
    ):
        monkeypatch.setattr(ProviderRouter, method, forbidden)
    request_id = _copy_request(source)
    result = _answer(BOB, BOB_UNIVERSE, request_id)
    assert result.get("installed"), result
    assert len(result["agents"]) == 1
    assert calls == []
    assert all(
        row["status"] == "configured"
        for row in list_bindings(base, universe_id=BOB_UNIVERSE, limit=None)
    )


def test_known_recipient_agent_conflict_refuses_before_workflow_copy(home):
    from tinyassets.command_center_packages import pin_for_request

    source, _definition, _binding = _publish(home)
    request_id = _copy_request(source)
    pin = pin_for_request(home, universe_id=BOB_UNIVERSE, request_id=request_id)
    template = pin["record"]["action"]["plan"]["agent_templates"][0]
    target = templates.install(home, BOB_UNIVERSE, BOB, pin["pin_id"], template)
    edited = update_binding(
        home,
        universe_id=BOB_UNIVERSE,
        binding_id=target,
        updated_by=BOB,
        expected_revision=1,
        payload={"schema_version": 1, "name": "Recipient changed this"},
    )
    result = _answer(BOB, BOB_UNIVERSE, request_id)
    assert result.get("error"), result
    assert _bobs_branches(home) == []
    assert get_binding(home, universe_id=BOB_UNIVERSE, binding_id=target) == edited
    assert not get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"]


def test_stable_template_keys_do_not_depend_on_selection_order(home):
    _definition, scout = _agent(home, name="Scout")
    _other_definition, scribe = _agent(home, name="Scribe")
    forward = {
        "village-scout": scout["agent_binding_id"],
        "village-scribe": scribe["agent_binding_id"],
    }
    backward = dict(reversed(list(forward.items())))
    assert templates.export_templates(home, UNIVERSE, OWNER, forward) == templates.export_templates(
        home, UNIVERSE, OWNER, backward
    )


def test_legacy_copy_plan_keeps_its_existing_digest_shape(home):
    import hashlib

    from tests.test_command_center_system_copy import _legacy
    from tinyassets.api.system_copy_requests import _plan

    source = _legacy(home)
    plan = _plan({"agent_definition_id": source})
    assert "agent_templates" not in plan
    old_shape = {key: value for key, value in plan.items() if key != "digest"}
    expected = hashlib.sha256(
        json.dumps(
            {"definition": get_definition(home, source), "plan": old_shape}, sort_keys=True
        ).encode()
    ).hexdigest()
    assert plan["digest"] == expected


@pytest.mark.parametrize("bad_target", ["nested", "missing", None, [], {}, 123])
def test_package_checks_copied_version_not_publisher_alias_before_effects(home, bad_target):
    from tests.test_command_center_packages import _published
    from tinyassets.branch_versions import publish_branch_version
    from tinyassets.daemon_server import get_branch_definition

    source = _published(home)["done"]["agent_definition_id"]
    public = get_definition(home, source)
    components = dict(public["components"])
    workflow = dict(components["workflow-1"])
    workflow["version_id"] = workflow["published_version_id"]
    if bad_target == "nested":
        branch = get_branch_definition(home, branch_def_id=SCOUT)
        node = branch["node_defs"][0]
        node["prompt_template"] = ""
        node["source_code"] = ""
        node["invoke_branch_spec"] = {"branch_def_id": "missing-live-child",
                                      "inputs_mapping": {}, "output_mapping": {}}
        version = publish_branch_version(home, branch, publisher=OWNER, public=True)
        workflow["published_version_id"] = version.branch_version_id
    elif bad_target == "missing":
        del workflow["published_version_id"]
    else:
        workflow["published_version_id"] = bad_target
    components["workflow-1"] = workflow
    definition = publish_definition(home, author_id=OWNER, payload={
        "schema_version": 1, "name": "Masked package", "description": "",
        "tags": public["tags"], "components": components})
    before = list_bindings(home, universe_id=BOB_UNIVERSE, limit=None)
    with _as(BOB):
        result = try_package(universe_id=BOB_UNIVERSE,
                            payload={"agent_definition_id": definition["agent_definition_id"]})
    assert result.get("error"), result
    assert "request_id" not in result
    assert _bobs_branches(home) == []
    assert list_bindings(home, universe_id=BOB_UNIVERSE, limit=None) == before
    assert not get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"]
