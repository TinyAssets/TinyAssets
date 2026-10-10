## Context

PR #4597 folds #4600 and includes main's #4596/#4599 fixes. Every daemon
publication beneath visible owner content must use the owner-content writer.

## Decisions

Subscriptions, bid execution logs, and auto-ship append/rewrite join the existing
config, soul, canon, wiki, domain, and node-bid fixes. Dotted coordination locks
remain daemon state. Runtime acceptance measures every admitted center after
requests and periodic bodies; static checks cover direct creation bypasses.

Recover existing damage with one explicit online operator operation in the
existing migration executable. It reads the live durable admission database in
a SQLite transaction, pins canonical center roots and no-follow descendants,
and relabels only daemon-owned owner content in place. It never copies, replaces,
deletes or truncates content, allocates identities, or repairs platform state.
Already owner-owned entries are unchanged. Foreign owners, links on repair
candidates, special files and multiply linked repair candidates refuse.

The operator container uses the existing migration capabilities; the running
daemon gains no authority. Keeping inodes preserves open descriptors and concurrent
appends, unlike republishing a stale copy. Retire the special empty-wiki replacement
and the stopped-migration recovery prescription. A verified fresh backup remains
required; the initial isolation cutover stays a separate offline operation.

## Verification

Prove both untouched production-backup recovery and explicit daemon-custody
damage replay with a running real server, all paths and byte digests preserved,
and zero changes on repeat. Then run real chat, all four tools, image and connection
calls and the complete writer guard. Diagnose the mapper authentication refusal
without weakening PID, credential or descriptor validation.

## Risks

A changing directory can require another pass; repaired inodes remain valid and
no content is rolled back. The repair refuses unexpected custody rather than
assuming another owner's entry belongs to this owner. Interrupted operations are
idempotent. Cross-family review covers custody and data-loss guards.

## Writer audit

Static search covered `tinyassets/` and `domains/` for mkdir, open/write, atomic replacement, copying, and database creation, then traced center/data-root/repository/workspace destinations. The existing `test_universe_path_io_guard` omits mkdir and domain modules; `test_owner_content_writers` adds those missing checks for content writers.

| Destination / writer family | Disposition |
| --- | --- |
| Soul edit snapshots | Remove daemon mkdir; owner-content creates parents and exclusive numbered files. Soul seeding and legacy soul updates already use `write_data_path`. |
| Inline canon upload | Remove daemon mkdir; ingestion source, manifest and provenance writes already use owner publication. |
| Domain prose/scene packets, premise, canon/reflect rewrites and markers, activity/status | Replace raw writes with `write_data_path`; remove eager parent creation. Dotfiles beneath visible canon are also owner content. |
| Node-bid output artifacts | Replace daemon mkdir/write with owner publication. |
| Config, subscriptions, bid execution and auto-ship | Owner-content publication; four dotted coordination locks. |
| Wiki request mkdir | Main #4599 owner-content publication; recovery unified here. |
| Queue, notes, signals, brain, skills/starter files, package content, run uploads | Existing `write_data_path` / `write_universe_file` or owner-cell publication. |
| Credentials, sessions, authoring blobs, locks, SQLite stores | Dot-prefixed protected entries or data-root/platform stores; no visible owner entry creation. |
| Workspace/git/provisioning, local boxes, provider/tool cells | Owner identity execution or private sidecar/staging paths; covered by the role image oracle's existing cell legs. |
| Daemon wiki/memory, catalog/bids/goal pool, OKF export | Data-root `daemon_wikis`, community repository, or explicit CLI export destinations, not admitted owner-center paths in daemon callers. |
| Desktop/local file-interface and standalone fantasy runner | Separate entry points, not the serving daemon's periodic/request graph; their host filesystem writes are outside this production-path proof. |


## Acceptance findings

The latest full data release at verification was `tinyassets-data-2026-10-10T23-20-50Z`;
its 1,669,836,618-byte archive matched the release SHA-256
`01acbb4088b5ce4ba9beddc3a9ef8b5f50287f92d969e452da36cd51aeda3a26`.
The stock copy was already converged: six centers, 71,322 visible entries,
zero repairs. Explicitly replaying the old history/config/empty-category custody
required 326 inode repairs. All 71,323 visible paths, inodes and content hashes
were preserved; both repeated repairs changed zero entries. The real serving
PID stayed healthy with the same capability-free daemon throughout.

The prior mapper line-497 refusal was teardown after the chat fixture raised
StopIteration selecting a GitHub connection from the history-only center
`u-01m4bezb4exbcmygtyyxvcm5py`. The connected founder center
`u-01kxm1vszd8hwp7em418asq8h9` passed both providers, all four tools, image
round trips, connection calls, sub-agent and background/automation/patch-intake
runs. The harness now checks that prerequisite explicitly and preserves bounded
exception-frame diagnostics. No launcher authentication check changed.

The successful chat run also emitted `revoked kind=provider-exec exit=-9` with
relay reset errno 104. `_service_jobs` kills a still-running cell when its
per-launch control channel closes; provider cleanup closes that channel after
the turn. Relay half-close errno 107 and pump errno 104 describe teardown of
already disconnected/reset sockets, not owner-measure custody refusal. A
revocation during an unfinished turn still needs its turn outcome inspected.

## Cross-family review

Claude review completed with `VERDICT: APPROVE`. AGREE: inode-preserving repair,
recorded identities, no-follow custody checks, writer routing, retired tests and
the root-test gate registration satisfy the floor/correctness checks. The
operator verifies the backup digest as documented; the CLI validates its name.
A concurrently missing center fails closed and can be retried. AGREE with
keeping outer harness diagnostics bounded: its top-level handler now exits
without printing an unfiltered exception message. Old/new lock coexistence is
avoided by the existing single-container replacement deployment.

Final combined acceptance completed `restore,repair,writers,chat`: 29 writer/request/periodic checks (every recorded center measured after each), real service health/census/canary, both provider turns and sub-agent, all four tools, images, connections and graph runs. Focused Linux validation passed 462 tests; seven root recovery tests and all 595 structural guards passed. The oracle/integrity checks passed 31 tests; Ruff and plugin build/import passed.

The hygiene gate passed with the declared retirement of two obsolete tests; hooks ran normally.
