#!/usr/bin/env python3
"""React to a red required-test run on main: re-run once, then alarm.

The merge gate runs only what a diff can reach, so the whole surface runs on main
(every push, and the hourly schedule). A red main is acted on at once, so this runs
after every Tests run on main (.github/workflows/main-red.yml):

* Only the REQUIRED surface counts: the `required-tests` aggregate job, which
  already excludes the heavy files (red baseline, `heavy-tests`) and applies the
  quarantine ledger, including its `flaky` lines. A red `heavy-tests` job never
  triggers anything here.
* First red attempt: re-run the failed jobs once. A flake costs one re-run.
* Still red: raise the alarm. Nothing is reverted. The only failing test IDs a run
  names are the old stack's Python suite's, and that stack takes fixes only: it is
  fixed forward, or reverted by a person who has read the failure.
* Green on main's current head: close the alarm.

The alarm is one open issue labelled `main-red`, with a plain comment per event:
no @-mentions (every PR here is the founder's account).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

REQUIRED_JOB = "required-tests"
LABEL = "main-red"
GH_TIMEOUT = 120


# ---- the decision (pure) -----------------------------------------------------


def decide(*, attempt: int, required: str | None, head_is_main_tip: bool) -> str:
    """One of: none, close-alarm, rerun, alarm."""
    if required == "success":
        return "close-alarm" if head_is_main_tip else "none"
    if required not in ("failure", "timed_out"):
        # cancelled / skipped / missing: no verdict about the code. A cancelled
        # required check is the re-run workflow's business, not a red main.
        return "none"
    return "rerun" if attempt < 2 else "alarm"


def render_body(*, sha: str, subject: str, run_url: str, failures: list[str]) -> str:
    lines = [
        f"<!-- main-red:{sha} -->",
        f"The required tests went red on main at `{sha[:12]}` ({subject}) and stayed red "
        f"after one re-run: {run_url}",
        "",
        "The cause is not established, and nothing is reverted automatically. Fix it "
        "forward, or revert by hand once the failure has been read.",
    ]
    if failures:
        lines += ["", "New failures (outside the quarantine ledger):"]
        lines += [f"- `{t}`" for t in failures[:40]]
        if len(failures) > 40:
            lines.append(f"- ... and {len(failures) - 40} more")
    return "\n".join(lines) + "\n"


# ---- I/O ---------------------------------------------------------------------


def _run(*cmd: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=check,
                          timeout=GH_TIMEOUT)


def _gh(*args: str) -> str:
    return _run("gh", *args).stdout


def required_conclusion(repo: str, run_id: int, attempt: int) -> str | None:
    """Read exactly the attempt whose completion is being handled."""
    out = _gh("api", "--paginate",
              f"repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs",
              "--jq", f'.jobs[] | select(.name == "{REQUIRED_JOB}") | .conclusion')
    found = [line for line in out.split() if line]
    return found[-1] if found else None


def tip_conclusion(repo: str, tip: str) -> str | None:
    """`required-tests` on the newest completed main run (push/schedule) of ``tip``."""
    runs = json.loads(_gh(
        "api", f"repos/{repo}/actions/workflows/tests.yml/runs?head_sha={tip}"
        "&status=completed&per_page=20", "--jq",
        '[.workflow_runs[] | select(.head_branch == "main" and '
        '(.event == "push" or .event == "schedule")) | {id, run_attempt}]'))
    for run in sorted(runs, key=lambda run: run["id"], reverse=True):
        conclusion = required_conclusion(repo, run["id"], run["run_attempt"])
        if conclusion in ("success", "failure", "timed_out"):
            return conclusion
    return None


def new_failures(repo: str, run_id: int, attempt: int) -> list[str]:
    import ci_required_tests as gate

    metadata = json.loads(_gh("api", f"repos/{repo}/actions/runs/{run_id}/attempts/{attempt}"))
    started = datetime.fromisoformat(metadata["run_started_at"])
    completed = datetime.fromisoformat(metadata["updated_at"])
    artifacts = _gh("api", "--paginate",
                    f"repos/{repo}/actions/runs/{run_id}/artifacts?name=junit-required-tests",
                    "--jq", ".artifacts[] | @json")
    candidates = [artifact for line in artifacts.splitlines()
                  if (artifact := json.loads(line))["name"] == "junit-required-tests"
                  and not artifact["expired"]
                  and started <= datetime.fromisoformat(artifact["created_at"]) <= completed]
    # Artifacts survive reruns. Missing/ambiguous evidence for THIS attempt is
    # infrastructure-only, even if an older attempt left failing test IDs.
    if len(candidates) != 1:
        return []
    with tempfile.TemporaryDirectory(prefix="ta-main-red-") as tmp:
        archive = Path(tmp) / "junit.zip"
        with archive.open("wb") as output:
            subprocess.run(
                ["gh", "api", f"repos/{repo}/actions/artifacts/{candidates[0]['id']}/zip"],
                stdout=output, stderr=subprocess.PIPE, check=True, timeout=GH_TIMEOUT)
        junit = Path(tmp) / "junit.xml"
        with zipfile.ZipFile(archive) as bundle:
            junit.write_bytes(bundle.read("junit.xml"))
        failing, _ = gate.collect_outcomes(junit)
    tolerated, flaky, _ = gate.parse_quarantine(gate.QUARANTINE)
    return sorted(failing - tolerated - flaky)


def _alarm(repo: str, body: str, sha: str) -> None:
    issues = json.loads(_gh("issue", "list", "-R", repo, "--label", LABEL, "--state", "open",
                            "--json", "number,body", "--limit", "5"))
    marker = f"<!-- main-red:{sha} -->"
    if issues:
        number = str(issues[0]["number"])
        if marker in (issues[0].get("body") or ""):
            return
        comments = _gh("api", "--paginate", f"repos/{repo}/issues/{number}/comments",
                       "--jq", ".[].body")
        if marker not in comments:
            _gh("issue", "comment", number, "-R", repo, "--body", body)
        return
    _gh("label", "create", LABEL, "-R", repo, "--force", "--color", "B60205",
        "--description", "The required tests are red on main")
    _gh("issue", "create", "-R", repo, "--title", "Main is red on the required tests",
        "--label", LABEL, "--body", body)


def _close_alarm(repo: str, sha: str, run_url: str) -> None:
    issues = json.loads(_gh("issue", "list", "-R", repo, "--label", LABEL, "--state", "open",
                            "--json", "number", "--limit", "5"))
    for issue in issues:
        _gh("issue", "close", str(issue["number"]), "-R", repo, "--comment",
            f"Required tests are green on main again at `{sha[:12]}`: {run_url}")


def handle(repo: str, run_id: int, sha: str, event: str, attempt: int, run_url: str,
           dry_run: bool = False) -> str:
    latest = json.loads(_gh("api", f"repos/{repo}/actions/runs/{run_id}"))
    if latest["run_attempt"] > attempt:
        # The newer completion will deliver its own event. Do not rerun again
        # or interpret a pending attempt's missing conclusion as a verdict.
        return "defer" if latest["status"] != "completed" else "none"
    required = required_conclusion(repo, run_id, attempt)
    main_tip = _run("git", "ls-remote", "origin", "refs/heads/main").stdout.split()[0]
    if sha != main_tip and tip_conclusion(repo, main_tip) == "success":
        return "none"
    action = decide(attempt=attempt, required=required, head_is_main_tip=(sha == main_tip))
    print(f"run {run_id} ({event}, attempt {attempt}) on {sha[:12]}: required={required} "
          f"-> {action}")
    if dry_run:
        return action
    if action == "close-alarm":
        _close_alarm(repo, sha, run_url)
    elif action == "rerun":
        _gh("run", "rerun", str(run_id), "-R", repo, "--failed")
    elif action == "alarm":
        subject = _run("git", "log", "-1", "--format=%s", sha, check=False).stdout.strip() or sha
        try:
            failures = new_failures(repo, run_id, attempt)
            note = ""
        except Exception:
            # Do not include exception text: gh errors can carry the token.
            failures, note = [], "\nFailure details could not be collected.\n"
        _alarm(repo, render_body(sha=sha, subject=subject, run_url=run_url,
                                 failures=failures) + note, sha)
    return action


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--run-id", type=int, required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--event", required=True)
    ap.add_argument("--attempt", type=int, required=True)
    ap.add_argument("--run-url", required=True)
    ap.add_argument("--dry-run", action="store_true", help="decide and print; act on nothing")
    args = ap.parse_args()
    handle(args.repo, args.run_id, args.sha, args.event, args.attempt, args.run_url,
           args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
