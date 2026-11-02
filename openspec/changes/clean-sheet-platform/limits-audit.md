# Limits audit (slice D)

The founder, 2026-11-02: no tiny limits before he has used the platform (`tasks.md`, the
limits-audit item). This lists every bound left in `platform/*/src` after slice D's
deletes that refuses or truncates something a user does, with its file and line, value,
what it bounds, what a user would hit, and one class:

- **tier**: simultaneous runs or storage, from the tier record (free 5 runs and 25 GB,
  paid 10 runs and 250 GB; ADR-034 decision 8);
- **owner setting**: the owner's own, default off or generous;
- **technical ceiling**: kept, with the platform fact that forces it;
- **raise**: an invented small default, with a proposed generous value.

**No limit changed in this slice.** The founder sees this list first; each change is its
own later slice, and a raise that moves an owner-bound guard term recomputes the guard
(slice D left the worst case at 40.6% of 10 GB, about 935 MB under half). `[I]` marks
an inference not checked against a primary source here.

Not listed, since none refuses or truncates what a user does: internal timings and
backoffs (leases, retries, garbage-collection grace, lane passes, wake retries), the
SQLite layout constants (`manifest.ts`), hashes and key sizes, and the HTTP status of
each fixed code.

## Summary

- **tier**: 3
- **owner setting**: 11
- **technical ceiling**: 71
- **raise**: 29
- **total**: 114

