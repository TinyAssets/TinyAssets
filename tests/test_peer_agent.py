from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "peer_agent.py"
SPEC = importlib.util.spec_from_file_location("peer_agent_contract", SCRIPT)
assert SPEC is not None
peer_agent = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = peer_agent
SPEC.loader.exec_module(peer_agent)


def _args(*, write: bool) -> argparse.Namespace:
    return argparse.Namespace(
        cwd="C:\\worktree",
        write=write,
        model=None,
        effort=None,
    )


def test_windows_provider_processes_are_created_without_a_console(
    monkeypatch,
) -> None:
    monkeypatch.setattr(peer_agent.sys, "platform", "win32")

    assert peer_agent.creation_flags() == 0x08000000


def test_non_windows_provider_processes_keep_default_creation_flags(
    monkeypatch,
) -> None:
    monkeypatch.setattr(peer_agent.sys, "platform", "linux")

    assert peer_agent.creation_flags() == 0


def test_git_common_dir_is_resolved_as_an_absolute_path(tmp_path: Path) -> None:
    common = tmp_path / "source" / ".git"
    common.mkdir(parents=True)
    calls: list[list[str]] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=str(common) + "\n",
            stderr="",
        )

    assert peer_agent.resolve_git_common_dir(tmp_path, runner=runner) == str(common.resolve())
    assert calls == [
        [
            "git",
            "-C",
            str(tmp_path),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ]
    ]


def test_git_common_dir_discovery_fails_closed_for_non_git_directory(
    tmp_path: Path,
) -> None:
    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 128, stdout="", stderr="not a repo")

    assert peer_agent.resolve_git_common_dir(tmp_path, runner=runner) is None


def test_write_codex_command_grants_only_the_resolved_git_common_dir(
    monkeypatch,
) -> None:
    monkeypatch.setattr(peer_agent, "resolve_codex", lambda: "codex.exe")

    command = peer_agent.build_codex_cmd(
        _args(write=True),
        "C:\\result.md",
        git_common_dir="C:\\source\\.git",
    )

    assert command.count("--add-dir") == 1
    assert command[command.index("--add-dir") + 1] == "C:\\source\\.git"
    assert "approval_policy=never" in command
    assert command[command.index("-s") + 1] == "danger-full-access"
    assert "--full-auto" not in command


def test_read_only_codex_command_never_grants_git_metadata(monkeypatch) -> None:
    monkeypatch.setattr(peer_agent, "resolve_codex", lambda: "codex.exe")

    command = peer_agent.build_codex_cmd(
        _args(write=False),
        "C:\\result.md",
        git_common_dir="C:\\source\\.git",
    )

    assert "--add-dir" not in command
    assert command[-2:] == ["-s", "read-only"]
    # Checking only the TRAILING pair leaves an earlier grant intact: codex
    # takes the last `-s`, but a bypass flag elsewhere in the argv still widens
    # authority, and a reviewer pointed at the live checkout must stay read-only.
    assert command.count("-s") == 1, command
    # SUBSTRING, not list membership: `--sandbox=danger-full-access` is a single
    # argv token, so `in command` never sees it. With the trailing
    # `-s read-only` still present, codex rejects the duplicate sandbox options
    # and the "guarded" dispatch becomes non-launchable while the test stays
    # green (round 6, mutation-proven).
    joined = " ".join(command)
    for banned in (
        "danger-full-access",
        "dangerously-bypass",
        "full-auto",
        "yolo",
        "workspace-write",
    ):
        assert banned not in joined, f"{banned!r} survives in a read-only command: {command}"


# --- Fail-closed dispatch -------------------------------------------------
#
# `openspec/specs/development-coordination-runtime/spec.md` requires that "a
# provider failure, timeout, or non-launchable CLI SHALL produce a non-zero
# exit and an explicit error marker rather than a silent empty result."
#
# That contract used to be covered by 11 tests against `scripts/codex_review.py`
# in `tests/test_codex_dispatch.py`. The 2026-08-26 cut consolidated the two
# dispatchers onto `peer_agent.py` and deleted those tests with the script,
# leaving the surviving file testing only console flags and worktree
# permissions -- so every fail-closed path could regress to silent success with
# CI green. Found by cross-family review of PR #2561. Ported here against the
# surviving implementation.


