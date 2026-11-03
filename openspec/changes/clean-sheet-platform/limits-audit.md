# Limits audit

The founder, 2026-11-02: no tiny limits before he has used the platform. This lists every
bound in `platform/*/src` and in the Workers' configs that refuses or truncates something a
user does, with the value that ships (slice LIM), what it bounds, and one of the founder's
four classes:

- **tier**: simultaneous runs or storage, from the platform control record (free 5 runs and
  25 GB, paid 10 and 250 GB; ADR-034 decision 8), or scaled from them;
- **owner setting**: the owner's own, off by default;
- **vendor limit**: a provider's documented limit, cited;
- **technical ceiling**: kept, with the platform fact that forces it. A share of the
  owner-bound guard's half of the 10 GB is one: the fact is the object's 10 GB, and the
  share is named by the guard's term.

Not listed, since none refuses or truncates what a user does:
- internal timings and backoffs: leases, retries, garbage-collection grace, lane passes, wake retries, a placement upload's 10-minute expiry, and the deadlines on the gateway's own calls to a provider (the setup check, a token endpoint);
- pages a read returns, which the caller pages past: a pull, a mailbox or history read, a file list, a receipts list, a chunk read, a run door's wait;
- the SQLite layout constants (`manifest.ts`), hashes and key sizes, and the ids we mint;
- the owner app's own protocol frames, and the HTTP status of each fixed code.

## The ceiling behind most bounds

**The 10 GB**: a SQLite-backed Durable Object stores at most 10 GB, and a string, BLOB or
row at most 2 MB (Cloudflare's Durable Objects limits, read 2026-11-03; the facet probe's
Q1 measured about 10 GiB, `facet-probe.md`). The owner-bound guard
(`owner/guards/owner-bound.test.ts`) holds the worst case of every owner table under half
of it, 5 GB. Each row is laid out as SQLite lays it at a thinned page's worst fill, every
index entry with it, each name at the widest its validator allows. A test checks that
SQLite makes no index the guard does not count (`owner/test/indexes.test.ts`).

**The placement rule** (ADR-035) is what lets the bounds below be generous: a body of more
than 2,048 bytes (a collection row's, a history entry's, a message's) lives in R2 through the
files plane, its row keeping only a content id. Its bytes count in the tier's storage,
charged to its writer's files scope. With it, the collection rows and history are counted
in the bytes they take, a message has one recipient (the key holds it), the mailboxes' caps
are within the ledger's ceilings rather than reserved out of them, and a grant's scopes are
counted in bytes.

**The worst case that ships is 4,019,734,080.75 bytes, 40.20% of the 10 GB: 980.3 MB under
half** (`owner/guards/limits-audit.test.ts` prints each term; slice D's raises had left
2.1 MB). Without each structural change, the same bounds would cost: bodies in their rows
843.6 GB; history as rows times revisions 111.1 GB; recipients as messages times agents
45.8 GB; scopes at 512 code points of JSON each 6.3 GB, each past the half.

The largest terms: the file manifest 1,208.0 MB (24.16% of the budget), `collection_rows`
604.0 MB, `ledger_messages` 322.5 MB, `ledger_receipts` 309.6 MB, `collection_history`
302.0 MB, `connections` 269.3 MB, `message_bodies` 219.7 MB, `ledger_recipients` 107.9 MB.

## Summary

- **tier**: 3
- **owner setting**: 12
- **vendor limit**: 4
- **technical ceiling**: 105
- **total**: 124

Removed by this slice, since only the tier's storage bounds what they bounded, or no
platform fact stood behind them:
- the collections' byte caps (`maxOwnerBytes`, `maxCollectionBytes`, `maxWriterBytes`,
  `maxHistoryBytes`) and their writer and row counts (`maxWriterRows`, `maxRows`,
  `maxOwnerRows`), now one charge of the rows as SQLite lays them out;
- the ledger's byte caps (`maxBodyBytes`, `maxOwnerUnackedBytes`, `maxAgentUnackedBytes`,
  `maxSenderUnackedBytes`);
- a row's history revisions (`maxRevisions`), now bounded only by the history's charge;
- the egress per-minute rates (`perRunPerMinute`, `refusalsPerMinute`): every refusal is
  receipted, the newest 500 kept;
