from __future__ import annotations

import functools
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "deploy" / "install-host-uptime-services.sh"
BOOTSTRAP = REPO / "deploy" / "hetzner-bootstrap.sh"
WORKFLOW = REPO / ".github" / "workflows" / "install-host-services.yml"
RESTART_WORKFLOW = REPO / ".github" / "workflows" / "restart-daemon.yml"
P0_WORKFLOW = REPO / ".github" / "workflows" / "p0-outage-triage.yml"
DEPLOY_WORKFLOW = REPO / ".github" / "workflows" / "deploy-prod.yml"

TIMERS = (
    "tinyassets-watchdog.timer",
    "daemon-watchdog.timer",
    "tinyassets-backup.timer",
    "tinyassets-prune.timer",
    "tinyassets-disk-watch.timer",
    "tinyassets-ship-logs.timer",
)
SERVICES = tuple(name.removesuffix(".timer") + ".service" for name in TIMERS)
UNIT_FILES = tuple(item for pair in zip(SERVICES, TIMERS, strict=True) for item in pair)
RUNTIME_FILES = (
    "deploy/daemon-watchdog.sh",
    "deploy/backup.sh",
    "deploy/ship-logs.sh",
    "scripts/__init__.py",
    "scripts/_canary_common.py",
    "scripts/watchdog.py",
    "scripts/mcp_public_canary.py",
    "scripts/disk_watch.py",
    "scripts/disk_autoprune.py",
    "scripts/daemon_image_retention.py",
    "scripts/rotate_run_transcripts.py",
    "scripts/backup_ship_gh.py",
    "scripts/backup_prune.py",
    "tinyassets/__init__.py",
    "tinyassets/ttl_memo.py",
    "tinyassets/storage/__init__.py",
    "tinyassets/storage/rotation.py",
)
# Not a RUNTIME_FILE: it is installed into /etc/systemd/journald.conf.d rather
# than into the content-addressed release directory, but it is part of the same
# manifest and the same transaction.
JOURNALD_DROPIN_SOURCE = "deploy/journald-tinyassets.conf"
JOURNALD_DROPIN_NAME = "tinyassets.conf"

_BASH = shutil.which("bash")


@functools.cache
def _is_wsl_bash() -> bool:
    if not _BASH or os.name != "nt":
        return False
    probe = subprocess.run(
        [_BASH, "-lc", "test -d /mnt/c"],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


def _require_meaningful_mode_checks(where: Path) -> None:
    """Skip unless this host can exercise the installer's mode comparison.

    Two preconditions, both real and both satisfied on Linux CI:

    * ``flock`` must exist. Git Bash ships none, and the installer refuses to
      start without it.
    * ``stat -c %a`` must report real mode bits **on the filesystem the test
      installs into**. Under WSL the Windows drive is DrvFs, which answers
      ``777`` for every file whatever ``chmod`` did. The installer compares
      installed modes to decide whether a release is already exact, so there
      that comparison can never match -- for a reason that has nothing to do
      with the installer.

    ``where`` must be the test's own ``tmp_path``: probing anywhere else
    measures the wrong filesystem. An earlier version of this probed the
    directory holding this file, which under WSL reported DrvFs even when
    ``tmp_path`` was real ext4, and skipped tests that would have run.
    """
    if not _BASH:
        pytest.skip("bash unavailable")
    probe = where / ".mode-probe"
    probe.write_text("x\n", encoding="utf-8")
    try:
        quoted = shlex.quote(_bash_path(probe))
        result = subprocess.run(
            [
                _BASH,
                "-lc",
                f"command -v flock >/dev/null || exit 3; "
                f"chmod 644 {quoted} && stat -c %a {quoted}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        probe.unlink(missing_ok=True)
    if result.returncode == 3:
        pytest.skip("flock is unavailable, so the installer refuses to start")
    if result.stdout.strip() != "644":
        pytest.skip(
            "the install filesystem does not report real mode bits "
            f"(stat -c %a said {result.stdout.strip()!r}); DrvFs under WSL"
        )


def _bash_path(path: Path) -> str:
    """Absolute path for bash — WITHOUT following symlinks.

    `Path.resolve()` looks like the obvious way to get an absolute path, but it
    also canonicalises symlinks away, and symlinks are what this suite is FOR.

    It broke three separate things, which together account for ALL TEN of this
    file's quarantined entries:

    - `_assert_current_release` — `test -L .../runtime/current` actually ran
      against `.../runtime/releases/<id>`, a real directory, so it could never
      pass once the installer created the link CORRECTLY.
    - `_bash_readlink` — `readlink` on an already-resolved path prints nothing
      and exits non-zero.
    - the backup-state classifier — the `-symlink-` parametrisation passes a
      symlinked `rclone.conf` through `_bash_path`, and resolving it made a
      deliberately-symlinked config look like a regular file, so the classifier
      returned `configured_ready` instead of `partial_or_invalid`. This is the
      one that does NOT go through the two `runtime/current` call sites, and
      the one I first mis-attributed to a separate cause.

    `os.path.abspath` gives the same absolute, `..`-normalised string while
    leaving the final component's link intact. For every non-symlink caller the
    two are equivalent.

    `os.path.abspath` gives the same absolute, `..`-normalised string while
    leaving the final component's link intact. For every non-symlink caller the
    two are equivalent.
    """
    absolute = Path(os.path.abspath(path))
    if _is_wsl_bash():
        drive = absolute.drive.rstrip(":").lower()
        suffix = absolute.as_posix().split(":", 1)[1]
        return f"/mnt/{drive}{suffix}"
    return str(absolute)


def _bash_path_env(fake_bin: Path) -> str:
    return f"{_bash_path(fake_bin)}:/usr/local/bin:/usr/bin:/bin"


def _backup_state_script() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    backup_step = next(
        step
        for step in workflow["jobs"]["install"]["steps"]
        if step.get("name") == "Ensure off-host backup configuration"
    )
    run = backup_step["run"]
    marker = "<<'BACKUP_STATE'\n"
    assert marker in run
    body = run.split(marker, 1)[1]
    terminator = "\nBACKUP_STATE\n"
    assert terminator in body
    return body.split(terminator, 1)[0]


def _backup_cleanup_function() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    backup_step = next(
        step
        for step in workflow["jobs"]["install"]["steps"]
        if step.get("name") == "Ensure off-host backup configuration"
    )
    run = backup_step["run"]
    marker = "cleanup() {\n"
    assert marker in run
    body = run.split(marker, 1)[1]
    terminator = "\ntrap cleanup EXIT"
    assert terminator in body
    return f"cleanup() {{\n{body.split(terminator, 1)[0]}"


def _backup_diagnostic_script() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    backup_step = next(
        step
        for step in workflow["jobs"]["install"]["steps"]
        if step.get("name") == "Ensure off-host backup configuration"
    )
    run = backup_step["run"]
    marker = "<<'DIAGNOSTIC_PY'\n"
    assert marker in run
    body = run.split(marker, 1)[1]
    terminator = "\nDIAGNOSTIC_PY\n"
    assert terminator in body
    return body.split(terminator, 1)[0]


def _backup_remote_install_script() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    backup_step = next(
        step
        for step in workflow["jobs"]["install"]["steps"]
        if step.get("name") == "Ensure off-host backup configuration"
    )
    run = backup_step["run"]
    marker = "<<'BACKUP_REMOTE'\n"
    assert marker in run
    body = run.split(marker, 1)[1]
    terminator = "\nBACKUP_REMOTE\n"
    assert terminator in body
    return body.split(terminator, 1)[0]


def _backup_exercise_script() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    backup_step = next(
        step
        for step in workflow["jobs"]["install"]["steps"]
        if step.get("name") == "Exercise and verify two-tier backup"
    )
    run = backup_step["run"]
    marker = "<<'BACKUP_EXERCISE'\n"
    assert marker in run
    body = run.split(marker, 1)[1]
    terminator = "\nBACKUP_EXERCISE\n"
    assert terminator in body
    return body.split(terminator, 1)[0]


def _run_backup_exercise(
    tmp_path: Path,
    *,
    emit_warning: bool = False,
) -> subprocess.CompletedProcess[str]:
    if not _BASH:
        pytest.skip("bash unavailable")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_sudo = fake_bin / "sudo"
    harness = tmp_path / "backup-exercise.sh"
    rclone_calls = tmp_path / "rclone-calls"
    journal_query = tmp_path / "journal-query"
    invocation_id = "0123456789abcdef0123456789abcdef"
    fake_sudo.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  rclone)
    count="$(cat "${{RCLONE_CALLS}}" 2>/dev/null || printf '0')"
    count=$((count + 1))
    printf '%s' "${{count}}" > "${{RCLONE_CALLS}}"
    if [[ "${{count}}" -le 2 ]]; then
      printf '%s\\n' \
        tinyassets-brain-2026-07-22T00-00-00Z.tar.gz \
        tinyassets-data-2026-07-22T00-00-00Z.tar.gz
    else
      printf '%s\\n' \
        tinyassets-brain-2026-07-24T00-00-00Z.tar.gz \
        tinyassets-data-2026-07-24T00-00-00Z.tar.gz
    fi
    ;;
  systemctl)
    if [[ "$*" == *"--property=Result"* ]]; then
      printf 'success\\n'
    elif [[ "$*" == *"--property=InvocationID"* ]]; then
      printf '{invocation_id}\\n'
    fi
    ;;
  journalctl)
    printf '%s\\n' "$*" > "${{JOURNAL_QUERY}}"
    [[ "$2" == "_SYSTEMD_INVOCATION_ID={invocation_id}" ]]
    if [[ "${{EMIT_WARNING:-0}}" == "1" ]]; then
      printf '%s\\n' 'WARN: retention failed'
    fi
    printf '%s\\n' \
      '  brain upload OK' \
      '  upload OK' \
      '  gh-ship: [backup-ship] uploaded: brain-asset' \
      '  gh-ship: [backup-ship] uploaded: full-asset' \
      'backup complete.'
    ;;
  *)
    exit 99
    ;;
