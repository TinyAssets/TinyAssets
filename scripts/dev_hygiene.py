#!/usr/bin/env python3
"""Automatic disk hygiene for the Windows dev box — inventory first, remove only
what is provably disposable.

Why this exists: on 2026-09-26 the dev box's C: drive reached 0 bytes free and
every agent lane broke. None of it was project data. It was accumulated agent
scratch: 126 pytest ``--basetemp`` directories holding 7.9 GB, ~25 git worktrees
of this repo, Docker build cache, and repo scratch folders. The production
droplet already self-cleans (count-based image retention in
``scripts/daemon_image_retention.py``, the ``disk_watch.py`` timer); the dev side
had nothing, so the founder was asked about it "every so often" instead. Founder
directive 2026-09-26: *disk cleaning should be an automatic part of the
architecture, not something I'm asked about every so often.*

Five classes, each with its own proof of disposability:

``basetemp``
    Directories directly under the OS temp root whose name matches an
    agent-convention prefix AND whose contents have pytest's numbered-dir shape,
    untouched for ``--min-age-hours``. A directory with NO agent prefix is a
    candidate only when it holds pytest's ``tmp_path`` dirs named for tests in
    this repo's suite (or ``popen-gw*N`` roots of them) — lanes also pick bare
    names like ``orunb``.
``worktree``
    Git worktrees **of this repository only**, taken from ``git worktree list
    --porcelain``. Removed only when clean of tracked *and* ignored content,
    idle, and content-merged into ``origin/main`` — or, for a detached HEAD,
    when that commit is on a remote-tracking ref.
    A lane refused only because it holds work (dirty, unique ignored files,
    commits on no remote) is **preserved, then removed** once it is idle 48h and
    finished — PR merged or closed, or no PR and no commit newer than 7 days:
    its work goes to a local-only ``refs/preserved/<name>-<date>`` commit and
    its ignored files to a sha256-manifested copy. Never pushed; a lane whose
    preservation cannot be verified, or holds over 50 MB of unique data, is kept.
``docker``
    Unused images and build cache older than ``--min-age-hours``; stopped
    containers and unused volumes only with explicit disposable and created-at
    labels. No forced removal, no remote engines, no unlabeled volume deletion.
``scratch``
    A closed allowlist of this repo's own scratch directory names, only when git
    confirms the path is ignored and it is older than ``--min-age-days``.
``toolcache``
    Package-manager download caches, cleared by the owning tool's own command
    (``pip cache purge``, ``npm cache clean --force``) and located by asking
    that tool. Never a directory this script picks. uv's is only reported: a
    linked environment can depend on it.

Everything else is KEPT, with a reason. **Every unknown is a KEEP**: an
undecidable git query, a directory shape the collector does not recognise, and
an inventory that exceeds its entry budget all fail closed. That is Hard Rule 13
("Inventory before you destroy") expressed as code — the 2026-08-26 incident was
a checkout that *looked* like stale cruft and held 4,711 lines of unique
research, so a clean ``git status`` is not on its own a licence to delete.

Modes
-----
``--dry-run``   (default) print the inventory with sizes; touch nothing.
``--apply``     remove the REMOVE set and append every removal to the log.
``--if-low-disk GB``
                apply only when free space is below GB; otherwise report.
``--escalate-below GB``
                after the pass, if free space is still below GB, print a
                concrete escalation block and exit 3.

Exit codes
----------
0   Nothing to do, or the pass finished and free space is acceptable.
2   The environment could not be inventoried at all (bad --repo, no git).
3   Escalation: the disposable set cannot bring free space above the threshold.

What runs it, without anyone asking
-----------------------------------
``.claude/hooks/dev_hygiene_hook.py`` (SessionStart) inventories all five classes,
cleans disposable resources, and injects an escalation when free space is under
``TINYASSETS_DEV_HYGIENE_FLOOR_GB`` (40 GB by default). The hourly unelevated
``TinyAssets-DevHygiene`` scheduled task also runs the full pass under disk
pressure. The hook surfaces that task's recent escalation too. Neither can fail
a session; ``TINYASSETS_DEV_HYGIENE_DISABLE`` no-ops the hook.

The two things it will NOT resolve alone (exit 3 names them)
-----------------------------------------------------------
* ACL-locked temp dirs, from a sandbox agent pointing ``--basetemp``/``TMPDIR``
  under a restricted token: reported ``acl_locked_needs_elevation``, cleared with an
  ELEVATED ``powershell -ExecutionPolicy Bypass -File
  scripts/clear_sandbox_temp_dirs.ps1 -Apply``. Prevention lives in
  ``tests/conftest.py``, which refuses a temp root inside the repo.
* Lanes needing a decision -- still in flight (open PR, or a no-PR lane with
  recent commits) or holding more unique data than the preserve cap. Finished
  lanes that merely hold work are preserved and removed without asking.

Reuse, so two tools cannot disagree
-----------------------------------
``scripts/wt.py sweep`` stays the interactive reaper. This script reuses its
``_archive_purpose`` (an unpublished ``_PURPOSE.md`` reaches
``.git/tinyassets-worktrees.log`` before anything is removed) and
``git_squash_merge.is_merged_into`` for the squash-aware merge proof, and adds the
three gates ``wt.py`` lacks: ignored-content, idleness, commits-on-no-remote.

Links, on every platform: a link contributes nothing to a size and is never
descended into. ``entry.is_dir(follow_symlinks=False)`` is True for a Windows
junction, and a POSIX symlink's own ``lstat`` size is its target-path length --
neither is content in this tree. ``remove_path`` refuses a link handed to it
directly. Git writes loose objects read-only, so the remover chmod-sweeps and
retries; a later failure reports PARTIAL removal, because ``rmtree`` deletes as it
walks. Status parsing uses ``--porcelain -z``.

Stdlib only. Runs from any cwd.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import os
import re
import shutil
import stat as stat_mod
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from git_squash_merge import is_merged_into  # noqa: E402  (sibling-script import)

CLASSES = ("basetemp", "worktree", "docker", "scratch", "toolcache")
_DOCKER_HOST: str | None = None

# Temp-root directory names the agents actually produce. Measured from the repo's
# own review docs (docs/reviews/2026-09-*.md): ta-pt-*, ta-pt2, ta-rev-*,
# ta-root-*, ta-finq, ta-retirement-final-*, tinyassets-review-*, plus pytest's
# own default root. A prefix match alone never authorizes removal — see
# looks_like_pytest_tree.
BASETEMP_PREFIXES = ("ta-", "pytest-of-", "pytest-", "tinyassets-review-", "tinyassets-test-")

# A DRIVE root (C:\) is also a basetemp root here: lanes put short basetemps at
# C:\ta-<lane>N to stay under MAX_PATH, and nothing swept them — 34 were sitting
# there on 2026-09-26, some from July. Deliberately narrower than the temp-root set:
# a drive root holds system directories, so only the agents' own `ta-` convention
# is even a candidate, top level only.
DRIVE_ROOT_PREFIXES = ("ta-",)

# pytest's numbered-dir scheme: "<slug><N>" for tmp_path dirs, "pytest-<N>" under
# pytest-of-<user>, and "garbage-<uuid>" for its own deferred cleanup.
_NUMBERED_DIR = re.compile(r".*\d+\Z")

# What an UNPREFIXED temp dir must hold to be a candidate at all: pytest's own
# per-test dirs or xdist's per-worker roots. On 2026-09-28 lanes had left 140 such
# dirs (``orunb``, ``orset*``, ``ct_x1``...) holding ~3.8 GB that no prefix rule
# could see. A ``test_<word>N`` NAME is not provenance (Codex, PR #4089, P1:
# ``recovery/test_import0/manuscript.md``), so a per-test dir must also be named
# for a test that exists in this repo's suite -- see ``is_suite_tmp_dir``.
_XDIST_WORKER = re.compile(r"popen-gw\d+\Z")
_TEST_DEF = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+(test\w*)[ \t]*\(", re.MULTILINE)
# pytest's tmp_path: the node name with non-word characters replaced by "_",
# cut to this many characters, then a number.
_TMP_PATH_NAME_MAX = 30
# Files the TinyAssets suite writes at a basetemp root. Accepted only beside a
# pytest session child — an exact app filename, not an extension.
PYTEST_ROOT_FILES = frozenset({".tinyassets.db", ".tinyassets.db-wal", ".tinyassets.db-shm"})

MAIN_BRANCHES = frozenset({"main", "master", "production"})

# Ignored paths inside a worktree that carry no unique work. Anything ignored and
# NOT on this list keeps the worktree: that is the Hard Rule 13 guard, and it is
# the one `git worktree remove` does not have (it decides cleanliness with
# `git status --porcelain`, which omits ignored files entirely).
DISPOSABLE_IGNORED = (
    ".venv/",
    "venv/",
    "env/",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    "node_modules/",
    ".svelte-kit/",
    "dist/",
    "build/",
    ".egg-info/",
    # Next.js build output for the site previews (WebSite/site-react/.gitignore).
    # Only `next build`/`next dev` write these; 1.5 GB was held by three preview
    # lanes on 2026-09-28.
    ".next/",
    ".next-build/",
    # Per-session hook telemetry for loop detection: events.jsonl plus one
    # keep-working-<session-uuid>.json per session. Regenerated on demand, scoped
    # to a session that has ended, and never a work product — verified by reading
    # a lane's copy on 2026-09-26. Without this, nearly every Codex lane is held
    # back by machine state it wrote about itself.
    ".agents/supervisor/",
)
# Ignored FILES with no unique content. Matched against the whole relative path
# for a root-anchored name and against the final component otherwise — never as a
# substring or a bare prefix. `_PURPOSE.md` is root-only because `wt.py`'s archive
# only preserves the root copy, so a nested one would be accepted as disposable
# and then never archived (Codex round 1, P0).
DISPOSABLE_IGNORED_FILES_ROOT = ("_PURPOSE.md", "junit.xml")
DISPOSABLE_IGNORED_BASENAMES = (".DS_Store", "Thumbs.db", "next-env.d.ts")
# `out/` (Next's static export) is NOT accepted, not even the site's own copy: the
# export copies `public/` verbatim, and nothing tells a copied file from one written
# there by hand (Codex, PR #4089, P1: `WebSite/site-react/out/review-notes.md`).
# Compiled artifacts only. `*.db`/`*.db-wal`/`*.db-shm` were here and are NOT:
# this repo ignores `*.db` for the SQLite mirror of the YAML catalog, but the same
# pattern covers a user's own local database, and Codex round 1 reproduced a
# `research.db` being accepted as disposable. An extension is not a provenance.
DISPOSABLE_IGNORED_GLOBS = ("*.pyc", "*.pyo")

# `git status --ignored=matching` collapses a wholly-ignored directory into ONE
# entry, so accepting the entry says nothing about what is inside it (Codex round
# 1, answer 4). Tool-owned caches above need no content check — nothing but the
# tool writes them. `.agents/supervisor/` is repo state, so its contents are
# verified against the filenames its producers actually emit
# (`scripts/supervisor.py`, and a since-removed keep-working hook): one
# `events.jsonl`, one `seen.json`, and `keep-working-<session-uuid>.json`.
# Anything else in there keeps the worktree.
DISPOSABLE_DIR_CONTENT_RULES: dict[str, tuple[str, ...]] = {
    ".agents/supervisor": ("events.jsonl", "seen.json", "keep-working-*.json"),
}

# This repo's own scratch directory names. A closed list on purpose: `.codex-worktrees/`
# holds sandbox-owned INDEPENDENT repos and `output/`, `universes/`, `logs/`,
# `data-room/`, `.secrets/` hold real local state, so none of them appear here.
# `.tmp` and `.review` were here and are NOT: a generic name plus ignore status
# plus age proves nothing about provenance, and Codex round 1 reproduced a
# ten-day-old `.tmp/research.md` being removed. Every name left is one only a
# test run or an agent tool creates.
SCRATCH_NAMES = (
    "codex-tmp",
    ".pytest-tmp",
    ".codex-test-tmp",
    ".workflow-test-data",
)
SCRATCH_GLOBS = (".codex-scratch-*", ".codex-*.txt", "codex-scratch-*")

# KEEP reasons that are the system working as designed, not something the founder
# has to decide. Everything else lands in the escalation list.
EXPECTED_KEEPS = frozenset(
    {
        "primary_checkout",
        "in_use_by_this_process",
        "in_use_or_recent",
        "recently_active",
        "contains_cwd",
        "protected_branch",
        "path_missing",
        "nothing_reclaimable",
        "docker_engine_not_running",
        "not_git_ignored",
        "recent",
        "not_inventoried",
        "deferred_to_next_pass",
        "acl_locked_needs_elevation",  # summarized on its own line, with the fix
        "tool_not_installed",
    }
)

# Fail closed rather than spend unbounded time measuring one tree.
MAX_TREE_ENTRIES = 200_000
DEFAULT_LOG = Path(".claude") / "logs" / "dev-hygiene.log"


class Undecidable(Exception):
    """Evidence unavailable. Never authorizes removal — the caller must KEEP."""


class AclLocked(Undecidable):
    """Windows denied access outright — needs an elevated clear, not a retry.

    Its own type because the remedy is different: a sandbox agent that pointed
    ``--basetemp``/``TMPDIR`` at a path under a restricted token leaves a
    directory the interactive user cannot read, list, or delete. Reported as
    ``acl_locked_needs_elevation`` with the elevated command, not folded into the
    generic "could not measure it" bucket.
    """


@dataclass
class Item:
    """One reclaimable candidate and the verdict on it."""

    kind: str
    path: str
    size_bytes: int
    verdict: str  # "REMOVE" | "KEEP"
    reason: str
    detail: str = ""
    # Class-specific payload the remover needs. Named fields rather than parsing
    # it back out of ``detail``: a human string is not a machine contract.
    branch: str = ""  # worktree class: the local branch to delete
    prune_flag: str = ""  # docker class: the keep-budget flag this CLI has
    docker_age_hours: float = 6.0
    tool: str = ""  # toolcache class: the tool whose own command clears it
    head: str = ""  # worktree class: HEAD at inventory, re-checked before preserving
    idle_hours: float | None = None  # worktree class: measured idleness, when known

    @property
    def removable(self) -> bool:
        return self.verdict == "REMOVE"


@dataclass
class Report:
    items: list[Item] = field(default_factory=list)
    free_before_gb: float = 0.0
    free_after_gb: float = 0.0
    applied: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(i.size_bytes for i in self.items if i.removable)


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #


def run(args: list[str], cwd: Path | None = None, timeout: float = 30.0):
    """Run a command, never raising. Callers treat a non-zero rc as undecidable."""
    if args[0] == "docker" and _DOCKER_HOST:
        args = ["docker", "--host", _DOCKER_HOST, *args[1:]]
    try:
        return subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(args, 127, "", str(exc))


def git_ok(args: list[str], cwd: Path, timeout: float = 30.0) -> str:
    """Run git and return stdout, or raise Undecidable. Fail-closed by default."""
    proc = run(["git", *args], cwd=cwd, timeout=timeout)
    if proc.returncode != 0:
        raise Undecidable(
            f"git {' '.join(args)} -> rc={proc.returncode} {proc.stderr.strip()[:200]}"
        )
    return proc.stdout


def free_gb(path: Path) -> float:
    return shutil.disk_usage(str(path)).free / (1024**3)


def is_reparse_point(info: os.stat_result) -> bool:
    """True for a Windows reparse point (junction or symlink); False off Windows.

    ``entry.is_dir(follow_symlinks=False)`` returns **True** for a junction, so it
    is not on its own a no-follow guard. Symlinks need privilege on this host and
    junctions do not, so the junction is the case that actually occurs here.
    """
    attrs = getattr(info, "st_file_attributes", 0)
    return bool(attrs & getattr(stat_mod, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def is_link(info: os.stat_result) -> bool:
    """True for anything whose content lives somewhere else, on any platform.

    One predicate for both mechanisms so the walk behaves identically everywhere:
    a POSIX symlink (``S_ISLNK``) and a Windows junction or symlink (a reparse
    point) are the same fact — "this entry names another directory".
    """
    return stat_mod.S_ISLNK(info.st_mode) or is_reparse_point(info)


def tree_stats(root: Path, budget: int = MAX_TREE_ENTRIES) -> tuple[int, float]:
    """Return ``(total_bytes, newest_mtime)`` for a directory tree.

    Raises ``Undecidable`` when the tree exceeds ``budget`` entries or cannot be
    read — an unmeasurable tree is kept rather than guessed at.

    **One rule for every link, on every platform: it contributes nothing and is
    never descended into.** ``entry.is_dir(follow_symlinks=False)`` returns True for
    a Windows junction, so the walk used to cross into the target and count its
    bytes here; and a POSIX symlink's own ``lstat`` size is the length of its target
    path, which is not content inside this tree either. Both returns are
    load-bearing — size ranks the escalation, newest mtime is the worktree idleness
    gate — so counting either would be a lie about a different directory.

    An earlier version raised ``Undecidable`` on any reparse point. That was
    unnecessary once nothing is counted or followed, and it cost real coverage: it
    made 2 worktrees and 7 temp dirs permanently un-inventoriable on this box, and
    it would have refused every POSIX tree holding a ``.venv/bin`` symlink.
    ``shutil.rmtree`` also does **not** delete through a nested junction —
    ``shutil._rmtree_islink`` tests ``IO_REPARSE_TAG_MOUNT_POINT`` (Python 3.14),
    and a 2026-09-26 probe confirmed the target's contents survive removal of the
    parent — so the remaining link guard lives at the deletion boundary in
    ``remove_path``, which refuses a link handed to it directly.
    """
    total = 0
    newest = 0.0
    seen = 0
    stack = [root]
    try:
        root_info = os.stat(root, follow_symlinks=False)
    except PermissionError as exc:
        raise AclLocked(f"access denied on {root}: {exc}") from exc
    except OSError as exc:
        raise Undecidable(f"cannot stat {root}: {exc}") from exc
    if is_link(root_info):
        raise Undecidable(f"{root} is itself a link; this tool sizes real directories only")
    newest = root_info.st_mtime
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    seen += 1
                    if seen > budget:
                        raise Undecidable(f"{root} exceeds {budget} entries")
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if is_link(info):
                            continue  # counts nothing, and never descend through it
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                            newest = max(newest, info.st_mtime)
                            continue
                    except FileNotFoundError:
                        continue  # raced with a delete; it contributes nothing
                    except PermissionError as exc:
                        # Same class as a denied root, so it gets the same answer and
                        # the same elevated-fix pointer.
                        raise AclLocked(f"access denied on {entry.path}: {exc}") from exc
                    except OSError as exc:
                        # Anything else is an answer we do not have. The recursive
                        # newest mtime is the worktree idleness gate, so skipping
                        # an unreadable entry would silently under-report activity
                        # (Codex round 1, P2).
                        raise Undecidable(f"cannot stat {entry.path}: {exc}") from exc
                    total += info.st_size
                    newest = max(newest, info.st_mtime)
        except PermissionError as exc:
            raise AclLocked(f"access denied on {current}: {exc}") from exc
        except OSError as exc:
            raise Undecidable(f"cannot read {current}: {exc}") from exc
    return total, newest


ELEVATED_CLEAR = (
    "powershell -ExecutionPolicy Bypass -File scripts/clear_sandbox_temp_dirs.ps1 -Apply"
)


def keep_for(kind: str, path, exc: Undecidable, size: int = 0) -> Item:
    """Turn an undecidable measurement into the KEEP it must become."""
    if isinstance(exc, AclLocked):
        return Item(
            kind, str(path), size, "KEEP", "acl_locked_needs_elevation", f"needs: {ELEVATED_CLEAR}"
        )
    return Item(kind, str(path), size, "KEEP", "not_inventoriable", str(exc))


def human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def contains(parent: Path, child: Path) -> bool:
    return parent == child or parent in child.parents


# --------------------------------------------------------------------------- #
# (a) pytest basetemp directories
# --------------------------------------------------------------------------- #


def classify_temp_dir(path: Path) -> str:
    """Return ``"pytest"``, ``"acl_locked"``, or ``"unrecognized"``.

    A name prefix is a hint, not proof: something else could be called ``ta-…``,
    and on this box several ``ta-*`` directories are whole stale repo checkouts
    kept as audit oracles. Accepted shapes are pytest's own: the
    ``pytest-of-<user>`` root, a child directory using pytest's ``<slug><N>``
    numbered scheme, its ``.lock`` file, its ``garbage-*`` deferred-cleanup
    links, or an empty directory (``pytest --basetemp=X`` wipes and recreates X
    at session start, so an empty X holds zero bytes of anything).

    **Every** child must be one of those shapes. Accepting on the first match was
    enough for Codex round 1 to get ``ta-research/`` removed by adding a
    ``chapter1/`` next to a unique ``manuscript.md``: one plausible-looking child
    vouched for its siblings. A single unrecognised sibling now keeps the
    directory, and ``pytest-of-*`` is checked the same way rather than trusted on
    its name.

    ``acl_locked`` is its own answer, not a shrug: a sandbox agent that pointed
    ``--basetemp`` at the temp root under a restricted token leaves a directory
    the interactive user cannot read, list, or delete. Those need an elevated
    ``scripts/clear_sandbox_temp_dirs.ps1 -Apply``, so they belong in the
    escalation list rather than lumped in with shapes we simply do not know.
    """
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if not _is_pytest_artifact(entry):
                    return "unrecognized"
            return "pytest"  # every child vouched for, or the directory is empty
    except PermissionError:
        return "acl_locked"
    except OSError:
        return "unrecognized"


def _is_pytest_artifact(entry: os.DirEntry) -> bool:
    """Whether one entry inside a temp root is something pytest itself created."""
    name = entry.name
    if name in {".lock", "pytest-current"} or name.startswith("garbage-"):
        return True
    try:
        is_dir = entry.is_dir(follow_symlinks=False)
    except OSError:
        return False
    if is_dir:
        # tmp_path dirs ("test_foo0"), pytest-of-<user>'s "pytest-<N>" roots, and
        # the per-session numbered dirs underneath them all end in a digit.
        return bool(_NUMBERED_DIR.match(name))
    # pytest leaves "<name>-current" links beside its numbered dirs.
    return name.endswith("-current")


def suite_test_names(repo: Path) -> frozenset[str]:
    """Every ``def test_*`` name in ``repo/tests``: what this suite's tmp dirs are called."""
    names: set[str] = set()
    for path in (repo / "tests").rglob("*.py"):
        try:
            names.update(_TEST_DEF.findall(path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return frozenset(names)


def is_suite_tmp_dir(name: str, test_names: frozenset[str]) -> bool:
    """Whether ``name`` is a ``tmp_path`` dir pytest made for a test in ``test_names``.

    pytest names it ``<node name, non-word chars -> "_", first 30 chars><N>``. A
    parametrized node ``test_x[a-b]`` becomes ``test_x_a_b_``, so a stem may also
    be a known name followed by ``_``. Every split of the trailing digits is
    tried, because a test name can itself end in a digit.
    """
    for cut in range(len(name) - 1, 0, -1):
        if not name[cut].isdigit():
            break
        stem = name[:cut]
        if len(stem) > _TMP_PATH_NAME_MAX:
            continue
        if stem in test_names:
            return True
        if len(stem) == _TMP_PATH_NAME_MAX and any(
            t[:_TMP_PATH_NAME_MAX] == stem for t in test_names
        ):
            return True
        if any(stem[j] == "_" and stem[:j] in test_names for j in range(len(stem))):
            return True
    return False


def is_pytest_session_dir(path: Path, test_names: frozenset[str]) -> bool:
    """Whether an UNPREFIXED directory is provably one session of this repo's suite.

    Stricter than ``classify_temp_dir`` because there is no name hint at all:
    every child directory must be a ``tmp_path`` dir of a test in this suite
    (``is_suite_tmp_dir``) or an xdist ``popen-gwN`` root holding only those, at
    least one must exist, and every file must be a pytest artifact or one of
    ``PYTEST_ROOT_FILES``. A bare ``chapter1/`` or an invented ``test_import0/``
    does not qualify. Unreadable means no.
    """
    found = False
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                name = entry.name
                if entry.is_dir(follow_symlinks=False):
                    if _XDIST_WORKER.match(name):
                        if not _only_suite_tmp_dirs(Path(entry.path), test_names):
                            return False
                    elif not is_suite_tmp_dir(name, test_names):
                        return False
                    found = True
                    continue
                if name in PYTEST_ROOT_FILES or name in {".lock", "pytest-current"}:
                    continue
                return False
    except OSError:
        return False
    return found


def _only_suite_tmp_dirs(worker: Path, test_names: frozenset[str]) -> bool:
    """An xdist worker root: suite ``tmp_path`` dirs and pytest's own files, nothing else."""
    with os.scandir(worker) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                if not is_suite_tmp_dir(entry.name, test_names):
                    return False
            elif entry.name not in PYTEST_ROOT_FILES | {".lock", "pytest-current"}:
                return False
    return True


def collect_basetemps(
    temp_root: Path,
    *,
    min_age_hours: float,
    now: float,
    prefixes: tuple[str, ...] = BASETEMP_PREFIXES,
    unprefixed_sessions: frozenset[str] | None = None,
) -> list[Item]:
    """Inventory one temp root. ``prefixes`` is narrower for a drive root.

    Called once per configured root. The OS temp root takes the full agent-prefix
    set; a **drive root** like ``C:\\`` takes ``ta-`` only, because lanes put short
    basetemps there to dodge MAX_PATH and a drive root also holds system
    directories that must never be candidates.

    ``unprefixed_sessions`` (OS temp root only) is this repo's test names; given,
    it also admits a directory of any name that ``is_pytest_session_dir`` proves
    is a session of this suite.
    """
    items: list[Item] = []
    here = Path.cwd().resolve()
    try:
        children = sorted(temp_root.iterdir())
    except OSError as exc:
        return [Item("basetemp", str(temp_root), 0, "KEEP", "temp_root_unreadable", str(exc))]

    for child in children:
        name = child.name
        session = False
        if not any(name.startswith(p) for p in prefixes):
            if unprefixed_sessions is None or child.is_symlink() or not child.is_dir():
                continue
            if (child / ".git").exists() or not is_pytest_session_dir(child, unprefixed_sessions):
                continue
            session = True
        # A checkout is never basetemp, whatever it is called. The shape gate below
        # would refuse it anyway, but saying so by name keeps a repo at a drive root
        # out of this class entirely — it belongs to the worktree class, which
        # applies the unique-work checks.
        if (child / ".git").exists():
            items.append(
                Item(
                    "basetemp",
                    str(child),
                    0,
                    "KEEP",
                    "git_checkout_not_basetemp",
                    "has a .git entry; the worktree class owns this path",
                )
            )
            continue
        try:
            if not child.is_dir() or child.is_symlink():
                continue
        except OSError:
            continue
        resolved = child.resolve()
        shape = "pytest" if session else classify_temp_dir(child)
        if shape == "acl_locked":
            items.append(keep_for("basetemp", child, AclLocked("cannot list it")))
            continue
        if shape != "pytest":
            # Sized anyway: an unrecognized directory is exactly what the founder
            # has to make a call on, and a concrete escalation needs its size.
            try:
                size, _ = tree_stats(child)
            except Undecidable:
                size = 0
            items.append(
                Item(
                    "basetemp",
                    str(child),
                    size,
                    "KEEP",
                    "unrecognized_shape",
                    "not a pytest temp tree",
                )
            )
            continue
        if contains(resolved, here):
            items.append(Item("basetemp", str(child), 0, "KEEP", "contains_cwd"))
            continue
        try:
            size, newest = tree_stats(child)
        except Undecidable as exc:
            items.append(keep_for("basetemp", child, exc))
            continue
        age_hours = max(0.0, (now - newest) / 3600.0)
        if age_hours < min_age_hours:
            items.append(
                Item(
                    "basetemp",
                    str(child),
                    size,
                    "KEEP",
                    "in_use_or_recent",
                    f"{age_hours:.1f}h old",
                )
            )
            continue
        items.append(
            Item(
                "basetemp",
                str(child),
                size,
                "REMOVE",
                "stale_pytest_basetemp",
                f"{age_hours:.1f}h old",
            )
        )
    return items


# --------------------------------------------------------------------------- #
# (b) git worktrees of THIS repo
# --------------------------------------------------------------------------- #


@dataclass
class Worktree:
    path: Path
    head: str
    branch: str  # "" when detached
    detached: bool
    locked: bool = False


def parse_worktrees(porcelain: str) -> list[Worktree]:
    out: list[Worktree] = []
    cur: dict[str, object] = {}

    def flush() -> None:
        if cur.get("path"):
            branch_ref = str(cur.get("branch") or "")
            out.append(
                Worktree(
                    path=Path(str(cur["path"])),
                    head=str(cur.get("head") or ""),
                    branch=branch_ref[len("refs/heads/") :]
                    if branch_ref.startswith("refs/heads/")
                    else "",
                    detached=bool(cur.get("detached")),
                    locked=bool(cur.get("locked")),
                )
            )

    for raw in porcelain.splitlines():
        line = raw.strip()
        if not line:
            flush()
            cur = {}
            continue
        if line.startswith("worktree "):
            flush()
            cur = {"path": line[len("worktree ") :]}
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD ") :]
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch ") :]
        elif line == "detached":
            cur["detached"] = True
        elif line == "locked" or line.startswith("locked "):
            cur["locked"] = True
    flush()
    return out


def is_disposable_ignored(rel: str) -> bool:
    """True when an ignored path inside a worktree carries no unique work.

    Matching is by **path component**, never by substring or bare prefix. The
    first version used ``norm.startswith(known)`` plus a ``"/" + known in
    "/" + norm`` substring test, and Codex round 1 reproduced three unique paths
    passing it: ``research.db`` (an extension is not a provenance),
    ``docs/_PURPOSE.md`` (accepted as disposable but only the root copy is ever
    archived), and ``_PURPOSE.md-git-credentials.txt`` (a prefix is not a
    filename). A component test refuses all three.
    """
    # Only a leading "./" is stripped. `str.lstrip("./")` would eat the leading
    # dot of every dotted path, turning ".ruff_cache/" into "ruff_cache/".
    norm = rel.replace("\\", "/")
    if norm.startswith("./"):
        norm = norm[2:]
    trimmed = norm.rstrip("/")
    if not trimmed:
        return False
    parts = trimmed.split("/")

    # A directory entry matches when it IS a component of the path, so
    # ".venv/", "a/.venv/" and "a/.venv/lib/x.py" all match while
    # ".venv-backup/" does not.
    for known in DISPOSABLE_IGNORED:
        segments = [s for s in known.strip("/").split("/") if s]
        if not segments:
            continue
        window = len(segments)
        if any(parts[i : i + window] == segments for i in range(len(parts) - window + 1)):
            return True

    if trimmed in DISPOSABLE_IGNORED_FILES_ROOT:  # root-anchored, exact
        return True
    if parts[-1] in DISPOSABLE_IGNORED_BASENAMES:  # exact final component
        return True
    return any(fnmatch.fnmatch(parts[-1], g) for g in DISPOSABLE_IGNORED_GLOBS)


def _status_entries(worktree: Path, *extra: str) -> list[tuple[str, str]]:
    """Parse ``git status --porcelain -z`` into ``(xy, path)`` pairs.

    NUL-delimited because git escapes and quotes a path with spaces or non-ASCII
    bytes in the newline form, and stripping the quotes leaves the escapes
    undecoded (Codex round 1, P2). Rename entries carry two NUL-separated paths;
    the origin path is returned as its own entry so neither half is lost.
    """
    raw = git_ok(["status", "--porcelain", "-z", "--untracked-files=all", *extra], worktree)
    fields = [f for f in raw.split("\0") if f]
    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if len(field) < 4 or field[2] != " ":
            continue  # not a status record; never silently treated as clean
        xy, path = field[:2], field[3:]
        entries.append((xy, path))
        if "R" in xy or "C" in xy:  # rename/copy: the next field is the origin
            if index < len(fields):
                entries.append((xy, fields[index]))
                index += 1
    return entries


def unexpected_dir_contents(worktree: Path, rel: str) -> list[str]:
    """Files under a content-ruled disposable directory that the rule does not allow.

    Raises ``Undecidable`` when the directory cannot be listed: an unreadable
    directory is not an empty one.
    """
    key = rel.replace("\\", "/").strip("/")
    allowed = DISPOSABLE_DIR_CONTENT_RULES.get(key)
    if allowed is None:
        return []
    root = worktree / key
    unexpected: list[str] = []
    try:
        for parent, _dirs, files in os.walk(root, onerror=_raise_walk_error):
            for name in files:
                if not any(fnmatch.fnmatch(name, pattern) for pattern in allowed):
                    unexpected.append(
                        str(Path(parent, name).relative_to(worktree)).replace("\\", "/")
                    )
    except OSError as exc:
        raise Undecidable(f"cannot list {root}: {exc}") from exc
    return unexpected


def _raise_walk_error(exc: OSError) -> None:
    raise exc


def unique_ignored_paths(worktree: Path) -> list[str]:
    """Ignored paths that are NOT provably disposable. Raises Undecidable on error."""
    found: list[str] = []
    for xy, path in _status_entries(worktree, "--ignored=matching"):
        if xy != "!!":
            continue
        if not is_disposable_ignored(path):
            found.append(path)
            continue
        found.extend(unexpected_dir_contents(worktree, path))
    return found


def dirty_paths(worktree: Path) -> list[str]:
    """Tracked modifications and non-ignored untracked paths. Raises Undecidable."""
    return [f"{xy} {path}" for xy, path in _status_entries(worktree) if xy != "!!"]


def unpushed_commits(worktree: Path, head: str) -> list[str]:
    """Commits reachable from HEAD but from no remote-tracking ref."""
    out = git_ok(["log", "--format=%h %s", head, "--not", "--remotes"], worktree)
    return [ln for ln in out.splitlines() if ln.strip()]


def pr_is_closed(branch: str, cwd: Path) -> bool | None:
    """``True``/``False`` for the branch's PR state, ``None`` when gh cannot say."""
    if not branch:
        return None
    proc = run(
        ["gh", "pr", "list", "--head", branch, "--state", "all", "--limit", "1", "--json", "state"],
        cwd=cwd,
        timeout=30,
    )
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not rows:
        return None
    return str(rows[0].get("state", "")).upper() in {"CLOSED", "MERGED"}


def collect_worktrees(
    repo: Path,
    *,
    now: float,
    idle_hours: float,
    deadline: float | None = None,
    base_ref: str = "refs/remotes/origin/main",
    pr_state_fn=None,
    open_pr_fn=None,
    preserve: PreservePolicy | None = None,
    pr_records_fn=None,
) -> list[Item]:
    """Inventory this repo's worktrees. Never looks outside ``git worktree list``.

    With a ``preserve`` policy, a lane the clean-removal gates refuse only because
    it holds work (dirty, unique ignored files, commits on no remote) becomes a
    ``preserve_then_remove`` candidate when it is long idle and finished — see
    ``preservable``. Nothing is preserved here; inventory never writes.
    """
    pr_state = pr_state_fn if pr_state_fn is not None else pr_is_closed
    try:
        porcelain = git_ok(["worktree", "list", "--porcelain"], repo)
        primary = Path(
            git_ok(["rev-parse", "--path-format=absolute", "--git-common-dir"], repo).strip()
        ).parent
    except Undecidable as exc:
        return [Item("worktree", str(repo), 0, "KEEP", "worktree_list_undecidable", str(exc))]

    # One liveness query for the whole pass. A branch with an OPEN PR is a lane
    # someone is still working, whatever its merge state looks like locally — and
    # with 115 removable worktrees the realistic failure is deleting the tree a
    # parallel builder is standing in. If gh cannot answer, the ENTIRE worktree
    # class is skipped: an unknown open-PR set is not a licence to remove any of
    # them (lead directive 2026-09-26).
    open_branches = (open_pr_fn if open_pr_fn is not None else open_pr_branches)(repo)
    if open_branches is None:
        return [
            Item(
                "worktree",
                str(repo),
                0,
                "KEEP",
                "open_pr_set_unknown",
                "gh could not list open PRs; the whole worktree class is skipped",
            )
        ]

    # Every PR state, once per pass, only when the preserve path is on. ``None``
    # (gh could not answer, or the listing hit its limit) turns the preserve path
    # off for the whole pass rather than reading "no PR" into an unknown.
    all_pr_records = None
    if preserve is not None:
        all_pr_records = (pr_records_fn if pr_records_fn is not None else pr_records_by_branch)(
            repo
        )

    here = Path.cwd().resolve()
    items: list[Item] = []
    entries = parse_worktrees(porcelain)
    for index, wt in enumerate(entries):
        if deadline is not None and time.monotonic() > deadline:
            for remaining in entries[index:]:
                items.append(Item("worktree", str(remaining.path), 0, "KEEP", "not_inventoried"))
            break
        item = _judge_worktree(
            wt,
            repo=repo,
            primary=primary,
            here=here,
            now=now,
            idle_hours=idle_hours,
            base_ref=base_ref,
            pr_state=pr_state,
            open_branches=open_branches,
        )
        if preserve is not None and item.verdict == "KEEP" and item.reason in PRESERVABLE_KEEPS:
            item = preservable(
                wt, item, policy=preserve, pr_records=all_pr_records, now=now, base_ref=base_ref
            )
        items.append(item)
    return items


def commits_after_push(worktree: Path, branch: str, head: str) -> list[str]:
    """Commits on ``head`` that its own remote-tracking ref does not have.

    The case this exists for: a branch squash-merges, then someone adds a commit
    locally. ``is_merged_into`` compares the branch's *cumulative* diff against the
    base, so a later commit touching only files the merge already changed can leave
    that comparison still true — and the commit would be destroyed with the
    worktree. Comparing the tip against ``refs/remotes/origin/<branch>`` answers it
    directly.

    Empty when there is no remote-tracking ref (nothing to compare against, and the
    unpushed-commits and merge gates already cover that shape) or when git cannot
    answer — the callers that matter have already established the branch is merged,
    and this is an extra refusal, not the only one.
    """
    remote_ref = f"refs/remotes/origin/{branch}"
    if run(["git", "rev-parse", "--verify", "--quiet", remote_ref], cwd=worktree).returncode != 0:
        return []
    proc = run(["git", "log", "--format=%h %s", head, f"^{remote_ref}"], cwd=worktree, timeout=60)
    if proc.returncode != 0:
        return []
    return [line for line in (proc.stdout or "").splitlines() if line.strip()]


def remote_refs_containing(worktree: Path, head: str) -> list[str]:
    """Remote-tracking refs whose history contains ``head``. Raises Undecidable."""
    if not head:
        raise Undecidable("no HEAD recorded for the worktree")
    out = git_ok(
        ["for-each-ref", "--contains", head, "--format=%(refname)", "refs/remotes/"],
        worktree,
        timeout=60,
    )
    return [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.endswith("/HEAD")]


def open_pr_branches(repo: Path) -> set[str] | None:
    """Head branch names with an OPEN PR, or ``None`` when gh cannot say.

    ``None`` is not "no open PRs" — callers must treat it as undecidable and skip
    the whole class. One call per pass, not per worktree.
    """
    proc = run(
        ["gh", "pr", "list", "--state", "open", "--limit", "500", "--json", "headRefName"],
        cwd=repo,
        timeout=60,
    )
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(rows, list):
        return None
    return {str(row.get("headRefName", "")) for row in rows if row.get("headRefName")}


def _judge_worktree(
    wt: Worktree,
    *,
    repo: Path,
    primary: Path,
    here: Path,
    now: float,
    idle_hours: float,
    base_ref: str,
    pr_state,
    open_branches: set[str],
) -> Item:
    path = wt.path
    label = wt.branch or f"(detached {wt.head[:8]})"
    measured_idle: list[float] = []

    def keep(reason: str, detail: str = "", size: int = 0) -> Item:
        return Item(
            "worktree",
            str(path),
            size,
            "KEEP",
            reason,
            detail or label,
            branch=wt.branch,
            head=wt.head,
            idle_hours=measured_idle[-1] if measured_idle else None,
        )

    try:
        resolved = path.resolve()
    except OSError:
        return keep("path_unresolvable")
    if not path.exists():
        return keep("path_missing", "git worktree prune handles the admin record")
    if resolved == primary.resolve():
        return keep("primary_checkout")
    if contains(resolved, here):
        return keep("in_use_by_this_process")
    if wt.branch in MAIN_BRANCHES:
        return keep("protected_branch")
    if not wt.detached and not wt.branch:
        return keep("no_branch", "neither a branch nor a detached HEAD; resolve by hand")
    if wt.detached and (path / "_PURPOSE.md").exists():
        # wt.py archives a purpose file against a branch; a detached lane has none,
        # so its draft would have no preservation path.
        return keep("detached_head", "detached lane holds a _PURPOSE.md; resolve by hand")
    if wt.branch in open_branches:
        return keep("open_pr", f"{label}: a PR is open on this branch")

    # LIVENESS FIRST. The size walk's recursive newest mtime is the idleness
    # answer, and it is taken BEFORE any git call: `git status` and `git log`
    # touch files under the gitdir, and the earlier direct-children-plus-index
    # check was invalidated by exactly that (measured 72.0h before the status
    # call, 0.0h after). Snapshot first, judge after.
    try:
        size, newest = tree_stats(path)
    except Undecidable as exc:
        return keep_for("worktree", path, exc)
    idle = max(0.0, (now - newest) / 3600.0)
    measured_idle.append(idle)
    if idle < idle_hours:
        return keep("recently_active", f"{label}: touched {idle:.1f}h ago", size)

    try:
        dirty = dirty_paths(path)
    except Undecidable as exc:
        return keep("status_undecidable", str(exc), size)
    if dirty:
        return keep("dirty", f"{label}: {len(dirty)} changed/untracked path(s)", size)

    try:
        ignored = unique_ignored_paths(path)
    except Undecidable as exc:
        return keep("ignored_scan_undecidable", str(exc), size)
    if ignored:
        return keep(
            "ignored_content_exists_nowhere_else", f"{label}: {', '.join(ignored[:3])}", size
        )

    if wt.detached:
        # Review and base-oracle checkouts: `git worktree add --detach <sha>`. With
        # no branch, "merged" has no meaning; the proof is that the exact commit is
        # on a remote-tracking ref, so the checkout can be recreated from it. 38
        # of these, ~100 MB each, were all this class could not see on 2026-09-28.
        try:
            remotes = remote_refs_containing(path, wt.head)
        except Undecidable as exc:
            return keep("remote_scan_undecidable", str(exc), size)
        if not remotes:
            return keep("detached_head", f"{label}: HEAD is on no remote-tracking ref", size)
        return Item(
            "worktree",
            str(path),
            size,
            "REMOVE",
            "detached_on_remote",
            f"{label}: HEAD is on {remotes[0]}",
        )

    merged = is_merged_into(lambda a: run(list(a), cwd=path), wt.head, base_ref)
    try:
        unpushed = unpushed_commits(path, wt.head)
    except Undecidable as exc:
        return keep("unpushed_scan_undecidable", str(exc), size)

    if merged:
        # A squash-merged branch always has commits unreachable from any remote —
        # its pre-squash history. The invariant the unpushed check protects is
        # "no work exists only here", and is_merged_into already proved this
        # branch's cumulative diff is on the base, so those commits are
        # duplicates of landed content, not unique work.
        #
        # EXCEPT when the tip moved after the push. `is_merged_into` compares the
        # CUMULATIVE diff, so a commit added after the merge can leave that diff
        # still matching the base — and it would then be destroyed. Comparing the
        # tip against its own remote-tracking ref catches that directly.
        ahead = commits_after_push(path, wt.branch, wt.head)
        if ahead:
            return keep(
                "local_commits_after_push",
                f"{label}: {len(ahead)} commit(s) on top of origin/{wt.branch}, "
                "made after it merged",
                size,
            )
        detail = f"{label}: merged into {base_ref}"
        if unpushed:
            detail += f"; {len(unpushed)} pre-squash commit(s) superseded"
        return Item(
            "worktree", str(path), size, "REMOVE", "merged_and_clean", detail, branch=wt.branch
        )

    if unpushed:
        return keep("unpushed_commits", f"{label}: {len(unpushed)} commit(s) on no remote", size)

    closed = pr_state(wt.branch, repo)
    if closed:
        return Item(
            "worktree",
            str(path),
            size,
            "REMOVE",
            "pr_closed_branch_fully_pushed",
            f"{label}: PR closed/merged, every commit is on a remote",
            branch=wt.branch,
        )
    if closed is None:
        return keep("unmerged_pr_state_unknown", f"{label}: gh could not answer", size)
    return keep("unmerged_pr_open", f"{label}: PR still open", size)


# --------------------------------------------------------------------------- #
# (b2) preserve, then remove: finished lanes that still hold work
# --------------------------------------------------------------------------- #
#
# The clean-removal gates above refuse every lane that holds anything unique, and
# that is correct as far as it goes -- but on 2026-10-01 it meant this tool had
# reclaimed nothing for days while 285 worktrees held 51 GB: almost every finished
# lane carries a stray untracked note, a review artifact under output/, or the
# pre-squash commits of a merged branch. The founder had to be asked, which is the
# thing this script exists to prevent.
#
# So a lane that is long idle AND finished is preserved first, then removed:
#   * tracked edits + untracked files -> one commit on a LOCAL-ONLY ref,
#     ``refs/preserved/<name>``, built in a temporary index from the files' RAW
#     bytes (``hash-object --no-filters``: no CRLF or clean-filter rewriting).
#     Parents: HEAD, plus the real index as its own commit when it differs, so
#     staged-only content survives the index being deleted with the worktree.
#   * every ignored file that is not a tool cache -> copied under
#     ``<preserve-dir>/<name>/files/`` with ``<name>/MANIFEST.json`` beside it.
#   * the worktree is then RENAMED to a quarantine name -- on Windows that fails
#     while any process holds a handle inside it -- and re-fingerprinted. Only an
#     identical tree is deleted; anything else is renamed back and kept.
# Nothing is ever pushed: this repo is public, and a dirty tree can hold a secret.
# Shapes it cannot preserve faithfully (submodules, embedded repositories, links)
# keep the worktree, as does any step that cannot be verified.

# The KEEP reasons that mean "holds work", which preservation can answer. Every
# other KEEP -- open PR, recent activity, undecidable git, a protected branch --
# stays a KEEP.
PRESERVABLE_KEEPS = frozenset(
    {
        "dirty",
        "ignored_content_exists_nowhere_else",
        "unpushed_commits",
        "local_commits_after_push",
        "detached_head",
        "unmerged_pr_state_unknown",
    }
)

# Tool-owned caches: untracked or ignored files under these directory names, and
# compiled bytecode, are neither snapshotted nor copied. Deliberately narrower than
# DISPOSABLE_IGNORED: ``build/``, ``dist/``, ``env/`` are ordinary names a person
# can write into, so their files ARE preserved -- and counted against the cap. A
# TRACKED file under one of these names is a tracked file like any other.
SNAPSHOT_EXCLUDED_DIRS = (
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
    ".next",
    ".next-build",
    ".svelte-kit",
)
TOOL_OWNED_GLOBS = ("*.pyc", "*.pyo")

PR_STATES_LIMIT = 10_000
_ZERO_OID = "0" * 40


@dataclass(frozen=True)
class PreservePolicy:
    idle_hours: float = 48.0
    # A lane with no PR is "finished" only when nothing on it is newer than this.
    max_commit_age_days: float = 7.0
    # Unique bytes the preserve step will capture; anything bigger is listed, never
    # copied, and the worktree is kept.
    max_bytes: int = 50 * 1024**2
    root: Path | None = None  # copies go here; default <git-common-dir>/tinyassets-preserved


@dataclass(frozen=True)
class PrRecord:
    state: str  # OPEN | MERGED | CLOSED
    head_oid: str
    cross_repo: bool = False


def pr_records_by_branch(repo: Path) -> dict[str, list[PrRecord]] | None:
    """Head branch -> every PR on it, or ``None`` when gh cannot say.

    A listing that reaches its limit is ``None`` too: a branch missing from a
    truncated list is not a branch with no PR.
    """
    proc = run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "all",
            "--limit",
            str(PR_STATES_LIMIT),
            "--json",
            "headRefName,state,headRefOid,isCrossRepository",
        ],
        cwd=repo,
        timeout=180,
    )
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(rows, list) or len(rows) >= PR_STATES_LIMIT:
        return None
    records: dict[str, list[PrRecord]] = {}
    for row in rows:
        name = str(row.get("headRefName", ""))
        if name:
            records.setdefault(name, []).append(
                PrRecord(
                    state=str(row.get("state", "")).upper(),
                    head_oid=str(row.get("headRefOid", "")),
                    cross_repo=bool(row.get("isCrossRepository")),
                )
            )
    return records


