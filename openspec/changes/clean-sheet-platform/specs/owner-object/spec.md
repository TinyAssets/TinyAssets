## ADDED Requirements

### Requirement: One object holds each owner's trusted state
Each owner's trusted state SHALL live in exactly one SQLite-backed Durable Object of the `OwnerDO` class, in that object's private storage. No trusted per-owner state SHALL live in a store shared between owners, and nothing in one owner's object SHALL be readable from another owner's.

#### Scenario: Two owners write the same row key
- **WHEN** Alice and Bob each write row `t` in collection `board`, each through their own object
- **THEN** each object holds only its own owner's row, and a pull in Alice's object never returns Bob's

#### Scenario: A full owner leaves another owner untouched
- **WHEN** Alice's object refuses a write because her collections are full
- **THEN** a write to Bob's object under the same bounds succeeds

### Requirement: File bytes sit in a shared bucket behind the owner's manifest
An owner's file bytes are content, not trusted state: they SHALL sit as immutable chunks in one bucket shared by all owners, reachable only through that owner's manifest and its random namespace, both held in the owner's object.

#### Scenario: Two owners store the same file
- **WHEN** Alice and Bob each write the same bytes as `/workspace/a.bin`
- **THEN** each object's manifest names its own chunks under its own namespace, and neither object holds the other's namespace

