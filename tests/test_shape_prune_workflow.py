"""The old suite's scheduled prune (`test-shape-prune.yml`): one draft PR, no deploy, no
auto-merge. It leaves with the workflow; the shape code's own cases are
`test_test_shape.py`'s."""

import pytest

from scripts import test_shape as shape


@pytest.mark.verifies(
    "test-suite-follows-shape:A test already checked another way is pruned automatically"
)
def test_scheduled_prune_is_one_draft_pr_with_no_deploy_or_auto_merge():
    import yaml

    workflow = yaml.safe_load((shape.ROOT / ".github/workflows/test-shape-prune.yml").read_text())
    assert "schedule" in workflow.get("on", workflow.get(True))
    steps = workflow["jobs"]["prune"]["steps"]
    create = next(
        s for s in steps if s.get("uses", "").startswith("peter-evans/create-pull-request@")
    )
    assert create["with"]["draft"] is True
    assert create["with"]["branch"] == "automation/test-shape-prune"
    assert "body-path" in create["with"]
    assert all("merge --auto" not in s.get("run", "") for s in steps)
    report = next(s for s in steps if s.get("name") == "Report subsets and delete orphaned tests")
    assert '"$COVERAGE_OUTCOME" = success' in report["run"]
    assert "git diff --name-only -z -- tests | xargs -0 -r" in report["run"]
