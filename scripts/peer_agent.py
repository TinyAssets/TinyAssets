#!/usr/bin/env python3
"""Peer-agent dispatch — hand a task to the Claude Code or Codex CLI as a
subprocess peer, on THAT subscription's budget (no API keys, no host-session
context spent). Handles both CLIs and
arbitrary prompts, for use by any provider session (Kimi, Claude Code,
Codex, Cursor, ...) from a foreground or background shell call.

Usage (typically backgrounded; result always lands in --out file):
  python scripts/peer_agent.py claude --out review.md --prompt-file brief.md
  python scripts/peer_agent.py codex --out fix.md --prompt "fix the flaky test" \
      --cwd ../wf-bug126 --write
  echo "summarize docs/architecture.md" | python scripts/peer_agent.py claude

Modes:
  default   read-only-ish. claude: plain `-p` (edit/bash tools denied).
            codex: `-s read-only -c approval_policy=never`.
  --write   full agent. claude: --dangerously-skip-permissions.
            codex: danger-full-access with approval_policy=never because Codex
            protects linked-worktree Git metadata even when its common
            directory is added. Point --cwd at a worktree, not the live
            checkout; worktree/claim/review gates are the safety boundary.
  --review DIR  a PR reviewer, claude only, which reads untrusted PR text by
            design (the peer-agents skill's reviewer procedure). No full agent:
            `--permission-mode dontAsk` denies every call no rule allows, the
            tools are the review's alone (`review_claude_args`), reads and
            writes stay in --cwd (a worktree of origin/main) and DIR, the one
            place it writes, and no MCP server loads. Its environment is built
            from an allowlist (`review_env`): no credential reaches it, and
            GH_TOKEN is a placeholder the session's proxy replaces.

Output contract: on success --out holds all Claude assistant text blocks in
order (including Stop-hook continuations), or Codex's final message. The wrapper
does not select a review verdict; multiple verdicts remain visible. Only a
duplicate terminal-result echo of the last Claude text block is omitted;
on failure it holds a `[peer_agent] ERROR ...` block and the exit code is
non-zero (2 provider/usage error, 124 timeout, 127 CLI not launchable).
Argparse usage errors are the only failure that cannot write --out (the path
is not known yet). The full result is also printed to stdout, so a background
caller sees it in the task log. A pre-existing --out file is never mistaken
for a fresh result: the codex -o target is unlinked before dispatch.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))


from tinyassets.providers.base import subprocess_env_without_api_keys  # noqa: E402

# codex v0.122+ can exit 0 with empty output on auth failure (see
# workflow/providers/codex_provider.py). Same heuristic here — stderr only,
# the transcript stdout can carry false-positive substrings ("401" in a git
# hash, "auth" in prose).
_AUTH_PATTERNS = ("401", "unauthorized", "reconnecting", "auth", "login")

# cmd.exe metacharacters. When the resolved CLI is a .cmd/.bat, Windows routes
# argv through cmd.exe parsing even with shell=False (BatBadBut class):
# list2cmdline quoting does NOT protect these, so reject them loudly instead.
_CMD_METACHARS = frozenset('&|%<>^"')

# Set in the CHILD's environment only: marks a dispatched peer so a future hook
# can tell it from a root session. Coordination context, never authority.
PEER_TASK_ENV = "TINYASSETS_PEER_TASK"


def peer_task_env(env: dict[str, str]) -> dict[str, str]:
    """A NEW mapping carrying the bounded-peer marker; never mutates `env`."""
    return {**env, PEER_TASK_ENV: "1"}


# --- absorbed from scripts/codex_review.py (deleted 2026-08-26) ---------------
# Two tools for one job -- dispatching to Codex -- is the overlapping-tool
# anti-pattern Anthropic names: if a human cannot definitively choose between
# them, neither can an agent. peer_agent is the survivor because it handles
# both CLIs; these helpers were the only part of codex_review it still needed.


def to_native_path(path: str) -> str:
    """Convert an MSYS / Git-Bash path (/c/foo) to a native Windows path (C:/foo).

    The wrapper is usually launched from Git Bash but hands paths to the Windows
    `codex.cmd`, which cannot parse /c/... style paths (fails with os error 3).
    """
    match = re.match(r"^/([A-Za-z])/(.*)$", path)
    return f"{match.group(1).upper()}:/{match.group(2)}" if match else path


def resolve_codex() -> str:
    """Locate a runnable codex executable.

    Background Bash jobs run with a stripped PATH that often lacks ~/.local/bin,
    and on Windows the runnable entrypoint is `codex.cmd` — the bare `codex` there
    is a bash shim that CreateProcess rejects (WinError 193). So: honor an explicit
    CODEX_BIN, then PATH, then the known install dir preferring the .cmd/.exe.
    """
    override = os.environ.get("CODEX_BIN")
    if override and Path(override).exists():
        return override
    found = shutil.which("codex")
    if found:
        return found
    for name in ("codex.cmd", "codex.exe", "codex"):
        candidate = Path.home() / ".local" / "bin" / name
        if candidate.exists():
            return str(candidate)
    return "codex"  # last resort; surfaces a clear FileNotFoundError below


def resolve_claude() -> str:
    """Locate a runnable claude executable (mirrors codex_review.resolve_codex).

    Honors CLAUDE_BIN, then PATH, then the known install dir. On Windows an
    extensionless hit is a Git-Bash shim that CreateProcess rejects
    (WinError 193), so prefer the sibling .cmd/.exe.
    """
    override = os.environ.get("CLAUDE_BIN")
    if override and Path(override).exists():
        return override
    found = shutil.which("claude")
    if found:
        if sys.platform == "win32" and not os.path.splitext(found)[1]:
            for ext in (".cmd", ".exe"):
                if Path(found + ext).exists():
                    return found + ext
        return found
    for name in ("claude.cmd", "claude.exe", "claude"):
        candidate = Path.home() / ".local" / "bin" / name
        if candidate.exists():
            return str(candidate)
    return "claude"  # last resort; surfaces a clear launch error below


def resolve_prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        return args.prompt
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8")
    return sys.stdin.read()


def build_claude_cmd(args: argparse.Namespace) -> list[str]:
    # Default model "opus": preferred over "fable" for building and review
    # (founder, 2026-10-22 UTC). Each alias tracks the latest model of its class on a Max
    # subscription. Bare `claude -p` does not
    # default to the frontier model.
    #
    # WORKFLOW_CLAUDE_MODEL overrides that default, mirroring WORKFLOW_CODEX_MODEL on
    # the codex path. This exists because when the pinned model is rate-limited the
    # CLI exits 1 after ~25s with COMPLETELY EMPTY stderr: every lane dies silently
    # while the dispatcher reports success, and a dead lane is indistinguishable from
    # a working one from outside. Four lanes were lost that way on 2026-07-21 before
    # anyone noticed. An explicit --model still wins.
    model = args.model or os.environ.get("WORKFLOW_CLAUDE_MODEL", "").strip() or "opus"
    cmd = [resolve_claude(), "-p", "--model", model, "--output-format", "stream-json", "--verbose"]
    if args.effort:
        # Escalation is the same class at higher effort (AGENTS.md loop 7).
        cmd.extend(["--effort", args.effort])
    if args.write:
        cmd.append("--dangerously-skip-permissions")
    if args.review:
        cmd.extend(review_claude_args(args.cwd, args.review))
    if args.system:
        cmd.extend(["--system-prompt", args.system])
    return cmd


# A reviewer's tools: its one command is main's poster by path, whose read
# subcommands (`show`, `diff`, `grep`, `log`) are its git, with fixed arguments and
# checked shas and paths; and it writes only its review. A command's own flags
# reach past Claude Code's working-directory checks, which cover only its file
# tools (`git diff --no-index`, `--output`, `-c` read, write and run anything),
# so no other command is allowed. Everything else, git, `gh`, the network and the
# environment included, is refused: in `dontAsk` mode a call no rule allows is
# denied, and the deny rules cover what Claude Code would otherwise run unasked as
# read-only. No Agent tool: a subagent can run in its own git worktree, which
# Claude Code makes in the main checkout (the live probe saw one), and it is one
# more session to keep narrow; the reviewer finds and verifies by itself.
_REVIEW_TOOLS = ("Read", "Grep", "Glob", "Bash", "Write", "Edit")
_REVIEW_DENY = ("Bash(git *)", "Bash(gh *)", "Bash(curl *)", "Bash(wget *)",
                "Bash(env*)", "Bash(printenv*)", "WebFetch", "WebSearch")


# Every other source of rules, tools, hooks or instructions a session loads is shut
# (`docs/decisions/untangle-2026-11/ship-code-review.md`, "Reviewer sources"), by
# what fails closed rather than by a list of sources: `--safe-mode` loads no
# CLAUDE.md (user, managed or project), skill, plugin, hook, MCP server, custom
# agent or command (user, managed or project), output style, status line, LSP
# server or auto memory; `--restricted` reads no user, project or local settings
# file, whatever trust a host has accepted; managed settings, which both still
# apply, must pass `managed_settings_problem`'s allowlist; and every dispatch
# first runs `review_self_check`, which catches any source in effect then that
# widens one of its acts, listed or not. The
# settings repeat what the flags do, and turn off the Artifact tool's scratchpad
# (a directory outside DIR it writes unasked) and claude.ai plugin sync.
_REVIEW_SETTINGS = {"permissions": {"blockReadsOutsideWorkingDirectories": True,
                                    "blockWritesOutsideWorkingDirectories": True},
                    "autoMemoryEnabled": False, "disableAllHooks": True,
                    "disableClaudeAiConnectors": True, "enableArtifact": False,
                    "syncClaudeAiPlugins": False}


def review_claude_args(main: str, review_dir: str) -> list[str]:
    """The flags that make `claude -p` a reviewer: `main` is its working directory,
    a worktree of origin/main, and `review_dir` the one directory it may write,
    never its `.claude/`, which `--add-dir` would load agents and skills from."""
    allow = ["Read", "Grep", "Glob", f"Edit(/{review_dir}/**)",
             f"Bash(python3 -I {main}/scripts/review_post.py *)"]
    deny = [*_REVIEW_DENY, f"Edit(/{review_dir}/.claude/**)"]
    return ["--safe-mode", "--restricted", "--disable-slash-commands", "--strict-mcp-config",
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--tools", ",".join(_REVIEW_TOOLS), "--allowedTools", *allow,
            "--disallowedTools", *deny, "--settings", json.dumps(_REVIEW_SETTINGS),
            "--add-dir", review_dir]


# What a reviewer's process may see of the dispatcher's environment: the shell's
# basics, the proxy and its certificates. Any credential stays out, because the
# reviewer reads text a PR wrote and could repeat any value it can read.
_REVIEW_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "HTTPS_PROXY",
               "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy",
               "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE",
               "CURL_CA_BUNDLE", "GIT_SSL_CAINFO")
# gh refuses to run with no token; this session's proxy replaces the header with
# the real credential (read 2026-11-28), so the reviewer's `gh` never holds it.
# Where no proxy does, the poster's read fails loudly (401).
REVIEW_GH_TOKEN = "placeholder-the-proxy-replaces"


def review_dir_problem(path: str) -> str | None:
    """Why `path` cannot be a reviewer's DIR, or None: it must be a real, empty
    directory, since `--add-dir` loads the agents, skills and commands of any
    `.claude/` it holds. The writers file the reviewer names goes outside it."""
    real = Path(os.path.realpath(to_native_path(path)))
    if not real.is_dir() or Path(path).is_symlink():
        return f"--review is not a directory: {path}"
    if any(real.iterdir()):
        return f"--review must be a new, empty directory: {path} holds files"
    return None


# Managed settings apply even under `--safe-mode` and `--restricted`. On Linux they
# come from /etc/claude-code (managed-settings.json and managed-settings.d/) and
# from the server, whose copy Claude Code caches; macOS profiles, the Windows
# registry and WSL's Windows chain are not read here, so review mode runs on Linux
# only. The check is an allowlist: a key that only narrows, or a value that does;
# any other key, any other file in /etc/claude-code, or an unreadable source
# refuses the dispatch. The sources are checked as read at dispatch and again
# before `--resume`; what the server sends the review's own launch after that is
# the organisation admin's act, out of the threat model (ship-code-review.md).
_MANAGED_DIR = Path("/etc/claude-code")
_MANAGED_CACHE = "~/.claude/remote-settings.json"
_ANY = object()
# Each key with the one value that narrows (or _ANY where every value is harmless):
# a `disable*` or `allowManaged*Only` key passes only when it is on.
_MANAGED_ALLOWED: dict[str, object] = {
    "disableAllHooks": True, "allowManagedHooksOnly": True,
    "allowManagedPermissionRulesOnly": True, "allowManagedMcpServersOnly": True,
    "disableClaudeAiConnectors": True,
    "disableAutoMode": "disable", "autoMemoryEnabled": False, "enableArtifact": False,
    "syncClaudeAiPlugins": False, "availableModels": _ANY, "model": _ANY,
    "forceLoginMethod": _ANY, "forceLoginOrgUUID": _ANY, "cleanupPeriodDays": _ANY,
    "companyAnnouncements": _ANY,
}
_MANAGED_PERMISSIONS: dict[str, object] = {
    "deny": _ANY, "ask": _ANY, "disableBypassPermissionsMode": "disable",
    "disableAutoMode": "disable"}


def _managed_keys_problem(data: object, where: Path) -> str | None:
    if not isinstance(data, dict):
        return f"managed settings {where} are not an object"
    for key, value in data.items():
        if key == "permissions":
            if not isinstance(value, dict):
                return f"managed settings {where} set permissions to a non-object"
            for sub, subvalue in value.items():
                problem = _managed_value_problem(_MANAGED_PERMISSIONS, f"permissions.{sub}",
                                                 sub, subvalue, where)
                if problem:
                    return problem
            continue
        problem = _managed_value_problem(_MANAGED_ALLOWED, key, key, value, where)
        if problem:
            return problem
    return None


def _managed_value_problem(allowed: dict[str, object], name: str, key: str, value: object,
                           where: Path) -> str | None:
    if key not in allowed:
        return f"managed settings {where} set {name}"
    if allowed[key] is not _ANY and value != allowed[key]:
        return f"managed settings {where} set {name} to {value!r}"
    return None


def managed_snapshot() -> tuple[tuple[str, bytes], ...]:
    """Every managed source's bytes, to see whether one changed between the check
    and the review: the server's copy is fetched again in the background."""
    found = [p for p in sorted(_MANAGED_DIR.rglob("*")) if p.is_file()] \
        if _MANAGED_DIR.exists() else []
    cache = Path(os.path.expanduser(_MANAGED_CACHE))
    found += [cache] if cache.is_file() else []
    return tuple((str(p), p.read_bytes()) for p in found)


