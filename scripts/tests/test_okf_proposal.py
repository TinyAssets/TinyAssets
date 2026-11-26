"""Run the actual proposal step offline at each boundary where a retry can start."""
import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml


REPO = Path(__file__).resolve().parents[2]
BRANCH = "okf-watch/example"
WORKFLOW = REPO / ".github/workflows/okf-watch.yml"
MOCK = r"""
git() {
  case "$1" in
    ls-remote) [[ "$REF" == absent ]] || echo "abc refs/heads/$BRANCH" ;;
    rev-parse) if [[ "$2" == 'HEAD^{tree}' ]]; then echo tree; else echo parent; fi ;;
    push) echo push >> "$LOG"; [[ "$*" == *"--force-with-lease=refs/heads/$BRANCH:"* ]] ;;
    *) return 92 ;;
  esac
}
gh() {
  case "$1 $2" in
    "api user") echo watcher ;;
    "api repos/example/repo/branches/"*) [[ "$REF" != absent ]] ;;
    "api repos/example/repo/git/commits/abc") echo "$REF" ;;
    "pr list")
      [[ "$FAIL" != lookup ]] || return 71
      if [[ "$*" == *"--head "* ]]; then
        jq -r "${!#}" <<< "$HISTORY"
      else
        jq -r "${!#}" <<< "$OPEN"
      fi ;;
    "label create") return 0 ;;
    "pr create") echo create >> "$LOG"; [[ "$FAIL" != create ]] || return 72
      echo "https://example.invalid/pull/12" ;;
    "pr close") echo "close $3" >> "$LOG" ;;
    *) return 93 ;;
  esac
}
"""


def proposal(number, state="OPEN", author="watcher", fork=False):
    return {"number": number, "state": state, "author": {"login": author},
            "headRefName": BRANCH, "isCrossRepository": fork}


@pytest.mark.parametrize("ref,history,fail,rc,actions", [
    ("absent", [], "", 0, ["push", "create", "close 9"]),
    ("tree parent", [], "", 0, ["create", "close 9"]),
    ("different parent", [], "", 1, []),
    ("tree different", [], "", 1, []),
    ("tree parent", [], "create", 72, ["create"]),
    ("absent", [proposal(12, "CLOSED")], "", 0, []),
    ("absent", [proposal(12, "MERGED")], "", 0, []),
    ("tree parent", [proposal(12)], "", 1, []),
    ("tree parent", [proposal(12)], "clean", 0, []),
    ("tree parent", [proposal(12, author="another")], "", 0, []),
    ("tree parent", [proposal(12, fork=True)], "", 0, ["create", "close 9"]),
    ("absent", [], "lookup", 71, []),
])
def test_proposal(tmp_path, ref, history, fail, rc, actions):
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["watch"]["steps"]
    step = next(s["run"] for s in steps if s.get("name") == "Push and open the draft")
    log = tmp_path / "actions"
    log.touch()
    opened = [proposal(9), proposal(10, author="another"), proposal(11, fork=True),
              proposal(12), proposal(13)]
    if fail == "clean":
        opened = [proposal(12)]
    env = {"PATH": os.defpath, "LOG": str(log), "REF": ref, "FAIL": fail,
           "HISTORY": json.dumps(history), "OPEN": json.dumps(opened),
           "GH_REPO": "example/repo", "GH_TOKEN": "offline", "BRANCH": BRANCH,
           "WIDENS": "false", "PREFIX": "okf-watch/", "TITLE": "test",
           "RUNNER_TEMP": str(tmp_path)}
    run = subprocess.run(["bash", "-c", MOCK + step], env=env, capture_output=True, text=True)
    assert run.returncode == rc, run.stderr
    assert log.read_text().splitlines() == actions


@pytest.mark.parametrize("listing", ["complete", "failed", "interrupted"])
def test_cooldown_observes_complete_listing(tmp_path, listing):
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["cooldown"]["steps"]
    step = next(s["run"] for s in steps if s.get("id") == "due")
    output = tmp_path / "result"
    records = [
        {**proposal(11), "isDraft": True, "createdAt": "2020-01-01T00:00:00Z",
         "labels": []},
        {**proposal(12), "isDraft": True, "createdAt": "2020-01-01T00:00:00Z",
         "labels": [{"name": "needs-founder"}]},
    ]
    env = {"PATH": os.defpath, "RECORDS": json.dumps(records), "LISTING": listing,
           "GH_REPO": "example/repo", "PREFIX": "okf-watch/", "COOLDOWN_DAYS": "7",
           "GITHUB_OUTPUT": str(output)}
    command = r"""
gh() {
  if [[ "$1 $2" == "api user" ]]; then echo watcher; return; fi
  [[ "$1 $2" == 'pr list' ]] || return 93
  [[ "$LISTING" != failed ]] || return 71
  jq -r "${!#}" <<< "$RECORDS"
  [[ "$LISTING" != interrupted ]] || return 71
}
"""
    run = subprocess.run(["bash", "-c", command + step], env=env, capture_output=True, text=True)
    if listing == "complete":
        assert run.returncode == 0, run.stderr
        assert output.read_text().splitlines() == ["ready=11", "founder=12"]
    else:
        assert run.returncode == 71, run.stderr
        assert not output.exists()
