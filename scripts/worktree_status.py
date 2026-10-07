"""Report git worktree pickup state for multi-provider TinyAssets sessions.

This complements scripts/claim_check.py. claim_check owns STATUS.md file
collisions; this script owns persistent local directories created by
``git worktree add``.
"""

from __future__ import annotations

import argparse
import io
import json
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from git_squash_merge import is_merged_into  # noqa: E402  (sibling-script import)

STALE_AFTER_SECONDS = 24 * 60 * 60
MAIN_BRANCHES = {"main", "master", "production"}
REQUIRED_PURPOSE_FIELDS = (
    "Purpose:",
    "Provider:",
    "Branch:",
    "Base ref:",
    "Issue/PR:",   # wt.py's scaffold; a legacy "STATUS/Issue/PR:" still matches
    "Spec refs:",
    "Ship condition:",
    "Abandon condition:",
    "Pickup hints:",
    "Memory refs:",
    "Related implications:",
    "Idea feed refs:",
)
STATE_PRIORITY = {
    "MISSING": 0,
    "DIRTY_CURRENT_NEEDS_PURPOSE": 0,
    "DIRTY_CURRENT_CHECKOUT": 1,
    "IN_FLIGHT_NEEDS_PURPOSE": 2,
    "IN_FLIGHT": 3,
    "NEEDS_PURPOSE": 4,
    "PURPOSE_INCOMPLETE": 5,
    "ORPHANED": 6,
    "NEEDS_PR_OR_STATUS": 7,
    "ACTIVE_LANE": 8,
    "PARKED_DRAFT": 9,
    "READY_TO_REMOVE": 10,
}

STATE_MAP_NOTE = (
    "# state map: ACTIVE_LANE/PARKED_DRAFT are canonical lanes; "
    "DIRTY_*/IN_FLIGHT*/NEEDS_*/PURPOSE_INCOMPLETE/ORPHANED/MISSING/"
    "READY_TO_REMOVE are action-required intermediates. "
    "Idea/reference-only lanes live in ideas/*.md or _PURPOSE.md idea refs."
)


