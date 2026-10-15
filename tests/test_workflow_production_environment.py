"""Production workflows run only inside the main-only `production` environment.

Incident: a `workflow_dispatch` on a branch runs that branch's YAML, so a
job-level `if: github.ref == 'refs/heads/main'` guards nothing — the branch can
simply delete the line. Repo-level secrets are reachable from any ref, and run
38484527257 used them to scp branch code onto the production host as root.

The fix is structural: the production secrets live in a GitHub Environment
named `production` whose deployment branches are restricted to `main`
(docs/host-actions.md). A job only receives them when it declares
`environment: production`, and GitHub refuses that job on any other ref before
a single step runs.

This test is conservative: EVERY secret is a production secret unless it is on
the justified allowlist below, and a job also counts as production when it
opens a shell to a host (ssh/scp/rsync/sftp), inherits the caller's secrets,
or runs a repo-local action that reads a secret. Each such job must declare
`environment: production`. box-preflight.yml is held to a stricter contract:
one ssh connection whose remote command is a fixed literal that signals the
harness deploy-prod installed, so nothing from the checkout reaches the host.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOWS = _REPO / ".github" / "workflows"
_ACTIONS = _REPO / ".github" / "actions"

# The secrets the `production` environment must own (docs/host-actions.md row).
PRODUCTION_SECRETS = (
    "DO_SSH_KEY",
    "DO_DROPLET_HOST",
    "DO_SSH_USER",
    "DIGITALOCEAN_TOKEN",
    "DO_API_TOKEN",
)

# Secrets a job may read WITHOUT `environment: production`. A branch dispatch
# that reaches one of these cannot touch the production host or the
# DigitalOcean account. Every entry says why; an entry no workflow uses fails
# `test_allowlist_is_in_use`, so the list cannot accrete.
NON_PRODUCTION_SECRETS: dict[str, str] = {
    "GITHUB_TOKEN": "per-run token GitHub mints, scoped by `permissions:`; not a stored secret",
    "MERGE_ATTRIBUTION_TOKEN": "GitHub PAT that enqueues/attributes merges on github.com only",
    "TINYASSETS_WIKI_CANARY_TOKEN": (
        "bearer for the read-only `canary` service principal; the daemon 403s anything "
        "beyond get_status / read_graph status (docs/reference/environment-variables.md)"
    ),
    "TINYASSETS_CORE_CANARY_CONFIG": (
        "email and password of the dedicated synthetic canary owner; the deploy derives "
        "the HMAC key on the host. No founder data (docs/host-actions.md)"
    ),
    "PUSHOVER_APP_TOKEN": "send-only notification credential",
    "PUSHOVER_USER_KEY": "send-only notification recipient",
    "CLOUDFLARE_API_TOKEN": (
        "Cloudflare edge account (Worker + DNS): separate credential, no host shell; "
        "deploy-worker.yml dry-runs on pull_request so it cannot sit in a main-only "
        "environment. An edge environment is its own lane."
    ),
    "CLOUDFLARE_ACCOUNT_ID": "Cloudflare account identifier, not a credential",
    "CLOUDFLARE_ZONE_ID": "Cloudflare zone identifier, not a credential",
    # Store release pipelines: these sign and upload artifacts, never reach the host,
    # and run on tag/release refs that a main-only environment would refuse.
    "APPLE_DISTRIBUTION_CERT_P12_B64": (
        "iOS distribution certificate (ios-release.yml, `app-store` env)"
    ),
    "APPLE_DISTRIBUTION_CERT_PASSWORD": "password for APPLE_DISTRIBUTION_CERT_P12_B64",
    "APPLE_PROVISIONING_PROFILE_B64": "iOS provisioning profile",
    "APP_STORE_CONNECT_API_KEY_ID": "App Store Connect API key id",
    "APP_STORE_CONNECT_API_KEY_B64": "App Store Connect API private key",
    "APP_STORE_CONNECT_API_ISSUER_ID": "App Store Connect issuer id",
    "APP_STORE_REVIEW_USERNAME": "App Review demo account sign-in (review video)",
    "APP_STORE_REVIEW_PASSWORD": "App Review demo account password",
    "AWS_DEVICEFARM_ACCESS_KEY_ID": "AWS Device Farm (review video on real devices)",
    "AWS_DEVICEFARM_SECRET_ACCESS_KEY": "AWS Device Farm secret",
    "AWS_DEVICEFARM_PROJECT_ARN": "AWS Device Farm project identifier",
    "ANDROID_DEBUG_KEYSTORE_B64": "debug keystore for the sideload APK",
    "ANDROID_UPLOAD_KEYSTORE_B64": "Play upload keystore",
    "ANDROID_UPLOAD_KEYSTORE_PASSWORD": "password for the Play upload keystore",
    "ANDROID_UPLOAD_KEY_ALIAS": "alias inside the Play upload keystore",
    "ANDROID_UPLOAD_KEY_PASSWORD": "key password inside the Play upload keystore",
    "ANDROID_GOOGLE_SERVICES_JSON_B64": "Firebase client config baked into the Android build",
    "TOKEN_HEX": "python `secrets.token_hex(...)` in a run script",
}

# `secrets.NAME`, `secrets['NAME']`, `secrets["NAME"]`, `secrets[NAME]`,
# `secrets[format(...)]` — any key, any quoting. A dynamic index is reported as
# the raw expression, which is never on the allowlist.
_SECRET_REF = re.compile(
    r"\bsecrets\s*(?:\.\s*(?P<dot>[A-Za-z_][A-Za-z0-9_]*)"
    r"|\[\s*(?P<quote>['\"]?)(?P<idx>[^\]]*?)(?P=quote)\s*\])"
)
# The whole secrets context at once.
_SECRETS_CONTEXT = re.compile(r"\btoJSON\s*\(\s*secrets\s*\)", re.IGNORECASE)
# A remote shell or file copy at command position (`ssh-keyscan`, `#do-ssh-key`
# and `DO_SSH_KEY` do not match).
_HOST_SHELL = re.compile(r"(?:^|[\s;&|(`$])(ssh|scp|rsync|sftp)\s", re.MULTILINE)

# Workflows known to hold production jobs today. The scan below is the source of
# truth; this list only guards against the scan silently matching nothing.
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


def _secret_names(text: str) -> set[str]:
    names = {(m.group("dot") or m.group("idx")).strip() for m in _SECRET_REF.finditer(text)}
    if _SECRETS_CONTEXT.search(text):
        names.add("<toJSON(secrets)>")
    return names


def _production_secret_names(text: str) -> set[str]:
    return {n for n in _secret_names(text) if n.upper() not in NON_PRODUCTION_SECRETS}


def _run_scripts(job: dict) -> list[str]:
    return [str(step.get("run")) for step in job.get("steps") or [] if step.get("run")]


def _local_action_file(uses: str, repo: Path) -> Path | None:
    if not uses.startswith("./"):
        return None
    folder = repo / uses[2:]
    for name in ("action.yml", "action.yaml"):
        if (folder / name).exists():
            return folder / name
    return folder / "action.yml"  # missing file: reported by the caller


def production_reasons(job: dict, *, workflow_text: str = "", repo: Path = _REPO) -> list[str]:
    """Why `job` must run inside the production environment ([] if it need not).

    `workflow_text` is the dumped workflow-level `env:`/`defaults:`; a secret read
    there is visible to every job. `repo` resolves `./.github/actions/...` steps.
    """
    reasons: list[str] = []
    job_text = yaml.safe_dump(job)
    for name in sorted(_production_secret_names(workflow_text + "\n" + job_text)):
        reasons.append(f"reads secrets.{name}")
    if job.get("secrets") == "inherit":
        reasons.append("inherits every repo-level secret (`secrets: inherit`)")
    for script in _run_scripts(job):
        hit = _HOST_SHELL.search(script)
        if hit:
            reasons.append(f"opens a shell to a host (`{hit.group(1)}`)")
            break
    for step in job.get("steps") or []:
        uses = str(step.get("uses") or "")
        action = _local_action_file(uses, repo)
        if action is None:
            continue
        if not action.exists():
            reasons.append(f"uses {uses}, which does not exist")
            continue
        action_text = action.read_text(encoding="utf-8")
        for name in sorted(_production_secret_names(action_text)):
            reasons.append(f"uses {uses}, which reads secrets.{name}")
        if _HOST_SHELL.search(action_text):
            reasons.append(f"uses {uses}, which opens a shell to a host")
    return reasons


def _environment_name(job: dict) -> str | None:
    env = job.get("environment")
    if isinstance(env, dict):
        env = env.get("name")
    return env if isinstance(env, str) else None


def _workflow_level_text(data: dict) -> str:
    return yaml.safe_dump({"env": data.get("env") or {}, "defaults": data.get("defaults") or {}})


def _all_production_jobs() -> list[tuple[str, str, dict, list[str]]]:
    out = []
    for path in _workflow_files():
        data = _load(path)
        wf_text = _workflow_level_text(data)
        for name, job in (data.get("jobs") or {}).items():
            reasons = production_reasons(job, workflow_text=wf_text)
            if reasons:
                out.append((path.name, name, job, reasons))
    return out


_PRODUCTION_JOBS = _all_production_jobs()


def test_scan_finds_the_known_production_workflows():
    seen = {wf for wf, _, _, _ in _PRODUCTION_JOBS}
    missing = _KNOWN_PRODUCTION_WORKFLOWS - seen
    assert not missing, (
        f"secret scan no longer sees production jobs in {sorted(missing)}; "
        "if a workflow stopped touching production, drop it from "
        "_KNOWN_PRODUCTION_WORKFLOWS, otherwise the scan is broken"
    )


def test_allowlist_is_in_use_and_never_names_a_production_secret():
    used: set[str] = set()
    for path in _workflow_files():
        used |= {n.upper() for n in _secret_names(path.read_text(encoding="utf-8"))}
    for action in _ACTIONS.glob("*/action.y*ml"):
        used |= {n.upper() for n in _secret_names(action.read_text(encoding="utf-8"))}
    stale = set(NON_PRODUCTION_SECRETS) - used
    assert not stale, (
        f"allowlisted secrets no workflow reads any more; delete them: {sorted(stale)}"
    )
    leaked = set(NON_PRODUCTION_SECRETS) & set(PRODUCTION_SECRETS)
    assert not leaked, f"production secrets may never be allowlisted: {sorted(leaked)}"
    assert all(v.strip() for v in NON_PRODUCTION_SECRETS.values()), (
        "every allowlist entry is justified"
    )


@pytest.mark.parametrize(
    ("workflow", "job_name", "job", "reasons"),
    [row for row in _PRODUCTION_JOBS if "uses" not in row[2]],
    ids=[f"{wf}:{job}" for wf, job, j, _ in _PRODUCTION_JOBS if "uses" not in j],
)
def test_every_production_job_declares_the_production_environment(workflow, job_name, job, reasons):
    assert _environment_name(job) == "production", (
        f"{workflow} job `{job_name}` lacks `environment: production` but "
        + "; ".join(reasons)
        + ". An `if: github.ref == ...` guard is not a substitute: a branch dispatch runs "
        "the branch's YAML. If the secret truly cannot reach the host or the DigitalOcean "
        "account, allowlist it in NON_PRODUCTION_SECRETS with a one-line justification."
    )


def _reusable_caller_problems(
    path_name: str, job_name: str, job: dict, reasons: list[str]
) -> list[str]:
    """A `uses:` job cannot declare `environment:`; the equivalent guard is that it
    forwards nothing production-grade to a callee this test cannot see.

    `secrets: inherit` hands the callee every repo-level secret, so it is allowed
    only toward a workflow in this repository (whose jobs the scan above holds to
    the same rule). An explicit `secrets:` map or `with:` may carry allowlisted
    names only.
    """
    problems = []
    target = str(job.get("uses") or "")
    local = target.startswith("./.github/workflows/")
    if local and not (_REPO / target[2:]).exists():
        problems.append(f"{path_name} job `{job_name}` calls {target}, which does not exist")
    for reason in reasons:
        if reason.startswith("inherits"):
            if not local:
                problems.append(
                    f"{path_name} job `{job_name}` inherits secrets into {target}, a workflow "
                    "outside this repository that this test cannot scan"
                )
        else:
            problems.append(
                f"{path_name} job `{job_name}` is a reusable-workflow call that {reason}; "
                "a `uses:` job cannot declare `environment: production`, so it may forward "
                "allowlisted secrets only"
            )
    return problems


def test_reusable_workflow_callers_forward_no_production_secrets():
    problems = []
    for wf, name, job, reasons in _PRODUCTION_JOBS:
        if "uses" in job:
            problems += _reusable_caller_problems(wf, name, job, reasons)
    assert not problems, "\n".join(problems)


# --- classifier mutation table ----------------------------------------------

_BASE_JOB = {
    "runs-on": "ubuntu-latest",
    "steps": [{"uses": "actions/checkout@v4"}, {"run": "echo hello"}],
}


def _job(**overrides) -> dict:
    job = copy.deepcopy(_BASE_JOB)
    job.update(overrides)
    return job


_MUST_FLAG = {
    "dot": _job(env={"K": "${{ secrets.DO_SSH_KEY }}"}),
    "dot-lowercase": _job(env={"K": "${{ secrets.do_ssh_key }}"}),
    "bracket-single": _job(env={"K": "${{ secrets['DO_SSH_KEY'] }}"}),
    "bracket-double": _job(env={"K": '${{ secrets["DO_SSH_KEY"] }}'}),
    "bracket-bare": _job(env={"K": "${{ secrets[DO_SSH_KEY] }}"}),
    "bracket-dynamic": _job(env={"K": "${{ secrets[format('DO_{0}', matrix.k)] }}"}),
    "unknown-name": _job(env={"K": "${{ secrets.BRAND_NEW_SECRET }}"}),
    "tojson": _job(env={"ALL": "${{ toJSON(secrets) }}"}),
    "step-env": _job(steps=[{"run": "x", "env": {"K": "${{ secrets.DIGITALOCEAN_TOKEN }}"}}]),
    "step-with": _job(steps=[{"uses": "x/y@v1", "with": {"k": "${{ secrets.DO_API_TOKEN }}"}}]),
    "ssh": _job(steps=[{"run": "ssh user@host 'uptime'"}]),
    "ssh-piped": _job(steps=[{"run": "cat f | ssh user@host 'bash -s'"}]),
    "scp": _job(steps=[{"run": "set -e\nscp a.sh user@host:/tmp/"}]),
    "rsync": _job(steps=[{"run": "rsync -a . user@host:/opt/"}]),
    "sftp": _job(steps=[{"run": "sftp user@host <<EOF\nput x\nEOF"}]),
    "inherit": _job(secrets="inherit"),
}
_MUST_NOT_FLAG = {
    "no-secrets": _job(),
    "github-token": _job(env={"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}),
    "allowlisted-bracket": _job(env={"T": "${{ secrets['MERGE_ATTRIBUTION_TOKEN'] }}"}),
    "python-secrets-module": _job(
        steps=[{"run": "python -c 'import secrets; secrets.token_hex(8)'"}]
    ),
    "ssh-keyscan-only": _job(steps=[{"run": "ssh-keyscan example.org >> ~/.ssh/known_hosts"}]),
    "ssh-in-a-word": _job(steps=[{"run": 'echo "see runbook.md#do-ssh-key"'}]),
    "secret-as-yaml-key": _job(steps=[{"run": "echo secrets: rotated"}]),
}


@pytest.mark.parametrize("case", sorted(_MUST_FLAG), ids=sorted(_MUST_FLAG))
def test_classifier_flags(case):
    assert production_reasons(_MUST_FLAG[case]), case


@pytest.mark.parametrize("case", sorted(_MUST_NOT_FLAG), ids=sorted(_MUST_NOT_FLAG))
def test_classifier_passes(case):
    assert production_reasons(_MUST_NOT_FLAG[case]) == [], case


def test_workflow_level_env_counts_for_every_job():
    wf_text = _workflow_level_text({"env": {"H": "${{ secrets.DO_DROPLET_HOST }}"}})
    assert production_reasons(_job(), workflow_text=wf_text)


def test_local_action_reading_a_production_secret_counts(tmp_path):
    action = tmp_path / ".github" / "actions" / "probe" / "action.yml"
    action.parent.mkdir(parents=True)
    job = _job(steps=[{"uses": "./.github/actions/probe"}])
    action.write_text(
        "runs:\n  using: composite\n  steps:\n    - run: echo ${{ secrets.DO_SSH_KEY }}\n",
        encoding="utf-8",
    )
    assert any("reads secrets.DO_SSH_KEY" in r for r in production_reasons(job, repo=tmp_path))
    action.write_text(
        "runs:\n  using: composite\n  steps:\n    - run: echo ${{ inputs.token }}\n",
        encoding="utf-8",
    )
    assert production_reasons(job, repo=tmp_path) == []
    missing = _job(steps=[{"uses": "./.github/actions/absent"}])
    assert production_reasons(missing, repo=tmp_path)


def test_reusable_caller_rules():
    inherit_local = _job(uses="./.github/workflows/ios-release.yml", secrets="inherit")
    assert (
        _reusable_caller_problems("w", "j", inherit_local, production_reasons(inherit_local)) == []
    )
    inherit_remote = _job(uses="other/repo/.github/workflows/x.yml@main", secrets="inherit")
    assert _reusable_caller_problems("w", "j", inherit_remote, production_reasons(inherit_remote))
    missing = _job(uses="./.github/workflows/does-not-exist.yml", secrets="inherit")
    assert _reusable_caller_problems("w", "j", missing, production_reasons(missing))
    forwards = _job(
        uses="./.github/workflows/ios-release.yml", secrets={"key": "${{ secrets.DO_SSH_KEY }}"}
    )
    assert _reusable_caller_problems("w", "j", forwards, production_reasons(forwards))
    forwards_ok = _job(
        uses="./.github/workflows/ios-release.yml",
        secrets={"t": "${{ secrets.GITHUB_TOKEN }}"},
    )
    assert _reusable_caller_problems("w", "j", forwards_ok, production_reasons(forwards_ok)) == []


# --- box-preflight.yml: one connection, one fixed literal --------------------

_BOX_PREFLIGHT = _WORKFLOWS / "box-preflight.yml"
_RESULT_PATH = "/run/tinyassets-boxhostd/preflight.json"
# The only command the production host ever receives from this workflow. It
# signals the harness deploy-prod installed, waits for its result file and
# prints it. Changing it means changing this constant in the same review.
EXPECTED_REMOTE_COMMAND = (
    "sudo systemctl is-active --quiet boxhostd.service"
    f" && sudo rm -f {_RESULT_PATH}"
    " && sudo systemctl kill --kill-who=main -s USR1 boxhostd.service"
    f" && for _ in $(seq 1 240); do sudo test -s {_RESULT_PATH} && break; sleep 10; done"
    f" && sudo cat {_RESULT_PATH}"
)
_HOST_ARG = '"${DO_SSH_USER}@${DO_DROPLET_HOST}"'
# Anything else that could move bytes to or from the host from a run script.
_NETWORK_TOOL = re.compile(
    r"(?:^|[\s;&|(`$])(scp|rsync|sftp|curl|wget|nc|ncat|socat|telnet|ftp|openssl)\s",
    re.MULTILINE,
)
_SSH_LINE = re.compile(
    r"^\s*ssh\s+(?P<opts>(?:-[A-Za-z]\s+\S+\s+|-[A-Za-z]+\s+)*)"
    r"(?P<host>\"[^\"]*\")\s+'(?P<remote>[^']*)'(?P<tail>[^\n]*)$",
    re.MULTILINE,
)


def _join_continuations(script: str) -> str:
    return re.sub(r"\\\n\s*", " ", script)


def box_preflight_problems(data: dict) -> list[str]:
    """Everything wrong with a parsed box-preflight.yml ([] when it is sound)."""
    problems: list[str] = []
    jobs = data.get("jobs") or {}
    if set(jobs) != {"measure"}:
        return [f"box-preflight has one job `measure`; found {sorted(jobs)}"]
    job = jobs["measure"]
    if _environment_name(job) != "production":
        problems.append("measure lacks `environment: production`")

    for step in job.get("steps") or []:
        uses = step.get("uses")
        if uses and not str(uses).startswith(("actions/checkout@", "actions/upload-artifact@")):
            problems.append(f"uses {uses!r}; only checkout and upload-artifact are allowed")

    scripts = [_join_continuations(s) for s in _run_scripts(job)]
    if not scripts:
        return problems + ["measure has no run steps"]
    for script in scripts:
        hit = _NETWORK_TOOL.search(script)
        if hit:
            problems.append(f"runs `{hit.group(1)}`, which could move files to or from the host")

    ssh_lines = [
        line
        for s in scripts
        for line in s.splitlines()
        if re.search(r"(?:^|[ 	;&|(`$])ssh\s", line)
    ]
    if len(ssh_lines) != 1:
        problems.append(f"expected exactly one ssh invocation, found {len(ssh_lines)}")
        return problems
    line = ssh_lines[0]
    m = _SSH_LINE.match(line)
    if not m:
        return problems + [
            'the ssh invocation must be `ssh <flags> "${DO_SSH_USER}@${DO_DROPLET_HOST}" '
            f"'<single-quoted literal>' > box-preflight-results.json`, got: {line.strip()!r}"
        ]
    opts = m.group("opts").split()
    if "-n" not in opts:
        problems.append("ssh must run with -n (stdin from /dev/null) so no local file is piped in")
    if "BatchMode=yes" not in m.group("opts"):
        problems.append("ssh must run with -o BatchMode=yes")
    if m.group("host") != _HOST_ARG:
        problems.append(f"ssh host argument must be {_HOST_ARG}, got {m.group('host')}")
    remote = " ".join(m.group("remote").split())
    if remote != EXPECTED_REMOTE_COMMAND:
        problems.append(
            "the remote command is not the allowlisted boxhostd trigger:\n"
            f"  got:      {remote}\n  expected: {EXPECTED_REMOTE_COMMAND}"
        )
    tail = m.group("tail").strip()
    if not re.fullmatch(r">\s*box-preflight-results\.json(\s*\|\|\s*status=\$\?)?", tail):
        problems.append(
            f"only `> box-preflight-results.json || status=$?` may follow the literal, got {tail!r}"
        )
    if "<" in line.split("'", 1)[0]:
        problems.append("nothing may be redirected into ssh's stdin")
    return problems


def test_box_preflight_signals_the_installed_harness_and_nothing_else():
    problems = box_preflight_problems(_load(_BOX_PREFLIGHT))
    assert not problems, (
        "box-preflight.yml may only signal boxhostd.service, the harness deploy-prod "
        "installed, over one ssh connection with a fixed literal:\n" + "\n".join(problems)
    )


def _box_text() -> str:
    return _BOX_PREFLIGHT.read_text(encoding="utf-8")


def _mutate(text: str, old: str, new: str) -> dict:
    assert old in text, f"mutation anchor missing from box-preflight.yml: {old!r}"
    return yaml.safe_load(text.replace(old, new, 1))


_TRIGGER = "sudo systemctl kill --kill-who=main -s USR1 boxhostd.service"
_BOX_MUST_FAIL = {
    "drop-environment": ("    environment: production\n", ""),
    "wrong-environment": ("environment: production", "environment: staging"),
    "scp-step": (
        "      - name: Gate summary",
        "      - run: scp -i ~/.ssh/do_deploy scripts/x.sh "
        '"${DO_SSH_USER}@${DO_DROPLET_HOST}:/tmp/"\n'
        "      - name: Gate summary",
    ),
    "rsync-step": (
        "      - name: Gate summary",
        '      - run: rsync -a deploy/ "${DO_SSH_USER}@${DO_DROPLET_HOST}:/opt/tinyassets/"\n'
        "      - name: Gate summary",
    ),
    "curl-to-host": (
        "      - name: Gate summary",
        '      - run: curl -X POST "http://${DO_DROPLET_HOST}:8080/hooks/inbound" '
        "-d @results.json\n"
        "      - name: Gate summary",
    ),
    "second-ssh": (
        "      - name: Gate summary",
        "      - run: ssh -n -i ~/.ssh/do_deploy -o BatchMode=yes "
        "\"${DO_SSH_USER}@${DO_DROPLET_HOST}\" 'uptime'\n"
        "      - name: Gate summary",
    ),
    "remote-runs-checkout-script": (_TRIGGER, "bash -s < scripts/box_preflight.sh"),
    "remote-other-unit": (_TRIGGER, "sudo systemctl restart tinyassets.service"),
    "remote-extra-command": (_TRIGGER, _TRIGGER + " && sudo rm -rf /opt/tinyassets"),
    "remote-reads-other-path": (
        "sudo cat /run/tinyassets-boxhostd/preflight.json",
        "sudo cat /etc/tinyassets/env",
    ),
    "double-quoted-remote": ("'sudo systemctl is-active", '"sudo systemctl is-active'),
    "stdin-from-file": ("ssh -n ", "ssh "),
    "no-batchmode": ("-o BatchMode=yes ", ""),
    "other-host": (_HOST_ARG, '"root@203.0.113.7"'),
    "third-party-action": ("uses: actions/checkout@v4", "uses: appleboy/ssh-action@v1"),
    "extra-job": (
        "jobs:\n  measure:",
        "jobs:\n  copy:\n    runs-on: ubuntu-latest\n    steps: []\n  measure:",
    ),
    "output-elsewhere": (
        "> box-preflight-results.json",
        "> box-preflight-results.json; cat results.json | tee /dev/stderr",
    ),
}
_BOX_MUST_PASS = {
    "longer-step-timeout": ("timeout-minutes: 40", "timeout-minutes: 44"),
    "extra-local-step": (
        "      - name: Gate summary",
        "      - run: echo local-only\n      - name: Gate summary",
    ),
    "artifact-retention": ("retention-days: 30", "retention-days: 7"),
    "keepalive-tuning": ("ServerAliveCountMax=6", "ServerAliveCountMax=8"),
}


@pytest.mark.parametrize("case", sorted(_BOX_MUST_FAIL), ids=sorted(_BOX_MUST_FAIL))
def test_box_preflight_guard_rejects(case):
    old, new = _BOX_MUST_FAIL[case]
    assert box_preflight_problems(_mutate(_box_text(), old, new)), case


@pytest.mark.parametrize("case", sorted(_BOX_MUST_PASS), ids=sorted(_BOX_MUST_PASS))
def test_box_preflight_guard_accepts_unrelated_edits(case):
    old, new = _BOX_MUST_PASS[case]
    assert box_preflight_problems(_mutate(_box_text(), old, new)) == [], case
