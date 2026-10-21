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
**Deleted:** the old implementation files.
### C2. Old correction
> **Superseded by C1.**
### C3. Superseded correction
Old path.
### C5. Mixed old row
Introductory prose.
> **Superseded by C1.**
Old path.
### C5. Repeated row heading
Still historical.
## Superseded layout
### C4. Former route
Historical.
| T2 | Old table row |
""",
        "openspec/specs/door/spec.md": (
            "### Requirement: Authenticate\nLive.\n"
            "### Requirement: The deleted row set is derived\nLive deletion behavior.\n"
        ),
        "openspec/changes/archive/old/design.md": "### D1. Historical\n",
    }
    rows = shape.registry(files)
    assert rows == {
        "box:T1": "current",
        "box:C1": "current",
        "box:C2": "superseded",
        "box:C3": "superseded",
        "box:C5": "superseded",
        "box:C4": "superseded",
        "box:T2": "superseded",
        "door:Authenticate": "current",
        "door:The deleted row set is derived": "current",
    }
    items = shape.tests(
        "tests/test_x.py",
        """
import pytest
@pytest.mark.verifies("box:C2", "box:C6", "typo:C1")
def test_old(): pass
def test_missing(): pass
""",
    )
    report = shape.audit(items, rows, {"box:C6": "current"})
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
    overwritten = shape.tests(
        "tests/test_x.py",
        """
import pytest
pytestmark = pytest.mark.verifies("box:T1")
pytestmark = []
@pytest.mark.parametrize("x", [pytest.param(1, marks=pytest.mark.verifies("box:T1")), 2])
def test_partial(x): pass
""",
    )
    assert not overwritten[0].ids


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
    (tests / "test_bad.py").write_text(
        'import pytest\n@pytest.mark.verifies("box:typo")\ndef test_bad(): pass\n',
        encoding="utf-8",
    )
    head = _commit(tmp_path, "retire")
    delta = gate.compute_delta(base, head, tmp_path, dt.date(2026, 10, 21))
    failures = gate.evaluate(delta, "")
    assert any("test_new" in f and "no verifies" in f for f in failures)
    assert any("test_old" in f and "still named" in f for f in failures)
    assert any("test_bad" in f and "unresolvable" in f for f in failures)
    (tests / "test_old.py").unlink()
    (tests / "test_new.py").unlink()
    (tests / "test_bad.py").unlink()
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
    report = next(s for s in steps if s.get("name") == "Report subsets and delete orphaned tests")
    assert '"$COVERAGE_OUTCOME" = success' in report["run"]
    assert "git diff --name-only -z -- tests | xargs -0 -r" in report["run"]


@pytest.mark.verifies(
    "test-suite-follows-shape:A test already checked another way is pruned automatically"
)
def test_prune_command_writes_reviewable_removal_reasons_and_kept_witnesses(tmp_path):
    import json

    spec = tmp_path / "openspec/changes/box/design.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("### C1. Route\nLive.\n", encoding="utf-8")
    path = tmp_path / "tests/test_x.py"
    path.parent.mkdir()
    path.write_text(
        """import pytest
@pytest.mark.verifies("box:C1")
def test_a(): assert True
@pytest.mark.verifies("box:C1")
def test_b(): assert True
@pytest.mark.verifies("box:retired")
def test_orphan(): assert True
""",
        encoding="utf-8",
    )
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps(
            {
                "meta": {"show_contexts": True},
                "files": {
                    "tinyassets/route.py": {
                        "contexts": {
                            "1": ["tests/test_x.py::test_a|run", "tests/test_x.py::test_b|run"]
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    body = tmp_path / "body.md"
    report = tmp_path / "report.json"
    assert (
        shape.main(
            [
                "--root",
                str(tmp_path),
                "--coverage",
                str(coverage),
                "--prune",
                "--body",
                str(body),
                "--json",
                str(report),
            ]
        )
        == 0
    )
    assert [t.node for t in shape.tests("tests/test_x.py", path.read_text())] == [
        "tests/test_x.py::test_a"
    ]
    assert "Test-Removal: retired" in body.read_text()
    assert "Test-Removal: consolidated" in body.read_text()
    assert "Registry row(s): S8" in body.read_text()
    assert json.loads(report.read_text())["duplicates"] == {
        "tests/test_x.py::test_b": "tests/test_x.py::test_a"
    }
