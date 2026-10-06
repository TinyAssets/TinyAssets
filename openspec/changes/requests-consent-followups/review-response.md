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
