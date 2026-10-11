# Review and reproduction state

## Closed September21: consolidated acceptance and model-edit follow-up

The earlier cancellation display follow-up passed the saved September9 reply.
PR3900 then exposed existing model-policy edits, deployed5394efd September21;
root53Windows/53Linux zero skips, Fable shape review and root independent code
review, required checks and protected/public canaries all passed. App20:46PDT
patched its original workflow and completed its run without replacement. Its
20:56PDT five-control response explicitly says All five are working / Nothing
is blocked. Full receipt: 2026-09-21-served-node-policy-deployment.md (archived in git).
Canonical lifecycle requirement is byte-equivalent (normalized line endings)
to this complete delta; archived with --skip-specs to avoid duplicate addition.
No fresh organic owner-use evidence; broader cloud/cancellation containment,
effect-edit parity, intermittent parallel failure and onboarding remain open.

## Historical state: four gaps accepted live, cancellation display follow-up

At Sep 8 23:30-23:31 PDT, the app confirmed editing, exact output, discard and
failed-node status fixed. It confirmed cancellation works and all nine older
checklist checks passed, including fresh webhook200/204 and cleanup. It still
reports cancelled code nodes as running; all-five closure therefore remains open.

Follow-up shape/basic-safety review: Claude APPROVE, exit0 after219seconds,
docs/reviews/2026-09-09-cancel-node-state-shape-review.md. Source confirms sandbox
cancellation has no terminal node event, both translators lack that phase, fold
priorities reject it, and diagrams lack its class/use definition IDs. It also
confirms between-node cancellation occurs before recording an actually completed
node. No stored-status constraint or migration is required.

Red tests reproduced actual child stopped/run cancelled/node running, fold
priority, diagram identity/class, both resume terminal phases, and initial
between-node completion. Initial resume test used an unsupported requested_by
keyword; corrected before its substantive red. The real predecessor/successor
fixture initially collided state-field names with node IDs; corrected to *_out
fields without changing validation. It now passes real A-ran/B-cancelled/C-pending
and verifies NodeCancelledError event provenance under graph-instance identity.

Follow-up candidate emits only an observed cancelled phase, records it through
both translators, gives it terminal fold priority, and colors actual graph IDs.
The cooperative checkpoint remains, after persisting the node's real ran event.
No authority, launch, kill, cleanup, timeout or resource policy change. Resume
coverage is the actual saved-row event translator driven by an injected compiled
graph, not a new full resumed-jail child-stop proof. The initial real-child path
is exercised. Windows/Python 3.14 candidate:233 passed, one platform skip;
pinned unchanged deployed00826957:226 passed, the same skip. Exact testcase
identity/outcome comparison finds seven added passes and no lost/changed base
cases. Command on both trees:
`python -m pytest -q tests/test_graph_run_controls.py tests/test_graph_compiler_failed_event.py tests/test_graph_compiler_provider_chain_propagation.py tests/test_resume_run.py tests/test_branch_runner.py tests/test_api_runs.py tests/test_cancel_reaches_the_running_child.py tests/test_queue_cancel_cooperative.py tests/test_node_timeout.py tests/test_graph_compiler_empty_response.py tests/test_runs_schema_migration.py --tb=short --junitxml=output/workflow-cancel-wide-head.xml`
Baseline used the same selection with an absolute output path to
workflow-cancel-wide-base.xml from a detached temporary worktree at00826957.
Ruff, mirror/import and strict OpenSpec checks pass. Exact-head review,
Linux/CI, deployment and the next rendered retest remain required.

## First deployed round

September 9, 2026 UTC. PR #3591 merged as
0082695793278fabf520c9bf2a8fa2694c4a2823 after exact-head Claude APPROVE for
436f72b8370e6cfbff00184b5f0a0a63f75dc0df (exit 0, 157 seconds). Follow-up runtime
and mirror were byte-identical to the earlier fully reviewed 41df8819.
Receipt: https://github.com/Jonnyton/TinyAssets/pull/3591#issuecomment-5596798037.

