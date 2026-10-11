# Workflow checklist deployment loop

## Founder direction, 2026-09-04

Deploy the current round of generic platform fixes, then send the existing
TinyAssets webapp conversation exactly:

> Retest your workflow checklist

Use the bound agent's rendered response to identify the next platform fix;
review, merge, deploy, and repeat until its checklist passes. Do not add
coaching or directly manage the user's workflow definitions or universe state.
This supersedes the previous instruction to stop before sending a live retest.

Founder clarification, 2026-09-04: success is the app agent reporting that it
can build the workflows it wants using reusable platform capabilities. Never
build, repair, or edit its private workflows to manufacture passing probes.
Continue the exact-prompt loop; its independent checklist is the acceptance
criterion, not platform-side test results alone.

Founder clarification, 2026-09-08 20:33 UTC: the prohibition applies to Codex
directly editing private workflows. Users must be able to ask their own app
agent to make needed changes. Codex is explicitly authorized to converse as
that user: ask the app to fix its remaining issues or explain what prevents it.
This supersedes the exact-message-only restriction for diagnostic/repair
conversation, not the exact post-deploy retest prompt or the prohibition on
operator-side private edits. Do not infer a host-only destination blocker before
the app has attempted the ordinary user-authorized repair path. Fix reusable
platform capabilities when the app surfaces a real missing tool or restriction.

## Current round

The workspace effect dispatcher did not forward the node timeout into pool
admission, leaving both create and checkout on the zero-wait default.
The fix forwards that budget into one retry after the initial admission probe
and reconciliation sweep. The existing transactional pool waits only on locks.

Windows Python 3.14 verification, 2026-09-04:

- `python -m pytest -q tests/test_effects_at_node_time.py tests/test_workspace_effector.py tests/test_workspace_pool.py`: 243 passed, 3 skipped.
- Broader workspace/runtime selection: 194 passed, 22 skipped.
- Ruff, plugin import probe and mirror parity passed.
- `python scripts/linux_oracle.py -- -q tests/test_effects_at_node_time.py tests/test_workspace_effector.py tests/test_workspace_pool.py`: could not start because Docker Desktop's Linux engine was unavailable. Startup then failed at its inference socket. Linux CI remains the landing gate.
- Claude review: APPROVE; recovered from its completed transcript because the wrapper retained only a closing message. Reviewer independently ran 140 tests (2 skipped).

Latest user checklist evidence was against `b1ec544c` around 21:29 UTC,
before terminal lock release deployed as `2102d630` at 21:33 UTC:
heartbeat, missing-input preflight and workspace happy path passed;
sequential and parallel probes were running; concurrent workspace refused;
external delivery reached a stale destination and received HTTP 404.
The intentional failure probe v3 is excluded.

## Deployed round, 2026-09-04 22:26 UTC

PR #2951 merged as `0512f3353bd5721c6bfa36b37e45eafeeb66e635`.
Linux required and slow tests passed in run `33924110227`. Linux, macOS and
Windows builds plus the Windows install test passed. Image build `33925175365`
published the exact revision; automatic deploy `33925440487` succeeded.
Its authenticated `python scripts/mcp_public_canary.py --url
https://tinyassets.io/mcp --assert-handles` and protected
`python scripts/deployed_sha.py --url https://tinyassets.io/mcp
--assert-contains 0512f3353bd5721c6bfa36b37e45eafeeb66e635` both passed.

The rendered app requested sign-in before the retest was sent. Sign-in opened
the existing AuthKit page; the browser integration then reinitialized and the
mission tab disappeared. No checklist prompt was sent and no private workflow
was modified. Asked the founder to restore the signed-in existing conversation;
the exact-prompt acceptance loop remains incomplete.

## Restored app, 2026-09-04 22:57–22:59 UTC

Founder restored the tab and explicitly told this task to proceed. Sent exactly
`Retest your workflow checklist`; the user message rendered verbatim, but the
app reported an unknown turn failure. One unchanged retry after the provider's
120-second cooldown failed identically. The bounded server log showed the
model subprocess exiting before a turn, and double truncation of the provider's
error hid its terminal cause. The recovery evidence is recorded below; no checklist
row has been newly accepted, and no private workflow state was edited.

Next round preserves the head and tail of already-scrubbed provider failure
details within the same 200-character server-log budget. Independent review
caught seven earlier router cuts, now replaced with the same scrub-before-cut
helper. No diagnostic fields, authority, routing, or workflow shape changes.
Focused Windows tests: 116 passed across failure notices and provider routing;
the real-router regression was first demonstrated red against the old router.

