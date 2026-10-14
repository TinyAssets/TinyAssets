## Decisions
Use existing v2 ExtensionStore activation, revision staging and dispatch; workspace manifests never grant execution. The public and pinned engine profiles share registration without sharing mutable owner globals. Ledger histories remain untouched when paid routes are retired.

Activities retain the existing durable activity generation, run identity, resource family, provider allocation and invocation receipts. An explicit activity execution subject binds record, generation and run; it is not a branch version or a synthetic stored BranchDefinition. The sole session.start resolves agent files under the owner box binding. The run binding commits before submission; cancellation, owner loss and superseded generation fence every invocation and effect. Interrupted workers use existing run liveness recovery.

Legacy workflow conversion is staged before runtime deletion. Originals and immutable versions are retained. Conversion must preserve mapping, blocking/async completion, failure handling, parent resource lineage and cancellation. Unsupported semantics fail closed; deployment requires backup/restore and zero unresolved live legacy nodes. No runtime auto-migration or grant expansion.

## Verification
Focused activation, activity generation/admission, invocation authority, migration negative tests, engine confinement and market route tests. Real jail oracle required; local skips are not evidence. Publish draft before one Claude cross-family review. Parent owns final composition and deployment.