def _is_tool_owned(rel: str) -> bool:
    parts = rel.replace("\\", "/").rstrip("/").split("/")
    if any(part in SNAPSHOT_EXCLUDED_DIRS for part in parts):
        return True
    return any(fnmatch.fnmatch(parts[-1], g) for g in TOOL_OWNED_GLOBS)


def newest_commit_age_days(worktree: Path, head: str, base_ref: str, now: float) -> float:
    """Age of the newest commit on ``head`` that ``base_ref`` lacks. Raises Undecidable.

    Every such commit, not the first N: committer dates are not monotonic, so a
    recent commit can sit behind an old one.
    """
    out = git_ok(["log", "--format=%ct", head, f"^{base_ref}"], worktree, timeout=120)
    stamps = [int(s) for s in out.split() if s.strip().isdigit()]
    if not stamps:
        stamps = [int(git_ok(["log", "-1", "--format=%ct", head], worktree).strip())]
    return max(0.0, (now - max(stamps)) / 86400.0)


def _contains(worktree: Path, ancestor: str, descendant: str) -> bool:
    if not ancestor or not descendant:
        return False
    if ancestor == descendant:
        return True
    proc = run(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=worktree)
    return proc.returncode == 0


@dataclass
class Payload:
    changed: list[str]  # tracked edits/deletions + untracked files, worktree-relative
    ignored: list[str]  # ignored files to copy
    bytes: int


