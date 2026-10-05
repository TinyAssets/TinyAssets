# First implementation slice

Branch: `feat/inline-connect-and-approve`. This is the explicitly authorized
once-only HTTP approval slice, not acceptance of the complete approved change.
There are no provider-specific dispatch paths or new MCP handles.

When the initiating agent's owner rule says `ask_first`, the existing generic
authenticated HTTP effector records its actual packet as a protected request.
The bubble renders the server preview in the thread. Approve once consumes an
interactive-session/action/revision token, reserves an existing effect intent,
rechecks authority, and executes through the ordinary effector. Editing the body
creates a new revision; Deny, Skip and Find alternative record decisions and wake
the saved agent. Not now retains the card. Uncertain effects cannot be resent.
The rail is read-only request history. Tokens stay out of MCP reads and history.

The supported envelope includes literal method/host/path/query/body and existing
connection/grant references. Strings preserve their bytes. Headers, unresolved
transforms, file references and oversized or credential-shaped inputs fail closed.
This slice does not claim support for every approved payload representation.

## Original tasks, in order

| Task | Delivered in this slice | Still unchecked |
| --- | --- | --- |
| 1.1 | Protected activity-store cutover; verified four-table copy and atomic marker; legacy writer refusal; explicit paused recovery; OS owner lock; existing activity/event/intent extensions | Full compatible rollback/in-flight crash matrix and coordination of every legacy mutation path |
| 1.2 | Canonical SHA-256, literal pinned bodies, trusted owner/agent/turn/task, deadlines, protected preview fields, forged provenance and prose-tamper rejection | Versioned file/blob payloads and transform pinning |
| 1.3 | Separate server-PKCE web owner session; single-use session/action/revision/scope token; bearer-only approval and Rules POST refusal; origin/account/logout checks | Native OS-backed first-party credential integration and full native account lifecycle verification |
| 1.4 | Once decisions outside Rules; dispatch-time relevant policy digest; foreground continuation task; fixed 24-hour task/30-minute preview caps; addressed Stop invalidation | Task/always grants, editable durations, revision-checked edit conflicts, all per-origin task lifecycles and terminal-state integration |
| 1.5 | Ordinary broker/connection/grant/consent enforcement; once effect reservation; duplicate-click exclusion; uncertain-result hold; process-backed execution locks | Wider-grant materialization/finalization and complete cross-store crash/revocation race matrix |
| 2.1 | Bound action-result/denial wakes in existing activity_events; durable dedupe/attempt/result/ack; runtime/boot sweep; saved-agent resume; stopped/busy/no-power hold; retained tombstones | Ordinary answers, OAuth/background origins, inherited continuation task identity, full admission-before-recovery barrier and every deployment crash boundary |
| 2.2 | Bubble inline protected approvals, draft edits, once/deadline display, sign-in/review/failure choices, read-only rail, account epoch guard, existing refresh transport, phone Chromium proof | Beside-original-turn positioning across history/reload, task/always controls, full connect card unification and real-user native/emergency-bubble proof |
| 2.3 | No provider-connect implementation claimed; owner approval login independently uses server PKCE | Entire provider OAuth custody/deposit/recovery and active-task continuation contract |
| 3.1 | Related tests, affected heavy tests, Linux oracle, Chromium tests, mirror regeneration/parity, Ruff and strict spec validation | Comprehensive guard mutation campaign beyond the independently reviewed S1 slice |
| 3.2 | None | Deployed SHA assertion, public canary, real-user connect/approve/edit/deny/retry/Stop pass and full-change spec sync |

## Approval receipt low notes

The approval owner-login authorization URL explicitly pins `response_mode=query`,
matching its top-level GET callback and SameSite=Lax flow cookie. Task 2.3 must
apply and test the same explicit response-mode choice for provider OAuth; it is
not implemented by the approval-login work.

The protected approval login opens a first-party browser view. Native users can
need a second sign-in in their system browser; the app's bearer/localStorage is
never promoted into interactive approval authority. Native OS credential storage
and suspended-app integration remain unimplemented.

Continuation computation is retried until a result and processed acknowledgement
commit together. A retry can repeat non-effect work, including memory writes;
this is not exactly-once computation. Uncertain effect intents cannot be blindly
resent. New actions from a later continuation remain subject to ordinary owner
rules and approval admission.

