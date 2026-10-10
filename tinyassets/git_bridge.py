"""Thin subprocess wrapper around ``git`` for Phase 7.2.

Six primitives (``stage``, ``commit``, ``pull``, ``push``, ``open_pr``,
``has_uncommitted_changes``) plus ``is_enabled`` for capability detection.

All primitives return structured results. None raise — an uninstalled
``git`` binary, a missing repo, a network hiccup, or a timeout all come
back as ``ok=False`` with a human-readable ``error`` string. Phase 7.2
features must no-op cleanly when git isn't usable so dev/test paths that
don't want git aren't forced into it.

Concurrency: mutation primitives (``stage`` / ``commit`` / ``pull`` /
``push``) are serialized via a process-local :class:`threading.Lock`.
FastMCP may dispatch concurrent write handlers; two overlapping
``git commit`` calls would race otherwise.

Not in scope for G1: wiring this to the SqliteCachedBackend (that's G2),
the ``save_and_commit`` composite helpers (G3), repo-level config files
(G4), or conflict resolution (deferred to v2).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_ENABLED_CACHE: bool | None = None

# Timeouts — every subprocess call must set one so a hung remote doesn't
# freeze the MCP dispatcher. Local ops are fast; network ops get more.
_TIMEOUT_LOCAL = 15
_TIMEOUT_PUSH = 60
_TIMEOUT_PULL = 30


@dataclass
class CommitResult:
    ok: bool
    sha: str = ""
    message: str = ""
    error: str = ""


@dataclass
class PullResult:
    ok: bool
    pulled_commits: int = 0
    conflicts: list[str] | None = None
    message: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        if self.conflicts is None:
            self.conflicts = []


@dataclass
class PushResult:
    ok: bool
    pushed_commits: int = 0
    message: str = ""
    error: str = ""


@dataclass
class PRResult:
    ok: bool
    url: str = ""
    mode: str = ""  # "gh" or "manual"
    message: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------


def _run(command, *, capture_output, text, check, timeout, cwd):
    """Keep owner git and its hooks inside the startup-admitted owner cell."""
    try:
        return _run_bounded(command, timeout=timeout, cwd=cwd)
    except (OSError, ValueError, RuntimeError):
        # Preserve the bridge's structured refusal contract; never retry as
        # the daemon. A capability probe outside owner storage means disabled.
        return subprocess.CompletedProcess(command, 126, '', 'owner git bridge refused')


def _run_bounded(command, *, timeout, cwd):
    from tinyassets.role_git import run

    if command[0] != 'git' or cwd is None:
        raise RuntimeError('owner git bridge requires an admitted git repository')
    # Only pathspecs after -- are paths. Commit messages, ref names and options
    # remain opaque, including strings which happen to contain host paths.
    args = list(command[1:])
    if '--' in args:
        boundary = args.index('--') + 1
        for index in range(boundary, len(args)):
            path = Path(args[index])
            if path.is_absolute():
                relative = path.relative_to(Path(cwd))
                if '..' in relative.parts:
                    raise PermissionError('git pathspec leaves its owner repository')
                args[index] = '/workspace/' + relative.as_posix()
    return run(args, cwd=cwd, options=(), timeout_s=timeout)


def is_enabled(repo_path: Path | None = None) -> bool:
    """Return True if ``git`` is usable from the current working tree.

    Checks:
    1. ``git`` binary is on PATH.
    2. ``git rev-parse --is-inside-work-tree`` inside ``repo_path`` (or CWD).

    Never cached across calls: the probe runs in the owner's git cell, so the
    answer is per-owner and per-repository, and one owner's refusal must not
    disable git for the next. Fail-open: when either check fails, callers
    should treat git features as no-ops rather than raising.
    """
    global _ENABLED_CACHE
    if shutil.which("git") is None:
        logger.info("git_bridge: git binary not found on PATH; disabling")
        _ENABLED_CACHE = False
        return False

    cwd = str(repo_path) if repo_path is not None else None
    try:
        result = _run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, check=False,
            timeout=_TIMEOUT_LOCAL, cwd=cwd,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.info("git_bridge: probe failed (%s); disabling", exc)
        _ENABLED_CACHE = False
        return False

    enabled = result.returncode == 0 and result.stdout.strip() == "true"
    if not enabled:
        logger.info(
            "git_bridge: not inside a git work tree (rc=%s); disabling",
            result.returncode,
        )
    _ENABLED_CACHE = enabled
    return enabled


def invalidate_cache() -> None:
    """Drop the cached ``is_enabled`` result. Test helper."""
    global _ENABLED_CACHE
    _ENABLED_CACHE = None


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def has_uncommitted_changes(path: Path, *, repo_path: Path | None = None) -> bool:
    """True if ``path`` has uncommitted changes relative to HEAD.

    Used by the backend to refuse overwriting a user's in-progress edit
    (see scope §3e). Returns False on any failure — safer to proceed and
    let a later conflict surface than to block on a probe error.
    """
    if not is_enabled(repo_path):
        return False
    cwd = str(repo_path) if repo_path is not None else None
    try:
        result = _run(
            ["git", "diff", "--quiet", "HEAD", "--", str(path)],
            capture_output=True, text=True, check=False,
            timeout=_TIMEOUT_LOCAL, cwd=cwd,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("has_uncommitted_changes(%s) probe failed: %s", path, exc)
        return False
    # rc 0 = clean, rc 1 = dirty, other = error. Treat errors as clean.
    return result.returncode == 1


def stage(path: Path, *, repo_path: Path | None = None) -> bool:
    """Stage ``path`` with ``git add``. Returns True on success."""
    if not is_enabled(repo_path):
        return False
    cwd = str(repo_path) if repo_path is not None else None
    with _LOCK:
        try:
            result = _run(
                ["git", "add", "--", str(path)],
                capture_output=True, text=True, check=False,
                timeout=_TIMEOUT_LOCAL, cwd=cwd,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("git stage(%s) failed: %s", path, exc)
            return False
    if result.returncode != 0:
        logger.warning(
            "git add failed for %s (rc=%s): %s",
            path, result.returncode, result.stderr.strip(),
        )
        return False
    return True


def unstage(paths: list[Path], *, repo_path: Path | None = None) -> bool:
    """Unstage ``paths`` via ``git reset HEAD -- <paths>``.

    Used by the storage backend to roll back the index after a commit
    fails mid-flight: without this, the next caller's commit would
    sweep in our half-staged YAML. Returns True if every path was
    unstaged (or the repo has no HEAD yet — unborn-branch case).
    """
    if not is_enabled(repo_path) or not paths:
        return False
    cwd = str(repo_path) if repo_path is not None else None
    with _LOCK:
        try:
            result = _run(
                ["git", "reset", "HEAD", "--", *[str(p) for p in paths]],
                capture_output=True, text=True, check=False,
                timeout=_TIMEOUT_LOCAL, cwd=cwd,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("git unstage failed: %s", exc)
            return False
    if result.returncode != 0:
        # Fresh repo without HEAD still benefits from `git rm --cached`
        # to clear the index entry the add created.
        combined = (result.stdout + result.stderr).lower()
        if "ambiguous argument 'head'" in combined or "unknown revision" in combined:
            with _LOCK:
                try:
                    rm_result = _run(
                        ["git", "rm", "--cached", "--", *[str(p) for p in paths]],
                        capture_output=True, text=True, check=False,
                        timeout=_TIMEOUT_LOCAL, cwd=cwd,
                    )
                except (subprocess.TimeoutExpired, OSError) as exc:
                    logger.warning("git unstage (rm --cached) failed: %s", exc)
                    return False
            return rm_result.returncode == 0
        logger.warning(
            "git unstage rc=%s: %s", result.returncode, result.stderr.strip(),
        )
        return False
    return True


def commit(
    message: str,
    author: str | None = None,
    *,
    paths: list[Path] | None = None,
    repo_path: Path | None = None,
) -> CommitResult:
    """Stage ``paths`` (if given) and commit with ``message`` and ``author``.

    ``author`` is passed opaquely to ``git commit --author=…``. When
    ``None``, :func:`tinyassets.identity.git_author` resolves it from the
    current process env (``TINYASSETS_GIT_AUTHOR`` or
    ``UNIVERSE_SERVER_USER``). Explicit string callers keep their
    existing behavior.

    If there is nothing to commit, returns ``ok=False`` with a distinctive
    message rather than emitting an empty commit.
    """
    if author is None:
        # Lazy import dodges any future circular edge if identity.py
        # grows dependencies on storage-adjacent modules.
        from tinyassets.identity import git_author
        author = git_author()
    if not is_enabled(repo_path):
        return CommitResult(ok=False, error="git not enabled")
    cwd = str(repo_path) if repo_path is not None else None
    with _LOCK:
        if paths:
            for p in paths:
                rc_add = _run(
                    ["git", "add", "--", str(p)],
                    capture_output=True, text=True, check=False,
                    timeout=_TIMEOUT_LOCAL, cwd=cwd,
                )
                if rc_add.returncode != 0:
                    return CommitResult(
                        ok=False,
                        error=f"git add failed for {p}: {rc_add.stderr.strip()}",
                    )
        try:
            result = _run(
                ["git", "commit", f"--author={author}", "-m", message],
                capture_output=True, text=True, check=False,
                timeout=_TIMEOUT_LOCAL, cwd=cwd,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return CommitResult(ok=False, error=f"git commit failed: {exc}")
        if result.returncode != 0:
            combined = (result.stdout + result.stderr).lower()
            if "nothing to commit" in combined or "no changes added" in combined:
                return CommitResult(
                    ok=False, message="nothing to commit", error="nothing to commit",
                )
            return CommitResult(
                ok=False,
                error=f"git commit failed (rc={result.returncode}): "
                      f"{result.stderr.strip() or result.stdout.strip()}",
            )
        sha_result = _run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
            timeout=_TIMEOUT_LOCAL, cwd=cwd,
        )
        sha = sha_result.stdout.strip() if sha_result.returncode == 0 else ""
    return CommitResult(ok=True, sha=sha, message=message)


def pull(
    *,
    remote: str = "origin",
    branch: str = "",
    repo_path: Path | None = None,
) -> PullResult:
    """Run ``git pull``. On merge conflict, aborts to keep the tree clean."""
    if not is_enabled(repo_path):
        return PullResult(ok=False, error="git not enabled")
    cwd = str(repo_path) if repo_path is not None else None
    with _LOCK:
        head_before = _head_sha(cwd)
        cmd = ["git", "pull", "--no-rebase", remote]
        if branch:
            cmd.append(branch)
        try:
            result = _run(
                cmd,
                capture_output=True, text=True, check=False,
                timeout=_TIMEOUT_PULL, cwd=cwd,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return PullResult(ok=False, error=f"git pull failed: {exc}")

        combined = result.stdout + result.stderr
        if result.returncode != 0:
            conflicts = _unmerged_paths(cwd)
            if conflicts:
                # Abort the half-applied merge so the next write doesn't
                # land on a broken tree. v2 will add resolve_conflict.
                _run(
                    ["git", "merge", "--abort"],
                    capture_output=True, text=True, check=False,
                    timeout=_TIMEOUT_LOCAL, cwd=cwd,
                )
                return PullResult(
                    ok=False, conflicts=conflicts,
                    message="merge conflict; pull aborted",
                    error="merge conflict; pull aborted",
                )
            return PullResult(
                ok=False,
                error=f"git pull failed (rc={result.returncode}): "
                      f"{result.stderr.strip() or result.stdout.strip()}",
            )

        head_after = _head_sha(cwd)
        pulled = _count_commits(cwd, head_before, head_after) if head_before and head_after else 0
    return PullResult(ok=True, pulled_commits=pulled, message=combined.strip())


def push(
    *,
    remote: str = "origin",
    branch: str = "HEAD",
    repo_path: Path | None = None,
) -> PushResult:
    """Run ``git push``. Returns structured result, never raises."""
    if not is_enabled(repo_path):
        return PushResult(ok=False, error="git not enabled")
    cwd = str(repo_path) if repo_path is not None else None
    with _LOCK:
        try:
            result = _run(
                ["git", "push", remote, branch],
                capture_output=True, text=True, check=False,
                timeout=_TIMEOUT_PUSH, cwd=cwd,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return PushResult(ok=False, error=f"git push failed: {exc}")
    if result.returncode != 0:
        return PushResult(
            ok=False,
            error=f"git push failed (rc={result.returncode}): "
                  f"{result.stderr.strip() or result.stdout.strip()}",
        )
    return PushResult(ok=True, message=(result.stdout + result.stderr).strip())


def open_pr(
    title: str,
    body: str,
    *,
    branch: str = "",
    repo_path: Path | None = None,
) -> PRResult:
    """Open a PR via ``gh pr create``. Falls back to a manual-PR payload.

    The host reports ``gh`` is not installed; in that case this returns
    ``PRResult(ok=False, mode="manual", ...)`` with a human message the
    MCP caller can render directly.
    """
    if shutil.which("gh") is None:
        return PRResult(
            ok=False, mode="manual",
            message="gh CLI not installed; create PR manually",
            error="gh CLI not installed; create PR manually",
        )
    cwd = str(repo_path) if repo_path is not None else None
    cmd = ["gh", "pr", "create", "--title", title, "--body", body]
    if branch:
        cmd.extend(["--head", branch])
    try:
        result = _run(
            cmd,
            capture_output=True, text=True, check=False,
            timeout=_TIMEOUT_PUSH, cwd=cwd,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return PRResult(ok=False, mode="gh", error=f"gh pr create failed: {exc}")
    if result.returncode != 0:
        return PRResult(
            ok=False, mode="gh",
            error=f"gh pr create failed (rc={result.returncode}): "
                  f"{result.stderr.strip() or result.stdout.strip()}",
        )
    url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    return PRResult(ok=True, mode="gh", url=url, message=result.stdout.strip())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _head_sha(cwd: str | None) -> str:
    try:
        result = _run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
            timeout=_TIMEOUT_LOCAL, cwd=cwd,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _count_commits(cwd: str | None, before: str, after: str) -> int:
    try:
        result = _run(
            ["git", "rev-list", "--count", f"{before}..{after}"],
            capture_output=True, text=True, check=False,
            timeout=_TIMEOUT_LOCAL, cwd=cwd,
        )
    except (subprocess.TimeoutExpired, OSError):
        return 0
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip() or "0")
    except ValueError:
        return 0


def _unmerged_paths(cwd: str | None) -> list[str]:
    """Files with merge conflict markers, per ``git diff --name-only --diff-filter=U``."""
    try:
        result = _run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            capture_output=True, text=True, check=False,
            timeout=_TIMEOUT_LOCAL, cwd=cwd,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]
