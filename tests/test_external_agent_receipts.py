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


def test_package_turn_has_native_receipt_and_no_subscription_call_price(tmp_path, monkeypatch):
    from tinyassets.external_agents import runner
    from tinyassets.external_agents.jsonrpc_job import TurnOutcome
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.execution_receipt import receipt_from_response

    center = tmp_path / "u-owner"
    center.mkdir()
    executable = tmp_path / "cli"
    executable.write_text("fixture", encoding="utf-8")
    package = SimpleNamespace(executable=executable, digest="package", row={
        "family": "openai",
        "auth": {"service": "codex", "home_dir": "codex", "home_env": "CODEX_HOME"},
        "job": {"transport": "jsonrpc", "argv": ["app-server"],
                "events": {"done": {"success": "completed"}}},
    })
    monkeypatch.setattr(runner.registry, "resolve", lambda _: package)
    monkeypatch.setattr(runner, "materialize", lambda *_: {"auth.json": b"token"})

    async def no_tools(*_):
        return None

    monkeypatch.setattr(runner, "_engine_tools", no_tools)

    class Job:
        def __init__(self, *_args, **_kwargs):
            self.outcome = TurnOutcome()

        async def run(self, **_kwargs):
            self.outcome = TurnOutcome(
                thread_id="session", configured_model="gpt-6.1-sol",
                messages=["done"], input_tokens=100, output_tokens=50,
                usage_seen=True, status="completed")
            return self.outcome

    monkeypatch.setattr("tinyassets.external_agents.jsonrpc_job.JsonRpcJob", Job)

    class Launch:
        async def spawn(self, *_args, **_kwargs):
            return object()

        async def close(self, _proc):
            return {}

    custody = SimpleNamespace(universe_dir=center, owner_user_id="owner",
                              reference=SimpleNamespace(service="codex",
                                                        reference_digest="custody"),
                              current=lambda: True)
    response = asyncio.run(runner._run_job_locked(
        "codex", Launch(), custody, prompt="hello", system="system",
        config=ModelConfig(invocation_owner_user_id="owner",
                           native_model_id="gpt-6.1-sol")))
    receipt = receipt_from_response(response)
    assert receipt.execution == "native_agent"
    assert receipt.cost_microunits == 0
    assert (receipt.input_tokens, receipt.output_tokens) == (100, 50)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        row = conn.execute(
            "SELECT execution, outcome, reason FROM external_agent_job_receipts").fetchone()
    assert row == ("native_agent", "succeeded", "")


@pytest.mark.parametrize("message, category, marked", [
    ("Unknown model gpt-5.6-sol", "model_unavailable", True),
    ("Model gpt-5.6-sol is temporarily unavailable due to capacity",
     "transient_capacity", False),
])
def test_package_model_refusal_is_classified_and_remembered(
    tmp_path, monkeypatch, message, category, marked,
):
    from tinyassets.exceptions import ExternalAgentUnavailable, ProviderError
    from tinyassets.external_agents import runner
    from tinyassets.external_agents.jsonrpc_job import TurnOutcome, refusal_category
    from tinyassets.external_agents.registry import resolve
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.shortlist_refresh import SHORTLIST_CACHE
    from tinyassets.storage.refused_models import active_refused_models

    center = tmp_path / "u-owner"
    center.mkdir()
    executable = tmp_path / "cli"
    executable.write_text("fixture")
    package = resolve("codex")
    monkeypatch.setattr(runner.registry, "resolve", lambda _: SimpleNamespace(
        executable=executable, digest=package.digest, row=package.row))
    monkeypatch.setattr(runner, "materialize", lambda *_: {})
    refreshes = []
    monkeypatch.setattr(SHORTLIST_CACHE, "schedule",
                        lambda **kwargs: refreshes.append(kwargs) or True)

    async def no_tools(*_):
        return None

    monkeypatch.setattr(runner, "_engine_tools", no_tools)

    class Job:
        def __init__(self, *_args, **kwargs):
            self.job = kwargs["job"]
            self.phase = self.job["start"]["method"]
            self.outcome = TurnOutcome()

        async def run(self, **_kwargs):
            raise ProviderError(
                f"external agent refused thread/start:{refusal_category({'message': message})}")

    monkeypatch.setattr("tinyassets.external_agents.jsonrpc_job.JsonRpcJob", Job)

    class Launch:
        async def spawn(self, *_args, **_kwargs):
            return object()

        async def close(self, _proc):
            return {}

    custody = SimpleNamespace(universe_dir=center, owner_user_id="owner",
                              reference=SimpleNamespace(service="codex",
                                                        reference_digest="custody"))
    with pytest.raises(ExternalAgentUnavailable) as failure:
        asyncio.run(runner._run_job_locked(
            "codex", Launch(), custody, prompt="hello", system="system",
            config=ModelConfig(invocation_owner_user_id="owner",
                               native_model_id="gpt-5.6-sol")))
    expected = (f"job_failed:refused:thread/start:{category}"
                if marked else "job_failed:refused")
    assert failure.value.reason == expected
    marks = active_refused_models(tmp_path, owner_user_id="owner")
    assert [(mark.connection_id, mark.model_id) for mark in marks] == (
        [("codex", "gpt-5.6-sol")] if marked else [])
    assert refreshes == ([{"base": tmp_path, "owner": "owner",
                          "universe_id": "u-owner", "provider": "codex"}]
                         if marked else [])


