"""Guards for `.github/workflows/auto-enroll-merge.yml`.

This workflow enrolls every PR for auto-merge, so breaking it stops the whole
fleet from landing anything. It also cannot be exercised locally — it runs on
`pull_request_target`, from the base branch, with repository secrets. These
tests are therefore the only pre-merge check on its shape.

The specific thing being pinned is the merge-attribution token. A merge
attributed to the default `GITHUB_TOKEN` raises no `push` on main, so
`build-image` never fires and nothing deploys (hard rule 14), and with the
merge queue on, a PR it arms never enqueues. Enrollment therefore runs on a
user credential, refuses to run without one, and checks who it enrolled as.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "auto-enroll-merge.yml"
)

#: Must match the name the host sets. Changing one without the other silently
#: reverts to the default token and re-opens the deploy gap with no failure.
_SECRET = "MERGE_ATTRIBUTION_TOKEN"


@pytest.fixture(scope="module")
def source() -> str:
    return _WORKFLOW.read_text("utf-8")




def _steps(source: str) -> list[dict]:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(source)["jobs"]["enroll"]["steps"]


@pytest.mark.parametrize(
    "change,armed,read_fails,disabled,exit_code",
    [
        ({}, True, False, True, 0),
        ({"headRefOid": "b" * 40}, True, False, False, 0),
        ({"baseRefOid": "c" * 40}, True, False, False, 0),
        ({"body": "new valid review receipt"}, True, False, False, 0),
        ({"autoMergeRequest": {"enabledAt": "new enrollment"}}, True, False, False, 0),
        ({}, True, True, False, 1),
        ({}, False, False, False, 0),
    ],
)
def test_stale_receipt_denial_cannot_disarm_newer_state(
    source, tmp_path, change, armed, read_fails, disabled, exit_code
):
    """Execute the real deny branch, with all GitHub I/O replaced offline."""
    bash = shutil.which("bash")
    if os.name == "nt" and shutil.which("git"):
        # Prefer Git Bash to the unrelated Windows WSL launcher on PATH.
        git_root = Path(shutil.which("git")).resolve().parents[1]
        bash = shutil.which("bash", path=str(git_root / "bin"))
    assert bash, "workflow regression requires bash (provided by CI or Git for Windows)"
    run = next(s for s in _steps(source) if s.get("name") == "Enable auto-merge")["run"]
    deny = run[run.index('if [ "$REVIEW_GATE" = "deny" ]; then'):run.index("# Queue-attempt cap:")]
    initial = {
        "headRefOid": "a" * 40,
        "baseRefOid": "d" * 40,
        "body": "invalid receipt",
        "autoMergeRequest": {"enabledAt": "original enrollment"} if armed else None,
    }
    script = """set -euo pipefail
gh() {
  if [ "$1 $2" = "pr view" ]; then
    if [ "$READ_FAILS" = "yes" ]; then return 1; fi
    printf '%s' "$LATEST_PR_JSON"
  elif [ "$1 $2" = "pr merge" ]; then
    printf 'MUTATION %s\\n' "$*"
  else
    echo "unexpected command" >&2; return 2
  fi
}
""" + deny
    result = subprocess.run(
        [bash, "--noprofile", "--norc"],
        input=script.encode("utf-8"),
        capture_output=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "STATE": "yes" if armed else "no",
            "REVIEW_GATE": "deny",
            "PR": "123",
            "REPO": "example/repo",
            "PR_JSON": json.dumps(initial),
            "LATEST_PR_JSON": json.dumps(initial | change),
            "READ_FAILS": "yes" if read_fails else "no",
            "GITHUB_STEP_SUMMARY": (tmp_path / "summary").as_posix(),
        },
        timeout=10,
    )
    assert result.returncode == exit_code, result.stderr
    mutation = b"MUTATION pr merge 123 --repo example/repo --disable-auto"
    assert (mutation in result.stdout) == disabled


def test_enrollment_uses_the_attribution_token_with_no_fallback(source):
    """Enrollment must run as a user, never as the default token.

    The `|| github.token` fallback this replaced hid an empty secret on
    2026-09-27. Every PR was armed by github-actions, and with the merge queue
    on, a bot-armed auto-merge never enqueues.
    """
    gh_tokens = [
        ln.strip() for ln in source.splitlines() if ln.strip().startswith("GH_TOKEN:")
    ]
    assert gh_tokens == ["GH_TOKEN: ${{ secrets.%s }}" % _SECRET], gh_tokens


def test_an_empty_secret_fails_the_run(source):
    """An unset secret reads as the empty string. It must stop the run with
    an error, before anything is enrolled."""
    steps = _steps(source)
    names = [s.get("name") for s in steps]
    guard = names.index("Require the merge-attribution token")
    assert guard < names.index("Enable auto-merge")
    step = steps[guard]
    assert step["env"]["TOKEN_PRESENT"] == "${{ secrets.%s != '' }}" % _SECRET
    assert 'if [ "$TOKEN_PRESENT" != "true" ]' in step["run"]
    assert "exit 1" in step["run"]


def test_enrollment_is_checked_to_be_a_user(source):
    """A bot enroller is an error right after enrolling. A later event that
    finds a bot enrollment replaces it instead of keeping it."""
    run = next(s for s in _steps(source) if s.get("name") == "Enable auto-merge")["run"]
    # The queue entry's enqueuer counts: autoMergeRequest reads null once queued.
    assert "mergeQueueEntry{enqueuer{__typename login}}" in run
    # The second `STATE = yes` branch; the first is the review-gate deny path.
    already = run.index('if [ "$STATE" = "yes" ]; then', run.index("Idempotent"))
    block = run[already:run.index("fi\n", run.index("--disable-auto", already)) + 3]
    assert "who_enrolled" in block
    assert '== User:* ]]' in block and "exit 0" in block
    assert "--disable-auto" in block
    after = run[run.index('enrolled for auto-merge (squash)."'):]
    assert '!= User:* ]]' in after and "exit 1" in after


def test_default_token_is_not_used_unconditionally(source):
    """A bare `GH_TOKEN: ${{ github.token }}` anywhere would reintroduce the
    gap even with the new expression present elsewhere."""
    bare = [
        ln.strip() for ln in source.splitlines()
        if ln.strip() == "GH_TOKEN: ${{ github.token }}"
    ]
    assert not bare, bare


def test_still_runs_on_pull_request_target(source):
    """`pull_request_target` is what gives this workflow the trusted base
    checkout AND access to repository secrets. On plain `pull_request` the
    secret would be unavailable for fork PRs and the fallback would silently
    take over — reopening the gap without any failure."""
    assert "pull_request_target:" in source
