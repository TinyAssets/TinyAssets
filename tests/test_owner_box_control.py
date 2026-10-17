"""Real root boxhostd control socket and unprivileged router client (Linux root rig)."""
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from tinyassets.boxes.host_service import HostService


def test_control_socket_uses_kernel_router_identity_and_returns_bounded_errors():
    assert os.geteuid() == 0, "run this file with linux_oracle.py --as-root"
    calls = []

    def wake(owner, *, scheduled):
        calls.append((owner, scheduled))
        if owner == "unknown":
            raise LookupError("unadmitted")
        if owner == "broken":
            raise RuntimeError("startup failed")
        return 300001

    manager = SimpleNamespace(ensure_awake=wake)
    service = HostService(None, None, None, manager)
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix="hostctl-") as directory:
        path = Path(directory)
        path.chmod(0o755)
        listener = threading.Thread(target=service.serve,
                                    args=(path / "daemon.sock", 1001, service.daemon, stop))
        listener.start()
        try:
            deadline = time.monotonic() + 5
            while not (path / "daemon.sock").exists() and time.monotonic() < deadline:
                time.sleep(.01)
            code = '''
import os
os.setgroups([])
os.setgid(1001)
os.setuid(1001)
from tinyassets.boxes.daemon_control import ensure_awake, request
assert ensure_awake("alice", scheduled=True) == 300001
for owner, expected in (("unknown", LookupError), ("broken", RuntimeError)):
    try:
        ensure_awake(owner)
    except expected:
        pass
    else:
        raise AssertionError(owner)
try:
    request("retire", "alice", uid=300001)
except RuntimeError:
    pass
else:
    raise AssertionError("router retired a box")
'''
            result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                    text=True, timeout=20,
                                    env={**os.environ, "TINYASSETS_BOXES_RUN": directory})
            assert result.returncode == 0, result.stderr
            assert calls == [("alice", True), ("unknown", False), ("broken", False)]
        finally:
            stop.set()
            listener.join(5)
            assert not listener.is_alive()


def test_host_restart_lock_precedes_recovery_and_excludes_a_second_controller(tmp_path):
    from tinyassets.boxes.host_service import host_lock

    assert os.geteuid() == 0, "run this file with linux_oracle.py --as-root"
    script = """
import sys
from pathlib import Path
from tinyassets.boxes.host_service import host_lock
try:
    with host_lock(Path(sys.argv[1])):
        raise AssertionError("second lifecycle controller acquired the host")
except BlockingIOError:
    pass
"""
    state = tmp_path / "state"
    with host_lock(state):
        result = subprocess.run([sys.executable, "-c", script, str(state)],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert (state / "service.lock").stat().st_mode & 0o777 == 0o600
    with host_lock(state):
        assert list(state.iterdir()) == [state / "service.lock"]


def test_host_restart_stops_both_runsc_and_bundle_survivors(tmp_path):
    import json

    from tinyassets.boxes.gvisor_runtime import GvisorRuntime
    from tinyassets.boxes.host_service import recover_daemons

    stopped = []
    (tmp_path / "owner-300002").mkdir()
    runtime = SimpleNamespace(
        box_id=GvisorRuntime.box_id, bundle_root=tmp_path, stop=stopped.append,
        _command=lambda *args: SimpleNamespace(stdout=json.dumps([{"id": "owner-300001"}])))
    recover_daemons(runtime)
    assert stopped == [300001, 300002]
    assert (tmp_path / "owner-300002").is_dir()


def test_host_restart_refuses_untrusted_state_before_stopping_any_box(tmp_path):
    import json

    import pytest

    from tinyassets.boxes.gvisor_runtime import GvisorRuntime
    from tinyassets.boxes.host_service import recover_daemons
    from tinyassets.boxes.provider import BoxError

    stopped = []
    runtime = SimpleNamespace(
        box_id=GvisorRuntime.box_id, bundle_root=tmp_path, stop=stopped.append,
        _command=lambda *args: SimpleNamespace(stdout=json.dumps([
            {"id": "owner-300001"}, {"id": "unexpected-300002"}])))
    with pytest.raises(ValueError):
        recover_daemons(runtime)
    assert stopped == []
    runtime._command = lambda *args: SimpleNamespace(stdout="[]")
    (tmp_path / "owner-300001").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(BoxError, match="unexpected sandbox bundle"):
        recover_daemons(runtime)
    assert stopped == []


def test_host_control_rejects_the_deleted_file_admission_operation():
    service = HostService(None, None, None, SimpleNamespace())
    result = service.lifecycle({"admit": {"owner": "alice", "center": "home"}}, 1001)
    assert set(result) == {"error"}
    assert not hasattr(service, "host")
