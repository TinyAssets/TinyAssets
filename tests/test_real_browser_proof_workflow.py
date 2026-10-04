"""Shape invariants for .github/workflows/real-browser-proof.yml.

The job exists because every Playwright-gated proof (the custom-UI form cases
in ``tests/test_custom_ui_forms_browser.py``) is SKIPPED by ``required-tests``,
which installs no browser, and a skip is invisible in a green run. Each
assertion pins a property whose loss would turn the job back into decoration
or widen it past cloud-only test infrastructure. The assertion helper it shares
with linux-jail-proof is exercised in ``tests/test_linux_jail_proof_workflow.py``.

PyYAML is imported hard: skipping this file is how the invariants would go quiet.
"""

from __future__ import annotations

import functools
import importlib.util
import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO / ".github" / "workflows" / "real-browser-proof.yml"
_SCRIPT = _REPO / "scripts" / "ci_assert_junit_case.py"
_JOB = "real-browser-proof"

_spec = importlib.util.spec_from_file_location("ci_assert_junit_case", _SCRIPT)
assert _spec and _spec.loader
_assert = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_assert)


@functools.cache
def _marked() -> list[str]:
    # Inside tests only: collecting the marked cases imports test modules.
    return _assert.marked_cases(_REPO, "real_browser")


def _load() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _triggers(wf: dict) -> dict:
    return wf[True] if True in wf else wf["on"]


def _job(wf: dict) -> dict:
    assert list(wf["jobs"]) == [_JOB]
    return wf["jobs"][_JOB]


def _step(wf: dict, needle: str) -> dict:
    hits = [s for s in _job(wf)["steps"]
            if needle in (s.get("name") or "") or needle in (s.get("uses") or "")]
    assert len(hits) == 1, f"expected one step matching {needle!r}, got {len(hits)}"
    return hits[0]


def test_triggers_are_pull_request_paths_plus_dispatch_only():
    triggers = _triggers(_load())
    assert set(triggers) == {"pull_request", "workflow_dispatch"}
    paths = triggers["pull_request"]["paths"]
    for required in (
        ".github/workflows/real-browser-proof.yml",
        "scripts/ci_assert_junit_case.py",
        "tinyassets/onboarding/app_ui.js",
        "tinyassets/onboarding/ui_frame.py",
    ):
        assert required in paths, f"{required} must retrigger the proof"


def test_every_literal_trigger_path_exists():
    for path in _triggers(_load())["pull_request"]["paths"]:
        if not any(ch in path for ch in "*?["):
            assert (_REPO / path).exists(), f"trigger path {path} does not exist"


def test_read_only_hosted_and_no_persisted_credentials():
    wf = _load()
    assert wf["permissions"] == {"contents": "read"}
    job = _job(wf)
    assert "permissions" not in job and "environment" not in job
    assert job["runs-on"] == "ubuntu-latest"
    assert _step(wf, "actions/checkout@")["with"]["persist-credentials"] is False
    assert "secrets." not in _WORKFLOW.read_text(encoding="utf-8")


def test_run_blocks_never_interpolate_expressions():
    for step in _job(_load())["steps"]:
        assert "${{" not in (step.get("run") or ""), step.get("name")


def test_the_marker_is_registered_and_carried_by_the_form_proofs():
    pyproject = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert '"real_browser:' in pyproject
    files = {n.split("::")[0] for n in _marked()}
    assert "tests/test_custom_ui_forms_browser.py" in files, files


def test_every_marked_file_retriggers_the_proof():
    paths = set(_triggers(_load())["pull_request"]["paths"])
    for path in sorted({n.split("::")[0] for n in _marked()}):
        assert path in paths, f"{path} carries real_browser but does not retrigger the proof"


def test_the_browser_is_installed_from_the_pinned_extra():
    wf = _load()
    assert "'.[dev,browser]'" in _step(wf, "Install the project")["run"]
    # Chromium itself is in the oracle image, where the proofs run.
    assert "playwright install --with-deps chromium" in (
        _REPO / "docker" / "linux-oracle.Dockerfile"
    ).read_text(encoding="utf-8")
    assert all("playwright install" not in s.get("run", "") for s in _job(wf)["steps"])
    pyproject = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'browser = \[\s*"playwright==\d+\.\d+\.\d+"', pyproject)


