"""Portable UI references are explicit data, bounded and independently editable."""

import pytest

from tests.test_command_center_packages import UI
from tinyassets.custom_agents import (
    AgentValidationError,
    _edited_entry,
    app_ui_renderability,
    app_ui_workflow_refs,
)


@pytest.mark.parametrize("refs", [None, [], {"bad.alias": "branch"}, {"_private": "branch"},
                                   {"scout": ""}, {"scout": " branch"}, {"scout": 1},
                                   {"scout": "x" * 201}, {"scout": "😀" * 101},
                                   {f"w{n}": "branch" for n in range(101)}])
def test_invalid_refs_are_refused_by_writer_and_renderability(refs):
    entry = {**UI, "workflow_refs": refs}
    with pytest.raises(AgentValidationError, match="workflow_refs"):
        app_ui_workflow_refs(entry)
    assert "workflow_refs" in app_ui_renderability(entry)["reason"]
    with pytest.raises(AgentValidationError, match="workflow_refs"):
        _edited_entry(UI, {"set": {"workflow_refs": refs}})


def test_refs_edit_and_read_are_copies_without_script_rewrite():
    refs = {"scout": "recipient-branch", "w" * 64: "😀" * 100}
    entry = _edited_entry(UI, {"set": {"workflow_refs": refs}})
    assert entry["script"] == UI["script"]
    assert app_ui_renderability(entry) == {}
    result = app_ui_workflow_refs(entry)
    result["scout"] = "other"
    assert entry["workflow_refs"] == refs
    assert "workflow_refs" not in UI
    assert app_ui_workflow_refs(UI) == {}