Rollback for this diagnostic-only round: deploy the last healthy immutable
image through the normal fail-safe release workflow if startup or connector
health regresses. No storage migration or private-state rollback is involved.

## Diagnostic round deployed, 2026-09-04 23:32 UTC

PR #2959 merged as `97da0eadc3d3ca0bf6edee81394aaf922582978a`.
Image build `33929636420` and deploy `33929823161` passed, including the
authenticated public canary and protected deployed-SHA assertion. Retried the
unchanged exact prompt in the existing app at 23:34 UTC; it still rendered an
unknown turn failure. No checklist row is newly accepted.

Stopped blind retries after the third failed attempt and obtained independent
Claude diagnosis. Subsequent read-only metadata narrowed one confirmed defect:
live catalogue HTTP 200 declares `max`, which deployed Codex CLI 0.135.0 cannot
deserialize. The next generic compatibility round is isolated on
`codex/provider-catalogue-compat`; no workflow, provider binding, credential, or
user state is altered. See the recovery record below for evidence and limits.

## Compatibility round verification, 2026-09-04 23:56 UTC

CLI pin 0.153.4 and explicit workspace-write launch replace the removed flag;
the platform keepalive job receives the same flag repair. The shape reviewer
required a real launch check, feature-default comparison, and preserving
scrubbed JSON error messages. Implemented all three; remote-plugin loading is
now explicitly disabled with account apps and shell tools. No private workflow
definitions, connection state, credentials, or model selections were changed.

Windows Python 3.14 baseline: 173 passed, 3 skipped. Changed focused selection
(`tests/test_codex_cli_compat.py`, providers, served_router, stream_watchdog,
dockerfile_shape, provider_sandbox): 189 passed, 3 skipped. Ruff and actionlint
passed; plugin import probe and mirror build passed. `python
scripts/codex_cli_smoke.py npm.cmd exec --yes --package=@openai/codex@0.153.4
-- codex` passed with an empty home, allowlisted environment, real required-MCP
startup, disabled account/plugin/shell features, and a fake loopback HTTP 401.
Docker image construction runs the same credential-free check on Linux.

`python scripts/linux_oracle.py -- -q tests/test_codex_cli_compat.py
tests/test_provider_served_router.py` could not start: local Docker Desktop's
Linux engine remains unavailable. Linux CI and Docker build must pass before
landing. Independent implementation review is running; no deployment claimed.

Rollback: normal fail-safe deploy of prior immutable image if startup or public
connector health regresses. No migration or user-state rollback is needed.

Final independent review approved 2026-09-05 00:10 UTC; durable artifact:
`2026-09-05-codex-catalogue-compat-claude.md (archived in git)`. The strengthened smoke
inspects actual model tool specs and MCP bearer headers. Both old and new CLI
pass; native file patching is explicitly reported as unchanged baseline,
contained by the existing read-only mounts. Keepalive and both non-served
launch tests now also pin account/plugin restrictions. Follow-up tests: 83
passed. Final Linux CI and live deploy/retest remain outstanding.

Linux required run `33932155764` completed 2026-09-05 00:25 UTC with 14,618
passing tests and two new ratchet failures: the new structured-error helper's
vendor-prefixed identifier increased the provider-name count from 75 to 76.
Mechanically renamed that generic JSON helper to `_structured_failure_excerpt`
and updated its references; no behavior, guard, or baseline change. Local
ratchet plus focused suite: 204 passed, 3 skipped. Final container build
`33932130912` had already passed the strengthened bearer/tool-spec smoke on
Linux CLI 0.153.4. CI must rerun on the naming-only follow-up before landing.

## Compatibility round landed, 2026-09-05 00:44 UTC

PR #2964 merged as `c30daa8f5ee86f8db64f6bf7c2ea7b1a7b94396b` after required
Linux run `33933159500`: 14,620 passed, 54 skipped, nine failed and two collection
errors all covered by the existing quarantine; zero new failures and zero stale
quarantine entries. The gate passed, not an entirely failure-free suite.
Production image build `33933967217` is in progress; merge is not live proof.

Founder separately requires compatibility with providers unknown to TinyAssets
without routine platform patches. The pinned CLI/catalogue repair does not meet
that architectural outcome; the verified execution/registration gap is recorded
in `docs/concerns/2026-09-04-provider-compatibility-is-not-open.md`.

