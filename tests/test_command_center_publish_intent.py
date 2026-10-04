"""Truthful publication intent and previews, through two synthetic owners."""
from pathlib import Path

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_command_center_packages import (
    BOB,
    BOB_UNIVERSE,
    OWNER,
    SCOUT,
    UNIVERSE,
    _answer,
    _as,
    _ask,
    _blob_files,
    _publish_action,
    home,  # noqa: F401
)
from tinyassets.api.publish_requests import validate_action

pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture(autouse=True)
def _pin_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


@pytest.mark.parametrize("nodes", [1, 3])
def test_publish_preview_counts_the_stored_graph_nodes(home: Path, nodes: int):  # noqa: F811
    from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
    from tinyassets.daemon_server import get_branch_definition, save_branch_definition

    branch = BranchDefinition.from_dict(get_branch_definition(home, branch_def_id=SCOUT))
    branch.graph_nodes = [GraphNodeRef(id=f"step{n}", node_def_id=f"step{n}")
                          for n in range(nodes)]
    branch.node_defs = [NodeDefinition(node_id=f"step{n}", display_name=f"Step {n}",
                                       prompt_template="Inspect the synthetic board.")
                        for n in range(nodes)]
    branch.edges = [EdgeDefinition(from_node=f"step{n}",
                                   to_node=f"step{n + 1}" if n + 1 < nodes else "END")
                    for n in range(nodes)]
    branch.entry_point = "step0"
    save_branch_definition(home, branch_def=branch.to_dict())
    raw = get_branch_definition(home, branch_def_id=SCOUT)
    assert len(raw["graph"]["nodes"]) == nodes
    action = {**_publish_action(), "publish_kind": "command_center", "branch_ids": [SCOUT]}
    ask = _ask(OWNER, UNIVERSE, action)
    assert "request_id" in ask, ask
    assert f"({nodes} steps)" in ask["body"]
    assert "(0 steps)" not in ask["body"]


@pytest.mark.parametrize("changes", [
    {"publish_kind": "command_center", "package": None},
    {"publish_kind": "command_center", "ui_id": ""},
    {"publish_kind": "workflows"},
    {"publish_kind": "unknown"},
])
def test_explicit_intent_cannot_silently_choose_another_publication(changes):
    with pytest.raises(ValueError):
        validate_action({**_publish_action(), **changes})


def test_full_center_preview_and_receipt_name_the_actual_catalogue(home: Path):  # noqa: F811
    from tinyassets.api.package_requests import list_packages

    action = {**_publish_action(), "publish_kind": "command_center", "branch_ids": [SCOUT]}
    ask = _ask(OWNER, UNIVERSE, action)
    assert "command center" in ask["title"].lower()
    assert "Publishing does not share your conversations, files" not in ask["body"]
    assert "Every file listed above becomes public" in ask["body"]
    assert not list_packages()
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert done["published"] is True
    assert done["publication_kind"] == "command_center"
    assert done["catalogue"] == "packages"
    with _as(BOB):
        listed = list_packages(author=OWNER)
    assert [x["agent_definition_id"] for x in listed] == [done["agent_definition_id"]]
    files = _blob_files(home, done["agent_definition_id"])
    assert "notes/board.md" in files
    assert "founder.md" not in files and "MEMORY.md" not in files
    assert not any(p.startswith(("canon/", "uploads/", ".")) for p in files)
    assert not (home / BOB_UNIVERSE / "notes" / "board.md").exists()


def test_legacy_screen_bundle_is_not_upgraded_to_a_file_package(home: Path):  # noqa: F811
    from tinyassets.api.package_requests import list_packages
    from tinyassets.custom_agents import get_definition

    action = _publish_action()
    del action["package"]
    ask = _ask(OWNER, UNIVERSE, action)
    assert "No files are included" in ask["body"]
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert done["published"] is True and done["publication_kind"] == "system"
    assert done["catalogue"] == "agents"
    assert not list_packages()
    definition = get_definition(home, done["agent_definition_id"])
    assert "ui" in definition["components"] and "workflow-1" in definition["components"]
    assert "package" not in definition["components"]


def test_workflow_only_intent_remains_explicitly_file_and_screen_free(home: Path):  # noqa: F811
    action = {"type": "publish", "publish_kind": "workflows", "name": "One workflow",
              "branch_ids": [SCOUT]}
    ask = _ask(OWNER, UNIVERSE, action)
    assert "workflows" in ask["title"].lower()
    assert "No files are included" in ask["body"]
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert done["published"] is True and done["publication_kind"] == "workflows"
    assert done["catalogue"] == "agents"
