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

## Verification and remaining integration blockers

Rebased onto main with merged #4599 (`03ba046ae2c`). The disposable combined image includes #4597 production files at `9fa0cc6d285`; this worktree does not modify that lane's files. Image `ta-soulver:final` digest is `sha256:74f695c4d3bbdd23e647149f6e3b3edd73201825dfacb615223ffd98e87e8081`.

Fresh release `tinyassets-data-2026-10-10T23-20-50Z` verified against release SHA-256 `01acbb4088b5ce4ba9beddc3a9ef8b5f50287f92d969e452da36cd51aeda3a26`. Its three soul-history entries already had owner custody. Both the unchanged restore and an explicit `--reproduce-daemon-soul-history` run passed migration. The latter replayed UID/GID 1001 on the copied directory, `0001.md` and `index.md`, without changing bytes, then proved all three returned to recorded owner UID/GID 300006 with identical paths/hashes. Repeated migration changed zero entries. The recovery-only oracle exited successfully; no content was deleted and production was not mutated.

On the unchanged restore, service health, role census and MCP canary passed. The runtime guard passed 22 request/maintenance checks, including first soul snapshots, canon, activity append, node-bid output and all seven periodic bodies with every recorded center measured. It then correctly failed because #4597 still publishes `subscriptions.json` as UID/GID 1001. Bid execution-log and auto-ship content have the same audited raw-write bypasses in that lane; the guard also exercises ledger append/rewrite after those blockers are fixed. These files remain handed off to #4597.

The post-rebase chat oracle still refused at `mapper:role_owner_launcher.py:497:ValueError:errno=None`, as before restart. Updated #4599 with the retained clone/image and failure; no further retry or launcher patch in this lane. Full combined acceptance remains blocked, so this PR must not claim the entire outage class closed or a production deployment.

Validation after restart: 151 affected Linux tests plus 97 Linux oracle/canon/proof tests passed without skips; ruff, plugin build/import probe and structural guards passed. The delta spec is synced to the main owner-content-writers spec. The non-draft infra-change PR is #4600.

## Cross-family review resolution

Retained Claude review (`VERDICT: ADAPT`, completed before restart): AGREE with the proof gaps. The runtime probe now measures every recorded owner center after each periodic body, checks the second activity append, and detects Path.open positional modes, O_CREAT, and filesystem renames. Recovery compares history custody with the durable admission identity and parses the repeated migration's changed count as JSON. Task completion reflects executed acceptance, not implementation alone.

DISAGREE_EVIDENCE on widening logging/marker exception suppression: AGENTS.md fact 8 requires failing loudly. A bounded owner launcher refusal means the center's publication authority is broken; silently continuing a phase would conceal that outage. RuntimeError/ValueError remain visible, including for activity/status and provenance markers. Existing ordinary OSError handling remains; no daemon-write fallback is added. Owner cell startup latency is the existing publication tradeoff; the runtime append check exercises the cheaper existing-file path.
