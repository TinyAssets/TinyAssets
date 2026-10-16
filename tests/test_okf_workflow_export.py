"""Portable workflow data crosses the real protected transfer door without grants."""
from __future__ import annotations

import copy
import json
from dataclasses import asdict

import pytest

from tests.test_okf_transfer import box  # noqa: F401
from tests.test_owner_transfer import (  # noqa: F401
    HOME_A,
    PATH,
    URL,
    A,
    B,
    door,
    download,
    request,
    transfer_door,
)
from tinyassets.okf.bundle import InvalidBundle
from tinyassets.okf.transfer import export_zip, ingest_zip, preview
from tinyassets.okf.workflow_export import PREFIX, SCHEDULES, serialize


def branch(owner="owner-a", identity="a" * 32):
    return {"branch_def_id": identity, "author": owner, "name": "Summarize notes",
            "description": "Keep the result", "entry_point": "step-source",
            "graph": {"nodes": [{"id": "step-source", "node_def_id": "b" * 32}],
                      "edges": [], "conditional_edges": []},
            "node_defs": [{"node_id": "b" * 32, "display_name": "Summary",
                           "prompt_template": "Summarize {notes}", "input_keys": ["notes"],
                           "output_keys": ["summary"], "model_hint": "private-source-model",
                           "llm_policy": {"preferred": {"provider": "private-source"}}}],
            "state_schema": [], "visibility": "private"}


def automation(owner="owner-a", center="center-a", **changes):
    from tinyassets.automations import Automation

    return asdict(Automation(
        automation_id="c" * 32, universe_id=center, owner_principal_id=owner,
        name="Morning notes", branch_def_id="a" * 32, trigger_kind="cron",
        interval_seconds=0, cron_expr="0 9 * * *", inputs={"notes": "Project notes"},
        desired_state="active", pause_reason="", revision=1,
        created_at="2026-10-14T00:00:00+00:00", updated_at="2026-10-14T00:00:00+00:00",
        retired_at="", last_due_at="", last_run_id="private-run", last_reason="",
        last_finished_at="", timezone="Europe/London",
    )) | changes


def test_structured_capture_preserves_graph_and_trigger_without_source_authority(box):  # noqa: F811
    provider, handle = box
    raw = branch(handle.account_id)
    schedule = automation(handle.account_id, handle.command_center_id)
    source = copy.deepcopy(raw)
    captured = serialize([raw], [schedule], owner=handle.account_id,
                         center=handle.command_center_id)
    paths = list(captured.files)
    view = preview(provider, handle, paths, structured=captured.files)
    assert not view.blocked
    bundle = ingest_zip(export_zip(provider, handle, view, selected=paths))
    text = b"".join(bundle.files.values()).decode()
    assert "a" * 32 not in text and "b" * 32 not in text and "private-source" not in text
    assert "private-run" not in text and handle.account_id not in text
    document = json.loads(bundle.files[PREFIX + "workflow-1.json"])
    assert document["definition"]["node_defs"][0]["prompt_template"] == "Summarize {notes}"
    assert document["definition"]["graph"]["nodes"] == [
        {"id": "step-1", "node_def_id": "node-1"}]
    row = json.loads(bundle.files[SCHEDULES])["automations"][0]
    assert row["trigger"] == {"kind": "cron", "expression": "0 9 * * *",
                               "timezone": "Europe/London"}
    assert row["enabled"] is False and row["inputs"] == {"notes": "Project notes"}
    assert json.loads(bundle.files["command-center.json"])["workflows"] == [
        {"id": "workflow-1", "path": PREFIX + "workflow-1.json"}]
    assert raw == source


def test_effect_credentials_become_references_but_free_text_secrets_stay_blocked(box):  # noqa: F811
    raw = branch(box[1].account_id)
    node = raw["node_defs"][0]
    node["effect_spec"] = {"connection": "private-account-link", "api_key": "secret" * 8}
    captured = serialize([raw], [], owner=box[1].account_id, center=box[1].command_center_id)
    assert b"private-account-link" not in b"".join(captured.files.values())
    assert (b"secret" * 8) not in b"".join(captured.files.values())
    assert "bindings.json" in " ".join(captured.files)
    node["prompt_template"] = "Read 7bc0b4cfac4f663169f21d7437e31432"
    captured = serialize([raw], [], owner=box[1].account_id, center=box[1].command_center_id)
    view = preview(*box, list(captured.files), structured=captured.files)
    assert view.blocked[PREFIX + "workflow-1.json"] == "detected credential"
    with pytest.raises(InvalidBundle, match="blocked"):
        export_zip(*box, view, selected=[PREFIX + "workflow-1.json"])


def test_schedules_require_selected_workflows_and_foreign_sources_refuse(box):  # noqa: F811
    owner, center = box[1].account_id, box[1].command_center_id
    captured = serialize([branch(owner)], [automation(owner, center)], owner=owner, center=center)
    view = preview(*box, list(captured.files), structured=captured.files)
    with pytest.raises(InvalidBundle, match="selected workflows"):
        export_zip(*box, view, selected=[SCHEDULES])
    for branches, schedules in (([branch("foreign")], []),
                                ([branch(owner)], [automation("foreign", center)]),
                                ([branch(owner)], [automation(owner, "foreign")])):
        with pytest.raises(InvalidBundle, match="another owner"):
            serialize(branches, schedules, owner=owner, center=center)


def test_owner_route_captures_real_rows_rechecks_changes_and_excludes_other_owners(
        transfer_door):  # noqa: F811
    from tinyassets.api.helpers import _base_path
    from tinyassets.automations import Automation, AutomationStore
    from tinyassets.daemon_server import save_branch_definition, update_branch_definition

    client, _, _ = transfer_door
    raw = branch(A)
    raw["graph_nodes"] = raw["graph"]["nodes"]
    save_branch_definition(_base_path(), branch_def=raw)
    other = branch(B, "d" * 32)
    other["name"] = "Other owner's private workflow"
    other["graph_nodes"] = other["graph"]["nodes"]
    save_branch_definition(_base_path(), branch_def=other)
    AutomationStore(_base_path()).insert(Automation(**automation(A, HOME_A)))
    listing = request(client, {"operation": "list", "command_center": HOME_A})
    assert listing.status_code == 200, listing.text
    paths = [row["path"] for row in listing.json()["files"] if row.get("structured")]
    assert paths == [SCHEDULES, PREFIX + "workflow-1.json"]
    response = request(client, {"operation": "preview", "command_center": HOME_A, "paths": paths})
    assert response.status_code == 200, response.text
    assert "Other owner's" not in response.text
    token = response.json()["preview"]
    result = download(client, token, selected=paths)
    assert result.status_code == 200, result.text
    assert SCHEDULES in ingest_zip(result.content).files
    update_branch_definition(_base_path(), branch_def_id="a" * 32,
                             updates={"description": "Changed after preview"})
    assert download(client, token, selected=paths).status_code == 409


def test_canonical_implicit_node_reference_survives_export():
    raw = branch()
    raw['graph']['nodes'] = [{'id': 'step-source'}]
    raw['node_defs'][0]['node_id'] = 'step-source'
    from tinyassets.branches import BranchDefinition

    assert BranchDefinition.from_dict(raw).validate() == []
    result = serialize([raw], [], owner='owner-a', center='center-a')
    definition = json.loads(result.files[PREFIX + 'workflow-1.json'])['definition']
    assert definition['graph']['nodes'] == [{'id': 'step-1', 'node_def_id': 'node-1'}]
