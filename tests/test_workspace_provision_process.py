"""Real jail lifecycle tests: no registries, credentials or production calls."""

import json
import os
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tinyassets import node_sandbox as sandbox
from tinyassets import workspace_fs
from tinyassets.workspace_provision_process import run_provision_stage
from tinyassets.workspace_registry_process import RegistryBrokerProcess


def invoke(launcher, source="print('ok')", **kwargs):
    settings = dict(timeout_s=5, storage_bound=1024 * 1024,
                    storage_usage=lambda: 0, cancelled=lambda: False)
    settings.update(kwargs)
    return run_provision_stage(launcher, source, [], **settings)


@pytest.mark.parametrize("launcher", [None, sandbox.PlainSubprocessLauncher(),
                                    sandbox.BwrapLauncher()])
def test_no_plain_or_untyped_launcher(launcher):
    with patch("subprocess.Popen") as launch:
        with pytest.raises(ValueError, match="typed bubblewrap"):
            invoke(launcher)
        launch.assert_not_called()


@pytest.fixture
def stage(tmp_path):
    if sys.platform != "linux" or not shutil.which("bwrap"):
        pytest.skip("real stage requires Linux bubblewrap")
    paths = [tmp_path / name for name in ("manifests", "cache", "checkout")]
    for path in paths:
        path.mkdir()
    fds = [os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW) for path in paths]
    def launcher(phase="install"):
        jail = sandbox.BwrapLauncher()
        if phase == "install":
            jail = jail.for_workspace(sandbox.WorkspaceMount(
                f"/proc/self/fd/{fds[2]}", pass_fds=(fds[2],)))
        return jail.for_provision(sandbox.ProvisionMount(fds[0], fds[1], phase))
    try:
        yield launcher, paths
    finally:
        for fd in fds:
            os.close(fd)


@pytest.mark.real_jail
def test_actual_offline_stage_profile_environment_and_closed_handles(stage):
    launcher, paths = stage
    source = '''
import json, os, resource, socket
from pathlib import Path
assert 'TA_PROVISION_SECRET' not in os.environ
assert resource.getrlimit(resource.RLIMIT_CORE) == (0, 0)
assert resource.getrlimit(resource.RLIMIT_AS)[0] <= 1536 * 1024 * 1024
assert resource.getrlimit(resource.RLIMIT_FSIZE)[0] <= 512 * 1024 * 1024
assert resource.getrlimit(resource.RLIMIT_NOFILE)[0] <= 1024
assert os.read(0, 1) == b''
for name in os.listdir('/proc/self/fd'):
    if int(name) > 2:
        try:
            os.fstat(int(name))
        except OSError:
            continue
        raise AssertionError('inherited mount or broker handle')
connection = socket.socket()
connection.settimeout(.1)
assert connection.connect_ex(('1.1.1.1', 443)) != 0
Path('/workspace/installed').write_text('ok')
print(json.dumps({'installed': True}))
'''
    with patch.dict(os.environ, {"TA_PROVISION_SECRET": "must-not-escape"}):
        result = invoke(launcher(), source)
    assert result.failure is None, result
    assert result.broker is None
    assert json.loads(result.stdout) == {"installed": True}
    assert (paths[2] / "installed").read_text() == "ok"


@pytest.mark.real_jail
@pytest.mark.parametrize("change", [dict(timeout_s=True), dict(timeout_s=float("nan")),
                                   dict(timeout_s=1801), dict(timeout_s=0),
                                   dict(storage_bound=False), dict(storage_bound=0)])
def test_invalid_limits_never_launch(stage, change):
    launcher, _ = stage
    with patch("subprocess.Popen") as launch, pytest.raises(ValueError):
        invoke(launcher(), **change)
    launch.assert_not_called()


@pytest.mark.real_jail
@pytest.mark.parametrize("setting, expected", [
    ({"cancelled": lambda: True}, "cancelled"),
    ({"storage_usage": lambda: -1}, "storage_measurement_failed"),
    ({"storage_usage": lambda: True}, "storage_measurement_failed"),
    ({"storage_usage": lambda: 2 * 1024 * 1024}, "storage_limit"),
])
def test_preflight_guard_prevents_execution(stage, setting, expected):
    launcher, _ = stage
    with patch("subprocess.Popen") as launch:
        assert invoke(launcher(), **setting).failure == expected
        launch.assert_not_called()


