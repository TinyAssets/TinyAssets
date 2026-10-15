"""Pure accounting and report contracts; execution is proved in the image."""

import asyncio
import json
from pathlib import Path

import pytest

from tinyassets import capability_health as health
from tinyassets.core_capabilities import CAPABILITIES, failure_code, validate_report


def test_shared_disk_refusal_is_logged_and_drives_rate_alarm(monkeypatch, caplog):
    from tinyassets.universe_tools import UniverseToolError

    rates = health.CapabilityRates()
    monkeypatch.setattr(health, "RATES", rates)
    for owner in ("a", "b"):
        for _ in range(3):
            health.record_exception("bash", UniverseToolError(
                "the shared disk is nearly full, so the tool jail will not start; nothing ran"
            ), owner=owner)
    assert rates.snapshot()["alarms"][0]["code"] == "shared_disk_full"
    assert "capability_failure capability=bash code=shared_disk_full" in caplog.text


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
        "box_acceptance.py",
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


def test_live_missing_config_and_restarts_are_unknown_not_failed(monkeypatch, tmp_path, capsys):
    from scripts import core_capability_live as live

    monkeypatch.delenv("TINYASSETS_CANARY_EMAIL", raising=False)
    report = asyncio.run(live.collect())
    assert report["warnings"][0]["code"] == "canary_not_provisioned"
    assert not report["failures"]
    assert live.main(["--report", str(tmp_path / "report.json")]) == 0
    assert "::warning::core canary canary: canary_not_provisioned" in capsys.readouterr().out
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


def test_model_missing_and_failed_evidence_is_alert_only(monkeypatch):
    from scripts import core_capability_live as live

    async def observed(report):
        report["model_dependent"] = ["chat_stream", "read_image"]
        for name in CAPABILITIES:
            if name not in report["model_dependent"]:
                live.passed(report, name, {"verified": True})
        report["capabilities"].append(
            dict(capability="read_image", status="failed", code="probe_failed")
        )

    monkeypatch.setattr(live, "run", observed)
    report = asyncio.run(live.collect())
    assert not report["failures"]
    assert {row["capability"] for row in report["warnings"]} == {"chat_stream", "read_image"}


def test_live_platform_failure_fails_the_step_and_model_rows_do_not(monkeypatch, tmp_path):
    from scripts import core_capability_live as live

    async def platform_red(report):
        report["model_dependent"] = ["chat_stream", "patch_request"]
        for name in CAPABILITIES:
            if name not in {"chat_stream", "patch_request", "background_run"}:
                live.passed(report, name, {"verified": True})

    monkeypatch.setattr(live, "run", platform_red)
    assert live.main(["--report", str(tmp_path / "red.json")]) == 1

    async def model_red(report):
        report["model_dependent"] = ["chat_stream", "patch_request"]
        for name in CAPABILITIES:
            if name not in report["model_dependent"]:
                live.passed(report, name, {"verified": True})

    monkeypatch.setattr(live, "run", model_red)
    assert live.main(["--report", str(tmp_path / "advisory.json")]) == 0

    async def crashed(report):
        raise RuntimeError("probe bug")

    monkeypatch.setattr(live, "run", crashed)
    assert live.main(["--report", str(tmp_path / "crash.json")]) == 1


@pytest.mark.parametrize("failure", ["github", "pushover", "report"])
def test_alert_transport_and_report_errors_never_fail_the_step(
    tmp_path, monkeypatch, capsys, failure
):
    import runpy
    import subprocess
    import sys

    from scripts import pushover_page

    report = tmp_path / "probe.json"
    report.write_text("{" if failure == "report" else json.dumps({
        "failures": [] if failure == "github" else [
            {"capability": "bash", "code": "probe_failed"}
        ]
    }))
    monkeypatch.setenv("GITHUB_REPOSITORY", "test/test")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setattr(sys, "argv", ["core_capability_alert.py", str(report)])
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    monkeypatch.setitem(sys.modules, "pushover_page", pushover_page)

    def github(command, **kwargs):
        if failure == "github":
            raise subprocess.CalledProcessError(1, command)
        payload = ({"number": 1, "html_url": "https://example.invalid/issue"}
                   if "POST" in command else [])
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload))

    monkeypatch.setattr(subprocess, "run", github)
    monkeypatch.setattr(pushover_page, "send_pushover", lambda *a, **kw: (False, "unavailable"))
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/core_capability_alert.py"),
                   run_name="__main__")
    assert "::warning::Core capability alert delivery failed:" in capsys.readouterr().out