Linux Tests 34317601056 passed required-tests in 14m13s and slow-tests in 1m30s.
Actual checkout e570f22e7f2eef65299849b76fb2dd3e7f80e554 merges reviewed head
436f72b8 with 87c520e9. Fetched that exact commit; `git diff --exit-code
436f72b8 e570f22e -- tinyassets packaging tests` is empty. Only three unrelated
Play-release documents differ across the full tree. Focused JUnit set comparison:
441 passes, two Windows-only skips versus baseline 390+2. No new failures and
no formerly passing focused cases missing/nonpassing. Full required artifact:
14816 passes, 54 skips, nine failures and two collection errors, with all errors/
failures identical to existing baseline cases. Passing required gate is not a
claim that every repository test passes; heavy files remain untested on this PR.
Receipt: https://github.com/Jonnyton/TinyAssets/pull/3591#issuecomment-5596933164.

Image build34318730934 passed and deploy34318999188 succeeded at06:26 UTC.
Authenticated public handle canary passed; protected SHA check at06:26:34Z
reported SHIPPED for0082695793278fabf520c9bf2a8fa2694c4a2823. Exact ordinary app
prompt sent and read back at23:27 PDT: Retest your workflow checklist. The app
is thinking; rendered all-five closure is still pending. The following sections
preserve dated implementation history, not current release status.

## Historical candidate: five platform gaps implemented locally

### First exact-head review and Linux CI feedback

Claude approved 41df881917c30ff5421bde4eaba3a8cde4356447 (terminal exit 0,
444 seconds). Full same-session receipt:
https://github.com/Jonnyton/TinyAssets/pull/3591#issuecomment-5596516658.
PR #3591 remains draft. Two nonblocking P3 notes: runner persists dict output;
the exact field selector intentionally does not trim authored names.

Linux Tests 34316150848 ran checkout 25fe2bba6647f250a255d22be56e9f3876445eaf,
verified in both checkout logs and full-tree identical to approved head. Slow/
stress job passed. Selected non-heavy workflow/control files: **441 passes, two
Windows-only skips**, versus 390+2 on main run 34314054165 (9e9f8397). Three heavy
files were not run on this PR; do not count them as candidate Linux passes.

The overall required job refused four NEW failures: the new concern lacked its
index row and ISO filed date, a separate effect test still expected the broken
patch_node advice, and an automation-stop fixture invented run_in_flight without
ever persisting a run. No new production defect was inferred from the latter:
automations._execute calls on_run_started only AFTER execute_branch_async returns
its persisted ID (automations.py:1160-1175). The test now creates an actual run,
still proves stop requests cancellation, and adds completed-state no-insert proof.
The effect assertion now requires op=update_node and rejects patch_node; all
original classification assertions remain. Concern indexing/date corrected.
No runtime changes, quarantine edits or test skips are needed for this feedback.

Follow-up Windows command:
`python -m pytest -q tests/test_automations.py tests/test_effects_at_node_time.py tests/test_concerns_index_matches_the_directory.py --tb=short --junitxml=output/workflow-gaps-ci-feedback-head.xml`
Result **193 passes**, versus 191 on pinned c2ed4534 (same command, output file
workflow-gaps-ci-feedback-base.xml). The two added cases are the concern's date
check and terminal-automation cancellation coverage. Re-run normal CI and obtain
an updated exact-head receipt before readying the draft; prior receipt names only
41df8819. No app prompt or live workflow edits have occurred.

September 9, 2026 UTC, Windows/Python 3.14. Source is not pushed or deployed.
read_graph run_output now always uses bounded formatting in the existing output
handler: catalog pages of 64 fields, exact strings/typed small values and Unicode
JSON continuation for larger fields. Existing legacy extension response remains
compatible. Ordinary run reads discover fields and report pending cancellation.
Both existing run/list and new output/cancel routes match actual run universe
before ACL/disclosure. A public foreign universe cannot bypass the pin.

