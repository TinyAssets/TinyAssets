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
