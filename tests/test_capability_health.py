"""Pure accounting and report contracts; execution is proved in the image."""

import asyncio
import json
from pathlib import Path

import pytest

from tinyassets import capability_health as health
from tinyassets.core_capabilities import CAPABILITIES, failure_code, validate_report


def test_rates_detect_total_failure_and_spikes_without_retaining_user_material():
    now = [0]
    rates = health.CapabilityRates(lambda: now[0])
    for _ in range(100):
        rates.record("bash")
    now[0] = 301
    for owner in ("owner-a", "owner-b"):
        for _ in range(3):
            rates.record(
                "bash",
                failure_code(ValueError("invalid owner content frame /private")),
                owner=owner,
            )
    for _ in range(4):
        rates.record("bash")
    snapshot = rates.snapshot()
    (alarm,) = snapshot["alarms"]
    assert alarm["code"] == "content_frame_invalid" and alarm["rate"] == 0.6
    assert alarm["baseline_samples"] == 100 and alarm["samples"] == 10
    assert "/private" not in json.dumps(snapshot)
    fresh = health.CapabilityRates(lambda: now[0])
    for owner in ("a", "b"):
        for _ in range(3):
            fresh.record(
                "connected_service", failure_code("inference usage authority refused"), owner=owner
            )
    assert fresh.snapshot()["alarms"][0]["rate"] == 1
    now[0] += health.HISTORY + 60
    assert rates.snapshot()["outcomes"] == 0 and not rates.snapshot()["alarms"]
    assert not rates.buckets


def test_observation_counts_returned_failures_and_exceptions_once(monkeypatch):
    rates = health.CapabilityRates()
    monkeypatch.setattr(health, "RATES", rates)

    @health.observed("read_image")
    def read(value):
        if isinstance(value, Exception):
            raise value
        return value

    @health.observed("read_image")
    async def asynchronous():
        return {"error": "could not enter the admitted image decoder cell"}

    assert read("ordinary text") == "ordinary text"
    assert read('{"error":"could not enter the admitted image decoder cell"}')
    with pytest.raises(ValueError):
        read(ValueError("could not enter the admitted image decoder cell"))
    asyncio.run(asynchronous())
    snapshot = rates.snapshot()
    assert snapshot["outcomes"] == 4
    assert snapshot["failures"][0]["failures"] == 3
    assert snapshot["failures"][0]["samples"] == 4
    assert not snapshot["alarms"]  # unknown owners cannot manufacture a systemic outage
    from types import SimpleNamespace

    for expected_refusal in (
        SimpleNamespace(status_code=403),
        {"status": 404},
        {"error": "unknown target"},
        "error: file not found",
    ):
        assert read(expected_refusal) is expected_refusal
    assert rates.snapshot()["outcomes"] == 4
    assert read(SimpleNamespace(status_code=503)).status_code == 503
    assert rates.snapshot()["failures"][-1]["code"] == "http_failed"
    with pytest.raises(PermissionError):
        read(PermissionError("OS custody failure"))
    assert any(r["code"] == "authority_refused" for r in rates.snapshot()["failures"])

    @health.observed("chat_stream", inspect_result=False)
    def chat():
        return '{"error":"an example the owner asked me to explain"}'

    assert chat().startswith('{"error"')
    assert rates.snapshot()["capability_successes"]["chat_stream"] == 1


def test_complete_report_cannot_hide_missing_duplicate_or_failed_capabilities():
    rows = [
        dict(
            capability=name,
            status="passed",
            evidence=contract.evidence,
            observation={"verified": True},
        )
        for name, contract in CAPABILITIES.items()
    ]
    assert validate_report({"capabilities": rows}) == []
    for index, row in enumerate(rows):
        for replacement in (
            [],
            [row, row],
            [{**row, "status": "skipped"}],
            [{**row, "observation": None}],
        ):
            report = {"capabilities": rows[:index] + replacement + rows[index + 1 :]}
            assert validate_report(report) == [
                {"capability": row["capability"], "code": "evidence_missing"}
            ]


def test_capability_image_keeps_real_execution_seams():
    import ast

    from tests.test_role_production_acceptance_integrity import substitutions

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    for name in (
        "core_capability_image.py",
        "core_capability_fixture.py",
        "core_capability_live.py",
    ):
        source = (scripts / name).read_text(encoding="utf-8")
        assert not substitutions(source), name
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                try:
                    ast.parse(node.value)
                except SyntaxError:
                    continue
                assert not substitutions(node.value), (name, node.lineno)


def test_one_broken_owner_and_low_volume_never_alarm():
    rates = health.CapabilityRates()
    for _ in range(100):
        rates.record("connected_service", "authentication_failed", owner="broken-key")
    rates.record("connected_service", "authentication_failed", owner="one-off")
    assert not rates.snapshot()["alarms"]
    for _ in range(2):
        rates.record("connected_service", "authentication_failed", owner="one-off")
    assert rates.snapshot()["alarms"][0]["failing_owners"] == 2
    assert "broken-key" not in json.dumps(rates.snapshot())


def test_live_missing_config_and_restarts_are_unknown_not_failed(monkeypatch):
    from scripts import core_capability_live as live

    monkeypatch.delenv("TINYASSETS_CORE_CANARY_CONFIG", raising=False)
    report = asyncio.run(live.collect())
    assert not report["failures"] and report["warnings"][0]["code"] == "canary_not_provisioned"
    attempts = []

    async def restarted(report):
        attempts.append(1)
        raise live.DaemonRestart()

    async def delay(_):
        pass

    monkeypatch.setattr(live, "run", restarted)
    monkeypatch.setattr(live.asyncio, "sleep", delay)
    report = asyncio.run(live.collect())
    assert len(attempts) == 3
    assert not report["failures"]
    assert report["warnings"][0]["code"] == "deployment_in_progress"


def test_only_candidate_specific_measured_image_failures_authorize_rollback():
    from scripts.core_capability_regression import regressions

    green = {
        "capabilities": [
            dict(
                capability=name,
                status="passed",
                evidence=contract.evidence,
                observation={"verified": True},
            )
            for name, contract in CAPABILITIES.items()
        ]
    }
    red = {"capabilities": [dict(capability="bash", status="failed", code="cell_entry_failed")]}
    live = {"failures": [{"capability": "bash", "code": "probe_failed"}]}
    assert regressions(red, green, live) == ["bash"]
    assert regressions(red, red, live) == []
    assert regressions({}, green, live) == []
    assert (
        regressions(red, green, {"warnings": [{"capability": "bash", "code": "rate_alarm"}]}) == []
    )
    assert regressions(green, green, live) == []
