Implementation absorbed into K2, `feat/four-tool-starter-cutover`, draft PR #4517,
at the founder's explicit continuation request. This remains the sole D10 seed
mechanism. API work is in progress; consumer wiring and live acceptance are pending.

## 1. Define and install

- [ ] 1.1 Implement immutable manifests and exact predecessor hashes with path validation; verify stock, unknown and legacy missing-path classification.
- [ ] 1.2 Implement design schema v1 at the canonical center sidecar starter-seeds.sqlite3 path, including binding, scoped composite keys, journal/byte blobs, tombstones and notice outbox; provision in one recoverable transaction and distinguish deletion from never-installed paths.
- [ ] 1.3 Implement exclusive creation and conditional automatic replacement of seed-identical files, preserving custom/empty/linked/unreadable/deleted paths without traversal.

## 2. Recover and expose

- [ ] 2.1 Implement turn-boundary index/receipt visibility and crash recovery with concurrent-edit preservation; verify data-loss and cross-user/center mutation matrices for create, replace, race, retry, forged binding/transaction/blob IDs, tombstones, candidates, notices and Undo.
- [ ] 2.2 Add one durable version-keyed notice with condition-specific legacy empty/linked/unreadable-AGENTS former-defaults diagnostics and actual hook/skill outcomes, plus optional hash-bound adoption/reinstall; verify missing responses never hold activation and notices deduplicate.
- [ ] 2.3 Add one-click conditional file Undo and durable owner-choice/tombstone handling; verify changed files survive and no legacy runtime or extraction is restarted.

## 3. Expose and prove the mechanism

- [ ] 3.1 Expose and contract-test the provisioning/upgrade/resume API and per-path outcomes using stock/custom/deleted fixtures, including new hooks beside unchanged AGENTS.md. Consumer/renderer wiring and all-center/dormant-center cutover proof depend exclusively on starter-agent-out-of-plumbing task 2.3; do not duplicate them here.
- [ ] 3.2 Run affected tests/heavy files and ruff; assert deployed SHA and prove stock auto-upgrade, customized preservation, visible notice and file Undo through the real app.
- [ ] 3.3 Sync starter-seed-lifecycle and confirm the parent D10 references this sole mechanism; archive only after live acceptance.
