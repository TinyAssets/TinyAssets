"""Production workflows run only inside the main-only `production` environment.

Incident: a `workflow_dispatch` on a branch runs that branch's YAML, so a
job-level `if: github.ref == 'refs/heads/main'` guards nothing — the branch can
simply delete the line. Repo-level secrets are reachable from any ref, and run
38484527257 used them to scp branch code onto the production host as root.

The fix is structural: the production secrets live in a GitHub Environment
named `production` whose deployment branches are restricted to `main`
(docs/host-actions.md). A job only receives them when it declares
`environment: production`, and GitHub refuses that job on any other ref before
a single step runs. This test keeps every job that touches those secrets inside
the environment, and keeps box-preflight.yml from copying checkout files to the
host (it may only signal the harness deploy-prod installed).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOWS = _REPO / ".github" / "workflows"

# Any secret that reaches the production host or the DigitalOcean account.
PRODUCTION_SECRETS = (
    "DO_SSH_KEY",
    "DO_DROPLET_HOST",
    "DO_SSH_USER",
    "DIGITALOCEAN_TOKEN",
    "DO_API_TOKEN",
)
_SECRET_REF = re.compile(r"secrets\.(" + "|".join(PRODUCTION_SECRETS) + r")\b")

# Workflows known to hold production jobs today. The scan below is the source of
# truth; this list only guards against the regex silently matching nothing.
_KNOWN_PRODUCTION_WORKFLOWS = {
    "apply-daemon-env.yml",
    "box-preflight.yml",
    "cloud-only-preflight.yml",
    "deploy-prod.yml",
    "diagnose-prod-startup.yml",
    "dr-drill.yml",
    "droplet-resize.yml",
    "fetch-prod-logs.yml",
    "install-host-services.yml",
    "p0-outage-triage.yml",
    "restart-daemon.yml",
}


def _workflow_files() -> list[Path]:
    return sorted(list(_WORKFLOWS.glob("*.yml")) + list(_WORKFLOWS.glob("*.yaml")))


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name}: not a mapping"
    return data


def _production_jobs(path: Path) -> dict[str, dict]:
    """Jobs in `path` that can see a production secret.

    A workflow-level `env:` that reads a secret exposes it to every job, so in
    that case every job counts.
    """
    data = _load(path)
    jobs = data.get("jobs") or {}
    workflow_env_refs = bool(_SECRET_REF.search(yaml.safe_dump(data.get("env") or {})))
    found: dict[str, dict] = {}
    for name, job in jobs.items():
        if workflow_env_refs or _SECRET_REF.search(yaml.safe_dump(job)):
            found[name] = job
    return found


def _environment_name(job: dict) -> str | None:
    env = job.get("environment")
    if isinstance(env, dict):
        env = env.get("name")
    return env if isinstance(env, str) else None


def _all_production_jobs() -> list[tuple[str, str, dict]]:
    out = []
    for path in _workflow_files():
        for name, job in _production_jobs(path).items():
            out.append((path.name, name, job))
    return out


_PRODUCTION_JOBS = _all_production_jobs()


def test_scan_finds_the_known_production_workflows():
    seen = {wf for wf, _, _ in _PRODUCTION_JOBS}
    missing = _KNOWN_PRODUCTION_WORKFLOWS - seen
    assert not missing, (
        f"secret scan no longer sees production secrets in {sorted(missing)}; "
        "if a workflow stopped touching production, drop it from "
        "_KNOWN_PRODUCTION_WORKFLOWS, otherwise the regex is broken"
    )


@pytest.mark.parametrize(
    ("workflow", "job_name", "job"),
    _PRODUCTION_JOBS,
    ids=[f"{wf}:{job}" for wf, job, _ in _PRODUCTION_JOBS],
)
def test_every_production_job_declares_the_production_environment(workflow, job_name, job):
    assert _environment_name(job) == "production", (
        f"{workflow} job `{job_name}` reads a production secret "
        f"({', '.join(PRODUCTION_SECRETS)}) but lacks `environment: production`. "
        "An `if: github.ref == ...` guard is not a substitute: a branch dispatch runs "
        "the branch's YAML. The environment is restricted to main by GitHub itself."
    )


def test_no_reusable_workflow_forwards_production_secrets():
    """`secrets: inherit` or an explicit secrets map on a `uses:` job would hand
    the production secrets to a callee whose jobs this test cannot see."""
    for path in _workflow_files():
        data = _load(path)
        for name, job in (data.get("jobs") or {}).items():
            if "uses" not in job:
                continue
            secrets = job.get("secrets")
            if secrets == "inherit":
                assert not _production_jobs(path), (
                    f"{path.name} job `{name}` inherits secrets into a reusable workflow"
                )
            elif isinstance(secrets, dict):
                assert not _SECRET_REF.search(yaml.safe_dump(secrets)), (
                    f"{path.name} job `{name}` forwards a production secret to a reusable workflow"
                )


# --- box-preflight.yml: signal the installed harness only -------------------

_COPY_TO_HOST = re.compile(
    r"(?m)"
    r"(^|[\s;&|(])(scp|rsync|sftp)\b"  # file copy tools
    r"|\bssh\b[^\n]*<\s*[^<&(\s]"  # stdin redirect of a local file into ssh
    r"|\b(ba)?sh\s+-s\b"  # remote shell reading a script from stdin
    r"|\bbase64\b[^\n]*\|\s*[^\n]*\bssh\b"  # piping encoded local content to ssh
)


def _run_scripts(job: dict) -> list[str]:
    return [str(step.get("run")) for step in job.get("steps") or [] if step.get("run")]


def test_box_preflight_never_copies_checkout_files_to_the_host():
    data = _load(_WORKFLOWS / "box-preflight.yml")
    jobs = data["jobs"]
    assert set(jobs) == {"measure"}, "box-preflight has one job; new jobs need the same review"
    job = jobs["measure"]
    assert _environment_name(job) == "production"

    scripts = _run_scripts(job)
    assert scripts, "measure job has no run steps"
    for script in scripts:
        hit = _COPY_TO_HOST.search(script)
        assert hit is None, (
            "box-preflight.yml copies checkout files to the production host "
            f"({hit.group(0).strip()!r}). It may only signal boxhostd.service, the "
            "harness deploy-prod installed, and read its result back."
        )

    # The only remote commands are: signal the installed harness, poll for and
    # read its result. Nothing on the host is sourced from this checkout.
    remote = "\n".join(s for s in scripts if "ssh " in s)
    assert "boxhostd.service" in remote, "measure must drive the installed boxhostd harness"
    assert "/run/tinyassets-boxhostd/preflight.json" in remote

    # Steps that run third-party or repo-local actions could also move files;
    # the only `uses:` allowed are GitHub's own checkout and artifact upload.
    for step in job.get("steps") or []:
        uses = step.get("uses")
        if uses:
            assert uses.startswith(("actions/checkout@", "actions/upload-artifact@")), (
                f"box-preflight.yml uses {uses!r}; only checkout and upload-artifact are allowed"
            )
