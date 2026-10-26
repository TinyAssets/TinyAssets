"""Pre-design cohit-prevention helper.

Run BEFORE drafting any design note proposing a new MCP action, citing an
unfixed BUG-NNN, or pinning a sha. Catches the "primitive already exists"
class of session waste — see
`.claude/agent-memory/dev-2/2026-05-02-check-primitive-exists-script.md`
for the four 2026-05-02 cohit incidents this script is calibrated against.

Usage
-----
    # Has someone already wired this MCP action verb?
    python scripts/check_primitive_exists.py action validate_branch

    # Is BUG-037 already fixed somewhere?
    python scripts/check_primitive_exists.py bug BUG-037

    # Is this sha actually on origin/main, or only on a feature branch?
    python scripts/check_primitive_exists.py sha a288444

Exit codes (each subcommand)
----------------------------
    0  CLEAN     — nothing landed; safe to proceed with new work.
    1  WARNING   — something matched but it may be a false positive
                   (e.g. the verb appears as a docstring; the BUG keyword
                   matches an unrelated commit). Investigate before assuming.
    2  COLLISION — definite hit. Stop and re-scope before drafting.

Each check is shallow — read-only grep + git log against `refs/remotes/origin/main`.
No network calls beyond the local git ref. Stdlib-only.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Force UTF-8 stdout for Windows consoles (matches scripts/claim_check.py).
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_REF = "refs/remotes/origin/main"

# Exit codes are also constants for the test suite.
EXIT_CLEAN = 0
EXIT_WARNING = 1
EXIT_COLLISION = 2


def _git(*args: str, repo_root: Path | None = None) -> str:
    """Run a git command and return stdout. Empty string on failure."""
    root = repo_root or REPO_ROOT
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False, capture_output=True, text=True, encoding="utf-8",
        )
    except (FileNotFoundError, OSError):
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout


# ──────────────────────────────────────────────────────────────────────────────
# Subcommand: action <verb>
# ──────────────────────────────────────────────────────────────────────────────


# Action-map files we expect to find dispatch tables / handler functions in.
# Hardcoded list — narrower than `tinyassets/api/*.py` because not every module
# defines actions, and broader-than-needed glob inflates false-positive rate.
_ACTION_MAP_FILES = (
    "tinyassets/api/branches.py",
    "tinyassets/api/runs.py",
    "tinyassets/api/workflow_actions.py",
    "tinyassets/api/wiki.py",
    "tinyassets/api/goals.py",
    "tinyassets/api/universe.py",
    "tinyassets/api/status.py",
    "tinyassets/api/evaluation.py",
    "tinyassets/api/goals.py",
    "tinyassets/api/prompts.py",
)


def check_action(verb: str, repo_root: Path | None = None) -> int:
    """Detect collisions for a proposed MCP action verb.

    Looks for three shapes on origin/main:
    1. `_ext_<verb>` handler function name
    2. `"<verb>": _ext_*` dispatch-table mapping
    3. `def <verb>(` standalone function (weaker signal)

    Returns EXIT_CLEAN / WARNING / COLLISION.
    """
    root = repo_root or REPO_ROOT

    if not re.match(r"^[a-z_][a-z0-9_]*$", verb):
        print(
            f"[ERROR] action verb '{verb}' is not a valid identifier "
            "(lowercase + underscores only).",
            file=sys.stderr,
        )
        return EXIT_COLLISION

    handler_pattern = f"_ext_{verb}\\b"
    map_pattern = f'["\']{verb}["\']\\s*:\\s*_ext_'
    def_pattern = f"^def {verb}\\b"

    hits: list[tuple[str, str, str]] = []  # (kind, file, line)
    for relpath in _ACTION_MAP_FILES:
        # Use git show to read the file at origin/main (not the working tree —
        # that may have unstaged drafts).
        raw = _git("show", f"{MAIN_REF}:{relpath}", repo_root=root)
        if not raw:
            continue
        for lineno, line in enumerate(raw.splitlines(), 1):
            if re.search(handler_pattern, line):
                hits.append(("handler", f"{relpath}:{lineno}", line.strip()))
            if re.search(map_pattern, line):
                hits.append(("map_entry", f"{relpath}:{lineno}", line.strip()))
            if re.search(def_pattern, line):
                hits.append(("def", f"{relpath}:{lineno}", line.strip()))

    if not hits:
        print(f"CLEAN — no '{verb}' action handler/map-entry on {MAIN_REF}.")
        return EXIT_CLEAN

    has_definite = any(kind in {"handler", "map_entry"} for kind, _, _ in hits)
    severity = EXIT_COLLISION if has_definite else EXIT_WARNING
    label = "COLLISION" if has_definite else "WARNING"

    print(f"{label} — found {len(hits)} hit(s) for action verb '{verb}':")
    for kind, loc, line in hits:
        print(f"  [{kind}] {loc}: {line[:120]}")
    print()
    if has_definite:
        print(
            f"This action appears to already be wired on {MAIN_REF}. "
            "Read the handler before proposing a new one."
        )
    else:
        print(
            f"Symbol '{verb}' appears in source but not as an MCP action "
            "handler/map-entry. Spot-check before assuming collision."
        )
    return severity


# ──────────────────────────────────────────────────────────────────────────────
# Subcommand: bug <BUG-NNN>
# ──────────────────────────────────────────────────────────────────────────────


_BUG_ID_RE = re.compile(r"^BUG-(\d{1,4})$", re.IGNORECASE)


def check_bug(bug_id: str, repo_root: Path | None = None) -> int:
    """Detect whether a BUG-NNN identifier already has a fix landed on main.

    Three signals:
    1. `git log --grep="BUG-NNN"` on origin/main → any matching commit.
    2. `git log --grep="<keyword>"` on origin/main → keyword-mode commits
       (looser; warning-tier if the bug-id grep is empty but keyword hits).
    3. local file mentions of BUG-NNN — warning-tier signal.
    """
    root = repo_root or REPO_ROOT

    m = _BUG_ID_RE.match(bug_id)
    if not m:
        print(
            f"[ERROR] '{bug_id}' is not a valid BUG-NNN identifier "
            "(expected e.g. 'BUG-037').",
            file=sys.stderr,
        )
        return EXIT_COLLISION
    canonical_id = f"BUG-{m.group(1).zfill(3)}"

    direct_hits_raw = _git(
        "log", MAIN_REF, "--grep", canonical_id, "--oneline",
        "-i", "-n", "20",
        repo_root=root,
    )
    direct_hits = [ln for ln in direct_hits_raw.splitlines() if ln.strip()]

    if direct_hits:
        print(
            f"COLLISION — found {len(direct_hits)} commit(s) on {MAIN_REF} "
            f"mentioning {canonical_id}:"
        )
        for ln in direct_hits[:10]:
            print(f"  {ln}")
        if len(direct_hits) > 10:
            print(f"  ... + {len(direct_hits) - 10} more")
        print()
        print(
            f"This bug may already be fixed. Check `git log --grep "
            f"{canonical_id} -p` for the patch before proposing a new fix."
        )
        return EXIT_COLLISION

    # No direct-id hit. Try a few weaker signals.
    weak_hits: list[str] = []

    # Signal: BUG-NNN mentioned anywhere in the working tree (docs, specs).
    grep_raw = _git(
        "grep", "--no-color", "-l", canonical_id,
        repo_root=root,
    )
    if grep_raw.strip():
        for ln in grep_raw.splitlines():
            ln = ln.strip()
            if ln:
                weak_hits.append(f"file mentions {canonical_id}: {ln}")

    if weak_hits:
        print(
            f"WARNING — no commit on {MAIN_REF} grep-matches '{canonical_id}', "
            f"but {len(weak_hits)} working-tree file(s) mention it:"
        )
        for ln in weak_hits[:10]:
            print(f"  {ln}")
        print()
        print(
            "The bug may be filed but unfixed, or fixed without naming the ID "
            "in the commit message (BUG-037 was an example: fix sha a288444 "
            "did not include the ID in its subject). Read the file mentions "
            "and recent commits in the same component before drafting a new fix."
        )
        return EXIT_WARNING

    print(f"CLEAN — no commit or file reference to {canonical_id} on {MAIN_REF}.")
    return EXIT_CLEAN


# ──────────────────────────────────────────────────────────────────────────────
# Subcommand: sha <sha>
# ──────────────────────────────────────────────────────────────────────────────


_SHA_RE = re.compile(r"^[0-9a-f]{4,40}$", re.IGNORECASE)


def check_sha(sha: str, repo_root: Path | None = None) -> int:
    """Verify a sha is actually on origin/main.

    Catches the "pinned to feature branch, not main" failure mode (e.g. a
    design-note frontmatter or memory file cites a sha that hasn't landed).
    Uses `git merge-base --is-ancestor`.
    """
    root = repo_root or REPO_ROOT

    if not _SHA_RE.match(sha):
        print(
            f"[ERROR] '{sha}' does not look like a git sha "
            "(expected 4-40 hex chars).",
            file=sys.stderr,
        )
        return EXIT_COLLISION

    # Resolve the sha first to a full identifier — catches typos.
    resolved = _git("rev-parse", "--verify", f"{sha}^{{commit}}", repo_root=root).strip()
    if not resolved:
        print(
            f"COLLISION — sha '{sha}' does not resolve to any commit in this "
            "checkout. Either it's a typo, or the commit lives in a remote "
            "you haven't fetched."
        )
        return EXIT_COLLISION

    # is-ancestor exits 0 if YES, 1 if NO. We bypass _git() because we need
    # the exit code, not stdout.
    rc = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", resolved, MAIN_REF],
        check=False, capture_output=True, text=True,
    ).returncode

    if rc == 0:
        # On main — find a friendly subject line for the report.
        subject = _git(
            "log", "-1", "--format=%h %s", resolved, repo_root=root,
        ).strip()
        print(
            f"CLEAN — sha {sha} is on {MAIN_REF}.\n"
            f"  {subject}"
        )
        return EXIT_CLEAN

    # Not on main. Report which branches DO have it (best-effort hint).
    branches_raw = _git(
        "branch", "-a", "--contains", resolved, repo_root=root,
    )
    branches = [b.strip().lstrip("* ") for b in branches_raw.splitlines() if b.strip()]
    print(
        f"COLLISION — sha {sha} is NOT on {MAIN_REF}. "
        "Citing it in a frontmatter / memory / design note will silently "
        "rot the moment readers expect it to be merged."
    )
    if branches:
        print(f"  Found on {len(branches)} branch(es):")
        for b in branches[:10]:
            print(f"    {b}")
        if len(branches) > 10:
            print(f"    ... + {len(branches) - 10} more")
    return EXIT_COLLISION


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="check_primitive_exists",
        description=(
            "Verify a proposed primitive (action / bug / sha) doesn't already "
            "exist on origin/main before drafting work. Run BEFORE design "
            "notes / new MCP actions / sha-citing frontmatter."
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp_action = sub.add_parser(
        "action",
        help="Check whether an MCP action verb is already wired.",
    )
    sp_action.add_argument("verb", help="Lowercase action verb, e.g. 'validate_branch'.")

    sp_bug = sub.add_parser(
        "bug",
        help="Check whether a BUG-NNN identifier has a landed fix.",
    )
    sp_bug.add_argument("bug_id", help="Canonical id, e.g. 'BUG-037'.")

    sp_sha = sub.add_parser(
        "sha",
        help="Verify a sha is on origin/main (not a feature branch).",
    )
    sp_sha.add_argument("sha", help="Short or long git sha.")

    args = ap.parse_args(argv)

    if args.cmd == "action":
        return check_action(args.verb)
    if args.cmd == "bug":
        return check_bug(args.bug_id)
    if args.cmd == "sha":
        return check_sha(args.sha)
    return EXIT_COLLISION


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