def preservation_payload(worktree: Path) -> Payload:
    """What the preserve step must capture. Raises Undecidable on any shape it
    cannot preserve faithfully: a submodule, an embedded repository, a link, or a
    tracked file whose local bytes ``git status`` would not report -- an
    assume-unchanged / skip-worktree entry, or a path under a clean filter that
    may rewrite what gets compared (Codex round 2, F1)."""
    staged = git_ok(["ls-files", "--stage"], worktree, timeout=120)
    if any(line.startswith("160000 ") for line in staged.splitlines()):
        raise Undecidable("the lane has a submodule; its contents cannot be snapshotted")
    flagged = [
        line[2:]
        for line in git_ok(["ls-files", "-v"], worktree, timeout=120).splitlines()
        if line[:1].islower() or line[:1] == "S"
    ]
    if flagged:
        raise Undecidable(f"assume-unchanged/skip-worktree entries hide bytes: {flagged[:3]}")
    filtered = _filtered_paths(worktree)
    if filtered:
        raise Undecidable(f"paths under a clean filter: {filtered[:3]}")
    raw = git_ok(
        [
            "status",
            "--porcelain",
            "-z",
            "--untracked-files=all",
            "--ignored=matching",
            "--ignore-submodules=none",
        ],
        worktree,
        timeout=300,
    )
    fields = [f for f in raw.split("\0") if f]
    changed: list[str] = []
    ignored_entries: list[str] = []
    index = 0
    while index < len(fields):
        rec = fields[index]
        index += 1
        if len(rec) < 4 or rec[2] != " ":
            raise Undecidable(f"unparseable status record {rec[:40]!r}")
        xy, rel = rec[:2], rec[3:]
        if "R" in xy or "C" in xy:
            if index < len(fields):
                changed.append(fields[index])  # the origin path of a rename/copy
                index += 1
        if xy == "!!":
            ignored_entries.append(rel)
            continue
        if xy == "??" and rel.endswith("/"):
            # -uall lists every file, so a directory here is a repository git
            # will not descend into; `git add` would record a bare gitlink.
            raise Undecidable(f"embedded repository at {rel}")
        if xy == "??" and _is_tool_owned(rel):
            continue
        changed.append(rel)

    total = 0
    for rel in changed:
        target = worktree / rel
        try:
            info = os.stat(target, follow_symlinks=False)
        except FileNotFoundError:
            continue  # a deletion; the snapshot records it as one
        except OSError as exc:
            raise Undecidable(f"cannot stat {rel}: {exc}") from exc
        if is_link(info):
            raise Undecidable(f"{rel} is a link; links are not preserved")
        total += info.st_size

    files: list[str] = []
    for rel in ignored_entries:
        if _is_tool_owned(rel):
            continue
        src = worktree / rel.rstrip("/")
        try:
            info = os.stat(src, follow_symlinks=False)
            if is_link(info):
                raise Undecidable(f"{rel} is a link; links are not preserved")
            if not stat_mod.S_ISDIR(info.st_mode):
                files.append(rel)
                total += info.st_size
                continue
            for parent, dirs, names in os.walk(src, onerror=_raise_walk_error):
                for name in list(dirs):
                    if is_link(os.stat(Path(parent, name), follow_symlinks=False)):
                        raise Undecidable(f"{Path(parent, name)} is a link")
                dirs[:] = [d for d in dirs if d not in SNAPSHOT_EXCLUDED_DIRS]
                for name in names:
                    full = Path(parent, name)
                    sub = str(full.relative_to(worktree)).replace("\\", "/")
                    if _is_tool_owned(sub):
                        continue
                    info = os.stat(full, follow_symlinks=False)
                    if is_link(info):
                        raise Undecidable(f"{sub} is a link; links are not preserved")
                    files.append(sub)
                    total += info.st_size
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise Undecidable(f"cannot list {rel}: {exc}") from exc
    return Payload(changed=changed, ignored=files, bytes=total)