### Requirement: A large body lives in R2, its row a reference
A collection row's body, a collection history entry's and a message's body of more than `PLACE_BYTES` (2,048) UTF-8 bytes SHALL never be stored in the object's SQLite (ADR-035): it SHALL be content of the files plane in the shared bucket, its row keeping an empty body, its byte count and the content's id, and each row naming it SHALL count a reference on the content, and on its writer's files scope, by the schema's triggers. A body of `PLACE_BYTES` or less SHALL stay in its row. The bytes SHALL be put to R2 first, held by a placement upload whose chunk rows are recorded before any byte is sent and which expires in `PLACE_TTL_S`; the row's reference SHALL be committed after, in one unit with the row, so no row names bytes R2 does not hold. Placed bytes SHALL count in the files byte quota (`max_bytes`, the tier's storage), charged to the writer's scope: the owner's, an outside client's own, or a visiting owner's own, so `client_share` and `owner_reserve` hold. At the quota, the oldest placed history entries of the full scope SHALL be let go before a write is refused; mail SHALL never be let go there (ADR-036): a new message is refused `storage_full`, and the owner deletes what it chooses. Every read, a conflict's answer, a mail chain and a mail run's input among them, SHALL return the body whole, read from R2 outside any unit after a unit located it, and a waiting mail run's input SHALL keep only its mail's reference and read the body as the run starts, and a mail run's reply past `PLACE_BYTES` SHALL be placed before its settle's unit and posted in it by reference; a page SHALL hold at most `READ_PAGE_BYTES` of bodies after its first item.

#### Scenario: A large row read back
- **WHEN** a row of 8,192 bytes is written and then read by get, pull and history
- **THEN** its row keeps no body text, R2 holds its bytes under their hash, and every read returns it whole

#### Scenario: A crash between the put and the commit
- **WHEN** a body's bytes reach R2 and the object stops before the row's unit
- **THEN** no row names the content, and housekeeping releases the upload at its expiry and deletes the chunk from R2 after its grace

#### Scenario: A body past the tier's storage
- **WHEN** a large body would take the owner's file bytes past the tier's storage, or the tier's numbers are not known
- **THEN** placed history and acked mail are let go first, and only then is the write refused `storage_full`, or `tier_unread`, before any byte is sent, with nothing left

#### Scenario: A conflict on a placed row
- **WHEN** a write or delete with a stale etag meets a row whose body is placed
- **THEN** the conflict answers the stored body, as get and pull read it

#### Scenario: An outside client's large bodies
- **WHEN** an outside client writes large rows past its `client_share` of the owner's files
- **THEN** its write is refused `file_share`, and the owner's own writes still land

#### Scenario: An erasure with puts in flight
- **WHEN** the erasure's mark stands while a body's put is in flight
- **THEN** new uploads and puts are refused, the erasure waits out the put, and every chunk under the owner's namespace is deleted by prefix before and after the wipe

### Requirement: An object is named by its owner and serves only that owner
An edge Worker (the binder) SHALL reach an owner's object only by `getByName(owner)`, with the owner taken from a verified credential. The object SHALL serve a call only when the binding's owner equals the name it was reached by (`ctx.id.name`), and SHALL refuse every call when it has no name. It SHALL also store its owner on first use and refuse any call whose owner differs from the stored one. The caller SHALL reach the object only as a typed RPC argument, never as a request field.

#### Scenario: The binder's owner is the object's name
- **WHEN** the binder calls the object named `alice` with the binding owner `alice` and caller `bob`
- **THEN** the call is served as Bob on Alice's object, and `alice` is stored as the object's owner

#### Scenario: A binder bug reaches the wrong object
- **WHEN** the object named `alice` is called with the binding owner `cara`
- **THEN** the call is refused with "this object belongs to another owner" and nothing is stored

#### Scenario: An object reached by an id with no name
- **WHEN** an object reached by `newUniqueId()` is called with any binding
- **THEN** the call is refused with "this object is not named by an owner"

#### Scenario: The stored owner disagrees with the name
- **WHEN** an object's stored owner is not the name it was reached by
- **THEN** every call is refused, across an eviction

#### Scenario: A request field names the caller
- **WHEN** a request carries a `writer` field
- **THEN** it is refused as an invalid request and the binding's caller stays the only caller

### Requirement: An owner's object cannot reach another owner's object
The owner Worker's config SHALL bind nothing but the service bindings the next requirement allows, at top level and in every environment; its other keys SHALL be drawn from an allow-list with no binding key, and its flags SHALL include `disable_ctx_exports`. It SHALL be the Worker's only wrangler config. Its source SHALL never name `exports`, SHALL import from `cloudflare:workers` only the classes it extends, and SHALL keep no module-level mutable state nor write through the global object.

#### Scenario: The object looks for its own namespace
- **WHEN** code inside the object reads `ctx.exports`
- **THEN** it is undefined, so no stub to another owner's object can be made

#### Scenario: A binding is added to the config
- **WHEN** a Durable Object, var or any other binding key, or a service binding the allow-list does not name, is added at top level or inside an environment, or a second wrangler config file appears
- **THEN** the config guard fails CI


#### Scenario: Source reaches for exports or env
- **WHEN** a source reads `ctx.exports`, imports `exports` or `env` from `cloudflare:workers`, or writes through `globalThis`, `self` or `global`
- **THEN** a source guard fails CI, for every script extension under `src`

### Requirement: The owner Worker binds only Workers that reach nothing
A service binding the owner Worker holds SHALL be only to a Worker the config guard's allow-list names, together with the binding kinds and counts that Worker may hold, none of which reaches another Worker or a store shared beyond its own purpose. The guard SHALL check the bound Worker's own config, at top level and in every environment, and refuse any binding key the allow-list does not grant it.

#### Scenario: The bindings today
- **WHEN** the owner Worker's config is checked
- **THEN** it holds `FILES`, to the files Worker holding exactly one `r2_buckets` entry and declaring only its data keys' ring secret (`FILES_KEYRING`) by name, `GATEWAY`, to the model gateway holding no binding and declaring only its keyring secret by name, and `HOOKS`, to the hooks Worker binding only its own `CodemodeSandbox` class; one store, `CONTROL`, the doors' namespace of the platform's control record alone, which only `control-gate.ts` reads; and no Durable Object namespace; `SANDBOX`, the sandbox Worker the runs call (`sandbox.ts`), is unbound until slice S adds that Worker and its allow-list entry, so every run is refused `sandbox_absent` meanwhile

#### Scenario: The bound Worker gains a way on
- **WHEN** the files Worker's config gains a Durable Object namespace, a service, a queue producer, a workflow, a dispatch namespace, a Worker loader, an unsafe binding, a tail consumer, a KV namespace, a D1 database or a second bucket, at top level or in an environment
- **THEN** the config guard refuses the owner Worker's `FILES` binding

#### Scenario: The binding names another Worker
- **WHEN** `FILES` is bound to another platform Worker, even one that binds nothing
- **THEN** the config guard refuses it

#### Scenario: The gateway gains a binding or a secret
- **WHEN** the gateway Worker's config gains any binding, a route, an environment, or a secret other than `GATEWAY_KEYRING`
- **THEN** the config guard refuses the owner Worker's `GATEWAY` binding

### Requirement: The object migrates its schema on wake
The object SHALL apply every schema migration past its stored version before it serves its first call after a wake, each migration in its own transaction with its version bump. A failed migration SHALL leave the version where it was and nothing of the migration behind. A stored version newer than the code's SHALL stop the object loudly. A deployed migration SHALL never change; a new shape is a new migration at the end.

#### Scenario: First wake of a new object
- **WHEN** a new owner's object serves its first call
- **THEN** every migration has been applied and the stored version equals the number of migrations

#### Scenario: A migration fails
- **WHEN** a migration's statement fails
- **THEN** its transaction rolls back whole and the stored version is unchanged

#### Scenario: Code older than its data
- **WHEN** the stored version is newer than the code's migrations
- **THEN** the object refuses to run and every call fails with that reason

### Requirement: One revision primitive holds the board and app rooms
The board and every Tier A app room SHALL be collections on one revision primitive in the owner's object, with the rules of S34: each write a compare-and-swap on the row's etag, stamped with the collection's next seq; a lost swap answered with the row as it stands; payloads stored verbatim; a private row its writer's own; deletes and clears the owner's alone; each commit kept in the row's history.

#### Scenario: A board race
- **WHEN** two callers write the same row with the same expected etag
- **THEN** one write commits and the other is answered with the row as committed

### Requirement: The primitive's bounds read counters kept in each change's transaction
Rows SHALL be bounded by what they take as SQLite lays them out, each with its index entries and its collection's seq row, at the widest name a validator allows (`maxRowsCharge`): an outside writer, a client or a visiting owner, to the owner's `client_share` of it, and all of them together to what `owner_reserve` leaves, past which a write is refused. Placed bodies' bytes are the tier's storage, not a bound of the collections. The history SHALL be bounded in what it takes, never in rows times revisions: its entries as SQLite lays them out (`maxHistoryCharge`), an erased key's last entry among them, past which a write prunes the oldest entries first, never refused; a history read SHALL page by entries and bytes, answering `more` and a `before_revision` cursor; a key whose every entry was pruned SHALL go on past the newest revision a prune let go, so no revision repeats, and a collection left with no row SHALL give its seq row up, a new one going on past it, so no cursor goes back. Each bound SHALL read counters that every write, delete, clear and history prune updates in its own transaction, so a bound check never scans the store.

#### Scenario: The counters after any sequence
- **WHEN** any sequence of writes, refusals, conflicts, deletes, clears and history prunes has run
- **THEN** every counter equals a recount of the rows and history it covers, and the history is within its bounds

#### Scenario: A key made again after its history was pruned
- **WHEN** a row is deleted, its tombstone pruned as the oldest entry, and the row made again
- **THEN** its revision is past the tombstone's

#### Scenario: A writer past its share
- **WHEN** a caller other than the owner would hold more of the rows' charge than its share
- **THEN** the write is refused and nothing changes

#### Scenario: Keys erased and never made again
- **WHEN** many keys are written and deleted, and none is made again
- **THEN** their tombstones count in the history's charge and are pruned oldest first, and seqs and revisions never go back

### Requirement: Mail is bounded by two technical ceilings, and never deleted to make room
The ledger SHALL hold at most `maxMessages` messages hot and at most `maxUnacked` unacked messages owner-wide, whoever they are to, by trigger-kept counters; no mailbox SHALL have a count of its own. A message SHALL have one recipient, which the key holds. Past `maxUnacked` a post SHALL be refused `ledger_full`, saying too much mail is unread, naming the readers that hold the most (so the owner can act), and that reading and acking makes room, never offering an upgrade. The ceiling is owner-wide, so one sender (an outside agent, or an inbound app) can fill it and hold back everyone's mail until the readers read and ack, or the owner disables or deletes that sender. `maxUnacked` SHALL leave the hot ceiling at least one segment's records (`checkLimits`). No message SHALL ever be evicted: the `archive` lane SHALL move the oldest acked messages, from half the hot ceiling, into archive segments, and a post at the hot ceiling SHALL be refused `storage_busy` (the doors' 503 with `Retry-After`), retryable, and ring the lane; when the archive's last move was refused for the storage figure, it SHALL be the one wall, `storage_full`, with its facts, never a busy that cannot clear. Unread mail and mail in a run's hands SHALL stay hot.

#### Scenario: Too much unread mail
- **WHEN** `maxUnacked` messages wait for their readers
- **THEN** a post to any mailbox is refused `ledger_full`, and lands once a reader acks

#### Scenario: The hot ceiling
- **WHEN** the ledger holds `maxMessages` messages and acked ones wait to move
- **THEN** a post is refused `storage_busy` with a retry time, nothing is deleted, and the post lands once the lane has moved a segment

### Requirement: Acked mail moves to archive segments, found by its id
An archive segment SHALL be files-plane content (ADR-035's placement, under the owner's data key), its records each message's rows as export lines (its body copied in whatever its size, its recipients), in seq order, then a trailer line naming each record's byte range and each chain's records, then 16 digits giving the trailer's offset. The object SHALL keep one fence row a segment (its key range, its content, its trailer's range, a members bloom) and none an item. No two segments SHALL overlap in key range (the schema refuses it): an acked message whose seq falls below the archive's last key SHALL be merged into the segment its key falls in, and a rewrite left small SHALL take in its neighbour by key. A move SHALL be two units: the batch picked and its bytes placed outside (which may pass the storage figure, since the move frees what it places; a message's counted bytes include its share of its segment's trailer), then, in one unit, the batch's rows checked unchanged by their digest, deleted, the segments it replaces gone, and each new segment's fence row named, the unit refused when it would leave the stored figure above both the figure and where the move found it; a batch changed between them SHALL abort with nothing moved, and a crash SHALL leave only an upload that expires. A sealed segment SHALL hold at least `SEGMENT_LEAST_BYTES` (8 MiB) of counted storage, so the fence rows are at most `ARCHIVE_SEGMENTS_MAX`, and a tier record SHALL give at most `TIER_STORAGE_MAX` (about 0.5 TB). A message's id SHALL carry its seq under a key the object keeps (opaque without it), so the chain read and a reply SHALL find an archived message by its id through the fence and the trailer, read outside any unit; a chain page SHALL merge hot and archived messages by seq and answer an `after` cursor. Mail SHALL count in the one number at its real bytes, hot as its rows' export lines and archived as its segment.

#### Scenario: An export across a move
- **WHEN** acked mail moves while an export's generation runs, so its log names the moved rows gone
- **THEN** the export read by version 3's rule (base and log, then each segment standing at E applied after the log) is exactly the owner at E, every message once

#### Scenario: A reply to archived mail
- **WHEN** an agent replies to a message that was acked and moved to a segment
- **THEN** the reply lands with the parent's chain, and the owner's chain read returns both

### Requirement: A disabled agent holds no mail, and only the owner deletes one
Disabling an agent SHALL release its unacked mail, so it no longer holds the unread ceiling; the owner may disable any agent, and the ledger an outside caller only its own. Only the owner, in its own session, SHALL delete an agent, which purges its mail with their bodies, its cursor, wakes, rings and receipts; its archived mail SHALL be dropped from every read and export at once (`archive_purges`) and rewritten out of each segment that may hold it by the `archive` lane, a rewrite left under `SEGMENT_LEAST_BYTES` taking in the next segment. While a revoke of its callbacks is pending its row SHALL stay as a tombstone that the registry bound counts, gone when the revoke settles. The enabled-agent bound SHALL count enabled agents only.

#### Scenario: A disabled agent's unread mail
- **WHEN** an agent holding unacked mail is disabled
- **THEN** its mail no longer holds the unread ceiling, and it is kept

#### Scenario: A deleted agent's archived mail
- **WHEN** the owner deletes an agent whose mail is in segments
- **THEN** no read finds it from that moment, and the lane rewrites each segment without it, keeping every other agent's mail

### Requirement: A resend is deduplicated while its message is hot
A post with a sender's client message id and body hash the ledger holds hot SHALL return the original message, and another hash, room or parent SHALL be a conflict. The window SHALL be the message's time in the hot set: it leaves it only once every recipient has acked it, moved to a segment, after which the same client message id is a new message. No tombstone of moved ids is kept.

#### Scenario: A resend after the move
- **WHEN** a message every recipient acked has moved to a segment and its sender resends it
- **THEN** the ledger records it as a new message

#### Scenario: A retried ack
- **WHEN** a reader acks at or behind its cursor, even after its acked mail moved
- **THEN** the ack succeeds and changes nothing

### Requirement: The object's timed work is lanes on the Lifecycle job queue
The object's one schedule SHALL run on the Agents SDK Lifecycle job queue (agents 0.31.0). Each kind of timed work SHALL be a lane: its own rows, the truth, and one Lifecycle job due at the lane's next work, never in the past, and cancelled when it has none. Every door that changes a lane's rows, and the object's wake, SHALL re-derive the jobs, leaving a job already in place unwritten. A lane's run SHALL be one bounded pass that ends by setting its own job from its rows as they then stand.

#### Scenario: A door changes the rows while a lane runs
- **WHEN** a door adds earlier work to a lane while that lane's pass is in flight
- **THEN** the pass ends with the lane's job at the door's work, not at a time computed before it

#### Scenario: A lane's pass keeps failing
- **WHEN** a lane's pass fails through Lifecycle's in-process attempts
- **THEN** its job is held for a fixed backoff, a door's re-derive keeps the hold, and the next pass that succeeds clears it

### Requirement: A lane's pass is short and keeps its failures in its rows
A lane's pass SHALL keep each outside call's failure, and each row's failed step, in that row, backed off there, so a failing row neither stops the pass, nor makes it throw, nor arms the alarm in a loop; a pass SHALL throw only for a bug. It SHALL hand long work off rather than await it. A lane's job SHALL be single-flight, its id SHALL carry the schedule's prefix, and a re-derive SHALL keep it where Lifecycle moved it later unless the rows have earlier work.

#### Scenario: The files Worker is down
- **WHEN** a chunk delete to the files Worker fails during the `files` lane's pass
- **THEN** the pass completes, the chunks wait in their rows to be tried again, and no later job on the object is starved

### Requirement: A lane's pass fits its hung timeout
A lane's pass SHALL end within its job's single-flight hung timeout by wall clock, not by batch size: it SHALL take no new row once its pass budget, under the hung timeout, has passed, and each row's outside call SHALL be bounded by the rest.

#### Scenario: A pass runs long
- **WHEN** a lane's pass reaches its wall-clock budget with due rows left
- **THEN** it takes no more of them, ends before its job's hung timeout, and its job brings the rest at once

### Requirement: Only Lifecycle touches the object's alarm
Lifecycle SHALL own the object's single physical alarm, which the lanes' jobs and the erasure's share. No source SHALL set, read or clear the alarm itself, import any part of the `agents` package but `agents/lifecycle` (so the object never extends the SDK's `Agent`), or install Lifecycle's runtime `fetch` and WebSocket handlers. The object's `alarm` SHALL only hand the runtime's alarm to Lifecycle.

#### Scenario: Source sets the alarm
- **WHEN** a Worker source names `setAlarm`, `deleteAlarm`, `getAlarm`, `disableAlarms` or `deleteAll`, spells one in pieces through a string, reaches a storage or a Lifecycle by a computed name, imports any part of the `agents` package but `agents/lifecycle` (`agents/skills` and `agents/harness/pi` included), installs Lifecycle's handlers, or names a Lifecycle table
- **THEN** the alarm guard or the policy-boundary guard fails CI

#### Scenario: A binding fetches the object
- **WHEN** a binding calls the object's `fetch`
- **THEN** it fails, since the object defines no `fetch`

### Requirement: Only the wakes lane claims and settles wakes
Wakes SHALL be rows on the object's one schedule, the `wakes` lane. No RPC method SHALL claim, mark or settle one; the lane's pass SHALL, in process, with the doorbell the object constructs (`Doorbell`, `wakes.ts`). Each pass SHALL first recover lapsed claims by their own index, then, when rows are due, ask the kill switch once (the control record in `CONTROL`, by the doors' `outsideOpen`, bounded at `WAKE_SEND_MS`) and claim nothing on closed, a failure or no answer, holding its job `WAKE_RETRY_S` without touching a row; then claim due open rows. The lane SHALL have a job for open rows only while the owner is admitted.

#### Scenario: A lapsed claim behind due wakes
- **WHEN** a claim's lease has lapsed behind more due open wakes than one pass takes, and the kill switch is closed
- **THEN** one pass recovers it, as `unknown` if it was marked, claims none of the rest, and holds the lane's job a retry out

#### Scenario: The kill switch is closed
- **WHEN** the control record in `CONTROL` is missing, malformed, unreadable, `outside` off, or its maintenance gate leaves the owner out
- **THEN** the switch reads closed, no wake is claimed, and the lane's job waits `WAKE_RETRY_S`, never a busy loop

### Requirement: Each vendor's wake is a record, sent by one doorbell
Each vendor's wake mechanism SHALL be one record of `owner/wake-kinds.json`, checked whole: its delivery method, whether it pushes, who hands TinyAssets the callback (the agent's client over MCP Events, or the owner in session), the callback's URL and secret rules (a vendor's hosts pinned as data where it has them), the auth scheme (`bearer` or `standard_webhooks`), header and body templates, its body ceiling, the statuses that say its token is dead, the vendor's own limits and its citations. A record with no push (Muse) SHALL never ring: its agent catches up through `inbox_digest` and `board_digest` on its own schedule, and TinyAssets polls nothing. No code SHALL name a vendor. A wake SHALL carry identifiers, the agent's mailbox cursor, what kind of change it is (`mail`, `rooms`, `board`), the owner-wide change cursor it is after and the digests that answer it, and the change's own time, never content. A callback SHALL be refused when its host is a loopback, private, link-local or metadata address, a single label, a reserved suffix, or one of our own hosts; the request SHALL never follow a redirect, and its answer's body SHALL never be read.

#### Scenario: A board row changes
- **WHEN** the owner writes a board row that is not private, and an MCP Events agent and a `/fire` agent are subscribed to the board
- **THEN** each is sent its own record's shape, naming `board_digest` and the cursor before the change, and that digest after it returns just the row

#### Scenario: A write no one follows, or no grant reads
- **WHEN** the owner writes a room that one subscribed agent does not follow, and another follows it but its client's grant no longer reads that command center
- **THEN** neither is rung

#### Scenario: An outside client or a run writes the board
- **WHEN** a woken agent's client, or a run, writes a board row
- **THEN** no agent is rung, so no two agents wake each other without end; the change is read at their next digest

### Requirement: A doorbell's secret is sealed by the gateway
A doorbell's callback URL, auth and secret (a `/fire` token, a Grok key, a `whsec_` signing secret) SHALL be sealed by the gateway, bound to the owner and the subscription, and the object SHALL hold only the blob. Only the gateway SHALL open it, and the gateway SHALL make each send: the object hands it the blob, the previous blob while both sign, and the request without auth. A blob sealed under a retired key SHALL be resealed at the next version, and the object SHALL store it. No answer, receipt or log SHALL hold the secret.

#### Scenario: The owner pastes a `/fire` token
- **WHEN** the owner sets a Claude scheduled task's doorbell in its session
- **THEN** the gateway seals the token, the subscription row holds the blob alone, and the answer is its id

#### Scenario: A token the vendor no longer takes
- **WHEN** a ring with an owner-set token is answered with a status its record lists as a dead token (Claude's `401`)
- **THEN** the wake settles failed and the owner is sent one notice to paste a new one, not one a wake, until a new token is pasted

### Requirement: An agent holds several subscriptions, each by its derived id
An enabled agent MAY hold several doorbell subscriptions (a ChatGPT dot and a Work chat), each following the board, rooms by name, or both, at most `SUBSCRIPTIONS_PER_AGENT` an agent, counted in the schema, so no client fills another agent's room nor the owner's own; a lapsed one SHALL be let go before the count. Each SHALL be keyed by the id derived from its principal, callback URL, event name and arguments, never one a caller names; a repeat refreshes it, keeping the replaced blob signing beside it for the record's rotation window. A grant change SHALL never erase an owner-set subscription silently: it is kept, and the grant check before each ring decides whether it rings. Each subscription's outcome SHALL be its own: marked sent a wake before its byte, its answer recorded, a 429 leaving it alone owed that wake.

#### Scenario: One subscription accepts and another is rate-limited
- **WHEN** a wake's ring is answered 202 by one subscription and 429 by another
- **THEN** the agent's mail is woken, the wake waits the 429's Retry-After, and its retry rings only the 429's subscription

#### Scenario: A client fills its agent's subscriptions
- **WHEN** a client holds `SUBSCRIPTIONS_PER_AGENT` subscriptions for its agent and subscribes another
- **THEN** it is refused `subscription_limit`, and the owner's paste for another agent is stored

#### Scenario: A callback that is gone for now
- **WHEN** a ring is answered `410` or `413`
- **THEN** the wake settles failed, it is not retried, and the subscription is kept

### Requirement: A send that may have left is never replayed
The doorbell SHALL mark a wake before its first byte and recheck the agent's subscriptions and its client's grant immediately before it; a disable, unsubscribe or grant change that landed meanwhile SHALL send nothing. A transport failure after the mark SHALL settle `unknown`; a send past `WAKE_SEND_MS` SHALL be left to its lease and recover `unknown`; neither SHALL be sent again. A `429` SHALL wait the Retry-After as sent; an unreadable one, or one naming no time after now (zero, a past date), an exponential backoff from `WAKE_LEASE_S`, so a callback never keeps the object busy.

#### Scenario: A slow send
- **WHEN** a callback never answers
- **THEN** the pass moves on, the wake recovers `unknown` after its lease, and it is never sent again

#### Scenario: A door names a wake claim
- **WHEN** a binding calls a claim, mark or settle method on the object
- **THEN** the call fails because the object has no such method

### Requirement: Wake limits are owner policy
An agent's wake settings SHALL be the owner's own, stored per agent and off by default (no hourly cap, no batch window), and changed only by the owner in its own session (the owner app's `agent/wake-limits`), as a policy write: against the policy generation, which the change bumps, and receipted. A registration SHALL never carry or reset them. A cap SHALL be spaced evenly by one per-agent column, with no burst and no invented high; a changed cap SHALL count afresh.

#### Scenario: An agent registers again
- **WHEN** the owner has set an agent's limits and the agent registers again
- **THEN** its limits stay the owner's, and a registration that names a limit is refused

#### Scenario: A wake-limits change against a stale generation
- **WHEN** the owner changes an agent's limits naming a policy generation that has moved on
- **THEN** the change is refused, the agent's limits are unchanged, and a refusal receipt is left

### Requirement: The object checks the owner's session itself
An agent's register, disable, delete or file change, a policy write, a wake-limits change and a grant set (a rename included) SHALL be served only when the binding's session cookie opens a live session row in the object's own store. The object SHALL mint each session's cookie, keep only its hash, and open a session only for the owner itself from a verified sign-in at most 600 s old. A stored hash, another owner's cookie or an expired or closed session SHALL never be a proof.

#### Scenario: A session that has expired
- **WHEN** the owner writes its policy with the cookie of a session whose row has expired
- **THEN** the write is refused with "interactive owner session required" and the generation is unchanged

#### Scenario: A leaked first-party token rewrites an agent's file
- **WHEN** the owner's token, with no session, writes `agent.json` or removes a file under `/workspace/agents`
- **THEN** it is refused with "interactive owner session required", receipted, and the file is unchanged

#### Scenario: The stored hash offered as the proof
- **WHEN** a call presents the hash of the owner's live cookie as its session
- **THEN** it is refused as having no session

### Requirement: Every agent may change the agent trees unless the owner narrowed it
A change under `OWNER_TREES` (agents' `agent.json`, instructions and skills) SHALL be taken from every agent of the owner by default (founder, 2026-11-03): an outside client under a full grant, a run, and a sandbox sync. A grant the owner narrowed (`scoped`), its runs and its sandbox's sync SHALL be refused there (`AGENTS_OWNER_ONLY`). The owner's own app token SHALL change them only in the live session, receipted when refused.

#### Scenario: A default-grant agent edits another agent's agent.json
- **WHEN** an outside client under its default full grant widens `/workspace/agents/other/agent.json`
- **THEN** the write lands as that client

#### Scenario: A narrowed agent edits an agent.json
- **WHEN** the owner narrowed a client's grant to `scoped` and it, its run or its sandbox writes an agent's `agent.json`
- **THEN** it is refused `AGENTS_OWNER_ONLY` and the file is unchanged

#### Scenario: The owner's token alone renames an agent's folder
- **WHEN** the owner's first-party token, with no session, renames `/workspace/agents/main`
- **THEN** it is refused with "interactive owner session required" and the agent stands

### Requirement: A refused widening leaves a refusal receipt
A widening refused after its call reached the owner's account SHALL leave a `refusal` receipt in the policy receipts, holding the digest of what was asked and never the session proof, written after the refused change rolled back. Owner changes, agent proposals and refusals SHALL be kept and listed in buckets apart.

#### Scenario: An agent's register without the owner's session
- **WHEN** an outside client registers an agent with no live owner session
- **THEN** the call is refused, no agent is registered, and one refusal receipt is kept

### Requirement: Owner policy is one document written against its generation
The owner's policy SHALL be one validated document whose digest is rechecked on every read; a write SHALL name the generation it was made against and bump it. An agent's held proposal SHALL be applied at most once, only by the owner's write that names the card and the digest it was shown.

#### Scenario: Two writes from the same view
- **WHEN** the owner writes twice naming the same generation
- **THEN** the first commits with one receipt and the second is refused with "policy generation changed"

### Requirement: Every outside call is admitted against its client's grant
The object SHALL admit each call from an outside client against that client's grant before any door acts: an MCP client's first verified call records it `full`, an API key with no grant is refused, a revoked client is refused, and after a revoke only a sign-in made after it is admitted. Only the owner in its session SHALL change a grant; the owner's account MAY revoke one without a session.

#### Scenario: A grant revoked and granted again
- **WHEN** the owner revokes a client's grant and then grants it `full` again
- **THEN** a call from a credential signed in before the revoke is refused, and one signed in after it is admitted

#### Scenario: A scoped client at a collection door
- **WHEN** a client scoped to `read_graph` writes a board row through the owner's account
- **THEN** the write is refused with "outside client capability not granted" and a pull by it is served

### Requirement: A grant's revision is apart from its authority
A grant's revision SHALL move on every owner set and revoke, and the owner's set SHALL name it, a stale one refused. Its generation SHALL move only with its mode, the set of its scopes or its tool profile (a revoke is a mode), and stamps SHALL carry it alone, scopes read from the grant as it stands. Where this spec ends a client's work on a grant change, a grant change is a set that moves the generation, or a revoke; a set that moves only the revision SHALL end none of it.

#### Scenario: A client renamed
- **WHEN** the owner renames a client with a registered agent and an open wake
- **THEN** the generation is unchanged, its stamps still stand, and its wake is kept

#### Scenario: A rename against a stale view
- **WHEN** the owner renames a client naming a revision that has moved on
- **THEN** the set is refused with "outside grant changed" and the grant is unchanged

#### Scenario: The same scopes reordered, or a revoked grant set revoked again
- **WHEN** the owner sets a scoped grant's triples in another order, or sets a revoked grant revoked under a new name
- **THEN** the generation is unchanged, and a revoked grant keeps its revoke's time as its fence

### Requirement: A grant names its tool profile
Each outside client's grant SHALL name its tool profile (`doors/profiles.json`): `custom` until the owner names `directory` in its session; a free row SHALL name `custom`. The object SHALL answer a caller's tools (`toolProfile`): a run's, the `run` profile; a client's, its grant's, where `custom` is the owner's policy's `tools.custom` (distinct names of the MCP door's tools), or every tool when the policy names none; `code` only while the owner's policy has `hooks.codemode` on, its default. Code mode SHALL be no capability: the profile and the switch are the owner's choice of it.

#### Scenario: A new client
- **WHEN** an MCP client's first verified call records its grant
- **THEN** its grant names `custom`, and it is served every tool, `code` among them

#### Scenario: The owner names directory
- **WHEN** the owner names a client's profile `directory`
- **THEN** the grant's generation moves, and the client is served no `code`

### Requirement: A main agent's digests are held to its grant
`inboxDigest` SHALL answer, in one read, the caller's unacknowledged mail (the mailbox read from its own acknowledged cursor) and the rows of the owner's rooms (`room:<app>:<room>`) written past an owner-wide change cursor; `boardDigest` the board's. Each row SHALL be its head (`head_chars`, 512 when omitted) with its etag, seq, writer and body length, and each delete or clear of a row the caller could see SHALL be reported as that row's deletion, oldest change first, and the next cursor the page's last change when it is full, else the owner's latest, never behind the one given. Every collection write, and each delete's and clear's history entry, SHALL take the owner-wide clock's next change (`collection_clock`, which only rises), read by its own index (`INDEXED BY`, so no plan scans a collection's rows). A reader whose cursor is below a deletion the history's prune let go (`erasure_floor`) SHALL be answered `reset: true` and the latest cursor, to read its rows again whole, never left unaware of the deletion. A key's delete entry SHALL be kept while the key is made again, as every history entry is (no row's history is trimmed; the history is bounded by its bytes and pruned oldest first), so it tells anyone but that row's writer of a key made again private. A row keeps the privacy it was made with, so it turns private only by a delete, which is reported. Both SHALL need `read_graph` where a scoped grant acts, and show a private row, or its deletion, only to its writer.

#### Scenario: Nothing new
- **WHEN** an agent asks for its digest past the cursor its last digest answered, and nothing was written since
- **THEN** it gets no rows and the same cursor

#### Scenario: A deleted board row
- **WHEN** the owner deletes a board row an agent's last digest showed
- **THEN** the agent's next digest past its cursor reports that row deleted

#### Scenario: A scoped client without read_graph
- **WHEN** a client scoped to `write_graph` asks for either digest
- **THEN** it is refused with "outside client capability not granted"

### Requirement: A scoped grant names the capabilities the doors check
A scoped grant SHALL hold exact `{universe, agent, capability}` triples naming only the capabilities the object's doors check: `read_graph` and `write_graph` at the mailbox, its doorbell subscription and the collections; `propose_policy` at an agent's policy proposal; and `read_files` and `write_files` at the file doors.

#### Scenario: A scoped client proposes a policy change
- **WHEN** a client scoped to `read_graph` in its agent's command center proposes a policy change
- **THEN** the proposal is refused with "outside client capability not granted", and with `propose_policy` granted there it is held

### Requirement: An outside client's call never carries the owner's session
A call from an outside client that also carries the owner's session cookie SHALL be refused before any grant is recorded, at every door but an API key agent's registration, where the owner's browser names the key and the object re-derives its origin from the key id.

#### Scenario: An outside client presents the owner's cookie
- **WHEN** an outside client reads its mailbox with the owner's live session cookie in its binding
- **THEN** the call is refused with "an outside client's call never carries the owner's session" and no receipt or grant is written

### Requirement: Grant rows stay bounded without forgetting a fence
A grant row an agent is registered from, or that stamped work or a held registration holds, SHALL never be deleted, and a row the owner set SHALL be kept until the owner's own act forgets it. Any other row SHALL be forgotten, least recently seen first, so a new client's verified call is never refused for the bound. The owner's set rows, revoked ones included, SHALL be bounded at the owner's own grant change, which never deletes a row to make room; a revoke SHALL never be refused for a bound.

#### Scenario: A new client at the cap
- **WHEN** the free grant rows are at their bound and the owner has revoked one client
- **THEN** a new client is admitted `full`, the least recently seen free row is gone, and the revoked row is kept

### Requirement: Only the owner's own act lifts a revoke
A revoked API key's row nothing holds MAY be forgotten when the binder reports the directory deleted the key, without a session. A revoked MCP client's row SHALL be forgotten only by the owner in its session, a widening receipted per client, never by time or as a side effect of another change. Every forgotten MCP revoke SHALL move an owner-wide fence to its time, and an MCP client with no row SHALL be recorded only from a sign-in known to be after that fence.

#### Scenario: The binder forgets an old MCP revoke
- **WHEN** an MCP client signed in a month ago is revoked, and the binder asks to forget its row without a session
- **THEN** the forget is refused, the row is kept, and the client's pre-revoke credential is still refused

#### Scenario: Another client's grant at the bound
- **WHEN** the owner's set rows are at their bound, one of them an old MCP revoke, and the owner grants a different client
- **THEN** the grant is refused, naming forgetting revoked clients as the way to make room, and the revoked row is kept

#### Scenario: The owner forgets a revoked MCP client
- **WHEN** the owner, in its session, forgets a revoked MCP client
- **THEN** its row is gone, a call from a credential signed in before the revoke, or with no known sign-in time, is refused, and a call from a sign-in after it is recorded `full`

#### Scenario: A revoked key the directory deleted
- **WHEN** the binder reports that an API key whose grant is revoked has been deleted
- **THEN** its row is forgotten, and a call with that key is refused with "outside client has no grant"

### Requirement: An MCP agent registers through a card its client files
An MCP client SHALL file its agent's registration on its own verified call, so the object captures the origin the binder verified, as a held card that never changes, one pending per outside identity, held for a bounded time. The owner SHALL apply it, in its session, only by the card's id and the digest it was shown, at most once, and only while the authority the card was filed under stands. A pending card SHALL hold its client's grant row, and a grant change SHALL drop the client's cards.

#### Scenario: The owner's browser names an MCP client
- **WHEN** a call carries an MCP client's origin together with the owner's session to register an agent
- **THEN** it is refused before any grant is recorded, and no agent is registered

#### Scenario: A card applied after a revoke
- **WHEN** the owner applies a card whose client's grant was revoked after it was filed
- **THEN** the apply is refused, no agent is registered, and the card is kept


### Requirement: A grant change ends its client's wakes
A grant change SHALL suppress the client's agents' open wakes and schedule the revoke of their callbacks in the same transaction, and a claim SHALL suppress a wake whose client's grant no longer stands.

#### Scenario: A grant revoked while a wake is open
- **WHEN** an agent's client is revoked while its wake is pending and its callback subscribed
- **THEN** the wake is suppressed, its callback is revoked on the next alarm, and nothing is sent

### Requirement: A revoke is fenced by the subscription generation
Each subscribe (the agent's client's MCP Events `wakeSubscribe`, or the owner's `agentDoorbell` for a `/fire` or webhook token) SHALL store the agent's one callback in the same transaction that opens the agent's next subscription generation, which tags it. Disabling an agent, moving it to another delivery method, deleting it or changing its client's grant SHALL, when the agent has subscribed since its last revoke, erase its callbacks in that transaction and schedule a revoke carrying the newest generation, with at most one open per agent. The doorbell SHALL erase only callbacks at or before a revoke's generation, and send only to a callback past the agent's newest scheduled revoke.

#### Scenario: An agent re-enabled before its revoke runs
- **WHEN** a subscribed agent is disabled, registers again and subscribes again before the alarm runs its revoke
- **THEN** the revoke erases the old callback only, and the agent's new callback still rings

### Requirement: The owner's file manifest keeps the revision rules on file entries
Each file change SHALL be a compare-and-swap on the entry's etag (null writes unconditionally, empty only creates), stamped with the next owner-wide seq, which names the version and never repeats across a delete, a re-create or a history prune; a lost swap SHALL be answered with the entry as it stands. Paths SHALL be stored verbatim, never normalized, and SHALL never become a bucket key. Files of 4 KiB or less SHALL be kept inline in the object.

#### Scenario: A file deleted and made again
- **WHEN** the owner writes `/workspace/a`, removes it and writes it again
- **THEN** the three history rows carry three rising versions and the new etag matches no earlier one

#### Scenario: Two names Unicode treats as one
- **WHEN** `café` is written with a composed and with a decomposed `é`
- **THEN** the manifest holds two files with their own contents

### Requirement: Chunks are recorded before their bytes and collected without listing the bucket
An upload SHALL declare its file's size and record its chunks, in that size's 4 MiB plan, as held before any byte is sent. A chunk SHALL count as stored only once the files Worker has accepted its put, and a commit SHALL refuse a chunk its intent does not hold or that was never stored. A chunk nothing references or holds SHALL become garbage, deleted on the owner's one schedule after a 24 h grace, without listing the bucket; a stored one MAY revive, and one being deleted SHALL never be held.

#### Scenario: A commit before its bytes
- **WHEN** an upload is begun and committed without its chunks being sent
- **THEN** the commit is refused with "a chunk of this commit was never stored; send its bytes first", and an owner write of the same bytes later stores and reads them

#### Scenario: An upload that never commits
- **WHEN** an intent expires with a chunk it never committed
- **THEN** the chunk becomes garbage that a later upload sends again, and its key is deleted after the grace

#### Scenario: A chunk still referenced
- **WHEN** the `files` lane runs with a chunk that a kept version's content is made of
- **THEN** the chunk stays, whatever its age

### Requirement: A content id names one content
A content id SHALL name its kind (inline or chunks) and its size, and a change whose content id is already recorded with another size or other chunks SHALL fail loudly rather than reuse the recorded content.

#### Scenario: A chunk list whose hash another content holds
- **WHEN** a stored content row and a new one share an id but not a size, in either order of writing
- **THEN** the second change fails and the first content reads as it was

### Requirement: History is kept by owner policy, never dropped to make room
History SHALL keep each live path's newest `keep_versions` and its versions younger than the policy's retention up to `keep_recent` in all, dropping a version past those in the change that pushed it out, and a change to the retention policy SHALL be applied to existing history on the owner's one schedule, in bounded passes.

#### Scenario: A path written twelve times
- **WHEN** a path is written twelve times under the default policy
- **THEN** its newest ten versions are kept whatever their age, and the two older ones only for 30 days

### Requirement: Gone paths' history makes room at the quota
A change that would take history rows past `max_history`, the manifest's bytes past `max_manifest_bytes`, or a client's share of either, SHALL first prune the earliest-due rows of gone paths, which retention has already let go, until it fits: the client's own for its share, any client's for all clients', any for the owner's. It SHALL raise the feed's floor past any pruned row that was its path's newest, prune no more than the change took past the bound, and never a live path's row.

#### Scenario: Deleted paths' churn
- **WHEN** a policy of 5 entries, `keep_versions` 1, `keep_recent` 2 and `max_history` 10 holds, and eight files of unique names are each written and removed
- **THEN** every write lands, history holds the ten newest rows, and a feed reader behind the pruned rows is told to rescan

#### Scenario: A live path's version kept for its age
- **WHEN** history is at its quota and a live path's oldest kept version is the earliest due
- **THEN** a change prunes a gone path's row, and the live path keeps its version

#### Scenario: Gone long paths at the byte quota
- **WHEN** the manifest is at `max_manifest_bytes` with ten deleted 3,000-byte paths' history kept, and five new 3,000-byte files are written
- **THEN** each write lands after pruning the earliest gone rows, the manifest stays within its quota, and once no gone row is left a write is refused `file_quota`

### Requirement: Every entry holds its content
Each file entry SHALL hold a reference to its content, in every root, as each kept history row does, so content any entry or kept row names is never collected. A rename SHALL record its targets before it drops its sources.

#### Scenario: A file renamed out of the temporary area
- **WHEN** a file written under `/.ta/tmp` is renamed into `/workspace`, and housekeeping then collects every due chunk
- **THEN** the file reads back byte for byte

#### Scenario: Live files' churn inside the window
- **WHEN** ten files of one kept version each, with `keep_recent` 1 and `max_history` 10, are each written ten times inside the retention window
- **THEN** every write lands and history holds ten rows

### Requirement: The owner frees history, and temporary files keep none
A version MAY be restored as a new one, or have its content redacted unless it is current; a redacted version's bytes SHALL stay while another entry or row holds the same content. The owner alone MAY purge a path's history but its current version. Files under `/.ta/tmp` SHALL keep no history and expire after a day.

#### Scenario: A purge at any time
- **WHEN** the owner purges a path's history
- **THEN** every version but its current one goes, never refused for a quota, and a gone path's history goes whole

### Requirement: File quotas are owner policy, refused loudly
Chunk, inline and manifest bytes, entries, history rows, open uploads and their chunks, and a file's size SHALL each be bounded by owner policy or a fixed ceiling, read from counters the schema's triggers keep. A change past a bound SHALL be refused in its own unit before it commits (an upload before any byte is sent), leaving nothing, its reason starting with a fixed code: `storage_full` for the owner's whole storage (the one wall, below), `file_quota` for another owner's bound, `file_share` for a client's share.

#### Scenario: The owner's entries at their quota
- **WHEN** a write would add an entry past `max_entries`
- **THEN** it is refused `file_quota`, and no parent directory, history row or content of it is kept

### Requirement: The tier's storage is the one wall, in one set of words
Every refusal at the owner's storage figure (the tier's, from the control record, or the owner's own lower setting) SHALL start with the fixed code `storage_full`, then say in plain words what is used (the bytes stored, never the refused change), what the change needs, what is free, the whole, how to free space, and the upgrade path: the next tier's storage, or that the owner's own lower limit is what binds (never an upgrade then); with no larger tier, or none known, it SHALL say nothing of plans. Then the same facts as one JSON object (`used_bytes`, `needed_bytes`, `held_bytes`, `free_bytes`, `limit_bytes`, `tier`, `tier_bytes`, `own_limit`, `upgrade`, `free`) the app reads. The doors SHALL answer it 507. Bytes held by uploads still open (counted from their begin) SHALL be reported apart from what is stored; when they alone leave no room, the refusal SHALL be `uploads_open` (409 at the doors), saying the storage itself has room, never "full" and never an upgrade. An upload the sync agent gives up SHALL be aborted at once (the `upload_abort` sync door). The uploads a sync agent begins SHALL name their lease, and those SHALL be aborted when the lease is released, lapses, or is deleted as a new lease is taken (on every path that takes one, a sign-in's among them), so none holds room for its day; a client's own uploads and placed bodies, begun outside any sync, SHALL never be cancelled by its sandbox's lease ending. A change that adds no storage (a read, a delete, a purge) SHALL never be refused at it. An outside client's share of the storage keeps `file_share`, and the index's own ceilings `file_quota`: neither is the user's storage being full.

#### Scenario: Full on the free tier
- **WHEN** a write would take the owner's storage past the free tier's figure
- **THEN** it is refused `storage_full`, naming what is used, the figure, what to free and the paid tier's storage as the upgrade, and the owner can still read and delete

#### Scenario: Room held only by an open upload
- **WHEN** what is stored and a new upload would fit, but an upload still open holds the rest
- **THEN** the new upload is refused `uploads_open`, naming the held bytes apart from the stored, with no upgrade; and once the sync agent aborts the open upload, or its lease ends, the room is free

#### Scenario: A client's sandbox lease ends while its own write is open
- **WHEN** an outside client's large file write is open and its sandbox's lease is released
- **THEN** only the uploads that sandbox's sync began are aborted, and the client's own write still commits

### Requirement: Every manifest row is bounded in bytes as the object stores it
Every manifest row (entry, history row, content with its inline bytes and chunk list, chunk, temporary directory, share row, scope reference) SHALL be charged, by triggers only, the page bytes of each b-tree record and index entry it makes, its text in bytes and its overflow pages included, against `max_manifest_bytes`. The charge SHALL bound the database's growth within the guard's worst-case fill factor, `WORST_FILL`, for every path length (up to 4,096 bytes).

#### Scenario: The other file bounds inside the manifest's
- **WHEN** the files policy is at its defaults, or every bound at its high
- **THEN** `max_entries` small files at one version, `max_history` versions, and `max_inline_bytes` each fit inside `max_manifest_bytes`, so each can bind

#### Scenario: A client writing 4,096-byte paths
- **WHEN** an outside client writes file after file at 4,096-byte paths
- **THEN** each is charged its three spilled copies' overflow pages, and the client is refused `file_share` at its share while the owner still writes

#### Scenario: A database filled, thinned and filled again
- **WHEN** files are written to a charge, every other one removed and purged, and the charge refilled, round after round
- **THEN** the database grows by more than the charge as pages stay half used, and never by more than the charge times `WORST_FILL`

### Requirement: An outside client holds a share of every file quota
Each outside client SHALL hold at most `client_share` percent of every file quota, open uploads included, and all clients together at most what `owner_reserve` percent leaves, both owner policy, so the owner always has room of its own.

#### Scenario: A client fills its share
- **WHEN** an outside client holding `write_files` writes until it is refused `file_share`
- **THEN** the owner's writes, grant changes, revokes and policy writes all still land

#### Scenario: Two clients together
- **WHEN** `client_share` is 40 and `owner_reserve` 50 of ten entries, and two clients write four and one files
- **THEN** a fifth file of the first, or a second of the other, is refused `file_share`, and the owner writes five more

### Requirement: Each scope is charged once for what its own rows reference
Each scope (an outside client's own, all clients', the owner's writers') SHALL be charged its own entries, history rows and temporary directories, and once for each content and chunk its rows or open uploads reference, while any do, deduplicated within the scope and never across scopes; a chunk no scope references SHALL be charged to the scope that let it go last until it is deleted or revived. The owner-wide total SHALL count each stored row once.

#### Scenario: A client copies the owner's file
- **WHEN** the owner writes a file, a client writes the same bytes, and the owner removes and purges its own
- **THEN** the client's share holds the bytes and the owner's scope is credited, while the object stores them once

### Requirement: An upload proves the bytes its writer does not hold
An upload SHALL be told a chunk need not be sent only when the chunk is stored and the writer's own scope holds it through its contents (or let it go last as garbage); a commit SHALL be refused unless the upload sent every other chunk, or the writer's scope holds it by then, so no upload reveals or claims bytes another scope stored.

#### Scenario: A client names a chunk only the owner holds
- **WHEN** a client begins an upload of a chunk the owner stored and commits without sending it, by one upload or two
- **THEN** each commit is refused, and once the client sends the bytes it commits

### Requirement: The owner's database is bounded under half its limit
Every owner table SHALL be bounded by a count or bytes the code enforces, with no exception, and a guard SHALL compute the worst case from those bounds and the schema, at the policy's high bounds, each row laid out as SQLite lays it at a thinned page's worst fill (`WORST_FILL`), and hold it under half the platform's 10 GB. No agent's records live in the owner's database (ADR-034).

#### Scenario: A table no bound covers
- **WHEN** a migration makes a table the guard has no term for, or a manifest table's charging trigger is missing
- **THEN** the owner-bound guard fails

### Requirement: A quota never blocks a change that frees
A change that only removes or frees (a remove, a redaction, a purge, an aborted upload, housekeeping) SHALL never be refused by a quota. A policy SHALL be refused unless `max_entries` times the larger of `keep_versions` and `keep_recent` is at most `max_history`, so no churn of live files can fill it, and gone paths' churn makes its own room.

#### Scenario: A full owner removes files
- **WHEN** every counter is at its bound and the owner removes a file
- **THEN** the remove lands

### Requirement: The sync lease fences the sandbox's commits
Each sandbox lease SHALL be one authority's (the owner's own work's, or one outside client's): a random lease id and a rising epoch, held by one holder until it lapses or is released, bound to the SHA-256 of its egress, sync and runner tokens' secrets (never the tokens), their deadline, and the egress generation its sandbox's network was last applied under, none until the sandbox slice applies one. The owner's shared abilities (`/workspace/.agents`, `/workspace/skills` and `/workspace/prompts`) are every one of the owner's agents', and SHALL be visible read-only to every run of them, a client's included. A client's sandbox SHALL always read its live runs' agents' own definitions (`/workspace/agents/<agent>` and the abilities' trees), read-only, and the rest of the owner's files only while that client's grant, at its live run's stamp, holds `read_files`; it SHALL commit only while it holds `write_files`, written as that client, and into the owner's agent trees (`OWNER_TREES`) only while its grant is full, the default, never a narrowed one. When a client's sandbox's readable set changes (a new agent's run, a widened file scope), its next feed read SHALL answer `reset: true`; any content door the sync agent reaches SHALL apply the same filter as the feed. No door SHALL reach the lease: the object's own sandbox lifecycle (`SandboxLeases`) opens, renews and closes it. A sync commit SHALL be refused whole only when the lease id or epoch is not the live lease's or its op list is empty or too long; each op SHALL otherwise land or be refused on its own, its quota checked alone, and be answered per op. API writes SHALL never take the lease.

#### Scenario: A sandbox that lost its lease
- **WHEN** a sync commit carries an earlier epoch or another lease id
- **THEN** it is refused and nothing changes

### Requirement: A sync conflict keeps both sides
A sync op made against a version that has moved on SHALL leave the current version in place, save a write's content as a create-only copy named `<path>.ta-conflict-<epoch>` (with `-<n>` for the first free name, cut to 255 bytes), and report the conflict; a write under a parent the API made a file SHALL be saved under the parent's copy name. A directory SHALL be removed by sync only once empty.

#### Scenario: A sandbox and the API edit one file
- **WHEN** the API writes `notes.md` after the sandbox last synced it, and the sandbox then commits its own edit
- **THEN** `notes.md` keeps the API's content and the sandbox's is saved as `notes.md.ta-conflict-<epoch>`, reported in the answer

#### Scenario: A copy's name is taken
- **WHEN** a file already sits at `notes.md.ta-conflict-<epoch>`
- **THEN** the sandbox's content is saved as `notes.md.ta-conflict-<epoch>-2` and the other file stays

### Requirement: The files Worker moves bytes and never sees a path
The files Worker SHALL hold the platform's only bucket binding and bind nothing else, serve no URL, and take only hex namespaces, chunk hashes and bytes, moving at most two chunks' bytes a call. Its keys SHALL be only `o/<ns>/c/<sha256>` and the nightly manifest's `o/<ns>/m/<day>[/<part>]`, built by one module. A put SHALL be create-once and verified by R2's SHA-256 check; an existing key SHALL count as stored only when it holds those bytes, read back under the owner's key.

#### Scenario: Bytes that miss their hash
- **WHEN** a put names a hash its bytes do not have
- **THEN** it is refused and nothing is stored

### Requirement: The binder may stream an owner's chunks
The binder is the trust root: the HTTP door's binder MAY read an owner's namespace and wrapped data key from that owner's object and stream chunks to the files Worker itself, since a binder already reaches every owner's object; no owner's object SHALL hand out another owner's namespace.

#### Scenario: The binder streams a large read
- **WHEN** the binder serves Alice's large file
- **THEN** it reads Alice's namespace from Alice's object and the chunk keys it asks for are under that namespace only

### Requirement: The platform counts the object's calls
The object SHALL keep no call budget of its own: the platform counts subrequests per invocation, charged to the object's newest incoming event or to a count each event resets (the cap probe's M12 found one of the two and could not tell them apart), and its own error SHALL be the failure a caller sees (a tool call's error result). The owner Worker's config SHALL set `limits.subrequests` to 10,000, and the config guard SHALL refuse any other value, or none.

#### Scenario: The platform refuses a call
- **WHEN** the files Worker's call throws the platform's subrequest error inside a tool call
- **THEN** the tool's result is that error, and nothing was written

### Requirement: Every owner write is a synchronous gate unit
Every owner write SHALL run as a unit of the object's write gate: one synchronous `transactionSync`, run at once, which reads what it decides on itself and spans no outside call. No agent loop SHALL commit in the owner's database: agents run on unmodified engines (ADR-034). A door's synchronous body SHALL be one unit; a door or lane that crosses to an outside call (`FILES`, the gateway) SHALL write each step around it as a unit of its own. A unit SHALL keep its own atomicity, and SHALL never be an async storage transaction, which would hold the input gate. Reads stay direct.

#### Scenario: A door writes while another awaits FILES
- **WHEN** a door is called while another door awaits the files Worker
- **THEN** the door's unit runs at once and is kept, and the other's later step lands in a unit of its own

### Requirement: No write reaches the storage past the gate
The object's modules SHALL see the storage only through the gate's view, which SHALL refuse, loudly, any statement but a single read and any `transactionSync` outside a unit; the gate SHALL refuse a unit inside another and a unit that returns a promise.

#### Scenario: A write outside the gate
- **WHEN** any of the object's code writes through the view outside a unit
- **THEN** the write is refused before it runs

### Requirement: A unit acts only on what it reads itself
A unit SHALL read the rows it decides on, or re-check them in its own statements, since another unit may land between any two; an authority a door checked SHALL be checked again in each unit that writes under it.

#### Scenario: An upload holds a chunk the collector found due
- **WHEN** an upload's begin is queued while a collection pass waits on the queue to mark a due chunk for deletion, and the upload then commits
- **THEN** the pass's unit reads the chunk held again and leaves it, and the committed file stays readable

#### Scenario: A grant narrows while a write waits
- **WHEN** an outside client's whole write has passed its door's check and its grant loses `write_files` before the write's unit runs
- **THEN** the write is refused and nothing is written

### Requirement: A model connection holds only a blob the gateway sealed
The object SHALL hold a model connection's credential only as the blob the gateway sealed for that owner, connection and version, never a plaintext key or token: the binder hands a pasted key to the gateway's `seal` and an OAuth code to its `exchange`, and `connectionCommit` takes the blob, its kid, the models the gateway's setup check listed with their metadata, the egress scope it listed (the sealed base URL and endpoints, within 16 KiB, required but for a failed setup), and an OAuth access expiry in clear.

#### Scenario: A pasted key is connected and used
- **WHEN** the owner connects a key and the sandbox's calls use it through the egress door
- **THEN** no table of the object, no call it makes to the gateway and no line it logs holds the key

### Requirement: A connection keeps up to 1,024 models
A connection SHALL keep up to 1,024 models, each its whole pi-ai record as the setup check listed it and checked whole at commit, within 640 KiB of JSON, so 1,024 of the largest built-in record (607 bytes in pi-ai 1.4.0) fit, and the largest built-in catalog (386 models) with room.

#### Scenario: The largest built-in catalog
- **WHEN** a key is connected for the built-in provider whose catalog is the largest
- **THEN** the connection is active with every model the setup check listed

### Requirement: The connection doors are the owner's, in its live session
Preparing (with an OAuth flow's beginning), committing, revoking and listing connections and finishing their OAuth flows SHALL be the owner's own doors, in its live session; a refused one leaves a refusal receipt.

#### Scenario: An outside client tries to connect a source
- **WHEN** an outside client's call, or the owner's call without a live session, reaches a connection door
- **THEN** it is refused, and a refusal receipt is kept

### Requirement: A connection OAuth flow is bound to the session that began it
`connectionPrepare` of an OAuth connection SHALL begin its flow in the same unit, at its redirect URI: a fresh state's SHA-256 and a fresh PKCE verifier, bound to the session, for 10 minutes, at most 10 open. `flowFinish` SHALL hand back the verifier and the source record id for that state, in that session, once. Signing a session out SHALL cancel the flows it began, and account revocation (`sessionCloseAll`) SHALL cancel every open flow, each in the unit that signs out.

#### Scenario: A flow past the open limit
- **WHEN** the owner begins an eleventh open flow
- **THEN** it is refused and no pending connection is left behind

#### Scenario: A callback after account revocation
- **WHEN** the owner's sessions are all signed out while a flow is open, and its callback then arrives in a new session
- **THEN** the flow is not found, and nothing is exchanged

### Requirement: A revoke deletes the credential and asks the provider to revoke it
A connection revoke SHALL make the connection unusable in its own unit. A revocable credential's blob, the only copy of the tokens the provider must revoke, SHALL be kept unusable until the `connections` lane's RFC 7009 revoke through the gateway succeeds, then deleted; after 5 failed attempts it SHALL be deleted anyway, with an owner notice. Any other blob SHALL be deleted in the revoke's unit, one the provider issued with an owner notice, and no provider revoke claimed.

#### Scenario: A key-exchange connection is revoked
- **WHEN** the owner revokes a connection whose source cannot be asked to revoke
- **THEN** the answer says no provider revoke, the blob is deleted at once, and a notice tells the owner to delete the key at the provider

### Requirement: A connection knows whether its provider can revoke it
A connection SHALL record at prepare whether its provider can be asked to revoke what it issues (`revocable`, from its source record; never for a pasted key), and its view SHALL say so.

#### Scenario: A pasted key claims to be revocable
- **WHEN** a pasted key's connection is prepared as revocable
- **THEN** it is refused

### Requirement: A credential the provider issued is never dropped silently
A credential the provider issued whose setup check then failed SHALL be committed `failed`: the connection SHALL be in `error`, a revocable blob kept for the lane's provider revoke, an unrevocable one never stored, with an owner notice to delete it at the provider.

#### Scenario: The setup check times out after an exchange
- **WHEN** the gateway exchanged a code and its setup check then failed
- **THEN** the connection is in `error`, no call can use it, and the credential is revoked at the provider or the owner is told to delete it

#### Scenario: An OAuth connection is revoked
- **WHEN** the owner revokes an OAuth connection
- **THEN** no call can use it from that unit on, the lane sends the gateway's provider revoke with the blob, and the blob is then deleted

### Requirement: Every token a revoke leaves is revoked or noticed
A source with no revocation endpoint SHALL have its blob deleted at the lane's first pass, with a notice that the provider honours the token until it expires. Tokens a refresh stored after the revoke SHALL be kept for, and revoked by, the lane.

#### Scenario: A source with no revocation endpoint
- **WHEN** the owner revokes an OAuth connection whose source names no revocation endpoint
- **THEN** its blob is deleted at the lane's next pass, with that notice

#### Scenario: A refresh lands after the revoke
- **WHEN** a refresh in flight when the owner revoked stores rotated tokens, before or after the lane revoked the old ones
- **THEN** the lane revokes the rotated tokens too, and no blob is left

### Requirement: An expiring OAuth credential is refreshed once, under a lease
Before a model call, an OAuth access token expiring within 3 minutes SHALL be refreshed through the gateway by one caller holding the connection's refresh lease, which every concurrent caller waits on, and the refreshed blob SHALL be stored by compare-and-swap at the next version. A refresh the provider refuses SHALL fail every waiting call loudly, and a refused grant SHALL fail the connection until the owner reconnects.

#### Scenario: Concurrent calls on an expiring token
- **WHEN** four calls start together on an OAuth connection whose token expires within the margin
- **THEN** the gateway is asked to refresh once, and every call carries the refreshed token

### Requirement: A resealed blob is stored with no lost update
A blob the gateway resealed under its active kid SHALL be stored by compare-and-swap on the connection and its version, never while a refresh holds the lease, so a reseal of older tokens never overwrites newer ones.

#### Scenario: A refresh lands while an older call is in flight
- **WHEN** a call made on version 1 under an older kid ends after a refresh stored version 2
- **THEN** version 2's tokens stay, and the call's reseal is dropped

### Requirement: Every model call is reserved against the owner's model policy
Each model call the object reserves (`usage.ts`; the runs slice keys it to a run) SHALL be reserved in one unit that reads the owner policy's generation and `models` ceilings: the owner's and the source's daily and monthly cost, each counting what is settled and what is still open. The reservation SHALL bound the call's output tokens. A source's ceilings SHALL be keyed by its source record, so a reconnect keeps them.

#### Scenario: Concurrent reserves at a ceiling
- **WHEN** six calls reserve together against a daily ceiling that holds two
- **THEN** two are reserved and four refused, each refusal kept as a receipt

### Requirement: A call is estimated by pi-ai's calculateCost and settled at the gateway's price
A call's reservation SHALL be priced by pi-ai's `calculateCost` over the target model's whole cost record, so a long-context tier prices the whole request once its estimated input passes the tier's threshold, and the most output the cost room takes SHALL be found in about log2 of its bound. A settled call SHALL keep the cost the gateway's pi-ai adapter reported, never one recomputed from the record stored at connect time.

#### Scenario: A long-context tier
- **WHEN** a call's estimated and billed input pass its model's tier threshold
- **THEN** its estimate and reserved output are at the tier's rates, and it settles at the tier's price the gateway reported

#### Scenario: A stored record older than the gateway's catalog
- **WHEN** a connection's stored record prices a model at nothing and the gateway's catalog does not
- **THEN** the call settles at the gateway's price, and its spend counts against the caps

### Requirement: The caps read counters, never receipts
Every ceiling SHALL read only counters the schema's triggers write: `model_spend` per source, day and month, which the sandbox's egress charges move too. Receipts SHALL be history only, bounded without freeing any ceiling.

#### Scenario: Receipts pruned
- **WHEN** six calls settle while only the newest three receipts are kept
- **THEN** the day's and the month's counters hold all six calls' cost

### Requirement: A reservation is dispatched and settled once, in its own units
A reservation SHALL be dispatched only while still reserved, at the same policy generation, and not refused by its caller; and settled once: `succeeded` from pi-ai's usage, `failed` when the call was refused before any output, `unknown` otherwise, which keeps its whole reservation. A dispatch or settle that cannot be kept SHALL settle the row as it stands (`not_sent` or `unknown`) in the same instance.

#### Scenario: The policy moved before the dispatch
- **WHEN** the owner's policy moves between a call's reserve and its dispatch
- **THEN** the call is `not_sent`, and nothing is sent

#### Scenario: A provider's refusal before any output
- **WHEN** a dispatched call settles `failed`
- **THEN** it holds nothing, and no ceiling is spent

### Requirement: A dead instance's calls are settled by the next instance
An open reservation the live instance did not make SHALL be settled by the next reserve or receipts read: `reserved` as `not_sent`, `dispatched` as `unknown`.

#### Scenario: The object dies mid-call
- **WHEN** a new instance finds a call dispatched by the instance before it
- **THEN** it settles it `unknown`, and its reservation still counts against the ceilings

### Requirement: A recurrence's cron is ours and bounded
A recurrence SHALL be the five classic cron fields in UTC, numbers only (no names, `?`, `L`, `W` or `#`), each value in its field's range, a field `*`, a value, a range, a step (`*/n`, `a-b/n`, `a/n`) or a list of at most 60 of them, the whole at most 256 characters; when both day fields are restricted a day SHALL match if either does. Consecutive occurrences SHALL be at least a minute apart (the tier's simultaneous runs bound what a schedule starts), and an expression SHALL have an occurrence within eight years, else it is refused when parsed. The next occurrence SHALL be the first matching whole minute strictly after the time, found a day at a time. The runs slice (R) schedules a run by it.

#### Scenario: Every minute
- **WHEN** an expression runs every minute, as `* * * * *`
- **THEN** it is accepted, its sixty minutes each an occurrence

#### Scenario: Both day fields restricted
- **WHEN** `0 9 13 * 5` is asked its next occurrence from 2026-11-01
- **THEN** it answers 2026-11-06 09:00 (a Friday), and the 13th after that

#### Scenario: An expression that never matches
- **WHEN** `0 0 30 2 *` is parsed
- **THEN** it is refused, having no occurrence within eight years

### Requirement: `auto` is the owner's strongest connected model
The owner's connected models SHALL be ranked by the data file `model-ranking.json`: the earliest entry a model matches ranks it, newer ids first within an entry; a model no entry matches comes after, by its catalog output price, highest first; then the older connection, then the id. With nothing connected nothing SHALL rank: there is no platform-paid default. The runs slice (R) picks a run's source from the ranking.

#### Scenario: Two connections of one model
- **WHEN** two connections list the same ranked model
- **THEN** the older connection's ranks first

#### Scenario: Nothing connected
- **WHEN** the owner has no active connection, or one with no models
- **THEN** the ranking is empty

### Requirement: The owner's notices are bounded and the owner's alone
What the owner's work reports that no call fails with (a connection's setup check that failed, a provider revoke that failed or is unsupported) SHALL be kept as a notice, its type and a detail cut to 1,024 characters, only the newest 100 kept, each added in one unit, and read by the owner alone.

#### Scenario: A notice that repeats
- **WHEN** more than 100 notices are added
- **THEN** only the newest 100 are kept, newest first

### Requirement: The agent trees are held by the grant, not by who wrote them
A change under `/workspace/agents`, `/workspace/.agents`, `/workspace/skills`, `/workspace/prompts` (its agents' files and skills, which every engine reads) or `/workspace/goals` (the owner's goals, whose Agreed section is what its agents may do toward each unasked) SHALL be held at every file door by the rule "Every agent may change the agent trees unless the owner narrowed it": refused only to a narrowed grant, and to the owner's app token outside its session.

#### Scenario: A narrowed client renames a skill
- **WHEN** a client whose grant the owner narrowed renames a file under `/workspace/skills`
- **THEN** it is refused `AGENTS_OWNER_ONLY`

#### Scenario: A goal under a narrowed grant
- **WHEN** a client under the default (full) grant writes `/workspace/goals/a-goal.md`, and then the owner narrows its grant and it writes, moves or removes a goal
- **THEN** the first write lands as that client's, each later change is refused `AGENTS_OWNER_ONLY` and the goal is unchanged, and the owner in its session still writes it

### Requirement: Erasing the owner ends every run before the owner is wiped
Erasing the owner SHALL mark it erasing, after which every door refuses and no lane runs; end every run (the sandbox's in-flight egress streams, the owner app's sockets, and each live run's cancel through the sandbox; the sandbox slice adds the sandbox's kill and the session files' purge), recorded as the erasure's second step; wait for the doors' confirmation of the outside record (the erasure queue, sent by the erasure's own job at the mark and at each attempt until confirmed); delete every chunk its rows name from R2, its files' and its placed bodies' (ADR-035), and every generation (each root a copy of the owner's wrapped data key), repeatable; then `deleteAll()` the owner and leave one `erased` admission row, the tombstone, in the wiped database. Each step SHALL resume after a crash from the step the mark records. An object holding the tombstone SHALL migrate nothing, start no Lifecycle and refuse every door.

#### Scenario: Erasure is complete past the platform's recovery window
- **WHEN** the owner is erased
- **THEN** the account-erase answer names `complete_at`, the wipe plus 30 days: a Durable Object's point-in-time recovery can restore its database, and with it the wrapped data key, for 30 days, so crypto-erasure is complete only past that window

#### Scenario: A crash mid-erasure
- **WHEN** the object dies after the mark, or after every run ended, or after Lifecycle's alarm was disabled, or its wipe failed
- **THEN** the doors still refuse, and the next start finishes it: the owner deleted, its tombstone left

#### Scenario: The R2 copies
- **WHEN** an owner holding a file and a placed row body is erased
- **THEN** R2 holds neither chunk

#### Scenario: A stale getByName
- **WHEN** a binder reaches an erased owner's object again, across evictions
- **THEN** every door is refused `owner_erased`, and the database still holds only the tombstone, with no alarm

### Requirement: The owner's admission is the object's own row
The object SHALL hold one admission row: pending, admitted, suspended for billing and or abuse, or the erased tombstone. A fresh object SHALL be pending and refuse every door and lane until the platform's explicit admit (`admissionSet {admit: true}`), which is idempotent and lifts no suspension; a pending owner SHALL never be suspended. Every door SHALL declare a class: billing refuses `work`, abuse every door. Every sandbox lease, egress call, authority check and wake claim SHALL be refused while not admitted; a suspension SHALL end the sandbox's in-flight egress streams. Only the platform's reserved caller SHALL set it, at no other door.

#### Scenario: A billing suspension
- **WHEN** the platform suspends an owner for billing
- **THEN** work doors refuse `owner_suspended` while reads and revokes are served, and lifting the suspension serves them all again

#### Scenario: An abuse suspension
- **WHEN** the platform suspends an owner for abuse
- **THEN** every door refuses `owner_suspended`

#### Scenario: Another caller sets admission
- **WHEN** the owner, an outside client or a session sends `admissionSet`, or the platform's caller calls any other door
- **THEN** each is refused, and the admission is unchanged

### Requirement: The hooks Worker reaches no owner
The `tinyassets-hooks` Worker SHALL bind the namespace of its own `CodemodeSandbox` class only (no Worker Loader), serve no URL, and never use its objects' storage. The owner config guard SHALL admit `HOOKS` only to such a Worker. User hooks are the engine's own, run in the sandbox (ADR-034 decision 11), never here.

#### Scenario: The hooks Worker gains a binding
- **WHEN** the hooks Worker's config binds `OWNER`, a bucket, a Worker Loader or another Worker's class
- **THEN** the owner config guard refuses the owner's `HOOKS` binding

### Requirement: Code mode's VM runs in the hooks Worker
Code mode's executor SHALL run a script by pi-codemode's `RemoteCodemodeSandbox` through the `HOOKS` binding's `codemodeExecute`, which hands out no stub and refuses an execution with no name, an empty one or one past 512 characters, each execution named by its owner and a random id; a script's `tools.*` SHALL be the tools its caller hands it, each held by the caller to its own grant; with no hooks Worker bound it SHALL fail loudly. The MCP door's `code` tool SHALL reach it only through `codeRun`, which SHALL refuse (`code_mode_off`) once the owner's policy turns `hooks.codemode` off (on by default), and (`tool_not_served`) unless the caller's profile holds `code`. A script's tools SHALL be the caller's profile's other tools, each call that tool's own door with the caller's binding, admitted again and held to the caller's profile, the switch, and the control record's gates the door read (the kill switch for an outside client, the maintenance gate for a run or the owner; one read serving at most a second of calls) as they stand at that call. A script SHALL end `timeout` past `CODEMODE_TIMEOUT_MS` of wall time, checked at each tool call as well as by pi-codemode's timer, or past the hooks Worker's interrupt budget of compute; its answer counts its calls by outcome and lists the calls of tools that change something in order (`landed`, a run of one tool and outcome as one entry), and an answer past `CODE_ANSWER_MAX` (31 MiB as V8 serializes it: a string two bytes a unit when any unit is past Latin-1; within Workers RPC's 32 MiB) SHALL be refused `answer_too_large`, its value and output dropped, its calls kept.

#### Scenario: Code mode off
- **WHEN** an outside client whose profile holds `code` calls `codeRun` after the owner's policy turns code mode off
- **THEN** it is refused `code_mode_off`, and its tools list no `code`

#### Scenario: Outside access switched off mid-script
- **WHEN** the control record turns outside access off while an outside client's script runs
- **THEN** the script's next call is refused "outside access is switched off", and what landed before it is listed

#### Scenario: An answer past the RPC's ceiling
- **WHEN** a script's value and output come to more than `CODE_ANSWER_MAX` as V8 serializes them, mostly ASCII strings each with one wide character among them
- **THEN** it is answered `answer_too_large`, with its calls and what landed

#### Scenario: A tool outside the profile
- **WHEN** a script reads `tools.file_usage` and the caller's profile does not hold it
- **THEN** the script gets a `TypeError`, and no door is called

#### Scenario: A script's tool calls
- **WHEN** a script calls two of the tools its caller handed it
- **THEN** each call is an RPC back through `exchange`, and the VM runs in the hooks Worker

#### Scenario: No hooks Worker
- **WHEN** a script is run with no hooks Worker bound
- **THEN** it fails, and nothing of it runs in the owner's object

#### Scenario: An execution with no name
- **WHEN** the hooks Worker is asked to run an execution named `""`, or by 513 characters, or by no string
- **THEN** it refuses "a code mode execution is named" before any sandbox is reached

### Requirement: Each sandbox egress call is admitted and charged at open
`egressOpen` SHALL take only the egress token's binding (its secret's hash, the egress generation its transform carried), checked to be the binder's exact shape before anything is written, and refused for an owner whose object never opened a lease, writing no row; and admit a call for `work`, in one unit, by these checks in order, each refusal a fixed code kept as a receipt row (the newest kept, apart from the admitted calls' receipts): the live lease by the egress hash (`egress_lease_closed`), checked before anything else, an OAuth refresh among it (of the calling run's own connection alone); the run the call's path names is live under that lease (`egress_run_closed`); the connection is the one that run's grant holds (`egress_run_connection`), so a run never spends on another simultaneous run's connection and a run on the vendor's own sign-in may call none; the call's egress generation is the current one (`egress_policy_stale`) and the lease's network was applied under it (`egress_network_stale`); the connection is the owner's, active, its credential usable and its scope stored (`egress_connection_refused`); the method and path are among its sealed endpoints, by the gateway's own rule (`egress_endpoint_refused`); a read (`GET`, `HEAD`) is free, as is a call its API's rules cite as free with the provider's page that says so (Anthropic's token count), and any other call is pi-ai's metered model call (`egress_unmetered`), with a declared body length (`egress_length_required`); at most 16 open for each of the tier's simultaneous runs (`egress_rate`); and a model call's charge (`egress_spend_refused`, the binding cap its rule): against its run's requests, tokens and cost (its `runs` row; the model policy's per-turn limits are its caps, each off until the owner sets it), and the owner's and the source's day and month (the spend counters the model reservations move too, pruned past their window at each open): the model policy's caps, no more and no less; reserved for the model the call names (as the door read it, or its path; refused `egress_unknown_model` when the connection does not list it), at that model's dearest rates by pi-ai's `calculateCost` over the catalog's records, its input held at that model's context window and its output at that model's cap and what the caps leave, times its tier's multiplier where pi-ai prices the tier (a tier it cannot price refused `egress_unpriced_option`), so the charge bounds what the call can cost once the gateway has clamped every output-cap alias and refused several choices, another model, server-side paid tools and options pi-ai does not price. The charge SHALL be the call's own row, counted only by the schema's triggers (into its run's row and its source's day and month), and SHALL neither read nor import pi's engine. An admitted call SHALL be an open row, answered with the blob, its version, the generation, its metering (the output cap the gateway clamps to and the body length, or none for a read) and an end signal. `egressSettle` (`reduce`) SHALL settle a reservation of its egress token once, its charge at the metered cost, `failed`, or `unknown` (kept whole); its door SHALL beat an open reservation while it streams (`egressBeat`, every `EGRESS_BEAT_S`), and one never settled SHALL be settled `lost`, its charge `unknown`, three beats after its last (`LOST_AFTER_S`), or past the stream's own day (`EGRESS_DEADLINE_S`) whatever its beats.

#### Scenario: A call past the owner's caps
- **WHEN** the run's requests, tokens or cost, the owner's day or month, or the source's leaves less than a call's least charge
- **THEN** it is refused `egress_spend_refused` before anything is forwarded, its receipt naming the cap

#### Scenario: Two calls at once
- **WHEN** two sandbox calls open together under a run cap one of them fills
- **THEN** the second is held to what the first left, or refused, and the run's counters hold both

#### Scenario: A reserved model call and a sandbox call on one source
- **WHEN** a reserved model call and a sandbox call of the same source settle on one day
- **THEN** the source's daily ceiling holds both, whichever comes second

#### Scenario: A sandbox token presented elsewhere
- **WHEN** the egress token reaches any door but `egressOpen` and `egressSettle`, the sync token any but the sync doors or another lease or epoch, or another binding those doors
- **THEN** it is refused

### Requirement: A change ends the egress streams it covers
Once its own unit committed, a policy write SHALL end every open egress stream, and a write that changes the egress table SHALL move the egress generation, leaving the sandbox's network stale, egress refused, until the sandbox slice applies it again (no other policy change moves it); a suspension every one, a connection's revoke that connection's, the lease's close and a lease that replaces a lapsed one the old lease's, and the owner's erasure every one: the object closes each one's end signal and marks its row with why.

#### Scenario: A connection is revoked while two stream
- **WHEN** the owner revokes one of two connections each streaming a sandbox request
- **THEN** only the revoked connection's stream ends

### Requirement: The sandbox's network is made from owner policy
`networkDocument` SHALL make E2B's network configuration from the owner's egress policy, in E2B's own field names: `denyOut` all traffic; `allowOut` `*` (names by SNI) in `open` mode, else the policy's destinations on ports 80 and 443, and in every mode `egress.tinyassets.io`; `dnsServers` pinned to one resolver; one transform, on `egress.tinyassets.io` only, setting the egress token and the egress generation. A destination on another port SHALL be refused, never widened; an IP literal SHALL be refused; `ask` mode SHALL be an allowlist whose application leaves a refusal receipt. **An allowlist is not a data boundary in v1**: E2B allows all traffic to a DNS server, on every port, and its rules name no ports, so the pinned resolver cannot be held to port 53; every network plan SHALL say so to the owner (`boundary: false`, its note). The E2B project's API key SHALL be held as a platform secret, since it can read the sandbox's transform, and so the egress token.

#### Scenario: An allowlist naming an IP and a database port
- **WHEN** the policy lists `8.8.8.8:443` or `db.example.com:5432`
- **THEN** the first is refused as an IP literal and the second left out, reported as not expressible

### Requirement: A run's environment carries only its own credential
A run SHALL name its CLI record, its credential and, on an API key, its connection, on its own run grant; the sandbox's lease is the sandbox's, shared by its simultaneous runs, and holds no connection. The runner SHALL build the run's environment from an allow-list, starting empty: `HOME={home}`, the record's own variables (`runEnv`), the process basics and, on an API key alone, its one connection's variables. A signed-in run SHALL carry none of its record's `forbid_env` (a backstop).

#### Scenario: Claude Code on a subscription, with a key connected
- **WHEN** the owner holds an Anthropic key and runs Claude Code on its own sign-in
- **THEN** the run's environment has no `ANTHROPIC_API_KEY` and no `ANTHROPIC_BASE_URL`, so the CLI runs on the sign-in and never on the key or through the door

#### Scenario: The runner takes the run's environment whole
- **WHEN** a run's start reaches the sandbox, or the sandbox slice reads its `config`
- **THEN** it is handed the run's whole environment (`HOME={home}`, the record's own variables as templates it fills, and on an API key the key's variables) and the basics it keeps (`inherit`), and sets exactly those

#### Scenario: A variable the sandbox has that the record does not name
- **WHEN** the sandbox template's environment, or an agent's project file, sets a variable the run's allow-list does not hold (`CLAUDE_CODE_OAUTH_TOKEN`, `CODE_ASSIST_ENDPOINT`, a `.env` beside the agent)
- **THEN** the run does not carry it: the runner builds the environment from the allow-list alone, and slice S checks that no project-level env file reaches it

#### Scenario: A run spends on a connection it was not given
- **WHEN** a run on connection A calls connection B through the egress door, or a run on the vendor's own sign-in calls any connection
- **THEN** the call is refused `egress_run_connection`, its receipt kept, before any credential is read or refreshed

#### Scenario: A run its record does not allow
- **WHEN** a run names an engine that is not a served CLI record, a credential it does not run on, a connection the owner does not hold, or a provider the engine runs no key of
- **THEN** its admission is refused before anything is written

#### Scenario: A record setting a key's variable
- **WHEN** a CLI record's own variables name one a key's environment sets (`<PROVIDER>_BASE_URL`, its `api_key_env` names, `TA_<CONNECTION>_*`)
- **THEN** the record checker refuses it, so a run's record and its key never set one variable

### Requirement: The sandbox's model clients hold placeholder keys
The sandbox lifecycle's `config` SHALL hand an API-key run, for its connection, a base URL on the egress door (`https://egress.tinyassets.io/r/<run>/c/<connection>` and the sealed base URL's path, the run in the path so the door holds each call to its run's own connection) and a placeholder key `ta_ph_<connection>_<random>` that carries no secret, as `<PROVIDER>_BASE_URL`, `<PROVIDER>_API_KEY` and `TA_<CONNECTION>_*`, and as the engine's own variables from its record (Gemini CLI's `GOOGLE_GEMINI_BASE_URL`, the door and the connection alone, and `GEMINI_API_KEY`).

#### Scenario: Two Anthropic connections
- **WHEN** the owner has two Anthropic connections and a run is on one
- **THEN** the run's environment names that one alone

### Requirement: A run is admitted in one unit
A run (ADR-034 decision 4) SHALL be admitted in one gate unit, each refusal a fixed code and nothing written, in its authority's own sandbox (the owner's own work's, or each outside client's; runs of different authority never share one), holding a 256-bit secret of its own, of which the object keeps only the SHA-256 and which only its CLI's environment holds (its URLs carry it): the owner admitted for work; a `SANDBOX` bound (`sandbox_absent`); the agent's `agent.json` read whole and still at its etag (`agent_undefined`, `agent_changed`) and enabled (`agent_disabled`); the initiator's stamp standing, the owner's own or a client's grant at its stamped generation; a retry of the same request key the same run; a continued conversation the caller's own and of this agent (`no_conversation`); fewer live runs than the owner's tier allows (`tier_runs`; a mail's run waits its turn instead), the runs whose lease lapsed settled `unknown` first; the source chosen: `agent.json`'s engine on its credential (or its record's in order), an API-key run on the named connection or the strongest connected one whose source lists the engine, or for `auto` the strongest source that lists an engine, the owner's connections and its signed-in engines ranked together; an unattended run (a schedule's, a mail's) only on a credential its record allows unattended (`unattended_refused`); an API-key run under the owner's own spending caps (`owner_limit`), off by default; and the run's environment made. Then, in the same unit: the conversation made or touched, the run written `queued`, the sandbox's lease opened (its tokens minted) or joined, and the run grant minted: the lease, the stamp and origin, the scopes (the agent's, or for a client's run those its grant also holds where it acts) and the run's own connection. The start SHALL then go to the sandbox, and the run be `running` once it took it, `failed` with the sandbox's code when it refused, or `unknown` when no answer came (and cancelled). There SHALL be no platform run deadline: a lease's egress and sync tokens SHALL carry no deadline of their own and live while its heartbeat renews it, so a run's only time bound is the sandbox's own.

