"""sandbox-template.yml spends money on E2B, so its shape is held: dispatch only, on main,
in `production`, one run at a time, the key in its build step alone, every action and
package pinned, no token left in the checkout, and a tag E2B already built never built
again."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/sandbox-template.yml"
LOCK = ROOT / "platform/sandbox/template/requirements.txt"
DEPLOY = ROOT / "platform/deploy/deploy.ts"


def _workflow() -> tuple[str, dict, dict]:
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    return text, workflow, workflow["jobs"]["template"]


def test_dispatch_only_on_main_in_production_one_at_a_time() -> None:
    _, workflow, job = _workflow()
    assert list(workflow.get("on", workflow.get(True))) == ["workflow_dispatch"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["environment"] == "production"
    assert workflow["concurrency"] == {"group": "sandbox-template", "cancel-in-progress": False}


def test_the_key_is_in_the_build_step_alone() -> None:
    text, _, job = _workflow()
    assert re.findall(r"secrets\.([A-Z0-9_]+)", text) == ["E2B_API_KEY"]
    holders = [step["name"] for step in job["steps"]
               if "secrets." in str(step.get("env", {}))]
    assert holders == ["Build and check"]


def test_actions_and_packages_are_pinned_and_no_token_is_kept() -> None:
    _, _, job = _workflow()
    uses = [step["uses"] for step in job["steps"] if "uses" in step]
    assert uses and all(re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", u) for u in uses)
    checkout = next(step for step in job["steps"] if "checkout" in step.get("uses", ""))
    assert checkout["with"]["persist-credentials"] is False
    runs = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "--require-hashes -r platform/sandbox/template/requirements.txt" in runs
    pins = [line for line in LOCK.read_text(encoding="utf-8").splitlines()
            if re.match(r"^[a-z0-9_.-]+==", line, re.I)]
    assert any(line.startswith("e2b==") for line in pins)
    assert all(line.endswith("\\") for line in pins)


@pytest.mark.parametrize(
    ("mode", "ready", "code", "calls"),
    [
        ("build", "false", 0, ["--tag-only", "node templateReady", ""]),
        ("build", "true", 0, ["--tag-only", "node templateReady"]),
        ("build", "", 1, ["--tag-only", "node templateReady"]),
        ("pair", "false", 0, ["--check-pair"]),
    ],
)
def test_a_tag_e2b_already_built_is_not_built_again(tmp_path, mode, ready, code, calls) -> None:
    """The build step's own script, run with E2B's answer stubbed: it builds only when E2B
    has no ready build of the tag, and an unreadable answer stops it."""
    _, _, job = _workflow()
    script = next(step for step in job["steps"] if step.get("name") == "Build and check")["run"]
    log = tmp_path / "calls"
    (tmp_path / "python3").write_text(
        f'#!/bin/sh\necho "${{2:-}}" >> {log}\n'
        '[ "${2:-}" = --tag-only ] && echo tinyassets-sandbox:s0\nexit 0\n')
    (tmp_path / "node").write_text(
        f'#!/bin/sh\ncase "$*" in *templateReady*) echo "node templateReady" >> {log};; esac\n'
        'echo "$READY"\n')
    for stub in ("python3", "node"):
        (tmp_path / stub).chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "MODE": mode, "READY": ready,
           "GITHUB_STEP_SUMMARY": str(tmp_path / "summary")}
    done = subprocess.run(["bash", "-c", script], env=env, cwd=ROOT, capture_output=True,
                          text=True, check=False)
    assert done.returncode == code, done.stderr
    assert (log.read_text().splitlines() if log.exists() else []) == calls
    # The check the script imports is the deploy kit's own.
    assert "export async function templateReady(" in DEPLOY.read_text(encoding="utf-8")
