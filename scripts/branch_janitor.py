"""Classify and optionally clean up stale git branches (Layer 1).

Part of the branch lifecycle automation; see
``docs/reference/worktree-discipline.md``.

Report-first by design. The default mode only classifies and prints (and can
write a rolling tracking issue with ``--issue``). ``--apply`` is the only mode
that deletes anything, and even then hard guardrails protect important
branches.

Categories
----------
PROTECTED    main/master/production/release/* — never touched.
MERGED       already merged into the base ref (ancestor *or* squash-merged) —
             its changes are all on main, so deleting the branch loses nothing.
             Swept in --apply (or --only-merged).
STALE_FLAG   unmerged, no open PR, no commit in STALE_DAYS — reported only.
STALE_DELETE flagged and still untouched past GRACE_DAYS — deleted in --apply.
ACTIVE       has an open PR, or a commit younger than RECENT_DAYS, or simply
             not yet stale — never touched.

Guardrails (hard, always on)
----------------------------
* Never delete a protected branch.
* Never delete a branch with an open PR.
* Never delete a branch with a commit younger than RECENT_DAYS, regardless of
  total age.
* If open-PR data cannot be fetched, unmerged deletion is disabled for the run
  (only provably-merged branches may be swept).
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from git_squash_merge import is_merged_into  # noqa: E402  (sibling-script import)

PROTECTED_EXACT = {"main", "master", "production", "develop", "HEAD"}
PROTECTED_PREFIXES = ("release/", "hotfix/")
RECENT_DAYS = 7
STALE_DAYS = 7
GRACE_DAYS = 14
ISSUE_MARKER = "<!-- branch-janitor -->"
ISSUE_TITLE = "🧹 Branch janitor report"

CATEGORIES = ("PROTECTED", "ACTIVE", "MERGED", "STALE_FLAG", "STALE_DELETE")


def _force_utf8_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        enc = (getattr(stream, "encoding", None) or "").lower().replace("_", "-")
        if enc == "utf-8":
            continue
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
                continue
            except (AttributeError, ValueError, OSError):
                pass
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            try:
                wrapped = io.TextIOWrapper(
                    buffer, encoding="utf-8", errors="replace", line_buffering=True
                )
                setattr(sys, name, wrapped)
            except (AttributeError, ValueError, OSError):
                pass


def _run(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, capture_output=True, text=True, check=check, encoding="utf-8", errors="replace"
    )


@dataclass
class BranchVerdict:
    name: str
    category: str
    age_days: int
    reason: str
    last_commit_unix: int


def remote_branches(remote: str, now: int) -> list[tuple[str, int]]:
    """Return (short-name-without-remote-prefix, last-commit-unix) per branch."""
    fmt = "%(refname:short)%09%(committerdate:unix)"
    proc = _run(["git", "for-each-ref", f"--format={fmt}", f"refs/remotes/{remote}"])
    out: list[tuple[str, int]] = []
    prefix = f"{remote}/"
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        ref, _, ts = line.partition("\t")
        if not ref.startswith(prefix):
            continue
        name = ref[len(prefix):]
        if name in ("HEAD", ""):
            continue
        try:
            out.append((name, int(ts)))
        except ValueError:
            continue
    return out


def open_pr_branches() -> set[str] | None:
    """Head-ref names of open PRs, or None if gh is unavailable/unauthed."""
    proc = _run(["gh", "pr", "list", "--state", "open", "--limit", "1000", "--json", "headRefName"])
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return {row.get("headRefName", "") for row in data if row.get("headRefName")}


def is_protected(name: str) -> bool:
    return name in PROTECTED_EXACT or name.startswith(PROTECTED_PREFIXES)


def is_merged(remote: str, name: str, base_ref: str) -> bool:
    # Squash-aware: PRs here squash-merge, so --is-ancestor alone would miss the
    # default merge style and leave merged branches lingering until STALE_DELETE.
    return is_merged_into(_run, f"refs/remotes/{remote}/{name}", base_ref)


def classify(
    remote: str,
    base_ref: str,
    now: int,
    *,
    open_prs: set[str] | None,
) -> list[BranchVerdict]:
    verdicts: list[BranchVerdict] = []
    pr_lookup_failed = open_prs is None
    prs = open_prs or set()
    for name, ts in remote_branches(remote, now):
        age_days = max(0, (now - ts) // 86400)
        if is_protected(name):
            verdicts.append(BranchVerdict(name, "PROTECTED", age_days, "protected branch", ts))
            continue
        if is_merged(remote, name, base_ref):
            verdicts.append(
                BranchVerdict(name, "MERGED", age_days, "already merged into base ref", ts)
            )
            continue
        if name in prs:
            verdicts.append(BranchVerdict(name, "ACTIVE", age_days, "has an open PR", ts))
            continue
        if age_days < RECENT_DAYS:
            verdicts.append(
                BranchVerdict(name, "ACTIVE", age_days, f"commit younger than {RECENT_DAYS}d", ts)
            )
            continue
        if age_days < STALE_DAYS:
            verdicts.append(BranchVerdict(name, "ACTIVE", age_days, "not yet stale", ts))
            continue
        if age_days >= GRACE_DAYS and not pr_lookup_failed:
            verdicts.append(
                BranchVerdict(name, "STALE_DELETE", age_days, f"stale > {GRACE_DAYS}d, no PR", ts)
            )
        else:
            note = (
                "stale, awaiting grace"
                if not pr_lookup_failed
                else "stale (PR lookup failed; delete disabled)"
            )
            verdicts.append(BranchVerdict(name, "STALE_FLAG", age_days, note, ts))
    verdicts.sort(key=lambda v: (CATEGORIES.index(v.category), -v.age_days))
    return verdicts


def delete_branch(remote: str, name: str, *, dry_run: bool) -> str:
    cmd = ["git", "push", remote, "--delete", name]
    if dry_run:
        return "DRY-RUN: " + " ".join(cmd)
    proc = _run(cmd)
    return ("deleted " + name) if proc.returncode == 0 else f"FAILED {name}: {proc.stderr.strip()}"


def render_report(verdicts: list[BranchVerdict], *, now: int) -> str:
    counts = {c: 0 for c in CATEGORIES}
    for v in verdicts:
        counts[v.category] += 1
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now))
    lines = [
        ISSUE_MARKER,
        f"## Branch janitor — {stamp}",
        "",
        f"**{len(verdicts)} branches** · "
        + " · ".join(f"{c}: {counts[c]}" for c in CATEGORIES),
        "",
        "| Branch | Category | Age (d) | Reason |",
        "|---|---|---|---|",
    ]
    shown = [v for v in verdicts if v.category != "ACTIVE"]
    for v in shown[:200]:
        lines.append(f"| `{v.name}` | {v.category} | {v.age_days} | {v.reason} |")
    if len(shown) > 200:
        lines.append(f"| … | … | … | +{len(shown) - 200} more |")
    lines += [
        "",
        f"_Guardrails: protects main/release, open-PR branches, and commits < {RECENT_DAYS}d._",
        "_This run deletes nothing unless invoked with `--apply`; the scheduled "
        "workflow runs `apply-all`._",
    ]
    return "\n".join(lines)


def upsert_issue(body: str) -> str:
    """Create or update the rolling tracking issue. Best-effort via gh."""
    find = _run(
        ["gh", "issue", "list", "--state", "open", "--search", ISSUE_TITLE,
         "--limit", "5", "--json", "number,title"]
    )
    number = None
    if find.returncode == 0:
        try:
            for row in json.loads(find.stdout or "[]"):
                if row.get("title") == ISSUE_TITLE:
                    number = row.get("number")
                    break
        except json.JSONDecodeError:
            pass
    if number is None:
        proc = _run(["gh", "issue", "create", "--title", ISSUE_TITLE, "--body", body])
        return f"created issue: {proc.stdout.strip() or proc.stderr.strip()}"
    proc = _run(["gh", "issue", "edit", str(number), "--body", body])
    return f"updated issue #{number}: {'ok' if proc.returncode == 0 else proc.stderr.strip()}"


def main(argv: list[str]) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete MERGED + STALE_DELETE branches. Without this, report only.",
    )
    parser.add_argument(
        "--only-merged",
        action="store_true",
        help="With --apply, delete only MERGED branches (provably safe).",
    )
    parser.add_argument(
        "--issue", action="store_true", help="Write/update the rolling tracking issue via gh."
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of the table.")
    parser.add_argument(
        "--fetch", action="store_true", help="git fetch --prune before classifying."
    )
    args = parser.parse_args(argv)

    if args.fetch:
        _run(["git", "fetch", "--prune", args.remote])

    now = int(time.time())
    open_prs = open_pr_branches()
    verdicts = classify(args.remote, args.base_ref, now, open_prs=open_prs)

    if args.json:
        print(json.dumps([asdict(v) for v in verdicts], indent=2))
    else:
        print(render_report(verdicts, now=now))

    if args.issue:
        print("\n" + upsert_issue(render_report(verdicts, now=now)))

    if args.apply:
        targets = [v for v in verdicts if v.category == "MERGED"]
        if not args.only_merged:
            targets += [v for v in verdicts if v.category == "STALE_DELETE"]
        print(f"\n# Applying deletions: {len(targets)} branch(es)")
        for v in targets:
            print(delete_branch(args.remote, v.name, dry_run=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
