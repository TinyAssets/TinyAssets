"""A REAL bubblewrap proof of what a provider launch can reach, and what it cannot.

Before 2026-10-01 every provider CLI ran with bubblewrap ``--share-net``: the
daemon container's own network. Measured in production, a provider-jailed
process reached the daemon on loopback, other universes' engine MCP ports, the
cloud metadata address, the host and the log sidecar (concern
``2026-10-01-provider-jail-has-unfiltered-host-network``). The launch now has no
network interface of its own. Its only ways out are the universe's checking
egress proxy and a pinned relay to the universe's OWN engine MCP server.

Each case launches through the SHIPPING spawn point
(``tinyassets.providers.owned_process.aspawn_owned`` inside
``provider_launch_scope``, which is what the router binds around every provider
call), with the real jail, the real proxy and the real relay. Only the CLI is
replaced, by a Python probe that reports what it could reach.

The provider API host is a synthetic public name (``provider.test``) mapped to
a page on the host loopback, the same way ``tests/test_universe_tools_jail.py``
proves the tool jail's egress: the whole path runs (environment, forwarder,
socket, proxy) without a test depending on the internet. That real CLIs honour
the proxy for their API calls was measured separately against the live APIs
(claude 2.1.183 and codex 0.153.4, Linux oracle, 2026-10-01).

Linux + bwrap only; ``.github/workflows/linux-jail-proof.yml`` runs them and
fails if any is absent or skipped.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

_BWRAP = shutil.which("bwrap") if sys.platform == "linux" else None

pytestmark = [
    pytest.mark.skipif(
        sys.platform != "linux" or not _BWRAP,
        reason="a real bubblewrap jail needs Linux + bwrap",
    ),
    pytest.mark.real_jail,
]

_PROBE = r'''
import ctypes, errno, json, os, socket, sys
cfg = json.loads(sys.argv[1])
out = {}

def direct(name, host, port, family=socket.AF_INET):
    s = socket.socket(family, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        s.connect((host, port) if family != socket.AF_UNIX else host)
        out[name] = "OPEN:" + s.recv(64).decode(errors="replace")
    except Exception as exc:
        out[name] = "closed:" + type(exc).__name__
    finally:
        s.close()

def via_proxy(name, request):
    try:
        s = socket.create_connection(("127.0.0.1", 3128), timeout=20)
        s.sendall(request.encode())
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        s.close()
        out[name] = data.decode(errors="replace")
    except Exception as exc:
        out[name] = "error:" + type(exc).__name__

def connect_line(target):
    return f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n"

direct("host_loopback", "127.0.0.1", cfg["host_port"])
direct("other_engine", "127.0.0.1", cfg["other_engine_port"])
direct("own_engine", "127.0.0.1", cfg["engine_port"])
direct("metadata", "169.254.169.254", 80)
direct("abstract_unix", "\0" + cfg["abstract"], 0, socket.AF_UNIX)
if cfg.get("host_ip"):
    direct("host_ip", cfg["host_ip"], cfg["host_ip_port"])
via_proxy("api", f"GET http://provider.test:{cfg['site_port']}/v1/messages HTTP/1.1\r\n"
                 f"Host: provider.test\r\nConnection: close\r\n\r\n")
via_proxy("proxy_loopback", connect_line(f"127.0.0.1:{cfg['host_port']}"))
via_proxy("proxy_metadata", connect_line("169.254.169.254:80"))
via_proxy("proxy_smtp", connect_line("provider.test:587"))
if cfg.get("host_ip"):
    via_proxy("proxy_host_ip", connect_line(f"{cfg['host_ip']}:{cfg['host_ip_port']}"))
out["env"] = {k: v for k, v in os.environ.items() if "proxy" in k.lower()}

libc = ctypes.CDLL(None, use_errno=True)
def call(name, nr, *args):
    ctypes.set_errno(0)
    rc = libc.syscall(nr, *args)
    out["sys_" + name] = errno.errorcode.get(ctypes.get_errno(), "?") if rc == -1 else "ok"
fifo = b"/tmp/ta-probe-fifo"
for name, nr, args in cfg["syscalls"]:
    call(name, nr, *[fifo if a == "FIFO" else a for a in args])
out["limits"] = {line[:26].strip(): line[26:47].strip()
                 for line in open("/proc/self/limits") if line.startswith("Max ")}
out["fds"] = sorted(int(fd) for fd in os.listdir("/proc/self/fd"))
print(json.dumps(out))
'''

# Calls whose UNFILTERED result is not EPERM, so EPERM can only be the filter.
# x86_64 numbers; the aarch64 decisions are asserted in tests/test_jail_seccomp.py.
_SYSCALLS_X86_64 = [
    ("keyctl", 250, (0, -3)),                       # KEYCTL_GET_KEYRING_ID, session -> ok
    ("ptrace", 101, (2, 999999, 0, 0)),             # PTRACE_PEEKDATA, no such pid -> ESRCH
    ("perf_event_open", 298, (0, 0, -1, -1, 0)),    # NULL attr -> EFAULT
    ("userfaultfd", 323, (1,)),                     # UFFD_USER_MODE_ONLY -> fd
    ("io_uring_setup", 425, (0, 0)),                # 0 entries -> EINVAL
    ("setns", 308, (-1, 0)),                        # bad fd -> EBADF
    ("mknod", 133, ("FIFO", 0o010600, 0)),          # a FIFO -> ok
]


@dataclass
class _World:
    data_root: Path
    universe: Path


@pytest.fixture
def world():
    """One universe under ``/tmp`` (see tests/test_provider_universe_jail.py for
    why not ``tmp_path``)."""
    from tinyassets.providers import base

    root = Path(tempfile.mkdtemp(prefix="ta-pnet-", dir="/tmp"))
    try:
        universe = root / "data" / "u-alpha"
        universe.mkdir(parents=True)
        base._sandbox_probe_cache = None
        yield _World(root / "data", universe)
    finally:
        base._sandbox_probe_cache = None
        shutil.rmtree(root, ignore_errors=True)


def _listener(banner: bytes, family=socket.AF_INET, address=("127.0.0.1", 0)):
    """A host-side listener that greets each connection; returns (sock, stop)."""
    srv = socket.socket(family, socket.SOCK_STREAM)
    srv.bind(address)
    srv.listen(16)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.sendall(banner)
                except OSError:
                    pass

    threading.Thread(target=serve, daemon=True).start()

    def close():
        stop.set()
        srv.close()

    return srv, close


def _site():
    """The synthetic provider API: one page on the host loopback."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    hits: list[str] = []

    class Api(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's name
            hits.append(self.path)
            body = b"PROVIDER-API-REACHED"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1], hits, server.shutdown


