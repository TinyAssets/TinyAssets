"""Small synthetic proofs of shape resolution, retirement and coverage pruning."""

import ast
import datetime as dt
import subprocess

import pytest

from scripts import test_hygiene_gate as gate
from scripts import test_shape as shape


@pytest.mark.verifies(
    "test-suite-follows-shape:Every test verifies a current piece of the shape we are building"
)
def test_registry_distinguishes_live_retired_deleted_and_history():
    files = {
        "openspec/changes/box/design.md": """## Target shape (binding)
| T1 | Read no owner data; the old path is deleted. |
### C1. Current correction
Delete the old shared code.
### C2. Old correction
> **Superseded by C1.**
### C3. Superseded correction
Old path.
""",
        "openspec/specs/door/spec.md": "### Requirement: Authenticate\nLive.\n",
        "openspec/changes/archive/old/design.md": "### D1. Historical\n",
    }
    rows = shape.registry(files)
    assert rows == {
        "box:T1": "current",
        "box:C1": "current",
        "box:C2": "superseded",
        "box:C3": "superseded",
        "door:Authenticate": "current",
    }
    items = shape.tests(
        "tests/test_x.py",
        """
import pytest
@pytest.mark.verifies("box:C2", "box:C4", "typo:C1")
def test_old(): pass
def test_missing(): pass
""",
    )
    report = shape.audit(items, rows, {"box:C4": "current"})
    assert [f["state"] for f in report["unresolvable"]] == ["superseded", "deleted", "unknown"]
    assert report["untagged"] == ["tests/test_x.py::test_missing"]


@pytest.mark.verifies(
    "test-suite-follows-shape:Every test verifies a current piece of the shape we are building"
)
def test_marks_inherit_module_class_and_function_without_reading_test_bodies():
    items = shape.tests(
        "tests/test_x.py",
        """
import pytest
pytestmark = [pytest.mark.verifies("box:T1")]
@pytest.mark.verifies("box:C1")
class TestBox:
    @pytest.mark.verifies("door:Authenticate")
    def test_route(self):
        pytest.mark.verifies("not:a declaration")
""",
    )
    assert items[0].ids == {"box:T1", "box:C1", "door:Authenticate"}


def _commit(root, message):
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            message,
        ],
        check=True,
        capture_output=True,
    )
    return shape.git(root, "rev-parse", "HEAD").strip()


@pytest.mark.verifies(
    "test-suite-follows-shape:Every test verifies a current piece of the shape we are building"
)
def test_hygiene_checks_changed_tests_and_unchanged_references_to_retired_rows(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    spec = tmp_path / "openspec/changes/box/design.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("### C1. Route\nLive.\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_old.py").write_text(
        'import pytest\npytestmark = pytest.mark.verifies("box:C1")\ndef test_old(): pass\n',
        encoding="utf-8",
    )
    base = _commit(tmp_path, "base")
    spec.write_text("### C1. Route\n> **Superseded.**\n", encoding="utf-8")
    (tests / "test_new.py").write_text("def test_new(): pass\n", encoding="utf-8")
    head = _commit(tmp_path, "retire")
    delta = gate.compute_delta(base, head, tmp_path, dt.date(2026, 10, 21))
    failures = gate.evaluate(delta, "")
    assert any("test_new" in f and "no verifies" in f for f in failures)
    assert any("test_old" in f and "still named" in f for f in failures)
    (tests / "test_old.py").unlink()
    (tests / "test_new.py").unlink()
    head = _commit(tmp_path, "prune")
    assert not gate.compute_delta(base, head, tmp_path, dt.date(2026, 10, 21)).shape


@pytest.mark.verifies(
    "test-suite-follows-shape:A test already checked another way is pruned automatically"
)
def test_coverage_subsets_keep_guards_and_a_witness_for_every_row_and_parameter():
    def item(name, *ids):
        return shape.Test(f"tests/test_{name}.py::test_it", frozenset(ids), 1, 2)

    guard, duplicate, mixed, unknown = (
        item("guard", "box:T1"),
        item("copy", "box:T1"),
        item("mixed", "box:T1", "box:T2"),
        item("unmeasured", "box:T1"),
    )
    document = {
        "meta": {"show_contexts": True},
        "files": {
            "tinyassets/route.py": {
                "contexts": {
                    "1": [guard.node + "|run", duplicate.node + "[a]|run", mixed.node + "|run"],
                    "2": [guard.node + "|run", duplicate.node + "[b]|run"],
                    "3": [unknown.node + "|setup", ""],
                }
            }
        },
    }
    lines = shape.coverage_lines(document)
    assert lines[duplicate.node] == {("tinyassets/route.py", 1), ("tinyassets/route.py", 2)}
    assert shape.duplicates([guard, duplicate, mixed, unknown], lines, {"tests/test_guard.py"}) == {
        duplicate.node: guard.node
    }
    with pytest.raises(ValueError, match="show-contexts"):
        shape.coverage_lines({"meta": {}, "files": {}})


@pytest.mark.verifies(
    "test-suite-follows-shape:A test already checked another way is pruned automatically"
)
def test_prune_removes_whole_cases_preserves_helpers_and_leaves_valid_classes(tmp_path):
    path = tmp_path / "tests/test_x.py"
    path.parent.mkdir()
    text = """import pytest
def helper(): return 1
class TestFirst:
    @pytest.mark.parametrize("x", [1, 2])
    def test_old(self, x): assert x
class TestSecond:
    async def test_old(self): assert True
@pytest.mark.verifies("box:T1")
def test_kept(): assert helper()
"""
    path.write_text(text, encoding="utf-8")
    items = shape.tests("tests/test_x.py", text)
    shape.prune(tmp_path, items, {t.node for t in items if "test_old" in t.node})
    result = path.read_text(encoding="utf-8")
    ast.parse(result)
    assert "def helper" in result and "def test_kept" in result
    assert "test_old" not in result and result.count("pass") == 2


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
