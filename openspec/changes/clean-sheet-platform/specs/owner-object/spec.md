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
A service binding the owner Worker holds SHALL be only to a Worker the config guard's allow-list names, together with the binding kinds and counts that Worker may hold, none of which reaches another Worker or a store shared beyond its own purpose (today only `FILES`, the files Worker, holding exactly one `r2_buckets` entry). The guard SHALL check the bound Worker's own config, at top level and in every environment, and refuse any binding key the allow-list does not grant it.

#### Scenario: The bound Worker gains a way on
- **WHEN** the files Worker's config gains a Durable Object namespace, a service, a queue producer, a workflow, a dispatch namespace, a Worker loader, an unsafe binding, a tail consumer, a KV namespace, a D1 database or a second bucket, at top level or in an environment
- **THEN** the config guard refuses the owner Worker's `FILES` binding

#### Scenario: The binding names another Worker
- **WHEN** `FILES` is bound to another platform Worker, even one that binds nothing
- **THEN** the config guard refuses it

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
Rows and payload bytes, history included, SHALL be bounded per writer, per collection and per owner, past which a write is refused, with S34's arithmetic. Each bound SHALL read counters that every write, delete, clear and history prune updates in its own transaction, so a bound check never scans the store.

#### Scenario: The counters after any sequence
- **WHEN** any sequence of writes, refusals, conflicts, deletes, clears and history prunes has run
- **THEN** every counter equals a recount of the rows and history it covers

#### Scenario: A writer past its share
- **WHEN** a caller other than the owner would create more rows, or hold more bytes, than its share of a collection
- **THEN** the write is refused and nothing changes

### Requirement: The ledger's mailboxes are reserved inside its ceilings
The ledger SHALL bound each mailbox's unacked messages and bytes, the owner's and each enabled agent's, by trigger-kept counters. The bounds for the most enabled agents allowed SHALL fit inside the ledger's ceilings, so unacked mail never fills it and eviction always finds a message no recipient holds. A post SHALL be refused only when its own mailbox, or its sender's share of the owner's, is full.

#### Scenario: An agent that never acks
- **WHEN** the owner's mail to one agent fills that agent's mailbox
- **THEN** the owner's next post to that agent is refused and posts to every other mailbox still succeed

### Requirement: A disabled agent holds no mail, and only the owner deletes one
Disabling an agent SHALL release its unacked mail, so it no longer holds the ledger; the owner may disable any agent, and the ledger an outside caller only its own. Only the owner, in its own session, SHALL delete an agent, which purges its mail with their bodies, its cursor, wakes, rings and receipts. While a revoke of its callbacks is pending its row SHALL stay as a tombstone that the registry bound counts, gone when the revoke settles. The enabled-agent bound SHALL count enabled agents only.

#### Scenario: A disabled agent's unread mail
- **WHEN** an agent holding unacked mail is disabled
- **THEN** its mail no longer holds the ledger, and later posts evict it as they need room

### Requirement: A resend is deduplicated while its message is held
A post with a sender's client message id and body hash the ledger holds SHALL return the original message, and another hash, room or parent SHALL be a conflict. The window SHALL be the held message's life: a message is evicted only once every recipient has acked it and the ledger is at a ceiling, after which the same client message id is a new message. No tombstone of evicted ids is kept.

#### Scenario: A resend after eviction
- **WHEN** a message every recipient acked has been evicted and its sender resends it
- **THEN** the ledger records it as a new message