## Safety evidence

| Property | Exercised guards / negative mutations | Evidence |
| --- | --- | --- |
| Credential custody | Reject raw headers, credential fields and forged provenance; preview/read does not expose vault secret or approval token; PKCE verifier sealed server-side; callback requires browser flow cookie; copied state and bearer fetch rejected | `test_inline_approvals.py`, `test_inline_owner_sessions.py`, existing `test_authenticated_external_call_effector.py` |
| Cross-user isolation | Mutated owner, session, revision and token refuse dispatch; copied cross-home grant refused; addressed custom agent checked; account mismatch, CSRF and bearer-only Rules writes rejected; separate homes and cross-process lock exclusion | Same two test files plus `test_inline_request_storage.py`, `test_agent_rules.py`, existing access/isolation tests |
| Initiating-agent owner rules | Custom initiating agent's ask-first policy cannot borrow main's authority; once ask-first changed to hand-off refuses; relevant review edit invalidates even when switched back; policy rechecked after reservation; unrelated rule stays stable; Stop/expiry refuse dispatch | `test_inline_approvals.py`, `test_agent_rules.py`, `test_agent_review.py` |
| Data loss and duplicate effects | Four migration tables compared; old writers blocked; failed copy stays paused until explicit recovery; no partial API ask; one effect for repeated click; changed draft invalidates old token; interrupted/fenced wake attempts cannot acknowledge; dedupe survives event trimming | `test_inline_request_storage.py`, `test_inline_approvals.py`, existing migration/item/activity tests |
| Rendered behavior | Real Chromium at 390px: shipped controller previews, edits and submits the new token/revision without a chat relay; failed preview preserves draft and offers sign-in; history has no controls | `test_inline_approvals_real_browser.py` (mock HTTP transport; not a deployed end-to-end pass) |

## Validation record

All pytest basetemps were outside the repository; no full suite was run.

- Main related Python regression group: **299 passed** (approval/session/storage,
  generic HTTP effector, Rules/review, pending requests/items/migration/activities).
- Final focused contract/UI run after policy-digest and UI changes: **60 passed**.
- Final Linux oracle: **119 passed**, Python 3.11.16, uid 1001, bwrap 0.12.0:
  `python scripts/linux_oracle.py -- tests/test_inline_request_storage.py tests/test_inline_approvals.py tests/test_inline_owner_sessions.py tests/test_pending_requests_migration.py tests/test_agent_activities.py tests/test_agent_rules.py -q --basetemp /tmp/ta-inline-complete`.
- Final Chromium/storage/mirror group: **33 passed**:
  `python -m pytest tests/test_inline_request_storage.py tests/test_inline_approvals_real_browser.py tests/test_connect_free_ai_real_browser.py tests/test_custom_ui_real_browser.py tests/test_mirror_parity_gate.py tests/test_pre_commit_mirror_parity.py -q --basetemp C:/Users/Jonathan/AppData/Local/Temp/ta-inline-complete`.
- After hardening the pre-copy migration pause: **31 passed** in the focused
  storage/migration/mirror run, and **15 passed** in the Linux oracle rerun of
  `test_inline_request_storage.py` and `test_pending_requests_migration.py`
  (`--basetemp /tmp/ta-inline-pause`).
- Earlier onboarding/Stop/access/package regression group: **310 passed, 1 skipped**;
  the skipped test is not acceptance evidence.
- Affected heavy/authentication group: **192 passed, 3 skipped**:
  `python -m pytest tests/test_universe_server_isolation.py tests/test_scoped_identity_reset.py tests/test_onboarding_session_refresh.py tests/test_account_deletion.py tests/test_delivery_account_deletion.py -q --basetemp C:/Users/Jonathan/AppData/Local/Temp/ta-inline-heavy-auth`.
  Host/schema-dependent skips are not acceptance evidence.
- Mirror regenerated with `python packaging/claude-plugin/build_plugin.py`;
  import probe passed and parity tests passed.
- Repository Ruff: **59 findings in untouched files, zero in changed files**.
  Machine-readable report: external temp `ta-inline-ruff-final.json`.