esac
""",
        encoding="utf-8",
        newline="\n",
    )
    harness.write_text(
        _backup_exercise_script(),
        encoding="utf-8",
        newline="\n",
    )
    command = " ".join(
        (
            f"chmod +x {shlex.quote(_bash_path(fake_sudo))} &&",
            f"RCLONE_CALLS={shlex.quote(_bash_path(rclone_calls))}",
            f"JOURNAL_QUERY={shlex.quote(_bash_path(journal_query))}",
            f"EMIT_WARNING={int(emit_warning)}",
            f"PATH={shlex.quote(_bash_path(fake_bin))}:/usr/local/bin:/usr/bin:/bin",
            "bash",
            shlex.quote(_bash_path(harness)),
        )
    )
    return subprocess.run(
        [_BASH, "-lc", command],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _run_backup_remote_install(
    tmp_path: Path,
    *,
    succeed_on_probe: int,
) -> subprocess.CompletedProcess[str]:
    if not _BASH:
        pytest.skip("bash unavailable")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_sudo = fake_bin / "sudo"
    harness = tmp_path / "backup-remote.sh"
    remote_stage = tmp_path / "remote-stage"
    remote_stage.mkdir()
    (remote_stage / "rclone.conf").write_text(
        "[spaces]\ntype = s3\n",
        encoding="utf-8",
        newline="\n",
    )
    counter = tmp_path / "probe-count"
    delays = tmp_path / "probe-delays"
    fake_sudo.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  install)
    exit 0
    ;;
  bash)
    cat >/dev/null
    exit 0
    ;;
  rclone)
    count="$(cat "${PROBE_COUNTER}" 2>/dev/null || printf '0')"
    count=$((count + 1))
    printf '%s' "${count}" > "${PROBE_COUNTER}"
    [[ "${count}" -ge "${SUCCEED_ON_PROBE}" ]]
    ;;
  *)
    exit 99
    ;;
esac
""",
        encoding="utf-8",
        newline="\n",
    )
    harness.write_text(
        (
            'sleep() { printf \'%s\\n\' "$1" >> "${PROBE_DELAYS}"; }\n'
            + _backup_remote_install_script()
        ),
        encoding="utf-8",
        newline="\n",
    )
    destination = "spaces:workflow-backups-jonnyton-sfo3/workflow-backups"
    command = " ".join(
        (
            f"chmod +x {shlex.quote(_bash_path(fake_sudo))} &&",
            f"PROBE_COUNTER={shlex.quote(_bash_path(counter))}",
            f"PROBE_DELAYS={shlex.quote(_bash_path(delays))}",
            f"SUCCEED_ON_PROBE={succeed_on_probe}",
            f"PATH={shlex.quote(_bash_path(fake_bin))}:/usr/local/bin:/usr/bin:/bin",
            "bash",
            shlex.quote(_bash_path(harness)),
            shlex.quote(_bash_path(remote_stage)),
            shlex.quote(destination),
        )
    )
    return subprocess.run(
        [_BASH, "-lc", command],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _run_backup_diagnostic(
    tmp_path: Path,
    response: str,
) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "diagnostic.py"
    response_file = tmp_path / "response.json"
    script.write_text(
        _backup_diagnostic_script(),
        encoding="utf-8",
        newline="\n",
    )
    response_file.write_text(response, encoding="utf-8", newline="\n")
    return subprocess.run(
        [sys.executable, str(script), str(response_file)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _run_backup_cleanup(
    tmp_path: Path,
    *,
    delete_status: str,
    transport_rc: int,
) -> subprocess.CompletedProcess[str]:
    if not _BASH:
        pytest.skip("bash unavailable")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    harness = tmp_path / "cleanup-harness.sh"
    fake_curl.write_text(
        f"#!/usr/bin/env bash\nprintf '%s' {shlex.quote(delete_status)}\n"
        f"exit {transport_rc}\n",
        encoding="utf-8",
        newline="\n",
    )
    temp_files = [tmp_path / name for name in ("response", "credentials", "rclone", "header")]
    for path in temp_files:
        path.write_text("fixture\n", encoding="utf-8", newline="\n")
    assignments = "\n".join(
        (
            f"response_file={shlex.quote(_bash_path(temp_files[0]))}",
            f"credentials_file={shlex.quote(_bash_path(temp_files[1]))}",
            f"rclone_file={shlex.quote(_bash_path(temp_files[2]))}",
            f"api_header_file={shlex.quote(_bash_path(temp_files[3]))}",
            'remote_stage=""',
            'created_access_key="ACCESSKEY12345678"',
            'DO_SSH_USER="fixture"',
            'DO_DROPLET_HOST="fixture"',
        )
    )
    harness.write_text(
        f"set -uo pipefail\n{assignments}\n{_backup_cleanup_function()}\n"
        "false\ncleanup\n",
        encoding="utf-8",
        newline="\n",
    )
    command = (
        f"chmod +x {shlex.quote(_bash_path(fake_curl))} && "
        f"PATH={shlex.quote(_bash_path(fake_bin))}:/usr/local/bin:/usr/bin:/bin "
        f"bash {shlex.quote(_bash_path(harness))}"
    )
    return subprocess.run(
        [_BASH, "-lc", command],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _run_backup_state(
    tmp_path: Path,
    *,
    env_text: str | None,
    config_kind: str,
    config_mode: str = "600",
    rclone_rc: int = 0,
) -> subprocess.CompletedProcess[str]:
    if not _BASH:
        pytest.skip("bash unavailable")
    state_script = tmp_path / "backup-state.sh"
    env_file = tmp_path / "tinyassets.env"
    config = tmp_path / "rclone.conf"
    real_config = tmp_path / "rclone.real.conf"
    fake_rclone = tmp_path / "rclone"
    state_script.write_text(
        _backup_state_script(),
        encoding="utf-8",
        newline="\n",
    )
    if env_text is not None:
        env_file.write_text(env_text, encoding="utf-8", newline="\n")
    if config_kind == "regular":
        config.write_text(
            "[spaces]\ntype = s3\n",
            encoding="utf-8",
            newline="\n",
        )
    elif config_kind == "symlink":
        real_config.write_text(
            "[spaces]\ntype = s3\n",
            encoding="utf-8",
            newline="\n",
        )
    elif config_kind != "absent":
        raise AssertionError(f"unknown config kind: {config_kind}")
    fake_rclone.write_text(
        f"#!/usr/bin/env bash\nexit {rclone_rc}\n",
        encoding="utf-8",
        newline="\n",
    )

    setup = [f"chmod +x {shlex.quote(_bash_path(fake_rclone))}"]
    if config_kind == "regular":
        setup.append(f"chmod {config_mode} {shlex.quote(_bash_path(config))}")
    elif config_kind == "symlink":
        setup.extend(
            (
                f"chmod {config_mode} {shlex.quote(_bash_path(real_config))}",
                (
                    f"ln -s {shlex.quote(_bash_path(real_config))} "
                    f"{shlex.quote(_bash_path(config))}"
                ),
            )
        )
    subprocess.run(
        [_BASH, "-lc", " && ".join(setup)],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    identity_target = real_config if config_kind == "symlink" else config
    if config_kind == "absent":
        expected_identity = "root:root 600"
    else:
        actual_identity = subprocess.run(
            [
                _BASH,
                "-lc",
                f"stat -c '%U:%G %a' {shlex.quote(_bash_path(identity_target))}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        if config_mode == "600":
            expected_identity = actual_identity
        else:
            owner_group = actual_identity.rsplit(" ", 1)[0]
            expected_identity = f"{owner_group} 600"

    args = (
        "spaces:workflow-backups-jonnyton-sfo3/workflow-backups",
        _bash_path(env_file),
        _bash_path(config),
        _bash_path(fake_rclone),
        expected_identity,
    )
    command = " ".join(
        (
            "bash",
            shlex.quote(_bash_path(state_script)),
            *(shlex.quote(arg) for arg in args),
        )
    )
    return subprocess.run(
        [_BASH, "-lc", command],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _assert_current_release(runtime_root: Path) -> Path:
    current = runtime_root / "current"
    result = subprocess.run(
        [
            _BASH,
            "-lc",
            (
                f"test -L {shlex.quote(_bash_path(current))} && "
                f"test -d {shlex.quote(_bash_path(current))}"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    releases = [path for path in (runtime_root / "releases").iterdir() if path.is_dir()]
    assert len(releases) == 1
    return releases[0]


def _bash_readlink(path: Path) -> str:
    result = subprocess.run(
        [_BASH, "-lc", f"readlink {shlex.quote(_bash_path(path))}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return result.stdout.strip()


def _bash_path_exists(path: Path) -> bool:
    result = subprocess.run(
        [
            _BASH,
            "-lc",
            (
                f"test -e {shlex.quote(_bash_path(path))} || "
                f"test -L {shlex.quote(_bash_path(path))}"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result.returncode == 0


@pytest.fixture(autouse=True)
def _clean_wsl_current_links(tmp_path):
    yield
    if _is_wsl_bash() and tmp_path.exists():
        subprocess.run(
            [
                _BASH,
                "-lc",
                (
                    f"find {shlex.quote(_bash_path(tmp_path))} "
                    "-type l -name current -delete"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )


def _run_installer(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    if not _BASH:
        pytest.skip("bash unavailable")
    if _is_wsl_bash():
        assignments = " ".join(
            f"{key}={shlex.quote(value)}" for key, value in env.items()
        )
        command = f"/usr/bin/env {assignments} {shlex.quote(_bash_path(INSTALLER))}"
        return subprocess.run(
            [_BASH, "-lc", command],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    return subprocess.run(
        [_BASH, str(INSTALLER)],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _popen_installer(env: dict[str, str]) -> subprocess.Popen[str]:
    if _is_wsl_bash():
        assignments = " ".join(
            f"{key}={shlex.quote(value)}" for key, value in env.items()
        )
        command = f"/usr/bin/env {assignments} {shlex.quote(_bash_path(INSTALLER))}"
        return subprocess.Popen(
            [_BASH, "-lc", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    return subprocess.Popen(
        [_BASH, str(INSTALLER)],
        env={**os.environ, **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _copy_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    for relative in (*UNIT_FILES, *RUNTIME_FILES, JOURNALD_DROPIN_SOURCE):
        if relative in UNIT_FILES:
            source_file = REPO / "deploy" / relative
            target = source / "deploy" / relative
        else:
            source_file = REPO / relative
            target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target)
    return source


def _fake_tools(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "systemctl").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
echo "${INSTALL_RUN_ID:-run}:$*" >> "$SYSTEMCTL_LOG"
cmd="$1"; shift
case "$cmd" in
  show)
    [[ "${FAIL_SYSTEMCTL_AT:-}" == "show" ]] && exit 68
    property="$1"
    unit="${@: -1}"
    if [[ "$property" == "--property=ActiveState" ]]; then
      if [[ "${FORCE_ACTIVE_SERVICE:-}" == "$unit" ]]; then
        echo "${FORCE_ACTIVE_STATE:-active}"
      elif [[ -f "$SYSTEMCTL_STATE/$unit.active" ]]; then
        echo active
      else
        echo inactive
      fi
    elif [[ -f "$SYSTEMD_UNITS/$unit" ]]; then
      echo loaded
    else
      echo not-found
    fi
    ;;
  stop)
    for unit in "$@"; do
      [[ -f "$SYSTEMD_UNITS/$unit" ]] || exit 5
      [[ "${FAIL_STOP_UNIT:-}" == "$unit" ]] && exit 69
      rm -f "$SYSTEMCTL_STATE/$unit.active"
    done
    ;;
  is-active)
    [[ "$1" == "--quiet" ]] && shift
    [[ "${FORCE_ACTIVE_SERVICE:-}" == "$1" ]] && exit 0
    [[ -f "$SYSTEMCTL_STATE/$1.active" ]]
    ;;
  reset-failed|restart)
    exit 0
    ;;
  daemon-reload)
    [[ "${FAIL_SYSTEMCTL_AT:-}" == "daemon-reload" ]] && exit 70
    [[ -n "${DAEMON_RELOAD_MARKER:-}" ]] && touch "$DAEMON_RELOAD_MARKER"
    [[ "${DAEMON_RELOAD_SLEEP:-0}" == "0" ]] || sleep "$DAEMON_RELOAD_SLEEP"
    ;;
  enable)
    [[ "${FAIL_SYSTEMCTL_AT:-}" == "enable" ]] && exit 71
    [[ "$1" == "--now" ]] && shift
    for unit in "$@"; do
      touch "$SYSTEMCTL_STATE/$unit.enabled" "$SYSTEMCTL_STATE/$unit.active"
    done
    ;;
  is-enabled)
    [[ "${FAIL_SYSTEMCTL_AT:-}" == "is-enabled" ]] && exit 72
    [[ -f "$SYSTEMCTL_STATE/$1.enabled" ]]
    ;;
  *)
    exit 73
    ;;
esac
""",
        encoding="utf-8",
        newline="\n",
    )
    (fake_bin / "systemctl").chmod(0o755)
    (fake_bin / "visudo").write_text(
        """#!/usr/bin/env bash
[[ "${FAIL_VISUDO:-0}" == "1" ]] && exit 80
[[ "$1" == "-cf" && -f "$2" ]]
""",
        encoding="utf-8",
        newline="\n",
    )
    (fake_bin / "visudo").chmod(0o755)
    (fake_bin / "docker").write_text(
        """#!/usr/bin/env bash
if [[ "$1" == "inspect" ]]; then
  echo true
  exit 0
fi
if [[ "$1" == "volume" && "$2" == "inspect" ]]; then
  exit 1
fi
exit 90
""",
        encoding="utf-8",
        newline="\n",
    )
    (fake_bin / "docker").chmod(0o755)
    return fake_bin


def _install_env(tmp_path: Path, source: Path | None = None) -> dict[str, str]:
    source = source or _copy_source(tmp_path)
    fake_bin = _fake_tools(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    return {
        "TINYASSETS_SOURCE_ROOT": _bash_path(source),
        "TINYASSETS_RUNTIME_ROOT": _bash_path(tmp_path / "runtime"),
        "TINYASSETS_SYSTEMD_DIR": _bash_path(tmp_path / "systemd"),
        "TINYASSETS_JOURNALD_DIR": _bash_path(tmp_path / "journald.conf.d"),
        "TINYASSETS_SUDOERS_DIR": _bash_path(tmp_path / "sudoers"),
        "TINYASSETS_LOCK_DIR": _bash_path(tmp_path / "locks"),
        "TINYASSETS_SOURCE_SHA": "a" * 40,
        "TINYASSETS_ALLOW_TEST_ROOTS": "1",
        "TINYASSETS_ACTIVE_WAIT_SECONDS": "0",
        "TINYASSETS_LOCK_WAIT_SECONDS": "60",
        "SYSTEMCTL_BIN": _bash_path(fake_bin / "systemctl"),
        "VISUDO_BIN": _bash_path(fake_bin / "visudo"),
        "SYSTEMCTL_LOG": _bash_path(tmp_path / "systemctl.log"),
        "SYSTEMCTL_STATE": _bash_path(state),
        "SYSTEMD_UNITS": _bash_path(tmp_path / "systemd"),
        "PATH": _bash_path_env(fake_bin),
    }


def test_fresh_install_converges_exact_manifest(tmp_path):
    env = _install_env(tmp_path)
    result = _run_installer(env)

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    systemd = tmp_path / "systemd"
    assert {path.name for path in systemd.iterdir()} == set(UNIT_FILES)
    current = _assert_current_release(tmp_path / "runtime")
    assert {
        path.relative_to(current).as_posix()
        for path in current.rglob("*")
        if path.is_file()
    } == set(RUNTIME_FILES)
    for service in (
        "tinyassets-watchdog.service",
        "daemon-watchdog.service",
        "tinyassets-backup.service",
        "tinyassets-prune.service",
        "tinyassets-disk-watch.service",
        "tinyassets-ship-logs.service",
    ):
        text = (systemd / service).read_text(encoding="utf-8")
        assert "/opt/tinyassets-host-uptime/current/" in text
        assert "/opt/tinyassets/scripts/" not in text
        assert "/opt/tinyassets/deploy/" not in text
    for relative in RUNTIME_FILES:
        assert (current / relative).read_bytes() == (REPO / relative).read_bytes()
    for unit in UNIT_FILES:
        assert (systemd / unit).read_bytes() == (REPO / "deploy" / unit).read_bytes()
    disk_watch = (systemd / "tinyassets-disk-watch.service").read_text(
        encoding="utf-8"
    )
    assert "WorkingDirectory=/opt/tinyassets-host-uptime/current" in disk_watch
    for watchdog_service in (
        "tinyassets-watchdog.service",
        "daemon-watchdog.service",
    ):
        watchdog_unit = (systemd / watchdog_service).read_text(encoding="utf-8")
        assert "EnvironmentFile=/etc/tinyassets/env" in watchdog_unit
        assert 'ExecCondition=/usr/bin/test -n "${TINYASSETS_IMAGE}"' in (
            watchdog_unit
        )
    log = (tmp_path / "systemctl.log").read_text(encoding="utf-8")
    assert "\nrun:stop " not in f"\n{log}"
    assert f"enable --now {' '.join(TIMERS)}" in log
    for timer in TIMERS:
        assert f"is-enabled {timer}" in log
        assert f"is-active {timer}" in log


def test_repeat_install_repairs_disabled_current_timer(tmp_path):
    env = _install_env(tmp_path)
    assert _run_installer(env).returncode == 0
    state = tmp_path / "state"
    (state / f"{TIMERS[0]}.active").unlink()
    (state / f"{TIMERS[0]}.enabled").unlink()

    result = _run_installer(env)

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert (state / f"{TIMERS[0]}.active").exists()
    assert (state / f"{TIMERS[0]}.enabled").exists()


def test_repeat_install_with_identical_content_stops_no_timer(tmp_path):
    """The second daemon restart per merge came from here.

    Stopping ``daemon-watchdog.timer`` and starting it again is not free: the
    timer declares ``OnBootSec=2min``, an elapse long past on a host up for
    days, so systemd fires ``daemon-watchdog.service`` the instant the timer
    starts. That run lands seconds after the deploy recreated the daemon, while
    the fresh container has not written a heartbeat -- and the watchdog restarts
    the daemon on a stale heartbeat. So every merge killed in-flight user turns
    twice: once for the deploy's own recreate, once for a watchdog tick this
    script provoked.

    A merge that installs nothing must therefore touch nothing. The second run
    here carries a DIFFERENT source sha with byte-identical content, which is
    the real case: releases are named with the sha, so without a content
    comparison every merge looks like work to do.
    """
    _require_meaningful_mode_checks(tmp_path)
    env = _install_env(tmp_path)
    first = _run_installer(env)
    assert first.returncode == 0, f"{first.stdout}\n{first.stderr}"
    log_path = tmp_path / "systemctl.log"
    first_log = log_path.read_text(encoding="utf-8")
    # The first install really did the transaction -- otherwise this test would
    # pass by never installing anything at all. (It stops no timer: on a fresh
    # host the units do not exist yet, so the pause loop sees `not-found`. The
    # stop only has something to stop from the SECOND install onwards, which is
    # exactly the case this test is about.)
    assert "run:daemon-reload" in first_log
    assert "run:enable --now" in first_log
    release_before = _assert_current_release(tmp_path / "runtime")

    log_path.unlink()
    second = _run_installer(
        {**env, "INSTALL_RUN_ID": "second", "TINYASSETS_SOURCE_SHA": "b" * 40}
    )

    assert second.returncode == 0, f"{second.stdout}\n{second.stderr}"
    assert "already current" in second.stdout
    second_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    for forbidden in ("second:stop", "second:daemon-reload", "second:enable --now"):
        assert forbidden not in second_log, second_log
    # Nothing moved, and the live release is still the one already installed.
    assert _assert_current_release(tmp_path / "runtime") == release_before


# --- drift the gate must NOT bless -----------------------------------------
#
# Content alone is not the test. Each case below is a drift the transaction
# repairs, so a gate that reads it as "already current" makes it permanent --
# strictly worse than the redundant work the gate removes. Two of them disable
# the daemon's own recovery lever and one is a local-root foothold, so these are
# the floor, not polish. Found by cross-family review of #3989 round 1.


def _sudoers_file(tmp_path: Path) -> Path:
    return tmp_path / "sudoers" / "tinyassets-watchdog"


def _installed_unit(tmp_path: Path, name: str) -> Path:
    return tmp_path / "systemd" / name


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform
        pytest.skip(f"symlink creation is unavailable here: {exc}")


def _drift_sudoers_world_writable(tmp_path: Path) -> None:
    _sudoers_file(tmp_path).chmod(0o666)


def _drift_sudoers_becomes_a_symlink(tmp_path: Path) -> None:
    path = _sudoers_file(tmp_path)
    elsewhere = tmp_path / "sudoers-elsewhere"
    elsewhere.write_bytes(path.read_bytes())  # byte-identical on purpose
    elsewhere.chmod(0o440)
    path.unlink()
    _symlink_or_skip(path, elsewhere)


def _drift_root_unit_world_writable(tmp_path: Path) -> None:
    _installed_unit(tmp_path, "daemon-watchdog.service").chmod(0o666)


def _drift_current_points_outside_releases(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    release = runtime / _bash_readlink(runtime / "current")
    rogue = tmp_path / "rogue-release"
    shutil.copytree(release, rogue)  # identical content, unmanaged location
    (runtime / "current").unlink()
    _symlink_or_skip(runtime / "current", rogue)


def _repoint_current(tmp_path: Path, target: str) -> None:
    current = tmp_path / "runtime" / "current"
    current.unlink()
    _symlink_or_skip(current, Path(target))


def _drift_current_points_at_the_releases_dir(tmp_path: Path) -> None:
    # `releases/.` is made entirely of "safe-looking" characters, so a character
    # class accepts it -- and it resolves to the releases directory itself.
    _repoint_current(tmp_path, "releases/.")


def _drift_current_points_above_the_releases_dir(tmp_path: Path) -> None:
    # `releases/..` likewise, resolving to RUNTIME_ROOT.
    _repoint_current(tmp_path, "releases/..")


def _drift_timer_stopped_but_still_enabled(tmp_path: Path) -> None:
    (tmp_path / "state" / f"{TIMERS[0]}.active").unlink()


def _repair_sudoers_is_an_exact_regular_file(tmp_path: Path) -> None:
    path = _sudoers_file(tmp_path)
    assert not path.is_symlink()
    assert oct(path.stat().st_mode & 0o777) == "0o440"


def _repair_unit_is_not_world_writable(tmp_path: Path) -> None:
    unit = _installed_unit(tmp_path, "daemon-watchdog.service")
    assert oct(unit.stat().st_mode & 0o777) == "0o644"


def _repair_current_points_at_a_release_id(tmp_path: Path) -> None:
    # The exact shape, not merely the prefix: `releases/.` and `releases/..` both
    # start with "releases/", so a prefix check would call either of them
    # repaired.
    target = _bash_readlink(tmp_path / "runtime" / "current")
    assert re.fullmatch(r"releases/[0-9a-f]{40}-[0-9a-f]{16}", target), target


def _repair_timer_is_active_again(tmp_path: Path) -> None:
    assert (tmp_path / "state" / f"{TIMERS[0]}.active").exists()


@pytest.mark.parametrize(
    "drift,repair",
    [
        pytest.param(
            _drift_sudoers_world_writable,
            _repair_sudoers_is_an_exact_regular_file,
            id="sudoers-0666",
        ),
        pytest.param(
            _drift_sudoers_becomes_a_symlink,
            _repair_sudoers_is_an_exact_regular_file,
            id="sudoers-symlink",
        ),
        pytest.param(
            _drift_root_unit_world_writable,
            _repair_unit_is_not_world_writable,
            id="root-unit-0666",
        ),
        pytest.param(
            _drift_current_points_outside_releases,
            _repair_current_points_at_a_release_id,
            id="current-outside-releases",
        ),
        # These two pin that the transaction REPAIRS such a pointer. They do not
        # discriminate the pointer pattern -- see
        # test_the_current_pointer_pattern_admits_only_a_release_id for why no
        # behavioural test can.
        pytest.param(
            _drift_current_points_at_the_releases_dir,
            _repair_current_points_at_a_release_id,
            id="current-is-releases-dot",
        ),
        pytest.param(
            _drift_current_points_above_the_releases_dir,
            _repair_current_points_at_a_release_id,
            id="current-is-releases-dotdot",
        ),
        pytest.param(
            _drift_timer_stopped_but_still_enabled,
            _repair_timer_is_active_again,
            id="timer-enabled-but-stopped",
        ),
    ],
)
def test_the_gate_does_not_bless_drift_the_transaction_repairs(tmp_path, drift, repair):
    """Install, break one property, install again: the second run must work.

    Stated as "the transaction ran and the drift is gone", not merely "the gate
    said no" -- a gate that declines but then fails to repair would pass the
    first half of that and still leave the host wrong.
    """
    _require_meaningful_mode_checks(tmp_path)
    env = _install_env(tmp_path)
    first = _run_installer(env)
    assert first.returncode == 0, f"{first.stdout}\n{first.stderr}"

    drift(tmp_path)
    log_path = tmp_path / "systemctl.log"
    log_path.unlink()
    second = _run_installer({**env, "INSTALL_RUN_ID": "second"})

    assert second.returncode == 0, f"{second.stdout}\n{second.stderr}"
    assert "already current" not in second.stdout, second.stdout
    second_log = log_path.read_text(encoding="utf-8")
    assert "second:daemon-reload" in second_log, second_log
    assert "second:enable --now" in second_log, second_log
    repair(tmp_path)


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
def test_the_current_pointer_pattern_admits_only_a_release_id():
    """Defence in depth, tested at the PATTERN rather than at the outcome.

    `releases/.` and `releases/..` are made entirely of characters a
    "safe-looking" class accepts, and they resolve to the releases directory and
    to RUNTIME_ROOT. Today the file checks *below* the pointer test decline both
    anyway -- neither directory holds the runtime files -- so a behavioural test
    cannot tell a loose class from the exact shape, and the two drift cases above
    pass either way. That is precisely why this is pinned here: the gate must not
    depend on a later check to reject a pointer it should never have accepted.

    The pattern is read out of the script and evaluated by bash, so loosening
    that line turns this red; restating the regex here would not.
    """
    source = INSTALLER.read_text(encoding="utf-8")
    match = re.search(r'\[\[ "\$\{link_target\}" =~ (\S+) \]\]', source)
    assert match, "the pointer check moved or changed shape; update this test"
    pattern = match.group(1)

    real = f"releases/{'a' * 40}-{'b' * 16}"
    accept = [real]
    reject = [
        "releases/.",
        "releases/..",
        "releases/",
        f"/abs/releases/{'a' * 40}-{'b' * 16}",
        f"releases/sub/{'a' * 40}-{'b' * 16}",
        f"releases/{'a' * 39}-{'b' * 16}",  # sha one char short
        f"releases/{'a' * 40}-{'b' * 15}",  # hash one char short
        f"releases/{'A' * 40}-{'b' * 16}",  # uppercase is not what we write
        f"releases/{'z' * 40}-{'b' * 16}",  # not hex
        f"releases/{'a' * 40}_{'b' * 16}",  # wrong separator
    ]

    def matches(value: str) -> bool:
        # Through the ENVIRONMENT, not argv. Git Bash mangles braces out of
        # arguments -- `{40}` arrives as `40` -- which silently turns an interval
        # quantifier into a literal and makes every comparison here wrong.
        # Verified on this host before relying on it.
        result = subprocess.run(
            [_BASH, "-c", '[[ "$TA_VALUE" =~ $TA_PATTERN ]]'],
            env={**os.environ, "TA_PATTERN": pattern, "TA_VALUE": value},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode in (0, 1), f"{result.stdout}\n{result.stderr}"
        return result.returncode == 0

    assert [value for value in accept if not matches(value)] == []
    assert [value for value in reject if matches(value)] == []


def test_repeat_install_with_changed_content_still_stops_timers(tmp_path):
    """The gate above must not make the installer inert. A real change still
    takes the full transaction -- otherwise a watchdog edit would never reach
    the host."""
    _require_meaningful_mode_checks(tmp_path)
    source = _copy_source(tmp_path)
    env = _install_env(tmp_path, source)
    assert _run_installer(env).returncode == 0
    (source / "scripts" / "watchdog.py").write_text(
        "WATCH = 2\n", encoding="utf-8", newline="\n"
    )
    log_path = tmp_path / "systemctl.log"
    log_path.unlink()

    result = _run_installer({**env, "INSTALL_RUN_ID": "second"})

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "already current" not in result.stdout
    second_log = log_path.read_text(encoding="utf-8")
    assert "second:stop" in second_log
    assert "second:daemon-reload" in second_log
    # Not _assert_current_release: a real change leaves the previous release in
    # place (KEEP_RELEASES), so there is more than one and that helper insists
    # on exactly one.
    runtime = tmp_path / "runtime"
    release = runtime / _bash_readlink(runtime / "current")
    assert (release / "scripts" / "watchdog.py").read_text(encoding="utf-8") == "WATCH = 2\n"


def test_repeat_install_repairs_corrupt_content_addressed_release(tmp_path):
    source = _copy_source(tmp_path)
    env = _install_env(tmp_path, source)
    first = _run_installer(env)
    assert first.returncode == 0, f"{first.stdout}\n{first.stderr}"
    release = _assert_current_release(tmp_path / "runtime")
    installed_watchdog = release / "scripts" / "watchdog.py"
    installed_watchdog.write_text("corrupt\n", encoding="utf-8", newline="\n")
    extra = release / "scripts" / "unexpected.py"
    extra.write_text("unexpected\n", encoding="utf-8", newline="\n")

    result = _run_installer(env)

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert installed_watchdog.read_bytes() == (
        source / "scripts" / "watchdog.py"
    ).read_bytes()
    assert not extra.exists()
    assert _bash_readlink(tmp_path / "runtime" / "current") == (
        f"releases/{release.name}"
    )


def test_install_bounds_old_runtime_releases_and_keeps_current(tmp_path):
    # Every deploy installs a new content-addressed release; production held 257
    # of them on 2026-09-24. The installer keeps the newest few for a manual
    # pointer rollback and never touches the live target or a foreign entry.
    env = _install_env(tmp_path)
    releases = tmp_path / "runtime" / "releases"
    releases.mkdir(parents=True)
    old = []
    for n in range(8):
        name = f"{n:040x}-{n:016x}"
        (releases / name / "scripts").mkdir(parents=True)
        stamp = time.time() - (100 - n) * 3600
        os.utime(releases / name, (stamp, stamp))
        old.append(name)
    foreign = releases / "operator-notes"
    foreign.mkdir()
    os.utime(foreign, (0, 0))

    result = _run_installer(env)

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    current = _bash_readlink(tmp_path / "runtime" / "current").removeprefix("releases/")
    remaining = {path.name for path in releases.iterdir()}
    assert current in remaining
    assert foreign.name in remaining
    kept_old = sorted(remaining - {current, foreign.name})
    # KEEP=5 newest releases including the one just installed.
    assert kept_old == old[-4:]
    assert "pruned 4 old runtime release" in result.stdout


def test_missing_manifest_source_fails_before_systemd(tmp_path):
    source = _copy_source(tmp_path)
    (source / RUNTIME_FILES[-1]).unlink()
    env = _install_env(tmp_path, source)

    result = _run_installer(env)

    assert result.returncode != 0
    assert not (tmp_path / "systemctl.log").exists()
    assert not (tmp_path / "runtime" / "current").exists()


def _change_runtime_content(source: Path) -> None:
    """Give the next install something to do.

    Since the idempotence gate (#3989), a repeat install of byte-identical
    content exits "already current" before it ever reads service state, so a
    test of the service-state refusal must change the bundle first -- the
    refusal only guards a run that would mutate the host.
    """
    (source / "scripts" / "watchdog.py").write_text(
        "WATCH = 2\n", encoding="utf-8", newline="\n"
    )


@pytest.mark.parametrize(
    "active_state", ["active", "activating", "reloading", "deactivating"]
)
def test_active_service_timeout_reactivates_timers_before_file_mutation(
    tmp_path, active_state
):
    source = _copy_source(tmp_path)
    env = _install_env(tmp_path, source)
    first = _run_installer(env)
    assert first.returncode == 0, f"{first.stdout}\n{first.stderr}"
    _change_runtime_content(source)
    current_before = _bash_readlink(tmp_path / "runtime" / "current")
    units_before = {
        unit: (tmp_path / "systemd" / unit).read_bytes()
        for unit in UNIT_FILES
    }
    env["FORCE_ACTIVE_SERVICE"] = SERVICES[2]
    env["FORCE_ACTIVE_STATE"] = active_state

    result = _run_installer(env)

    assert result.returncode != 0
    assert _bash_readlink(tmp_path / "runtime" / "current") == current_before
    assert {
        unit: (tmp_path / "systemd" / unit).read_bytes()
        for unit in UNIT_FILES
    } == units_before
    log = (tmp_path / "systemctl.log").read_text(encoding="utf-8")
    for timer in TIMERS:
        assert f"stop {timer}" in log
    assert f"enable --now {' '.join(TIMERS)}" in log


def test_unknown_service_state_fails_closed_before_file_mutation(tmp_path):
    source = _copy_source(tmp_path)
    env = _install_env(tmp_path, source)
    first = _run_installer(env)
    assert first.returncode == 0, f"{first.stdout}\n{first.stderr}"
    _change_runtime_content(source)
    current_before = _bash_readlink(tmp_path / "runtime" / "current")
    units_before = {
        unit: (tmp_path / "systemd" / unit).read_bytes()
        for unit in UNIT_FILES
    }
    env["FORCE_ACTIVE_SERVICE"] = SERVICES[2]
    env["FORCE_ACTIVE_STATE"] = "maintenance"

    result = _run_installer(env)

    assert result.returncode != 0
    assert "unsafe service active state" in result.stdout
    assert _bash_readlink(tmp_path / "runtime" / "current") == current_before
    assert {
        unit: (tmp_path / "systemd" / unit).read_bytes()
        for unit in UNIT_FILES
    } == units_before
    assert f"enable --now {' '.join(TIMERS)}" in (
        tmp_path / "systemctl.log"
    ).read_text(encoding="utf-8")


def test_partial_timer_stop_failure_reactivates_every_timer(tmp_path):
    source = _copy_source(tmp_path)
    env = _install_env(tmp_path, source)
    first = _run_installer(env)
    assert first.returncode == 0, f"{first.stdout}\n{first.stderr}"
    _change_runtime_content(source)
    env["FAIL_STOP_UNIT"] = TIMERS[2]

    result = _run_installer(env)

    assert result.returncode != 0
    state = tmp_path / "state"
    for timer in TIMERS:
        assert (state / f"{timer}.active").exists()
        assert (state / f"{timer}.enabled").exists()


@pytest.mark.parametrize("failure", ["show", "daemon-reload", "enable", "is-enabled"])
def test_systemd_failure_propagates(tmp_path, failure):
    env = _install_env(tmp_path)
    env["FAIL_SYSTEMCTL_AT"] = failure
    result = _run_installer(env)
    assert result.returncode != 0
    assert "converged" not in result.stdout.lower()
    assert not _bash_path_exists(tmp_path / "runtime" / "current")
    assert not any((tmp_path / "systemd").iterdir())


def test_post_mutation_failure_rolls_back_units_and_runtime(tmp_path):
    source = _copy_source(tmp_path)
    env = _install_env(tmp_path, source)
    first = _run_installer(env)
    assert first.returncode == 0, f"{first.stdout}\n{first.stderr}"
    current_before = _bash_readlink(tmp_path / "runtime" / "current")
    units_before = {
        unit: (tmp_path / "systemd" / unit).read_bytes()
        for unit in UNIT_FILES
    }
    (source / "deploy" / "tinyassets-watchdog.service").write_text(
        (source / "deploy" / "tinyassets-watchdog.service").read_text(
            encoding="utf-8"
        )
        + "\n# replacement candidate\n",
        encoding="utf-8",
        newline="\n",
    )
    (source / "scripts" / "watchdog.py").write_text(
        (source / "scripts" / "watchdog.py").read_text(encoding="utf-8")
        + "\n# replacement candidate\n",
        encoding="utf-8",
        newline="\n",
    )
    env["TINYASSETS_SOURCE_SHA"] = "b" * 40
    env["FAIL_SYSTEMCTL_AT"] = "daemon-reload"

    result = _run_installer(env)

    assert result.returncode != 0
    assert _bash_readlink(tmp_path / "runtime" / "current") == current_before
    assert {
        unit: (tmp_path / "systemd" / unit).read_bytes()
        for unit in UNIT_FILES
    } == units_before
    log = (tmp_path / "systemctl.log").read_text(encoding="utf-8")
    assert f"enable --now {' '.join(TIMERS)}" in log


def test_installed_disk_rotation_imports_from_runtime(tmp_path):
    env = _install_env(tmp_path)
    assert _run_installer(env).returncode == 0
    current = tmp_path / "runtime" / "current"
    data = tmp_path / "data"
    (data / "runs").mkdir(parents=True)
    result = subprocess.run(
        [
            _BASH,
            "-lc",
            (
                f"cd {shlex.quote(_bash_path(current))} && "
                f"TINYASSETS_DATA_DIR={shlex.quote(_bash_path(data))} "
                "python3 -m scripts.rotate_run_transcripts --dry-run"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_package_public_api_stays_compatible_but_initializes_lazily():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, tinyassets; "
                "assert 'tinyassets.discovery' not in sys.modules; "
                "[getattr(tinyassets, name) for name in tinyassets.__all__]"
            ),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_installed_operational_entrypoints_invoke_from_runtime(tmp_path):
    env = _install_env(tmp_path)
    assert _run_installer(env).returncode == 0
    current = tmp_path / "runtime" / "current"
    (tmp_path / "state" / "tinyassets-daemon.service.active").touch()
    commands = (
        "python3 scripts/watchdog.py --help",
        "python3 scripts/mcp_public_canary.py --help",
        "python3 scripts/disk_watch.py --help",
        "python3 scripts/disk_autoprune.py --help",
        "python3 scripts/daemon_image_retention.py --help",
        "python3 scripts/backup_ship_gh.py --help",
        "python3 scripts/backup_prune.py --help",
        (
            f"DRY_RUN=1 BACKUP_LOG={shlex.quote(_bash_path(tmp_path / 'backup.log'))} "
            "bash deploy/backup.sh"
        ),
        (
            f"PATH={shlex.quote(env['PATH'])} "
            f"SYSTEMCTL_LOG={shlex.quote(env['SYSTEMCTL_LOG'])} "
            f"SYSTEMCTL_STATE={shlex.quote(env['SYSTEMCTL_STATE'])} "
            f"SYSTEMD_UNITS={shlex.quote(env['SYSTEMD_UNITS'])} "
            f"TINYASSETS_DAEMON_WATCHDOG_LOCK="
            f"{shlex.quote(_bash_path(tmp_path / 'daemon-watchdog.lock'))} "
            f"TINYASSETS_COMPOSE_FILE={shlex.quote(_bash_path(tmp_path / 'missing.yml'))} "
            "bash deploy/daemon-watchdog.sh"
        ),
    )
    for command in commands:
        result = subprocess.run(
            [
                _BASH,
                "-lc",
                f"cd {shlex.quote(_bash_path(current))} && {command}",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, (
            f"command failed: {command}\n{result.stdout}\n{result.stderr}"
        )


def test_bounded_distinct_target_burst_is_isolated(tmp_path):
    cases = [(tmp_path / f"host-{index}", index) for index in range(64)]

    def install(item: tuple[Path, int]) -> subprocess.CompletedProcess[str]:
        case, index = item
        case.mkdir()
        source = _copy_source(case)
        watchdog = source / "scripts" / "watchdog.py"
        watchdog.write_text(
            watchdog.read_text(encoding="utf-8") + f"\n# host-{index}\n",
            encoding="utf-8",
            newline="\n",
        )
        return _run_installer(_install_env(case, source))

    with ThreadPoolExecutor(max_workers=64) as executor:
        results = list(executor.map(install, cases))

    failures = [
        f"host-{index}: {result.stdout}\n{result.stderr}"
        for index, result in enumerate(results)
        if result.returncode != 0
    ]
    assert not failures, "\n".join(failures)
    for case, index in cases:
        release = _assert_current_release(case / "runtime")
        watchdog = (release / "scripts" / "watchdog.py").read_text(
            encoding="utf-8"
        )
        assert watchdog.endswith(f"# host-{index}\n")


def test_same_target_burst_waits_and_each_caller_verifies(tmp_path):
    env = _install_env(tmp_path)
    env["DAEMON_RELOAD_SLEEP"] = "0.05"
    # Leave headroom for a loaded Windows/WSL runner while keeping the test
    # wait bounded below the subprocess harness's 120-second timeout.
    env["TINYASSETS_LOCK_WAIT_SECONDS"] = "110"
    caller_ids = tuple(f"same-{index}" for index in range(32))

    def install(caller_id: str) -> subprocess.CompletedProcess[str]:
        return _run_installer({**env, "INSTALL_RUN_ID": caller_id})

    with ThreadPoolExecutor(max_workers=32) as executor:
        results = list(executor.map(install, caller_ids))

    failures = [
        f"{caller_id}: {result.stdout}\n{result.stderr}"
        for caller_id, result in zip(caller_ids, results, strict=True)
        if result.returncode != 0
    ]
    assert not failures, "\n".join(failures)
    observed = [
        line.split(":", 1)[0]
        for line in (tmp_path / "systemctl.log").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    blocks = [
        caller_id
        for index, caller_id in enumerate(observed)
        if index == 0 or caller_id != observed[index - 1]
    ]
    assert len(blocks) == len(caller_ids)
    assert set(blocks) == set(caller_ids)


def test_same_target_lock_timeout_is_red_before_systemd(tmp_path):
    env = _install_env(tmp_path)
    marker = tmp_path / "reload.marker"
    env["DAEMON_RELOAD_MARKER"] = _bash_path(marker)
    env["DAEMON_RELOAD_SLEEP"] = "2"
    first = _popen_installer(env)
    deadline = time.time() + 10
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert marker.exists()
    second_env = {
        **env,
        "INSTALL_RUN_ID": "second",
        "DAEMON_RELOAD_SLEEP": "0",
        "TINYASSETS_LOCK_WAIT_SECONDS": "0",
    }
    second = _run_installer(second_env)
    first_stdout, first_stderr = first.communicate(timeout=20)

    assert first.returncode == 0, f"{first_stdout}\n{first_stderr}"
    assert second.returncode != 0
    log = (tmp_path / "systemctl.log").read_text(encoding="utf-8")
    assert "second:" not in log


def test_callers_and_workflow_have_one_pinned_installer_owner():
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    restart_text = RESTART_WORKFLOW.read_text(encoding="utf-8")
    restart = yaml.safe_load(restart_text)
    manifest = _run_installer({"TINYASSETS_PRINT_MANIFEST": "1"})

    assert bootstrap.count("install-host-uptime-services.sh") == 1
    for timer in TIMERS:
        assert f"systemctl enable --now {timer}" not in bootstrap
    checkout = workflow["jobs"]["install"]["steps"][0]
    assert checkout["with"]["ref"] == (
        "${{ github.event_name == 'workflow_run' && "
        "github.event.workflow_run.head_sha || github.sha }}"
    )
    assert "source_ref:" not in workflow_text
    assert "sha256sum" in workflow_text
    assert "mktemp -d /tmp/tinyassets-host-uptime." in workflow_text
    assert "REQUESTED_SOURCE_REF:" in workflow_text
    assert "Resolved requested source ${REQUESTED_SOURCE_REF}" in workflow_text
    assert '[[ "${source_sha}" == "${REQUESTED_SOURCE_REF}" ]]' in workflow_text
    assert "install-host-uptime-services.sh" in workflow_text
    assert (
        workflow_text.count(
            "guard-host-mutation --command-timeout 300 -- /bin/bash -se --"
        )
        == 2
    )
    assert workflow_text.count("<<'REMOTE'") == 1
    assert workflow_text.count("<<'BACKUP_REMOTE'") == 1
    assert 'remote_stage="$1"' in workflow_text
    assert manifest.returncode == 0, f"{manifest.stdout}\n{manifest.stderr}"
    assert manifest.stdout.splitlines() == [
        "deploy/install-host-uptime-services.sh",
        *(f"deploy/{unit}" for unit in UNIT_FILES),
        *RUNTIME_FILES,
        JOURNALD_DROPIN_SOURCE,
    ]
    restart_checkout = restart["jobs"]["restart"]["steps"][0]
    assert restart_checkout["with"]["ref"] == "${{ github.sha }}"
    assert "TINYASSETS_PRINT_MANIFEST=1" in restart_text
    assert "install-host-uptime-services.sh" in restart_text
    assert "sha256sum" in restart_text
    assert "mktemp -d /tmp/tinyassets-host-uptime." in restart_text
    assert (
        restart_text.count(
            "guard-host-mutation --command-timeout 300 -- /bin/bash -se --"
        )
        == 1
    )
    assert restart_text.count("<<'REMOTE'") == 1
    assert 'remote_stage="$1"' in restart_text
    assert "/tmp/daemon-watchdog" not in restart_text
    assert "systemctl enable --now daemon-watchdog.timer" not in restart_text


def test_clean_host_bootstrap_provisions_agent_interchange_env():
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    service = (REPO / "deploy" / "tinyassets-daemon.service").read_text(
        encoding="utf-8"
    )
    docs = (REPO / "deploy" / "DEPLOY.md").read_text(encoding="utf-8")

    assert "deploy/agent-interchange-env.template" in bootstrap
    assert '"${ENV_DIR}/agent-interchange.env"' in bootstrap
    existing_branch = bootstrap.index(
        '${ENV_DIR}/agent-interchange.env already present; leaving contents alone'
    )
    conditional_end = bootstrap.index("\nfi", existing_branch)
    chown = bootstrap.index(
        'chown "root:${TINYASSETS_USER}" "${ENV_DIR}/agent-interchange.env"'
    )
    chmod = bootstrap.index('chmod 640 "${ENV_DIR}/agent-interchange.env"')
    assert conditional_end < chown < chmod
    assert (
        'chown "root:${TINYASSETS_USER}" "${ENV_DIR}/agent-interchange.env"'
        in bootstrap
    )
    assert 'chmod 640 "${ENV_DIR}/agent-interchange.env"' in bootstrap
    assert "test -r /etc/tinyassets/agent-interchange.env" in service
    assert "AGENT-INTERCHANGE-ENV-UNREADABLE" in service
    assert "root:tinyassets 640" in service
    assert "openssl rand -base64 48" in docs
    assert "/etc/tinyassets/agent-interchange.env" in docs
    assert "Generate the daemon-only agent interchange key" in bootstrap


def test_clean_host_bootstrap_provisions_request_idempotency_env():
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    service = (REPO / "deploy" / "tinyassets-daemon.service").read_text(
        encoding="utf-8"
    )
    docs = (REPO / "deploy" / "DEPLOY.md").read_text(encoding="utf-8")

    assert "deploy/request-idempotency-env.template" in bootstrap
    assert '"${ENV_DIR}/request-idempotency.env"' in bootstrap
    assert (
        'chown "root:${TINYASSETS_USER}" "${ENV_DIR}/request-idempotency.env"'
        in bootstrap
    )
    assert 'chmod 640 "${ENV_DIR}/request-idempotency.env"' in bootstrap
    assert "test -r /etc/tinyassets/request-idempotency.env" in service
    assert "REQUEST-IDEMPOTENCY-ENV-UNREADABLE" in service
    assert "/etc/tinyassets/request-idempotency.env" in docs


def test_restart_workflow_serializes_production_host_mutations():
    workflow = yaml.safe_load(RESTART_WORKFLOW.read_text(encoding="utf-8"))

    assert workflow["concurrency"] == {
        "group": "production-host-mutation",
        "cancel-in-progress": False,
    }


def test_host_service_installer_serializes_production_host_mutations():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    assert workflow["concurrency"] == {
        "group": "production-host-mutation",
        "cancel-in-progress": False,
    }


@pytest.mark.parametrize(
    ("path", "job_name", "first_mutation"),
    (
        (RESTART_WORKFLOW, "restart", "Converge host uptime services"),
        (WORKFLOW, "install", "Ensure off-host backup configuration"),
    ),
)
def test_host_mutators_refuse_nonterminal_stop_writer_fence(
    path: Path,
    job_name: str,
    first_mutation: str,
):
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = workflow["jobs"][job_name]["steps"]
    names = [step.get("name") for step in steps]
    guard_name = "Refuse host mutation during stop-writer cutover"
    assert names.index(guard_name) < names.index(first_mutation)
    guard = steps[names.index(guard_name)]["run"]
    assert "scp -i ~/.ssh/do_deploy" in guard
    assert "scripts/retire_cheat_loop_deploy_fence.py" in guard
    assert "guard-host-mutation" in guard
    assert "retire-cheat-loop-task-2-1-fence.json" in guard
    assert "/run/tinyassets-host-mutation-guard" not in guard


def test_host_mutator_fence_guards_share_exact_residue_contract():
    guards = []
    for path, job_name in (
        (RESTART_WORKFLOW, "restart"),
        (WORKFLOW, "install"),
        (P0_WORKFLOW, "triage"),
    ):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        step = next(
            item
            for item in workflow["jobs"][job_name]["steps"]
            if item.get("name") == "Refuse host mutation during stop-writer cutover"
        )
        guards.append(step["run"])
    assert guards[0] == guards[1] == guards[2]


def test_writer_affecting_remote_mutations_run_inside_authoritative_host_lock():
    expected_steps = {
        RESTART_WORKFLOW: {
            "Converge host uptime services": 1,
            "Restart workflow daemon": 1,
        },
        WORKFLOW: {
            "Ensure off-host backup configuration": 2,
            "Install exact uptime bundle": 1,
        },
    }
    for path, names in expected_steps.items():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        job_name = "restart" if path == RESTART_WORKFLOW else "install"
        steps = workflow["jobs"][job_name]["steps"]
        for name, expected_count in names.items():
            run = next(step["run"] for step in steps if step.get("name") == name)
            assert run.count("guard-host-mutation") == expected_count, (path, name)
            assert "--command-timeout" in run, (path, name)


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
def test_host_mutation_workflow_shell_blocks_parse():
    selected = {
        RESTART_WORKFLOW: (
            "Refuse host mutation during stop-writer cutover",
            "Converge host uptime services",
            "Restart workflow daemon",
        ),
        WORKFLOW: (
            "Refuse host mutation during stop-writer cutover",
            "Ensure off-host backup configuration",
            "Install exact uptime bundle",
        ),
        P0_WORKFLOW: (
            "Refuse host mutation during stop-writer cutover",
            "Repair — ENV-UNREADABLE (chown + chmod)",
            "Repair — OOM (compose restart; memory cap NOT auto-bumped)",
            "Repair — disk full (docker prune + journalctl vacuum)",
            "Repair — image pull failure (release-state rollback target)",
            "Repair — watchdog hot-loop (stop + sleep 60 + start)",
            "Repair — provider_exhaustion (.pause every universe)",
            "Attempt compose restart",
        ),
    }
    for path, names in selected.items():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        job_name = next(iter(workflow["jobs"]))
        steps = workflow["jobs"][job_name]["steps"]
        for name in names:
            run = next(step["run"] for step in steps if step.get("name") == name)
            parsed = subprocess.run(
                [_BASH, "-n"],
                input=run.encode("utf-8"),
                capture_output=True,
                check=False,
            )
            assert parsed.returncode == 0, (
                path,
                name,
                parsed.stderr.decode("utf-8", errors="replace"),
            )


def test_guard_release_cannot_race_a_new_deploy_preflight():
    for path in (
        DEPLOY_WORKFLOW,
        RESTART_WORKFLOW,
        WORKFLOW,
        P0_WORKFLOW,
    ):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert workflow["concurrency"] == {
            "group": "production-host-mutation",
            "cancel-in-progress": False,
        }


def test_host_service_workflow_converges_backup_before_installing_timers():
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    steps = workflow["jobs"]["install"]["steps"]
    names = [step.get("name") for step in steps]
    backup_index = names.index("Ensure off-host backup configuration")
    install_index = names.index("Install exact uptime bundle")
    assert backup_index < install_index

    backup_step = steps[backup_index]
    run = backup_step["run"]
    assert backup_step["env"]["DO_API_TOKEN"] == "${{ secrets.DO_API_TOKEN }}"
    assert "/v2/spaces/keys" in run
    assert "--max-filesize 4096" in run
    assert '"permission":"readwrite"' in run
    assert 'bucket="workflow-backups-jonnyton-sfo3"' in run
    assert "tinyassets-backups-jonnyton-sfo3" not in run
    assert '"bucket":"%s"' in run
    assert "fullaccess" not in run
    assert "::add-mask::" in run
    assert "secret_key" in run
    assert 'Authorization: Bearer ${DO_API_TOKEN}' not in run
    assert '-H @"${api_header_file}"' in run
    assert "/root/.config/rclone/rclone.conf" in run
    assert 'install-tinyassets-env.sh" set BACKUP_DEST' in run
    assert 'rclone mkdir "${destination}"' not in run
    assert "for delay in 0 5 10 20 30" in run
    assert 'sleep "${delay}"' in run
    assert (
        'timeout --kill-after=1s 5s sudo rclone lsf "${destination}" --max-depth 1 '
        "--retries 1 --low-level-retries 1"
    ) in run
    assert "Spaces key did not become usable within the propagation window" in run
    assert "configured_ready" in run
    assert "completely_absent" in run
    assert "partial_or_invalid" in run
    assert "stat -c '%U:%G %a'" in run
    assert "root:root 600" in run
    assert '! -L "${config}"' in run
    assert '"${assignment_count}" == "1"' in run
    assert 'delete_http_status="$(' in run
    assert "-w '%{http_code}' -X DELETE" in run
    assert '"${delete_http_status}" != "204"' in run
    assert "Spaces key rollback failed with HTTP" in run
    assert 'curl -sS -o /dev/null -X DELETE' not in run
    assert 'cat "${response_file}"' not in run
    assert "provider_error=" in run
    assert "json.load(handle)" in run
    assert "category = \"authorization_or_scope\"" in run
    assert "category = \"bucket_or_grant\"" in run
    assert "message=" not in run
    assert "GITHUB_OUTPUT" not in run


def test_backup_remote_install_retries_same_key_until_probe_propagates(tmp_path):
    result = _run_backup_remote_install(tmp_path, succeed_on_probe=3)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert (tmp_path / "probe-count").read_text(encoding="utf-8") == "3"
    assert (tmp_path / "probe-delays").read_text(encoding="utf-8").splitlines() == [
        "5",
        "10",
    ]


def test_backup_remote_install_rolls_back_after_bounded_probe_window(tmp_path):
    result = _run_backup_remote_install(tmp_path, succeed_on_probe=99)
    assert result.returncode == 1
    assert (tmp_path / "probe-count").read_text(encoding="utf-8") == "5"
    assert (tmp_path / "probe-delays").read_text(encoding="utf-8").splitlines() == [
        "5",
        "10",
        "20",
        "30",
    ]
    assert "did not become usable within the propagation window" in result.stderr


@pytest.mark.parametrize(
    ("env_text", "config_kind", "config_mode", "rclone_rc", "expected"),
    (
        (
            "BACKUP_DEST=spaces:workflow-backups-jonnyton-sfo3/"
            "workflow-backups\n",
            "regular",
            "600",
            0,
            "configured_ready",
        ),
        (
            "BACKUP_DEST=spaces:workflow-backups-jonnyton-sfo3/"
            "workflow-backups\n",
            "regular",
            "644",
            0,
            "partial_or_invalid",
        ),
        (
            "BACKUP_DEST=spaces:workflow-backups-jonnyton-sfo3/"
            "workflow-backups\n",
            "symlink",
            "600",
            0,
            "partial_or_invalid",
        ),
        (None, "absent", "600", 0, "completely_absent"),
        (
            "BACKUP_DEST=spaces:workflow-backups-jonnyton-sfo3/"
            "workflow-backups\n",
            "absent",
            "600",
            0,
            "partial_or_invalid",
        ),
        (
            "BACKUP_DEST=spaces:workflow-backups-jonnyton-sfo3/"
            "workflow-backups\n",
            "regular",
            "600",
            1,
            "partial_or_invalid",
        ),
        (
            "BACKUP_DEST=spaces:workflow-backups-jonnyton-sfo3/"
            "workflow-backups\n"
            "BACKUP_DEST=spaces:workflow-backups-jonnyton-sfo3/"
            "workflow-backups\n",
            "regular",
            "600",
            0,
            "partial_or_invalid",
        ),
    ),
)
def test_backup_state_classifier_is_fail_closed(
    tmp_path,
    env_text,
    config_kind,
    config_mode,
    rclone_rc,
    expected,
):
    result = _run_backup_state(
        tmp_path,
        env_text=env_text,
        config_kind=config_kind,
        config_mode=config_mode,
        rclone_rc=rclone_rc,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    ("delete_status", "transport_rc", "expected_message"),
    (
        ("204", 0, "Rolled back the newly created Spaces key."),
        ("403", 0, "Spaces key rollback failed with HTTP 403."),
        ("000", 7, "Spaces key rollback failed with a transport error."),
    ),
)
def test_backup_key_rollback_requires_explicit_204(
    tmp_path,
    delete_status,
    transport_rc,
    expected_message,
):
    result = _run_backup_cleanup(
        tmp_path,
        delete_status=delete_status,
        transport_rc=transport_rc,
    )
    assert result.returncode == 1
    combined = f"{result.stdout}\n{result.stderr}"
    assert expected_message in combined
    if delete_status != "204" or transport_rc != 0:
        assert "::error::" in combined


@pytest.mark.parametrize(
    ("response", "expected", "forbidden"),
    (
        (
            '{"id":"forbidden","message":"token lacks spaces_key:create_credentials"}',
            "id=forbidden category=authorization_or_scope",
            None,
        ),
        (
            '{"id":"forbidden","message":"dop_v1_0123456789ABCDEFGHIJ rejected"}',
            "id=forbidden category=other",
            "dop_v1_0123456789ABCDEFGHIJ",
        ),
        (
            '{"id":"forbidden","message":"secret_key: '
            'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef0123456789"}',
            "id=forbidden category=other",
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef0123456789",
        ),
        (
            '{"id":"forbidden","message":"Authorization: Bearer '
            '0123456789abcdef0123456789abcdef"}',
            "id=forbidden category=authorization_or_scope",
            "0123456789abcdef0123456789abcdef",
        ),
        (
            '{"id":"bad id ABCDEFGHIJKLMNOPQRSTUVWXYZ","message":"bucket grant rejected"}',
            "id=redacted category=bucket_or_grant",
            "bad id ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        ),
        (
            '{"id":"dop_v1_0123456789abcdefghijklmnopqrstuv","message":"forbidden"}',
            "id=redacted category=authorization_or_scope",
            "dop_v1_0123456789abcdefghijklmnopqrstuv",
        ),
        (
            "not-json",
            "unparseable provider error",
            "not-json",
        ),
    ),
)
def test_backup_provider_error_is_bounded_and_redacted(
    tmp_path,
    response,
    expected,
    forbidden,
):
    result = _run_backup_diagnostic(tmp_path, response)
    assert result.returncode == 0
    assert expected in result.stdout
    if forbidden is not None:
        assert forbidden not in result.stdout
    assert len(result.stdout.strip()) <= 80


def test_host_service_workflow_requires_backup_provisioning_authority():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    verify_step = next(
        step
        for step in workflow["jobs"]["install"]["steps"]
        if step.get("name") == "Verify secrets present"
    )
    run = verify_step["run"]
    assert verify_step["env"]["DO_API_TOKEN"] == "${{ secrets.DO_API_TOKEN }}"
    assert '[ -z "$DO_API_TOKEN" ]' in run
    assert 'missing+=("DO_API_TOKEN")' in run


def test_host_service_workflow_exercises_backup_only_on_explicit_dispatch():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True) or {}
    dispatch = triggers.get("workflow_dispatch") or {}
    run_backup = (dispatch.get("inputs") or {}).get("run_backup") or {}
    assert run_backup.get("type") == "boolean"
    assert run_backup.get("required") is False
    assert run_backup.get("default") is False

    job = workflow["jobs"]["install"]
    assert job["timeout-minutes"] == 40
    step = next(
        item
        for item in job["steps"]
        if item.get("name") == "Exercise and verify two-tier backup"
    )
    assert step["if"] == (
        "github.event_name == 'workflow_dispatch' && inputs.run_backup"
    )
    run = step["run"]
    assert "sudo systemctl start tinyassets-backup.service" in run
    assert "systemctl show tinyassets-backup.service --property=Result" in run
    assert "systemctl show tinyassets-backup.service --property=InvocationID" in run
    assert '_SYSTEMD_INVOCATION_ID=${invocation_id}' in run
    assert "--since" not in run
    assert "tinyassets-brain-" in run
    assert "tinyassets-data-" in run
    assert '"${after_brain}" != "${before_brain}"' in run
    assert '"${after_full}" != "${before_full}"' in run
    assert "gh-ship: [backup-ship] uploaded:" in run
    assert '"${gh_upload_count}" -eq 2' in run
    assert "backup complete." in run
    assert "grep -Eq 'WARN:|ERROR:'" in run
    assert "Backup invocation emitted a warning or error." in run


def test_backup_exercise_scopes_evidence_to_new_systemd_invocation(tmp_path):
    result = _run_backup_exercise(tmp_path)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    query = (tmp_path / "journal-query").read_text(encoding="utf-8").strip()
    assert query == (
        "journalctl "
        "_SYSTEMD_INVOCATION_ID=0123456789abcdef0123456789abcdef "
        "--no-pager --output=cat"
    )
    assert "Verified fresh backup:" in result.stdout
    assert "github_assets=2" in result.stdout


def test_backup_exercise_rejects_invocation_warning(tmp_path):
    result = _run_backup_exercise(tmp_path, emit_warning=True)
    assert result.returncode != 0
    assert "Verified fresh backup:" not in result.stdout

def test_bash_path_is_absolute_and_normalised_without_resolving(tmp_path):
    """The half of the contract that can be checked on every platform.

    Kept SEPARATE from the symlink case on purpose: when both lived in one
    test, the symlink skip swallowed this assertion too and the whole thing
    reported `s`, teaching a reader nothing about whether the contract holds.
    """
    target = tmp_path / "releases" / "r1"
    target.mkdir(parents=True)
    messy = tmp_path / "releases" / ".." / "releases" / "r1"
    assert _bash_path(messy) == os.path.abspath(messy)


def test_bash_path_does_not_follow_symlinks(tmp_path):
    """`_bash_path` must not canonicalise the final component's symlink.

    This is the defect that quarantined most of this file: `_bash_path` used
    `Path.resolve()`, so `test -L .../runtime/current` was really asking
    `test -L .../runtime/releases/<id>` — a directory — and `readlink` on the
    same path returned nothing. Both became impossible to satisfy precisely
    WHEN the installer was behaving correctly.

    Skips where symlink creation needs a privilege the platform withholds. A
    skip here is not evidence the contract holds; the sibling test above
    carries the part that always runs.
    """
    target = tmp_path / "releases" / "r1"
    target.mkdir(parents=True)
    link = tmp_path / "current"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform
        pytest.skip(f"symlink creation not permitted here: {exc}")

    # resolve() would have returned `target` here — that is the whole bug.
    assert _bash_path(link) == os.path.abspath(link)
    assert _bash_path(link) != os.path.abspath(target)
    assert Path(_bash_path(link)).is_symlink()


# --- deploy/daemon-watchdog.sh command repertoire -------------------------
#
# Preservation, not a new guarantee. The watchdog script is UNCHANGED by this
# branch; these cases exist because matrix row 6 ("automatic recovery never
# re-homes work to an unadmitted runtime") cited the watchdog in prose with no
# executable evidence behind it. What is actually proven here is narrow and
# worth stating plainly: on each of the three triggers the script can fire on,
# the ONLY things it invokes are a restart of the same daemon container and a
# reset-failed/restart of the same configured cloud unit. No second host, no
# relay binary, no reassignment.
#
# Deliberately NOT proven: locking (flock is mocked, so concurrency behaviour
# is untested here), real docker/systemd semantics, and anything about what the
# daemon does after it restarts. Admission refusal after a restart is asserted
# nowhere in this file — it belongs to the admission modules.

WATCHDOG = REPO / "deploy" / "daemon-watchdog.sh"

# Command families used for reads in the expected transcript. The exact
# transcript assertion, not this family filter, excludes mutations such as
# `compose down` or `volume rm`.
_WATCHDOG_READ_ONLY_DOCKER_VERBS = frozenset({"inspect", "volume", "compose"})

# Record common relay commands in addition to the exact docker/systemctl
# transcripts. This list is not a sandbox or exhaustive network prohibition.
_WATCHDOG_RELAY_TOOLS = (
    "ssh",
    "scp",
    "curl",
    "wget",
    "rsync",
    "nc",
    "kubectl",
    "doctl",
    "ansible",
)


def _watchdog_arg(path: Path) -> str:
    """Bash-visible path for the watchdog fixtures.

    `_bash_path` is correct under WSL, but on Git Bash it hands back a
    backslashed Windows path and bash eats the backslashes as escapes. MSYS
    bash accepts a forward-slashed drive path verbatim, so that is what this
    returns off WSL. PATH entries cannot use this form at all -- a drive letter
    colon would split the variable -- so `_run_watchdog` has the shell derive
    those itself with `cd`/`pwd`.
    """
    if _is_wsl_bash():
        return _bash_path(path)
    return Path(os.path.abspath(path)).as_posix()


def _watchdog_fake_bin(tmp_path: Path) -> Path:
    """External commands the watchdog reaches for, replaced by recorders.

    Named `wd-bin` rather than `bin` on purpose: `_fake_tools` already owns
    `tmp_path / "bin"`, and two fixtures writing one scratch name produce a
    command log that belongs to neither test.
    """
    fake_bin = tmp_path / "wd-bin"
    fake_bin.mkdir()

    (fake_bin / "systemctl").write_text(
        """#!/usr/bin/env bash
echo "$*" >> "$WATCHDOG_SYSTEMCTL_LOG"
case "$1" in
  is-active)
    [[ "${WATCHDOG_UNIT_ACTIVE:-1}" == "1" ]]
    ;;
  reset-failed|restart)
    exit 0
    ;;
  *)
    exit 97
    ;;
esac
""",
        encoding="utf-8",
        newline="\n",
    )
    (fake_bin / "systemctl").chmod(0o755)

    (fake_bin / "docker").write_text(
        """#!/usr/bin/env bash
echo "$*" >> "$WATCHDOG_DOCKER_LOG"
case "$1" in
  inspect)
    if [[ "$2" == "-f" ]]; then
      case "$3" in
        *State.StartedAt*)
          # Default is far in the past: an OLD container, so the start-up grace
          # does not apply unless a test asks for it.
          echo "${WATCHDOG_CONTAINER_STARTED_AT:-2020-01-01T00:00:00.000000000Z}"
          ;;
        *)
          echo "${WATCHDOG_CONTAINER_RUNNING:-true}"
          ;;
      esac
    fi
    exit 0
    ;;
  volume)
    [[ -n "${WATCHDOG_VOLUME_MOUNT:-}" ]] || exit 1
    echo "$WATCHDOG_VOLUME_MOUNT"
    ;;
  compose|restart)
    exit 0
    ;;
  *)
    exit 98
    ;;
esac
""",
        encoding="utf-8",
        newline="\n",
    )
    (fake_bin / "docker").chmod(0o755)

    # Git Bash ships no flock at all. Mocking it makes the script reach its
    # checks; it proves nothing about the lock itself.
    # WATCHDOG_FLOCK_BUSY_FD names one descriptor whose lock is "held
    # elsewhere", so a test can stand in for a deploy holding its lock.
    (fake_bin / "flock").write_text(
        """#!/usr/bin/env bash
echo "$*" >> "$WATCHDOG_FLOCK_LOG"
if [[ -n "${WATCHDOG_FLOCK_BUSY_FD:-}" && "$2" == "$WATCHDOG_FLOCK_BUSY_FD" ]]; then
  exit 1
fi
exit 0
""",
        encoding="utf-8",
        newline="\n",
    )
    (fake_bin / "flock").chmod(0o755)

    for tool in _WATCHDOG_RELAY_TOOLS:
        (fake_bin / tool).write_text(
            f"""#!/usr/bin/env bash
echo "{tool} $*" >> "$WATCHDOG_RELAY_LOG"
exit 0
""",
            encoding="utf-8",
            newline="\n",
        )
        (fake_bin / tool).chmod(0o755)

    return fake_bin


def _run_watchdog(
    tmp_path: Path,
    trigger: str,
    *,
    started_at: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str], Path, str]:
    """Run the real watchdog against mocked externals; return its command logs.

    State paths are under pytest's temp root. The unchanged script's service
    commands resolve to recorders; find/stat/date and other shell utilities
    remain real. This is not a sandbox for arbitrary future shell commands.
    """
    if not _BASH:
        pytest.skip("bash unavailable")

    fake_bin = _watchdog_fake_bin(tmp_path)
    volume = tmp_path / "wd-volume"
    (volume / "founder").mkdir(parents=True)
    heartbeat = volume / "founder" / ".worker_supervisor.json"
    heartbeat.write_text("{}", encoding="utf-8")
    # Only the third trigger ever reads this; the first two exit earlier.
    stamp = time.time() - (7200 if trigger == "stale-heartbeat" else 0)
    os.utime(heartbeat, (stamp, stamp))

    compose = tmp_path / "wd-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    compose_arg = _watchdog_arg(compose)

    systemctl_log = tmp_path / "wd-systemctl.log"
    docker_log = tmp_path / "wd-docker.log"
    relay_log = tmp_path / "wd-relay.log"

    assignments = {
        "WATCHDOG_SYSTEMCTL_LOG": _watchdog_arg(systemctl_log),
        "WATCHDOG_DOCKER_LOG": _watchdog_arg(docker_log),
        "WATCHDOG_RELAY_LOG": _watchdog_arg(relay_log),
        "WATCHDOG_FLOCK_LOG": _watchdog_arg(tmp_path / "wd-flock.log"),
        "WATCHDOG_UNIT_ACTIVE": "0" if trigger == "inactive-unit" else "1",
        "WATCHDOG_CONTAINER_RUNNING": (
            "false" if trigger == "stopped-container" else "true"
        ),
        # Empty falls through to the stub's own far-past default (an old
        # container), so the start-up grace stays off unless a test asks.
        "WATCHDOG_CONTAINER_STARTED_AT": started_at or "",
        "WATCHDOG_VOLUME_MOUNT": _watchdog_arg(volume),
        "TINYASSETS_COMPOSE_FILE": compose_arg,
        "TINYASSETS_DAEMON_WATCHDOG_LOCK": _watchdog_arg(tmp_path / "wd.lock"),
        # Absent unless a test creates it: no deploy has run since boot.
        "TINYASSETS_HOST_MUTATION_LOCK": _watchdog_arg(tmp_path / "host-mutation.lock"),
        "TINYASSETS_HEARTBEAT_MAX_AGE_SECONDS": "60",
        # Emptied rather than omitted: `${VAR:-default}` falls through on an
        # empty value, so this pins the script's own production defaults even
        # if the invoking shell already exports them.
        "TINYASSETS_DAEMON_UNIT": "",
        "TINYASSETS_DATA_VOLUME": "",
        "TINYASSETS_HEARTBEAT_RELATIVE": "",
    }
    # Last, so a test can override a pinned default (a deliberately bad
    # threshold, say) rather than only add to the set.
    assignments.update(extra_env or {})
    exported = " ".join(
        f"{key}={shlex.quote(value)}" for key, value in assignments.items()
    )
    # The shell derives the POSIX forms: a Windows drive path cannot go into
    # PATH, and `bash script.sh` wants a path its own runtime understands.
    command = (
        f'wd_bin="$(cd {shlex.quote(_watchdog_arg(fake_bin))} && pwd)"; '
        f'wd_dir="$(cd {shlex.quote(_watchdog_arg(WATCHDOG.parent))} && pwd)"; '
        f'exec /usr/bin/env PATH="$wd_bin:/usr/local/bin:/usr/bin:/bin" '
        f'{exported} bash "$wd_dir/{WATCHDOG.name}"'
    )
    result = subprocess.run(
        [_BASH, "-lc", command],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    def _lines(path: Path) -> list[str]:
        if not path.exists():
            return []
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    return result, _lines(systemctl_log), _lines(docker_log), relay_log, compose_arg


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
@pytest.mark.parametrize(
    "trigger,reason,docker_prefix",
    [
        ("inactive-unit", "systemd unit is not active", []),
        (
            "stopped-container",
            "tinyassets-daemon container is not running",
            ["inspect -f {{.State.Running}} tinyassets-daemon"],
        ),
        (
            "stale-heartbeat",
            "heartbeat stale",
            [
                "inspect -f {{.State.Running}} tinyassets-daemon",
                "compose -f {compose} ps",
                "volume inspect tinyassets-data --format {{ .Mountpoint }}",
                # A stale heartbeat asks how old the container is before acting
                # (within_heartbeat_grace). This default container is old, so the
                # answer does not spare it.
                "inspect -f {{.State.StartedAt}} tinyassets-daemon",
            ],
        ),
    ],
)
def test_daemon_watchdog_restart_repertoire_is_same_service_only(
    tmp_path, trigger, reason, docker_prefix
):
    """Each watchdog trigger restarts this droplet's daemon and nothing else.

    The three cases are the three ways `main` can decide to act: the systemd
    unit reporting inactive, the daemon container not running, and the
    freshest worker-supervisor heartbeat aging past the configured maximum.
    All three converge on one repertoire, and pinning it is the point -- a
    future edit that added a second host, a relay hop or a work reassignment
    would have to change these lists to stay green.
    """
    result, systemctl_lines, docker_lines, relay_log, compose_arg = _run_watchdog(
        tmp_path, trigger
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert f"restarting daemon container: {reason}" in result.stdout
    assert "healthy: unit active" not in result.stdout

    # The systemd half is identical on every trigger: probe the configured
    # unit, then reset-failed and restart that same unit. Nothing else.
    assert systemctl_lines == [
        "is-active --quiet tinyassets-daemon.service",
        "reset-failed tinyassets-daemon.service",
        "restart tinyassets-daemon.service",
    ]

    # A literal replace, not `.format`: these lines carry Go template braces
    # (`{{.State.Running}}`) and format() would collapse them to one level.
    expected_docker = [
        line.replace("{compose}", compose_arg) for line in docker_prefix
    ] + [
        "inspect tinyassets-daemon",
        "restart -t 20 tinyassets-daemon",
    ]
    assert docker_lines == expected_docker

    # Stated as a repertoire and not just a transcript: every docker call is
    # either a read or the one permitted mutation.
    mutations = [
        line
        for line in docker_lines
        if line.split()[0] not in _WATCHDOG_READ_ONLY_DOCKER_VERBS
    ]
    assert mutations == ["restart -t 20 tinyassets-daemon"]

    # No second target anywhere. Path-shaped tokens are excluded because the
    # temp root is not ours to predict.
    named = {
        token
        for line in (*systemctl_lines, *docker_lines)
        for token in line.split()
        if token.startswith("tinyassets") and "/" not in token
    }
    allowed = {"tinyassets-daemon", "tinyassets-daemon.service"}
    if trigger == "stale-heartbeat":
        allowed.add("tinyassets-data")
    assert named == allowed

    # No invocation of the specifically shimmed relay tools.
    assert not relay_log.exists(), relay_log.read_text(encoding="utf-8")


def _minutes_ago(minutes: float) -> str:
    """Docker's RFC3339 `State.StartedAt` form, that many minutes in the past."""
    started = time.gmtime(time.time() - minutes * 60)
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", started)


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
def test_a_freshly_started_container_is_not_restarted_for_a_stale_heartbeat(tmp_path):
    """The second daemon restart per merge.

    Every deploy recreates the container, and the heartbeat lives on the DATA
    VOLUME -- so the freshest file on disk is the one the PREVIOUS container
    left behind. Seconds after a recreate that file is already stale, and the
    watchdog read it as proof the new container was dead. Measured 2026-09-25:
    deploy at ~23:02, watchdog restart at ~23:04, each killing an in-flight
    user turn.

    A container cannot refresh a heartbeat it has not had time to write, so
    while it is younger than the threshold it is judged against, the heartbeat
    says nothing about it.
    """
    result, systemctl_lines, docker_lines, _relay, _compose = _run_watchdog(
        tmp_path, "stale-heartbeat", started_at=_minutes_ago(0.2)
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "restarting daemon container" not in result.stdout
    assert "too young to have refreshed the heartbeat" in result.stdout
    # Nothing was restarted, by either lever.
    assert systemctl_lines == ["is-active --quiet tinyassets-daemon.service"]
    assert [
        line
        for line in docker_lines
        if line.split()[0] not in _WATCHDOG_READ_ONLY_DOCKER_VERBS
    ] == []


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
def test_an_old_container_with_a_stale_heartbeat_is_still_restarted(tmp_path):
    """The grace must not disarm auto-recovery. A genuine hang shows up as a
    stale heartbeat in a container that has been up long enough to have written
    one, and that still restarts. The harness default container is old, so this
    states the age explicitly rather than leaning on it."""
    result, systemctl_lines, docker_lines, _relay, _compose = _run_watchdog(
        tmp_path, "stale-heartbeat", started_at=_minutes_ago(600)
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "restarting daemon container: heartbeat stale" in result.stdout
    assert "too young" not in result.stdout
    assert systemctl_lines == [
        "is-active --quiet tinyassets-daemon.service",
        "reset-failed tinyassets-daemon.service",
        "restart tinyassets-daemon.service",
    ]
    assert "restart -t 20 tinyassets-daemon" in docker_lines


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
def test_an_unreadable_container_start_time_does_not_grant_grace(tmp_path):
    """Fail toward recovering. If `State.StartedAt` cannot be read or parsed the
    watchdog cannot prove the container is young, and a watchdog that cannot
    tell must still restart -- otherwise an unparseable answer would silence
    auto-recovery for every hang."""
    result, systemctl_lines, _docker, _relay, _compose = _run_watchdog(
        tmp_path, "stale-heartbeat", started_at="not-a-timestamp"
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "restarting daemon container: heartbeat stale" in result.stdout
    assert "too young" not in result.stdout
    assert "restart tinyassets-daemon.service" in systemctl_lines


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
def test_a_freshly_started_container_still_restarts_when_it_is_not_running(tmp_path):
    """The grace covers the heartbeat signal only. A container that is young AND
    not running is still broken, and the not-running check runs before the
    heartbeat is ever read."""
    result, systemctl_lines, _docker, _relay, _compose = _run_watchdog(
        tmp_path, "stopped-container", started_at=_minutes_ago(0.2)
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert (
        "restarting daemon container: tinyassets-daemon container is not running"
        in result.stdout
    )
    assert "too young" not in result.stdout
    assert "restart tinyassets-daemon.service" in systemctl_lines


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
def test_a_container_reporting_a_future_start_time_gets_no_grace(tmp_path):
    """A negative age is unknowable, not young.

    A container reporting a start time in the future means the host clock
    stepped back -- an NTP correction, or a VM restored from a snapshot. Every
    negative number is below the grace window, so the young-container branch
    would have been taken unconditionally and recovery suppressed for as long as
    the skew lasted. Same treatment as an unreadable timestamp: no grace.
    """
    result, systemctl_lines, _docker, _relay, _compose = _run_watchdog(
        tmp_path, "stale-heartbeat", started_at=_minutes_ago(-5)
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "restarting daemon container: heartbeat stale" in result.stdout
    assert "too young" not in result.stdout
    assert "restart tinyassets-daemon.service" in systemctl_lines


# Distinct failure modes, not spellings of one. Each verified against
# `set -euo pipefail` before being added here:
#   "abc"   -> arithmetic treats it as a variable NAME; `set -u` makes it an
#              unbound variable and the script EXITS 1. A recovery tool that
#              refuses to run is the one outcome worse than a wrong threshold.
#   "1+"    -> arithmetic syntax error; `(( ))` returns non-zero, so inside an
#              `if` it silently evaluates FALSE -- no heartbeat is ever stale.
#   "-3"    -> parses fine and is accepted, so EVERY heartbeat is stale.
#   "08"    -> a leading zero means OCTAL and 8 is not an octal digit: "value
#   "0900"     too great for base". As the max age that reads FALSE, so a hung
#              container logs "healthy" and is never restarted; as the margin it
#              aborts. All digits, and still either silence or death.
#   "010"   -> valid octal, so silently EIGHT rather than ten. The insidious one:
#              no error at all, just the wrong window forever.
#   20 digits -> overflows signed 64-bit and WRAPS; 99999999999999999999 came out
#              as 7766279631452241979.
# The last four are why the pattern is the decimal SHAPE and not `^[0-9]+$`
# (#3995 round 1).
_BAD_THRESHOLDS = (
    "abc",
    "1+",
    "-3",
    "  ",
    "08",
    "0900",
    "010",
    "99999999999999999999",
)


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
@pytest.mark.parametrize(
    "var",
    [
        "TINYASSETS_HEARTBEAT_MAX_AGE_SECONDS",
        "TINYASSETS_HEARTBEAT_GRACE_MARGIN_SECONDS",
    ],
)
@pytest.mark.parametrize("bad", _BAD_THRESHOLDS)
def test_a_bad_threshold_falls_back_instead_of_silencing_recovery(tmp_path, var, bad):
    """An operator typo in a host env file must not disarm the watchdog.

    Both thresholds feed `(( ... ))`, where a bad value does one of several
    unrelated wrong things (see above) -- some kill the script outright, some
    silence recovery without a word, and being all-digits is no defence. Whatever
    the operator wrote, the run has to reach a decision, so anything that is not
    a plain decimal integer is replaced by its default and said out loud.
    """
    result, systemctl_lines, _docker, _relay, _compose = _run_watchdog(
        tmp_path, "stale-heartbeat", extra_env={var: bad}
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert f"ignoring {var}='{bad}'" in result.stdout, result.stdout
    # And it still recovered: the default container is old and its heartbeat is
    # two hours stale under either threshold's default.
    assert "restarting daemon container: heartbeat stale" in result.stdout
    assert "restart tinyassets-daemon.service" in systemctl_lines


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
def test_the_threshold_pattern_takes_every_plain_decimal_and_nothing_else():
    """The boundary, which the behavioural tests above cannot reach.

    Tightening `^[0-9]+$` to a decimal shape creates the opposite risk: a pattern
    too strict would discard an operator's legitimate value and silently pin the
    default. The behavioural tests only ever exercise one good value, so the
    accepted set is pinned here -- read out of the script and evaluated by bash,
    so narrowing that line turns this red.
    """
    source = WATCHDOG.read_text(encoding="utf-8")
    match = re.search(r'\[\[ "\$\{value\}" =~ (\S+) \]\]', source)
    assert match, "the threshold validator moved or changed shape; update this test"
    pattern = match.group(1)

    accept = ["0", "1", "9", "30", "60", "120", "900", "86400", "999999999"]
    reject = [
        "",
        " ",
        "abc",
        "1+",
        "-3",
        "+3",
        "08",
        "0900",
        "010",
        "00",
        "1.5",
        "1e3",
        "0x10",
        "1000000000",  # ten digits: past the cap, so past overflow risk
        "99999999999999999999",
        "12 ",
        " 12",
    ]

    def matches(value: str) -> bool:
        # Environment, not argv: Git Bash strips braces out of arguments, which
        # would turn `{0,8}` into a literal and make every answer here wrong.
        result = subprocess.run(
            [_BASH, "-c", '[[ "$TA_VALUE" =~ $TA_PATTERN ]]'],
            env={**os.environ, "TA_PATTERN": pattern, "TA_VALUE": value},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode in (0, 1), f"{result.stdout}\n{result.stderr}"
        return result.returncode == 0

    assert [value for value in accept if not matches(value)] == []
    assert [value for value in reject if matches(value)] == []


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
def test_a_valid_threshold_is_left_alone(tmp_path):
    """The fallback must not fire on good input -- otherwise an operator's real
    setting would be silently discarded and the grace window would always be
    the default."""
    result, _systemctl, _docker, _relay, _compose = _run_watchdog(
        tmp_path,
        "stale-heartbeat",
        started_at=_minutes_ago(1),
        extra_env={"TINYASSETS_HEARTBEAT_GRACE_MARGIN_SECONDS": "30"},
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "ignoring" not in result.stdout
    # max-age 60 + margin 30 = a 90s window, and the container is 60s old.
    assert "too young to have refreshed the heartbeat" in result.stdout
    assert "(< 90s)" in result.stdout, result.stdout


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
@pytest.mark.parametrize("trigger", ["inactive-unit", "stopped-container"])
def test_daemon_watchdog_stands_down_while_a_deploy_holds_the_lock(tmp_path, trigger):
    """A deploy's recreate looks like a dead daemon; the watchdog must not act on it.

    2026-10-01: the unit was inactive and the container absent mid-recreate, so
    this script restarted the container. That helped kill the new image and
    fail the rollback
    (docs/audits/2026-10-01-deploy-drain-repro/INCIDENT.md).
    flock is mocked here, so this proves the script ASKS for the deploy's lock
    on fd 8 and stops when it is held. It does not prove kernel lock semantics.
    """
    (tmp_path / "host-mutation.lock").write_text("", encoding="utf-8")
    result, systemctl_lines, docker_lines, _relay, _compose = _run_watchdog(
        tmp_path, trigger, extra_env={"WATCHDOG_FLOCK_BUSY_FD": "8"},
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "a deploy holds" in result.stdout
    assert "restarting daemon container" not in result.stdout
    assert systemctl_lines == []
    assert docker_lines == []


@pytest.mark.skipif(not _BASH, reason="bash is unavailable")
def test_daemon_watchdog_acts_when_the_deploy_lock_is_free(tmp_path):
    """The lock gates the watchdog only while it is HELD; a free lock changes nothing."""
    (tmp_path / "host-mutation.lock").write_text("", encoding="utf-8")
    result, systemctl_lines, _docker, _relay, _compose = _run_watchdog(
        tmp_path, "inactive-unit",
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "restarting daemon container: systemd unit is not active" in result.stdout
    assert "restart tinyassets-daemon.service" in systemctl_lines
    flock_calls = (tmp_path / "wd-flock.log").read_text(encoding="utf-8").split("\n")
    assert "-n 8" in flock_calls, "the deploy's lock must actually be asked for"
