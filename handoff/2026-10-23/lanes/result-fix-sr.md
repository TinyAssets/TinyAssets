Head: `aaee00c7452d125bcb17848171f9da1bf4826658`. Worktree clean.

Fixed findings 1–6 and 8 in seven separate commits, each with a regression test observed failing first. Changes cover assignment re-anchoring, custody limits, writeback retries and validation, record-independent disconnect, stale-lease cleanup, legacy digest upgrades, and stopped-cutover enforcement.

Targeted Linux oracle runs passed: 35 related tests, 11 cleanup/drain tests, 6 cutover tests, and 16 runtime tests. Ruff and commit hooks passed. No JS changed.

Sole role: `/root`. Changed six implementation files, four test files, and `owner-daemon-in-box/design.md`; no other roles remain running.

Finding 7 was left untouched as requested. No production access, deployment, secrets, or full-suite run.