- `openspec validate inline-connect-and-approve --strict`: passed.
- `git diff --check`: passed.

No production deploy, public canary or full-change spec sync is claimed.
PR #4449 now includes cross-family closure of three reproduced lifecycle defects:
Stop interrupts before a contended approval lock; wakes persist retry backoff;
stale cards permit session-bound dismissal while approval and editing stay blocked.
Review receipt: https://github.com/TinyAssets/TinyAssets/pull/4449#issuecomment-5983207487
at implementation head1e475b9f094e11a492d8220fa0917aa2a4366699.
On2026-10-04 Windows/Python3.14 the reviewer independently passed31 approval tests;
the integrated root run passed86 tests across test_inline_approvals,
test_inline_approvals_real_browser, test_turn_interrupt, test_inline_owner_sessions,
test_inline_request_storage and test_mirror_parity_gate via `python -m pytest`
with an external basetemp. Changed Python Ruff, plugin import/parity and strict
OpenSpec checks passed. Hosted and live acceptance remain separate requirements.
The full change remains open for the unchecked work above.

Implemented S1 requirements are now recorded in `openspec/specs/inline-connect-and-approve/spec.md` during the PR drain. This partial as-built sync does not mark the broader unchecked change complete or assert deployment.

## First web sign-in follow-up (2026-10-04)

Branch `fix/single-signin-first-connect` fixes concern item 1 for fresh web
sign-in. Normal web login reuses the existing protected server-PKCE callback,
establishing owner proof and app renewal together. The token proxy and native
login do not mint owner proof. The native browser still needs its own protected
login before OpenRouter when it has no live owner cookie; completion still asks
the user to return to chat. Eight-hour owner expiry is unchanged on both paths.
The as-built web-login requirement is synced to `onboarding-web-app` only;
broader server-completed model-connect work remains unfinished.

One Claude cross-family review via `peer-agents`: **ADAPT**, no floor findings.
**AGREE** with the pending-logout correctness finding: finish a previously failed
logout before starting web login, rather than letting it revoke new cookies.
The public completion marker deliberately cannot clear logout intent. New Node
regressions cover successful and failed cleanup plus marker non-authority.
**AGREE** with the expiry documentation finding: distinguish eight-hour owner
proof from seven-day app renewal in the concern and design. Existing owner,
CSRF, cross-user, action-binding and copied-URL tests remain unchanged.

This lane is commit-and-push only by user instruction: no PR, deployment, or
real-user production pass is claimed.

Final verification: Windows **432 passed, 6 skipped** (four protected-lock POSIX
cases and two POSIX-mode cases); Linux oracle Python **3.11.16**, uid 1001,
bwrap 0.12.0: **438 passed, no skips**. The selected files were
`test_app_owner_sign_in`, `test_inline_owner_sessions`, `test_inline_model_connect`,
`test_inline_approvals`, `test_inline_request_storage`, `test_agent_rules`,
`test_onboarding_app`, `test_onboarding_session_refresh`,
`test_refresh_session_hardening`, `test_refresh_session_seal`,
`test_onboarding_auth_boundary`, `test_app_notification_routes`,
`test_onboarding_model_connect`, `test_app_url_is_apex_app`,
`test_mirror_parity_gate`, and `test_pre_commit_mirror_parity` under `tests/`.
Windows used external basetemp `C:/Users/Jonathan/AppData/Local/Temp/ta-single-signin-final`;
`python scripts/linux_oracle.py -- <same files> -q -ra` used external `/tmp/b`.
All 15 new regressions passed. Plugin rebuild/import, changed-Python Ruff,
strict OpenSpec validation (`--type change`), and `git diff --check` passed.

## Approval sheet and service connection slice (2026-10-04)

Lane: `feat/approval-sheet-and-connect-card`, based on main after #4468.
S2 replaces the side rail and interim composer region with a modal sheet and
bubble Needs you inbox. Foreground bound asks display protected purpose and exact
action; all five decision choices use #4449 owner sessions and revision-bound
tokens. Background asks use the existing notify-owner path; answered history is
read-only. Polling preserves active drafts; owner changes clear cached secrets.

