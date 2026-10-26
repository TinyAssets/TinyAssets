"""C12: one image selection, ordinary sleep and demand-driven wake."""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.admission import drive
from tests.test_owner_box_lifecycle import _other_turn, fleet  # noqa: F401
from tinyassets.boxes.images import Images
from tinyassets.boxes.provider import BoxError


def image(root, digit, *, layout="owner-box"):
    tree = root / ("harness-" + digit * 64)
    (tree / "app/tinyassets").mkdir(parents=True)
    (tree / ".box-image-id").write_text("sha256:" + digit * 64)
    (tree / "app/tinyassets/storage_layout.py").write_text(f'ACTIVE_BOX_LAYOUT = "{layout}"\n')
    return tree


@pytest.fixture
def rolling(fleet, tmp_path):  # noqa: F811
    root = tmp_path / "images"
    old, new = image(root, "1"), image(root, "2")
    fleet.manager.images = Images(root, tmp_path / "state/images.json", old)
    return SimpleNamespace(**vars(fleet), old=old.name, new=new.name, root=root,
                           state=tmp_path / "state/images.json")


def test_deploy_sleeps_awake_owners_without_eager_restart(rolling):
    uid = rolling.wake("a")
    before = len(rolling.runtime.starts)
    assert drive(rolling.routing.deploy(rolling.new)) == {
        "slept": 1, "asleep": 2, "busy": 0, "unresponsive": 0, "incomplete": 0,
        "incomplete_status": 0, "incomplete_sleep": 0}
    assert rolling.runtime.live == set()
    assert rolling.runtime.stops == [uid]
    assert len(rolling.runtime.starts) == before
    assert rolling.wake("a") == uid
    assert rolling.runtime.starts[-1][1]["rootfs"] == rolling.root / rolling.new


def test_sleeping_box_untouched_and_only_current_image_survives_host_restart(rolling):
    uid = rolling.wake("a")
    drive(rolling.routing.sleep("a"))
    before = len(rolling.runtime.starts), len(rolling.runtime.stops)
    assert drive(rolling.routing.deploy(rolling.new)) == {
        "slept": 0, "asleep": 3, "busy": 0, "unresponsive": 0, "incomplete": 0,
        "incomplete_status": 0, "incomplete_sleep": 0}
    assert (len(rolling.runtime.starts), len(rolling.runtime.stops)) == before
    recovered = Images(rolling.root, rolling.state, rolling.root / rolling.old)
    assert recovered.current == rolling.new
    assert json.loads(rolling.state.read_text()) == {"current": rolling.new}
    rolling.manager.images = recovered
    assert rolling.wake("a") == uid
    assert rolling.runtime.starts[-1][1]["rootfs"] == rolling.root / rolling.new


@pytest.mark.parametrize("keep_awake", [True, False])
def test_busy_request_and_background_turn_finish_before_normal_snapshot_sleep(rolling,
                                                                          keep_awake):
    uid = rolling.wake("a")
    activity = rolling.states[uid]
    with activity.enter(keep_awake=keep_awake):
        result = drive(rolling.routing.deploy(rolling.new))
        assert result == {"slept": 0, "asleep": 2, "busy": 1, "unresponsive": 0,
                          "incomplete": 0,
        "incomplete_status": 0, "incomplete_sleep": 0}
        # Never drained: its turn and new turns keep running on this image.
        assert not activity.draining and activity.active == 1
        assert rolling.runtime.live == {uid} and rolling.runtime.stops == []
        admitted = []
        thread = threading.Thread(target=_other_turn, args=(activity, admitted))
        thread.start()
        thread.join()
        assert admitted == ["admitted"]
        # Still on its own image while it serves; the next wake already gets the new one.
        assert rolling.manager.boxes[uid].image == rolling.old
        assert rolling.manager.images.current == rolling.new
    # Its own ordinary sleep later, then the next wake starts the new image.
    drive(rolling.routing.sleep("a"))
    assert rolling.runtime.stops == [uid]
    assert len(rolling.runtime.starts) == 1
    rolling.wake("a")
    assert rolling.manager.boxes[uid].image == rolling.new


