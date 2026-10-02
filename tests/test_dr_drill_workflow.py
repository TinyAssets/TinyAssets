"""Tests for .github/workflows/dr-drill.yml structure.

Covers:
  (a) YAML parses without error
  (b) Only workflow_dispatch trigger (never auto-runs)
  (c) Required inputs present (drill_droplet_size, backup_source,
      destroy_on_failure, cleanup_droplet_id)
  (d) Required secrets referenced (DIGITALOCEAN_TOKEN, DO_SSH_KEY, DO_DROPLET_HOST, DO_SSH_USER)
  (e) Droplet provision step creates Droplet via DO API
  (f) Bootstrap step runs hetzner-bootstrap.sh
  (g) Restore step runs backup-restore.sh
  (h) Probe uses direct Droplet IP:8001 (not tunnel URL)
  (i) Pass path destroys Droplet + appends to log
  (j) Fail path opens dr-failed issue + leaves Droplet up
  (k) Runbook and log files exist
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO / ".github" / "workflows" / "dr-drill.yml"
_RUNBOOK = _REPO / "docs" / "ops" / "dr-drill-runbook.md"
_LOG = _REPO / "docs" / "ops" / "dr-drill-log.md"

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
    return wf.get("jobs", {}).get("drill", {}).get("steps", [])


def _step_names(wf: dict) -> list[str]:
    return [(s.get("name") or "").lower() for s in _steps(wf)]


def _step(name: str) -> dict:
    for step in _steps(_load()):
        if step.get("name") == name:
            return step
    raise AssertionError(f"{name} step missing")


def _dispatch_inputs(wf: dict) -> dict:
    return _triggers(wf).get("workflow_dispatch", {}).get("inputs", {})


def _workflow_input_default(input_name: str) -> str:
    inputs = _dispatch_inputs(_load())
    assert input_name in inputs, f"{input_name} input missing"
    return str(inputs[input_name].get("default", ""))


def _runbook_input_default(input_name: str) -> str:
    text = _RUNBOOK.read_text(encoding="utf-8")
    pattern = rf"\| `{re.escape(input_name)}` \| `([^`]+)` \|"
    match = re.search(pattern, text)
    assert match, f"{input_name} default missing from DR runbook inputs table"
    return match.group(1)


def _bootstrap_step_run() -> str:
    for step in _steps(_load()):
        if step.get("name") == "Bootstrap drill Droplet":
            return step.get("run", "")
    raise AssertionError("Bootstrap drill Droplet step missing")


def _append_log_step_run() -> str:
    for step in _steps(_load()):
        if step.get("name") == "Append drill result to log":
            return step.get("run", "")
    raise AssertionError("Append drill result to log step missing")


# ---------------------------------------------------------------------------
# (a) YAML parses
# ---------------------------------------------------------------------------

def test_dr_drill_yml_parses():
    _load()


# ---------------------------------------------------------------------------
# (b) Triggers: weekly schedule + manual dispatch (target-architecture S1a.5)
# ---------------------------------------------------------------------------

def test_drill_runs_weekly_and_on_dispatch_only():
    """Inverted 2026-10-02. The drill used to be dispatch-only and last ran
    2026-07-24 (docs/concerns/2026-10-02-dr-drill-is-dispatch-only.md); the
    uptime-and-alarms spec requires a weekly scheduled drill."""
    wf = _load()
    triggers = _triggers(wf)
    assert "workflow_dispatch" in triggers, "must have workflow_dispatch trigger"
    crons = [entry["cron"] for entry in triggers.get("schedule") or []]
    assert len(crons) == 1, "exactly one schedule"
    minute, hour, dom, month, dow = crons[0].split()
    assert dom == "*" and month == "*" and dow.isdigit(), "weekly: one fixed weekday"
    assert "push" not in triggers, "dr-drill must not run on push"
    assert "pull_request" not in triggers, "dr-drill must not run on PR"


def test_a_scheduled_run_is_the_off_region_drill():
    """A schedule carries no inputs; the origin must then default to offregion,
    never to the primary's own tarball."""
    wf = _load()
    assert wf["env"]["BACKUP_ORIGIN"] == "${{ inputs.backup_origin || 'offregion' }}"
    origin = _dispatch_inputs(wf)["backup_origin"]
    assert origin["default"] == "offregion"
    assert origin["options"] == ["offregion", "primary"]


