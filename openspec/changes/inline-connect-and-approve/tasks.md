Proposal-only handoff; implementation boxes intentionally remain open. No PR is opened in this session.

## 1. Bound requests

- [ ] 1.1 Add protected control/event/scoped-rule tables and projection reconciliation; verify existing-store upgrade and rollback preserve pending work.
- [ ] 1.2 Bind generic actions and initiating provenance; verify normalization, immutable payload hashes, field-specific schema errors and rejection of forged identities.
- [ ] 1.3 Implement revision-checked edit/approve/deny and scoped owner rules; prove edited drafts re-preview and scopes cannot cross agent/task/consent boundaries.
- [ ] 1.4 Execute through ordinary enforcement with durable reservations/results; prove duplicate answers, revocation races and uncertain effects cannot cause unapproved retries.

## 2. Continuation and presentation

- [ ] 2.1 Persist answer/result wakes from every surface; prove closed-page, item-answer, restart and busy-turn continuation with deduplicated admission.
- [ ] 2.2 Add authenticated owner SSE snapshot/replay; verify cross-user rejection, account switching, reconnect and complete projections.
- [ ] 2.3 Render the one inline connect/approval card, history rail and live bubble status; verify phone layout, preserved drafts, Edit and every failure choice.
- [ ] 2.4 Use popup/in-app sign-in and trusted completion; verify blocked/cancelled/replayed flows, private key deposit and unprompted original-task continuation.

## 3. Implementation acceptance (later)

- [ ] 3.1 Run affected tests/heavy files and Ruff, with data-loss/cross-user guard mutations; satisfy the applicable floor review before landing implementation.
- [ ] 3.2 Assert the deployed implementation SHA, run the public canary and one real-user app pass covering connect/approve/edit/deny/retry and addressed emergency Stop, then sync specs.
