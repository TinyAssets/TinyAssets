# D7a storage and owner door

The local D7a slice adds item editing for `MEMORY.md` and reversible owner-door
harness writes. It does not complete the broader D7 task or claim live delivery.

`history.db` lives in `.agent-sessions/<universe>/`, beside `rules.db`, outside
every universe mount. Each row contains a relative harness path, prior bytes
(at most 256 KiB), prior state (`present`, `absent`, or `too large`), SHA-256 of
the resulting bytes, actor, and timestamp. Oversized prior content is explicitly
non-undoable. Undo checks the current digest, then records its own change.
SQLite write transactions serialize owner-door mutations. File replacement uses
a fresh inode, with descriptor-relative link-free parent traversal on POSIX;
the Windows tray uses the existing link-check and atomic-replace pattern.
Filesystem and SQLite commits are not a single crash-atomic transaction.

Memory reads do not write. Legacy bullets receive deterministic provisional IDs
for addressing in the UI; the next successful owner write persists those IDs.
Existing IDs remain stable; headings and other non-item lines are retained.
The authenticated `/app/memory` door resolves only the caller's home and rejects
an explicitly supplied different home. POST requires same-origin JSON.

Agent tool history is deferred: jailed writes and later workspace promotion do
not currently provide a single atomic snapshot/write hook. `MEMORY.md` joins
the brain-file mount list so owner memory is available to the agent.

Verification (2026-10-02): the six requested/added Windows test files passed
172 tests, with two symlink-privilege skips. The Linux oracle passed the 25
memory/history tests present at its snapshot, including both symlink cases,
without skips; the subsequently added write-failure rollback test also passed
on Windows. Ruff and plugin build/import checks passed. The post-app-commit
brand render left `generated-assets.json` unchanged; other renderer changes
were reverted. Local commits only; no live proof or deployment was attempted.
