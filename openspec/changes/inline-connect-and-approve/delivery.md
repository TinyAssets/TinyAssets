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
| 3.1 | Related tests, affected heavy tests, Linux oracle, Chromium tests, mirror regeneration/parity, Ruff and strict spec validation | Cross-family floor review (no sub-agents were used, as instructed), comprehensive guard mutation campaign |
| 3.2 | None | Deployed SHA assertion, public canary, real-user connect/approve/edit/deny/retry/Stop pass and spec sync |

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
