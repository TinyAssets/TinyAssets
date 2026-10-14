"""Real local smart HTTP and broker frames; no gVisor isolation claim."""

import os
import select
import socket
import sqlite3
import threading

import pytest

from tests.test_agent_git_credentials import synthetic
from tinyassets import git_egress
from tinyassets import rpc_frames as rf
from tinyassets.boxes import git_bridge
from tinyassets.broker.client import BrokerClient
from tinyassets.broker.owner_identities import OwnerIdentities
from tinyassets.broker.server import BOX_HOST


@pytest.fixture
def git_server(tmp_path, monkeypatch):
    yield from synthetic.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("claimed_owner", ["alice", "bob"])
def test_git_frames_use_owner_bound_channel_without_guest_credential(git_server, claimed_owner):
    s = git_server
    identities = OwnerIdentities(s.tmp_path / "identities.db", initialize=True)
    uid = identities.resolve("alice", allocate=True).uid
    identities.admission("admit", "alice", "center")
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT, machine INT, "
                   "project_id INT, bhard_bytes INT)")
        db.execute("INSERT INTO box_records VALUES (?,?,?,?)", ("alice", uid, uid, 1024**3))
    s.server._owner_identities = identities
    s.server._roles = {os.getuid(): BOX_HOST}
    relay_path = s.tmp_path / "relay.sock"
    errors = []
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(relay_path))
        listener.listen(1)
        listener.settimeout(5)

        def relay():
            # hermetic-ok: disposable local AF_UNIX protocol fixture, not remote I/O.
            try:
                guest, _ = listener.accept()
                with guest, socket.socket(socket.AF_UNIX) as broker:
                    guest.settimeout(5)
                    assert rf.read_frame_blocking(guest).control() == {
                        "op": "BOX_CONNECT", "center": "center"}
                    broker.connect(str(s.path))
                    broker.sendall(rf.control(0, {
                        "op": "BOX_CHANNEL", "principal": "alice", "center": "center"}))
                    assert rf.read_frame_blocking(broker).control()["op"] == "BOX_ADMITTED"
                    guest.sendall(rf.control(0, {"op": "BOX_CONNECTED"}))
                    while True:
                        ready, _, _ = select.select([guest, broker], [], [], 5)
                        assert ready
                        for source in ready:
                            data = source.recv(65536)
                            if not data:
                                return
                            (broker if source is guest else guest).sendall(data)
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=relay)
        thread.start()
        s.monkeypatch.setattr(git_bridge, "broker_client", lambda path, **kwargs:
                             BrokerClient(relay_path, **kwargs))
        for key, value in git_bridge.environment([s.record], "developer").items():
            assert s.secret not in value
            s.monkeypatch.setenv(key, value)
        s.monkeypatch.setenv("TINYASSETS_BOX_OWNER", claimed_owner)
        s.monkeypatch.setenv("TINYASSETS_BOX_CENTER", "center")
        # Isolate the guest's git config projection from this pytest process.
        s.monkeypatch.setattr(git_bridge.os, "environ", dict(os.environ))
        context, proxy = git_bridge.prepare()
        try:
            route = next(key[1] for key in git_egress._routes if key[0] == id(proxy))
            raw = (f"GET http://ta-git.invalid/{route}/owner/repo.git/info/refs?"
                   "service=git-upload-pack HTTP/1.1\r\nHost: ta-git.invalid\r\n\r\n").encode()
            left, right = socket.socketpair()
            with left, right:
                assert git_egress.serve(proxy, left, raw)
                response = right.recv(65536)
            assert s.secret.encode() not in response
            if claimed_owner == "alice":
                assert response.startswith(b"HTTP/1.1 200")
                assert s.state["seen"] == [
                    ("/owner/repo.git/info/refs?service=git-upload-pack", True)]
            else:
                assert response.startswith(b"HTTP/1.1 403")
                assert not s.state["seen"]
        finally:
            context.__exit__(None, None, None)
            thread.join(6)
        assert not thread.is_alive()
        assert not errors
        assert not any(key[0] == id(proxy) for key in git_egress._routes)


def test_cli_forwarder_without_git_stays_stdlib_only(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from tinyassets.boxes.git_bridge import prepare; "
            "assert prepare() == (None, None); "
            "assert 'tinyassets.broker.client' not in sys.modules")
    result = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(root)],
                            capture_output=True, timeout=10,
                            env={key: value for key, value in os.environ.items()
                                 if key != git_bridge.ENV})
    assert result.returncode == 0, result.stderr.decode()