class _FakeProc:
    """Stand-in for Popen: scripted returncode / output / timeout."""

    def __init__(self, *, returncode=0, stdout=b"", stderr=b"", timeout=False):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._timeout = timeout
        self.communicate_input = None
        self.killed = False

    def communicate(self, input=None, timeout=None):  # noqa: A002 - Popen's name
        if self._timeout:
            self._timeout = False  # the post-kill reap call succeeds
            raise subprocess.TimeoutExpired(cmd="peer", timeout=timeout or 0)
        self.communicate_input = input
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.returncode

    @property
    def pid(self):
        return 4242


def _run_main(
    monkeypatch, tmp_path, proc, *, provider="claude", extra=(), out_file_text=None, seen=None
):
    """Invoke peer_agent.main() with a scripted subprocess.

    Returns (rc, out_text, argv). `out_file_text` simulates the codex CLI's
    `-o` behaviour: codex writes its answer to the out FILE and peer_agent reads
    it back, where claude's answer arrives on stdout. The two branches are
    genuinely different code and both need covering.
    """
    out = tmp_path / "verdict.txt"
    seen = {} if seen is None else seen

    def _popen(cmd, *a, **k):
        argv = list(cmd)
        seen["argv"] = argv
        seen["env"] = k.get("env")
        if out_file_text is not None:
            # Write where the IMPLEMENTATION told codex to write, not where the
            # test wishes it would. Writing to `out` directly would keep passing
            # even if `build_codex_cmd` pointed `-o` at the wrong path -- the
            # real CLI would then write elsewhere and peer_agent would read an
            # empty file. (Cross-family review of PR #2561, round 5.)
            if provider == "codex":
                # No silent fallback: codex's answer arrives ONLY via `-o`, so a
                # command that lost the flag must produce no readable out-file --
                # which is what the real CLI would do. Defaulting to `out` here
                # kept the test green when `-o` was dropped entirely (round 6).
                assert "-o" in argv, f"codex command has no -o: {argv}"
                target = Path(argv[argv.index("-o") + 1])
            else:
                target = out
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(out_file_text, encoding="utf-8")
        return proc

    monkeypatch.setattr(peer_agent.subprocess, "Popen", _popen)
    monkeypatch.setattr(peer_agent, "kill_tree", lambda p: p.kill())
    monkeypatch.setattr(
        peer_agent.sys,
        "argv",
        [
            "peer_agent.py",
            provider,
            "--prompt",
            "review this",
            "--cwd",
            str(tmp_path),
            "--out",
            str(out),
            *extra,
        ],
    )
    rc = peer_agent.main()
    return (
        rc,
        (out.read_text(encoding="utf-8") if out.exists() else ""),
        seen.get("argv", []),
    )


def test_timeout_kills_the_tree_and_writes_an_error_marker(monkeypatch, tmp_path):
    proc = _FakeProc(timeout=True)
    rc, out, _ = _run_main(monkeypatch, tmp_path, proc, extra=("--timeout", "5"))
    assert rc == 124
    assert "[peer_agent] ERROR" in out
    assert "timeout" in out.lower()
    assert proc.killed, "a timed-out .cmd leaves node grandchildren alive"


def test_non_launchable_binary_writes_an_error_marker(monkeypatch, tmp_path):
    def _boom(*a, **k):
        raise OSError(2, "No such file or directory")

    out = tmp_path / "verdict.txt"
    monkeypatch.setattr(peer_agent.subprocess, "Popen", _boom)
    monkeypatch.setattr(
        peer_agent.sys,
        "argv",
        [
            "peer_agent.py",
            "codex",
            "--prompt",
            "x",
            "--cwd",
            str(tmp_path),
            "--out",
            str(out),
        ],
    )
    assert peer_agent.main() == 127
    text = out.read_text(encoding="utf-8")
    assert "[peer_agent] ERROR" in text
    assert "not launchable" in text


def test_zero_exit_with_empty_output_is_a_failure_not_a_silent_pass(monkeypatch, tmp_path):
    # The whole point: exit 0 + nothing produced must NOT read as a clean review.
    rc, out, _ = _run_main(monkeypatch, tmp_path, _FakeProc(returncode=0, stdout=b"  \n"))
    assert rc == 2
    assert "[peer_agent] ERROR" in out
    assert "empty output" in out


def test_nonzero_exit_writes_an_error_marker_carrying_stderr(monkeypatch, tmp_path):
    rc, out, _ = _run_main(
        monkeypatch,
        tmp_path,
        _FakeProc(returncode=3, stdout=b"partial", stderr=b"upstream exploded"),
    )
    assert rc == 2
    assert "[peer_agent] ERROR" in out
    assert "upstream exploded" in out