Task/site/always HTTP preapprovals reuse Rules and effect_intents. Authority is
limited to the displayed operation, class, account and exact origin, with policy,
consent and connection revisions checked again at dispatch. Task grants also bind
task generation/deadline; completed or stopped tasks cannot reuse them. Deletion
tombstones prevent grant resurrection. Network sends release the owner-control
lock so owners can revoke or Stop while a provider is slow. Crashed unfinalized
grants remain inert; recovery invalidates their planned attempt, exposes an
unresolved request and allows protected dismissal without blind resend.

Settings and agent service requests share the sheet, with registered OAuth,
protected secret deposit and generic HTTP shapes. Changing shape clears staged
secrets. Account labels make independent deposits for the same service. Agent
connection answers now atomically commit a sanitized activity-event wake with
request resolution; the browser avoids the former second-chat-turn relay.
Settings connections do not invent agent work. No platform LLM or provider-specific
integration code was introduced.

Original tasks 1.1–3.2 remain unchecked because their complete acceptance is wider
than S2: full migration/rollback recovery matrix; broader packet forms and proposal
classification authority; per-origin/background task contracts, owner classification
and budget-capped spend defaults; exhaustive crash/fencing mutations; background
cross-device notification delivery; complete first-power/model entry consolidation;
and server-held generic OAuth PKCE/callback completion with parent closed or native
suspended. Existing OAuth custody was reused, not represented as server-held. Push
uses the existing delivery behavior, not a new durable cross-device notification
protocol. Production SHA assertion, public canary and real-user production pass
are not performed by this commit-and-push-only lane. Tests use scripted external
provider transports, not live provider accounts.

### Migration and review disposition

The Rules migration now preserves the old upsert key. Independent grants live in
`approval_grants`, with distinct positive IDs reserved across both tables. Grant
rows and the conservative legacy compatibility rows use `hand_off`; current
behavior decisions ignore compatibility rows and match grants by record kind and
protected predicate. Old readers refuse those rows, including revoked grants.
Old owner upserts still add/tighten rules, and editing a compatibility row converts
it to a behavior rule. Existing behavior rows are never replaced by grant issuance.
Migration is transactional and closes/rolls back on interruption; old-format values,
IDs and the ID high-water mark survive. Initial sheet-format grants and tombstones
are copied before deduplicating compatibility rows. The migration is no longer
rollback-hostile. The broader task 1.1 recovery matrix remains unfinished.

One cross-family Claude round through the peer-agents skill returned ADAPT, with
no floor findings. AGREE: release owner-control before network sends and handle
busy prechecks; invalidate incomplete grant issuance in recovery; use named-column
migration (Round 2 adds compatible rollback); refuse missing task context visibly;
move direct capture notification after its own lock; exercise subsequent effector
reuse, policy edits, task Stop/expiry/generation and unrelated-rule stability;
and exclude preapprovals from behavior-rule hand-back checks. These changes are
implemented. The later connection-answer wake extension was validated by tests
and local review; it was not in that peer snapshot. No additional peer round is
claimed.

Cross-user/data-loss guard evidence includes the unchanged owner-session/storage
and universe-isolation suites, existing replay/fencing cases, and injected grant
issuance and connection-wake write failures. The latter proves failed wake writes
roll back resolution and report failure, then retry commits one sanitized wake.
No exhaustive new source-mutation matrix is claimed; task 3.1 remains open.

### Verification for S2

Final backend/isolation selection: Windows 476 passed, 4 POSIX-lock skips;
Linux oracle Python 3.11.16: 480 passed, no skips. Files: connection-sheet
continuations, inline approvals, approval-sheet scopes, pending requests, generic
OAuth connections, turn interruption, inline-request storage, owner notifications,
agent activities, universe-server isolation, scoped-identity reset and first-contact.
The three affected heavy files are included (186 cases).

Final sheet/contract selection: 179 passed on Windows and 179 on Linux, including
all six new whole-app desktop/390px browser cases, the four existing pending-request
browser cases, request-card DOM contracts and onboarding app contracts. Existing
browser/test names remain; the relay contract now strictly requires two refreshes
(answer removal and completed-turn foreground discovery), not a weakened bound.
The DOM test harness now implements the browser APIs the sheet uses.

