#!/usr/bin/env python3
"""Inspect OpenSpec delivery flow and check admission without mutating state."""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tarfile
from collections import defaultdict
from pathlib import Path
from typing import Any

TASK_RE = re.compile(r"^\s*-\s*\[([ xX])\]", re.MULTILINE)
UMBRELLA_RE = re.compile(
    r"\b(full|complete|entire|whole)\b.{0,40}\b(platform|product|vision|system)\b",
    re.IGNORECASE | re.DOTALL,
)
OWNER_RE = re.compile(r"(?:claimed|in-flight):([^\s|]+)", re.IGNORECASE)
ACTIVE_STATUSES = ("claimed:", "in-flight")
QUEUED_STATUSES = ("pending", "dev-ready")
HOST_STATUSES = ("host-action", "host-decision", "host-review", "monitoring")
TASK_CEILING = 12
CEILING_REVIEW_DATE = "2026-08-11"


def _task_counts_text(text: str) -> tuple[int, int]:
    matches = TASK_RE.findall(text)
    completed = sum(mark.lower() == "x" for mark in matches)
    return completed, len(matches) - completed


def _status_rows(status_path: Path) -> list[dict[str, str]]:
    if not status_path.exists():
        return []
    return _status_rows_text(status_path.read_text(encoding="utf-8"))


