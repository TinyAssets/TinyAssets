Implementation backlog only; design split from starter-agent-out-of-plumbing. This is the sole D10 seed mechanism. Implementation is unclaimed and will use its own worktree/PR; no product code is changed here.

## 1. Define and install

- [ ] 1.1 Implement immutable manifests and exact predecessor hashes with path validation; verify stock, unknown and legacy missing-path classification.
- [ ] 1.2 Implement owner-scoped per-path receipts and new-center provisioning as one recoverable transaction; verify installed hashes distinguish deletion from never-installed paths.
- [ ] 1.3 Implement exclusive creation and conditional automatic replacement of seed-identical files, preserving custom/empty/linked/unreadable/deleted paths without traversal.

## 2. Recover and expose

- [ ] 2.1 Implement turn-boundary index/receipt visibility and crash recovery with concurrent-edit preservation; verify the data-loss mutation matrix for create, replace, race and retry.
- [ ] 2.2 Add one visible version-keyed notice and optional hash-bound custom-file adoption/reinstall; verify missing responses never hold activation and notices deduplicate.
- [ ] 2.3 Add one-click conditional file Undo and durable owner-choice/tombstone handling; verify changed files survive and no legacy runtime or extraction is restarted.

## 3. Integrate and prove

- [ ] 3.1 Wire D10 and the starter content consumer to this one mechanism; prove unattended stock, customized and dormant centers all use the new renderer at cutover without a review gate.
- [ ] 3.2 Run affected tests/heavy files and ruff; assert deployed SHA and prove stock auto-upgrade, customized preservation, visible notice and file Undo through the real app.
- [ ] 3.3 Sync starter-seed-lifecycle and confirm the parent D10 references this sole mechanism; archive only after live acceptance.
