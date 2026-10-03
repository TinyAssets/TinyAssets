"""The tool jail's only way out: a proxy that connects only to public addresses.

Change ``universe-agent-harness``, slice S3. The jail keeps its empty network
namespace; ``bash`` reaches the internet only through one unix socket served by
:mod:`tinyassets.universe_egress`, which resolves every destination itself and
refuses anything that is not globally routable. The real-jail proofs are in
``tests/test_universe_tools_jail.py``.
"""

from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path

import pytest

from tinyassets import universe_egress as egress

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="unix sockets and O_NOFOLLOW dirs (Linux hosts)",
)


# --- parsing --------------------------------------------------------------------


def test_connect_opens_a_tunnel():
    host, port, first = egress._destination(b"CONNECT pypi.org:443 HTTP/1.1\r\nHost: x\r\n\r\n")
    assert (host, port, first) == ("pypi.org", 443, None)


def test_an_absolute_http_request_is_forwarded_in_origin_form():
    head = b"GET http://example.com:8080/a?b=1 HTTP/1.1\r\nHost: example.com\r\n\r\n"
    host, port, first = egress._destination(head)
    assert (host, port) == ("example.com", 8080)
    assert first.startswith(b"GET /a?b=1 HTTP/1.1\r\nHost: example.com")


@pytest.mark.parametrize("head", [
    b"GET https://example.com/ HTTP/1.1\r\n\r\n",
    b"GET /relative HTTP/1.1\r\n\r\n",
    b"CONNECT nohost HTTP/1.1\r\n\r\n",
    b"\xff\xfe garbage\r\n\r\n",
])
def test_anything_else_is_refused(head):
    with pytest.raises(egress.EgressRefused):
        egress._destination(head)


# --- the floor ------------------------------------------------------------------


@pytest.mark.parametrize("address", [
    "127.0.0.1",          # the host itself
    "10.0.0.5",           # the container network
    "172.18.0.2",         # a docker bridge peer
    "169.254.169.254",    # cloud metadata
    "100.64.0.1",         # CGNAT
    "::1",
    "fd00::1",            # ULA
    "64:ff9b::a00:5",     # NAT64-wrapped 10.0.0.5
    "::ffff:10.0.0.5",    # v4-mapped private
])
def test_non_public_destinations_are_refused(address):
    with pytest.raises(egress.EgressRefused):
        egress._checked_addresses(address, 443)


def test_outbound_mail_is_refused_even_to_a_public_address():
    with pytest.raises(egress.EgressRefused, match="outbound mail"):
        egress._checked_addresses("1.1.1.1", 25)


def test_a_public_address_is_allowed():
    assert egress._checked_addresses("1.1.1.1", 443) == ["1.1.1.1"]


# --- the proxy, end to end on this host ------------------------------------------


