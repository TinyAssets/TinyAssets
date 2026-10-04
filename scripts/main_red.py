#!/usr/bin/env python3
"""React to a red required-test run on main: re-run once, then revert or alarm.

Lean-CI lever L1 (docs/design-notes/2026-10-02-affected-only-merge-gate.md)
moves the full required suite out of the merge-group gate and onto the
post-merge run on main. That is only safe if a red main is acted on at once,
so this runs after every Tests run on main (.github/workflows/main-red.yml):

* Only the REQUIRED surface counts: the `required-tests` aggregate job, which
  already excludes the heavy files (red baseline, `heavy-tests`) and applies the
  quarantine ledger, including its `flaky` lines. A red `heavy-tests` job never
  triggers anything here.
* First red attempt: re-run the failed jobs once. A flake costs one re-run,
  never a revert.
* Still red with failing test IDs on a push run whose PARENT commit was green:
  that merge is a candidate cause. Open a revert PR (not auto-armed; it needs a
  receipt like every PR) and raise the alarm issue.
* Still red otherwise (the parent's state is unknown or red, a scheduled run, a
  revert commit): alarm only. Reverting without knowing the culprit reverts
  the wrong change.
* Green on main's current head: close the alarm.

The alarm is one open issue labelled `main-red`, with a plain comment per event:
no @-mentions (every PR here is the founder's account).
"""

from __future__ import annotations

import argparse
import json
import os
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
REVERT_PREFIX = "auto-revert/"
GH_TIMEOUT = 120


# ---- the decision (pure) -----------------------------------------------------


def decide(*, event: str, attempt: int, required: str | None, parent_required: str | None,
           head_is_main_tip: bool, is_revert_commit: bool) -> str:
    """One of: none, close-alarm, rerun, revert, alarm."""
    if required == "success":
        return "close-alarm" if head_is_main_tip else "none"
    if required not in ("failure", "timed_out"):
        # cancelled / skipped / missing: no verdict about the code. A cancelled
        # required check is the re-run workflow's business, not a red main.
        return "none"
    if attempt < 2:
        return "rerun"
    if (required == "failure" and event == "push" and parent_required == "success"
            and not is_revert_commit):
        return "revert"
    return "alarm"


def revert_title(subject: str) -> str:
    return f"Revert \"{subject}\" (required tests red on main)"


def render_body(*, sha: str, subject: str, run_url: str, failures: list[str],
                action: str) -> str:
    lines = [
        f"<!-- main-red:{sha} -->",
        f"The required tests went red on main at `{sha[:12]}` ({subject}) and stayed red "
        f"after one re-run: {run_url}",
        "",
    ]
    if action == "revert":
        lines += [
            "Its parent was green and failing test IDs were reported; this merge is a "
            "candidate cause.",
            "This PR reverts it. It is not armed: it needs a Drain-Review receipt like "
            "every PR. Re-land the change with a fix once main is green.",
        ]
    else:
        lines += [
            "The cause is not established. Infrastructure or unavailable test evidence "
            "may explain the failure; nothing was reverted automatically.",
        ]
    if failures:
        lines += ["", "New failures (outside the quarantine ledger):"]
        lines += [f"- `{t}`" for t in failures[:40]]
        if len(failures) > 40:
            lines.append(f"- ... and {len(failures) - 40} more")
    return "\n".join(lines) + "\n"


# ---- I/O ---------------------------------------------------------------------


def _run(*cmd: str, check: bool = True, env: dict | None = None,
         cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd or REPO_ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=check,
                          timeout=GH_TIMEOUT, env=env)


def _gh(*args: str, token: str | None = None) -> str:
    env = None
    if token:
        env = {**os.environ, "GH_TOKEN": token}
    return _run("gh", *args, env=env).stdout


def required_conclusion(repo: str, run_id: int, attempt: int) -> str | None:
    """Read exactly the attempt whose completion is being handled."""
    out = _gh("api", "--paginate",
              f"repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs",
              "--jq", f'.jobs[] | select(.name == "{REQUIRED_JOB}") | .conclusion')
    found = [line for line in out.split() if line]
    return found[-1] if found else None


