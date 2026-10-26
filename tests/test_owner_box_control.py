"""Real root boxhostd control socket and unprivileged router client (Linux root rig)."""
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_owner_box_lifecycle import fleet  # noqa: F401
from tests.test_owner_box_roll import rolling  # noqa: F401
from tinyassets.boxes.host_service import HostService


def test_detached_runtime_does_not_wait_for_daemon_stdio(tmp_path, capfd):
    import signal

    from tinyassets.boxes.gvisor_runtime import GvisorRuntime

    executable = tmp_path / "runsc"
    pidfile = tmp_path / "daemon.pid"
    executable.write_text(
        f"#!{sys.executable}\nimport subprocess, sys\nfrom pathlib import Path\n"
        "if sys.argv[-1] == '--detach':\n"
        " print('detached runtime diagnostic', file=sys.stderr)\n"
        " child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f" Path({str(pidfile)!r}).write_text(str(child.pid))\n"
        "else:\n print('captured control output')\n")
    executable.chmod(0o755)
    runtime = object.__new__(GvisorRuntime)
    runtime.runsc, runtime.state_root = executable, tmp_path
    try:
        assert runtime._command("run", "--detach", timeout=2).returncode == 0
        assert capfd.readouterr().err == "detached runtime diagnostic\n"
        assert runtime._command("state", timeout=2).stdout == "captured control output\n"
    finally:
        if pidfile.exists():
            os.kill(int(pidfile.read_text()), signal.SIGKILL)


def test_control_socket_uses_kernel_router_identity_and_returns_bounded_errors():
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip(
            "requires linux_oracle.py --as-root; owner=c6-owner-box runs-in=linux_oracle.py"
        )
    assert os.geteuid() == 0, "run this file with linux_oracle.py --as-root"
    calls = []

    launch = {"uid": 300001, "secret": "s" * 43, "volume_generation": 1}

    def wake(owner, *, scheduled, launch):
        calls.append((owner, scheduled, launch))
        if owner == "unknown":
            raise LookupError("unadmitted")
        if owner == "broken":
            raise RuntimeError("startup failed")
        return 300001

    leases = []
    manager = SimpleNamespace(
        ensure_awake=wake,
        resolve=lambda owner, cleanup: {"uid": 300001, "lease": "l" * 43},
        release=lambda owner, token: leases.append((owner, token)),
    )
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
import json, os, sys
role = int(sys.argv[1])
record = json.loads(sys.argv[2])
os.setgroups([1001])
os.setgid(role)
os.setuid(role)
from tinyassets.boxes.daemon_control import request
if role == 1001:
    for operation, fields in (
        ("wake", {"scheduled": True, "record": record}),
        ("resolve", {"cleanup": False}),
        ("release", {"lease": "l" * 43}),
        ("destroy", {"uid": 300001}),
        ("retire", {"uid": 300001}),
    ):
        try:
            request(operation, "alice", **fields)
        except RuntimeError:
            pass
        else:
            raise AssertionError("router gained lifecycle authority: " + operation)
    sys.exit(0)
assert request("wake", "alice", scheduled=True, record=record) == {"uid": 300001}
for owner, expected, message in (
    ("unknown", LookupError, "owner has no live admission"),
    ("broken", RuntimeError, "owner box lifecycle failed: startup failed"),
):
    try:
        request("wake", owner, scheduled=False, record=record)
    except expected as error:
        assert str(error) == message
    else:
        raise AssertionError(owner)
try:
    request("wake", "alice", scheduled=False)
except RuntimeError:
    pass
else:
    raise AssertionError("unbound broker wake was accepted")
'''
            import json

            for role in (1001, 1002):
                result = subprocess.run(
                    [sys.executable, "-c", code, str(role), json.dumps(launch)],
                    capture_output=True, text=True, timeout=20,
                    env={**os.environ, "TINYASSETS_BOXES_RUN": directory})
                assert result.returncode == 0, result.stderr
                if role == 1001:
                    assert calls == []
            assert calls == [("alice", True, launch), ("unknown", False, launch),
                             ("broken", False, launch)]
            assert leases == []
        finally:
            stop.set()
            listener.join(5)
            assert not listener.is_alive()


def test_host_restart_lock_precedes_recovery_and_excludes_a_second_controller(tmp_path):
    from tinyassets.boxes.host_service import host_lock

    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip(
            "requires linux_oracle.py --as-root; owner=c6-owner-box runs-in=linux_oracle.py"
        )
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


def test_host_first_boot_accepts_runsc_null_census_and_still_reaps_bundles(tmp_path):
    from tinyassets.boxes.gvisor_runtime import GvisorRuntime
    from tinyassets.boxes.host_service import recover_daemons

    stopped = []
    (tmp_path / "owner-300001").mkdir()
    runtime = SimpleNamespace(
        box_id=GvisorRuntime.box_id, bundle_root=tmp_path, stop=stopped.append,
        _command=lambda *args: SimpleNamespace(stdout="null"))
    recover_daemons(runtime)
    assert stopped == [300001]


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


def test_image_selection_socket_pins_broker_identity_and_refuses_router(rolling):  # noqa: F811
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip(
            "requires linux_oracle.py --as-root; owner=c6-owner-box runs-in=linux_oracle.py"
        )
    assert os.geteuid() == 0, "run this file with linux_oracle.py --as-root"
    manager = rolling.manager
    uid = rolling.wake("a")
    service = HostService(None, None, None, manager)
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix="rollctl-") as directory:
        path = Path(directory)
        path.chmod(0o755)
        listener = threading.Thread(target=service.serve,
                                    args=(path / "daemon.sock", 1102, service.daemon, stop))
        listener.start()
        try:
            deadline = time.monotonic() + 5
            while not (path / "daemon.sock").exists() and time.monotonic() < deadline:
                time.sleep(.01)
            code = '''
import os, sys
from tinyassets.boxes.daemon_control import request
role, image = int(sys.argv[1]), sys.argv[2]
os.setgroups([1102])
os.setgid(role)
os.setuid(role)
if role in (0, 1001):
    for operation, fields in (("resolve", {"cleanup": False}),
                              ("release", {"lease": "l" * 43})):
        try:
            request(operation, "a", **fields)
        except RuntimeError:
            pass
        else:
            raise AssertionError("router gained lifecycle authority: " + operation)
    try:
        request("select_image", image=image)
    except RuntimeError:
        pass
    else:
        raise AssertionError("router gained deploy authority")
else:
    assert request("select_image", image=image) == {"selected": image}
    try:
        request("select_image", image=image, uid=300001)
    except RuntimeError:
        pass
    else:
        raise AssertionError("executor orchestrated owner rollout")
for operation, fields in (("roll", {"image": image}), ("drain", {"uid": 300001})):
    try:
        request(operation, **fields)
    except RuntimeError:
        pass
    else:
        raise AssertionError("retired lifecycle operation survived: " + operation)
'''
            for role in (0, 1001, 1002):
                result = subprocess.run([sys.executable, "-c", code, str(role), rolling.new],
                                        capture_output=True, text=True, timeout=15,
                                        env={**os.environ, "TINYASSETS_BOXES_RUN": directory})
                assert result.returncode == 0, result.stderr
            assert manager.boxes[uid].image == rolling.old
            assert manager.images.current == rolling.new

        finally:
            stop.set()
            listener.join(5)
            assert not listener.is_alive()


pytestmark = pytest.mark.verifies('owner-daemon-in-box:C12')
