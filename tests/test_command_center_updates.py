"""Data-loss/authority mutation table for unconnected recipient update planning."""

import copy
import hashlib
import json
from dataclasses import replace

import pytest

from tinyassets.command_center_updates import (
    AGENT_KIND,
    Adoption,
    Candidate,
    Component,
    InstalledComponent,
    Observation,
    Release,
    digest,
    legacy_provenance,
    plan_update,
    recheck_plan,
    verify_chain,
)

UI = "tinyassets.app-ui.v1"
WORKFLOW = "tinyassets.branch-ref.v1"
AUTO = "tinyassets.automation-spec.v1"


def component(key="screen", content="v1", **kwargs):
    return Component(key, kwargs.pop("kind", UI), digest(content), **kwargs)


def release(components, parent=None, **kwargs):
    return Release(
        "publication-1",
        "publisher",
        kwargs.pop("definition_id", "def-1"),
        digest(kwargs.pop("definition", components[0].source_digest if components else "empty")),
        1 if parent is None else parent.sequence + 1,
        "" if parent is None else parent.release_id,
        "Release summary",
        tuple(components),
        **kwargs,
    )


def fixture(*, old=None, new=None, automatic=False):
    old, new = old or component(), new or component(content="v2")
    first = release([old])
    second = release([new], first)
    installed = InstalledComponent(old, first.release_id, "recipient-screen", digest("remapped-v1"))
    adoption = Adoption(
        "adoption-1",
        "recipient-home",
        first.publication_id,
        first.author_id,
        (installed,),
        (),
        automatic,
    )
    return dict(
        adoption=adoption,
        releases=(first, second),
        selected=(old.key,),
        observations=(Observation(old.key, installed.target_id, installed.installed_digest),),
        candidates=(Candidate(new.key, installed.target_id, digest("remapped-v2")),),
        public_release_ids=frozenset({first.release_id, second.release_id}),
    )


def test_clean_manual_plan_is_owner_bound_and_requires_no_inference():
    args = fixture()
    before = copy.deepcopy(args)
    plan = plan_update(**args)
    assert args == before  # Pure, no mutations even on successful planning.
    assert plan.ready and not plan.automatic
    assert plan.universe_id == "recipient-home"
    assert plan.changes[0].expected_digest == digest("remapped-v1")
    assert plan.changes[0].installed_digest == digest("remapped-v2")
    assert plan.changes[0].preserve_runtime_state
    assert plan.plan_digest == plan_update(**args).plan_digest


@pytest.mark.parametrize(
    ("current", "reason"),
    [
        (digest("recipient-private-edit"), "recipient_edited"),
        (None, "recipient_deleted"),
    ],
)
def test_recipient_mutations_block_overwrite(current, reason):
    args = fixture(automatic=True)
    args["observations"] = (replace(args["observations"][0], current_digest=current),)
    plan = plan_update(**args)
    assert ("screen", reason) in plan.conflicts
    assert not plan.changes and not plan.automatic and not plan.ready


def test_recipient_edit_survives_unchanged_source():
    old = component()
    args = fixture(old=old, new=old, automatic=True)
    args["observations"] = (replace(args["observations"][0], current_digest=digest("my-edit")),)
    plan = plan_update(**args)
    assert not plan.changes and not plan.conflicts and not plan.automatic
    assert plan.installed_versions == (("screen", args["releases"][0].release_id),)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("withdrawn", "source_unavailable"),
        ("missing_observation", "missing_destination_observation"),
        ("different_target", "recipient_target_changed"),
        ("removed", "source_removed_keep_recipient"),
        ("kind", "component_kind_changed"),
    ],
)
def test_closed_failure_table(mutation, reason):
    args = fixture(automatic=True)
    if mutation == "withdrawn":
        args["public_release_ids"] = frozenset()
    elif mutation == "missing_observation":
        args["observations"] = ()
    elif mutation == "different_target":
        args["observations"] = (replace(args["observations"][0], target_id="other"),)
        args["candidates"] = (replace(args["candidates"][0], target_id="other"),)
    else:
        new = [] if mutation == "removed" else [component(kind=WORKFLOW)]
        args["releases"] = (args["releases"][0], release(new, args["releases"][0]))
        args["public_release_ids"] = frozenset(r.release_id for r in args["releases"])
    plan = plan_update(**args)
    assert any(reason == value for _, value in plan.conflicts)
    assert not plan.automatic and not plan.ready


