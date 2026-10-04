"""Tests for scripts/community_loop_watch.py after cheat-loop retirement."""

from __future__ import annotations

import argparse
import datetime as dt

import pytest

from scripts import community_loop_watch as watch


@pytest.fixture(autouse=True)
def no_remote_receipts(monkeypatch):
    # Composition tests never query live evidence. The separate typed-observation
    # suite exercises the exact API contract; absent fixture data is unknown here.
    monkeypatch.setattr(watch, "_gh_get_url", lambda *a, **kw: ({}, None))


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        repo="owner/repo",
        api="https://api.test",
        token="fixture-token",
        timeout=1.0,
        max_observation_age_min=90,
        max_observation_stale_red_min=360,
        json=False,
    )


def _success_run(workflow_id: str, created_at: str = "2026-06-25T12:00:00Z") -> dict:
    return {
        "id": 123,
        "run_attempt": 1,
        "head_sha": "a" * 40,
        "head_branch": "main",
        "path": f".github/workflows/{workflow_id}",
        "head_repository": {"full_name": "owner/repo"},
        "status": "completed",
        "conclusion": "success",
        "event": "schedule",
        "created_at": created_at,
        "html_url": f"https://example.test/{workflow_id}",
    }


def test_build_status_keeps_only_uptime_deploy_and_tier3_stages(monkeypatch) -> None:
    # This test owns stage composition; exact receipt validation is exercised
    # against API-shaped fixtures in test_community_loop_typed_observation.py.
    monkeypatch.setattr(watch, "_canary_receipt", lambda *a, **kw: ("green", "verified fixture"))
    monkeypatch.setattr(
        watch,
        "_latest_workflow_run",
        lambda _repo, workflow_id, **_kwargs: _success_run(workflow_id),
    )
    monkeypatch.setattr(
        watch,
        "list_open_issues_by_label",
        lambda *_args, **_kwargs: [],
    )

    status = watch.build_status(
        _args(),
        now=dt.datetime(2026, 6, 25, 12, 5, tzinfo=dt.timezone.utc),
    )

    assert status["overall"] == "green"
    assert [stage["name"] for stage in status["stages"]] == [
        "Observation canary",
        "Observation incidents",
        "Tier-3 clone smoke",
        "Production deploy",
        "Website deploy",
    ]


def test_build_status_does_not_read_deleted_cheat_loop_workflows(monkeypatch) -> None:
    seen: list[str] = []

    def fake_latest(_repo: str, workflow_id: str, **_kwargs) -> dict:
        seen.append(workflow_id)
        return _success_run(workflow_id)

    monkeypatch.setattr(watch, "_latest_workflow_run", fake_latest)
    monkeypatch.setattr(watch, "list_open_issues_by_label", lambda *_args, **_kwargs: [])

    watch.build_status(
        _args(),
        now=dt.datetime(2026, 6, 25, 12, 5, tzinfo=dt.timezone.utc),
    )

    retired_workflows = [
        "wiki-" + "bug-sync.yml",
        "auto-" + "fix-bug.yml",
        "auto-" + "check-pr.yml",
    ]
    for workflow_id in retired_workflows:
        assert workflow_id not in seen


def test_observation_canary_mild_staleness_warns_yellow(monkeypatch) -> None:
    # A stale heartbeat is a monitoring-cadence gap, not a measured endpoint
    # red: 125 min without a canary success warns instead of paging.
    monkeypatch.setattr(
        watch,
        "_latest_workflow_run",
        lambda _repo, workflow_id, **_kwargs: _success_run(
            workflow_id,
            created_at="2026-06-25T10:00:00Z",
        ),
    )
    monkeypatch.setattr(watch, "list_open_issues_by_label", lambda *_args, **_kwargs: [])

    status = watch.build_status(
        _args(),
        now=dt.datetime(2026, 6, 25, 12, 5, tzinfo=dt.timezone.utc),
    )

    observation = status["stages"][0]
    assert observation["name"] == "Observation canary"
    assert observation["status"] == "yellow"
    assert "not an endpoint-health measurement" in (observation.get("evidence") or "")
    assert status["overall"] == "yellow"