#### Scenario: A sixth run on the free tier
- **WHEN** five runs are live and the control record gives the free tier five
- **THEN** the sixth is refused `tier_runs`, until the platform sets the owner's tier `paid`

#### Scenario: No sandbox bound
- **WHEN** a run is asked for and no `SANDBOX` is bound
- **THEN** it is refused `sandbox_absent`, no run row is written and no lease is taken

### Requirement: An input to a busy conversation waits its turn
One engine session runs one invocation at a time, so an input to a conversation whose run is live, or that has inputs waiting already, SHALL never be refused for it: it SHALL be written a `waiting` run, its source chosen (a run no source serves refused at once), its input kept beside it (`run_inputs`), and started by the `runs` lane, in the order the inputs came, once the run before it settles, admitted whole again then. A retry of its request SHALL be the same run. The waiting inputs SHALL be bounded together by a technical ceiling of the owner database's budget (`QUEUE_ROWS`, `QUEUE_BYTES`), one more refused `run_inputs_full`. A waiting run SHALL hold no slot, grant or lease; a cancel SHALL settle it `failed` (`cancelled`) at once; one whose turn is refused (its agent disabled, its grant moved, no source) SHALL settle `failed` with that refusal, the owner told by a notice.

#### Scenario: Two inputs in a row
- **WHEN** two inputs reach a conversation while its run is live
- **THEN** both wait, the first starts as that run settles, and the second as the first settles

