Approval of C3 tail (one model call; registry S1, S13, S23).
- Cross-family adversarial review: DISAGREE_EVIDENCE with 5 findings (HIGH second fallback path for workflow nodes with a fallback_chain; MEDIUM fallback without proof of no side effect; S1 text; S13 guard gap for the async bridge; dead _get_shared_router).
- Fix 236ebe91144, checked by the lead: fallback only under an open request carrier; workflow/served authority fall back only through the run's own candidate loop; fallback requires every attempt side_effect_state none and reuses the chat/work boundary helpers (refusal, auth, capacity) with source cooling; S1 corrected; S13 guard extended; dead router deleted.
- Validation: failing-first tests; 103 targeted Linux oracle tests passed; ruff.

- Queue follow-up 1fee54214e6, checked by the lead: adds the missing C3 verifies id to tests only; 28/28 passed.
