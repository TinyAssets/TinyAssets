Implementation lane: `feat/inline-connect-and-approve`. The authorized first
coherent slice is implemented: once-only generic HTTP approval in the bubble,
protected owner-session decisions that execute, and server-side continuation.
See [delivery.md](delivery.md) for behavior, evidence and the remaining work in
each original task. Original boxes stay unchecked where their broader contracts
are not fully implemented or verified; this is not full change acceptance.

Current follow-up lane: `feat/approval-sheet-and-connect-card`, based on
`origin/main` after #4468. This is commit-and-push delivery, not deployment.

- [x] S1 Deliver the first coherent slice: protected literal HTTP action envelopes,
  owner-session once approval that executes through ordinary enforcement, inline
  thread cards/read-only history, durable bound-answer wakes and server recovery.

- [x] S2 Replace the side/composer request regions with a responsive approval
  sheet, bubble Needs you inbox and read-only history; add protected task/site/
  always HTTP grants, shared settings/agent service connect entry with auth-shape
  switching and labelled accounts, and durable agent connection-answer wakes.
  This milestone covers the implemented subset described in delivery.md, not
  completion of the broader numbered tasks below.

## 1. Bound requests and owner authority

Follow-up `fix/consent-asks-owner-session`: close bearer answers for all ten
consent kinds using the existing protected owner-session door, including item,
retry, Clear/Deny and OAuth token-answer paths. Tasks 1.2–1.4 stay unchecked:
this partial slice does not complete their universal bound-token, classification,
or payment-scope contracts. Evidence and entry-point inventory live in
[consent-delivery.md](consent-delivery.md).

- [ ] 1.1 Migrate existing pending-request tables into protected activity storage with one lifecycle authority; extend rules/effect_intents/activity_events rather than duplicate them. Implement the owner-control coordinator's cross-worker lock and durable pause with explicit retryable refusal (no queue/partial success) of ask and other mutations. Verify drain/copy/atomic cutover marker, pre/post-cutover recovery and compatible rollback preserve requests/items/answers/suppressions/unmutes and in-flight receipts.
- [ ] 1.2 Bind generic actions, pinned payloads, trusted provenance and deadlines; render all approval fields from the protected envelope. Verify forged identities and tampered legacy prose cannot change the preview or executable action.
- [ ] 1.3 Add interactive owner sessions and single-use session/action/scope-bound approval tokens for protected web/native views. Reject bearer-only chatbot/MCP/CLI approvals, token minting, dispatch-retries and rule/classification-write bypasses; prove package/template/agent classification proposals are inert until exact-revision owner approval and changed revisions cannot reuse approval; verify replay, CSRF, account switch and cross-user rejection.
- [ ] 1.4 Implement revision-checked decisions and existing-rule task/site/always grants, keeping once out of Rules; define per-origin tasks, exact-origin site scope, read/write/destructive/spend classes and trusted owner classification, editable unknown-effect defaults (starter ask), and editable payment defaults with optional budget-capped owner spend grants, deadlines and Stop invalidation. Verify do_if_preapproved, dispatch-time policy digest comparison for once/task/site/always (including once ask_first changed to hand_off), unrelated-card stability and fresh approval after termination.
- [ ] 1.5 Execute through ordinary enforcement and existing effect intents; make wider-grant finalization idempotent and inert until finalized under the shared owner-control lock. Verify duplicate clicks, policy/revocation/Stop races, expiry before dispatch, lock loss/fencing, each cross-store crash boundary, recovery invalidation without overwriting later edits and unknown-effect reconciliation.

## 2. Continuation and presentation

- [ ] 2.1 Persist one logical wake per answer in activity_events, including foreground tasks, with authoritative dedupe/attempt state and atomic result/processed-ack. Re-deliver on every boot/runtime recovery until acked, fencing interrupted attempts. Verify deploy before/after admission and processed-ack, misleading journal done states, exactly one committed processing result, effect reconciliation, busy turns, missing power/context, held stopped tasks, retention beyond MAX_EVENTS and dedupe tombstones outliving replay sources.
- [ ] 2.2 Render the protected approval sheet (Allow once / for this task / for this site / always / Deny), Needs you inbox with deduplicated push via notify-owner-of-requests, and emergency bubble; own the single chat/settings connect card with auth-shape switching, staged-credential invalidation and independent labelled multi-account selection; remove the side requests panel after pending asks are reachable in the inbox. Verify phone layout, account switching, preserved drafts, edit/scope/deadline display, protected owner-session approval and all failure choices, background delivery across devices and automatic continuation after an answer.
- [ ] 2.3 Move inline PKCE custody to the server; begin through a top-level TinyAssets hop authenticated in the popup/system browser, binding state to the initiating owner session and flow cookie. Require both on callback before exchange and recheck before owner-bound deposit; reuse flow receipt recovery. Verify copied authorization/launch URLs, absent/mismatched cookies, another browser/session, parent closed, native suspended with no app bearer/verifier/deep link, replay/expiry/logout/account switch, crash after deposit, private key deposit and active-task continuation.

## 3. Implementation acceptance (later)

- [ ] 3.1 Run affected tests/heavy files and Ruff, with data-loss/cross-user guard mutations; satisfy the applicable floor review before landing implementation.
- [ ] 3.2 Assert the deployed implementation SHA, run the public canary and one real-user app pass covering connect/approve/edit/deny/retry and addressed Stop, then sync specs.