def test_the_off_region_origin_never_touches_the_primary():
    """Every primary contact is behind the `primary` origin, so the drill would
    still run with the primary host gone."""
    for name in ("Install SSH key", "Validate selected backup",
                 "Resolve immutable production daemon image",
                 "Transfer verified backup to drill Droplet"):
        run = _step(name)["run"]
        if "DO_DROPLET_HOST" not in run:
            continue
        offregion = run.split('if [ "${BACKUP_ORIGIN}" = "offregion" ]; then', 1)
        if len(offregion) == 2:
            # The offregion branch is everything up to its `else`.
            assert "DO_DROPLET_HOST" not in offregion[1].split("\nelse\n", 1)[0], name
        else:
            assert 'if [ "${BACKUP_ORIGIN}" = "primary" ]; then' in run, name


def test_an_unconfigured_off_region_store_fails_loudly():
    run = _step("Verify secrets present")["run"]
    assert 'missing+=("OFFREGION_BACKUP_REMOTE")' in run
    assert 'missing+=("OFFREGION_BACKUP_RCLONE_CONFIG")' in run
    assert "red on purpose" in run
    assert "exit 1" in run


def test_off_region_names_are_checked_before_rclone_uses_them():
    run = _step("Validate selected backup")["run"]
    grammar_check = run.index('if [[ ! "${name}" =~ ${grammar} ]]; then')
    copy = run.index("rclone --config \"${rclone_conf}\" copyto")
    assert grammar_check < copy
    assert "umask 077" in run, "the rclone credential must not be world-readable"
    trap = run.index("""trap 'rm -f "${rclone_conf}"' EXIT""")
    assert trap < run.index("umask 077"), "cleanup must be armed before the file exists"


def test_one_validator_binds_to_the_origin_s_root():
    run = _step("Validate selected backup")["run"]
    assert "backup_root = Path(sys.argv[2]).resolve(strict=True)" in run
    assert 'python3 - "${candidate}" "${backup_root}"' in run
    assert '/var/backups/tinyassets").resolve' not in run


def test_off_region_image_comes_from_ghcr_not_the_primary():
    run = _step("Resolve immutable production daemon image")["run"]
    marker = 'if [ "${BACKUP_ORIGIN}" = "offregion" ]; then'
    offregion = run.split(marker, 1)[1].split("\nelse\n", 1)[0]
    assert "docker buildx imagetools inspect" in offregion
    assert "git rev-list --max-count=100 HEAD" in offregion
    drill_job = _load()["jobs"]["drill"]
    checkout = drill_job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout.get("with", {}).get("fetch-depth", 1) >= 100


# ---------------------------------------------------------------------------
# (c) Required inputs
# ---------------------------------------------------------------------------

def test_has_drill_droplet_size_input():
    wf = _load()
    inputs = _dispatch_inputs(wf)
    assert "drill_droplet_size" in inputs


def test_has_backup_source_input():
    wf = _load()
    inputs = _dispatch_inputs(wf)
    assert "backup_source" in inputs


def test_has_destroy_on_failure_input():
    wf = _load()
    inputs = _dispatch_inputs(wf)
    assert "destroy_on_failure" in inputs


# ---------------------------------------------------------------------------
# (d) Required secrets
# ---------------------------------------------------------------------------

def test_digitalocean_token_referenced():
    assert "DIGITALOCEAN_TOKEN" in _text()


def test_do_ssh_key_referenced():
    assert "DO_SSH_KEY" in _text()


def test_do_droplet_host_referenced():
    assert "DO_DROPLET_HOST" in _text()


def test_do_ssh_user_referenced():
    assert "DO_SSH_USER" in _text()


