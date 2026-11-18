"""A freshness probe can claim its shared tests passed only when a test ran."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import queue_freshness as freshness  # noqa: E402


@pytest.mark.parametrize("cases", [
    "", '<testcase file="tests/test_probe.py" name="test_probe"><skipped/></testcase>',
])
def test_an_empty_or_skipped_report_proves_no_outcome(tmp_path, cases):
    report = tmp_path / "junit.xml"
    report.write_text(f"<testsuite>{cases}</testsuite>", encoding="utf-8")
    assert freshness._junit_outcomes(report) == (set(), set())


def test_missing_and_unreadable_reports_prove_no_outcome(tmp_path):
    report = tmp_path / "junit.xml"
    assert freshness._junit_outcomes(report) == (set(), set())
    report.write_text("<truncated")
    assert freshness._junit_outcomes(report) == (set(), set())


def test_a_report_with_a_passing_case_can_clear_the_merged_tree(tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text('<testsuite><testcase file="tests/test_probe.py" name="test_probe"/></testsuite>',
                      encoding="utf-8")
    assert freshness._junit_outcomes(report) == (set(), {"tests/test_probe.py::test_probe"})


def test_probe_runs_the_selected_gate_on_a_clean_merge(tmp_path, monkeypatch):
    """Two branches touch separate inputs to one test. Probe the refs as CI does."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True,
                              text=True).stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.name", "fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("remote", "add", "origin", str(tmp_path))
    for directory in ["scripts", "tests", ".github"]:
        (tmp_path / directory).mkdir()
    for script in ["ci_required_tests.py", "affected_tests.py"]:
        shutil.copy(Path(freshness.__file__).with_name(script), tmp_path / "scripts")
    for name in ["known-failing-tests.txt", "heavy-test-files.txt"]:
        (tmp_path / ".github" / name).touch()
    case = tmp_path / "tests/test_pair.py"
    # Leave separate diff hunks so both Git merges succeed.
    base_case = """from pathlib import Path
import pytest

LEFT = 0


def marker():
    return Path(__file__).with_name("first-failure")


# Git must merge both assignments without a textual conflict.
RIGHT = "base"


def test_pair():
    if RIGHT == "skip-merged" and marker().exists():
        pytest.skip("unavailable on merged confirmation")
    if RIGHT == "base" and marker().exists() and marker().read_text() == "skip-main":
        pytest.skip("unavailable on main")
    if LEFT and RIGHT in ("skip-merged", "skip-main"):
        marker().write_text(RIGHT)
    assert not LEFT or RIGHT in ("base", "compatible")
"""
    case.write_text(base_case)
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    case.write_text(base_case.replace("LEFT = 0", "LEFT = 1"))
    git("commit", "-qam", "main")
    main = git("rev-parse", "HEAD")
    monkeypatch.setattr(freshness, "REPO_ROOT", tmp_path)

    for right, expected in [("compatible", "fresh"), ("fail", "semantic-conflict"),
                            ("skip-merged", "unchecked"), ("skip-main", "unchecked")]:
        git("checkout", "-q", "--detach", base)
        case.write_text(base_case.replace('RIGHT = "base"', f'RIGHT = "{right}"'))
        git("commit", "-qam", "candidate")
        head = git("rev-parse", "HEAD")
        git("update-ref", "refs/pull/7/head", head)
        verdict = freshness.probe_one(7, head, main)
        assert verdict["verdict"] == expected, verdict
