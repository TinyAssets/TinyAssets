## Implementation
- [x] Remove v1 workspace loader and keep only revision-bound v2 catalog/dispatch.
- [x] Replace Activities branch execution with durable activity session admission and cancellation.
- [x] Convert existing owner-edited Activities instructions and queued legacy work under protected migration/restore gate.
- [ ] Retire the still-live invoke/await workflow compiler only after its callers move with equivalent authority, allocation, and workspace lineage. C10 keeps that compiler and its callers intact.
- [x] Unify handle registration while retaining pinned-owner confinement.
- [x] Retire bid/treasury and paid-market executable routes; preserve stored history.
## Verification
- [x] Run focused/affected heavy tests, Ruff and real Linux jail proof.
- [ ] Publish draft PR and complete required cross-family review with dispositions.
- [ ] Parent composes protected deployment, migration/restore evidence, SHA assertion and live acceptance.
