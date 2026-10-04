Proposal-only handoff; implementation boxes intentionally remain open. No product code, deployment or new PR in this session.

## 1. Bound requests and owner authority

- [ ] 1.1 Migrate existing pending-request tables into protected activity storage with one lifecycle authority; extend rules/effect_intents/activity_events rather than duplicate them. Verify copy/cutover, crash recovery and rollback preserve requests/items/answers/suppressions.
- [ ] 1.2 Bind generic actions, pinned payloads, trusted provenance and deadlines; render all approval fields from the protected envelope. Verify forged identities and tampered legacy prose cannot change the preview or executable action.
- [ ] 1.3 Add interactive owner sessions and single-use session/action/scope-bound approval tokens for protected web/native views. Reject bearer-only chatbot/MCP/CLI approvals, token minting, dispatch-retries and rule-write bypasses; verify replay, CSRF, account switch and cross-user rejection.
- [ ] 1.4 Implement revision-checked decisions and existing-rule task/always grants, keeping once out of Rules; define per-origin tasks, deadlines and Stop invalidation. Verify do_if_preapproved, matching-policy digests, unrelated-card stability and fresh approval after termination.
- [ ] 1.5 Execute through ordinary enforcement and existing effect intents; make wider-grant finalization idempotent and inert until finalized. Verify duplicate clicks, revocation/Stop races, expiry before dispatch, each cross-store crash boundary and unknown-effect reconciliation.

## 2. Continuation and presentation

- [ ] 2.1 Persist result/answer wakes in activity_events, including foreground tasks; implement boot/runtime sweeps and durable coordinator admission dedupe. Verify closed-page/item answers, deploy-before/after-admission, busy turns, missing power/context and wake retention beyond event trimming.
- [ ] 2.2 Render protected inline cards, history rail and emergency bubble using existing refresh transport. Verify phone layout, account switching, preserved drafts, edit/scope/deadline display, protected owner-session approval and all failure choices.
- [ ] 2.3 Move inline PKCE custody to the server and exchange on the callback route; reuse existing flow records with binding/receipt recovery. Verify parent closed, native suspended with no bearer/verifier/deep link, replay/expiry/logout, crash after deposit, private key deposit and automatic active-task continuation.

## 3. Implementation acceptance (later)

- [ ] 3.1 Run affected tests/heavy files and Ruff, with data-loss/cross-user guard mutations; satisfy the applicable floor review before landing implementation.
- [ ] 3.2 Assert the deployed implementation SHA, run the public canary and one real-user app pass covering connect/approve/edit/deny/retry and addressed Stop, then sync specs.
