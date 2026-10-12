#!/usr/bin/env python3
"""Render the Shape registry queue and maintain its single GitHub issue."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import check_shapes, openspec_flow

ROOT = Path(__file__).resolve().parent.parent
TITLE = "Shape reconciliation"
LABEL = "shape-registry"
ROW_LINE = re.compile(r"^\ufeff?Registry row\(s\):\s*(.*)$", re.MULTILINE | re.IGNORECASE)
ROW_ID = re.compile(r"\b(?:B[12]|S(?:[1-9]|1\d|2[0-8])|N(?:10|[1-9]))\b")
FLEET_PREFIXES = (
    "cursor/",
    "codex/",
    "claude/",
    "cloud/",
    "tiny/",
    "conv/",
    "fix/",
    "feat/",
    "docs/",
    "spec/",
    "chore/",
    "ci/",
    "change/",
    "infra/",
    "uptime/",
    "usage/",
    "app/",
    "web/",
    "mobile/",
)


def gh(*args: str, input_data: str | None = None) -> object:
    proc = subprocess.run(
        ["gh", "api", *args],
        input=input_data,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError(f"gh api {' '.join(args)}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def pages(path: str) -> list[dict]:
    result = gh(path, "--paginate", "--slurp")
    return [item for page in result for item in page]


def declared_rows(body: str) -> list[str]:
    match = ROW_LINE.search(body or "")
    return sorted(set(ROW_ID.findall(match.group(1)))) if match else []


def shape_paths(files: list[dict], *, old_only: bool = False) -> set[str]:
    found: set[str] = set()
    for row, shape in check_shapes.SHAPES.items():
        if row in {"S20", "S21"}:
            if not old_only and any(
                item["filename"] == "docs/architecture.md"
                and re.search(rf"^[+-]\| {row} \|", item.get("patch") or "", re.MULTILINE)
                for item in files
            ):
                found.add(row)
            continue
        patterns = (
            shape.old
            if old_only
            else (
                *((glob, "") for glob in shape.paths),
                *shape.old,
                *((glob, "") for glob in shape.forbid),
            )
        )
        if any(
            check_shapes.registered_path(item["filename"], glob)
            and (not regex or re.search(regex, item.get("patch") or ""))
            for item in files
            if attributable(item["filename"])
            for glob, regex in patterns
        ):
            found.add(row)
    return found


def attributable(path: str) -> bool:
    return not path.startswith(
        (
            "packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/",
            "tests/",
            "docs/",
            "openspec/",
        )
    )


def overlap_file(path: str) -> bool:
    return attributable(path) and path.startswith(
        (
            "tinyassets/", "domains/", "starter/", "packages/", "desktop/",
            "desktop-app/", "mobile/", "agents/", "WebSite/",
        )
    ) and path.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".swift", ".sh"))


def old_path_changes(files: list[dict]) -> tuple[set[str], set[str]]:
    """Return rows whose old-path evidence grew and shrank in this PR."""
    growth: set[str] = set()
    convergence: set[str] = set()
    for row, shape in check_shapes.SHAPES.items():
        added_total = removed_total = 0
        for item in files:
            path = item["filename"]
            if not attributable(path):
                continue
            for glob, regex in shape.old:
                if not check_shapes.registered_path(path, glob):
                    continue
                patch = item.get("patch") or ""
                added = sum(
                    bool(not regex or re.search(regex, line[1:]))
                    for line in patch.splitlines()
                    if line.startswith("+") and not line.startswith("+++")
                )
                removed = sum(
                    bool(not regex or re.search(regex, line[1:]))
                    for line in patch.splitlines()
                    if line.startswith("-") and not line.startswith("---")
                )
                if item.get("status") == "added" and not patch:
                    added = 1
                if item.get("status") == "removed" and not patch:
                    removed = 1
                added_total += added
                removed_total += removed
        if added_total >= removed_total and added_total:
            growth.add(row)
        elif removed_total > added_total:
            convergence.add(row)
    return growth, convergence


def lowered_ceilings(files: list[dict]) -> set[str]:
    """Read pin reductions from the registry diff without fetching stale base commits."""
    result = set()
    source_rows = []
    for number, line in enumerate(
        (ROOT / "scripts/check_shapes.py").read_text(encoding="utf-8").splitlines(), 1
    ):
        match = re.search(r'["\'](B[12]|S\d+|N\d+)["\']:\s*Shape\(', line)
        if match:
            source_rows.append((number, match.group(1)))
    for item in files:
        if item["filename"] != "scripts/check_shapes.py":
            continue
        row = None
        removed = added = None
        new_line = 0
        for line in (item.get("patch") or "").splitlines():
            hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if hunk:
                new_line = int(hunk.group(1))
                row = next(
                    (name for number, name in reversed(source_rows) if number <= new_line), None
                )
                removed = added = None
                continue
            match = re.search(r'["\'](B[12]|S\d+|N\d+)["\']:\s*Shape\(', line)
            if match:
                row = match.group(1)
            pin = re.search(r"\bceiling=(\d+)", line)
            if not pin:
                if not line.startswith("-"):
                    new_line += 1
                continue
            if line.startswith("-"):
                removed = int(pin.group(1))
            elif line.startswith("+"):
                added = int(pin.group(1))
            if row and removed is not None and added is not None:
                if added < removed:
                    result.add(row)
                removed = added = None
            if not line.startswith("-"):
                new_line += 1
    return result


def registry_status() -> dict[str, str]:
    rows = check_shapes.registry_rows(check_shapes.REGISTRY.read_text(encoding="utf-8"))
    return {row: status.lower() for row, status in rows.items()}


def issue_body(repo: str, now: datetime) -> str:
    pulls = pages(f"repos/{repo}/pulls?state=open&per_page=100")
    pulls.sort(key=lambda pr: pr["number"])
    statuses = registry_status()
    lane_files: dict[int, set[str]] = {}
    lane_rows: dict[int, set[str]] = {}
    lines = [
        "# How to pick up work",
        "Start from [Direction](../blob/main/AGENTS.md#direction), "
        "[Shape registry](../blob/main/docs/architecture.md#shape-registry), and this issue. "
        "Pick an unclaimed convergence item or a frontier work item in #4624. "
        "Name the registry row in the PR: one lane = one row. "
        "Files-only starter-package (B1) items are safest for new fleets. "
        "Never add a second path; the guards will fail you.",
        "",
        "# Open PRs",
        "| PR | Branch | Declared rows | Touched rows | Findings |",
        "|---|---|---|---|---|",
    ]
    for pr in pulls:
        number = pr["number"]
        file_items = pages(f"repos/{repo}/pulls/{number}/files?per_page=100")
        files = {item["filename"] for item in file_items if overlap_file(item["filename"])}
        declared = declared_rows(pr.get("body") or "")
        touched = shape_paths(file_items)
        old_growth, old_convergence = old_path_changes(file_items)
        converges = old_convergence | lowered_ceilings(file_items)
        lane_files[number] = files
        lane_rows[number] = set(declared)
        findings = []
        if converges:
            findings.append("converges: " + ", ".join(sorted(converges)))
        if old_growth - set(declared):
            findings.append(
                "old path without row: " + ", ".join(sorted(old_growth - set(declared)))
            )
        deleting = sorted(row for row in old_growth if statuses.get(row) == "drifted")
        if deleting:
            findings.append("builds on deleting path: " + ", ".join(deleting))
        if not declared and datetime.fromisoformat(
            pr["created_at"].replace("Z", "+00:00")
        ) < now - timedelta(days=7):
            findings.append("registry row missing")
        if pr.get("draft") and datetime.fromisoformat(
            pr["updated_at"].replace("Z", "+00:00")
        ) < now - timedelta(days=7):
            findings.append("draft >7 days")
        if any(label["name"] == "shape-ceiling-raise" for label in pr.get("labels", [])):
            reason = next(
                (
                    line.strip()
                    for line in (pr.get("body") or "").splitlines()
                    if re.search(r"security|data.loss|outage", line, re.IGNORECASE)
                ),
                "reason missing",
            )
            findings.append("ceiling raise: " + reason[:160].replace("|", "\\|"))
        lines.append(
            f"| [#{number}]({pr['html_url']}) | `{pr['head']['ref']}` | "
            f"{', '.join(declared) or '—'} | {', '.join(sorted(touched)) or '—'} | "
            f"{'; '.join(findings) or '—'} |"
        )
    lines += ["", "# Overlap", "| Lanes | Shared row or file |", "|---|---|"]
    for i, left in enumerate(pulls):
        for right in pulls[i + 1 :]:
            a, b = left["number"], right["number"]
            shared_rows = lane_rows[a] & lane_rows[b]
            shared_files = lane_files[a] & lane_files[b]
            if shared_rows or shared_files:
                detail = ", ".join(sorted(shared_rows))
                if shared_files:
                    detail += (
                        ("; " if detail else "")
                        + f"{len(shared_files)} shared file(s): "
                        + ", ".join(sorted(shared_files)[:5])
                    )
                escaped = detail.replace("|", "\\|")
                lines.append(f"| #{a}, #{b} | {escaped} |")
    if lines[-1] == "|---|---|":
        lines.append("| — | — |")

    report = openspec_flow.build_report(ROOT)
    change_names = {change["name"] for change in report["changes"]}
    lines += ["", "# OpenSpec queue", "| Change | Finding |", "|---|---|"]
    for change in report["changes"]:
        name = change["name"]
        finding = []
        if change["total_tasks"] and change["classification"] == "complete-but-unarchived":
            finding.append("complete, unarchived")
        proposal = ROOT / "openspec/changes" / name / "proposal.md"
        if proposal.exists():
            content = proposal.read_text(encoding="utf-8")
            for other in sorted(change_names - {name}):
                if re.search(
                    rf"\bsupersedes?\b[^.\n]{{0,160}}`?{re.escape(other)}`?", content, re.IGNORECASE
                ):
                    finding.append(f"supersedes open `{other}`")
        if finding:
            lines.append(f"| `{name}` | {'; '.join(finding)} |")
    if lines[-1] == "|---|---|":
        lines.append("| — | — |")

    branches = pages(f"repos/{repo}/branches?per_page=100")
    merged = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "merged",
            "--limit",
            "1000",
            "--json",
            "headRefName,number,mergedAt",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        check=True,
    )
    merged_heads = {pr["headRefName"]: pr for pr in json.loads(merged.stdout)}
    open_heads = {
        pr["head"]["ref"]
        for pr in pulls
        if pr["head"]["repo"] and pr["head"]["repo"]["full_name"].lower() == repo.lower()
    }
    lines += ["", "# Remote branches without an open PR", "| Branch | Status |", "|---|---|"]
    for branch in sorted(branches, key=lambda item: item["name"]):
        name = branch["name"]
        if name in open_heads or name in {"main", "master"}:
            continue
        if not name.startswith(FLEET_PREFIXES):
            continue
        sha = branch["commit"]["sha"][:12]
        historical = merged_heads.get(name)
        status = (
            f"merged as #{historical['number']}" if historical else "no merged PR in latest 1,000"
        )
        lines.append(f"| `{name}` | {status}; pushed `{sha}` |")
    if lines[-1] == "|---|---|":
        lines.append("| — | — |")
    lines += [
        "",
        "Generated from GitHub PRs/branches and the checked-out registry. No branch is deleted.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print body; make no writes")
    parser.add_argument(
        "--repo", default=os.environ.get("GITHUB_REPOSITORY", "TinyAssets/TinyAssets")
    )
    args = parser.parse_args()
    body = issue_body(args.repo, datetime.now(timezone.utc))
    if args.dry_run:
        sys.stdout.write(body)
        return 0
    issues = pages(f"repos/{args.repo}/issues?state=all&per_page=100")
    matches = [issue for issue in issues if "pull_request" not in issue and issue["title"] == TITLE]
    if len(matches) > 1:
        raise RuntimeError(f"{len(matches)} '{TITLE}' issues exist; refusing to choose")
    if matches:
        issue = matches[0]
        existing_labels = {label["name"] for label in issue.get("labels", [])}
        labels = sorted(existing_labels | {LABEL})
        if issue.get("body") != body or issue["state"] != "open" or LABEL not in existing_labels:
            gh(
                f"repos/{args.repo}/issues/{issue['number']}",
                "--method",
                "PATCH",
                "--input",
                "-",
                input_data=json.dumps({"body": body, "state": "open", "labels": labels}),
            )
        print(issue["html_url"])
    else:
        issue = gh(
            f"repos/{args.repo}/issues",
            "--method",
            "POST",
            "--input",
            "-",
            input_data=json.dumps({"title": TITLE, "body": body, "labels": [LABEL]}),
        )
        print(issue["html_url"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