- a grant's scope count (`maxScopes`), now bounded only by its bytes;
- conflict copies of one path (`MAX_CONFLICT_COPIES`), now within the entries quota;
- the setup check's models tried (`LEARN_TRIES`): each try is local.

Flagged for the founder:
- the files policy's retention defaults are not off: `retention_days` 30, `keep_versions`
  10 and `keep_recent` 20 let old versions go before the bounds need the room. Off would
  keep history until `max_history` or the tier's storage prunes the earliest-due first, as
  the collections' history now is. `keep_recent` is already the most the policy's rule
  allows at `max_entries` 30,000.
- `client_share` 25% and `owner_reserve` 25% are on by default. They split the owner's
  files, now also the placed bodies and the collections' rows, between the owner and
  outside writers; off, one client could fill the owner's space.
- `max_entries` stays 30,000: the files policy's rule (`max_history` at least `max_entries`
  times the larger of `keep_versions` and `keep_recent`) caps it at 600,000 / 20. Counting
  the files' history in bytes, as the collections' now is, would lift it to about 480,000.

## The list

| Where | Value | Bounds | A user hits | Class | Reason |
|---|---|---|---|---|---|
| control record `tiers` (`owner/src/tier.ts`) | free 5, paid 10 | simultaneous runs | a run past them refused `tier_runs` | tier | ADR-034 decision 8, edited in the record with no deploy. |
| `owner/src/policy.ts` `fileLimits` | the tier's storage: free 25 GB, paid 250 GB | the owner's file bytes, live and in history, and the placed bodies of collections and mail | placed history and acked mail let go first, then refused `file_quota`; with no tier numbers kept, `tier_unread` | tier | The control record's figure, kept from every door's binding. |
| `owner/src/egress.ts` `EGRESS_LIMITS.openPerRun` | 16 a run (free 80, paid 160) | sandbox model calls streaming at once | a call refused `egress_rate` | tier | Scaled by the tier's runs. Guard term `egress_reservations`, 89.4 MB, costed at `RUNS_CEILING`. |
| `owner/src/policy.ts` files `max_bytes` | none (the owner may set 0 to 1 PiB) | the owner's own file quota | only below the tier's storage | owner setting | Off by default: the tier's figure binds. |
| `owner/src/policy.ts` `FILE_DEFAULTS.max_file_bytes` | 40 GiB, its most | one file's size | only the owner's own lower cap | owner setting | Off by default: the most one upload holds (`MAX_INTENT_CHUNKS`). |
| `owner/src/policy.ts` `FILE_DEFAULTS.retention_days` | 30 days (high 3,650) | how long kept versions stay | older versions pruned | owner setting | Flagged above: on by default. |
| `owner/src/policy.ts` `FILE_DEFAULTS.keep_versions` | 10 (high 1,000) | versions kept for each live path | older versions pruned | owner setting | Flagged above. |
| `owner/src/policy.ts` `FILE_DEFAULTS.keep_recent` | 20 (high 1,000) | recent versions kept for a path | older versions pruned | owner setting | Flagged above; the policy's rule against `max_history`. |
| `owner/src/policy.ts` `FILE_DEFAULTS.client_share` | 25% | one outside writer's share of every file quota and of the collections' rows | a client's or visitor's write refused past it | owner setting | Flagged above. A visiting owner writes in a scope of its own. |
| `owner/src/policy.ts` `FILE_DEFAULTS.owner_reserve` | 25% | the room all outside writers leave the owner | their writes refused within it | owner setting | Flagged above. |
| `owner/src/policy.ts` `MODEL_DEFAULTS.turn_requests` | none (owner may set 1 to 1,000,000) | model calls in one sandbox run | only the owner's own cap refuses `run_requests` | owner setting | ADR-034 decision 7: the owner's own limits are off by default. |
| `owner/src/policy.ts` `MODEL_DEFAULTS.turn_tokens` | none (owner may set 1 to 1e9) | tokens in one sandbox run | only the owner's own cap | owner setting | As above. |
| `owner/src/policy.ts` `MODEL_DEFAULTS.turn_cost` | none (owner may set to $1,000,000) | cost of one sandbox run | only the owner's own cap | owner setting | As above. |
| `owner/src/policy.ts` `MODEL_DEFAULTS.daily_cost`, `monthly_cost` | none | the owner's and each source's day and month | only the owner's own ceiling | owner setting | As above. |
| `owner/src/ledger.ts` `WAKE_DEFAULTS` | 60 wakes an hour, 60 s batch | an agent's doorbells | wakes past it wait for the hour | owner setting | Off by default: the most (`WAKE_SETTINGS`); per agent (`policyWakeLimits`), 1 to 60 an hour. |
| `owner/src/network.ts` `EGRESS_DEADLINE_S` | 86,400 s | one streamed sandbox model call | a stream past a day is cut `deadline`, settled `unknown` | vendor limit | An E2B Pro sandbox lives at most 24 hours (`engines-replan.md` §3); a subrequest has no time limit while its client stays connected (Workers limits). `LOST_AFTER_S` follows. |
| `gateway/src/forward.ts` `FORWARD_DEADLINE_MS` | 24 hours | one forwarded request | as `EGRESS_DEADLINE_S` | vendor limit | Held equal to it by a test. |
| `owner/src/codemode.ts` `CODEMODE_TIMEOUT_MS` | 300 s | one code mode script's wall time | a longer script ends `timeout` | vendor limit | The hooks Worker's `cpu_ms`, Workers Paid's most, 5 minutes (Workers limits). |
| `hooks/wrangler.jsonc` `limits.cpu_ms` | 300,000 ms | the hooks Worker's CPU an invocation | a script past it resets its sandbox | vendor limit | Workers Paid's most, 5 minutes (Workers limits). |
| `owner/wrangler.jsonc` `limits.subrequests` | 10,000,000 | the owner object's calls an invocation | the platform's error past it | technical ceiling | Workers Paid's most (Workers limits: "10,000 (up to 10M)"). P1 probed 1,000,000; a live check at the most is owed. |
| `owner/src/tier.ts` `RUNS_CEILING` | 1,000 | the most simultaneous runs a tier record may give | a record past it is malformed | technical ceiling | The 10 GB: each live run's row, grant, lease and egress calls are costed at it. Far past any tier. |
| `owner/src/policy.ts` `FILE_DEFAULTS.max_inline_bytes` | 256 MiB (high 384 MiB) | small files kept in the object | a small file refused `file_quota` | technical ceiling | The 10 GB, inside the manifest's term. Every file over 4 KiB is in R2 already. |
| `owner/src/policy.ts` `FILE_DEFAULTS.max_manifest_bytes` | 512 MiB (high 512 MiB) | every manifest row as SQLite stores it | file changes refused once the manifest fills it | technical ceiling | The 10 GB: guard term `file_manifest`, 1,208.0 MB at `WORST_FILL` 2.25, the largest. Placed bodies' contents and chunks are charged in it too. |
| `owner/src/policy.ts` `FILE_DEFAULTS.max_entries` | 30,000 (high 500,000) | live paths | a new path refused `file_quota` | technical ceiling | The policy's own rule against `max_history`'s 600,000 at `keep_recent` 20 (flagged above). |
| `owner/src/policy.ts` `FILE_DEFAULTS.max_history` | 600,000 (high 600,000) | kept history rows | old versions pruned earliest-due first | technical ceiling | The 10 GB, inside `max_manifest_bytes`. |
| `owner/src/files.ts` `MAX_INTENT_CHUNKS` | 10,000 chunks (40 GiB) | one upload's chunks | a file over 40 GiB cannot be uploaded | technical ceiling | The 10 GB: guard term `file_intents`, 82.2 MB. One open upload's chunk list is one row. |
| `owner/src/files.ts` `MAX_OPEN_INTENTS` | 64 | uploads open at once (placed bodies' among them) | a 65th refused until one commits | technical ceiling | The 10 GB, inside `file_intents`. |
| `owner/src/files.ts` `MAX_HELD_CHUNKS` | 20,000 | chunks all open uploads hold | a new upload refused while they hold it | technical ceiling | The 10 GB, inside `file_intents`. |
| `owner/src/files.ts` `INTENT_TTL_S` | 86,400 s | how long a file upload may stay open | a large upload over a slow link must finish within a day | technical ceiling | An open upload holds its chunks in `file_intents` and the quota until it ends; a 40 GiB file at 1 MB/s takes 11 hours. |
| `owner/src/files.ts` `TEMP_TTL_S` | 7 days | a temporary directory under `/.ta/tmp` | its files deleted after a week unused | technical ceiling | Temporary files are charged to the manifest's share of the 10 GB; the area is for spills, not keeping. |
| `owner/src/files.ts` `WHOLE_BYTES` | 16 MiB | one whole-file read or write through the object | a larger file is read by range, written by upload | technical ceiling | A Worker isolate's 128 MB of memory, which a whole file in one call holds with its copies. |
| `owner/src/files.ts` `MAX_SYNC_OPS` | 1,000 | ops in one sandbox sync commit | the sync agent splits a larger commit | technical ceiling | One synchronous gate unit per commit, which must stay short in the object's request CPU. |
| `owner/src/files.ts` `KEEP_CEILING` | 1,000 | the most `keep_versions` and `keep_recent` take | the setting refused above it | technical ceiling | The 10 GB, inside the manifest's bytes. |
| `owner/src/files.ts` `tempDir` | 239 bytes | a temporary directory's prefix | a longer prefix refused | technical ceiling | One path component, `MAX_NAME_BYTES` 255, with its 16 hex. |
| `owner/src/owner-do.ts` `fileWrite` | 4 MiB | one `fileWrite` call's content | a larger file goes by upload | technical ceiling | One chunk; Workers RPC's message and the isolate's 128 MB. |
| `owner/src/paths.ts` `MAX_PATH_BYTES` | 4,096 bytes | a path | a longer path refused | technical ceiling | Linux's `PATH_MAX`, which the sandbox's disk holds. |
| `owner/src/paths.ts` `MAX_NAME_BYTES` | 255 bytes | one path component | a longer name refused | technical ceiling | Linux's `NAME_MAX`. |
| `owner/src/manifest.ts` `PLACE_BYTES` | 2,048 bytes | the most of a body kept in its row | nothing: a larger body is placed in R2 | technical ceiling | ADR-035: a rowid table keeps a record of 4,061 bytes in place; half is the body's, half the row's own names and ids, so a row keeps in its page. |
| `owner/src/bodies.ts` `BODY_BYTES_MAX` (`LIMITS.maxRowBytes`, `MAX_BODY_BYTES`) | 16 MiB | one row's, history entry's or message's body | a larger body refused | technical ceiling | One call through the object whole, as `WHOLE_BYTES`: the isolate's 128 MB. Placed in four 4 MiB chunks. Live memory probe owed to slice L. |
| `owner/src/collections.ts` `LIMITS.maxRowsCharge` | 256 MiB laid out | every collection row, its index entries and its collection's seq row, as SQLite lays them out | a write refused: the owner's collections are full | technical ceiling | The 10 GB: guard term `collection_rows`, 604.0 MB at `WORST_FILL`. About 262,000 rows at the narrowest names; an outside writer's share by `client_share`. |
| `owner/src/collections.ts` `LIMITS.maxHistoryCharge` | 128 MiB laid out | the history's entries, erased keys' last entries among them | the oldest entries pruned, never refused | technical ceiling | The 10 GB: guard term `collection_history`, 302.0 MB at `WORST_FILL`. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxAgents` | 1,000 | enabled outside agents | the 1,001st registration refused | technical ceiling | The 10 GB, within `maxAgentRows`. A message has one recipient, so no term moves with it. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxAgentRows` | 1,000 | agent rows, tombstones included | a registration refused past it | technical ceiling | The 10 GB: guard terms `outside_agents`, `ledger_readers`, `ledger_rings`, `ledger_usage`. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxMessages` | 50,000 | messages held | the oldest acked message evicted; refused `ledger_full` if every one is unread | technical ceiling | The 10 GB: guard terms `ledger_messages` 322.5 MB, `message_bodies` 219.7 MB (each body up to 2 KiB) and `ledger_recipients` 107.9 MB. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxOwnerUnacked` | 10,000 | messages unread by the owner | a post to the owner refused past it | technical ceiling | Within `maxMessages` (`checkLimits`), so no mailbox holds the whole ledger. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxAgentUnacked` | 1,000 | messages unread in one agent's mailbox | a post to that agent refused | technical ceiling | As above. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxSenderUnacked` | 5,000 | one sender's messages unread by the owner | a sender's post refused past it | technical ceiling | Within `maxOwnerUnacked`, so no agent fills the owner's mailbox. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxReceipts` | 50,000 | read and reply receipts kept | the oldest let go | technical ceiling | The 10 GB: guard term `ledger_receipts`, 309.6 MB. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxSettledWakes` | 1,000 | settled wakes kept | the oldest let go | technical ceiling | The 10 GB: guard term `ledger_wakes`. |
| `owner/src/ledger.ts` `WAKE_SETTINGS.maxWakesPerHour` | 60 an hour at most | the wake setting's high | a higher setting refused | technical ceiling | The 10 GB: guard term `ledger_rings`, 72.0 MB, an hour's wakes for each agent row. |
| `owner/src/registrations.ts` `CARD_LIMITS` | 200 pending, 10 a client, 4,096-byte card | registration cards waiting | a client's 11th card refused | technical ceiling | The 10 GB: guard term `agent_cards`. |
| `owner/src/registrations.ts` `CARD_TTL_S` | 7 days | how long a filed card waits | the client files again | technical ceiling | The cards are a count-bounded queue in the 10 GB; an expiry frees a slot a client never returns for. |
| `owner/src/policy.ts` `POLICY_LIMITS.maxDocument` | 65,536 characters | the owner policy document | a larger policy refused | technical ceiling | The 10 GB, one row read on every door. |
| `owner/src/policy.ts` `POLICY_LIMITS.maxChange` | 16,000 characters | one agent's proposal | a larger proposal refused | technical ceiling | The 10 GB: guard term `policy_proposals`. |
| `owner/src/policy.ts` `POLICY_LIMITS.proposalTtlS` | 90 days | a pending policy proposal | it expires unanswered | technical ceiling | As `CARD_TTL_S`: the proposals are a count-bounded queue. |
| `owner/src/policy.ts` `POLICY_LIMITS.pendingProposals` | 200 (50 a client) | proposals waiting for the owner | a proposal refused past it | technical ceiling | The 10 GB, inside `policy_proposals`. |
| `owner/src/policy.ts` `POLICY_LIMITS.appliedProposals` | 200 | applied proposals kept | the oldest let go | technical ceiling | As above. |
| `owner/src/policy.ts` `POLICY_LIMITS.receiptsPerBucket` | 5,000 a bucket | policy receipts kept | the oldest let go | technical ceiling | The 10 GB: guard term `policy_receipts`, 65.0 MB. |
| `owner/src/policy.ts` `MODEL_SOURCES_MAX` | 64 | sources a `models.sources` table names | a larger table refused | technical ceiling | Inside `maxDocument`, the one row every door reads. |
| `owner/src/grants.ts` `GRANT_LIMITS.maxFreeClients` | 1,000 | clients recorded without an owner grant | the oldest free one forgotten (never refused) | technical ceiling | The 10 GB: guard terms `outside_clients`, `proposal_shares`. |
| `owner/src/grants.ts` `GRANT_LIMITS.maxOwnerClients` | 1,000 | clients the owner set a grant for | the 1,001st grant refused | technical ceiling | As above. |
| `owner/src/grants.ts` `GRANT_LIMITS.maxScopesBytes` | 65,536 bytes | a grant's scopes as JSON | a larger grant refused | technical ceiling | The 10 GB, inside `outside_clients`; the schema's own check on the column. |
| `doors/src/api-keys.ts` `MAX_KEYS_PER_OWNER` | 1,000 | API keys an owner holds | a 1,001st refused | technical ceiling | Each key's grant is an owner-set client: `maxOwnerClients`, held equal by a test. |
| names and ids a caller gives (`text.ts` `name` 256 characters; a scope field 512; a display name 80; a history op 32; a lease holder 128; a connection's name 128; a model's id 200) | as listed | each name | a longer name refused | technical ceiling | The 10 GB: the guard costs every row at its validator's widest name. |
| `owner/src/sessions.ts` `SESSION_LIMITS.maxSessions` | 64 | signed-in devices at once | the oldest session closed | technical ceiling | The 10 GB, each session a row the guard costs. |
| `owner/src/sessions.ts` `SESSION_LIMITS` life | 8 hours | the owner's protected session | the owner signs in again for a widening | technical ceiling | A credential's life: NIST SP 800-63B's reauthentication at AAL2, at most 12 hours. The cookie's `Max-Age`. |
| `owner/src/sessions.ts` `SESSION_LIMITS` freshness | 600 s | how fresh a sign-in must be to open a session | the owner signs in again | technical ceiling | S30: a widening needs a sign-in the owner just made. |
| `owner/src/connections.ts` `CONNECTION_LIMITS.maxConnections` | 50 | live model connections | the 51st refused | technical ceiling | The 10 GB: guard terms `connections` 269.3 MB, `connection_secrets`. A connection's models list is read in every egress admission unit, so it stays in its row (ADR-035 decision 9). |
| `owner/src/connections.ts` `CONNECTION_LIMITS.maxOpenFlows` | 10 | OAuth connect flows open at once | an 11th refused | technical ceiling | The 10 GB: guard term `connection_flows`; each holds a PKCE verifier for `flowTtlS`. |
| `owner/src/connections.ts` `CONNECTION_LIMITS.flowTtlS` | 600 s | an OAuth flow's life | the owner starts the connect again | technical ceiling | An authorization code's most recommended life, 10 minutes (RFC 6749 §4.1.2). |
| `owner/src/connections.ts` `CONNECTION_LIMITS.maxModels` | 1,024 | models one connection lists | models past it refused at commit | technical ceiling | Inside `connections`; the largest built-in catalog has 386. |
| `owner/src/connections.ts` `MODELS_JSON_MAX` | 640 KiB | a connection's models, as JSON | a larger list refused at commit | technical ceiling | As above. |
| `owner/src/connections.ts` `EGRESS_JSON_MAX` | 16 KiB | a connection's egress scope | many custom paths refused at commit | technical ceiling | As above. |
| `owner/src/connections.ts` `BLOB_MAX` | 64 KiB | a connection's sealed blob | a credential past it refused | technical ceiling | Guard term `connection_secrets`; the gateway's own bound too. |
| `owner/src/connections.ts` model numbers | $1,000,000 a million tokens; 100,000,000 tokens; 16 tiers | a model record's prices, windows and tiers | a record past them refused | technical ceiling | A charge, price times tokens, stays an exact integer in a JavaScript number (2^53). Far past every catalog's. |
| `owner/src/connections.ts` redirect | 2,048 characters | an OAuth redirect URI | a longer URI refused | technical ceiling | The 10 GB: inside `connection_flows`' 4 KiB a flow. |
| `owner/src/usage.ts` `USAGE_LIMITS.minOutput` | 1,024 tokens (2,048 reasoning) | the least output a reservation is worth | refused when an owner's ceiling leaves less | technical ceiling | Only when an owner's own ceiling binds; provider thinking budgets start at 1,024. |
| `owner/src/usage.ts` `USAGE_LIMITS.receiptsKept` | 5,000 | model-call receipts kept | the oldest let go | technical ceiling | The 10 GB: guard term `model_reservations`. |
| `owner/src/schema.ts` `CALLS_OPEN` | 1,000 | model reservations open at once | refused `model_calls_full` | technical ceiling | Inside `model_reservations`; far above any tier's runs. |
| `owner/src/schema.ts` `SPEND_KEPT` | 4,300 | day and month spend counters | a 101st source's first call in a period refused | technical ceiling | The 10 GB: guard term `model_spend`. |
| `owner/src/egress.ts` `EGRESS_LIMITS.receiptsKept`, `refusalsKept` | 2,000 admitted, 500 refused | egress receipts kept | the oldest let go | technical ceiling | Inside `egress_reservations`. |
| `owner/src/egress.ts` `MIN_OUTPUT` | 1,024 tokens | a sandbox call's least output | refused when a cap leaves less | technical ceiling | As `usage.ts`'s. |
| `owner/src/egress.ts` `EGRESS_PATH_MAX` | 2,048 characters | a sandbox request's path and query | a longer path refused | technical ceiling | The 10 GB: each egress row keeps its path, costed at it in `egress_reservations`. |
| `owner/src/network.ts` host | 253 characters | an egress destination's host | a longer host refused | technical ceiling | DNS's own bound (RFC 1035). |
| `owner/src/network.ts` `EGRESS_BODY_MAX` | 16 MiB | one sandbox model request's body | refused `egress_too_large` | technical ceiling | The isolate's 128 MB, which holds the body and its decoded text while the gateway meters it. Providers take more, past what an isolate holds. Live memory probe owed to slice L. |
| `gateway/src/forward.ts` `BODY_MAX` | 16 MiB | a metered call's body | as `EGRESS_BODY_MAX` | technical ceiling | As `EGRESS_BODY_MAX`. |
| `doors/src/routing-mac.ts` `LEASE_LIVED` | none of their own | the egress and sync tokens' life | a token lives while its lease's heartbeat renews it | technical ceiling | The lease's life (`LEASE_TTL_S` without a renewal); no run meets a fixed deadline. |
| `owner/src/cron.ts` `CRON_MIN_GAP_MIN` | 1 minute | the shortest gap between a schedule's runs | nothing below cron's own minute | technical ceiling | Cron's resolution; the tier's runs bound what a schedule starts. |
| `owner/src/cron.ts` `CRON_MAX_ITEMS` | 60 | values in one cron field's list | a longer list refused | technical ceiling | A minute field's every value. |
| `owner/src/cron.ts` `CRON_MAX_CHARS` | 256 characters | a cron expression | a longer expression refused | technical ceiling | The 10 GB: a schedule's row, costed at it in `run_schedules`. |
| `owner/src/cron.ts` `CRON_HORIZON_DAYS` | 8 years | how far ahead an occurrence is searched | an expression with none in 8 years refused | technical ceiling | The Gregorian calendar: the longest gap between two 29 Februaries is 8 years, so an expression with no occurrence within it has none. |
| `owner/src/notices.ts` `NOTICES_KEPT` | 1,000 | owner notices kept | the oldest let go | technical ceiling | The 10 GB: guard term `owner_notices`. |
| `owner/src/notices.ts` `NOTICE_DETAIL` | 1,024 characters | a notice's detail | a longer detail cut | technical ceiling | Inside `owner_notices`; the full error stays in its source. |
| `owner/src/run-rows.ts` `RUNS_KEPT` | 2,000 | settled runs kept as receipts | an older run's status is gone (its transcript stays) | technical ceiling | The 10 GB: guard term `runs`, 90.4 MB with the live and waiting runs. |
| `owner/src/run-rows.ts` `RESULT_BYTES` | 16 KiB | a run's result on its receipt | a longer result kept cut, `result_cut` | technical ceiling | Inside `runs`. The whole result is in the engine's transcript, in the owner's files. |
| `owner/src/run-rows.ts` `ERROR_BYTES` | 1 KiB | a run's first error on its receipt | a longer error kept cut | technical ceiling | As above. |
| `owner/src/run-rows.ts` `CONVERSATIONS_KEPT` | 10,000 | conversations kept | the least recently run idle one forgets its resume id | technical ceiling | The 10 GB: guard term `conversations`. Nothing is refused. |
| `owner/src/run-rows.ts` `SCHEDULES_KEPT` | 1,000 | the `runs` lane's schedules | a new schedule refused `run_schedules_full` | technical ceiling | The 10 GB: guard term `run_schedules`. |
| `owner/src/run-rows.ts` `INPUT_BYTES` | 16 KiB | a schedule's stored input | a longer scheduled input refused | technical ceiling | Inside `run_schedules`. |
| `owner/src/runs.ts` one live run a conversation | one | runs in one conversation at once | a second message waits its turn, never refused | technical ceiling | One engine session resumes one invocation at a time. |
| `owner/src/run-rows.ts` `QUEUE_ROWS` | 256 | inputs waiting for their conversations | one more refused `run_inputs_full` until a run settles | technical ceiling | The 10 GB: each waits as a `runs` row beside its input. |
| `owner/src/run-rows.ts` `QUEUE_BYTES` | 6 MiB | the waiting inputs' bytes | as above | technical ceiling | The 10 GB: guard term `run_inputs`. |
| `owner/src/run-rows.ts` `ENGINE_SIGNINS` | 64 | engines whose sign-in is kept | a report past it fails loudly | technical ceiling | Guard term `engine_signins`. |
| `owner/src/run-rows.ts` `SIGNIN_MODELS_CHARS` | 2,048 characters | an engine's reported models | a longer report refused | technical ceiling | Inside `engine_signins`. |
| `owner/src/run-rows.ts` `SIGNIN_LIST_CHARS` | 256 characters | a client's sandbox's signed-in engines | a longer report refused | technical ceiling | Inside `sandbox_leases`. |
| `owner/src/socket-shape.ts` `SOCKET_TICKETS` | 1,024 | socket tickets outstanding | refused `socket_tickets_full` until one is used or expires | technical ceiling | Guard term `socket_tickets`. |
| `owner/src/socket-shape.ts` ticket life | 30 s | a socket ticket's life | the app asks again | technical ceiling | A single-use credential in a URL, which servers and proxies log; it lives only for the connect. |
| `owner/src/relay.ts` sockets | 1,024 | the owner app's sockets open at once | another refused `sockets_full` | technical ceiling | The isolate's 128 MB (memory guard). |
| `owner/src/relay.ts` tails | 4 Mi UTF-16 units | the live runs' tails, together | a late joiner sees fewer old frames | technical ceiling | The isolate's 128 MB. Frames are never committed. |
| `owner/src/relay.ts` one frame kept | 256 Ki units | one frame in a tail | relayed live, not kept | technical ceiling | As above. |
| `owner/src/run-events.ts` `EVENTS_BODY_MAX` | 64 KiB | a runner's events post | refused `events_too_large`; the runner posts more often | technical ceiling | The isolate's 128 MB (memory guard). |
| `owner/src/run-events.ts` `EVENTS_IN_FLIGHT` | 8 | events posts held at once | `events_busy` (503), posted again | technical ceiling | As above. |
| `owner/src/run-events.ts` `EVENT_FRAMES_MAX` | 64 | frames in one post | the runner posts more often | technical ceiling | As above. |
| `owner/src/run-events.ts` `FRAME_UNITS_MAX` | 32 Ki units | one frame's JSON | the runner cuts a longer CLI event, marked | technical ceiling | As above. |
| `doors/src/config.ts` `ACCESS_TOKEN_TTL_S` | 3,600 s | an MCP or app access token's life | the client refreshes | technical ceiling | A bearer credential's short life (OAuth 2.0 Security BCP, RFC 9700), renewed by its refresh token. |
| `doors/src/config.ts` `REFRESH_TOKEN_TTL_S` | 30 days | a refresh token's life | an unused client signs in again | technical ceiling | As above: a refresh token's grant slides while used and ends when unused (RFC 9700). |
| `doors/src/config.ts` consent freshness | 600 s | how fresh a sign-in is for consent | the owner signs in again | technical ceiling | `prompt=login`: a consent needs a sign-in the owner just made. |
| `doors/src/config.ts` `AUTHORIZE_BODY_MAX` | 4 KiB | the consent form's body | refused `413` | technical ceiling | The form's own few short fields. |
| `doors/src/config.ts` `API_BODY_MAX`, `APP_BODY_MAX` | 32 MiB | an `/api/tools/*` or owner-surface request body | refused `413` | technical ceiling | Carries a `BODY_BYTES_MAX` body as JSON with room for its escapes, within the isolate's 128 MB. |
| `gateway/src/oauth.ts` token answer | 64 KiB | a provider's token endpoint answer | the connect fails | technical ceiling | It must fit the sealed blob it becomes (`BLOB_MAX`). |
| `gateway/src/credential.ts` limits | 16 KiB secret; 16 endpoints of 64 paths; 64 declared models; 2,048-character URLs | a pasted credential and its declared endpoints | a larger record refused at seal | technical ceiling | Inside the 64 KiB sealed blob (`keyring.ts` `BLOB_MAX`). |
| `gateway/src/model-record.ts` numbers | as the owner's | a declared model record | refused past them | technical ceiling | As the owner's model numbers. |
| `hooks/src/codemode.ts` `CODEMODE_INTERRUPT_BUDGET` | 1,000,000 polls (about 100 s) | a script's CPU | a compute-bound script ends `timeout` | technical ceiling | Within the hooks Worker's `cpu_ms`. |
| `hooks/src/codemode.ts` `CODEMODE_MEMORY_BYTES` | 32 MiB | a script's VM memory | a script past it fails | technical ceiling | The isolate's 128 MB, shared by its VMs. |
