"""DA4/DA5/DA6: the mapper's runtime admit and retire, verified against log rows."""
import json
import os
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GID = 1001
MACHINE = 300000 + GID  # the root's inner group in the mapper's namespace
pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX descriptors")


class Channel:
    def __init__(self):
        self.sent = []

    def sendall(self, data):
        self.sent.append(json.loads(data))


def mapper(tmp_path, *, bindings=None, generation=5, rows=None, states=None):
    module = runpy.run_path(str(ROOT / "deploy/role_owner_launcher.py"))
    value = object.__new__(module["OwnerLauncher"])
    value.bindings = dict(bindings or {})
    value.generation = generation
    value.data_root = str(tmp_path)
    value.overflow_uid, value.overflow_gid = os.getuid(), os.getgid()
    value.jobs, value.delete_fences = {}, {}
    value.channel = Channel()
    rows = rows or {}
    value._admission_row = lambda generation: rows.get(generation)
    value._center_state = lambda center: (states or {}).get(center, "admitted")
    return value


def row(generation, event="admit", principal="alice", center="alice-home", machine=MACHINE):
    return dict(op="ADMISSION_ROW_IS", generation=generation, event=event,
                principal=principal, center=center, machine=machine)


def root(tmp_path, name="alice-home", mode=0o750, gid=GID):
    path = tmp_path / name
    path.mkdir()
    if os.geteuid() == 0:
        os.chown(path, -1, gid)
    elif gid != os.getgid():
        pytest.skip("owner=uid-admission runs-in=linux_oracle.py --as-root: "
            "changing a root's group")
    path.chmod(mode)
    return os.open(path, os.O_PATH | os.O_DIRECTORY)


ADMIT = dict(op="ADMIT", principal="alice", command_center="alice-home", generation=6)
RETIRE = dict(op="RETIRE", principal="alice", command_center="alice-home", generation=7)


@pytest.fixture(autouse=True)
def group_stand_in():
    if os.geteuid() != 0 and os.getgid() != GID:
        pytest.skip("owner=uid-admission runs-in=linux_oracle.py (uid 1001) and --as-root: "
            "inner-group stand-in")


def test_admit_binds_from_the_row_and_is_idempotent(tmp_path):
    value = mapper(tmp_path, rows={6: row(6)})
    fd = root(tmp_path)
    try:
        value._decoder(ADMIT, [fd])
        value._decoder(ADMIT, [fd])  # retry of a bound center is a success
    finally:
        os.close(fd)
    assert value.bindings == {("alice", "alice-home"): MACHINE}
    assert value.channel.sent == [{"op": "ADMITTED"}, {"op": "ADMITTED"}]


@pytest.mark.parametrize("case", [
    "absent", "retire-row", "other-principal", "other-center", "stale", "rebound-machine",
    "bound-center", "retired-center", "wrong-group", "wrong-inode", "wrong-mode",
    "numeric-field", "path-field", "no-descriptor"])
