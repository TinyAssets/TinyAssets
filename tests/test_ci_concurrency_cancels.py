"""Pin per-PR cancellation on heavy PR workflows, and its non-PR safety.

Without a `concurrency:` block, every push to a PR stacks another full run of
that workflow beside the one already going. The repo is user-owned, so
GitHub-hosted jobs are capped at about 20 concurrent across every PR, main push
and deploy -- see docs/design-notes/2026-09-27-lean-ci-pipeline.md.

The half that is easy to get wrong is the non-PR half. Grouping non-PR runs by
ref makes each push cancel the previous run on the same ref, which is how
tests.yml's post-merge tripwire came to "read as coverage while providing none".
So every group below falls back to `github.run_id`, making each non-PR run its
own group with nothing to cancel, AND keeps `cancel-in-progress` conditional on
`pull_request`. Two layers, deliberately: for build-bundle a cancelled run is a
published .mcpb, and for android-release it is a signed Play bundle.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

#: Branch-protection contexts on `main`, read from the API 2026-10-03. Kept here
#: so the "never a required check" assertion below states what it compares to.
_REQUIRED_CONTEXTS = frozenset(
    {"Diff scope declared", "required-tests", "invariants", "slow-tests"},
)

#: Workflows this change gave a per-PR group, with the group's name prefix. The
#: first four had no `concurrency:` at all; the last three had one keyed by
#: `github.ref`, which cancelled their own non-PR runs (see
#: ``test_no_group_lets_two_non_pull_request_runs_collide``).
_ADDED = {
    "preview-security.yml": "preview-security",
    "build-bundle.yml": "build-bundle",
    "docker-build.yml": "docker-build",
    "android-release.yml": "android-release",
    "actionlint.yml": "actionlint",
    "release-reconcile-regression.yml": "release-reconcile-regression",
    "uptime-layer2-regression.yml": "layer2-windows",
}

_PR_KEY = "${{ github.event.pull_request.number || github.run_id }}"
_PR_ONLY_CANCEL = "${{ github.event_name == 'pull_request' }}"


def _load(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(wf: dict) -> dict:
    # PyYAML parses a bare `on:` key as the boolean True.
    return wf[True] if True in wf else wf["on"]


def _pull_request_workflows() -> list[str]:
    found = []
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        triggers = _triggers(_load(path.name))
        if isinstance(triggers, dict) and "pull_request" in triggers:
            found.append(path.name)
    return found


@pytest.mark.parametrize("name", sorted(_ADDED))
def test_added_groups_are_one_per_pr(name: str) -> None:
    concurrency = _load(name)["concurrency"]
    assert concurrency["group"] == f"{_ADDED[name]}-{_PR_KEY}", name


@pytest.mark.parametrize("name", sorted(_ADDED))
def test_added_groups_cancel_only_pull_requests(name: str) -> None:
    """The second layer. `true` here would be a policy about non-PR runs too."""
    assert _load(name)["concurrency"]["cancel-in-progress"] == _PR_ONLY_CANCEL, name


def test_every_pull_request_workflow_has_a_concurrency_group() -> None:
    """A ratchet, not a count: a new PR workflow must declare its own group."""
    missing = [n for n in _pull_request_workflows() if not _load(n).get("concurrency")]
    assert not missing, (
        f"these pull_request workflows stack superseded runs: {missing}. Add a "
        "concurrency group keyed on the PR number with a github.run_id fallback."
    )


#: The one `pull_request` workflow still keyed by `github.ref` while cancelling.
#: `invariants` is a REQUIRED check, so changing its cancellation could eject a
#: PR from the merge queue -- out of scope for a runner-budget change, and named
#: here rather than silently allowed. Its `push: main` runs share one group, so
#: each push to main cancels the previous invariants run: the same shape
#: tests.yml calls "coverage while providing none".
_REF_KEYED_BY_DESIGN_DEBT = frozenset({"invariants.yml"})


def test_no_group_lets_two_non_pull_request_runs_collide() -> None:
    """The invariant the ref-keyed shape violates, checked repo-wide.

    A cancelling group that keys only on something two non-PR runs SHARE --
    `github.ref`, a literal, the workflow name -- puts every push to that ref in
    one group, and each push then kills the run before it. A `github.run_id`
    fallback is the fix.

    A workflow whose ONLY trigger is `pull_request` is exempt because it has no
    non-PR run to collide with: `github.event.pull_request.number` is always
    set there, so the group is already one per PR (preview-worker.yml).
    """
    offenders = []
    for name in _pull_request_workflows():
        workflow = _load(name)
        if set(_triggers(workflow)) == {"pull_request"}:
            continue
        concurrency = workflow.get("concurrency") or {}
        group = str(concurrency.get("group", ""))
        cancels = str(concurrency.get("cancel-in-progress", "")).lower() != "false"
        if not group or not cancels:
            continue
        if "github.run_id" in group or "merge_group" in group:
            continue
        offenders.append(name)
    unexpected = sorted(set(offenders) - _REF_KEYED_BY_DESIGN_DEBT)
    assert not unexpected, (
        f"ref-keyed cancelling group(s): {unexpected}. Two non-PR runs on one "
        "ref share this group, so the later one cancels the earlier. Key the "
        "group on github.event.pull_request.number || github.run_id."
    )


@pytest.mark.parametrize("name", sorted(_ADDED))
def test_cancellation_never_lands_on_a_required_check(name: str) -> None:
    """Cancelling a required check ejects a PR from the merge queue."""
    jobs = _load(name)["jobs"]
    declared = {(body.get("name") or job_id) for job_id, body in jobs.items()}
    assert not declared & _REQUIRED_CONTEXTS, name


def test_the_release_and_tag_paths_that_must_never_be_cancelled_still_exist() -> None:
    """If these triggers go away the comments above stop describing the risk."""
    assert _triggers(_load("build-bundle.yml"))["release"]["types"] == ["published"]
    assert _triggers(_load("android-release.yml"))["push"]["tags"] == ["mobile-v*"]
