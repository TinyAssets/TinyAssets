"""Tests for deploy/backup.sh and deploy/backup-restore.sh.

Coverage:
  - shellcheck lint (skipped if shellcheck not installed)
  - DRY_RUN=1 exits 0 with no mutations (no tar, upload, or rclone calls)
  - Missing BACKUP_DEST exits 1 when DRY_RUN is not set
  - backup-restore.sh DRY_RUN=1 exits 0 after identifying target archive
  - backup-restore.sh missing BACKUP_DEST exits 1
  - Retention-policy logic: validated via scripts/backup_prune.py (canonical)
    daily-7 / weekly-4 / monthly-6 boundaries
"""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BACKUP_SH = REPO / "deploy" / "backup.sh"
RESTORE_SH = REPO / "deploy" / "backup-restore.sh"
PRUNE_PY = REPO / "scripts" / "backup_prune.py"

# Import the canonical retention logic directly from the script.
sys.path.insert(0, str(REPO / "scripts"))
from backup_prune import _apply_retention  # noqa: E402

_SHELLCHECK = shutil.which("shellcheck")
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(_BASH is None, reason="bash not available")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _is_wsl_bash() -> bool:
    return (
        os.name == "nt"
        and _BASH is not None
        and Path(_BASH).name.lower() == "bash.exe"
        and "system32" in str(Path(_BASH).parent).lower()
    )


def _bash_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name != "nt":
        return str(resolved)
    if _is_wsl_bash():
        drive = resolved.drive.rstrip(":").lower()
        rest = resolved.as_posix()[2:]
        return f"/mnt/{drive}{rest}"
    return resolved.as_posix()


def _bash_path_env(*leading_paths: Path) -> str:
    leading = [_bash_path(path) for path in leading_paths]
    if _is_wsl_bash():
        return ":".join([
            *leading,
            "/usr/local/sbin",
            "/usr/local/bin",
            "/usr/sbin",
            "/usr/bin",
            "/sbin",
            "/bin",
        ])
    return ":".join([*leading, os.environ.get("PATH", "")])


def _run(script: Path, env: dict, args: list[str] | None = None) -> subprocess.CompletedProcess:
    if _is_wsl_bash():
        assignments = " ".join(
            f"{name}={shlex.quote(str(value))}"
            for name, value in env.items()
        )
        command = " ".join(
            [
                "/usr/bin/env",
                assignments,
                shlex.quote(_bash_path(script)),
                *(shlex.quote(arg) for arg in (args or [])),
            ]
        )
        return subprocess.run([_BASH, "-lc", command], capture_output=True, text=True)

    full_env = {**os.environ, **env}
    cmd = [_BASH, _bash_path(script)] + (args or [])
    return subprocess.run(cmd, capture_output=True, text=True, env=full_env)


# ---------------------------------------------------------------------------
# shellcheck
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('mode', ['snapshot', 'nightly'])
def test_browser_keys_never_enter_data_archives(tmp_path, mode):
    import sqlite3

    volume = tmp_path / 'volume'
    keys = volume / '.broker' / 'browser-vault'
    keys.mkdir(parents=True)
    (keys / 'owner.key').write_bytes(b'private owner key')
    with sqlite3.connect(volume / '.broker' / 'outbound.db') as conn:
        conn.execute('CREATE TABLE custody (sealed BLOB)')
        conn.execute('INSERT INTO custody VALUES (?)', (b'encrypted session',))
    archives = tmp_path / 'archives'
    archives.mkdir()
    fake_env = tmp_path / 'commands.sh'
    fake_env.write_text(
        f"docker() {{ if [[ $1 == volume ]]; then echo {shlex.quote(str(volume))}; fi; }}\n"
        'rclone() { if [[ $1 == copyto ]]; then '
        f'cp "$6" {shlex.quote(str(archives))}/; fi; }}\n')
    result = _run(BACKUP_SH, {
        'BASH_ENV': str(fake_env), 'BACKUP_VOLUME': 'nonexistent-browser-backup-test',
        'BACKUP_DEST': 'test:backup', 'BACKUP_MODE': mode, 'GH_TOKEN': '',
        'BACKUP_LOG': str(tmp_path / 'log'),
        'BACKUP_SNAPSHOT_DIR': str(tmp_path / 'snapshots'),
    })
    assert result.returncode == 0, result.stdout + result.stderr
    found = list(archives.glob('*.tar.gz'))
    assert len(found) == (1 if mode == 'snapshot' else 2)
    for archive in found:
        with tarfile.open(archive) as handle:
            names = handle.getnames()
            assert any(name.endswith('.broker/outbound.db') for name in names)
            assert not any('browser-vault' in name for name in names)


@pytest.mark.parametrize('entry', ['egress-42.sock', 'engine-42-abcdef123456.sock',
                                  'unknown.sock', 'omit-file'])
