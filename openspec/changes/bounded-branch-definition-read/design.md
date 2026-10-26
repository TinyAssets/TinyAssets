# Design
## Context
See proposal. Existing node pagination is useful for previews but irreversibly clips a single large node.
## Decisions
Use the existing @definition selector pattern from agent reads. Canonically serialize the authorized complete branch as ASCII JSON, then return bounded chunks with Unicode-code-point offsets, full UTF-8 length and SHA-256. JSON escaping preserves all source characters, including non-BMP and surrogate values. Consumers concatenate chunks, require identical digests/lengths across pages, verify SHA-256, then decode JSON. A changed definition has a different digest; restart instead of mixing snapshots. No snapshot storage or execution is introduced.
Authorization runs on every read before selection. Engine reads forward selectors and retain the existing foreign-content envelope. Projection measures serialized JSON and keeps existing envelope headroom; tests exercise the final engine envelope and ceiling. Node previews advertise @definition when paged, including the single-node complete=true case. Invalid selector/offset/size returns an explicit error.
## Risks / Trade-offs
Full serialization and hashing repeat per page; this trades CPU for no durable snapshot/access state. Concurrent edits require restarting. Existing node preview behavior remains.
## Migration Plan
No migration. Parent owns eventual merge/deploy; after deployment retrieve and verify the actual owned adapter before installing anything. Revert restores the existing preview-only read.