def _status_rows_text(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[0] in {"Task", "---"}:
            continue
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(
            {
                "task": cells[0],
                "files": cells[1],
                "depends": cells[2],
                "status": cells[3],
                "raw": line,
            }
        )
    return rows


def _row_mentions(row: dict[str, str], change_name: str) -> bool:
    boundary = rf"(?<![A-Za-z0-9_-]){re.escape(change_name)}(?![A-Za-z0-9_-])"
    return re.search(boundary, row["raw"]) is not None


def _active_owners(matching_rows: list[dict[str, str]]) -> list[str]:
    owners = {
        owner_match.group(1)
        for row in matching_rows
        if row["status"].lower().startswith(ACTIVE_STATUSES)
        if (owner_match := OWNER_RE.search(row["status"]))
    }
    return sorted(owners)


def _branch_names(repo: Path) -> list[str]:
    """Every local and remote branch name, lowercased.

    Ownership signal after STATUS.md was retired (2026-08-25). `AGENTS.md`
    § *Two Living Files* names git branches and open PRs as the home for
    who-is-working-on-what, so the queue reads that instead of a prose board.
    A branch is also harder to lie to than a table someone has to remember to
    update -- it exists because work started.
    """
    proc = subprocess.run(
        ["git", "branch", "-a", "--format=%(refname:short)"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if proc.returncode != 0:
        return []
    return [b.strip().lower() for b in proc.stdout.splitlines() if b.strip()]


def _branch_owners(branches: list[str], change_name: str) -> list[str]:
    """Branches whose name references this change.

    Matched on the slug with separators normalized, so `codex/reconcile-stale`
    and `claude/reconcile_stale` both count for `reconcile-stale`. The owner is
    the branch prefix when there is one (`codex`, `claude`), else the branch.
    """
    slug = change_name.lower()
    loose = re.sub(r"[-_]+", "[-_]+", re.escape(slug).replace(r"\-", "-"))
    pattern = re.compile(rf"(?<![a-z0-9]){loose}(?![a-z0-9])")
    owners: set[str] = set()
    for branch in branches:
        stem = branch.split("/", 1)[-1] if "/" in branch else branch
        if pattern.search(stem) or pattern.search(branch):
            prefix = branch.split("/", 1)[0] if "/" in branch else branch
            if prefix in {"origin", "remotes"}:
                rest = branch.split("/", 2)
                prefix = rest[1] if len(rest) > 2 else branch
            owners.add(prefix)
    return sorted(owners)


def _classify(
    *,
    remaining: int,
    matching_rows: list[dict[str, str]],
    tasks_exist: bool,
    owners: list[str],
    board_present: bool = True,
    branch_owners: list[str] | None = None,
) -> str:
    branch_owners = branch_owners or []
    if not tasks_exist:
        return "invalid-artifacts"
    if remaining == 0:
        return "complete-but-unarchived"
    if owners or any(
        row["status"].lower().startswith(ACTIVE_STATUSES) for row in matching_rows
    ):
        return "in-flight"
    if branch_owners:
        return "in-flight"
    if any(
        row["status"].lower() in HOST_STATUSES
        or "host" in row["status"].lower()
        or "manual" in row["status"].lower()
        for row in matching_rows
    ):
        return "host-owned"
    if any(row["status"].lower().startswith(QUEUED_STATUSES) for row in matching_rows):
        return "queued"
    # "untracked" is a claim about a board. With STATUS.md retired
    # (2026-08-25) there is no board to be untracked *by*: ownership lives in
    # git branches and open PRs, which this offline audit deliberately does
    # not query. Say "unclassified" rather than assert the change is unowned.
    if not board_present and not branch_owners:
        return "unclassified"
    return "untracked"


def _git_flow(repo: Path, since: str | None) -> dict[str, Any] | None:
    if not since:
        return None
    if since.startswith("-"):
        return {"since": since, "error": "git revision must not start with '-'"}
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", f"{since}^{{commit}}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if verify.returncode != 0:
        return {"since": since, "error": verify.stderr.strip()}

    def tree_dirs(tree: str) -> set[str]:
        result = subprocess.run(
            ["git", "ls-tree", "-d", "--name-only", tree],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        return set(result.stdout.splitlines()) if result.returncode == 0 else set()

    baseline_active = tree_dirs(f"{since}:openspec/changes") - {"archive"}
    baseline_archived = tree_dirs(f"{since}:openspec/changes/archive")
    result = subprocess.run(
        [
            "git",
            "log",
            "--format=",
            "--name-only",
            "--diff-filter=A",
            "--no-renames",
            f"{since}..HEAD",
            "--",
            "openspec/changes",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {"since": since, "error": result.stderr.strip()}
    admitted: set[str] = set()
    archived: set[str] = set()
    for line in result.stdout.splitlines():
        if not line:
            continue
        parts = Path(line).parts
        if len(parts) < 4 or parts[:2] != ("openspec", "changes"):
            continue
        if parts[2] == "archive" and len(parts) >= 5:
            if parts[3] not in baseline_archived:
                archived.add(parts[3])
        elif parts[2] not in baseline_active:
            admitted.add(parts[2])
    return {
        "since": since,
        "admitted_changes": len(admitted),
        "archived_changes": len(archived),
        "net_active_arrival": len(admitted) - len(archived),
    }


def _git_ref_snapshot(
    repo: Path,
    git_ref: str,
) -> tuple[list[str], dict[str, str]]:
    """Read active change metadata from one immutable Git tree."""
    if not git_ref or git_ref.startswith("-"):
        raise RuntimeError(f"cannot inspect Git ref {git_ref}: invalid ref")
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", f"{git_ref}^{{commit}}"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if verify.returncode != 0:
        detail = (verify.stderr or verify.stdout).strip()
        raise RuntimeError(f"cannot inspect Git ref {git_ref}: {detail}")
    commit = verify.stdout.strip()
    listing = subprocess.run(
        [
            "git",
            "ls-tree",
            "-r",
            "--name-only",
            commit,
            "--",
            "openspec/changes",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if listing.returncode != 0:
        detail = (listing.stderr or listing.stdout).strip()
        raise RuntimeError(f"cannot inspect Git ref {git_ref}: {detail}")

    names: set[str] = set()
    wanted: list[str] = []
    for raw_path in listing.stdout.splitlines():
        path = raw_path.strip().replace("\\", "/")
        parts = path.split("/")
        if len(parts) < 4 or parts[:2] != ["openspec", "changes"]:
            continue
        if parts[2] == "archive":
            continue
        names.add(parts[2])
        if len(parts) == 4 and parts[3] in {"tasks.md", "proposal.md"}:
            wanted.append(path)

    contents: dict[str, str] = {}
    if wanted:
        archived = subprocess.run(
            ["git", "archive", "--format=tar", commit, "--", *wanted],
            cwd=repo,
            capture_output=True,
            check=False,
        )
        if archived.returncode != 0:
            detail = archived.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"cannot inspect Git ref {git_ref}: {detail}")
        try:
            with tarfile.open(fileobj=io.BytesIO(archived.stdout), mode="r:") as tar:
                for member in tar.getmembers():
                    if not member.isfile():
                        continue
                    stream = tar.extractfile(member)
                    if stream is None:
                        continue
                    contents[member.name.replace("\\", "/")] = stream.read().decode(
                        "utf-8"
                    )
        except (tarfile.TarError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                f"cannot inspect Git ref {git_ref}: invalid archive: {exc}"
            ) from exc
    return sorted(names), contents


def build_report(
    repo: Path | str,
    *,
    since: str | None = None,
    git_ref: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic, JSON-serializable delivery-flow report."""
    root = Path(repo).resolve()
    changes: list[dict[str, Any]] = []
    provider_wip: defaultdict[str, list[str]] = defaultdict(list)
    branches = _branch_names(root)

    if git_ref is not None:
        candidate_names, snapshot = _git_ref_snapshot(root, git_ref)
        rows = _status_rows_text(snapshot.get("STATUS.md", ""))

        def read_change_file(name: str, filename: str) -> str | None:
            return snapshot.get(f"openspec/changes/{name}/{filename}")

    else:
        changes_root = root / "openspec" / "changes"
        rows = _status_rows(root / "STATUS.md")
        candidate_names = (
            sorted(
                path.name
                for path in changes_root.iterdir()
                if path.is_dir() and path.name != "archive"
            )
            if changes_root.exists()
            else []
        )

        def read_change_file(name: str, filename: str) -> str | None:
            path = changes_root / name / filename
            return path.read_text(encoding="utf-8") if path.exists() else None

    for change_name in candidate_names:
        tasks = read_change_file(change_name, "tasks.md")
        tasks_exist = tasks is not None
        completed, remaining = _task_counts_text(tasks) if tasks is not None else (0, 0)
        proposal = read_change_file(change_name, "proposal.md") or ""
        matching_rows = [row for row in rows if _row_mentions(row, change_name)]
        owners = _active_owners(matching_rows)
        active_status = any(
            row["status"].lower().startswith(ACTIVE_STATUSES)
            for row in matching_rows
        )
        change_branch_owners = _branch_owners(branches, change_name)
        classification = _classify(
            board_present=bool(rows),
            branch_owners=change_branch_owners,
            remaining=remaining,
            matching_rows=matching_rows,
            tasks_exist=tasks_exist,
            owners=owners,
        )
        total = completed + remaining
        record = {
            "name": change_name,
            "completed_tasks": completed,
            "remaining_tasks": remaining,
            "total_tasks": total,
            "classification": classification,
            "owner": (owners or change_branch_owners or [None])[0],
            "owners": owners or change_branch_owners,
            "branch_owners": change_branch_owners,
            "active_status": active_status,
            "oversized": total > TASK_CEILING,
            "umbrella_warning": bool(
                UMBRELLA_RE.search(f"{change_name}\n{proposal}")
            ),
            "status_rows": [row["task"] for row in matching_rows],
        }
        changes.append(record)
        if classification in {"in-flight", "invalid-artifacts"}:
            for owner in owners:
                provider_wip[owner].append(change_name)

    completed_first = sorted(
        (change for change in changes if change["classification"] == "complete-but-unarchived"),
        key=lambda change: change["name"],
    )
    claimed_next = sorted(
        (change for change in changes if change["classification"] == "in-flight"),
        key=lambda change: (change["remaining_tasks"], change["name"]),
    )
    queued_last = sorted(
        (change for change in changes if change["classification"] == "queued"),
        key=lambda change: (change["remaining_tasks"], change["name"]),
    )

    summary = {
        "active_changes": len(changes),
        "completed_tasks": sum(change["completed_tasks"] for change in changes),
        "remaining_tasks": sum(change["remaining_tasks"] for change in changes),
        "delivery_wip": sum(
            change["classification"] in {"in-flight", "invalid-artifacts"}
            for change in changes
        ),
        "untracked_changes": sum(
            change["classification"] == "untracked" for change in changes
        ),
        "oversized_changes": sum(change["oversized"] for change in changes),
        "complete_unarchived": len(completed_first),
        "invalid_changes": sum(
            change["classification"] == "invalid-artifacts" for change in changes
        ),
    }
    warnings = [
        (
            "Provider WIP uses exact STATUS identities. Global WIP remains visible; "
            "minting a provider suffix to evade the limit is a review violation."
        )
    ]
    warnings.extend(
        f"{change['name']} is missing tasks.md and needs artifact triage."
        for change in changes
        if change["classification"] == "invalid-artifacts"
    )
    return {
        "summary": summary,
        "changes": changes,
        "provider_wip": {
            provider: sorted(names) for provider, names in sorted(provider_wip.items())
        },
        "recommendations": [
            change["name"] for change in completed_first + claimed_next + queued_last
        ],
        "warnings": warnings,
        "git_flow": _git_flow(root, since),
        "source_ref": git_ref,
        "policy": {
            "task_ceiling": TASK_CEILING,
            "ceiling_review_date": CEILING_REVIEW_DATE,
            "provider_identity": "exact STATUS session identity",
        },
    }


def check_admission(
    report: dict[str, Any],
    *,
    change_name: str,
    provider: str,
) -> dict[str, Any]:
    """Check a named change against bounded admission rules."""
    changes = {change["name"]: change for change in report["changes"]}
    errors: list[str] = []
    warnings = list(report["warnings"])
    candidate = changes.get(change_name)
    if candidate is None:
        errors.append(f"Unknown active change: {change_name}")
    else:
        if candidate["total_tasks"] > TASK_CEILING:
            errors.append(
                f"{change_name} has {candidate['total_tasks']} tasks; "
                f"the {TASK_CEILING}-task ceiling counts all checkboxes."
            )
        other_owned = [
            name
            for name in report["provider_wip"].get(provider, [])
            if name != change_name
        ]
        if other_owned:
            errors.append(
                f"{provider} already owns active OpenSpec change(s): "
                + ", ".join(other_owned)
            )
        if candidate.get("umbrella_warning"):
            warnings.append(
                f"{change_name} contains umbrella/full-vision language; "
                "perform semantic scope review."
            )

    return {
        "allowed": not errors,
        "change": change_name,
        "provider": provider,
        "global_delivery_wip": report["summary"]["delivery_wip"],
        "errors": errors,
        "warnings": warnings,
    }


def render_text(report: dict[str, Any]) -> str:
    """Render a compact human-readable report."""
    summary = report["summary"]
    lines = [
        "OpenSpec delivery flow",
        f"Active changes: {summary['active_changes']}",
        (
            f"Tasks: {summary['completed_tasks']} complete / "
            f"{summary['remaining_tasks']} remaining"
        ),
        f"Delivery WIP: {summary['delivery_wip']}",
        (
            f"Exceptions: {summary['complete_unarchived']} complete-unarchived / "
            f"{summary['untracked_changes']} untracked / "
            f"{summary['oversized_changes']} oversized"
        ),
        "",
        "Finish-first:",
    ]
    if report["recommendations"]:
        lines.extend(f"- {name}" for name in report["recommendations"])
    else:
        lines.append("- none; triage coordination state before starting work")
    lines.extend(["", "Provider WIP:"])
    if report["provider_wip"]:
        lines.extend(
            f"- {provider}: {', '.join(changes)}"
            for provider, changes in report["provider_wip"].items()
        )
    else:
        lines.append("- none")
    lines.extend(["", "Changes:"])
    for change in report["changes"]:
        owner = (
            f" | owners={','.join(change['owners'])}" if change["owners"] else ""
        )
        oversized = " | OVERSIZED" if change["oversized"] else ""
        lines.append(
            f"- {change['name']} | {change['classification']} | "
            f"{change['completed_tasks']}/{change['total_tasks']}{owner}{oversized}"
        )
    if report["git_flow"]:
        lines.extend(["", "Git flow:", f"- {json.dumps(report['git_flow'], sort_keys=True)}"])
    lines.extend(["", "Warnings:"])
    lines.extend(f"- {warning}" for warning in report["warnings"])
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command")
    audit = subparsers.add_parser("audit", help="report delivery flow")
    audit.add_argument("--json", action="store_true", dest="as_json")
    audit.add_argument("--since", help="git revision for admission/archive counts")
    audit.add_argument(
        "--ref",
        dest="git_ref",
        help="inspect STATUS and active changes from one exact Git ref",
    )
    check = subparsers.add_parser("check-change", help="check new-change admission")
    check.add_argument("change")
    check.add_argument("--provider", required=True)
    check.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "audit"
    try:
        report = build_report(
            args.repo,
            since=getattr(args, "since", None),
            git_ref=getattr(args, "git_ref", None),
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if command == "check-change":
        result = check_admission(
            report,
            change_name=args.change,
            provider=args.provider,
        )
        if args.as_json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print("ALLOWED" if result["allowed"] else "BLOCKED")
            for error in result["errors"]:
                print(f"ERROR: {error}")
            for warning in result["warnings"]:
                print(f"WARNING: {warning}")
            print(f"Global delivery WIP: {result['global_delivery_wip']}")
        return 0 if result["allowed"] else 2

    if getattr(args, "as_json", False):
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
