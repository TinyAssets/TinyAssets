"""Codex configuration is named without fabricating answering-model evidence."""

import contextlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.support.owned_spawn import install_fake_owned_spawn
from tests.test_codex_cli_compat import _RECORDED_0_153_STREAM
from tinyassets.providers import codex_provider as provider
from tinyassets.providers.base import ModelConfig
from tinyassets.providers.execution_receipt import (
    ExecutionReceipt,
    WriterExecutionReceipt,
    normalize_execution_receipt,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("persistent,requested", [(True, ""), (False, "owner-picked")])
async def test_codex_configuration_reaches_reply_and_stored_receipt(
    monkeypatch, tmp_path, persistent, requested,
):
    auth = tmp_path / "auth"
    auth.mkdir()
    store = tmp_path / "sessions"
    store.mkdir()
    monkeypatch.setattr(provider, "_resolve_codex_cmd", lambda: (["codex"], False))
    monkeypatch.setattr(provider, "get_sandbox_status", lambda: {
        "bwrap_available": True, "bwrap_path": "fake-bwrap",
    })
    monkeypatch.setattr(provider, "subprocess_env_for_provider", lambda *a, **kw: {
        "CODEX_HOME": str(auth),
    })
    monkeypatch.setattr(provider, "_codex_sandbox_mounts", lambda _: [])
    monkeypatch.setattr(provider, "_codex_home_file_mounts", lambda _: [])
    proc = AsyncMock(returncode=0)
    install_fake_owned_spawn(monkeypatch, provider.__name__, return_value=proc)
    monkeypatch.setattr(
        provider.agent_sessions, "exclusive", lambda _: contextlib.nullcontext(True),
    )
    monkeypatch.setattr(provider.agent_sessions, "native_store", lambda *a: store)
    monkeypatch.setattr(provider.agent_sessions, "resumable", lambda *a, **kw: None)
    monkeypatch.setattr(provider.agent_sessions, "save", lambda *a, **kw: None)

    async def stream(*a, **kw):
        # The CLI's rollout shape, not assistant text or a platform default.
        (store / "rollout-00000000-0000-0000-0000-000000000000.jsonl").write_text(
            json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(),
                        "type": "turn_context", "payload": {"model": "cli-selected"}}) + "\n",
            encoding="utf-8",
        )
        return _RECORDED_0_153_STREAM, b""

    monkeypatch.setattr(provider, "_stream_codex_exec", stream)
    result = await provider.CodexProvider().complete(
        "prompt", "system", ModelConfig(
            sandbox_workspace=True, native_model_id=requested,
            agent_session=SimpleNamespace(key="main") if persistent else None,
        ), universe_dir=tmp_path,
    )
    receipt = WriterExecutionReceipt()
    receipt.observe(result)
    projected = receipt.projection()
    assert projected["configured_model"] == ("cli-selected" if persistent else requested)
    assert projected["model"] == "" and projected["model_status"] == "unknown"
    assert result.reported_model == ""
    assert normalize_execution_receipt(projected) == projected
    assert normalize_execution_receipt(ExecutionReceipt(**projected)) == projected


@pytest.mark.parametrize("record", [
    {"timestamp": "2000-01-01T00:00:00Z", "type": "turn_context",
     "payload": {"model": "previous-turn"}},
    {"timestamp": "2099-01-01T00:00:00Z", "type": "agent_message",
     "payload": {"model": "assistant-claim"}},
    {"timestamp": "2099-01-01T00:00:00Z", "type": "turn_context",
     "payload": {"model": "bad\nlabel"}},
])
def test_rollout_never_attributes_old_turns_or_assistant_claims(tmp_path, record):
    store = tmp_path / "sessions"
    store.mkdir()
    (store / "rollout-thread-id.jsonl").write_text(json.dumps(record), encoding="utf-8")
    assert provider._configured_rollout_model(tmp_path, store, "thread-id", 1_700_000_000) == ""