# ---------------------------------------------------------------------------
# (e) Droplet provision via DO API
# ---------------------------------------------------------------------------

def test_provisions_droplet_via_do_api():
    text = _text()
    assert "digitalocean.com/v2/droplets" in text, (
        "workflow must create a Droplet via DO API"
    )


def test_provision_step_present():
    names = _step_names(_load())
    assert any("provision" in n or "droplet" in n for n in names), (
        "must have a provision/droplet creation step"
    )


def test_provision_resolves_current_region_compatible_debian_image():
    provision = _step("Provision drill Droplet")
    run = provision["run"]

    assert "scripts/select_do_image.py" in run
    assert '--region "${DRILL_REGION}"' in run
    assert "image_slug=$(" in run
    assert 'echo "image_slug=${image_slug}" >> "$GITHUB_OUTPUT"' in run
    selector_index = run.index("scripts/select_do_image.py")
    post_indexes = [
        match.start()
        for match in re.finditer(r"--method POST", run)
    ]
    assert post_indexes
    assert all(selector_index < post_index for post_index in post_indexes)
    assert "debian-12-x64" not in _text()


def test_resolved_image_is_preserved_in_terminal_evidence():
    image_output = "steps.droplet.outputs.image_slug"
    probe_failure = _step("Open dr-failed issue on failure")
    success_log = _step("Append drill result to log")
    delete_failure = _step("Escalate failed Droplet deletion")
    summary = _step("Summary")

    for evidence_step in (
        probe_failure,
        success_log,
        delete_failure,
        summary,
    ):
        assert image_output in str(evidence_step)
        assert "Image" in str(evidence_step)


def test_runbook_requires_digitalocean_image_read_scope():
    runbook = _RUNBOOK.read_text(encoding="utf-8")
    assert "image:read" in runbook
    assert "before any mutation" in runbook


# ---------------------------------------------------------------------------
# (f) Bootstrap runs hetzner-bootstrap.sh
# ---------------------------------------------------------------------------

def test_bootstrap_step_runs_bootstrap_sh():
    text = _text()
    assert "hetzner-bootstrap.sh" in text or "bootstrap.sh" in text


def test_bootstrap_step_present():
    names = _step_names(_load())
    assert any("bootstrap" in n for n in names)


# ---------------------------------------------------------------------------
# (g) Restore step runs backup-restore.sh
# ---------------------------------------------------------------------------

def test_restore_step_present():
    names = _step_names(_load())
    assert any("restore" in n for n in names)


def test_restore_uses_backup_restore_sh():
    assert "backup-restore.sh" in _text()


# ---------------------------------------------------------------------------
# (h) Probe uses direct IP:8001 (not tunnel URL)
# ---------------------------------------------------------------------------

def test_probe_uses_direct_ip_not_tunnel():
    text = _text()
    assert "8001" in text, "probe must target port 8001 directly on drill Droplet"
    # Must NOT probe tinyassets.io (that goes through the tunnel the drill lacks).
    # The probe URL should use the drill IP variable, not the canonical URL.
    assert "mcp_probe.py" in text or "mcp_public_canary.py" in text


def test_probe_step_present():
    names = _step_names(_load())
    assert any("probe" in n for n in names)


# ---------------------------------------------------------------------------
# (i) Pass path destroys Droplet + appends to log
# ---------------------------------------------------------------------------

def test_pass_path_destroys_droplet():
    text = _text()
    assert "digitalocean.com/v2/droplets/${DROPLET_ID}" in text or \
           "droplets/${DROPLET_ID}" in text, (
        "pass path must destroy drill Droplet via DO API DELETE"
    )


def test_pass_path_appends_to_log():
    text = _text()
    assert "dr-drill-log.md" in text


def test_pass_log_describes_port_forward_probe():
    run = _append_log_step_run()
    assert "direct HTTP to drill Droplet port 8001" not in run
    assert "SSH port-forward" in run
    assert "localhost:8001" in run


