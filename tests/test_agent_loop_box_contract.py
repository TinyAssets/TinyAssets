"""Thin-loop integration with the canonical provider (local only as a test double)."""

import asyncio
from dataclasses import replace

import pytest

from tinyassets.agent_loop import served_chat
from tinyassets.agent_loop.box_tools import BoxExecutor, BoxOperationRefused
from tinyassets.boxes.provider import BoxAuthError, ExecEvent, ExecLimits
from tinyassets.engine_tool_client import EngineToolError


def test_bound_turn_consumes_real_provider_output_and_does_not_replay(tmp_path, monkeypatch):
    from tinyassets.boxes.local import LocalBoxProvider

    provider = LocalBoxProvider(
        boxes_root=tmp_path / "boxes", state_dir=tmp_path / "state",
        owner_of={"cc-a": "alice", "cc-b": "bob"}.get, allow_unisolated=True,
    )
    monkeypatch.setattr(served_chat, "_box_provider", provider)
    monkeypatch.setattr(served_chat, "_box_limits", ExecLimits())
    try:
        tools, root = served_chat.bind_turn_box(owner="alice", command_center="cc-a", turn_id="t")
        assert root == "/cc"
        command = "printf x >> count; printf 'binary-safe output'; exit 7"
        first = asyncio.run(tools.bash("t:1:1", command))
        assert first == "binary-safe output\n[exit code 7]"
        assert asyncio.run(tools.bash("t:1:1", command)) == first
        handle = provider.bind("cc-a", account_id="alice", turn_id="t")
        assert provider.read(handle, "/cc/count", max_bytes=16).data == b"x"
        with pytest.raises(BoxAuthError):
            served_chat.bind_turn_box(owner="alice", command_center="cc-b", turn_id="t")
        executor = BoxExecutor(provider, replace(handle, account_id="bob"), limits=ExecLimits())
        with pytest.raises(BoxOperationRefused):
            asyncio.run(executor.run("foreign", ["/bin/true"], wall_seconds=5))
        assert provider.read(handle, "/cc/count", max_bytes=16).data == b"x"
    finally:
        provider.close()


@pytest.mark.parametrize("reason", ["timeout", "output_limit", "cancelled"])
def test_provider_kill_reason_survives_the_tool_boundary(reason):
    class Provider:
        def stream(self, *args, **kwargs):
            yield ExecEvent("output", offset=3, data=b"abc")
            yield ExecEvent("exit", exit_code=137, killed=reason)

    outcome = BoxExecutor(Provider(), object(), limits=ExecLimits())._collect("e", "o", 100)
    assert outcome.output == b"abc"
    assert outcome.exit_code == 137
    assert outcome.killed == reason


@pytest.mark.parametrize("code,reason", [(None, "unknown_after_restore"), (None, None),
                                        (0, "unknown_after_restore")])
def test_unconfirmed_exit_holds_the_turn_instead_of_returning_a_tool_result(code, reason):
    class Provider:
        def stream(self, *args, **kwargs):
            yield ExecEvent("output", offset=3, data=b"abc")
            yield ExecEvent("exit", exit_code=code, killed=reason)

    with pytest.raises(EngineToolError) as raised:
        BoxExecutor(Provider(), object(), limits=ExecLimits())._collect("e", "o", 100)
    assert raised.value.outcome == "unknown"