def test_snapshot_restorable_inventory(tmp_path, entry):
    import socket

    volume = tmp_path / 'volume'
    sidecar = volume / '.universe-sidecars/alice'
    sidecar.mkdir(parents=True)
    (volume / 'keep').write_bytes(b'restore me')
    endpoint = socket.socket(socket.AF_UNIX)
    if entry != 'omit-file':
        endpoint.bind(str(sidecar / entry))
    fake_env = tmp_path / 'commands.sh'
    fake_env.write_text(
        f"docker() {{ if [[ $1 == volume ]]; then echo {shlex.quote(str(volume))}; fi; }}\n"
        "rclone() { return 0; }\n" + (
            "tar() { if [[ $1 == -czf ]]; then command tar --exclude=./keep \"$@\"; "
            "else command tar \"$@\"; fi; }\n" if entry == 'omit-file' else ''))
    try:
        result = _run(BACKUP_SH, {
            'BASH_ENV': str(fake_env), 'BACKUP_VOLUME': 'nonexistent-snapshot-test',
            'BACKUP_DEST': 'test:backup', 'BACKUP_MODE': 'snapshot',
            'BACKUP_LOG': str(tmp_path / 'log'),
            'BACKUP_SNAPSHOT_DIR': str(tmp_path / 'snapshots'),
        })
    finally:
        endpoint.close()
    expected = entry.startswith(('egress-', 'engine-'))
    assert (result.returncode == 0) == expected, result.stdout + result.stderr
    if expected:
        archive = next((tmp_path / 'snapshots').glob('*.tar.gz'))
        with tarfile.open(archive) as handle:
            assert handle.extractfile('./keep').read() == b'restore me'

