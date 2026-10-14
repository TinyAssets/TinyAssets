## Implementation
- [x] Remove v1 workspace loader and keep only revision-bound v2 catalog/dispatch.
- [x] Replace Activities branch execution with durable activity session admission and cancellation.
- [ ] Convert existing owner-edited Activities instructions and queued legacy work under protected migration/restore gate.
- [ ] Stage guarded legacy invoke/await conversion and retire runtime only with equivalent authority/resource semantics.
- [ ] Unify handle registration while retaining pinned-owner confinement.
- [ ] Retire bid/treasury and paid-market executable routes; preserve stored history.
## Verification
- [ ] Run focused/affected heavy tests, Ruff and real Linux jail proof.
- [ ] Publish draft PR and complete required cross-family review with dispositions.
- [ ] Parent composes protected deployment, migration/restore evidence, SHA assertion and live acceptance.

## Checkpoint evidence
Activity/loader branch: 224 focused tests passed, including affected heavy provider_work_authority, real HTTP transport/router/journal yield and background authority mutation guards. Additional agent/activity/UI compatibility suite: 99 passed. Changed Python Ruff passed. The Linux oracle could not build: mirror.gcr.io/library/python:3.11-slim manifest HEAD returned Forbidden. No alternate dispatch or gate bypass.

Handle/market retirement is draft PR #4937 (6540bdcb4717e9e3cf7fe7a7779a5d59c818adf8), a distinct coordinated draft with isolated C12 api/extensions and C6 role-writer patches; exact-head proof awaits composition. Invoke/await package conversion has not started: generic run_graph calls do not yet preserve the old parent's allocation/workspace family. Existing edited Activities instructions and old immutable work need conversion before deployment; no runtime data was migrated or deleted. Cross-family review and protected deployment remain required.