@pytest.mark.real_jail
@pytest.mark.parametrize("stream", [1, 2])
def test_output_flood_bounded_and_terminated(stage, stream):
    launcher, _ = stage
    result = invoke(launcher(), f"import os\nwhile True: os.write({stream}, b'x'*65536)")
    assert result.failure == "output_limit"
    assert len(result.stdout) + len(result.stderr) <= sandbox.MAX_WORKSPACE_OUTPUT_BYTES


@pytest.mark.real_jail
@pytest.mark.parametrize("mode", ["timeout", "cancel", "storage", "memory", "measurement"])
def test_running_stage_is_reaped_on_every_guard(stage, mode):
    launcher, paths = stage
    marker = paths[2] / "started"
    source = ("from pathlib import Path; import time; "
              "Path('/workspace/started').touch(); time.sleep(60)")
    kwargs = {"timeout_s": .7} if mode == "timeout" else {}
    if mode == "cancel":
        kwargs["cancelled"] = marker.exists
    if mode == "storage":
        kwargs["storage_usage"] = lambda: 2 * 1024 * 1024 if marker.exists() else 0
    reader = sandbox.PROCESS_TREE_RSS_READER
    def measure(pid):
        if marker.exists() and mode == "memory":
            return sandbox.WorkspaceLimits().rss_cap_bytes + 1
        if marker.exists() and mode == "measurement":
            return -1
        return reader(pid)
    with patch.object(sandbox, "PROCESS_TREE_RSS_READER", side_effect=measure):
        started = time.monotonic()
        result = invoke(launcher(), source, **kwargs)
    assert marker.exists()
    assert time.monotonic() - started < 5
    assert result.failure == {"timeout": "timeout", "cancel": "cancelled",
                              "storage": "storage_limit", "memory": "memory_limit",
                              "measurement": "memory_measurement_failed"}[mode]


@pytest.mark.real_jail
def test_nonzero_exit_is_not_success(stage):
    launcher, _ = stage
    assert invoke(launcher(), "raise SystemExit(17)").failure == "process_failed"


@pytest.mark.real_jail
@pytest.mark.parametrize("phase", ["acquire", "install"])
def test_actual_storage_growth_is_measured_and_ends_jail(stage, phase):
    launcher, paths = stage
    disk_path = paths[1] if phase == "acquire" else paths[2]
    inside = "/provision/cache" if phase == "acquire" else "/workspace"
    held = workspace_fs.open_dir_nofollow(disk_path)
    broker = RegistryBrokerProcess(max_bytes=4096) if phase == "acquire" else None
    try:
        if broker:
            broker.start()
        result = invoke(
            launcher(phase),
            f"from pathlib import Path; import time; "
            f"Path('{inside}/expanded').write_bytes(b'x' * 262144); time.sleep(60)",
            broker=broker, storage_bound=65536,
            storage_usage=lambda: workspace_fs.measure_tree_beneath(held, max_bytes=65536))
        assert result.failure == "storage_limit"
        assert (disk_path / "expanded").stat().st_size > 65536
        if broker:
            assert broker.process.poll() is not None
            assert result.broker.bytes_to_charge == 4096
    finally:
        if broker:
            broker.close()
        os.close(held)


@pytest.mark.real_jail
def test_resource_setup_failure_never_executes_install(stage):
    launcher, paths = stage
    with patch.object(sandbox, "_RLIMIT_HELPER",
                      "def _apply_rlimits(*args): return ['not applied']"):
        result = invoke(launcher(), "open('/workspace/installed', 'w').close()")
    assert result.failure == "process_failed"
    assert not (paths[2] / "installed").exists()