def parent_conclusion(repo: str, parent: str) -> str | None:
    """`required-tests` on the newest completed main run (push/schedule) of ``parent``."""
    runs = json.loads(_gh(
        "api", f"repos/{repo}/actions/workflows/tests.yml/runs?head_sha={parent}"
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


def _open_revert(repo: str, sha: str, subject: str, body: str, token: str) -> str | None:
    """Push auto-revert/<sha12> and open the PR with a user token (so CI runs on it)."""
    branch = f"{REVERT_PREFIX}{sha[:12]}"
    existing = _gh("pr", "list", "-R", repo, "--head", branch, "--state", "all",
                   "--json", "url", "--jq", ".[].url", token=token).strip()
    if existing:
        return existing.splitlines()[0]
    git = ["git", "-c", "user.name=main-red", "-c", "user.email=main-red@users.noreply.github.com"]
    _run("git", "fetch", "-q", "origin", "main")
    _run("git", "checkout", "-q", "-B", branch, "origin/main")
    if _run(*git, "revert", "--no-edit", sha, check=False).returncode:
        _run("git", "revert", "--abort", check=False)
        return None
    remote = f"https://x-access-token:{token}@github.com/{repo}.git"
    _run("git", "push", "-q", remote, f"HEAD:refs/heads/{branch}")
    return _gh("pr", "create", "-R", repo, "--base", "main", "--head", branch,
               "--title", revert_title(subject), "--body", body, "--label", LABEL,
               token=token).strip()


def handle(repo: str, run_id: int, sha: str, event: str, attempt: int, run_url: str,
           revert_token: str | None, dry_run: bool = False) -> str:
    latest = json.loads(_gh("api", f"repos/{repo}/actions/runs/{run_id}"))
    if latest["run_attempt"] > attempt:
        # The newer completion will deliver its own event. Do not rerun again
        # or interpret a pending attempt's missing conclusion as a verdict.
        return "defer" if latest["status"] != "completed" else "none"
    required = required_conclusion(repo, run_id, attempt)
    main_tip = _run("git", "ls-remote", "origin", "refs/heads/main").stdout.split()[0]
    if sha != main_tip and parent_conclusion(repo, main_tip) == "success":
        return "none"
    subject = _run("git", "log", "-1", "--format=%s", sha, check=False).stdout.strip() or sha
    parents = _run("git", "rev-list", "--parents", "-n", "1", sha,
                   check=False).stdout.split()[1:]
    parent_required = parent_conclusion(repo, parents[0]) if len(parents) == 1 else None
    action = decide(event=event, attempt=attempt, required=required,
                    parent_required=parent_required, head_is_main_tip=(sha == main_tip),
                    is_revert_commit=subject.startswith("Revert \""))
    failures: list[str] = []
    detail_note = ""
    if action in ("revert", "alarm"):
        try:
            failures = new_failures(repo, run_id, attempt)
        except Exception:
            # Do not include exception text: git/gh errors can contain the PAT.
            detail_note = "Failure details could not be collected."
            if action == "revert":
                detail_note += " The revert could not be opened."
        if not failures:
            action = "alarm"
    print(f"run {run_id} ({event}, attempt {attempt}) on {sha[:12]}: required={required}, "
          f"parent={parent_required} -> {action}")
    if dry_run:
        return action
    if action == "close-alarm":
        _close_alarm(repo, sha, run_url)
    elif action == "rerun":
        _gh("run", "rerun", str(run_id), "-R", repo, "--failed")
    elif action in ("revert", "alarm"):
        body = render_body(sha=sha, subject=subject, run_url=run_url, failures=failures,
                           action="alarm")
        if detail_note:
            body += f"\n{detail_note}\n"
        if action == "revert":
            try:
                pr_body = render_body(sha=sha, subject=subject, run_url=run_url,
                                      failures=failures, action="revert")
                url = (_open_revert(repo, sha, subject, pr_body, revert_token)
                       if revert_token else None)
            except Exception:
                url = None
            note = (f"Revert PR (UNARMED): {url}" if url else
                    "The revert could not be opened; investigate and revert by hand "
                    "if appropriate.")
            body += f"\n{note}\n"
        _alarm(repo, body, sha)
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
           os.environ.get("REVERT_TOKEN") or None, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