Deploy `33934197685` completed 2026-09-05 00:50 UTC. Its authenticated
`python scripts/deployed_sha.py --url https://tinyassets.io/mcp --assert-contains
c30daa8f5ee86f8db64f6bf7c2ea7b1a7b94396b` gate confirms production contains
the fix; `python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp
--assert-handles` passed. At 00:51 UTC the existing browser conversation received
exactly `Retest your workflow checklist`; the message rendered verbatim and the
app is thinking. The previous retry button targets a different user message and
was not used. Checklist acceptance remains pending.

The 00:51 UTC app turn failed before a checklist reply. Retained diagnostics
prove HTTP 400 rejection of the implicit platform model, not provider absence:
`gpt-5.4` is unsupported for the connected ChatGPT account. A model-only
production `printenv TINYASSETS_CODEX_MODEL` read returned exit 1/unset.

## Native model recovery, 2026-09-05 01:05 UTC

Work continues on `codex/provider-native-model-defaults`. Removing the implicit
model argv pin preserves explicit operator overrides and all isolation flags;
outcome and runtime labels say `provider-default`, not an invented model name.
The founder clarified the durable target: provider default initially, user-saved
defaults and choice from all models available through their connection. PLAN and
the compatibility concern record that target; ignored connection-local model
selection remains open, not silently activated by this patch.

Independent shape review ADAPT supported the change and required label/document
alignment plus coverage. Baseline Windows selection: 153 passed, 3 skipped.
New native-default tests failed on the old forced model (3 failed, 2 explicit
override cases passed). Changed expanded selection: 183 passed, 3 skipped; Ruff
and plugin mirror import probe passed. Local Linux oracle could not start
because Docker Desktop's Linux engine is unavailable; Linux CI remains required.

Initial hold: credential-free real CLI smoke, now omitting the model flag,
resolved `gpt-6-astra` but omitted tools from outgoing model requests. The fake
required MCP initialized/listed tools; even advertising `read_graph` did not
produce a tools field. Three attempts failed the same inspection invariant;
stopped retries and dispatched independent round-2 diagnosis/review (peer output
`output/provider-native-model-review.md`, completed execution handle 67648).

Round 2 returned ADAPT at 01:14 UTC: implementation supported, fixture must
understand Responses-lite additional_tools input items and deferred MCP discovery.
After those corrections the native-default real CLI smoke passed; it still
requires nonempty tool specs and applies the forbidden-name walk. It explicitly
reports code-mode deferred engine visibility as unobserved, not a proven tool
call. This unauthenticated custom-provider run uses the bundled default, not the
account default. App retest remains the final proof; no checklist row is closed.

Release queue, 2026-09-05 02:12 UTC: PR #2977 merged as
`9d361262896519c1ab3c05c4154d6aac0a151588` at 02:08:56 UTC. Final independent
round-3 review APPROVE is recorded in
`2026-09-05-provider-native-model-defaults-claude.md (archived in git)`; no fourth round.
The first Linux run found two new failures (concern index entry and static command
shape parsing); mechanical corrections are pushed and 242 focused local tests
pass, 3 skip. Fresh required Linux run 33937064317 passed: 14,642 tests passed,
54 skipped, 9 failed and 2 collection errors matched the existing quarantine,
zero new failures and zero stale quarantine entries. This is a green baseline
comparison, not a failure-free suite. Both image smoke checks passed. Production
image publication 33938244843 is active. Production remains c30daa8f, and no app
prompt has been sent since its 00:51 UTC failure. Verify the actual deployed
target plus authenticated public canary, then send only the exact
retest request in the existing app conversation. Never claim this PR closes the
broader provider/model-selection gap or any checklist row before rendered proof.

Deployment verified at 2026-09-05 02:15 UTC: image build 33938244843 succeeded;
deploy 33938479563 passed authenticated `mcp_public_canary.py --assert-handles`
and protected `deployed_sha.py --assert-contains` for
`9d361262896519c1ab3c05c4154d6aac0a151588`. Exact retest request sent and rendered
in the existing app at 02:15 UTC; app is thinking. No private workflow, binding,
credential, model preference, or external destination was changed.