@pytest.fixture
def upstream():
    """A plain TCP echo server on the host loopback, standing in for a site."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    seen = []

    def run():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            seen.append(True)
            data = conn.recv(1024)
            conn.sendall(b"echo:" + data)
            conn.close()

    threading.Thread(target=run, daemon=True).start()
    yield server.getsockname()[1], seen
    server.close()


def _proxy(tmp_path: Path, monkeypatch, *, allow: str | None = None) -> Path:
    universe = Path(tmp_path) / "u-one"
    universe.mkdir()
    if allow is not None:
        real = egress._checked_addresses

        def checked(host, port):
            return ["127.0.0.1"] if host == allow else real(host, port)

        monkeypatch.setattr(egress, "_checked_addresses", checked)
    path = egress.ensure_proxy(universe)
    assert path is not None
    assert path.parent == Path(tmp_path) / egress.UNIVERSE_SIDECARS_DIR / "u-one"
    return path


def _ask(path: Path, request: bytes) -> bytes:
    client = socket.socket(socket.AF_UNIX)
    client.settimeout(10)
    client.connect(str(path))
    client.sendall(request)
    return client, client.recv(4096)


@posix_only
def test_a_checked_destination_is_tunnelled(tmp_path, monkeypatch, upstream):
    port, seen = upstream
    path = _proxy(tmp_path, monkeypatch, allow="public.test")
    client, reply = _ask(path, f"CONNECT public.test:{port} HTTP/1.1\r\n\r\n".encode())
    assert reply.startswith(b"HTTP/1.1 200")
    client.sendall(b"hello")
    assert client.recv(4096) == b"echo:hello"
    client.close()
    assert seen


@posix_only
def test_a_refusal_says_why_and_never_connects(tmp_path, monkeypatch, upstream):
    port, seen = upstream
    path = _proxy(tmp_path, monkeypatch)
    client, reply = _ask(path, f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\n\r\n".encode())
    client.close()
    assert reply.startswith(b"HTTP/1.1 403")
    assert b"egress refused" in reply and b"not globally routable" in reply
    assert not seen


@posix_only
def test_the_socket_lives_outside_every_universe_folder(tmp_path, monkeypatch):
    """A workflow provider jail binds the universe read-write, and bubblewrap
    resolves a bind source again at launch, so a socket inside the universe
    could be swapped for a link to another universe (gpt-6-astra refute)."""
    path = _proxy(tmp_path, monkeypatch)
    assert not path.is_relative_to(tmp_path / "u-one")


def test_the_jail_binds_its_own_sidecar_and_nothing_else_outside(tmp_path):
    from tinyassets.providers import provider_jail as jail

    universe = tmp_path / "u-one"
    universe.mkdir()
    own = tmp_path / jail.UNIVERSE_SIDECARS_DIR / "u-one"
    other = tmp_path / jail.UNIVERSE_SIDECARS_DIR / "u-two"
    own.mkdir(parents=True)
    other.mkdir(parents=True)
    ok = jail.UniverseView(universe, (jail.JailMount("bind", "/tmp/s", own),))
    assert jail._validated_view(ok).mounts[0].source == own.resolve()
    for source in (other, tmp_path):
        bad = jail.UniverseView(universe, (jail.JailMount("bind", "/tmp/s", source),))
        with pytest.raises(jail.ProviderConfinementError):
            jail._validated_view(bad)


@posix_only
def test_a_trickled_head_is_cut_off_at_one_deadline(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(egress, "_HEAD_TIMEOUT_S", 0.5)
    path = _proxy(tmp_path, monkeypatch)
    client = socket.socket(socket.AF_UNIX)
    client.settimeout(10)
    client.connect(str(path))
    started = time.monotonic()
    for _ in range(8):  # one byte every 0.2 s: each recv is quick, the head never ends
        try:
            client.sendall(b"C")
        except OSError:
            break
        time.sleep(0.2)
    reply = client.recv(4096)
    client.close()
    assert b"took too long" in reply
    assert time.monotonic() - started < 5


@posix_only
def test_a_new_proxy_generation_shares_the_universe_budget(tmp_path, monkeypatch):
    first = _proxy(tmp_path, monkeypatch)
    budget = egress._UNIVERSE_SLOTS["u-one"]
    first.unlink()
    egress.ensure_proxy(tmp_path / "u-one")
    assert egress._UNIVERSE_SLOTS["u-one"] is budget


@posix_only
def test_a_replaced_socket_is_served_again(tmp_path, monkeypatch):
    path = _proxy(tmp_path, monkeypatch)
    path.unlink()
    again = egress.ensure_proxy(tmp_path / "u-one")
    assert again == path and path.is_socket()


# --- the provider jail's relay to its universe's own engine server ---------------


@pytest.fixture
def short_root():
    """Engine relay sockets carry an owner tag; keep the path well under 108 bytes."""
    import shutil
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="ta-er-", dir="/tmp"))
    (root / "u-one").mkdir()
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def routes(monkeypatch):
    """The owner-checked route read, with a table the test controls."""
    from tinyassets import engine_mcp_http

    table: dict = {}

    def read(*, actor_id, graph_id, root=None):
        port = table.get((actor_id, graph_id))
        if port is None:
            return None
        return engine_mcp_http.EngineMcpRoute(
            actor_id, graph_id, f"http://127.0.0.1:{port}/mcp", "s" * 32,
        )

    monkeypatch.setattr(engine_mcp_http, "read_engine_mcp_route", read)
    return table


@posix_only
def test_the_engine_relay_reaches_only_the_route_its_owner_holds(short_root, routes, upstream):
    port, seen = upstream
    routes[("owner", "u-one")] = port
    universe = short_root / "u-one"
    assert egress.ensure_engine_relay(universe, actor_id="stranger", graph_id="u-one") is None
    path, relayed_port = egress.ensure_engine_relay(universe, actor_id="owner", graph_id="u-one")
    assert relayed_port == port
    assert path.parent == short_root / egress.UNIVERSE_SIDECARS_DIR / "u-one"
    client = socket.socket(socket.AF_UNIX)
    client.settimeout(10)
    client.connect(str(path))
    client.sendall(b"hello")
    assert client.recv(4096) == b"echo:hello"
    client.close()
    assert seen == [True]


@posix_only
def test_the_engine_relay_rereads_the_route_for_every_connection(short_root, routes, upstream):
    """A revoked route relays nothing, even through a relay that already exists."""
    port, seen = upstream
    routes[("owner", "u-one")] = port
    path, _ = egress.ensure_engine_relay(short_root / "u-one", actor_id="owner", graph_id="u-one")
    routes.clear()
    client = socket.socket(socket.AF_UNIX)
    client.settimeout(10)
    client.connect(str(path))
    client.sendall(b"hello")
    try:
        reply = client.recv(4096)
    except ConnectionResetError:  # closed with our bytes unread: also nothing relayed
        reply = b""
    assert reply == b""
    client.close()
    assert seen == []
