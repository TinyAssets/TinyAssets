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
  share is named by the guard's term;
- **no rate limit** (slice L, after its review): the platform sets no number on any caller,
  authenticated or not. The bounds on an unauthenticated caller are Cloudflare's always-on
  DDoS protection in front of every Worker, the OAuth provider's own per-fetch bounds
  (below), and the runtime's own ceilings. A per-IP backstop, if the founder ever wants one,
  is a zone WAF rule with the founder's own number, an owner setting of the founder's,
  never a number in code. Dividing an account's included quota among IPs derives nothing,
  so no such number is kept.

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

**The worst case that ships is 4,651,798,214.75 bytes, 46.52% of the 10 GB: 348.2 MB under
half** (the sum of every term and the runtime's rows, `owner/guards/limits-audit.test.ts`).
Slice STORE item 2 took 152.6 MB of the room under half the change log's bound was derived
from (665.1 MB), within its quarter (166.3 MB, held by the guard): the archive's fence rows
(`archive_segments`, 151.4 MB at 59,606 rows), its moves and purges, and a message's two byte
counts; slice STARTER's terms took 0.4 MB more. Before it,
slice ID's agent identities and mail
authority, slice M's change orders, slice W's doorbell subscriptions (`wake_callbacks`,
423.6 MB at 1,000 agents and their widest), slice S's sandbox wait, slice L's keys and
inbound bindings (`inbound_bindings` 23.0 MB, `owner_keys` 2.9 MB), slice SIGNIN's sign-ins,
slice P's tables (`sandbox_billed` 29.7 MB with its index by age,
`change_log` 164.0 MB, `sandbox_live` 3.8 MB, `sandbox_spans` 3.1 MB, `owner_backups` 1.8 MB
and its kept rows 0.02 MB, `schema_digests` 1.0 MB, `sandbox_days` 0.4 MB), and the starter
slice's receipts (`starter_receipts` 360,352 bytes and `starter_state` 837 bytes, 361,189
together, exported and captured) included (`owner/guards/limits-audit.test.ts` prints each term; slice D's raises had left
2.1 MB). Without each structural change, the same bounds would cost: bodies in their rows
424.1 GB; history as rows times revisions 111.5 GB; recipients as messages times agents
46.2 GB; scopes at 512 code points of JSON each 6.8 GB, each past the half.

