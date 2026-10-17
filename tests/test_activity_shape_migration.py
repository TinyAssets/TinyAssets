"""Activities remain retired as executable workflow branches."""
import pytest

from tinyassets import runs
from tinyassets.automations import _failure_pause_reason
from tinyassets.branches import BranchDefinition


def test_retired_activities_branch_cannot_be_started_as_a_workflow(tmp_path):
    with pytest.raises(PermissionError, match="Activities workflow was retired"):
        runs._prepare_run(
            tmp_path, branch=BranchDefinition(branch_def_id="activities::one"),
            inputs={}, run_name="", actor="owner-one",
        )
    assert not (tmp_path / ".runs.db").exists()
    assert _failure_pause_reason("The Activities workflow was retired") == (
        "run_admission_refused")