Flagged for the founder: the files default `max_bytes` (1 GiB, to become the tier's storage); the egress tokens' `RUN_DEADLINE_S` (a day: a token lifetime, with the proposal to tie it to the lease); and the model policy's per-run caps (`turn_requests` 100, `turn_tokens` 10M, `turn_cost` $25), on by default where ADR-034 decision 7 makes the owner's own limits off by default.

### Every raise, with its proposed value

| Where | Today | Proposed |
|---|---|---|
| `owner/src/policy.ts:180` | 30,000 (high 500,000) | Propose 480,000, what `max_manifest_bytes` holds at one version each. Blocked today by the policy rule (`max_history >= max_entries x max(keep_versions, keep_recent)`, `max_history` at most 600,000), which caps `max_entries` at 30,000 with `keep_recent` 20: the rule must count history in bytes, as the manifest already does, before this can rise. |
| `owner/src/files.ts:107` | 3,600 s | Propose 86,400 s (a day): a 40 GiB file at 10 MB/s takes over an hour. |
| `owner/src/collections.ts:51` | 65,536 characters | Propose 1 MiB: the schema's CHECK is ours (a new migration), and a Durable Object's SQLite row may hold 2 MB. |
| `owner/src/collections.ts:52` | 64 | Propose 1,000, inside `maxRows`; its purpose is a share, which `maxRows` and `maxOwnerRows` already bound. |
| `owner/src/collections.ts:53` | 512 KiB | Propose 8 MiB (the collection's own bound). |
| `owner/src/collections.ts:54` | 1,000 | Propose 10,000. |
| `owner/src/collections.ts:55` | 8 MiB | Propose 64 MiB. |
| `owner/src/collections.ts:56` | 10,000 | Propose 100,000; the owner-bound guard's room (about 935 MB under half after slice D) takes it, the guard recomputed. |
| `owner/src/collections.ts:57` | 64 MiB | Propose 512 MiB, recomputed in the owner-bound guard. |
| `owner/src/collections.ts:59` | 10 | Propose 100, recomputed in the owner-bound guard. |
| `owner/src/ledger.ts:68` | 16,384 characters | Propose 65,536 (the board's row size today); larger hand-offs go as files. |
| `owner/src/ledger.ts:163` | 100 | Propose 1,000 (the row bound `maxAgentRows`). |
| `owner/src/ledger.ts:167` | 2,000 | Propose 10,000 (`maxMessages`). |
| `owner/src/ledger.ts:168` | 16 MiB | Propose 64 MiB (`maxBodyBytes`). |
| `owner/src/ledger.ts:169` | 80 | Propose 1,000. |
| `owner/src/ledger.ts:170` | 480 KiB | Propose 8 MiB. |
| `owner/src/ledger.ts:171` | 500 | Propose 5,000. |
| `owner/src/ledger.ts:172` | 4 MiB | Propose 32 MiB. |
| `owner/src/grants.ts:115` | 64 | Propose 256; a scope is about 9 KiB of the guard's term, so recompute. |
| `owner/src/sessions.ts:57` | 16 | Propose 64: one owner's phones, laptops and browsers. |
| `owner/src/network.ts:157` | 300 s | Propose 3,600 s: a long reasoning answer can stream past 5 minutes, and a Worker serving a streamed response has no wall-clock limit while its client stays connected [I: Cloudflare Workers limits]. `LOST_AFTER_S` and the gateway's `FORWARD_DEADLINE_MS` follow it (a test holds them equal). |
| `owner/src/network.ts:159` | 16 MiB | Propose 32 MiB, the largest request a provider takes [I: Anthropic's Messages API documents 32 MB; to verify]; the gateway's `BODY_MAX` follows. |
| `owner/src/codemode.ts:29` | 60 s | Propose 300 s: a script that calls slow tools (a run, a fetch) needs more than a minute. |
| `owner/src/cron.ts:29` | 15 minutes | Propose 1 minute: the owner pays the sandbox time; the tier's simultaneous runs already bound it. |
| `owner/src/notices.ts:14` | 100 | Propose 1,000; recomputed in the owner-bound guard. |
| `doors/src/api-keys.ts:40` | 25 | Propose 200: a key per device or agent. |
| `gateway/src/forward.ts:34` | 5 minutes | Follows `EGRESS_DEADLINE_S` (propose an hour). |
| `gateway/src/forward.ts:43` | 16 MiB | Follows `EGRESS_BODY_MAX` (propose 32 MiB). |
| `gateway/src/credential.ts:134` | 64 | Propose 1,024, a connection's own `maxModels`. |

## The list

| Where | Value | Bounds | A user hits | Class | Reason or proposal |
|---|---|---|---|---|---|
| `owner/src/policy.ts:177` | 1 GiB (owner may set up to 1 TiB) | the owner's file bytes, live and in history (unique chunks) | a write, upload or sync refused `file_quota` past 1 GiB | tier | **Flagged.** Becomes the tier's storage: free 25 GB, paid 250 GB, read from the tier record (ADR-034 decision 8). The setting's range (to 1 TiB, `FILE_SETTINGS`) stays above both. |
| `owner/src/policy.ts:178` | 256 MiB (high 384 MiB) | small files kept inside the object's own database | a small file refused `file_quota` once 256 MiB of them are held | technical ceiling | The object's 10 GB SQLite database (Cloudflare Durable Objects); the owner-bound guard holds the worst case under half. Not a user's storage figure: larger files go to R2 chunks. |
| `owner/src/policy.ts:179` | 512 MiB (high 512 MiB) | every manifest row (paths, versions, history) as SQLite stores it | file changes refused once the manifest's rows fill it | technical ceiling | The object's 10 GB database at `WORST_FILL` 2.25 (the owner-bound guard's largest term). |
| `owner/src/policy.ts:180` | 30,000 (high 500,000) | live paths (files and directories) | a new file or directory refused `file_quota` at 30,000 (one `node_modules` passes it) | raise | Propose 480,000, what `max_manifest_bytes` holds at one version each. Blocked today by the policy rule (`max_history >= max_entries x max(keep_versions, keep_recent)`, `max_history` at most 600,000), which caps `max_entries` at 30,000 with `keep_recent` 20: the rule must count history in bytes, as the manifest already does, before this can rise. |
| `owner/src/policy.ts:181` | 5 GiB (high 40 GiB) | one file's size | an upload of a larger file refused | owner setting | Owner setting; 5 GiB default. Its high is `MAX_INTENT_CHUNKS` chunks. |
| `owner/src/policy.ts:182` | 600,000 (high 600,000) | kept history rows | old versions pruned earliest-due first; a change refused only when nothing can be pruned | technical ceiling | The manifest's byte bound (`max_manifest_bytes`): about 850 bytes a kept version. |
| `owner/src/policy.ts:183` | 30 days (high 3,650) | how long kept versions stay | a version older than this pruned | owner setting |  |
| `owner/src/policy.ts:184` | 10 (high 1,000) | versions kept for each live path | older versions pruned | owner setting |  |
| `owner/src/policy.ts:185` | 20 (high 1,000) | recent versions kept in all for a path | older versions pruned | owner setting |  |
| `owner/src/policy.ts:186` | 25% (0 to 100) | one outside client's share of every file quota | a client's write refused past its share | owner setting |  |
| `owner/src/policy.ts:187` | 25% (0 to 100) | the room all clients together leave the owner | clients' writes refused within it | owner setting |  |
| `owner/src/files.ts:132` | 10,000 chunks (40 GiB) | one upload's chunks: one file's largest size | a file over 40 GiB cannot be uploaded | technical ceiling | One upload intent's chunk list is one row of the manifest; 10,000 of 64 hex each. Above every tier's single-file need [I]. |
| `owner/src/files.ts:134` | 64 | uploads open at once | a 65th concurrent upload refused | technical ceiling | Each open intent holds up to 20,000 chunk ids in the owner's database (owner-bound guard). |
| `owner/src/files.ts:135` | 20,000 | chunks all open uploads hold | a new upload refused while they hold it | technical ceiling | As `MAX_OPEN_INTENTS`. |
| `owner/src/files.ts:107` | 3,600 s | how long an upload may stay open | a large upload over a slow link expires mid-way and starts over | raise | Propose 86,400 s (a day): a 40 GiB file at 10 MB/s takes over an hour. |
| `owner/src/files.ts:137` | 1,000 | ops in one sandbox sync commit | the sync agent splits a larger commit | technical ceiling | One synchronous gate unit per commit; a unit must stay short (Durable Object CPU per request). |
| `owner/src/files.ts:139` | 1,000 | the most versions `keep_versions` and `keep_recent` take | the setting refused above it | technical ceiling | Bounds a path's history rows inside the manifest's bytes. |
| `owner/src/files.ts:141` | 100 | conflict copies `<path>.ta-conflict-<epoch>-<n>` for one path | the 101st conflicting sync of one path refused | technical ceiling | Bounds the names a conflict probes; a user resolving conflicts never nears it [I]. |
| `owner/src/files.ts:130` | 1,000 | entries one list call returns | the caller pages | technical ceiling | A page, not a limit on what is held. |
| `owner/src/owner-do.ts:1679` | 4 MiB | one `fileWrite` call's content | a larger file goes by upload | technical ceiling | Workers RPC's message size (32 MiB) and the object's per-request memory; the upload doors move any size. |
| `owner/src/owner-do.ts:1662` | 2 chunks (8 MiB) | one `fileReadChunks` call | the caller reads in more calls | technical ceiling | As above. |
| `owner/src/paths.ts:30` | 4,096 bytes | a path | a longer path refused | technical ceiling | Linux's `PATH_MAX`, which the sandbox's disk holds. |
| `owner/src/paths.ts:46` | 255 bytes | one path component | a longer name refused | technical ceiling | Linux's `NAME_MAX`. |
| `owner/src/files.ts:1604` | 64 characters | a temporary directory's prefix | a longer prefix refused | technical ceiling | A name, not content [I]. |
| `owner/src/files.ts:1643` | 128 characters | the sync lease holder's name | a longer holder name refused | technical ceiling | Internal name, set by the sandbox slice. |
| `owner/src/collections.ts:51` | 65,536 characters | one board or room row's body | a larger row refused | raise | Propose 1 MiB: the schema's CHECK is ours (a new migration), and a Durable Object's SQLite row may hold 2 MB. |
| `owner/src/collections.ts:52` | 64 | rows one writer holds in a collection | a writer's 65th row refused | raise | Propose 1,000, inside `maxRows`; its purpose is a share, which `maxRows` and `maxOwnerRows` already bound. |
| `owner/src/collections.ts:53` | 512 KiB | bytes one writer holds in a collection | a writer's row refused past it | raise | Propose 8 MiB (the collection's own bound). |
| `owner/src/collections.ts:54` | 1,000 | rows in one collection | a board's 1,001st card refused | raise | Propose 10,000. |
| `owner/src/collections.ts:55` | 8 MiB | bytes in one collection | rows refused past it | raise | Propose 64 MiB. |
| `owner/src/collections.ts:56` | 10,000 | rows in all of the owner's collections | rows refused past it | raise | Propose 100,000; the owner-bound guard's room (about 935 MB under half after slice D) takes it, the guard recomputed. |
| `owner/src/collections.ts:57` | 64 MiB | bytes in all of the owner's collections | rows refused past it | raise | Propose 512 MiB, recomputed in the owner-bound guard. |
| `owner/src/collections.ts:58` | 100 | rows one pull returns | the caller pages | technical ceiling | A page. |
| `owner/src/collections.ts:59` | 10 | history revisions kept per row | older revisions dropped | raise | Propose 100, recomputed in the owner-bound guard. |
| `owner/src/text.ts:33` | 256 characters | a collection, row or writer name | a longer name refused | technical ceiling | A name, not content. |
| `owner/src/ledger.ts:68` | 16,384 characters | one message body | a longer message refused | raise | Propose 65,536 (the board's row size today); larger hand-offs go as files. |
| `owner/src/ledger.ts:163` | 100 | enabled outside agents | the 101st agent's registration refused | raise | Propose 1,000 (the row bound `maxAgentRows`). |
| `owner/src/ledger.ts:164` | 1,000 | agent rows, tombstones included | a registration refused past it | technical ceiling | Owner-bound guard term. |
| `owner/src/ledger.ts:165` | 10,000 | messages held | a post refused past it | technical ceiling | Owner-bound guard term (each message times each recipient). |
| `owner/src/ledger.ts:166` | 64 MiB | message bodies held | a post refused past it | technical ceiling | Owner-bound guard term. |
| `owner/src/ledger.ts:167` | 2,000 | messages unread by the owner | a post to the owner refused past it | raise | Propose 10,000 (`maxMessages`). |
| `owner/src/ledger.ts:168` | 16 MiB | bytes unread by the owner | a post refused past it | raise | Propose 64 MiB (`maxBodyBytes`). |
| `owner/src/ledger.ts:169` | 80 | messages unread in one agent's mailbox | a post to an agent refused once 80 wait | raise | Propose 1,000. |
| `owner/src/ledger.ts:170` | 480 KiB | bytes unread in one agent's mailbox | a post refused past it | raise | Propose 8 MiB. |
| `owner/src/ledger.ts:171` | 500 | one sender's messages still unread | a sender's post refused past it | raise | Propose 5,000. |
| `owner/src/ledger.ts:172` | 4 MiB | one sender's unread bytes | a sender's post refused past it | raise | Propose 32 MiB. |
| `owner/src/ledger.ts:173` | 50,000 | read and reply receipts kept | the oldest let go | technical ceiling | Owner-bound guard term; a history. |
| `owner/src/ledger.ts:175` | 1,000 | settled wakes kept | the oldest let go | technical ceiling | Owner-bound guard term. |
| `owner/src/ledger.ts:176` | 100 | messages one read returns | the caller pages | technical ceiling | A page. |
| `owner/src/ledger.ts:93` | 12 wakes an hour, 60 s batch | an agent's doorbells | wakes past 12 an hour wait for the hour | owner setting | Owner setting per agent (`policyWakeLimits`), 1 to 60 an hour. Propose the default 60. |
| `owner/src/ledger.ts:95` | 60 an hour at most | the wake setting's high | a higher setting refused | technical ceiling | The rings table's bound (the owner-bound guard: an hour's rings per agent). |
| `owner/src/ledger.ts:407` | 80 characters | an agent's display name | a longer name refused | technical ceiling | A label. |
| `owner/src/registrations.ts:52` | 200 pending, 10 per client, 4,096-byte card | registration cards held for the owner's apply | a client's 11th card refused | technical ceiling | Owner-bound guard term; cards wait for the owner. |
| `owner/src/registrations.ts:43` | 86,400 s | how long a filed card waits | the client files again | technical ceiling | A held request, not the owner's state. |
| `owner/src/grants.ts:113` | 1,000 | clients recorded without an owner grant | the oldest free one forgotten (never refused) | technical ceiling | Owner-bound guard term. |
| `owner/src/grants.ts:114` | 1,000 | clients the owner set a grant for | the 1,001st grant refused | technical ceiling | Owner-bound guard term. |
| `owner/src/grants.ts:115` | 64 | scope triples in one grant | a wider grant refused | raise | Propose 256; a scope is about 9 KiB of the guard's term, so recompute. |
| `owner/src/grants.ts:341` | 80 characters | a client's name | a longer name refused | technical ceiling | A label. |
| `owner/src/grants.ts:178` | 512 characters | a scope's universe, agent or capability | a longer field refused | technical ceiling | A label. |
| `owner/src/sessions.ts:56` | 8 hours | the owner's protected session | the owner signs in again for a widening | technical ceiling | Security: the Python's session life, the cookie's `Max-Age`. |
| `owner/src/sessions.ts:57` | 16 | signed-in devices at once | the oldest session closed | raise | Propose 64: one owner's phones, laptops and browsers. |
| `owner/src/sessions.ts:58` | 600 s | how fresh a sign-in must be to open a session | the owner signs in again | technical ceiling | Security (S30). |
| `owner/src/policy.ts:123` | 65,536 characters | the owner policy document | a larger policy refused | technical ceiling | One row read on every door; owner-bound guard term. |
| `owner/src/policy.ts:124` | 16,000 characters | one agent's proposal | a larger proposal refused | technical ceiling | Owner-bound guard term. |
| `owner/src/policy.ts:126` | 200 (50 a client) | proposals waiting for the owner | a proposal refused past it | technical ceiling | Owner-bound guard term; the owner reviews each. |
| `owner/src/policy.ts:129` | 5,000 a bucket | policy receipts kept | the oldest let go | technical ceiling | Owner-bound guard term; a history. |
| `owner/src/policy.ts:261` | 64 | sources a `models.sources` table names | a larger table refused | technical ceiling | Inside `maxDocument`. |
| `owner/src/policy.ts:246` | 100 (1 to 10,000) | model calls in one sandbox run (held at the egress door) | a long engine run's 101st model call refused `run_requests` | owner setting | **Flagged.** On by default today; ADR-034 decision 7 makes the owner's own limits off by default. Propose off (no cap) unless the owner sets one, with the range kept. |
| `owner/src/policy.ts:247` | 10,000,000 tokens | tokens in one sandbox run | a call refused `run_tokens` | owner setting | **Flagged** as above: propose off by default. |
| `owner/src/policy.ts:248` | $25 | cost of one sandbox run | a call refused `run_cost` | owner setting | **Flagged** as above: propose off by default. |
| `owner/src/policy.ts:249` | none | the owner's daily and monthly spend | nothing until the owner sets one | owner setting | Off by default, as ADR-034 asks. |
| `owner/src/connections.ts:100` | 50 | live model connections | the 51st connect refused | technical ceiling | Owner-bound guard term (640 KiB of models each). |
| `owner/src/connections.ts:104` | 10 | OAuth connect flows open at once | an 11th flow refused | technical ceiling | Security: each holds a PKCE verifier; flows expire in 10 minutes. |
| `owner/src/connections.ts:103` | 600 s | an OAuth flow's life | the owner starts the connect again | technical ceiling | Security (RFC 6749 practice: short-lived authorization state). |
| `owner/src/connections.ts:106` | 1,024 | models one connection lists | models past it are refused at commit | technical ceiling | 640 KiB of models JSON (`MODELS_JSON_MAX`), the guard's connection term; the largest built-in catalog has 386. |
| `owner/src/connections.ts:341` | 200 characters | a model's display name | the model refused at commit | technical ceiling | A label. |
| `owner/src/usage.ts:74` | 1,024 tokens | the least output a reservation is worth | a call refused when a ceiling leaves less | technical ceiling | Only when an owner's ceiling binds; provider thinking budgets start at 1,024 tokens (pi-ai's floor). |
| `owner/src/usage.ts:75` | 2,048 tokens | the least output for a reasoning call | as above | technical ceiling | pi-ai keeps 1,024 for the answer beside a 1,024 thinking budget. |
| `owner/src/usage.ts:72` | 5,000 | model-call receipts kept | the oldest let go | technical ceiling | Owner-bound guard term; pruning frees no counter. |
| `owner/src/schema.ts:176` | 1,000 | model reservations open at once | a reservation refused `model_calls_full` | technical ceiling | Owner-bound guard term; far above any tier's simultaneous runs. |
| `owner/src/schema.ts:182` | 4,300 | day and month spend counters | a 101st source's first call in a period refused `model_spend_full` | technical ceiling | Owner-bound guard term: 40 days and 3 months at 100 sources. |
| `owner/src/egress.ts:84` | 64 | sandbox model calls streaming at once | a 65th concurrent call refused `egress_rate` | tier | Derive from the tier's simultaneous runs: propose 16 a run (free 80, paid 160). |
| `owner/src/egress.ts:85` | 600 a minute | sandbox calls opened a minute | a call refused `egress_rate` | tier | Derive from the tier's simultaneous runs: propose 120 a run a minute (free 600, paid 1,200). |
| `owner/src/egress.ts:86` | 60 a minute | refusals receipted a minute | later refusals in the minute are not receipted (still refused) | technical ceiling | Keeps a refused flood from writing rows. |
| `owner/src/egress.ts:87` | 2,000 admitted, 500 refused | egress receipts kept | the oldest let go | technical ceiling | Owner-bound guard term. |
| `owner/src/egress.ts:247` | 1,024 tokens | a sandbox call's least output | refused when a cap leaves less | technical ceiling | As `usage.ts`'s `minOutput`. |
| `owner/src/egress.ts:95` | 2,048 characters | a sandbox request's path and query | a longer path refused | technical ceiling | Common URL length bound; every provider API path is far shorter. |
| `owner/src/network.ts:157` | 300 s | one streamed sandbox model call | a model call streaming past 5 minutes is cut (`deadline`) and settled `unknown` | raise | Propose 3,600 s: a long reasoning answer can stream past 5 minutes, and a Worker serving a streamed response has no wall-clock limit while its client stays connected [I: Cloudflare Workers limits]. `LOST_AFTER_S` and the gateway's `FORWARD_DEADLINE_MS` follow it (a test holds them equal). |
| `owner/src/network.ts:159` | 16 MiB | one sandbox model request's body | a larger request refused `egress_too_large` | raise | Propose 32 MiB, the largest request a provider takes [I: Anthropic's Messages API documents 32 MB; to verify]; the gateway's `BODY_MAX` follows. |
| `owner/src/network.ts:166` | 86,400 s | the egress and sync tokens' life from the lease's open | a run longer than a day loses its tokens and must open a new lease | technical ceiling | **Flagged.** With no run deadline it is only a token lifetime: E2B's longest continuous sandbox session is a day (the network module's note; to confirm in slice S). Proposed: tie the tokens' expiry to the lease, renewed with its heartbeat, so a run never meets a fixed deadline. |
| `owner/src/codemode.ts:29` | 60 s | one code mode script's wall time | a longer script ends `timeout` | raise | Propose 300 s: a script that calls slow tools (a run, a fetch) needs more than a minute. |
| `owner/src/cron.ts:29` | 15 minutes | the shortest gap between a recurring schedule's runs | a schedule more often than every 15 minutes refused | raise | Propose 1 minute: the owner pays the sandbox time; the tier's simultaneous runs already bound it. |
| `owner/src/cron.ts:27` | 16 | values in one cron field's list | a longer list refused | technical ceiling | Bounds a field's parse. |
| `owner/src/cron.ts:25` | 64 characters | a cron expression | a longer expression refused | technical ceiling | Five fields fit in far less. |
| `owner/src/cron.ts:31` | 8 years | how far ahead the next occurrence is searched | an expression with no occurrence in 8 years refused | technical ceiling | Bounds the search (a leap-day schedule recurs inside it). |
| `owner/src/notices.ts:14` | 100 | owner notices kept | the oldest let go | raise | Propose 1,000; recomputed in the owner-bound guard. |
| `owner/src/notices.ts:16` | 1,024 characters | a notice's detail | a longer detail cut | technical ceiling | A summary; the full error stays in the source. |
| `doors/src/api-keys.ts:40` | 25 | API keys an owner holds | a 26th key refused | raise | Propose 200: a key per device or agent. |
| `doors/src/api-keys.ts:41` | 80 characters | an API key's name | a longer name refused | technical ceiling | A label. |
| `doors/src/config.ts:59` | 3,600 s | an MCP or app access token's life | the client refreshes | technical ceiling | Security; refresh is silent. |
| `doors/src/config.ts:60` | 30 days | a refresh token's life | an unused client signs in again after 30 days | technical ceiling | Security (OAuth practice). |
| `doors/src/config.ts:66` | 600 s | how fresh a sign-in is for consent | the owner signs in again | technical ceiling | Security (`prompt=login`). |
| `doors/src/config.ts:70` | 4 KiB | the consent form's body | a larger body refused `413` | technical ceiling | The form holds a few fields. |
| `doors/src/config.ts:71` | 64 KiB | an owner-surface request body | a larger body refused `413` | technical ceiling | No owner-surface door takes more than a 64 KiB field. |
| `doors/src/config.ts:72` | 1 MiB | an `/api/tools/*` request body | a larger body refused `413` | technical ceiling | Above every tool's largest argument (a 64 KiB row); file bytes are streamed by their own doors (tasks 2). Workers accept 100 MB on Paid. |
| `gateway/src/forward.ts:34` | 5 minutes | one forwarded request | as `EGRESS_DEADLINE_S` | raise | Follows `EGRESS_DEADLINE_S` (propose an hour). |
| `gateway/src/forward.ts:43` | 16 MiB | a metered call's body the gateway reads | as `EGRESS_BODY_MAX` | raise | Follows `EGRESS_BODY_MAX` (propose 32 MiB). |
| `gateway/src/credential.ts:134` | 64 | models a declared custom endpoint lists | a larger list refused at seal | raise | Propose 1,024, a connection's own `maxModels`. |
| `gateway/src/credential.ts:132` | 16 (64 paths each) | sealed endpoints of one credential | a larger record refused | technical ceiling | Inside the 64 KiB sealed blob. |
| `gateway/src/credential.ts:131` | 16 KiB | a pasted secret | a larger secret refused | technical ceiling | Inside the 64 KiB sealed blob; no provider key nears it. |
| `gateway/src/oauth.ts:36` | 15 s | a provider's token endpoint answer | a slow provider's connect fails `oauth_unreachable` | technical ceiling | An OAuth token call; the owner retries. |
| `gateway/src/forward.ts:37` | 15 s | the setup check's provider call | a slow check fails; the owner retries | technical ceiling | As above. |
| `hooks/src/codemode.ts:18` | 100,000 polls (about 10 s of compute) | a script's CPU | a compute-bound script ends `timeout` | technical ceiling | The hooks Worker's `cpu_ms` (30 s): a Durable Object past its CPU limit resets, failing every script in it, so the budget stays below it. |
| `hooks/src/codemode.ts:20` | 32 MiB | a script's VM memory | a script past it fails | technical ceiling | A Worker isolate's 128 MB memory, shared by the VMs one object runs. |