def test_nonzero_exit_carries_the_end_of_a_long_stderr(monkeypatch, tmp_path):
    """The reason a CLI died comes LAST, after a long banner; a head slice of
    stderr made a rejected-model run look like it had produced output."""
    banner = b"".join(b"config line %d\n" % i for i in range(400))
    rc, out, _ = _run_main(
        monkeypatch, tmp_path,
        _FakeProc(returncode=1, stderr=banner + b"ERROR: model gpt-6.1-sol is not supported"),
        provider="codex", out_file_text=None,
    )
    assert rc == 2
    assert out.startswith("[peer_agent] ERROR: codex exited 1")
    assert "model gpt-6.1-sol is not supported" in out
    assert "config line 0" in out


def test_codex_pins_model_and_effort_by_default(monkeypatch) -> None:
    monkeypatch.setattr(peer_agent, "resolve_codex", lambda: "codex.exe")
    monkeypatch.delenv("WORKFLOW_CODEX_MODEL", raising=False)
    monkeypatch.delenv("WORKFLOW_CODEX_EFFORT", raising=False)

    command = peer_agent.build_codex_cmd(_args(write=False), "C:\\result.md")

    assert command[command.index("-m") + 1] == "gpt-6-astra"
    assert "model_reasoning_effort=medium" in command


def test_codex_model_and_effort_overrides_win(monkeypatch) -> None:
    monkeypatch.setattr(peer_agent, "resolve_codex", lambda: "codex.exe")
    monkeypatch.setenv("WORKFLOW_CODEX_MODEL", "env-model")
    monkeypatch.setenv("WORKFLOW_CODEX_EFFORT", "high")

    command = peer_agent.build_codex_cmd(_args(write=False), "C:\\result.md")
    assert command[command.index("-m") + 1] == "env-model"
    assert "model_reasoning_effort=high" in command

    args = _args(write=False)
    args.model, args.effort = "flag-model", "low"
    command = peer_agent.build_codex_cmd(args, "C:\\result.md")
    assert command[command.index("-m") + 1] == "flag-model"
    assert "model_reasoning_effort=low" in command
    assert command.count("-m") == 1


def test_prompt_reaches_the_provider_on_stdin_not_argv(monkeypatch, tmp_path):
    # Windows cmd.exe truncates argv at a newline, which silently shortened
    # multi-line review prompts -- stdin is the contract.
    proc = _FakeProc(
        returncode=0, stdout=_stream(_assistant("VERDICT: APPROVE"), _result("VERDICT: APPROVE"))
    )
    rc, _, argv = _run_main(monkeypatch, tmp_path, proc)
    assert rc == 0
    assert proc.communicate_input == b"review this"
    # Asserting stdin alone would still pass if the prompt were ALSO on argv,
    # which is the exact regression: cmd.exe would truncate it there.
    assert not any("review this" in str(part) for part in argv), argv


def test_success_leaves_the_provider_output_intact(monkeypatch, tmp_path):
    rc, out, _ = _run_main(
        monkeypatch,
        tmp_path,
        _FakeProc(
            returncode=0, stdout=_stream(_assistant("VERDICT: REJECT"), _result("VERDICT: REJECT"))
        ),
    )
    assert rc == 0
    assert "VERDICT: REJECT" in out
    assert "[peer_agent] ERROR" not in out


def _assistant(text):
    # Same full-message shape as test_provider_stream_and_classify.py.
    return {
        "type": "assistant",
        "message": {"id": "shared-id", "content": [{"type": "text", "text": text}]},
    }


def _result(text=""):
    return {"type": "result", "subtype": "success", "is_error": False, "result": text}


def _stream(*events):
    return ("\n".join(json.dumps(event) for event in events) + "\n").encode()


