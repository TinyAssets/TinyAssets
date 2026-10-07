"""DA1: the broker-private, append-only center admission log and its IPC op."""
import os
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.test_broker_server import broker  # noqa: F401 - real Unix server fixture
from tinyassets import rpc_frames as rf
from tinyassets.broker.owner_identities import AdmissionRow, OwnerIdentities

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX identity storage")


def store(tmp_path, *principals):
    tmp_path.chmod(0o700)
    identities = OwnerIdentities(tmp_path / "identities.db", initialize=True)
    for principal in principals:
        identities.resolve(principal, allocate=True)
    return identities


def rows(identities):
    with sqlite3.connect(identities.path) as db:
        return db.execute("SELECT * FROM center_admissions ORDER BY generation").fetchall()


def test_admit_takes_machine_from_reservation_and_retry_is_idempotent(tmp_path):
    identities = store(tmp_path, "alice", "bob")
    first = identities.admission("admit", "bob", "bob-home")
    assert first == AdmissionRow(1, "admit", "bob", "bob-home", 300002)
    assert identities.admission("admit", "bob", "bob-home") == first
    assert identities.center_state("bob-home") == "admitted"
    assert identities.center_state("never") == "unadmitted"
    retired = identities.admission("retire", "bob", "bob-home")
    assert retired == AdmissionRow(2, "retire", "bob", "bob-home", 300002)
    assert identities.admission("retire", "bob", "bob-home") == retired
    assert identities.center_state("bob-home") == "retired"
    assert rows(identities) == [(1, "admit", "bob", "bob-home", 300002),
                                (2, "retire", "bob", "bob-home", 300002)]
    assert identities.admission_row(2) == retired
    assert identities.admission_row(3) is None
    assert identities.admissions_after(1) == [retired]
    restarted = OwnerIdentities(identities.path)
    assert restarted.admissions_after(0) == [first, retired]


def test_every_refusal_appends_nothing(tmp_path):
    identities = store(tmp_path, "alice", "bob")
    identities.admission("admit", "alice", "shared")
    refusals = [
        (PermissionError, ("admit", "bob", "shared")),        # another principal
        (PermissionError, ("retire", "bob", "shared")),
        (LookupError, ("admit", "carol", "carol-home")),      # no reservation
        (LookupError, ("retire", "alice", "never-admitted")),
        (ValueError, ("grant", "alice", "other")),
        (ValueError, ("admit", "alice", "../escape")),
        (ValueError, ("admit", "alice", ".")),
        (ValueError, ("admit", "alice", "x" * 129)),
        (ValueError, ("admit", " alice", "other")),
    ]
    for error, arguments in refusals:
        with pytest.raises(error):
            identities.admission(*arguments)
    identities.admission("retire", "alice", "shared")
    with pytest.raises(PermissionError, match="never admitted again"):
        identities.admission("admit", "alice", "shared")
    assert len(rows(identities)) == 2
    with pytest.raises(LookupError):
        identities.resolve("carol")  # an admission never allocates


def test_log_is_append_only(tmp_path):
    identities = store(tmp_path, "alice")
    identities.admission("admit", "alice", "home")
    with sqlite3.connect(identities.path) as db:
        for statement in ("DELETE FROM center_admissions",
                          "UPDATE center_admissions SET machine=300009"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(statement)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO center_admissions (event, principal, center, machine) "
                       "VALUES ('admit', 'alice', 'home', 300001)")


def test_concurrent_writers_serialize_to_one_owner_per_center(tmp_path):
    names = [f"user{index}" for index in range(8)]
    identities = store(tmp_path, *names)

    def admit(job):
        principal, center = job
        try:
            return identities.admission("admit", principal, center)
        except PermissionError:
            return None

    jobs = [(name, f"{name}-home") for name in names] * 4 + [(name, "contested") for name in names]
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(admit, jobs))
    winners = {row.principal for row in results if row and row.center == "contested"}
    assert len(winners) == 1
    table = rows(identities)
    assert [row[0] for row in table] == list(range(1, 10))
    assert len({row[3] for row in table}) == 9


def test_missing_map_refuses_and_is_never_created(tmp_path):
    identities = store(tmp_path, "alice")
    identities.path.unlink()
    with pytest.raises(sqlite3.OperationalError):
        identities.admission("admit", "alice", "home")
    with pytest.raises(sqlite3.OperationalError):
        identities.center_state("home")
    assert not identities.path.exists()


def request(broker, **changes):  # noqa: F811 - fixture passed as a helper argument
    document = {"op": "CENTER_ADMISSION", "event": "admit", "principal": "alice",
                "center": "alice-home", "generation": broker.state["generation"],
                "token": broker.state["token"]}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(5)
        channel.connect(str(broker.path))
        channel.sendall(rf.control(rf.CONNECTION, document | changes))
        return rf.read_frame_blocking(channel).control()


def test_owner_channel_admission_retry_and_refusals(broker, tmp_path):  # noqa: F811
    refused = {"op": "CENTER_ADMISSION_REFUSED"}
    assert request(broker) == refused  # no map is never initialized at runtime
    identities = store(tmp_path, "alice")
    broker.server._owner_identities = identities
    for changes in ({"token": "stale"}, {"generation": True}, {"machine": 300001},
                    {"uid": 300001}, {"path": "/data/alice-home"},
                    {"principal": "bob"}, {"center": "../x"}, {"event": 1}):
        assert request(broker, **changes) == refused
    # D218 retires a tree-less center only when the log admitted it.
    assert request(broker, event="retire") == {"op": "CENTER_ADMISSION_UNADMITTED"}
    assert request(broker, event="retire", token="stale") == refused
    assert rows(identities) == []
    first = {"op": "CENTER_ADMISSION_IS", "generation": 1, "machine": 300001}
    assert request(broker) == first
    assert request(broker) == first  # a lost acknowledgement is safe to retry
    assert request(broker, event="retire") == dict(first, generation=2)
    assert request(broker) == refused


def test_client_distinguishes_a_never_admitted_retire(monkeypatch, tmp_path):
    from tinyassets.broker import owner_identities

    answers = iter([{"op": "CENTER_ADMISSION_UNADMITTED"}, {"op": "CENTER_ADMISSION_REFUSED"}])
    monkeypatch.setattr(owner_identities, "_owner_request", lambda root, doc: next(answers))
    with pytest.raises(owner_identities.CenterUnadmitted):
        owner_identities.center_admission(tmp_path, event="retire", principal="alice",
                                          center="alice-home")
    with pytest.raises(RuntimeError, match="refused"):
        owner_identities.center_admission(tmp_path, event="retire", principal="alice",
                                          center="alice-home")