**The ceiling is the half, and only the half** (coordinator's ruling, 2026-11-03). The guard
holds the worst case at or under half the 10 GB. Slice LIM's 500 MB `MARGIN` past it was a
cushion ("no thin margin") that no platform fact derives, so it is gone: an underived
headroom is an invented limit. What is under the half is room, given out by stated shares:
the change log's `LOG_BYTES` takes a quarter of the room as it stood at its derivation
(665.1 MB with every other term at its worst, main at `8161540c1` with slice P, a
connection's models and egress counted in bytes since review L5-1), an equal
share with each of the three slices still to land (STORE, STARTER, BRAIN). Their terms take
the rest, 500.8 MB (STARTER's 361,189 bytes, well inside its quarter); the day a slice's terms would pass the half, the guard fails loudly and
the shares are decided again.

The largest terms: the file manifest 1,208.0 MB (24.16% of the budget), `collection_rows`
604.0 MB, `wake_callbacks` 423.6 MB, `ledger_messages` 399.2 MB, `ledger_receipts` 309.6 MB, `collection_history`
302.0 MB, `message_bodies` 219.7 MB, `change_log` 164.0 MB, `ledger_recipients` 107.9 MB;
`connections` 67.8 MB.

## Summary

- **tier**: 3
- **owner setting**: 12
- **vendor limit**: 13
- **technical ceiling**: 156
- **total**: 184

Counted by a script over every table row (`| ` lines past the header, six cells each), the first row and the names row among them.

Removed by this slice, since only the tier's storage bounds what they bounded, or no
platform fact stood behind them:
- the ledger's mailbox caps (`maxOwnerUnacked`, `maxAgentUnacked`, `maxSenderUnacked`), slice
  STORE item 2: one owner-wide unread ceiling, `maxUnacked`, in their place;
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

Four expiries are not ceilings: each queue or area is bounded by its count or charge in the
10 GB, and needs no expiry to fit. Each is a retention chosen for what it serves, with its
reason; none refuses a write.

- `owner/src/files.ts` `TEMP_TTL_S`, 7 days: a temporary directory under `/.ta/tmp` unused
  for a week has its files deleted. The area is the sandbox's for spills, charged to the
  owner's files; a week is long past any run (a sandbox lives a day at most, E2B Pro's 24
  hours), so a crashed run's spills do not hold the owner's storage.
- `owner/src/registrations.ts` `CARD_TTL_S`, 7 days: a filed registration card waits a week
  for the owner, so an owner away for a week misses none; the client files again past it.
- the control record's `scratch_retention_days` (30 to start; `sandbox/runner/scratch.ts`): an agent's sandbox scratch unused that long is deleted at its sandbox's next run. Scratch is a cache on the sandbox's own disk, never counted in the owner's storage; 30 days matches Codespaces' default idle deletion and Claude Code's transcript default. It refuses nothing; with none in the record only the disk's own pressure lets scratch go.
- `sandbox/runner/runner.ts` `SLOW_MATCH_MS`, 5 s: past it, a sync's report says how long matching the sync-ignore list took (`ta.sync_ignore_slow`), so the owner sees why a sync was slow. It refuses nothing. The matcher's own ceilings are `AUTOMATON_STATES` and `LIST_BYTES` (above); each name and folder is matched once a sync, remembered in `owner/src/sync-ignore.ts` `REMEMBERED_CHARS`, a memory that starts again past 8 Mi characters of keys and automaton words (16 MiB), so it holds the same for any tree or path length.
- `sandbox/runner/scratch.ts` `LOW_WATER_SHARE`, a chosen threshold, a tenth of the sandbox disk (about 1.4 GB): below that much free before a run, the least recently used agent's scratch is let go first. Room for a run's working files (a large clone and its build) before it starts. It refuses nothing; scratch is a cache, so letting it go costs a rebuild, never data. Each agent's use is its own stamp file, so two runs stamping at once never lose one.
- `owner/src/policy.ts` `POLICY_LIMITS.proposalTtlS`, 90 days: a pending policy proposal
  waits a quarter for the owner's answer, then expires unanswered, so a stale proposal never
  applies against a policy long since changed.

## The list

| Where | Value | Bounds | A user hits | Class | Reason |
|---|---|---|---|---|---|
| control record `tiers` (`owner/src/tier.ts`) | free 5, paid 10 | simultaneous runs | a run past them refused `tier_runs` | tier | ADR-034 decision 8, edited in the record with no deploy. |
| `owner/src/policy.ts` `fileLimits` | the tier's storage: free 25 GB, paid 250 GB | every byte of user content the owner stores, wherever it lives (slice P, the founder's one storage number of 2026-11-03): file chunks live and in history, small files kept inline, and the collections' rows, their history and mail, inline or placed in R2 (`file_space.stored`); never the platform's bookkeeping (indexes, receipts, counters) | history entries the rows no longer are and acked mail let go first, oldest first, then refused `storage_full` (used, total, what to free, the upgrade; 507 at the doors; `storage-full.ts`; `file_share` past an outside writer's share); with no tier numbers kept, `tier_unread`. The owner reads used and left (`storageUsage`, the app's `storage`) | tier | The control record's figure, kept from every door's binding. |
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
| E2B sandbox disk (`sandbox_disk_bytes`) | 14,825 MiB on our template (14.4 GB at `/`, 10.7 GB free when made; ext4, 3,788,096 inodes, 4 KiB blocks, 320 MB kept for root) | the agents' scratch, the mirror of the owner's files and the engines' homes, shared by an owner's live runs | a tool's write fails `ENOSPC`; old scratch is let go first (`scratch.ts`) and the run is told plainly, never an upgrade | vendor limit | E2B's disk per plan (Hobby 10 GiB, Pro 20+ GiB, fixed at template build; docs.e2b.dev/billing), the same on every tier; probed 2026-11-03 (`scripts/probes/sandboxprobe/`). |
| `sandbox/runner/scratch.ts` `RESERVE_BYTES` | 256 MiB | the runner's own room on the sandbox disk | nothing: a ballast the runner lets go on `ENOSPC`, and before sync-out on a full disk | technical ceiling | What a run stages: only the sessions the owner's files do not hold yet (never the engine's whole history), in the common case the one it changed, at most the largest engine transcript measured (86 MB), with the run's state and room to spare. The reserve is let go before that staging whenever the disk has less free than it, so the worst case (a full disk and an 86 MB session) still stages. Staging happens before the sync agent's lock, so runs that end together stage together; beyond the reserve, ext4 keeps 320 MB for root, which stages. |
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
| `owner/src/files.ts` open uploads' bytes | the storage figure, its most | bytes all open uploads hold before they commit | an upload's begin refused `uploads_open` (409 at the doors) when what is stored fits but what open uploads hold leaves no room: reported apart from stored bytes, never "full", never an upgrade | technical ceiling | Their chunks count in the storage figure from begin, so they can never hold more than it. An upload the sync agent gives up is aborted at once (`upload_abort`), and a sync lease's open uploads are aborted when the lease ends, so none waits out `INTENT_TTL_S` holding room. |
| `owner/src/files.ts` `INTENT_TTL_S` | 86,400 s | how long a file upload may stay open | a large upload over a slow link must finish within a day | technical ceiling | An open upload holds its chunks in `file_intents` and the quota until it ends; a 40 GiB file at 1 MB/s takes 11 hours. |
| `owner/src/files.ts` `WHOLE_BYTES` | 16 MiB | one whole-file read or write through the object | a larger file is read by range, written by upload | technical ceiling | A Worker isolate's 128 MB of memory, which a whole file in one call holds with its copies. |
| `owner/src/files.ts` `MAX_SYNC_OPS` | 1,000 | ops in one sandbox sync commit | the sync agent splits a larger commit | technical ceiling | One synchronous gate unit per commit, which must stay short in the object's request CPU. |
| `owner/src/files.ts` `KEEP_CEILING` | 1,000 | the most `keep_versions` and `keep_recent` take | the setting refused above it | technical ceiling | The 10 GB, inside the manifest's bytes. |
| `owner/src/files.ts` `tempDir` | 239 bytes | a temporary directory's prefix | a longer prefix refused | technical ceiling | One path component, `MAX_NAME_BYTES` 255, with its 16 hex. |
| `doors/src/file-bytes.ts` one upload request | one chunk, 4 MiB at most | one `PUT files/upload/<intent_id>/<index>` | an upload sends one request a chunk, in any order, again after any failure; the door answers a chunk it holds without a second put | technical ceiling | Cloudflare's request body ceiling, 100 MB on Free and Pro (its Workers limits page, read 2026-11-03), never bounds a file: the policy's `max_file_bytes` and the intent's plan do. |
| `owner/src/owner-do.ts` `fileWrite` | 4 MiB | one `fileWrite` call's content | a larger file goes by upload | technical ceiling | One chunk; Workers RPC's message and the isolate's 128 MB. |
| `owner/src/paths.ts` `MAX_PATH_BYTES` | 4,096 bytes | a path | a longer path refused | technical ceiling | Linux's `PATH_MAX`, which the sandbox's disk holds. |
| `owner/src/paths.ts` `MAX_NAME_BYTES` | 255 bytes | one path component | a longer name refused | technical ceiling | Linux's `NAME_MAX`. |
| `owner/src/manifest.ts` `PLACE_BYTES` | 2,048 bytes | the most of a body kept in its row | nothing: a larger body is placed in R2 | technical ceiling | ADR-035: a rowid table keeps a record of 4,061 bytes in place; half is the body's, half the row's own names and ids, so a row keeps in its page. |
| `owner/src/bodies.ts` `BODY_BYTES_MAX` (`LIMITS.maxRowBytes`, `MAX_BODY_BYTES`) | 8 MiB | one row's, history entry's or message's body | a larger body refused | technical ceiling | Half the request body's ceiling (`EGRESS_BODY_MAX`), so the body with its JSON escapes fits the tool call that carries it; placed in two 4 MiB chunks. |
| `owner/src/collections.ts` `LIMITS.maxRowsCharge` | 256 MiB laid out | every collection row, its index entries and its collection's seq row, as SQLite lays them out | a write refused: the owner's collections are full | technical ceiling | The 10 GB: guard term `collection_rows`, 604.0 MB at `WORST_FILL`. About 262,000 rows at the narrowest names; an outside writer's share by `client_share`. |
| `owner/src/collections.ts` `LIMITS.maxHistoryCharge` | 128 MiB laid out | the history's entries, erased keys' last entries among them | the oldest entries pruned, never refused | technical ceiling | The 10 GB: guard term `collection_history`, 302.0 MB at `WORST_FILL`. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxAgents` | 1,000 | enabled agents: outside agents and the owner's own live agents (slice ID) together | the 1,001st outside agent's registration, or live agent's `agent.json` write, refused (`ledger_agents_full`) | technical ceiling | The 10 GB, within `maxAgentRows`. A message has one recipient, so no term moves with it. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxAgentRows` | 1,000 | agent rows, disabled agents included | a registration refused past it | technical ceiling | The 10 GB: guard terms `outside_agents`, `ledger_readers`, `ledger_usage`. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxMessages` | 50,000 | messages held hot | never a delete (ADR-036): the `archive` lane moves acked mail to segments from half of it, and a post at it is `storage_busy` (503, `Retry-After` 30 s) until a move lands; when a move cannot land for the storage figure, `storage_full` | technical ceiling | A chosen hot size whose cost the guard holds under half: `ledger_messages` 399.2 MB, `message_bodies` 219.7 MB (each body up to 2 KiB) and `ledger_recipients` 107.9 MB, each at 50,000 rows, 726.8 MB together, 14,535 B a message at the validators' widest. It cannot grow: the room left under half (348.6 MB) is STARTER's and BRAIN's quarters (332.6 MB) and 16 MB. Slice STORE item 2: the hot set, not the owner's mail; archived mail is bounded by the tier's storage alone. |
| `owner/src/ledger.ts` `LEDGER_LIMITS.maxUnacked` | 25,000 | messages some recipient has not acked, owner-wide, whoever they are to | a post refused `ledger_full`: too much mail is unread, naming the readers holding the most; reading and acking makes room (never an upgrade) | technical ceiling | The hot ceiling less one segment's records: 50,000 − 25,000. The guard costs the three hot terms (a message, its body, its recipient, 14,535 B together at the validators' widest) at `maxMessages` rows, so any unread count up to it fits under half; the one binding constraint is that at the hot ceiling a segment's records (`SEGMENT_MIN_RECORDS`) stay acked, so the lane always has one to move (`checkLimits`, `limits-audit.test.ts`). It replaces the owner's 10,000, each agent's 1,000 and each sender's 5,000, which no platform fact forced; being owner-wide, one noisy sender can fill it (the spec says so). |
| `owner/src/segments.ts` `SEGMENT_MIN_BYTES`, `SEGMENT_MIN_RECORDS` | 8 MiB of records' own bytes (their trailer shares out, so a seal is never short of the least: review N4, at the production limits 11,999 small messages sealed at 8,489 records, encoded past 8 MiB), or 25,000 records | when a segment is sealed | nothing: a move waits for a segment's worth of acked mail | technical ceiling | A fence row costs the owner-bound guard about 2.5 KB at its worst fill; 0.5 TB of tier in 4 MiB segments took 317.5 MB, past STORE's quarter of the room under the half (166.3 MB). At 8 MiB: 151.4 MB. 25,000 records of at least `MIN_RECORD_BYTES` (336, the least mail's three rows, tested) pass 8 MiB too, so every sealed segment holds at least `SEGMENT_LEAST_BYTES` (8 MiB). A body is stored raw, never escaped (review N3), so a record is its body's bytes and its lines'. |
| `owner/src/segments.ts` `ARCHIVE_SEGMENTS_MAX`, `TIER_STORAGE_MAX` | 59,606 fence rows; 500,002,979,840 bytes (about 0.5 TB) | the archive's fence rows; the most storage a tier record may give | a tier record past it refused (`parseTiers`): a larger tier needs larger segments first, a redesign | technical ceiling | The guard's `archive_segments` term at its rows is 151.4 MB, within STORE's quarter (`limits-audit.test.ts`); the rows are at most the storage over 8 MiB, plus one: every segment holds at least 8 MiB but a lone one (a move seals at it; a straggler's merge only grows a segment, within a byte budget a pass (`MOVE_BYTES` less the segment, at least one straggler); a rewrite left small takes in its neighbours by key; `pack` cuts only where both sides hold at least it, else makes one piece), and no two overlap in key range (the schema refuses it). Paid's 250 GB is half of it. |
| `owner/src/ledger.ts` `STORAGE_BUSY_RETRY_S`; `owner/src/owner-do.ts` `ARCHIVE_RETRY_S`, `ARCHIVE_FAILS_TOLD` | 30 s; 60 s; 3 | a busy post's retry; a failed archive pass's hold; failed passes in a row before the owner is told once | nothing refused: a retry, a hold, one notice (`storage_busy_long`), quoting the last error or saying the passes stopped before they could say why | technical ceiling | About one lane pass (`LANE_PASS_S` 45 s); an R2 outage holds the lane a minute a try. The count is a row (`ledger_usage.archive_fails`), raised as a pass begins and cleared as it ends, so passes killed with their isolate count too (review N3). |
| `owner/src/segments.ts` `MOVE_BYTES` | 32 MiB (4 × `SEGMENT_LEAST_BYTES`) | the records' bytes one merge rewrites in a pass: its segment and its stragglers | the stragglers past it merge on the next passes (always at least one a pass) | technical ceiling | Work a pass, not memory (the move is streamed: `MOVE_HOLD_BYTES`): it bounds each pass's reads, hashing and calls. `pack`'s largest piece (twice the least and a record) and the one straggler always taken fit it but for their lines (`limits-audit.test.ts`). |
| `owner/src/segments.ts` `MOVE_HOLD_BYTES` | 44 MiB | what one archive move or rewrite holds in the isolate, whatever its records | the grant the move takes from the isolate-wide memory guard (`platform-memory`); refused, the lane holds and retries | technical ceiling | The memory research's §2 A (reviews N5, N6): a move is planned from its records' lengths alone, then streamed in two passes, each 4 MiB output chunk made from just the source ranges it covers (pass 1 hashes it for `Files.begin` and drops it, checking each placed body against its message's hash; pass 2 makes it again and sends it, one a call), the segment's bytes and format unchanged (tested against the reference encoder, and the production `mailParts` against `mailRecord`). A chunk that differed between the passes is refused by R2's check of its hash, loudly (tested). Its records are packed (`MoveTable`: keys, lengths, times, offsets, kinds and content refs in typed arrays, chains' names in one pool of bytes, rooms by index; plans in typed arrays): about 192 bytes a record (150 measured in workerd for the table at the bound, 42 in V8 for the plans), not an object a record (round 4's about 550). Derived from workerd's measurement (the vitest runner isolate under the inspector, two forced GCs before every sample, the move paused at its holding points): the move with the most records one merge took at the production limits (a piece of the smallest real messages near twice the least, 16,175 records, and 16,393 stragglers up to `MOVE_BYTES`: 32,568 records) grew the live heap by at most 32.2 MiB, so 26.2 MiB besides its records (its chunks and the runtime's buffers around the binding's calls); the most records a move can hold is `MOVE_RECORDS`, 74,933 (`pack`'s largest piece of records at `MIN_RECORD_BYTES`, 49,932 and the record past twice the least, and the 25,000 stragglers a pick takes at most), 13.7 MiB at 192 bytes; and the binding's copy of the chunk sent, 4 MiB: 43.9 MiB, rounded up to whole chunks (`limits-audit.test.ts` holds the arithmetic). The other mixes, measured the same way: the worst merge of large bodies (23.8 MiB of control-character bodies and a straggler at the cap) 20.0 MiB, the worst rewrite after it 16.1, a tail seal of the smallest messages 8.4. Round 3's unstreamed move held 39.8 MiB for that merge. The sum across co-located objects and requests is the isolate-wide guard's. |
| `owner/src/segments.ts` `MOVE_RECORDS`; `owner/src/owner-do.ts` `#archiveRewrite` | 74,933 records; a rewrite pass's neighbours within `MOVE_RECORDS` kept and `MOVE_BYTES` scanned | the records one move holds; what one purge rewrite pass takes in | `MoveTable` refuses past it, loudly (a counted, told failure, never a silent cut); a pass stops taking in neighbours at its bound | technical ceiling | Review N7: a purge's rewrite scans each segment with the purge's filter, so only kept records enter the move's table, and a pass takes in a neighbour only while what it keeps and the neighbour's records fit `MOVE_RECORDS` and the neighbours it scans fit `MOVE_BYTES` (a merge's work). The compaction rule across passes: a pass stopped by its bound while still under the least places what it kept as one small segment; the next pass, finding a segment under the least that is not the only one (derived from the fences alone through the partial index `archive_segments_small`, one entry at most, so a restore keeps it), takes it back as its target and goes on taking in its neighbours, until it holds the least or stands alone. Every pass leaves the archive whole and disjoint (`archive_segments_disjoint`), with at most one segment under the least, only less compacted until the last; the first neighbour always fits, so every pass makes progress. The purges' cursors are no longer moved past the replaced segments' ids (that skipped the segments between them). A pass runs a move whenever mail is due (the hot set past half), a rewrite or not (review N8), so a long purge never holds new mail at `storage_busy`; a rewrite that fails is counted and told at three. Tested at the production segment sizes: nine in ten of a run's mail deleted (each rewrite at most its target and three neighbours, several compacting passes, Dot's mail byte-exact), and 19 in 20 (completes, what is left standing alone). Measured in workerd: the purge of most of the small-record merge's records (16,175 kept) grew the live heap by at most 14.9 MiB, within `MOVE_HOLD_BYTES`. |