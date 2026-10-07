"""Executable contract and load proofs for release reconciliation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "release-reconcile.yml"
_BUILD_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "build-image.yml"
_REGRESSION_WORKFLOW = (
    _REPO_ROOT / ".github" / "workflows" / "release-reconcile-regression.yml"
)
_WINDOWS_GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
_BASH = (
    str(_WINDOWS_GIT_BASH)
    if _WINDOWS_GIT_BASH.exists()
    else shutil.which("bash")
)


def _load(path: Path = _WORKFLOW) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # PyYAML 1.1 parses the unquoted GitHub Actions `on` key as boolean true.
    return workflow.get(True, workflow.get("on", {})) or {}


def _compact(expression: str) -> str:
    return " ".join(expression.split())


def _step_script(name: str) -> str:
    step = next(
        step
        for step in _load()["jobs"]["reconcile"]["steps"]
        if step.get("name") == name
    )
    return str(step["run"])


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _release_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "tinyassets").mkdir()
    (repo / ".github" / "workflows" / "build-image.yml").write_text(
        """name: Build and publish image
on:
  push:
    paths:
      - 'tinyassets/**'
""",
        encoding="utf-8",
    )
    (repo / "tinyassets" / "app.py").write_text("VERSION = 1\n", encoding="utf-8")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "proof@example.invalid")
    _git(repo, "config", "user.name", "Release Proof")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "release one")
    stale_sha = _git(repo, "rev-parse", "HEAD")
    (repo / "tinyassets" / "app.py").write_text("VERSION = 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "release two")
    relevant_sha = _git(repo, "rev-parse", "HEAD")
    return repo, stale_sha, relevant_sha


_GH_DECISION_HARNESS = r"""
# Windows Python writes CRLF; the runner's python3 does not. Without the tr a
# path read as `pyproject.toml\r` silently matches nothing (and `tinyassets/**\r`
# still matches, by accident), so a Windows run would prove the wrong thing.
# pipefail (set by the step) keeps python's exit status.
python3() { "${PYTHON_BIN}" "$@" | tr -d '\r'; }
git() {
  if [ "$1" = "log" ] && [ "${GIT_LOG_STATUS:-0}" != "0" ]; then
    return "${GIT_LOG_STATUS}"
  fi
  if [ "$1" = "rev-parse" ] && [ "${GIT_REV_PARSE_STATUS:-0}" != "0" ]; then
    return "${GIT_REV_PARSE_STATUS}"
  fi
  command git "$@"
}
gh() {
  case "$*" in
    *"deploy-prod.yml/runs?branch=main&per_page=100"*)
      if [ "${RETRY_API_FAIL:-0}" = "1" ]; then return 1; fi
      printf '%s\n' "${RETRY_RUNS_JSON:-}"
      ;;
    *"actions/workflows/deploy-prod.yml/runs"*)
      if [ "${DEPLOY_API_FAIL:-0}" = "1" ]; then return 1; fi
      printf '%s\n' "${DEPLOYED_SHAS:-}"
      ;;
    *"actions/runs?branch=main"*)
      if [ "${ACTIVE_API_FAIL:-0}" = "1" ]; then return 1; fi
      printf '%s\n' "${ACTIVE_RUNS_JSON:-}"
      ;;
    *)
      echo "unexpected gh call: $*" >&2
      return 97
      ;;
  esac
}
"""


def _run_decision(
    repo: Path,
    *,
    active_runs: list[dict[str, object]] | None = None,
    retry_runs: list[dict[str, object]] | None = None,
    deployed_shas: str = "",
    active_api_fail: bool = False,
    deploy_api_fail: bool = False,
    retry_api_fail: bool = False,
    git_log_status: int = 0,
    git_rev_parse_status: int = 0,
) -> dict[str, str]:
    if not _BASH:
        pytest.skip("bash is required to execute the exact reconciliation script")
    output = repo / ".github-output"
    decision_script = _step_script("Compare main against the last successful deploy")
    shell_script = f"{_GH_DECISION_HARNESS}\n{decision_script}"
    result = subprocess.run(
        [_BASH, "-s"],
        cwd=repo,
        check=False,
        capture_output=True,
        input=shell_script,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "ACTIVE_RUNS_JSON": json.dumps(
                {"workflow_runs": active_runs or []}
            ),
            "DEPLOYED_SHAS": deployed_shas,
            "ACTIVE_API_FAIL": "1" if active_api_fail else "0",
            "DEPLOY_API_FAIL": "1" if deploy_api_fail else "0",
            "GITHUB_OUTPUT": ".github-output",
            "GIT_LOG_STATUS": str(git_log_status),
            "GIT_REV_PARSE_STATUS": str(git_rev_parse_status),
            "PYTHON_BIN": Path(sys.executable).as_posix(),
            "REPO": "owner/repo",
            "RETRY_API_FAIL": "1" if retry_api_fail else "0",
            "RETRY_RUNS_JSON": json.dumps(
                {"workflow_runs": retry_runs or []}
            ),
        },
    )
    assert result.returncode == 0, result.stderr
    values: dict[str, str] = {}
    for line in output.read_text(encoding="utf-8").splitlines():
        key, value = line.split("=", 1)
        values[key] = value
    return values


_GH_CONVERGE_HARNESS = r"""
python3() { "${PYTHON_BIN}" "$@"; }
gh() {
  printf '%s\n' "$*" >> "${GH_CALLS}"
  if [ "$1" = "run" ] && [ "$2" = "list" ]; then
    printf '%s\n' "${BUILD_RUNS_JSON}"
  elif [ "$1" = "run" ] && [ "$2" = "watch" ]; then
    return "${WATCH_STATUS:-0}"
  elif [ "$1" = "run" ] && [ "$2" = "view" ]; then
    printf '%s\n' "${WATCH_CONCLUSION}"
  elif [ "$1" = "api" ] && [[ "$*" == *"deploy-prod.yml/runs"* ]]; then
    printf '%s\n' "${DEPLOY_RUNS_JSON}"
  elif [ "$1" = "api" ] && [[ "$*" == *"/commits/main"* ]]; then
    printf '%s\n' "${CURRENT_MAIN}"
  fi
}
sleep() { :; }
"""


def _run_converge(
    repo: Path,
    current_main: str,
    *,
    build_runs: list[dict[str, object]] | None = None,
    deploy_runs: list[dict[str, object]] | None = None,
    watch_status: int = 0,
    watch_conclusion: str = "success",
    expected_returncode: int = 0,
) -> list[str]:
    if not _BASH:
        pytest.skip("bash is required to execute the exact convergence script")
    target_sha = _git(repo, "rev-parse", "HEAD")
    if build_runs is None:
        build_runs = [
            {
                "databaseId": 111,
                "headSha": "0" * 40,
                "createdAt": "9999-01-03T00:00:00Z",
            },
            {
                "databaseId": 222,
                "headSha": target_sha,
                "createdAt": "9999-01-01T00:00:00Z",
            },
            {
                "databaseId": 12345,
                "headSha": target_sha,
                "createdAt": "9999-01-02T00:00:00Z",
            },
        ]
    if deploy_runs is None:
        deploy_runs = []
    result = subprocess.run(
        [_BASH, "-s"],
        cwd=repo,
        check=False,
        capture_output=True,
        input=f"{_GH_CONVERGE_HARNESS}\n{_step_script('Converge')}",
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "BUILD_RUNS_JSON": json.dumps(build_runs),
            "CURRENT_MAIN": current_main,
            "DETAIL": "test drift",
            "DEPLOY_RUNS_JSON": json.dumps({"workflow_runs": deploy_runs}),
            "GH_CALLS": ".gh-calls",
            "GITHUB_STEP_SUMMARY": ".summary",
            "PYTHON_BIN": Path(sys.executable).as_posix(),
            "REPO": "owner/repo",
            "WATCH_CONCLUSION": watch_conclusion,
            "WATCH_STATUS": str(watch_status),
        },
    )
    assert result.returncode == expected_returncode, (
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return (repo / ".gh-calls").read_text(encoding="utf-8").splitlines()


def _active_run(
    sha: str,
    *,
    status: str = "in_progress",
    path: str = ".github/workflows/build-image.yml",
    event: str = "push",
    conclusion: str | None = None,
    run_id: int = 123,
) -> dict[str, object]:
    return {
        "conclusion": conclusion,
        "event": event,
        "head_sha": sha,
        "id": run_id,
        "path": path,
        "status": status,
    }


def _one_running_one_replaceable_pending(arrivals: list[int]) -> list[int]:
    assert arrivals
    running: int | None = None
    pending: int | None = None
    for arrival in arrivals:
        if running is None:
            running = arrival
        else:
            pending = arrival
    assert running is not None
    return [running] + ([pending] if pending is not None else [])


def test_retains_backstops_and_wakes_on_completed_main_docker_smoke() -> None:
    triggers = _triggers(_load())

    assert triggers.get("schedule") == [{"cron": "*/15 * * * *"}]
    assert "workflow_dispatch" in triggers
    assert triggers.get("workflow_dispatch") is None
    assert triggers.get("workflow_run") == {
        "workflows": ["Docker build smoke"],
        "types": ["completed"],
        "branches": ["main"],
    }


def test_privileged_job_condition_is_exact_trusted_provenance_contract() -> None:
    condition = _compact(str(_load()["jobs"]["reconcile"].get("if", "")))

    assert condition == (
        "github.event_name != 'workflow_run' || ( "
        "github.event.workflow_run.conclusion == 'success' && "
        "github.event.workflow_run.head_branch == 'main' && "
        "github.event.workflow_run.head_repository.full_name == github.repository && "
        "( github.event.workflow_run.event == 'push' || "
        "github.event.workflow_run.event == 'workflow_dispatch' ) )"
    )


def test_permissions_checkout_and_reconcile_concurrency_remain_narrow() -> None:
    workflow = _load()

    assert workflow["permissions"] == {"contents": "read", "actions": "write"}
    assert workflow["concurrency"] == {
        "group": "release-reconcile",
        "cancel-in-progress": False,
    }
    checkout = next(
        step
        for step in workflow["jobs"]["reconcile"]["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == "main"
    assert "github.event.workflow_run.head_sha" not in _WORKFLOW.read_text(
        encoding="utf-8"
    )


def test_operator_summary_distinguishes_deferred_from_in_sync() -> None:
    steps = _load()["jobs"]["reconcile"]["steps"]
    converge = next(step for step in steps if step.get("name") == "Converge")
    deferred = next(step for step in steps if step.get("name") == "Deferred")
    in_sync = next(step for step in steps if step.get("name") == "In sync")

    assert converge["if"] == "steps.check.outputs.action == 'dispatch'"
    assert deferred["if"] == "steps.check.outputs.action == 'defer'"
    assert in_sync["if"] == "steps.check.outputs.action == 'none'"
    assert "No release decision" in deferred["run"]
    assert "Production is current" in in_sync["run"]


def test_converge_embedded_python_is_valid_on_production_python_312() -> None:
    script = _step_script("Converge")

    assert script.count("python3 -c '\n") == 2
    assert script.count("python3 -c '\nimport ") == 2


def test_python_312_release_reconcile_regression_runs_in_ci() -> None:
    workflow = _load(_REGRESSION_WORKFLOW)
    steps = workflow["jobs"]["test"]["steps"]
    setup_python = next(
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/setup-python@")
    )
    install = next(
        step
        for step in steps
        if step.get("name") == "Install focused test dependencies"
    )

    assert setup_python["with"]["python-version"] == "3.12"
    assert "-e ." in _compact(str(install["run"]))
    assert any(
        "tests/test_release_reconcile_workflow.py" in _compact(str(step.get("run", "")))
        for step in steps
    )


def test_build_image_concurrency_declares_push_only_cancellation() -> None:
    build_workflow = _load(_BUILD_WORKFLOW)

    assert build_workflow["concurrency"] == {
        "group": "build-image-${{ github.ref }}",
        "cancel-in-progress": "${{ github.event_name == 'push' }}",
    }


def test_exact_decision_defers_for_current_active_release(tmp_path: Path) -> None:
    repo, _, relevant_sha = _release_repo(tmp_path)

    result = _run_decision(repo, active_runs=[_active_run(relevant_sha)])

    assert result["action"] == "defer"
    assert "in-flight release run" in result["detail"]


def test_exact_decision_stale_active_does_not_suppress(tmp_path: Path) -> None:
    repo, stale_sha, _ = _release_repo(tmp_path)

    result = _run_decision(repo, active_runs=[_active_run(stale_sha)])

    assert result == {
        "action": "dispatch",
        "detail": "no successful deploy to main on record",
    }


def test_exact_decision_caps_failed_manual_deploy_retry_before_rebuilding(
    tmp_path: Path,
) -> None:
    repo, _, relevant_sha = _release_repo(tmp_path)

    result = _run_decision(
        repo,
        retry_runs=[
            _active_run(
                relevant_sha,
                status="completed",
                path=".github/workflows/deploy-prod.yml",
                event="workflow_dispatch",
                conclusion="failure",
                run_id=67890,
            )
        ],
    )

    assert result["action"] == "defer"
    assert result["detail"] == (
        "automatic deploy retry 67890 already failed for "
        f"{relevant_sha[:8]} — awaiting newer main"
    )


def test_exact_decision_success_overrides_older_failed_manual_retry(
    tmp_path: Path,
) -> None:
    repo, _, relevant_sha = _release_repo(tmp_path)

    result = _run_decision(
        repo,
        retry_runs=[
            _active_run(
                relevant_sha,
                status="completed",
                path=".github/workflows/deploy-prod.yml",
                event="workflow_dispatch",
                conclusion="failure",
                run_id=67890,
            ),
            _active_run(
                relevant_sha,
                status="completed",
                path=".github/workflows/deploy-prod.yml",
                event="workflow_dispatch",
                conclusion="success",
                run_id=67891,
            ),
        ],
        deployed_shas=relevant_sha,
    )

    assert result["action"] == "none"
    assert result["detail"] == (
        f"deploy {relevant_sha[:8]} contains {relevant_sha[:8]}"
    )


def test_exact_decision_a_displaced_manual_deploy_is_not_a_failed_retry(
    tmp_path: Path,
) -> None:
    """A deploy that waits up to 45 min for in-flight turns makes a pending
    manual deploy easy to displace from the shared group. That cancellation must
    not read as the exhausted retry, or the sha never deploys (Codex, #turn-handover)."""
    repo, _, relevant_sha = _release_repo(tmp_path)

    result = _run_decision(
        repo,
        retry_runs=[
            _active_run(
                relevant_sha,
                status="completed",
                path=".github/workflows/deploy-prod.yml",
                event="workflow_dispatch",
                conclusion="cancelled",
                run_id=67890,
            )
        ],
    )

    assert result["action"] == "dispatch"


def test_completed_and_unrelated_runs_do_not_suppress_recovery(
    tmp_path: Path,
) -> None:
    repo, _, relevant_sha = _release_repo(tmp_path)

    result = _run_decision(
        repo,
        active_runs=[
            _active_run(relevant_sha, status="completed"),
            _active_run(
                relevant_sha,
                path=".github/workflows/uptime-canary.yml",
            ),
        ],
    )

    assert result["action"] == "dispatch"


@pytest.mark.parametrize("failed_query", ["active", "retry", "deploy"])
def test_exact_decision_api_failure_fails_closed(
    tmp_path: Path, failed_query: str
) -> None:
    repo, _, _ = _release_repo(tmp_path)

    result = _run_decision(
        repo,
        active_api_fail=failed_query == "active",
        deploy_api_fail=failed_query == "deploy",
        retry_api_fail=failed_query == "retry",
    )

    assert result["action"] == "defer"
    assert "query failed" in result["detail"]


def test_exact_decision_git_history_failure_defers_with_warning_state(
    tmp_path: Path,
) -> None:
    repo, _, _ = _release_repo(tmp_path)

    result = _run_decision(repo, git_log_status=55)

    assert result["action"] == "defer"
    assert result["detail"] == "release-history query failed — no decision made"


def test_exact_decision_rev_parse_failure_defers_when_path_list_is_unreadable(
    tmp_path: Path,
) -> None:
    repo, _, _ = _release_repo(tmp_path)
    (repo / ".github" / "workflows" / "build-image.yml").unlink()

    result = _run_decision(repo, git_rev_parse_status=56)

    assert result["action"] == "defer"
    assert result["detail"] == "release-history query failed — no decision made"


def test_exact_decision_empty_release_history_is_not_reported_in_sync(
    tmp_path: Path,
) -> None:
    repo, _, _ = _release_repo(tmp_path)
    (repo / ".github" / "workflows" / "build-image.yml").write_text(
        """name: Build and publish image
on:
  push:
    paths:
      - 'never-created/**'
""",
        encoding="utf-8",
    )

    result = _run_decision(repo)

    assert result["action"] == "defer"
    assert result["detail"] == "no release-relevant history — no decision made"


def test_exact_decision_successful_deploy_ancestry_is_in_sync(
    tmp_path: Path,
) -> None:
    repo, _, relevant_sha = _release_repo(tmp_path)

    result = _run_decision(repo, deployed_shas=relevant_sha)

    assert result["action"] == "none"
    assert result["detail"].startswith("deploy ")


def test_exact_decision_missing_deploy_dispatches(tmp_path: Path) -> None:
    repo, _, _ = _release_repo(tmp_path)

    result = _run_decision(repo)

    assert result["action"] == "dispatch"


def test_exact_scripts_coalesce_thousand_arrivals_to_one_dispatch(
    tmp_path: Path,
) -> None:
    repo, _, relevant_sha = _release_repo(tmp_path)
    arrivals = list(range(1_000))
    executed = _one_running_one_replaceable_pending(arrivals)
    shared_active_runs: list[dict[str, str]] = []
    actions: list[str] = []
    for _arrival in executed:
        decision = _run_decision(repo, active_runs=shared_active_runs)
        actions.append(decision["action"])
        if decision["action"] == "dispatch":
            shared_active_runs = [_active_run(relevant_sha)]

    assert executed == [0, 999]
    assert actions == ["dispatch", "defer"]
    assert actions.count("dispatch") == 1


def test_exact_converge_waits_for_build_then_deploys_unchanged_main(
    tmp_path: Path,
) -> None:
    repo, _, relevant_sha = _release_repo(tmp_path)

    calls = _run_converge(repo, current_main=relevant_sha)

    assert any(
        call == "workflow run build-image.yml --repo owner/repo --ref main"
        for call in calls
    )
    assert any(call.startswith("run list ") for call in calls)
    assert "run watch 12345 --repo owner/repo --exit-status" in calls
    assert (
        f"workflow run deploy-prod.yml --repo owner/repo --ref main "
        f"-f image_tag={relevant_sha[:12]}"
    ) in calls


def test_exact_converge_rejects_predispatch_same_sha_run(
    tmp_path: Path,
) -> None:
    repo, _, relevant_sha = _release_repo(tmp_path)

    calls = _run_converge(
        repo,
        current_main=relevant_sha,
        build_runs=[
            {
                "databaseId": 444,
                "headSha": relevant_sha,
                "createdAt": "2000-01-01T00:00:00Z",
            }
        ],
        expected_returncode=1,
    )

    assert sum(call.startswith("run list ") for call in calls) == 12
    assert not any(call.startswith("run watch ") for call in calls)
    assert not any("workflow run deploy-prod.yml" in call for call in calls)


def test_exact_converge_does_not_deploy_after_main_advances(
    tmp_path: Path,
) -> None:
    repo, _, _ = _release_repo(tmp_path)

    calls = _run_converge(repo, current_main="f" * 40)

    assert "run watch 12345 --repo owner/repo --exit-status" in calls
    assert not any("workflow run deploy-prod.yml" in call for call in calls)


def test_exact_converge_does_not_duplicate_active_deploy(
    tmp_path: Path,
) -> None:
    repo, _, relevant_sha = _release_repo(tmp_path)

    calls = _run_converge(
        repo,
        current_main=relevant_sha,
        deploy_runs=[
            {
                "id": 67890,
                "head_sha": relevant_sha,
                "status": "in_progress",
                "conclusion": None,
            }
        ],
    )

    assert any("deploy-prod.yml/runs" in call for call in calls)
    assert not any("workflow run deploy-prod.yml" in call for call in calls)


@pytest.mark.parametrize(
    ("event", "status", "conclusion", "expect_dispatch"),
    [
        ("workflow_run", "completed", "failure", True),
        ("workflow_dispatch", "completed", "failure", False),
        # Displaced from the shared concurrency group while pending: it never
        # tried, so it must not count as the one failed retry.
        ("workflow_dispatch", "completed", "cancelled", True),
        ("workflow_run", "completed", "success", False),
    ],
)
def test_exact_converge_completed_deploy_state_controls_retry(
    tmp_path: Path,
    event: str,
    status: str,
    conclusion: str,
    expect_dispatch: bool,
) -> None:
    repo, _, relevant_sha = _release_repo(tmp_path)

    calls = _run_converge(
        repo,
        current_main=relevant_sha,
        deploy_runs=[
            {
                "id": 67890,
                "event": event,
                "head_sha": relevant_sha,
                "status": status,
                "conclusion": conclusion,
            },
        ],
    )

    dispatched = any("workflow run deploy-prod.yml" in call for call in calls)
    assert dispatched is expect_dispatch


def test_exact_converge_cancelled_build_defers_without_deploy(
    tmp_path: Path,
) -> None:
    repo, _, relevant_sha = _release_repo(tmp_path)

    calls = _run_converge(
        repo,
        current_main=relevant_sha,
        watch_status=1,
        watch_conclusion="cancelled",
    )

    assert "run view 12345 --repo owner/repo --json conclusion --jq .conclusion" in calls
    assert not any("workflow run deploy-prod.yml" in call for call in calls)


def test_exact_converge_failed_build_stays_visible(tmp_path: Path) -> None:
    repo, _, relevant_sha = _release_repo(tmp_path)

    calls = _run_converge(
        repo,
        current_main=relevant_sha,
        watch_status=1,
        watch_conclusion="failure",
        expected_returncode=1,
    )

    assert "run view 12345 --repo owner/repo --json conclusion --jq .conclusion" in calls
    assert not any("workflow run deploy-prod.yml" in call for call in calls)


def test_exact_converge_main_advance_during_discovery_defers(
    tmp_path: Path,
) -> None:
    repo, _, _ = _release_repo(tmp_path)

    calls = _run_converge(
        repo,
        current_main="f" * 40,
        build_runs=[],
    )

    assert not any(call.startswith("run watch ") for call in calls)
    assert not any("workflow run deploy-prod.yml" in call for call in calls)


# --- the drift backstop uses the deploy chain's runtime classifier ----------
#
# build-image.yml cancels itself for a head that is runtime-equivalent to
# production (scripts/runtime_paths.py), so such a head is never deployed. If
# this backstop kept judging drift by the coarse path filter alone, every merge
# the filter hits but the classifier clears would read as drift and be rebuilt
# and redeployed on the next tick -- the very recreate the skip exists to avoid.
#
# `scripts/**` is the coarse pattern here: the fixture ships
# scripts/unrelated_tool.py, which no Dockerfile COPY, host manifest entry or
# deploy-chain workflow names, so it is a path hit that changes no runtime input.


def _runtime_release_repo(tmp_path: Path, *, classifier: str | None = None):
    from tests.runtime_repo_fixture import make_repo

    build_image = """name: Build and publish image
on:
  push:
    paths:
      - 'scripts/**'
      - 'tinyassets/**'
"""
    script = (
        classifier
        if classifier is not None
        else (_REPO_ROOT / "scripts" / "runtime_paths.py").read_text(encoding="utf-8")
    )
    return make_repo(
        tmp_path / "runtime-repo",
        {
            ".github/workflows/build-image.yml": build_image,
            "scripts/runtime_paths.py": script,
        },
    )


def test_a_coarse_path_hit_that_changes_no_runtime_input_is_in_sync(
    tmp_path: Path,
) -> None:
    """The newest commit the path filter hits changes no runtime input, so the
    refinement walks back to the deployed runtime commit: no drift."""
    repo, _ = _runtime_release_repo(tmp_path)
    runtime = repo.commit("code", {"tinyassets/app.py": "VERSION = 2\n"})
    repo.commit("tool", {"scripts/unrelated_tool.py": "TOOL = 2\n"})

    result = _run_decision(repo.root, deployed_shas=runtime)

    assert result["action"] == "none", result
    assert runtime[:8] in result["detail"]


def test_a_later_runtime_merge_is_still_drift(tmp_path: Path) -> None:
    """The refinement must not swallow a genuine runtime commit that landed
    after the deployed one."""
    repo, _ = _runtime_release_repo(tmp_path)
    runtime = repo.commit("code", {"tinyassets/app.py": "VERSION = 2\n"})
    later = repo.commit("more code", {"tinyassets/app.py": "VERSION = 3\n"})

    result = _run_decision(repo.root, deployed_shas=runtime)

    assert result["action"] == "dispatch"
    assert later[:8] in result["detail"]


def test_docs_merge_after_an_undeployed_runtime_merge_is_drift(
    tmp_path: Path,
) -> None:
    repo, base = _runtime_release_repo(tmp_path)
    runtime = repo.commit("code", {"tinyassets/app.py": "VERSION = 2\n"})
    repo.commit("docs", {"docs/notes.md": "more\n"})

    result = _run_decision(repo.root, deployed_shas=base)

    assert result["action"] == "dispatch"
    assert runtime[:8] in result["detail"]


def test_a_failing_classifier_keeps_the_coarse_commit_and_dispatches(
    tmp_path: Path,
) -> None:
    repo, _ = _runtime_release_repo(
        tmp_path, classifier="import sys\nsys.exit(3)\n"
    )
    runtime = repo.commit("code", {"tinyassets/app.py": "VERSION = 2\n"})
    coarse = repo.commit("tool", {"scripts/unrelated_tool.py": "TOOL = 2\n"})

    result = _run_decision(repo.root, deployed_shas=runtime)

    assert result["action"] == "dispatch"
    assert coarse[:8] in result["detail"]