def test_claude_capture_flags_preserve_model_permissions_and_hooks(monkeypatch):
    monkeypatch.setattr(peer_agent, "resolve_claude", lambda: "claude.exe")
    args = argparse.Namespace(model="claude-fable-5-1", write=False, system=None)
    command = peer_agent.build_claude_cmd(args)
    assert command == [
        "claude.exe",
        "-p",
        "--model",
        "claude-fable-5-1",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    args.write = True
    args.system = "Review this scope only"
    assert peer_agent.build_claude_cmd(args) == command + [
        "--dangerously-skip-permissions",
        "--system-prompt",
        args.system,
    ]


def test_claude_retains_review_and_stop_continuation_without_payloads(
    monkeypatch, tmp_path, capsys
):
    stdout = _stream(
        {"type": "system", "subtype": "init", "secret": "SYSTEM_SENTINEL"},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "THINKING_SENTINEL"},
                    {"type": "tool_use", "name": "Read", "input": {"secret": "INPUT_SENTINEL"}},
                    {"type": "text", "text": "Evidence A: fix the missing check.\nVERDICT: ADAPT"},
                    {"type": "text", "text": "Evidence B: keep this separate block."},
                ]
            },
        },
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "content": "RESULT_SENTINEL"}]},
        },
        {"type": "future_metadata", "value": "FUTURE_SENTINEL"},
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "PARTIAL_SENTINEL"},
            },
        },
        _assistant("Stop-hook recap: the review above stands."),
        _result("Stop-hook recap: the review above stands."),
    )
    rc, out, _ = _run_main(monkeypatch, tmp_path, _FakeProc(stdout=stdout))
    assert rc == 0
    assert out == (
        "Evidence A: fix the missing check.\nVERDICT: ADAPT\n\n"
        "Evidence B: keep this separate block.\n\n"
        "Stop-hook recap: the review above stands.\n"
    )
    captured = capsys.readouterr()
    assert "SENTINEL" not in out + captured.out + captured.err


@pytest.mark.parametrize(
    "events, expected",
    [
        ([_assistant("Same"), _assistant("Same"), _result(" Same \n")], "Same\n\nSame\n"),
        ([_assistant("Review"), _result("Different final")], "Review\n\nDifferent final\n"),
        ([_result("Only final")], "Only final\n"),
        (
            [{"type": "assistant", "message": {"content": "String content"}}, _result()],
            "String content\n",
        ),
    ],
)
def test_claude_echo_dedupe_never_discards_distinct_blocks(monkeypatch, tmp_path, events, expected):
    rc, out, _ = _run_main(monkeypatch, tmp_path, _FakeProc(stdout=_stream(*events)))
    assert rc == 0
    assert out == expected


@pytest.mark.parametrize(
    "stdout",
    [
        b"plain text PRIVATE_SENTINEL",
        _stream(_assistant("VERDICT: APPROVE")),
        _stream(
            _assistant("VERDICT: APPROVE"),
            {**_result(), "is_error": True, "result": "PRIVATE_SENTINEL"},
        ),
        _stream({**_result(), "is_error": "false"}),
        _stream({**_result(), "subtype": "error_max_turns"}),
        _stream({"type": "result", "result": "PRIVATE_SENTINEL"}),
        _stream({**_result(), "result": {"secret": "PRIVATE_SENTINEL"}}),
        _stream({"type": "assistant", "message": None}, _result("VERDICT: APPROVE")),
        _stream({"type": "assistant", "message": {"content": ["PRIVATE_SENTINEL"]}}, _result()),
        _stream(_assistant(123), _result()),
        _stream(["PRIVATE_SENTINEL"]),
        _stream(_result("VERDICT: APPROVE")) + b"PRIVATE_SENTINEL",
    ],
)
def test_invalid_claude_stream_fails_without_exposing_payloads(
    monkeypatch, tmp_path, capsys, stdout
):
    (tmp_path / "verdict.txt").write_text("STALE APPROVAL", encoding="utf-8")
    rc, out, _ = _run_main(monkeypatch, tmp_path, _FakeProc(stdout=stdout))
    assert rc == 2
    assert out.startswith("[peer_agent] ERROR")
    captured = capsys.readouterr()
    assert "PRIVATE_SENTINEL" not in out + captured.out + captured.err
    assert "STALE APPROVAL" not in out
    assert "VERDICT: APPROVE" not in out


def test_empty_claude_result_does_not_dump_raw_stream(monkeypatch, tmp_path, capsys):
    rc, out, _ = _run_main(
        monkeypatch,
        tmp_path,
        _FakeProc(stdout=_stream({"type": "system", "data": "PRIVATE_SENTINEL"}, _result())),
    )
    assert rc == 2
    assert "empty output" in out
    captured = capsys.readouterr()
    assert "PRIVATE_SENTINEL" not in out + captured.out + captured.err


@pytest.mark.parametrize("timeout, returncode, expected", [(False, 1, 2), (True, 0, 124)])
def test_valid_claude_review_cannot_override_process_failure(
    monkeypatch, tmp_path, timeout, returncode, expected
):
    rc, out, _ = _run_main(
        monkeypatch,
        tmp_path,
        _FakeProc(
            stdout=_stream(_assistant("VERDICT: APPROVE"), _result("VERDICT: APPROVE")),
            timeout=timeout,
            returncode=returncode,
        ),
    )
    assert rc == expected
    assert "[peer_agent] ERROR" in out
    assert "VERDICT: APPROVE" not in out