# ---------------------------------------------------------------------------
# (j) Fail path opens dr-failed issue + leaves Droplet up
# ---------------------------------------------------------------------------

def test_fail_path_opens_dr_failed_issue():
    assert "dr-failed" in _text()


def test_fail_path_leaves_droplet_up_by_default():
    """By default (destroy_on_failure=false), fail path must NOT destroy."""
    text = _text()
    # The destroy-on-failure step must be conditional on the input being true.
    assert "destroy_on_failure" in text
    assert "'true'" in text or '"true"' in text


# ---------------------------------------------------------------------------
# (k) Runbook and log files exist
# ---------------------------------------------------------------------------

def test_dr_drill_runbook_exists():
    assert _RUNBOOK.exists(), f"Missing: {_RUNBOOK}"


def test_dr_drill_log_exists():
    assert _LOG.exists(), f"Missing: {_LOG}"


def test_dr_drill_runbook_mentions_weekly_off_region():
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "Weekly" in text
    assert "off-region" in text
    assert "Quarterly" not in text, "the cadence is weekly now (S1a.5)"


def test_dr_drill_runbook_mentions_pass_fail():
    text = _RUNBOOK.read_text(encoding="utf-8").lower()
    assert "pass" in text and "fail" in text


def test_dr_drill_runbook_size_default_matches_workflow():
    assert _runbook_input_default("drill_droplet_size") == (
        _workflow_input_default("drill_droplet_size")
    )


def test_dr_drill_runbook_mentions_ssh_port_forward_probe():
    text = _RUNBOOK.read_text(encoding="utf-8").lower()
    assert "ssh port-forward" in text
    assert "localhost:8001" in text


# ---------------------------------------------------------------------------
# Task #66 — pipefail fix + size bump + mid-job cleanup
# ---------------------------------------------------------------------------

def test_bootstrap_step_uses_pipefail():
    """Bootstrap pipe to tee must not swallow exit code — pipefail required."""
    text = _text()
    assert "pipefail" in text, (
        "Bootstrap step must use set -euo pipefail so SSH exit code propagates through tee"
    )


def test_bootstrap_step_does_not_pipe_ssh_to_tee():
    """2026-04-22 Task #66 follow-up: the `ssh ... | tee` pattern itself
    swallowed the bootstrap exit code on two drill runs because a following
    `tail` command always exits 0 and masked the pipeline result. Fix is
    to redirect to a file, capture $?, surface last lines, then propagate.
    """
    run = _bootstrap_step_run()
    assert "| tee /tmp/bootstrap.log" not in run, (
        "ssh | tee bootstrap.log is the anti-pattern — replace with "
        "redirect + explicit exit-code capture"
    )


def test_bootstrap_step_captures_exit_code_explicitly():
    run = _bootstrap_step_run()
    assert "bootstrap_code=$?" in run, (
        "bootstrap step must capture ssh exit code into a variable"
    )
    assert ('exit "${bootstrap_code}"' in run
            or "exit ${bootstrap_code}" in run), (
        "captured bootstrap exit code must be propagated so a failed "
        "bootstrap fails the step"
    )


def test_bootstrap_step_redirects_instead_of_piping():
    run = _bootstrap_step_run()
    assert ">/tmp/bootstrap.log" in run or "> /tmp/bootstrap.log" in run, (
        "bootstrap output must be captured via redirect, not pipe"
    )


def test_bootstrap_step_surfaces_tail_before_exit():
    """The last-50-lines dump must run before the exit so operators see
    what failed, even on a red drill run."""
    run = _bootstrap_step_run()
    tail_idx = run.find("tail -50 /tmp/bootstrap.log")
    assert tail_idx >= 0, "tail of bootstrap.log must be surfaced"
    after = run[tail_idx:]
    assert ('if [ "${bootstrap_code}" -ne 0 ]' in after
            or "if [ ${bootstrap_code} -ne 0 ]" in after), (
        "exit-on-failure check must come AFTER tail so the operator "
        "sees the log before the step aborts"
    )


