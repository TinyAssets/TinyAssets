# Claude cross-family review

Reviewed implementation: 6b622b0825. Peer-agents Claude review completed successfully;
verdict **ADAPT**. One correctness finding; other four follow-ups accepted.

**AGREE — system-created action classification.** The validator-only inventory
missed `start_activity`; protecting unknown actions exposed five proposal tests
that still used bearer answers. Explicitly classify start_activity and approve_action
as consent, and notify as non-consent (its dedicated branch refuses answers).
Extend the inventory test to literal action dictionaries at system creation sites.
Keep all existing proposal assertions and exercise them through the real protected
owner transport; add explicit bearer approve/decline/clear refusal tests.

Local correction check: 94 passed, including all proposal tests and action inventory
and recurrence cases. Linux follow-up: 305 passed across six affected/adjacent files.
Ruff and plugin mirror passed. All existing proposal assertions are preserved.
No second review round: this response implements the review's smallest correction.

Clarification: revoke_owner is called for account deletion. Logout's single-cookie
revoke is unchanged; this lane makes no claim that logout cancels connection flows.

## Reask history follow-up — draft PR #4527

Claude reviewed ed17394362 through peer-agents and returned **APPROVE** (exit 0).
**AGREE:** no floor or correctness findings. The reviewer checked history status
selection, original-agent routing, protected consent and mute preservation,
dedupe/history assertions, mirror parity and the as-built spec, and independently
ran the new Chromium test (1 passed). Non-blocking observations: browser routing
coverage uses the main agent; the quoted historical title returns to that same
owner's agent. No requested changes. Final Linux oracle: 234 passed, zero skips.

## Reask round 2 - history consent boundary

Claude peer-agents review completed with exit 0 and **APPROVE** after the
origin/main merge and browser assertion updates. **AGREE:** no floor or
correctness findings. History permits only Ask again, sends to the original
agent, and makes no protected answer/approval call. The existing account fence
remains covered. The lifecycle proof checks one fresh, deduplicated pending
card with Accept/Clear controls, still awaiting its normal protected answer.
The peer did not run tests; Linux oracle execution is recorded in tasks.md.
The controlled agent/API bridge is not a live agent re-ask proof; deployment
and the real-user app pass remain post-merge work.