Rendered result at 02:17 UTC: the app reports all fresh runs settled. Sequential
`23afa7e5342146b3`, parallel `dda972835cc149f4`, heartbeat `675c4f54d8634185`
completed with provider-call evidence. Missing `context` was refused preflight.
Workspace `1c34376d6b9b498d` and overlapping `228acaa65de54cc3` completed; the
agent qualifies contention as smoke evidence, not fairness proof. External run
`6df9005077324601` still received HTTP 404 for the expired webhook destination.
The agent explicitly says the full nine-row checklist is NOT cleared and only
enumerates seven previously exercised lanes. At that read the other two rows were
unidentified/unverified; do not substitute these six PASS rows for full success.

Next: the existing host-action row for the expired exact delivery destination is
still needed; asked the founder asynchronously to restore/choose it through the
app's owner-controlled setup, without pasting credentials. No platform defect is
established merely by that third-party 404. No workflow/connection repair or
coaching is authorized. Preserve the full acceptance scope and obtain actual
rendered evidence for the omitted lanes as well. Native-default startup recovery
is verified; broader provider portability/model choice remains open separately.

## 2026-09-05 03:40 UTC — read-only review of new owner conversation

Rendered app read through `cua.getTab("1346516692", {browser:"2"})`, production
https://tinyassets.io/mcp/app. No prompt sent or workflow changed.

At 03:33 UTC the app reports another settled retest on 9d361262: sequential,
parallel, heartbeat, preflight and workspace execution PASS. Both overlapping
workspace runs completed, but the app explicitly keeps `workspace_busy` OPEN.
Delivery still fails HTTP 404. Do not convert overlap smoke success into closure
of the app's broader contention criterion.

At 03:35 UTC the app names `repo/channel-agnostic` as an eighth identifiable
check, citing a founder statement, but provides no fresh proof for that row in
the inspected messages. The ninth is still unidentified. Intentional v3 failure
is not established as that ninth row. Full acceptance remains incomplete.

Ordinary owner voice-test turns at 03:27–03:31 UTC received relevant replies,
and the owner confirmed hearing audio. This resolves the organic provider-turn
freshness watch, not all voice usability: the reported voice gaps are recorded
in `docs/concerns/2026-09-05-voice-conversation-turn-taking-and-choice.md`.

## 2026-09-05 — resumed goal: contention contract revalidation

Previous goal activity is progress: the new owner conversation supplies fresh
post-deploy use and corrects the identifiable checklist from seven to eight rows.
Goal resumed after the earlier blocked state; any blocked audit starts afresh.

Read-only release check `gh pr view 2984 --json
state,mergedAt,headRefOid,statusCheckRollup` confirms the proof-record PR merged
at 02:42:12 UTC with required and slow tests successful. This is documentation,
not a new runtime deployment or reason to send another retest.

Windows working-tree verification at HEAD 1ccc399e, with only evidence-doc edits:
`python -m pytest -q tests/test_workspace_pool.py
tests/test_workspace_run_wiring.py tests/test_workspace_effector.py
-k 'bounded_wait or wait_budget or busy or terminal or sweep'`:
30 passed, 217 deselected, one Starlette deprecation warning. The inspected
adapter forwards the node wait budget; pool admission retries held job locks
until that budget expires. The as-built graph-execution-substrate spec explicitly
permits `workspace_busy` after timeout and promises no FIFO fairness. These tests
do not prove Linux isolation, queue fairness, or the app's broader acceptance.

No additional runtime defect was established by this bounded revalidation.
Do not invent a platform patch, alter a workflow, or redefine the app's OPEN row
as PASS. Delivery still needs owner-controlled destination restoration; the
eighth row needs fresh evidence and the ninth remains unidentified. Existing
questions to the founder remain unanswered. Broader provider/model-choice
requirements remain open in their separate concern; no completion claim.

03:44 UTC resumed-goal audit, second consecutive turn with the same owner-input
blocker: the rendered app snapshot still ends at 03:35 UTC, with no destination
restoration or ninth-row identification. No active run is shown; this is not a
verified wait. The preceding contention checks are supporting evidence but did
not change the next action, so classify that goal turn as no progress toward
acceptance. No further blind retest or speculative deployment; goal remains
active until the strict third-consecutive-blocked audit or new actionable input.

## 2026-09-08 — fresh owner-provided retest and actionable evidence gap

