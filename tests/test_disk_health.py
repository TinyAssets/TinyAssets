import io
import json
from types import SimpleNamespace

import pytest

from scripts.disk_health_canary import probe
from scripts.uptime_observations import PROBES, classify_observations
from tinyassets.disk_health import snapshot


@pytest.mark.parametrize("used,status,code,page", [
    (79, "ok", 0, False), (80, "warning", 80, False),
    (89, "warning", 80, False), (90, "critical", 90, True), (99, "critical", 90, True),
])
def test_disk_pressure_health_to_uptime_escalation(monkeypatch, used, status, code, page):
    disk = snapshot(disk_fn=lambda _: SimpleNamespace(total=100, free=100-used))
    assert disk["status"] == status
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", "test-only")
    result, message = probe(opener=lambda *a, **k: io.BytesIO(
        json.dumps({"disk_health": disk}).encode()))
    assert result == code and status in message
    combined = classify_observations(dict.fromkeys(PROBES, "0") | {"disk": str(code)})
    assert combined["overall"] == "green"
    assert combined["disk_overall"] == ("green" if code == 0 else "red")
    assert combined["disk_page_needed"] is page
    assert not combined["page_needed"]


def test_warning_never_suppresses_other_outage():
    result = classify_observations(dict.fromkeys(PROBES, "0") | {"disk": "80", "tool": "1"})
    assert result["page_needed"]


def test_disk_unknown_is_loud_without_host_paths(monkeypatch):
    def unavailable(_):
        raise OSError("private host path")

    assert snapshot(disk_fn=unavailable) == {"status": "unknown"}
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", "test-only")
    code, message = probe(opener=lambda *a, **k: io.BytesIO(
        b'{"disk_health":{"status":"unknown"}}'))
    assert code == 2 and "private" not in message


def test_missing_disk_observation_is_unknown_and_cannot_page():
    combined = classify_observations(dict.fromkeys(PROBES, "0") | {"disk": "2"})
    assert combined["disk_overall"] == "unknown"
    assert combined["overall"] == "unknown"
    assert not combined["disk_page_needed"] and not combined["page_needed"]