def preservable(
    wt: Worktree,
    item: Item,
    *,
    policy: PreservePolicy,
    pr_records: dict[str, list[PrRecord]] | None,
    now: float,
    base_ref: str,
) -> Item:
    """Turn a holds-work KEEP into ``preserve_then_remove`` when the lane is finished.

    Finished = idle at least ``policy.idle_hours`` AND either a same-repository PR
    on this branch is merged or closed and its head CONTAINS this lane's HEAD (so a
    reused branch name with newer work is not "finished" by an old PR), or no such
    PR and no commit newer than ``max_commit_age_days``. A HEAD that is the head of
    any open PR is never finished, branch or detached. Any question that cannot be
    answered leaves the original KEEP in place.
    """
    path = Path(item.path)

    def still(why: str) -> Item:
        item.detail = f"{item.detail}; not preserved: {why}"
        return item

    if wt.locked:
        return still("worktree is locked")
    if item.idle_hours is None or item.idle_hours < policy.idle_hours:
        return still(f"idle under {policy.idle_hours:.0f}h")
    if wt.detached and (path / "_PURPOSE.md").exists():
        return still("detached lane holds a _PURPOSE.md")
    if pr_records is None:
        return still("PR states unknown")
    mine = [p for p in pr_records.get(wt.branch, []) if not p.cross_repo] if wt.branch else []
    open_heads = {p.head_oid for recs in pr_records.values() for p in recs if p.state == "OPEN"}
    if any(p.state == "OPEN" for p in mine) or wt.head in open_heads:
        return still("a PR is open")
    done = [
        p for p in mine if p.state in {"MERGED", "CLOSED"} and _contains(path, wt.head, p.head_oid)
    ]
    if done:
        finished = "PR " + "/".join(sorted({p.state.lower() for p in done}))
    else:
        try:
            age = newest_commit_age_days(path, wt.head, base_ref, now)
        except Undecidable as exc:
            return still(f"commit age undecidable ({exc})")
        if age < policy.max_commit_age_days:
            return still(f"no finished PR for this HEAD and a commit {age:.1f} days old")
        finished = f"no finished PR for this HEAD, newest commit {age:.0f} days old"
    try:
        payload = preservation_payload(path)
    except Undecidable as exc:
        return still(f"cannot preserve this shape ({exc})")
    if payload.bytes > policy.max_bytes:
        return still(
            f"{human(payload.bytes)} of unique data exceeds the {human(policy.max_bytes)} cap"
        )
    item.verdict = "REMOVE"
    item.detail = (
        f"{finished}; held: {item.reason} ({item.detail}); preserves {human(payload.bytes)}"
    )
    item.reason = "preserve_then_remove"
    return item


