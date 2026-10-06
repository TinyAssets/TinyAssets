"""Tests for .github/workflows/dns-canary.yml structure.

Covers:
  (a) YAML parses without error
  (b) Only schedule + workflow_dispatch triggers (no push/PR)
  (c) Both jobs present: dns-check + alarm-sink
  (d) dns-check job uses stdlib socket (not pytest invocation)
  (e) alarm-sink uses workflow_id file name (not context.workflow)
  (f) alarm-sink has if: always()
  (g) alarm-sink opens issue on consecutive red + closes on green
  (h) dns-red label used consistently
  (i) No checkout/setup-python steps (stdlib needs no deps)
  (j) uptime-canary.yml also uses a literal workflow_id (llm-binding-canary.yml
      was retired 2026-09-24: the platform has no LLM, Hard Rule 15)
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

_REPO = Path(__file__).resolve().parent.parent
_DNS_WF = _REPO / ".github" / "workflows" / "dns-canary.yml"
_UPTIME_WF = _REPO / ".github" / "workflows" / "uptime-canary.yml"

pytestmark = pytest.mark.skipif(
    not _YAML_AVAILABLE, reason="pyyaml not installed"
)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _triggers(wf: dict) -> dict:
    return wf.get(True, {}) or wf.get("on", {}) or {}


def _jobs(wf: dict) -> dict:
    return wf.get("jobs", {})


def _steps(wf: dict, job: str) -> list[dict]:
    return _jobs(wf).get(job, {}).get("steps", [])


# ---------------------------------------------------------------------------
# (a) YAML parses
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# (b) Triggers
# ---------------------------------------------------------------------------

def test_has_schedule_trigger():
    wf = _load(_DNS_WF)
    triggers = _triggers(wf)
    assert "schedule" in triggers


def test_has_workflow_dispatch_trigger():
    wf = _load(_DNS_WF)
    triggers = _triggers(wf)
    assert "workflow_dispatch" in triggers


def test_no_push_trigger():
    wf = _load(_DNS_WF)
    triggers = _triggers(wf)
    assert "push" not in triggers


def test_no_pull_request_trigger():
    wf = _load(_DNS_WF)
    triggers = _triggers(wf)
    assert "pull_request" not in triggers


# ---------------------------------------------------------------------------
# (c) Both jobs present
# ---------------------------------------------------------------------------

def test_dns_check_job_present():
    wf = _load(_DNS_WF)
    assert "dns-check" in _jobs(wf)


def test_alarm_sink_job_present():
    wf = _load(_DNS_WF)
    assert "alarm-sink" in _jobs(wf)


# ---------------------------------------------------------------------------
# (d) dns-check uses socket (no pytest)
# ---------------------------------------------------------------------------

def test_dns_check_uses_socket():
    text = _text(_DNS_WF)
    assert "socket" in text, "dns-check must use stdlib socket, not pytest"


def test_dns_check_does_not_invoke_pytest():
    text = _text(_DNS_WF)
    # Should not call `python -m pytest` in the run step
    assert "python -m pytest" not in text, (
        "dns-canary.yml must not invoke pytest — use stdlib socket directly"
    )


def test_dns_check_probes_tinyassets_io():
    text = _text(_DNS_WF)
    assert "tinyassets.io" in text


def test_dns_check_probes_mcp_tinyassets_io():
    text = _text(_DNS_WF)
    assert "mcp.tinyassets.io" in text


# ---------------------------------------------------------------------------
# (e) alarm-sink uses literal workflow_id, not context.workflow
# ---------------------------------------------------------------------------

def test_alarm_sink_workflow_id_is_literal_filename():
    text = _text(_DNS_WF)
    assert "workflow_id: 'dns-canary.yml'" in text or \
           'workflow_id: "dns-canary.yml"' in text, (
        "alarm-sink must use literal 'dns-canary.yml' as workflow_id, not context.workflow"
    )


def test_alarm_sink_does_not_use_context_workflow():
    text = _text(_DNS_WF)
    assert "context.workflow" not in text, (
        "context.workflow is the workflow NAME, not slug — causes 404 on API calls"
    )


# ---------------------------------------------------------------------------
# (f) alarm-sink has if: always()
# ---------------------------------------------------------------------------

def test_alarm_sink_fires_always():
    wf = _load(_DNS_WF)
    alarm_job = _jobs(wf).get("alarm-sink", {})
    cond = alarm_job.get("if", "")
    assert "always()" in str(cond).lower(), (
        f"alarm-sink must have if: always(), got: {cond!r}"
    )


def test_dns_check_propagates_published_red_as_job_failure():
    wf = _load(_DNS_WF)
    job = _jobs(wf)["dns-check"]
    steps = job["steps"]
    probe_index = next(
        index for index, step in enumerate(steps) if step.get("id") == "dns"
    )
    probe = steps[probe_index]
    terminal = steps[-1]

    assert probe.get("continue-on-error") is True
    assert job["outputs"]["overall"] == "${{ steps.dns.outputs.overall }}"
    assert probe_index < len(steps) - 1
    assert terminal.get("env", {}).get("OVERALL") == (
        "${{ steps.dns.outputs.overall }}"
    )
    assert '"$OVERALL" = "red"' in terminal.get("run", "")
    assert "exit 1" in terminal.get("run", "")
    assert terminal.get("continue-on-error", False) is False

    if os.name == "nt":
        shell = shutil.which("wsl")
        shell_command = [shell, "--exec", "bash", "-c"] if shell else []
    else:
        shell = shutil.which("bash")
        shell_command = [shell, "-c"] if shell else []
    if not shell_command:
        pytest.skip("bash is required to exercise the propagation script")
    green = subprocess.run(
        shell_command + ["export OVERALL=green\n" + terminal["run"]],
        check=False,
        capture_output=True,
        text=True,
    )
    red = subprocess.run(
        shell_command + ["export OVERALL=red\n" + terminal["run"]],
        check=False,
        capture_output=True,
        text=True,
    )
    assert green.returncode == 0, green.stderr
    assert red.returncode != 0, red.stderr

    sink = _jobs(wf)["alarm-sink"]
    assert sink["needs"] == "dns-check"
    assert sink.get("if", "").strip().lower() == "always()"
    assert sink["steps"][0]["env"]["OVERALL"] == (
        "${{ needs.dns-check.outputs.overall }}"
    )


# ---------------------------------------------------------------------------
# (g) alarm-sink logic: issue opened / closed
# ---------------------------------------------------------------------------

def test_alarm_sink_opens_issue_on_consecutive_red():
    text = _text(_DNS_WF)
    assert "issues.create" in text


def test_alarm_sink_closes_issue_on_green():
    text = _text(_DNS_WF)
    assert "state: 'closed'" in text or '"state": "closed"' in text or \
           "state_reason: 'completed'" in text or "state_reason: \"completed\"" in text


def test_alarm_sink_checks_previous_run_was_red():
    text = _text(_DNS_WF)
    assert "previousRunWasRed" in text or "priorRed" in text


# ---------------------------------------------------------------------------
# (h) dns-red label
# ---------------------------------------------------------------------------



def test_dns_red_label_in_env_block():
    wf = _load(_DNS_WF)
    env = wf.get("env", {})
    assert env.get("ALARM_ISSUE_LABEL") == "dns-red"


# ---------------------------------------------------------------------------
# (i) No checkout or setup-python needed for stdlib check
# ---------------------------------------------------------------------------

def test_no_checkout_step_in_dns_check():
    steps = _steps(_load(_DNS_WF), "dns-check")
    uses_values = [s.get("uses", "") for s in steps]
    assert not any("checkout" in u for u in uses_values), (
        "dns-check job should not need checkout — stdlib socket needs no repo files"
    )


def test_no_setup_python_step_in_dns_check():
    steps = _steps(_load(_DNS_WF), "dns-check")
    uses_values = [s.get("uses", "") for s in steps]
    assert not any("setup-python" in u for u in uses_values), (
        "dns-check job should not need setup-python — ubuntu-latest has python3 built in"
    )


# ---------------------------------------------------------------------------
# (j) Audit: uptime-canary also uses a literal workflow_id
# ---------------------------------------------------------------------------

def test_uptime_canary_workflow_id_is_literal():
    text = _text(_UPTIME_WF)
    assert "context.workflow" not in text, (
        "uptime-canary.yml must use literal workflow filename, not context.workflow"
    )
    assert "workflow_id: 'uptime-canary.yml'" in text or \
           'workflow_id: "uptime-canary.yml"' in text


def test_uptime_canary_runs_after_successful_deploy():
    wf = _load(_UPTIME_WF)
    triggers = _triggers(wf)
    workflow_run = triggers.get("workflow_run", {})
    assert "Deploy prod" in workflow_run.get("workflows", []), (
        "uptime-canary must run after production deploys so the loop gets "
        "fresh observation evidence without waiting for cron/manual dispatch"
    )
    assert "completed" in workflow_run.get("types", [])
    assert (
        _jobs(wf)["probe"].get("if")
        == "github.event_name != 'workflow_run' || "
           "github.event.workflow_run.conclusion == 'success'"
    )