Source: founder pasted the app's September 8 00:54 PDT response; not a new browser
read by this task. Reported deploy 9d361262, sequential/parallel/heartbeat/preflight
and workspace+code PASS, repo/channel-agnostic PASS read-only (GitHub GET 200),
workspace contention OPEN despite both runs completing (generations 28/29), and
webhook delivery HTTP 404. Defaulted sequential input is valid; the missing-input
test used a branch without a topic default. No full checklist completion claimed.

The new read-only repo proof supersedes the earlier absent eighth-row evidence
for that narrow operation only. The old nine-row reference is not reconstructed
or silently redefined by this eight-row report. Delivery remains owner-controlled.

Further code inspection establishes a safe next platform action: create/checkout
receipts expose lease generations but no admission attempts, lock conflicts or
retry sleep. Users cannot infer internal contention from generations. Work is
tracked in `openspec/changes/archive/2026-09-08-workspace-admission-evidence/`, on its matching Codex
branch. Add only factual evidence, preserving policy and private workflows; the
app independently evaluates acceptance. This new finding interrupts the blocked
audit: meaningful implementation progress is possible without destination access.

## 2026-09-08 08:48 UTC — receipt deployment and fresh rendered retest

PR #3442 merged as `0e485ba0add1b852d712b4c5d349e542a4131268`;
image build 34205838812 and deploy 34206123316 succeeded. The deployment ran
authenticated public `--assert-handles` canary and protected SHA containment.
Required CI 34204412801 had no new failures; its four focused Linux files had
262 passes and 2 skips (the broader repository suite retains known failures).

Sent only `Retest your workflow checklist` in the existing rendered app at
08:46 UTC. Its 08:48 response reported the deployed revision and nine explicit
rows: sequential PASS, parallel PASS, heartbeat PASS, preflight PASS,
workspace+code PASS (generations 30/31), contention recovery PASS (two observed
lock conflicts followed by completion), final `workspace_busy` refusal OPEN,
exact webhook FAIL 404, and repository/channel check PASS read-only GET 200.
This resolves the earlier unidentified ninth-row ambiguity: the app now lists
recovery and final refusal separately. No full completion or arbitrary-provider
claim follows. No private workflow, trigger, credential or destination edits.

The app additionally still cannot confirm its automatic heartbeat trigger is
stopped. Source audit verified the served automation route was absent despite
existing canonical controls. `served-automation-lifecycle` implements that
general capability in draft PR #3447; live acceptance remains pending. The
user-requested wider missing-tool inventory is retained in
`docs/concerns/2026-09-08-served-tool-capability-parity-gaps.md`.

## 2026-09-08 19:59 UTC — automation capabilities deployed, app retest blocked

PR #3447 passed exact-head Claude review and required CI 34270207709 (zero new
failures; known baseline failures remain). Merged revision
`4a1877f0044a974585ba0daaacf2d190256bf76e` built in 34271720339 and deployed in
34271996544. Authenticated public canary and protected containment passed;
production reported `4a1877f0044a` at 19:58:53 UTC. Platform automation reads and
create/pause/resume/delete are now available without private workflow repairs.

No retest sent on this deploy: the reopened Chrome app tab 1346517456 is owned
by another Codex browser session, and the supported driver refuses access.
Owner was asked to release it. Do not open a replacement conversation, bypass
the tab's ownership, or substitute direct MCP calls for the requested rendered
test. Next action after access returns: send exactly
`Retest your workflow checklist`, then read the final response. Goal remains
active and incomplete; all acceptance rows retain their last qualified states.

## 2026-09-08 20:21 UTC — new-tab permission and automation readback

Owner explicitly permits opening new tabs at any time. Opened Chrome tab
1346517476 at the existing app URL, verified the same saved conversation, and
sent exactly `Retest your workflow checklist` at 20:19 UTC. No fresh conversation,
coaching, private edit, forced tab claim or replay of prior approvals.

Rendered response timestamp 13:20 PDT reports deploy `4a1877f0044a`: sequential,
parallel, deliberate pinned heartbeat, preflight, workspace/code (generations
32/33), contention recovery (two conflicts then successful retry), and repository
read-only GET 200 PASS. Exhausted-wait refusal remains OPEN; exact webhook FAIL
404. Additionally the app now confirms its automation is paused, future triggers
are paused, and the last scheduled run failed. It remains attached. This closes
unknown trigger-state readback, not live retirement or all lifecycle writes.
Other missing capability findings and broad provider/model selection remain
open. A dead owner destination and an unexercised exhaustion path are not evidence
that private workflow edits or a speculative runtime patch would be appropriate.

