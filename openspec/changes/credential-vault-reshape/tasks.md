# S28 tasks

- [x] 1. Replace pre-C6 authority/custody design and delta specs.
- [x] 2. Implement broker root seam, owner envelopes and transactional key rotation.
- [x] 3. Seal HTTP/OAuth, subscription and browser custody through that hierarchy.
- [x] 4. Add offline verified conversion and ciphertext-only backup/restore checks.
- [x] 5. Preserve admission and account erasure semantics.
- [x] 6. Native subscription CLIs stay in broker custody at rest and lease into a per-run tmpfs HOME with refresh writeback (ADR-022 P2b, tracker 8.1). S28 seals that at-rest store.
- [x] 7. Run targeted Linux oracle tests and ruff; record results.
- [ ] 8. Founder: KMS setup, maintenance conversion, retained-backup rewrite and live restore proof.
- [x] 9. Open draft PR #5234 with the host-action evidence.

Verification: targeted Linux oracle custody run: 52 passed. Final root-mode
backup/recovery/key/inventory run: 86 passed. Ruff on all touched Python and
`openspec validate credential-vault-reshape --strict`: passed. Earlier runs
exposed a stale fixture, a stdlib-host import problem, and root-only recovery
fixtures; these were resolved and rerun. No full suite, production access,
cloud provisioning or secret access. Native subscription custody remains the
per-run lease in tracker 8.1. Full platform recovery retains its reconnect policy.
