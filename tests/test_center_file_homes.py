"""Exercise the scheduler, action, status, ledger, and first-contact entry paths."""

from __future__ import annotations

import json
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets.api.first_contact import ensure_founder_home, home_is_complete
from tinyassets.api.universe import _action_control_daemon, _worker_liveness
from tinyassets.auto_ship_ledger import ShipAttempt, read_attempts, record_attempt
from tinyassets.control_plane.tick import AssignedQueueConsumer

pytestmark = pytest.mark.role_split


@pytest.fixture
def center(tmp_path: Path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    path = root / "center"
    path.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setattr("tinyassets.broker.owner_identities.admitted_owner",
                        lambda root, *, center: "owner")
    return path


class _Box:
    def __init__(self, present: bool):
        self.present = present
        self.binds = []

    def bind(self, center, *, account_id, turn_id):
        self.binds.append((center, account_id, turn_id))
        return SimpleNamespace(command_center_id=center, account_id=account_id)

    def stat(self, handle, name):
        assert name == "/cc/soul.md"
        return SimpleNamespace(kind="file") if self.present else None


def test_pause_action_controls_scheduler_dispatch(center, monkeypatch):
    import tinyassets.api.universe as api
    import tinyassets.automations as automations

    monkeypatch.setattr(api, "_request_universe", lambda _uid: center.name)
    monkeypatch.setattr(api, "_universe_dir", lambda _uid: center)
    monkeypatch.setattr(automations, "due_automations", lambda *a, **k: [
        (SimpleNamespace(universe_id=center.name), "due")])
    monkeypatch.setattr(automations, "owed_since", lambda *a: 0)
    monkeypatch.setattr(automations, "automation_lease_key", lambda _a: "center:agent")
    consumer = AssignedQueueConsumer(center.parent)
    monkeypatch.setattr(consumer, "_reap_finished", lambda: (1, set()))
    monkeypatch.setattr(consumer, "_agent_running_elsewhere", lambda *a: False)

    class Recorder:
        count = 0

        def submit(self, *args):
            self.count += 1
            return Future()

    recorder = Recorder()
    consumer._executor.shutdown(wait=False)
    consumer._continuation_executor.shutdown(wait=False)
    consumer._executor = recorder
    box = _Box(present=True)
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: (box, None))

    assert json.loads(_action_control_daemon(center.name, "pause"))["status"] == "signal_written"
    assert consumer._submit_due_automations([center.name], None)[0] == 0
    assert recorder.count == 0
    assert json.loads(_action_control_daemon(center.name, "resume"))["status"] == "resumed"
    assert consumer._submit_due_automations([center.name], None)[0] == 1
    assert recorder.count == 1


def test_supervisor_heartbeat_reaches_status(center, monkeypatch):
    monkeypatch.setenv("TINYASSETS_BUILD_SHA", "a" * 40)
    consumer = AssignedQueueConsumer(center.parent)
    try:
        consumer._publish_heartbeat(center.name)
        liveness = _worker_liveness(center)
        assert liveness["present"] is True
        assert liveness["worker_count"] == 1
        assert liveness["workers"][0]["worker_id"] == consumer.consumer_id
    finally:
        consumer._executor.shutdown(wait=False)
        consumer._continuation_executor.shutdown(wait=False)


def test_ledger_read_sees_all_recorded_attempts(center):
    for i in range(3):
        record_attempt(center, ShipAttempt(
            ship_attempt_id=f"ship_{i}", created_at="t", updated_at="t",
            ship_status="skipped",
        ))
    assert [a.ship_attempt_id for a in read_attempts(center)] == [
        "ship_0", "ship_1", "ship_2",
    ]


def test_first_contact_uses_only_box_soul(center, monkeypatch):
    import tinyassets.daemon_server as server

    monkeypatch.setattr(server, "get_founder_home", lambda *a: center.name)
    box = _Box(present=True)
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: (box, None))
    assert not (center / "soul.md").exists()
    assert home_is_complete(center.parent, center.name)
    assert ensure_founder_home(center.parent, "owner") == center.name
    assert box.binds and all(entry[1] == "owner" for entry in box.binds)

    (center / "soul.md").write_text("stale host copy")
    box.present = False
    assert not home_is_complete(center.parent, center.name)


def test_soul_presence_uses_admitted_binding(center, monkeypatch):
    from tinyassets.boxes.provider import BoxHandle, ExecLimits
    from tinyassets.control_plane.binding import SessionBinding, use_binding
    from tinyassets.universe_soul import soul_present

    box = _Box(present=True)
    binding = SessionBinding(box, BoxHandle(center.name, "owner", 1, "turn"),
                             ExecLimits(), center)
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: (None, None))
    with use_binding(binding):
        assert soul_present(center)
        binding._ended = True
        with pytest.raises(OSError):
            soul_present(center)
    with pytest.raises(RuntimeError, match="provider is unavailable"):
        soul_present(center)


