"""sandbox-template.yml spends money on E2B, so its shape is held: dispatch only, on main,
in `production`, one run at a time, the key in its build step alone, every action and
package pinned, no token left in the checkout, a tag E2B already built never built again
but still checked and recorded, and no upload that is not a new record."""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/sandbox-template.yml"
LOCK = ROOT / "platform/sandbox/template/requirements.txt"
DEPLOY = ROOT / "platform/deploy/deploy.ts"
BUILD = ROOT / "platform/sandbox/template/build.py"


def _workflow() -> tuple[str, dict, dict]:
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    return text, workflow, workflow["jobs"]["template"]


def _step(name: str) -> dict:
    return next(step for step in _workflow()[2]["steps"] if step.get("name") == name)


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
        ("build", "true", 0, ["--tag-only", "node templateReady", "checks tinyassets-sandbox:s0"]),
        ("build", "", 1, ["--tag-only", "node templateReady"]),
        ("pair", "false", 0, ["--check-pair"]),
    ],
)
def test_a_tag_e2b_already_built_is_not_built_again(tmp_path, mode, ready, code, calls) -> None:
    """The build step's own script, run with E2B's answer stubbed: it builds only when E2B
    has no ready build of the tag, runs the checks alone on one it has, and an unreadable
    answer stops it."""
    script = _step("Build and check")["run"]
    log = tmp_path / "calls"
    (tmp_path / "python3").write_text(
        f'#!/bin/sh\n[ "$1" = -c ] && {{ echo "checks $3" >> {log}; exit 0; }}\n'
        f'echo "${{2:-}}" >> {log}\n'
        '[ "${2:-}" = --tag-only ] && echo tinyassets-sandbox:s0\nexit 0\n')
    (tmp_path / "node").write_text(
        f'#!/bin/sh\ncase "$*" in *templateReady*) echo "node templateReady" >> {log};; esac\n'
        'echo "$READY"\n')
    for stub in ("python3", "node"):
        (tmp_path / stub).chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "MODE": mode, "READY": ready,
           "CHECKS": _step("Build and check")["env"]["CHECKS"],
           "GITHUB_STEP_SUMMARY": str(tmp_path / "summary")}
    done = subprocess.run(["bash", "-c", script], env=env, cwd=ROOT, capture_output=True,
                          text=True, check=False)
    assert done.returncode == code, done.stderr
    assert (log.read_text().splitlines() if log.exists() else []) == calls
    # The check the script imports is the deploy kit's own.
    assert "export async function templateReady(" in DEPLOY.read_text(encoding="utf-8")


_FAKE_BUILD = '''
import json, os
from pathlib import Path
NAME = "tinyassets-sandbox"
PLATFORM = Path(__file__).resolve().parent.parent.parent
LOG = PLATFORM.parent / "calls"
def _log(*what):
    with open(LOG, "a") as out:
        out.write(" ".join(map(str, what)) + "\\n")
def tag():
    _log("tag", os.getcwd() == str(PLATFORM))
    return "s0"
def bundle(out):
    _log("bundle")
    return b"runner"
def check_logins(name, runner):
    _log("check_logins", name)
    return ["logins"]
def route_probe(name, runner):
    _log("route_probe", name)
    return ["routes"]
def check_matcher(name, runner):
    _log("check_matcher", name)
    return {"install": {"ms": 1}}
def record_pass(name, runner, logins, routes, matcher):
    _log("record_pass", name, runner.decode(), logins, routes, matcher)
'''


def _run_checks(tmp_path: Path, name: str) -> subprocess.CompletedProcess:
    template = tmp_path / "platform" / "sandbox" / "template"
    template.mkdir(parents=True)
    (template / "build.py").write_text(_FAKE_BUILD)
    return subprocess.run(["python3", "-c", _step("Build and check")["env"]["CHECKS"], name],
                          cwd=tmp_path, capture_output=True, text=True, check=False)


def test_the_checks_alone_run_build_pys_checks_and_record_on_this_trees_tag(tmp_path) -> None:
    """CHECKS drives build.py with no build: no e2b import (the fake tree has none), its
    three checks and its record on this tree's tag, from build.py's working directory."""
    done = _run_checks(tmp_path, "tinyassets-sandbox:s0")
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "calls").read_text().splitlines() == [
        "tag True", "bundle",
        "check_logins tinyassets-sandbox:s0", "route_probe tinyassets-sandbox:s0",
        "check_matcher tinyassets-sandbox:s0",
        "record_pass tinyassets-sandbox:s0 runner ['logins'] ['routes'] {'install': {'ms': 1}}",
    ]
    assert '"template": "tinyassets-sandbox:s0"' in done.stdout


def test_the_checks_alone_refuse_a_tag_that_is_not_this_trees(tmp_path) -> None:
    done = _run_checks(tmp_path, "tinyassets-sandbox:s1")
    assert done.returncode != 0
    assert "this tree's template is tinyassets-sandbox:s0" in done.stderr
    assert "check_logins" not in (tmp_path / "calls").read_text()


def _build_calls(tree: ast.AST) -> list[str]:
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr != "tag"
             and isinstance(node.func.value, ast.Name) and node.func.value.id == "build"]
    calls.sort(key=lambda node: (node.lineno, node.col_offset))
    return [node.func.attr for node in calls]


def test_the_checks_alone_are_build_pys_own_sequence_after_its_build() -> None:
    """CHECKS follows main()'s calls after Template.build, so the two cannot drift."""
    module = ast.parse(BUILD.read_text(encoding="utf-8"))
    main = next(node for node in module.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    # The calls that make and check a pair (the tag's own call sits in an f-string).
    local = {node.name for node in module.body if isinstance(node, ast.FunctionDef)} - {"tag"}
    calls = [node for node in ast.walk(main) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id in local]
    calls.sort(key=lambda node: (node.lineno, node.col_offset))
    names = [node.func.id for node in calls]
    after_build = names[len(names) - 1 - names[::-1].index("check_logins"):]
    driven = _build_calls(ast.parse(_step("Build and check")["env"]["CHECKS"]))
    assert after_build == ["check_logins", "route_probe", "check_matcher", "record_pass"]
    assert driven[driven.index("check_logins"):] == after_build
    assert "bundle" in names and driven.index("bundle") < driven.index("check_logins")


@pytest.mark.parametrize(("changed", "fresh", "code"), [(False, "no", 0), (True, "yes", 0),
                                                         (None, None, 1)])
def test_only_a_new_record_is_uploaded(tmp_path, changed, fresh, code) -> None:
    record = _step("Did this run record a pass")
    upload = _step("Keep the record of what passed")
    assert record["id"] == "record" and record["if"] == "always()"
    assert upload["if"] == "always() && steps.record.outputs.fresh == 'yes'"
    repo = tmp_path / "repo"
    passed = repo / "platform/sandbox/template/passed.json"
    passed.parent.mkdir(parents=True)
    passed.write_text("[]\n")
    if changed is not None:
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git[:3], "init", "-q"], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-qm", "base"], check=True)
        if changed:
            passed.write_text('[{"tag": "s0"}]\n')
    out = tmp_path / "out"
    done = subprocess.run(["bash", "-e", "-c", record["run"]], cwd=repo, capture_output=True,
                          text=True, check=False,
                          env={"PATH": "/usr/bin:/bin", "GITHUB_OUTPUT": str(out),
                               "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
                               "GIT_CEILING_DIRECTORIES": str(tmp_path)})
    assert done.returncode == code, done.stderr
    assert (out.read_text().strip() if out.exists() else None) == (
        None if fresh is None else f"fresh={fresh}")
