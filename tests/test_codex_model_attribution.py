"""Codex configuration is named without fabricating answering-model evidence."""

import pytest

from tests.test_codex_app_server import served  # noqa: F401 - the shared fixture
from tinyassets.providers.execution_receipt import (
    ExecutionReceipt,
    WriterExecutionReceipt,
    normalize_execution_receipt,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", ["", "owner-picked"])
async def test_codex_configuration_reaches_reply_and_stored_receipt(served, requested):  # noqa: F811
    run, _launch, _state, config, _root = served
    result, server = await run(cfg=config(native_model_id=requested))
    receipt = WriterExecutionReceipt()
    receipt.observe(result)
    projected = receipt.projection()
    # The thread names its configured model (the CLI's own pick when none was
    # requested); nothing in the protocol names the model that answered.
    assert projected["configured_model"] == (requested or "cli-selected")
    assert projected["model"] == "" and projected["model_status"] == "unknown"
    assert result.reported_model == ""
    if requested:
        assert server.requests("thread/start")[0]["params"]["model"] == requested
        assert projected["requested_model"] == requested
    else:
        assert "model" not in server.requests("thread/start")[0]["params"]
        assert "requested_model" not in projected
    assert normalize_execution_receipt(projected) == projected
    assert normalize_execution_receipt(ExecutionReceipt(**projected)) == projected


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["bad\nlabel", "x" * 201])
async def test_an_unprintable_configured_model_is_never_named(served, label):  # noqa: F811
    from tests.support.fake_codex_app_server import FakeAppServer

    run, *_ = served
    result, _ = await run(server=FakeAppServer(default_model=label))
    assert result.configured_model == ""


@pytest.mark.parametrize("claim", [
    {"method": "turn/started", "params": {"turn": {"id": "old", "model": "previous-turn"}}},
    {"method": "item/completed", "params": {"item": {
        "type": "agentMessage", "text": "I am assistant-claim", "model": "assistant-claim"}}},
    {"method": "thread/started", "params": {"thread": {"id": "thr-1", "model": "bad\nlabel"}}},
])
@pytest.mark.asyncio
async def test_rollout_never_attributes_old_turns_or_assistant_claims(served, claim):  # noqa: F811
    """Only the thread record names the configured model; nothing streamed
    during the turn -- an earlier turn, the assistant's own words -- does."""
    from tests.support.fake_codex_app_server import ScriptedAppServer, finished

    run, *_ = served
    result, _ = await run(server=ScriptedAppServer([(0, claim), *finished()]))
    assert result.configured_model == "cli-selected"
    assert result.reported_model == ""
