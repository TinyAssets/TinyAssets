Approval of sign-in in the user's own context (tracker 8.12).
- Cross-family review: one MEDIUM (native/desktop "Sign in to approve" started a full app re-login that could switch accounts) and LOWs. Fixed in ccf201c2ba4: native and desktop no longer offer that link and say how to approve in a browser with the same account; a new inline connection cancels the previous flow; expired flows clear provider-side custody. Accepted: custom scheme claimable by another app (platform limit). Recorded: two older desktop flows still lack a verifier handoff. Checked by the lead.
- Validation: failing-first focused tests; targeted Linux oracle 45 + 14 passed; ruff, node --check.

- Queue follow-up 12e87bbfd99, checked by the lead: one line, the native sign-in client is looked up only on native/desktop surfaces; browser test 4/4.