def test_unknown_activity_is_incomplete_and_never_stops_a_turn(rolling, monkeypatch):
    uid = rolling.wake("a")
    original = rolling.manager._rpc
    monkeypatch.setattr(rolling.manager, "_rpc", lambda *a, **k: (_ for _ in ()).throw(
        OSError("unknown activity")))
    # Not answering its own status: left untouched, never drained on a guess.
    assert drive(rolling.routing.deploy(rolling.new))["unresponsive"] == 1
    assert rolling.runtime.live == {uid} and not rolling.runtime.stops
    monkeypatch.setattr(rolling.manager, "_rpc", original)
    assert drive(rolling.routing.deploy(rolling.new))["slept"] == 1


def test_rollback_republishes_previous_image_for_every_next_wake(rolling):
    a, b = rolling.wake("a"), rolling.wake("b")
    assert drive(rolling.routing.deploy(rolling.new))["slept"] == 2
    assert not rolling.runtime.live
    rolling.wake("a")
    assert rolling.manager.boxes[a].image == rolling.new
    assert drive(rolling.routing.deploy(rolling.old))["slept"] == 1
    assert not rolling.runtime.live
    rolling.wake("a")
    rolling.wake("b")
    assert rolling.manager.boxes[a].image == rolling.manager.boxes[b].image == rolling.old
    assert json.loads(rolling.state.read_text()) == {"current": rolling.old}


def test_failed_health_does_not_select_a_per_owner_rollback(rolling, monkeypatch):
    uid = rolling.wake("a")
    drive(rolling.routing.deploy(rolling.new))
    rolling.manager.startup_timeout = .05
    original = rolling.manager._rpc
    def rpc(box, operation="status", **body):
        if box.image == rolling.new and operation == "status":
            raise OSError("synthetic unhealthy image")
        return original(box, operation, **body)
    monkeypatch.setattr(rolling.manager, "_rpc", rpc)
    with pytest.raises(BoxError, match="startup failed"):
        rolling.wake("a")
    assert rolling.manager.images.current == rolling.new
    assert rolling.runtime.starts[-1][1]["rootfs"] == rolling.root / rolling.new
    assert not rolling.runtime.live
    assert "roll_failed" not in vars(rolling.manager.boxes[uid])


def test_cutover_boundary_refuses_previous_image_and_never_starts_it(rolling):
    uid = rolling.wake("a")
    (rolling.root / rolling.old / "app/tinyassets/storage_layout.py").write_text(
        'ACTIVE_BOX_LAYOUT = "owner-split"\n')
    drive(rolling.routing.deploy(rolling.new))
    with pytest.raises(BoxError, match="cutover boundary"):
        drive(rolling.routing.deploy(rolling.old))
    assert rolling.manager.images.current == rolling.new
    assert not rolling.runtime.live and rolling.runtime.stops == [uid]


def test_image_reference_and_symlink_cannot_select_another_host_path(rolling):
    with pytest.raises(BoxError, match="immutable"):
        drive(rolling.routing.deploy("../somewhere"))
    link = rolling.root / ("harness-" + "3" * 64)
    link.symlink_to(rolling.root / rolling.new, target_is_directory=True)
    with pytest.raises(BoxError, match="unavailable"):
        drive(rolling.routing.deploy(link.name))


def test_host_refuses_retired_roll_drain_and_wake_image_override(rolling):
    record = rolling.admission.owner("a")
    for operation, owner, fields in (
        ("roll", None, {"image": rolling.new}),
        ("drain", None, {"uid": record["uid"]}),
        ("wake", "a", {"record": record, "scheduled": False, "image": rolling.new}),
    ):
        with pytest.raises(BoxError):
            rolling.control(operation, owner, **fields)
    assert not rolling.runtime.starts
    assert rolling.manager.images.current == rolling.old