def test_default_drill_size_is_not_1gb():
    """s-1vcpu-1gb OOMs on apt+docker install; default must be at least 2GB."""
    wf = _load()
    inputs = _dispatch_inputs(wf)
    default_size = inputs.get("drill_droplet_size", {}).get("default", "")
    assert default_size != "s-1vcpu-1gb", (
        f"Default size {default_size!r} is known to OOM; bump to s-2vcpu-2gb or larger"
    )
    assert "2vcpu" in default_size or "2gb" in default_size.lower() or \
           default_size >= "s-2", (
        f"Default size {default_size!r} appears smaller than recommended minimum"
    )


def test_mid_job_cleanup_step_exists():
    """A cleanup step must fire even when bootstrap/restore fail (before probe runs)."""
    names = _step_names(_load())
    assert any(
        "cleanup" in name
        or "mid-job" in name
        or name == "destroy drill droplet when required"
        for name in names
    ), (
        "Must have a cleanup step that fires on mid-job failure (before probe color is set)"
    )


def test_mid_job_cleanup_fires_on_always():
    """Cleanup step must have if: always() so it fires even when prior steps fail."""
    steps = _steps(_load())
    cleanup_steps = [s for s in steps
                     if "cleanup" in (s.get("name") or "").lower()
                     or "mid-job" in (s.get("name") or "").lower()
                     or (s.get("name") or "").lower()
                     == "destroy drill droplet when required"]
    assert cleanup_steps, "no cleanup step found"
    for s in cleanup_steps:
        cond = s.get("if", "")
        assert "always()" in str(cond).lower(), (
            f"Cleanup step '{s.get('name')}' must have if: always(), got: {cond!r}"
        )


def test_mid_job_cleanup_checks_probe_color_empty():
    """Cleanup fires only when probe color is unset (mid-job fail, not normal paths)."""
    text = _text()
    # Must guard on drillprobe.outputs.color == '' to avoid double-destroy.
    assert "drillprobe.outputs.color" in text and "''" in text, (
        "Mid-job cleanup must check that drillprobe.outputs.color is empty"
    )


# ---------------------------------------------------------------------------
# 2026-07-23 — hardened archive, API, transfer, and deletion evidence
# ---------------------------------------------------------------------------


def test_primary_archive_preflight_precedes_droplet_provisioning():
    steps = _steps(_load())
    names = [step.get("name") for step in steps]
    preflight_index = names.index("Validate selected backup")
    provision_index = names.index("Provision drill Droplet")
    assert preflight_index < provision_index

    run = steps[preflight_index]["run"]
    assert "/var/backups/tinyassets" in run
    assert "tinyassets-data-*.tar.gz" in run
    assert "tarfile" in run
    assert "archive_sha256" in run
    assert '"path_b64"' in run
    assert '"tarball_b64"' in run
    assert "sample_path_b64" in run
    assert "sample_sha256" in run
    assert "printf '%q'" in run
    assert "re.fullmatch" in run
    assert r"\d{4}-\d{2}-\d{2}T" in run
    output_keys = run[run.index('for key in ('):]
    assert '"path",' not in output_keys
    assert '"tarball",' not in output_keys


def test_primary_archive_filename_grammar_rejects_output_protocol_injection():
    run = _step("Validate selected backup")["run"]
    grammar_block = run[run.index("re.fullmatch("):run.index("selected.name,")]
    fragments = re.findall(r'r"([^"]+)"', grammar_block)
    grammar = "".join(fragments)

    assert re.fullmatch(
        grammar,
        "tinyassets-data-2026-07-23T12-34-56Z.tar.gz",
    )
    for adversarial in (
        "tinyassets-data-2026-07-23T12-34-56Z.tar.gz\nforged=value",
        "tinyassets-data-2026-07-23T12-34-56Z.tar.gz\rforged=value",
        "tinyassets-data-anything.tar.gz",
    ):
        assert re.fullmatch(grammar, adversarial) is None


