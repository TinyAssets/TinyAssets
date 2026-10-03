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
  echo "summarize PLAN.md" | python scripts/peer_agent.py claude

Modes:
  default   read-only-ish. claude: plain `-p` (edit/bash tools denied).
            codex: `-s read-only -c approval_policy=never`.
  --write   full agent. claude: --dangerously-skip-permissions.
            codex: danger-full-access with approval_policy=never because Codex
            protects linked-worktree Git metadata even when its common
            directory is added. Point --cwd at a worktree, not the live
            checkout; worktree/claim/review gates are the safety boundary.

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
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))


from tinyassets.providers.base import subprocess_env_for_provider  # noqa: E402
from tinyassets.providers.claude_provider import _normalize_stream_obj  # noqa: E402

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
    # Default model "fable": the alias tracks the latest Claude model on a Max
    # subscription (currently claude-fable-5). Bare `claude -p` does not
    # default to the frontier model.
    #
    # WORKFLOW_CLAUDE_MODEL overrides that default, mirroring WORKFLOW_CODEX_MODEL on
    # the codex path. This exists because when the pinned model is rate-limited the
    # CLI exits 1 after ~25s with COMPLETELY EMPTY stderr: every lane dies silently
    # while the dispatcher reports success, and a dead lane is indistinguishable from
    # a working one from outside. Four lanes were lost that way on 2026-07-21 before
    # anyone noticed. An explicit --model still wins.
    model = args.model or os.environ.get("WORKFLOW_CLAUDE_MODEL", "").strip() or "fable"
    cmd = [resolve_claude(), "-p", "--model", model, "--output-format", "stream-json", "--verbose"]
    if args.write:
        cmd.append("--dangerously-skip-permissions")
    if args.system:
        cmd.extend(["--system-prompt", args.system])
    return cmd


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
            raise ValueError("Claude text/result event after terminal result")
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
        for event, payload in _normalize_stream_obj(obj):
            if event == "text_delta":
                blocks.append(payload["text"])
            elif event == "result":
                terminal = payload["obj"]
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


#: The model every codex peer runs on unless --model / WORKFLOW_CODEX_MODEL says
#: otherwise. Never the CLI's own default: on a ChatGPT account `codex exec`
#: defaults to a model the account rejects in seconds (gpt-6.1-sol, 2026-10-02),
#: and ~/.codex/config.toml is whatever it last said, named nowhere in a result.
DEFAULT_CODEX_MODEL = "gpt-6-astra"
#: Reasoning effort when --effort / WORKFLOW_CODEX_EFFORT is not given.
DEFAULT_CODEX_EFFORT = "medium"


def build_codex_cmd(
    args: argparse.Namespace,
    out_path: str,
    *,
    git_common_dir: str | None = None,
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
    model = (
        args.model or os.environ.get("WORKFLOW_CODEX_MODEL", "").strip() or DEFAULT_CODEX_MODEL
    )
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
    p.add_argument(
        "--write", action="store_true", help="Grant write/exec autonomy (see docstring)."
    )
    p.add_argument("--model", default=None, help="Model override passed through to the CLI.")
    p.add_argument(
        "--effort", default=None, help="Codex reasoning effort (minimal/low/medium/high/xhigh)."
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

    def fail(msg: str, code: int) -> int:
        """Every non-argparse failure leaves an ERROR block in --out when set."""
        full = f"[peer_agent] ERROR: {msg}"
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

    if args.provider == "claude":
        env = subprocess_env_for_provider("claude-code")
        cmd = build_claude_cmd(args)
    else:
        env = subprocess_env_for_provider("codex")
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

    mode = "write" if args.write else "read-only"
    print(
        f"[peer_agent] dispatching to {args.provider} ({mode}); cwd={args.cwd}",
        file=sys.stderr,
    )
    start = time.monotonic()
    try:
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
                input=prompt.encode("utf-8"), timeout=args.timeout
            )
        except subprocess.TimeoutExpired:
            kill_tree(proc)  # .cmd -> node grandchildren must die too
            proc.communicate()  # reap once pipe handles are gone
            return fail(
                f"{args.provider} exceeded {args.timeout}s timeout — process tree killed.",
                124,
            )
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
