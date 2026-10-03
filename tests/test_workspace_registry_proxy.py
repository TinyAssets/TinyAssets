"""Real namespace relay proof; synthetic upstream bytes, not package/TLS proof."""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from tinyassets import workspace_registry as registry
from tinyassets import workspace_registry_proxy as proxy
from tinyassets.node_sandbox import BwrapLauncher


def test_bad_limits_refuse_without_starting_listener():
    # No listener is opened by these portable constructor checks.
    with socket.socket() as wrong:
        error = NotImplementedError if os.name != "posix" else ValueError
        with pytest.raises(error, match="Unix"):
            proxy.NamespaceRegistryProxy(wrong, timeout_s=1)


linux_jail = pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("bwrap"),
    reason="namespace registry relay requires Linux bubblewrap and SCM_RIGHTS",
)


@pytest.mark.skipif(os.name != "posix", reason="Unix packet sockets require POSIX")
@pytest.mark.parametrize("settings", [
    {"timeout_s": 0}, {"timeout_s": float("inf")}, {"timeout_s": float("nan")},
    {"timeout_s": True}, {"timeout_s": "1"}, {"timeout_s": 1, "max_active": 0},
    {"timeout_s": 1, "max_connections": False},
])
def test_invalid_budgets_refuse_before_socket_ownership(settings):
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        with pytest.raises(ValueError):
            proxy.NamespaceRegistryProxy(child, **settings)
        assert child.fileno() >= 0
    finally:
        parent.close()
        child.close()


@pytest.mark.skipif(os.name != "posix", reason="Unix packet sockets require POSIX")
def test_failed_thread_start_closes_cleanly_without_joining_an_unstarted_thread():
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    manager = proxy.NamespaceRegistryProxy(child, timeout_s=1)
    try:
        with patch.object(proxy.socket, "socket") as listener:
            with patch.object(proxy.threading.Thread, "start", side_effect=RuntimeError("test")):
                with pytest.raises(RuntimeError, match="test"):
                    manager.start()
            listener.return_value.close.assert_called_once()
        manager.close()
        assert child.fileno() == -1
        assert manager.failure == "transport_failed"
        with pytest.raises(ValueError, match="single-use"):
            manager.start()
    finally:
        manager.close()
        parent.close()


def launch_child(tail, child_control):
    # The platform ships this fixed stdlib-only source, not a user file path.
    source = Path(proxy.__file__).read_text(encoding="utf-8") + "\n" + tail
    launcher = BwrapLauncher()
    return subprocess.Popen(
        launcher.build_argv(source, []), stdin=child_control,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
        env=launcher.env("/tmp"), text=True,
    )