def test_every_digitalocean_request_uses_bounded_helper():
    text = _text()
    assert "scripts/do_api_request.py" in text
    assert "curl -sf" not in text
    assert "--fail-with-body" not in text
    assert "|| echo ''" not in text


def test_transfer_propagates_pipeline_failure_and_verifies_checksum():
    run = _step("Transfer verified backup to drill Droplet")["run"]
    assert "set -euo pipefail" in run
    assert "printf '%q'" in run
    assert "SOURCE_SHA256" in run
    assert "sha256sum" in run
    assert "checksum mismatch" in run.lower()


def test_restore_uses_exact_transferred_backup_file():
    restore = _step("Restore exact backup on drill Droplet")
    run = restore["run"]
    assert "BACKUP_FILE=" in run
    assert "BACKUP_DEST=" not in run
    assert "steps.backup.outputs.tarball" in str(restore.get("env", {}))


def test_restored_state_proof_precedes_compose_and_probe():
    steps = _steps(_load())
    names = [step.get("name") for step in steps]
    proof_index = names.index("Verify representative restored state")
    compose_index = names.index("Start compose on drill Droplet")
    probe_index = names.index("Probe drill Droplet directly")
    assert proof_index < compose_index < probe_index

    proof = steps[proof_index]
    run = proof["run"]
    assert "docker volume inspect tinyassets-data" in run
    assert "sample_path_b64" in str(proof.get("env", {}))
    assert "sample_sha256" in str(proof.get("env", {}))
    assert "is_symlink" in run
    assert "sha256" in run


def test_success_log_records_archive_and_restored_member_evidence():
    run = _append_log_step_run()
    assert "archive_sha256" in run
    assert "sample_path_b64" in run
    assert "sample_sha256" in run
    assert "git push || true" not in run


def test_adversarial_member_name_remains_encoded_in_rendered_evidence():
    proof_run = _step("Verify representative restored state")["run"]
    assert "path_b64={sys.argv[2]}" in proof_run
    assert "verified: {relative}" not in proof_run

    log_run = _append_log_step_run()
    assert "Representative member path (base64 UTF-8)" in log_run
    assert "${sample_path_b64}" in log_run
    assert "base64.b64decode(sys.argv[1]).decode()" not in log_run


def test_success_log_requires_confirmed_destroy_and_runs_after_it():
    steps = _steps(_load())
    names = [step.get("name") for step in steps]
    destroy_index = names.index("Destroy drill Droplet when required")
    log_index = names.index("Append drill result to log")
    assert destroy_index < log_index

    log = steps[log_index]
    condition = str(log.get("if", ""))
    assert "steps.destroy.outcome == 'success'" in condition
    assert "steps.drillprobe.outputs.color == 'green'" in condition


def test_unified_destroy_runs_always_and_exposes_bounded_failure():
    destroy = _step("Destroy drill Droplet when required")
    assert destroy.get("id") == "destroy"
    assert destroy.get("continue-on-error") is True
    condition = str(destroy.get("if", ""))
    assert "always()" in condition
    assert "steps.droplet.outputs.droplet_id != ''" in condition
    assert "steps.drillprobe.outputs.color == 'green'" in condition
    assert "inputs.destroy_on_failure == 'true'" in condition
    assert "steps.drillprobe.outputs.color == ''" in condition
    assert "diagnostic" in destroy["run"]
    assert "scripts/do_api_request.py" in destroy["run"]


def test_failed_destroy_escalates_with_identity_then_forces_red():
    escalation = _step("Escalate failed Droplet deletion")
    assert "steps.destroy.outcome == 'failure'" in str(escalation.get("if", ""))
    escalation_text = str(escalation)
    assert "dr-failed" in escalation_text
    assert "DROPLET_ID" in escalation_text
    assert "GITHUB_RUN_ID" in escalation_text
    assert "DELETE_DIAGNOSTIC" in escalation_text

    terminal = _step("Fail after Droplet deletion failure")
    assert "always()" in str(terminal.get("if", ""))
    assert "steps.destroy.outcome == 'failure'" in str(terminal.get("if", ""))
    assert "exit 1" in terminal["run"]


