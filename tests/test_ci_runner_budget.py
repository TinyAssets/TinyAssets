"""Pin the trigger cuts that keep PR CI from starving on runners.

The repo is user-owned, so GitHub-hosted jobs are capped at about 20 concurrent
across every PR, main push and deploy. On 2026-09-27 two triggers spent about
2,150 of about 4,300 runner-minutes in 4 h without gating anything, and the
deploy job waited 22 min for a runner. See
docs/design-notes/2026-09-27-lean-ci-pipeline.md. A quiet revert of either cut
brings that back, and nothing else would notice.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _triggers(name: str) -> dict:
    wf = yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))
    # PyYAML parses a bare `on:` key as the boolean True.
    return wf[True] if True in wf else wf["on"]


def test_desktop_installer_matrix_does_not_run_on_pull_requests() -> None:
    triggers = _triggers("desktop-release.yml")
    assert "pull_request" not in triggers and "pull_request_target" not in triggers
    # The cut moves the work, it does not delete it: landed changes still build.
    assert triggers["push"]["branches"] == ["main"]
    assert "workflow_dispatch" in triggers


def test_docker_smoke_push_runs_only_on_main() -> None:
    triggers = _triggers("docker-build.yml")
    assert triggers["push"].get("branches") == ["main"], (
        "an unfiltered push trigger builds every agent-branch push on top of "
        "that branch's PR run"
    )
    assert "pull_request" in triggers, "PRs must still get the Docker smoke"


def test_mobile_builds_do_not_run_on_pull_requests() -> None:
    """Lean pipeline: no platform builds on PRs; landed changes still build."""
    for name in ("android-build.yml", "ios-build.yml", "desktop-chat-app.yml"):
        triggers = _triggers(name)
        assert "pull_request" not in triggers, name
        assert "main" in triggers["push"]["branches"], name
        assert "workflow_dispatch" in triggers, name


#: Heavy `pull_request` workflows that skip while a PR is a draft, mapped to the
#: job that carries the condition. Measured 2026-10-03 over the last 100
#: completed PR runs: these five spent 311 of 436 runner-minutes, and
#: merge-group runs queue behind them.
_DRAFT_SKIPPING = {
    "preview-security.yml": "contract",
    "build-bundle.yml": "stage-and-probe",
    "docker-build.yml": "build-smoke",
    "real-browser-proof.yml": "real-browser-proof",
    "linux-jail-proof.yml": "linux-jail-proof",
}

#: Branch-protection contexts on `main`, read 2026-10-03. A job whose name is
#: one of these may NEVER carry a draft condition: a skipped job reports
#: `conclusion=skipped` and branch protection accepts that as satisfied, so the
#: gate would pass without running. tests.yml states this at length for
#: `required-tests`, verified empirically on PR #2197.
_REQUIRED_CONTEXTS = frozenset(
    {"Diff scope declared", "required-tests", "invariants", "slow-tests"},
)

_DRAFT_CONDITION = (
    "github.event_name != 'pull_request' "
    "|| github.event.pull_request.draft == false"
)


def _job(name: str, job_id: str) -> dict:
    wf = yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))
    return wf["jobs"][job_id]


def test_heavy_pull_request_jobs_skip_drafts() -> None:
    """The cut itself: a draft push must not spend a runner on these."""
    for name, job_id in _DRAFT_SKIPPING.items():
        assert _job(name, job_id).get("if") == _DRAFT_CONDITION, name


def test_draft_skipping_workflows_rerun_when_a_pr_becomes_ready() -> None:
    """Without `ready_for_review` the skip survives until the next push.

    `ready_for_review` is not one of the default `pull_request` types, so this
    is the half of the change that makes the skip recoverable rather than
    sticky. Dropping it would leave a ready PR showing a draft-era skip.
    """
    for name in _DRAFT_SKIPPING:
        types = _triggers(name)["pull_request"]["types"]
        assert "ready_for_review" in types, name
        # The default set still has to be there, or ordinary pushes stop testing.
        for default in ("opened", "reopened", "synchronize"):
            assert default in types, f"{name} dropped {default}"


def test_draft_skipping_never_lands_on_a_required_context() -> None:
    """A skipped required check is accepted as satisfied, so it would fail OPEN."""
    for name, job_id in _DRAFT_SKIPPING.items():
        job = _job(name, job_id)
        declared = job.get("name") or job_id
        assert declared not in _REQUIRED_CONTEXTS, (
            f"{name}:{job_id} reports as required context {declared!r}; a draft "
            "skip there merges untested code"
        )


def test_the_required_gates_still_run_on_drafts() -> None:
    """The other half of the same invariant, from the required side.

    `invariants` and `Diff scope declared` are cheap and stay on every draft
    push; `required-tests` keeps `always()` so no condition can skip it.
    """
    assert "if" not in _job("invariants.yml", "invariants")
    assert _job("pr-scope-guard.yml", "scope").get("if") != _DRAFT_CONDITION
    assert _job("tests.yml", "required-tests")["if"] == "always()"


def test_draft_condition_does_not_reach_non_pull_request_events() -> None:
    """merge_group, push, schedule, release and dispatch must be unaffected.

    The first clause is what preserves them. A condition of only
    `github.event.pull_request.draft == false` evaluates false off a PR, which
    would silently stop the post-merge and release paths.
    """
    for name, job_id in _DRAFT_SKIPPING.items():
        condition = _job(name, job_id)["if"]
        assert condition.startswith("github.event_name != 'pull_request'"), name
