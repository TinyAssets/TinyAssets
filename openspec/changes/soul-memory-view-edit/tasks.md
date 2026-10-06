## Implementation
- [x] Add protected personal-file route and conditional history writes.
- [x] Add Account soul/identity/full-memory editors and preserve memory row controls.
- [x] Add on-demand forget guidance and fresh-account/privacy/browser proofs.
## Verification and delivery
- [x] Run affected Linux tests, prompt budgets, ruff, mirror and hygiene.
- [x] Commit verified slice, open draft PR and record Claude review.
- [x] Merge origin/main, recheck affected changes, sync spec and push.

## Evidence
Linux oracle: 327 passed (Python 3.11.16, uid 1001), including real Chromium, real bubblewrap forget-through-chat, owner refusal, fresh-account isolation and unchanged prompt budgets. Ruff passed; plugin mirror rebuilt with probe-ok. Windows browser repair: 3 passed. The Windows prompt-description budget differs by installed FastMCP; advertised tool docstrings are unchanged and the Linux budget passes.

Delivery: draft PR #4505; implementation 4bc8fe2113. Hygiene: 12 tests added, 0 removed, 0 tampering findings. origin/main was fetched and merged (already up to date at b945fb3b3a). Spec synced to openspec/specs/soul-memory-editor/spec.md. Claude review: APPROVE (read-only, 4bc8fe2113, no blockers). No deployment or production real-user pass is claimed.

## Review disposition
- AGREE: return the saved bytes revision rather than a later listing revision; fixed with a deterministic concurrent-agent regression test.
- AGREE: memory sign-in uses its own element id; fixed.
- AGREE: an oversized/non-UTF-8 personal file blocks the combined listing; deferred in docs/concerns/2026-10-06-personal-editor-unreadable-file.md (low severity, fail-closed).
- DISAGREE_EVIDENCE: requiring soul_versions snapshots for this owner editor. Its declared design uses harness history with exact-byte Undo; no data is lost and pinned soul reads already support content hashes. Legacy governed soul learning keeps its own history.
- Evidence limits: real Chromium uses shipped Account markup/functions with real handlers; forget uses a scripted model and real jailed editor. Neither claims live production/model behavior.

Review-fix verification: 58 Linux tests passed, zero skipped (personal-file server/browser/memory/history suite). Ruff passed and plugin mirror import probe passed again. Draft delivery complete; deployment and production real-user/model proof remain for rollout.
