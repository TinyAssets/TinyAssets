## Implementation
- [x] Add protected personal-file route and conditional history writes.
- [x] Add Account soul/identity/full-memory editors and preserve memory row controls.
- [x] Add on-demand forget guidance and fresh-account/privacy/browser proofs.
## Verification and delivery
- [ ] Run affected Linux tests, prompt budgets, ruff, mirror and hygiene.
- [ ] Commit verified slice, open draft PR and record Claude review.
- [ ] Merge origin/main, recheck affected changes, sync spec and push.

## Evidence
Linux oracle: 327 passed (Python 3.11.16, uid 1001), including real Chromium, real bubblewrap forget-through-chat, owner refusal, fresh-account isolation and unchanged prompt budgets. Ruff passed; plugin mirror rebuilt with probe-ok. Windows browser repair: 3 passed. The Windows prompt-description budget differs by installed FastMCP; advertised tool docstrings are unchanged and the Linux budget passes.