def test_host_files_refuse_links_and_bad_center(center):
    from tinyassets.center_platform_files import is_paused, read_ledger, set_paused

    with pytest.raises(ValueError):
        is_paused("../other")
    victim = center.parent / "victim"
    victim.write_text("do not change")
    link = center / ".pause"
    try:
        link.symlink_to(victim)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a symlink")
    with pytest.raises(OSError):
        set_paused(center, "timestamp")
    assert victim.read_text() == "do not change"
    link.unlink()
    (center / "auto_ship_attempts.jsonl").symlink_to(victim)
    with pytest.raises(OSError):
        read_ledger(center)


def test_host_helpers_use_caller_base_and_missing_centers_are_empty(center, monkeypatch):
    from tinyassets.center_platform_files import (
        center_from_path,
        is_paused,
        read_ledger,
        read_worker_heartbeats,
        set_paused,
    )

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(center.parent / "unrelated"))
    set_paused(center, "now")
    assert is_paused(center)
    missing = center.parent / "missing"
    assert not is_paused(missing)
    assert read_worker_heartbeats(missing, max_bytes=4096) == {}
    assert read_ledger(missing) is None
    assert read_attempts(missing) == []
    assert not missing.exists()
    assert center_from_path(center, base_path=center.parent) == center.name
    with pytest.raises(ValueError, match="outside caller base"):
        center_from_path(center, base_path=center.parent / "unrelated")


@pytest.mark.parametrize("session", [False, True])
def test_linked_center_refused_before_any_box_bind_or_read(center, monkeypatch, session):
    from tinyassets.boxes.provider import BoxHandle, ExecLimits
    from tinyassets.control_plane.binding import SessionBinding, use_binding
    from tinyassets.owner_box_reads import box_exists, box_read

    alias = center.parent / "cc-alias"
    alias.symlink_to(center, target_is_directory=True)
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: pytest.fail("linked center reached provider"))
    monkeypatch.setattr("tinyassets.broker.owner_identities.admitted_owner",
                        lambda *a, **k: pytest.fail("linked center reached admission"))
    binding = SessionBinding(None, BoxHandle(alias.name, "acct-a", 1, "test"),
                             ExecLimits(), alias) if session else None
    with use_binding(binding):
        with pytest.raises(OSError):
            box_read(alias, "soul.md", max_bytes=100)
        with pytest.raises(OSError):
            box_exists(alias, "soul.md")
        with pytest.raises(OSError):
            home_is_complete(center.parent, alias.name)


@pytest.mark.parametrize("action,text", [("control_daemon", "pause"),
                                        ("control_daemon", "status"),
                                        ("read_premise", ""), ("inspect", "")])
def test_linked_center_control_daemon_never_writes_target(center, monkeypatch, action, text):
    from tinyassets.api import universe as api
    from tinyassets.center_platform_files import set_paused

    alias = center.parent / "cc-alias"
    alias.symlink_to(center, target_is_directory=True)
    authorized = []

    def allows(uid, **kwargs):
        authorized.append(uid)
        return uid == alias.name

    monkeypatch.setattr(api.permissions, "universe_access_allows", allows)
    monkeypatch.setattr(api, "_dispatch_scope_error", lambda *a, **k: None)
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: pytest.fail("authorized alias reached B's box"))
    with pytest.raises(ValueError, match="linked center"):
        api._universe_impl(action, universe_id=alias.name, text=text)
    assert authorized == [alias.name]
    with pytest.raises(OSError):
        set_paused(alias, "must not reach B")
    assert not (center / ".pause").exists()
    assert alias.is_symlink()


def test_linked_center_heartbeat_and_ledger_refuse_target(center):
    from tinyassets.center_platform_files import (
        read_worker_heartbeats,
        write_worker_heartbeat,
    )

    alias = center.parent / "cc-alias"
    alias.symlink_to(center, target_is_directory=True)
    with pytest.raises(OSError):
        write_worker_heartbeat(alias, "worker", b"{}")
    with pytest.raises(OSError):
        read_worker_heartbeats(alias, max_bytes=4096)
    with pytest.raises(OSError):
        read_attempts(alias)
    with pytest.raises(OSError):
        record_attempt(alias, ShipAttempt(
            ship_attempt_id="blocked", ship_status="skipped", created_at="t", updated_at="t",
        ))
    assert not list(center.iterdir())
