#!/usr/bin/env python3
"""Pre/PostToolUse hook: refuse shell commands that cost time on 2026-10-21.

Each check replaces a rule line. Exit 2 with stderr is fed back to the agent.

Pre (refuse before running):
  * a heredoc whose body holds a backslash -- it reached the file unescaped
    and broke a patch; file contents go through the Write tool;
  * `pkill -f PATTERN` without the `[x]yz` bracket trick -- the pattern also
    sits in the command line of the shell running it, so it killed its own
    ssh session;
  * posting a `Drain-Review-Head: <sha>` receipt for a sha that is not the
    PR's current head -- the gate refuses it later, after a wasted cycle.
Post (after running):
  * `git push` whose branch did not land on origin -- a silent push failure
    looked like a pushed fix.

Network failures pass: the merge gate still refuses a stale receipt, so this
hook only saves time and must never block on its own outage.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)\n\s*\2\s*(?:\n|$)", re.S)
_PKILL = re.compile(r"\bpkill\b([^|;&\n]*)")
_RECEIPT_HEAD = re.compile(r"Drain-Review-Head: ([0-9a-f]{40})")
_GH_POST = re.compile(r"\bgh\s+pr\s+(?:comment|review|edit)\b([^|;&\n]*)")
_BODY_FILE = re.compile(r"(?:--body-file|-F)\s+(\"[^\"]+\"|'[^']+'|\S+)")


def _git(cwd: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                             text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def heredoc_backslash(command: str) -> str | None:
    for match in _HEREDOC.finditer(command):
        if "\\" in match.group(3):
            return ("a heredoc body holds a backslash, and heredocs on this host "
                    "unescape it; write the file with the Write tool instead")
    return None


def self_matching_pkill(command: str) -> str | None:
    for match in _PKILL.finditer(command):
        args = match.group(1)
        if re.search(r"(?:^|\s)-\w*f\b|--full\b", args) and "[" not in args:
            return ("`pkill -f PATTERN` also matches the shell running it (an ssh "
                    "session died this way); write the pattern as '[p]attern'")
    return None


def stale_receipt(command: str, cwd: Path) -> str | None:
    post = _GH_POST.search(command)
    if not post:
        return None
    text = command
    body_file = _BODY_FILE.search(post.group(1))
    if body_file:
        path = Path(body_file.group(1).strip("\"'"))
        try:
            text += (path if path.is_absolute() else cwd / path).read_text(encoding="utf-8")
        except OSError:
            pass
    cited = _RECEIPT_HEAD.search(text)
    if not cited:
        return None
    number = re.search(r"^\s*(\d+)\b", post.group(1))
    try:
        out = subprocess.run(
            ["gh", "pr", "view", *([number.group(1)] if number else []),
             "--json", "headRefOid", "-q", ".headRefOid"],
            cwd=cwd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    head = out.stdout.strip()
    if out.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", head):
        return None
    if head != cited.group(1):
        return (f"receipt names {cited.group(1)[:12]} but the PR head is {head[:12]}; "
                "review the current head (or push first), then post")
    return None


def _path(cwd: Path, raw: str) -> Path:
    # Git Bash spells C:\x as /c/x; Windows Python would read that as C:\c\x.
    drive = re.match(r"^/([a-zA-Z])(/.*)?$", raw)
    if os.name == "nt" and drive:
        raw = f"{drive.group(1)}:{drive.group(2) or '/'}"
    return (cwd / raw).resolve()


def push_did_not_land(command: str, cwd: Path) -> str | None:
    for segment in re.split(r"&&|\|\||;|\n", command):
        try:
            words = shlex.split(segment, posix=True)
        except ValueError:
            continue
        if words[:1] == ["cd"] and len(words) > 1:
            cwd = _path(cwd, words[1])
            continue
        if words[:1] != ["git"] or "push" not in words:
            continue
        rest = words[words.index("push") + 1:]
        if any(":" in w or w in ("--dry-run", "-n", "--delete", "-d", "--tags") for w in rest):
            continue
        where = cwd
        if "-C" in words[:words.index("push")]:
            where = _path(cwd, words[words.index("-C") + 1])
        branch = _git(where, "rev-parse", "--abbrev-ref", "HEAD")
        head = _git(where, "rev-parse", "HEAD")
        if not branch or branch == "HEAD" or not head:
            continue
        remote = _git(where, "ls-remote", "origin", f"refs/heads/{branch}")
        if remote is None:
            continue
        landed = remote.split()[0] if remote else "(absent)"
        if landed != head:
            return (f"push did not land: origin/{branch} is {landed[:12]}, "
                    f"HEAD is {head[:12]}; read the push output and push again")
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    cwd = Path(payload.get("cwd") or ".")
    if payload.get("hook_event_name") == "PostToolUse":
        reasons = [push_did_not_land(command, cwd)]
    elif payload.get("tool_name") == "Bash":
        reasons = [heredoc_backslash(command), self_matching_pkill(command),
                   stale_receipt(command, cwd)]
    else:
        reasons = [self_matching_pkill(command), stale_receipt(command, cwd)]
    reasons = [r for r in reasons if r]
    if not reasons:
        return 0
    print("bash_guard: " + "; ".join(reasons), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
