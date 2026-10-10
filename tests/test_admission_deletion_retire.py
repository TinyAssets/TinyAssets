"""DA6: whole-center deletion retires the binding before finish, resumably.

The daemon side (role_owner_delete.retire) is wired to a real broker log and the
real mapper's RETIRE/DELETE_DONE handling; only the transport is in-process.
"""
import os
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets import role_owner_delete
from tinyassets.broker.owner_identities import OwnerIdentities

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX identity storage")
ROOT = Path(__file__).resolve().parents[1]
TOKEN = "a" * 32


class Crash(RuntimeError):
    pass


@pytest.fixture
def world(tmp_path, monkeypatch):
    from tinyassets import role_decoder, storage
    from tinyassets.auth import middleware
    from tinyassets.broker import owner_identities, supervisor

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    identities = OwnerIdentities(state / "identities.db", initialize=True)
    machine = identities.resolve("alice", allocate=True).uid
    admitted = identities.admission("admit", "alice", "alice-home")
    data = tmp_path / "data"
    data.mkdir()

    module = runpy.run_path(str(ROOT / "deploy/role_owner_launcher.py"))
    mapper = object.__new__(module["OwnerLauncher"])
    mapper.bindings = {("alice", "alice-home"): machine}
    mapper.generation = admitted.generation
    mapper.jobs = {}
    mapper.delete_fences = {machine: ("alice", "alice-home", TOKEN)}  # pass one began
    mapper.channel = SimpleNamespace(sendall=lambda value: None)

    def admission_row(generation):
        row = identities.admission_row(generation)
        return None if row is None else dict(
            op="ADMISSION_ROW_IS", generation=row.generation, event=row.event,
            principal=row.principal, center=row.center, machine=row.machine)

    mapper._admission_row = admission_row
    crashes = {"append": 0, "unbind": 0}

    def center_admission(root, *, event, principal, center):
        row = identities.admission(event, principal, center)
        if crashes["append"]:
            crashes["append"] -= 1
            raise Crash("died after the retire row committed")
        return row.generation, row.machine

    class Client:
        def retire(self, *, principal, command_center, generation):
            mapper._decoder(dict(op="RETIRE", principal=principal,
                                 command_center=command_center, generation=generation), [])
            if crashes["unbind"]:
                crashes["unbind"] -= 1
                raise Crash("died after the mapper unbind")

        def finish_delete(self, *, principal, command_center, token):
            mapper._decoder(dict(op="DELETE_DONE", principal=principal,
                                 command_center=command_center, delete_token=token), [])

    monkeypatch.setattr(role_decoder, "_bounded_client", Client())
    monkeypatch.setattr(storage, "data_dir", lambda: data)
    monkeypatch.setattr(middleware, "current_identity",
                        lambda: SimpleNamespace(user_id="alice"))
    monkeypatch.setattr(supervisor, "_protect_daemon", lambda: None)
    monkeypatch.setattr(owner_identities, "center_admission", center_admission)
    return SimpleNamespace(identities=identities, mapper=mapper, data=data,
                           machine=machine, crashes=crashes)


def test_retire_refuses_while_the_tree_exists(world):
    (world.data / "alice-home").mkdir()
    with pytest.raises(RuntimeError, match="daemon pass"):
        role_owner_delete.retire(world.data / "alice-home", token=TOKEN)
    assert world.identities.center_state("alice-home") == "admitted"
    assert world.mapper.bindings


def test_normal_path_retires_unbinds_then_finishes(world):
    center = world.data / "alice-home"
    generation = role_owner_delete.retire(center, token=TOKEN)
    assert world.identities.admission_row(generation).event == "retire"
    assert world.mapper.bindings == {}
    assert world.mapper.delete_fences  # the fence outlives the binding until finish
    role_owner_delete.finish(center, token=TOKEN)
    assert world.mapper.delete_fences == {}
    with pytest.raises(PermissionError, match="never admitted again"):
        world.identities.admission("admit", "alice", "alice-home")


@pytest.mark.parametrize("step", ["append", "unbind"])
def test_a_crash_at_each_new_step_resumes_idempotently(world, step):
    center = world.data / "alice-home"
    world.crashes[step] = 1
    with pytest.raises(Crash):
        role_owner_delete.retire(center, token=TOKEN)
    generation = role_owner_delete.retire(center, token=TOKEN)  # the resume
    rows = world.identities.admissions_after(0)
    assert [row.event for row in rows] == ["admit", "retire"]  # one retire row, ever
    assert rows[-1].generation == generation
    assert world.mapper.bindings == {}
    role_owner_delete.finish(center, token=TOKEN)
    assert world.mapper.delete_fences == {}


def test_after_restart_an_unbound_retire_is_a_verified_no_op(world):
    """Tree gone and center unbound (restart, or F1(b) missing): resume still retires."""
    world.mapper.bindings.clear()  # restart: bootstrap omitted the tree-less center
    center = world.data / "alice-home"
    generation = role_owner_delete.retire(center, token=TOKEN)
    assert world.identities.center_state("alice-home") == "retired"
    assert world.identities.admission_row(generation).event == "retire"
    role_owner_delete.finish(center, token=TOKEN)


def test_a_bound_retire_without_quiescence_keeps_the_binding(world):
    world.mapper.jobs[7] = (1, world.machine, 0, None)
    with pytest.raises(ValueError, match="quiescent"):
        role_owner_delete.retire(world.data / "alice-home", token=TOKEN)
    assert world.mapper.bindings == {("alice", "alice-home"): world.machine}
    world.mapper.jobs.clear()
    role_owner_delete.retire(world.data / "alice-home", token=TOKEN)  # resume
    assert world.mapper.bindings == {}
