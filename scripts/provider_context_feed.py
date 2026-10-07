#!/usr/bin/env python3
"""Surface provider memories, ideas, and automation notes for worktree lanes.

This is a coordination scanner, not a backlog writer. It finds candidate
context that a provider should consider before planning, building, reviewing,
or folding work back through GitHub.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

TEXT_SUFFIXES = {
    ".json",
    ".log",
    ".md",
    ".mdc",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}

MAX_CANDIDATES_PER_FILE = 4
MAX_CANDIDATES_PER_SOURCE = 10
WORKTREE_PURPOSE_CANDIDATES_PER_FILE = 8
WORKTREE_PURPOSE_CANDIDATES_PER_SOURCE = 24
WORKTREE_PURPOSE_LABEL_PRIORITY: tuple[tuple[str, int], ...] = (
    ("purpose:", 0),
    ("status/issue/pr:", 1),
    ("ship condition:", 2),
    ("abandon condition:", 3),
    ("blocker", 4),
    ("review gate", 4),
    ("memory refs:", 5),
    (".claude/agent-memory/", 5),
    (".agents/activity.log", 5),
    ("related implications:", 6),
    ("idea feed refs:", 7),
    ("pickup hints:", 8),
    ("branch:", 9),
    ("base ref:", 10),
    ("provider:", 11),
)

SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "task",
        re.compile(
            r"\b(TODO|FIXME|follow[- ]?up|next action|needs?|blocked|pending|"
            r"host-action|host-decision|must|should|blockers?|ship condition|"
            r"abandon condition|pickup hints?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "idea",
        re.compile(
            r"\b(idea|proposal|promotion|frontier|implications?|adopt|adapt|"
            r"defer|watch|research-derived|idea feed refs?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "memory",
        re.compile(
            r"\b(memory|remember|learned|correction|feedback|preference|"
            r"recalled|memory refs?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "coordination",
        re.compile(
            r"\b(worktree|ADR-\d+|PR|pull request|branch|Depends|"
            r"claim|review gate|fold-back|active lane|parked draft|orphaned|"
            r"abandoned|swept|live-safe|live deploy|dirty checkout)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "automation",
        re.compile(
            r"\b(hook|automation|SessionStart|UserPromptSubmit|PostToolUse|"
            r"scheduled|recurring|suggested tasks|autopilot)\b",
            re.IGNORECASE,
        ),
    ),
)

PHASE_SIGNALS: dict[str, set[str]] = {
    "all": {"task", "idea", "memory", "coordination", "automation"},
    "session-start": {"task", "idea", "memory", "coordination", "automation"},
    "claim": {"task", "idea", "memory", "coordination"},
    "plan": {"task", "idea", "memory", "coordination"},
    "build": {"task", "memory", "coordination", "automation"},
    "review": {"task", "memory", "coordination", "automation"},
    "foldback": {"task", "idea", "memory", "coordination", "automation"},
    "memory-write": {"task", "idea", "memory", "coordination"},
}

SOURCE_PRIORITY: dict[str, int] = {
    "worktree-purpose": 0,
    "worktree-inventory": 10,
    "idea-pipeline": 20,
    "research-artifact": 30,
    "provider-memory": 35,
    "provider-automation": 40,
    "brain-concept": 42,
    "brain-plan": 43,
    "vetted-specs": 45,
    "provider-routing": 55,
    "provider-config": 60,
    "exec-plan": 65,
    "idea-feed": 70,
    "activity-log": 90,
}

SIGNAL_PRIORITY: dict[str, int] = {
    "coordination": 0,
    "task": 1,
    "idea": 2,
    "memory": 3,
    "automation": 4,
}

PROVIDER_FAMILIES: dict[str, tuple[str, ...]] = {
    "claude": ("claude", "anthropic"),
    "codex": ("codex", "openai"),
    "cursor": ("cursor",),
    "cowork": ("cowork",),
}


@dataclass(frozen=True)
class SourceSpec:
    path: str
    provider: str
    source_type: str
    recursive: bool = True


@dataclass(frozen=True)
class FeedCandidate:
    provider: str
    source_type: str
    path: str
    line: int
    signal: str
    text: str


def default_specs(root: Path) -> list[SourceSpec]:
    specs = [
        SourceSpec("_PURPOSE.md", "shared", "worktree-purpose", False),
        SourceSpec(".claude/agent-memory", "claude", "provider-memory"),
        SourceSpec(".claude/projects", "claude", "provider-memory"),
        SourceSpec(".claude/hooks", "claude", "provider-automation"),
        SourceSpec(".claude/settings.json", "claude", "provider-automation", False),
        SourceSpec(".codex", "codex", "provider-config"),
        SourceSpec(".cursor", "cursor", "provider-config"),
        SourceSpec(".cursorrules", "cursor", "provider-config", False),
        SourceSpec("CLAUDE.md", "claude", "provider-routing", False),
        SourceSpec("CODEX.md", "codex", "provider-routing", False),
        SourceSpec(".agents/activity.log", "shared", "activity-log", False),
        SourceSpec("ideas/INBOX.md", "shared", "idea-feed", False),
        SourceSpec("ideas/PIPELINE.md", "shared", "idea-pipeline", False),
        SourceSpec("pages/concepts", "shared", "brain-concept"),
        SourceSpec("pages/plans", "shared", "brain-plan"),
        SourceSpec("docs/audits", "shared", "research-artifact"),
        SourceSpec("docs/exec-plans/active", "shared", "exec-plan"),
        SourceSpec("docs/vetted-specs.md", "shared", "vetted-specs", False),
    ]
    specs.extend(
        SourceSpec(str(path), "shared", "worktree-purpose", False)
        for path in _worktree_purpose_paths(root)
    )
    return specs


def _worktree_purpose_paths(root: Path) -> list[Path]:
    candidates: set[Path] = set()

    porcelain = _git_worktree_porcelain(root)
    branch_map = _parse_worktree_branches(porcelain)
    for worktree_path in branch_map:
        candidates.add(worktree_path / "_PURPOSE.md")

    if root.parent.is_dir():
        for pattern in ("wf-*/_PURPOSE.md", "TinyAssets*/_PURPOSE.md"):
            candidates.update(root.parent.glob(pattern))
    candidates.update((root / ".claude" / "worktrees").glob("*/_PURPOSE.md"))
    candidates.update((root / "origin").glob("*/_PURPOSE.md"))

    existing = sorted(path.resolve() for path in candidates if path.is_file())
    return _drop_dead_lane_purposes(existing, branch_map=branch_map, root=root)


def _git_worktree_porcelain(root: Path) -> str:
    """Raw output of `git worktree list --porcelain` (empty string on failure)."""
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def _parse_worktree_branches(porcelain: str) -> dict[Path, str]:
    """Parse `git worktree list --porcelain` into a {worktree_path: branch_name} dict.

    The porcelain format emits one block per worktree, blank-line separated. Each block
    starts with a `worktree <path>` line and (for branch worktrees) carries a
    `branch refs/heads/<name>` line. Detached worktrees omit the branch line; we skip those.
    """
    out: dict[Path, str] = {}
    current: Path | None = None
    for raw in porcelain.splitlines():
        line = raw.rstrip()
        if line.startswith("worktree "):
            current = Path(line[len("worktree "):])
        elif line.startswith("branch ") and current is not None:
            ref = line[len("branch "):].strip()
            branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
            try:
                key = current.resolve()
            except OSError:
                key = current
            out[key] = branch
            current = None
        elif not line:
            current = None
    return out


def _merged_branch_set(root: Path, base: str = "origin/main") -> set[str] | None:
    """Short names of local branches fully merged into `base`, via ONE git call.

    Replaces a per-branch `git merge-base --is-ancestor` loop (one subprocess
    spawn per worktree branch) with a single `git branch --merged`. The old shape
    grew O(N) subprocess spawns with worktree count and pushed the SessionStart
    feed toward its hook timeout exactly when the repo was busiest (most lanes).

    Used to identify dead lanes — a worktree whose branch is already an ancestor
    of `base` is landed work, not a place to look for new work. Returns None on
    any git error so callers fail safe (surface candidates rather than hide them).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "branch", "--merged", base,
             "--format=%(refname:short)"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _drop_dead_lane_purposes(
    paths: list[Path],
    *,
    branch_map: dict[Path, str],
    root: Path,
) -> list[Path]:
    """Filter _PURPOSE.md candidates to only those belonging to live (unmerged) worktrees.

    Worktrees whose branch is fully an ancestor of origin/main represent landed work and
    should not surface as actionable lanes for the lead. Set
    `TINYASSETS_FEED_INCLUDE_DEAD_LANES=1` to bypass this filter (useful for archaeology).
    """
    import os as _os

    if _os.environ.get("TINYASSETS_FEED_INCLUDE_DEAD_LANES", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return paths
    if not branch_map:
        return paths
    merged = _merged_branch_set(root)
    if merged is None:
        # git failed -> fail safe: surface everything rather than hide live lanes.
        return paths
    dead_worktrees = {
        worktree_path
        for worktree_path, branch in branch_map.items()
        if branch in merged
    }
    if not dead_worktrees:
        return paths
    live: list[Path] = []
    for path in paths:
        ancestor = _closest_worktree_ancestor(path, branch_map.keys())
        if ancestor is not None and ancestor in dead_worktrees:
            continue
        live.append(path)
    return live


def _closest_worktree_ancestor(path: Path, worktrees: Iterable[Path]) -> Path | None:
    """Pick the deepest worktree path that contains `path`."""
    try:
        resolved = path.resolve()
    except OSError:
        return None
    best: Path | None = None
    best_len = -1
    for wt in worktrees:
        try:
            wt_resolved = wt if wt.is_absolute() else wt.resolve()
        except OSError:
            continue
        try:
            resolved.relative_to(wt_resolved)
        except ValueError:
            continue
        length = len(str(wt_resolved))
        if length > best_len:
            best = wt_resolved
            best_len = length
    return best


def _is_text_path(path: Path) -> bool:
    return path.name == "_PURPOSE.md" or path.suffix.lower() in TEXT_SUFFIXES


def _iter_files(root: Path, spec: SourceSpec) -> Iterable[Path]:
    spec_path = Path(spec.path)
    base = spec_path if spec_path.is_absolute() else root / spec.path
    if base.is_file():
        if _is_text_path(base):
            yield base
        return
    if not base.is_dir():
        return

    if spec.source_type == "provider-memory":
        patterns = ("*.md",)
    elif spec.source_type == "worktree-purpose":
        patterns = ("_PURPOSE.md",)
    else:
        patterns = tuple(f"*{suffix}" for suffix in TEXT_SUFFIXES)

    iterator: Iterable[Path]
    if spec.recursive:
        files: list[Path] = []
        for pattern in patterns:
            files.extend(base.rglob(pattern))
        iterator = _sort_recent_first(set(files))
    else:
        files = []
        for pattern in patterns:
            files.extend(base.glob(pattern))
        iterator = _sort_recent_first(set(files))

    for path in iterator:
        rel_parts = _parts_for_skip_check(path, root)
        if rel_parts & SKIP_PARTS:
            continue
        if path.is_file() and _is_text_path(path):
            yield path


def _parts_for_skip_check(path: Path, root: Path) -> set[str]:
    try:
        return set(path.resolve().relative_to(root).parts)
    except (OSError, ValueError):
        return set(path.parts)


def _sort_recent_first(paths: Iterable[Path]) -> list[Path]:
    def key(path: Path) -> tuple[float, str]:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0
        return (-mtime, path.as_posix())

    return sorted(paths, key=key)


def _signal_for_line(line: str) -> str | None:
    for signal, pattern in SIGNALS:
        if pattern.search(line):
            return signal
    return None


def _safe_console_text(text: str) -> str:
    return text.encode("ascii", errors="replace").decode("ascii")


def _safe_read_lines(path: Path, max_bytes: int) -> list[str]:
    try:
        if path.stat().st_size > max_bytes:
            return []
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _provider_scope(provider: str) -> set[str]:
    normalized = provider.strip().lower()
    if normalized in {"", "*", "all", "any"}:
        return {"all", "shared"}

    scope = {normalized, "shared"}
    matched_family = False
    for family, tokens in PROVIDER_FAMILIES.items():
        if any(token in normalized for token in tokens):
            scope.add(family)
            matched_family = True

    if not matched_family:
        scope.add("all")
    return scope


def _spec_visible(spec: SourceSpec, scope: set[str], *, phase: str) -> bool:
    if phase == "claim" and spec.source_type == "provider-memory":
        return True
    return "all" in scope or spec.provider in scope or spec.provider == "shared"


def _rank_candidate(
    spec: SourceSpec,
    path: Path,
    signal: str,
    line: int,
    text: str,
) -> tuple[int, int, float, int, str]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0
    return (
        SOURCE_PRIORITY.get(spec.source_type, 80),
        _line_priority(spec.source_type, signal, text),
        -mtime,
        line,
        path.as_posix(),
    )


def _line_priority(source_type: str, signal: str, text: str) -> int:
    if source_type == "worktree-purpose":
        lower = text.lower()
        for label, priority in WORKTREE_PURPOSE_LABEL_PRIORITY:
            if label in lower:
                return priority
        return 20
    return SIGNAL_PRIORITY.get(signal, 9)


def collect_candidates(
    root: Path,
    *,
    provider: str = "all",
    phase: str = "all",
    max_bytes: int = 250_000,
    limit: int | None = 80,
) -> list[FeedCandidate]:
    root = root.resolve()
    scope = _provider_scope(provider)
    wanted_signals = PHASE_SIGNALS.get(phase, PHASE_SIGNALS["all"])
    ranked: list[tuple[tuple[int, int, float, int, str], FeedCandidate]] = []
    seen: set[tuple[str, int, str]] = set()

    for spec in default_specs(root):
        if not _spec_visible(spec, scope, phase=phase):
            continue
        for path in _iter_files(root, spec):
            try:
                rel = path.resolve().relative_to(root).as_posix()
            except ValueError:
                rel = path.as_posix()
            for number, line in enumerate(_safe_read_lines(path, max_bytes), start=1):
                stripped = _safe_console_text(" ".join(line.strip().split()))
                if not stripped or len(stripped) < 4:
                    continue
                signal = _signal_for_line(stripped)
                if signal is None or signal not in wanted_signals:
                    continue
                key = (rel, number, stripped)
                if key in seen:
                    continue
                seen.add(key)
                candidate = FeedCandidate(
                    provider=spec.provider,
                    source_type=spec.source_type,
                    path=rel,
                    line=number,
                    signal=signal,
                    text=stripped[:240],
                )
                ranked.append((_rank_candidate(spec, path, signal, number, stripped), candidate))

    selected: list[FeedCandidate] = []
    per_file: dict[str, int] = {}
    per_source: dict[str, int] = {}
    for _, candidate in sorted(ranked, key=lambda item: item[0]):
        count = per_file.get(candidate.path, 0)
        per_file_limit = (
            WORKTREE_PURPOSE_CANDIDATES_PER_FILE
            if candidate.source_type == "worktree-purpose"
            else MAX_CANDIDATES_PER_FILE
        )
        source_count = per_source.get(candidate.source_type, 0)
        per_source_limit = (
            WORKTREE_PURPOSE_CANDIDATES_PER_SOURCE
            if candidate.source_type == "worktree-purpose"
            else MAX_CANDIDATES_PER_SOURCE
        )
        if count >= per_file_limit:
            continue
        if source_count >= per_source_limit:
            continue
        per_file[candidate.path] = count + 1
        per_source[candidate.source_type] = source_count + 1
        selected.append(candidate)
        if limit is not None and len(selected) >= limit:
            break
    return selected


def render_text(candidates: list[FeedCandidate], *, phase: str, provider: str) -> str:
    header = [
        f"# provider_context_feed --phase {phase} --provider {provider}",
        "",
        "Use this feed at lifecycle checkpoints: claim, plan, build, review,",
        "foldback, and after writing provider memories or idea artifacts.",
        "Promote relevant candidates into worktree/PR state before",
        "building. ideas/INBOX.md remains idea-feed context, not build authority.",
        "",
    ]
    if not candidates:
        return "\n".join(header + ["No provider-context candidates found."])

    rows = []
    for item in candidates:
        rows.append(
            f"- {item.provider}/{item.source_type} {item.path}:{item.line} "
            f"[{item.signal}] {item.text}"
        )
    return "\n".join(header + rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--provider", default="all")
    parser.add_argument(
        "--phase",
        default="all",
        choices=sorted(PHASE_SIGNALS),
        help="Lifecycle checkpoint to filter for.",
    )
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--max-bytes", type=int, default=250_000)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--fail-on-candidates",
        action="store_true",
        help="Exit 2 when candidates are present. Use only for strict hooks.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    candidates = collect_candidates(
        args.root,
        provider=args.provider,
        phase=args.phase,
        max_bytes=args.max_bytes,
        limit=args.limit,
    )
    if args.as_json:
        print(json.dumps([asdict(item) for item in candidates], indent=2))
    else:
        print(render_text(candidates, phase=args.phase, provider=args.provider))
    return 2 if args.fail_on_candidates and candidates else 0


if __name__ == "__main__":
    raise SystemExit(main())