#### Scenario: A retried ack
- **WHEN** a reader acks at or behind its cursor, even after its acked mail was evicted
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
Lifecycle SHALL own the object's single physical alarm, which PiHarness's wake job shares. No source SHALL set, read or clear the alarm itself, import any part of the `agents` package but `agents/lifecycle` (so the object never extends the SDK's `Agent`), or install Lifecycle's runtime `fetch` and WebSocket handlers. The object's `alarm` SHALL only hand the runtime's alarm to Lifecycle.

#### Scenario: Source sets the alarm
- **WHEN** a Worker source names `setAlarm`, `deleteAlarm`, `getAlarm`, `disableAlarms` or `deleteAll`, spells one in pieces through a string, reaches a storage or a Lifecycle by a computed name, imports any part of the `agents` package but `agents/lifecycle`, installs Lifecycle's handlers, or names a Lifecycle or pi table
- **THEN** the alarm guard or the policy-boundary guard fails CI

#### Scenario: A binding fetches the object
- **WHEN** a binding calls the object's `fetch`
- **THEN** it fails, since the object defines no `fetch`

### Requirement: Only the wakes lane claims and settles wakes
Wakes, and revokes of an agent's callbacks, SHALL be rows on the object's one schedule, the `wakes` lane. No RPC method SHALL claim, mark or settle one; the lane's pass SHALL, in process, with the doorbell's sender. Each pass SHALL first recover lapsed claims by their own index, then, with a sender, claim due open rows. The lane SHALL have a job for open rows only with a sender, so without one they stay pending and arm nothing.

#### Scenario: A lapsed claim behind due wakes
- **WHEN** a claim's lease has lapsed behind more due open wakes than one pass takes, and the object has no sender
- **THEN** one pass recovers it, as `unknown` if it was marked, and the lane is left with no job

#### Scenario: A door names a wake claim
- **WHEN** a binding calls a claim, mark or settle method on the object
- **THEN** the call fails because the object has no such method

### Requirement: Wake limits are owner policy
An agent's wake limits SHALL be stored per agent, 12 wakes an hour and a 60 s batch window by default, and changed only by the owner in its own session, as a policy write: against the policy generation, which the change bumps, and receipted. A registration SHALL never carry or reset them.

#### Scenario: An agent registers again
- **WHEN** the owner has set an agent's limits and the agent registers again
- **THEN** its limits stay the owner's, and a registration that names a limit is refused

#### Scenario: A wake-limits change against a stale generation
- **WHEN** the owner changes an agent's limits naming a policy generation that has moved on
- **THEN** the change is refused, the agent's limits are unchanged, and a refusal receipt is left

### Requirement: The object checks the owner's session itself
An agent's register, disable or delete, a policy write, a wake-limits change and a grant change SHALL be served only when the binding's session cookie opens a live session row in the object's own store. The object SHALL mint each session's cookie, keep only its hash, and open a session only for the owner itself from a verified sign-in at most 600 s old. A stored hash, another owner's cookie or an expired or closed session SHALL never be a proof.

#### Scenario: A session that has expired
- **WHEN** the owner writes its policy with the cookie of a session whose row has expired
- **THEN** the write is refused with "interactive owner session required" and the generation is unchanged

#### Scenario: The stored hash offered as the proof
- **WHEN** a call presents the hash of the owner's live cookie as its session
- **THEN** it is refused as having no session

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

### Requirement: Scheduled work carries the authority it was made under
A scheduled turn or automation SHALL carry a stamp of the outside authority it was made under (the client, its grant generation and its sign-in time), or none for the owner's own, rechecked with the grant's standing when it is made and before it is handed to pi; a changed, revoked or fenced grant SHALL refuse it. A grant change SHALL refuse the client's pending work, never work being handed, in its own transaction. Open stamped work SHALL hold its client's grant row.

#### Scenario: Free rows at their bound
- **WHEN** the free grant rows are at their bound while a client's stamped work is pending
- **THEN** the client's row is kept, and the work still runs under its standing grant

#### Scenario: A grant changed around the doors
- **WHEN** a stamped turn's client's grant generation moves before the turn is due
- **THEN** the turn is refused at its hand-off and the runner is never called

### Requirement: Scheduled work is handed to pi, never run in the schedule
The `work` lane SHALL only hand a turn to pi's durable submit; pi runs it, and its status is pi's receipts. A hand-off SHALL be claimed under a lease that bounds the hand-off alone, and marked before it can have any effect, the runner stopping if its mark fails. A lapsed claim SHALL be `unknown` if it was marked, never handed again, and an unmarked one due again, a bounded number of times. A hand-off held past its bound SHALL be reported and left, marked, to its lease.

#### Scenario: A runner dies after its mark
- **WHEN** the runner marks a turn and dies before it answers
- **THEN** once the lease lapses the turn is `unknown`, and the runner is never handed it again

#### Scenario: A runner that awaits the turn
- **WHEN** a runner holds its hand-off past the bound
- **THEN** the pass reports it, marks the row, moves on to the next, and the row is `unknown` once its lease lapses

### Requirement: A grant change ends its client's wakes
A grant change SHALL suppress the client's agents' open wakes and schedule the revoke of their callbacks in the same transaction, and a claim SHALL suppress a wake whose client's grant no longer stands.

#### Scenario: A grant revoked while a wake is open
- **WHEN** an agent's client is revoked while its wake is pending and its callback subscribed
- **THEN** the wake is suppressed, its callback is revoked on the next alarm, and nothing is sent

### Requirement: A revoke is fenced by the subscription generation
Each `mailbox.message` subscribe SHALL open the agent's next subscription generation, which tags the doorbell's callback. Disabling an agent, moving it off `mcp_event` or deleting it SHALL schedule a revoke only when the agent has subscribed since its last revoke, carrying the newest generation, with at most one open per agent. The doorbell SHALL erase only callbacks at or before a revoke's generation.

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

#### Scenario: History at its quota
- **WHEN** history rows reach `max_history`
- **THEN** a write is refused, and the owner frees room by a purge or a lowered retention without either being refused

### Requirement: File quotas are owner policy, refused loudly
Chunk bytes, inline bytes, entries, history rows, open uploads and the chunks they hold, and a single file's size SHALL each be bounded by owner policy (the document's optional `files` table over the defaults) or a fixed ceiling, read from counters the schema's triggers keep. A change that takes a counter past its bound SHALL be refused with nothing left behind.