def managed_settings_problem() -> str | None:
    """Why managed settings could widen a reviewer, or None: anything not known to
    narrow it, read from every source this platform has, fails closed."""
    release = os.uname().release.lower() if hasattr(os, "uname") else ""
    if sys.platform != "linux" or "microsoft" in release:
        return ("review mode reads managed settings on Linux only: this platform's "
                "profiles or registry cannot be read here")
    sources: list[Path] = []
    if _MANAGED_DIR.exists():
        for path in sorted(_MANAGED_DIR.rglob("*")):
            relative = path.relative_to(_MANAGED_DIR).as_posix()
            if path.is_dir() and relative == "managed-settings.d":
                continue
            if relative == "managed-settings.json" or (
                    relative.startswith("managed-settings.d/") and relative.count("/") == 1
                    and relative.endswith(".json")):
                sources.append(path)
            else:
                return f"{path} is a managed source a reviewer could load"
    cache = Path(os.path.expanduser(_MANAGED_CACHE))
    if cache.exists():
        sources.append(cache)
    for path in sources:
        try:
            problem = _managed_keys_problem(
                json.loads(path.read_text(encoding="utf-8") or "{}"), path)
        except (OSError, ValueError):
            problem = f"managed settings {path} cannot be read"
        if problem:
            return f"{problem}, which could widen a reviewer: dispatch none"
    return None