# 2026-07-23 — immutable runtime image on the fresh drill host


def test_runtime_image_is_resolved_and_validated_before_provisioning():
    steps = _steps(_load())
    names = [step.get("name") for step in steps]
    resolve_index = names.index("Resolve immutable production daemon image")
    provision_index = names.index("Provision drill Droplet")
    assert resolve_index < provision_index

    step = steps[resolve_index]
    assert step.get("id") == "runtime-image"
    run = step["run"]
    assert "grep -E '^TINYASSETS_IMAGE='" in run
    assert "cat /etc/tinyassets/env" not in run
    assert "scp" not in run
    assert "re.fullmatch" in run
    assert (
        r"ghcr\.io/tinyassets/tinyassets-daemon@sha256:[0-9a-f]{64}"
        in run
    )
    assert 'echo "image=${runtime_image}" >> "$GITHUB_OUTPUT"' in run


def test_runtime_image_grammar_rejects_tags_and_output_injection():
    run = _step("Resolve immutable production daemon image")["run"]
    match = re.search(r're\.fullmatch\(r"([^"]+)", runtime_image\)', run)
    assert match, "runtime image must use one full-string Python regex"
    grammar = match.group(1)

    def normalize(raw: str) -> str:
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
            return raw[1:-1]
        return raw

    assert "raw_runtime_image[0] == raw_runtime_image[-1]" in run
    assert """raw_runtime_image[0] in {"'", '"'}""" in run
    valid = "ghcr.io/tinyassets/tinyassets-daemon@sha256:" + "a" * 64
    for accepted in (valid, f"'{valid}'", f'"{valid}"'):
        assert re.fullmatch(grammar, normalize(accepted))
    for adversarial in (
        "ghcr.io/tinyassets/tinyassets-daemon:latest",
        "other.example/tinyassets-daemon@sha256:" + "a" * 64,
        valid + "\nforged=value",
        valid + "\rforged=value",
        "ghcr.io/tinyassets/tinyassets-daemon@sha256:" + "A" * 64,
        f'"{valid}\nforged=value"',
        f"'{valid}\rforged=value'",
        f'"{valid}\'',
        f'""{valid}""',
    ):
        assert re.fullmatch(grammar, normalize(adversarial)) is None


def test_compose_uses_runtime_image_ephemerally_with_fresh_env():
    step = _step("Start compose on drill Droplet")
    env_text = str(step.get("env", {}))
    assert "steps.runtime-image.outputs.image" in env_text

    run = step["run"]
    assert "printf '%q'" in run
    assert "TINYASSETS_IMAGE=${quoted_runtime_image}" in run
    assert "--env-file /etc/tinyassets/env" in run
    assert "install-tinyassets-env.sh" not in run
    assert "sed -i" not in run


def test_validated_runtime_image_is_persisted_into_exactly_one_fresh_assignment():
    step = _step("Persist validated runtime image in fresh env")
    assert "steps.runtime-image.outputs.image" in str(step.get("env", {}))

    run = step["run"]
    assert "ghcr\\.io/tinyassets/tinyassets-daemon@sha256:[0-9a-f]{64}" in run
    assert 'Path("/etc/tinyassets/env")' in run
    assert "env_path.is_symlink()" in run
    assert 'line.startswith("TINYASSETS_IMAGE=")' in run
    assert "len(matches) != 1" in run
    assert 'lines[index] = f"TINYASSETS_IMAGE={image}{newline}"' in run
    assert 'env_path.write_text("".join(lines))' in run
    assert "DO_DROPLET_HOST" not in run