@pytest.mark.skipif(_SHELLCHECK is None, reason="shellcheck not installed")
def test_backup_sh_shellcheck():
    result = subprocess.run(
        [_SHELLCHECK, "--severity=warning", str(BACKUP_SH)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"shellcheck backup.sh:\n{result.stdout}\n{result.stderr}"


@pytest.mark.skipif(_SHELLCHECK is None, reason="shellcheck not installed")
def test_restore_sh_shellcheck():
    result = subprocess.run(
        [_SHELLCHECK, "--severity=warning", str(RESTORE_SH)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"shellcheck backup-restore.sh:\n{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# backup.sh — DRY_RUN
# ---------------------------------------------------------------------------

def test_backup_dry_run_exits_0_without_backup_dest():
    """DRY_RUN=1 must exit 0 even when BACKUP_DEST is absent."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = {
            "DRY_RUN": "1",
            "BACKUP_DEST": "",
            "BACKUP_LOG": _bash_path(Path(tmpdir) / "backup.log"),
        }
        result = _run(BACKUP_SH, env)
    assert result.returncode == 0, f"expected exit 0, got {result.returncode}\n{result.stderr}"


def test_backup_dry_run_prints_dry_run_indicator():
    """DRY_RUN=1 output must mention 'dry'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = {
            "DRY_RUN": "1",
            "BACKUP_DEST": "s3://test-bucket/backups",
            "BACKUP_LOG": _bash_path(Path(tmpdir) / "backup.log"),
        }
        result = _run(BACKUP_SH, env)
    combined = (result.stdout + result.stderr).lower()
    assert "dry" in combined, f"Expected 'dry' in output:\n{result.stdout}\n{result.stderr}"


def test_backup_dry_run_no_mutating_commands(tmp_path):
    """DRY_RUN=1: tar and rclone must not be invoked."""
    call_log = tmp_path / "calls.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for cmd in ("tar", "rclone", "docker"):
        fake_cmd = fake_bin / cmd
        fake_cmd.write_text(
            "#!/usr/bin/env bash\n"
            f"echo \"{cmd} called: $*\" >> '{_bash_path(call_log)}'\n"
            "exit 0\n",
            encoding="utf-8",
            newline="\n",
        )
        fake_cmd.chmod(0o755)

    env = {
        "DRY_RUN": "1",
        "BACKUP_DEST": "s3://test-bucket/backups",
        "BACKUP_LOG": _bash_path(tmp_path / "backup.log"),
        "PATH": _bash_path_env(fake_bin),
    }
    result = _run(BACKUP_SH, env)
    assert result.returncode == 0, f"exit {result.returncode}\n{result.stderr}"
    if call_log.exists():
        calls = call_log.read_text()
        assert "tar called" not in calls, f"tar was invoked in DRY_RUN:\n{calls}"
        assert "rclone called" not in calls, f"rclone was invoked in DRY_RUN:\n{calls}"


# ---------------------------------------------------------------------------
# backup.sh — BACKUP_DEST check
# ---------------------------------------------------------------------------

def test_backup_exits_1_when_backup_dest_missing():
    """Without DRY_RUN, missing BACKUP_DEST must exit 1."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = {
            "DRY_RUN": "0",
            "BACKUP_DEST": "",
            "BACKUP_LOG": _bash_path(Path(tmpdir) / "backup.log"),
        }
        result = _run(BACKUP_SH, env)
    assert result.returncode == 1, (
        f"expected exit 1 for missing BACKUP_DEST, got {result.returncode}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# backup-restore.sh — DRY_RUN
# ---------------------------------------------------------------------------

def _fake_rclone_bin(tmp_path: Path) -> Path:
    """Return path dir containing a fake rclone that returns one listing entry."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    fake_rclone = fake_bin / "rclone"
    fake_rclone.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "lsf" ]]; then\n'
        "    echo '2026-04-20T02-00-00Z;tinyassets-data-2026-04-20T02-00-00Z.tar.gz'\n"
        "    exit 0\n"
        "fi\n"
        'if [[ "$1" == "ls" ]]; then exit 0; fi\n'
        'if [[ "$1" == "obscure" ]]; then echo "obscured"; exit 0; fi\n'
        "exit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_rclone.chmod(0o755)
    return fake_bin


def _fake_rclone_bash_env(tmp_path: Path) -> Path:
    """Return a BASH_ENV file that defines fake rclone for mounted Windows paths."""
    fake_env = tmp_path / "fake-rclone-env.sh"
    fake_env.write_text(
        "rclone() {\n"
        '    if [[ "$1" == "lsf" ]]; then\n'
        "        echo '2026-04-20T02-00-00Z;tinyassets-data-2026-04-20T02-00-00Z.tar.gz'\n"
        "        return 0\n"
        "    fi\n"
        '    if [[ "$1" == "ls" ]]; then return 0; fi\n'
        '    if [[ "$1" == "obscure" ]]; then echo "obscured"; return 0; fi\n'
        "    return 0\n"
        "}\n",
        encoding="utf-8",
        newline="\n",
    )
    return fake_env


def test_restore_dry_run_exits_0(tmp_path):
    """DRY_RUN=1 on restore must exit 0 after identifying the archive."""
    fake_bin = _fake_rclone_bin(tmp_path)
    fake_env = _fake_rclone_bash_env(tmp_path)
    env = {
        "DRY_RUN": "1",
        "BACKUP_DEST": "s3://test-bucket/tinyassets-backups",
        "BACKUP_LOG": _bash_path(tmp_path / "backup.log"),
        "BASH_ENV": _bash_path(fake_env),
        "PATH": _bash_path_env(fake_bin),
    }
    result = _run(RESTORE_SH, env)
    assert result.returncode == 0, f"exit {result.returncode}\n{result.stdout}\n{result.stderr}"


def test_restore_dry_run_prints_dry_run_indicator(tmp_path):
    """DRY_RUN=1 restore output must mention 'dry'."""
    fake_bin = _fake_rclone_bin(tmp_path)
    fake_env = _fake_rclone_bash_env(tmp_path)
    env = {
        "DRY_RUN": "1",
        "BACKUP_DEST": "s3://test-bucket/tinyassets-backups",
        "BACKUP_LOG": _bash_path(tmp_path / "backup.log"),
        "BASH_ENV": _bash_path(fake_env),
        "PATH": _bash_path_env(fake_bin),
    }
    result = _run(RESTORE_SH, env)
    combined = (result.stdout + result.stderr).lower()
    assert "dry" in combined, f"Expected 'dry':\n{result.stdout}\n{result.stderr}"


def test_restore_exits_1_when_backup_dest_missing():
    """Missing BACKUP_DEST must exit 1 on restore."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = {
            "BACKUP_DEST": "",
            "BACKUP_LOG": _bash_path(Path(tmpdir) / "backup.log"),
        }
        result = _run(RESTORE_SH, env)
    assert result.returncode == 1, (
        f"expected exit 1 for missing BACKUP_DEST, got {result.returncode}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# backup-restore.sh — rollback-safe full-volume restore
# ---------------------------------------------------------------------------


def _fake_restore_bin(tmp_path: Path, volume_dir: Path) -> Path:
    """Create Docker/rclone shims for an isolated named-volume test."""
    fake_bin = tmp_path / "restore-bin"
    fake_bin.mkdir(exist_ok=True)
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env bash\n"
        'if [[ -n "${DOCKER_CALL_LOG:-}" ]]; then echo "$*" >> "$DOCKER_CALL_LOG"; fi\n'
        'if [[ "$1" == "volume" && "$2" == "inspect" ]]; then\n'
        '    printf "%s\\n" "$TEST_VOLUME_DIR"\n'
        "    exit 0\n"
        "fi\n"
        'if [[ "$1" == "volume" && "$2" == "create" ]]; then exit 0; fi\n'
        'if [[ "$1" == "ps" ]]; then\n'
        '    if [[ "${DOCKER_PS_EXIT:-0}" != "0" ]]; then exit "$DOCKER_PS_EXIT"; fi\n'
        '    if [[ -n "${DOCKER_STOP_MARKER:-}" && -e "$DOCKER_STOP_MARKER" ]]; then exit 0; fi\n'
        '    printf "%s\\n" "${DOCKER_PS_OUTPUT:-}"; exit 0\n'
        "fi\n"
        'if [[ "$1" == "stop" ]]; then\n'
        '    if [[ -n "${DOCKER_STOP_MARKER:-}" ]]; then touch "$DOCKER_STOP_MARKER"; fi\n'
        "    exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
        newline="\n",
    )
    (fake_bin / "docker").chmod(0o755)
    return fake_bin


def _backup_archive(tmp_path: Path, source_dir: Path) -> Path:
    """Run backup.sh and return its real full-tier archive.

    The fake rclone copies uploads to a local directory, exercising the exact
    `tar -C <parent> _data` archive shape the restore path must accept.
    """
    remote = tmp_path / "remote"
    remote.mkdir()
    fake_bin = _fake_restore_bin(tmp_path, source_dir)
    fake_rclone = fake_bin / "rclone"
    fake_rclone.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "copyto" ]]; then\n'
        '    argc=$#; src="${!argc}"; prev=$((argc - 1)); src="${!prev}"\n'
        '    dest="${!argc}"; cp "$src" "$dest"; exit 0\n'
        "fi\n"
        'if [[ "$1" == "lsf" ]]; then exit 0; fi\n'
        'if [[ "$1" == "deletefile" ]]; then exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_rclone.chmod(0o755)
    result = _run(
        BACKUP_SH,
        {
            "BACKUP_DEST": _bash_path(remote),
            "BACKUP_LOG": _bash_path(tmp_path / "backup.log"),
            "BACKUP_VOLUME": "test-volume",
            "TEST_VOLUME_DIR": _bash_path(source_dir),
            "PATH": _bash_path_env(fake_bin),
        },
    )
    assert result.returncode == 0, f"backup.sh failed:\n{result.stdout}\n{result.stderr}"
    archives = list(remote.glob("tinyassets-data-*.tar.gz"))
    assert len(archives) == 1
    return archives[0]


def _restore_env(tmp_path: Path, volume_dir: Path, archive: Path, fake_bin: Path) -> dict:
    return {
        "BACKUP_FILE": _bash_path(archive),
        "BACKUP_LOG": _bash_path(tmp_path / "restore.log"),
        "BACKUP_VOLUME": "test-volume",
        "TEST_VOLUME_DIR": _bash_path(volume_dir),
        "PATH": _bash_path_env(fake_bin),
    }


def test_restore_rejects_unsafe_docker_volume_name(tmp_path):
    result = _run(
        RESTORE_SH,
        {
            "BACKUP_LOG": _bash_path(tmp_path / "restore.log"),
            "BACKUP_VOLUME": "../outside",
        },
    )

    assert result.returncode == 1
    assert "invalid Docker volume name" in (result.stdout + result.stderr)


def test_restore_round_trip_from_backup_archive_preserves_dotfiles(tmp_path):
    source_dir = tmp_path / "source-volume" / "_data"
    source_dir.mkdir(parents=True)
    (source_dir / ".tinyassets.db").write_text("secret-state", encoding="utf-8")
    (source_dir / "nested").mkdir()
    (source_dir / "nested" / "canon.json").write_text("{}", encoding="utf-8")
    archive = _backup_archive(tmp_path, source_dir)

    target_dir = tmp_path / "target-volume" / "_data"
    target_dir.mkdir(parents=True)
    target_dir.chmod(0o750)
    (target_dir / "obsolete.txt").write_text("old", encoding="utf-8")
    fake_bin = _fake_restore_bin(tmp_path, target_dir)
    result = _run(RESTORE_SH, _restore_env(tmp_path, target_dir, archive, fake_bin))

    assert result.returncode == 0, f"restore failed:\n{result.stdout}\n{result.stderr}"
    assert (target_dir / ".tinyassets.db").read_text(encoding="utf-8") == "secret-state"
    assert (target_dir / "nested" / "canon.json").read_text(encoding="utf-8") == "{}"
    assert not (target_dir / "obsolete.txt").exists()
    assert archive.exists(), "caller-owned BACKUP_FILE must not be deleted"
    if not _is_wsl_bash():
        assert stat.S_IMODE(target_dir.stat().st_mode) == 0o750
    assert list(target_dir.parent.glob(".tinyassets-restore-old.*")), (
        "successful swap must retain the previous volume for post-canary rollback"
    )


def test_restore_corrupt_archive_leaves_volume_and_containers_intact(tmp_path):
    target_dir = tmp_path / "target-volume" / "_data"
    target_dir.mkdir(parents=True)
    (target_dir / ".preserve").write_text("prior", encoding="utf-8")
    corrupt = tmp_path / "truncated.tar.gz"
    corrupt.write_bytes(b"not a gzip archive")
    fake_bin = _fake_restore_bin(tmp_path, target_dir)
    call_log = tmp_path / "docker.calls"
    env = _restore_env(tmp_path, target_dir, corrupt, fake_bin)
    env["DOCKER_CALL_LOG"] = _bash_path(call_log)
    result = _run(RESTORE_SH, env)

    assert result.returncode != 0
    assert (target_dir / ".preserve").read_text(encoding="utf-8") == "prior"
    assert not call_log.exists() or " ps " not in f" {call_log.read_text()} ", (
        "archive validation must occur before stopping volume consumers"
    )


def test_restore_staging_failure_leaves_volume_and_containers_intact(tmp_path):
    target_dir = tmp_path / "target-volume" / "_data"
    target_dir.mkdir(parents=True)
    (target_dir / ".preserve").write_text("prior", encoding="utf-8")
    archive = _simple_full_archive(tmp_path / "valid.tar.gz")
    fake_bin = _fake_restore_bin(tmp_path, target_dir)
    call_log = tmp_path / "docker.calls"
    (fake_bin / "tar").write_text(
        "#!/usr/bin/env bash\n"
        'if [[ " $* " == *" -xzf "* ]]; then exit 74; fi\n'
        'exec /bin/tar "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    (fake_bin / "tar").chmod(0o755)
    env = _restore_env(tmp_path, target_dir, archive, fake_bin)
    env["DOCKER_CALL_LOG"] = _bash_path(call_log)
    result = _run(RESTORE_SH, env)

    assert result.returncode != 0
    assert "archive staging extract failed" in (result.stdout + result.stderr)
    assert (target_dir / ".preserve").read_text(encoding="utf-8") == "prior"
    assert not call_log.exists() or " ps " not in f" {call_log.read_text()} "


def test_restore_consumer_enumeration_failure_leaves_volume_intact(tmp_path):
    target_dir = tmp_path / "target-volume" / "_data"
    target_dir.mkdir(parents=True)
    (target_dir / ".preserve").write_text("prior", encoding="utf-8")
    archive = _simple_full_archive(tmp_path / "valid.tar.gz")
    fake_bin = _fake_restore_bin(tmp_path, target_dir)
    env = _restore_env(tmp_path, target_dir, archive, fake_bin)
    env["DOCKER_PS_EXIT"] = "70"
    result = _run(RESTORE_SH, env)

    assert result.returncode != 0
    assert "failed to enumerate running volume consumers" in (
        result.stdout + result.stderr
    )
    assert (target_dir / ".preserve").read_text(encoding="utf-8") == "prior"


def _simple_full_archive(path: Path, value: str = "new") -> Path:
    with tarfile.open(path, "w:gz") as tar:
        member = tarfile.TarInfo("_data/value.txt")
        data = value.encode("utf-8")
        member.size = len(data)
        tar.addfile(member, BytesIO(data))
    return path


@pytest.mark.parametrize(
    "kind",
    [
        "absolute",
        "traversal",
        "mixed-root",
        "root-file",
        "special",
        "symlink",
        "hardlink",
    ],
)
def test_restore_rejects_unsafe_archive_before_mutation(tmp_path, kind):
    target_dir = tmp_path / "target-volume" / "_data"
    target_dir.mkdir(parents=True)
    (target_dir / "prior.txt").write_text("prior", encoding="utf-8")
    archive = tmp_path / f"{kind}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        good = tarfile.TarInfo("_data/good.txt")
        good.size = 2
        tar.addfile(good, BytesIO(b"ok"))
        if kind == "absolute":
            bad = tarfile.TarInfo("/absolute/nope.txt")
            bad.size = 4
            tar.addfile(bad, BytesIO(b"nope"))
        elif kind == "traversal":
            bad = tarfile.TarInfo("_data/../outside.txt")
            bad.size = 4
            tar.addfile(bad, BytesIO(b"nope"))
        elif kind == "mixed-root":
            bad = tarfile.TarInfo("other-root/nope.txt")
            bad.size = 4
            tar.addfile(bad, BytesIO(b"nope"))
        elif kind == "root-file":
            bad = tarfile.TarInfo("_data")
            bad.size = 4
            tar.addfile(bad, BytesIO(b"nope"))
        elif kind == "special":
            bad = tarfile.TarInfo("_data/pipe")
            bad.type = tarfile.FIFOTYPE
            tar.addfile(bad)
        elif kind == "symlink":
            bad = tarfile.TarInfo("_data/link")
            bad.type = tarfile.SYMTYPE
            bad.linkname = "good.txt"
            tar.addfile(bad)
        else:
            bad = tarfile.TarInfo("_data/hardlink")
            bad.type = tarfile.LNKTYPE
            bad.linkname = "_data/good.txt"
            tar.addfile(bad)
    fake_bin = _fake_restore_bin(tmp_path, target_dir)
    result = _run(RESTORE_SH, _restore_env(tmp_path, target_dir, archive, fake_bin))

    assert result.returncode != 0
    assert (target_dir / "prior.txt").read_text(encoding="utf-8") == "prior"


def test_restore_rolls_back_when_second_rename_fails(tmp_path):
    target_dir = tmp_path / "target-volume" / "_data"
    target_dir.mkdir(parents=True)
    (target_dir / "prior.txt").write_text("prior", encoding="utf-8")
    archive = _simple_full_archive(tmp_path / "valid.tar.gz")
    fake_bin = _fake_restore_bin(tmp_path, target_dir)
    marker = tmp_path / "second-rename-failed"
    (fake_bin / "mv").write_text(
        "#!/usr/bin/env bash\n"
        'args=("$@"); while [[ "${args[0]}" == "--" ]]; do args=("${args[@]:1}"); done\n'
        'dest="${args[$((${#args[@]} - 1))]}"\n'
        'if [[ "$dest" == "$FAIL_SECOND_RENAME_TARGET" '
        '&& ! -e "$FAIL_SECOND_RENAME_MARKER" ]]; then\n'
        '    touch "$FAIL_SECOND_RENAME_MARKER"; exit 1\n'
        "fi\n"
        'exec /bin/mv "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    (fake_bin / "mv").chmod(0o755)
    env = _restore_env(tmp_path, target_dir, archive, fake_bin)
    env.update({
        "FAIL_SECOND_RENAME_TARGET": _bash_path(target_dir),
        "FAIL_SECOND_RENAME_MARKER": _bash_path(marker),
    })
    result = _run(RESTORE_SH, env)

    assert result.returncode != 0
    assert marker.exists(), "test must inject the staged-directory rename failure"
    assert (target_dir / "prior.txt").read_text(encoding="utf-8") == "prior"
    assert not (target_dir / "value.txt").exists()


def test_local_backup_file_bypasses_rclone_and_is_preserved(tmp_path):
    target_dir = tmp_path / "target-volume" / "_data"
    target_dir.mkdir(parents=True)
    archive = _simple_full_archive(tmp_path / "local.tar.gz")
    fake_bin = _fake_restore_bin(tmp_path, target_dir)
    rclone_marker = tmp_path / "rclone-called"
    (fake_bin / "rclone").write_text(
        "#!/usr/bin/env bash\n"
        'touch "$RCLONE_MARKER"; exit 99\n',
        encoding="utf-8",
        newline="\n",
    )
    (fake_bin / "rclone").chmod(0o755)
    env = _restore_env(tmp_path, target_dir, archive, fake_bin)
    env["RCLONE_MARKER"] = _bash_path(rclone_marker)
    result = _run(RESTORE_SH, env)

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert not rclone_marker.exists()
    assert archive.exists()


def test_local_backup_file_rejects_symlink_source(tmp_path):
    archive = _simple_full_archive(tmp_path / "target.tar.gz")
    link = tmp_path / "linked.tar.gz"
    try:
        link.symlink_to(archive)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    result = _run(
        RESTORE_SH,
        {
            # Not _bash_path(link): it resolve()s, which follows the link and
            # handed the script the TARGET -- so this test never saw a symlink
            # and failed on every Linux run since it was written.
            "BACKUP_FILE": f"{_bash_path(link.parent)}/{link.name}",
            "BACKUP_LOG": _bash_path(tmp_path / "restore.log"),
        },
    )

    assert result.returncode == 2
    assert "non-symlink regular file" in (result.stdout + result.stderr)


def test_remote_restore_filters_brain_and_uses_unique_download_dir(tmp_path):
    target_dir = tmp_path / "target-volume" / "_data"
    target_dir.mkdir(parents=True)
    remote_archive = _simple_full_archive(tmp_path / "remote-data.tar.gz")
    fake_bin = _fake_restore_bin(tmp_path, target_dir)
    rclone_log = tmp_path / "rclone.log"
    (fake_bin / "rclone").write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "lsf" ]]; then\n'
        "  echo '2026-07-23T00-00-00Z;tinyassets-brain-2026-07-23T00-00-00Z.tar.gz'\n"
        "  echo '2026-07-23T00-00-00Z;tinyassets-data-2026-07-23T00-00-00Z.tar.gz'\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "$1" == "ls" ]]; then exit 0; fi\n'
        'if [[ "$1" == "copyto" ]]; then\n'
        '  argc=$#; dest="${!argc}"; echo "$dest" >> "$RCLONE_LOG"\n'
        '  cp "$REMOTE_ARCHIVE" "$dest"; exit 0\n'
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
        newline="\n",
    )
    (fake_bin / "rclone").chmod(0o755)
    env = {
        "BACKUP_DEST": "fake:backups",
        "BACKUP_LOG": _bash_path(tmp_path / "restore.log"),
        "BACKUP_VOLUME": "test-volume",
        "TEST_VOLUME_DIR": _bash_path(target_dir),
        "REMOTE_ARCHIVE": _bash_path(remote_archive),
        "RCLONE_LOG": _bash_path(rclone_log),
        "PATH": _bash_path_env(fake_bin),
    }
    result = _run(RESTORE_SH, env)

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    download_path = rclone_log.read_text(encoding="utf-8").strip()
    assert ".tinyassets-restore-download." in download_path
    assert (target_dir / "value.txt").read_text(encoding="utf-8") == "new"


def test_restore_stops_all_running_volume_consumers_after_validation(tmp_path):
    target_dir = tmp_path / "target-volume" / "_data"
    target_dir.mkdir(parents=True)
    archive = _simple_full_archive(tmp_path / "valid.tar.gz")
    fake_bin = _fake_restore_bin(tmp_path, target_dir)
    call_log = tmp_path / "docker.calls"
    env = _restore_env(tmp_path, target_dir, archive, fake_bin)
    env.update({
        "DOCKER_CALL_LOG": _bash_path(call_log),
        "DOCKER_PS_OUTPUT": "container-a\ncontainer-b",
        "DOCKER_STOP_MARKER": _bash_path(tmp_path / "docker.stopped"),
    })
    result = _run(RESTORE_SH, env)

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    calls = call_log.read_text(encoding="utf-8")
    assert "ps -q --filter volume=test-volume" in calls
    assert "stop container-a container-b" in calls


def test_bounded_parallel_volume_restore_burst_is_isolated(tmp_path):
    cases = []
    for index in range(8):
        name = f"volume-{index}"
        case_dir = tmp_path / name
        target_dir = case_dir / "volume" / "_data"
        target_dir.mkdir(parents=True)
        archive = _simple_full_archive(case_dir / "archive.tar.gz", name)
        fake_bin = _fake_restore_bin(case_dir, target_dir)
        cases.append((case_dir, target_dir, archive, fake_bin, name))

    def restore(case):
        case_dir, target_dir, archive, fake_bin, _ = case
        return _run(RESTORE_SH, _restore_env(case_dir, target_dir, archive, fake_bin))

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(restore, cases))

    assert all(result.returncode == 0 for result in results), [
        f"{result.stdout}\n{result.stderr}" for result in results
    ]
    for _, target_dir, _, _, value in cases:
        assert (target_dir / "value.txt").read_text(encoding="utf-8") == value
        assert not list(target_dir.parent.glob(".tinyassets-restore-stage.*"))


def test_same_volume_restore_is_refused_while_lock_is_held(tmp_path):
    target_dir = tmp_path / "volume" / "_data"
    target_dir.mkdir(parents=True)
    archive = _simple_full_archive(tmp_path / "archive.tar.gz", "new")
    fake_bin = _fake_restore_bin(tmp_path, target_dir)
    (fake_bin / "tar").write_text(
        "#!/usr/bin/env bash\n"
        'if [[ " $* " == *" -xzf "* ]]; then sleep 1; fi\n'
        'exec /bin/tar "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    (fake_bin / "tar").chmod(0o755)
    env = _restore_env(tmp_path, target_dir, archive, fake_bin)

    if _is_wsl_bash():
        assignments = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env.items())
        command = f"/usr/bin/env {assignments} {shlex.quote(_bash_path(RESTORE_SH))}"
        first = subprocess.Popen([_BASH, "-lc", command], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
    else:
        first = subprocess.Popen([_BASH, _bash_path(RESTORE_SH)], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env={**os.environ, **env})
    time.sleep(0.2)
    second = _run(RESTORE_SH, env)
    first_stdout, first_stderr = first.communicate(timeout=10)

    assert first.returncode == 0, f"first restore failed:\n{first_stdout}\n{first_stderr}"
    assert second.returncode != 0
    assert "already in progress" in (second.stdout + second.stderr)


# ---------------------------------------------------------------------------
# Retention-policy logic (delegates to scripts/backup_prune.py — canonical)
# ---------------------------------------------------------------------------


def _make_archives(dates: list[str]) -> list[str]:
    return [f"tinyassets-data-{d}T02-00-00Z.tar.gz" for d in dates]


def _retention_set(names: list[str], **kwargs: int) -> set[str]:
    return set(_apply_retention(names, **kwargs))


def test_retention_keeps_last_7_daily():
    """With 10 archives and daily=7, most recent 7 must never be deleted."""
    dates = [f"2026-04-{d:02d}" for d in range(1, 11)]
    names = _make_archives(dates)
    to_delete = _retention_set(names, keep_daily=7, keep_weekly=4, keep_monthly=6)
    recent_7 = set(_make_archives(dates[-7:]))
    assert recent_7.isdisjoint(to_delete), (
        f"Recent 7 should never be deleted: {to_delete & recent_7}"
    )


def test_retention_keeps_weekly_anchors():
    """Archives from different weeks should be kept beyond the daily window."""
    # 14 days across 2 weeks
    dates = [f"2026-04-{d:02d}" for d in range(1, 15)]
    names = _make_archives(dates)
    to_delete = _retention_set(names, keep_daily=7, keep_weekly=2, keep_monthly=6)
    # Apr 8 is first of week 2 (days 8-14)
    week2_anchor = "tinyassets-data-2026-04-08T02-00-00Z.tar.gz"
    assert week2_anchor not in to_delete, f"Week anchor should be kept: {week2_anchor}"


def test_retention_all_recent_no_pruning():
    """Fewer archives than daily window means nothing is pruned."""
    dates = [f"2026-04-{d:02d}" for d in range(1, 6)]
    names = _make_archives(dates)
    to_delete = _retention_set(names, keep_daily=7, keep_weekly=4, keep_monthly=6)
    assert to_delete == set(), f"Nothing should be pruned: {to_delete}"


def test_retention_monthly_anchor_kept():
    """One archive per month is kept when beyond weekly window.

    The retention algo keeps the NEWEST archive per monthly bucket (since
    it walks newest-first). With two months of data and keep_monthly=2,
    at least one archive from each month must survive.
    """
    march = [f"2026-03-{d:02d}" for d in range(1, 32) if d <= 31]
    april = [f"2026-04-{d:02d}" for d in range(1, 5)]
    names = _make_archives(march + april)
    to_delete = _retention_set(names, keep_daily=7, keep_weekly=4, keep_monthly=2)
    # At least one March archive must survive (the monthly anchor is newest-first,
    # so Mar 31 is kept as the March monthly representative).
    march_kept = [n for n in names if "2026-03-" in n and n not in to_delete]
    assert march_kept, f"At least one March archive should be kept; all deleted: {to_delete}"


def test_retention_never_deletes_unrecognized_names():
    """Foreign files at the destination must never be emitted for deletion.

    Regression: before 2026-06-10 the delete set was computed as
    all-names-minus-kept, so any file not matching the tinyassets-data
    pattern was deleted on every successful prune.
    """
    foreign = ["README.txt", "manual-snapshot.tar.gz", "somebody-elses-file.bin"]
    dates = [f"2026-04-{d:02d}" for d in range(1, 15)]
    names = _make_archives(dates) + foreign
    to_delete = _retention_set(names, keep_daily=1, keep_weekly=1, keep_monthly=1)
    assert set(foreign).isdisjoint(to_delete), (
        f"Foreign names must never be pruned: {to_delete & set(foreign)}"
    )


def test_retention_per_tier_independent():
    """Brain and data archives get independent retention windows."""
    dates = [f"2026-04-{d:02d}" for d in range(1, 11)]
    data_names = _make_archives(dates)
    brain_names = [f"tinyassets-brain-{d}T03-00-00Z.tar.gz" for d in dates]
    to_delete = _retention_set(
        data_names + brain_names, keep_daily=7, keep_weekly=4, keep_monthly=6
    )
    # Same policy as test_prune_script_subprocess_emits_deletions, applied
    # per tier: only the Apr 01 archive of EACH tier is pruned.
    assert to_delete == {
        "tinyassets-data-2026-04-01T02-00-00Z.tar.gz",
        "tinyassets-brain-2026-04-01T03-00-00Z.tar.gz",
    }, f"Expected per-tier Apr 01 pruning; got: {to_delete}"


def test_backup_sh_has_brain_tier_and_tolerates_live_tar():
    """Structural anchors for the 2026-06-10 two-tier redesign."""
    text = BACKUP_SH.read_text(encoding="utf-8")
    assert "tinyassets-brain-" in text, "brain-tier archive missing from backup.sh"
    assert "--warning=no-file-changed" in text, "live-tar warning suppression missing"
    assert 'tar_rc' in text and '-ge 2' in text, (
        "full-tier tar must tolerate rc=1 and fail only on rc>=2"
    )
    assert "src.backup(dst)" in text, "consistent sqlite copy (sqlite3 backup API) missing"


def test_prune_script_subprocess_emits_deletions():
    """backup_prune.py via subprocess: 10 archives, daily=7 → correct prune set.

    Policy: daily=7 keeps Apr 04-10. Apr 03 is the weekly anchor for week 1
    (days 1-7). Apr 02 is the monthly anchor for 2026-04. Only Apr 01 is pruned.
    """
    dates = [f"2026-04-{d:02d}" for d in range(1, 11)]
    names = _make_archives(dates)
    cmd = [sys.executable, str(PRUNE_PY),
           "--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "6"]
    proc = subprocess.run(
        cmd,
        input="\n".join(names) + "\n",
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"backup_prune.py failed:\n{proc.stderr}"
    deleted = [line for line in proc.stdout.strip().splitlines() if line]
    assert deleted == ["tinyassets-data-2026-04-01T02-00-00Z.tar.gz"], (
        f"Expected only Apr 01 pruned; got: {deleted}"
    )