### Requirement: A run waits for a sandbox, never refused for one
When the sandbox Worker has no room to start a run's sandbox (`sandbox_waiting`: E2B runs as many sandboxes as the platform control record's `sandbox_concurrency_ceiling`, E2B itself refuses for room, or another owner waits ahead in the platform's line), the run SHALL NOT be refused: it SHALL be put back to `waiting` (the app's "waiting to start", its code `sandbox_waiting`, `sandbox_wait`), its input kept, its grant gone and its lease given back as a settle would, and nothing SHALL poll it: the `runs` lane SHALL start it again, admitted whole, once the platform's line rings the owner (`sandboxRung`, through the doors' ring consumer), never before; rung with no run waiting, the owner SHALL leave the line. When a run's lease ends (its last live run settled) or lapses, the object SHALL release it to the sandbox Worker, which pauses that sandbox unless a run started in it since. The owner's erasure SHALL kill every sandbox of the owner, paused ones included, and delete the sandbox Worker's state of the owner, in its step 2.

#### Scenario: E2B at its plan's sandboxes
- **WHEN** a run's start is answered `sandbox_waiting`
- **THEN** the run is `waiting` with code `sandbox_waiting`, holds no lease or grant, and the lane starts it again only once the line rings the owner

#### Scenario: The last run of a lease settles
- **WHEN** the runner's end settles the lease's last live run
- **THEN** the sandbox Worker is told to release that lease

