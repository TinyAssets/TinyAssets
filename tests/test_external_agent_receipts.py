"""Catalogue status comes from durable attempts and never creates state on read."""
import asyncio
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tinyassets.external_agents import receipts
from tinyassets.storage.provider_work_authority import db_path


def test_status_is_read_only_and_a_failed_attempt_has_a_reason(tmp_path):
    where = dict(owner="owner", universe_id="u-owner", agent="codex")
    assert receipts.status(tmp_path, **where) == "never_run"
    assert not db_path(tmp_path).exists()

    attempt = receipts.admit(tmp_path, **where, package_digest="package",
                             custody_digest="custody")
    assert receipts.status(tmp_path, **where) == "never_run"
    receipts.start(tmp_path, attempt)
    assert receipts.status(tmp_path, **where) == "refreshing"
    receipts.finish(tmp_path, attempt, outcome="failed", reason="offline")
    assert receipts.status(tmp_path, **where) == "failed: offline"

    receipts.admit(tmp_path, **where, package_digest="package",
                   custody_digest="custody")
    later = datetime.now(timezone.utc) + timedelta(seconds=receipts.DEADLINE_SECONDS + 1)
    assert receipts.status(tmp_path, **where, now=later) == "failed: not_started"


def test_in_flight_refresh_stays_refreshing_through_read_budget(tmp_path):
    where = dict(owner="owner", universe_id="u-owner", agent="codex")
    attempt = receipts.admit(tmp_path, **where, package_digest="package",
                             custody_digest="custody")
    receipts.start(tmp_path, attempt)
    at = datetime.now(timezone.utc) + timedelta(seconds=receipts.REFRESH_TIMEOUT_SECONDS + 1)
    assert receipts.status(tmp_path, **where, now=at) == "refreshing"


def test_job_receipt_distinguishes_resolved_and_unreported_model(tmp_path):
    fields = dict(owner="owner", universe_id="u-owner", agent="codex",
                  package_digest="package", custody_digest="custody",
                  requested_model="gpt-6.1-sol")
    attempt = receipts.admit_job(tmp_path, **fields)
    receipts.finish_job(tmp_path, attempt, outcome="succeeded",
                        resolved_model="gpt-6.1-sol", input_tokens=3,
                        output_tokens=4)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        row = conn.execute("""SELECT requested_model, resolved_model, reported_model,
            model_status, usage_provenance FROM external_agent_job_receipts""").fetchone()
    assert row == ("gpt-6.1-sol", "gpt-6.1-sol", "", "unknown", "cli_reported")


def test_job_stream_does_not_report_thread_configuration_as_answering_model():
    from tinyassets.external_agents.jsonrpc_job import JsonRpcJob
    from tinyassets.external_agents.registry import resolve
    from tinyassets.providers.base import ModelConfig

    class Process:
        returncode = None

        def __init__(self):
            self.stdout = asyncio.StreamReader()
            self.stdin = self

        def write(self, data):
            request = json.loads(data)
            method = request.get("method")
            ident = request.get("id")
            if ident is None:
                return
            result = ({"thread": {"id": "thread-1", "model": "gpt-6.1-sol"}}
                      if method == "thread/start" else {})
            self.stdout.feed_data((json.dumps({"id": ident, "result": result}) + "\n").encode())
            if method == "turn/start":
                for event, params in (
                    ("turn/started", {"turn": {"id": "turn-1"}}),
                    ("thread/tokenUsage/updated", {"tokenUsage": {"last": {
                        "inputTokens": 3, "outputTokens": 4}}}),
                    ("turn/completed", {"turn": {"status": "completed", "items": [
                        {"type": "agentMessage", "text": "done"}]}}),
                ):
                    self.stdout.feed_data((json.dumps({"method": event,
                                                       "params": params}) + "\n").encode())

        async def drain(self):
            pass

    async def run():
        job = resolve("codex").row["job"]
        turn = JsonRpcJob(Process(), job=job, agent="codex", tools=None,
                          profile=ModelConfig().stream_timeout_profile(),
                          start=time.monotonic(), turn_wait=5, tool_wait=5)
        return await turn.run(definition=SimpleNamespace(instructions="", tools=[]),
                              model="gpt-6.1-sol", input_text="hello", effort=None,
                              workdir="/agent-home/jobs/test")

    outcome = asyncio.run(run())
    assert outcome.configured_model == "gpt-6.1-sol"
    assert outcome.reported_model == ""
    assert outcome.messages == ["done"]


def test_missing_package_binary_fails_loudly(tmp_path, monkeypatch):
    from tinyassets.exceptions import ExternalAgentUnavailable, ProviderError
    from tinyassets.external_agents import runner
    from tinyassets.providers.base import ModelConfig

    package = SimpleNamespace(executable=tmp_path / "absent", row={
        "auth": {"service": "codex"}})
    monkeypatch.setattr(runner.registry, "resolve", lambda _: package)
    center = tmp_path / "u-owner"
    center.mkdir()
    custody = SimpleNamespace(universe_dir=center, owner_user_id="owner",
                              reference=SimpleNamespace(service="codex"))
    with pytest.raises(ExternalAgentUnavailable) as failure:
        asyncio.run(runner._run_job_locked(
            "codex", None, custody, prompt="hello", system="system",
            config=ModelConfig(invocation_owner_user_id="owner")))
    assert failure.value.reason == "not_installed"
    with pytest.raises(ProviderError, match="not_installed"):
        asyncio.run(runner._run_catalogue_locked("codex", None, custody))


def test_catalogue_failure_is_receipted_and_loud(tmp_path, monkeypatch):
    from tinyassets.exceptions import ProviderError
    from tinyassets.external_agents import runner

    executable = tmp_path / "cli"
    executable.write_text("fixture")
    package = SimpleNamespace(executable=executable, digest="package", row={
        "auth": {"service": "codex"}})
    monkeypatch.setattr(runner.registry, "resolve", lambda _: package)
    monkeypatch.setattr(runner, "materialize", lambda *_: (_ for _ in ()).throw(
        RuntimeError("secret detail")))
    center = tmp_path / "u-owner"
    center.mkdir()
    custody = SimpleNamespace(universe_dir=center, owner_user_id="owner",
                              reference=SimpleNamespace(service="codex",
                                                        reference_digest="custody"))
    with pytest.raises(ProviderError, match="catalogue failed: failed") as failure:
        asyncio.run(runner._run_catalogue_locked("codex", None, custody))
    assert "secret detail" not in str(failure.value)
    assert receipts.status(tmp_path, owner="owner", universe_id=center.name,
                           agent="codex") == "failed: failed"
