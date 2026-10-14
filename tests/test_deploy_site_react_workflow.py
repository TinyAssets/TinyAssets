"""The production site publishes itself when its source lands on main."""

from __future__ import annotations

from pathlib import Path

import yaml

_WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy-site-react.yml"
)

# The folders the site build reads, plus this workflow so a change to the
# publisher itself publishes the tree on main.
_PUSH_PATHS = [
    "WebSite/site-react/**",
    "WebSite/design-system/**",
    "WebSite/shared/**",
    ".github/workflows/deploy-site-react.yml",
]


def _load() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _triggers(wf: dict) -> dict:
    return wf[True] if True in wf else wf["on"]


def test_site_source_on_main_publishes_and_a_manual_run_still_confirms() -> None:
    triggers = _triggers(_load())
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["push"]["paths"] == _PUSH_PATHS
    assert "confirm" in triggers["workflow_dispatch"]["inputs"]
    jobs = _load()["jobs"]
    assert jobs["guard"]["if"] == "github.event_name == 'workflow_dispatch'"
    assert "needs.guard.result == 'skipped'" in jobs["build"]["if"]
    assert jobs["deploy"]["needs"] == "build"
    # Manual runs still refuse anything except confirm=deploy. The step
    # exits before the build when the typed value is not that word.
    confirm = jobs["guard"]["steps"][0]
    assert confirm["if"] == "${{ github.event.inputs.confirm != 'deploy' }}"
    concurrency = _load()["concurrency"]
    assert concurrency["group"] == "deploy-site-react"
    assert concurrency["cancel-in-progress"] is False


def test_per_pr_preview_worker_pipeline_is_absent() -> None:
    """The Cloudflare preview worker never published; it must not return."""
    root = Path(__file__).resolve().parents[1]
    absent = (
        ".github/workflows/preview-worker.yml",
        ".github/workflows/preview-worker-deploy.yml",
        ".github/workflows/preview-security.yml",
        "WebSite/site-react/wrangler.preview.toml",
        "WebSite/site-react/cf-worker/worker.js",
    )
    present = [path for path in absent if (root / path).exists()]
    assert not present, present


def test_push_does_not_skip_deploy_when_build_succeeds() -> None:
    """A skipped guard must not skip deploy after a successful build.

    GitHub skips every job downstream of a skipped job, through jobs that
    already opted back in, unless the downstream job's own if contains a
    status function (``always()``, ``cancelled()``, or ``failure()``) and
    then requires the result it actually needs. ``success()`` does not opt
    out: it is false for the whole ancestor chain once any ancestor was
    skipped, so a bare ``needs.build.result == 'success'`` would still skip
    deploy on every push. build already uses ``!cancelled()`` plus an
    explicit result check, and that is what let build run on run
    38320052898 while deploy, which had no if, was skipped.
    """
    deploy_if = _load()["jobs"]["deploy"]["if"]
    assert "success()" not in deploy_if
    assert "cancelled()" in deploy_if
    assert "needs.build.result == 'success'" in deploy_if
    assert deploy_if == "${{ !cancelled() && needs.build.result == 'success' }}"
