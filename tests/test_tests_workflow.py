"""Trigger-shape invariants for the Tests workflow.

These exist because a trigger that never fires is indistinguishable from
coverage until you go looking. Two such bugs were found in this workflow on
2026-08-03, both of which read as "the full suite guards main" while guarding
nothing:

  1. `concurrency` grouped main runs by ref, so each push killed the previous
     main run and the post-merge job almost never finished.
  2. `push: branches: [main]` does not fire at all on the normal merge path.
     auto-enroll-merge.yml enrols PRs with the repo's GITHUB_TOKEN, so GitHub
     performs the merge as `app/github-actions`, and events raised by
     GITHUB_TOKEN never start a workflow run (AGENTS.md hard rule 14). Of the
     last 6 merges, the 5 app-performed ones produced ZERO push runs; only the
     one merged by a human PAT produced a run.

So the assertions below are not style checks. Each one pins a property whose
absence silently converts a gate into decoration.

**The assertions are exact where behaviour is exact, and structural where a
stricter check would produce false positives.** Two rounds of cross-family
review found five vacuous assertions in earlier drafts of this very file —
including an "is there a non-PR trigger?" check that `workflow_dispatch` had
satisfied since before the bug existed, and a `--min-ran` check that accepted
`--min-ran 1`. A regression test for a silent-failure bug must not itself be
able to fail silently. Equally, it must not fail on a harmless reformat, so
expression comparisons canonicalize the optional `${{ }}` wrapper and cron
grammar is left to actionlint rather than re-implemented badly here.

PyYAML is imported hard, with no `skipif`. Sibling workflow tests skip when it
is absent; that is wrong for this file specifically, because skipping is how
these invariants would go quiet — the failure mode they exist to prevent.
PyYAML is declared in the `dev` extra so the import is guaranteed, not
transitive.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO / ".github" / "workflows" / "tests.yml"
_SCRIPT = _REPO / "scripts" / "ci_required_tests.py"

# Import the gate script so the floor asserted below cannot drift away from the
# floor the gate actually enforces.
_spec = importlib.util.spec_from_file_location("ci_required_tests", _SCRIPT)
assert _spec and _spec.loader
_ci = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ci)

# The one expression `heavy-tests` may use: the hourly schedule and manual
# dispatch, nothing else. Pinned exactly rather than by substring, because a
# condition that drops `schedule` removes the only automatic coverage of the
# heavy files, and one that admits `push` (or a future `merge_group`) runs a
# ~41 min red-at-baseline job on every merge (2026-09-27 lean pipeline).
_FULL_TESTS_IF = "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"

_CONCURRENCY_GROUP = (
    "tests-${{ github.event.pull_request.number || github.run_id }}"
)
# Compared LITERALLY, not through _expr(): in `concurrency`, unlike `if:`, the
# `${{ }}` wrapper is NOT optional — actionlint rejects a bare expression there.
# Normalizing it away would let an invalid workflow pass this test.
_CANCEL_IN_PROGRESS = "${{ github.event_name == 'pull_request' }}"


def _load() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def test_required_shards_install_the_browser_before_running_tests():
    """Install precedes every step that runs tests, and covers every such step.

    The install is conditional now (a shard owning no selected file skips it,
    which is the whole saving), so "has no `if`" is no longer the invariant.
    What must hold is that NO run-tests step can execute without it: the
    install's condition is the weakest one, and every run step's condition
    includes it. A run step reachable while the install is skipped would fail on
    a missing pytest, not on the code under test.
    """
    steps = _load()["jobs"]["required-tests-shard"]["steps"]
    install = next(i for i, s in enumerate(steps)
                   if "playwright install --with-deps chromium" in s.get("run", ""))
    assert "'.[dev,browser]'" in steps[install]["run"]
    assert not steps[install].get("continue-on-error", False)
    # The implication is checked over the only three values `--plan-shard` can
    # print: ALL, 0, or a positive count. Spelling the conditions out means a
    # change to any of them fails here and has to be re-reasoned rather than
    # silently widening what runs without an install.
    accepts = {
        "steps.plan.outputs.count != '0'": {"ALL", "n"},
        "steps.plan.outputs.count == 'ALL'": {"ALL"},
        "steps.plan.outputs.count != '0' && steps.plan.outputs.count != 'ALL'": {"n"},
    }
    install_if = _expr(steps[install].get("if", ""))
    assert install_if in accepts, install_if

    # Every step that RUNS tests (not the stdlib-only planning step).
    runners = [
        (i, s) for i, s in enumerate(steps)
        if "ci_required_tests.py" in s.get("run", "") and "--plan-shard" not in s["run"]
    ]
    assert len(runners) == 2, [s.get("name") for _, s in runners]
    for index, step in runners:
        assert install < index, f"{step.get('name')} runs before the install"
        condition = _expr(step.get("if", ""))
        assert condition in accepts, condition
        assert accepts[condition] <= accepts[install_if], (
            f"{step.get('name')} can run while the install is skipped: {condition}"
        )
    # Together they must cover every value that installs, or a shard with work
    # would install and then run nothing while reporting success.
    assert set().union(*(accepts[_expr(s.get("if", ""))] for _, s in runners)) == accepts[
        install_if
    ]

    # The planning step must come FIRST and must not need the install: knowing
    # the slice is empty is what lets the install be skipped at all.
    plan = next(i for i, s in enumerate(steps) if "--plan-shard" in s.get("run", ""))
    assert plan < install


def test_required_aggregate_rejects_missing_or_skipped_browser_proofs():
    """Whole-surface only, because the assertion is "present AND clean".

    `ci_assert_junit_case` exits 1 when a named case is ABSENT. On a selective
    union the browser cases are legitimately absent unless the entry touched
    them, so running this there would fail the gate for the one reason that is
    not a regression. It must therefore be scoped to ALL -- and it must still be
    unconditional WITHIN that scope, which is what the rest of this pins.
    """
    steps = _load()["jobs"]["required-tests"]["steps"]
    aggregate = next(i for i, s in enumerate(steps)
                     if "--aggregate shards/" in s.get("run", ""))
    proof = next(i for i, s in enumerate(steps)
                 if "ci_assert_junit_case.py" in s.get("run", ""))
    step = steps[proof]
    assert aggregate < proof
    assert _expr(step["if"]) == (
        "github.event_name != 'pull_request' && needs.select.outputs.scope == 'ALL'"
    ), step["if"]
    assert "--junit junit.xml" in step["run"]
    assert "--marker real_browser" in step["run"]
    assert "'.[dev,browser]'" in step["run"]
    assert not step.get("continue-on-error", False)
    assert "|| true" not in step["run"]


def _triggers(wf: dict) -> dict:
    # PyYAML parses a bare `on:` key as the boolean True.
    return wf[True] if True in wf else wf["on"]


def _norm(value: object) -> str:
    """Collapse whitespace only. Use where the literal text is the assertion."""
    return re.sub(r"\s+", " ", str(value)).strip()


def _expr(value: object) -> str:
    """Normalize whitespace and strip a whole-value `${{ }}` wrapper.

    Use ONLY for `if:`, where GitHub makes the wrapper optional. Do NOT use for
    `concurrency`, where the wrapper is mandatory — stripping it there would let
    a workflow actionlint rejects pass this suite.

    `if: foo` and `if: ${{ foo }}` are the same expression to GitHub, so a test
    that accepts only one spelling is testing spelling, not behaviour. Only a
    wrapper spanning the ENTIRE value is stripped — `tests-${{ x }}` keeps its
    interpolation, since there the literal text is the thing being asserted.

    The inner pattern is `[^{}]*`, not `.*`: greedy `.*` also matches a value
    made of TWO interpolations, e.g. `${{ a }}-${{ b }}`, mangling it to
    `a }}-${{ b`. That failed in the safe direction here (a mangled value just
    fails the exact comparison) but it is still wrong, and a future assertion
    might not be exact.
    """
    text = re.sub(r"\s+", " ", str(value)).strip()
    m = re.fullmatch(r"\$\{\{([^{}]*)\}\}", text)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else text


def test_full_suite_runs_on_an_in_repo_schedule() -> None:
    """The post-merge tripwire needs a trigger `push:` cannot provide.

    Regression guard for bug (2). `push: branches: [main]` may stay — it still
    catches the rare human-PAT merge — but it cannot carry the tripwire, and
    `workflow_dispatch` needs a human to press it.

    This deliberately requires an **in-repository `schedule`** rather than
    "any automatic trigger". A `repository_dispatch` driven by some external
    scheduler would also be automatic, and is even a documented GITHUB_TOKEN
    exception — but then the tripwire's liveness depends on infrastructure that
    is not in this repo and cannot be reviewed here. Requiring the schedule is
    a policy choice, named honestly rather than dressed up as a generic check.

    An empty `schedule:` or `schedule: []` parses cleanly and fires nothing, so
    presence of the key proves nothing on its own.
    """
    schedule = _triggers(_load()).get("schedule")
    assert isinstance(schedule, list) and schedule, (
        f"tests.yml needs a non-empty `schedule:` — got {schedule!r}. Without "
        f"it the heavy-tests tripwire never runs: `push: branches: [main]` does "
        f"NOT fire when auto-merge lands a PR via GITHUB_TOKEN, and "
        f"workflow_dispatch needs a human."
    )
    for entry in schedule:
        assert isinstance(entry, dict) and "cron" in entry, (
            f"each schedule entry needs a `cron:` key, got {entry!r}"
        )
        # Structure plus numeric ranges only. A full re-implementation of
        # GitHub's cron grammar got day-of-week `7` and symbolic `SUN-SAT`
        # wrong in review, and a validator that rejects a VALID cron is a
        # false-positive gate. But pure structure accepted `99 99 99 99 99`,
        # so numeric values are range-checked and anything containing letters
        # (`JAN-DEC`, `SUN-SAT`) is left to actionlint.
        fields = str(entry["cron"]).split()
        assert len(fields) == 5, (
            f"cron must have 5 fields, got {len(fields)}: {entry['cron']!r}"
        )
        bounds = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
        for field, (lo, hi) in zip(fields, bounds):
            if re.search(r"[A-Za-z]", field):
                continue  # symbolic form — actionlint owns it
            for number in re.findall(r"\d+", field.split("/")[0]):
                assert lo <= int(number) <= hi, (
                    f"cron field {field!r} has {number} outside {lo}-{hi} in "
                    f"{entry['cron']!r}"
                )


def test_schedule_declares_at_most_one_nominal_slot_per_hour() -> None:
    """Cadence policy: the schedule declares AT MOST one nominal slot per hour.

    "At most", not "one": `17 */3 * * *` declares one slot every three hours
    and is deliberately accepted. The test bounds the declared rate from
    above; it does not require any particular rate.

    `heavy-tests` runs only the heavy files (the old `full-tests` also re-ran
    the whole required suite: 36-45 min) and scheduled
    runs do NOT displace each other — the sibling concurrency test pins non-PR
    runs to a unique group with cancel-in-progress false, deliberately, so
    this workflow's own concurrency policy will not replace a queued tripwire
    run. (GitHub may still drop a queued job under load — a separate
    mechanism no concurrency key can address.) The narrow guarantee is
    non-replacement: this workflow will not replace one non-PR run with
    another. It does not follow that two runs execute simultaneously —
    runner capacity may serialize them — nor that any particular number of
    runs is delivered at all.

    **This pins the DECLARED cadence, and nothing more.** GitHub documents that
    scheduled events may be delayed or dropped; it does not guarantee any
    minimum spacing between the starts it does deliver. The two runs cited
    above were dispatched 59 and 17 minutes late, so even one nominal slot per
    hour can produce starts minutes apart. Overlap is therefore tolerated (free
    runners, duplicated work, nothing corrupted), not prevented — do not read
    this test as proving runs cannot overlap. The declaration is the only part
    of the cadence the repo controls, so it is the only part testable here.

    Three deliberate conservatisms, all of which would otherwise make this
    vacuous or wrong:

    * The check is on the WHOLE schedule, not per entry. `17 * * * *` plus
      `47 * * * *` each satisfy "one fixed minute" while collectively declaring
      two slots an hour, so a per-entry check does not enforce its own headline
      — cross-family review caught exactly that.
    * ALL multi-entry schedules are rejected, not merely collectively
      sub-hourly ones. Disjoint weekday/weekend entries whose union never
      exceeds one slot per hour would be legitimate and are refused anyway.
    * A bare fixed minute is required, so legitimate once-per-hour forms like
      `59/5 * * * *` are rejected too. Evaluating real cron occurrences to
      admit either case would mean re-implementing GitHub's scheduler here,
      which an earlier version of this file got wrong; a conservative policy
      that is easy to read beats a clever one that is subtly wrong.

    If `heavy-tests` is ever made materially faster, relax this in the same
    commit that proves the new duration — do not delete it, because the
    behaviour it prevents is silent.
    """
    schedule = _triggers(_load())["schedule"]
    assert len(schedule) == 1, (
        f"expected exactly one schedule entry, got {len(schedule)}: "
        f"{[e.get('cron') for e in schedule]}. Multiple entries can each look "
        f"hourly while collectively declaring more slots, which is what this "
        f"test exists to prevent."
    )
    minute = str(schedule[0]["cron"]).split()[0]
    assert re.fullmatch(r"\d{1,2}", minute), (
        f"cron {schedule[0]['cron']!r} does not use a literal fixed minute "
        f"(minute field {minute!r}), so this check cannot establish that it "
        f"declares at most one slot per hour. Some such expressions, e.g. "
        f"`59/5 * * * *`, ARE once-hourly; the policy is conservative on "
        f"purpose and refuses them rather than evaluating cron occurrences. "
        f"`heavy-tests` runs the heavy files only, and this workflow will not replace one "
        f"non-PR run with another. Use a single fixed minute, or prove a "
        f"shorter runtime first."
    )


def test_heavy_tests_runs_on_schedule_and_dispatch_only() -> None:
    """Pinned exactly: dropping `schedule` strands the tripwire; adding `push`
    puts a ~41 min job back on every merge."""
    condition = _expr(_load()["jobs"]["heavy-tests"].get("if", ""))
    assert condition == _FULL_TESTS_IF, (
        f"heavy-tests `if:` must be exactly {_FULL_TESTS_IF!r}; got "
        f"{condition!r}. Without `schedule` nothing automatic covers "
        f".github/heavy-test-files.txt; with `push` it runs on every merge."
    )


def test_non_pr_runs_never_cancel_or_queue_behind_each_other() -> None:
    """Regression guard for bug (1), pinned exactly.

    Substring checks passed here too: `cancel-in-progress:
    github.event_name != 'pull_request'` contains "pull_request" while meaning
    the exact opposite — cancel main runs, keep PR runs.
    """
    concurrency = _load()["concurrency"]
    group = _norm(concurrency["group"])
    cancel = _norm(concurrency["cancel-in-progress"])
    assert group == _CONCURRENCY_GROUP, (
        f"concurrency group must be exactly {_CONCURRENCY_GROUP!r}; got "
        f"{group!r}. Keying non-PR runs by ref lets each push cancel the "
        f"previous run; keying them by SHA still lets a newer pending run "
        f"replace a queued one on an unchanged main."
    )
    assert cancel == _CANCEL_IN_PROGRESS, (
        f"cancel-in-progress must be exactly {_CANCEL_IN_PROGRESS!r}; got "
        f"{cancel!r}. Compared literally on purpose — the `${{{{ }}}}` wrapper "
        f"is mandatory in `concurrency`, so accepting a bare expression here "
        f"would pass a workflow actionlint rejects."
    )


def test_required_tests_job_name_matches_the_protection_context() -> None:
    """Renaming this job orphans the required context and blocks every PR.

    It does not fail open: the old context stays "Expected — waiting for
    status" forever.
    """
    assert _load()["jobs"]["required-tests"]["name"] == "required-tests"


def test_required_tests_cannot_decline_to_report() -> None:
    """The required check must always produce a real conclusion.

    Two different failure modes, and they fail in OPPOSITE directions:

    * A `paths:`/`paths-ignore:` filter on the trigger can stop the workflow
      from running at all. Then no check is ever reported and branch protection
      waits on "Expected — waiting for status" FOREVER — every PR wedged.
    * A job-level `if:` does NOT do that. A skipped job reports
      `conclusion=skipped`, which branch protection accepts as satisfied — so a
      mistaken condition FAILS OPEN and silently merges untested code. Verified
      empirically 2026-08-03: the scheduled tripwire (`full-tests` then,
      `heavy-tests` now) carries a job-level `if:` and
      reported `COMPLETED/SKIPPED` on PR #2197, not pending.

    Fail-open is the more dangerous of the two, which is why the required job
    gets neither.
    """
    wf = _load()
    triggers = _triggers(wf)
    # Asserted separately: `.get("pull_request") or {}` treats a MISSING
    # pull_request trigger as an unfiltered one, so deleting the trigger
    # outright would have passed the filter checks below.
    assert "pull_request" in triggers, (
        "the required check must be triggered by `pull_request` at all — "
        "without it no check is ever reported and every PR hangs on "
        "'Expected — waiting for status'"
    )
    pr_trigger = triggers.get("pull_request") or {}
    for key in ("paths", "paths-ignore"):
        assert key not in pr_trigger, (
            f"`{key}:` on the required check's trigger stops the workflow from "
            f"running on out-of-scope PRs, and the required context then hangs "
            f"on 'Expected — waiting for status' forever"
        )
    # The aggregate is the one exception, and it is forced rather than
    # allowed: it `needs` the shards, and a job whose need failed is SKIPPED
    # unless its condition says otherwise -- so with no `if:` a red shard would
    # skip the required check and merge green. `always()` is the only condition
    # that can never evaluate false. Anything else (`success()`, `!cancelled()`)
    # reintroduces a skip path.
    condition = _expr(wf["jobs"]["required-tests"].get("if", ""))
    assert condition == "always()", (
        f"the REQUIRED aggregate's `if:` must be exactly `always()`, got "
        f"{condition!r}: without it a failed shard SKIPS the required check, "
        f"and protection treats skipped as SUCCESS"
    )
    # The heavy run happens ONCE, on the merge-group commit (founder
    # 2026-09-30: "cut the double CI run in the merge queue"). Shards may skip
    # on exactly one event -- the pull request -- and on nothing else, so the
    # queue, the schedule and a manual dispatch always run them.
    queue_only = "github.event_name != 'pull_request'"
    assert _expr(wf["jobs"]["required-tests-shard"].get("if", "")) == queue_only, (
        "shards skip ONLY on pull_request; any other condition could skip the "
        "merge-queue run, which is the one that gates main"
    )
    assert "merge_group" in triggers, "without merge_group the shards never run at all"
    agg_steps = wf["jobs"]["required-tests"]["steps"]
    # Two verdict steps now -- whole surface and selection -- and they must
    # PARTITION every non-PR event. If a non-PR event could match neither, the
    # job would report success having judged nothing, which is the fail-open
    # this whole test exists to prevent. Complementary conditions on one
    # expression are what make them exhaustive; `scope` is unvalidated input
    # from another job, so the selective branch is written `!= 'ALL'` rather
    # than `== 'affected'` to catch an empty value too.
    decide = [s for s in agg_steps if "--aggregate" in str(s.get("run", ""))]
    assert len(decide) == 2, [s.get("name") for s in decide]
    conditions = {_expr(s.get("if", "")) for s in decide}
    assert conditions == {
        f"{queue_only} && needs.select.outputs.scope == 'ALL'",
        f"{queue_only} && needs.select.outputs.scope != 'ALL'",
    }, conditions
    assert any(
        _expr(s.get("if", "")) == "github.event_name == 'pull_request'" for s in agg_steps
    ), "on a PR the aggregate must still REPORT, explicitly deferring to the queue"
    assert _expr(wf["jobs"]["slow-tests"].get("if", "")) == queue_only


def test_required_tests_enforces_an_adequate_vacuity_floor() -> None:
    """The subset gate must assert a *meaningful* minimum test count.

    Without `--min-ran`, deselecting or erroring out of every test yields a
    green gate. But the flag alone is not enough: `--min-ran 1` is accepted by
    the script verbatim and disables the floor just as effectively, so this
    asserts the actual number.
    """
    steps = _load()["jobs"]["required-tests"]["steps"]
    run_block = " ".join(str(s.get("run", "")) for s in steps)
    values = [int(v) for v in re.findall(r"--min-ran[\s=]+(\d+)", run_block)]
    assert values, (
        "required-tests must pass --min-ran so a mass-deselect cannot pass"
    )
    # Exactly one, because argparse honours the LAST occurrence while a naive
    # scan reads the first: `--min-ran 10700 --min-ran 1` would look compliant
    # while setting the real floor to 1. Rated BLOCKING in review — it lets a
    # mass-deselected suite merge. The script now also rejects a low value
    # outright (ci_required_tests._min_ran_arg); this is the second lock.
    assert len(values) == 1, (
        f"--min-ran must appear exactly once, found {values}. argparse uses the "
        f"LAST value, so a repeated flag can silently lower the floor."
    )
    assert values[0] >= _ci.MIN_RAN_FLOOR, (
        f"--min-ran {values[0]} is below the script's own MIN_RAN_FLOOR "
        f"({_ci.MIN_RAN_FLOOR}); a low floor disables the vacuity check as "
        f"surely as omitting the flag. If the suite legitimately shrank, lower "
        f"MIN_RAN_FLOOR in the same PR and say why."
    )


def test_heavy_tests_uses_the_reviewed_runner_and_floor() -> None:
    """heavy-tests must not regress to raw pytest.

    Cross-family review rejected a raw-pytest version for losing two things:
    the quarantine ledger (17 current entries live in heavy-listed files, so a
    raw run is red on an unchanged baseline) and the vacuity floor (a green run
    of zero tests). Both come from `ci_required_tests.py`, so the job must go
    through it -- and must name a PROFILE rather than pick a bare number.
    """
    steps = _load()["jobs"]["heavy-tests"]["steps"]
    run = "\n".join(s.get("run", "") for s in steps)
    assert "ci_required_tests.py" in run, (
        "heavy-tests must run through ci_required_tests.py, not raw pytest -- "
        "raw pytest loses quarantine handling and the vacuity floor."
    )
    assert "--include-from .github/heavy-test-files.txt" in run, (
        "heavy-tests must run exactly the files the required gate excludes."
    )
    assert "--profile heavy" in run, "heavy-tests must select the reviewed heavy floor"
    assert "--min-ran" not in run, (
        "heavy-tests must NOT pass --min-ran: `_min_ran_arg` rejects anything "
        "below MIN_RAN_FLOOR at parse time (a locked contract), so the floor "
        "comes from --profile instead."
    )


def test_required_and_heavy_do_not_overlap() -> None:
    """The point of the split: the suite runs once across the two jobs."""
    jobs = _load()["jobs"]
    req = "\n".join(s.get("run", "") for s in jobs["required-tests-shard"]["steps"])
    heavy = "\n".join(s.get("run", "") for s in jobs["heavy-tests"]["steps"])
    assert "--exclude-from .github/heavy-test-files.txt" in req
    assert "--include-from .github/heavy-test-files.txt" in heavy


def test_every_heavy_listed_path_still_exists() -> None:
    """A path in the list that no longer exists is silent coverage loss.

    The list does double duty: `--exclude-from` for the required gate and
    `--include-from` for the tripwire. A stale entry is therefore invisible
    twice over -- pytest's `--ignore` accepts a nonexistent path without
    complaint, and `--include-from` simply collects nothing from it. Found for
    real on 2026-08-27: `tests/command_center/test_server.py` outlived the
    directory the harness reset deleted, and neither job said a word.
    """
    listing = _REPO / ".github" / "heavy-test-files.txt"
    entries = [
        line.strip()
        for line in listing.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert entries, "heavy-test-files.txt has no entries -- the split is inert"
    missing = [e for e in entries if not (_REPO / e).is_file()]
    assert not missing, (
        f"{len(missing)} heavy-listed path(s) no longer exist: {missing}. "
        "Delete them from .github/heavy-test-files.txt in the same change that "
        "deleted the tests, or the required gate keeps --ignore-ing a ghost."
    )
    assert len(set(entries)) == len(entries), "duplicate entries in the heavy list"


# ---- sharding ----------------------------------------------------------------


def _shard_count_declarations() -> dict[str, object]:
    jobs = _load()["jobs"]
    shard_job = jobs["required-tests-shard"]
    shard_run = "\n".join(s.get("run", "") for s in shard_job["steps"])
    agg_run = "\n".join(s.get("run", "") for s in jobs["required-tests"]["steps"])
    return {
        "matrix": shard_job["strategy"]["matrix"]["shard"],
        "shard_arg": re.findall(r'--shard\s+"\$\{\{ matrix\.shard \}\}/(\d+)"', shard_run),
        "name": re.findall(r"/(\d+)$", str(shard_job["name"])),
        "expect": re.findall(r"--expect-shards[\s=]+(\d+)", agg_run),
    }


def test_every_shard_count_declaration_agrees() -> None:
    """The shard count is written in four places; they must be ONE number.

    Matrix larger than `--shard .../N`: two jobs run the same shard and a
    duplicate manifest fails the gate. Matrix SMALLER: some hash buckets have
    no job, those files never run, and -- unless `--expect-shards` also
    disagrees -- nothing notices. That silent case is why the matrix must be
    exactly 1..N rather than merely N entries long.
    """
    d = _shard_count_declarations()
    # `--shard .../N` now appears once per step that takes a slice (plan, the
    # whole-surface run, the selective run). The invariant was never "written
    # once" -- it is "written as ONE number wherever it appears".
    assert d["shard_arg"], d
    assert len(set(d["shard_arg"])) == 1, d
    assert len(set(d["expect"])) == 1 and len(d["name"]) == 1, d
    n = int(d["shard_arg"][0])
    assert n >= 2, "a single shard is the old serial job with extra steps"
    assert d["matrix"] == list(range(1, n + 1)), d
    assert int(d["expect"][0]) == n, d
    assert int(d["name"][0]) == n, d


def test_only_the_aggregate_carries_the_protection_context() -> None:
    """A shard named `required-tests` would let one green shard satisfy it."""
    jobs = _load()["jobs"]
    shard_name = str(jobs["required-tests-shard"]["name"])
    assert shard_name.startswith("required-tests shard "), shard_name
    names = [str(j.get("name", k)) for k, j in jobs.items()]
    assert names.count("required-tests") == 1


def test_aggregate_waits_for_every_shard_and_reads_their_results() -> None:
    jobs = _load()["jobs"]
    shard, agg = jobs["required-tests-shard"], jobs["required-tests"]
    # `select` joined `needs` so the aggregate can read the published digest and
    # scope. required-tests-shard must STAY in it: without that edge the
    # aggregate could start before the shards finished and judge an empty
    # directory, and `needs.required-tests-shard.result` would not resolve.
    needs = agg.get("needs")
    needs = [needs] if isinstance(needs, str) else list(needs or [])
    assert "required-tests-shard" in needs, needs
    assert "select" in needs, needs
    assert "select" in (
        [shard["needs"]] if isinstance(shard.get("needs"), str) else list(shard.get("needs") or [])
    ), "a shard must not start before the selection it slices exists"
    # fail-fast would cancel sibling shards, turning one real failure into
    # "missing shards" and hiding what actually broke.
    assert shard["strategy"].get("fail-fast") is False

    upload = next(s for s in shard["steps"] if "upload-artifact" in str(s.get("uses", "")))
    assert _expr(upload.get("if", "")) == "always()", "a red shard must still upload"
    assert upload["with"]["name"] == "junit-required-shard-${{ matrix.shard }}"
    download = next(s for s in agg["steps"] if "download-artifact" in str(s.get("uses", "")))
    assert download["with"]["pattern"] == "junit-required-shard-*"

    decide = next(s for s in agg["steps"] if "--aggregate" in str(s.get("run", "")))
    run = str(decide["run"])
    assert "ci_required_tests.py --aggregate" in run
    # The job result is checked as well as the files: a shard that failed
    # AFTER writing a clean-looking junit must still fail the gate.
    # The failure itself is unit-tested in test_ci_required_tests; here, pin
    # that the real job result reaches it and that the step's exit code is the
    # script's (a single command, nothing after it that could exit 0).
    assert decide["env"]["SHARD_RESULT"] == "${{ needs.required-tests-shard.result }}"
    assert '--shard-job-result "$SHARD_RESULT"' in run
    code = [
        line.strip()
        for line in run.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert code[0].startswith("python scripts/ci_required_tests.py --aggregate"), code
    assert all(line.startswith("--") for line in code[1:]), code
    assert all(line.endswith("\\") for line in code[:-1]), code
    assert "continue-on-error" not in decide


def test_shards_run_the_reviewed_runner_with_the_shard_floor() -> None:
    run = "\n".join(s.get("run", "") for s in _load()["jobs"]["required-tests-shard"]["steps"])
    assert "ci_required_tests.py" in run
    assert "--profile shard" in run
    assert "--min-ran" not in run, "the per-shard floor comes from --profile shard"


# ---- PR-time affected tests ----------------------------------------------------


def test_affected_tests_run_on_the_pr_only_and_never_carry_a_required_name() -> None:
    """The PR slice is advisory: the merge-group shards stay the gate."""
    job = _load()["jobs"]["affected-tests"]
    assert _expr(job.get("if", "")) == "github.event_name == 'pull_request'"
    assert not str(job["name"]).startswith("required-tests")
    assert job["strategy"].get("fail-fast") is False
    checkout = next(s for s in job["steps"] if "actions/checkout" in str(s.get("uses", "")))
    # HEAD^1 of the pull_request merge commit is the base tip; depth 1 has no parent.
    assert checkout["with"]["fetch-depth"] == 2


def test_affected_tests_select_then_run_their_slice_through_the_gate_script() -> None:
    job = _load()["jobs"]["affected-tests"]
    run = "\n".join(s.get("run", "") for s in job["steps"])
    assert "scripts/affected_tests.py --base HEAD^1 --out affected.txt" in run
    assert "--affected affected.txt" in run
    assert "--profile affected" in run
    # Same exclusion as the required shards: the heavy list is red at baseline.
    assert "--exclude-from .github/heavy-test-files.txt" in run
    n = re.findall(r'--shard\s+"\$\{\{ matrix\.shard \}\}/(\d+)"', run)
    assert len(n) == 1
    assert job["strategy"]["matrix"]["shard"] == list(range(1, int(n[0]) + 1))
    assert re.findall(r"/(\d+)$", str(job["name"])) == n
    # The same split as the queue's shards: a different split co-locates
    # different neighbours, and an order-dependent test then reds the PR job
    # on a failure the queue never produces.
    required = _load()["jobs"]["required-tests-shard"]["strategy"]["matrix"]["shard"]
    assert job["strategy"]["matrix"]["shard"] == required


# ---- the conservative merge gate (round-2 fixes from the #4359 review) ------


def test_select_installs_before_it_selects() -> None:
    """Finding 1: without deps the conftest probe raises and selection is ALL.

    That fallback is correct -- it is the only safe direction -- but it made the
    gate INERT while still paying for the select job. The install is therefore
    load-bearing, not an optimisation, and it has no `if`.
    """
    steps = _load()["jobs"]["select"]["steps"]
    install = next(
        i for i, s in enumerate(steps) if "pip install -e" in s.get("run", "")
    )
    pick = next(
        i for i, s in enumerate(steps) if "affected_tests.py" in s.get("run", "")
    )
    assert install < pick
    assert "if" not in steps[install]
    assert not steps[install].get("continue-on-error", False)


def test_the_gate_asks_for_the_conservative_selection() -> None:
    """`--gate`, not the advisory PR-time mode.

    Without it the merge group would trust a selection the import graph cannot
    prove complete; the review reproduced two real omissions (a function-local
    import and a json data file).
    """
    run = next(
        s["run"] for s in _load()["jobs"]["select"]["steps"]
        if "affected_tests.py" in s.get("run", "")
    )
    assert "--gate" in run
    assert re.search(r"affected_tests\.py\s+--gate\s+--base", run), run


def test_the_selection_is_pruned_before_it_is_digested() -> None:
    """Finding 3: a slow-only file would fail coverage and exit its shard 5.

    Order matters -- pruning after the digest would pin a digest for a
    selection the shards never ran.
    """
    run = next(
        s["run"] for s in _load()["jobs"]["select"]["steps"]
        if "affected_tests.py" in s.get("run", "")
    )
    assert "--prune-to-collectible affected.txt" in run
    assert run.index("--prune-to-collectible") < run.index("--print-selection-digest"), run


def test_a_selective_run_still_refuses_a_skipped_browser_proof() -> None:
    """Finding 4: coverage accepts a skip, and these tests skip themselves.

    Both scopes must assert the proofs; the selective one is restricted to the
    selected files because an absent case is exit 1 and the unselected marked
    cases are legitimately absent there.
    """
    steps = _load()["jobs"]["required-tests"]["steps"]
    proofs = [s for s in steps if "ci_assert_junit_case.py" in s.get("run", "")]
    assert len(proofs) == 2, [s.get("name") for s in proofs]
    by_scope = {_expr(s["if"]).split("&&")[-1].strip(): s for s in proofs}
    assert set(by_scope) == {
        "needs.select.outputs.scope == 'ALL'",
        "needs.select.outputs.scope != 'ALL'",
    }, list(by_scope)
    whole = by_scope["needs.select.outputs.scope == 'ALL'"]["run"]
    selective = by_scope["needs.select.outputs.scope != 'ALL'"]["run"]
    assert "--only-files" not in whole
    assert "--only-files affected.txt" in selective
    for run in (whole, selective):
        assert "--marker real_browser" in run
        assert "|| true" not in run