### 20:25 UTC continuation audit

Previous turn is progress: actual rendered automation state narrowed the open
finding. Current source recheck of `workspace_pool.admit` and the workspace
effector confirms bounded wait exhaustion preserves `workspace_busy`; an initial
refusal first gets a stale-holder sweep, then the node's wait budget. Windows
`python -m pytest -q tests/test_workspace_pool.py tests/test_workspace_effector.py
-k 'lock_held_by_another_run or admission_receives_the_effect_nodes_wait_budget or
observation_preserves_the_uninstrumented_wait_policy'` passed 6 tests, with 209
deselected. This supports routing, held-lock refusal and differential retry
policy; it is not Linux or rendered live exhaustion proof. No runtime fix is
justified by the current missing-exhaustion evidence alone.

Evidence PR #3513 remains open; required CI 34272460513 job 102217194171 was
authoritatively in progress at 20:24:45 UTC, slow tests succeeded. Watching this
same job, not restarting it. Local follow-up 83e383cf records the new rendered
results and passes the 90-test concern-index/OpenSpec checks; it has not been
pushed while the previous head's required checks run. All runtime fixes remain
deployed. Exact webhook renewal still needs owner action through ordinary
controls; no private edits, extra app prompts, or false checklist completion.

### Resumed blocked audit — 2026-09-08

After the owner enabled the new-tab route, the 20:19/20:20 live retest, the
20:25 source/test audit, and the following rendered re-read all retained the
same exact-destination HTTP 404 blocker. The latest re-read has no newer app
message. Paused-state readback is real progress, but neither it nor documentary
CI can renew that external owner destination. The remaining exhausted-lock
row has no demonstrated runtime defect; do not force it by changing workflows
or contention policy. No further supported runtime patch or blind retest is
justified by these two rows. Mark the goal blocked, not complete, pending owner
renewal/selection of the destination or materially new app failure evidence.

The final rendered receipts and audit are added to evidence PR #3513 as a
normal documentation follow-up. Its prior required job 34272460513 was still
running when this update was prepared; an updated head requires new checks.
This is not a runtime redeployment or proof that any open checklist row passed.
Broader provider portability and the remaining missing-tool inventory stay
explicitly unresolved in their existing concern files, not silently completed.

## 2026-09-08 — clarified user conversation repairs the evidence gap

The earlier host-only blocker conclusion was premature under the owner's
clarified delegation above. Sent the ordinary repair request at 20:33 UTC.
The app's 13:41 PDT response reports its corrected three-second workspace probe
exhausted after eight conflicts and returned workspace_busy, then a later run
succeeded. It independently repaired this checklist evidence gap without a
platform change or operator workflow edit.

The app also built a temporary receiver lifecycle test, initially requesting
broad UUID endpoint access. Asked through the request's reply control for access
limited to its own receiver. Its 13:42 response narrows to creation-only, then
exact receiver delivery/deletion after creation. Through the ordinary UI, cleared
the obsolete broad request and approved only POST webhook.site/token; no new
key, wildcard receiver grant or "don't ask again". Receiver creation is now
the app's next action, not a user manual-renewal prerequisite. Webhook success
remains unproven until the app runs and reports it.

This interaction found two real platform gaps: periodic request rendering drops
an in-progress reply (dedicated concern and independent shape review underway),
and the app cannot withdraw its own obsolete request (capability inventory row
eleven). Scope/custody remain enforced; user-facing request approvals do not
authorize broad operator writes to private state.

## 2026-09-08 20:48 UTC — full checklist accepted by the app

The app created its receiver (201), then ran its own exact-ID delivery and
cleanup after scoped user-UI approval (200 and 204). Asked for its final
assessment; at 13:48 PDT it explicitly confirmed its full workflow checklist
passes on its latest tests. It correctly qualified that the last two checks
were verified separately from the earlier retest and repository proof is read-only.
All nine original checklist rows now have rendered PASS evidence. The former
manual webhook-renewal host action is resolved and removed, not left stale.
Detailed requirement/evidence map:
`2026-09-08-workflow-checklist-live-acceptance.md (archived in git)`.

The deployment/retest checklist goal is achieved on this evidence, not on unit
tests or invented provider-general claims. Broader provider/model-selection
requirements and newly discovered request lifecycle/UI defects remain explicit
follow-ups; no assertion that every conceivable workflow or tool is complete.
