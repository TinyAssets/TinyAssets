"""The gate that stops every branch appending to one shared file.

`.agents/activity.log` is append-only and every branch wrote to the same region,
so every landing conflicted. The record belongs in the commit message, which git
already keys per PR. The file itself stays and may still grow on `main`; what is
refused is a PR carrying a change to it.

Wired into pr-scope-guard, which runs the TRUSTED BASE copy of the script, so a
PR cannot edit the thing judging it.
"""

from __future__ import annotations

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
