"""D11 roll decisions: no owner tree or real deployment venue is used."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from tests.support.admission import drive
from tests.test_owner_box_lifecycle import fleet  # noqa: F401
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
    fleet.manager.roll_wait = .15
    return SimpleNamespace(**vars(fleet), old=old.name, new=new.name, root=root,
                           state=tmp_path / "state/images.json")


def test_roll_waits_for_a_turn_and_interactive_wake_uses_newest_image(rolling):
    manager = rolling.manager
    uid = rolling.wake("a")
    original = rolling.states[uid]
    result = []
    manager.roll_wait = 3
    assert drive(rolling.routing.roll(rolling.new)) == {"uids": [uid], "asleep": 0}
    with original.enter():
        thread = threading.Thread(
            target=lambda: result.append(drive(rolling.routing.roll(rolling.new, uid))))
        thread.start()
        other = rolling.wake("b")
        assert manager.boxes[other].image == rolling.new
        assert manager.boxes[uid].image == rolling.old
        assert not rolling.runtime.stops
    thread.join(5)
    assert not thread.is_alive()
    assert result == [{"uid": uid, "outcome": "rolled"}]
    assert rolling.runtime.stops == [uid]
    assert original.draining
    assert manager.boxes[uid].image == rolling.new


def test_sleeping_box_untouched_and_image_record_survives_host_restart(rolling):
    manager = rolling.manager
    uid = rolling.wake("a")
    with manager.condition:
        assert manager._sleep(manager.boxes[uid], idle=0)
    starts, stops = len(rolling.runtime.starts), len(rolling.runtime.stops)
    assert drive(rolling.routing.roll(rolling.new)) == {"uids": [], "asleep": 1}
    assert drive(rolling.routing.roll(rolling.new, uid)) == {"uid": uid, "outcome": "asleep"}
    assert (len(rolling.runtime.starts), len(rolling.runtime.stops)) == (starts, stops)
    recovered = Images(rolling.root, rolling.state, rolling.root / rolling.old)
    assert recovered.current == rolling.new and recovered.last[str(uid)] == rolling.old
    manager.images = recovered
    rolling.wake("a")
    assert recovered.last[str(uid)] == rolling.new
    assert rolling.runtime.starts[-1][1]["rootfs"] == rolling.root / rolling.new


def test_busy_turn_defers_roll_without_stopping(rolling):
    uid = rolling.wake("a")
    drive(rolling.routing.roll(rolling.new))
    with rolling.states[uid].enter():
        assert drive(rolling.routing.roll(rolling.new, uid))["outcome"] == "deferred_busy"
    assert not rolling.runtime.stops
    assert drive(rolling.routing.roll(rolling.new, uid))["outcome"] == "rolled"


def test_abandoned_wake_has_no_lease_pinning_the_box(rolling):
    manager = rolling.manager
    uid = rolling.wake("a")
    drive(rolling.routing.roll(rolling.new))
    assert drive(rolling.routing.roll(rolling.new, uid)) == {"uid": uid, "outcome": "rolled"}
    assert "leases" not in vars(manager.boxes[uid])


def test_failed_health_rolls_back_only_that_box(rolling, monkeypatch):
    manager = rolling.manager
    a, b = rolling.wake("a"), rolling.wake("b")
    manager.wake_timeout = .05
    original = manager._rpc

    def rpc(box, operation="status", **body):
        if box.uid == a and box.image == rolling.new and operation == "status":
            raise OSError("synthetic failed health")
        return original(box, operation, **body)

    monkeypatch.setattr(manager, "_rpc", rpc)
    drive(rolling.routing.roll(rolling.new))
    assert drive(rolling.routing.roll(rolling.new, b))["outcome"] == "rolled"
    assert drive(rolling.routing.roll(rolling.new, a)) == {
        "uid": a, "outcome": "rolled_back", "reason": "health_failed"}
    assert rolling.runtime.live == {a, b}
    assert manager.boxes[a].image == rolling.old
    assert manager.boxes[b].image == rolling.new
    assert manager.images.last == {str(a): rolling.old, str(b): rolling.new}


def test_cutover_boundary_refuses_previous_image_and_never_starts_it(rolling, monkeypatch):
    manager = rolling.manager
    uid = rolling.wake("a")
    # An old-image record cannot be used after split writes, even if the
    # candidate's health fails. There is no caller override for this refusal.
    (rolling.root / rolling.old / "app/tinyassets/storage_layout.py").write_text(
        'ACTIVE_BOX_LAYOUT = "owner-split"\n')
    original = rolling.runtime.start

    def start(*args, **kwargs):
        original(*args, **kwargs)
        raise BoxError("synthetic unhealthy image")

    monkeypatch.setattr(rolling.runtime, "start", start)
    drive(rolling.routing.roll(rolling.new))
    with pytest.raises(BoxError, match="cutover boundary.*backup-restore"):
        drive(rolling.routing.roll(rolling.new, uid))
    assert len(rolling.runtime.starts) == 2
    assert not rolling.runtime.live and not hasattr(manager.boxes[uid], "wanted")
    with pytest.raises(BoxError, match="cutover boundary"):
        drive(rolling.routing.roll(rolling.old))


def test_image_reference_and_symlink_cannot_select_another_host_path(rolling):
    with pytest.raises(BoxError, match="immutable"):
        drive(rolling.routing.roll("../somewhere"))
    link = rolling.root / ("harness-" + "3" * 64)
    link.symlink_to(rolling.root / rolling.new, target_is_directory=True)
    with pytest.raises(BoxError, match="unavailable"):
        drive(rolling.routing.roll(link.name))


def test_interactive_wake_is_not_blocked_by_another_box_readiness_poll(rolling, monkeypatch):
    manager = rolling.manager
    uid = rolling.wake("a")
    entered, ready = threading.Event(), threading.Event()
    original = manager._rpc

    def rpc(box, operation="status", **body):
        if box.uid == uid and box.image == rolling.new and operation == "status":
            entered.set()
            if not ready.is_set():
                raise OSError("synthetic startup not ready yet")
        return original(box, operation, **body)

    monkeypatch.setattr(manager, "_rpc", rpc)
    drive(rolling.routing.roll(rolling.new))
    results = []
    thread = threading.Thread(
        target=lambda: results.append(drive(rolling.routing.roll(rolling.new, uid))))
    thread.start()
    assert entered.wait(2)
    try:
        other = rolling.wake("b")
        assert manager.boxes[other].image == rolling.new
        assert not results
    finally:
        ready.set()
        thread.join(5)
    assert results == [{"uid": uid, "outcome": "rolled"}]


@pytest.mark.parametrize("previous_present", [True, False])
def test_failed_candidate_running_turn_is_not_killed_even_when_rollback_cannot_drain(
    rolling, monkeypatch, previous_present,
):
    manager = rolling.manager
    uid = rolling.wake("a")
    manager.wake_timeout = manager.roll_wait = .05
    original_start, original_rpc = rolling.runtime.start, manager._rpc
    gates = []

    def start(*args, **kwargs):
        original_start(*args, **kwargs)
        if kwargs["rootfs"].name == rolling.new:
            gate = rolling.states[uid].enter()
            gate.__enter__()
            gates.append(gate)

    def rpc(box, operation="status", **body):
        if box.image == rolling.new and operation == "status":
            raise OSError("synthetic failed health")
        return original_rpc(box, operation, **body)

    monkeypatch.setattr(rolling.runtime, "start", start)
    monkeypatch.setattr(manager, "_rpc", rpc)
    drive(rolling.routing.roll(rolling.new))
    if not previous_present:
        (rolling.root / rolling.old).rename(rolling.root / "missing-rollback-image")
    try:
        with pytest.raises(RuntimeError, match="candidate cannot safely drain"):
            drive(rolling.routing.roll(rolling.new, uid))
        assert rolling.runtime.live == {uid}
        assert rolling.runtime.stops == [uid]  # only the original drained image
        manager.maintain()
        assert rolling.runtime.live == {uid}
        assert manager.boxes[uid].roll_failed
    finally:
        for gate in gates:
            gate.__exit__(None, None, None)
    manager.maintain()
    assert rolling.runtime.live == {uid}  # C15: cleanup needs a broker decision.
    drive(rolling.routing.sleep("a"))
    assert not rolling.runtime.live


def test_roll_cannot_stop_a_deletion_drain_after_the_last_turn(rolling):
    manager = rolling.manager
    uid = rolling.wake("a")
    state = rolling.states[uid]
    with state.enter():
        state.status(deleting=True)
    drive(rolling.routing.roll(rolling.new))
    assert drive(rolling.routing.roll(rolling.new, uid)) == {"uid": uid, "outcome": "deferred_busy"}
    assert not rolling.runtime.stops
    assert rolling.runtime.live == {uid}
    assert manager.boxes[uid].image == rolling.old
