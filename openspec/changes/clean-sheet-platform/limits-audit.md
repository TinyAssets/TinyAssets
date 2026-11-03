# Limits audit

The founder, 2026-11-02: no tiny limits before he has used the platform. This lists every
bound in `platform/*/src` and in the Workers' configs that refuses or truncates something a
user does, with the value that ships (slice LIM), what it bounds, and one class:

- **tier**: simultaneous runs or storage, from the platform control record (free 5 runs and
  25 GB, paid 10 and 250 GB; ADR-034 decision 8), or scaled from them;
- **owner setting**: the owner's own, off by default or generous;
- **technical ceiling**: kept, with the platform fact that forces it;
- **budget allocation**: a share of the owner-bound guard's budget (below), traded against
  another term by the founder's choice;
- **security**: a credential's or session's life, a sign-in's freshness, one principal's
  share against another;
- **label or page**: a name or id of ours, or a page a read returns, never what is held;
- **convention**: a sanity bound on a number, a URL or a call's deadline, no platform fact
  behind it.

Not listed, since none refuses or truncates what a user does: internal timings and backoffs
(leases, retries, garbage-collection grace, lane passes, wake retries), the SQLite layout
constants (`manifest.ts`), hashes and key sizes, and the HTTP status of each fixed code.

## The ceiling behind most bounds

**The 10 GB**: a SQLite-backed Durable Object stores at most 10 GB, and a string, BLOB or
row at most 2 MB (Cloudflare's Durable Objects limits, read 2026-11-03; the facet probe's
Q1 measured about 10 GiB, `facet-probe.md`). The owner-bound guard
(`owner/guards/owner-bound.test.ts`) holds the worst case of every owner table under half
of it, 5 GB, each row laid out as SQLite lays it at a thinned page's worst fill.

**The placement rule** (ADR-035) is what lets the bounds below be generous: a body of more
than 4,096 bytes (a collection row's, a history entry's, a message's) lives in R2 through the
files plane, its row keeping only a content id, and its bytes count in the tier's storage.
With it, the collection history is counted in bytes, a message has one recipient (the key
holds it), the mailboxes' caps are within the ledger's ceilings rather than reserved out of
them, and a grant's scopes are counted in bytes.

**The worst case that ships is 4,225,851,030.75 bytes, 42.26% of the 10 GB: 774.1 MB under
half** (`owner/guards/limits-audit.test.ts` prints each term; slice D's raises had left
2.1 MB). Without each structural change, the same bounds would cost: bodies in their rows
37.8 GB; history as rows times revisions 60.2 GB; recipients as messages times agents
46.0 GB; scopes at 512 code points of JSON each 6.5 GB, each past the half.

The largest terms: the file manifest 1,208.0 MB (24.16% of the budget), `collection_rows`
563.0 MB, `message_bodies` 486.3 MB, `collection_history` 302.0 MB, `connections`
269.3 MB, `collection_usage` 251.1 MB, `ledger_messages` 204.8 MB, `ledger_receipts`
204.8 MB.

## Summary

- **tier**: 4
- **owner setting**: 12
- **technical ceiling**: 75
- **budget allocation**: 16
- **security**: 13
- **label or page**: 16
- **convention**: 24
- **total**: 160

Flagged for the founder:
- the collections' byte bounds (`maxOwnerBytes` 16 GiB, `maxCollectionBytes` 4 GiB) and the
  ledger's (`maxBodyBytes` 16 GiB and the mailboxes' byte caps) now count bytes the tier's
  storage counts too, since their bodies past 4 KiB are in R2. They bind before the paid
  tier's 250 GB. Dropping them for the tier's figure alone is one change each (the ledger's
  eviction reads `maxBodyBytes`).
- `max_entries` stays 30,000: the files policy's rule (`max_history` at least `max_entries`
  times the larger of `keep_versions` and `keep_recent`) caps it at 600,000 / 20. Counting
  the files' history in bytes, as the collections' now is, would lift it to about 480,000.
- the owner Worker's `limits.subrequests` is Paid's documented most, 10,000,000; live probe
  P1 ran at 1,000,000, so a live check at the most is owed.

## The list

