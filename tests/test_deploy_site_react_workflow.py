"""The production site publishes itself when its source lands on main."""

from __future__ import annotations

from pathlib import Path

import yaml

_WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy-site-react.yml"
)

# The folders the site build reads. preview-worker.yml uses the same three.
_SITE_PATHS = [
    "WebSite/site-react/**",
    "WebSite/design-system/**",
    "WebSite/shared/**",
]


def _load() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _triggers(wf: dict) -> dict:
    return wf[True] if True in wf else wf["on"]


def test_site_source_on_main_publishes_and_a_manual_run_still_confirms() -> None:
    triggers = _triggers(_load())
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["push"]["paths"] == _SITE_PATHS
    assert "confirm" in triggers["workflow_dispatch"]["inputs"]
    jobs = _load()["jobs"]
    assert jobs["guard"]["if"] == "github.event_name == 'workflow_dispatch'"
    assert "needs.guard.result == 'skipped'" in jobs["build"]["if"]
    assert jobs["deploy"]["needs"] == "build"
    concurrency = _load()["concurrency"]
    assert concurrency["group"] == "deploy-site-react"
    assert concurrency["cancel-in-progress"] is False