def _host_ip() -> str | None:
    """A non-loopback address of this host (no packet is sent), or None."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if ip.startswith("127.") else ip


def _launch(world: _World, cfg: dict, *, env: dict, engine_route=None) -> dict:
    from tinyassets.providers.owned_process import aspawn_owned, kill_owned_tree
    from tinyassets.providers.provider_jail import provider_launch_scope

    python = shutil.which("python3", path="/usr/bin:/bin")
    assert python, "the probe needs a system python3"

    async def drive():
        with provider_launch_scope(world.universe, engine_route=engine_route):
            proc = await aspawn_owned(
                [python, "-I", "-c", _PROBE, json.dumps(cfg)],
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), 120)
        finally:
            kill_owned_tree(proc)
        assert proc.returncode == 0, stderr.decode(errors="replace")
        return json.loads(stdout)

    return asyncio.run(drive())


@pytest.fixture
def network(world, monkeypatch):
    """The host side: a daemon loopback port, two engine servers, the provider
    API page, an abstract unix socket and (when there is one) a host address."""
    from tinyassets import universe_egress

    real = universe_egress._checked_addresses
    monkeypatch.setattr(universe_egress, "_checked_addresses",
                        lambda host, port: ["127.0.0.1"] if host == "provider.test"
                        else real(host, port))
    closers = []
    host_srv, close = _listener(b"DAEMON-LOOPBACK")
    closers.append(close)
    own_srv, close = _listener(b"ENGINE-OWN")
    closers.append(close)
    other_srv, close = _listener(b"ENGINE-OTHER-UNIVERSE")
    closers.append(close)
    abstract = f"ta-pnet-{os.getpid()}-{id(world)}"
    _abs, close = _listener(b"ABSTRACT", socket.AF_UNIX, "\0" + abstract)
    closers.append(close)
    host_ip = _host_ip()
    host_ip_port = None
    if host_ip:
        ip_srv, close = _listener(b"HOST-ADDRESS", address=(host_ip, 0))
        closers.append(close)
        host_ip_port = ip_srv.getsockname()[1]
    site_port, hits, stop = _site()
    closers.append(stop)
    own_port = own_srv.getsockname()[1]
    # The owner-checked route read (engine_mcp_http.read_engine_mcp_route) names
    # the universe's own engine server; nothing else can be relayed to.
    routes = {("user_alpha", "u-alpha"): own_port}
    monkeypatch.setattr(universe_egress, "_route_port",
                        lambda actor_id, graph_id: routes.get((actor_id, graph_id)))
    cfg = {
        "host_port": host_srv.getsockname()[1],
        "engine_port": own_port,
        "other_engine_port": other_srv.getsockname()[1],
        "abstract": abstract,
        "host_ip": host_ip,
        "host_ip_port": host_ip_port,
        "site_port": site_port,
        "syscalls": _SYSCALLS_X86_64 if platform.machine() == "x86_64" else [],
    }
    try:
        yield cfg, hits
    finally:
        for close in closers:
            close()


_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/tmp",
    # A provider env pointing around the forwarder must not survive the jail.
    "HTTPS_PROXY": "http://evil.test:1",
    "NO_PROXY": "*",
}


def test_a_provider_reaches_its_api_only_through_the_proxy(world, network):
    from tinyassets import universe_egress

    cfg, hits = network
    out = _launch(world, cfg, env=_ENV, engine_route=("user_alpha", "u-alpha"))

    # Positive controls: the API host through the proxy, and the universe's own
    # engine server through its relay.
    assert out["api"].startswith("HTTP/1.0 200") and "PROVIDER-API-REACHED" in out["api"], out
    assert hits == ["/v1/messages"]
    assert out["own_engine"] == "OPEN:ENGINE-OWN", out
    # The jail, not the provider env, decides where the proxy is.
    for name, value in universe_egress.PROXY_ENV:
        assert out["env"].get(name) == value, (name, out["env"])

    # No direct route to anything: the daemon's loopback, another universe's
    # engine port, the metadata address, the host's own address, an abstract
    # unix socket in the daemon's network namespace.
    for name in ("host_loopback", "other_engine", "metadata", "abstract_unix"):
        assert out[name].startswith("closed:"), (name, out[name])
    if cfg["host_ip"]:
        assert out["host_ip"].startswith("closed:"), out["host_ip"]

    # And the proxy refuses the same places, plus outbound mail.
    for name in ("proxy_loopback", "proxy_metadata", "proxy_smtp"):
        assert out[name].startswith("HTTP/1.1 403") and "egress refused" in out[name], (
            name, out[name])
    if cfg["host_ip"]:
        assert out["proxy_host_ip"].startswith("HTTP/1.1 403"), out["proxy_host_ip"]


def test_without_a_granted_route_no_engine_server_is_reachable(world, network):
    cfg, _hits = network
    out = _launch(world, cfg, env=_ENV, engine_route=None)
    assert out["own_engine"].startswith("closed:"), out["own_engine"]
    assert out["other_engine"].startswith("closed:"), out["other_engine"]
    # A route for another owner of the same universe relays nothing either.
    out = _launch(world, cfg, env=_ENV, engine_route=("user_bravo", "u-alpha"))
    assert out["own_engine"].startswith("closed:"), out["own_engine"]


@pytest.mark.skipif(platform.machine() != "x86_64", reason="x86_64 syscall numbers")
def test_the_provider_jail_refuses_the_kernel_surface_and_applies_limits(world, network):
    from tinyassets.providers.provider_jail import PROVIDER_LIMITS

    cfg, _hits = network
    out = _launch(world, cfg, env=_ENV)
    for name, _nr, _args in _SYSCALLS_X86_64:
        assert out["sys_" + name] == "EPERM", (name, out["sys_" + name])
    expected = {"--nproc": "Max processes", "--nofile": "Max open files",
                "--fsize": "Max file size", "--core": "Max core file size"}
    import resource

    for flag, rlimit, value in PROVIDER_LIMITS:
        _soft, hard = resource.getrlimit(getattr(resource, rlimit))
        want = value if hard == resource.RLIM_INFINITY else min(value, hard)
        assert out["limits"][expected[flag]] == str(want), (flag, out["limits"])
    # The seccomp descriptor bubblewrap read is not left open in the CLI.
    assert out["fds"][:3] == [0, 1, 2] and len(out["fds"]) == 4, out["fds"]


def test_the_shared_spawn_point_jails_a_plain_command_adapter(world, network):
    """The router-bound shape any command adapter has: no jail code of its own,
    and still no host network. Uses a /bin/sh one-liner rather than Python, so
    nothing about the probe interpreter is load-bearing."""
    from tinyassets.providers.owned_process import aspawn_owned, kill_owned_tree
    from tinyassets.providers.provider_jail import provider_launch_scope

    cfg, _hits = network
    script = (
        f"if (exec 3<>/dev/tcp/127.0.0.1/{cfg['host_port']}) 2>/dev/null; "
        "then echo LOOPBACK-OPEN; else echo LOOPBACK-CLOSED; fi"
    )
    bash = shutil.which("bash", path="/usr/bin:/bin")

    async def drive():
        with provider_launch_scope(world.universe):
            proc = await aspawn_owned(
                [bash, "-c", script], stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=dict(_ENV),
            )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), 60)
        finally:
            kill_owned_tree(proc)
        return stdout.decode()

    assert asyncio.run(drive()).strip() == "LOOPBACK-CLOSED"
    # Control: the same one-liner outside the jail does reach the listener.
    assert subprocess.run([bash, "-c", script], capture_output=True, text=True,
                          check=False).stdout.strip() == "LOOPBACK-OPEN"