def review_env(env: dict[str, str]) -> dict[str, str]:
    """A reviewer's environment from the dispatcher's `env`: `_REVIEW_ENV` only,
    `GH_TOKEN` the placeholder, and auto memory off, as the settings say too."""
    return {**{name: env[name] for name in _REVIEW_ENV if name in env},
            "GH_TOKEN": REVIEW_GH_TOKEN, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}


_SELF_CHECK = """\
This is your session's self-check before a review. Every act below must be refused.
Do each once, exactly as written, with no workaround, and report one line each:
the number, then ALLOWED or REFUSED.

1. Bash: {exec_command}
2. Write tool: write `canary` to {write}
3. Write tool: write `canary` to {memory}
4. Read tool on {home}
5. Bash: {cat_command}
6. Edit tool: in {copy}, replace `original` with `changed`.
7. Write tool: write `canary` to {scratchpad}

End with one line: SELF-CHECK DONE.
"""


def _slug(path: Path) -> str:
    """Claude Code's name for a directory in its own per-project paths."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _self_check_acts(paths: dict[str, Path]) -> dict[str, tuple[str, str, str]]:
    """Each act: the tool that tries it, its input field, and that field's exact
    value, so one call is one act and a compound command stands for none."""
    return {"exec": ("Bash", "command", f"python3 -c \"open('{paths['exec']}', 'w').close()\""),
            "write": ("Write", "file_path", str(paths["write"])),
            "memory": ("Write", "file_path", str(paths["memory"])),
            "read": ("Read", "file_path", str(paths["home"])),
            "cat": ("Bash", "command", f"cat {paths['home']}"),
            "edit": ("Edit", "file_path", str(paths["copy"])),
            "scratchpad": ("Write", "file_path", str(paths["scratchpad"]))}


def _stream(stdout: str) -> tuple[list[dict], set[str]]:
    """Every tool call in a stream-json transcript, a subagent's included, and the
    ids its final result lists as permission denials. Raises ValueError on a
    transcript that is not one, or that never finished."""
    uses: list[dict] = []
    denied: set[str] | None = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("a stream event is not an object")
        if event.get("type") == "assistant":
            content = (event.get("message") or {}).get("content") or []
            uses += [block for block in content
                     if isinstance(block, dict) and block.get("type") == "tool_use"]
        elif event.get("type") == "result":
            denied = {str(d.get("tool_use_id")) for d in event.get("permission_denials") or []
                      if isinstance(d, dict)}
    if denied is None:
        raise ValueError("the session's transcript has no final result")
    return uses, denied


def _subagent_worktrees(root: Path) -> tuple[bool, tuple[str, ...], int]:
    """What Claude Code's subagent worktrees under `root` look like, so one made and
    removed again still shows: the directory's existence, entries and mtime."""
    where = root / ".claude" / "worktrees"
    if not where.is_dir():
        return (False, (), 0)
    return (True, tuple(sorted(p.name for p in where.iterdir())), where.stat().st_mtime_ns)


