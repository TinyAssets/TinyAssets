"""Synthetic package portability: real stores and the shipped UI controller.

The Node transport boundary records requests. The real event handler replays
those requests to create synthetic owner-scoped wakes, never provider runs.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_automations import _seed_branch
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
    _bob_files,
    _install,
    _pin_data_dir,  # noqa: F401
    _publish_action,
    home,  # noqa: F401
)
from tests.test_custom_ui_bridge import HARNESS
from tests.test_in_platform_agent_systems import _wakes
from tinyassets.api.automations import automations
from tinyassets.automations import STATE_PAUSED, AutomationStore, register_automation
from tinyassets.custom_agents import get_app_ui, get_definition, save_app_ui
from tinyassets.daemon_server import get_branch_definition

pytestmark = pytest.mark.usefixtures("cloud_runtime", "_pin_data_dir")

EVENT = "portable-scout-click"
SCRIPT = """
document.getElementById('portable-run').onclick = async () => {
  const workflow = (await tinyassets.whoami()).workflow_refs.scout;
  const rows = (await tinyassets.listAutomations()).automations;
  const target = rows.find(row => row.branch_id === workflow);
  if (!target) return {error: 'workflow_missing'};
  return tinyassets.emit({name: 'portable-scout-click', data: {from: 'button'}});
};
"""


def _click(tmp_path, component, actor, universe, server_document):
    """Execute actual listAutomations/emit controller methods, capture wire args."""
    node = shutil.which("node")
    assert node, "This acceptance investigation requires Node, not a skipped proof"
    controller = Path("tinyassets/onboarding/app_ui.js").read_text(encoding="utf-8")
    data = json.dumps({"component": component, "actor": actor, "home": universe,
                       "document": server_document})
    checks = """
