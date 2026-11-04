"""box-preflight.yml signals the installed harness over one ssh connection, nothing else.

The production-environment rule every workflow is held to (a job that reads a production
secret, opens a host shell or inherits secrets declares `environment: production`) lives in
platform/ci/guards/environment.test.ts, where it survives the cutover. This contract is the
old host's and leaves with it: one connection whose remote command is a fixed literal that
signals the harness deploy-prod installed, so nothing from the checkout reaches the host.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOWS = _REPO / ".github" / "workflows"


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name}: not a mapping"
    return data


def _run_scripts(job: dict) -> list[str]:
    return [str(step.get("run")) for step in job.get("steps") or [] if step.get("run")]


def _environment_name(job: dict) -> str | None:
    env = job.get("environment")
    if isinstance(env, dict):
        env = env.get("name")
    return env if isinstance(env, str) else None


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


pytestmark = pytest.mark.verifies(
    'uptime-and-alarms:'
    'Digest-Pinned Deploy Admission Rollback And Receipt',
)
