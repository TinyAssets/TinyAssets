Implementation lane: `feat/inline-connect-and-approve`. First slice targets once-only
bound HTTP approvals, inline cards and server continuation. Wider task/always grants,
server-completed provider OAuth (2.3), and deployed acceptance (3.2) remain deferred.
Boxes remain unchecked until every part of the original task is verified.

Task 1.1 progress: requests now cut over to protected activity storage under an
OS-backed cross-worker owner-control lock. Copy verification and the cutover marker
commit together; legacy write-refusal triggers retain the original file for recovery.
134 targeted tests passed, including legacy migration, items, activity records,
restart recovery, disjoint owners and lock exclusion. Schema extensions and shared
dispatch/policy/Stop integration continue in the next slice commit.

## 1. Bound requests and owner authority

- [ ] 1.1 Migrate existing pending-request tables into protected activity storage with one lifecycle authority; extend rules/effect_intents/activity_events rather than duplicate them. Implement the owner-control coordinator's cross-worker lock and durable pause with explicit retryable refusal (no queue/partial success) of ask and other mutations. Verify drain/copy/atomic cutover marker, pre/post-cutover recovery and compatible rollback preserve requests/items/answers/suppressions/unmutes and in-flight receipts.
- [ ] 1.2 Bind generic actions, pinned payloads, trusted provenance and deadlines; render all approval fields from the protected envelope. Verify forged identities and tampered legacy prose cannot change the preview or executable action.
- [ ] 1.3 Add interactive owner sessions and single-use session/action/scope-bound approval tokens for protected web/native views. Reject bearer-only chatbot/MCP/CLI approvals, token minting, dispatch-retries and rule-write bypasses; verify replay, CSRF, account switch and cross-user rejection.
- [ ] 1.4 Implement revision-checked decisions and existing-rule task/always grants, keeping once out of Rules; define per-origin tasks, deadlines and Stop invalidation. Verify do_if_preapproved, dispatch-time policy digest comparison for once/task/always (including once ask_first changed to hand_off), unrelated-card stability and fresh approval after termination.
- [ ] 1.5 Execute through ordinary enforcement and existing effect intents; make wider-grant finalization idempotent and inert until finalized under the shared owner-control lock. Verify duplicate clicks, policy/revocation/Stop races, expiry before dispatch, lock loss/fencing, each cross-store crash boundary, recovery invalidation without overwriting later edits and unknown-effect reconciliation.

## 2. Continuation and presentation

- [ ] 2.1 Persist one logical wake per answer in activity_events, including foreground tasks, with authoritative dedupe/attempt state and atomic result/processed-ack. Re-deliver on every boot/runtime recovery until acked, fencing interrupted attempts. Verify deploy before/after admission and processed-ack, misleading journal done states, exactly one committed processing result, effect reconciliation, busy turns, missing power/context, held stopped tasks, retention beyond MAX_EVENTS and dedupe tombstones outliving replay sources.
- [ ] 2.2 Render protected inline cards, history rail and emergency bubble using existing refresh transport. Verify phone layout, account switching, preserved drafts, edit/scope/deadline display, protected owner-session approval and all failure choices.
- [ ] 2.3 Move inline PKCE custody to the server; begin through a top-level TinyAssets hop authenticated in the popup/system browser, binding state to the initiating owner session and flow cookie. Require both on callback before exchange and recheck before owner-bound deposit; reuse flow receipt recovery. Verify copied authorization/launch URLs, absent/mismatched cookies, another browser/session, parent closed, native suspended with no app bearer/verifier/deep link, replay/expiry/logout/account switch, crash after deposit, private key deposit and active-task continuation.

## 3. Implementation acceptance (later)

- [ ] 3.1 Run affected tests/heavy files and Ruff, with data-loss/cross-user guard mutations; satisfy the applicable floor review before landing implementation.
- [ ] 3.2 Assert the deployed implementation SHA, run the public canary and one real-user app pass covering connect/approve/edit/deny/retry and addressed Stop, then sync specs.
