"""Identity allocation is a fenced daemon operation, never a numeric-ID RPC."""
import os
import socket

import pytest

from tests.test_broker_server import broker  # noqa: F401 - real Unix server fixture
from tinyassets import rpc_frames as rf
from tinyassets.broker.owner_identities import OwnerIdentities

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Unix identity broker")


def request(broker, **changes):  # noqa: F811 - fixture passed as a helper argument
    document = {"op": "OWNER_IDENTITY", "principal": "alice", "allocate": True,
                "generation": broker.state["generation"], "token": broker.state["token"]}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(5)
        channel.connect(str(broker.path))
        channel.sendall(rf.control(rf.CONNECTION, document | changes))
        return rf.read_frame_blocking(channel).control()


def test_real_ipc_allocation_replay_and_missing_store(broker, tmp_path):  # noqa: F811
    assert request(broker) == {"op": "OWNER_IDENTITY_REFUSED"}
    tmp_path.chmod(0o700)
    identities = OwnerIdentities(tmp_path / "identities.db", initialize=True)
    broker.server._owner_identities = identities
    first = request(broker)
    assert first == {"op": "OWNER_IDENTITY_IS", "uid": 300001, "gid": 300001}
    assert request(broker, allocate=False) == first
    assert request(broker) == first  # lost acknowledgement is safe to retry explicitly
    assert request(broker, principal="bob")["uid"] == 300002
    assert identities.resolve("alice").uid == 300001


def test_bad_authority_and_numeric_or_path_override_do_not_allocate(broker, tmp_path):  # noqa: F811
    tmp_path.chmod(0o700)
    identities = OwnerIdentities(tmp_path / "identities.db", initialize=True)
    broker.server._owner_identities = identities
    for changes in ({"token": "stale"}, {"generation": True}, {"principal": ""},
                    {"allocate": 1}, {"uid": 1002}, {"gid": 1001},
                    {"path": "/data/foreign"}, {"allocate": False}):
        assert request(broker, **changes) == {"op": "OWNER_IDENTITY_REFUSED"}
    with pytest.raises(LookupError):
        identities.resolve("alice")
    assert identities.resolve("bob", allocate=True).uid == 300001