run_graph cancel reuses the run_cancels table and existing executor polls, with
no admission, new run or provider launch. Its SQL insert tests nonterminal state
atomically; repeated terminal calls return actual status. Real queued and child
tests exposed a NodeCancelledError constructor bug (node_id keyword became a
TypeError), now corrected. Production-style resolve-always scope tests also
exposed the legacy admin classification: cancel is now an ordinary write, with
an added legacy-record owner/actor check so it cannot broaden cross-user reach.
No admin capability is granted to the served agent. The first classification
edit accidentally placed cancel in the costly override; live-style tests caught
it, and the final implementation uses the existing _RUN_WRITE_ACTIONS default.

Real owned-branch edit/readback follows the refusal's taught payload and rejects
a different actor without changing storage. Real code exception/timeout tests
cover identical and distinct graph/definition IDs plus successful parallel
siblings. Timeout attribution needed exception.node_id normalization as well as
event normalization; the same original exception type/object is propagated.

Red evidence: initial graph controls suite had 18 expected failures; real running
child then failed with TypeError rather than cancelled; distinct graph-ID timeout
cases failed with the definition ID. Final graph-controls suite has 39 passes,
including production-style permission gating, read-only refusal, foreign-public
pin refusal, legacy foreign-owner refusal, race-at-finish, bounded Unicode/types,
and real queue/child stopping. Node state/timeout/empty-response suite: 41 passes.

Full focused candidate command:
`python -m pytest -q tests/test_graph_run_controls.py tests/test_api_runs.py tests/test_engine_mcp_server.py tests/test_engine_mcp_write_graph_patch.py tests/test_branch_runner.py tests/test_universe_server_isolation.py tests/test_text_channel_id_redaction.py tests/test_run_branch_version.py tests/test_graph_compiler_failed_event.py tests/test_node_timeout.py tests/test_graph_compiler_empty_response.py tests/test_workspace_effector.py tests/test_cancel_reaches_the_running_child.py tests/test_queue_cancel_cooperative.py tests/test_canonical_branch_mcp.py tests/test_mcp_instruction_surfaces.py tests/test_action_scopes.py tests/test_optional_auth_mode.py --tb=short --junitxml=output/workflow-gaps-windows-head.xml`
Result: **560 passed, 6 skipped, 1 pre-existing failure**, 63.91s. This is not a
green suite or Linux proof. A detached, unchanged c2ed4534 checkout ran the same
existing 15 files (the new graph-controls file did not exist) with 486 passes,
6 skips and the exact same instruction-response fixture failure in
output/workflow-gaps-pinned-windows-base.xml; the two auth files separately pass
23 tests on both baseline and candidate. Baseline total: 509 passes +6 skips +1
failure, versus candidate 560+6+1. Two legacy terminal-cancel assertions now test
actual completed status and absence of a cancel record, not a fabricated pending
cancel after completion. No tests or platform assertions were dropped.
Baseline fixture finding:
`docs/concerns/2026-09-09-instruction-response-test-page-fixture.md`.

Generated plugin mirror/import probe passes. Independent exact-head review,
Linux comparison/CI, authenticated deployment and rendered app retest remain
required. No app prompt has been sent during this local implementation.

## Historical shape and partial-candidate evidence

At shape review, September 9 UTC, there were no runtime edits yet; branch codex/close-workflow-control-gaps
starts from c2ed4534 (runtime 8f1b4760). Independent Claude shape/basic-safety
review dispatched with eight-minute timeout to `output/workflow-gaps-shape-review.md`.
Command: `python scripts/peer_agent.py claude --out output/workflow-gaps-shape-review.md --prompt-file output/workflow-gaps-shape-brief.md --timeout 480`.
Claude completed exit 0 after 282 seconds: VERDICT ADAPT. Its self-contained
final artifact confirms all five source anchors and gives four bounded
adaptations, incorporated in design/spec before runtime edits:
keep the existing edit-field restrictions but correct all four wrong operation
strings; pass separate compiler ancestry to workspace only; use bounded exact
output chunks; emit failed code events with the same graph key as starting.
Basic-safety blocker added to scope: existing served run read drops the pin and
can admit another public universe's run. Require uniform not-found graph-match
checks for existing run, output and cancel, then normal ACL. Cancel guidance
must also stop claiming cancellation is unavailable. No tests run by reviewer.

