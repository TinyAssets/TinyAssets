"""Tests for the ENV-UNREADABLE marker plumbing.

Navigator 2026-04-22 §a/§b fix: the entrypoint and the systemd
ExecStartPre + deploy-prod sed-assertions all emit the same canonical
token so p0-outage-triage.yml can grep journalctl and self-repair
the /etc/tinyassets/env perm-regression class without an SSH shell.

This test file exercises:
  - docker-entrypoint.sh's ENV-UNREADABLE detection path (all sentinels empty).
  - docker-entrypoint.sh's happy path (at least one sentinel non-empty).
  - That the marker token matches across deploy-prod, entrypoint, systemd unit,
    and p0-outage-triage so auto-triage's grep stays aligned.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_ENTRYPOINT = _REPO / "deploy" / "docker-entrypoint.sh"
_SYSTEMD_UNIT = _REPO / "deploy" / "tinyassets-daemon.service"
_DEPLOY_YAML = _REPO / ".github" / "workflows" / "deploy-prod.yml"
_FAILSAFE = _REPO / "deploy" / "deploy_fail_safe.sh"
_ENV_HELPER = _REPO / "deploy" / "install-tinyassets-env.sh"
_TRIAGE_YAML = _REPO / ".github" / "workflows" / "p0-outage-triage.yml"

CANONICAL_MARKER = "ENV-UNREADABLE"


def _have_bash() -> bool:
    return shutil.which("bash") is not None


def _bash_readable_path(path: Path) -> str:
    text = str(path)
    if sys.platform.startswith("win") and len(text) > 2 and text[1] == ":":
        drive = text[0].lower()
        rest = text[2:].replace("\\", "/").lstrip("/")
        if shutil.which("bash"):
            probe = subprocess.run(
                ["bash", "-lc", f"test -d /mnt/{drive}"],
                capture_output=True,
                timeout=5,
            )
            if probe.returncode == 0:
                return f"/mnt/{drive}/{rest}"
        return f"/{drive}/{rest}"
    return text


# ---- entrypoint behavior --------------------------------------------------


def _run_entrypoint_via_stdin(
    *,
    exec_replacement: str,
    extra_env: dict[str, str] | None = None,
    raw_preamble_lines: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """Run docker-entrypoint.sh with a harnessed exec line, fed via stdin.

    Avoids Windows path-translation issues — bash reads the script from
    stdin so no cross-OS argv path munging is needed. Git Bash on Windows
    does not reliably forward freshly-added env vars from Python's
    subprocess env= dict, so we prepend the env assignments directly into
    the piped script body. Also clear the sentinels in-script in case the
    parent shell inherited them.
    """
    script = _ENTRYPOINT.read_text(encoding="utf-8").replace(
        'exec "$@"', exec_replacement,
    )
    scratch = Path(tempfile.mkdtemp(prefix="tinyassets-entrypoint-"))
    preamble_lines = [
        # Clear sentinels first so ambient-shell values don't leak through.
        "unset CLOUDFLARE_TUNNEL_TOKEN SUPABASE_DB_URL TINYASSETS_IMAGE",
        f"export TINYASSETS_PACKAGE_ROOT={_bash_readable_path(_REPO)!r}",
        f"export TINYASSETS_DATA_DIR={_bash_readable_path(scratch / 'data')!r}",
        f"export CODEX_HOME={_bash_readable_path(scratch / 'codex')!r}",
        f"export CLAUDE_CONFIG_DIR={_bash_readable_path(scratch / 'claude')!r}",
    ]
    preamble_lines.extend(raw_preamble_lines or [])
    for key, value in (extra_env or {}).items():
        preamble_lines.append(f"export {key}={value!r}")
    combined = "\n".join(preamble_lines) + "\n" + script
    # Pass bytes to avoid Windows cp1252 encode errors on Unicode chars
    # (e.g. the → in our own comments).
    result = subprocess.run(
        ["bash", "-s", "--", "/bin/true"],
        input=combined.encode("utf-8"),
        capture_output=True,
        timeout=15,
    )
    return subprocess.CompletedProcess(
        args=result.args,
        returncode=result.returncode,
        stdout=result.stdout.decode("utf-8", "replace"),
        stderr=result.stderr.decode("utf-8", "replace"),
    )


@pytest.mark.skipif(not _have_bash(), reason="bash not on PATH")
def test_entrypoint_exits_with_marker_when_all_sentinels_empty():
    """All sentinels unset -> entrypoint emits ENV-UNREADABLE + exit 1."""
    result = _run_entrypoint_via_stdin(
        exec_replacement='echo "[harness] would-exec: $@"',
    )
    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}. stderr={result.stderr!r}"
    )
    assert CANONICAL_MARKER in result.stderr, (
        f"canonical marker missing from stderr: {result.stderr!r}"
    )
    # Should name the sentinel env vars so the operator can see what was expected.
    assert "CLOUDFLARE_TUNNEL_TOKEN" in result.stderr
    assert "TINYASSETS_IMAGE" in result.stderr


@pytest.mark.skipif(not _have_bash(), reason="bash not on PATH")
def test_entrypoint_passes_through_when_one_sentinel_set():
    """At least one sentinel non-empty -> entrypoint proceeds past the check."""
    result = _run_entrypoint_via_stdin(
        exec_replacement='echo "[harness] would-exec: $@"',
        extra_env={"TINYASSETS_IMAGE": "ghcr.io/tinyassets/tinyassets-daemon:abc123"},
    )
    assert result.returncode == 0, (
        f"expected happy-path exit 0; got {result.returncode}. "
        f"stderr={result.stderr!r} stdout={result.stdout!r}"
    )
    assert CANONICAL_MARKER not in result.stderr, (
        "marker should NOT fire when a sentinel is set"
    )
    assert "would-exec" in result.stdout


@pytest.mark.skipif(not _have_bash(), reason="bash not on PATH")
def test_entrypoint_fails_loud_when_required_data_file_missing(tmp_path: Path):
    """Required static data absent -> entrypoint emits DATA-FILE-MISSING."""
    result = _run_entrypoint_via_stdin(
        exec_replacement='echo "[harness] would-exec: $@"',
        extra_env={
            "TINYASSETS_IMAGE": "ghcr.io/tinyassets/tinyassets-daemon:abc123",
            "TINYASSETS_PACKAGE_ROOT": str(tmp_path),
        },
    )

    assert result.returncode == 1, (
        f"expected missing data-file exit 1; got {result.returncode}. "
        f"stderr={result.stderr!r} stdout={result.stdout!r}"
    )
    assert "DATA-FILE-MISSING: data/world_rules.lp" in result.stderr
    assert "would-exec" not in result.stdout


@pytest.mark.skipif(not _have_bash(), reason="bash not on PATH")
def test_entrypoint_data_file_probe_accepts_git_bash_windows_package_root(
    tmp_path: Path,
):
    """Git Bash gets TINYASSETS_PACKAGE_ROOT as C:\\...; normalize before -f."""
    package_root = tmp_path / "package-root"
    (package_root / "data").mkdir(parents=True)
    (package_root / "data" / "world_rules.lp").write_text("% rules\n")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_cygpath = fake_bin / "cygpath"
    fake_cygpath.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$TINYASSETS_FAKE_POSIX_ROOT\"\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_cygpath.chmod(0o755)

    result = _run_entrypoint_via_stdin(
        exec_replacement='echo "[harness] would-exec: $@"',
        extra_env={
            "TINYASSETS_IMAGE": "ghcr.io/tinyassets/tinyassets-daemon:abc123",
            "TINYASSETS_PACKAGE_ROOT": r"C:\Users\Jonathan\Projects\wf-review-108",
            "TINYASSETS_FAKE_POSIX_ROOT": _bash_readable_path(package_root),
        },
        raw_preamble_lines=[f"export PATH={_bash_readable_path(fake_bin)!r}:$PATH"],
    )

    assert result.returncode == 0, (
        f"expected normalized Windows root to pass; got {result.returncode}. "
        f"stderr={result.stderr!r} stdout={result.stdout!r}"
    )
    assert "DATA-FILE-MISSING" not in result.stderr
    assert "would-exec" in result.stdout


def test_entrypoint_data_file_probe_does_not_require_python_alias():
    """The startup data-file probe must stay shell-only for Git Bash hosts."""
    text = _ENTRYPOINT.read_text(encoding="utf-8")
    probe_start = text.index("_tinyassets_bash_path()")
    probe_text = text[probe_start:text.index('exec "$@"', probe_start)]

    assert "python" not in probe_text.lower()
    assert "cygpath" in probe_text
    assert "DATA-FILE-MISSING" in probe_text


@pytest.mark.skipif(not _have_bash(), reason="bash not on PATH")
def test_entrypoint_marker_goes_to_stderr_not_stdout():
    """journalctl captures both streams, but the marker belongs on stderr."""
    result = _run_entrypoint_via_stdin(
        exec_replacement='echo "would-exec"',
    )
    # Marker on stderr only.
    assert CANONICAL_MARKER in result.stderr
    assert CANONICAL_MARKER not in result.stdout


# ---- marker alignment across surfaces -------------------------------------


def test_systemd_unit_execstartpre_emits_canonical_marker():
    text = _SYSTEMD_UNIT.read_text(encoding="utf-8")
    assert "ExecStartPre=" in text, "ExecStartPre directive missing"
    assert CANONICAL_MARKER in text, (
        "systemd unit ExecStartPre must emit canonical ENV-UNREADABLE marker"
    )


def test_systemd_unit_compose_loads_tinyassets_env_for_interpolation():
    """Compose gets /etc/tinyassets/env via --env-file ONLY. The unit must NOT
    also EnvironmentFile= it: that file is the CONTAINER env (HOME=/app) and
    EnvironmentFile= overrides Environment= regardless of order, which broke
    the docker CLI's plugin discovery under systemd (2026-08-21). Only the
    three production services are started (compose.yml has unprofiled
    workers), and there is no ExecStop: a `compose down` on restart took the
    tunnel down with the daemon."""
    text = _SYSTEMD_UNIT.read_text(encoding="utf-8")
    service_section = text.split("[Service]", 1)[1].split("[Install]", 1)[0]
    directives = [ln for ln in service_section.splitlines() if ln and not ln.startswith("#")]
    assert any(
        ln.startswith("ExecStart=/usr/bin/docker compose --env-file /etc/tinyassets/env")
        # `--timeout 20` (the bounded drain, 2026-10-01) sits between them.
        and " up -d " in ln
        and ln.endswith(" daemon cloudflared logs")
        for ln in directives
    ), directives
    assert not any(ln.startswith("EnvironmentFile=") for ln in directives), directives
    assert not any(ln.startswith("ExecStop=") for ln in directives), directives
    assert "Environment=HOME=/opt/tinyassets" in directives


def test_systemd_start_limit_is_in_unit_section_not_service_section():
    text = _SYSTEMD_UNIT.read_text(encoding="utf-8")
    unit_section = text.split("[Service]", 1)[0]
    service_section = text.split("[Service]", 1)[1].split("[Install]", 1)[0]

    assert "StartLimitIntervalSec=300" in unit_section
    assert "StartLimitBurst=5" in unit_section
    assert "StartLimitIntervalSec" not in service_section
    assert "StartLimitBurst" not in service_section


def test_deploy_prod_env_mutation_routes_through_helper_with_marker():
    """Helper-mediated invariant, after the fail-safe-swap rewrite
    (fe83fbc9): the ENV-mutation logic moved OUT of inline
    deploy-prod.yml heredocs and INTO ``deploy/deploy_fail_safe.sh``,
    which mutates ``/etc/tinyassets/env`` *only* through
    ``deploy/install-tinyassets-env.sh``. The helper's
    ``assert_readable()`` emits the canonical ``ENV-UNREADABLE`` marker
    after every write. The invariant that prevents the 2026-04-21 P0
    perm-regression class is unchanged; only its location moved (and
    tightened to a single mutation site).

    The new cross-file invariant:
      1. the helper still carries the marker on its readability-fail path;
      2. deploy-prod.yml ships the helper to the droplet AND runs the
         fail-safe script (so the helper is present where the swap runs);
      3. the fail-safe script mutates the env file ONLY via the helper —
         no raw ``sed -i`` / ``>`` / ``tee`` write to ``$ENV_FILE`` that
         would bypass the perm-restore + marker path.
    """
    yaml_text = _DEPLOY_YAML.read_text(encoding="utf-8")
    helper_text = _ENV_HELPER.read_text(encoding="utf-8")
    failsafe_text = _FAILSAFE.read_text(encoding="utf-8")

    # (1) Helper carries the marker on its readability-fail path.
    assert CANONICAL_MARKER in helper_text, (
        f"deploy/install-tinyassets-env.sh must contain the canonical "
        f"{CANONICAL_MARKER!r} marker so post-write readability failures "
        f"surface in journalctl with the same token as the entrypoint."
    )

    # (2) deploy-prod.yml delivers the helper to the droplet AND runs the
    # fail-safe swap. Both are required: the fail-safe script hard-refuses
    # if the helper is not present (`ENV_HELPER missing`), so shipping it
    # is load-bearing, not incidental.
    assert "install-tinyassets-env.sh" in yaml_text, (
        "deploy-prod.yml must scp deploy/install-tinyassets-env.sh to the "
        "droplet so the fail-safe swap can mutate the env file through it."
    )
    assert "deploy_fail_safe.sh" in yaml_text, (
        "deploy-prod.yml must run deploy/deploy_fail_safe.sh (the fail-safe "
        "swap that replaced the inline stop-writer-fence heredocs)."
    )

    # (3) The fail-safe script mutates the env file ONLY through the helper.
    # It writes the image pin via `bash "$ENV_HELPER" set TINYASSETS_IMAGE`;
    # there must be no un-helpered write to $ENV_FILE that would bypass the
    # perm-restore + marker path and reintroduce the P0 perm-regression class.
    assert 'bash "$ENV_HELPER" set' in failsafe_text, (
        "deploy/deploy_fail_safe.sh must mutate the env file through the "
        "helper (`bash \"$ENV_HELPER\" set ...`), not via a raw write."
    )
    unhelpered_write = re.search(
        r'(?:sed\s+-i|>>?|tee)\s*[^\n]*\$(?:ENV_FILE|\{ENV_FILE\})',
        failsafe_text,
    )
    assert unhelpered_write is None, (
        f"deploy/deploy_fail_safe.sh writes to $ENV_FILE without going "
        f"through the helper: {unhelpered_write.group(0)!r}. Every env "
        f"mutation must route through install-tinyassets-env.sh so perms "
        f"are restored and the {CANONICAL_MARKER!r} marker fires — this is "
        f"the 2026-04-21 P0 perm-regression guard."
    )


def test_triage_detection_delegates_to_classifier_module():
    """The triage YAML must invoke `scripts/triage_classify.py` for
    outage detection. Task #11 moved ENV-UNREADABLE detection out of
    inline bash `grep -q` and into the classifier module — the invariant
    we care about is that the token is still canonical and still
    detected, which now means a cross-file check: the YAML invokes the
    classifier, AND the classifier regex matches the canonical token.
    """
    yaml_text = _TRIAGE_YAML.read_text(encoding="utf-8")
    assert "scripts/triage_classify.py" in yaml_text, (
        "triage workflow must delegate detection to triage_classify.py "
        "(Task #11 classifier replaces the inline grep pattern)"
    )
    # Cross-file check: classifier regex must cover the canonical token.
    classifier_path = Path(__file__).resolve().parent.parent / "scripts" / "triage_classify.py"
    classifier_text = classifier_path.read_text(encoding="utf-8")
    assert CANONICAL_MARKER in classifier_text, (
        f"{classifier_path.name} must contain the canonical "
        f"ENV-UNREADABLE token so env-unreadable outages get detected"
    )


def test_triage_auto_repair_runs_chown_chmod():
    text = _TRIAGE_YAML.read_text(encoding="utf-8")
    # The auto-repair must apply the exact mitigation.
    assert "chown root:tinyassets /etc/tinyassets/env" in text
    assert "chmod 640 /etc/tinyassets/env" in text


def test_triage_auto_repair_is_gated_on_env_class():
    """The repair must only run when the classifier reports
    `env_unreadable` — otherwise we'd apply the chown+chmod mitigation
    on every triage, including OOM/disk-full/image-pull/etc. outages
    where it's irrelevant.

    Task #11 moved the gate from inline bash `if ... grep -q` / `fi`
    to a YAML step-level `if: steps.classify.outputs.class ==
    'env_unreadable'`. The INTENT is the same (chown is conditional on
    env-unreadable detection); the mechanism changed. We walk the YAML
    to confirm the Repair-ENV step carries the class gate AND that the
    chown command only appears inside that step's `run:` block.
    """
    try:
        import yaml
    except ImportError:
        import pytest
        pytest.skip("pyyaml not installed")

    wf = yaml.safe_load(_TRIAGE_YAML.read_text(encoding="utf-8"))
    steps = wf["jobs"]["triage"]["steps"]

    env_repair_step = None
    for step in steps:
        name = step.get("name", "")
        if "ENV-UNREADABLE" in name or name.startswith("Repair — ENV-UNREADABLE"):
            env_repair_step = step
            break
    assert env_repair_step is not None, (
        "triage workflow must have a dedicated 'Repair — ENV-UNREADABLE' step"
    )

    # The step's `if:` must gate on the classifier class output.
    cond = str(env_repair_step.get("if", ""))
    assert "steps.classify.outputs.class" in cond and "env_unreadable" in cond, (
        f"ENV-UNREADABLE repair step's `if:` must gate on "
        f"steps.classify.outputs.class == 'env_unreadable'; got: {cond!r}"
    )

    # The chown command must live inside THIS step's run: block, not
    # leak into an unconditional step. Look for chown in every other
    # step's run body as a negative check.
    for other in steps:
        if other is env_repair_step:
            continue
        run = str(other.get("run", ""))
        # Allow chown in the `image_pull_failure` repair step too — that
        # step correctly maintains the Task #3 ENV-UNREADABLE invariant
        # after its own sed on /etc/tinyassets/env (cross-referential: if
        # the sed clobbers perms, chown+chmod+test-r restores them, and
        # the next triage tick's env_unreadable branch catches any miss).
        other_name = other.get("name", "")
        if "image pull failure" in other_name.lower() or \
           "image_pull_failure" in other_name.lower() or \
           "fall back to :latest" in other_name.lower():
            continue
        assert "chown root:tinyassets /etc/tinyassets/env" not in run, (
            f"chown root:tinyassets /etc/tinyassets/env leaked outside the "
            f"ENV-UNREADABLE gate into step {other.get('name')!r}"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
