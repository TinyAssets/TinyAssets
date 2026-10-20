"""C11: the real broker admits owners without shared center state."""

import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.support.admission import drive, projection
from tests.test_broker_server import broker  # noqa: F401
from tinyassets import rpc_frames as rf
from tinyassets.broker.admission import local_mutate, read
from tinyassets.broker.owner_identities import OwnerIdentities, OwnerIdentity


def store(tmp_path, *principals):
    tmp_path.chmod(0o700)
    identities = OwnerIdentities(tmp_path / "identities.db", initialize=True)
    for principal in principals:
        identities.resolve(principal, allocate=True)
    return identities


def rows(identities):
    with sqlite3.connect(identities.path) as db:
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name='center_admissions'").fetchall()
        return db.execute("SELECT * FROM owner_admission ORDER BY digest").fetchall()


def test_admit_takes_machine_from_reservation_and_retry_is_idempotent(tmp_path):
    identities = store(tmp_path, "alice", "bob")
    routing = projection(identities.path)
    assert drive(identities.admit("bob", routing=routing)) == OwnerIdentity(300002, 300002)
    before = rows(identities)
    secret = identities.box_route("bob")
    assert drive(identities.admit("bob", routing=routing)) == OwnerIdentity(300002, 300002)
    assert rows(identities) == before
    assert OwnerIdentities(identities.path).box_route("bob") == secret
    assert read(identities, "alice").state == "absent"
    assert read(identities, "bob").state == "admitted"


def test_missing_invalid_and_erased_owners_refuse_without_mutation(tmp_path):
    identities = store(tmp_path, "alice")
    routing = projection(identities.path)
    for principal in ("", " alice", "never-allocated"):
        before = rows(identities)
        with pytest.raises((ValueError, LookupError)):
            drive(identities.admit(principal, routing=routing))
        assert rows(identities) == before
    drive(identities.admit("alice", routing=routing))
    drive(local_mutate(identities.path, "alice", "erase", routing=routing))
    before = rows(identities)
    with pytest.raises(PermissionError):
        drive(identities.admit("alice", routing=routing))
    assert rows(identities) == before


def test_concurrent_writers_keep_one_record_per_owner(tmp_path):
    names = [f"user{index}" for index in range(8)]
    identities = store(tmp_path, *names)
    def admit(owner):
        return drive(identities.admit(owner, routing=projection(identities.path)))
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(admit, names * 4))
    assert len(rows(identities)) == 8
    assert len(set(results)) == 8
    assert results[:8] == results[8:16] == results[16:24] == results[24:]


def test_missing_map_refuses_and_is_never_created(tmp_path):
    identities = store(tmp_path, "alice")
    identities.path.unlink()
    with pytest.raises(sqlite3.OperationalError):
        drive(identities.admit("alice", routing=projection(identities.path)))
    assert not identities.path.exists()


def request(broker, **changes):  # noqa: F811
    document = {"op": "ADMIT_OWNER", "principal": "alice",
                "generation": broker.state["generation"], "token": broker.state["token"]}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(5)
        channel.connect(str(broker.path))
        channel.sendall(rf.control(rf.CONNECTION, document | changes))
        return rf.read_frame_blocking(channel).control()


def test_owner_channel_admission_retry_and_refusals(broker, tmp_path):  # noqa: F811
    refused = {"op": "OWNER_ADMIT_REFUSED"}
    broker.server._owner_identities = None
    assert request(broker) == refused
    identities = store(tmp_path, "alice")
    broker.server._owner_identities = identities
    broker.server._routing = projection(identities.path)
    for changes in ({"token": "stale"}, {"generation": True}, {"machine": 300001},
                    {"uid": 300001}, {"path": "/data/alice-home"},
                    {"principal": "bob"}, {"center": "x"}, {"event": "retire"}):
        assert request(broker, **changes) == refused
    assert rows(identities) == []
    expected = {"op": "OWNER_ADMIT_IS", "machine": 300001}
    assert request(broker) == expected
    assert request(broker) == expected
    assert len(rows(identities)) == 1
