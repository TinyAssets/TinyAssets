"""The live-like gate deploys the way production deploys.

deploy-prod.yml and live-like-gate.yml both run deploy/deploy_host.sh, one
phase per step. This keeps them calling the same phases, in the driver's
production order, with the same step ids, `if:` wiring, timeouts and input
names. Production steps the gate does not run must be authority or GitHub
work, named below with the reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / ".github/workflows/deploy-prod.yml"
GATE = ROOT / ".github/workflows/live-like-gate.yml"
PHASE = re.compile(r"bash deploy/deploy_host\.sh ([a-z-]+)")

# deploy-prod steps with no driver phase that the gate does not run, and why.
PROD_ONLY = {
    "Resolve image tag -> immutable digest":
        "GHCR resolution; the gate builds and resolves its own candidate",
    "Verify target descends from the stop-writer floor":
        "release authority over production revisions",
    "Verify deploy secrets present": "production environment secrets",
    "Validate agent interchange HMAC prerequisite":
        "production secret validation; gate keys are generated valid",
    "Validate request idempotency HMAC prerequisite":
        "production secret validation; gate keys are generated valid",
    "Verify core canary is provisioned":
        "signs in through WorkOS with production canary credentials",
    "Validate OAuth provider credentials":
        "production OAuth client; the gate installs none (OAUTH_ACTION=skip)",
    "Install SSH key": "production SSH authority; the gate's host is the runner",
    "Core user capabilities and systemic failure rates":
        "WorkOS sign-in and production canary credentials",
    "Save owner-box roll receipt": "GitHub artifact upload",
    "Verify CF Access gates the direct origin (expects 403)":
        "Cloudflare edge in front of production only",
    "Open deploy-failed issue": "production incident tracking",
    "Close deploy-failed issue": "production incident tracking",
    "Upload failed candidate startup diagnostics": "GitHub artifact upload",
    "Summary": "GitHub step summary",
}
# Gate-only conjuncts: the rollback case stops at the fail-safe's verdict.
CASE_GUARD = "matrix.case == 'cutover'"
ROLLBACK_CONTINUE = "${{ matrix.case == 'rollback' }}"
# The gate's own steps outside the deploy: host setup recreating the live
# revision's deploy (its receipt), and the verdict, which reruns the
# read-only preflight after a rollback.
GATE_OWN = {
    "Start the live image and make it live",
    "Assert the host is live on the expected image",
}


def steps(path: Path) -> list[dict]:
    jobs = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
    (job,) = jobs.values()
    return job["steps"]


def phase_steps(path: Path) -> list[tuple[str, dict]]:
    found = []
    for step in steps(path):
        if step.get("name") in GATE_OWN:
            continue
        phases = PHASE.findall(step.get("run", ""))
        assert len(phases) <= 1, f"{step.get('name')}: one driver phase per step"
        if phases:
            found.append((phases[0], step))
    return found


def strip_case_guard(condition: str | None) -> str | None:
    if condition is None:
        return None
    if condition == "${{ " + CASE_GUARD + " }}":
        return None
    return condition.replace(" && " + CASE_GUARD + " }}", " }}")


def driver_phases() -> list[str]:
    text = (ROOT / "deploy/deploy_host.sh").read_text(encoding="utf-8")
    body = text.split("\nPHASES=(\n", 1)[1].split("\n)\n", 1)[0]
    return body.split()


def test_production_calls_every_driver_phase_in_driver_order():
    assert [phase for phase, _ in phase_steps(PROD)] == driver_phases()


def test_gate_calls_the_same_phases_in_the_same_order():
    assert [phase for phase, _ in phase_steps(GATE)] == [phase for phase, _ in phase_steps(PROD)]


def test_gate_phase_steps_keep_production_wiring():
    prod = dict(phase_steps(PROD))
    for phase, gate in phase_steps(GATE):
        want = prod[phase]
        assert gate["name"] == want["name"], phase
        assert gate.get("id") == want.get("id"), phase
        assert strip_case_guard(gate.get("if")) == want.get("if"), phase
        assert gate.get("timeout-minutes") == want.get("timeout-minutes"), phase
        if phase == "fail-safe-deploy":
            assert gate.get("continue-on-error") == ROLLBACK_CONTINUE
        else:
            assert gate.get("continue-on-error") == want.get("continue-on-error"), phase
        # The caller supplies every input production supplies (values differ).
        assert set(gate.get("env", {})) >= set(want.get("env", {})) - {
            # secrets the phase reads but the gate already exports job-wide
            "TINYASSETS_WIKI_CANARY_TOKEN", "TINYASSETS_CANARY_DOOR_KEY",
            "TINYASSETS_CANARY_USER_ID", "TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY",
            "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY", "TINYASSETS_CANARY_EMAIL",
            "TINYASSETS_CANARY_PASSWORD", "TINYASSETS_CANARY_UNIVERSE_ID", "DO_API_TOKEN",
        }, phase


def test_every_production_step_is_a_phase_or_named_authority_work():
    names = {step.get("name") for step in steps(PROD)}
    phased = {step.get("name") for _, step in phase_steps(PROD)}
    unexplained = names - phased - set(PROD_ONLY) - {None}
    # checkout / setup-python / run clock / canary waiver are caller plumbing
    # the gate repeats with its own values.
    gate_names = {step.get("name") for step in steps(GATE)}
    assert {name for name in unexplained if name not in gate_names} == set()
    assert set(PROD_ONLY) <= names, set(PROD_ONLY) - names


def test_gate_binds_no_environment_and_reads_no_secret():
    text = GATE.read_text(encoding="utf-8")
    jobs = yaml.safe_load(text)["jobs"]
    assert all("environment" not in job for job in jobs.values())
    assert "secrets." not in text


pytestmark = pytest.mark.verifies('live-like-deploy-gate:G1')