(async()=>{
  const input = INPUT;
  AppUI.home=input.home; AppUI.principal=input.actor;
  const parsed=AppUI.parseBundle(input.component);
  assert.equal(parsed.ok, true);
  AppUI.active=parsed.bundle;
  const recorded=[];
  MCP.callTool=async(tool,args)=>{
    assert.equal(args.graph_id,input.home);
    if(tool==='read_graph' && args.target==='automations') return input.document;
    assert.equal(tool,'run_graph'); assert.equal(args.operation,'emit_event');
    recorded.push({tool,args});
    return {emitted:true,woke:0}; // transport recorder only; no execution claim
  };
  document.getElementById=$;
  const tinyassets={whoami:()=>AppUI.whoami(),listAutomations:()=>AppUI.listAutomations(),
    emit:args=>AppUI.emit(args)};
  eval(input.component.script);
  const result=await $('portable-run').onclick();
  console.log(JSON.stringify({result,recorded}));
})().catch(error=>{console.error(error);process.exit(1);});
""".replace("INPUT", data)
    script = tmp_path / ("portable-" + actor + ".js")
    script.write_text(HARNESS + controller + checks, encoding="utf-8")
    run = subprocess.run([node, str(script)], text=True, capture_output=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(run.stdout)


def _listed(actor, universe):
    with _as(actor):
        result = automations(action="list", universe_id=universe, limit=None)
    assert not result.get("error"), result
    return result


def _replay_click(actor, click):
    from tinyassets.universe_server import run_graph

    [call] = click["recorded"]
    assert call["tool"] == "run_graph"
    assert call["args"]["operation"] == "emit_event"
    with _as(actor):
        return json.loads(run_graph(**call["args"]))


def test_installed_button_resolves_recipient_workflow_before_emitting(home, tmp_path):  # noqa: F811
    """Declared metadata relocates; supported emit still uses recipient authority."""
    source = {**UI, "markup": '<button id="portable-run">Run scout</button>',
              "script": SCRIPT, "workflow_refs": {"scout": SCOUT}}
    alice_row = get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
    save_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE,
                expected_revision=alice_row["revision"], changes={"ui_library": [source]})
    existing = {**UI, "ui_id": "bobs-own", "name": "Bob's existing screen"}
    save_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE,
                expected_revision=0, changes={"ui_library": [existing]})
    before_files = _bob_files(home)
    with _as(OWNER):
        original = register_automation(
            home, universe_id=UNIVERSE, owner_principal_id=OWNER,
            name="scout click", branch_def_id=SCOUT, event_type="app_event",
            event_filter={"name": EVENT}, inputs={})
    action = _publish_action()
    action["publish_kind"] = "command_center"
    action["automation_ids"] = [original.automation_id]
    ask = _ask(OWNER, UNIVERSE, action)
    assert "request_id" in ask, ask
    published = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert published.get("published"), published
    definition = get_definition(home, published["agent_definition_id"])
    public_ui = next(component for component in definition["components"].values()
                     if component.get("kind") == "tinyassets.app-ui.v1")
    assert public_ui["workflow_refs"]["scout"].startswith("workflow-")
    assert public_ui["workflow_refs"]["scout"] in definition["components"]
    assert public_ui["script"] == source["script"]
    pending = _install(home, published["agent_definition_id"])
    assert "request_id" in pending, pending
    installed = _answer(BOB, BOB_UNIVERSE, pending["request_id"])
    assert installed.get("installed"), installed

    [copy] = AutomationStore(home).list(universe_id=BOB_UNIVERSE)
    assert copy.branch_def_id != SCOUT
    copied_branch = get_branch_definition(home, branch_def_id=copy.branch_def_id)
    assert copied_branch["author"] == BOB and copied_branch["visibility"] == "private"
    assert copied_branch["graph"]["nodes"], "The installed workflow retains executable graph nodes"
    assert copied_branch.get("default_llm_policy") is None
    from tinyassets.api.extensions import _extensions_impl
    from tinyassets.branches import BranchDefinition

    source_before_edit = get_branch_definition(home, branch_def_id=SCOUT)
    with _as(BOB):
        edit = _extensions_impl(action="patch_branch", branch_def_id=copy.branch_def_id,
                                changes_json=json.dumps([
                                    {"op": "set_name", "name": "Bob's independent scout"}]))
    edit = json.loads(edit) if isinstance(edit, str) else edit
    assert not edit.get("error"), edit
    renamed = get_branch_definition(home, branch_def_id=copy.branch_def_id)
    assert renamed["name"] == "Bob's independent scout"
    assert BranchDefinition.from_dict(renamed).graph_nodes
    assert get_branch_definition(home, branch_def_id=SCOUT) == source_before_edit
    assert copy.owner_principal_id == BOB and copy.desired_state == STATE_PAUSED
    assert copy.event_filter == {"name": EVENT}
    assert AutomationStore(home).get(original.automation_id) == original
    library = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"]
    assert existing in library
    [copied_ui] = [item for item in library if item["ui_id"] != existing["ui_id"]]
    assert copied_ui["script"] == source["script"], "Publisher code is copied verbatim"
    assert copied_ui["workflow_refs"] == {"scout": copy.branch_def_id}
    assert get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)["ui_library"] == [source]
    after_files = _bob_files(home)
    assert all(after_files[path] == content for path, content in before_files.items())

    source_click = _click(tmp_path, source, OWNER, UNIVERSE, _listed(OWNER, UNIVERSE))
    assert len(source_click["recorded"]) == 1, source_click
    assert source_click["recorded"][0]["args"]["graph_id"] == UNIVERSE
    copied_click = _click(tmp_path, copied_ui, BOB, BOB_UNIVERSE, _listed(BOB, BOB_UNIVERSE))
    assert len(copied_click["recorded"]) == 1, {
        "source_branch": SCOUT, "recipient_branch": copy.branch_def_id,
        "recipient_click": copied_click,
        "gap": "the copied screen cannot find its remapped workflow through listAutomations",
    }
    assert copied_click["recorded"][0]["args"]["graph_id"] == BOB_UNIVERSE
    assert _replay_click(BOB, copied_click)["woke"] == 0, "Installation never resumes work"
    assert _wakes(home, BOB_UNIVERSE) == []
    with _as(BOB):
        resumed = automations(action="resume", universe_id=BOB_UNIVERSE,
                              automation_id=copy.automation_id, expected_revision=copy.revision)
    assert not resumed.get("error"), resumed
    assert _replay_click(BOB, copied_click)["woke"] == 1
    [wake] = _wakes(home, BOB_UNIVERSE)
    assert wake.branch_def_id == copy.branch_def_id
    assert wake.owner_principal_id == BOB and wake.universe_id == BOB_UNIVERSE
    assert wake.inputs["event"]["data"] == {"from": "button"}
    assert _wakes(home, UNIVERSE) == []
    assert AutomationStore(home).get(original.automation_id) == original
    # A named event is broadcast within the owner's matching subscriptions.
    # One wake here follows from one matching fixture, not exclusive run authority.


@pytest.mark.parametrize("bad_reference", ["legacy_literal", "unselected", "other_owner"])
def test_explicit_package_refuses_unportable_ui_before_publish(home, bad_reference):  # noqa: F811
    source = {**UI, "script": SCRIPT, "workflow_refs": {"scout": SCOUT}}
    action = _publish_action()
    action["publish_kind"] = "command_center"
    if bad_reference == "legacy_literal":
        source.pop("workflow_refs")
        source["script"] = SCRIPT.replace(
            "(await tinyassets.whoami()).workflow_refs.scout", "'branch_scout'")
    elif bad_reference == "unselected":
        action["branch_ids"] = [SCRIBE]
    else:
        _seed_branch(home, branch_def_id="bobs-private-workflow", author=BOB, visibility="private")
        source["workflow_refs"] = {"scout": "bobs-private-workflow"}
    row = get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
    save_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE,
                expected_revision=row["revision"], changes={"ui_library": [source]})
    before = _bob_files(home)
    result = _ask(OWNER, UNIVERSE, action)
    assert "request_id" not in result, result
    assert result.get("error"), result
    assert "workflow" in json.dumps(result).lower(), result
    if bad_reference == "legacy_literal":
        assert "workflow_refs" in json.dumps(result), result
    assert get_branch_definition(home, branch_def_id=SCOUT)["visibility"] == "private"
    assert get_branch_definition(home, branch_def_id=SCRIBE)["visibility"] == "private"
    assert _bob_files(home) == before
    assert AutomationStore(home).list(universe_id=BOB_UNIVERSE) == []
