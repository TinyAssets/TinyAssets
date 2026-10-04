"""Tests for .github/workflows/deploy-prod.yml structure and DO secret names.

Covers:
  (a) YAML parses without error
  (b) workflow_dispatch trigger is present (manual test-deploy path)
  (c) workflow_run trigger fires on build-image success
  (d) Required DO secret names referenced (not legacy Hetzner names)
  (e) SSH key file and known_hosts use DO_DROPLET_HOST variable
  (f) Post-deploy canary step probes ONLY canonical URL (not direct)
  (g) Rollback step present and conditioned on failure
  (h) CF Access gate step blocks deploy on 200 (Access broken); advisory on tunnel-down
  (i) No platform model credential is synced, prepared or verified (Hard Rule 15)
  (j) Droplet disk pressure is pruned before image pull/restart
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.retire_cheat_loop_deploy_fence import RECOVERY_SCRIPT_PATH
from scripts.sanitize_startup_diagnostics import (
    STATE_SEPARATOR,
    sanitize_candidate_state,
)

try:
    import yaml

    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO / ".github" / "workflows" / "deploy-prod.yml"
_RECOVERY_OVERRIDE = _REPO / "deploy" / "recovery-restart-no.yml"

pytestmark = pytest.mark.skipif(not _YAML_AVAILABLE, reason="pyyaml not installed")


def _load() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def _triggers(wf: dict) -> dict:
    return wf.get(True, {}) or {}


# ---------------------------------------------------------------------------
# (a) YAML parses
# ---------------------------------------------------------------------------


def test_deploy_prod_yml_parses():
    _load()


# ---------------------------------------------------------------------------
# (b) workflow_dispatch present (manual deploy path)
# ---------------------------------------------------------------------------


def test_has_workflow_dispatch_trigger():
    wf = _load()
    triggers = _triggers(wf)
    assert "workflow_dispatch" in triggers, (
        "deploy-prod must have workflow_dispatch for manual invocation"
    )


def test_workflow_dispatch_has_image_tag_input():
    wf = _load()
    triggers = _triggers(wf)
    dispatch = triggers.get("workflow_dispatch") or {}
    inputs = dispatch.get("inputs") or {}
    assert "image_tag" in inputs, "workflow_dispatch must expose image_tag input"


def test_manual_unsafe_fence_recovery_is_separate_and_source_bound():
    wf = _load()
    inputs = (_triggers(wf).get("workflow_dispatch") or {}).get("inputs") or {}
    assert "unsafe_fence_source_run_id" in inputs
    assert "<run_id>-<attempt>" in str(
        inputs["unsafe_fence_source_run_id"].get("description", "")
    )
    recovery = wf["jobs"]["recover-unsafe"]
    assert "workflow_dispatch" in str(recovery.get("if", ""))
    checkout = recovery["steps"][0]
    assert checkout.get("uses") == "actions/checkout@v4"
    assert checkout.get("with", {}).get("fetch-depth") == 0
    step = _step_named({"jobs": {"deploy": recovery}}, "Recover canonical unsafe fence")
    script = str(step.get("run", ""))
    assert "recover-unsafe --source-run-id" in script
    assert "[A-Za-z0-9._-]{1,128}" in script
    assert step.get("env", {}).get("SOURCE_RUN_ID") == (
        "${{ inputs.unsafe_fence_source_run_id }}"
    )
    assert "inputs.unsafe_fence_source_run_id" not in script
    assert " --image-ref " in script
    assert " --revision " in script
    assert " --expected-script-sha256 " in script
    assert "sha256sum scripts/retire_cheat_loop_deploy_fence.py" in script
    assert "sha256sum -c -" in script
    assert "set -euo pipefail" in script
    assert "deploy/recovery-restart-no.yml" in script
    assert "deploy/tinyassets-recovery-reconcile.service" in script
    assert "systemctl enable tinyassets-recovery-reconcile.service" in script
    reconcile_unit = (
        Path("deploy/tinyassets-recovery-reconcile.service")
        .read_text(encoding="utf-8")
    )
    assert "reconcile-recovery-on-boot" in reconcile_unit
    assert "After=docker.service" in reconcile_unit
    for unit in (
        "daemon-watchdog.timer",
        "tinyassets-watchdog.timer",
        "tinyassets-autoheal.timer",
        "tinyassets-daemon.service",
    ):
        assert unit in reconcile_unit
    recovery_script_path = RECOVERY_SCRIPT_PATH.as_posix()
    assert recovery_script_path in script
    assert (
        f"/tmp/retire-cheat-loop-deploy-fence.py {recovery_script_path}"
        in script
    )
    assert "recovery_pending_canary" not in script
    resolve = _step_named(
        {"jobs": {"deploy": recovery}}, "Resolve unsafe recovery image"
    )
    resolve_script = str(resolve.get("run", ""))
    assert "docker buildx imagetools inspect" in resolve_script
    assert "org.opencontainers.image.revision" in resolve_script
    assert "35da9d4fc1a1fc51d3db56bf5d1627691f54d894" in resolve_script
    assert "git merge-base --is-ancestor" in resolve_script
    refence = _step_named(
        {"jobs": {"deploy": recovery}}, "Re-fence failed recovery"
    )
    refence_script = str(refence.get("run", ""))
    assert "refence-recovery --source-run-id" in refence_script
    assert "[A-Za-z0-9._-]{1,128}" in refence_script
    assert "quiesce-unsafe" not in refence_script
    assert "cancelled()" in str(refence.get("if", ""))
    finalize = _step_named(
        {"jobs": {"deploy": recovery}},
        "Finalize canonical unsafe-fence recovery",
    )
    assert "finalize-recovery --source-run-id" in str(finalize.get("run", ""))
    step_names = [str(item.get("name", "")) for item in recovery["steps"]]
    pull_index = step_names.index("Pull recovery image on production host")
    recover_index = step_names.index("Recover canonical unsafe fence")
    assert pull_index < recover_index
    host_pull = _step_named(
        {"jobs": {"deploy": recovery}},
        "Pull recovery image on production host",
    )
    host_pull_script = str(host_pull.get("run", ""))
    assert host_pull.get("env", {}).get("RECOVERY_IMAGE_REF") == (
        "${{ steps.recovery-image.outputs.image_ref }}"
    )
    assert "sudo docker pull '${RECOVERY_IMAGE_REF}'" in host_pull_script
    assert step_names.index("Recovery daemon MCP canary (loopback)") < step_names.index(
        "Finalize canonical unsafe-fence recovery"
    )
    assert step_names.index("Recovery daemon MCP canary (loopback)") < step_names.index(
        "Finalize canonical unsafe-fence recovery"
    )
    assert "inputs.unsafe_fence_source_run_id == ''" in str(
        wf["jobs"]["deploy"].get("if", "")
    )


def test_recovery_override_fences_writers_and_fixed_name_sidecars():
    """The override must fence every default-profile service, and only those.

    The four `worker*` entries were dropped 2026-08-29 with the host-run fleet
    (nothing runs outside a user's universe -- PLAN.md). Compared AGAINST
    compose.yml rather than a hardcoded list: an override naming a service the
    base file does not define would declare an imageless service and fail the
    whole project on `-f compose.yml -f override`.
    """
    override = yaml.safe_load(_RECOVERY_OVERRIDE.read_text(encoding="utf-8"))
    services = override["services"]
    compose = yaml.safe_load((_REPO / "deploy" / "compose.yml").read_text(encoding="utf-8"))
    default_profile = {
        name
        for name, service in compose["services"].items()
        if not service.get("profiles")
    }
    assert set(services) == default_profile, (
        "recovery override must fence exactly the default-profile services"
    )
    assert set(services) == {"daemon", "cloudflared", "logs"}
    assert all(service.get("restart") == "no" for service in services.values())


def test_deploy_resolves_image_to_digest_and_never_latest():
    text = _text()
    assert "image_ref=" in text
    assert "docker buildx imagetools inspect" in text
    assert 'tag="latest"' not in text
    assert ":latest" not in text, "deploy-prod must not use :latest for deploy or rollback targets"


def test_manual_image_tag_is_env_bound_and_validated_before_use():
    wf = _load()
    step = _step_named(wf, "Resolve image tag")
    run_script = step.get("run", "") or ""
    env = step.get("env") or {}

    assert env.get("REQUESTED_IMAGE_TAG") == "${{ inputs.image_tag }}"
    assert "${{ inputs.image_tag }}" not in run_script, (
        "workflow input must not be interpolated into executable shell source"
    )
    assert "[A-Za-z0-9_][A-Za-z0-9._-]{0,127}" in run_script
    assert "refusing invalid OCI image tag" in run_script


def test_resolved_digest_is_canonical_before_any_host_write():
    wf = _load()
    step = _step_named(wf, "Resolve image tag")
    run_script = step.get("run", "") or ""

    assert "sha256:[0-9a-f]{64}" in run_script
    assert "refusing non-canonical immutable image digest" in run_script


def test_capture_previous_uses_configured_and_running_digest_observations():
    wf = _load()
    step = _step_named(wf, "Capture previous image tag (for rollback)")
    run_script = step.get("run", "") or ""

    assert "docker inspect --type container" in run_script
    assert "{{.Image}}" in run_script
    assert "tinyassets-daemon" in run_script
    assert "docker image inspect" in run_script
    assert "{{json .RepoDigests}}" in run_script
    assert "configured_image_ref=" in run_script
    assert "running_image_ref=" in run_script
    assert "previous=" in run_script
    assert "docker buildx imagetools inspect" not in run_script, (
        "a mutable configured tag cannot be converted into rollback proof"
    )


def test_capture_previous_transports_bounded_prior_receipt_read_only():
    wf = _load()
    step = _step_named(wf, "Capture previous image tag (for rollback)")
    run_script = step.get("run", "") or ""

    assert "docker volume inspect tinyassets-data" in run_script
    assert "head -c 65537" in run_script
    assert "base64 -w0" in run_script
    assert "prior_receipt_b64=" in run_script
    for forbidden in (" install ", " mv ", " rm ", "set TINYASSETS_IMAGE"):
        assert forbidden not in run_script, (
            "pre-mutation capture must remain read-only on the production host"
        )


def test_capture_previous_does_not_emit_untrusted_image_labels_as_outputs():
    wf = _load()
    step = _step_named(wf, "Capture previous image tag (for rollback)")
    run_script = step.get("run", "") or ""

    assert "previous_active_revision_label=" not in run_script
    assert "active_revision_label" not in run_script
    assert "org.opencontainers.image.revision" not in run_script


# ---------------------------------------------------------------------------
# (c) workflow_run trigger fires on build-image success
# ---------------------------------------------------------------------------


def test_has_workflow_run_trigger():
    wf = _load()
    triggers = _triggers(wf)
    assert "workflow_run" in triggers


def test_workflow_run_fires_on_build_image():
    wf = _load()
    triggers = _triggers(wf)
    wr = triggers.get("workflow_run") or {}
    workflows = wr.get("workflows", [])
    assert any("Build" in w for w in workflows), (
        "workflow_run must reference the build-image workflow"
    )


# ---------------------------------------------------------------------------
# (d) DO secret names — not legacy Hetzner names
# ---------------------------------------------------------------------------


def test_do_droplet_host_secret_referenced():
    assert "DO_DROPLET_HOST" in _text()


def test_do_ssh_user_secret_referenced():
    assert "DO_SSH_USER" in _text()


def test_do_ssh_key_secret_referenced():
    assert "DO_SSH_KEY" in _text()


def test_no_legacy_hetzner_secrets():
    text = _text()
    assert "HETZNER_HOST" not in text, "Legacy HETZNER_HOST still in deploy-prod.yml"
    assert "HETZNER_SSH_USER" not in text, "Legacy HETZNER_SSH_USER still in deploy-prod.yml"
    assert "HETZNER_SSH_KEY" not in text, "Legacy HETZNER_SSH_KEY still in deploy-prod.yml"


# ---------------------------------------------------------------------------
# (e) SSH step uses DO_DROPLET_HOST
# ---------------------------------------------------------------------------


def test_ssh_keyscan_uses_do_droplet_host():
    assert "DO_DROPLET_HOST" in _text()
    assert "hetzner_deploy" not in _text(), "Stale hetzner_deploy key filename still in workflow"


# ---------------------------------------------------------------------------
# (f) Post-deploy canary step present
# ---------------------------------------------------------------------------


def _steps(wf: dict) -> list[dict]:
    return wf.get("jobs", {}).get("deploy", {}).get("steps", [])


def _step_named(wf: dict, name: str) -> dict:
    step = next(
        (candidate for candidate in _steps(wf) if candidate.get("name") == name),
        None,
    )
    assert step is not None, f"deploy job must include a '{name}' step"
    return step


def _step_with_run_token(wf: dict, token: str) -> dict:
    step = next(
        (candidate for candidate in _steps(wf) if token in (candidate.get("run", "") or "")),
        None,
    )
    assert step is not None, f"deploy job must include a run step containing {token!r}"
    return step


def _previous_executable_line(lines: list[str], before: int) -> str:
    for line in reversed(lines[:before]):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def test_post_deploy_canary_step_present():
    wf = _load()
    names = [s.get("name", "") for s in _steps(wf)]
    assert any("canary" in (n or "").lower() for n in names), (
        "deploy job must have a post-deploy canary step"
    )


def test_canary_step_only_probes_canonical():
    """Canary must NOT probe the direct URL (returns 403 after CF Access cutover)."""
    wf = _load()
    for step in _steps(wf):
        name = step.get("name", "") or ""
        if "canary" in name.lower() and "access" not in name.lower():
            run_script = step.get("run", "") or ""
            assert "DIRECT_URL" not in run_script, (
                f"Canary step '{name}' must not probe DIRECT_URL — it correctly "
                "returns 403 after CF Access Option-1 cutover. Only canonical URL is valid."
            )
            assert "CANARY_URL" in run_script, (
                f"Canary step '{name}' must probe CANARY_URL (canonical)"
            )
            return
    pytest.fail("Post-deploy canary step not found")


def test_access_gate_step_present():
    """A post-deploy step must prove Cloudflare Access still gates the origin.

    #2442 deleted it, so for six weeks a change that opened the Access-gated
    internal origin would have deployed green: the public canary goes through
    the Worker and says nothing about the origin behind it.
    """
    wf = _load()
    steps = _steps(wf)
    access_steps = [s for s in steps if "access" in (s.get("name") or "").lower()]
    assert access_steps, (
        "deploy job must have a CF Access gate verification step"
    )
    gate = _step_named(wf, "Verify CF Access gates the direct origin (expects 403)")
    assert gate.get("id") == "access-gate"
    # After the public surfaces, before the rollback, and only on a green run.
    canary = _step_named(wf, "Public MCP canary (--assert-handles)")
    rollback = _step_named(wf, "Roll back if the public canary is red")
    assert steps.index(canary) < steps.index(gate) < steps.index(rollback)
    assert "if" not in gate, "the gate runs only when the deploy is otherwise green"
    # A red gate must NOT revert a healthy image: an image rollback cannot
    # restore a missing Cloudflare policy. Detection, not containment.
    assert "access" not in str(rollback.get("if", "")).lower()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash to execute the step")
@pytest.mark.parametrize(
    "curl_rc,http_out,want_rc",
    [(6, "000", 0), (28, "000", 0), (0, "403", 0), (0, "401", 1), (0, "200", 1)],
)
def test_access_gate_step_executes_under_errexit(tmp_path, curl_rc, http_out, want_rc):
    """Run the step's script the way Actions does (`bash -e`), curl stubbed.

    String assertions passed while a transfer failure aborted the step before
    `curl_rc` was read: `set -uo pipefail` does not clear the inherited errexit.
    """
    wf = _load()
    gate = _step_named(wf, "Verify CF Access gates the direct origin (expects 403)")
    stub = tmp_path / "curl"
    # LF endings: a CRLF script breaks bash on a Windows checkout.
    stub.write_bytes(f"#!/usr/bin/env bash\nprintf '%s' '{http_out}'\nexit {curl_rc}\n".encode())
    stub.chmod(0o755)
    script = tmp_path / "step.sh"
    # Set inside the script, not via env=: a Windows `bash` may be WSL, which
    # does not inherit the caller's environment.
    prelude = 'export DIRECT_ORIGIN=origin.invalid\nexport PATH="$(pwd):$PATH"\n'
    script.write_bytes((prelude + str(gate.get("run", ""))).encode())
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", script.name],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == want_rc, proc.stdout + proc.stderr
    if curl_rc:
        assert "could not reach the direct origin" in proc.stdout


def test_access_gate_treats_our_own_401_as_an_open_gate():
    """401 is a FAILURE here, which is the whole point of the step.

    The Access policy is service-token, non-identity
    (``scripts/cf_access_cutover.py``), so Cloudflare denies an unauthenticated
    request with **403** and no login redirect. Our own application answers an
    anonymous GET on ``/mcp`` with **401**
    (``tinyassets/auth/middleware.py``). So a 401 proves the request reached the
    application and Access did not stop it -- the exact hole this step exists to
    catch. The deleted original, and the first version of this restore, both
    accepted 401 and so certified the failure as a pass (Codex, 2026-10-03).
    """
    wf = _load()
    gate = _step_named(wf, "Verify CF Access gates the direct origin (expects 403)")
    run_script = str(gate.get("run", ""))

    # The pass branch is 403 and only 403.
    pass_branch = re.search(r'elif \[ "\$\{http_code\}" = "([0-9]{3})" \]; then\n\s*echo "Access gate confirmed',
                            run_script)
    assert pass_branch, "the step has a single, identifiable pass branch"
    assert pass_branch.group(1) == "403", (
        f"only 403 proves Access denied the request; found {pass_branch.group(1)}"
    )

    # 401 is named explicitly, and it exits non-zero.
    assert '"${http_code}" = "401"' in run_script, (
        "401 must be handled explicitly, not swept into a generic branch, so the "
        "message can say it is OUR middleware answering rather than Access"
    )
    assert "our application, which answered 401 itself" in run_script
    assert run_script.count("exit 1") >= 2, "401 and every other non-403 fail"

    # The advisory band is transfer failures and codes that prove nothing.
    for code in ("429", "502", "503", "504", "530"):
        assert f'"${{http_code}}" = "{code}"' in run_script, (
            f"{code} proves nothing about the policy and must stay advisory"
        )

    # curl's exit status is read SEPARATELY from the status code. The earlier
    # `|| echo 000` appended to curl's own "000" on a connect failure, giving
    # 000000, which matched no branch and failed the deploy -- breaking the
    # advisory band the step was written to provide.
    assert "curl_rc=$?" in run_script
    assert "|| curl_rc=$?" in run_script, (
        "a bare `x=$(curl ...)` exits under Actions' default `bash -e` before "
        "curl_rc is read; capture it with `|| curl_rc=$?`"
    )
    assert '[ "${curl_rc}" != "0" ]' in run_script
    code_lines = [
        line for line in run_script.splitlines() if not line.strip().startswith("#")
    ]
    assert not any("|| echo 000" in line for line in code_lines), (
        "concatenating a fallback onto curl's own output produced 000000 "
        "(the step's comment may describe the old bug; the code may not have it)"
    )
    # A missing curl is not an advisory pass: the check could not run at all.
    assert "command -v curl" in run_script

    # Still satisfies the original contract: 200 blocks, and the exit is guarded
    # rather than unconditional.
    assert "exit 1" in run_script
    assert run_script.count("exit 1") < run_script.count("if [")
def test_rollback_step_present():
    wf = _load()
    names = [s.get("name", "") for s in _steps(wf)]
    assert any("rollback on failure" in (n or "").lower() for n in names), (
        "deploy job must have a 'Rollback on failure' step"
    )


def test_failed_candidate_diagnostics_are_preserved_before_rollback():
    """Evidence outlives the rollback, and never delays it.

    Two landmarks this test used to key on are gone for different reasons, and
    the difference is the finding:

    * ``Wait for daemon health`` was REPLACED, not dropped. #2442 moved the wait
      into ``deploy/deploy_fail_safe.sh``, which reaches 'healthy' within
      ``HEALTH_TIMEOUT`` or rolls itself back (rc 2); the workflow proves the
      PUBLIC surfaces separately afterwards. Asserted through the step that now
      owns it.
    * ``Rollback on failure``, the task 2.1 cleanup and the ``terminal`` receipt
      outputs belonged to the stop-writer fence the same PR retired, so their
      orderings are not re-asserted.

    What did NOT survive was the diagnostics path: #2442 took the capture and
    upload with it and left ``scripts/sanitize_startup_diagnostics.py`` with no
    caller, so a failed prod deploy kept nothing.

    The split into snapshot-then-rollback-then-sanitize is a Codex P1 finding on
    the first version of this restore: collecting evidence over two SSH calls
    before the rollback left the broken candidate serving while it ran. Only the
    fast raw-bytes snapshot may precede the rollback.
    """
    wf = _load()
    steps = _steps(wf)
    deploy = _step_named(wf, "Run fail-safe deploy on the droplet")
    snapshot = _step_named(wf, "Snapshot failed candidate evidence (before rollback)")
    sanitize = _step_named(wf, "Sanitize failed candidate diagnostics")
    upload = _step_named(wf, "Upload failed candidate startup diagnostics")
    rollback = _step_named(wf, "Roll back if the public canary is red")
    terminal = _step_named(wf, "Publish release-state receipt")

    # The ONLY thing between the deploy and the rollback is the raw snapshot.
    assert steps.index(deploy) < steps.index(snapshot) < steps.index(rollback)
    # Everything that costs time happens after production is recovered.
    assert steps.index(rollback) < steps.index(sanitize) < steps.index(terminal)
    assert steps.index(terminal) < steps.index(upload)
    assert deploy.get("id") == "deploy"
    assert snapshot.get("id") == "candidate_snapshot"
    assert sanitize.get("id") == "candidate_diagnostics"

    # The health wait is the fail-safe script's, bounded and self-rolling-back,
    # which is why no workflow step polls for it any more.
    deploy_run = str(deploy.get("run", ""))
    assert "HEALTH_TIMEOUT=180" in deploy_run
    assert "deploy_fail_safe.sh" in deploy_run
    assert "snapshot_candidate_evidence.sh" in deploy_run, (
        "the snapshot script ships in the same scp as the deploy script"
    )

    # ---- the snapshot must be cheap and bounded -------------------------
    snapshot_condition = str(snapshot.get("if", "")).strip()
    assert snapshot_condition == (
        "${{ always() && steps.deploy.outputs.rc == '0' "
        "&& (failure() || cancelled()) }}"
    )
    assert "steps.deploy.outputs.rc == '0'" in snapshot_condition, (
        "rc 2 was already rolled back inside the script, so its container is "
        "the PREVIOUS image; reading it would mislabel the evidence"
    )
    assert "steps.canary.outcome" not in snapshot_condition, (
        "a cancellation, or any later failure after a good swap, still needs "
        "identity-bound evidence -- not only a red canary"
    )
    assert snapshot.get("continue-on-error") is True, (
        "the rollback must run even if the snapshot fails"
    )
    assert 0 < int(snapshot["timeout-minutes"]) <= 2, (
        "an unbounded snapshot step is an unbounded delay before rollback"
    )
    snapshot_run = str(snapshot.get("run", ""))
    assert snapshot_run.count("ssh ") == 1, (
        "ONE round trip: two sequential SSH calls is what delayed the rollback"
    )
    assert "timeout 25s ssh" in snapshot_run
    assert "ConnectTimeout=10" in snapshot_run
    assert "ServerAliveInterval=5" in snapshot_run
    assert "ServerAliveCountMax=2" in snapshot_run
    assert "exit 0" in snapshot_run
    # No sanitizing, no artifact work, no local python on the critical path.
    assert "sanitize_startup_diagnostics.py" not in snapshot_run
    assert "scp" not in snapshot_run

    # ---- the snapshot script binds both reads to one container id -------
    script = Path("deploy/snapshot_candidate_evidence.sh").read_text(encoding="utf-8")
    assert "--format '{{.Id}}'" in script, "the id is resolved first"
    assert script.count('"${cid}"') >= 3, (
        "inspect and logs must read the ID, not the mutable container NAME: "
        "a replacement between two name reads can supply the rollback "
        "container's logs under a matching manifest"
    )
    assert "docker logs --tail" in script
    assert 'tail -c "${LOG_BYTES}"' in script
    assert "LOG_BYTES=131072" in script
    assert "STATE_BYTES=16385" in script
    assert "rm -f" in script, "a stale file from an earlier deploy is not evidence"
    assert script.count("timeout ") >= 3, "every docker call is bounded"
    assert "exit 0" in script
    assert "org.opencontainers.image.revision" in script
    assert ".Config.Image" in script
    assert ".Config.Env" not in script
    assert "/etc/tinyassets/env" not in script
    assert "docker compose" not in script
    state_template_match = re.search(r"--format '(\{\{\.State\.Status\}\}[^']+)'", script)
    assert state_template_match is not None, "the inspect template is quoted once"
    state_template = state_template_match.group(1)
    assert state_template.split("|")[:6] == [
        "{{.State.Status}}",
        "{{.State.Running}}",
        "{{.State.Restarting}}",
        "{{.State.ExitCode}}",
        "{{.State.OOMKilled}}",
        "{{if .State.Health}}{{.State.Health.Status}}{{end}}",
    ], state_template
    assert state_template.endswith("{{json .State.Error}}")

    # ---- sanitizing is after recovery, non-fatal, and fails closed -----
    sanitize_condition = str(sanitize.get("if", "")).strip()
    assert sanitize_condition == (
        "${{ always() && steps.candidate_snapshot.outcome != 'skipped' }}"
    )
    assert sanitize.get("continue-on-error") is True
    assert 0 < int(sanitize["timeout-minutes"]) <= 5
    sanitize_run = str(sanitize.get("run", ""))
    assert "scripts/sanitize_startup_diagnostics.py" in sanitize_run
    assert '--target-revision "${TARGET_REVISION}"' in sanitize_run
    assert '--target-image-ref "${TARGET_IMAGE_REF}"' in sanitize_run
    assert "TARGET_REVISION" in (sanitize.get("env") or {})
    assert "TARGET_IMAGE_REF" in (sanitize.get("env") or {})
    assert '"capture":"unavailable"' in sanitize_run, (
        "a vanished container produces unavailable evidence, not a manifest "
        "that implies the logs were the candidate's"
    )
    assert '"candidate_identity_match":false' in sanitize_run, "the default is no match"
    assert '"container_id"' in sanitize_run, "the manifest records which container"
    assert 'rm -f "${raw_log}"' in sanitize_run, "raw bytes never reach the artifact"
    assert "exit 0" in sanitize_run
    assert "deploy_fail_safe.sh" not in sanitize_run, "it must not touch production"

    # ---- the upload is non-fatal and bounded ---------------------------
    upload_with = upload.get("with") or {}
    upload_condition = str(upload.get("if", "")).strip()
    assert upload_condition == (
        "${{ always() && steps.candidate_diagnostics.outcome == 'success' }}"
    )
    assert upload.get("continue-on-error") is True, (
        "an artifact-service error on the recovery path must not fail the job"
    )
    assert 0 < int(upload["timeout-minutes"]) <= 5, (
        "a slow upload holds the production-host-mutation group"
    )
    assert (
        upload.get("uses")
        == "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
    )
    # The invariant is that every upload is pinned to the reviewed commit, not
    # how many uploads there are: the count was 3 when two of them belonged to
    # the stop-writer artifacts #2442 retired.
    assert "actions/upload-artifact@v4" not in _text()
    uploads = re.findall(r"actions/upload-artifact@(\S+)", _text())
    assert uploads, "the diagnostics upload is the one artifact this job writes"
    assert set(uploads) == {"ea165f8d65b6e75b540449e92b4886f43607fa02"}, uploads
    assert upload_with.get("if-no-files-found") == "warn", (
        "an empty evidence dir is legitimate when the container was gone"
    )
    assert 0 < int(upload_with["retention-days"]) <= 7

def test_rollback_runs_always_and_eligibility_keys_to_image_marker():
    wf = _load()
    step = _step_named(wf, "Rollback on failure")
    cond = str(step.get("if", ""))
    step_env = step.get("env") or {}
    run_script = step.get("run", "") or ""

    assert cond.strip() == "always()", (
        "rollback must always run so pre-host, pre-image, success, and required "
        "rollback paths all publish a bounded result tuple"
    )
    assert "failure()" not in cond
    assert "steps.prev.outputs.previous != ''" not in cond
    assert "image_mutation_started" in str(step_env.get("IMAGE_MUTATION_STARTED", "")), (
        "rollback eligibility must consume the image-mutation marker"
    )
    assert "IMAGE_MUTATION_STARTED" in run_script
    assert "production_mutation_started" not in cond, (
        "production mutation requires terminal publication, but it must not "
        "make image rollback eligible"
    )


# ---------------------------------------------------------------------------
# (i) No platform model credential in the deploy (Hard Rule 15)
# ---------------------------------------------------------------------------


def test_deploy_syncs_no_platform_model_credential():
    """The platform has no LLM (Hard Rule 15): no step syncs, seeds or verifies
    a platform model login, key, bundle or opt-in switch. Full guard across
    every workflow: tests/test_no_platform_llm_credentials.py."""
    text = _text()
    for name in (
        "TINYASSETS_CODEX_AUTH_JSON_B64",
        "TINYASSETS_CLAUDE_CREDENTIALS_JSON_B64",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "TINYASSETS_ALLOW_API_KEY_PROVIDERS",
        "OPENAI_API_KEY",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
    ):
        assert name not in text, f"deploy-prod.yml still handles {name}"
    assert "verify_llm_binding.py" not in text, (
        "the deploy must not require the platform to report a bound model"
    )


def test_deploy_syncs_runtime_compose_and_systemd_files():
    """The sync step STAGES the runtime bundle and installs nothing.

    Installing here mutated production config outside the fail-safe
    transaction: the auth-volume step, the deploy_fail_safe.sh scp, the
    host-mutation lock and the candidate-image preflight can all fail AFTER the
    install, leaving the new compose file live under the old image with no
    rollback path that restores it (Codex ADAPT on PR #2685, 2026-08-29).
    deploy_fail_safe.sh now owns validate -> snapshot -> install -> restore.
    """
    wf = _load()
    sync_step = next(
        (s for s in _steps(wf) if s.get("name") == "Sync runtime deploy files"),
        None,
    )
    assert sync_step is not None, "deploy must stage the runtime bundle"
    run_script = sync_step.get("run", "") or ""

    for repo_file in (
        "deploy/compose.yml",
        "deploy/vector.yaml",
        "deploy/vector-betterstack.yaml",
        "deploy/vector-entrypoint.sh",
        "deploy/tinyassets-daemon.service",
    ):
        assert repo_file in run_script, f"{repo_file} must be staged"
    assert "/tmp/tinyassets-bundle-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}" in run_script, (
        "the stage must be PER RUN: a shared path is populated before the "
        "script's host-mutation lock exists, so a concurrent deploy could swap "
        "the bytes between validation and install"
    )
    assert "mkdir -p" in run_script, "the stage directory must be created first"

    # Installs nothing: no destination path and no reload belongs in this step.
    for install_marker in (
        "/opt/tinyassets/compose.yml",
        "/opt/tinyassets/deploy/",
        "/etc/systemd/system/tinyassets-daemon.service",
        "systemctl daemon-reload",
        "install -m",
    ):
        assert install_marker not in run_script, (
            f"the sync step must stage only; {install_marker!r} installs runtime "
            "config outside the fail-safe transaction"
        )


def test_deploy_bundle_is_installed_inside_the_fail_safe_transaction():
    """The install the sync step gave up must live in deploy_fail_safe.sh."""
    script = (_REPO / "deploy" / "deploy_fail_safe.sh").read_text(encoding="utf-8")
    for marker in (
        "/tmp/tinyassets-bundle",
        "/var/lib/tinyassets-deploy",
        "bundle-previous",
        "bundle-snapshots",
        "validate_bundle",
        "snapshot_bundle",
        "install_bundle",
        "restore_previous_bundle",
        "--force-recreate logs",
        # the entrypoint keeps its exec bit; the rest of the bundle is 0644
        "vector-entrypoint.sh|0755|runtime",
        # round 2: the transaction is fail-loud, atomic, and race-free
        "claim_bundle",
        "write_pointer",
        "bundle-dirty",
        "prune_snapshots",
        "manifest",
        "mv -f",
        "INSTALLED_THIS_RUN",
    ):
        assert marker in script, f"deploy_fail_safe.sh must own {marker!r}"
    assert "--restore-bundle" in script
    # bundle_install_failed / bundle_pointer_failed reach the caller through
    # `recover_from_failed_install <result>`, which restores the snapshot first.
    for result in (
        "deploy_result=bundle_invalid",
        "deploy_result=bundle_dirty",
        "deploy_result=rollback_failed",
        "recover_from_failed_install bundle_install_failed",
        "recover_from_failed_install bundle_pointer_failed",
    ):
        assert result in script, f"deploy_fail_safe.sh must be able to report {result!r}"


def test_deploy_passes_the_per_run_stage_directory_to_the_script():
    """The script must be told which stage to consume, not guess a shared path."""
    wf = _load()
    steps = _steps(wf)
    stage_step = next(s for s in steps if s.get("name") == "Sync runtime deploy files")
    assert stage_step.get("id") == "stage_bundle", (
        "the stage step needs an id so the deploy step can consume its output"
    )
    assert 'echo "stage=${stage}" >> "$GITHUB_OUTPUT"' in (stage_step.get("run") or "")

    deploy_step = next(s for s in steps if s.get("id") == "deploy")
    assert (
        deploy_step.get("env", {}).get("BUNDLE_DIR")
        == "${{ steps.stage_bundle.outputs.stage }}"
    )
    assert "BUNDLE_DIR='${BUNDLE_DIR}'" in (deploy_step.get("run") or "")


def test_canary_rollback_restores_the_bundle_before_the_image():
    """Converging PREV_IMAGE against the NEW compose file rolls back half a change."""
    wf = _load()
    rollback = next(
        (
            s
            for s in _steps(wf)
            if s.get("name") == "Roll back if the public canary is red"
        ),
        None,
    )
    assert rollback is not None, "the canary rollback step must exist"
    run_script = rollback.get("run", "") or ""
    assert "deploy_fail_safe.sh --restore-bundle" in run_script, (
        "the canary rollback must restore the runtime bundle snapshot before "
        "converging the previous image"
    )
    assert "${PREV_IMAGE}" in run_script


# ---------------------------------------------------------------------------
# (j) Disk preflight before image pull/restart
# ---------------------------------------------------------------------------


def test_disk_preflight_runs_before_deploy_image_pull():
    wf = _load()
    steps = _steps(wf)
    names = [s.get("name", "") for s in steps]
    preflight_idx = next(
        i for i, name in enumerate(names) if name == "Preflight droplet disk before image pull"
    )
    deploy_idx = next(i for i, step in enumerate(steps) if step.get("id") == "deploy")

    assert preflight_idx < deploy_idx, (
        "disk preflight must happen before TINYASSETS_IMAGE is changed, "
        "docker pull runs, or systemd restart can take the live daemon down"
    )


def test_disk_preflight_prunes_disposable_state_and_fails_before_restart():
    wf = _load()
    step = next(
        s for s in _steps(wf) if s.get("name") == "Preflight droplet disk before image pull"
    )
    run_script = step.get("run", "") or ""

    assert "df -h / /var/lib/docker /data" in run_script
    assert "docker system prune -af" in run_script
    assert "docker builder prune -af" in run_script
    assert "journalctl --vacuum-time=3d" in run_script
    assert "fail_threshold=90" in run_script
    assert "refusing deploy before image pull/restart" in run_script


def test_deploy_preserves_host_owned_backup_destination():
    wf = _load()
    scrub_step = next(
        (s for s in _steps(wf) if s.get("name") == "Scrub stale cloud env overrides"),
        None,
    )
    assert scrub_step is not None
    run_script = scrub_step.get("run", "") or ""

    assert "BACKUP_DEST" not in run_script


def test_deploy_preserves_host_owned_log_destination():
    wf = _load()
    scrub_step = next(
        (s for s in _steps(wf) if s.get("name") == "Scrub stale cloud env overrides"),
        None,
    )
    assert scrub_step is not None
    run_script = scrub_step.get("run", "") or ""

    assert "LOG_DEST" not in run_script


# Three fleet-only cases were deleted here on 2026-08-29:
# `test_deploy_verifies_cloud_worker_running`,
# `test_deploy_proves_running_workers_lack_request_hmac`, and
# `test_deploy_rejects_cloud_worker_workflow_universe_override`. All three
# asserted a "Verify cloud worker is running" step over the four
# `tinyassets-worker*` containers. Those containers are gone with the host-run
# fleet (nothing runs outside a user's universe -- PLAN.md), and the step they
# asserted had already been removed from deploy-prod.yml, so all three were
# already red at b9225243 before this change touched anything.


def test_deploy_retires_legacy_workflow_service_before_restart():
    wf = _load()
    steps = _steps(wf)
    retire_idx = next(
        (i for i, s in enumerate(steps) if s.get("name") == "Retire legacy Workflow service"),
        None,
    )
    deploy_idx = next(
        (i for i, s in enumerate(steps) if s.get("name") == "Deploy new image"),
        None,
    )
    assert retire_idx is not None
    assert deploy_idx is not None
    assert retire_idx < deploy_idx

    run_script = steps[retire_idx].get("run", "") or ""
    assert "workflow-daemon.service" in run_script
    assert "workflow.service" in run_script
    assert "workflow-watchdog.timer" in run_script
    assert "workflow-backup.timer" in run_script
    assert "workflow-ship-logs.timer" in run_script
    assert "systemctl disable --now" in run_script
    assert "/opt/workflow/compose.yml" in run_script
    assert "/etc/workflow/env" in run_script
    assert "workflow-tunnel" in run_script
    assert "workflow-worker-codex-2" in run_script
    assert "workflow-worker-claude-1" in run_script
    assert "workflow-worker-claude-2" in run_script
    assert "docker rm -f" in run_script
    assert 'rm -f "$unit_file"' in run_script
    assert "systemctl mask workflow-daemon.service" in run_script


def test_production_marker_is_immediately_before_first_scrub_host_write():
    wf = _load()
    scrub_step = _step_named(wf, "Scrub stale cloud env overrides")
    run_script = scrub_step.get("run", "") or ""
    lines = run_script.splitlines()
    first_host_write = next(i for i, line in enumerate(lines) if line.strip().startswith("ssh "))
    marker_line = _previous_executable_line(lines, first_host_write)

    assert scrub_step.get("id"), (
        "the scrub step needs an id so later always-running steps can consume "
        "production_mutation_started even when the SSH write fails"
    )
    assert "production_mutation_started=true" in marker_line
    assert "GITHUB_OUTPUT" in marker_line


def test_image_marker_is_immediately_before_first_tinyassets_image_write():
    wf = _load()
    deploy_step = next(step for step in _steps(wf) if step.get("id") == "deploy")
    run_script = deploy_step.get("run", "") or ""
    lines = run_script.splitlines()
    image_write_line = next(
        i
        for i, line in enumerate(lines)
        if "install-tinyassets-env.sh set TINYASSETS_IMAGE" in line
        and not line.lstrip().startswith("#")
    )

    # Walk to the start of the continued ssh command that invokes the helper.
    command_start = image_write_line
    while command_start > 0 and lines[command_start - 1].rstrip().endswith("\\"):
        command_start -= 1
    marker_line = _previous_executable_line(lines, command_start)

    assert "image_mutation_started=true" in marker_line
    assert "GITHUB_OUTPUT" in marker_line


def test_rollback_and_terminal_receipt_are_ordered_under_always():
    wf = _load()
    steps = _steps(wf)
    canary_step = _step_named(wf, "Post-deploy canary — canonical URL only")
    access_step = _step_named(wf, "Verify CF Access gates direct URL (expects 403/401)")
    rollback_step = _step_named(wf, "Rollback on failure")
    terminal_step = _step_with_run_token(wf, "terminal_receipt_result=")

    assert str(rollback_step.get("if", "")).strip() == "always()"
    assert str(terminal_step.get("if", "")).strip() == "always()"
    assert steps.index(canary_step) < steps.index(rollback_step)
    assert steps.index(access_step) < steps.index(rollback_step)
    assert steps.index(rollback_step) < steps.index(terminal_step), (
        "terminal classification must run after rollback so its receipt "
        "describes the final observed production state"
    )


def test_daemon_deploy_owns_exact_public_server_name_assertion():
    wf = _load()
    canary_step = _step_named(wf, "Post-deploy canary — canonical URL only")
    run_script = canary_step.get("run", "") or ""

    assert "scripts/mcp_public_canary.py" in run_script
    assert "--assert-name TinyAssets" in run_script


def test_terminal_receipt_keys_to_production_marker():
    wf = _load()
    scrub_step = _step_named(wf, "Scrub stale cloud env overrides")
    terminal_step = _step_with_run_token(wf, "terminal_receipt_result=")
    step_env = terminal_step.get("env") or {}
    run_script = terminal_step.get("run", "") or ""

    expected_ref = f"steps.{scrub_step['id']}.outputs.production_mutation_started"
    assert expected_ref in str(step_env.get("PRODUCTION_MUTATION_STARTED", ""))
    assert (
        "steps.stop-writer-cleanup.outputs.cutover_started"
        in str(step_env.get("PRODUCTION_MUTATION_STARTED", ""))
    )
    assert "PRODUCTION_MUTATION_STARTED" in run_script
    assert "not_applicable" in run_script
    assert "failed" in run_script


def test_rollback_emits_safe_defaults_and_final_outputs_before_exit():
    wf = _load()
    rollback_step = _step_named(wf, "Rollback on failure")
    run_script = rollback_step.get("run", "") or ""
    output_keys = (
        "rollback_attempted",
        "rollback_result",
        "rollback_canary_status",
        "rollback_reason",
    )

    fallible_positions = [
        position
        for token in ("scp ", "ssh ", "scripts/mcp_public_canary.py")
        if (position := run_script.find(token)) != -1
    ]
    assert fallible_positions, "rollback must contain the fallible rollback work"
    first_fallible = min(fallible_positions)
    output_helper = "emit_rollback_outputs"

    for key in output_keys:
        first_output = run_script.find(f"{key}=")
        assert first_output != -1, f"rollback must expose {key}"
        assert first_output < first_fallible, (
            f"rollback must emit a safe {key} default before fallible work"
        )

    final_exit = run_script.rfind("exit ")
    assert final_exit != -1, "rollback must return its exact classified exit"
    if output_helper in run_script:
        assert run_script.count(output_helper) >= 3, (
            "the rollback output helper must be defined and called for both "
            "safe defaults and the final tuple"
        )
        helper_definition = run_script.find(output_helper)
        first_helper_call = run_script.find(output_helper, helper_definition + len(output_helper))
        assert first_helper_call < first_fallible
        assert first_fallible < run_script.rfind(output_helper) < final_exit
    else:
        for key in output_keys:
            assert run_script.count(f"{key}=") >= 2, (
                f"rollback must emit both the safe default and final {key} output"
            )
            assert run_script.rfind(f"{key}=") < final_exit, (
                f"rollback final {key} output must be visible before failure"
            )


def test_rollback_identity_failure_preserves_the_passed_canary_tuple(tmp_path):
    """A healthy rollback with the wrong image must never report success.

    The old workflow emitted a separate canary/identity tuple. Its classifier
    matrix remains in test_deploy_terminal_receipt; the active producer is now
    deploy_fail_safe.sh. Execute its rollback branch and real acceptance/output
    functions, faking only host operations. No Docker, host files or network.
    """
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    bash = str(git_bash) if sys.platform == "win32" and git_bash.exists() else shutil.which("bash")
    assert bash is not None and not (sys.platform == "win32" and "system32" in bash.lower()), (
        "this rollback contract requires a POSIX bash executable (Git Bash on Windows)"
    )
    source = (_REPO / "deploy" / "deploy_fail_safe.sh").read_text(encoding="utf-8")
    functions = []
    for name in ("container_state", "health_ok", "tunnel_up",
                 "running_image_matches", "accept", "finish"):
        match = re.search(rf"^{name}\(\) \{{[^\n]*\n.*?^\}}\n", source, re.M | re.S)
        assert match, f"cannot find current rollback collaborator {name}"
        functions.append(match.group(0))
    marker = "# --- 6. unhealthy -> restore the bundle, then roll back the image"
    assert source.count(marker) == 1
    rollback = source[source.index(marker):]
    harness = tmp_path / "rollback.sh"
    harness.write_text(r'''
set -uo pipefail
PREV_IMAGE="ghcr.io/tinyassets/tinyassets-daemon@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
RUNNING_ID="$1"
CALLS="$2"
: > "$CALLS"
DAEMON_CONTAINER=tinyassets-daemon
TUNNEL_CONTAINER=tinyassets-tunnel
LOGS_CONTAINER=tinyassets-logs
INSTALLED_THIS_RUN=0
MARKER_STALE=0
HEALTH_TIMEOUT=10
HEALTH_INTERVAL=0
INSPECT_ERR_TOLERANCE=1
HEALTH_FORMAT='{{if .State.Health}}{{.State.Health.Status}}'
HEALTH_FORMAT+='{{else}}{{.State.Status}}{{end}}'
log() { :; }
err() { printf '%s\n' "$*" >&2; }
layout_marker_path() { echo fixture-layout; }
layout_allows_any_image() { return 0; }
set_image() { printf 'set_image:%s\n' "$1" >> "$CALLS"; }
restart_stack() { echo restart_stack >> "$CALLS"; }
docker() {
  printf 'docker:%s\n' "$*" >> "$CALLS"
  case "$*" in
    "inspect -f ${HEALTH_FORMAT} tinyassets-daemon") echo healthy ;;
    "image inspect -f {{.Id}} ${PREV_IMAGE}") echo sha256:previous ;;
    "inspect -f {{.Image}} tinyassets-daemon") echo "$RUNNING_ID" ;;
    "inspect -f {{.Config.Image}} tinyassets-daemon") echo "$RUNNING_ID" ;;
    "inspect -f {{.State.Status}} tinyassets-tunnel"|\
    "inspect -f {{.State.Status}} tinyassets-logs") echo running ;;
    *) echo "unexpected docker call: $*" >> "$CALLS"; return 97 ;;
  esac
}
''' + "\n".join(functions) + rollback, encoding="utf-8", newline="\n")
    for identity, expected_code, expected_result in (
        ("sha256:previous", 2, "rolled_back"),
        ("sha256:other", 3, "rollback_unhealthy"),
    ):
        calls = tmp_path / "rollback-calls.txt"
        result = subprocess.run(
            [bash, harness.as_posix(), identity, calls.as_posix()],
            capture_output=True, text=True, check=False, timeout=30,
        )
        assert result.returncode == expected_code, result.stderr
        assert f"deploy_result={expected_result}\n" in result.stdout
        # Both scenarios reached real health AND identity checks after converge;
        # a refusal in fixture setup would not exercise the guarantee.
        trace = calls.read_text(encoding="utf-8")
        assert "unexpected docker call" not in trace
        assert trace.index("set_image:") < trace.index("restart_stack\n")
        assert trace.index("restart_stack\n") < trace.index("{{.State.Health.Status}}")
        assert trace.index("{{.State.Health.Status}}") < trace.index("{{.Image}}")
        if identity == "sha256:previous":
            assert "deployed_image=ghcr.io/tinyassets/tinyassets-daemon@sha256:" in result.stdout
            assert "{{.State.Status}} tinyassets-logs" in trace
        else:
            assert "daemon is healthy but NOT running" in result.stderr
            assert "deploy_result=rolled_back" not in result.stdout
            assert "deployed_image=" not in result.stdout


def test_terminal_receipt_invokes_pure_helper_and_preserves_atomic_writer():
    wf = _load()
    terminal_step = _step_with_run_token(wf, "terminal_receipt_result=")
    run_script = terminal_step.get("run", "") or ""

    helper_idx = run_script.find("python scripts/deploy_terminal_receipt.py")
    transfer_idx = run_script.find("scp ")
    install_idx = run_script.find("install -m 0644 -o 1001 -g 1001")
    assert helper_idx != -1, (
        "terminal publication must invoke the directly executable pure classifier/builder"
    )
    assert transfer_idx != -1
    assert install_idx != -1
    assert helper_idx < transfer_idx < install_idx
    assert "release-state.json" in run_script
    assert "/data/release-state.json" in run_script
    assert "release-state.json.next" in run_script
    assert "mv " in run_script, (
        "receipt replacement must rename a validated same-volume sibling "
        "instead of exposing a partially written terminal receipt"
    )
    assert terminal_step.get("continue-on-error") is not True, (
        "terminal writer failure must keep the workflow red"
    )


def test_terminal_receipt_never_mutates_the_deployed_image_after_publication():
    wf = _load()
    terminal_step = _step_with_run_token(wf, "terminal_receipt_result=")
    run_script = terminal_step.get("run", "") or ""

    published_idx = run_script.find("terminal_receipt_result=published")
    assert published_idx != -1
    post_publication = run_script[published_idx:]
    for forbidden in (
        "TINYASSETS_IMAGE",
        "docker pull",
        "systemctl restart tinyassets-daemon",
        "${PREV_IMAGE}",
    ):
        assert forbidden not in post_publication, (
            "the installed terminal receipt must describe the final production "
            f"state; found a later image mutation token: {forbidden}"
        )


def test_terminal_receipt_summary_python_is_executable(tmp_path):
    wf = _load()
    terminal_step = _step_with_run_token(wf, "terminal_receipt_result=")
    run_script = terminal_step.get("run", "") or ""
    match = re.search(
        r"python -c '([^']+)' \"\$RUNNER_TEMP/tinyassets-release-state\.json\"",
        run_script,
    )
    assert match is not None, "terminal receipt outcome summary command is missing"

    receipt_path = tmp_path / "release-state.json"
    receipt_path.write_text(json.dumps({"outcome": "deployed"}), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", match.group(1), str(receipt_path)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "deployed"


def test_terminal_receipt_does_not_assign_manual_image_source_from_github_sha():
    text = _text()
    assert "github.event.workflow_run.head_sha || github.sha" not in text
    # The deploy identifies an image by its OCI revision label, never by the
    # run's own sha. The label is read in the script the workflow ships to the
    # host (deploy/snapshot_candidate_evidence.sh) rather than inline, so the
    # assertion covers the deploy CHAIN -- grepping only the workflow text would
    # pass or fail on where the string happens to live.
    chain = text + Path("deploy/snapshot_candidate_evidence.sh").read_text(
        encoding="utf-8"
    )
    assert "org.opencontainers.image.revision" in chain


def test_terminal_writer_outputs_are_visible_before_fallible_work():
    wf = _load()
    terminal_step = _step_with_run_token(wf, "terminal_receipt_result=")
    run_script = terminal_step.get("run", "") or ""
    first_fallible = min(
        position
        for token in ("ssh ", "scp ", "python scripts/deploy_terminal_receipt.py")
        if (position := run_script.find(token)) != -1
    )

    failed_idx = run_script.find("terminal_receipt_result=failed")
    not_applicable_idx = run_script.find("terminal_receipt_result=not_applicable")
    published_idx = run_script.find("terminal_receipt_result=published")
    install_idx = run_script.find("install -m 0644 -o 1001 -g 1001")
    assert 0 <= failed_idx < first_fallible, (
        "writer failure must leave a visible failed output before host "
        "observation, classification, transfer, or install can fail"
    )
    assert 0 <= not_applicable_idx < first_fallible, (
        "the pre-host path must publish not_applicable without host contact"
    )
    assert install_idx != -1
    assert published_idx > install_idx, (
        "published is truthful only after the atomic receipt install succeeds"
    )
    for output_name in (
        "terminal_outcome",
        "terminal_active_identity_status",
        "terminal_canary_status",
    ):
        output_idx = run_script.find(f"{output_name}=")
        assert 0 <= output_idx < install_idx, (
            f"{output_name} must be exposed before atomic install so issue "
            "wording survives writer failure"
        )


def test_terminal_canary_output_preserves_the_raw_applicable_canary():
    wf = _load()
    terminal_step = _step_with_run_token(wf, "terminal_receipt_result=")
    run_script = terminal_step.get("run", "") or ""

    assert "terminal_canary_status={receipt['canary_bundle_status']}" not in run_script
    assert 'receipt["forward_canary_status"]' in run_script
    assert 'receipt["rollback_canary_status"]' in run_script
    assert 'receipt["rollback_attempted"]' in run_script


def test_forward_green_terminal_identity_failure_stays_red_after_publication():
    wf = _load()
    terminal_step = _step_with_run_token(wf, "terminal_receipt_result=")
    run_script = terminal_step.get("run", "") or ""

    published_idx = run_script.find("terminal_receipt_result=published")
    proof_gate_idx = run_script.find('if [ "${FORWARD_SUCCEEDED}" = "true" ]')
    proof_error_idx = run_script.find("forward terminal state is unproven")
    assert 0 <= published_idx < proof_gate_idx < proof_error_idx, (
        "terminal evidence must be installed before the forward identity gate returns nonzero"
    )
    proof_tail = run_script[proof_gate_idx:]
    assert "exit 1" in proof_tail
    for required in ("deployed", "active_identity_status", "agreed", "passed"):
        assert required in proof_tail


def test_deploy_failure_issue_consumes_rollback_and_terminal_outputs():
    wf = _load()
    rollback_step = _step_named(wf, "Rollback on failure")
    terminal_step = _step_with_run_token(wf, "terminal_receipt_result=")
    issue_step = _step_named(wf, "Open deploy-failed issue")
    assert rollback_step.get("id"), "rollback outputs require a stable step id"
    assert terminal_step.get("id"), "terminal outputs require a stable step id"

    cond = str(issue_step.get("if", ""))
    env_text = "\n".join(str(value) for value in (issue_step.get("env") or {}).values())
    assert "always()" in cond and "failure()" in cond, (
        "the issue must still run after a red rollback or terminal writer"
    )
    for output_name in (
        "rollback_attempted",
        "rollback_result",
        "rollback_canary_status",
        "rollback_reason",
    ):
        assert f"steps.{rollback_step['id']}.outputs.{output_name}" in env_text, (
            f"deploy-failed issue must consume {output_name}"
        )
    for output_name in (
        "terminal_receipt_result",
        "terminal_outcome",
        "terminal_active_identity_status",
        "terminal_canary_status",
        "terminal_configured_image_ref",
        "terminal_running_image_ref",
        "terminal_active_image_ref",
    ):
        assert f"steps.{terminal_step['id']}.outputs.{output_name}" in env_text, (
            f"deploy-failed issue must consume {output_name}"
        )


def test_deploy_failure_issue_rejects_partial_or_contradictory_tuples():
    wf = _load()
    issue_step = _step_named(wf, "Open deploy-failed issue")
    script = str((issue_step.get("with") or {}).get("script", ""))

    for required in (
        "productionNotStarted",
        "imageNotStarted",
        "rollbackNotAttempted",
        'rollbackResult === "not_attempted"',
        'rollbackResult === "succeeded"',
        'rollbackResult === "failed"',
        'rollbackCanary === "not_run"',
        'rollbackCanary === "passed"',
        'rollbackCanary === "failed"',
        'rollbackReason === "attempted"',
        "canonicalRepoDigest.test(previousImage)",
    ):
        assert required in script
    assert 'rollbackAttempted && rollbackCanary === "failed"' not in script
    assert 'rollbackAttempted && rollbackCanary === "not_run"' not in script


def test_deploy_failure_issue_has_truthful_bounded_wording():
    wf = _load()
    issue_step = _step_named(wf, "Open deploy-failed issue")
    script = str((issue_step.get("with") or {}).get("script", ""))

    assert "Rolled back to:" not in script, (
        "a previous-image value is not proof that rollback succeeded"
    )
    for required_sentence in (
        "Production host write did not start; image rollback was not attempted.",
        (
            "Production mutation started, but image mutation did not; "
            "image rollback was not required."
        ),
        (
            "Rollback was not needed because terminal outcome is deployed, "
            "active image identity agrees, and the applicable canary passed."
        ),
        "Rollback was not attempted; forward production health is unproven.",
        "Rollback succeeded and the rollback canary passed.",
        ("Rollback status is unavailable; rollback success was not proven."),
        "Terminal release-state receipt published.",
        (
            "Terminal release-state publication failed; durable active-release "
            "truth is not proven and the prior receipt may be stale."
        ),
        (
            "Terminal release-state publication was not applicable; the prior "
            "receipt was left unchanged."
        ),
    ):
        assert required_sentence in script


# ---------------------------------------------------------------------------
# Data volume root: ownership repair, and no platform login dirs (Hard Rule 15)
# ---------------------------------------------------------------------------


def _volume_root_step(wf: dict) -> dict:
    step = next(
        (s for s in _steps(wf) if s.get("name") == "Prepare data volume root"),
        None,
    )
    assert step is not None, (
        "deploy must include a 'Prepare data volume root' step that repairs "
        "the tinyassets-data root on every deploy (Forever Rule: no host action)"
    )
    return step


def test_volume_root_step_runs_before_deploy():
    wf = _load()
    steps = _steps(wf)
    names = [s.get("name", "") for s in steps]
    volume_idx = names.index("Prepare data volume root")
    deploy_idx = next(i for i, step in enumerate(steps) if step.get("id") == "deploy")
    assert volume_idx < deploy_idx, (
        "the volume root (OAuth db) must be writable by uid 1001 before the "
        "daemon container restarts"
    )


def test_volume_root_step_creates_volume_idempotently():
    run_script = _volume_root_step(_load()).get("run", "") or ""
    assert 'docker volume create "$VOLUME_NAME"' in run_script
    assert 'docker volume inspect "$VOLUME_NAME"' in run_script


def test_volume_root_step_repairs_volume_root_for_auth_db():
    run_script = _volume_root_step(_load()).get("run", "") or ""
    assert 'chown "$TINYASSETS_UID:$TINYASSETS_GID" "$VOLUME_DIR"' in run_script
    assert 'chmod 755 "$VOLUME_DIR"' in run_script
    assert ".auth.db" in run_script
    assert "unable to open database file" in run_script


def test_volume_root_step_creates_no_platform_login_dir():
    """Until 2026-09-24 this step created /data/.codex and /data/.claude as the
    platform's own Codex and Claude login homes. The platform has no LLM (Hard
    Rule 15), so it creates neither, and it reaches into no fleet container."""
    run_script = _volume_root_step(_load()).get("run", "") or ""
    for retired in ("CODEX_DIR", "CLAUDE_DIR", "/.codex", "/.claude", "tinyassets-worker"):
        assert retired not in run_script, f"volume step still handles {retired!r}"


def test_retire_step_runs_the_host_cleanup_only_after_a_green_canary():
    wf = _load()
    steps = _steps(wf)
    idx = {s.get("name"): i for i, s in enumerate(steps)}
    retire = steps[idx["Retire platform LLM logins from the host"]]
    assert "deploy/retire_platform_llm_logins.sh" in retire.get("run", "")
    assert "steps.canary.outcome == 'success'" in str(retire.get("if", ""))
    canary_idx = next(i for i, s in enumerate(steps) if s.get("id") == "canary")
    assert idx["Retire platform LLM logins from the host"] > canary_idx
    assert idx["Retire platform LLM logins from the host"] < idx["Open deploy-failed issue"], (
        "a failed cleanup must still reach the deploy-failed issue step"
    )


# ---------------------------------------------------------------------------
# No platform GitHub push credential (retired 2026-09-24)
# ---------------------------------------------------------------------------
#
# PR-128 synced a GitHub push-capability map (from the WORKFLOW_GITHUB_PR_
# CAPABILITIES repository secret) into /etc/tinyassets/env on every deploy,
# and a GitHub App refresher re-minted it. Pushing with a platform token must
# not be possible: GitHub is a connection a universe's owner may or may not
# have made. The tests below assert the ABSENCE of that path; the host copies
# are removed by deploy/retire_platform_llm_logins.sh after a green canary.


_RETIRED_GITHUB_PUSH_NAMES = (
    "HAS_GITHUB_PR_CAPABILITY",
    "GITHUB_PR_CAPABILITIES_SOURCE",
    "WORKFLOW_GITHUB_PR_CAPABILITIES",
    "TINYASSETS_GITHUB_PR_CAPABILITIES",
    "TINYASSETS_GITHUB_PUSH_CAPABILITIES",
)


def test_deploy_job_env_has_no_github_push_capability():
    wf = _load()
    job_env = (wf.get("jobs", {}).get("deploy", {}) or {}).get("env") or {}
    leaked = [name for name in _RETIRED_GITHUB_PUSH_NAMES if name in job_env]
    assert not leaked, f"deploy job env still carries {leaked}"


def test_deploy_never_installs_a_github_push_capability():
    text = _text()
    for name in _RETIRED_GITHUB_PUSH_NAMES:
        assert name not in text, f"deploy-prod.yml still handles {name}"
    assert "install-tinyassets-env.sh set TINYASSETS_GITHUB" not in text


@pytest.mark.parametrize(
    "value",
    [
        "",
        base64.b64encode(b"x" * 31).decode("ascii"),
        base64.b64encode(b"x" * 32).decode("ascii") + "\nINJECTED_SETTING=1",
        "not-base64!",
        base64.b64encode(b"x" * 32).decode("ascii").rstrip("="),
    ],
)
def test_agent_interchange_hmac_validator_rejects_unsafe_values(value: str):
    from scripts.validate_agent_interchange_hmac import validate_secret

    with pytest.raises(ValueError):
        validate_secret(value)


def test_agent_interchange_hmac_validator_accepts_32_random_bytes():
    from scripts.validate_agent_interchange_hmac import validate_secret

    raw = bytes(range(32))
    encoded = base64.b64encode(raw).decode("ascii")
    assert validate_secret(encoded) == raw


def test_agent_interchange_hmac_validator_never_echoes_rejected_secret():
    secret = base64.b64encode(b"x" * 32).decode("ascii") + "\nINJECTED_SETTING=1"
    env = {**os.environ, "TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY": secret}
    result = subprocess.run(
        [sys.executable, "scripts/validate_agent_interchange_hmac.py"],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert secret not in result.stdout
    assert "INJECTED_SETTING" not in result.stdout
    assert secret not in result.stderr


@pytest.mark.parametrize(
    "value",
    [
        "",
        base64.b64encode(b"x" * 31).decode("ascii"),
        base64.b64encode(b"x" * 32).decode("ascii") + "\nINJECTED_SETTING=1",
        "not-base64!",
        base64.b64encode(b"x" * 32).decode("ascii").rstrip("="),
    ],
)
def test_request_idempotency_hmac_validator_rejects_unsafe_values(value: str):
    from scripts.validate_request_idempotency_hmac import validate_secret

    with pytest.raises(ValueError):
        validate_secret(value)


def test_request_idempotency_hmac_validator_accepts_and_never_echoes_secret():
    from scripts.validate_request_idempotency_hmac import validate_secret

    raw = bytes(range(48))
    encoded = base64.b64encode(raw).decode("ascii")
    assert validate_secret(encoded) == raw

    rejected = encoded + "\nINJECTED_SETTING=1"
    env = {**os.environ, "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY": rejected}
    result = subprocess.run(
        [sys.executable, "scripts/validate_request_idempotency_hmac.py"],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert rejected not in result.stdout
    assert "INJECTED_SETTING" not in result.stdout
    assert rejected not in result.stderr


def test_request_idempotency_hmac_validator_rejects_agent_key_reuse():
    encoded = base64.b64encode(bytes(range(48))).decode("ascii")
    env = {
        **os.environ,
        "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY": encoded,
        "TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY": encoded,
    }
    result = subprocess.run(
        [sys.executable, "scripts/validate_request_idempotency_hmac.py"],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert encoded not in result.stdout
    assert encoded not in result.stderr
    assert "must differ" in result.stdout


def test_unsafe_recovery_validates_both_hmac_prerequisites_before_mutation():
    wf = _load()
    steps = wf["jobs"]["recover-unsafe"]["steps"]
    indexes = {step.get("name"): index for index, step in enumerate(steps)}
    request = steps[indexes["Validate recovery request idempotency HMAC"]]
    agent = steps[indexes["Validate recovery agent interchange HMAC"]]
    assert request.get("run") == (
        "python scripts/validate_request_idempotency_hmac.py"
    )
    assert agent.get("run") == "python scripts/validate_agent_interchange_hmac.py"
    assert indexes["Validate recovery request idempotency HMAC"] < indexes[
        "Pull recovery image on production host"
    ]
    assert indexes["Validate recovery agent interchange HMAC"] < indexes[
        "Pull recovery image on production host"
    ]
    host = steps[indexes["Validate host HMAC pair before recovery mutation"]]
    host_script = host.get("run", "") or ""
    assert "scripts/validate_host_runtime_hmac_pair.py" in host_script
    assert '"sudo python3 -"' in host_script
    assert indexes["Install recovery SSH key"] < indexes[
        "Validate host HMAC pair before recovery mutation"
    ]
    assert indexes["Validate host HMAC pair before recovery mutation"] < indexes[
        "Pull recovery image on production host"
    ]


# ---------------------------------------------------------------------------
# retire-cheat-loop task 2.1 — transitional production stop-writer fence
# ---------------------------------------------------------------------------


def _stop_writer_step(wf: dict, name: str) -> dict:
    step = _step_named(wf, name)
    assert "retire-cheat-loop task 2.1" in str(step.get("run", "")), (
        f"{name!r} must be explicitly transitional so task 2.5 can remove "
        "the product-specific receipt/queue guard"
    )
    return step


def test_stop_writer_preflight_runs_before_image_mutation():
    wf = _load()
    steps = _steps(wf)
    preflight = _stop_writer_step(wf, "Transitional task 2.1 stop-writer preflight")
    deploy = next(step for step in steps if step.get("id") == "deploy")
    production_mutation = next(
        step for step in steps if step.get("id") == "production_mutation"
    )
    disk = _step_named(wf, "Preflight droplet disk before image pull")

    assert (
        steps.index(disk)
        < steps.index(preflight)
        < steps.index(production_mutation)
        < steps.index(deploy)
    )
    assert str(preflight.get("id")) == "stop-writer"
    assert str(preflight.get("env", {}).get("NEW_IMAGE", "")).endswith(
        "steps.tag.outputs.image_ref }}"
    )


def test_deploy_shares_production_host_mutation_concurrency_group():
    wf = _load()
    assert wf.get("concurrency") == {
        "group": "production-host-mutation",
        "cancel-in-progress": False,
    }


def test_stop_writer_ancestry_gate_has_complete_git_history():
    wf = _load()
    checkout = next(
        step
        for step in _steps(wf)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout.get("with", {}).get("fetch-depth") == 0


def test_disk_preflight_precedes_every_remote_image_pull():
    wf = _load()
    steps = _steps(wf)
    disk_index = steps.index(_step_named(wf, "Preflight droplet disk before image pull"))
    pull_indexes = []
    for index, step in enumerate(steps):
        for line in str(step.get("run", "")).splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "docker pull" in stripped:
                pull_indexes.append(index)
    assert pull_indexes
    assert all(disk_index < index for index in pull_indexes)


def test_stop_writer_workflow_invokes_transitional_helper_subcommands():
    text = _text()
    assert "scripts/retire_cheat_loop_deploy_fence.py" in text
    for command in (
        " preflight --image-ref ",
        " prepare-deploy --image-ref ",
        " prove --image-ref ",
        " post-canary --image-ref ",
        " status",
        " observe",
        " quiesce-unsafe",
        " restore-if-safe --image-ref ",
    ):
        assert command in text


def test_stop_writer_deploy_proves_exact_safe_image_and_drains_old_ids():
    wf = _load()
    preflight = _stop_writer_step(
        wf, "Transitional task 2.1 stop-writer preflight"
    ).get("run", "")
    proof = _stop_writer_step(
        wf, "Transitional task 2.1 prove exact fleet and unchanged receipts"
    ).get("run", "")

    assert "35da9d4fc1a1fc51d3db56bf5d1627691f54d894" in preflight
    assert "org.opencontainers.image.revision" in preflight
    assert "git merge-base --is-ancestor" in preflight
    assert "systemd-run --quiet --collect --wait --pipe" in preflight
    assert "--property RuntimeMaxSec=300" in preflight
    assert "--property TimeoutStartSec=300" in preflight
    assert "--run-id '${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}'" in preflight
    assert "prove --image-ref" in proof
    assert "receipt_snapshot_post_deploy.json" in proof


def test_stop_writer_blocks_unsafe_rollback_image():
    wf = _load()
    preflight = _stop_writer_step(
        wf, "Transitional task 2.1 stop-writer preflight"
    )
    rollback = _step_named(wf, "Rollback on failure")

    assert "safe_previous_image" in str(preflight.get("run", ""))
    assert (
        rollback.get("env", {}).get("PREV_IMAGE")
        == "${{ steps.stop-writer.outputs.safe_previous_image }}"
    )
    assert "steps.prev.outputs.previous" not in str(
        rollback.get("env", {}).get("PREV_IMAGE", "")
    )


def test_stop_writer_compares_post_deploy_and_post_canary_snapshots():
    wf = _load()
    steps = _steps(wf)
    deploy_proof = _stop_writer_step(
        wf, "Transitional task 2.1 prove exact fleet and unchanged receipts"
    )
    canary = _step_named(wf, "Post-deploy canary — canonical URL only")
    post_canary = _stop_writer_step(
        wf, "Transitional task 2.1 post-canary receipt proof"
    )
    forward = _step_named(wf, "Mark forward path complete")
    rollback = _step_named(wf, "Rollback on failure")

    assert (
        steps.index(deploy_proof)
        < steps.index(canary)
        < steps.index(post_canary)
        < steps.index(forward)
        < steps.index(rollback)
    )
    preflight = _stop_writer_step(
        wf, "Transitional task 2.1 stop-writer preflight"
    )
    assert "receipt_snapshot_before.json" in str(preflight.get("run", ""))
    assert "receipt_snapshot_post_deploy.json" in str(deploy_proof.get("run", ""))
    assert "receipt_snapshot_post_canary.json" in str(post_canary.get("run", ""))
    assert "post-canary --image-ref" in str(post_canary.get("run", ""))


def test_stop_writer_restores_timers_only_for_safe_fleet_and_uploads_evidence():
    wf = _load()
    restore = _stop_writer_step(
        wf, "Transitional task 2.1 restore restart racers when safe"
    )
    artifact = _step_named(wf, "Upload transitional task 2.1 deploy proof")

    assert str(restore.get("if", "")).strip() == "always()"
    restore_script = str(restore.get("run", ""))
    assert "retire-cheat-loop-deploy-fence.py status" in restore_script
    assert "retire-cheat-loop-deploy-fence.py observe" in restore_script
    assert "retire-cheat-loop-deploy-fence.py quiesce-unsafe" in restore_script
    assert "cleanup_mutation_started=true" in restore_script
    assert "cleanup_safely_fenced=false" in restore_script
    assert "cleanup_safely_fenced=true" in restore_script
    assert restore_script.index("cleanup_safely_fenced=true") > restore_script.index(
        'if [ "$fence_status" -ne 0 ]'
    )
    assert restore_script.index("cleanup_mutation_started=true") < restore_script.index(
        "retire-cheat-loop-deploy-fence.py quiesce-unsafe"
    )
    assert "git merge-base --is-ancestor" in restore_script
    assert "cleanup_restored=true" in restore_script
    assert "masked_units_after" in restore_script
    assert "tinyassets-daemon.service" in restore_script
    assert restore_script.index("git merge-base --is-ancestor") < restore_script.index(
        "restore-if-safe --image-ref"
    )
    assert str(artifact.get("if", "")).strip() == "always()"
    assert (artifact.get("uses") or "").startswith("actions/upload-artifact@")
    assert "retire-cheat-loop-task-2-1" in str(artifact.get("with", {}).get("name", ""))
    assert "stop-writer-evidence" in str(artifact.get("with", {}).get("path", ""))


def test_terminal_never_reports_deployed_without_exact_cleanup_restoration():
    wf = _load()
    terminal = _step_with_run_token(wf, "terminal_receipt_result=")
    assert (
        terminal.get("env", {}).get("STOP_WRITER_CLEANUP_RESTORED")
        == "${{ steps.stop-writer-cleanup.outputs.cleanup_restored }}"
    )
    assert (
        terminal.get("env", {}).get("STOP_WRITER_CLEANUP_MUTATION_STARTED")
        == "${{ steps.stop-writer-cleanup.outputs.cleanup_mutation_started }}"
    )
    assert (
        terminal.get("env", {}).get("STOP_WRITER_CLEANUP_SAFELY_FENCED")
        == "${{ steps.stop-writer-cleanup.outputs.cleanup_safely_fenced }}"
    )
    assert "steps.stop-writer-cleanup.outputs.cleanup_mutation_started" in str(
        terminal.get("env", {}).get("PRODUCTION_MUTATION_STARTED", "")
    )
    script = str(terminal.get("run", ""))
    assert 'if [ "${STOP_WRITER_CLEANUP_RESTORED}" != "true" ]' in script
    assert "export FORWARD_SUCCEEDED=false" not in script
    assert "export FORWARD_CANARY_OUTCOME=failure" not in script
    assert '"cleanup_restored": marker(' in script
    assert '"cleanup_safely_fenced": marker(' in script
    assert '"cleanup_mutation_started": marker(' in script
    assert "{{json .State.Running}}" in script
    cleanup_script = str(
        _step_named(
            wf,
            "Transitional task 2.1 restore restart racers when safe",
        ).get("run", "")
    )
    assert "expected_restored_unit_states" in cleanup_script
    assert "restored != expected" in cleanup_script
    assert 'daemon.get("enabled") != "enabled"' not in cleanup_script


def test_cleanup_derives_cutover_only_from_current_run_generation():
    wf = _load()
    cleanup = _step_named(
        wf,
        "Transitional task 2.1 restore restart racers when safe",
    )
    script = str(cleanup.get("run", ""))
    assert "current_run_cutover_started" in script
    assert "str(bool(status.get(\"state_exists\")))" not in script
    assert "status --run-id '${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}'" in script


def test_compose_declares_no_host_run_worker_fleet():
    """The four `worker*` services must not come back.

    This replaced an accept-direction control asserting "the shared fleet must
    stay shared". Inverted 2026-08-29: nothing runs unless it lives inside a
    user's universe under that user's control, and the platform never runs an
    actor of its own (PLAN.md). The surviving services keep the no-universe-pin
    assertion the old control carried.
    """
    compose = yaml.safe_load(Path("deploy/compose.yml").read_text(encoding="utf-8"))
    for name in ("worker", "worker-codex-2", "worker-claude-1", "worker-claude-2"):
        assert name not in compose["services"], (
            f"host-run fleet service {name} is back"
        )
    for name, service in compose["services"].items():
        env = service.get("environment") or {}
        assert "TINYASSETS_UNIVERSE" not in env, name


def test_recovery_canary_waits_for_the_daemon_instead_of_probing_instantly():
    """Recovery must not fail its own success check on a booting daemon.

    The ordinary deploy path has a "Wait for daemon health" step before its
    canary; the recovery path had none and probed the PUBLIC url immediately
    after starting the fleet. The daemon's healthcheck allows a 60s
    start_period, so live recovery 31048315265 probed ~0.7s after container
    start, got 502 from a daemon that had not finished booting, and that
    failure re-fenced the fleet — leaving /mcp down while the daemon itself
    was healthy.
    """
    wf = _load()
    step = next(
        s for s in wf["jobs"]["recover-unsafe"]["steps"]
        if s.get("name") == "Recovery daemon MCP canary (loopback)"
    )
    run = step.get("run", "") or ""
    # Recovery proves the DAEMON serves MCP; the public route needs the tunnel
    # sidecar, which recovery does not restore and the later deploy does.
    assert "127.0.0.1:8001/mcp" not in run  # it is base64'd, not inline
    assert "recovered daemon never served /mcp on loopback" in run
    assert "exit 1" in run
    # The public assertion must still exist elsewhere in the recovery job.
    wf_all = _WORKFLOW.read_text(encoding="utf-8")
    assert 'python scripts/mcp_public_canary.py --url "${CANARY_URL}" --assert-handles' in wf_all


def test_active_universe_repoint_is_explicit_input_only_and_validated():
    """The marker that decides which universe the fleet serves.

    The daemon resolves /data/.active_universe before any
    other default, and an AUTHENTICATED `switch_universe` is request-scoped by
    design and never writes it — so there is no in-band way for a user to
    repoint the fleet at their own universe. Four admissible slices sat
    unclaimed for >18h because of exactly this.

    It must never move by default, and never to an unvalidated value.
    """
    wf = _load()
    inputs = wf[True]["workflow_dispatch"]["inputs"] if True in wf else wf["on"]["workflow_dispatch"]["inputs"]
    assert "set_active_universe" in inputs
    assert not inputs["set_active_universe"].get("required", False)
    assert "default" not in inputs["set_active_universe"]

    step = next(
        s for s in wf["jobs"]["deploy"]["steps"]
        if s.get("name", "").startswith("Repoint the active-universe marker")
    )
    # Only ever on an explicit non-empty input.
    assert "inputs.set_active_universe != ''" in str(step.get("if", ""))
    run = step.get("run", "") or ""
    assert "invalid universe id" in run
    assert "^[A-Za-z0-9._-]{1,128}$" in run


def test_level2_quiesced_restore_is_gated_and_ordered_before_legacy_cleanup():
    """Level 2 auto-rollback: reverse a proved-but-uncommitted quiesce.

    The 2026-08-07 zero-container outages happened when the stop-writer fence
    quiesced (stopped + runtime-masked the daemon and racers) and the deploy
    then FAILED before the new image committed. The legacy "restore restart
    racers when safe" cleanup could not help because its restore-if-safe path
    needs an already-RUNNING exact-five fleet to observe — but the fleet was
    down and masked. The Level 2 block runs ONLY in that precise
    image-not-committed case and lets the fence reverse its own recorded
    quiesce via `restore-quiesced` (compose-up on the unchanged image).
    """
    wf = _load()
    cleanup = _stop_writer_step(
        wf, "Transitional task 2.1 restore restart racers when safe"
    )
    # (c) Only acts in the image-not-committed safe case: the block is gated on
    # the deploy step's image_mutation_started output being not-"true".
    assert (
        cleanup.get("env", {}).get("IMAGE_MUTATION_STARTED")
        == "${{ steps.deploy.outputs.image_mutation_started }}"
    )
    script = str(cleanup.get("run", ""))
    assert 'if [ "${IMAGE_MUTATION_STARTED}" != "true" ]; then' in script
    assert "restore-quiesced --image-ref" in script
    # Recovery is compose-up on the unchanged image, NEVER `docker start <id>`.
    assert "docker start" not in script

    # Transition order inside the cleanup step: prove the old fleet's ancestry
    # is descended from the stop-writer floor BEFORE invoking restore-quiesced,
    # and only declare cleanup_restored=true AFTER restore-quiesced returns.
    assert script.index('if [ "${IMAGE_MUTATION_STARTED}" != "true" ]') < script.index(
        "restore-quiesced --image-ref"
    )
    assert script.index("git merge-base --is-ancestor") < script.index(
        "restore-quiesced --image-ref"
    )
    assert script.index("restore-quiesced --image-ref") < script.index(
        "cleanup_restored=true"
    )

    # (b) Fails LOUD to manual recovery, never a silent restored: an eligible
    # durable identity that cannot prove restoration is a hard fence failure,
    # not a fall-through. A non-eligible/not-applicable result (status 3) is the
    # ONLY path that falls back to today's observe/restore-or-fence behavior.
    assert "fence_unsafe_and_fail" in script
    assert 'quiesced_identity_status" -ne 3' in script
    assert 'quiesced_proof_status" -ne 3' in script


def test_level2_quiesced_restore_keeps_rollback_and_receipt_tuple_valid():
    """(d) The Level 2 path must not disturb the release-state receipt tuple.

    restore-quiesced runs in the always() stop-writer-cleanup step and feeds
    the same cleanup_restored / cleanup_mutation_started outputs the terminal
    receipt already consumes. The separate rollback step's
    image_mutation_not_started tuple must remain intact.
    """
    text = _text()
    rollback_slice = text[
        text.index("id: rollback") : text.index("id: stop-writer-cleanup")
    ]
    for token in (
        "rollback_attempted=false",
        "rollback_result=not_attempted",
        "rollback_canary_status=not_run",
        "rollback_reason=image_mutation_not_started",
    ):
        assert token in rollback_slice, token

    # The successful Level 2 recovery marks a host mutation and a full restore,
    # exactly the two outputs the terminal receipt reads.
    cleanup_slice = text[
        text.index("id: stop-writer-cleanup") : text.index(
            "- name: Publish release-state receipt"
        )
    ]
    assert "cleanup_mutation_started=true" in cleanup_slice
    assert "cleanup_restored=true" in cleanup_slice
    # The evidence the workflow validates must assert the exact restored shape.
    assert '"quiesced_before_image_commit"' in cleanup_slice
    assert 'evidence.get("phase") == "not_applicable"' in cleanup_slice
    assert 'evidence.get("masked_units_after") == []' in cleanup_slice

    # The terminal receipt still consumes the cleanup outputs unchanged.
    terminal = _step_with_run_token(wf=_load(), token="terminal_receipt_result=")
    assert (
        terminal.get("env", {}).get("STOP_WRITER_CLEANUP_RESTORED")
        == "${{ steps.stop-writer-cleanup.outputs.cleanup_restored }}"
    )


def test_level2_quiesced_restore_is_bounded_and_isolated_to_current_run():
    """The recovery ssh must be time-bounded and scoped to the current run."""
    cleanup = _stop_writer_step(
        _load(), "Transitional task 2.1 restore restart racers when safe"
    )
    script = str(cleanup.get("run", ""))
    # systemd-run bounds the recovery so a hung restore cannot wedge the runner.
    assert "systemd-run --quiet --collect --wait --pipe" in script
    assert "--property RuntimeMaxSec=720" in script
    assert "--property TimeoutStartSec=720" in script
    # Identity is read from the CURRENT-run durable fence only.
    assert "current_run_matches" in script
    assert "current_run_previous_image_ref" in script
    assert "current_run_previous_revision" in script
    assert "--run-id '${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}'" in script