def test_broker_lifecycle_socket_is_the_boxhostd_broker(monkeypatch):
    from tinyassets.broker import lifecycle_control

    assert lifecycle_control.SOCKET == Path("/run/tinyassets-boxhostd/broker/broker.sock")
    monkeypatch.delenv("TINYASSETS_BOXES_RUN", raising=False)
    assert lifecycle_control.socket_path() == lifecycle_control.SOCKET
    monkeypatch.setenv("TINYASSETS_BOXES_RUN", "/tmp/rehearsal-run")
    assert lifecycle_control.socket_path() == Path("/tmp/rehearsal-run/broker/broker.sock")


def test_missing_recorded_image_falls_back_to_the_resolved_harness(tmp_path):
    root = tmp_path / "images"
    current = image(root, "2")
    state = tmp_path / "state/images.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"current": "harness-" + "9" * 64}))
    loaded = Images(root, state, current)
    assert loaded.current == current.name


def test_non_string_recorded_image_falls_back_to_the_resolved_harness(tmp_path):
    root = tmp_path / "images"
    current = image(root, "4")
    state = tmp_path / "state/images.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"current": None}))
    loaded = Images(root, state, current)
    assert loaded.current == current.name


def test_pruned_recorded_image_falls_back_to_the_startup_harness(tmp_path):
    root = tmp_path / "images"
    startup = image(root, "2")
    recorded = image(root, "1")
    state = tmp_path / "state/images.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"current": recorded.name}))
    loaded = Images(root, state, startup)
    assert loaded.current == recorded.name
    shutil.rmtree(recorded)
    assert loaded.current == startup.name
    assert json.loads(state.read_text()) == {"current": startup.name}
    assert loaded.current == startup.name


def test_launch_uses_harness_current_when_startup_and_recorded_images_are_gone(rolling):
    shutil.rmtree(rolling.root / rolling.old)
    rolling.manager.images.current = "harness-" + "9" * 64
    (rolling.root / "harness-current").symlink_to(rolling.new)
    uid = rolling.wake("a")
    assert rolling.manager.boxes[uid].image == rolling.new
    assert rolling.runtime.starts[-1][1]["rootfs"] == rolling.root / rolling.new


def test_launch_keeps_a_present_image_when_harness_current_points_elsewhere(rolling):
    (rolling.root / "harness-current").symlink_to(rolling.new)
    uid = rolling.wake("a")
    assert rolling.manager.boxes[uid].image == rolling.old
    assert rolling.runtime.starts[-1][1]["rootfs"] == rolling.root / rolling.old


def test_launch_refuses_a_missing_image_without_harness_current(rolling):
    shutil.rmtree(rolling.root / rolling.old)
    rolling.manager.images.current = "harness-" + "9" * 64
    with pytest.raises(BoxError, match="startup failed"):
        rolling.wake("a")
    assert not rolling.runtime.starts


def test_deploy_cannot_stop_a_deletion_drain_after_the_last_turn(rolling):
    uid = rolling.wake("a")
    with rolling.states[uid].enter():
        rolling.states[uid].status(deleting=True)
    result = drive(rolling.routing.deploy(rolling.new))
    # The receipt says where it stopped: the status answered, the sleep refused.
    assert result["incomplete"] == 1 and result["incomplete_sleep"] == 1
    assert not rolling.runtime.stops and rolling.runtime.live == {uid}
    assert rolling.manager.boxes[uid].image == rolling.old


pytestmark = pytest.mark.verifies('owner-daemon-in-box:C12')


def test_a_refused_sleep_intent_is_withdrawn_while_the_owner_is_still_busy(rolling):
    uid = rolling.wake("a")
    activity = rolling.states[uid]
    with activity.enter():
        # What an earlier deploy's refused drain left behind.
        rolling.routing.volumes.volume("a", "sleep")
        assert rolling.routing.volumes.volume("a", "pending")["work"]["kind"] == "snapshot"
        record = drive(rolling.routing.volumes.recover("a"))
        # The box never slept: no snapshot is owed and readiness waits on nothing.
        assert record["work"] == {"kind": "running"}
        assert rolling.runtime.stops == [] and rolling.runtime.live == {uid}
        assert drive(rolling.routing.readiness())["ready"] is True
