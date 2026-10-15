"""Session admission precedes owner files; disconnect never means owner stop."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets.boxes.provider import (
    BoxAuthError,
    BoxBusy,
    BoxHandle,
    DirEntry,
    DirPage,
    ExecLimits,
    FileRead,
    Snapshot,
)
from tinyassets.control_plane import binding, session


class Files:
    def __init__(self):
        self.events = []
        self.contents = {"AGENTS.md": b"Private owner instructions.",
                         "settings.yaml": b"schema_version: 1\n"}

    def bind(self, center, *, account_id, turn_id):
        self.events.append(("bind", center, account_id, turn_id))
        return BoxHandle(center, account_id, 1, turn_id)

    def read(self, handle, path, *, max_bytes):
        self.events.append(("read", handle, path))
        if path not in self.contents:
            raise FileNotFoundError(path)
        data = self.contents[path]
        if len(data) > max_bytes:
            raise OSError("too large")
        return FileRead(data, 1, len(data))

    def read_many(self, handle, paths, *, max_total):
        self.events.append(("read_many", handle, tuple(paths)))
        files = {path: self.contents[path] for path in paths if path in self.contents}
        if sum(map(len, files.values())) > max_total:
            raise OSError("too large")
        return Snapshot(files, tuple(path for path in paths if path not in files), 1)

    def committed_generation(self, handle):
        return 1

    def list(self, handle, path, **kwargs):
        self.events.append(("list", handle, path))
        if path == "agents":
            return DirPage((DirEntry("scout", "dir", 0),), None)
        if path == "agents/scout":
            return DirPage((DirEntry("settings.yaml", "file", 0),
                            DirEntry("AGENTS.md", "file", 0)), None)
        raise FileNotFoundError(path)

    def end_session(self, handle):
        self.events.append(("cancel", handle))


@pytest.fixture
def admitted(tmp_path, monkeypatch):
    from tinyassets.agent_loop import served_chat

    files = Files()
    monkeypatch.setattr(served_chat, "_box_provider", files)
    monkeypatch.setattr(served_chat, "_box_limits", ExecLimits())
    parent = session.OwnerTurn(tmp_path, "center", "owner", "founder", "thread:owner",
                               execution_id="send:thread:owner:durable-send-id")
    return files, parent


def test_session_binds_before_real_agent_loader_without_host_directory(admitted, monkeypatch):
    files, parent = admitted

    def execute(agent, text, passed):
        assert agent.instructions == "Private owner instructions."
        assert passed.box_binding is binding.current_binding()
        return "done"

    monkeypatch.setattr(session, "_owner_turn", execute)
    assert session.start("main", "hello", parent) == "done"
    assert files.events[0][:3] == ("bind", "center", "owner")
    assert all(event[0] == "read_many" for event in files.events[1:])
    assert not parent.base_path.joinpath("center").exists()
    assert binding.current_binding() is None


def test_reconnect_reuses_durable_session_identity_and_does_not_cancel(admitted, monkeypatch):
    files, parent = admitted
    monkeypatch.setattr(session, "_owner_turn", lambda *args: "done")
    session.start("main", "hello", parent)
    session.start("main", "hello", parent)
    binds = [event for event in files.events if event[0] == "bind"]
    assert binds[0] == binds[1]
    assert not any(event[0] == "cancel" for event in files.events)
    session.start("main", "another", replace(parent, execution_id="send:new"))
    assert [event for event in files.events if event[0] == "bind"][-1] != binds[0]


@pytest.mark.parametrize("stopped", [False, True])
def test_only_explicit_owner_stop_ends_the_box_session(admitted, monkeypatch, stopped):
    from tinyassets import turn_interrupt

    files, parent = admitted
    monkeypatch.setattr(turn_interrupt, "current",
                        lambda: SimpleNamespace(requested=lambda: stopped))

    def disconnected(*args):
        raise ConnectionError("client disconnected")

    monkeypatch.setattr(session, "_owner_turn", disconnected)
    with pytest.raises(ConnectionError):
        session.start("main", "hello", parent)
    assert sum(event[0] == "cancel" for event in files.events) == int(stopped)


def test_missing_stable_identity_refuses_before_files(admitted):
    files, parent = admitted
    with pytest.raises(BoxAuthError, match="stable execution"):
        session.start("main", "hello", replace(parent, execution_id=""))
    assert files.events == []


def test_foreign_parent_binding_refuses_before_loading(admitted):
    files, parent = admitted
    bound = binding.bind_session(provider=files, limits=ExecLimits(),
                                 universe_dir=parent.base_path / "center", owner="owner",
                                 execution_id="stable")
    with pytest.raises(BoxAuthError):
        session.start("main", "hello", replace(parent, owner="someone-else", box_binding=bound))
    assert [event[0] for event in files.events] == ["bind"]


def test_explicit_child_binding_survives_executor_handoff(admitted, monkeypatch):
    files, parent = admitted
    bound = binding.bind_session(provider=files, limits=ExecLimits(),
                                 universe_dir=parent.base_path / "center", owner="owner",
                                 execution_id="stable")
    child = session.ChildOf(parent.base_path, "center", "owner", "main", box_binding=bound)
    monkeypatch.setattr(session, "_child", lambda *args: binding.current_binding())
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(session.start, "main", "child", child).result() is bound
    assert sum(event[0] == "bind" for event in files.events) == 1


def test_child_cannot_replace_its_live_parent_handle(admitted):
    files, parent = admitted
    first = binding.bind_session(provider=files, limits=ExecLimits(),
                                 universe_dir=parent.base_path / "center", owner="owner",
                                 execution_id="first")
    second = binding.bind_session(provider=files, limits=ExecLimits(),
                                  universe_dir=parent.base_path / "center", owner="owner",
                                  execution_id="second")
    child = session.ChildOf(parent.base_path, "center", "owner", "main",
                            box_binding=second)
    with binding.use_binding(first), pytest.raises(BoxAuthError, match="active parent"):
        session.start("main", "child", child)
    assert not any(event[0] == "read_many" for event in files.events)


def test_owner_box_layout_refuses_missing_provider_before_host_read(admitted, monkeypatch):
    from tinyassets import storage_layout
    from tinyassets.agent_loop import served_chat

    _, parent = admitted
    monkeypatch.setattr(storage_layout, "ACTIVE_BOX_LAYOUT", "owner-box")
    monkeypatch.setattr(served_chat, "_box_provider", None)
    with pytest.raises(BoxAuthError, match="not configured"):
        session.start("main", "hello", parent)


def test_bound_file_scope_rejects_another_owner_root(admitted):
    from tinyassets.universe_files import read_universe_file

    files, parent = admitted
    bound = binding.bind_session(provider=files, limits=ExecLimits(),
                                 universe_dir=parent.base_path / "center", owner="owner",
                                 execution_id="stable")
    with binding.use_binding(bound), pytest.raises(BoxAuthError):
        read_universe_file(Path(parent.base_path) / "other", "AGENTS.md")
    assert [event[0] for event in files.events] == ["bind"]


def test_mixed_agent_generations_refuse_before_executor(admitted, monkeypatch):
    files, parent = admitted
    generations = iter((1, 2))
    monkeypatch.setattr(files, "committed_generation", lambda handle: next(generations))
    called = []
    monkeypatch.setattr(session, "_owner_turn", lambda *args: called.append(args))
    with pytest.raises(session.AgentUnavailable):
        session.start("main", "hello", parent)
    assert called == []


def test_named_agent_uses_one_snapshot_for_settings_and_instructions(admitted, monkeypatch):
    files, parent = admitted
    files.contents.update({
        "agents/scout/settings.yaml": b"schema_version: 1\nagent:\n  name: Scout\n",
        "agents/scout/AGENTS.md": b"Scout instructions.",
    })
    seen = []
    monkeypatch.setattr(session, "_owner_turn", lambda agent, *_: seen.append(agent))
    session.start("scout", "hello", parent)
    assert seen[0].name == "Scout"
    assert seen[0].instructions == "Scout instructions."
    assert any(event[0] == "read_many" and event[2] == (
        "agents/scout/settings.yaml", "agents/scout/AGENTS.md")
        for event in files.events)


def test_roster_change_between_pages_refuses_before_agent_files(admitted, monkeypatch):
    files, parent = admitted
    generations = iter((1, 2))
    monkeypatch.setattr(files, "committed_generation", lambda handle: next(generations))
    with pytest.raises(session.AgentUnavailable):
        session.start("scout", "hello", parent)
    assert not any(event[0] == "read_many" for event in files.events)


def test_busy_roster_does_not_look_like_an_empty_agent_list(admitted, monkeypatch):
    files, parent = admitted

    def busy(*args, **kwargs):
        raise BoxBusy("roster busy")

    monkeypatch.setattr(files, "list", busy)
    with pytest.raises(session.AgentUnavailable):
        session.start("scout", "hello", parent)


def test_native_launcher_uses_exact_session_and_rejects_foreign_custody(admitted):
    from tinyassets.control_plane.native_launch import launcher

    files, parent = admitted
    bound = binding.bind_session(provider=files, limits=ExecLimits(),
                                 universe_dir=parent.base_path / "center", owner="owner",
                                 execution_id="stable")
    custody = SimpleNamespace(owner_user_id="owner", universe_dir=bound.universe_dir,
                              reference=SimpleNamespace(universe_id="center"))
    launch = launcher(custody, binding=bound, operation_id="native:usage:3")
    assert launch.handle is bound.handle and launch.provider is files
    assert launch.operation_id == "native:usage:3"
    custody.owner_user_id = "other"
    with pytest.raises(BoxAuthError):
        launcher(custody, binding=bound, operation_id="native:usage:3")


def test_native_launcher_does_not_fall_back_when_binding_is_lost(admitted):
    from tinyassets.control_plane.native_launch import launcher

    _, parent = admitted
    custody = SimpleNamespace(owner_user_id="owner", universe_dir=parent.base_path / "center")
    with pytest.raises(BoxAuthError, match="session binding"):
        launcher(custody, operation_id="native:usage:3")


def test_current_layout_keeps_the_cell_launcher(admitted, monkeypatch):
    from tinyassets.agent_loop import served_chat
    from tinyassets.control_plane.native_launch import launcher
    from tinyassets.role_external_agent import CellLauncher
    from tinyassets.storage_layout import ACTIVE_BOX_LAYOUT

    assert ACTIVE_BOX_LAYOUT == "owner-split"
    monkeypatch.setattr(served_chat, "_box_provider", None)
    _, parent = admitted
    custody = SimpleNamespace(owner_user_id="owner",
                              universe_dir=parent.base_path / "center")
    assert isinstance(launcher(custody, operation_id="catalogue:1"), CellLauncher)
