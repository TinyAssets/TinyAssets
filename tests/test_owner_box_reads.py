"""Provider contract tests that also execute on Windows."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets.boxes.provider import BoxHandle, DirEntry, DirPage, ExecLimits, FileRead
from tinyassets.control_plane.binding import SessionBinding, use_binding
from tinyassets.owner_box_reads import box_exists, box_list, box_read, box_stat

pytestmark = pytest.mark.role_split


@pytest.fixture
def center(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("tinyassets.broker.owner_identities.admitted_owner",
                        lambda root, *, center: "owner")
    (tmp_path / "center").mkdir()
    return tmp_path / "center"


def test_missing_provider_is_not_missing_content(center, monkeypatch):
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: (None, None))
    with pytest.raises(RuntimeError, match="provider is unavailable"):
        box_exists(center, "soul.md")


def test_admitted_binding_validates_owner_and_skips_configured_provider(center, monkeypatch):
    provider = SimpleNamespace(stat=lambda handle, path: SimpleNamespace(kind="file"))
    binding = SessionBinding(provider, BoxHandle(center.name, "owner", 1, "turn"),
                             ExecLimits(), center)
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: pytest.fail("must use the admitted provider"))
    monkeypatch.setattr("tinyassets.broker.owner_identities.admitted_owner",
                        lambda *a, **k: pytest.fail("matching session is already admitted"))
    with use_binding(binding):
        assert box_exists(center, "soul.md")
        binding._ended = True
        with pytest.raises(OSError, match="owner/center"):
            box_stat(center, "soul.md")


def test_other_center_binds_broker_admitted_owner(center, monkeypatch):
    calls = []

    def bind(name, *, account_id, turn_id):
        calls.append((name, account_id))
        return BoxHandle(name, account_id, 1, turn_id)

    provider = SimpleNamespace(bind=bind, stat=lambda handle, path: None)
    binding = SessionBinding(provider, BoxHandle("other", "other-owner", 1, "turn"),
                             ExecLimits(), center.parent / "other")
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: (provider, None))
    with use_binding(binding):
        assert not box_exists(center, "soul.md")
    assert calls == [("center", "owner")]


@pytest.mark.parametrize("path", ["../soul.md", "/soul.md", "a/../b", "a\\b", "a\0b"])
@pytest.mark.parametrize("operation", [box_stat, box_list, box_read])
def test_invalid_relpaths_never_reach_provider(center, monkeypatch, path, operation):
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: pytest.fail("invalid path must fail before binding"))
    with pytest.raises(OSError):
        kwargs = {"max_bytes": 10} if operation is box_read else {}
        operation(center, path, **kwargs)


def test_no_host_metadata_is_consulted(center, monkeypatch):
    provider = SimpleNamespace(
        bind=lambda name, **kw: BoxHandle(name, kw["account_id"], 1, kw["turn_id"]),
        read=lambda *a, **kw: FileRead(b"box", 1, 3),
    )
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: (provider, None))
    real_stat, real_resolve = Path.stat, Path.resolve

    def guarded(operation):
        def call(path, *args, **kwargs):
            if path == center or path.is_relative_to(center):
                pytest.fail("host owner metadata")
            return operation(path, *args, **kwargs)
        return call

    monkeypatch.setattr(Path, "stat", guarded(real_stat))
    monkeypatch.setattr(Path, "resolve", guarded(real_resolve))
    assert box_read(center, "identity.md", max_bytes=3) == b"box"


@pytest.mark.parametrize("data,size", [(b"abc", 4), (b"ab", 3)])
def test_oversized_or_incomplete_reads_fail(center, monkeypatch, data, size):
    provider = SimpleNamespace(
        bind=lambda name, **kw: BoxHandle(name, kw["account_id"], 1, kw["turn_id"]),
        read=lambda *a, **kw: FileRead(data, 1, size),
    )
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: (provider, None))
    with pytest.raises(OSError, match="bound or is incomplete"):
        box_read(center, "soul.md", max_bytes=3)


def test_pagination_and_cyclic_cursor_refusal(center, monkeypatch):
    pages = iter([DirPage((DirEntry("a", "file", 1),), "a"),
                  DirPage((DirEntry("b", "file", 1),), None)])
    provider = SimpleNamespace(
        bind=lambda name, **kw: BoxHandle(name, kw["account_id"], 1, kw["turn_id"]),
        list=lambda *a, **kw: next(pages),
    )
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: (provider, None))
    assert [entry.name for entry in box_list(center, "notes")] == ["a", "b"]
    provider.list = lambda *a, **kw: DirPage((), "same")
    with pytest.raises(RuntimeError, match="cursor did not advance"):
        box_list(center, "notes")


@pytest.mark.parametrize("stub", [False, True])
def test_unadmitted_reads_never_bind_or_materialize(center, monkeypatch, stub):
    from tinyassets.owner_box_reads import UnadmittedCenter, box_tree_size
    from tinyassets.universe_soul import soul_present

    if not stub:
        center.rmdir()

    def denied(*args, **kwargs):
        raise PermissionError("center owner admission refused")

    monkeypatch.setattr("tinyassets.broker.owner_identities.admitted_owner", denied)
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: pytest.fail("unproven center reached provider"))
    assert not soul_present(center)
    assert not box_exists(center, "soul.md")
    for operation in (box_stat, box_list, box_read, box_tree_size):
        with pytest.raises(UnadmittedCenter):
            operation(center, "soul.md", **({"max_bytes": 10} if operation is box_read else {}))
    assert center.exists() is stub
    assert not list(center.iterdir()) if stub else not center.exists()


def test_broker_owner_is_independent_of_actor_and_ownership_table(center, monkeypatch):
    from tinyassets.api import permissions

    monkeypatch.setattr(permissions, "current_actor_id", lambda: "collaborator")
    monkeypatch.setattr("tinyassets.universe_owner.owner_of",
                        lambda *a, **k: pytest.fail("ownership table is not box admission"))
    calls = []

    def bind(name, *, account_id, turn_id):
        calls.append((name, account_id))
        return BoxHandle(name, account_id, 1, turn_id)

    provider = SimpleNamespace(bind=bind, read=lambda *a, **k: FileRead(b"box", 1, 3))
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: (provider, None))
    assert box_read(center, "soul.md", max_bytes=3) == b"box"
    assert calls == [(center.name, "owner")]
