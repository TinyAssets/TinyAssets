"""Shape and hygiene gates follow tests explicitly moved beside the CI scripts."""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import ci_structural_guards, test_hygiene_gate, test_inventory, test_shape

pytestmark = pytest.mark.verifies(
    "test-suite-follows-shape:Every test verifies a current piece of the shape we are building"
)


def test_shape_and_inventory_follow_only_the_opted_in_process_tests(tmp_path: Path) -> None:
    moved = "scripts/tests/ci/test_queue.py"
    other = "scripts/tests/test_process.py"
    for rel in [moved, other]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_result():\n    assert True\n")

    assert "scripts.tests.ci.test_queue" not in test_inventory.build_module_index(tmp_path)
    sources = test_shape.sources(tmp_path)
    assert list(sources) == [moved]
    assert [test.node for test in test_shape.inventory(sources)] == [moved + "::test_result"]
    assert list(test_inventory.iter_test_files(tmp_path)) == [tmp_path / moved]
    assert test_inventory.area_of(moved, []) == "ci"
    assert test_hygiene_gate.collectable(moved)
    assert not test_hygiene_gate.collectable(other)
    context = [moved + "::test_result|run"]
    measured = {
        "meta": {"show_contexts": True},
        "files": {
            moved: {"contexts": {"2": context}},
            "scripts/queue.py": {"contexts": {"1": context}},
        },
    }
    assert test_shape.coverage_lines(measured) == {
        moved + "::test_result": {("scripts/queue.py", 1)}
    }


def test_weekly_process_evidence_belongs_only_to_relocated_ci_tests(tmp_path: Path) -> None:
    shards = tmp_path / "required"
    shards.mkdir()
    (shards / "shard.xml").write_text(
        '<testsuite><testcase file="tests/test_old.py" classname="tests.test_old" name="test_a">'
        '<skipped message="old shard skip"/></testcase></testsuite>'
    )
    process = tmp_path / "junit-process.xml"
    process.write_text(
        '<testsuite>'
        '<testcase file="tests/test_old.py" classname="tests.test_old" name="test_a" time="7"/>'
        '<testcase file="scripts/tests/test_other.py" classname="scripts.tests.test_other" name="test_b">'
        '<failure message="unrelated process failure"/></testcase>'
        '<testcase file="scripts/tests/ci/test_queue.py" classname="scripts.tests.ci.test_queue" '
        'name="test_c" time="0.25"/></testsuite>'
    )
    window = test_inventory.read_junit([shards], process)
    assert window.skipped == {"tests/test_old.py::test_a": "old shard skip"}
    assert window.ran == {"scripts/tests/ci/test_queue.py::test_c"}
    assert window.durations == {"scripts/tests/ci/test_queue.py::test_c": 0.25}
    assert not window.failing


def test_a_relocated_structural_guard_keeps_its_required_target(tmp_path: Path) -> None:
    old = "tests/test_rulebook_ratchet.py"
    moved = "scripts/tests/ci/test_rulebook_ratchet.py"
    assert old in ci_structural_guards.selected_targets([moved], tmp_path)
    (tmp_path / moved).parent.mkdir(parents=True)
    (tmp_path / moved).touch()
    targets = ci_structural_guards.targets(tmp_path)
    assert moved in targets
    assert old not in targets
    (tmp_path / old).parent.mkdir()
    (tmp_path / old).touch()
    assert old in ci_structural_guards.targets(tmp_path)

def test_pr_selection_loads_the_structural_targets_in_a_clean_process() -> None:
    root = Path(__file__).resolve().parents[3]
    subprocess.run(
        [sys.executable, "-c",
         "from pathlib import Path; from scripts.affected_tests import pr_tier; "
         "assert pr_tier([], Path.cwd()) == []"],
        cwd=root, check=True,
    )
