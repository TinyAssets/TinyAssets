# Lean CI Phase 1

Founder scope: move structural failures to PR time, require affected tests,
report summed test cost without gating, supply slow-test browser dependencies,
and correct pipeline documentation. No tests are deleted, skipped or xfailed.

The existing required-tests protection context enforces structural-guards on
all events and all six affected-tests shards on PRs. Failure, cancellation or
unexpected skip rejects admission. Keeping the existing context avoids adding
an expected check that older branches cannot emit. The queue still verifies
its combined commit; main and scheduled required runs remain the full backstop.

The structural runner centralizes existing guard targets with repair text,
without changing their assertions or quarantine. It rejects skipped guards.
It includes the Playwright collection-import guard from PR #4526 verbatim so
that lane and this one converge on the same file, rather than competing copies.
Existing invariants remain required separately. Cold dependency install time
is outside the approximately one-minute guard execution target.

MAX_TEST_SECONDS remains a telemetry reference. Test failures, missing shard
artifacts, coverage floors, skip counts and quarantine limits still gate.
Slow tests install dev,browser and Chromium; no test is deselected.

The research snapshot predates deletion of quality-gates.md. Its resurrection
is explicitly forbidden by test_rulebook_ratchet, so the current pipeline is
consolidated into executable-gates.md within its existing byte ceiling.
Live GitHub API checks confirmed strict=false and active organization ruleset
24065294 (main merge queue), enforcing the existing four required contexts.

Enabling all guards exposed pre-existing served guidance over budget on the
local runtime. Webhook, recurring-work and connection setup details move
verbatim into existing on-demand handbook chapters, with short resident
pointers. Both static prompt budgets stay unchanged; the plugin mirror is
regenerated. This repair is necessary to enable the guard without baseline red.

Expected effect: eliminate summed-time-only bounces (3 of 22 sampled failed
runs in the supplied research), and catch inventory, wording and structural
mismatches before queue admission. The measured 36% historical failure rate
is a baseline, not a predicted new rate. Interaction defects and queue cascades
remain possible. Phase 2 inventory generation/test removal and queue tuning
are deferred. This draft does not merge or deploy the change.


## Cross-family review and validation

Claude reviewed the complete Phase 1 diff through peer-agents (196 seconds),
verdict **ADAPT**. Lead disposition:

- **AGREE:** make the shell result-propagation tests portable to Windows/WSL.
  The repeated local quoting failure was handed off under AGENTS loop item 7.
- **AGREE:** exercise the unconditional structural gate on success, failure,
  cancellation, skip and missing result, not just the PR-only gate.
- **AGREE:** add ci_structural_guards.py to the scope guard's release-critical
  paths and its classification regression test. Removing targets now requires
  the same declared review as changing the other gate machinery.
- **AGREE:** measure the runtime before judging the five-minute job timeout.
  The standalone Linux oracle runner executed 568 cases, zero skips, in 76.58s
  (79.62s including Python startup). Hosted CI dependency install took 29s.

The broader Linux oracle run passed 680 tests with zero skips in 112.98s,
including the changed runner/selection/workflow tests, every structural target
and the full converse-turn-cost file. Static prompt limits are unchanged.
The plugin mirror was rebuilt and import-probed. Ruff and pre-commit invariants
passed. Actionlint covers both edited workflows, using CI's existing
SC2002/SC2129 style exclusions for the scope workflow's legacy shell blocks.
Hygiene reports zero removed tests and zero tampering findings.

The peer also confirmed fail-closed result propagation, retained queue coverage,
unchanged skip/quarantine budgets, verbatim handbook relocation, and no conflicting
lane edits. Its ADAPT verdict is recorded as such; the lead accepts the corrected
implementation after the focused checks below, rather than claiming a second
Claude approval round.

Focused post-review validation: Linux oracle **163 passed, zero skips, 4.73s**;
Windows **163 passed, 20.37s**. The two result-propagation tests send LF-preserving
binary stdin to bash, avoiding both WSL quoting and Windows newline conversion.
Hosted structural job [112147412256](https://github.com/TinyAssets/TinyAssets/actions/runs/37426408176/job/112147412256)
passed all 568 checks: 109.15s pytest time, 112s guard step, 29s dependency install,
150s total. This is above the roughly one-minute target, but well within the
five-minute timeout; further scanner/runtime optimization is left visible rather
than removing guards to manufacture a faster number.
