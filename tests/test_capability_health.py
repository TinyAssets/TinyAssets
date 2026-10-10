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
    for _ in range(3):
        rates.record("bash", failure_code(ValueError("invalid owner content frame /private")))
    for _ in range(7):
        rates.record("bash")
    snapshot = rates.snapshot()
    (alarm,) = snapshot["alarms"]
    assert alarm["code"] == "content_frame_invalid" and alarm["rate"] == 0.3
    assert alarm["baseline_samples"] == 100 and alarm["samples"] == 10
    assert "/private" not in json.dumps(snapshot)
    fresh = health.CapabilityRates(lambda: now[0])
    for _ in range(3):
        fresh.record("connected_service", failure_code("inference usage authority refused"))
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
    assert snapshot["alarms"][0]["failures"] == 3
    assert snapshot["alarms"][0]["samples"] == 4


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