The plugin mirror was regenerated and its import probe passed. Touched Python
Ruff, strict change and as-built spec validation, concerns-index check and diff
whitespace checks passed. No new concern file was created. New real-browser tests
are included in the real-browser workflow's pull_request.paths.

The complementary approval-browser, disclosure and mirror/parity checks passed
36/36 on each platform. Final disjoint selections total **691 passed, 4 skipped
on Windows; 695 passed, no skips on Linux 3.11**. Earlier affected rules/effector
and first-run/connect browser selections also passed, and are not added again to
these totals. Post-commit test hygiene reports **12 new test functions, 0 removed,
0 tampering findings** (parameterization expands the case count). No PR or
production deployment is created by this lane.


### Round 2 follow-ups

Review areas 1 and 5: AGREE with conservative rollback behavior, old-upsert
compatibility, migration regression coverage, notification after continuation-bind
failure, and the inbox separator. Bind failure returns the saved pending ask with
`server_continuation=false` and `continuation_status=unavailable`; the owner still
receives the initial notification. Duplicate asks do not send it twice. Inbox
buttons use the escaped middle dot, covered at desktop and 390px widths.

This remains commit-and-push only. No production deployment, SHA assertion or
real-user production pass is claimed; the broader unchecked work above remains.


The focused Round 2 cross-family migration review through `peer-agents` returned
ADAPT with one correctness finding. AGREE: old edits/deletes must tombstone every
grant for the legacy key, or deletion during rollback could revive consent after
rollforward. Both triggers now revoke the exact-key grants; regression tests
exercise old DELETE/upsert, multiple overlapping grants and actual grant matching.
Other scoped items had no findings. No new unresolved concern was introduced.


Round 2 final verification: **818 passed, 4 POSIX-lock skips on Windows/Python
3.14; 822 passed, no skips on Linux oracle Python 3.11.16, uid 1001,
bwrap 0.12.0**. Both runs include 14 rendered browser cases and all 186 cases
in the three affected heavy files (first-contact, scoped-identity reset and
universe-server isolation). The 14 added regression cases cover rollback reads
and writes, overlapping grant IDs/revocation, old edits/deletes on rollforward,
migration values/counter, four interruption points, actual process death, and
saved-ask notification despite binding failure. Existing tests were retained.

Final selection: `tests/test_rules_rollback.py`, `tests/test_agent_rules.py`, `tests/test_authenticated_external_call_effector.py`, `tests/test_approval_sheet_scopes.py`, `tests/test_connection_sheet_continuations.py`, `tests/test_pending_requests.py`, `tests/test_pending_requests_migration.py`, `tests/test_pending_requests_power.py`, `tests/test_inline_approvals.py`, `tests/test_inline_request_storage.py`, `tests/test_inline_owner_sessions.py`, `tests/test_generic_oauth_connections.py`, `tests/test_turn_interrupt.py`, `tests/test_owner_notifications.py`, `tests/test_agent_activities.py`, `tests/test_universe_server_isolation.py`, `tests/test_scoped_identity_reset.py`, `tests/test_first_contact.py`, `tests/test_approval_sheet_real_browser.py`, `tests/test_inline_approvals_real_browser.py`, `tests/test_app_pending_requests_browser.py`, `tests/test_onboarding_app.py`, `tests/test_request_card_layout_and_links.py`, `tests/test_mirror_parity_gate.py`, `tests/test_pre_commit_mirror_parity.py`.

Windows command: `python -m pytest <selection> -q -ra --basetemp
C:/Users/Jonathan/AppData/Local/Temp/ta-sheet-r2-final-windows` (JUnit alongside
that directory). Linux: `python scripts/linux_oracle.py -- <selection> -q -ra`,
using external `/tmp/b`. Plugin regeneration/import and mirror parity, touched
Python Ruff, strict OpenSpec change validation, concerns metadata and whitespace
checks pass. The modified browser file already appears in the workflow paths;
no new real-browser file or concern was added. Review disposition is recorded
above. Production acceptance and remaining broader tasks are unchanged.

Post-commit hygiene passes against both the Round 2 starting SHA and the PR
merge base: **8 new test functions this round / 20 across the PR, 0 removed,
0 tampering findings**. Commit hooks also pass mirror parity, mojibake scanning,
import-graph smoke, path-resolver lint, cross-provider drift and skill validation.