@pytest.mark.real_jail
def test_detached_descendant_really_started_then_dies_on_cancel(stage):
    launcher, paths = stage
    marker = "ta-provision-child-" + os.urandom(6).hex()
    source = f'''
import os, subprocess, sys, time
child = "from pathlib import Path; import time; Path('/workspace/ready').touch(); time.sleep(60)"
subprocess.run([sys.executable, '-c',
    "import subprocess,sys; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]], "
    "start_new_session=True)", child, {marker!r}])
time.sleep(60)
'''
    observed = []
    def live():
        for path in Path('/proc').glob('[0-9]*/cmdline'):
            try:
                command = path.read_bytes().split(b'\0')
            except (OSError, ProcessLookupError):
                continue
            if marker.encode() in command:
                return True
        return False
    def cancel():
        if (paths[2] / "ready").exists() and live():
            observed.append(True)
            return True
        return False
    result = invoke(launcher(), source, cancelled=cancel)
    assert observed, "prove the detached process was live before cancelling it"
    assert result.failure == "cancelled"
    deadline = time.monotonic() + 2
    while live() and time.monotonic() < deadline:
        time.sleep(.02)
    assert not live(), "detached child survived the tracked bubblewrap supervisor"


@pytest.mark.real_jail
def test_failed_acquisition_revokes_and_reaps_broker(stage):
    launcher, _ = stage
    broker = RegistryBrokerProcess(max_bytes=4096)
    broker.start()
    result = invoke(launcher("acquire"), "raise SystemExit(17)", broker=broker)
    assert result.failure == "process_failed"
    assert result.broker.bytes_to_charge == 4096
    assert broker.process.poll() is not None
    assert broker.control is None


@pytest.mark.real_jail
def test_launch_failure_reaps_started_broker(stage):
    launcher, _ = stage
    broker = RegistryBrokerProcess(max_bytes=4096)
    broker.start()
    with patch("subprocess.Popen", side_effect=OSError("private diagnostic")):
        result = invoke(launcher("acquire"), broker=broker)
    assert result.failure == "launch_or_supervision_failed"
    assert result.broker.bytes_to_charge == 4096
    assert result.stdout == result.stderr == b""
    assert broker.process.poll() is not None


@pytest.mark.real_jail
@pytest.mark.parametrize("failure", ["output", "storage", "cancel"])
def test_late_guard_failure_keeps_full_transfer_charge(stage, failure):
    launcher, _ = stage
    broker = RegistryBrokerProcess(max_bytes=4096)
    broker.start()
    calls = []
    def measure():
        calls.append(1)
        return 2 * 1024 * 1024 if failure == 'storage' and len(calls) > 1 else 0
    source = "print('x' * (1024*1024+1))" if failure == 'output' else "print('ok')"
    result = invoke(launcher("acquire"), source, broker=broker, storage_usage=measure,
                    cancelled=lambda: failure == 'cancel')
    assert result.failure in ('output_limit', 'storage_limit', 'cancelled')
    assert result.broker.bytes_to_charge == 4096


@pytest.mark.real_jail
def test_broker_cannot_enter_offline_stage(stage):
    launcher, _ = stage
    broker = RegistryBrokerProcess(max_bytes=4096)
    with patch("subprocess.Popen") as launch, pytest.raises(ValueError, match="only acquisition"):
        invoke(launcher(), broker=broker)
    launch.assert_not_called()


@pytest.mark.real_jail
def test_acquisition_requires_started_broker(stage):
    launcher, _ = stage
    with pytest.raises(ValueError, match="only acquisition"):
        invoke(launcher("acquire"))
    with pytest.raises(ValueError, match="started registry"):
        invoke(launcher("acquire"), broker=RegistryBrokerProcess(max_bytes=4096))


@pytest.mark.real_jail
def test_broker_finalization_cannot_outlive_stage_deadline(stage):
    launcher, _ = stage
    broker = RegistryBrokerProcess(max_bytes=4096, timeout_s=60)
    broker.start()
    # Broker waits for control EOF; hold an extra *test-owned* copy so the
    # caller's stage deadline, not the 60s broker timeout, must end it.
    duplicate = broker.control.dup()
    try:
        started = time.monotonic()
        result = invoke(launcher("acquire"), broker=broker, timeout_s=.7)
        assert time.monotonic() - started < 5
        assert result.failure == "registry_failed"
        assert result.broker.failure == "timeout"
        assert result.broker.bytes_to_charge == 4096
        assert broker.process.poll() is not None
    finally:
        duplicate.close()
        broker.close()