def _force_utf8_stdio() -> None:
    """Keep Windows console encodings from crashing on non-ASCII output."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("_", "-")
        if encoding == "utf-8":
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
                setattr(
                    sys,
                    stream_name,
                    io.TextIOWrapper(
                        buffer,
                        encoding="utf-8",
                        errors="replace",
                        newline="\n",
                        line_buffering=True,
                    ),
                )
            except (AttributeError, ValueError, OSError):
                pass


@dataclass
class WorktreeEntry:
    path: str
    head: str | None
    branch_ref: str | None
    detached: bool = False

    @property
    def slug(self) -> str:
        return Path(self.path).name

    @property
    def branch(self) -> str:
        if not self.branch_ref:
            return "(detached HEAD)" if self.detached else "(unknown)"
        prefix = "refs/heads/"
        if self.branch_ref.startswith(prefix):
            return self.branch_ref[len(prefix) :]
        return self.branch_ref


@dataclass
class WorktreeStatus:
    slug: str
    path: str
    branch: str
    head: str | None
    state: str
    age_hours: float | None
    upstream: str
    dirty: bool
    current: bool
    live_safety: str
    status_ref: bool
    purpose_exists: bool
    purpose_missing_fields: list[str]
    purpose: str
    memory_refs: list[str]
    action: str


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(
            ["git", *args],
            127,
            "",
            str(exc),
        )


def parse_porcelain(text: str) -> list[WorktreeEntry]:
    entries: list[WorktreeEntry] = []
    current: dict[str, str | bool] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if current:
                entries.append(_entry_from_dict(current))
                current = None
            continue
        if line.startswith("worktree "):
            if current:
                entries.append(_entry_from_dict(current))
            current = {"path": line[len("worktree ") :]}
            continue
        if current is None:
            continue
        if line.startswith("HEAD "):
            current["head"] = line[len("HEAD ") :]
        elif line.startswith("branch "):
            current["branch_ref"] = line[len("branch ") :]
        elif line == "detached":
            current["detached"] = True
    if current:
        entries.append(_entry_from_dict(current))
    return entries


def _entry_from_dict(data: dict[str, str | bool]) -> WorktreeEntry:
    return WorktreeEntry(
        path=str(data.get("path", "")),
        head=str(data["head"]) if data.get("head") else None,
        branch_ref=str(data["branch_ref"]) if data.get("branch_ref") else None,
        detached=bool(data.get("detached", False)),
    )


def collect_worktrees(repo: Path) -> list[WorktreeEntry]:
    result = run_git(["worktree", "list", "--porcelain"], repo)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "git worktree list failed")
    return parse_porcelain(result.stdout)


def matches_provider(entry: WorktreeEntry, provider: str) -> bool:
    """Return whether an inventory entry matches the existing provider filter."""
    needle = provider.lower()
    return (
        needle in entry.slug.lower()
        or needle in entry.branch.lower()
        or needle in entry.path.lower()
    )


def build_status(
    entry: WorktreeEntry,
    now: float | None = None,
    *,
    repo: Path | None = None,
    status_text: str = "",
) -> WorktreeStatus:
    now = time.time() if now is None else now
    path = Path(entry.path)
    if not path.exists():
        return WorktreeStatus(
            slug=entry.slug,
            path=entry.path,
            branch=entry.branch,
            head=entry.head,
            state="MISSING",
            age_hours=None,
            upstream="missing",
            dirty=False,
            current=False,
            live_safety=_live_safety(entry.branch),
            status_ref=_has_status_ref(status_text, entry),
            purpose_exists=False,
            purpose_missing_fields=list(REQUIRED_PURPOSE_FIELDS),
            purpose="-",
            memory_refs=[],
            action=(
                "Worktree path missing; log sweep/prune only after extracting "
                "useful branch or PR ideas."
            ),
        )
    dirty = _is_dirty(path)
    age = _last_commit_age_hours(path, now)
    upstream = _upstream_state(path, entry)
    purpose_exists, purpose = _purpose(path)
    missing_fields = _purpose_missing_fields(path)
    memory_refs = _memory_refs(path)
    current = _is_current_worktree(path, repo)
    status_ref = _has_status_ref(status_text, entry)
    live_safety = _live_safety(entry.branch)
    fully_merged = _is_fully_merged(path, entry)
    state = classify(
        dirty=dirty,
        purpose_exists=purpose_exists,
        purpose_complete=not missing_fields,
        age_hours=age,
        upstream=upstream,
        current=current,
        status_ref=status_ref,
        branch=entry.branch,
        fully_merged=fully_merged,
    )
    action = _action_for_state(
        state=state,
        current=current,
        live_safety=live_safety,
        status_ref=status_ref,
    )
    return WorktreeStatus(
        slug=entry.slug,
        path=entry.path,
        branch=entry.branch,
        head=entry.head,
        state=state,
        age_hours=age,
        upstream=upstream,
        dirty=dirty,
        current=current,
        live_safety=live_safety,
        status_ref=status_ref,
        purpose_exists=purpose_exists,
        purpose_missing_fields=missing_fields,
        purpose=purpose,
        memory_refs=memory_refs,
        action=action,
    )


def classify(
    *,
    dirty: bool,
    purpose_exists: bool,
    purpose_complete: bool = True,
    age_hours: float | None,
    upstream: str,
    current: bool = False,
    status_ref: bool = False,
    branch: str = "",
    fully_merged: bool = False,
) -> str:
    if dirty and current and not purpose_exists:
        return "DIRTY_CURRENT_NEEDS_PURPOSE"
    if dirty and current:
        return "DIRTY_CURRENT_CHECKOUT"
    if dirty and not purpose_exists:
        return "IN_FLIGHT_NEEDS_PURPOSE"
    if dirty:
        return "IN_FLIGHT"
    old = age_hours is not None and age_hours >= 24
    if fully_merged and not _is_main_branch(branch):
        return "READY_TO_REMOVE"
    if upstream == "gone":
        return "READY_TO_REMOVE"
    if not purpose_exists and old and upstream in {"none", "gone", "detached"}:
        return "ORPHANED"
    if not purpose_exists:
        return "NEEDS_PURPOSE"
    if not purpose_complete:
        return "PURPOSE_INCOMPLETE"
    if purpose_exists and not old and upstream in {"tracking", "ahead-behind"}:
        return "ACTIVE_LANE" if status_ref else "PARKED_DRAFT"
    if purpose_exists and old:
        return "ACTIVE_LANE" if status_ref else "PARKED_DRAFT"
    if status_ref:
        return "ACTIVE_LANE"
    if upstream in {"none", "detached"} and not _is_main_branch(branch):
        return "NEEDS_PR_OR_STATUS"
    return "PARKED_DRAFT"


def _is_dirty(path: Path) -> bool:
    result = run_git(["status", "--short"], path)
    return bool(result.stdout.strip()) if result.returncode == 0 else False


def _last_commit_age_hours(path: Path, now: float) -> float | None:
    result = run_git(["log", "-1", "--format=%ct"], path)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return max(0.0, (now - int(result.stdout.strip())) / 3600)


def _upstream_state(path: Path, entry: WorktreeEntry) -> str:
    if entry.detached or not entry.branch_ref:
        return "detached"
    branch = entry.branch
    upstream = run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], path)
    if upstream.returncode != 0:
        return "none"
    track = run_git(["for-each-ref", "--format=%(upstream:track)", f"refs/heads/{branch}"], path)
    if "[gone]" in track.stdout:
        return "gone"
    if track.stdout.strip():
        return "ahead-behind"
    return "tracking"


def _is_fully_merged(path: Path, entry: WorktreeEntry) -> bool:
    if entry.detached or not entry.head or not entry.branch_ref:
        return False
    branch = entry.branch
    if _is_main_branch(branch):
        return False
    # Squash-aware: PRs here squash-merge, so a plain --is-ancestor check would
    # leave clean squash-merged lanes mislabelled instead of READY_TO_REMOVE.
    # is_merged_into prepends "git"; run_git takes args without it, so drop a[0].
    def _run(a: list[str]) -> subprocess.CompletedProcess[str]:
        return run_git(list(a)[1:], path)

    for base in _merge_base_candidates(path):
        if is_merged_into(_run, entry.head, base):
            return True
    return False


def _merge_base_candidates(path: Path) -> list[str]:
    candidates = [
        "refs/remotes/origin/main",
        "refs/remotes/origin/master",
        "refs/remotes/origin/production",
        "refs/heads/main",
        "refs/heads/master",
        "refs/heads/production",
    ]
    existing: list[str] = []
    for candidate in candidates:
        result = run_git(["rev-parse", "--verify", "--quiet", candidate], path)
        if result.returncode == 0 and result.stdout.strip():
            existing.append(candidate)
    return existing


def _purpose(path: Path) -> tuple[bool, str]:
    purpose = path / "_PURPOSE.md"
    if not purpose.exists():
        return False, "-"
    for line in purpose.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip(" -")
        if stripped and stripped not in {"---", "# Purpose"}:
            return True, stripped[:90]
    return True, "(empty _PURPOSE.md)"


def _purpose_missing_fields(path: Path) -> list[str]:
    purpose = path / "_PURPOSE.md"
    if not purpose.exists():
        return list(REQUIRED_PURPOSE_FIELDS)
    text = purpose.read_text(encoding="utf-8", errors="replace").lower()
    return [field for field in REQUIRED_PURPOSE_FIELDS if field.lower() not in text]


def _memory_refs(path: Path) -> list[str]:
    purpose = path / "_PURPOSE.md"
    if not purpose.exists():
        return []
    refs: list[str] = []
    in_memory_refs = False
    for line in purpose.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip(" -")
        lowered = stripped.lower()
        if lowered.startswith("memory refs:") or lowered.startswith("prior-provider memory refs:"):
            in_memory_refs = True
            if stripped.partition(":")[2].strip():
                refs.append(stripped)
            continue
        if in_memory_refs and _looks_like_purpose_heading(stripped):
            in_memory_refs = False
        if in_memory_refs and stripped:
            refs.append(stripped)
        elif _known_memory_ref(stripped):
            refs.append(stripped)
    return refs


def _looks_like_purpose_heading(text: str) -> bool:
    return any(text.lower().startswith(field.lower()) for field in REQUIRED_PURPOSE_FIELDS)


def _known_memory_ref(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            ".agents/activity.log",
            ".claude/agent-memory/",
            ".claude/projects/",
            ".codex/",
            ".cursor/",
            ".cursorrules",
            "copilot memory",
            "jules memory",
        )
    )


def _is_current_worktree(path: Path, repo: Path | None) -> bool:
    if repo is None:
        return False
    try:
        return path.resolve() == repo.resolve()
    except OSError:
        return False


def _is_main_branch(branch: str) -> bool:
    return branch.lower() in MAIN_BRANCHES


def _live_safety(branch: str) -> str:
    if branch == "(detached HEAD)":
        return "DETACHED"
    if _is_main_branch(branch):
        return "LIVE_MAIN"
    return "ISOLATED_UNTIL_MERGED"


def _has_status_ref(status_text: str, entry: WorktreeEntry) -> bool:
    haystack = status_text.lower()
    if not haystack:
        return False
    needles = {entry.slug.lower(), Path(entry.path).as_posix().lower()}
    branch = entry.branch.lower()
    if branch and branch not in MAIN_BRANCHES and not branch.startswith("("):
        needles.add(branch)
    return any(needle and needle in haystack for needle in needles)


def _action_for_state(
    *,
    state: str,
    current: bool,
    live_safety: str,
    status_ref: bool,
) -> str:
    current_warning = " Do not switch this dirty checkout to main." if current else ""
    if state == "DIRTY_CURRENT_NEEDS_PURPOSE":
        return "Add _PURPOSE.md before continuing; finish or isolate work." + current_warning
    if state == "DIRTY_CURRENT_CHECKOUT":
        return (
            "Dirty current lane; finish, commit, or park before branch changes."
            + current_warning
        )
    if state == "IN_FLIGHT_NEEDS_PURPOSE":
        return "Dirty lane without durable memory; add _PURPOSE.md immediately."
    if state == "IN_FLIGHT":
        return "Dirty lane; pickup only after reading purpose/STATUS/PR context."
    if state == "NEEDS_PURPOSE":
        return "Add _PURPOSE.md or sweep after extracting useful ideas."
    if state == "PURPOSE_INCOMPLETE":
        return "Complete _PURPOSE.md template fields before pickup or PR."
    if state == "ORPHANED":
        return "Extract useful ideas, log abandoned/swept, then remove worktree."
    if state == "NEEDS_PR_OR_STATUS":
        return "Promote to STATUS if active, or push branch and open draft PR."
    if state == "ACTIVE_LANE":
        return "Pickup through STATUS Files/Depends/Status; do not bypass gates."
    if state == "PARKED_DRAFT":
        prefix = "Confirm draft PR/body has blockers, gates, memory refs."
        if not status_ref:
            return prefix
        return "STATUS-backed parked lane; confirm PR before foldback."
    if state == "READY_TO_REMOVE":
        return "Extract ideas, then `python scripts/wt.py done` (the PR is the record)."
    if live_safety == "LIVE_MAIN":
        return "Main worktree; production-impacting changes require live gates."
    return "Inspect before pickup."


def render_table(statuses: list[WorktreeStatus]) -> str:
    headers = (
        "SLUG",
        "STATE",
        "CUR",
        "DIRTY",
        "LIVE",
        "UPSTREAM",
        "BRANCH",
        "AGE_H",
        "MEM",
        "ACTION",
        "PURPOSE",
    )
    rows = [
        (
            s.slug[:30],
            s.state,
            "yes" if s.current else "-",
            "yes" if s.dirty else "-",
            s.live_safety,
            s.upstream,
            s.branch[:44],
            "-" if s.age_hours is None else f"{s.age_hours:.1f}",
            str(len(s.memory_refs)),
            s.action,
            s.purpose,
        )
        for s in statuses
    ]
    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(cell)) for w, cell in zip(widths, row)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [
        STATE_MAP_NOTE,
        fmt.format(*headers),
        fmt.format(*("-" * w for w in widths)),
    ]
    lines.extend(fmt.format(*row) for row in rows)
    return "\n".join(lines)


def sweep_commands(statuses: list[WorktreeStatus]) -> list[str]:
    commands: list[str] = []
    for status in statuses:
        if status.state not in {"ORPHANED", "READY_TO_REMOVE"}:
            continue
        commands.append(f"git worktree remove {shlex.quote(status.path)}")
        if status.state == "READY_TO_REMOVE" and _branch_can_be_deleted(status.branch):
            # -D, not -d: READY_TO_REMOVE means proven-merged (squash-aware), but
            # `git branch -d` is ancestor-based and would refuse a squash-merged
            # branch, leaving it behind when the printed commands are run.
            commands.append(f"git branch -D {shlex.quote(status.branch)}")
    return commands


def _branch_can_be_deleted(branch: str) -> bool:
    return bool(branch and not branch.startswith("(") and not _is_main_branch(branch))


def main(argv: list[str]) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    parser.add_argument("--provider", help="Filter by provider/branch/slug substring.")
    parser.add_argument(
        "--sweep-orphaned",
        action="store_true",
        help=(
            "Print cleanup commands for ORPHANED entries and fully merged "
            "READY_TO_REMOVE branches. Dry-run only."
        ),
    )
    args = parser.parse_args(argv)

    repo = Path.cwd()
    status_text = _read_status_text(repo)
    entries = collect_worktrees(repo)
    if args.provider:
        entries = [
            entry
            for entry in entries
            if matches_provider(entry, args.provider)
        ]
    statuses = [
        build_status(entry, repo=repo, status_text=status_text)
        for entry in entries
    ]
    statuses.sort(key=lambda s: (STATE_PRIORITY.get(s.state, 99), s.slug))

    if args.json:
        print(json.dumps([asdict(status) for status in statuses], indent=2))
    else:
        print(render_table(statuses))

    if args.sweep_orphaned:
        print("\n# Dry-run orphan/removable sweep commands")
        for command in sweep_commands(statuses):
            print(command)
        print("# Prefer `python scripts/wt.py done`; the PR is the record of the lane.")
    return 0


def _read_status_text(repo: Path) -> str:
    path = repo / "STATUS.md"
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
