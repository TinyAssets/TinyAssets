"""The gate that stops every branch appending to one shared file.

`.agents/activity.log` is append-only and every branch wrote to the same region,
so every landing conflicted. The record belongs in the commit message, which git
already keys per PR. The file itself stays and may still grow on `main`; what is
refused is a PR carrying a change to it.

Wired into pr-scope-guard, which runs the TRUSTED BASE copy of the script, so a
PR cannot edit the thing judging it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "check_forbidden_pr_paths.py"

sys.path.insert(0, str(_REPO))
from scripts.check_forbidden_pr_paths import FORBIDDEN, forbidden_hits  # noqa: E402


def _run(paths: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input="\n".join(paths),
        capture_output=True,
        text=True,
    )


def test_the_activity_log_is_forbidden() -> None:
    assert ".agents/activity.log" in FORBIDDEN


def test_an_ordinary_diff_passes() -> None:
    result = _run(["tinyassets/runs.py", "tests/test_runs.py", "AGENTS.md"])
    assert result.returncode == 0, result.stderr


def test_the_activity_log_fails_with_what_to_do_instead() -> None:
    result = _run(["tinyassets/runs.py", ".agents/activity.log"])
    assert result.returncode == 1
    assert ".agents/activity.log" in result.stderr
    # The message has to say where the record goes, or the author just reverts
    # the file and loses the note they were trying to leave.
    assert "git log" in result.stderr and "commit" in result.stderr


def test_a_leading_dot_slash_still_matches() -> None:
    """`./.agents/activity.log` is the same file.

    Regression on a real bug in the first draft of this script: `lstrip("./")`
    strips a CHARACTER SET, so it ate the leading dot of `.agents` and the rule
    matched nothing at all.
    """
    assert forbidden_hits(["./.agents/activity.log"])
    assert forbidden_hits([".agents/activity.log"])


def test_a_windows_separator_still_matches() -> None:
    assert forbidden_hits([r".agents\activity.log"])


def test_a_neighbouring_path_is_not_caught() -> None:
    """Exact match, not a substring: the directory and siblings stay editable."""
    assert not forbidden_hits([
        ".agents/activity.log.bak",
        ".agents/worktrees.md",
        "docs/activity.log",
        "output/demo/activity.log",
    ])


def test_the_daemon_s_own_per_universe_log_is_untouched() -> None:
    """p0-outage-triage reads `<universe>/activity.log` in the DATA dir.

    A different file with the same basename. Forbidding it would break outage
    triage, so the rule is anchored to the repo-relative path.
    """
    assert not forbidden_hits(["/data/universe_alice/activity.log"])


def test_empty_and_blank_lines_are_not_hits() -> None:
    assert not forbidden_hits(["", "   ", "\n"])
    assert _run([]).returncode == 0


@pytest.mark.parametrize("rule", sorted(FORBIDDEN))
def test_every_rule_says_what_to_do_instead(rule: str) -> None:
    """A refusal with no destination just gets worked around."""
    assert len(FORBIDDEN[rule]) > 40, rule


def test_the_scope_guard_actually_calls_the_script() -> None:
    """Wiring, not just the helper: the rule is only real if the gate runs it."""
    workflow = (_REPO / ".github" / "workflows" / "pr-scope-guard.yml").read_text(
        encoding="utf-8",
    )
    assert "scripts/check_forbidden_pr_paths.py" in workflow
    # It must be on the PR path, not merge_group: a merge_group run of this
    # workflow passes unconditionally by design (it would grade itself).
    parsed = yaml.safe_load(workflow)
    triggers = parsed[True] if True in parsed else parsed["on"]
    assert "pull_request_target" in triggers


def test_the_guard_runs_before_the_receipt_check() -> None:
    """Order is the decision, not an accident.

    Fixing a forbidden path is a PUSH, and a push voids a head-pinned receipt.
    Reporting this first means the author fixes it and gets stamped once;
    reporting it after the receipt would have them get stamped and then void it.
    """
    workflow = (_REPO / ".github" / "workflows" / "pr-scope-guard.yml").read_text(
        encoding="utf-8",
    )
    assert workflow.index("check_forbidden_pr_paths.py") < workflow.index(
        "--blocking-review",
    )


def test_agents_md_points_the_narrative_at_the_git_log() -> None:
    """The rule and the doc have to agree, or the doc sends people to the file."""
    agents = (_REPO / "AGENTS.md").read_text(encoding="utf-8")
    narrative = [
        line for line in agents.splitlines()
        if line.startswith("| Narrative")
    ]
    assert len(narrative) == 1, narrative
    assert "activity.log" not in narrative[0], narrative[0]
    assert "git log" in narrative[0], narrative[0]


def test_the_guard_tolerates_a_base_that_predates_the_script() -> None:
    """Under pull_request_target the workflow and the CHECKOUT come from
    DIFFERENT commits, and this step has to survive that.

    The workflow is the trusted copy from the default branch; the checkout is
    the PR's own base sha. So for any PR based before this script landed, the
    step calls a file that is not in the tree. Without an `-f` guard python
    exits 2 ("can't open file"), the `if !` reads that as "a forbidden path was
    found", and the REQUIRED `Diff scope declared` check fails every such PR
    while naming the wrong cause. That happened: #4356 landed at 07:42Z on
    2026-10-03 and broke the gate for PRs based earlier, including #4359.

    The sibling step does the same thing for scripts/test_hygiene_gate.py, so
    this asserts the convention rather than inventing one.
    """
    workflow = (_REPO / ".github" / "workflows" / "pr-scope-guard.yml").read_text(
        encoding="utf-8",
    )
    parsed = yaml.safe_load(workflow)
    step = next(
        s for s in parsed["jobs"]["scope"]["steps"]
        if "check_forbidden_pr_paths" in (s.get("run") or "")
    )
    code = [
        line for line in step["run"].splitlines()
        if not line.strip().startswith("#")
    ]
    guard = next(
        i for i, line in enumerate(code)
        if "-f scripts/check_forbidden_pr_paths.py" in line
    )
    call = next(
        i for i, line in enumerate(code)
        if "python scripts/check_forbidden_pr_paths.py" in line
    )
    assert guard < call, "the existence check must precede the call"
    # ...and it must not be a bare `exit 0` that skips the REST of the step.
    assert "elif" in code[call - 1] or "elif" in code[call], code[call - 2:call + 1]


#: Scripts this job calls that must NOT be `-f` guarded, and why. The whole
#: question is which direction is safe when the base checkout lacks the script:
#:
#: * a RULE that did not exist at the base has nothing to say about this PR, so
#:   skipping is correct and failing is a false refusal (check_forbidden_pr_paths);
#: * a GATE that verifies something must refuse when it cannot verify, so a
#:   missing drain_review_gate.py has to fail CLOSED. Guarding it would skip the
#:   receipt requirement -- an unreviewed head merging, which is the worst
#:   outcome available here.
_MUST_FAIL_CLOSED = {
    "scripts/drain_review_gate.py": "a receipt gate that cannot run must refuse, not skip",
}


def test_each_script_the_guard_calls_has_a_stated_base_predates_policy() -> None:
    """Every script called from the BASE checkout is either guarded or named here.

    A new script added to this job without thinking about it is the defect that
    broke the required gate on 2026-10-03; this makes the next one a test
    failure with the question in front of the author.
    """
    workflow = yaml.safe_load(
        (_REPO / ".github" / "workflows" / "pr-scope-guard.yml").read_text(encoding="utf-8")
    )
    undecided = []
    for step in workflow["jobs"]["scope"]["steps"]:
        run = step.get("run") or ""
        code = "\n".join(
            line for line in run.splitlines() if not line.strip().startswith("#")
        )
        for script in set(re.findall(r"python (scripts/[\w./-]+\.py)", code)):
            if f"-f {script}" in code or script in _MUST_FAIL_CLOSED:
                continue
            undecided.append(f"{step.get('name')}: {script}")
    assert not undecided, (
        "these run from the PR's BASE checkout with no existence check and no "
        "stated fail-closed reason, so a PR based before the script landed fails "
        f"the required gate naming the wrong cause: {undecided}"
    )


def test_the_fail_closed_scripts_are_really_unguarded() -> None:
    """The other direction: nobody may quietly add an `-f` to a gate that must refuse."""
    code = "\n".join(
        line
        for step in yaml.safe_load(
            (_REPO / ".github" / "workflows" / "pr-scope-guard.yml").read_text(
                encoding="utf-8",
            )
        )["jobs"]["scope"]["steps"]
        for line in (step.get("run") or "").splitlines()
        if not line.strip().startswith("#")
    )
    for script, why in _MUST_FAIL_CLOSED.items():
        assert f"-f {script}" not in code, f"{script} must fail closed: {why}"
