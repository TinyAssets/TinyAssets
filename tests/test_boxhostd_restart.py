"""Linux socket inode and authenticated registration restart regressions."""

import configparser
import os
import select
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux host sockets and mounts")


def line(process):
    result = bytearray()
    while True:
        assert select.select([process.stdout], [], [], 15)[0], "child did not report progress"
        value = os.read(process.stdout.fileno(), 1)
        assert value, f"child exited: {process.poll()}"
        if value == b"\n":
            return result.decode()
        result.extend(value)


@pytest.mark.verifies('owner-daemon-in-box:T4')
def test_preserved_runtime_keeps_both_container_bind_mounts_reachable(tmp_path):
    unit = configparser.ConfigParser(interpolation=None, strict=False)
    unit.read("deploy/boxhostd.service")
    assert unit["Service"]["RuntimeDirectoryPreserve"] == "yes"
    runtime = tmp_path / unit["Service"]["RuntimeDirectory"]
    runtime.mkdir()
    broker_mount, daemon_mount = tmp_path / "broker-run", tmp_path / "daemon-run"
    broker_mount.mkdir()
    daemon_mount.mkdir()
    compose = Path("deploy/compose.yml").read_text()
    assert "- /run/tinyassets-boxhostd:/run/tinyassets-boxhostd:ro" in compose
    assert "- /run/tinyassets-boxhostd:/run/tinyassets-boxhostd\n" in compose
    inode = runtime.stat().st_ino
    client = r'''
import socket, sys
for _ in range(2):
    print("waiting", flush=True)
    assert sys.stdin.readline().strip() == "connect"
    for root in sys.argv[1:]:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.connect(root + "/daemon.sock")
            assert sock.recv(2) == b"ok"
    print("connected", flush=True)
'''
    process = subprocess.Popen([
        "bwrap", "--die-with-parent", "--unshare-user", "--unshare-pid",
        "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev",
        "--bind", str(runtime), str(broker_mount),
        "--ro-bind", str(runtime), str(daemon_mount), sys.executable, "-c", client,
        str(broker_mount), str(daemon_mount),
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        for _ in range(2):
            assert line(process) == "waiting"
            # systemd preserves the directory; HostService.serve replaces only
            # the socket. Both existing bind mounts must see the new listener.
            path = runtime / "daemon.sock"
            path.unlink(missing_ok=True)
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path))
                listener.listen(2)
                listener.settimeout(10)
                process.stdin.write("connect\n")
                process.stdin.flush()
                for _ in range(2):
                    sock, _ = listener.accept()
                    with sock:
                        sock.sendall(b"ok")
            assert line(process) == "connected"
            assert runtime.stat().st_ino == inode
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.verifies('owner-daemon-in-box:T4')
def test_running_broker_reregisters_after_real_host_process_restart(tmp_path):
    if os.geteuid() != 0:
        pytest.skip("owner=cowork-agent runs-in=linux-oracle-as-root: needs a root host and real service UIDs")
    tmp_path.chmod(0o755)
    # /tmp/b's pytest parents are private; use a directly traversable root.
    import tempfile

    host_code = r'''
import os, threading, time
from pathlib import Path
from types import SimpleNamespace
from tinyassets.boxes.host_service import HostService
root = Path(os.environ["TEST_RUNTIME"])
service = HostService(SimpleNamespace(broker_socket=root / "broker/broker.sock"),
                      None, None, SimpleNamespace(
                          runtime=SimpleNamespace(box_id=lambda uid: str(uid)),
                          status=lambda uid: {"uid": uid, "awake": False}))
stop = threading.Event()
for name, gid, handler in (("broker-registration.sock", 1002, service.register),
                           ("daemon.sock", 1102, service.daemon)):
    threading.Thread(target=service.serve, args=(root/name, gid, handler, stop),
                     daemon=True).start()
assert service.broker_ready.wait(12)
print("registered", flush=True)
while True: time.sleep(1)
'''
    broker_code = r'''
import asyncio, os, sys
from pathlib import Path
from tinyassets.boxes import bootstrap
from tinyassets.boxes.daemon_control import request
bootstrap.RUNTIME = Path(os.environ["TEST_RUNTIME"])
os.environ["TINYASSETS_BOXES_RUN"] = str(bootstrap.RUNTIME)
async def main():
    registration = bootstrap.BrokerRegistration("x" * 64)
    registration.ready()
    print("ready", flush=True)
    stop = asyncio.Event()
    maintenance = asyncio.create_task(registration.maintain(stop, poll_seconds=.05))
    try:
        while await asyncio.to_thread(sys.stdin.readline):
            assert await asyncio.to_thread(request, "status", uid=300001) == {
                "uid": 300001, "awake": False}
            print("reachable", flush=True)
    finally:
        stop.set()
        await maintenance
asyncio.run(main())
'''
    router_code = r'''
import os
from pathlib import Path
from tinyassets.boxes import bootstrap
from tinyassets.boxes.daemon_control import broker_bootstrap
bootstrap.RUNTIME = Path(os.environ["TEST_RUNTIME"])
assert broker_bootstrap()["proof"] == "x" * 64
'''
    with tempfile.TemporaryDirectory(prefix="host-restart-") as directory:
        Path(directory).chmod(0o755)
        env = {**os.environ, "TEST_RUNTIME": directory}
        processes = []

        def launch(code, **kwargs):
            process = subprocess.Popen([sys.executable, "-c", code], env=env,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       text=True, **kwargs)
            processes.append(process)
            return process

        def host():
            process = launch(host_code)
            # Wait for the listeners, not a fixed startup delay.
            for _ in range(1000):
                if all((Path(directory) / name).exists() for name in
                       ("daemon.sock", "broker-registration.sock")):
                    return process
                assert process.poll() is None
                threading.Event().wait(.01)
            pytest.fail("host listeners unavailable")

        try:
            first = host()
            broker = launch(broker_code, user=1002, group=1002, extra_groups=[1102])
            assert line(broker) == "ready"
            assert line(first) == "registered"
            broker.stdin.write("status\n")
            broker.stdin.flush()
            assert line(broker) == "reachable"
            assert launch(router_code, user=1001, group=1001,
                          extra_groups=[1102]).wait(timeout=10) == 0
            first.terminate()
            first.wait(timeout=5)
            # The same broker and router namespace remain alive; no rebootstrap
            # of the broker proof or restart of the broker process is allowed.
            second = host()
            assert line(second) == "registered"
            assert broker.poll() is None
            broker.stdin.write("status\n")
            broker.stdin.flush()
            assert line(broker) == "reachable"
            assert launch(router_code, user=1001, group=1001,
                          extra_groups=[1102]).wait(timeout=10) == 0
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
