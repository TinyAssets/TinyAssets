"""The converge removes compose's temp-named daemon containers before `up -d`.

Compose recreates a container by creating `<12-hex>_<name>`, stopping and
removing the old one, then renaming the new one. On 2026-10-01 a second compose
run (the systemd unit's, started by a watchdog) raced the deploy's. The deploy's
rollback `up -d` then failed with "Conflict. The container name
/1cc5a277f659_tinyassets-daemon is already in use", and the result was
`deploy_result=rollback_failed`
(docs/audits/2026-10-01-deploy-drain-repro/INCIDENT.md).

This runs the REAL shell function, lifted out of deploy_fail_safe.sh, against a
recording fake `docker`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "deploy" / "deploy_fail_safe.sh"
_BASH = shutil.which("bash")


def _function(name: str) -> str:
    body = SCRIPT.read_text(encoding="utf-8")
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", body, re.S | re.M)
    assert match, f"{name} is no longer a plain shell function in {SCRIPT.name}"
    return match.group(0)


def _run(
    tmp_path: Path, names: list[str], state: str = "exited",
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    listing = tmp_path / "names.txt"
    listing.write_text(
        "".join(f"{n} {state}\n" for n in names), encoding="utf-8", newline="\n",
    )
    calls = tmp_path / "calls.txt"
    script = tmp_path / "run.sh"
    script.write_text(
        "\n".join([
            "set -u",
            f"LISTING='{listing.as_posix()}'",
            f"CALLS='{calls.as_posix()}'",
            "DAEMON_CONTAINER=tinyassets-daemon",
            'log() { echo "LOG $*"; }',
            'err() { echo "ERR $*" >&2; }',
            'docker() {',
            '  echo "$*" >> "$CALLS"',
            '  case "$1" in',
            '    ps) cat "$LISTING" ;;',
            '    rm) return 0 ;;',
            '    *) return 97 ;;',
            '  esac',
            '}',
            _function("remove_compose_temp_daemons"),
            "remove_compose_temp_daemons",
            "",
        ]),
        encoding="utf-8",
        newline="\n",
    )
    result = subprocess.run(
        [_BASH, script.as_posix()], capture_output=True, text=True, timeout=60, check=False,
    )
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result, lines


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
def test_temp_named_daemon_containers_are_removed(tmp_path: Path):
    result, calls = _run(tmp_path, [
        "tinyassets-daemon",
        "1cc5a277f659_tinyassets-daemon",
        "a28968a166cd_tinyassets-daemon",
        "tinyassets-tunnel",
        "tinyassets-logs",
    ])
    assert result.returncode == 0, result.stderr
    removed = [c for c in calls if c.startswith("rm ")]
    assert removed == [
        "rm -f 1cc5a277f659_tinyassets-daemon",
        "rm -f a28968a166cd_tinyassets-daemon",
    ]


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
@pytest.mark.parametrize("name", [
    "tinyassets-daemon",                      # the live container
    "1cc5a277f659_tinyassets-daemon-extra",   # suffix: not the daemon's temp name
    "x1cc5a277f659_tinyassets-daemon",        # 13 chars of prefix
    "1CC5A277F659_tinyassets-daemon",         # docker ids are lowercase hex
    "1cc5a277f65_tinyassets-daemon",          # 11 hex
    "1cc5a277f659_tinyassets-tunnel",         # another service's temp name
])
def test_nothing_but_a_daemon_temp_name_is_touched(tmp_path: Path, name: str):
    result, calls = _run(tmp_path, [name])
    assert result.returncode == 0, result.stderr
    assert [c for c in calls if c.startswith("rm ")] == []


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
def test_no_containers_is_a_clean_no_op(tmp_path: Path):
    result, calls = _run(tmp_path, [])
    assert result.returncode == 0, result.stderr
    assert [c for c in calls if c.startswith("rm ")] == []


def test_the_rollback_converge_runs_the_same_cleanup():
    """Both converges go through restart_stack, so both get the cleanup.

    The 2026-10-01 conflict hit the ROLLBACK converge. A cleanup only on the
    forward path would not have saved it.
    """
    body = SCRIPT.read_text(encoding="utf-8")
    rollback = body[body.index("# --- 6. unhealthy -> restore the bundle"):]
    assert "if ! restart_stack; then" in rollback
    assert "remove_compose_temp_daemons" in _function("restart_stack")


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
@pytest.mark.parametrize("state", ["running", "restarting", "paused"])
def test_a_temp_named_container_that_may_be_serving_is_left_alone(tmp_path: Path, state: str):
    """`compose start` can start a temp-named replacement without renaming it, so a
    running one may be the only daemon serving. Removing it would SIGKILL prod."""
    result, calls = _run(tmp_path, ["1cc5a277f659_tinyassets-daemon"], state=state)
    assert result.returncode == 0, result.stderr
    assert [c for c in calls if c.startswith("rm ")] == []
    assert "leaving it" in result.stderr


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
@pytest.mark.parametrize("state", ["created", "exited", "dead"])
def test_every_not_running_state_is_cleaned(tmp_path: Path, state: str):
    result, calls = _run(tmp_path, ["1cc5a277f659_tinyassets-daemon"], state=state)
    assert result.returncode == 0, result.stderr
    assert [c for c in calls if c.startswith("rm ")] == ["rm -f 1cc5a277f659_tinyassets-daemon"]
