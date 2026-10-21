"""Owner-only helpers never select shared-daemon execution from runtime state."""

import pytest

from tinyassets import owner_activity, owner_runtime
from tinyassets.agent_loop import served_chat
from tinyassets.control_plane import binding
from tinyassets.providers.base import get_sandbox_status
from tinyassets.universe_files import ensure_data_directory


def forbidden(*args, **kwargs):
    raise AssertionError("owner-only code must not select a shared-daemon path")


@pytest.mark.verifies('owner-daemon-in-box:T8')
def test_local_directory_creation_never_uses_shared_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(owner_runtime, "current", forbidden)
    monkeypatch.setattr(binding, "file_binding", forbidden)
    center = tmp_path / "center"
    center.mkdir()
    target = center / "empty" / "nested"
    ensure_data_directory(target)
    assert target.is_dir()
    ensure_data_directory(target)
    assert list(target.iterdir()) == []




@pytest.mark.verifies('owner-daemon-in-box:T8')
def test_owner_sandbox_status_never_reads_per_call_provider(monkeypatch):
    monkeypatch.setattr(owner_runtime, "current", forbidden)
    assert not hasattr(served_chat, "configured_box_provider")
    assert get_sandbox_status() == {"box_available": True, "reason": ""}


@pytest.mark.verifies('owner-daemon-in-box:C12')
def test_owner_work_always_holds_gate_and_refuses_after_drain(monkeypatch):
    activity = owner_activity.Activity()
    monkeypatch.setattr(owner_activity, "activity", activity)
    monkeypatch.setattr(owner_runtime, "current", forbidden)
    calls = []

    @owner_activity.owner_work
    def work(value):
        calls.append(value)
        assert activity.status(drain=True)["active"] == 1
        assert activity.draining is False
        return value

    assert work("done") == "done"
    assert activity.active == 0
    assert activity.status(drain=True)["draining"] is True
    with pytest.raises(RuntimeError, match="draining"):
        work("never")
    assert calls == ["done"]
    assert activity.active == 0


@pytest.mark.verifies('owner-daemon-in-box:C12')
def test_owner_work_releases_gate_after_failure(monkeypatch):
    activity = owner_activity.Activity()
    monkeypatch.setattr(owner_activity, "activity", activity)
    monkeypatch.setattr(owner_runtime, "current", forbidden)

    @owner_activity.owner_work
    def work():
        assert activity.active == 1
        raise ValueError("failed work")

    with pytest.raises(ValueError, match="failed work"):
        work()
    assert activity.active == 0
    assert activity.status(drain=True)["draining"] is True



@pytest.mark.verifies('owner-daemon-in-box:T8')
def test_session_loads_local_agent_without_a_shared_binding(tmp_path, monkeypatch):
    from tinyassets.control_plane import session

    center = tmp_path / "center"
    center.mkdir()
    (center / "AGENTS.md").write_text("Local owner instructions.")
    assert not hasattr(served_chat, "configured_box_provider")
    monkeypatch.setattr(owner_runtime, "current", forbidden)
    monkeypatch.setattr(binding, "file_binding", lambda root: None)
    seen = []
    def execute(agent, message, parent):
        seen.append((agent.instructions, message, parent.owner))
        return "done"
    monkeypatch.setattr(session, "_owner_turn", execute)
    parent = session.OwnerTurn(tmp_path, "center", "alice", "founder", "thread:owner")
    assert session.start("main", "hello", parent) == "done"
    assert seen == [("Local owner instructions.", "hello", "alice")]
    assert binding.current_binding() is None