@pytest.mark.real_jail
@linux_jail
@pytest.mark.parametrize("destination", ["pypi.org", "127.0.0.1"])
def test_namespace_hands_only_unix_relay_to_real_registry_broker(destination):
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    parent.settimeout(8)
    process = None
    upstream, remote = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    observed = []

    def echo_upstream():
        remote.settimeout(8)
        chunks = []
        try:
            while chunk := remote.recv(8192):
                chunks.append(chunk)
            data = b"".join(chunks)
            observed.append(data)
            remote.sendall(data)
        except OSError:
            pass
        finally:
            remote.close()

    echo = threading.Thread(target=echo_upstream, daemon=True)
    tail = r'''
import json, os, stat, subprocess, sys
manager = NamespaceRegistryProxy(socket.socket(fileno=0), timeout_s=6)
try:
    manager.start()
    direct = socket.socket()
    direct.settimeout(0.1)
    assert direct.connect_ex(('1.1.1.1', 443)) != 0
    direct.close()
    # The package child gets pipes/devnull, not proxy or parent-control sockets.
    probe = "import os,stat; assert stat.S_ISCHR(os.fstat(0).st_mode); "
    probe += "assert all(int(n)<=2 or not os.path.exists('/proc/self/fd/'+n) "
    probe += "for n in os.listdir('/proc/self/fd'))"
    checked = subprocess.run([sys.executable, '-I', '-c', probe],
        stdin=subprocess.DEVNULL, close_fds=True, capture_output=True, text=True, timeout=2)
    assert checked.returncode == 0, checked.stderr
    client = socket.create_connection(('127.0.0.1',3128), timeout=4)
    destination = __DESTINATION__
    request = f'CONNECT {destination}:443 HTTP/1.1\r\nHost: {destination}:443\r\n\r\n'
    client.sendall(request.encode())
    header = bytearray()
    while not header.endswith(b'\r\n\r\n'):
        part = client.recv(1)
        assert part
        header.extend(part)
    if destination == 'pypi.org':
        assert bytes(header).startswith(b'HTTP/1.1 200'), header
        payload = b'opaque registry bytes ' * 25000
        client.sendall(payload)
        client.shutdown(socket.SHUT_WR)
        received = bytearray()
        while part := client.recv(8192):
            received.extend(part)
        assert bytes(received) == payload
    else:
        assert bytes(header).startswith(b'HTTP/1.1 502'), header
    client.close()
    print(json.dumps({'destination':destination, 'roundtrip':True}))
finally:
    manager.close()
'''.replace("__DESTINATION__", repr(destination))
    try:
        process = launch_child(tail, child)
        child.close()
        admitted = registry.receive_relay(parent)
        assert admitted is not None
        assert admitted.family == socket.AF_UNIX
        assert admitted.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_STREAM
        budget = registry.TransferBudget(max_bytes=2_000_000, max_connections=2,
                                         max_active=1, timeout_s=8)
        echo.start()
        with patch.object(registry, "_connect_pinned", return_value=upstream) as dial:
            registry.serve_registry_tunnel(
                admitted, budget, resolver=lambda _host, _port: ["93.184.216.34"])
        output, errors = process.communicate(timeout=8)
        assert process.returncode == 0, errors
        assert json.loads(output) == {"destination": destination, "roundtrip": True}
        if destination == "pypi.org":
            assert dial.call_count == 1
            assert budget.snapshot().failure is None
            echo.join(timeout=1)
            assert observed == [b"opaque registry bytes " * 25000]
        else:
            dial.assert_not_called()
            assert budget.snapshot().failure == "bad_connect"
    finally:
        parent.close()
        child.close()
        upstream.close()
        remote.close()
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        if echo.ident is not None:
            echo.join(timeout=1)


@pytest.mark.real_jail
@linux_jail
@pytest.mark.parametrize("mode", ["timeout", "broker_closed", "connection_limit"])
def test_proxy_stops_on_deadline_revocation_and_connection_limit(mode):
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    parent.settimeout(5)
    process = None
    admitted = None
    tail = r'''
import json
mode = __MODE__
manager = NamespaceRegistryProxy(socket.socket(fileno=0), timeout_s=0.25 if mode=='timeout' else 4,
                                  max_connections=1, max_active=1)
clients = []
try:
    manager.start()
    if mode == 'connection_limit':
        clients.append(socket.create_connection(('127.0.0.1',3128),timeout=1))
        clients.append(socket.create_connection(('127.0.0.1',3128),timeout=1))
    deadline = time.monotonic()+5
    while manager.failure is None and time.monotonic()<deadline:
        time.sleep(0.01)
    assert manager.failure == mode, manager.failure
    print(json.dumps({'failure':manager.failure}))
finally:
    manager.close()
    for client in clients:
        client.close()
'''.replace("__MODE__", repr(mode))
    try:
        process = launch_child(tail, child)
        child.close()
        if mode == "broker_closed":
            parent.close()
        if mode == "connection_limit":
            admitted = registry.receive_relay(parent)
            assert admitted is not None
        output, errors = process.communicate(timeout=8)
        assert process.returncode == 0, errors
        assert json.loads(output) == {"failure": mode}
    finally:
        if admitted is not None:
            admitted.close()
        parent.close()
        child.close()
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
