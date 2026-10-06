"""Session-start sync gate (Layer 3): catch the off-main / behind trap.

Part of the branch lifecycle automation; see
``docs/design-notes/2026-06-24-branch-lifecycle-automation.md``.

Provider-agnostic. Every session runs this at start (Claude Code wires it as a
SessionStart hook; Codex/Cursor/Cowork call the script). It:

* fetches with --prune so deleted remote branches stop lingering locally,
* warns loudly if the PRIMARY checkout is off ``main`` or behind origin/main —
  the exact "1,209 behind / dirty" condition that triggered this work,
* never mutates the working tree (honors hard rule #13 — advisory only).

Exit code is 0 by default (advisory). ``--strict`` exits 1 when the primary
checkout is off-main or behind, so a hook can surface it more forcefully.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def _force_utf8_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        enc = (getattr(stream, "encoding", None) or "").lower().replace("_", "-")
        if enc == "utf-8":
            continue
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
            except (AttributeError, ValueError, OSError):
                pass


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")


def is_primary_checkout() -> bool:
    """True when cwd is the main checkout, not a linked worktree."""
    common = _run(["git", "rev-parse", "--git-common-dir"])
    top = _run(["git", "rev-parse", "--show-toplevel"])
    if common.returncode != 0 or top.returncode != 0:
        return True  # fail open — treat as primary so we still warn
    git_common = Path(common.stdout.strip()).resolve()
    toplevel = Path(top.stdout.strip()).resolve()
    return git_common == (toplevel / ".git")


def behind_count(base_ref: str) -> int | None:
    proc = _run(["git", "rev-list", "--count", f"HEAD..{base_ref}"])
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def current_branch() -> str:
    proc = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return proc.stdout.strip() if proc.returncode == 0 else "?"


def heal(remote: str, main_name: str) -> list[str]:
    """Put the PRIMARY checkout on current ``main``; return what was done.

    A stale primary checkout loads a stale AGENTS.md into every session, so
    drift here is drift everywhere. Nothing is discarded: a non-main branch or
    uncommitted tracked edits are first committed to a local
    ``backup/primary-autosave-*`` branch (never pushed), then the checkout
    switches to ``main`` and fast-forwards. Untracked files ride along
    untouched. Any refusal leaves the tree as it was and reports why.
    """
    done: list[str] = []
    branch = current_branch()
    dirty = _run(["git", "status", "--porcelain", "--untracked-files=no"]).stdout.strip()
    if branch != main_name or dirty:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = f"backup/primary-autosave-{stamp}"
        if _run(["git", "switch", "-c", backup]).returncode != 0:
            return [f"could not create {backup}; left as is"]
        if dirty:
            _run(["git", "add", "-u"])
            commit = _run(["git", "commit", "-q", "-m",
                           f"autosave: primary checkout edits before syncing to {main_name}"])
            if commit.returncode != 0:
                return [f"could not commit edits to {backup}: "
                        f"{(commit.stderr or commit.stdout).strip()[:200]}; left on {backup}"]
        done.append(f"saved '{branch}'{' + uncommitted edits' if dirty else ''} as {backup}")
        switch = _run(["git", "switch", main_name])
        if switch.returncode != 0:
            return done + [f"could not switch to {main_name}: "
                           f"{(switch.stderr or '').strip()[:200]}; left on {backup}"]
    merged = _run(["git", "merge", "--ff-only", f"{remote}/{main_name}"])
    if merged.returncode != 0:
        return done + [f"could not fast-forward {main_name}: "
                       f"{(merged.stderr or '').strip()[:200]}"]
    done.append(f"{main_name} fast-forwarded to {remote}/{main_name}")
    return done


def main(argv: list[str]) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if primary checkout is off-main or behind",
    )
    parser.add_argument(
        "--heal",
        action="store_true",
        help="in the PRIMARY checkout, save any branch/edits to a local backup branch "
        "and fast-forward main (never in a linked worktree)",
    )
    args = parser.parse_args(argv)

    if not args.no_fetch:
        _run(["git", "fetch", "--prune", args.remote])

    if args.heal and is_primary_checkout():
        steps = heal(args.remote, args.base_ref.split("/", 1)[-1])
        if steps:
            print("Sync gate — healed the primary checkout:")
            for step in steps:
                print("  • " + step)

    branch = current_branch()
    behind = behind_count(args.base_ref)
    primary = is_primary_checkout()
    main_name = args.base_ref.split("/", 1)[-1]

    warnings: list[str] = []
    if primary and branch != main_name:
        warnings.append(
            f"⚠ primary checkout is on '{branch}', not '{main_name}'. "
            f"Never live on a feature branch — `git switch {main_name}` and work from worktrees "
            f"(`python scripts/wt.py new <slug>`)."
        )
    if behind and behind > 0:
        warnings.append(
            f"⚠ {behind} commit(s) behind {args.base_ref}. Run `git pull` (on {main_name}) to sync."
        )

    if warnings:
        print("Sync gate — action recommended:")
        for w in warnings:
            print("  " + w)
        return 1 if args.strict else 0

    print(f"✓ sync gate clean — on '{branch}', up to date with {args.base_ref}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