Baseline Windows command:
`python -m pytest -q tests/test_engine_mcp_write_graph_patch.py tests/test_engine_mcp_server.py tests/test_api_runs.py tests/test_workspace_effector.py tests/test_cancel_reaches_the_running_child.py tests/test_queue_cancel_cooperative.py --junitxml=output/workflow-gaps-windows-base.xml`
Result **282 passed, 6 skipped**, 21.28 seconds. This is not Linux proof; the
legacy queue-helper tests also do not establish run cancellation surface behavior.

New two-case real-dispatch workspace regression at unchanged runtime:
`python -m pytest -q tests/test_workspace_effector.py -k discard_uses_graph_ancestry_through_real_dispatch --junitxml=output/workflow-gaps-discard-red.xml`
Result **1 expected failure, 1 pass**: actual creation registers a held workspace
under a graph-instance ID, but legitimate descendant discard falsely refuses
"not one of its graph ancestors" because the HTTP-result map is empty. A parallel
non-ancestor correctly refuses. This reproduces the reported gap without editing
a live workflow or pretending a workspace produced an HTTP result. The first test
draft had an incorrect cleanup-helper import; corrected to the existing chain
settle method before this substantive red result. Test data root is explicitly
temporary and outside the repository.

Source localization also finds ordinary output omitted by _compose_run_snapshot,
existing legacy output/cancel implementations, wrong patch_node prose versus
update_node vocabulary, and missing code failure event emission. Those are
source findings pending their own executable regression and end-to-end proof.
The primitive checker reports false-negative CLEAN for legacy cancel/output
actions; reuse actual inspected handlers, do not invent duplicates.

Failed-event baseline: `python -m pytest -q tests/test_graph_compiler_failed_event.py --junitxml=output/workflow-gaps-failed-event-base.xml`
reported 9 passed. Two new real-child code-failure cases (same ID and distinct
graph/definition IDs) fail because no failed node event is recorded. The initial
test draft lacked branch authorship and correctly refused foreign code; giving
the fixture its actual actor as author reaches the intended error and reproduces
the missing event without bypassing production authorship. The local launcher
override exists only in the isolated test; production sandbox remains unchanged.

## Initial candidate, not ready to ship

Separate trusted ancestry now reaches workspace create/push/discard; direct
internal callers and the two prior ancestry tests use the new keyword rather
than HTTP-result membership. The full workspace effector suite passes 144 tests
with its two unchanged Windows skips (9.07 seconds); the new real-dispatch valid
and sibling cases pass. Linux proof remains required.

Code failure now emits the existing failed event and graph-instance wrappers
normalize starting/terminal event identity. Both real-child regression cases pass.
Five (not four) wrong patch_node guidance strings were found and corrected;
unknown-op guidance supplies a payload accepted by the actual sanitizer.
Combined failed-event/edit/API suite: 51 passes, 18 deprecation warnings before
the final guidance relational test addition. More parallel/cancel/timeout and
real owned-branch coverage is still required; these changes are not live.

Remaining implementation: shared pinned run-scope checks, exact bounded output
catalog/field reads, and reachable cancellation with actual queued/running/
terminal semantics. No review is running now: shape review finished. No app
prompt has been sent since the owner's read-only inspection.

Final partial candidate Windows check, September 9 UTC:
`python -m pytest -q tests/test_engine_mcp_write_graph_patch.py tests/test_engine_mcp_server.py tests/test_api_runs.py tests/test_workspace_effector.py tests/test_cancel_reaches_the_running_child.py tests/test_queue_cancel_cooperative.py tests/test_graph_compiler_failed_event.py --junitxml=output/workflow-gaps-partial-head.xml`
reported **296 passed, 6 skipped**, 18 deprecation warnings, 19.33 seconds.
This equals 282 original passes + 9 existing failed-event cases + 5 new cases.
Ruff on the nine changed canonical/test files passes, generated six-file plugin
mirror rebuild/import passes, strict OpenSpec and diff check pass. No Linux
candidate proof or exact-head code approval yet; do not push this partial branch
as ready or deploy it before output/cancel/scope work and the remaining proof.
