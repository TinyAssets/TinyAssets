## Context

Owner measurement refuses visible daemon inodes. #4597 owns config/coordination locks; #4599 owns wiki mkdir and empty-category recovery. This lane owns remaining writers and recovery acceptance.

## Decisions

Remove the soul snapshot's eager mkdir: the existing owner-content writer creates missing parents and preserves exclusive snapshot publication. Audit raw directory, file, copy, and database writers plus their call sites, separating owner content from protected platform state and owner-cell execution.

Use the existing offline role migration for nonempty debris. Its exclusive layout lock, preflight, pinned descriptors, recorded owner identities and label repair already support idempotent reruns. Compare every soul-history path and byte digest before/after on a restored production copy and assert owner UID/GID; do not add an online recursive repair or delete content. The production daemon must be stopped and a fresh full backup verified before an operator runs this repair.

## Risks / Trade-offs

Runtime coverage cannot exercise every branch: pair real split-UID request/loop execution with a static writer inventory. Migration relabels content in place and requires the existing maintenance capabilities; serving capabilities remain unchanged. Revert code independently of the repaired owner labels.

## Writer audit

Static search covered `tinyassets/` and `domains/` for mkdir, open/write, atomic replacement, copying, and database creation, then traced center/data-root/repository/workspace destinations. The existing `test_universe_path_io_guard` omits mkdir and domain modules; `test_owner_content_writers` adds those missing checks for content writers.

| Destination / writer family | Disposition |
| --- | --- |
| Soul edit snapshots | Remove daemon mkdir; owner-content creates parents and exclusive numbered files. Soul seeding and legacy soul updates already use `write_data_path`. |
| Inline canon upload | Remove daemon mkdir; ingestion source, manifest and provenance writes already use owner publication. |
| Domain prose/scene packets, premise, canon/reflect rewrites and markers, activity/status | Replace raw writes with `write_data_path`; remove eager parent creation. Dotfiles beneath visible canon are also owner content. |
| Node-bid output artifacts | Replace daemon mkdir/write with owner publication. |
| Config and four coordination locks | #4597. Runtime test additionally found subscriptions content still daemon-owned; bid execution and auto-ship raw writers handed back to that lane. |
| Wiki request mkdir | #4599. |
| Queue, notes, signals, brain, skills/starter files, package content, run uploads | Existing `write_data_path` / `write_universe_file` or owner-cell publication. |
| Credentials, sessions, authoring blobs, locks, SQLite stores | Dot-prefixed protected entries or data-root/platform stores; no visible owner entry creation. |
| Workspace/git/provisioning, local boxes, provider/tool cells | Owner identity execution or private sidecar/staging paths; covered by the role image oracle's existing cell legs. |
| Daemon wiki/memory, catalog/bids/goal pool, OKF export | Data-root `daemon_wikis`, community repository, or explicit CLI export destinations, not admitted owner-center paths in daemon callers. |
| Desktop/local file-interface and standalone fantasy runner | Separate entry points, not the serving daemon's periodic/request graph; their host filesystem writes are outside this production-path proof. |

## Verification in progress

Fresh release `tinyassets-data-2026-10-10T21-45-21Z` verified against release SHA-256 `84ca0d360e71f52e5adb98abb7762a3d4e61e2e550eee0df64e0cefbf8b8e999`. On a disposable restored clone, soul-history hashes/paths and target owner custody passed; repeated migration changed zero entries. Service health, role census and MCP canary passed. Combined runtime probe found the subscriptions writer owned by #4597. Two chat attempts encountered the same mapper refusal and were handed to #4599, without modifying that lane's files. No production mutation.