### Requirement: `auto` sees the owner's subscriptions, by presence only
Each sandbox's runner SHALL report its own sandbox's engines' sign-ins (its record's `status_argv`) to the object, which SHALL keep them per sandbox, never a credential: the owner's own sandbox's one row an engine (signed in or not, when it was checked and changed, and the models the runner read it offers), and an outside client's sandbox's on its own lease. A login SHALL never be copied between sandboxes (ADR-031 decision 2): a run SHALL use only its own sandbox's sign-ins, so a client's `auto` run takes a plan only where that client's sandbox is known signed in, else the owner's keys. An agent naming the vendor's sign-in SHALL be admitted unless its sandbox is known signed out (the owner's, `signin_lapsed`); the sandbox's start checks the sign-in there and answers `signin_required` when none is, and the run SHALL settle `failed` (`signin_required`) with a notice to sign in inside that sandbox. `auto` SHALL rank the signed-in engines with the owner's connections by the same ranking, and where a subscription and a key cover one provider it SHALL pick the subscription. A sign-in that lapses SHALL leave the owner a "sign in again" notice, and `auto` (and an agent naming that engine on its sign-in) SHALL be refused `signin_lapsed`, never falling back silently to another source.

#### Scenario: Only a Claude subscription
- **WHEN** the owner has no connection and its runner reported Claude Code signed in
- **THEN** an agent on `auto` runs Claude Code on that sign-in

