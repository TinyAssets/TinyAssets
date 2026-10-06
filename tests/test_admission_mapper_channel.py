"""DA2: the mapper's inherited read-only broker pair, both ends, real sockets."""
import array
import json
import os
import runpy
import socket
import threading
from pathlib import Path

import pytest

from tinyassets.broker import mapper_channel
from tinyassets.broker.owner_identities import OwnerIdentities

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Unix credentials")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def identities(tmp_path):
    tmp_path.chmod(0o700)
    store = OwnerIdentities(tmp_path / "identities.db", initialize=True)
    store.resolve("alice", allocate=True)
    store.admission("admit", "alice", "alice-home")
    return store


def make_channel(broker_end, identities):
    """The test process is the mapper; as container PID 1 (root oracle) the
    production pid guard (a mapper is never PID 1) is bypassed, nothing else."""
    if os.getpid() > 1:
        return mapper_channel.MapperChannel(broker_end, os.getpid(), identities)
    channel = object.__new__(mapper_channel.MapperChannel)
    channel._channel, channel._pid = broker_end, os.getpid()
    channel._pidfd, channel._identities = os.pidfd_open(os.getpid()), identities
    broker_end.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    return channel


@pytest.fixture
def pair(monkeypatch, identities):
    """The test process plays the mapper; its own uid stands in for host 300000."""
    if os.getuid() != os.getgid():
        pytest.skip("credential stand-in needs uid == gid")
    monkeypatch.setattr(mapper_channel, "MAPPER_HOST_ID", os.getuid())
    broker_end, mapper_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    channel = make_channel(broker_end, identities)
    mapper_end.settimeout(5)
    yield channel, mapper_end
    mapper_end.close()
    channel.close()


def ask(channel, mapper_end, document, *, fds=()):
    ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))] if fds else []
    mapper_end.sendmsg([json.dumps(document).encode()], ancillary)
    served = channel.serve_one()
    return served, json.loads(mapper_end.recv(4096))


def test_three_read_only_ops(pair, identities):
    channel, mapper_end = pair
    assert ask(channel, mapper_end, {"op": "OWNER_MACHINE", "principal": "alice"}) == (
        True, {"op": "OWNER_MACHINE_IS", "machine": 300001})
    assert ask(channel, mapper_end, {"op": "OWNER_MACHINE", "principal": "bob"}) == (
        True, {"op": "ABSENT"})
    assert ask(channel, mapper_end, {"op": "CENTER_STATE", "center": "alice-home"}) == (
        True, {"op": "CENTER_STATE_IS", "state": "admitted"})
    assert ask(channel, mapper_end, {"op": "ADMISSION_ROW", "generation": 1}) == (
        True, {"op": "ADMISSION_ROW_IS", "generation": 1, "event": "admit",
               "principal": "alice", "center": "alice-home", "machine": 300001})
    assert ask(channel, mapper_end, {"op": "ADMISSION_ROW", "generation": 2}) == (
        True, {"op": "ABSENT"})
    with pytest.raises(LookupError):
        identities.resolve("bob")  # OWNER_MACHINE never allocates


@pytest.mark.parametrize("document", [
    {"op": "OWNER_IDENTITY", "principal": "alice", "allocate": True},
    {"op": "CENTER_ADMISSION", "event": "admit", "principal": "alice", "center": "x"},
    {"op": "OWNER_MACHINE", "principal": "alice", "allocate": True},
    {"op": "CENTER_STATE", "center": "alice-home", "path": "/data/alice-home"},
    {"op": "ADMISSION_ROW", "generation": "1"},
    {"op": "ADMISSION_ROW", "generation": True},
    {"op": "CENTER_STATE", "center": "../escape"},
    ["OWNER_MACHINE", "alice"],
])
def test_unknown_op_or_field_refuses_and_closes(pair, identities, document):
    channel, mapper_end = pair
    assert ask(channel, mapper_end, document) == (False, {"op": "REFUSED"})
    assert mapper_end.recv(4096) == b""  # closed: no later op is served
    assert identities.admissions_after(0)[-1].generation == 1


def test_descriptor_refuses_and_closes(pair):
    channel, mapper_end = pair
    left, right = socket.socketpair()
    with left, right:
        assert ask(channel, mapper_end, {"op": "CENTER_STATE", "center": "alice-home"},
                   fds=[right.fileno()]) == (False, {"op": "REFUSED"})


def test_non_mapper_sender_refuses_even_holding_the_endpoint(pair):
    channel, mapper_end = pair
    child = os.fork()
    if child == 0:
        try:
            mapper_end.sendall(json.dumps({"op": "CENTER_STATE",
                                           "center": "alice-home"}).encode())
        finally:
            os._exit(0)
    assert os.waitpid(child, 0)[1] == 0
    assert channel.serve_one() is False
    assert json.loads(mapper_end.recv(4096)) == {"op": "REFUSED"}


def test_missing_map_refuses(monkeypatch):
    if os.getuid() != os.getgid():
        pytest.skip("credential stand-in needs uid == gid")
    monkeypatch.setattr(mapper_channel, "MAPPER_HOST_ID", os.getuid())
    broker_end, mapper_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    channel = make_channel(broker_end, None)
    with mapper_end:
        mapper_end.settimeout(5)
        assert ask(channel, mapper_end, {"op": "CENTER_STATE", "center": "a"}) == (
            False, {"op": "REFUSED"})


def test_channel_must_be_an_unnamed_seqpacket_pair(identities):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        with pytest.raises(PermissionError):
            mapper_channel.MapperChannel(stream, os.getpid() + (os.getpid() == 1), identities)
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    with left, right:
        for pid in (1, 0, True):  # PID1 is the daemon, never the mapper
            with pytest.raises(PermissionError):
                mapper_channel.MapperChannel(left, pid, identities)


def mapper(broker, broker_pid):
    module = runpy.run_path(str(ROOT / "deploy/role_owner_launcher.py"))
    value = object.__new__(module["OwnerLauncher"])
    value.broker, value.broker_pid = broker, broker_pid
    value.overflow_uid, value.overflow_gid = os.getuid(), os.getgid()
    broker.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    broker.settimeout(5)
    return value


def test_mapper_reads_are_typed_and_authenticated(pair, identities):
    channel, mapper_end = pair
    thread = threading.Thread(target=channel.serve_forever, daemon=True)
    thread.start()
    value = mapper(mapper_end, os.getpid())
    assert value._owner_machine("alice") == 300001
    assert value._owner_machine("bob") is None
    assert value._center_state("alice-home") == "admitted"
    assert value._center_state("other") == "unadmitted"
    assert value._admission_row(1)["machine"] == 300001
    assert value._admission_row(9) is None


def test_mapper_refuses_an_answer_from_any_other_pid(pair):
    _, mapper_end = pair
    value = mapper(mapper_end, os.getpid() + 1)
    peer = socket.socket(fileno=os.dup(pair[0]._channel.fileno()))
    with peer:
        peer.sendall(b'{"op":"CENTER_STATE_IS","state":"admitted"}')
        with pytest.raises(ValueError, match="unauthenticated"):
            value._center_state("alice-home")
    assert value.broker is None  # poisoned: no misaligned later answer
    with pytest.raises(ValueError, match="unavailable"):
        value._center_state("alice-home")