@pytest.mark.parametrize(
    ("kind", "capabilities", "privileged", "reason"),
    [
        (UI, ("write:mail",), False, "new_capabilities"),
        (UI, (), True, "privileged_or_agent_change"),
        (AGENT_KIND, (), False, "privileged_or_agent_change"),
    ],
)
def test_opt_in_does_not_grant_new_authority(kind, capabilities, privileged, reason):
    args = fixture(
        old=component(kind=kind),
        new=component(content="v2", kind=kind, capabilities=capabilities, privileged=privileged),
        automatic=True,
    )
    plan = plan_update(**args)
    assert ("screen", reason) in plan.decisions
    assert not plan.automatic and not plan.ready


def test_automatic_only_with_existing_accepted_capabilities():
    args = fixture(new=component(content="v2", capabilities=("read:own-data",)), automatic=True)
    assert not plan_update(**args).automatic
    args["adoption"] = replace(args["adoption"], accepted_capabilities=("read:own-data",))
    assert plan_update(**args).automatic
    args["adoption"] = replace(args["adoption"], auto_update=False)
    assert not plan_update(**args).automatic


def test_automation_update_cannot_enable_paused_state():
    args = fixture(old=component(kind=AUTO), new=component(content="v2", kind=AUTO), automatic=True)
    change = plan_update(**args).changes[0]
    assert change.preserve_runtime_state is True
    assert "enabled" not in change.__dataclass_fields__


def multi_component_fixture():
    workflow1 = component("workflow", "w1", kind=WORKFLOW)
    screen1 = component(dependencies=(("workflow", workflow1.source_digest),))
    workflow2 = component("workflow", "w2", kind=WORKFLOW)
    screen2 = component(content="v2", dependencies=(("workflow", workflow2.source_digest),))
    first, second = release([screen1, workflow1]), None
    second = release([screen2, workflow2], first)
    installed = tuple(
        InstalledComponent(c, first.release_id, f"recipient-{c.key}", digest(c.key))
        for c in first.components
    )
    adoption = Adoption(
        "adoption-1", "recipient-home", "publication-1", "publisher", installed, auto_update=True
    )
    return dict(
        adoption=adoption,
        releases=(first, second),
        selected=("screen", "workflow"),
        observations=tuple(
            Observation(c.source.key, c.target_id, c.installed_digest) for c in installed
        ),
        candidates=tuple(
            Candidate(c.key, f"recipient-{c.key}", digest(c.source_digest))
            for c in second.components
        ),
        public_release_ids=frozenset({second.release_id}),
    )


@pytest.mark.parametrize("selected", [("screen",), ("workflow",)])
def test_selected_and_retained_dependencies_both_checked_without_auto_expansion(selected):
    args = multi_component_fixture()
    args["selected"] = selected
    plan = plan_update(**args)
    assert len(plan.changes) == 1
    assert ("screen", "dependency_mismatch:workflow") in plan.conflicts
    assert not plan.automatic


def test_compatible_dependency_selection_and_concurrent_guard():
    args = multi_component_fixture()
    plan = plan_update(**args)
    assert plan.ready and plan.automatic
    assert {c.key for c in plan.changes} == {"screen", "workflow"}
    args["observations"] = (
        args["observations"][0],
        replace(args["observations"][1], current_digest=digest("racing-edit")),
    )
    later = plan_update(**args)
    assert not later.ready and later.plan_digest != plan.plan_digest
    assert ("workflow", "recipient_edited") in later.conflicts


def test_unchanged_dependency_with_local_edit_blocks_new_dependent():
    args = multi_component_fixture()
    first = args["releases"][0]
    workflow = first.components[1]
    screen = component(content="v2", dependencies=(("workflow", workflow.source_digest),))
    second = release([screen, workflow], first)
    args.update(
        releases=(first, second),
        selected=("screen",),
        public_release_ids=frozenset({second.release_id}),
    )
    args["observations"] = (
        args["observations"][0],
        replace(args["observations"][1], current_digest=None),
    )
    assert ("screen", "dependency_changed:workflow") in plan_update(**args).conflicts


def test_mixed_versions_preserved_when_independent_component_updated():
    args = multi_component_fixture()
    first = release([component(), component("workflow", kind=WORKFLOW)])
    second = release([component(content="v2"), first.components[1]], first)
    args["releases"] = (first, second)
    args["adoption"] = replace(
        args["adoption"],
        components=tuple(
            replace(old, source=new, release_id=first.release_id)
            for old, new in zip(args["adoption"].components, first.components)
        ),
    )
    args.update(selected=("screen",), public_release_ids=frozenset({second.release_id}))
    plan = plan_update(**args)
    assert plan.ready
    assert dict(plan.installed_versions) == {
        "screen": first.release_id,
        "workflow": first.release_id,
    }
    assert dict(plan.proposed_versions) == {
        "screen": second.release_id,
        "workflow": first.release_id,
    }


