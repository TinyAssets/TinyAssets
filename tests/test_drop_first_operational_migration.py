"""Regressions for the two operational hazards the drop-first migration creates.

1. The env-apply helper reads the running daemon with a deliberately fail-open
   `|| true`. If the wrapper is absent (merge landed, image not deployed yet),
   that read returns "" and the caller mutates /etc/tinyassets/env and restarts
   the daemon on a false premise. The version preflight must therefore sit
   ABOVE that read and above every mutation, and must refuse without a restart
   and without a bare `docker exec ... printenv` fallback.

2. The compose healthcheck now needs a binary that ships with the image. That
   is only safe because deploy_fail_safe.sh restores the runtime bundle (which
   carries compose.yml) BEFORE it rolls the image back. Both rollback paths
   must keep that order.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APPLY = REPO / "deploy" / "apply-daemon-env-remote.sh"
FAIL_SAFE = REPO / "deploy" / "deploy_fail_safe.sh"
COMPOSE = REPO / "deploy" / "compose.yml"


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _first(lines: list[str], pattern: str) -> int:
    rx = re.compile(pattern)
    for i, line in enumerate(lines):
        if rx.search(line):
            return i
    raise AssertionError(f"pattern not found: {pattern}")


def _all(lines: list[str], pattern: str) -> list[int]:
    """Executable occurrences only — a header comment that *mentions*
    `systemctl restart` is documentation, not a mutation."""
    rx = re.compile(pattern)
    return [
        i for i, line in enumerate(lines)
        if rx.search(line) and not line.lstrip().startswith("#")
    ]


# --- 1. fail before mutation, no restart -------------------------------

def test_version_preflight_precedes_the_fail_open_read():
    lines = _lines(APPLY)
    preflight = _first(lines, r'"\$TA_OP" version')
    read = _first(lines, r'ta_op_printenv "')
    assert preflight < read, (
        "the fail-open `|| true` read must never be what discovers a missing "
        "wrapper"
    )


def test_version_preflight_precedes_every_mutation():
    lines = _lines(APPLY)
    preflight = _first(lines, r'"\$TA_OP" version')
    for pattern in (
        r'bash "\$HELPER" set ',        # env-file write
        r'bash "\$HELPER" delete ',     # env-file delete
        r"^restart_daemon\(\)",         # the restart helper's definition
        r"systemctl restart",
    ):
        for hit in _all(lines, pattern):
            assert preflight < hit, f"{pattern!r} at line {hit + 1} precedes the preflight"


def test_missing_wrapper_refuses_without_restarting_or_falling_back():
    text = APPLY.read_text(encoding="utf-8")
    block = text.split('"$TA_OP" version', 1)[1].split("ta_op_printenv()", 1)[0]
    assert "exit 1" in block, "the preflight must abort, not warn"
    assert "systemctl" not in block, "no restart on the refusal path"
    assert "printenv" not in block.replace("ta_op_printenv", ""), (
        "no bare `docker exec ... printenv` fallback"
    )
    # The banner is checked, not merely the exit status: a future incompatible
    # wrapper must not pass as 'present'.
    assert '"ta-op 1 "*' in text


def test_no_unwrapped_daemon_printenv_survives():
    text = APPLY.read_text(encoding="utf-8")
    assert 'docker exec "$DAEMON_CONTAINER" printenv' not in text


# --- 2. coupled rollback ordering ---------------------------------------

def test_internal_rollback_restores_the_bundle_before_the_old_image():
    lines = _lines(FAIL_SAFE)
    # The internal path: the INSTALLED_THIS_RUN guard, then set_image "$PREV_IMAGE".
    guard = _first(lines, r'if \[ "\$INSTALLED_THIS_RUN" = "1" \]')
    restore = _first(lines[guard:], r"restore_previous_bundle") + guard
    set_image = _first(lines[guard:], r'set_image "\$PREV_IMAGE"') + guard
    assert restore < set_image, (
        "converging the previous IMAGE against the new CONFIG rolls back half a "
        "change — and would pair a ta-op healthcheck with an image that has no "
        "ta-op binary"
    )


def test_restore_bundle_path_restores_before_converging():
    lines = _lines(FAIL_SAFE)
    start = _first(lines, r'if \[ "\$RESTORE_BUNDLE" = "1" \]')
    restore = _first(lines[start:], r"restore_previous_bundle") + start
    after = lines[restore + 1:]
    converge = next(
        (i for i, line in enumerate(after) if re.search(r"set_image|restart_stack", line)),
        None,
    )
    assert converge is not None, "the --restore-bundle path must still converge"
    assert restore < restore + 1 + converge


def test_healthcheck_uses_the_wrapper_and_no_shell_fallback():
    """The unprivileged daemon probes itself with the canary, no wrapper, no shell."""
    text = COMPOSE.read_text(encoding="utf-8")
    assert "/app/scripts/mcp_public_canary.py" in text
    assert "ta-op" not in text
    assert "CMD-SHELL" not in text.split("healthcheck:", 1)[1].split("labels:", 1)[0], (
        "a shell fallback in the probe would silently undo the migration"
    )