def test_cleanup_only_dispatch_is_identity_guarded_and_cannot_provision():
    wf = _load()
    inputs = _dispatch_inputs(wf)
    assert inputs["cleanup_droplet_id"]["default"] == ""

    jobs = wf["jobs"]
    cleanup = jobs["cleanup_retained_droplet"]
    assert cleanup["if"] == "inputs.cleanup_droplet_id != ''"
    assert jobs["drill"]["if"] == "inputs.cleanup_droplet_id == ''"

    steps = cleanup["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Verify retained drill Droplet identity") < names.index(
        "Delete retained drill Droplet"
    )
    assert "Provision drill Droplet" not in names
    assert "Install SSH key" not in names
    assert "Validate selected backup" not in names

    identity = next(
        step for step in steps
        if step.get("name") == "Verify retained drill Droplet identity"
    )["run"]
    assert "--method GET" in identity
    assert 'droplet.get("name") != "tinyassets-dr-drill"' in identity
    assert '{"dr-drill", "tinyassets"}.issubset(tags)' in identity

    deletion = next(
        step for step in steps
        if step.get("name") == "Delete retained drill Droplet"
    )["run"]
    assert "--method DELETE" in deletion
    assert "--method POST" not in deletion
    assert "/v2/droplets/${DROPLET_ID}" in deletion


def test_red_probe_always_records_issue_then_fails_the_run():
    probe = _step("Probe drill Droplet directly")
    assert "color=red" in probe["run"]
    assert "exit 98" in probe["run"]

    issue = _step("Open dr-failed issue on failure")
    assert issue["if"] == "always() && steps.drillprobe.outputs.color == 'red'"

    fail = _step("Fail after red probe")
    assert fail["if"] == "always() && steps.drillprobe.outputs.color == 'red'"
    assert fail["run"] == "exit 1"


@pytest.mark.parametrize(
    "step_name",
    [
        "Open dr-failed issue on failure",
        "Append drill result to log",
        "Escalate failed Droplet deletion",
        "Summary",
    ],
)
def test_terminal_evidence_distinguishes_runtime_image(step_name):
    step = _step(step_name)
    assert "steps.runtime-image.outputs.image" in str(step.get("env", {}))
    assert "Runtime Image" in str(step)


def test_every_input_the_drill_job_reads_survives_a_scheduled_run():
    """A schedule supplies no inputs and dispatch defaults do not apply. Every
    input read outside the cleanup-only job must either carry an `||` default or
    be one whose empty value is the intended scheduled behaviour (Codex on #4271:
    an empty droplet size reached the create payload)."""
    empty_is_intended = {
        "inputs.backup_source",        # empty = newest backup
        "inputs.destroy_on_failure",   # empty != 'true' = keep a red Droplet up
        "inputs.cleanup_droplet_id",   # empty = run the drill, not cleanup
    }
    wf = _load()
    drill_text = yaml.safe_dump(wf["jobs"]["drill"]) + yaml.safe_dump(wf.get("env", {}))
    for expr in re.findall(r"\$\{\{([^}]*)\}\}", drill_text):
        for name in re.findall(r"inputs\.[a-z_]+", expr):
            if name in empty_is_intended:
                continue
            assert "||" in expr, f"{name} has no default for a scheduled run: {expr.strip()}"


def test_the_droplet_payload_gets_the_effective_size():
    wf = _load()
    assert wf["env"]["DRILL_DROPLET_SIZE"] == "${{ inputs.drill_droplet_size || 's-2vcpu-2gb' }}"
    provision = _step("Provision drill Droplet")
    assert provision["env"]["DRILL_SIZE"] == "${{ env.DRILL_DROPLET_SIZE }}"
    assert "inputs.drill_droplet_size" not in yaml.safe_dump(wf["jobs"]["drill"])


def test_no_step_reaches_the_primary_outside_the_primary_origin():
    """All steps, not a named few (Codex on #4271)."""
    for step in _steps(_load()):
        run = step.get("run", "") or ""
        if "DO_DROPLET_HOST" not in run:
            continue
        marker = 'if [ "${BACKUP_ORIGIN}" = "offregion" ]; then'
        if marker in run:
            offregion = run.split(marker, 1)[1].split("\nelse\n", 1)[0]
            assert "DO_DROPLET_HOST" not in offregion, step.get("name")
        else:
            assert 'if [ "${BACKUP_ORIGIN}" = "primary" ]; then' in run, step.get("name")
