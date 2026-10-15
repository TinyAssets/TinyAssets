## Implementation
- [x] Remove v1 workspace loader and keep only revision-bound v2 catalog/dispatch.
- [x] Replace Activities branch execution with durable activity session admission and cancellation.
- [ ] Convert existing owner-edited Activities instructions and queued legacy work under protected migration/restore gate.
- [ ] Stage guarded legacy invoke/await conversion and retire runtime only with equivalent authority/resource semantics.
- [x] Unify handle registration while retaining pinned-owner confinement.
- [x] Retire bid/treasury and paid-market executable routes; preserve stored history.
## Verification
- [ ] Run focused/affected heavy tests, Ruff and real Linux jail proof.
- [ ] Publish draft PR and complete required cross-family review with dispositions.
- [ ] Parent composes protected deployment, migration/restore evidence, SHA assertion and live acceptance.