@pytest.mark.parametrize("collision", [False, True])
def test_new_components_require_decision_and_never_overwrite(collision):
    args = fixture(automatic=True)
    first = args["releases"][0]
    added = component("new-screen")
    second = release([first.components[0], added], first)
    args.update(
        releases=(first, second),
        selected=("new-screen",),
        public_release_ids=frozenset({second.release_id}),
        candidates=(Candidate("new-screen", "new-target", digest("new")),),
        observations=(
            Observation("new-screen", "new-target", digest("mine") if collision else None),
        ),
    )
    plan = plan_update(**args)
    assert not plan.automatic
    if collision:
        assert ("new-screen", "destination_collision") in plan.conflicts and not plan.changes
    else:
        assert ("new-screen", "new_component") in plan.decisions


@pytest.mark.parametrize(
    "mutation", ["author", "publication", "parent", "sequence", "missing_ancestry"]
)
def test_release_chain_mutations_rejected(mutation):
    args = fixture()
    first, second = args["releases"]
    changes = {
        "author": {"author_id": "attacker"},
        "publication": {"publication_id": "other"},
        "parent": {"parent_id": digest("wrong")},
        "sequence": {"sequence": 4},
    }
    chain = (
        (second,)
        if mutation == "missing_ancestry"
        else (first, replace(second, **changes[mutation]))
    )
    with pytest.raises(ValueError):
        verify_chain(chain)


def test_forged_adoption_component_baseline_rejected():
    args = fixture()
    old = args["adoption"].components[0]
    args["adoption"] = replace(
        args["adoption"], components=(replace(old, source=component(content="forged")),)
    )
    with pytest.raises(ValueError, match="provenance"):
        plan_update(**args)


def legacy_fixture():
    definition = {"agent_definition_id": "source-id", "author_id": "publisher", "name": "Village"}
    plan = {
        "publication_kind": "system",
        "definition_id": "source-id",
        "author": "publisher",
        "ui": {"name": "Village"},
        "workflows": [{"key": "flow"}],
        "automations": [{"key": "timer"}],
    }
    plan["digest"] = hashlib.sha256(
        json.dumps({"definition": definition, "plan": plan}, sort_keys=True).encode()
    ).hexdigest()
    pin = {
        "pin_id": "owner-pin",
        "kind": "install",
        "state": "activated",
        "digest": plan["digest"],
        "record": {
            "action": {
                "agent_definition_id": "source-id",
                "plan": plan,
                "snapshot_digest": plan["digest"],
            }
        },
        "progress": {
            "installed": True,
            "publication_kind": "system",
            "ui": "recipient-ui",
            "workflows": {"flow": "recipient-flow"},
            "automations": {"timer": "recipient-auto"},
        },
    }
    return pin, definition


def test_exact_legacy_pin_recovers_source_not_series_or_opt_in():
    pin, definition = legacy_fixture()
    provenance = legacy_provenance(pin=pin, definition=definition)
    assert provenance.definition_id == "source-id"
    assert dict(provenance.targets) == {
        "ui": "recipient-ui",
        "flow": "recipient-flow",
        "timer": "recipient-auto",
    }
    assert not hasattr(provenance, "publication_id") and not hasattr(provenance, "auto_update")


@pytest.mark.parametrize(
    "mutation",
    ["state", "digest", "plan", "definition", "author", "targets", "extra", "overlap", "receipt"],
)
def test_legacy_unknown_or_tampered_provenance_never_guessed(mutation):
    pin, definition = legacy_fixture()
    if mutation == "state":
        pin["state"] = "activating"
    elif mutation == "digest":
        pin["digest"] = digest("forged")
    elif mutation == "plan":
        pin["record"]["action"]["plan"]["ui"]["name"] = "same-name-new-source"
    elif mutation == "definition":
        definition["agent_definition_id"] = "different-same-name"
    elif mutation == "author":
        definition["author_id"] = "attacker"
    elif mutation == "targets":
        pin["progress"]["workflows"] = {}
    elif mutation == "extra":
        pin["progress"]["workflows"]["unknown"] = "extra-target"
    elif mutation == "overlap":
        pin["progress"]["workflows"]["flow"] = "recipient-ui"
    elif mutation == "receipt":
        pin["progress"]["installed"] = False
    with pytest.raises(ValueError):
        legacy_provenance(pin=pin, definition=definition)


