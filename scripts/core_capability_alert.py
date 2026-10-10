"""Open/update one capability incident and use the existing emergency push sink."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pushover_page import P0_EXPIRE_S, P0_RETRY_S, send_pushover

from tinyassets.core_capabilities import validate_report  # noqa: E402

TITLE = "Core user capability failure"


def github(path, *, method="GET", document=None):
    command = ["gh", "api", path, "--method", method]
    if document is not None:
        command += ["--input", "-"]
    result = subprocess.run(
        command,
        input=json.dumps(document) if document is not None else None,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def alert(report):
    repo = os.environ["GITHUB_REPOSITORY"]
    sha = report.get("deployed_sha") or "unknown (receipt unavailable)"
    failures = report.get("failures") or validate_report(report)
    if report.get("probe_error") and not failures:
        failures = [dict(capability="public_mcp", code="probe_failed")]
    existing = [
        issue
        for issue in github(f"repos/{repo}/issues?state=open&labels=p0-outage")
        if issue["title"] == TITLE
    ]
    if not failures:
        for issue in existing:
            github(
                f"repos/{repo}/issues/{issue['number']}/comments",
                method="POST",
                document={"body": f"RECOVERED: every core capability passed at `{sha}`."},
            )
            github(
                f"repos/{repo}/issues/{issue['number']}",
                method="PATCH",
                document={"state": "closed"},
            )
        return
    detail = "\n".join(
        f"- {row['capability']}: `{row['code']}`"
        + (f" — {row['detail']}" if row.get("detail") else "")
        for row in failures
    )
    run = f"https://github.com/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    body = f"Deployed SHA (receipt): `{sha}`\n\n{detail}\n\n[Canary evidence]({run})"
    if existing:
        issue = existing[0]
        github(f"repos/{repo}/issues/{issue['number']}", method="PATCH", document={"body": body})
    else:
        issue = github(
            f"repos/{repo}/issues",
            method="POST",
            document={"title": TITLE, "body": body, "labels": ["p0-outage", "bug"]},
        )
    # A failed push is a failed alert job, never an apparently delivered page.
    # The incident comment remembers a delivered page across hosted invocations.
    comments = github(f"repos/{repo}/issues/{issue['number']}/comments")
    if not any(c["body"].startswith("[PAGED core-capabilities]") for c in comments):
        ok, error = send_pushover(
            TITLE,
            f"{sha}\n{detail}"[:900],
            issue["html_url"],
            priority=2,
            retry=P0_RETRY_S,
            expire=P0_EXPIRE_S,
        )
        if not ok:
            raise RuntimeError("Emergency push delivery failed: " + error)
        github(
            f"repos/{repo}/issues/{issue['number']}/comments",
            method="POST",
            document={"body": "[PAGED core-capabilities] Emergency Pushover page delivered."},
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    args = parser.parse_args()
    path = Path(args.report)
    document = (
        json.loads(path.read_text())
        if path.exists()
        else {
            "failures": [
                {
                    "capability": "public_mcp",
                    "code": "probe_failed",
                    "detail": "Canary crashed before writing its report.",
                }
            ]
        }
    )
    alert(document)
