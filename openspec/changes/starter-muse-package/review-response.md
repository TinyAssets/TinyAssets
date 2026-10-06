# Claude cross-family review disposition

Reviewer: Claude through peer-agents, read-only, 167 seconds, exit 0.
Reviewed first slice f7b659dfd8; verdict **ADAPT**, not APPROVE. Full output: review.md.
One review round; the lead implements and verifies agreed findings below.

1. **AGREE — settled/muted notify receipts.** The helper now acknowledges a
   settled owner decision, returns suppressed with notified=false and does not
   retry. Added goals, monitors and reminders regressions. Genuine failures still
   leave state unchanged and retry.
2. **AGREE — lost UI punctuation.** Replaced lost characters with ASCII Loading...
   and " - Due "; browser regression asserts a rendered due date. Mirror rebuilt.
3. **AGREE — publisher test scope.** Retained the existing test identifier to
   avoid representing a rename as test removal, explicitly documented its narrow
   contract, and made writes/unlinks fail during publication. It now guards the
   pure publisher's no-mutation behavior; D10 preservation proof remains pending.
4. **AGREE — advisory digest size and reminder cadence.** Bound notification
   summaries below 8,000 characters with a pointer to unchanged source content;
   regression covers it. Default reminder interval is 300 seconds, owner precision
   overrides it. Workflow guidance retires acknowledged reminders (including
   suppressed notices), preserves rescheduled due dates and pauses when empty.

The on-demand onboarding skill now routes fresh setup through the layout and
goals skills, preserving existing choices. No resident prompt changes.

Verification after fixes: Linux oracle 41 passed, zero skips; ruff and plugin
mirror/import probe pass. This is a package draft with delivery prerequisites
open, not a declaration of deployed completeness or an invented reviewer APPROVE.
