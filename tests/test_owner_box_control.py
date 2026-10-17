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
    service = HostService(None, None, None, None, manager)
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
