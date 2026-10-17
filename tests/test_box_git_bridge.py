"""In-box git routes use real broker smart HTTP and never hold a credential."""
import socket
from urllib.parse import urlsplit

import pytest

from tests.support.synthetic_git import synthetic
from tinyassets.boxes import git_routes
from tinyassets.broker.client import BrokerClient
from tinyassets.owner_git import environment, routes


@pytest.fixture
def git_server(tmp_path, monkeypatch):
    yield from synthetic.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("claimed_owner", ["alice", "bob"])
def test_git_frames_use_owner_bound_channel_without_guest_credential(git_server, claimed_owner):
    s = git_server
    client = BrokerClient(s.path, principal=claimed_owner, command_center="center",
                          fence=lambda: s.generation)
    projected = environment([s.record], "developer")
    assert set(projected) == {"TINYASSETS_BOX_GIT"}
    assert s.secret not in projected["TINYASSETS_BOX_GIT"]
    assert "credential_ref" not in projected["TINYASSETS_BOX_GIT"]
    assert "principal" not in projected["TINYASSETS_BOX_GIT"]
    assert not git_routes._routes
    with routes(client, [s.record], "developer") as env:
        assert env["GIT_CONFIG_COUNT"] == "1"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert s.secret not in str(env)
        key = env["GIT_CONFIG_KEY_0"]
        assert key.startswith("url.http://ta-git.invalid/")
        assert key.endswith(".insteadOf")
        assert env["GIT_CONFIG_VALUE_0"] == "https://git.test/owner/repo.git"
        target = key[len("url."):-len(".insteadOf")]
        proxy = urlsplit(env["http_proxy"])
        assert proxy.hostname == "127.0.0.1"
        with socket.create_connection((proxy.hostname, proxy.port), timeout=5) as connection:
            connection.sendall((f"GET {target}/info/refs?service=git-upload-pack HTTP/1.1\r\n"
                                "Host: ta-git.invalid\r\nConnection: close\r\n\r\n").encode())
            response = bytearray()
            while block := connection.recv(65536):
                response.extend(block)
        assert s.secret.encode() not in response
        if claimed_owner == "alice":
            assert response.startswith(b"HTTP/1.1 200"), response
            assert s.state["seen"] == [("/owner/repo.git/info/refs?service=git-upload-pack", True)]
        else:
            assert response.startswith(b"HTTP/1.1 403"), response
            assert not s.state["seen"]
    assert not git_routes._routes