def preserved_name(repo: Path, path: Path) -> str:
    """A ref-safe name: the path below the repo's parent, plus a hash of the full
    path so ``a/b`` and ``a-b`` cannot collide."""
    resolved = path.resolve()
    try:
        raw = "-".join(resolved.relative_to(repo.resolve().parent).parts)
    except ValueError:
        raw = "-".join(resolved.parts[-2:])
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-.") or "worktree"
    tag = hashlib.sha1(str(resolved).lower().encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{time.strftime('%Y%m%d')}-{tag}"


def fingerprint(root: Path) -> dict[str, tuple[int, int]]:
    """``{relpath: (size, mtime_ns)}`` for every file and directory. Raises
    Undecidable on a link (nothing here reasons about where one points) and when
    the tree exceeds the entry budget."""
    out: dict[str, tuple[int, int]] = {}
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    info = entry.stat(follow_symlinks=False)
                    if is_link(info):
                        raise Undecidable(f"{entry.path} is a link")
                    rel = os.path.relpath(entry.path, root).replace("\\", "/")
                    if stat_mod.S_ISDIR(info.st_mode):
                        stack.append(Path(entry.path))
                        out[rel + "/"] = (0, 0)
                    else:
                        out[rel] = (info.st_size, info.st_mtime_ns)
                    if len(out) > MAX_TREE_ENTRIES:
                        raise Undecidable(f"{root} exceeds {MAX_TREE_ENTRIES} entries")
        except OSError as exc:
            raise Undecidable(f"cannot read {current}: {exc}") from exc
    return out


def _git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def snapshot_commit(worktree: Path, head: str, changed: list[str]) -> str:
    """A commit holding HEAD + every changed path's RAW bytes. Raises Undecidable.

    Built in a temporary index, so the worktree's own index is untouched. Each
    blob is written with ``hash-object --no-filters`` and re-derived from the
    file's bytes, so CRLF conversion or a clean filter cannot rewrite it. A
    real index that differs from HEAD becomes a second parent: staged-only content
    would otherwise be deleted with the worktree.
    """
    fd, index = tempfile.mkstemp(prefix="ta-preserve-index-")
    os.close(fd)
    os.unlink(index)  # git must create it; an empty file is not a valid index
    env = dict(os.environ, GIT_INDEX_FILE=index)

    def git_env(args: list[str], stdin: str = "") -> str:
        # Bytes in and out: text mode on Windows turns "\n" into "\r\n", and
        # `update-index --index-info` then IGNORES "a.txt\r" with exit 0.
        proc = subprocess.run(
            ["git", *args],
            cwd=str(worktree),
            env=env,
            input=stdin.encode("utf-8"),
            capture_output=True,
            timeout=600,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()[:200]
            raise Undecidable(f"git {args[0]} -> rc={proc.returncode}: {err}")
        return proc.stdout.decode("utf-8", "surrogateescape")

    def staged() -> dict[str, tuple[str, str]]:
        entries = {}
        for rec in git_env(["ls-files", "--stage", "-z"]).split("\0"):
            meta, _, rel = rec.partition("\t")
            if rel:
                mode, sha, _stage = meta.split()
                entries[rel] = (mode, sha)
        return entries

    try:
        git_env(["read-tree", head])
        modes = {rel: mode for rel, (mode, _sha) in staged().items()}
        records: list[str] = []
        want: dict[str, str | None] = {}
        unique = list(dict.fromkeys(changed))
        present = [rel for rel in unique if os.path.lexists(worktree / rel)]
        for rel in unique:
            if rel not in present:
                records.append(f"0 {_ZERO_OID}\t{rel}")
                want[rel] = None
        if present:
            shas = git_env(
                ["hash-object", "-w", "--no-filters", "--stdin-paths"],
                "".join(f"{rel}\n" for rel in present),
            ).split()
            if len(shas) != len(present):
                raise Undecidable("hash-object returned the wrong number of ids")
            for rel, sha in zip(present, shas):
                if _git_blob_id((worktree / rel).read_bytes()) != sha:
                    raise Undecidable(f"{rel} changed while it was being preserved")
                executable = os.name != "nt" and os.access(worktree / rel, os.X_OK)
                mode = modes.get(rel, "100755" if executable else "100644")
                if mode not in {"100644", "100755"}:
                    raise Undecidable(f"{rel} has mode {mode}; not preserved as a file")
                records.append(f"{mode} {sha}\t{rel}")
                want[rel] = sha
        if records:
            git_env(["update-index", "-z", "--index-info"], "".join(r + "\0" for r in records))
        # Verify the index holds exactly what was asked: git skips a path it
        # dislikes with a warning and exit 0, which is how a preserved snapshot
        # can silently be missing the one file that mattered.
        now_staged = staged()
        for rel, sha in want.items():
            got = now_staged.get(rel, (None, None))[1]
            if got != sha:
                raise Undecidable(f"snapshot index does not hold {rel} (wanted {sha}, has {got})")
        tree = git_env(["write-tree"]).strip()
    finally:
        if os.path.exists(index):
            os.unlink(index)

    parents = ["-p", head]
    real_tree = git_ok(["write-tree"], worktree).strip()  # fails on unmerged entries
    if real_tree != git_ok(["rev-parse", f"{head}^{{tree}}"], worktree).strip():
        staged = _commit_tree(worktree, real_tree, ["-p", head], "the lane's real index")
        parents += ["-p", staged]
    if (
        parents == ["-p", head]
        and tree == git_ok(["rev-parse", f"{head}^{{tree}}"], worktree).strip()
    ):
        return head
    return _commit_tree(worktree, tree, parents, f"preserved worktree {worktree}")


def _commit_tree(worktree: Path, tree: str, parents: list[str], what: str) -> str:
    return git_ok(
        [
            "-c",
            "user.name=dev-hygiene",
            "-c",
            "user.email=dev-hygiene@localhost",
            "commit-tree",
            tree,
            *parents,
            "-m",
            f"dev_hygiene: {what}",
        ],
        worktree,
    ).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reserve(repo: Path, base: str, commit: str, root: Path, need_dir: bool) -> tuple[str, Path]:
    """Create ``refs/preserved/<name>`` (create-only) and, if needed, an exclusive
    ``root/<name>`` directory. Never overwrites an earlier preservation."""
    for n in range(1, 50):
        name = base if n == 1 else f"{base}-{n}"
        dest = root / name
        if need_dir:
            try:
                dest.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue
            except OSError as exc:
                raise Undecidable(f"cannot create {dest}: {exc}") from exc
        ref = f"refs/preserved/{name}"
        proc = run(["git", "update-ref", ref, commit, _ZERO_OID], cwd=repo)
        if proc.returncode == 0:
            return ref, dest
        if need_dir:
            dest.rmdir()
    raise Undecidable(f"no free preserved name for {base}")


def copy_with_manifest(worktree: Path, files: list[str], dest: Path) -> int:
    """Copy ``files`` under ``dest/files`` and write ``dest/MANIFEST.json`` beside
    them -- outside the copied tree, so no copied path can collide with it."""
    manifest = []
    try:
        for rel in files:
            src, out = worktree / rel, dest / "files" / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out, follow_symlinks=False)
            shutil.copystat(src, out, follow_symlinks=False)
            digest = _sha256(src)
            if _sha256(out) != digest:
                raise Undecidable(f"copy of {rel} does not match its source")
            manifest.append({"path": rel, "bytes": out.stat().st_size, "sha256": digest})
        (dest / "MANIFEST.json").write_text(
            json.dumps({"source": str(worktree), "files": manifest}, indent=1), encoding="utf-8"
        )
    except OSError as exc:
        raise Undecidable(f"copy failed: {exc}") from exc
    return len(manifest)


def _filtered_paths(worktree: Path) -> list[str]:
    """Tracked paths with a ``filter`` attribute set. Raises Undecidable."""
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(worktree), capture_output=True, timeout=120
    )
    if listed.returncode != 0:
        raise Undecidable("git ls-files failed")
    proc = subprocess.run(
        ["git", "check-attr", "-z", "--stdin", "filter"],
        cwd=str(worktree),
        input=listed.stdout,
        capture_output=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise Undecidable(f"git check-attr -> rc={proc.returncode}")
    fields = proc.stdout.decode("utf-8", "surrogateescape").split("\0")
    out = []
    for index in range(0, len(fields) - 2, 3):
        path, _attr, value = fields[index : index + 3]
        if value not in {"unspecified", "unset"}:
            out.append(path)
    return out


def payload_digests(worktree: Path, payload: Payload) -> dict[str, str]:
    """Content digest of every byte the preserve step captures. Compared before
    and after the quarantine rename: equal size and mtime is not equal content
    (Codex round 2, F2). Raises Undecidable."""
    out: dict[str, str] = {}
    try:
        for rel in dict.fromkeys(payload.changed):
            target = worktree / rel
            out["c:" + rel] = (
                _git_blob_id(target.read_bytes()) if os.path.lexists(target) else "deleted"
            )
        for rel in payload.ignored:
            out["i:" + rel] = _sha256(worktree / rel)
    except OSError as exc:
        raise Undecidable(f"cannot read the payload: {exc}") from exc
    return out


def _admin_dir(worktree: Path, common: Path) -> Path:
    """This worktree's administrative directory under ``common/worktrees``."""
    text = (worktree / ".git").read_text(encoding="utf-8").strip()
    if not text.startswith("gitdir:"):
        raise Undecidable(f"{worktree}/.git is not a worktree link")
    admin = Path(text[len("gitdir:") :].strip())
    if admin.parent.resolve() != (common / "worktrees").resolve() or not admin.is_dir():
        raise Undecidable(f"{admin} is not an administrative directory of this repository")
    return admin


def _lane_state(worktree: Path, admin: Path) -> tuple:
    """Everything the final check compares: HEAD, the real index's tree, the lock,
    the captured payload's content, and the whole tree's fingerprint."""
    payload = preservation_payload(worktree)
    return (
        git_ok(["rev-parse", "HEAD"], worktree).strip(),
        git_ok(["write-tree"], worktree).strip(),  # the real index; fails on conflicts
        (admin / "locked").exists(),
        sorted(payload.changed),
        sorted(payload.ignored),
        payload_digests(worktree, payload),
        fingerprint(worktree),
    )


def _artifacts_mismatch(repo: Path, head: str, commit: str, dest: Path, state: tuple) -> str:
    """Why the preserved ref + copies do NOT hold ``state``, or "" when they do.

    Checks the artifacts themselves: every changed path's blob in the snapshot
    tree, the real-index tree against the snapshot's index parent (HEAD's tree
    when there is none), and every copy's manifest digest. Raises Undecidable.
    """
    _head, index_tree, _locked, changed, ignored, digests, _fp = state
    tree = {}
    for rec in git_ok(["ls-tree", "-r", "-z", commit], repo, timeout=120).split("\0"):
        meta, _, rel = rec.partition("\t")
        if rel:
            tree[rel] = meta.split()[2]
    for rel in changed:
        want = digests["c:" + rel]
        got = tree.get(rel, "deleted")
        if got != want:
            return f"the snapshot does not hold the final {rel}"
    parents = git_ok(["rev-list", "--parents", "-n", "1", commit], repo).split()[1:]
    index_parent = parents[1] if commit != head and len(parents) > 1 else head
    if git_ok(["rev-parse", f"{index_parent}^{{tree}}"], repo).strip() != index_tree:
        return "the snapshot does not hold the final index"
    if ignored:
        try:
            manifest = json.loads((dest / "MANIFEST.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Undecidable(f"manifest unreadable: {exc}") from exc
        copied = {f["path"]: f["sha256"] for f in manifest["files"]}
        for rel in ignored:
            if (
                copied.get(rel) != digests["i:" + rel]
                or _sha256(dest / "files" / rel) != copied[rel]
            ):
                return f"the copy does not hold the final {rel}"
    return ""


def preserve_and_remove(repo: Path, item: Item, policy: PreservePolicy) -> tuple[bool, str]:
    """Preserve one finished lane, verify the preservation, then remove the worktree.

    Inventory and apply are minutes apart, so everything is re-proved here. The
    lane's state -- HEAD, real-index tree, lock, payload CONTENT digests, whole-tree
    fingerprint -- is read before preserving and again after the worktree is
    renamed to a quarantine name (Windows refuses that rename while any process
    holds a handle inside, and no writer knows the new name). Only an identical,
    unlocked lane is deleted; anything else is renamed back and kept. The branch
    ref is never deleted.
    """
    path = Path(item.path)
    quarantine = path.with_name(path.name + ".hygiene-removing")
    try:
        common = Path(
            git_ok(["rev-parse", "--path-format=absolute", "--git-common-dir"], repo).strip()
        )
        admin = _admin_dir(path, common)
        if (admin / "locked").exists():
            return False, "changed since inventory: the worktree is locked"
        head = git_ok(["rev-parse", "HEAD"], path).strip()
        if item.head and head != item.head:
            return False, "changed since inventory: HEAD moved"
        before = _lane_state(path, admin)
        newest = max((m for _s, m in before[-1].values()), default=0) / 1e9
        if (time.time() - newest) / 3600.0 < policy.idle_hours:
            return False, "changed since inventory: the worktree is active again"
        payload = preservation_payload(path)
        if payload.bytes > policy.max_bytes:
            return False, f"changed since inventory: {human(payload.bytes)} exceeds the cap"
        if (path / "_PURPOSE.md").exists():
            import wt  # noqa: PLC0415  (sibling script; only needed on the apply path)

            wt._archive_purpose(repo, path, item.branch, "dev_hygiene: preserve_then_remove")
        commit = snapshot_commit(path, head, payload.changed)
        root = policy.root or common / "tinyassets-preserved"
        ref, dest = reserve(repo, preserved_name(repo, path), commit, root, bool(payload.ignored))
        copied = copy_with_manifest(path, payload.ignored, dest) if payload.ignored else 0
    except Undecidable as exc:
        return False, f"not preserved, so not removed: {exc}"
    except Exception as exc:  # noqa: BLE001 -- any failure before removal keeps the lane
        return False, f"not preserved, so not removed: {type(exc).__name__}: {exc}"

    try:
        os.rename(path, quarantine)
    except OSError as exc:
        return False, f"preserved as {ref}; kept: the folder is in use ({exc})"
    try:
        after = _lane_state(quarantine, admin)
        # Equal before/after does not prove the ARTIFACTS hold that state: a change
        # and its revert during preservation leave both samples equal while the
        # snapshot captured the intermediate (Codex round 3). So the artifacts are
        # checked against the final state directly, and a lock is refused outright
        # rather than compared -- one taken before the baseline is in both samples.
        problem = "" if after == before else "changed during preservation"
        if not problem and (before[2] or after[2]):
            problem = "the worktree is locked"
        if not problem:
            problem = _artifacts_mismatch(repo, head, commit, dest, after)
    except Undecidable as exc:
        problem = f"undecidable: {exc}"
    if problem:
        try:
            os.rename(quarantine, path)
        except OSError as exc:
            return (
                False,
                f"preserved as {ref}; {problem}; LEFT AT {quarantine}: {exc}",
            )
        return False, f"{problem}; kept (preserved anyway as {ref})"

    ok, detail = remove_path(quarantine)
    if not ok:
        return False, f"preserved as {ref}; removal failed: {detail}"
    remove_path(admin)
    where = f"; {copied} ignored file(s) copied to {dest}" if copied else ""
    return True, f"preserved as {ref} @ {commit[:12]}{where}; branch ref kept"


# --------------------------------------------------------------------------- #
# (c) Docker build cache
# --------------------------------------------------------------------------- #


def local_docker() -> None:
    """Pin the local endpoint for the entire pass, even if another lane switches context."""
    global _DOCKER_HOST
    if _DOCKER_HOST:
        return
    endpoint = os.environ.get("DOCKER_HOST", "")
    if os.environ.get("DOCKER_CONTEXT") or not endpoint:
        proc = run(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"])
        if proc.returncode:
            raise Undecidable("cannot determine Docker endpoint")
        endpoint = proc.stdout.strip()
    if not endpoint.startswith(("npipe:////./pipe/", "unix:///")):
        raise Undecidable(f"not a local Docker endpoint: {endpoint}")
    _DOCKER_HOST = endpoint


def docker_json(args: list[str], docker: str = "docker") -> list[dict]:
    proc = run([docker, *args], timeout=40)
    if proc.returncode:
        raise Undecidable(proc.stderr.strip()[:300] or "Docker inventory failed")
    try:
        rows = json.loads(proc.stdout)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("expected object list")
        return rows
    except (ValueError, TypeError) as exc:
        raise Undecidable(f"invalid Docker inventory: {exc}") from exc


def docker_objects(kind: str, docker: str) -> list[dict]:
    proc = run([docker, kind, "ls", "-q", *(["--all"] if kind != "volume" else [])])
    if proc.returncode:
        raise Undecidable(proc.stderr.strip()[:300])
    ids = list(dict.fromkeys(proc.stdout.split()))
    rows = []
    for offset in range(0, len(ids), 50):
        rows.extend(docker_json([kind, "inspect", *ids[offset : offset + 50]], docker))
    return rows


def docker_epoch(value: object) -> float:
    try:
        stamp = float(value)
    except (ValueError, TypeError):
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except (ValueError, OverflowError, OSError):
            return math.inf
    return stamp if math.isfinite(stamp) and stamp > 0 else math.inf


def select_docker_objects(
    containers: list[dict],
    volumes: list[dict],
    images: list[dict],
    *,
    min_age_hours: float,
    now: float,
) -> list[Item]:
    """Every container reference (even stopped) protects its images and volumes."""
    cutoff = now - min_age_hours * 3600
    used_images = {c.get("Image") for c in containers}
    used_volumes = {
        m.get("Name") for c in containers for m in c.get("Mounts", []) if m.get("Type") == "volume"
    }
    result = []
    for kind, rows in (("container", containers), ("volume", volumes), ("image", images)):
        for row in rows:
            identity = row.get("Name") if kind == "volume" else row.get("Id")
            if not identity:
                raise Undecidable("Docker object has no identity")
            labels = (
                (row.get("Config") or {}).get("Labels")
                if kind == "container"
                else row.get("Labels")
            )
            labels = labels or {}
            created = docker_epoch(row.get("CreatedAt") if kind == "volume" else row.get("Created"))
            reason = "old_unused"
            if kind != "image" and labels.get("tinyassets.disposable") != "true":
                reason = "not_labelled_disposable"
            elif kind != "image" and docker_epoch(labels.get("tinyassets.created-at")) >= cutoff:
                reason = "label_age_unknown_or_recent"
            elif created >= cutoff:
                reason = "age_unknown_or_recent"
            elif kind == "image" and identity in used_images:
                reason = "in_use"
            elif kind == "volume" and identity in used_volumes:
                reason = "in_use"
            elif kind == "container":
                state = row.get("State") or {}
                if (
                    state.get("Status") not in {"exited", "dead"}
                    or state.get("Running") is not False
                ):
                    reason = "in_use"
                elif docker_epoch(state.get("FinishedAt")) >= cutoff:
                    reason = "recently_stopped"
            result.append(
                Item(
                    "docker",
                    f"{kind}:{identity}",
                    int(row.get("Size") or 0),
                    "REMOVE" if reason == "old_unused" else "KEEP",
                    reason,
                    head=str(row.get("CreatedAt") or row.get("Created") or ""),
                    docker_age_hours=min_age_hours,
                )
            )
    return result


def collect_docker_objects(
    *, min_age_hours: float, now: float, docker: str = "docker"
) -> list[Item]:
    return select_docker_objects(
        docker_objects("container", docker),
        docker_objects("volume", docker),
        docker_objects("image", docker),
        min_age_hours=min_age_hours,
        now=now,
    )


def docker_desktop_notice(repo: Path) -> list[str]:
    """One warning per oversized disk episode, shared by all repo worktrees."""
    try:
        local_docker()
        proc = run(["docker", "system", "df", "--format", "{{json .}}"], timeout=40)
        if proc.returncode:
            return []
        rows = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        if not rows or any("Size" not in row for row in rows):
            return []
        used = sum(parse_docker_size(str(row["Size"])) for row in rows)
        root = Path(os.environ.get("LOCALAPPDATA", "")) / "Docker" / "wsl"
        disks = list(root.glob("**/*.vhdx")) if root.is_dir() else []
        oversized = [p for p in disks if p.stat().st_size > max(used * 2, used + 40 * 1024**3)]
        common = Path(git_ok(["rev-parse", "--git-common-dir"], repo))
        if not common.is_absolute():
            common = repo / common
        marker = common / "dev-hygiene-docker-compaction.warned"
        if not oversized:
            marker.unlink(missing_ok=True)
            return []
        try:
            with marker.open("x", encoding="utf-8") as handle:
                handle.write("\n".join(str(p) for p in oversized))
        except FileExistsError:
            return []
        return [
            "Docker Desktop disk file(s) much larger than Docker usage "
            f"({human(used)}): "
            + ", ".join(f"{p} ({human(p.stat().st_size)})" for p in oversized)
            + ". Deleting Docker objects does not shrink the Windows disk file. "
            "Reclaiming host space needs Docker Desktop Clean / Purge data (destructive: "
            "also deletes retained Docker data), or elevated offline VHDX compaction. "
            "Hygiene does neither. This notice is shown once per oversized-disk episode."
        ]
    except (OSError, Undecidable, ValueError):
        return []


def docker_keep_flag(help_text: str) -> str:
    """Pick the keep-budget flag this Docker CLI actually has.

    Docker Desktop routes ``docker builder prune`` through buildx, which renamed
    ``--keep-storage`` to ``--reserved-space``. Probing the help text beats
    guessing: a wrong flag makes the prune a no-op that reports success.
    """
    if "--reserved-space" in help_text:
        return "--reserved-space"
    if "--keep-storage" in help_text:
        return "--keep-storage"
    raise Undecidable("docker builder prune has neither --reserved-space nor --keep-storage")


def collect_docker_cache(
    *, keep_gb: float, docker: str = "docker", min_age_hours: float = 6.0
) -> list[Item]:
    probe = run([docker, "version", "--format", "{{.Server.Version}}"], timeout=20)
    server = (probe.stdout or "").strip()
    if probe.returncode != 0 or not server or "cannot find" in (probe.stderr or ""):
        return [Item("docker", "build-cache", 0, "KEEP", "docker_engine_not_running")]

    helped = run([docker, "builder", "prune", "--help"], timeout=20)
    if helped.returncode != 0:
        # Recognisable help text in a FAILED invocation is not a probe result
        # (Codex round 1, P2): the returncode is the answer, not the stdout.
        return [
            Item(
                "docker",
                "build-cache",
                0,
                "KEEP",
                "prune_flag_unknown",
                f"docker builder prune --help rc={helped.returncode}",
            )
        ]
    try:
        flag = docker_keep_flag(helped.stdout or "")
    except Undecidable as exc:
        return [Item("docker", "build-cache", 0, "KEEP", "prune_flag_unknown", str(exc))]

    df = run([docker, "system", "df", "--format", "{{json .}}"], timeout=40)
    if df.returncode != 0:
        return [
            Item("docker", "build-cache", 0, "KEEP", "docker_df_failed", df.stderr.strip()[:200])
        ]
    reclaimable = _build_cache_reclaimable(df.stdout or "")
    if reclaimable is None:
        return [Item("docker", "build-cache", 0, "KEEP", "build_cache_row_absent")]
    if reclaimable <= 0:
        return [Item("docker", "build-cache", 0, "KEEP", "nothing_reclaimable")]
    return [
        Item(
            "docker",
            "build-cache",
            0,  # Docker df cannot age-filter: do not promise the unfiltered byte total.
            "REMOVE",
            "docker_build_cache",
            f"builder prune {flag}={int(keep_gb * 1024**3)} "
            f"until={min_age_hours:g}h (at most {human(reclaimable)} before age filtering)",
            prune_flag=flag,
            docker_age_hours=min_age_hours,
        )
    ]


def _build_cache_reclaimable(text: str) -> int | None:
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("Type", "")).strip().lower() not in {"build cache", "buildcache"}:
            continue
        return parse_docker_size(str(row.get("Reclaimable", "0")))
    return None


_SIZE_UNITS = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}


def parse_docker_size(text: str) -> int:
    """Parse docker's ``"1.234GB (100%)"`` size strings into bytes."""
    match = re.match(r"\s*([0-9.]+)\s*([A-Za-z]*)", text.replace("iB", "B"))
    if not match:
        return 0
    value = float(match.group(1))
    unit = (match.group(2) or "B").lower()
    return int(value * _SIZE_UNITS.get(unit, 1))


# --------------------------------------------------------------------------- #
# (d) repo scratch directories
# --------------------------------------------------------------------------- #


def collect_repo_scratch(repo: Path, *, min_age_days: float, now: float) -> list[Item]:
    items: list[Item] = []
    try:
        children = sorted(repo.iterdir())
    except OSError as exc:
        return [Item("scratch", str(repo), 0, "KEEP", "repo_unreadable", str(exc))]
    for child in children:
        name = child.name
        if name not in SCRATCH_NAMES and not any(fnmatch.fnmatch(name, g) for g in SCRATCH_GLOBS):
            continue
        # git must agree the path is ignored: a tracked or newly-added path is
        # never scratch, whatever it is called.
        if (
            run(["git", "check-ignore", "--quiet", "--", name], cwd=repo, timeout=20).returncode
            != 0
        ):
            items.append(Item("scratch", str(child), 0, "KEEP", "not_git_ignored"))
            continue
        try:
            size, newest = (
                tree_stats(child)
                if child.is_dir()
                else (child.stat().st_size, child.stat().st_mtime)
            )
        except OSError as exc:
            items.append(keep_for("scratch", child, Undecidable(str(exc))))
            continue
        except Undecidable as exc:
            items.append(keep_for("scratch", child, exc))
            continue
        age_days = max(0.0, (now - newest) / 86400.0)
        if age_days < min_age_days:
            items.append(
                Item("scratch", str(child), size, "KEEP", "recent", f"{age_days:.1f}d old")
            )
            continue
        items.append(
            Item(
                "scratch", str(child), size, "REMOVE", "stale_repo_scratch", f"{age_days:.1f}d old"
            )
        )
    return items


# --------------------------------------------------------------------------- #
# (e) package-manager caches
# --------------------------------------------------------------------------- #

# (tool, args that print its cache dir, args that clear it, subdir the clear owns).
# The tool is asked where its cache is and the tool clears it: this script never
# chooses a directory. npm's `config get cache` names a root that also holds
# `_npx/`, where running MCP servers live, so only `_cacache/` is measured and
# `npm cache clean` only touches that. 5.1 GB across the three on 2026-09-28.
TOOL_CACHES: tuple[tuple[str, tuple[str, ...], tuple[str, ...], str], ...] = (
    ("uv", ("cache", "dir"), ("cache", "clean"), ""),
    ("pip", ("cache", "dir"), ("cache", "purge"), ""),
    ("npm", ("config", "get", "cache"), ("cache", "clean", "--force"), "_cacache"),
)
# Measured and reported, never cleared. uv installs by link, and an environment
# made with `--link-mode symlink` (a CLI flag, invisible to this script) points
# into the cache, so `uv cache clean` breaks its imports (Codex, PR #4089, P1).
# pip and npm copy out of theirs.
TOOL_CACHES_REPORT_ONLY = {"uv": "cache_may_back_linked_envs"}
# A download cache is not a safety question, so an oversized one is still cleared;
# the budget only bounds how long the pass spends measuring it.
TOOL_CACHE_MAX_ENTRIES = 1_000_000


def collect_tool_caches(*, which=None) -> list[Item]:
    which = which or shutil.which
    items: list[Item] = []
    for tool, dir_args, clean_args, sub in TOOL_CACHES:
        exe = which(tool)
        if not exe:
            items.append(Item("toolcache", tool, 0, "KEEP", "tool_not_installed"))
            continue
        proc = run([exe, *dir_args], timeout=60)
        lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        if proc.returncode != 0 or not lines:
            detail = f"{tool} {' '.join(dir_args)} rc={proc.returncode}"
            items.append(Item("toolcache", tool, 0, "KEEP", "cache_dir_unknown", detail))
            continue
        target = Path(lines[-1]) / sub if sub else Path(lines[-1])
        if not target.is_dir():
            items.append(Item("toolcache", str(target), 0, "KEEP", "nothing_reclaimable"))
            continue
        detail = f"{tool} {' '.join(clean_args)}"
        try:
            size, _ = tree_stats(target, budget=TOOL_CACHE_MAX_ENTRIES)
        except AclLocked as exc:
            items.append(keep_for("toolcache", target, exc))
            continue
        except Undecidable:
            size, detail = 0, detail + " (size unmeasured: over the entry budget)"
        if size == 0 and "unmeasured" not in detail:
            items.append(Item("toolcache", str(target), 0, "KEEP", "nothing_reclaimable"))
            continue
        if tool in TOOL_CACHES_REPORT_ONLY:
            reason = TOOL_CACHES_REPORT_ONLY[tool]
            items.append(Item("toolcache", str(target), size, "KEEP", reason, detail))
            continue
        items.append(
            Item("toolcache", str(target), size, "REMOVE", "tool_owned_cache", detail, tool=tool)
        )
    return items


def clean_tool_cache(item: Item, *, which=None) -> tuple[bool, str]:
    which = which or shutil.which
    spec = next((t for t in TOOL_CACHES if t[0] == item.tool), None)
    if item.tool in TOOL_CACHES_REPORT_ONLY:
        return False, f"{item.tool} cache is report-only; refusing to clean"
    if spec is None:
        return False, f"unknown tool {item.tool!r}; refusing to clean"
    exe = which(spec[0])
    if not exe:
        return False, f"{spec[0]} is no longer on PATH"
    proc = run([exe, *spec[2]], timeout=900)
    if proc.returncode != 0:
        return False, f"{spec[0]} {' '.join(spec[2])} failed: {proc.stderr.strip()[:300]}"
    return True, f"{spec[0]} {' '.join(spec[2])}"


# --------------------------------------------------------------------------- #
# removal
# --------------------------------------------------------------------------- #


def remove_path(path: Path) -> tuple[bool, str]:
    """Delete a directory tree or file. One read-only retry, then give up loudly.

    ``shutil.rmtree``'s ``onerror`` is deprecated and ``onexc`` does not exist
    before 3.12, so neither is used: a read-only-attribute failure gets one
    explicit chmod sweep and a single retry instead.

    Refuses a **link** handed to it directly, on either platform. Not because
    ``shutil.rmtree`` would delete through one — it would not;
    ``shutil._rmtree_islink`` recognises ``IO_REPARSE_TAG_MOUNT_POINT`` and a
    2026-09-26 probe confirmed a junction's target survives removal of its parent —
    but because a path whose identity is a name for somewhere else is not a path
    this tool reasoned about when it sized it.
    """
    try:
        if is_link(os.stat(path, follow_symlinks=False)):
            return False, "refusing to delete a link (symlink/junction)"
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True, ""
    except PermissionError:
        pass
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    try:
        for parent, dirs, files in os.walk(path):
            for name in dirs + files:
                try:
                    os.chmod(os.path.join(parent, name), 0o700)
                except OSError:
                    continue
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    except OSError as exc:
        # rmtree deletes as it walks, so a failure here means the tree is PARTLY
        # gone. Saying so is the honest report (Codex round 1, P2); a bare "kept"
        # would imply the path is intact.
        return False, f"PARTIALLY REMOVED then failed — {type(exc).__name__}: {exc}"
    return True, "removed after chmod retry"


def remove_worktree(repo: Path, item: Item) -> tuple[bool, str]:
    """Remove one worktree via git, archiving its ignored ``_PURPOSE.md`` first.

    ``git worktree remove`` is used **without** ``--force``, so git's own
    cleanliness and lock checks stay in the path as a second net behind this
    script's verdict. The purpose archive reuses ``wt.py`` so an unpublished lane
    draft survives in ``.git/tinyassets-worktrees.log``.

    **A failed archive aborts the removal** when a ``_PURPOSE.md`` exists. The
    first version printed the error and carried on, which defeated the only
    preservation mechanism that file has (Codex round 1, P0) — a disk-full or
    permission error during the archive would have quietly destroyed the draft.
    """
    path = Path(item.path)
    branch = item.branch
    detached = item.reason == "detached_on_remote"
    if not branch and not detached:
        return False, "no branch recorded on the candidate; refusing to remove"

    # Re-verify at the boundary. Inventory and removal are minutes apart on a full
    # pass, and `git worktree remove` re-checks tracked cleanliness but not ignored
    # content, so a unique ignored file written in between would be destroyed on a
    # verdict that predates it (Codex round 1, answer 6).
    try:
        if dirty_paths(path):
            return False, "changed since inventory: the worktree is now dirty"
        stale = unique_ignored_paths(path)
    except Undecidable as exc:
        return False, f"could not re-verify before removal: {exc}"
    if stale:
        return (
            False,
            f"changed since inventory: ignored content now present ({', '.join(stale[:3])})",
        )

    if detached:
        # Same boundary re-check for the detached proof: HEAD can move between
        # inventory and removal, and a purpose file has nowhere to be archived.
        if (path / "_PURPOSE.md").exists():
            return False, "changed since inventory: a _PURPOSE.md appeared"
        try:
            head = git_ok(["rev-parse", "HEAD"], path).strip()
            on_remote = remote_refs_containing(path, head)
        except Undecidable as exc:
            return False, f"could not re-verify before removal: {exc}"
        if not on_remote:
            return False, "changed since inventory: HEAD is no longer on a remote ref"
        proc = run(["git", "worktree", "remove", str(path)], cwd=repo, timeout=120)
        if proc.returncode != 0:
            return False, f"git worktree remove refused: {proc.stderr.strip()[:300]}"
        return True, f"detached worktree removed; {head[:10]} stays on {on_remote[0]}"

    try:
        import wt  # noqa: PLC0415  (sibling script; only needed on the apply path)

        wt._archive_purpose(repo, path, branch, f"dev_hygiene: {item.reason}")
    except Exception as exc:  # noqa: BLE001 — any failure here must fail closed
        if (path / "_PURPOSE.md").exists():
            return False, f"refusing to remove: could not archive _PURPOSE.md ({exc})"
        print(
            f"  note: purpose archive skipped for {path.name} (none present): {exc}",
            file=sys.stderr,
        )

    proc = run(["git", "worktree", "remove", str(path)], cwd=repo, timeout=120)
    if proc.returncode != 0:
        return False, f"git worktree remove refused: {proc.stderr.strip()[:300]}"
    # The disk win is the worktree; a branch ref is ~41 bytes. So the ref is only
    # deleted for `merged_and_clean`, where the content is provably on the base
    # everything integrates into, and always with `-d` so git's own check is the
    # last word.
    #
    # The PR-closed path keeps its ref deliberately. `-D` would have forced it away
    # on the strength of `git log --not --remotes`, which reads LOCAL tracking refs
    # — and `-d` is no better here, since it also accepts "merged into its
    # upstream" from the same local ref. A tracking ref pruned after the PR closed
    # leaves nothing behind (Codex round 1, answer 7), so the ref stays and is the
    # recovery path.
    if item.reason != "merged_and_clean":
        return True, "worktree removed; branch ref kept as the recovery path"
    delete = run(["git", "branch", "-d", branch], cwd=repo, timeout=60)
    detail = (
        "branch deleted"
        if delete.returncode == 0
        else f"branch kept as the recovery ref ({delete.stderr.strip()[:120]})"
    )
    return True, detail


def prune_docker(item: Item, *, keep_gb: float, docker: str = "docker") -> tuple[bool, str]:
    if item.path != "build-cache":
        try:
            fresh = collect_docker_objects(
                min_age_hours=item.docker_age_hours, now=time.time(), docker=docker
            )
            if not any(i.path == item.path and i.removable and i.head == item.head for i in fresh):
                return False, "Docker object changed or is now in use; kept"
            kind, identity = item.path.split(":", 1)
            proc = run([docker, kind, "rm", identity], timeout=120)
            return proc.returncode == 0, (proc.stdout + proc.stderr).strip()
        except (Undecidable, ValueError) as exc:
            return False, str(exc)
    flag = item.prune_flag
    if not flag:
        return False, "no keep-budget flag recorded on the candidate; refusing to prune"
    proc = run(
        [
            docker,
            "builder",
            "prune",
            "--force",
            flag,
            str(int(keep_gb * 1024**3)),
            "--filter",
            f"until={item.docker_age_hours:g}h",
        ],
        timeout=600,
    )
    if proc.returncode != 0:
        return False, f"docker builder prune failed: {proc.stderr.strip()[:300]}"
    return True, proc.stdout.strip() or "pruned"


def apply_removals(
    report: Report,
    repo: Path,
    *,
    keep_gb: float,
    log_path: Path | None,
    max_removals: int = 0,
    preserve: PreservePolicy | None = None,
) -> list[str]:
    """Remove the REMOVE set, at most ``max_removals`` per class (0 = unbounded).

    The per-class cap is the same idea as ``daemon_image_retention.MAX_REMOVALS``
    on the droplet: a pass that runs by itself should never be able to do
    something enormous, so a logic bug costs N items and shows up in the log
    before the next pass. Largest first, so a capped pass still reclaims the most.
    """
    lines: list[str] = []
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    done: dict[str, int] = {}
    order = sorted(report.items, key=lambda i: (i.kind, -i.size_bytes, i.path))
    for item in order:
        if not item.removable:
            continue
        if max_removals and done.get(item.kind, 0) >= max_removals:
            item.verdict = "KEEP"
            item.reason = "deferred_to_next_pass"
            item.detail = f"per-class cap of {max_removals} reached"
            continue
        done[item.kind] = done.get(item.kind, 0) + 1
        if item.kind == "worktree" and item.reason == "preserve_then_remove":
            if preserve is None:
                ok, detail = False, "no preserve policy on this pass; refusing to remove"
            else:
                ok, detail = preserve_and_remove(repo, item, preserve)
        elif item.kind == "worktree":
            ok, detail = remove_worktree(repo, item)
        elif item.kind == "docker":
            try:
                local_docker()
                ok, detail = prune_docker(item, keep_gb=keep_gb)
            except Undecidable as exc:
                ok, detail = False, str(exc)
            if ok:
                item.detail = detail
                if item.path == "build-cache":
                    total = re.search(r"(?:Total reclaimed space:|Total:)\s*(.+)", detail)
                    if total:
                        item.size_bytes = parse_docker_size(total.group(1))
                        if item.size_bytes == 0:
                            item.verdict, item.reason = "KEEP", "nothing_reclaimed"
                            lines.append(f"{stamp} KEPT docker build-cache {detail}")
                            continue
        elif item.kind == "toolcache":
            ok, detail = clean_tool_cache(item)
        else:
            ok, detail = remove_path(Path(item.path))
        if ok:
            lines.append(
                f"{stamp} REMOVED {item.kind} {item.path} {human(item.size_bytes)} "
                f"{item.reason} {detail}".rstrip()
            )
        else:
            item.verdict = "KEEP"
            item.reason = "remove_failed"
            item.detail = detail
            lines.append(f"{stamp} FAILED  {item.kind} {item.path} {detail}")
    if log_path is not None and lines:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
        except OSError as exc:
            report.notes.append(f"log write failed: {exc}")
    return lines


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def render(report: Report, *, verbose: bool) -> str:
    out: list[str] = []
    verb = "removed" if report.applied else "would remove"
    removes = [i for i in report.items if i.removable]
    keeps = [i for i in report.items if not i.removable]
    out.append(
        f"[dev-hygiene] free {report.free_before_gb:.1f} GB -> {report.free_after_gb:.1f} GB; "
        f"{verb} {len(removes)} item(s), {human(report.reclaimable_bytes)}; kept {len(keeps)}"
    )
    for item in sorted(removes, key=lambda i: -i.size_bytes):
        out.append(
            f"  REMOVE  {item.kind:9} {human(item.size_bytes):>9}  {item.path}  "
            f"[{item.reason}] {item.detail}".rstrip()
        )
    if verbose:
        for item in sorted(keeps, key=lambda i: -i.size_bytes):
            out.append(
                f"  KEEP    {item.kind:9} {human(item.size_bytes):>9}  {item.path}  "
                f"[{item.reason}] {item.detail}".rstrip()
            )
    for note in report.notes:
        out.append(f"  note: {note}")
    return "\n".join(out)


def write_summary(path: Path, report: Report, *, escalation: str, classes: tuple[str, ...]) -> None:
    """Record one compact pass summary. The SessionStart hook reads this file
    instead of re-running a scan, so a session start costs no inventory time."""
    removed = [i for i in report.items if i.removable]
    payload = {
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "finished_epoch": time.time(),
        "classes": list(classes),
        "applied": report.applied,
        "free_before_gb": round(report.free_before_gb, 2),
        "free_after_gb": round(report.free_after_gb, 2),
        "removed_count": len(removed) if report.applied else 0,
        "reclaimable_bytes": report.reclaimable_bytes,
        "escalation": escalation,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        report.notes.append(f"summary write failed: {exc}")


def render_escalation(report: Report, threshold_gb: float) -> str:
    """The founder-facing block: what is left, why it was kept, what to decide."""
    out = [
        f"[dev-hygiene] ESCALATION: {report.free_after_gb:.1f} GB free is below the "
        f"{threshold_gb:.0f} GB floor and the disposable set cannot close the gap.",
        "Largest items this tool refused to remove, with the reason it refused:",
    ]
    keeps = [i for i in report.items if not i.removable and i.reason not in EXPECTED_KEEPS]
    for item in sorted(keeps, key=lambda i: (-i.size_bytes, i.path))[:12]:
        out.append(
            f"  {human(item.size_bytes):>9}  {item.path}  [{item.reason}] {item.detail}".rstrip()
        )
    if not keeps:
        out.append("  (nothing reclaimable was found — the space is real project or system data)")
    locked = [i for i in report.items if i.reason == "acl_locked_needs_elevation"]
    if locked:
        out.append(
            f"  PLUS {len(locked)} temp director(ies) this user cannot read, list, or delete "
            "(sandbox-token ACL; size unknown). Clear them with an ELEVATED shell: "
            "powershell -ExecutionPolicy Bypass -File scripts/clear_sandbox_temp_dirs.ps1 -Apply"
        )
    missing = [c for c in CLASSES if c not in {i.kind for i in report.items}]
    if missing:
        out.append(
            f"  NOT inventoried in this pass: {', '.join(missing)}. "
            "The full pass takes a couple of minutes: python scripts/dev_hygiene.py --verbose"
        )
    out.append(
        "Each of these needs a decision this tool will not make for you: a dirty or "
        "unmerged lane to land or abandon, ignored content that exists nowhere else, "
        "or non-repo data. `python scripts/dev_hygiene.py --verbose` shows the full set."
    )
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #


def inventory(
    *,
    repo: Path,
    temp_root: Path,
    classes: tuple[str, ...],
    min_age_hours: float,
    min_age_days: float,
    idle_hours: float,
    docker_keep_gb: float,
    now: float,
    deadline: float | None,
    extra_temp_roots: tuple[Path, ...] = (),
    preserve: PreservePolicy | None = None,
) -> list[Item]:
    items: list[Item] = []
    if "basetemp" in classes:
        items += collect_basetemps(
            temp_root,
            min_age_hours=min_age_hours,
            now=now,
            unprefixed_sessions=suite_test_names(repo),
        )
        for root in extra_temp_roots:
            items += collect_basetemps(
                root, min_age_hours=min_age_hours, now=now, prefixes=DRIVE_ROOT_PREFIXES
            )
    if "scratch" in classes:
        items += collect_repo_scratch(repo, min_age_days=min_age_days, now=now)
    if "worktree" in classes:
        items += collect_worktrees(
            repo, now=now, idle_hours=idle_hours, deadline=deadline, preserve=preserve
        )
    if "docker" in classes:
        try:
            local_docker()
            items += collect_docker_cache(keep_gb=docker_keep_gb, min_age_hours=min_age_hours)
            items += collect_docker_objects(min_age_hours=min_age_hours, now=now)
        except Undecidable as exc:
            items.append(
                Item("docker", "inventory", 0, "KEEP", "docker_inventory_failed", str(exc))
            )
    if "toolcache" in classes:
        items += collect_tool_caches()
    return items


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev_hygiene.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--docker", action="store_true", help="run only Docker hygiene")
    parser.add_argument(
        "--defer-notices",
        action="store_true",
        help="unattended scheduler: leave the one-time Docker disk notice for the next session",
    )
    parser.add_argument(
        "--apply", action="store_true", help="remove the REMOVE set (default: dry-run)"
    )
    parser.add_argument("--dry-run", action="store_true", help="explicit dry-run (the default)")
    parser.add_argument(
        "--if-low-disk",
        type=float,
        metavar="GB",
        help="apply only when free space is below GB; otherwise report only",
    )
    parser.add_argument(
        "--escalate-below",
        type=float,
        default=40.0,
        metavar="GB",
        help="exit 3 with a concrete list when free space is still below GB after the pass",
    )
    parser.add_argument(
        "--classes",
        default=",".join(CLASSES),
        help=f"comma-separated subset of {','.join(CLASSES)} (default: all)",
    )
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=6.0,
        help="basetemp and Docker minimum age (default 6)",
    )
    parser.add_argument(
        "--min-age-days", type=float, default=7.0, help="repo-scratch staleness (default 7)"
    )
    parser.add_argument(
        "--worktree-idle-hours", type=float, default=24.0, help="worktree idleness (default 24)"
    )
    parser.add_argument(
        "--preserve-idle-hours",
        type=float,
        default=48.0,
        help=(
            "preserve-then-remove finished lanes idle this long (merged/closed PR, or no PR "
            "and no commit newer than --preserve-max-commit-age-days); 0 = off (default 48)"
        ),
    )
    parser.add_argument(
        "--preserve-max-commit-age-days",
        type=float,
        default=7.0,
        help="a no-PR lane is finished only when its newest commit is this old (default 7)",
    )
    parser.add_argument(
        "--preserve-max-mb",
        type=float,
        default=50.0,
        help="unique data one lane may preserve; above it the lane is kept (default 50)",
    )
    parser.add_argument(
        "--preserve-dir",
        default="",
        help="where ignored files are copied (default: <git-common-dir>/tinyassets-preserved)",
    )
    parser.add_argument(
        "--docker-keep-gb", type=float, default=8.0, help="build-cache keep budget (default 8)"
    )
    parser.add_argument(
        "--max-removals",
        type=int,
        default=25,
        help="per-class cap on removals in one pass, 0 = unbounded (default 25)",
    )
    parser.add_argument("--budget-seconds", type=float, default=0.0, help="0 = unbounded (default)")
    parser.add_argument(
        "--summary-out",
        default="",
        help="write a compact JSON summary of this pass here (the SessionStart hook reads it)",
    )
    parser.add_argument("--repo", default="", help="repository root (default: this script's repo)")
    parser.add_argument("--temp-root", default="", help="OS temp root (default: %%TEMP%%)")
    parser.add_argument(
        "--extra-temp-root",
        action="append",
        default=None,
        metavar="DIR",
        help=(
            "additional basetemp root, scanned for top-level `ta-*` only "
            "(default: the repo's drive root; pass --extra-temp-root '' for none)"
        ),
    )
    parser.add_argument(
        "--log", default="", help=f"append removals here (default: <repo>/{DEFAULT_LOG})"
    )
    parser.add_argument("--no-log", action="store_true", help="do not write the removal log")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument(
        "--verbose", action="store_true", help="also print every KEEP with its reason"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="print only escalations and actionable notices"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    repo = Path(args.repo).resolve() if args.repo else Path(__file__).resolve().parent.parent
    if not (repo / ".git").exists():
        print(f"[dev-hygiene] not a git repository: {repo}", file=sys.stderr)
        return 2
    temp_root = (
        Path(args.temp_root).resolve() if args.temp_root else Path(os.environ.get("TEMP") or "/tmp")
    )
    if args.extra_temp_root is None:
        # Default: the drive the repo lives on, which is where the short
        # MAX_PATH-dodging basetemps land. Data, not a hard-coded "C:\".
        extra_roots: tuple[Path, ...] = (Path(repo.anchor),) if repo.anchor else ()
    else:
        extra_roots = tuple(Path(r).resolve() for r in args.extra_temp_root if r.strip())
    extra_roots = tuple(r for r in extra_roots if r != temp_root)

    classes = tuple(c.strip() for c in args.classes.split(",") if c.strip())
    unknown = [c for c in classes if c not in CLASSES]
    if unknown:
        print(f"[dev-hygiene] unknown class(es): {', '.join(unknown)}", file=sys.stderr)
        return 2

    if args.docker:
        classes = ("docker",)
    if not math.isfinite(args.min_age_hours) or args.min_age_hours <= 0:
        print("--min-age-hours must be finite and positive", file=sys.stderr)
        return 2
    now = time.time()
    deadline = time.monotonic() + args.budget_seconds if args.budget_seconds > 0 else None
    report = Report(free_before_gb=free_gb(repo))
    report.free_after_gb = report.free_before_gb

    gated = args.if_low_disk is not None and report.free_before_gb >= args.if_low_disk
    if gated:
        report.notes.append(
            f"--if-low-disk {args.if_low_disk:.0f} GB not met "
            f"({report.free_before_gb:.1f} GB free): report only"
        )

    preserve = (
        PreservePolicy(
            idle_hours=args.preserve_idle_hours,
            max_commit_age_days=args.preserve_max_commit_age_days,
            max_bytes=int(args.preserve_max_mb * 1024**2),
            root=Path(args.preserve_dir).resolve() if args.preserve_dir else None,
        )
        if args.preserve_idle_hours > 0
        else None
    )
    report.items = inventory(
        repo=repo,
        temp_root=temp_root,
        extra_temp_roots=extra_roots,
        classes=classes,
        min_age_hours=args.min_age_hours,
        min_age_days=args.min_age_days,
        idle_hours=args.worktree_idle_hours,
        docker_keep_gb=args.docker_keep_gb,
        now=now,
        deadline=deadline,
        preserve=preserve,
    )

    if args.apply and not args.dry_run and not gated:
        log_path = None if args.no_log else (Path(args.log) if args.log else repo / DEFAULT_LOG)
        apply_removals(
            report,
            repo,
            keep_gb=args.docker_keep_gb,
            log_path=log_path,
            max_removals=max(0, args.max_removals),
            preserve=preserve,
        )
        report.applied = True
        report.free_after_gb = free_gb(repo)

    if "docker" in classes and os.name == "nt" and not args.defer_notices:
        report.notes.extend(docker_desktop_notice(repo))

    escalating = args.escalate_below is not None and report.free_after_gb < args.escalate_below
    escalation = render_escalation(report, args.escalate_below) if escalating else ""

    if args.summary_out:
        write_summary(Path(args.summary_out), report, escalation=escalation, classes=classes)

    if args.json:
        print(
            json.dumps(
                {
                    "free_before_gb": round(report.free_before_gb, 2),
                    "free_after_gb": round(report.free_after_gb, 2),
                    "applied": report.applied,
                    "reclaimable_bytes": report.reclaimable_bytes,
                    "escalating": escalating,
                    "notes": report.notes,
                    "items": [asdict(i) for i in report.items],
                },
                indent=2,
            )
        )
    elif not args.quiet:
        print(render(report, verbose=args.verbose))
    elif report.notes:
        print("\n".join(report.notes))
    if escalating:
        print(escalation)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