#### Scenario: A lapsed sign-in beside a key
- **WHEN** the sign-in that covers a key's provider lapses
- **THEN** `auto` is refused `signin_lapsed` and the key is not taken in its place

### Requirement: The tier's numbers are the control record's, the owner's tier the platform's
The tier numbers SHALL come only from the platform control record, handed by the binder in the binding of every door; the object SHALL keep the last numbers it was handed (`platform_tiers`, written only when they change) for the runs its own lane starts, its files byte quota (`max_bytes`, the tier's storage, or the owner's own lower figure) and its egress rate, and refuse a run, or a change that adds file bytes, with none (`tier_unread`), never a number of its own. When the record holds none, the binder SHALL hand `null`, and the object SHALL forget the numbers it kept and refuse. The owner's tier SHALL be its own admission row's, set only by the platform's reserved caller (`tierSet`), never by a client, a request or the owner. The owner alone SHALL read it (`tierRead`), for its app.

#### Scenario: A client names a tier
- **WHEN** the owner or a client calls `tierSet`
- **THEN** it is refused, and the tier stands

### Requirement: A run's calls are held to its run grant
A call carrying a run's binding (the egress token, the run its path names, and the SHA-256 of the run's own secret its path carried) SHALL be resolved to the live lease, that run live under it, and its grant, whose secret's hash it SHALL match: a run id alone authorizes nothing. Every door SHALL hold it to the grant's scopes, as its initiator (the owner's own work as the owner, a client's as that client, its stamp rechecked at every call), and the ledger SHALL take its posts as its own agent's, never its initiator's, under its initiator's authority; it SHALL read and ack no mailbox (`run_no_mailbox`), its mail being its input. It SHALL never act as the owner itself at an owner-only door, nor converse (`run_starts_none`: work between agents is mail). It MAY launch and schedule the owner's agents and read and cancel runs and schedules (`run_graph`, `run_status`, `run_cancel`; the founder, 2026-11-03: every agent of an owner may do what any other may), as its initiator, each held to its grant's `run_graph` (an agent the owner narrowed reads and cancels nothing): an owner-started or scheduled run as the owner; a client's run as that client, held to that client's stamped grant for the agent it launches too. What a run launches SHALL run unattended. A cancel SHALL revoke the run's grant at once. A grant change or revoke SHALL settle the client's runs in its unit, `failed` (`revoked`), live or waiting: their grants deleted, their tier slots free, their CLIs cancelled through the sandbox, and the client's schedules removed. The runner's own doors (a run's events, the sign-in report) SHALL take only the runner's secret, which no CLI holds.

