"""Tests for .github/workflows/p0-outage-triage.yml structure.

Covers:
  (a) YAML parses without error
  (b) Triggered only on issues.labeled (not schedule, not push)
  (c) Job condition gates on p0-outage label only
  (d) SSH secrets verified before acting
  (e) Compose restart command is non-destructive (--force-recreate daemon only)
  (f) Re-probe uses canonical CANARY_URL
  (g) Green path closes issue
  (h) Red path adds needs-human label (not closes)
  (i) Production host mutations share one repository-wide concurrency group
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO / ".github" / "workflows" / "p0-outage-triage.yml"

pytestmark = pytest.mark.skipif(
    not _YAML_AVAILABLE, reason="pyyaml not installed"
)


def _load() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def _triggers(wf: dict) -> dict:
    return wf.get(True, {}) or {}


def _steps(wf: dict) -> list[dict]:
    return wf.get("jobs", {}).get("triage", {}).get("steps", [])


def _step_by_id(step_id: str) -> dict:
    return next(step for step in _steps(_load()) if step.get("id") == step_id)


# ---------------------------------------------------------------------------
# (a) YAML parses
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# (b) Triggered only on issues.labeled
# ---------------------------------------------------------------------------

def test_triggered_on_issues_labeled():
    wf = _load()
    triggers = _triggers(wf)
    assert "issues" in triggers, "must have issues trigger"
    issues_trigger = triggers["issues"] or {}
    types = issues_trigger.get("types", [])
    assert "labeled" in types, "must trigger on issues.labeled"


def test_not_triggered_on_schedule():
    wf = _load()
    triggers = _triggers(wf)
    assert "schedule" not in triggers, (
        "p0-triage must NOT run on schedule — only on label events"
    )


def test_not_triggered_on_push():
    wf = _load()
    triggers = _triggers(wf)
    assert "push" not in triggers, "p0-triage must not run on push"


# ---------------------------------------------------------------------------
# (c) Job condition gates on p0-outage label
# ---------------------------------------------------------------------------

def test_job_condition_checks_p0_outage_label():
    wf = _load()
    job_if = wf.get("jobs", {}).get("triage", {}).get("if", "")
    assert "p0-outage" in str(job_if), (
        "triage job must be conditional on p0-outage label"
    )


# ---------------------------------------------------------------------------
# (d) SSH secrets verified
# ---------------------------------------------------------------------------

def test_secrets_verified_before_ssh():
    text = _text()
    assert "DO_SSH_KEY" in text
    assert "DO_DROPLET_HOST" in text
    assert "DO_SSH_USER" in text
    # There must be a verification step before the restart step.
    steps = _steps(_load())
    step_names = [s.get("name", "").lower() for s in steps]
    verify_idx = next((i for i, n in enumerate(step_names) if "secret" in n or "verify" in n), None)
    restart_idx = next((i for i, n in enumerate(step_names) if "restart" in n), None)
    assert verify_idx is not None, "must have a secrets-verify step"
    assert restart_idx is not None, "must have a restart step"
    assert verify_idx < restart_idx, "secrets verify must come before restart"


# ---------------------------------------------------------------------------
# (e) Compose restart is non-destructive
# ---------------------------------------------------------------------------

def test_restart_uses_force_recreate_daemon_only():
    text = _text()
    assert "--force-recreate" in text, "restart must use --force-recreate"
    assert "daemon" in text, "restart must target daemon service only"
    # Must NOT use `down` (destructive) or restart all services
    assert "compose down" not in text, (
        "restart must not use 'compose down' — non-destructive only"
    )


def test_restart_uses_env_file():
    assert "--env-file /etc/tinyassets/env" in _text()


def test_p0_diag_uses_env_file_and_daemon_logs():
    text = _text()
    assert "docker compose --env-file /etc/tinyassets/env" in text
    assert "docker logs tinyassets-daemon --tail 120" in text


def test_triage_uses_live_systemd_compose_file():
    text = _text()
    assert "/opt/tinyassets/compose.yml" in text
    assert "/opt/tinyassets/deploy/compose.yml" not in text


def test_image_pull_repair_uses_immutable_release_state_rollback_target():
    text = _text()
    assert "ghcr.io/tinyassets/tinyassets-daemon:latest" not in text
    assert "release-state.json" in text
    assert "rollback_target" in text
    assert "*@sha256:*" in text


# ---------------------------------------------------------------------------
# (f) Re-probe uses canonical URL
# ---------------------------------------------------------------------------

def test_reprobe_uses_canary_url():
    text = _text()
    assert "CANARY_URL" in text
    assert "tinyassets.io/mcp" in text




def test_classifier_step_does_not_inline_multiline_diag_in_bash():
    steps = _steps(_load())
    step = next(s for s in steps if s.get("id") == "classify")
    run = step.get("run", "")
    env = step.get("env", {})

    assert "steps.prediag.outputs.diag" not in run, (
        "raw diagnostics must not be interpolated into bash; multiline log "
        "content can contain shell syntax and break auto-triage"
    )
    assert env.get("DIAG") == "${{ steps.prediag.outputs.diag }}"
    assert "--input-file" in run


# ---------------------------------------------------------------------------
# (g) Green path closes issue
# ---------------------------------------------------------------------------

def test_green_path_closes_issue():
    text = _text()
    assert "state: 'closed'" in text or '"closed"' in text or "'closed'" in text, (
        "green path must close the issue"
    )
    assert "auto-recover" in text.lower() or "auto_recover" in text.lower(), (
        "green path comment must mention auto-recovery"
    )


# ---------------------------------------------------------------------------
# (h) Red path adds needs-human label (does not close)
# ---------------------------------------------------------------------------

def test_red_path_adds_needs_human_label():
    text = _text()
    assert "needs-human" in text, "red path must add needs-human label"


def test_red_path_does_not_close_issue():
    # The needs-human step should not call issues.update with state: closed.
    # Check that closing only happens in the green-path conditional block.
    steps = _steps(_load())
    red_step = next(
        (s for s in steps if "needs-human" in str(s.get("with", {}).get("script", ""))),
        None,
    )
    if red_step:
        script = red_step.get("with", {}).get("script", "")
        assert "state: 'closed'" not in script and '"closed"' not in script, (
            "red path must not close the issue"
        )


# ---------------------------------------------------------------------------
# (i) Production host mutation concurrency
# ---------------------------------------------------------------------------

def test_concurrency_group_serializes_production_host_mutations():
    wf = _load()
    concurrency = wf.get("concurrency", {})
    assert concurrency.get("group") == "production-host-mutation"


def test_concurrency_not_cancel_in_progress():
    """Triage must complete even if a second label event fires mid-run."""
    wf = _load()
    concurrency = wf.get("concurrency", {})
    assert concurrency.get("cancel-in-progress") is False


def test_triage_refuses_nonterminal_stop_writer_fence_before_repair():
    steps = _steps(_load())
    names = [step.get("name") for step in steps]
    guard_name = "Refuse host mutation during stop-writer cutover"
    assert names.index(guard_name) < names.index("Capture pre-restart diag")
    guard = steps[names.index(guard_name)]["run"]
    assert "scp -i ~/.ssh/do_deploy" in guard
    assert "scripts/retire_cheat_loop_deploy_fence.py" in guard
    assert "guard-host-mutation" in guard
    assert "retire-cheat-loop-task-2-1-fence.json" in guard
    assert "/run/tinyassets-host-mutation-guard" not in guard


def test_triage_repairs_run_inside_authoritative_host_lock():
    expected = (
        "Repair — ENV-UNREADABLE (chown + chmod)",
        "Repair — OOM (compose restart; memory cap NOT auto-bumped)",
        "Repair — disk full (docker prune + journalctl vacuum)",
        "Repair — image pull failure (release-state rollback target)",
        "Repair — watchdog hot-loop (stop + sleep 60 + start)",
        "Repair — provider_exhaustion (.pause every universe)",
        "Attempt compose restart",
    )
    steps = _steps(_load())
    for name in expected:
        run = next(step["run"] for step in steps if step.get("name") == name)
        assert "guard-host-mutation" in run, name
        assert "--command-timeout" in run, name


def test_provider_exhaustion_page_uses_existing_pushover_cli_contract():
    step = next(
        item for item in _steps(_load())
        if item.get("name", "").startswith("Page — provider_exhaustion")
    )
    run = step["run"]

    assert "scripts/pushover_page.py" in run
    for required in (
        "--issue-number",
        "--run-url",
        "--probe-url",
        "--probe-exit",
        "--kind",
        "--first-alarm",
    ):
        assert required in run
    assert "--probe-exit 3" in run
    for retired in ("--title", "--message", "--priority"):
        assert retired not in run
    assert "|| echo" not in run
    assert step.get("continue-on-error") is True


def test_provider_exhaustion_page_is_not_gated_by_worker_pause_setting():
    steps = _steps(_load())
    page_index = next(
        index for index, item in enumerate(steps)
        if item.get("name", "").startswith("Page — provider_exhaustion")
    )
    page_step = steps[page_index]

    assert page_step["if"] == "steps.classify.outputs.class == 'provider_exhaustion'"
    assert "AUTO_REPAIR" not in page_step.get("env", {})
    assert page_index > next(
        index for index, item in enumerate(steps)
        if item.get("id") == "repair_provider_exhaustion_gate"
    )
    assert page_index < next(
        index for index, item in enumerate(steps)
        if item.get("id") == "reprobe"
    )


def test_bounded_repair_failures_continue_to_canonical_reprobe():
    repair_ids = (
        "repair_env",
        "repair_oom",
        "repair_disk",
        "repair_pull",
        "repair_watchdog",
        "repair_provider_exhaustion",
        "restart",
    )
    steps = _steps(_load())
    reprobe_index = next(index for index, step in enumerate(steps) if step.get("id") == "reprobe")

    for repair_id in repair_ids:
        step = _step_by_id(repair_id)
        assert step.get("continue-on-error") is True, repair_id
        assert steps.index(step) < reprobe_index, repair_id


def test_provider_exhaustion_repair_keeps_its_ssh_remote_target():
    run = _step_by_id("repair_provider_exhaustion")["run"]

    remote_target = '"${DO_SSH_USER}@${DO_DROPLET_HOST}"'
    # Was `docker stop tinyassets-worker`. That container went away 2026-08-29
    # with the host-run fleet (PLAN.md); the .pause sweep is now the whole
    # repair, and it must still run on the REMOTE host, not the runner.
    repair_command = "for udir in /var/lib/docker/volumes/tinyassets-data/_data/*/"
    assert remote_target in run
    assert run.index(remote_target) < run.index(repair_command)
    assert "docker stop tinyassets-worker" not in run


def test_persistent_red_escalation_fails_visibly_after_actual_reprobe():
    red_step = next(
        step for step in _steps(_load())
        if step.get("name") == "Add needs-human label on persistent red"
    )
    script = red_step["with"]["script"]

    assert red_step["if"] == "steps.reprobe.outputs.color == 'red'"
    assert "labels: [label]" in script
    assert "process.exitCode = 1" in script


def test_existing_pushover_cli_dry_run_accepts_provider_exhaustion_page_shape():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/pushover_page.py",
            "--issue-number", "1564",
            "--run-url", "https://github.com/example/repo/actions/runs/1",
            "--probe-url", "https://tinyassets.io/mcp",
            "--probe-exit", "3",
            "--kind", "PROVIDER_EXHAUSTION",
            "--first-alarm",
            "--dry-run",
        ],
        cwd=_REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "decision=PAGE" in result.stdout
    assert "DRY-RUN" in result.stdout
    assert "PROVIDER_EXHAUSTION" in result.stdout
