"""Tests for deploy/tinyassets-prune.service and tinyassets-prune.timer.

Coverage:
  - Service file: Type=oneshot, shared bounded daemon-only retention entrypoint
  - No broad Docker prune or age-only safety policy; activation stays operator-owned
  - Timer file: daily OnCalendar backstop, Persistent=true
  - Deploy workflow runs the same unit after the public canary is green
  - Bootstrap delegates both units and activation to the shared installer
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVICE = REPO / "deploy" / "tinyassets-prune.service"
TIMER = REPO / "deploy" / "tinyassets-prune.timer"
BOOTSTRAP = REPO / "deploy" / "hetzner-bootstrap.sh"
INSTALLER = REPO / "deploy" / "install-host-uptime-services.sh"


def _svc() -> str:
    return SERVICE.read_text(encoding="utf-8")


def _tmr() -> str:
    return TIMER.read_text(encoding="utf-8")


def _boot() -> str:
    return BOOTSTRAP.read_text(encoding="utf-8")


def _installer() -> str:
    return INSTALLER.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# service file shape
# ---------------------------------------------------------------------------

def test_service_file_exists():
    assert SERVICE.exists(), f"Missing: {SERVICE}"


def test_service_type_oneshot():
    assert "Type=oneshot" in _svc()


def test_service_uses_shared_bounded_retention_entrypoint():
    assert "WorkingDirectory=/opt/tinyassets-host-uptime/current" in _svc()
    assert (
        "ExecStart=/usr/bin/python3 "
        "/opt/tinyassets-host-uptime/current/scripts/disk_autoprune.py --apply"
    ) in _svc()
    hourly = (REPO / "deploy" / "tinyassets-disk-watch.service").read_text(encoding="utf-8")
    assert "scripts/disk_autoprune.py --apply" in hourly


def test_service_has_no_broad_prune_or_age_only_deletion():
    for forbidden in (
        "docker image prune", "docker builder prune", "docker system prune",
        "docker volume prune", "until=168h",
    ):
        assert forbidden not in _svc()


def test_service_does_not_opt_itself_into_destructive_retention():
    # --apply alone is read-only; the tested helper requires a separate exact
    # operator opt-in. A timer must not hardcode that authority in the unit.
    assert "EnvironmentFile=/etc/tinyassets/env" in _svc()
    assert "TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY" not in _svc()
    assert "SuccessExitStatus=1" in _svc()


def test_service_after_docker():
    assert "After=docker.service" in _svc()


# ---------------------------------------------------------------------------
# timer file shape
# ---------------------------------------------------------------------------

def test_timer_file_exists():
    assert TIMER.exists(), f"Missing: {TIMER}"


def _directives(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def test_timer_daily_backstop():
    # Deploys pull ~2.7 GB several times a day; a weekly backstop let the disk
    # fill between passes (2026-09-24: 23 daemon images, 87% used).
    calendars = [d for d in _directives(_tmr()) if d.startswith("OnCalendar=")]
    assert calendars == ["OnCalendar=*-*-* 04:00:00 UTC"]
    assert "Sun" not in calendars[0] and "weekly" not in calendars[0].lower()


def test_timer_persistent():
    assert "Persistent=true" in _tmr()


def test_timer_requires_service():
    assert "tinyassets-prune.service" in _tmr()


def test_timer_wantedby_timers_target():
    assert "WantedBy=timers.target" in _tmr()


# ---------------------------------------------------------------------------
# bootstrap delegates the units
# ---------------------------------------------------------------------------

def test_bootstrap_installs_prune_service():
    assert _boot().count("install-host-uptime-services.sh") == 1
    assert "tinyassets-prune.service" in _installer()


def test_bootstrap_installs_prune_timer():
    assert "tinyassets-prune.timer" in _installer()


def test_bootstrap_enables_prune_timer():
    installer = _installer()
    assert "tinyassets-prune.timer" in installer
    assert '"${SYSTEMCTL_BIN}" enable --now "${TIMERS[@]}"' in installer


# ---------------------------------------------------------------------------
# post-deploy retention
# ---------------------------------------------------------------------------

DEPLOY_WORKFLOW = REPO / ".github" / "workflows" / "deploy-prod.yml"
RETENTION_STEP = "Enforce daemon image retention (post-canary)"


def _deploy_steps() -> list[dict]:
    import yaml

    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["deploy"]["steps"]


def test_deploy_runs_retention_after_canary_and_receipt_are_green():
    steps = _deploy_steps()
    names = [step.get("name") for step in steps]
    assert RETENTION_STEP in names
    index = names.index(RETENTION_STEP)
    for gate in (
        "Run fail-safe deploy on the droplet",
        "Public MCP canary (--assert-handles)",
        "Roll back if the public canary is red",
        "Verify protected receipt contains target revision",
    ):
        assert names.index(gate) < index, f"retention must run after {gate!r}"
    step = steps[index]
    # Default success() gating: never after a failed/rolled-back deploy, when the
    # captured previous image may still be needed.
    assert "if" not in step or "always()" not in str(step["if"])
    assert "failure()" not in str(step.get("if", ""))


def test_post_deploy_retention_runs_the_installed_unit_and_never_fails_the_deploy():
    step = next(s for s in _deploy_steps() if s.get("name") == RETENTION_STEP)
    run = step["run"]
    # The same installed, locked, registry-verified path the timers use -- not
    # a second copy of the policy and never a broad Docker prune.
    assert "systemctl start tinyassets-prune.service" in run
    for forbidden in ("image prune", "system prune", "builder prune", "image rm"):
        assert forbidden not in run
    # The deploy already succeeded; a retention refusal must not open a
    # deploy-failed issue or suppress the install-host-services chain.
    assert step.get("continue-on-error") is True
    assert int(step.get("timeout-minutes", 0)) <= 5