| Where | Value | Bounds | A user hits | Class | Reason |
|---|---|---|---|---|---|
| control record `tiers` (`owner/src/tier.ts`) | free 5, paid 10 | simultaneous runs | a run past them refused `tier_runs` | tier | ADR-034 decision 8, edited in the record with no deploy. |
| `owner/src/policy.ts` `fileLimits` | the tier's storage: free 25 GB, paid 250 GB | the owner's file bytes, live and in history, and placed bodies (R2) | a change adding bytes refused `file_quota`; with no tier numbers kept, `tier_unread` | tier | The control record's figure, kept from every door's binding. The owner's own `max_bytes` (0 to 1 PiB) binds only below it. |
| `owner/src/egress.ts` `EGRESS_LIMITS.openPerRun` | 16 a run (free 80, paid 160) | sandbox model calls streaming at once | a call refused `egress_rate` | tier | Scaled by the tier's runs. Guard term `egress_reservations`, 75.8 MB, costed at `RUNS_CEILING`. |
| `owner/src/egress.ts` `EGRESS_LIMITS.perRunPerMinute` | 120 a run a minute (free 600, paid 1,200) | sandbox calls opened a minute | a call refused `egress_rate` | tier | Scaled by the tier's runs; no guard term moves. |
| `owner/src/tier.ts` `RUNS_CEILING` | 1,000 | the most simultaneous runs a tier record may give | a record past it is malformed | budget allocation | Each live run's row, grant, lease and egress calls are costed at it. Far past any tier. |
| `owner/src/policy.ts` `FILE_DEFAULTS.max_inline_bytes` | 256 MiB (high 384 MiB) | small files kept in the object | a small file refused `file_quota` | technical ceiling | The 10 GB, inside the manifest's term. Every file over 4 KiB is in R2 already. |
| `owner/src/policy.ts` `FILE_DEFAULTS.max_manifest_bytes` | 512 MiB (high 512 MiB) | every manifest row as SQLite stores it | file changes refused once the manifest fills it | technical ceiling | The 10 GB: guard term `file_manifest`, 1,208.0 MB at `WORST_FILL` 2.25, the largest. The manifest is the files' index, read in each change's unit. Placed bodies' contents and chunks are charged in it too. |
| `owner/src/policy.ts` `FILE_DEFAULTS.max_entries` | 30,000 (high 500,000) | live paths | a new path refused `file_quota` | technical ceiling | The policy's own rule against `max_history`'s 600,000 at `keep_recent` 20 (flagged above). |
| `owner/src/policy.ts` `FILE_DEFAULTS.max_file_bytes` | 5 GiB (high 40 GiB) | one file's size | a larger upload refused | owner setting | Its high is `MAX_INTENT_CHUNKS` chunks. |
| `owner/src/policy.ts` `FILE_DEFAULTS.max_history` | 600,000 (high 600,000) | kept history rows | old versions pruned earliest-due first | technical ceiling | The 10 GB, inside `max_manifest_bytes`. |
| `owner/src/policy.ts` `FILE_DEFAULTS.retention_days` | 30 days (high 3,650) | how long kept versions stay | older versions pruned | owner setting | |
| `owner/src/policy.ts` `FILE_DEFAULTS.keep_versions` | 10 (high 1,000) | versions kept for each live path | older versions pruned | owner setting | |
| `owner/src/policy.ts` `FILE_DEFAULTS.keep_recent` | 20 (high 1,000) | recent versions kept for a path | older versions pruned | owner setting | |
| `owner/src/policy.ts` `FILE_DEFAULTS.client_share` | 25% | one outside client's share of every file quota | a client's write refused past it | owner setting | |
| `owner/src/policy.ts` `FILE_DEFAULTS.owner_reserve` | 25% | the room all clients leave the owner | clients' writes refused within it | owner setting | |
| `owner/src/files.ts` `MAX_INTENT_CHUNKS` | 10,000 chunks (40 GiB) | one upload's chunks | a file over 40 GiB cannot be uploaded | technical ceiling | The 10 GB: guard term `file_intents`, 82.1 MB. One open upload's chunk list is one row. |
| `owner/src/files.ts` `MAX_OPEN_INTENTS` | 64 | uploads open at once (placed bodies' among them) | a 65th refused until one commits | technical ceiling | The 10 GB, inside `file_intents`. |
| `owner/src/files.ts` `MAX_HELD_CHUNKS` | 20,000 | chunks all open uploads hold | a new upload refused while they hold it | technical ceiling | The 10 GB, inside `file_intents`. |
| `owner/src/files.ts` `INTENT_TTL_S` | 86,400 s | how long an upload may stay open | a large upload over a slow link must finish within a day | technical ceiling | Its expiry is the sweep of a crashed placement's orphan (ADR-035); a 40 GiB file at 1 MB/s takes 11 hours. |
| `owner/src/files.ts` `TEMP_TTL_S` | 7 days | a temporary directory under `/.ta/tmp` | its files deleted after a week | technical ceiling | Temporary files are charged to the manifest and quotas; the area is for spills, not keeping. |
| `owner/src/files.ts` `WHOLE_BYTES` | 16 MiB | one whole-file read or write through the object | a larger file is read by range, written by upload | technical ceiling | A Worker isolate's 128 MB of memory, which a whole file in one call holds with its copies. |
| `owner/src/files.ts` `MAX_SYNC_OPS` | 1,000 | ops in one sandbox sync commit | the sync agent splits a larger commit | technical ceiling | One synchronous gate unit per commit, which must stay short in the object's request CPU. |
| `owner/src/files.ts` `KEEP_CEILING` | 1,000 | the most `keep_versions` and `keep_recent` take | the setting refused above it | technical ceiling | The 10 GB, inside the manifest's bytes. |
| `owner/src/files.ts` `MAX_CONFLICT_COPIES` | 1,000 | conflict copies of one path in one epoch | the 1,001st conflicting sync of one path refused | convention | Bounds the names a conflict probes, one indexed read each. |
| `owner/src/files.ts` `MAX_LIST` | 1,000 | entries one list call returns | the caller pages | label or page | |
| `owner/src/files.ts` `tempDir` | 239 bytes | a temporary directory's prefix | a longer prefix refused | technical ceiling | One path component, `MAX_NAME_BYTES` 255, with its 16 hex. |
| `owner/src/files.ts` `leaseAcquire` | 128 characters | the sync lease holder's name | a longer name refused | label or page | A name the sandbox slice sets. |
| `owner/src/owner-do.ts` `fileWrite` | 4 MiB | one `fileWrite` call's content | a larger file goes by upload | technical ceiling | Workers RPC's message size and the isolate's 128 MB. |
| `owner/src/owner-do.ts` `fileReadChunks` | 2 chunks (8 MiB) | one chunk read call | the caller reads in more calls | technical ceiling | As above. |
| `owner/src/paths.ts` `MAX_PATH_BYTES` | 4,096 bytes | a path | a longer path refused | technical ceiling | Linux's `PATH_MAX`, which the sandbox's disk holds. |
| `owner/src/paths.ts` `MAX_NAME_BYTES` | 255 bytes | one path component | a longer name refused | technical ceiling | Linux's `NAME_MAX`. |
| `owner/src/manifest.ts` `PLACE_BYTES` | 4,096 bytes | the most of a body kept in its row | nothing: a larger body is placed in R2 | technical ceiling | ADR-035: one b-tree page's in-place room (4,061 bytes in a rowid table); past it every 4,092 bytes is an overflow page read with the row, and R2 is cheaper to keep it. |
| `owner/src/bodies.ts` `READ_PAGE_BYTES` | 8 MiB | bodies one pull, history or mailbox read returns past its first | the caller pages | label or page | Two chunks, one `FILES` call, within the isolate's memory. |
| `owner/src/collections.ts` `LIMITS.maxRow` | 1,048,576 characters | one board or room row's body | a larger row refused | technical ceiling | A placed body is one 4 MiB chunk at four bytes a character; `API_BODY_MAX` carries it as JSON. |
| `owner/src/collections.ts` `LIMITS.maxWriterRows` | 10,000 | rows one writer other than the owner holds in a collection | a visitor's next row refused | security | One visitor's share of a room (S34's rule), so no visitor starves it. |
| `owner/src/collections.ts` `LIMITS.maxWriterBytes` | 1 GiB | bytes one writer other than the owner holds in a collection | a visitor's row refused past it | security | As above. |
| `owner/src/collections.ts` `LIMITS.maxRows` | 100,000 | rows in one collection | a board's next card refused | budget allocation | Within `maxOwnerRows`. |
| `owner/src/collections.ts` `LIMITS.maxCollectionBytes` | 4 GiB | bytes in one collection, wherever placed | rows refused past it | budget allocation | Flagged above: the tier's storage counts the placed bytes too. |
| `owner/src/collections.ts` `LIMITS.maxOwnerRows` | 100,000 | rows in all the owner's collections | rows refused past it | budget allocation | The 10 GB: guard terms `collection_rows` 563.0 MB (each its body up to 4 KiB), `collection_usage` 251.1 MB and `collection_seqs` 83.7 MB. |
| `owner/src/collections.ts` `LIMITS.maxOwnerBytes` | 16 GiB | bytes in all the owner's collections, wherever placed | rows refused past it | budget allocation | Flagged above. No guard term moves: past 4 KiB a body is in R2. |
| `owner/src/collections.ts` `LIMITS.maxRevisions` | 100 | history revisions kept per row | older revisions dropped | owner setting | A retention; the history's bytes bound it in all. |
| `owner/src/collections.ts` `LIMITS.maxHistoryCharge` | 128 MiB | the history's entries as SQLite lays them out | the oldest entries pruned, never refused | budget allocation | The 10 GB: guard term `collection_history`, 302.0 MB at `WORST_FILL`. |
| `owner/src/collections.ts` `LIMITS.maxHistoryBytes` | 16 GiB | the history's bodies, wherever placed | the oldest entries pruned | budget allocation | Their R2 bytes count in the tier's storage; this keeps the history from holding it all. |
| `owner/src/collections.ts` `LIMITS.maxPull` | 100 | rows one pull returns | the caller pages | label or page | |
| `owner/src/collections.ts` `write` | 32 characters | a write's history op | a longer op refused | label or page | |
| `owner/src/text.ts` `name` | 256 characters | a collection, row or writer name | a longer name refused | label or page | |
| `owner/src/ledger.ts` `MAX_BODY` | 1,048,576 characters | one message body | a longer message refused | technical ceiling | As `maxRow`: one placed chunk. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxAgents` | 1,000 | enabled outside agents | the 1,001st registration refused | budget allocation | Within `maxAgentRows`. No guard term moves: a message has one recipient. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxAgentRows` | 1,000 | agent rows, tombstones included | a registration refused past it | technical ceiling | The 10 GB: guard terms `outside_agents`, `ledger_readers`, `ledger_rings`, `ledger_usage`, 55.9 MB. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxMessages` | 50,000 | messages held | the oldest acked message evicted; refused `ledger_full` if every one is unread | budget allocation | The 10 GB: guard terms `ledger_messages` 204.8 MB, `message_bodies` 486.3 MB (each body up to 4 KiB) and `ledger_recipients` 41.9 MB. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxBodyBytes` | 16 GiB | message bodies held, wherever placed | the oldest acked message evicted | budget allocation | Flagged above. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxOwnerUnacked` | 10,000 | messages unread by the owner | a post to the owner refused past it | budget allocation | Within `maxMessages` (`checkLimits`). |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxOwnerUnackedBytes` | 4 GiB | bytes unread by the owner | a post refused past it | budget allocation | Within `maxBodyBytes`. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxAgentUnacked` | 1,000 | messages unread in one agent's mailbox | a post to that agent refused | budget allocation | Within `maxMessages`. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxAgentUnackedBytes` | 512 MiB | bytes unread in one agent's mailbox | a post refused past it | budget allocation | Within `maxBodyBytes`. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxSenderUnacked` | 5,000 | one sender's messages unread by the owner | a sender's post refused past it | security | One agent's share of the owner's mailbox, so no agent fills it. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxSenderUnackedBytes` | 2 GiB | one sender's unread bytes | a sender's post refused past it | security | As above. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxReceipts` | 50,000 | read and reply receipts kept | the oldest let go | technical ceiling | The 10 GB: guard term `ledger_receipts`, 204.8 MB. A history. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxSettledWakes` | 1,000 | settled wakes kept | the oldest let go | technical ceiling | The 10 GB: guard term `ledger_wakes`, 8.2 MB. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxRead` | 100 | messages one read returns | the caller pages | label or page | |
| `owner/src/ledger.ts` `WAKE_DEFAULTS` | 60 wakes an hour, 60 s batch | an agent's doorbells | wakes past it wait for the hour | owner setting | Per agent (`policyWakeLimits`), 1 to 60 an hour, default the most. |
| `owner/src/ledger.ts` `WAKE_SETTINGS.maxWakesPerHour` | 60 an hour at most | the wake setting's high | a higher setting refused | technical ceiling | The 10 GB: guard term `ledger_rings`, 50.2 MB, an hour's wakes for each agent row. |
| `owner/src/ledger.ts` `register` | 80 characters | an agent's display name | a longer name refused | label or page | |
| `owner/src/registrations.ts` `CARD_LIMITS` | 200 pending, 10 a client, 4,096-byte card | registration cards waiting | a client's 11th card refused | technical ceiling | The 10 GB: guard term `agent_cards`, 1.1 MB. |
| `owner/src/registrations.ts` `CARD_TTL_S` | 7 days | how long a filed card waits | the client files again | convention | An owner away for a week misses no card. |
| `owner/src/policy.ts` `POLICY_LIMITS.maxDocument` | 65,536 characters | the owner policy document | a larger policy refused | technical ceiling | The 10 GB, one row read on every door. |
| `owner/src/policy.ts` `POLICY_LIMITS.maxChange` | 16,000 characters | one agent's proposal | a larger proposal refused | technical ceiling | The 10 GB: guard term `policy_proposals`, 26.8 MB. |
| `owner/src/policy.ts` `POLICY_LIMITS.proposalTtlS` | 90 days | a pending policy proposal | it expires unanswered | convention | Pending proposals are bounded by count. |
| `owner/src/policy.ts` `POLICY_LIMITS.pendingProposals` | 200 (50 a client) | proposals waiting for the owner | a proposal refused past it | technical ceiling | The 10 GB, inside `policy_proposals`. |
| `owner/src/policy.ts` `POLICY_LIMITS.appliedProposals` | 200 | applied proposals kept | the oldest let go | technical ceiling | As above. A history. |
| `owner/src/policy.ts` `POLICY_LIMITS.receiptsPerBucket` | 5,000 a bucket | policy receipts kept | the oldest let go | technical ceiling | The 10 GB: guard term `policy_receipts`, 61.4 MB. |
| `owner/src/policy.ts` `POLICY_LIMITS.receiptsListed` | 50 | policy receipts one read lists | the newest 50 | label or page | |
| `owner/src/policy.ts` `MODEL_SOURCES_MAX` | 64 | sources a `models.sources` table names | a larger table refused | convention | Inside `maxDocument`. |
| `owner/src/policy.ts` `MODEL_DEFAULTS.turn_requests` | none (owner may set 1 to 1,000,000) | model calls in one sandbox run | only the owner's own cap refuses `run_requests` | owner setting | ADR-034 decision 7: the owner's own limits are off by default. |
| `owner/src/policy.ts` `MODEL_DEFAULTS.turn_tokens` | none (owner may set 1 to 1e9) | tokens in one sandbox run | only the owner's own cap | owner setting | As above. |
| `owner/src/policy.ts` `MODEL_DEFAULTS.turn_cost` | none (owner may set to $1,000,000) | cost of one sandbox run | only the owner's own cap | owner setting | As above. |
| `owner/src/policy.ts` `MODEL_DEFAULTS.daily_cost`, `monthly_cost` | none | the owner's and each source's day and month | only the owner's own ceiling | owner setting | |
| `owner/src/policy.ts` `proposalId` | 128 characters | a card's or proposal's id | a longer id refused | label or page | Minted by the object. |
| `owner/src/grants.ts` `GRANT_LIMITS.maxFreeClients` | 1,000 | clients recorded without an owner grant | the oldest free one forgotten (never refused) | technical ceiling | The 10 GB: guard terms `outside_clients`, `proposal_shares`, 70.3 MB. |
| `owner/src/grants.ts` `GRANT_LIMITS.maxOwnerClients` | 1,000 | clients the owner set a grant for | the 1,001st grant refused | technical ceiling | As above. |
| `owner/src/grants.ts` `GRANT_LIMITS.maxScopes` | 256 | scope triples in one grant | a wider grant refused | convention | A grant's cost is its bytes, below. |
| `owner/src/grants.ts` `GRANT_LIMITS.maxScopesBytes` | 65,536 bytes | a grant's scopes as JSON | a larger grant refused | technical ceiling | The 10 GB, inside `outside_clients`; the schema's own check on the column. |
| `owner/src/grants.ts` `scopes` | 512 characters | a scope's universe, agent or capability | a longer field refused | label or page | |
| `owner/src/grants.ts` `setClient` | 80 characters | a client's name | a longer name refused | label or page | |
| `owner/src/sessions.ts` `SESSION_LIMITS.maxSessions` | 64 | signed-in devices at once | the oldest session closed | security | One owner's phones, laptops and browsers; a session is a credential. |
| `owner/src/sessions.ts` `SESSION_LIMITS` life | 8 hours | the owner's protected session | the owner signs in again for a widening | security | The cookie's `Max-Age`. |
| `owner/src/sessions.ts` `SESSION_LIMITS` freshness | 600 s | how fresh a sign-in must be to open a session | the owner signs in again | security | S30. |
| `owner/src/connections.ts` `CONNECTION_LIMITS.maxConnections` | 50 | live model connections | the 51st refused | technical ceiling | The 10 GB: guard terms `connections` 269.3 MB, `connection_secrets` 26.8 MB. A connection's models list is read in every egress admission unit, so it stays in its row (ADR-035 decision 6). |
| `owner/src/connections.ts` `CONNECTION_LIMITS.maxOpenFlows` | 10 | OAuth connect flows open at once | an 11th refused | security | Each holds a PKCE verifier; flows expire in 10 minutes. |
| `owner/src/connections.ts` `CONNECTION_LIMITS.flowTtlS` | 600 s | an OAuth flow's life | the owner starts the connect again | security | Short-lived authorization state (RFC 6749 practice). |
| `owner/src/connections.ts` `CONNECTION_LIMITS.maxModels` | 1,024 | models one connection lists | models past it refused at commit | technical ceiling | Inside `connections`; the largest built-in catalog has 386. |
| `owner/src/connections.ts` `MODELS_JSON_MAX` | 640 KiB | a connection's models, as JSON | a larger list refused at commit | technical ceiling | As above. |
| `owner/src/connections.ts` `EGRESS_JSON_MAX` | 16 KiB | a connection's egress scope | many custom paths refused at commit | technical ceiling | As above. |
| `owner/src/connections.ts` `BLOB_MAX` | 64 KiB | a connection's sealed blob | a credential past it refused | technical ceiling | Guard term `connection_secrets`; the gateway's own bound too. |
| `owner/src/connections.ts` model numbers | $1,000,000 a million tokens; 100,000,000 tokens; 16 tiers | a model record's prices, windows and tiers | a record past them refused | convention | Sanity bounds far past every catalog's. |
| `owner/src/connections.ts` model id, name | 200 characters | a model's id and display name | the model refused at commit | label or page | |
| `owner/src/connections.ts` redirect | 2,048 characters | an OAuth redirect URI | a longer URI refused | convention | |
| `owner/src/schema.ts` connection name | 1 to 128 characters | a connection's display name | a longer name refused | label or page | |
| `owner/src/usage.ts` `USAGE_LIMITS.minOutput` | 1,024 tokens (2,048 reasoning) | the least output a reservation is worth | refused when an owner's ceiling leaves less | technical ceiling | Only when an owner's own ceiling binds; provider thinking budgets start at 1,024. |
| `owner/src/usage.ts` `USAGE_LIMITS.receiptsKept` | 5,000 | model-call receipts kept | the oldest let go | technical ceiling | The 10 GB: guard term `model_reservations`, 24.6 MB. |
| `owner/src/schema.ts` `CALLS_OPEN` | 1,000 | model reservations open at once | refused `model_calls_full` | technical ceiling | Inside `model_reservations`; far above any tier's runs. |
| `owner/src/schema.ts` `SPEND_KEPT` | 4,300 | day and month spend counters | a 101st source's first call in a period refused | technical ceiling | The 10 GB: guard term `model_spend`, 3.6 MB. |
| `owner/src/egress.ts` `EGRESS_LIMITS.refusalsPerMinute` | 60 a minute | refusals receipted a minute | later refusals not receipted | convention | Keeps a refused flood from writing rows. |
| `owner/src/egress.ts` `EGRESS_LIMITS.receiptsKept`, `refusalsKept` | 2,000 admitted, 500 refused | egress receipts kept | the oldest let go | technical ceiling | Inside `egress_reservations`. |
| `owner/src/egress.ts` `MIN_OUTPUT` | 1,024 tokens | a sandbox call's least output | refused when a cap leaves less | technical ceiling | As `usage.ts`'s. |
| `owner/src/egress.ts` `EGRESS_PATH_MAX` | 2,048 characters | a sandbox request's path and query | a longer path refused | convention | |
| `owner/src/network.ts` host | 253 characters | an egress destination's host | a longer host refused | technical ceiling | DNS's own bound (RFC 1035). |
| `owner/src/network.ts` `EGRESS_DEADLINE_S` | 3,600 s | one streamed sandbox model call | a stream past an hour is cut `deadline`, settled `unknown` | convention | A subrequest has no time limit while its client stays connected (Workers limits); `LOST_AFTER_S` and the gateway's `FORWARD_DEADLINE_MS` follow. |
| `owner/src/network.ts` `EGRESS_BODY_MAX` | 16 MiB | one sandbox model request's body | refused `egress_too_large` | technical ceiling | The isolate's 128 MB, which holds the body and its decoded text while the gateway meters it. Providers take more (Anthropic's Messages API, 128 MB), past what an isolate holds. |
| `doors/src/routing-mac.ts` `LEASE_LIVED` | none of their own | the egress and sync tokens' life | a token lives while its lease's heartbeat renews it | technical ceiling | The lease's life (`LEASE_TTL_S` without a renewal); no run meets a fixed deadline. |
| `owner/src/codemode.ts` `CODEMODE_TIMEOUT_MS` | 300 s | one code mode script's wall time | a longer script ends `timeout` | convention | A script that calls slow tools. |
| `owner/src/cron.ts` `CRON_MIN_GAP_MIN` | 1 minute | the shortest gap between a schedule's runs | nothing below cron's own minute | technical ceiling | Cron's resolution; the tier's runs bound what a schedule starts. |
| `owner/src/cron.ts` `CRON_MAX_ITEMS` | 60 | values in one cron field's list | a longer list refused | technical ceiling | A minute field's every value. |
| `owner/src/cron.ts` `CRON_MAX_CHARS` | 256 characters | a cron expression | a longer expression refused | convention | Room for every list a field takes. |
| `owner/src/cron.ts` `CRON_HORIZON_DAYS` | 8 years | how far ahead an occurrence is searched | an expression with none in 8 years refused | convention | A leap-day schedule recurs inside it. |
| `owner/src/notices.ts` `NOTICES_KEPT` | 1,000 | owner notices kept | the oldest let go | technical ceiling | The 10 GB: guard term `owner_notices`, 5.6 MB. |
| `owner/src/notices.ts` `NOTICE_DETAIL` | 1,024 characters | a notice's detail | a longer detail cut | convention | The full error stays in its source. |
| `owner/src/run-rows.ts` `RUNS_KEPT` | 2,000 | settled runs kept as receipts | an older run's status is gone (its transcript stays) | technical ceiling | The 10 GB: guard term `runs`, 85.0 MB with the live and waiting runs. |
| `owner/src/run-rows.ts` `RESULT_BYTES` | 16 KiB | a run's result on its receipt | a longer result kept cut, `result_cut` | technical ceiling | Inside `runs`. The whole result is in the engine's transcript, in the owner's files. |
| `owner/src/run-rows.ts` `ERROR_BYTES` | 1 KiB | a run's first error on its receipt | a longer error kept cut | technical ceiling | As above. |
| `owner/src/run-rows.ts` `CONVERSATIONS_KEPT` | 10,000 | conversations kept | the least recently run idle one forgets its resume id | technical ceiling | The 10 GB: guard term `conversations`, 30.7 MB. Nothing is refused. |
| `owner/src/run-rows.ts` `SCHEDULES_KEPT` | 1,000 | the `runs` lane's schedules | a new schedule refused `run_schedules_full` | technical ceiling | The 10 GB: guard term `run_schedules`, 26.1 MB. |
| `owner/src/run-rows.ts` `INPUT_BYTES` | 16 KiB | a schedule's stored input | a longer scheduled input refused | technical ceiling | Inside `run_schedules`. |
| `owner/src/runs.ts` one live run a conversation | one | runs in one conversation at once | a second message waits its turn, never refused | technical ceiling | One engine session resumes one invocation at a time. |
| `owner/src/run-rows.ts` `QUEUE_ROWS` | 256 | inputs waiting for their conversations | one more refused `run_inputs_full` until a run settles | budget allocation | The 10 GB: each waits as a `runs` row beside its input. |
| `owner/src/run-rows.ts` `QUEUE_BYTES` | 6 MiB | the waiting inputs' bytes | as above | budget allocation | Guard term `run_inputs`, 9.8 MB. |
| `owner/src/run-rows.ts` `ENGINE_SIGNINS` | 64 | engines whose sign-in is kept | a report past it fails loudly | technical ceiling | Guard term `engine_signins`, 0.4 MB. |
| `owner/src/run-rows.ts` `SIGNIN_MODELS_CHARS` | 2,048 characters | an engine's reported models | a longer report refused | technical ceiling | Inside `engine_signins`. |
| `owner/src/run-rows.ts` `SIGNIN_LIST_CHARS` | 256 characters | a client's sandbox's signed-in engines | a longer report refused | technical ceiling | Inside `sandbox_leases`, 3.1 MB. |
| `owner/src/socket-shape.ts` `SOCKET_TICKETS` | 1,024 | socket tickets outstanding | refused `socket_tickets_full` until one is used or expires | technical ceiling | Guard term `socket_tickets`, 3.1 MB. |
| `owner/src/socket-shape.ts` ticket life | 30 s | a socket ticket's life | the app asks again | security | A single-use credential in a URL. |
| `owner/src/relay.ts` sockets | 1,024 | the owner app's sockets open at once | another refused `sockets_full` | technical ceiling | The isolate's 128 MB (memory guard). |
| `owner/src/relay.ts` tails | 4 Mi UTF-16 units | the live runs' tails, together | a late joiner sees fewer old frames | technical ceiling | The isolate's 128 MB. Frames are never committed. |
| `owner/src/relay.ts` one frame kept | 256 Ki units | one frame in a tail | relayed live, not kept | technical ceiling | As above. |
| `owner/src/relay.ts` app frame | 1,024 units | a frame the app sends | the socket closed `frame_refused` | convention | The app sends only pings. |
| `owner/src/run-events.ts` `EVENTS_BODY_MAX` | 64 KiB | a runner's events post | refused `events_too_large`; the runner posts more often | technical ceiling | The isolate's 128 MB (memory guard). |
| `owner/src/run-events.ts` `EVENTS_IN_FLIGHT` | 8 | events posts held at once | `events_busy` (503), posted again | technical ceiling | As above. |
| `owner/src/run-events.ts` `EVENT_FRAMES_MAX` | 64 | frames in one post | the runner posts more often | technical ceiling | As above. |
| `owner/src/run-events.ts` `FRAME_UNITS_MAX` | 32 Ki units | one frame's JSON | the runner cuts a longer CLI event, marked | technical ceiling | As above. |
| `owner/src/owner-do.ts` `wait_ms` | 60 s | how long a run door waits for its run | the door answers the run as it stands | convention | A call's own bound; the run has no deadline. |
| `owner/wrangler.jsonc` `limits.subrequests` | 10,000,000 | the owner object's calls an invocation | the platform's error past it | technical ceiling | Workers Paid's most (Workers limits: "10,000 (up to 10M)"). Flagged: P1 probed 1,000,000. |
| `doors/src/api-keys.ts` `MAX_KEYS_PER_OWNER` | 200 | API keys an owner holds | a 201st refused | convention | A key per device or agent; each key's grant is inside `maxOwnerClients`. |
| `doors/src/api-keys.ts` key name | 80 characters | an API key's name | a longer name refused | label or page | |
| `doors/src/config.ts` `ACCESS_TOKEN_TTL_S` | 3,600 s | an MCP or app access token's life | the client refreshes | security | |
| `doors/src/config.ts` `REFRESH_TOKEN_TTL_S` | 30 days | a refresh token's life | an unused client signs in again | security | OAuth practice. |
| `doors/src/config.ts` consent freshness | 600 s | how fresh a sign-in is for consent | the owner signs in again | security | `prompt=login`. |
| `doors/src/config.ts` `AUTHORIZE_BODY_MAX` | 4 KiB | the consent form's body | refused `413` | convention | |
| `doors/src/config.ts` `APP_BODY_MAX` | 64 KiB | an owner-surface request body | refused `413` | convention | |
| `doors/src/config.ts` `API_BODY_MAX` | 8 MiB | an `/api/tools/*` request body | refused `413` | technical ceiling | Carries a `maxRow` body as JSON (up to 6 bytes a character escaped); file bytes go by their own doors. |
| `doors/src/session.ts` token | 32 to 512 characters | a session token | another token ignored | label or page | Minted by the object. |
| `gateway/src/forward.ts` `FORWARD_DEADLINE_MS` | 1 hour | one forwarded request | as `EGRESS_DEADLINE_S` | convention | Held equal to it by a test. |
| `gateway/src/forward.ts` `BODY_MAX` | 16 MiB | a metered call's body | as `EGRESS_BODY_MAX` | technical ceiling | As `EGRESS_BODY_MAX`. |
| `gateway/src/forward.ts` `VERIFY_DEADLINE_MS` | 15 s | the setup check's provider call | the check fails; the owner retries | convention | |
| `gateway/src/oauth.ts` token answer | 15 s, 64 KiB | a provider's token endpoint answer | the connect fails | convention | It must fit the sealed blob it becomes. |
| `gateway/src/meter.ts` `LEARN_TRIES` | 32 | models the verify-time metering tries | a source whose first 32 models cannot be metered is refused `unmetered` | convention | Within the setup check's 15 s. |
| `gateway/src/credential.ts` limits | 16 KiB secret; 16 endpoints of 64 paths; 64 declared models; 2,048-character URLs | a pasted credential and its declared endpoints | a larger record refused at seal | technical ceiling | Inside the 64 KiB sealed blob (`keyring.ts` `BLOB_MAX`). |
| `gateway/src/model-record.ts` numbers | as the owner's | a declared model record | refused past them | convention | |
| `hooks/src/codemode.ts` `CODEMODE_INTERRUPT_BUDGET` | 1,000,000 polls (about 100 s) | a script's CPU | a compute-bound script ends `timeout` | technical ceiling | Within the hooks Worker's `cpu_ms`. |
| `hooks/src/codemode.ts` `CODEMODE_MEMORY_BYTES` | 32 MiB | a script's VM memory | a script past it fails | technical ceiling | The isolate's 128 MB, shared by its VMs. |
| `hooks/wrangler.jsonc` `limits.cpu_ms` | 300,000 ms | the hooks Worker's CPU an invocation | a script past it resets its sandbox | technical ceiling | Workers Paid's most, 5 minutes (Workers limits). |