def test_assertion_step_always_runs_over_the_marker():
    wf = _load()
    step = _step(wf, "Assert every real-browser case")
    assert step["if"] == "always()"
    assert "--marker real_browser" in step["run"]
    run_step = _step(wf, "Run the real-browser proofs")
    assert "-m real_browser" in run_step["run"]
    assert step["env"]["JUNIT_PATH"] == run_step["env"]["OUT_DIR"] + "/junit-real-browser.xml"


def test_junit_uploaded_even_on_failure():
    step = _step(_load(), "Upload junit")
    assert step["if"] == "always()"
    assert step["with"]["path"].endswith("/junit-real-browser.xml")


def test_not_the_required_context():
    assert _job(_load())["name"] == _JOB != "required-tests"


def _code(steps: list[dict]) -> str:
    return "\n".join(
        ln for s in steps for ln in s.get("run", "").splitlines()
        if not ln.lstrip().startswith("#")
    )


def test_the_proofs_run_in_the_jail_proofs_venue_and_nowhere_weaker():
    wf = _load()
    steps = _job(wf)["steps"]
    profile = _step(wf, "Allow user namespaces for the jail container only")
    jail = yaml.safe_load(
        (_REPO / ".github/workflows/linux-jail-proof.yml").read_text(encoding="utf-8")
    )
    expected = next(s for s in jail["jobs"]["linux-jail-proof"]["steps"]
                    if s.get("name") == profile["name"])
    # The SAME named profile, byte for byte; not a second, wider one.
    assert profile == expected
    assert "if" not in profile and not profile.get("continue-on-error", False)
    assert steps.index(profile) < steps.index(_step(wf, "Run the real-browser proofs"))
    for name in ("Run the real-browser proofs", "Run the complete preview containment module"):
        step = _step(wf, name)
        run = step["run"]
        assert 'python scripts/linux_oracle.py --out "$OUT_DIR" --apparmor ta-jail-userns' in run
        assert "--env TINYASSETS_DATA_DIR=/tmp/ta-data" in run
        assert "--basetemp /tmp/b" in run
        assert "set -euo pipefail" in run
        assert "if" not in step and not step.get("continue-on-error", False)
        for weaker in ("--no-bwrap", "--as-root", "--shell", "|| true", "python -m pytest"):
            assert weaker not in run, weaker
    code = _code(steps)
    for forbidden in ("sysctl", "--privileged", "--cap-add", "--no-sandbox", "docker run"):
        assert forbidden not in code, forbidden
    assert "secrets." not in _WORKFLOW.read_text(encoding="utf-8")
    assert code.count("sudo") == 1 and "sudo apparmor_parser -r" in code  # the profile load
    paths = _triggers(wf)["pull_request"]["paths"]
    assert "scripts/linux_oracle.py" in paths and "docker/linux-oracle.Dockerfile" in paths


def test_ordinary_pull_requests_still_trigger_it():
    assert _job(_load())["if"] == (
        "github.event_name != 'pull_request' || github.event.pull_request.draft == false"
    )


def test_the_whole_preview_module_runs_and_every_collected_case_is_asserted():
    wf = _load()
    run = _step(wf, "Run the complete preview containment module")
    assert "-- tests/test_ui_preview.py -q" in run["run"]
    pytest_args = run["run"].split("-- tests/test_ui_preview.py", 1)[1]
    assert " -m " not in pytest_args and " -k " not in pytest_args
    assert "--junitxml /out/junit-preview.xml" in pytest_args
    check = _step(wf, "Assert every preview case executed")
    assert check["if"] == "always()"
    assert "tests/test_ui_preview.py --collect-only" in check["run"]
    assert '"${#cases[@]}" -eq 0' in check["run"] and "exit 1" in check["run"]
    assert 'args+=(--nodeid "$case")' in check["run"]
    assert 'ci_assert_junit_case.py --junit "$JUNIT_PATH"' in check["run"]
    assert "|| true" not in check["run"] and not check.get("continue-on-error", False)
    assert check["env"]["JUNIT_PATH"] == run["env"]["OUT_DIR"] + "/junit-preview.xml"
    artifact = _step(wf, "Upload preview junit")
    assert artifact["if"] == "always()"
    assert artifact["with"]["path"].endswith("/junit-preview.xml")