@pytest.mark.parametrize("mutation", ["content", "target", "opt_out", "withdraw", "missing"])
def test_recheck_rejects_stale_consent_and_policy(mutation):
    args = fixture(automatic=True)
    plan = plan_update(**args)
    params = dict(
        plan=plan,
        adoption=args["adoption"],
        observations=args["observations"],
        public_release_ids=args["public_release_ids"],
    )
    recheck_plan(**params)
    if mutation == "opt_out":
        params["adoption"] = replace(params["adoption"], auto_update=False)
    elif mutation == "withdraw":
        params["public_release_ids"] = frozenset()
    elif mutation == "missing":
        params["observations"] = ()
    else:
        change = (
            {"current_digest": digest("new-edit")}
            if mutation == "content"
            else {"target_id": "other"}
        )
        params["observations"] = (replace(params["observations"][0], **change),)
    with pytest.raises(ValueError):
        recheck_plan(**params)


def test_recheck_fences_retained_dependencies_too():
    args = multi_component_fixture()
    first = args["releases"][0]
    workflow = first.components[1]
    screen = component(content="v2", dependencies=(("workflow", workflow.source_digest),))
    second = release([screen, workflow], first)
    args.update(
        releases=(first, second),
        selected=("screen",),
        public_release_ids=frozenset({second.release_id}),
    )
    plan = plan_update(**args)
    assert plan.ready and len(plan.changes) == 1
    observations = (
        args["observations"][0],
        replace(args["observations"][1], current_digest=digest("racing-dependency")),
    )
    with pytest.raises(ValueError, match="content changed"):
        recheck_plan(
            plan=plan,
            adoption=args["adoption"],
            observations=observations,
            public_release_ids=args["public_release_ids"],
        )


def test_blocked_plan_cannot_pass_recheck():
    args = fixture(new=component(content="v2", privileged=True))
    with pytest.raises(ValueError, match="recipient decisions"):
        recheck_plan(
            plan=plan_update(**args),
            adoption=args["adoption"],
            observations=args["observations"],
            public_release_ids=args["public_release_ids"],
        )


def test_real_component_install_pin_validates_without_enrolling_updates(tmp_path, monkeypatch):
    from tests.cloud_runtime_fixture import cloud_runtime
    from tests.test_command_center_packages import BOB, BOB_UNIVERSE, _answer, home
    from tests.test_command_center_system_copy import _legacy, _preview
    from tinyassets.command_center_packages import pin_for_request
    from tinyassets.custom_agents import get_definition

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    cloud_runtime.__wrapped__(monkeypatch)
    base = home.__wrapped__(tmp_path, monkeypatch)
    source_id = _legacy(base)
    ask = _preview(source_id)
    done = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert done.get("installed"), done
    pin = pin_for_request(base, universe_id=BOB_UNIVERSE, request_id=ask["request_id"])
    provenance = legacy_provenance(pin=pin, definition=get_definition(base, source_id))
    assert provenance.definition_id == source_id
    assert dict(provenance.targets)["ui"] == done["ui"]
    assert len(provenance.targets) == 5  # One UI, two workflows, two paused automations.
    assert (
        pin_for_request(base, universe_id="different-owner-home", request_id=ask["request_id"])
        is None
    )


def test_agent_template_pin_uses_coordinated_plan_and_progress_keys():
    pin, definition = legacy_fixture()
    action = pin["record"]["action"]
    plan = action["plan"]
    plan["agent_templates"] = [
        {
            "key": "guide",
            "kind": AGENT_KIND,
            "agent_definition_id": "public-guide",
            "name": "Guide",
            "content_fingerprint": digest("public-guide"),
        }
    ]
    unhashed = {key: value for key, value in plan.items() if key != "digest"}
    fingerprint = hashlib.sha256(
        json.dumps({"definition": definition, "plan": unhashed}, sort_keys=True).encode()
    ).hexdigest()
    pin["digest"] = plan["digest"] = action["snapshot_digest"] = fingerprint
    pin["progress"]["agents"] = {"guide": "recipient-private-guide"}
    assert (
        dict(legacy_provenance(pin=pin, definition=definition).targets)["guide"]
        == "recipient-private-guide"
    )
    pin["progress"]["agents"] = {}
    with pytest.raises(ValueError, match="target mapping is incomplete"):
        legacy_provenance(pin=pin, definition=definition)
