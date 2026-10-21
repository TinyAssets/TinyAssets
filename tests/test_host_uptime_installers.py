from __future__ import annotations

import functools
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from scripts.host_service_bundle import service_bundle

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
RUNTIME_FILES = (*service_bundle(REPO, [REPO / "deploy" / u for u in SERVICES]),
                 "scripts/host_service_bundle.py")
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
    if [[ -n "${DAEMON_RELOAD_RELEASE:-}" ]]; then
      while [[ ! -e "$DAEMON_RELOAD_RELEASE" ]]; do sleep 0.05; done
    fi
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




def _minutes_ago(minutes: float) -> str:
    """Docker's RFC3339 `State.StartedAt` form, that many minutes in the past."""
    started = time.gmtime(time.time() - minutes * 60)
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", started)












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










