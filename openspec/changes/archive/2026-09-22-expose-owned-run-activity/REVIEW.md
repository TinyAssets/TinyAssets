# Pre-build shape review

September22,2026 UTC. Independent Claude Fable subscription review13185 finished
exit0 after210s on tree f2b3fc00b92f5515b07118b07605ee527a2a4a2c, identical to
deployed cad07bd0. Read-only, no tests. Verdict READY_TO_PROPOSE, not exact-head
implementation approval. Brief/result remain in root output/run-diagnostics-shape-*
and this record preserves the decision that gates building/push/rollout.

Reviewer supports adding typed metadata to existing target=run instead of a new
event target, preserving existing scope/ACL/envelope and reusing stored events.
Required holes: no raw detail copying, distinguish local start from provider
acknowledgment, do not treat terminal-row timestamp span as execution duration.
Suggested tests: completed evidence after later failure, payload allowlist,
unknown evidence, timing, scope and unchanged status/adapter behavior.

Lead source verification/dispositions:

- AGREE: api/runs.py1523 loads events, snapshot1341 loses useful metadata;
  runs.py3832-3841 records local start,3892-3893 stamps terminal observation;
  graph_compiler.py1487 emits returned-call detail.
- Refine timing wording: the terminal timestamps are separate clock calls, so
  their span is not literally guaranteed zero; it is not execution duration.
- Confirm returned does not mean validated: graph_compiler.py1504 onward parses
  the JSON contract after the ran event. Reviewer did not inspect that tail.
- Adapt: prefer strict normalized execution receipts from
  providers/execution_receipt.py; do not omit actual answering-model evidence
  while promoting legacy configured-model fields instead.
- Adapt: omit free-form failure_message rather than truncating arbitrary event
  exceptions into newly exposed metadata. Raw data remains under existing reads.
- Adapt: local start count is not provider attempt count; loop-return receipts
  need their own step/time to avoid attribution to a later attempt.
- Scope correction: _run_matches_scope compares the resolved universe, not the
  actor; canonical read ACL permits visibility/grant-authorized readers, while
  served graph selection stays pinned. This patch changes no grant policy.
- A bounded metadata projection is not a new telemetry producer or proof of the
  historical stall cause. Broader runtime reliability remains open.

Before ready/merge: independent exact-head review of implementation and test
evidence is still required. No implementation verdict is inferred from this
pre-build recommendation.

## Implementation verification (not release approval)

September22,2026 around01:38UTC, Windows Python3.14: base cad07bd0 tree
(frozen node-stream-budget worktree) selected snapshot/graph-receipt/engine
tests: 93 passed, 3 skipped. Candidate with tests/test_run_activity.py and
tests/test_run_activity_surface.py added: 216 passed, the same 3 skipped.
Command: `python -m pytest -q tests/test_run_activity.py
tests/test_run_activity_surface.py tests/test_run_snapshot_phase.py
tests/test_graph_answer_execution.py tests/test_engine_mcp_server.py`;
baseline omits the two new files. No new failure or skip.

Opus test builder46287 wrote tests/test_run_activity.py within its sole file
ownership, then exceeded420s and was killed by the wrapper (exit1). No passing
result or release approval is attributed to that peer. Lead read its entire
test file, retained it, and ran the tests above. Lead owns the integration tests.
Initial integration setup used legacy OAuth scopes incorrectly; corrected to
explicit extensions.read for canonical OAuth and the serving resolve-always
founder mode for engine reads. Actual private-universe ACLs and pinned selectors
are tested before events load; no runtime authorization policy was changed.

Plugin build completed497files/import probe-ok. Linux baseline:96passed/no skips.
First Linux candidate:219passed/no skips, but plugin rebuilding overlapped its
archive capture and emitted changed-file warnings. Do not treat that copy as
frozen-tree release proof; a clean candidate rerun is required before freeze.

Clean candidate rerun46537 completed exit0 after archive capture with no changed
file warnings: Linux Python3.11.15/git2.47.3/bwrap0.12.0,219passed in5.07s,
no skips (baseline96passed in6.89s). Command: `python scripts/linux_oracle.py
-- -q tests/test_run_activity.py tests/test_run_activity_surface.py
tests/test_run_snapshot_phase.py tests/test_graph_answer_execution.py
tests/test_engine_mcp_server.py`. Ruff, `git diff --check`, seven pre-commit
invariants and strict change validation also passed before release-review freeze.

## Exact-head review1: ADAPT

Fable34490 completed exit0/314s at abd21c920ec90cb4598d087c14630cb2d5ff64f4,
verified unchanged and mirror equal; independently ran surface tests6passed.
AGREE on truthful event/receipt semantics, metadata allowlist, ACL-before-load,
pins and unchanged envelopes. DISAGREE_CONCERN: placing the new fields before
existing guidance pushes that guidance beyond the6000-character text prefix
at roughly8nodes. Lead accepts this concrete compatibility risk before release.
Move the additive fields to the end of get_run after cancel_requested, and add
a10-node text-prefix regression. No authorization or envelope-policy change.

Also add a producer-shaped outer-validation-failure test: real generic validation
failure updates the run only, without a new failed node event. Existing pure
ran-then-failed fixture tests fold semantics, not a claim that that producer
emits the hypothetical failure row. Design clarifies both facts and elapsed
start-to-failure versus separately tagged return time. New exact-head review
is required; review1 does not approve the adapted head.

Adapted tree verification at01:48UTC: same Windows selection218passed/3unchanged
skips; clean Linux oracle221passed/no skips in4.43s. Two new cases cover the
ordering and actual validation-failure event shape. Focused new files125passed.
Ruff, rebuilt497-file mirror/import, seven invariants, strict change validation
and diff check all pass. No baseline failure was hidden or new skip introduced.

## Live follow-up: bounded Fable presentation review

PR3909 subsequently passed exact-head Fable81316 approval and CI and deployed
as8f4ee4a8 at02:15UTC September22. See
rendered acceptance (archived in git).
The app could recover stored failed-run evidence but misread model_status as
provider acknowledgment, including after one ordinary correction. No further
coaching or private-workflow rerun was used to manufacture acceptance.

Independent Fable50428 completed exit0/113s at02:31UTC, verdict CLARIFY_SURFACE.
It confirmed a generic truncation risk: current caveat follows the node array,
while tests preserve only legacy guidance. Recommended legacy guidance first,
then caveat, then node array, plus explicit definitions of both existing model
status values. Full structured data remains unchanged. This is shape review,
not exact-head approval of the follow-up implementation.

Lead accepts the proposal. Regression first: two surface tests failed on the
deployed runtime, including missing activity_evidence in a13-node text prefix.
After the two-line order swap and constant clarification: Windows218passed/
same3skips, matching the previous frozen five-file selection. Baseline surface
alone8passed before new assertions. Ruff, strict change/canonical spec and
497-file plugin mirror/import pass. No runtime policy, data or enum changes.
Canonical spec is synced but archive/closure waits for follow-up release proof.
Clean Linux oracle selection221passed/no skips in4.32s, matching prior baseline;
seven pre-commit invariants passed after mirror generation. No archive capture
overlapped code or mirror edits. Exact-head review remains required.

## Closeout

PR3910 subsequently received exact-head Fable9757 approval, required CI and
verified production deployment. Original20:13PDT rendered saved-run inspection
correctly distinguished recorded model identity from acknowledgment/admission
and validated output. See the linked acceptance record for immutable commit,
deployment and test evidence, plus limits of the live answer. Canonical spec
matches the delta. This archive closes only stored-activity exposure and its
presentation clarification. Historical failures and organic-use watch remain open.
