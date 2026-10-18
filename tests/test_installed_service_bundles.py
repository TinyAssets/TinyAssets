"""Every shipped unit executes its real command with only its installed Python."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import venv
from pathlib import Path

from scripts.boxhostd_install_files import ship_list
from scripts.host_service_bundle import service_bundle
from tests.test_backup_owner_box import shim
from tests.test_host_uptime_installers import _install_env, _run_installer

ROOT = Path(__file__).resolve().parents[1]


def test_every_installed_unit_execstart_has_closed_imports(tmp_path):
    installation = tmp_path / "uptime"
    installation.mkdir()
    result = _run_installer(_install_env(installation))
    assert result.returncode == 0, result.stdout + result.stderr
    runtime = installation / "runtime/current"
    units = {p.name: (p, runtime) for p in (installation / "systemd").glob("*.service")}

    # Install the boxhost bundle with the very same operation its installer runs.
    box = tmp_path / "box"
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts/host_service_bundle.py"),
        "--root", str(ROOT), "--install-to", str(box), "--flat-scripts",
        "boxhostd.service", "box-store-device.service",
    ], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    for relative in ship_list(ROOT):
        if relative.endswith(".service"):
            unit = box / Path(relative).name
            shutil.copyfile(ROOT / relative, unit)
            units[unit.name] = (unit, box)
    # Bootstrap's Docker-only service has an empty Python closure.
    bootstrap = (ROOT / "deploy/hetzner-bootstrap.sh").read_text()
    for name in re.findall(r"deploy/([\w.-]+\.service)", bootstrap):
        unit = tmp_path / name
        shutil.copyfile(ROOT / "deploy" / name, unit)
        assert service_bundle(ROOT, [unit]) == []
        units[name] = (unit, tmp_path)

    # An additional unit requires a real execution fixture, never silent omission.
    covered = {
        "tinyassets-backup.service", "tinyassets-disk-watch.service",
        "tinyassets-prune.service", "tinyassets-watchdog.service",
        "daemon-watchdog.service", "tinyassets-ship-logs.service",
        "boxhostd.service", "box-store-device.service", "tinyassets-daemon.service",
    }
    assert set(units) == covered
    # These two retired templates have no installer; any other new template needs coverage.
    assert {p.name for p in (ROOT / "deploy").glob("*.service")} == covered | {
        "tinyassets-autoheal.service", "tinyassets-recovery-reconcile.service"}

    venv.EnvBuilder(with_pip=False).create(tmp_path / "python")
    python = str(tmp_path / "python/bin/python")
    binary = tmp_path / "bin"
    binary.mkdir()
    shim(binary, "python3", "exec " + shlex.quote(python) + ' -S "$@"\n')
    shim(binary, "docker", 'echo "synthetic docker: $*"; exit 0\n')
    shim(binary, "systemctl", 'exit 0\n')
    data = tmp_path / "data"
    (data / "runs").mkdir(parents=True)
    (data / ".layout.json").write_text('{"layout":2,"box_layout":"owner-box","state":"stable"}')
    transcript = data / "runs/expired.jsonl"
    transcript.write_text('{"synthetic":true}\n')
    os.utime(transcript, (1, 1))
    env = {
        "PATH": f"{binary}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tmp_path), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "TINYASSETS_DATA_DIR": str(data), "DISK_WATCH_PATH": str(data),
        "DISK_WARN_PCT": "101", "DRY_RUN": "0",
        "TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY": "0",
        "TINYASSETS_HOST_MUTATION_LOCK": str(tmp_path / "mutation.lock"),
        "TINYASSETS_DAEMON_WATCHDOG_LOCK": str(tmp_path / "watchdog.lock"),
        "TINYASSETS_COMPOSE_FILE": str(tmp_path / "absent.yml"),
    }
    # The real box unit must refuse this non-systemd venue. Also load its two
    # deferred serving branches from the installed bundle, with no site fallback.
    result = subprocess.run([
        python, "-S", "-c", "import boxhostd_idle; import tinyassets.boxes.host_service",
    ], cwd=box, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    (tmp_path / "mutation.lock").touch()
    expected = {
        "tinyassets-disk-watch.service": [(0, "below threshold"), (0, "rotation summary"),
                                           (2, '"reason": "invalid_docker_json"')],
        "tinyassets-prune.service": [(2, '"reason": "invalid_docker_json"')],
        "tinyassets-watchdog.service": [(0, "RED #1/")],
        "daemon-watchdog.service": [(0, "synthetic docker: restart")],
        "tinyassets-ship-logs.service": [(0, "dry-run")],
        "boxhostd.service": [(1, "delegated systemd service cgroup")],
        "box-store-device.service": [(1, "root-owned box-store.conf required")],
        "tinyassets-daemon.service": [(0, "synthetic docker:")],
    }
    for name, (unit, bundle) in sorted(units.items()):
        text = unit.read_text()
        statuses = re.findall(r"^SuccessExitStatus=(.*)$", text, re.M)
        assert not any("1" in row.split() for row in statuses), name
        commands = re.findall(r"^ExecStart=(.*)$", text, re.M)
        assert commands and all(not command.startswith("-") for command in commands)
        if name == "tinyassets-backup.service":
            # Real installed ExecStart, live SQLite, erased-owner ledger, paired archives.
            from tests.test_backup_owner_box import OwnerBoxLiveBackupTest

            OwnerBoxLiveBackupTest(
                "test_installed_service_publishes_live_pair_without_stopping_writers"
            ).test_installed_service_publishes_live_pair_without_stopping_writers()
            continue
        assert len(commands) == len(expected[name]), name
        for command, (code, evidence) in zip(commands, expected[name], strict=True):
            command = command.replace("/opt/tinyassets-host-uptime/current", str(runtime))
            command = command.replace("/opt/tinyassets/boxhostd", str(box))
            args = shlex.split(command)
            if args[0] == "/usr/bin/python3":
                args[:1] = [python, "-S"]
            elif args[0] == "/usr/bin/docker":
                args[0] = str(binary / "docker")
            run_env = dict(env)
            for setting in re.findall(r"^Environment=(.*)$", text, re.M):
                for assignment in shlex.split(setting):
                    key, value = assignment.split("=", 1)
                    run_env[key] = value.replace(
                        "/opt/tinyassets-host-uptime/current", str(runtime)).replace(
                            "/opt/tinyassets/boxhostd", str(box))
            working = re.findall(r"^WorkingDirectory=(.*)$", text, re.M)
            cwd = working[0] if working else "/"
            cwd = cwd.replace("/opt/tinyassets-host-uptime/current", str(runtime))
            cwd = cwd.replace("/opt/tinyassets", str(tmp_path / "bootstrap"))
            Path(cwd).mkdir(parents=True, exist_ok=True)
            if name == "tinyassets-ship-logs.service":
                run_env["DRY_RUN"] = "1"
            if "scripts.rotate_run_transcripts" in args:
                rotation = (args, cwd, run_env)
            result = subprocess.run(args, cwd=cwd, env=run_env, text=True,
                                    capture_output=True, timeout=30)
            output = result.stdout + result.stderr
            assert "ModuleNotFoundError" not in output, (name, command, output)
            assert result.returncode == code and evidence in output, (name, command, output)
    assert not transcript.exists(), "the real rotation command must reach its layout import"

    # A missing transitive import must fail the real unit command loudly.
    (runtime / "tinyassets/storage_layout.py").unlink()
    command, cwd, run_env = rotation
    result = subprocess.run(command, cwd=cwd, env=run_env,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 1 and "ModuleNotFoundError" in result.stderr


def test_manifest_follows_new_transitive_imports_and_commands(tmp_path):
    (tmp_path / "deploy").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tinyassets").mkdir()
    unit = tmp_path / "deploy/new.service"
    unit.write_text("ExecStart=/usr/bin/python3 -m scripts.new\n")
    (tmp_path / "scripts/new.py").write_text("from tinyassets import first\nfirst.later()\n")
    (tmp_path / "tinyassets/__init__.py").touch()
    (tmp_path / "tinyassets/first.py").write_text("def later():\n    from . import second\n")
    (tmp_path / "tinyassets/second.py").touch()
    assert "tinyassets/second.py" in service_bundle(tmp_path, [unit])


def test_shell_heredoc_imports_and_package_initializers_are_shipped(tmp_path):
    (tmp_path / "deploy").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/__init__.py").write_text("from . import initialization\n")
    (tmp_path / "scripts/initialization.py").touch()
    (tmp_path / "scripts/embedded.py").touch()
    unit = tmp_path / "deploy/new.service"
    unit.write_text("ExecStart=/bin/bash /opt/bundle/deploy/new.sh\n")
    (tmp_path / "deploy/new.sh").write_text(
        "python3 - <<-'PY'\n\tfrom scripts import embedded\n\tPY\n")
    assert set(service_bundle(tmp_path, [unit])) == {
        "deploy/new.sh", "scripts/__init__.py", "scripts/initialization.py",
        "scripts/embedded.py"}
