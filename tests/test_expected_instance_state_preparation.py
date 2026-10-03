"""Focused tests for expected-instance state preparation and its deploy ordering.

OpenSpec change `cloud-only-runtime-admission`, task 4.

Two things are asserted here:

1. `scripts/prepare_expected_instance_state.py` writes exactly the typed state
   the resolver reads, resolves identity from the DigitalOcean inventory read
   (never from the droplet's own metadata answer, which would be circular), and
   emits no identifier on stdout. No real API call: the single resolver helper is
   monkeypatched.
2. `deploy-prod.yml` prepares that state **before** the candidate starts, and
   neither the success-receipt publication nor the rollback path erases it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "prepare_expected_instance_state.py"
_SPEC = importlib.util.spec_from_file_location("prepare_expected_instance_state", _SCRIPT)
prep = importlib.util.module_from_spec(_SPEC)
sys.modules["prepare_expected_instance_state"] = prep
_SPEC.loader.exec_module(prep)

from tinyassets import platform_runtime_provenance as prov  # noqa: E402

# --- the preparer ---------------------------------------------------------


def test_prepared_state_is_exactly_what_the_resolver_accepts(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        prep,
        "resolve_expected_droplet",
        lambda token, host: {
            "name": "expected_droplet",
            "verdict": prep.PASS,
            "reason": "resolved",
            "_instance_id": "512345678",
        },
    )
    monkeypatch.setenv("DO_API_TOKEN", "read-scoped-token")
    monkeypatch.setenv("DO_DROPLET_HOST", "198.51.100.7")
    out = tmp_path / prov.EXPECTED_INSTANCE_FILENAME

    assert prep.main(["--out", str(out)]) == 0

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == prov.EXPECTED_INSTANCE_SCHEMA
    assert payload["version"] == prov.EXPECTED_INSTANCE_VERSION
    assert payload["expected_instance_id"] == "512345678"
    assert payload["enforced"] is False
    # The resolver must accept the file the deploy writes; this is the contract
    # both halves have to agree on.
    read = prov.read_expected_instance_id(data_root=tmp_path)
    assert (read.instance_id, read.reason) == ("512345678", "prepared")
    # No identity in CI logs.
    assert "512345678" not in capsys.readouterr().out


def test_unresolved_identity_is_not_prepared_and_never_guessed(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        prep,
        "resolve_expected_droplet",
        lambda token, host: {
            "name": "expected_droplet",
            "verdict": "unknown",
            "reason": "host_hint_matched_no_droplet",
        },
    )
    out = tmp_path / prov.EXPECTED_INSTANCE_FILENAME
    assert prep.main(["--out", str(out)]) == 3
    assert not out.exists()
    assert "host_hint_matched_no_droplet" in capsys.readouterr().out
    # The resolver then observes a missing expectation and refuses — fail-closed.
    read = prov.read_expected_instance_id(data_root=tmp_path)
    assert read.reason == "expected_identity_missing"


def test_malformed_resolved_identity_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        prep,
        "resolve_expected_droplet",
        lambda token, host: {
            "verdict": prep.PASS,
            "reason": "resolved",
            "_instance_id": "droplet-512345678",
        },
    )
    out = tmp_path / prov.EXPECTED_INSTANCE_FILENAME
    assert prep.main(["--out", str(out)]) == 3
    assert not out.exists()


def test_identity_comes_from_the_inventory_read_not_the_droplets_own_metadata() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "resolve_expected_droplet" in source
    # Recording what the box claims and then comparing it to the same claim
    # proves nothing; the preparer must not read the metadata service at all.
    assert "169.254.169.254" not in source
    assert "METADATA_URL" not in source


def test_preflight_keeps_the_raw_identity_internal() -> None:
    # `_`-prefixed fact fields are stripped before output (cloud_only_preflight
    # sanitization), which is why the preparer may read `_instance_id`.
    source = (_REPO / "scripts" / "cloud_only_preflight.py").read_text(encoding="utf-8")
    assert "_instance_id=matched_id" in source
    assert 'if not k.startswith("_")' in source


# --- deploy ordering ------------------------------------------------------


@pytest.fixture(scope="module")
def deploy_steps() -> list[dict]:
    workflow = yaml.safe_load(
        (_REPO / ".github" / "workflows" / "deploy-prod.yml").read_text(encoding="utf-8")
    )
    jobs = workflow["jobs"]
    for job in jobs.values():
        steps = job.get("steps") or []
        names = [str(step.get("name", "")) for step in steps]
        if any("fail-safe deploy" in name for name in names):
            return steps
    raise AssertionError("deploy-prod.yml has no fail-safe deploy job")


def _index(steps: list[dict], needle: str) -> int:
    for i, step in enumerate(steps):
        if needle in str(step.get("name", "")):
            return i
    raise AssertionError(f"no deploy step named like {needle!r}")


def test_expected_state_is_prepared_before_the_candidate_starts(deploy_steps) -> None:
    prepare = _index(deploy_steps, "Prepare expected-instance state")
    start = _index(deploy_steps, "Run fail-safe deploy")
    receipt = _index(deploy_steps, "Publish release-state receipt")
    # The receipt is published only after the candidate is healthy, so the
    # expected identity cannot live inside it: first enforcement would depend on
    # a field that does not exist yet at startup.
    assert prepare < start < receipt


def test_preparation_does_not_publish_a_success_receipt_early(deploy_steps) -> None:
    prepare = deploy_steps[_index(deploy_steps, "Prepare expected-instance state")]
    run = str(prepare.get("run", ""))
    assert "release-state.json" not in run
    assert "receipt_available" not in run
    assert prov.EXPECTED_INSTANCE_FILENAME in run


def test_preparation_failure_blocks_candidate_deployment(
    deploy_steps,
) -> None:
    prepare = deploy_steps[_index(deploy_steps, "Prepare expected-instance state")]
    assert prepare.get("continue-on-error", False) is False
    start = deploy_steps[_index(deploy_steps, "Run fail-safe deploy")]
    assert start.get("if", "success()") == "success()"


@pytest.mark.parametrize(
    ("prepare_rc", "upload_rc", "expected_rc", "remote_commands"),
    [(3, 0, 3, ""), (2, 0, 2, ""), (0, 7, 7, "scp\n"), (0, 0, 0, "scp\nssh\n")],
)
def test_preparation_shell_propagates_failures_before_candidate_start(
    deploy_steps, tmp_path, prepare_rc, upload_rc, expected_rc, remote_commands
) -> None:
    """Run the actual workflow shell with inert commands, no API/SSH calls."""
    if os.name == "nt":
        bash = Path("C:/Program Files/Git/bin/bash.exe")
        if not bash.is_file():
            pytest.skip("Git Bash unavailable; do not start WSL")
    else:
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("bash unavailable")
    prepare = deploy_steps[_index(deploy_steps, "Prepare expected-instance state")]
    output = tmp_path / "github-output"
    trace = tmp_path / "remote-commands"
    prelude = (
        f"python() {{ return {prepare_rc}; }}\n"
        f'scp() {{ printf "scp\\n" >> "$TRACE"; return {upload_rc}; }}\n'
        'ssh() { printf "ssh\\n" >> "$TRACE"; '
        'while IFS= read -r ignored; do :; done; }\n'
    )
    result = subprocess.run(
        [str(bash), "--noprofile", "--norc", "-s"],
        input=prelude + prepare["run"], text=True, capture_output=True,
        cwd=tmp_path, timeout=10,
        env={
            **os.environ,
            "GITHUB_OUTPUT": output.as_posix(), "TRACE": trace.as_posix(),
            "DO_SSH_USER": "fixture", "DO_DROPLET_HOST": "fixture.invalid",
        },
    )
    assert result.returncode == expected_rc, result.stderr
    assert (trace.read_text() if trace.exists() else "") == remote_commands
    if expected_rc:
        assert not output.exists() or "prepared=true" not in output.read_text()
    else:
        assert "prepared=true" in output.read_text()


def test_receipt_and_rollback_preserve_the_expected_state(deploy_steps) -> None:
    receipt = deploy_steps[_index(deploy_steps, "Publish release-state receipt")]
    rollback = deploy_steps[_index(deploy_steps, "Roll back if the public canary")]
    for step in (receipt, rollback):
        run = str(step.get("run", ""))
        # The receipt is rewritten whole every deploy; a separate file cannot be
        # erased by that rewrite, and nothing in either step may delete it.
        assert prov.EXPECTED_INSTANCE_FILENAME not in run
        assert "rm " + prov.EXPECTED_INSTANCE_FILENAME not in run
    receipt_run = str(receipt.get("run", ""))
    assert "cat > release-state.json" in receipt_run


def test_expected_state_lands_in_the_mounted_data_root(deploy_steps) -> None:
    prepare = deploy_steps[_index(deploy_steps, "Prepare expected-instance state")]
    run = str(prepare.get("run", ""))
    # Same volume the daemon resolves through TINYASSETS_DATA_DIR=/data, so the
    # resolver reads it through data_dir() with no path logic of its own.
    assert "docker volume inspect tinyassets-data" in run
    assert "install -m 0644" in run


def test_preparation_atomically_replaces_state_without_claiming_absence(deploy_steps) -> None:
    prepare = deploy_steps[_index(deploy_steps, "Prepare expected-instance state")]
    run = str(prepare.get("run", ""))
    assert 'mktemp "${vol}/.platform-expected-instance.XXXXXX"' in run
    assert 'mv -f -- "$staged" "${vol}/platform-expected-instance.json"' in run
    assert run.index('sudo test -d "$vol"') < run.index("sudo mktemp")
    assert run.index("sudo install -m 0644") < run.index("sudo mv -f")
    assert "any prior state is unchanged" in run
    assert "will observe expected_identity_missing" not in run


def test_rollback_bundle_destinations_exclude_runtime_data() -> None:
    """Pin actual restore inputs, not merely the workflow's wrapper command.

    This is a structural regression check, not a production rollback drill.
    Changing the bundle map requires reviewing data-custody compatibility.
    """
    script = (_REPO / "deploy" / "deploy_fail_safe.sh").read_text(encoding="utf-8")
    match = re.search(r"(?ms)^BUNDLE_MAP=\(\n(.*?)^\)", script)
    assert match is not None
    destinations = []
    for line in match.group(1).splitlines():
        entry = line.strip().strip('"')
        assert len(entry.split("|")) == 4
        destinations.append(entry.split("|")[1])
    assert destinations == [
        "${RUNTIME_DIR}/compose.yml",
        "${RUNTIME_DIR}/deploy/compose.yml",
        "${RUNTIME_DIR}/deploy/vector.yaml",
        "${RUNTIME_DIR}/deploy/vector-betterstack.yaml",
        "${RUNTIME_DIR}/deploy/vector-entrypoint.sh",
        "${UNIT_FILE}",
    ]


def test_rollback_reconverges_without_replacing_named_data_volume() -> None:
    compose = yaml.safe_load((_REPO / "deploy" / "compose.yml").read_text(encoding="utf-8"))
    assert compose["volumes"]["tinyassets-data"]["name"] == "tinyassets-data"
    daemon = compose["services"]["daemon"]
    assert "tinyassets-data:/data" in daemon["volumes"]
    assert daemon["environment"]["TINYASSETS_DATA_DIR"] == "/data"
    script = (_REPO / "deploy" / "deploy_fail_safe.sh").read_text(encoding="utf-8")
    restart = re.search(r"(?ms)^restart_stack\(\) \{\n(.*?)^\}", script)
    assert restart is not None
    # One logical command; `--timeout` (the bounded drain, 2026-10-01) sits on
    # a continuation line, so join continuations before matching.
    reconverge = (
        'docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d '
        '--timeout "$MAX_DAEMON_STOP_GRACE_S" daemon cloudflared logs'
    )
    joined = re.sub(r"\\\n\s*", "", restart.group(1))
    assert reconverge in joined
    executable = "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )
    # Guard explicit destructive volume operations in any rollback helper too.
    # This source check is not a security parser for arbitrary shell programs.
    assert not re.search(r"docker\s+(?:volume\s+(?:rm|prune)|system\s+prune)\b", executable)
    assert not re.search(r"docker\s+compose[^\n;]*\bdown\b", executable)
    assert "--renew-anon-volumes" not in executable
