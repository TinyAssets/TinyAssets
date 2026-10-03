"""deploy-prod.yml installs, validates and guards both daemon HMAC keys.

PR #2442 (5aeb64da) rewrote deploy-prod.yml and silently dropped this whole path:
the rotation input, runner-side validation of both secrets, the set-once install
of the request-idempotency key into its own daemon-only file, the agent
interchange install, the on-host pair validation, and the assert-absent check on
the shared env. Production kept working only because the two files written on
2026-08-10 survived. A rebuilt host would boot without them, and a rotation had
no path at all.

These tests lived in tests/test_deploy_prod_workflow.py, which is on
.github/heavy-test-files.txt and gates no merge. That is how the drop went unseen
for five weeks. They are moved here, against the current step names, so the path
gates.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO / ".github" / "workflows" / "deploy-prod.yml"

VALIDATE_AGENT = "Validate agent interchange HMAC prerequisite"
VALIDATE_REQUEST = "Validate request idempotency HMAC prerequisite"
INSTALL_REQUEST = "Install daemon-only request idempotency HMAC secret"
INSTALL_AGENT = "Install daemon-only agent interchange HMAC secret"
VALIDATE_HOST_PAIR = "Validate installed host HMAC pair"
SCRUB = "Scrub stale cloud env overrides"

#: Every step that changes the production host. Both validations must precede
#: all of them, so a bad GitHub secret stops the run with the host untouched.
MUTATING_STEPS = (
    INSTALL_REQUEST,
    INSTALL_AGENT,
    SCRUB,
    "Sync runtime deploy files",
    "Prepare data volume root",
    "Sync the canary bearer into /etc/tinyassets/env",
    "Prepare expected-instance state (required for admission)",
    "Run fail-safe deploy on the droplet",
)


def _wf() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _wf()["jobs"]["deploy"]["steps"]


def _index() -> dict[str, int]:
    return {step.get("name"): i for i, step in enumerate(_steps())}


def _step(name: str) -> dict:
    return _steps()[_index()[name]]


def test_every_mutating_step_still_exists():
    """The ordering checks below are only meaningful while these names are real."""
    missing = [name for name in MUTATING_STEPS if name not in _index()]
    assert missing == [], f"renamed or removed: {missing}; re-point MUTATING_STEPS"


def test_workflow_dispatch_has_explicit_request_hmac_rotation_input():
    inputs = _wf()[True]["workflow_dispatch"]["inputs"]
    rotation = inputs["rotate_request_idempotency_hmac"]
    assert rotation.get("type") == "boolean"
    assert rotation.get("default") is False
    assert "exposed" in str(rotation.get("description", "")).lower()


@pytest.mark.parametrize("name,secret,companion,script", [
    (VALIDATE_AGENT, "TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY", None,
     "python scripts/validate_agent_interchange_hmac.py"),
    (VALIDATE_REQUEST, "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY",
     "TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY",
     "python scripts/validate_request_idempotency_hmac.py"),
])
def test_both_secrets_are_validated_before_any_host_mutation(name, secret, companion, script):
    step = _step(name)
    env = step.get("env") or {}
    assert f"secrets.{secret}" in str(env.get(secret, ""))
    if companion:
        # The request validator refuses a key equal to its companion.
        assert f"secrets.{companion}" in str(env.get(companion, ""))
    assert step.get("run") == script
    index = _index()
    assert index[name] < min(index[m] for m in MUTATING_STEPS)


def test_install_order_ends_in_host_validation_then_the_shared_env_guard():
    index = _index()
    assert index[INSTALL_REQUEST] < index[INSTALL_AGENT] < index[VALIDATE_HOST_PAIR] < index[SCRUB]
    assert index[SCRUB] < index["Run fail-safe deploy on the droplet"]
    host = _step(VALIDATE_HOST_PAIR).get("run", "") or ""
    assert "scripts/validate_host_runtime_hmac_pair.py" in host
    assert '"sudo python3 -"' in host


def test_request_key_is_installed_set_once_into_its_own_daemon_only_file():
    step = _step(INSTALL_REQUEST)
    env = step.get("env") or {}
    assert "secrets.TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY" in str(
        env.get("TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY", ""))
    script = step.get("run", "") or ""
    assert re.search(
        r'printf \'%s\' "\$\{TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY\}" \|\s*\\?\s*ssh', script)
    assert "TINYASSETS_ENV_FILE=/etc/tinyassets/request-idempotency.env" in script
    assert "no-request-idempotency-legacy" in script
    assert "bash /tmp/install-tinyassets-env.sh '${REQUEST_HMAC_INSTALL_MODE}'" in script
    assert 'echo "${TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY}"' not in script


def test_rotation_is_explicit_manual_dispatch_only():
    """set-once is the default: a host key that differs from GitHub fails the run
    closed (install-tinyassets-env.sh exit 5) before any swap. Only an explicit
    dispatch with rotate_request_idempotency_hmac=true replaces it."""
    step = _step(INSTALL_REQUEST)
    mode = str((step.get("env") or {}).get("REQUEST_HMAC_INSTALL_MODE", ""))
    assert "github.event_name == 'workflow_dispatch'" in mode
    assert "inputs.rotate_request_idempotency_hmac" in mode
    assert "'set'" in mode and "'set-once'" in mode
    assert mode.index("'set'") < mode.index("'set-once'"), "set-once must be the fallback"
    script = step.get("run", "") or ""
    assert 'case "${REQUEST_HMAC_INSTALL_MODE}" in' in script
    assert "set-once|set)" in script
    assert "request HMAC rotation is manual-dispatch only" in script
    before_install = script.split("printf '%s'")[0]
    rotation = before_install[before_install.index("# Rotating"):]
    assert "assert-absent TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY" in rotation, (
        "rotation must first prove the shared env holds no copy")
    # Refusals must actually stop the run, not just print (Codex on #4260).
    for refusal in ("invalid request HMAC install mode",
                    "request HMAC rotation is manual-dispatch only",
                    "rotation prerequisite failed: shared env exposes request admission authority"):
        tail = script[script.index(refusal):]
        assert tail.split("\n", 2)[1].strip() == "exit 1", f"'{refusal}' must be followed by exit 1"


def test_agent_key_is_installed_into_its_own_daemon_only_file():
    step = _step(INSTALL_AGENT)
    assert "secrets.TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY" in str(
        (step.get("env") or {}).get("TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY", ""))
    script = step.get("run", "") or ""
    assert re.search(
        r'printf \'%s\' "\$\{TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY\}" \|\s*\\?\s*ssh', script)
    assert "TINYASSETS_ENV_FILE=/etc/tinyassets/agent-interchange.env" in script
    assert "install-tinyassets-env.sh set TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY" in script
    assert 'echo "${TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY}"' not in script


def test_no_hmac_secret_reaches_the_deploy_step():
    deploy = next(step for step in _steps() if step.get("id") == "deploy")
    env = deploy.get("env") or {}
    assert "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY" not in env
    assert "TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY" not in env


def test_shared_env_is_scrubbed_and_fails_closed_on_a_request_key_copy():
    script = _step(SCRUB).get("run", "") or ""
    assert "delete TINYASSETS_WIKI_PATH TINYASSETS_UNIVERSE" in script
    assert "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY" in script
    delete = next(line for line in script.splitlines() if "bash -s -- delete " in line)
    for key in ("TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY", "WORKFLOW_IMAGE",
                "WORKFLOW_DATA_DIR", "WORKFLOW_MCP_CANARY_URL"):
        assert f" {key}" in delete, f"{key} must be in the delete command itself"
    for kept in ("BACKUP_DEST", "LOG_DEST", "BACKUP_GH_REPO"):
        # BACKUP_GH_REPO: deploy/backup.sh still reads it (Codex on #4260).
        assert kept not in delete, f"{kept} is host-owned and must survive deploys"
    assert "assert-absent TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY" in script
    assert "shared env still contains request admission minting authority" in script


@pytest.mark.parametrize("mode", ["set-once", "set", "assert-absent"])
def test_the_helper_still_implements_every_mode_the_workflow_calls(mode):
    helper = (_REPO / "deploy" / "install-tinyassets-env.sh").read_text(encoding="utf-8")
    assert f"    {mode})" in helper


# Moved verbatim from tests/test_deploy_prod_workflow.py.


def test_request_idempotency_hmac_template_and_compose_contract():
    template = (_REPO / "deploy/tinyassets-env.template").read_text(encoding="utf-8")
    dedicated = (_REPO / "deploy/request-idempotency-env.template").read_text(
        encoding="utf-8"
    )
    assert "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY" not in template
    assert "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY=" in dedicated
    assert "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY=change" not in dedicated

    compose = yaml.safe_load((_REPO / "deploy/compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    dedicated_path = "/etc/tinyassets/request-idempotency.env"
    assert dedicated_path in (services["daemon"].get("env_file") or [])
    for name, service in services.items():
        if name != "daemon":
            assert dedicated_path not in (service.get("env_file") or []), name


def test_request_hmac_operator_guidance_is_daemon_only_and_rotation_aware():
    deploy_doc = (_REPO / "deploy/DEPLOY.md").read_text(encoding="utf-8")
    template = (_REPO / "deploy/request-idempotency-env.template").read_text(
        encoding="utf-8"
    )
    bootstrap = (_REPO / "deploy/hetzner-bootstrap.sh").read_text(encoding="utf-8")

    assert "daemon+worker request-admission" not in deploy_doc
    assert "daemon + worker request-admission" not in deploy_doc
    assert "daemon + worker request-admission" not in template
    assert "daemon+worker request-admission" not in bootstrap
    assert "daemon+worker template" not in bootstrap
    assert "daemon-only request-admission" in deploy_doc
    assert "daemon-only request-admission" in template
    assert "daemon-only template" in bootstrap
    assert "rotate_request_idempotency_hmac=true" in deploy_doc


def test_self_host_template_declares_empty_agent_interchange_hmac_key():
    shared = (_REPO / "deploy/tinyassets-env.template").read_text(encoding="utf-8")
    dedicated = (_REPO / "deploy/agent-interchange-env.template").read_text(
        encoding="utf-8"
    )
    assert "TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY" not in shared
    assert "TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY=" in dedicated
    assert "TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY=change" not in dedicated

    compose = yaml.safe_load((_REPO / "deploy/compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    dedicated_path = "/etc/tinyassets/agent-interchange.env"
    assert dedicated_path in services["daemon"]["env_file"]
    for name, service in services.items():
        if name != "daemon":
            assert dedicated_path not in (service.get("env_file") or []), name


@pytest.mark.parametrize("name", [
    VALIDATE_AGENT, VALIDATE_REQUEST, INSTALL_REQUEST, INSTALL_AGENT, VALIDATE_HOST_PAIR, SCRUB,
])
def test_every_hmac_step_fails_the_run(name):
    """`continue-on-error` or a soft `if:` would turn each fail-closed check into a
    log line (Codex on #4260 mutated exactly this and the structure tests passed)."""
    step = _step(name)
    assert not step.get("continue-on-error"), f"{name} must fail the run"
    assert "if" not in step, f"{name} must run on every deploy"
    script = step.get("run", "") or ""
    if "\n" in script.strip():
        assert script.lstrip().startswith("set -euo pipefail"), f"{name} must stop on error"


def test_the_shared_env_guard_exits_nonzero_on_a_copy():
    script = _step(SCRUB).get("run", "") or ""
    guard = script[script.index("assert-absent TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY"):]
    assert "|| {" in guard and "exit 1" in guard.split("}")[0]

