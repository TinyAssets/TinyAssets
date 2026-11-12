"""The scope guard's path rules: what a PR may not touch, and what counts as release-critical."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
import check_forbidden_pr_paths as forbidden  # noqa: E402
import enqueue_merge  # noqa: E402

_LOG = ".agents/activity.log"
_GUARD = _ROOT / ".github" / "workflows" / "pr-scope-guard.yml"


@pytest.mark.parametrize("change,refused", [
    (("removed", _LOG, ""), False),  # the cutover deletes it with the old stack
    (("modified", _LOG, ""), True),
    (("added", _LOG, ""), True),
    (("renamed", ".agents/old.log", _LOG), True),   # moved away, the appends go on
    (("renamed", _LOG, ".agents/other.log"), True),  # moved in
    (("removed", "tinyassets/runs.py", ""), False),
    (("modified", "./" + _LOG, ""), True),
])
def test_a_forbidden_path_may_be_deleted_and_never_edited_or_moved(change, refused) -> None:
    assert bool(forbidden.forbidden_hits([change])) is refused


def test_the_guard_hands_the_check_each_change_with_its_status() -> None:
    run = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "check_forbidden_pr_paths.py"), "--changes"],
        input=f"removed\t{_LOG}\t\nmodified\tREADME.md\t\n", capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    edit = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "check_forbidden_pr_paths.py"), "--changes"],
        input=f"modified\t{_LOG}\t\n", capture_output=True, text=True)
    assert edit.returncode == 1 and edit.stderr.startswith(f"{_LOG}: ")
    # Bare paths, as a guard older than `--changes` sends them, are each an edit.
    bare = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "check_forbidden_pr_paths.py")],
        input=f"{_LOG}\n", capture_output=True, text=True)
    assert bare.returncode == 1
    guard = _GUARD.read_text(encoding="utf-8")
    assert "[.status, .filename, (.previous_filename // \"\")] | @tsv" in guard
    assert 'check_forbidden_pr_paths.py --changes < "$FILES_TSV"' in guard


def test_platform_deploy_is_release_critical() -> None:
    pattern = enqueue_merge.load_scope_policy(_GUARD.read_text(encoding="utf-8"))
    for path in ("platform/deploy/deploy.ts", "platform/deploy/platform.json",
                 "deploy/deploy_fail_safe.sh", ".github/workflows/tests.yml"):
        assert pattern.search(path), path
    for path in ("platform/doors/src/doors.ts", "docs/platform/deploy/x.md"):
        assert not pattern.search(path), path


def test_breadth_is_the_reviewers_to_judge_not_a_cap() -> None:
    pattern = enqueue_merge.load_scope_policy(_GUARD.read_text(encoding="utf-8"))
    broad = [f".github/workflows/w{n}.yml" for n in range(40)]
    assert enqueue_merge.label_block(broad, {"infra-change"}, pattern=pattern) is None
    assert enqueue_merge.label_block(broad, set(), pattern=pattern) == (
        "missing infra-change label")