#### Scenario: The owner's entries at their quota
- **WHEN** a write would add an entry past `max_entries`
- **THEN** it is refused, and no parent directory, history row or content of it is kept

### Requirement: A quota never blocks a change that frees
A change that only removes or frees (a remove, a redaction, a purge, an aborted upload, housekeeping) SHALL never be refused by a quota. A policy SHALL be refused unless `max_entries` times the larger of `keep_versions` and `keep_recent` is at most `max_history`, so no churn of live files can fill it.

#### Scenario: A full owner removes files
- **WHEN** every counter is at its bound and the owner removes a file
- **THEN** the remove lands

### Requirement: The sync lease fences the sandbox's commits
The sync lease SHALL be a random lease id and a rising epoch, held by one holder until it lapses or is released. A sync commit SHALL be refused whole only when the lease id or epoch is not the live lease's or its op list is empty or too long; each op SHALL otherwise land or be refused on its own, its quota checked alone, and be answered per op. API writes SHALL never take the lease.

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
The files Worker SHALL hold the platform's only bucket binding and bind nothing else, serve no URL, and take only hex namespaces, chunk hashes and bytes, moving at most two chunks' bytes a call. Its keys SHALL be only `o/<ns>/c/<sha256>`, built by one function. A put SHALL be create-once and verified by R2's SHA-256 check; an existing key SHALL count as stored only when it holds those bytes.

#### Scenario: Bytes that miss their hash
- **WHEN** a put names a hash its bytes do not have
- **THEN** it is refused and nothing is stored

### Requirement: The binder may stream an owner's chunks
The binder is the trust root: the HTTP door's binder MAY read an owner's namespace from that owner's object and stream chunks to the files Worker itself, since a binder already reaches every owner's object; no owner's object SHALL hand out another owner's namespace.

#### Scenario: The binder streams a large read
- **WHEN** the binder serves Alice's large file
- **THEN** it reads Alice's namespace from Alice's object and the chunk keys it asks for are under that namespace only

### Requirement: One pi environment over the manifest
The owner's pi `ExecutionEnv` SHALL serve its file methods from the manifest and the files Worker, never waking a sandbox, and SHALL pass pi-durable 1.4.0's conformance cases other than `exec`'s, with symbolic links off. A write to a path the environment read SHALL fail with `changed since read` when anything else committed there since; `exec` SHALL answer `shell_unavailable` until the sandbox lands.

#### Scenario: An edit raced by another writer
- **WHEN** one environment reads `notes.md`, another writes it, and the first writes its edit
- **THEN** the first write fails with `changed since read` and the other's content stays

### Requirement: The environment stays inside a request's limits and keeps bytes verbatim
A whole-file read or write by the environment SHALL move at most 16 MiB, and one environment, built per tool call, SHALL make at most 24 calls to the files Worker, each refused as `not_supported` before it is made. `readTextFile` SHALL keep a byte-order mark.

#### Scenario: pi's edit of a file with a BOM
- **WHEN** pi's `edit` changes one line of a CRLF file that starts with a BOM
- **THEN** the file holds the BOM, the CRLF endings and the edit, and nothing else changed