def test_observation_canary_staleness_still_goes_red(monkeypatch) -> None:
    # Past the red tier the monitor itself looks dead: still pages.
    monkeypatch.setattr(
        watch,
        "_latest_workflow_run",
        lambda _repo, workflow_id, **_kwargs: _success_run(
            workflow_id,
            created_at="2026-06-25T05:00:00Z",
        ),
    )
    monkeypatch.setattr(watch, "list_open_issues_by_label", lambda *_args, **_kwargs: [])

    status = watch.build_status(
        _args(),
        now=dt.datetime(2026, 6, 25, 12, 5, tzinfo=dt.timezone.utc),
    )

    observation = status["stages"][0]
    assert observation["name"] == "Observation canary"
    assert observation["status"] == "red"
    assert status["overall"] == "red"


def _deploy_run_with_conclusion(conclusion: str | None) -> dict:
    run = _success_run("deploy-prod.yml")
    run["conclusion"] = conclusion
    return run


@pytest.mark.parametrize("conclusion", ["skipped", "cancelled", "neutral", "stale"])
def test_deploy_benign_conclusion_warns_yellow(monkeypatch, conclusion: str) -> None:
    # A skipped/cancelled deploy carries no failure signal: warn, don't page.
    def fake_latest(_repo: str, workflow_id: str, **_kwargs) -> dict:
        if workflow_id == "deploy-prod.yml":
            return _deploy_run_with_conclusion(conclusion)
        return _success_run(workflow_id)

    monkeypatch.setattr(watch, "_latest_workflow_run", fake_latest)
    monkeypatch.setattr(watch, "list_open_issues_by_label", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        watch, "_canary_receipt", lambda *a, **kw: ("green", "verified fixture")
    )

    status = watch.build_status(
        _args(),
        now=dt.datetime(2026, 6, 25, 12, 5, tzinfo=dt.timezone.utc),
    )

    deploy = next(
        stage for stage in status["stages"] if stage["name"] == "Production deploy"
    )
    assert deploy["status"] == "yellow"
    assert status["overall"] == "yellow"


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "action_required", None])
def test_deploy_failure_like_conclusion_still_goes_red(monkeypatch, conclusion: str | None) -> None:
    # Real failures — and unrecognized conclusions, fail-closed — still page.
    def fake_latest(_repo: str, workflow_id: str, **_kwargs) -> dict:
        if workflow_id == "deploy-prod.yml":
            return _deploy_run_with_conclusion(conclusion)
        return _success_run(workflow_id)

    monkeypatch.setattr(watch, "_latest_workflow_run", fake_latest)
    monkeypatch.setattr(watch, "list_open_issues_by_label", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        watch, "_canary_receipt", lambda *a, **kw: ("green", "verified fixture")
    )

    status = watch.build_status(
        _args(),
        now=dt.datetime(2026, 6, 25, 12, 5, tzinfo=dt.timezone.utc),
    )

    deploy = next(
        stage for stage in status["stages"] if stage["name"] == "Production deploy"
    )
    assert deploy["status"] == "red"
    assert status["overall"] == "red"


def test_open_p0_outage_still_goes_red(monkeypatch) -> None:
    def fake_issues(_repo: str, label: str, **_kwargs) -> list[dict]:
        if label == watch.P0_OUTAGE_LABEL:
            return [
                {
                    "number": 44,
                    "title": "MCP canary red",
                    "html_url": "https://example.test/issues/44",
                }
            ]
        return []

    monkeypatch.setattr(
        watch,
        "_latest_workflow_run",
        lambda _repo, workflow_id, **_kwargs: _success_run(workflow_id),
    )
    monkeypatch.setattr(watch, "list_open_issues_by_label", fake_issues)

    status = watch.build_status(
        _args(),
        now=dt.datetime(2026, 6, 25, 12, 5, tzinfo=dt.timezone.utc),
    )

    incident = next(stage for stage in status["stages"] if stage["name"] == "Observation incidents")
    assert incident["status"] == "red"
    assert incident["details"] == {"open_p0_outages": [44]}
    assert status["overall"] == "red"