def test_admit_refusals_leave_the_table_unchanged(tmp_path, case):
    rows = {6: row(6)}
    bindings = {("bob", "bob-home"): 300002}
    states = {}
    request = dict(ADMIT)
    generation = 5
    name, mode, gid = "alice-home", 0o750, GID
    if case == "absent":
        rows = {}
    elif case == "retire-row":
        rows = {6: row(6, event="retire")}
    elif case == "other-principal":
        rows = {6: row(6, principal="bob")}
    elif case == "other-center":
        rows = {6: row(6, center="bob-home")}
    elif case == "stale":
        generation = 6
    elif case == "rebound-machine":
        bindings = {("bob", "bob-home"): MACHINE}
    elif case == "bound-center":
        bindings = {("bob", "alice-home"): 300002}
    elif case == "retired-center":
        states = {"alice-home": "retired"}
    elif case == "wrong-group":
        gid = GID + 1
    elif case == "wrong-mode":
        mode = 0o755
    elif case == "numeric-field":
        request["machine"] = MACHINE
    elif case == "path-field":
        request["path"] = str(tmp_path / "alice-home")
    value = mapper(tmp_path, bindings=bindings, generation=generation, rows=rows, states=states)
    before = dict(value.bindings)
    if case == "wrong-group" and os.geteuid() != 0:
        pytest.skip("owner=uid-admission runs-in=linux_oracle.py --as-root: "
            "changing a root's group")
    fd = root(tmp_path, name, mode, gid)
    other = None
    try:
        received = [fd]
        if case == "wrong-inode":
            (tmp_path / "decoy").mkdir()
            other = os.open(tmp_path / "decoy", os.O_PATH | os.O_DIRECTORY)
            received = [other]
        elif case == "no-descriptor":
            received = []
        with pytest.raises((ValueError, KeyError)):
            value._decoder(request, received)
    finally:
        os.close(fd)
        if other is not None:
            os.close(other)
    assert value.bindings == before
    assert value.channel.sent == []


def test_a_bound_retry_still_checks_the_attached_root(tmp_path):
    value = mapper(tmp_path, bindings={("alice", "alice-home"): MACHINE}, rows={6: row(6)})
    fd = root(tmp_path)
    (tmp_path / "decoy").mkdir()
    decoy = os.open(tmp_path / "decoy", os.O_PATH | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match="label"):
            value._decoder(ADMIT, [decoy])
        value._decoder(ADMIT, [fd])
    finally:
        os.close(fd)
        os.close(decoy)
    assert value.channel.sent == [{"op": "ADMITTED"}]


def test_bound_retire_needs_the_fence_and_no_running_cell(tmp_path):
    value = mapper(tmp_path, bindings={("alice", "alice-home"): MACHINE},
                   rows={7: row(7, event="retire")})
    with pytest.raises(ValueError, match="fence"):
        value._decoder(RETIRE, [])
    value.delete_fences[MACHINE] = ("alice", "alice-home", "a" * 32)
    value.jobs[99] = (GID, MACHINE, 0, None)
    with pytest.raises(ValueError, match="fence"):
        value._decoder(RETIRE, [])
    assert value.bindings == {("alice", "alice-home"): MACHINE}
    value.jobs.clear()
    value._decoder(RETIRE, [])
    assert value.bindings == {}
    # Finish still releases the exact fence after the binding is gone (DA6 order).
    value._decoder(dict(op="DELETE_DONE", principal="alice", command_center="alice-home",
                        delete_token="a" * 32), [])
    assert value.delete_fences == {}
    assert value.channel.sent == [{"op": "RETIRED"}, {"op": "DELETE_FINISHED"}]


def test_unbound_retire_is_a_verified_no_op(tmp_path):
    value = mapper(tmp_path, rows={7: row(7, event="retire")})
    value._decoder(RETIRE, [])
    assert value.channel.sent == [{"op": "RETIRED"}]
    for bad in ({7: row(7)}, {7: row(7, event="retire", center="other")}, {}):
        refused = mapper(tmp_path, rows=bad)
        with pytest.raises(ValueError):
            refused._decoder(RETIRE, [])
        assert refused.channel.sent == []


def test_retire_refuses_another_owners_fence(tmp_path):
    value = mapper(tmp_path, bindings={("alice", "alice-home"): MACHINE},
                   rows={7: row(7, event="retire")})
    value.delete_fences[MACHINE] = ("alice", "alice-second", "a" * 32)
    with pytest.raises(ValueError):
        value._decoder(RETIRE, [])
    assert value.bindings == {("alice", "alice-home"): MACHINE}


def test_cells_for_an_unbound_center_refuse_without_fallback(tmp_path):
    value = mapper(tmp_path)
    with pytest.raises(KeyError):
        value._decoder(dict(op="SPAWN", kind="ui-preview", principal="alice",
                            command_center="alice-home"), [0])