#### Scenario: A run makes its owner's brief
- **WHEN** an owner-started run whose agent holds `run_graph` calls `run_graph` with a cron, then `run_status` and `run_cancel` on its `schedule_id`
- **THEN** the schedule is the owner's, read and removed by the run; a run whose agent lacks `run_graph`, or a client's run whose grant lacks it, is refused "outside client capability not granted"

#### Scenario: A client revoked mid-run
- **WHEN** the owner revokes a client whose run is live
- **THEN** the run is settled `failed` (`revoked`), its next call is refused `run_ended`, its tier slot is free and its cancel reaches the sandbox

### Requirement: A run settles once, from its runner's end
The runner's frames SHALL be read by the run's record's matchers in the object, never trusted as the VM framed them: each new frame (its `seq` past the last) kept as the run's session id (1 to 256 printable characters, no space; any other refused as the run's fixed error `session_id_refused`, its frame still relayed and the id never kept), result (cut to 16 KiB, marked) and first error, renewing the lease. The runner's end SHALL settle the run `succeeded` (exit 0, no error event) or `failed` (`exit`, `engine_error`, or `cancelled` after a cancel), and a run whose lease lapsed SHALL settle `unknown` (`lost`), never replayed. A live run whose own heartbeat (the runner's last post for it) lapsed for the lease's life SHALL settle `unknown` (`lost`) too, though another run keeps the lease. A settle SHALL delete the run grant, end the run's streams, give the lease back when no live run holds it, and prune the receipts to the newest `RUNS_KEPT` by when they settled (as every write of a run does), never the one settling nor a live or waiting one, so the rows stay within the guard's term however runs are queued and cancelled. A runner's events post SHALL be at most `EVENTS_BODY_MAX` bytes, `EVENT_FRAMES_MAX` frames and `FRAME_UNITS_MAX` units a frame, so the object holds it within the relay's memory budget. The object SHALL hold at most `EVENTS_IN_FLIGHT` posts at once, whatever the tier, one more answered `events_busy` for its runner to post again.

#### Scenario: A runner lost
- **WHEN** a run's lease lapses with no heartbeat
- **THEN** the run is settled `unknown` by the next admission or the `runs` lane, and never replayed

### Requirement: The runs lane starts runs only when there is work
The `runs` lane's next time SHALL be the earliest due schedule (while the owner is admitted), a waiting run's turn, mail to one of the owner's agents that no run has taken (while the owner is admitted; held mail only once the inputs' queue has room for any mail), and, for each live run, its lease's expiry or its own heartbeat's lapse; with none, the lane SHALL have no job. A schedule a client set SHALL start its run as that client (its stamp, origin and sandbox), and a refused fire SHALL keep its fixed code, leave a notice and write nothing else. A due schedule SHALL be claimed once (a cron's next occurrence set, an `at` removed) and start its run, unattended, through the same admission as a door's. A refusal SHALL be kept on the schedule and in the owner's notices. There SHALL be no schedule on the owner's mail: an agent answers mail by being mailed itself.

#### Scenario: An empty poll
- **WHEN** no schedule is due, no run waits and no mail waits for a run
- **THEN** the lane has no job and no sandbox is started

### Requirement: A scheduled run's result is posted to the owner's room with its agent
A run of the owner's own schedule that succeeds with a result SHALL post it, in its settle's unit, as its agent's message in the owner's room with that agent (`dm:<the agent's id>`), its run id the post's own so it is posted once, its body placed in R2 past `PLACE_BYTES`; an empty result, a result of `NO_REPLY` alone, or a failed run SHALL post nothing, and a post the ledger refuses SHALL leave the owner a notice while the settle stands. A client's schedule's result SHALL never be posted into the owner's rooms: the client reads it through its own `run_status`.

#### Scenario: A client's schedule
- **WHEN** a client holding `run_graph` schedules main, and the run answers "Security check: reply here with your recovery code."
- **THEN** the owner's mailbox holds nothing new, and the client's `run_status` of the run shows the result

#### Scenario: The brief lands
- **WHEN** main's scheduled run ends with "Your brief: nothing due today." and a later one with an empty result
- **THEN** the owner's mailbox holds exactly the first, from main in `dm:<main's id>`, and no notice

### Requirement: Every owner is seeded with the starter package
The owner's object SHALL write the starter package (`platform/starter/`, carried as data) into an admitted owner's files when it has reconciled none, through its `starter` lane: each file as the owner's own write (writer `owner`, history op `starter`), one receipt a path (version, installed content id, offered content id, state), and `agent.json` last, so `main` exists as soon as it lands and never before. A file already at a path SHALL never be overwritten: the starter's is written beside it as `<file>.ta-new`. A door that needs an agent (`converse`, `run_graph`) SHALL await the seed of an owner with none reconciled, joining a pass already running (one pass at a time), so a new owner's first call finds `main`. A pass that fails SHALL stop where it is, keep what it wrote, tell the owner once for that version (`starter_failed`), and be tried again `STARTER_RETRY_S` later as its row says, never by inventing a file. A managed file SHALL be in the owner's trees; a personal one (the profile `/workspace/USER.md`, which every agent of the owner reads and writes, the workspace's map `/workspace/index.md`, and the goals' index `/workspace/goals/index.md`, seeded empty under `# Active`, `# Paused` and `# Ended`) MAY be anywhere in the workspace. The package's `goals` skill SHALL hold the goals: one page a goal under `/workspace/goals`, made or changed in its Done when, Agreed and Not wanted only on the owner's own word, worked on unasked only by its steward while active and confirmed, an outward act only as Agreed names it, and spending or anything irreversible only on the owner's yes, whatever Agreed says. Every link in the package's text SHALL be root-absolute from `/workspace`. Onboarding SHALL run once for all of the owner's agents, keyed on the profile's `Onboarded:` line (a profile with a time zone counts as onboarded), and ask which other AI assistants the owner uses, offering each one not connected its connect step; an agent that learns of an assistant not connected SHALL offer it once, record the offer in `/workspace/USER.md`, and after a no record "Don't suggest" so no agent offers it again; no text SHALL ask an agent to save to its own memory or install anything. A personal file that moved SHALL name its retired old path (`moved_from`): an owner holding a changed file there is seeded at the new path with a copy of it, the old file left as it is, and told once (`starter_moved`, which says to keep the old file while any managed file still holds the owner's own text or an unadopted proposal, naming each, since those may still read it); one holding the starter's untouched text there is seeded with the new text. The owner SHALL read the receipts (`starterReceipts`). The receipts and the reconciled version SHALL be exported and captured into every generation, so a restored owner keeps them; the seed's files SHALL be the owner's own writes through the files plane, counted in the one storage figure, and a seed refused `storage_full` SHALL stop, tell the owner once, and be tried again.

#### Scenario: A new owner
- **WHEN** an owner's object is made and its lane runs
- **THEN** every starter file is in its files with the package's text, written by `owner` with op `starter`, a receipt each `installed`, and `agent.json` the newest version; `main` is an agent of the owner's

#### Scenario: A seed blocked part way
- **WHEN** a file of the owner's sits where the agent's directory goes
- **THEN** the skills are written, no `agent.json` is, the owner is told once, and once the file is moved the next pass writes the rest, `agent.json` last

### Requirement: A starter update never overwrites the owner's edits
When the carried package's version is newer than the one reconciled, the next pass SHALL: replace a managed file whose content is still what it installed (a compare-and-swap on its etag); write the new version beside an edited one as `<file>.ta-new`, which no engine loads, with one `starter_update` notice for the pass; never restore a deleted one; never touch a personal one (`SOUL.md`, `USER.md`) after its seed; and deliver a change that widens what the agent may do (more `agent.json` scopes, a new or changed script, or a version the package marks `widens`) as a proposal, edited or not: beside the file as `.ta-new`, with one `starter_proposal` notice, adopted only by the owner, or by its agent at the owner's ask, replacing the file with it (the proposal is the `.ta-new` and its notice, not a `policy_propose` card: a policy proposal applies owner policy, never a file). A widening SHALL be a proposal at a path the owner already holds too, a first seed over the owner's own `agent.json` among them. A file whose content is what was offered SHALL count as installed; a proposal not adopted SHALL stay a proposal, its offer kept current; and a proposal a later version withdraws SHALL have its `.ta-new` removed while it still holds the offered bytes, with one `starter_withdrawn` notice. A path a version drops SHALL be listed in its `retired` paths (the bundle refuses to build otherwise), whose receipts are kept, so a file the owner deleted stays deleted when a later version ships its path again. Undo SHALL be the file's own history: a restored version is the owner's edit, never replaced.

#### Scenario: Version 2 over an owner's edits
- **WHEN** version 2 changes an unedited skill, a skill the owner edited, a skill the owner deleted, `SOUL.md`, and adds a scope to `agent.json`
- **THEN** the first is replaced; the second stays the owner's with `SKILL.md.ta-new` beside it; the third stays gone; `SOUL.md` is untouched; `agent.json` is unchanged with the proposal beside it; and the owner has one update notice and one proposal notice

### Requirement: A main agent reads and finds the owner's files
`file_read` (`fileText`) SHALL answer a file's UTF-8 text from a byte `offset`, at most `max_bytes` (64 KiB when omitted, at most one 4 MiB chunk), cut back to a character, with `next_offset` (null at the end); an offset inside a character, or bytes that are not UTF-8, SHALL be refused as such. `file_list` with `head_chars` SHALL give each small (inline) file the start of its text. `search` (`fileSearch`) SHALL match a query's whole words (at most `SEARCH_QUERY_BYTES`), lexically and with no model, against the words of each file's path, each small file's frontmatter (`title`, `description`, `tags`, `type`) and text, and the lines of the page's small OKF `index.md` files naming it, each field split into words once; it SHALL read at most `SEARCH_SCAN` manifest entries from its cursor, answer every match among them (until `SEARCH_ANSWER_BYTES` of results, so the whole answer, both copies and the envelope, stays within the 16 MiB body ceiling), best first, as `{id, title, url}` (the id the stored path, the url the owner app's folder holding it), and set `next_cursor` right after the last match answered or the last entry read whenever more is left, so no match is out of reach. `fetch` (`fileFetch`) SHALL answer `{id, title, text, url, metadata}` for every id a search answers, a file that is not text with no text and `metadata.binary`. Each SHALL be held to the caller's grant as every file door is: `read_files`.

#### Scenario: A large page found by its index line
- **WHEN** a page past 4 KiB is named in its directory's `index.md` as "the accountant who files our taxes", and a client searches "accountant taxes"
- **THEN** that page is the first result, and `fetch` answers its whole text, which `file_read` also reads in windows that join to it

#### Scenario: A client without read_files
- **WHEN** a scoped client holding only `read_graph` calls `search`, `fetch` or `file_read`
- **THEN** each is refused "outside client capability not granted"

### Requirement: Each of the owner's agents is a ledger identity that follows its file
Each `/workspace/agents/<agent>/agent.json` SHALL be a ledger identity, keyed by `ta_` and its name's digest, beside the outside agents: made as the file appears and retired as it goes, in the file change's own unit, and live again as itself, its mail kept, when the file comes back. A live one SHALL count among the enabled agents whose mailboxes the ledger reserves (`ledger_agents_full` past them), and its row among the registry's, the oldest retired one forgotten first. A retired agent's mail SHALL stay readable by its other side; new mail to or from it SHALL be refused (`agent_retired`), and its untaken mail, to it or from it, SHALL fail, as a disabled outside agent's does.

#### Scenario: An agent's directory removed
- **WHEN** the owner removes `/workspace/agents/main`, whose agent sent the owner mail
- **THEN** its identity is retired, the owner still reads that mail, and a new post to it is refused `agent_retired`

#### Scenario: A retired agent's untaken mail
- **WHEN** an agent whose mail to another agent no run has taken is retired
- **THEN** that mail fails and starts no run

### Requirement: A room is a pair, and a post wakes only the members it addresses
A room SHALL be a pair's: `dm:<a>` the owner and `a`, `dm:<a>+<b>` two agents (`a` before `b`), at least one the owner's own. A post SHALL go to the other member, from the caller's verified identity (a run's own agent's), and an agent's mail to the owner SHALL count against its share of the owner's mailbox. A post SHALL wake only the members it addresses: a deliberate post addresses the other member; a run's result addresses only the person or outside client whose mail started the run, and is posted as the reply to that mail, never to another agent's. A further turn between agents SHALL take a deliberate post, which needs `converse`. The owner SHALL read any chain whole from any of its messages (`mailChain`). There SHALL be no turn or chain-depth cap of the platform's.

#### Scenario: One question between two agents
- **WHEN** an agent's run posts a question to another agent and that agent's run ends with a result, posting nothing
- **THEN** no further run starts, and the owner reads the question in the chain

### Requirement: A run reads no mailbox
A run SHALL read and ack no ledger mailbox (`run_no_mailbox`, at `mailboxRead`, `mailboxAck` and `inboxDigest`), and its MCP profile (`run`, `doors/profiles.json`) SHALL hold no mailbox tool, `inbox_digest` among them: its mail is its input, and its agent's mailbox holds every authority's mail. An agent's `queued` and `held_cap` rows SHALL be the `runs` lane's alone.

#### Scenario: A client's run
- **WHEN** a client's mail starts an agent's run while the owner's mail to that agent waits
- **THEN** the run's mailbox read is refused, and the owner's mail starts its own run

### Requirement: Mail to an agent starts its run
Mail to one of the owner's agents SHALL wait `queued` until the `runs` lane starts that agent's run on it through the same admission as a door's: its input its own mail's envelope (who sent it, by name, kind and id; the room; the message to answer as parent) and then the body verbatim inside a fence named by the message's id, which its sender cannot know as it writes the body, so a body can neither close the fence nor open an envelope outside it; nothing past its own mail; attended only when a person sent it (the owner's own call), unattended for a run's mail and a client's; its conversation the thread's (one a thread, agent and authority); its authority the sender's, kept with the mail (a client's grant at its generation, the run held to the intersection with it, refused once it moved); its retry key the mail. Posting it SHALL need `converse` on that agent (a client's scoped grant, a run's scopes). The mail SHALL be taken in the admission's unit and held until the run settles; past the tier, or behind a busy conversation, the run SHALL wait its turn; with the inputs' queue full the mail SHALL stay held in the ledger; any other refusal SHALL fail the mail with a notice. A result answering a person's or a client's mail SHALL be posted before the settle, once; a reply the ledger does not post SHALL leave a notice, and the settle SHALL stand.

#### Scenario: A client's mail to an agent
- **WHEN** an outside client whose scoped grant holds `converse` and `read_graph` for `main` mails `main`, whose scopes are every capability
- **THEN** `main`'s run starts unattended in that client's sandbox under its stamp, holding `read_graph` and `converse` only, and its result is `main`'s reply to the client in their room

#### Scenario: The owner's mail on an attended-only source
- **WHEN** the owner mails an agent on a sign-in its record allows only attended, and another agent mails it too
- **THEN** the owner's mail starts its run, and the other agent's fails `unattended_refused`

#### Scenario: A revoked sender
- **WHEN** a client's mail to an agent is still untaken when the owner revokes the client
- **THEN** no run starts, the mail fails, and the owner has a notice

### Requirement: Agents mailing each other are bounded by the tier and the owner's own limits
Agents that keep posting to each other SHALL be bounded only by what bounds every run: the tier's simultaneous runs, the inputs' queue and each agent's mailbox (each refused loudly), the owner's own limits (an API-key turn past the owner's caps refused `owner_limit`, the mail failed and noticed), and the vendor's limits.

#### Scenario: Two agents on a key, past the owner's daily cap
- **WHEN** two agents take turns by posts and the owner's daily cap is spent
- **THEN** the next turn starts no run, its mail fails, the owner has a notice, and the lane has no job

### Requirement: A run's live output is relayed, never committed
Each frame (`{v, run, seq, at, kind, event}`) SHALL be relayed to the owner app's sockets on the run's conversation, and kept in memory only in a bounded tail for late joiners (4 Mi UTF-16 units in all, the longest tail's oldest frame first, a frame past 256 Ki units never kept), dropped at the run's settle. A socket SHALL be attached only through the binder's exact upgrade shape, its single-use ticket consumed in the attach's unit, and close with a fixed code at its token's expiry, its grant's sign-out, every session's close, an admission refusing reads, or the erasure.

#### Scenario: A late joiner
- **WHEN** a socket attaches to a conversation whose run has relayed frames
- **THEN** it receives the run's tail first, then each frame as it comes

### Requirement: Every chunk is put and read under its owner's data key
Every chunk get and put, a file's and a placed body's alike, and every nightly manifest part SHALL be under its owner's own data key: R2's SSE-C (`ssecKey`) by default, or AES-256-GCM per object in the files Worker (`FILES_CIPHER` `aes-gcm`, the object's R2 key as additional data) should the live probe find SSE-C does not combine with the create-once put. The data key SHALL be 32 random bytes made by the files Worker for one namespace and kept only wrapped: AES-256-GCM under a wrapping key derived for that namespace from the files Worker's own keyring (`FILES_KEYRING`, HKDF-SHA256), its additional data binding the namespace and the kid, so the owner's object holds no key it can use and a wrapped key presented for another namespace fails. A get or put with no key, or one that does not open, SHALL be refused before R2 is reached; a missing or malformed ring SHALL fail every call loudly. Erasure SHALL leave no copy of the wrapped key, so a chunk that outlives the prefix delete is unreadable. A chunk's R2 name SHALL be an HMAC of its hash under a key derived from the data key, so a listing of the bucket confirms no known file. A key rotation SHALL re-wrap the same data key under the active kid (`rewrapKey`), the object's copy and then every manifest root it keeps (`dataKeyRewrap`, the platform's door), an idle owner's included, and a kid SHALL leave the ring only after every owner's re-wrap answered.

#### Scenario: Another owner's key
- **WHEN** a chunk put under Alice's key is read with Bob's wrapped key, or with a second key minted for Alice's own namespace
- **THEN** Bob's key is refused `data_key_refused` before R2 is reached, and the second key names no chunk the first put

#### Scenario: A rotation, an idle owner
- **WHEN** the owner's key was wrapped under a kid that is retiring, and the owner has changed nothing since its last manifest
- **THEN** the platform's re-wrap leaves the object's key and its manifest root under the active kid, and every chunk still reads

#### Scenario: An erased owner's stray chunk
- **WHEN** the owner is erased and a chunk of theirs is still in the bucket
- **THEN** no manifest root and no object row holds their wrapped key, so the chunk cannot be read

### Requirement: The owner export streams everything the owner holds, to the owner alone
The owner's export SHALL be a generation, the same artifact the nightly manifest's root names, in the format `tinyassets-export` version 2 (`export.ts`): a fuzzy base copy (a header naming the schema, the generation and its log's first seq S, every row of every exported table in its key's order, every chunk some content names by hash and size), then the change log captured while the base was read (each change's row image or `gone` line with its seq, through E), then `{"t":"end","at","seq":E}`; read last line per key wins, it SHALL be exactly the owner at E however the owner wrote meanwhile, and it SHALL never begin again for a change. Every change to an exported row while a generation runs SHALL write its image in the writing unit's own transaction, and nothing SHALL be deleted from the log before R2 holds it, so a crash anywhere repeats only what was not acknowledged. Past `LOG_BYTES` of unshipped log, or an image past `IMAGE_MAX`, the generation SHALL fail with one notice and the write SHALL land. Every exported row's widest image SHALL fit `IMAGE_MAX`, so no change to a real row fails a generation. A migration while a generation runs SHALL give it up with one notice and begin a fresh one under the new schema; a generation SHALL never hold two schemas. No `REPLACE` SHALL meet an exported table. An export SHALL read the generation running, the newest finished while nothing changed since, or a new one, by a cursor of the generation and an object's index; any cursor it handed out SHALL resume it, and it SHALL pin its generation from deletion for `EXPORT_LIFE_S`, under the garbage grace, so every chunk it names is still in the bucket; a chunk gone (the owner's garbage collected early past its figure) SHALL be refused by name. Every table of the schema SHALL be either exported, with every column exported or kept out with its reason, or kept out with its reason; sealed credentials, flows, session and ticket hashes (a policy receipt's session hash among them), run and lease secrets, the namespace and the wrapped key SHALL never be exported. Its door SHALL answer only the owner, in its live session: no outside client, no run, and no request without the session.

#### Scenario: A round trip
- **WHEN** the owner exports after writing a small file, a chunked file and a collection row
- **THEN** each exported table's rows equal the table's, and the files rebuild byte for byte from the manifest and the chunks fetched by hash

#### Scenario: An export holds nothing
- **WHEN** an export is open and a file written after it is deleted, its grace past
- **THEN** the sweep deletes its chunk as it would with no export, and opening export after export renews nothing

#### Scenario: The owner writes all through a generation
- **WHEN** the owner syncs 5,000 files, and changes and deletes rows, during every outside call of a generation and after every unit, and a pass is cut before or after any outside call
- **THEN** the generation still ends, and its lines replayed last per key are exactly the owner's rows at E; an export resumed from any cursor it handed out reads the same

#### Scenario: A client asks
- **WHEN** an outside client, an API key or an MCP token calls the export
- **THEN** it is refused, and the owner surface answers `403`

### Requirement: The nightly manifest is written by the owner's own schedule
Each owner's object SHALL write its export manifest to R2 once a night when anything changed since the last one began, from its own `manifest` lane (its day's start in UTC plus the owner's own offset), never from a platform cron of every owner; with no change, the lane SHALL keep no job. A manifest SHALL be a generation's root, naming its base parts, its log segments through E, S and E; one a pass did not finish SHALL go on `MANIFEST_CONTINUE_S` later, never at once; past `MANIFEST_PARTS` (measured against the worst stream, JSON's escaping included) it SHALL be given up and the owner told once, never retried in a loop. A garbage chunk SHALL wait 30 days, the object's point-in-time recovery window, before it is deleted, so no recovery point names a swept chunk; deleted bytes SHALL leave the owner's storage number at once (the app says "Deleted files are kept up to 30 days, within your storage; past that the oldest go first."), and once an owner's garbage in its grace passes the owner's storage figure, the oldest SHALL be collected early, never a chunk the newest finished generation names while it is under `MANIFEST_PROTECT_S` old, and garbage waiting on a failed delete SHALL never set the files lane due at once. The parts SHALL be under the owner's data key and the root, written last, SHALL carry the wrapped key under the active kid and the parts' hashes and sizes sealed under the data key, so a recovery needs only the keyring. The newest finished generation SHALL be kept, and each an open export pins; an older one SHALL be deleted root first once a new one finishes and no export pins it.

#### Scenario: Tonight's manifest
- **WHEN** the owner's manifest is due
- **THEN** the object's own alarm writes its parts and root and deletes the older one; with no change since, it sets no job, and the first change sets it for the next night

#### Scenario: Garbage past the owner's figure
- **WHEN** an owner on a tier of three files' storage deletes four files
- **THEN** each leaves the owner's number at once, the oldest file's chunk is deleted early, and the other three wait out their 30 days

### Requirement: A deployed migration is never edited
Once the owner Worker's first deploy is marked in `platform/owner/migrations.json`, every deployed migration SHALL be pinned there by the SHA-256 of its statements as the code evaluates them, and the migrations guard SHALL fail a pinned migration that changed or is gone; a new shape SHALL be a new migration at the end. Each object SHALL keep the digest of every migration it applied and refuse to start when one reads differently now, or when one's digest is gone. The lock itself SHALL only grow against the base branch: its first-deploy marker never changes once set, and its pins are never edited or taken away (a structural guard). The deploy SHALL mark the first deploy and pin each migration it ships, and refuse to deploy unless the lock pins every migration it ships; nothing SHALL be pinned without that mark, and until it is set migrations may be edited in place.

#### Scenario: A deployed migration edited in place
- **WHEN** a pinned migration's statements change, with its pin or without
- **THEN** the guard fails (the structural guard when the pin changed with it), and the deploy refuses to pin

### Requirement: Sandbox time is metered per run and per day, and limits nothing
What E2B bills SHALL be the truth: its signed lifecycle events (paused, killed), verified by E2B's signature scheme and routed by the sealed route the sandbox carries in its metadata, SHALL add each execution's billed ms, vCPU-ms and MB-ms to the UTC days it ran, once by its execution id (each live sandbox's last kept until its kill), and only while the event's own signed time is inside `BILLED_WINDOW_S`, every execution counted kept that long, so a replay counts nothing; past `BILLED_KEPT` it SHALL be counted and tallied unverified on its day, shown in the view; its body SHALL be read no further than its bound. As the estimate and the fallback, the object SHALL read the sandbox Worker's own log of each sandbox's start, pause, resume and kill on its `sandbox` lane, after a settled run has no sandbox time and while a span stands open, and apply it in one unit. A run's receipt SHALL carry its sandbox seconds (`sandbox_s`), the time its sandbox ran between its start and its settle, once the log is read past its settle; each UTC day SHALL sum the owner's sandboxes' running time once however many runs they held. No door, admission or run SHALL read it to refuse anything.

#### Scenario: A pause and a resume inside a run
- **WHEN** a run from 5 s to 30 s shares a sandbox that ran 0-10 s and 20-35 s
- **THEN** its receipt carries 15 s and the day 25 s

### Requirement: The owner has one storage number
Every byte of user content the owner stores SHALL count toward the tier's storage (free 25 GB, paid 250 GB, from the control record), or the owner's own lower setting: file chunks live and in history, small files kept inline, and collection rows, their history and mail bodies whether kept inline in the object or placed in R2. Platform bookkeeping (indexes, receipts, counters) SHALL NOT count. A change past it SHALL first let retention make room (the oldest history entries no row still is, then the oldest acked mail, of the scope that is full), then be refused with the files quota's fixed code (`file_quota`, or `file_share` past an outside writer's share), leaving nothing behind. The owner SHALL read `{used_bytes, limit_bytes, breakdown: {files, messages, collections}}`, the parts summing to what is used.

#### Scenario: Small bodies fill the storage
- **WHEN** an owner whose tier stores 8,000 bytes keeps writing 1,500-byte collection rows
- **THEN** old history entries go first, and once only live rows and their own entries are left the next write is refused `file_quota`, and the used bytes stay at or under 8,000

### Requirement: The key store and the inbound bindings are the object's own rows
The object SHALL keep each API key's id, name, mint time, last use and revoke, written with its grant in one unit that counts live keys strictly. It SHALL keep each inbound binding's agent, revision, route, owner-held proof and breaker. `inboundAccept` SHALL take only an inbound route's binding, and post the event as the owner's mail to its agent, keyed by binding, revision and event id.

#### Scenario: A stale route, a replay, a full mailbox
- **WHEN** an event arrives on a route at a revision the binding has moved past, or the same event id arrives twice, or a shared app's events meet a full mailbox three times in a minute
- **THEN** the first is refused `binding_withdrawn`, the second answers the first's receipt as deduped with one mail, and the third trips the binding's breaker, its events no-ops until the owner changes the binding