# --- The codex branch is different code -------------------------------------
#
# claude's answer arrives on stdout; codex writes it to the `-o` FILE and
# peer_agent reads it back (`scripts/peer_agent.py:401`). Every test above runs
# the claude branch, so a regression that accepted codex stdout, or accepted a
# missing/stale out-file as success, would stay green. Found by cross-family
# review of PR #2561, round 4.


def test_codex_answer_is_read_from_the_out_file(monkeypatch, tmp_path):
    rc, out, _ = _run_main(
        monkeypatch,
        tmp_path,
        _FakeProc(returncode=0, stdout=b""),
        provider="codex",
        out_file_text="VERDICT: ADAPT\n",
    )
    assert rc == 0
    assert "VERDICT: ADAPT" in out


def test_codex_stdout_is_not_accepted_in_place_of_the_out_file(monkeypatch, tmp_path):
    # Exit 0 with chatty stdout but nothing written to `-o` is a FAILED review,
    # not a passing one. Accepting stdout here would let a silent codex run read
    # as an approval.
    rc, out, _ = _run_main(
        monkeypatch,
        tmp_path,
        _FakeProc(returncode=0, stdout=b"thinking...\nall done\n"),
        provider="codex",
    )
    assert rc == 2
    assert "[peer_agent] ERROR" in out
    assert "empty output" in out


def test_codex_empty_out_file_is_a_failure(monkeypatch, tmp_path):
    rc, out, _ = _run_main(
        monkeypatch,
        tmp_path,
        _FakeProc(returncode=0, stdout=b""),
        provider="codex",
        out_file_text="   \n",
    )
    assert rc == 2
    assert "[peer_agent] ERROR" in out


# --- bounded-peer marker -------------------------------------------------------
# The Stop hook blocked dispatched peers on ledger rows they do not own (their
# own parent's, even). The fix is one env marker on the CHILD. These pin that it
# reaches the child, only the child, and that the hook reads the same name.


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_peer_task_marker_reaches_only_the_child_environment(
    monkeypatch, tmp_path, provider
):
    monkeypatch.delenv(peer_agent.PEER_TASK_ENV, raising=False)
    monkeypatch.setattr(peer_agent, "resolve_codex", lambda: "codex.exe")
    monkeypatch.setattr(peer_agent, "resolve_claude", lambda: "claude.exe")
    before = dict(os.environ)
    seen = {}
    _run_main(
        monkeypatch,
        tmp_path,
        _FakeProc(returncode=0, stdout=b"ok" + b"\n"),
        provider=provider,
        out_file_text="VERDICT: APPROVE" + chr(10),
        seen=seen,
    )
    assert seen["env"] is not None, "Popen was not handed an explicit env"
    assert seen["env"][peer_agent.PEER_TASK_ENV] == "1"
    # The marker is coordination context in the child, not state in the parent.
    assert peer_agent.PEER_TASK_ENV not in os.environ
    assert dict(os.environ) == before
    # And it is never an argv value: nothing downstream can parse it as a flag.
    assert all(peer_agent.PEER_TASK_ENV not in a for a in seen["argv"])


def test_peer_task_env_returns_a_new_mapping_and_never_mutates_its_input():
    base = {"PATH": "x", "HOME": "y"}
    marked = peer_agent.peer_task_env(base)
    assert marked is not base
    assert marked == {"PATH": "x", "HOME": "y", peer_agent.PEER_TASK_ENV: "1"}
    assert base == {"PATH": "x", "HOME": "y"}





def test_claude_post_terminal_capture_keeps_review_in_out(monkeypatch, tmp_path, capsys):
    capture = Path(__file__).parent / "fixtures" / "claude_review_post_terminal.jsonl"
    rc, out, _ = _run_main(monkeypatch, tmp_path, _FakeProc(stdout=capture.read_bytes()))
    assert rc == 0
    assert out == "Review evidence.\n\nVERDICT: ADAPT\n"
    captured = capsys.readouterr()
    assert "ignored Claude assistant event after terminal result" in captured.err
    assert "ignored Claude result event after terminal result" in captured.err
    assert "EXTRA_SENTINEL" not in out + captured.out + captured.err
