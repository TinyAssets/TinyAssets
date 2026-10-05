## 1. Delivered (PR #4458)

- [x] 1.1 Validate an optional `client_send_id` at the converse boundary (`invalid_client_send_id`)
- [x] 1.2 Persist it on the saved founder turn and on the active turn (additive columns, race-tolerant migration)
- [x] 1.3 Echo it in recent-conversation rows and `/app/turn/pending`, scoped to the caller's thread
- [x] 1.4 Mint per send in the app; keep it in the inflight record and notice; confirm only on an exact id, running turn first
- [x] 1.5 Display-only reply drawing for id-less running turns; no confirmation
- [x] 1.6 Regressions: identical earlier prompt (notice and restore paths), id match, missing id, id-less steering, server validation and scoping
- [x] 1.7 As-built requirement synced into `openspec/specs/onboarding-web-app/spec.md`
