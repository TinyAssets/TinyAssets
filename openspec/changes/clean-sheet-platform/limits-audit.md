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
- internal timings and backoffs: leases, retries, garbage-collection grace, lane passes, wake retries, a placement upload's 10-minute expiry, how long one read of the control record serves a script's calls (`CONTROL_FRESH_MS`), an egress reservation's beat and its lost detection three beats on (`EGRESS_BEAT_S`, `LOST_AFTER_S`), the erasure's day under its mark before its R2 step and wipe (`PUT_GRACE_MS`), its job waking it, the deadlines on the gateway's own calls to a provider (the setup check, a token endpoint), and the sandbox's own (slice S): a renewal's window (`sandbox_renewal_window_s`, the control record's) and its measured edge, the sandbox look's and the line's back-offs, a runner command's deadline (a lease's TTL), and the idle reap a lease's TTL on;
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

**The worst case that ships is 4,449,862,167.75 bytes, 44.50% of the 10 GB: 550.1 MB under
half**, slice ID's agent identities and mail authority, slice M's change orders and slice W's
doorbell subscriptions (`wake_callbacks`, 423.6 MB at 1,000 agents and their widest) included (`owner/guards/limits-audit.test.ts` prints each term; slice D's raises had left
2.1 MB). Without each structural change, the same bounds would cost: bodies in their rows
424.1 GB; history as rows times revisions 111.5 GB; recipients as messages times agents
46.2 GB; scopes at 512 code points of JSON each 6.8 GB, each past the half.

The largest terms: the file manifest 1,208.0 MB (24.16% of the budget), `collection_rows`
604.0 MB, `wake_callbacks` 423.6 MB, `ledger_messages` 399.2 MB, `ledger_receipts` 309.6 MB, `collection_history`
302.0 MB, `connections` 269.3 MB, `message_bodies` 219.7 MB, `ledger_recipients` 107.9 MB.

## Summary

- **tier**: 3
- **owner setting**: 12
- **vendor limit**: 9
- **technical ceiling**: 113
- **total**: 137

Counted by a script over every table row (`| ` lines past the header, six cells each), the first row and the names row among them.

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

## Chosen retentions

Three expiries are not ceilings: each queue or area is bounded by its count or charge in the
10 GB, and needs no expiry to fit. Each is a retention chosen for what it serves, with its
reason; none refuses a write.

- `owner/src/files.ts` `TEMP_TTL_S`, 7 days: a temporary directory under `/.ta/tmp` unused
  for a week has its files deleted. The area is the sandbox's for spills, charged to the
  owner's files; a week is long past any run (a sandbox lives a day at most, E2B Pro's 24
  hours), so a crashed run's spills do not hold the owner's storage.
- `owner/src/registrations.ts` `CARD_TTL_S`, 7 days: a filed registration card waits a week
  for the owner, so an owner away for a week misses none; the client files again past it.