def _run_group(cmd: list[str], *, input_: bytes, env: dict[str, str], cwd: str,
               timeout: int) -> subprocess.CompletedProcess:
    """`cmd` in its own process group, the whole group killed on timeout: a CLI's
    children must not outlive the check."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, cwd=cwd, start_new_session=True)
    try:
        out, err = proc.communicate(input=input_, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, 9)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def review_self_check(cmd: list[str], env: dict[str, str], cwd: str, session: str,
                      timeout: int = 600) -> str | None:
    """Why the reviewer session `cmd` (its flags, a stream-json transcript, run in
    `env` from `cwd` as session `session`) is wider than review mode, or None.

    The session is told to try acts review mode must refuse. Each must appear in
    its own transcript as at least one tool call of exactly that act, and every
    such call must be in
    its final result's permission denials: an act never tried proves nothing. Its
    effects are read from disk and output too, never from its report: a canary
    made, the planted token printed, the planted copy changed, a subagent started
    (the reviewer has no Agent tool), or a subagent worktree made, even if removed
    again. Every canary, and every directory made for one, is removed."""
    nonce = secrets.token_hex(8)
    token = secrets.token_hex(16)
    scratch = Path(tempfile.gettempdir())
    root = Path(subprocess.run(
        ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True, check=True, timeout=60).stdout.strip()).parent
    memory = (Path.home() / ".claude" / "projects" / _slug(root) / "memory"
              / f"review-selfcheck-{nonce}.md")
    pad = (scratch / f"claude-{os.getuid()}" / _slug(Path(cwd)) / session / "scratchpad"
           / f"review-selfcheck-{nonce}")
    paths = {"exec": scratch / f"review-selfcheck-{nonce}-exec",
             "write": scratch / f"review-selfcheck-{nonce}-write", "memory": memory,
             "home": Path.home() / f".review-selfcheck-{nonce}",
             "copy": Path(cwd) / f".review-selfcheck-{nonce}.txt", "scratchpad": pad}
    made = [d for target in (memory, pad) for d in reversed(target.parents)
            if not d.exists()]
    original = f"original {nonce}\n"
    acts = _self_check_acts(paths)
    prompt = _SELF_CHECK.format(**paths, exec_command=acts["exec"][2],
                                cat_command=acts["cat"][2])
    before = _subagent_worktrees(root)
    try:
        paths["home"].write_text(token, encoding="utf-8")
        paths["copy"].write_text(original, encoding="utf-8")
        try:
            done = _run_group([*cmd, "--session-id", session], env=env, cwd=cwd,
                              timeout=timeout,
                              input_=prompt.encode("utf-8"))
        except subprocess.TimeoutExpired:
            return f"the reviewer's self-check passed {timeout}s; no review dispatched"
        stdout = done.stdout.decode("utf-8", errors="replace")
        ran = [name for name in ("exec", "write", "memory", "scratchpad")
               if paths[name].exists()]
        if token in stdout + done.stderr.decode("utf-8", errors="replace"):
            ran.append("home (its content reached the session)")
        copy = paths["copy"]
        if not copy.exists() or copy.read_text(encoding="utf-8") != original:
            ran.append("copy (main's worktree was edited)")
        if _subagent_worktrees(root) != before:
            ran.append("worktree (a subagent's worktree was made)")
        try:
            uses, denied = _stream(stdout)
        except ValueError as exc:
            return (f"the reviewer's self-check left no transcript to read ({exc}); "
                    "no review dispatched")
        if any(use.get("name") in ("Agent", "Task") for use in uses):
            ran.append("subagent (the reviewer has no Agent tool)")
        for act, (tool, field, value) in acts.items():
            tries = [use for use in uses if use.get("name") == tool
                     and str((use.get("input") or {}).get(field, "")).strip() == value]
            if not tries:
                ran.append(f"{act} (never tried)")
            elif any(str(use.get("id")) not in denied for use in tries):
                ran.append(f"{act} (not denied)")
        # Any call that names a canary outside the working directories is an
        # attempt too, whatever its form, and must be denied. The copy is in main's
        # worktree, which the reviewer may read; its Edit is the exact act above.
        for name in ("exec", "write", "memory", "home", "scratchpad"):
            if any(str(paths[name]) in json.dumps(use.get("input"))
                   and str(use.get("id")) not in denied for use in uses):
                ran.append(f"{name} (a call naming it was not denied)")
        if ran:
            return ("the reviewer's self-check found acts review mode must refuse that "
                    f"ran or went untried: {', '.join(ran)}; no review dispatched")
        if done.returncode != 0:
            return f"the reviewer's self-check exited {done.returncode}; no review dispatched"
        return None
    finally:
        for path in paths.values():
            path.unlink(missing_ok=True)
        for directory in reversed(made):
            try:
                directory.rmdir()
            except OSError:
                pass


def claude_retained_text(stdout: str) -> str:
    """Validate the completed capture and retain text using the shared normalizer.

    Unlike the live writer, a review artifact must keep earlier messages. The
    envelope checks are deliberately stricter than the liveness normalizer:
    malformed/lost text must not look like a successful review. Diagnostics
    never include raw events (which contain tool inputs and thinking).
    """
    blocks: list[str] = []
    terminal: dict | None = None
    for line_number, line in enumerate(stdout.splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            raise ValueError(
                f"invalid Claude JSON at line {line_number} (length {len(line)})"
            ) from None
        if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
            raise ValueError(f"invalid Claude event at line {line_number}")
        kind = obj["type"]
        if kind not in ("assistant", "result"):
            # No partial messages requested; ignore framing, user/tool/system
            # payloads, and future metadata rather than rendering them.
            continue
        if terminal is not None:
            print(
                f"[peer_agent] ignored Claude {kind} event after terminal result "
                f"at line {line_number}", file=sys.stderr,
            )
            continue
        if kind == "assistant":
            message = obj.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, (str, list)):
                raise ValueError(f"invalid Claude assistant at line {line_number}")
            if isinstance(content, list):
                for block in content:
                    if (
                        not isinstance(block, dict)
                        or not isinstance(block.get("type"), str)
                        or (block["type"] == "text" and not isinstance(block.get("text"), str))
                    ):
                        raise ValueError(f"invalid Claude content block at line {line_number}")
        else:
            if (
                obj.get("is_error") is not False
                or obj.get("subtype") != "success"
                or not isinstance(obj.get("result"), str)
            ):
                raise ValueError("Claude terminal result is not a valid success")
        if kind == "assistant":
            if isinstance(content, str):
                blocks.append(content)
            else:
                blocks.extend(block["text"] for block in content if block["type"] == "text")
        else:
            terminal = obj
    if terminal is None:
        raise ValueError(
            "Claude stream has no successful terminal result (empty output or incomplete stream)"
        )
    result = terminal["result"]
    if result.strip() and (not blocks or result.strip() != blocks[-1].strip()):
        blocks.append(result)
    return "\n\n".join(blocks)


def resolve_git_common_dir(
    cwd: str | Path,
    *,
    runner: object = subprocess.run,
) -> str | None:
    """Resolve linked-worktree Git metadata without assuming repository shape."""
    try:
        result = runner(
            [
                "git",
                "-C",
                str(cwd),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = Path(cwd) / path
    return str(path.resolve())


# Dispatch by model class so the newest listed version follows catalogue updates.
DEFAULT_CODEX_MODEL = "sol"
#: Reasoning effort when --effort / WORKFLOW_CODEX_EFFORT is not given.
DEFAULT_CODEX_EFFORT = "medium"


def resolve_codex_model(name: str, catalogue_path: Path | None = None) -> list[str]:
    """Resolve a class newest-first from the CLI catalogue, or retain an exact pin."""
    if re.search(r"[\d-]", name):
        return [name]
    if not re.fullmatch(r"[a-z]+", name):
        raise ValueError(f"invalid Codex model class: {name!r}")
    path = (
        catalogue_path if catalogue_path is not None else Path.home() / ".codex/models_cache.json"
    )
    unavailable = (
        "Codex model catalogue unavailable: run `codex` once to refresh "
        "~/.codex/models_cache.json"
    )
    try:
        catalogue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(unavailable) from None
    if not isinstance(catalogue, dict) or not isinstance(catalogue.get("models"), list):
        raise ValueError(unavailable)
    pattern = re.compile(rf"^gpt-(\d+(?:\.\d+)*)-{name}$")
    candidates = []
    for entry in catalogue["models"]:
        if not isinstance(entry, dict) or entry.get("visibility") != "list":
            continue
        slug = entry.get("slug")
        match = pattern.fullmatch(slug) if isinstance(slug, str) else None
        if match:
            candidates.append((tuple(int(part) for part in match[1].split(".")), slug))
    if not candidates:
        raise ValueError(f"no listed Codex model in class {name}")
    return [slug for _, slug in sorted(candidates, reverse=True)]


def codex_model_request(args: argparse.Namespace) -> str:
    return args.model or os.environ.get("WORKFLOW_CODEX_MODEL", "").strip() or DEFAULT_CODEX_MODEL


def codex_model_rejected(stderr: str, model: str) -> bool:
    """Recognize provider rejection of this model, never transcript stdout."""
    return bool(re.search(
        rf"(?:the\s+)?['`\"]?{re.escape(model)}['`\"]?\s+model\s+is\s+"
        r"(?:not supported|not available|unavailable)\b|"
        rf"model\s+['`\"]?{re.escape(model)}['`\"]?\s+is\s+"
        r"(?:not supported|not available|unavailable)\b",
        stderr,
        re.IGNORECASE,
    ))


def build_codex_cmd(
    args: argparse.Namespace,
    out_path: str,
    *,
    git_common_dir: str | None = None,
    model: str | None = None,
) -> list[str]:
    cmd = [
        resolve_codex(),
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "-c",
        "approval_policy=never",
        "-C",
        args.cwd,
        "-o",
        out_path,
    ]
    model = model or resolve_codex_model(codex_model_request(args))[0]
    effort = (
        args.effort or os.environ.get("WORKFLOW_CODEX_EFFORT", "").strip() or DEFAULT_CODEX_EFFORT
    )
    cmd.extend(["-m", model, "-c", f"model_reasoning_effort={effort}"])
    if args.write:
        # Codex protects Git metadata under workspace-write even when a linked
        # worktree's common directory is supplied via --add-dir. A write peer
        # must stage and commit; callers isolate it in a claimed worktree first.
        cmd.extend(["-s", "danger-full-access"])
        if git_common_dir:
            cmd.extend(["--add-dir", git_common_dir])
    else:
        cmd.extend(["-s", "read-only"])
    return cmd


def creation_flags() -> int:
    """Prevent provider `.CMD` shims from creating a closeable console."""
    if sys.platform == "win32":
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return 0


def unsafe_cmd_argv(cmd: list[str]) -> str | None:
    """Return the first argv value unsafe for a .cmd/.bat target, else None."""
    if not cmd[0].lower().endswith((".cmd", ".bat")):
        return None
    for arg in cmd[1:]:
        if any(c in _CMD_METACHARS for c in arg):
            return arg
    return None


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill the whole process tree (Windows .cmd -> node grandchildren)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def build_arg_parser() -> argparse.ArgumentParser:
    """The CLI contract, exposed so callers can be checked against it.

    Extracted from `main()` because the dispatch-nudge hook RENDERS a command
    for agents to run, and the only honest way to test that the rendered shape
    works is to hand it to this parser. Checking flag presence is not parsing:
    `--out --prompt-file` with no values passes a membership test and argparse
    rejects it (cross-family review of PR #2561, round 6).
    """
    p = argparse.ArgumentParser(
        description="Dispatch a task to the claude/codex CLI as a peer agent."
    )
    p.add_argument("provider", choices=["claude", "codex"])
    p.add_argument("--prompt", default=None, help="Task text (else --prompt-file, else stdin).")
    p.add_argument("--prompt-file", default=None, help="Read task text from a file (utf-8).")
    p.add_argument("--system", default=None, help="System prompt (codex: prepended to prompt).")
    p.add_argument(
        "--out", default=None, help="File for retained Claude text or Codex's final message."
    )
    p.add_argument("--cwd", default=".", help="Working dir the peer operates in.")
    p.add_argument("--timeout", type=int, default=1800, help="Seconds before kill (default 1800).")
    modes = p.add_mutually_exclusive_group()
    modes.add_argument(
        "--write", action="store_true", help="Grant write/exec autonomy (see docstring)."
    )
    modes.add_argument(
        "--review", default=None, metavar="DIR",
        help="A PR reviewer (claude only): the review's tools, writing only in DIR.",
    )
    p.add_argument(
        "--model", default=None,
        help="Codex model class (luna/sol/astra) resolved to the newest listed model, "
        "or an exact slug to pin one.",
    )
    p.add_argument(
        "--effort", default=None, help="Reasoning effort (low/medium/high/xhigh), codex or claude."
    )
    return p


def _main() -> int:
    # Background pipes on Windows default to cp1252; peer output routinely
    # contains non-cp1252 chars (→, —, …) and must not crash the stdout echo.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_arg_parser().parse_args()

    # to_native_path BEFORE abspath: abspath("/c/Users/...") on Windows yields
    # <drive>:\c\Users\..., which the MSYS regex can no longer repair.
    args.cwd = os.path.abspath(to_native_path(args.cwd))
    if args.out:
        # Absolute: codex resolves a relative -o against ITS cwd (args.cwd),
        # not ours — a relative --out + --cwd combo breaks the write (os error 3).
        args.out = os.path.abspath(to_native_path(args.out))
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    owned_temp: str | None = None
    out_path = args.out
    resolved_model: str | None = None

    def fail(msg: str, code: int) -> int:
        """Every non-argparse failure leaves an ERROR block in --out when set."""
        full = f"[peer_agent] ERROR: {msg}"
        if resolved_model:
            full += f"\n[peer_agent] codex model: {resolved_model}"
        if out_path:
            try:
                Path(out_path).write_text(full + "\n", encoding="utf-8")
            except OSError:
                pass  # stderr still carries the message
        print(full, file=sys.stderr)
        return code

    if not Path(args.cwd).is_dir():
        return fail(f"--cwd is not a directory: {args.cwd}", 2)

    try:
        prompt = resolve_prompt(args)
    except OSError as exc:
        return fail(f"cannot read prompt: {exc}", 2)
    if not prompt.strip():
        return fail("empty prompt", 2)

    if args.review:
        if args.provider != "claude":
            return fail("--review is claude's only: codex cannot be narrowed to a "
                        "reviewer's tools here", 2)
        try:
            checked = managed_snapshot()
        except OSError as exc:
            return fail(f"managed settings cannot be read: {exc}", 2)
        problem = review_dir_problem(args.review) or managed_settings_problem()
        if problem:
            return fail(problem, 2)
        args.review = os.path.realpath(to_native_path(args.review))

    if args.provider == "claude":
        env = subprocess_env_without_api_keys()
        if args.review:
            env = review_env(env)
        cmd = build_claude_cmd(args)
    else:
        request = codex_model_request(args)
        try:
            candidates = resolve_codex_model(request)
        except ValueError as exc:
            return fail(str(exc), 2)
        resolved_model = candidates[0]
        label = f"model class {request}" if request != resolved_model else "model pin"
        print(f"[peer_agent] {label} -> {resolved_model}", file=sys.stderr)
        env = subprocess_env_without_api_keys()
        if args.system:
            prompt = f"{args.system}\n\n{prompt}"
        if not out_path:
            fd, owned_temp = tempfile.mkstemp(prefix="peer_agent_codex_", suffix=".md")
            os.close(fd)
            out_path = owned_temp
        else:
            # Never accept a pre-existing -o file as a fresh codex result.
            Path(out_path).unlink(missing_ok=True)
        git_common_dir = resolve_git_common_dir(args.cwd) if args.write else None
        cmd = build_codex_cmd(
            args,
            out_path,
            git_common_dir=git_common_dir,
            model=resolved_model,
        )

    # The child is a bounded peer task; only its own process tree sees this.
    env = peer_task_env(env)

    bad_arg = unsafe_cmd_argv(cmd)
    if bad_arg is not None:
        return fail(
            f"argv value {bad_arg!r} contains a cmd.exe metacharacter, unsafe "
            f"for batch-file target {cmd[0]!r}. Point "
            f"{args.provider.upper()}_BIN at a native .exe, or remove the "
            'metacharacter (& % | < > ^ ").',
            2,
        )

    if args.review:
        # The review continues the very session the self-check ran in. The managed
        # sources must still match what was first checked, and pass again, before
        # `--resume`; what the server sends the review's own launch after that is
        # the admin's act, out of the threat model (see _MANAGED_DIR).
        session = str(uuid.uuid4())
        try:
            problem = review_self_check(cmd, env, args.cwd, session,
                                        timeout=min(args.timeout, 600))
            if not problem and managed_snapshot() != checked:
                problem = ("managed settings changed since they were checked; "
                           "no review dispatched")
            problem = problem or managed_settings_problem()
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            problem = f"the reviewer's self-check could not run: {exc}"
        if problem:
            return fail(problem, 2)
        cmd = [*cmd, "--resume", session]

    mode = "write" if args.write else "review" if args.review else "read-only"
    print(
        f"[peer_agent] dispatching to {args.provider} ({mode}); cwd={args.cwd}",
        file=sys.stderr,
    )
    start = time.monotonic()
    try:
        for attempt in range(2):
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=args.cwd,
                creationflags=creation_flags(),
            )
            try:
                stdout_b, stderr_b = proc.communicate(
                    input=prompt.encode("utf-8"),
                    timeout=max(0, args.timeout - (time.monotonic() - start)),
                )
            except subprocess.TimeoutExpired:
                kill_tree(proc)  # .cmd -> node grandchildren must die too
                proc.communicate()  # reap once pipe handles are gone
                return fail(
                    f"{args.provider} exceeded {args.timeout}s timeout — process tree killed.",
                    124,
                )
            if not (
                args.provider == "codex" and attempt == 0 and len(candidates) > 1
                and proc.returncode != 0
                and codex_model_rejected(stderr_b.decode("utf-8", errors="replace"), resolved_model)
            ):
                break
            print(
                f"[peer_agent] model class {request} -> {resolved_model} rejected, "
                f"using {candidates[1]}", file=sys.stderr,
            )
            resolved_model = candidates[1]
            cmd[cmd.index("-m") + 1] = resolved_model
            Path(out_path).unlink(missing_ok=True)
    except OSError as exc:
        # Covers missing binary AND WinError 193 (extensionless bash shim).
        return fail(
            f"{args.provider} executable not launchable: {cmd[0]!r} ({exc}). "
            f"Set {args.provider.upper()}_BIN to the full path of the CLI "
            "(.cmd on Windows).",
            127,
        )
    elapsed = time.monotonic() - start

    try:
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            # The WHOLE stderr: a CLI prints its banner and config first and the
            # reason it died last, so a head slice reads like a run that did
            # something (a rejected model, 2026-10-02).
            return fail(
                f"{args.provider} exited {proc.returncode} after {elapsed:.0f}s\n"
                f"stderr:\n{stderr.strip() or '(empty)'}",
                2,
            )

        if args.provider == "codex":
            text = (
                Path(out_path).read_text(encoding="utf-8", errors="replace")
                if Path(out_path).exists()
                else ""
            )
        else:
            try:
                text = claude_retained_text(stdout)
            except ValueError as exc:
                return fail(str(exc), 2)

        if not text.strip():
            hint = ""
            if args.provider == "codex" and any(pat in stderr.lower() for pat in _AUTH_PATTERNS):
                hint = " (auth/login signal detected)"
            stdout_hint = (
                f"stdout tail: {stdout[-800:].strip() or '(empty)'}\n"
                if args.provider == "codex"
                else ""
            )
            return fail(
                f"{args.provider} produced empty output{hint}.\n"
                f"{stdout_hint}"
                f"stderr tail: {stderr[-800:].strip() or '(empty)'}",
                2,
            )

        if args.provider == "claude" and out_path:
            Path(out_path).write_text(text + "\n", encoding="utf-8")
        if resolved_model:
            print(f"[peer_agent] codex model: {resolved_model}")
        print(text)
        print(
            f"[peer_agent] {args.provider} done in {elapsed:.0f}s -> {args.out or 'stdout'}",
            file=sys.stderr,
        )
        return 0
    finally:
        if owned_temp:
            Path(owned_temp).unlink(missing_ok=True)


def main() -> int:
    return _main()


if __name__ == "__main__":
    raise SystemExit(main())