def test_package_refusal_category_never_carries_upstream_text():
    from tinyassets.external_agents.jsonrpc_job import refusal_category

    assert refusal_category({"message": "Unknown model SECRET_TOKEN"}) == "model_unavailable"
    assert refusal_category({"message": "SECRET_TOKEN"}) == "unknown"
    assert refusal_category({"message": "Model is temporarily unavailable"}) == (
        "transient_capacity")
    assert refusal_category({"message": "High demand for this model"}) == (
        "transient_capacity")


def test_package_rpc_refusal_names_phase_and_sanitized_category():
    from tinyassets.exceptions import ProviderError
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
            if "id" not in request:
                return
            answer = ({"error": {"message": "Model gpt-5.6-sol is not available; SECRET"}}
                      if request["method"] == "thread/start" else {"result": {}})
            self.stdout.feed_data((json.dumps({"id": request["id"], **answer}) + "\n").encode())

        async def drain(self):
            pass

    async def run():
        turn = JsonRpcJob(
            Process(), job=resolve("codex").row["job"], agent="codex", tools=None,
            profile=ModelConfig().stream_timeout_profile(), start=time.monotonic(),
            turn_wait=5, tool_wait=5)
        await turn.run(definition=SimpleNamespace(instructions="", tools=[]),
                       model="gpt-5.6-sol", input_text="hello", effort=None,
                       workdir="/agent-home/jobs/test")

    with pytest.raises(ProviderError) as failure:
        asyncio.run(run())
    assert str(failure.value) == "external agent refused thread/start:model_unavailable"


def test_stale_source_and_refused_model_remain_pickable():
    from dataclasses import replace

    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.model_options import model_options_document
    from tinyassets.providers.model_policy import (
        Catalog,
        ConnectionModels,
        Ineligible,
        Interaction,
        Model,
        ModelPolicy,
        ModelRef,
        Pricing,
    )

    ref = ModelRef("codex", "gpt-6-sol")
    listed = Model("gpt-6-sol", True, frozenset({"text"}),
                   pricing=Pricing("fresh", unmetered=True),
                   availability_basis="executor_enumerated")
    other = Model("gpt-6.1-sol", True, frozenset({"text"}),
                  pricing=Pricing("fresh", unmetered=True),
                  availability_basis="executor_enumerated")
    default = Model("", True, frozenset({"text"}),
                    pricing=Pricing("fresh", unmetered=True),
                    availability_basis="executor_default")
    catalog = Catalog("owner", "u-owner", (ConnectionModels(
        "codex", "native-subscription:codex", "subscription", "fresh", True, True,
        (default, listed, other), default_model_id=""),))
    plan = AgentModelPlan(catalog, ModelPolicy(0, "automatic", ()),
                          Interaction(True, frozenset({"text"}), frozenset()),
                          refused_models=(ref,))
    document = model_options_document(
        catalog, plan, (Ineligible(ModelRef("codex", ""), "stale", scope="source"),))
    rows = {row["reference"]["model_id"]: row for row in document["options"]}
    assert all(row["in_candidate_catalog"] and row["reasons"] == []
               for row in rows.values())
    assert "recently_refused" in rows[ref.model_id]["labels"]
    assert document["order"][-1] == {"provider_ref": "codex", "model_id": ref.model_id}
    assert document["source_failures"] == [{
        "provider_ref": "codex",
        "reasons": [{"reason": "stale", "component": ""}],
    }]
    retry = model_options_document(catalog, replace(plan, policy=ModelPolicy(
        0, "explicit", (), saved_default=ref)),
        (Ineligible(ModelRef("codex", ""), "stale", scope="source"),))
    retried = next(row for row in retry["options"]
                   if row["reference"]["model_id"] == ref.model_id)
    assert retried["in_candidate_catalog"] and retried["reasons"] == []


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
    from tinyassets.providers.base import ProviderResponse
    from tinyassets.providers.execution_receipt import (
        normalize_execution_receipt,
        receipt_from_response,
    )

    receipt = normalize_execution_receipt(receipt_from_response(ProviderResponse(
        text=outcome.messages[-1], provider="codex", model="gpt-6.1-sol",
        family="codex", latency_ms=0,
        requested_model="gpt-6.1-sol",
        configured_model=outcome.configured_model,
        reported_model=outcome.reported_model,
    )))
    assert receipt["requested_model"] == "gpt-6.1-sol"
    assert receipt["configured_model"] == "gpt-6.1-sol"
    assert receipt["model_status"] == "unknown"
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