- `owner/src/policy.ts` `POLICY_LIMITS.proposalTtlS`, 90 days: a pending policy proposal
  waits a quarter for the owner's answer, then expires unanswered, so a stale proposal never
  applies against a policy long since changed.

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
| `owner/src/ledger.ts` `WAKE_DEFAULTS` | none: no hourly cap, no batch window (slice W) | an agent's doorbells | nothing by default; with a cap the owner set, wakes past it wait their turn | owner setting | Off by default (`policyWakeLimits`, the owner app's `agent/wake-limits`). A cap is spaced evenly by one `wake_tat` column, no burst. With none, each change rings, one open wake an agent at a time, and the vendor's own limit answers 429, whose `Retry-After` the wake waits out (`wake-kinds.json` records each vendor's). |
| `owner/src/network.ts` `EGRESS_DEADLINE_S` | 86,400 s | one streamed sandbox model call | a stream past a day is cut `deadline`, settled `unknown` | vendor limit | An E2B Pro sandbox lives at most 24 hours (`engines-replan.md` §3); a subrequest has no time limit while its client stays connected (Workers limits). `LOST_AFTER_S` follows. |
| `gateway/src/forward.ts` `FORWARD_DEADLINE_MS` | 24 hours | one forwarded request | as `EGRESS_DEADLINE_S` | vendor limit | Held equal to it by a test. |
| `owner/src/owner-do.ts` `CODE_ANSWER_MAX` | 31 MiB as V8 serializes it | a `code` script's answer (value and output) | answered `answer_too_large`, its calls and what landed kept | technical ceiling | Workers RPC's 32 MiB of serialized arguments or return value (its own error, observed in workerd: "Serialized RPC arguments or return values are limited to 32MiB"), the owner-to-doors hop, less 1 MiB for the envelope. Measured as V8 serializes (`serializedBytes`): a string one byte a unit when every unit is Latin-1, else two bytes a unit for the whole string, so mostly ASCII text with one wide character counts double. pi-codemode's own output bound (16 Mi characters, 100,000 items) is the vendor's, not ours, and is not this path's ceiling: 16 Mi two-byte characters are 32 MiB serialized. |
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
| `owner/src/files.ts` `WHOLE_BYTES` | 16 MiB | one whole-file read or write through the object | a larger file is read by range, written by upload | technical ceiling | A Worker isolate's 128 MB of memory, which a whole file in one call holds with its copies. |
| `owner/src/files.ts` `MAX_SYNC_OPS` | 1,000 | ops in one sandbox sync commit | the sync agent splits a larger commit | technical ceiling | One synchronous gate unit per commit, which must stay short in the object's request CPU. |
| `owner/src/files.ts` `KEEP_CEILING` | 1,000 | the most `keep_versions` and `keep_recent` take | the setting refused above it | technical ceiling | The 10 GB, inside the manifest's bytes. |
| `owner/src/files.ts` `tempDir` | 239 bytes | a temporary directory's prefix | a longer prefix refused | technical ceiling | One path component, `MAX_NAME_BYTES` 255, with its 16 hex. |
| `owner/src/owner-do.ts` `fileWrite` | 4 MiB | one `fileWrite` call's content | a larger file goes by upload | technical ceiling | One chunk; Workers RPC's message and the isolate's 128 MB. |
| `owner/src/paths.ts` `MAX_PATH_BYTES` | 4,096 bytes | a path | a longer path refused | technical ceiling | Linux's `PATH_MAX`, which the sandbox's disk holds. |
| `owner/src/paths.ts` `MAX_NAME_BYTES` | 255 bytes | one path component | a longer name refused | technical ceiling | Linux's `NAME_MAX`. |
| `owner/src/manifest.ts` `PLACE_BYTES` | 2,048 bytes | the most of a body kept in its row | nothing: a larger body is placed in R2 | technical ceiling | ADR-035: a rowid table keeps a record of 4,061 bytes in place; half is the body's, half the row's own names and ids, so a row keeps in its page. |
| `owner/src/bodies.ts` `BODY_BYTES_MAX` (`LIMITS.maxRowBytes`, `MAX_BODY_BYTES`) | 8 MiB | one row's, history entry's or message's body | a larger body refused | technical ceiling | Half the request body's ceiling (`EGRESS_BODY_MAX`), so the body with its JSON escapes fits the tool call that carries it; placed in two 4 MiB chunks. |
| `owner/src/collections.ts` `LIMITS.maxRowsCharge` | 256 MiB laid out | every collection row, its index entries and its collection's seq row, as SQLite lays them out | a write refused: the owner's collections are full | technical ceiling | The 10 GB: guard term `collection_rows`, 604.0 MB at `WORST_FILL`. About 262,000 rows at the narrowest names; an outside writer's share by `client_share`. |
| `owner/src/collections.ts` `LIMITS.maxHistoryCharge` | 128 MiB laid out | the history's entries, erased keys' last entries among them | the oldest entries pruned, never refused | technical ceiling | The 10 GB: guard term `collection_history`, 302.0 MB at `WORST_FILL`. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxAgents` | 1,000 | enabled agents: outside agents and the owner's own live agents (slice ID) together | the 1,001st outside agent's registration, or live agent's `agent.json` write, refused (`ledger_agents_full`) | technical ceiling | The 10 GB, within `maxAgentRows`. A message has one recipient, so no term moves with it. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxAgentRows` | 1,000 | agent rows, disabled agents included | a registration refused past it | technical ceiling | The 10 GB: guard terms `outside_agents`, `ledger_readers`, `ledger_usage`. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxMessages` | 50,000 | messages held | the oldest acked message evicted; refused `ledger_full` if every one is unread | technical ceiling | The 10 GB: guard terms `ledger_messages` 399.2 MB, `message_bodies` 219.7 MB (each body up to 2 KiB) and `ledger_recipients` 107.9 MB. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxOwnerUnacked` | 10,000 | messages unread by the owner | a post to the owner refused past it | technical ceiling | Within `maxMessages` (`checkLimits`), so no mailbox holds the whole ledger. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxAgentUnacked` | 1,000 | messages unread in one agent's mailbox | a post to that agent refused | technical ceiling | As above. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxSenderUnacked` | 5,000 | one sender's messages unread by the owner | a sender's post refused past it | technical ceiling | Within `maxOwnerUnacked`, so no agent fills the owner's mailbox. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxReceipts` | 50,000 | read and reply receipts kept | the oldest let go | technical ceiling | The 10 GB: guard term `ledger_receipts`, 309.6 MB. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxSettledWakes` | 1,000 | settled wakes kept | the oldest let go | technical ceiling | The 10 GB: guard term `ledger_wakes`. |
| `owner/src/ledger.ts` `WAKE_SETTINGS` | 9,007,199,254,740,991 | the owner's cap and batch window | a larger number refused | technical ceiling | JavaScript's largest exact whole number (`Number.MAX_SAFE_INTEGER`): the cap's state is one column whatever its value. |
| `owner/src/wake-records.ts` `SUBSCRIPTIONS_PER_AGENT` | 16 an agent | doorbell subscriptions one enabled agent holds | the 17th refused `subscription_limit`; a refresh of one held is no new place | technical ceiling | The 10 GB: guard term `wake_callbacks`, 423.6 MB (8.47% of the budget), its rows at the schema's widest: `maxAgents` (1,000) times 16. Per agent, so no client fills another agent's room, nor the owner's. |
| `owner/src/wake-records.ts` `DOORBELL_BLOB_CHARS` | 8,192 characters | a sealed doorbell (URL, auth scheme, secret, record), base64url | a larger blob refused | technical ceiling | Inside `wake_callbacks`; the schema holds it visible ASCII, a byte a character. |
| `owner/src/wake-records.ts` `CALLBACK_URL_CHARS`, `CALLBACK_SECRET_CHARS` | 2,048 and 512 printable ASCII characters | a doorbell callback's URL and secret | a longer callback refused | technical ceiling | Inside `wake_callbacks`, through the blob they seal into. |
| `owner/src/wake-records.ts` `ARGUMENTS_CHARS` | 1,024 characters of JSON | a subscription's arguments (command center, board, rooms) | a subscription following more rooms refused | technical ceiling | Inside `wake_callbacks`, at four bytes a character. Several subscriptions follow more rooms between them. |
| `owner/wake-kinds.json` `max_body_bytes` | 256 KiB (ChatGPT); none recorded elsewhere | one doorbell body | a larger wake refused before it is sent | vendor limit | OpenAI's MCP Events page: at most 256 KiB a webhook (read 2026-11-03). A wake carries IDs and cursors only, far under it. |
| `owner/wake-kinds.json` `events` | 10 s verification, a day's default lifetime, 300 s dual-signing | an MCP Events subscription | a callback silent past 10 s refused (`timeout`); a subscription unrefreshed past its grant lets go | vendor limit | OpenAI's MCP Events page (its verification example's 10 s) and the MCP Events design sketch (grants up to about a day; receivers reject a timestamp over 5 minutes old). |
| `gateway/src/doorbell.ts` `ECHO_MAX_BYTES` | 1 KiB | a callback's answer to its verification challenge | a longer answer fails verification (`challenge_failed`) | technical ceiling | The answer is read only for the echoed 43-character challenge; at most 1 KiB keeps a hostile endpoint from making the gateway's isolate (128 MB) buffer its body. |
| `owner/src/registrations.ts` `CARD_LIMITS` | 200 pending, 10 a client, 4,096-byte card | registration cards waiting | a client's 11th card refused | technical ceiling | The 10 GB: guard term `agent_cards`. |
| `owner/src/policy.ts` `POLICY_LIMITS.maxDocument` | 65,536 characters | the owner policy document | a larger policy refused | technical ceiling | The 10 GB, one row read on every door. |
| `owner/src/policy.ts` `POLICY_LIMITS.maxChange` | 16,000 characters | one agent's proposal | a larger proposal refused | technical ceiling | The 10 GB: guard term `policy_proposals`. |
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
| `owner/src/network.ts` `EGRESS_BODY_MAX` | 16 MiB | one sandbox model request's body | refused `egress_too_large` | technical ceiling | One request body a Worker reads whole: the text (16 MiB, at most twice that as UTF-16), its parsed copy and its serialized copy to the next hop come to 80 MiB at worst, within the isolate's 128 MB (Workers limits) with room for the rest; one ceiling for the egress door, the gateway, the API and the owner surface (`network.ts`). Live memory probe owed to slice L. Providers take more (Anthropic's Messages API, 128 MB), past what an isolate holds. |
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
| `owner/src/run-rows.ts` `SIGNIN_URL_MAX` | 8,000 characters | the vendor's sign-in URL a sign-in shows | a longer one refused, the sign-in ended `refused` | technical ceiling | RFC 9110 section 4.1: senders and recipients support URIs of at least 8,000 octets. Inside `sandbox_signins`. |
| `owner/src/run-rows.ts` `SIGNIN_CODE_MAX` | 16 characters | the device code a sign-in shows | a longer one refused, the sign-in ended `refused` | technical ceiling | RFC 8628 section 6.1 keeps device codes short (8 recommended); every record's shape is within 16. |
| `owner/src/run-rows.ts` `SIGNIN_PASTE_MAX` | 4,095 characters | a code the owner pastes back | a longer one refused | technical ceiling | One line to the CLI's terminal: Linux's `N_TTY_BUF_SIZE`, 4,096 bytes with its newline. |
| `sources/{codex,gemini,kimi}.json` `cli.login.expires` | Codex 900 s, Gemini 300 s, Kimi 900 s | an open sign-in, from its start | the sign-in ended `expired` (one lease TTL later on the trusted side); the owner starts again | vendor limit | Codex's CLI's own text ("expires in 15 minutes"); the pinned gemini-cli 0.66.0's and kimi-code 2.4.1's own timers. Each record cites its own. |
| `sources/claude-code.json` `cli.login.expires` | 600 s | an open Claude sign-in, from its start: the owner's password, 2FA and account steps, then the code pasted back | the sign-in ended `expired`; the owner starts again | technical ceiling | The platform's own bound, not Anthropic's: an open sign-in keeps a metered sandbox up, and Anthropic documents no lifetime (claude 2.1.309 waits with no timer). RFC 6749 section 4.1.2's 10-minute maximum for an authorization code is context only, since it runs from the code's issue. Owed: the time a real sign-in with 2FA takes (the founder's live test), which sets it if 600 s is too tight. |
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
| `doors/src/config.ts` `API_BODY_MAX`, `APP_BODY_MAX` | 16 MiB | an `/api/tools/*` or owner-surface request body | refused `413` | technical ceiling | As `EGRESS_BODY_MAX`, held equal to it by a test. Streaming is not taken: a tool call's arguments are parsed whole as JSON at the door. |
| `gateway/src/oauth.ts` token answer | 64 KiB | a provider's token endpoint answer | the connect fails | technical ceiling | It must fit the sealed blob it becomes (`BLOB_MAX`). |
| `gateway/src/credential.ts` limits | 16 KiB secret; 16 endpoints of 64 paths; 64 declared models; 2,048-character URLs | a pasted credential and its declared endpoints | a larger record refused at seal | technical ceiling | Inside the 64 KiB sealed blob (`keyring.ts` `BLOB_MAX`). |
| `gateway/src/model-record.ts` numbers | as the owner's | a declared model record | refused past them | technical ceiling | As the owner's model numbers. |
| `hooks/src/codemode.ts` `CODEMODE_INTERRUPT_BUDGET` | 1,000,000 polls (about 100 s) | a script's CPU | a compute-bound script ends `timeout` | technical ceiling | Within the hooks Worker's `cpu_ms`. |
| `hooks/src/codemode.ts` `CODEMODE_MEMORY_BYTES` | 32 MiB | a script's VM memory | a script past it fails | technical ceiling | The isolate's 128 MB, shared by its VMs. |
| control record `sandbox_runtime_ceiling_s` (`doors/src/directory.ts`) | the E2B plan's: Hobby 3,600 s, Pro 86,400 s | how long one sandbox runs at a stretch | nothing ends: its runs are renewed (paused and resumed with their memory) at a quiet moment before it | vendor limit | E2B's continuous runtime per plan (docs.e2b.dev, sandbox lifetime), which a pause resets (probed, `sandbox-probe.md`); edited in the record with no deploy. |
| control record `sandbox_concurrency_ceiling` (`doors/src/directory.ts`) | the E2B plan's: Hobby 20, Pro 100 | sandboxes the E2B team runs at once | a run waits `sandbox_waiting` in the platform's line, never refused | vendor limit | E2B's concurrent sandboxes per plan; E2B's own refusal (`429`) holds it regardless. |
| `sandbox/runner/guards.ts` `ATOM_MIN` | 39 characters | the shortest value or line of a `secret_paths` file the content backstop matches | a synced file or transcript line holding one refused | vendor limit | A Google API key, 39 characters: the shortest credential any record's provider issues. |
| `sandbox/template/build.py` run pool | `RUNS_CEILING` (1,000) users | runs live in one sandbox at once | a run past it refused, every run user taken | technical ceiling | One uid a live run (review H-4); one authority's sandbox holds at most its owner's live runs, which `RUNS_CEILING` bounds. |
| `doors/src/egress.ts` `SYNC_BODY_MAX` | 12 MiB | one sync door's body | the sync agent sends more calls | technical ceiling | Two 4 MiB chunks' base64 and the call's fields: the files plane's most a call (`BATCH_BYTES`). |
| `sandbox/runner/runner.ts` `CANCEL_GRACE_MS` | 20 s | a cancelled CLI's grace after SIGTERM | killed past it | technical ceiling | One heartbeat (`HEARTBEAT_MS`): the run's end and its last post reach the object inside its lease's TTL. |
| `sandbox/runner/users.ts` `KILL_WITHIN_MS` | 20 s | how long a run user's processes may outlive their SIGKILL | the run's end fails loudly, the user not given back | technical ceiling | One heartbeat; a process still there is stuck in the kernel. |
| `sandbox/runner/runner.ts` a failed CLI's stderr | `FRAME_UNITS_MAX` | the stderr a failed run shows | its head cut | technical ceiling | As much as one frame shows; the frame's own cut marks the rest. |
