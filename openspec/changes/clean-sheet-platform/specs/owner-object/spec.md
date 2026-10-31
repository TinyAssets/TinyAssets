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
A service binding the owner Worker holds SHALL be only to a Worker the config guard's allow-list names, together with the binding kinds and counts that Worker may hold, none of which reaches another Worker or a store shared beyond its own purpose. The guard SHALL check the bound Worker's own config, at top level and in every environment, and refuse any binding key the allow-list does not grant it.

#### Scenario: The bindings today
- **WHEN** the owner Worker's config is checked
- **THEN** it holds `FILES`, to the files Worker holding exactly one `r2_buckets` entry, and `GATEWAY`, to the model gateway holding no binding and declaring only its keyring secret by name

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
Lifecycle SHALL own the object's single physical alarm, which PiHarness's wake job shares. No source SHALL set, read or clear the alarm itself, import any part of the `agents` package but `agents/lifecycle` and `agents/harness/pi` (so the object never extends the SDK's `Agent`), or install Lifecycle's runtime `fetch` and WebSocket handlers. The object's `alarm` SHALL only hand the runtime's alarm to Lifecycle.

#### Scenario: Source sets the alarm
- **WHEN** a Worker source names `setAlarm`, `deleteAlarm`, `getAlarm`, `disableAlarms` or `deleteAll`, spells one in pieces through a string, reaches a storage or a Lifecycle by a computed name, imports any part of the `agents` package but `agents/lifecycle` and `agents/harness/pi`, installs Lifecycle's handlers, or names a Lifecycle or pi table
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
Chunk, inline and manifest bytes, entries, history rows, open uploads and their chunks, and a file's size SHALL each be bounded by owner policy or a fixed ceiling, read from counters the schema's triggers keep. A change past a bound SHALL be refused in its own unit before it commits (an upload before any byte is sent), leaving nothing, its reason starting with a fixed code: `file_quota` for the owner's bound, `file_share` for a client's share.

#### Scenario: The owner's entries at their quota
- **WHEN** a write would add an entry past `max_entries`
- **THEN** it is refused `file_quota`, and no parent directory, history row or content of it is kept

### Requirement: Every manifest row is bounded in bytes as the object stores it
Every row of the file manifest (each entry, history row, content with its inline bytes and its chunk list, chunk, temporary directory and share row) SHALL be charged, by triggers only, what SQLite stores it in: each b-tree record it makes, its text counted in bytes, its page share and overflow pages included, against `max_manifest_bytes`. The charge SHALL be at least what the object's database grows by, for every path length a path may have (up to 4,096 bytes, components of up to 255).

#### Scenario: A client writing 4,096-byte paths
- **WHEN** an outside client writes file after file at 4,096-byte paths
- **THEN** each is charged its three spilled copies' overflow pages, the object's database grows by no more than the charge, and the client is refused `file_share` at its share while the owner still writes

### Requirement: An outside client holds a share of every file quota
Each outside client SHALL hold at most `client_share` percent of every file quota, open uploads included, and all clients together at most what `owner_reserve` percent leaves, both owner policy, so the owner always has room of its own. A client SHALL be charged the entries it last wrote, its history rows, and the contents, chunks, temporary directories and uploads it first stored; a chunk revived from garbage, to the upload that revived it.

#### Scenario: A client fills its share
- **WHEN** an outside client holding `write_files` writes until it is refused `file_share`
- **THEN** the owner's writes, grant changes, revokes and policy writes all still land

#### Scenario: Two clients together
- **WHEN** `client_share` is 40 and `owner_reserve` 50 of ten entries, and two clients write four and one files
- **THEN** a fifth file of the first, or a second of the other, is refused `file_share`, and the owner writes five more

### Requirement: The owner's database is bounded under half its limit
Every table the owner's schema makes SHALL be bounded by a count or bytes the code enforces, with no exception, and a guard SHALL compute the object's worst case from those bounds and the schema, at the policy's high bounds, and hold it under half of the platform's 10 GB. The model turns' counters SHALL be bounded too: at most `TURNS_KEPT` turns of at most 320 bytes each, a new turn past it dropping the turn idle longest with no call open.

#### Scenario: A table no bound covers
- **WHEN** a migration makes a table the guard has no term for, or a manifest table's charging trigger is missing
- **THEN** the owner-bound guard fails

### Requirement: A quota never blocks a change that frees
A change that only removes or frees (a remove, a redaction, a purge, an aborted upload, housekeeping) SHALL never be refused by a quota. A policy SHALL be refused unless `max_entries` times the larger of `keep_versions` and `keep_recent` is at most `max_history`, so no churn of live files can fill it, and gone paths' churn makes its own room.

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
A whole-file read or write by the environment SHALL move at most 16 MiB, and `readTextFile` SHALL keep a byte-order mark.

#### Scenario: pi's edit of a file with a BOM
- **WHEN** pi's `edit` changes one line of a CRLF file that starts with a BOM
- **THEN** the file holds the BOM, the CRLF endings and the edit, and nothing else changed

### Requirement: The platform counts the object's calls
The object SHALL keep no call budget of its own: the platform counts subrequests per invocation, charged to the object's newest incoming event or to a count each event resets (the cap probe's M12 found one of the two and could not tell them apart), and its own error SHALL be the failure a caller sees (a tool call's error result). The owner Worker's config SHALL set `limits.subrequests` to 10,000, and the config guard SHALL refuse any other value, or none.

#### Scenario: The platform refuses a call
- **WHEN** the files Worker's call throws the platform's subrequest error inside a tool call
- **THEN** the tool's result is that error, and nothing was written

### Requirement: Every owner write goes through pi's SQLite queue
Every owner write SHALL run as a unit of the object's write gate: a synchronous turn of chord's per-storage queue, which pi's commits use, run once the queue holds nothing. A door's synchronous body SHALL be one unit; a door, lane or tool call that crosses to an outside call SHALL write each step around it as a unit of its own; Lifecycle's job sets SHALL be made inside a unit. A unit SHALL keep its own atomicity. Reads stay direct.

#### Scenario: A door writes while a transaction of the queue is open
- **WHEN** a door is called while a transaction of the shared queue is open, and that transaction then rolls back
- **THEN** the door's write waits for the queue and is kept, where a direct write made at the same point is rolled back with the transaction

#### Scenario: A unit queued as Lifecycle starts
- **WHEN** a unit is queued just before Lifecycle's startup, which holds the input gate, awaits pi's open on the queue
- **THEN** the unit runs, then the open, and neither waits on the other: a unit is never an async storage transaction of its own, which would need the input gate

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

### Requirement: pi runs in the owner's object on PiHarness
pi-durable SHALL run in the owner's object on PiHarness, a capability of the object's own Lifecycle beside the schedule, with none of Lifecycle's handlers installed. pi SHALL open in Lifecycle's startup, which the first alarm or the first PiHarness call runs, with no door starting it first. Its tables SHALL carry their own schema version beside ours, which the pi pin guard SHALL hold at 2, with pi-durable, pi-ai and chord pinned by exact version and lockfile integrity.

#### Scenario: A cold object's first call
- **WHEN** a cold object's first call submits a turn
- **THEN** pi opens, migrates its tables to version 2, and the turn completes

### Requirement: pi opens with the object's registry, tools and settings
The object SHALL open pi with one registry per object, pi's coding tools with `read` alone replay-safe, settings read at each use (progress committed at most once a second, a failed run's queued inputs withdrawn, owner policy's part beneath those), TinyAssets' attribution, and one model provider, `gateway`, listing only the owner's connected models.

#### Scenario: An eviction mid-tool
- **WHEN** the object dies while a `read` and an unsafe tool are running, and its alarm starts it again
- **THEN** `read` runs again, and the unsafe tool comes back `interrupted` and never runs twice

### Requirement: A conversation's file changes are by its stamp
The environment SHALL be built per tool call over the owner's files, its history writer the conversation's `ta.authority` stamp: `client:<id>` for an outside client's conversation, else `agent`. A conversation a task creates SHALL take its owner conversation's stamp in the creating commit, and a fork SHALL keep its parent's.

#### Scenario: A subagent of an outside client's conversation
- **WHEN** a tool in a conversation stamped for a client creates a conversation it owns
- **THEN** the new conversation carries the same stamp, and its file changes are the client's

### Requirement: pi's reports reach the owner, and owner content never stops the object
What pi reports through `onReport`, and each extension pi refuses to install while opening, SHALL become an owner notice, the newest 100 kept, read by the owner alone; a refused extension SHALL be noticed once while that notice is kept, though pi opens again at every new instance. Opening pi SHALL never fail for owner content, since a failed open stops the object's lanes with Lifecycle's startup.

#### Scenario: A malformed package
- **WHEN** an extension pi refuses to install is among the object's
- **THEN** it is skipped and kept as a notice, pi opens with the rest, and the `wakes` and `files` lanes run

### Requirement: A model connection holds only a blob the gateway sealed
The object SHALL hold a model connection's credential only as the blob the gateway sealed for that owner, connection and version, never a plaintext key or token: the binder hands a pasted key to the gateway's `seal` and an OAuth code to its `exchange`, and `connectionCommit` takes the blob, its kid, the models the gateway's setup check listed with their metadata, and an OAuth access expiry in clear.

#### Scenario: A pasted key is connected and used
- **WHEN** the owner connects a key and its agent runs a turn on it
- **THEN** no table of the object, no call it makes to the gateway and no line it logs holds the key

### Requirement: A connection keeps up to 1,024 models
A connection SHALL keep up to 1,024 models, each its whole pi-ai record as the setup check listed it and checked whole at commit, within 640 KiB of JSON, so 1,024 of the largest built-in record (607 bytes in pi-ai 1.4.0) fit, and the largest built-in catalog (386 models) with room.

#### Scenario: The largest built-in catalog
- **WHEN** a key is connected for the built-in provider whose catalog is the largest
- **THEN** the connection is active with every model the setup check listed

#### Scenario: A model whose thinking map names xhigh
- **WHEN** a connected model's record maps `xhigh` and `max`, and another reasoning model's has no map
- **THEN** pi-ai's `getSupportedThinkingLevels` offers both levels for the first, through `high` only for the second, and `auto` offers the strongest's

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
Each model call SHALL be reserved in one unit that also reads the connection's blob and the owner policy's generation and `models` caps: the turn's requests, tokens and cost, and the owner's and the source's daily and monthly cost, each counting what is settled and what is still open. The reservation SHALL bound the call's output tokens. A source's ceilings SHALL be keyed by its source record, so a reconnect keeps them.

#### Scenario: Concurrent reserves at a cap
- **WHEN** eight calls of one turn reserve together against a cap of three requests
- **THEN** three are reserved and five refused, each refusal kept as a receipt

### Requirement: A call is estimated by pi-ai's calculateCost and settled at the gateway's price
A call's reservation SHALL be priced by pi-ai's `calculateCost` over the target model's whole cost record, so a long-context tier prices the whole request once its estimated input passes the tier's threshold, and the most output the cost room takes SHALL be found in about log2 of its bound. A settled call SHALL keep the cost the gateway's pi-ai adapter reported, never one recomputed from the record stored at connect time.

#### Scenario: A long-context tier
- **WHEN** a call's estimated and billed input pass its model's tier threshold
- **THEN** its estimate and reserved output are at the tier's rates, and it settles at the tier's price the gateway reported, which pi's own `pi.usage` records too

#### Scenario: A stored record older than the gateway's catalog
- **WHEN** a connection's stored record prices a model at nothing and the gateway's catalog does not
- **THEN** the call settles at the gateway's price, and its spend counts against the caps

### Requirement: The caps read counters, never receipts
Every cap SHALL read only counters the schema's triggers write: `model_turns` per turn, `model_spend` per source, day and month. Receipts SHALL be history only, bounded without freeing any cap; a turn's counters SHALL go only once it has been idle 30 days with no call open, or, past `TURNS_KEPT` turns, once it is the turn idle longest with no call open.

#### Scenario: Many requests in one turn
- **WHEN** one turn makes 5,000 requests while only the newest 100 receipts are kept
- **THEN** the turn's counters hold all 5,000, and a cap of 5,000 refuses the next

### Requirement: A model call's turn is the input at the origin of its causal chain
A turn SHALL be the submitted input at the origin of a call's causal chain, carried by pi's own telemetry propagation in its durable records: every call one input caused, through a subagent, a compaction or background work, even after its run ended, SHALL be charged to that input's turn. An input submitted under no origin (a door's, an enqueued one) SHALL begin a turn; an owner's compaction outside a run SHALL be its own; a call of any other task with no carrier SHALL be refused `no_run`.

#### Scenario: A compaction a run starts
- **WHEN** a run's context passes pi's background threshold, and pi summarizes after the run ended
- **THEN** the summarization is the run's turn

#### Scenario: A subagent's calls
- **WHEN** a tool of the root's run makes a conversation and submits its work there, which does the same once more
- **THEN** every call is the root run's turn

#### Scenario: Background work after its run
- **WHEN** a tool starts background work, the run ends, a later input runs, and the background work then calls a model
- **THEN** its calls are the first run's turn, count against that turn's caps, and never against the later run's

#### Scenario: An input from outside any run
- **WHEN** an input is submitted to a subagent's conversation from outside any run
- **THEN** its calls begin a turn of their own

### Requirement: Only platform code can start a turn or forge one
A new turn or a forged task carrier SHALL be in reach of trusted platform code only: the owner object runs no package executable code (`pi.extensions` are refused), hooks run in the hooks Worker with no submit or task path, and the model's tools have none of their own.

#### Scenario: A tool submits to its subagent
- **WHEN** a tool of a run submits work to a conversation it made, under the context pi passed it
- **THEN** the work is that run's turn, and no new turn opens

### Requirement: A reservation is dispatched and settled once, in its own units
A reservation SHALL be dispatched only while still reserved, at the same policy generation and connection version, and not aborted; and settled once: `succeeded` from pi-ai's usage, `failed` when the gateway answered with a refusal's status (its own, or the provider's before any output), `unknown` otherwise, which keeps its whole reservation. A dispatch or settle that cannot be kept SHALL settle the row as it stands (`not_sent` or `unknown`) in the same instance.

#### Scenario: A connection revoked before the dispatch
- **WHEN** a connection is revoked between a call's reserve and its dispatch
- **THEN** the call is `not_sent`, and nothing reaches the gateway

#### Scenario: A provider's rate limit, retried
- **WHEN** the provider answers 429 before any output, and pi retries the call twice
- **THEN** each try settles `failed` at no cost, and no ceiling is spent

#### Scenario: A provider refuses the key
- **WHEN** the provider answers 401 before any output, with retries allowed
- **THEN** pi does not try again, and the turn ends `upstream_unauthorized`

### Requirement: A dead instance's calls are settled by the next instance
An open reservation the live instance did not make SHALL be settled by the next reserve or receipts read: `reserved` as `not_sent`, `dispatched` as `unknown`.

#### Scenario: The object dies mid-call
- **WHEN** the object is reset while a dispatched call streams
- **THEN** the next instance settles it `unknown`, and its reservation still counts against the ceilings

### Requirement: The agent's models are the owner's connected models and `auto`
The `gateway` provider SHALL list each active connection's models, each with its whole record, and, with any, `auto`, carrying the strongest's. `auto` SHALL route each request in the object, by the ranking data file: to the strongest connected model for a user's message, to the model that answered for a continuation, to the next-ranked after one that failed before any output, and to the same model after one that failed for its context's length.

#### Scenario: Nothing is connected
- **WHEN** a turn starts on an object with no active connection
- **THEN** it settles `unanswered: no_model`

### Requirement: Each model is compacted at its own thresholds
pi's compaction thresholds SHALL be each listed model's own, from its context window (pi's defaults from 256K tokens, in proportion below), `auto`'s the strongest's. A request `auto` routes to a model whose threshold is lower than the strongest's SHALL be held to that model's own: over it, the call is refused `context_length_exceeded` before anything is reserved, so pi compacts and retries.

#### Scenario: A provider refuses an overflowing context
- **WHEN** the provider refuses a turn's call for its context's length, through the gateway
- **THEN** pi compacts the conversation and retries once, the refusal settles `failed` at no cost, and the turn succeeds

#### Scenario: auto continues on a smaller model
- **WHEN** a turn's continuation stays on a model whose threshold is lower than the strongest's, and its context passes that threshold
- **THEN** the call is refused before any reservation, pi compacts, and the retry goes to the same model under its threshold

### Requirement: pi's and pi-ai's metrics are kept
The telemetry adapter SHALL carry a metrics capability on its root, every span and every extracted parent, aggregating every metric pi and pi-ai record by name, kind and attributes in the object's memory, at most 1,024 series, a measurement past them counted as dropped. The owner's `usageMetrics` SHALL answer them with pi's own `pi.usage`, summed over the conversations; no cap reads either.

#### Scenario: A turn's metrics
- **WHEN** a turn runs through the gateway and the owner reads its usage
- **THEN** the answer holds pi-ai's token and cost metrics, pi-durable's phase and task metrics, and pi's `pi.usage` for the physical model that answered

### Requirement: The telemetry adapter never throws and reads pi's exported names
The span names and attributes the adapter reads SHALL be pi-durable's exported `DURABLE_TELEMETRY_SCHEMA`'s, and no adapter method SHALL throw, whatever it is handed: on pi 1.4 a throwing adapter can reject a durable commit.

#### Scenario: Hostile input
- **WHEN** a span, its propagation or its metrics is handed values whose every read throws
- **THEN** nothing throws, and a measurement that cannot be kept is counted as dropped

#### Scenario: pi renames a span
- **WHEN** a pi upgrade's `DURABLE_TELEMETRY_SCHEMA` no longer names a span or attribute the adapter reads
- **THEN** the build fails, at the adapter's types and in its test

### Requirement: Each model call goes to the gateway and to the live tee
Each call SHALL go to the gateway as pi-messages over the `GATEWAY` service binding, with the envelope the gateway reads, and every event SHALL be published to the live tee under the request's `sessionId` as it streams.

#### Scenario: A live view of a streaming answer
- **WHEN** an answer streams for longer than a second
- **THEN** the tee hands a subscriber every delta as it comes, while pi commits the answer at most once a second

#### Scenario: A turn is aborted mid-stream
- **WHEN** the owner aborts a turn whose model call is streaming
- **THEN** the gateway's upstream request is cancelled, and the call settles `unknown`

### Requirement: An outside client's authority is enforced in its environment
The environment pi builds for every tool call, a replay's included and whatever extensions run, SHALL hold each operation to the conversation's authority under a grant that still stands as stamped: a read, and each read of an opened reader, `read_files`; a change `write_files`, again in the change's own unit; `exec` refused to a scoped grant. A refusal SHALL be `permission_denied`.

#### Scenario: The gate configured away
- **WHEN** a scoped client's conversation, granted `read_files` only, runs with no `beforeTool` gate and calls `write`
- **THEN** its environment refuses the write, and nothing is written

#### Scenario: A revoke lands after a write began
- **WHEN** a revoke commits after a write's call passed its first check, before the write's own unit
- **THEN** that unit refuses it, and nothing is written

### Requirement: An outside client's model calls re-check its authority
Each model call SHALL find its conversation by its provider session, and its reservation and dispatch units SHALL each re-check that conversation's authority: a client's that no longer stands SHALL be refused `outside_authority` with a receipt and nothing sent, and a session the object never made `no_conversation`. A client's conversation whose stored extension selection drops the gate SHALL make no model call.

#### Scenario: A revoke the abort missed
- **WHEN** a revoke commits while a client's hand-off is between its mark and its submit, or between a call's reservation and its dispatch
- **THEN** the call is refused or not sent, and the gateway is never called

### Requirement: The authority gate refuses before a tool begins
A trusted `ta.authority` extension, installed first, SHALL check each tool call, nested ones included, in `beforeTool`: `read_files` for `read`, `write_files` for `write`, both for `edit`; `bash` and any tool without a capability refused to a scoped grant; the `ta:` names reserved for `ta`'s ops. A refusal SHALL be a block with a fixed reason and a receipt. Packages named `ta.*` SHALL be refused.

#### Scenario: A scoped client without write_files
- **WHEN** a scoped client's conversation, granted `read_files` only, calls `read`, `write`, `edit` and `bash`
- **THEN** the read runs, the other three are blocked with fixed reasons, and each block leaves a refusal receipt

### Requirement: Every conversation's authority is explicit and never shed
Each conversation SHALL carry its authority in pi's own document: the owner's (the root's, an owner thread's), a client's stamp, or none, under which no tool runs and no model call is sent. A thread SHALL be made with its authority in pi's creating commit; a task's conversation SHALL take its owner conversation's in `conversationCreated`, and a fork's copy that differs SHALL fail.

#### Scenario: A fork of a client's thread
- **WHEN** a client's thread is forked and the client is then revoked
- **THEN** the fork carries the same stamp, and its next tool call is blocked

### Requirement: A client's threads are bounded and hold its grant
`agent_threads` SHALL record a thread in a unit that re-checks its stamp stands, SHALL never change a thread's stamp, SHALL refuse loudly a thread past its client's bound or all clients' together, before any conversation is made, SHALL never record the root, and SHALL count each thread on its client's grant row (`held`), so the row is not forgotten while a thread names it. A grant set or revoke SHALL drop its client's threads in its own unit.

#### Scenario: A client at its bound
- **WHEN** a client has as many threads as its bound and asks for another
- **THEN** the thread is refused loudly, and the client's grant row counts each thread it keeps

### Requirement: Idle threads are dropped, never live ones
The `threads` lane SHALL drop a thread no door or run has touched for the idle age (7 days), in a unit that re-checks the activity it read, so a thread touched since is kept; a thread whose conversation pi holds live work in, its subagents' and background work's included, SHALL never be dropped, its activity moved on instead; a thread unsettled scheduled work names SHALL never be idle, and is in use from its work's claim.

#### Scenario: Clients that stopped calling hold the shared bound
- **WHEN** clients' threads fill the clients' shared bound and then go unused for the idle age
- **THEN** the lane drops them, and a new client's thread is made

#### Scenario: A thread whose background work runs on
- **WHEN** a thread's run ended but the background work it started still runs, past the idle age
- **THEN** the thread is kept and its activity moved on

#### Scenario: A far launch cancelled
- **WHEN** a client schedules launches months ahead and cancels some
- **THEN** the cancelled launches' threads are dropped once idle, and the open one's is kept

### Requirement: Every door that uses a thread keeps it active
Every door that makes or uses a thread SHALL move its activity on and re-derive the `threads` lane's job.

#### Scenario: A message to an idle-aged thread
- **WHEN** a door touches a thread after the lane read it as idle, before its drop
- **THEN** the drop re-checks its activity and keeps it

### Requirement: The owner's agent definitions are the owner's alone
Only the owner's own authority, the owner at the file doors or its agent under the owner's authority, SHALL create, change, move into or out of, or remove anything under `/workspace/agents`; an outside client SHALL be refused at every file door and every change of the agent's environment, its thread's tools included. A thread SHALL be configured only from an `AGENTS.md` whose recorded writer is the owner or its agent, any other refused loudly.

#### Scenario: A client plants an agent's instructions
- **WHEN** a client writes, renames or uploads into `/workspace/agents/main/AGENTS.md` by a door, or its conversation's model calls `write` there
- **THEN** each is refused "only the owner changes its agents' files under /workspace/agents", and no thread of the owner's or another client's is configured from it

#### Scenario: A file another wrote is already there
- **WHEN** an agent's `AGENTS.md` carries a writer other than the owner's
- **THEN** a launch or a new thread for that agent is refused loudly, and nothing is made

### Requirement: A launch's time is bounded
A launch's `at`, at the door and through `ta`, SHALL be seconds since the epoch, at most a year and a day ahead, and refused loudly otherwise; one already past SHALL run now.

#### Scenario: Milliseconds sent for seconds
- **WHEN** a client launches with `at` in milliseconds, or at 1e300
- **THEN** the launch is refused as invalid, and no thread, work row or alarm is made

### Requirement: The storage gauge fails loudly before the object is full
The object SHALL read its database size against its limit (10 GB) before each new thread or launch and in each `threads` lane pass, tell the owner once past 70% (`storage_high`), and past 90% tell the owner (`storage_full`) and refuse each new thread and launch loudly with the fixed code `storage_full`, while existing threads go on. The gauge is a safety net only; the fix is one pi Session per thread, wiped whole when the thread is dropped.

#### Scenario: The object nears its limit
- **WHEN** the owner's storage passes 90% of its limit
- **THEN** a new launch is refused `storage_full`, a message to an existing thread is answered, and the owner's notices hold `storage_full`

### Requirement: The bounded waits are shared
The object SHALL keep at most 256 pending waits, at most 16 of any one outside client's, and the clients together SHALL leave 32 for the owner; a wait refused for room SHALL answer `running` at once, the operation still submitted.

#### Scenario: One client fills its waits
- **WHEN** a client holds 16 waits and sends a 17th, and every client's together fill theirs
- **THEN** each further client wait answers `running` at once, and the owner's message is still waited on and answered

### Requirement: The agent's doors admit and scope their caller before any pi call
`agentSubmit` and `agentRun` SHALL admit the caller and check its scope (`converse`, `run_graph`) for the agent it names, in the universe its one enabled agent is registered in, in their first unit, before any thread is made or any input submitted; a thread SHALL be stamped where its caller acts, and its recording unit SHALL re-check the scope there. An outside client's text SHALL reach pi only inside the untrusted envelope naming the client; the owner's as typed.

#### Scenario: A scoped client without converse
- **WHEN** a client granted `read_files` only sends a message, or holds `converse` for `main` only and names another agent
- **THEN** it is refused "outside client capability not granted", no thread is made and pi is handed nothing

### Requirement: The agent's work is addressed by thread, the caller's own
Every agent door SHALL address work by a thread id resolved against the calling client's own rows (the owner's for the owner), and hand pi only the session that row names, so no door reaches the root conversation; an operation id SHALL be its thread's id and pi's request id, and any operation not the caller's, a forged one and a pi session string included, SHALL be refused alike as no such operation.

#### Scenario: Another client asks for an operation
- **WHEN** one client, the owner, or anyone naming a pi session string asks the status of, or cancels, a client's operation
- **THEN** each is refused "no such operation", and the operation runs on

### Requirement: A message is one submission and is waited on a bounded time
`agentSubmit` SHALL find or make the caller's one `converse` thread with the agent, submit with pi's request id `sha256(client ∥ client_send_id)`, so a resend is pi's duplicate, queued behind a running turn or, with `steer`, placed after its current tool round, and wait at most `wait_ms` (25 s by default, 60 s at most), past which it SHALL answer `running` with the operation id. A grant change of the client or a policy write SHALL end the wait as a refusal and end the turn.

#### Scenario: A client resends a message
- **WHEN** a client sends the same `client_send_id` twice
- **THEN** the model is asked once and both answers are the same

#### Scenario: A resend after the thread was dropped
- **WHEN** a client resends a `client_send_id` after idle retention dropped its thread
- **THEN** the message runs again in a new thread, since pi deduplicates per conversation

#### Scenario: A steer during a tool round
- **WHEN** a message with `steer` arrives while the thread's run holds a tool call
- **THEN** pi places it after that tool round, before the run's next answer

### Requirement: A launch runs in a fresh thread, now or scheduled
`agentRun` SHALL make a fresh `run` thread configured from the agent's data files (`/workspace/agents/<agent>/AGENTS.md` as its instructions), submit the input now, or with `at` write a scheduled turn naming the thread, which the `work` lane hands to pi when due; a status SHALL be pi's receipts, or for work not yet handed its row.

#### Scenario: A scheduled launch
- **WHEN** a client launches an agent with `at` a moment ahead
- **THEN** its status reads `scheduled` until the work runner hands it over, then the run's answer

### Requirement: ta's agent:run launches in the run's own scope
`ta`'s `agent:run` SHALL launch on `agentRun`'s path, stamped with the calling conversation's authority where the launched agent acts, and SHALL be refused unless that holds `run_graph`, before pi makes a conversation.

#### Scenario: A launch of an agent the grant does not name
- **WHEN** a client's conversation that holds `run_graph` for `main` calls `agent:run` for another agent
- **THEN** the call is refused "outside client capability not granted" before pi makes a conversation

### Requirement: A grant or policy change ends the agent work it covers
After its own unit commits, in the same door, a grant set or revoke SHALL end its client's pending waits, close the live views of its threads and of every live conversation stamped for it, and abort each such conversation's work, background work included; a policy write SHALL do so for every conversation with live work and close every view. The door SHALL wait at most 5 s, and SHALL reject if the ending fails.

#### Scenario: A client revoked mid-turn
- **WHEN** a client is revoked while its turn, its subagent's and another client's run
- **THEN** the client's turn and its subagent's settle `unanswered: aborted`, its views hear `end`, its waits end, and the other client's turn runs on

#### Scenario: A revoke during a model call
- **WHEN** a revoke lands while a client's turn's model call streams
- **THEN** the turn is aborted, the upstream request cancelled, and its reservation settles `unknown` through the owner's usage

### Requirement: A scheduled turn runs in its own thread, handed once
A scheduled turn SHALL name a thread made at its own client and generation, and the runner SHALL check that again, and that the stamp stands, before it marks the row and submits with the row's `work_id` as the operation id, queued behind a busy run; a second hand-off SHALL be pi's duplicate. A status SHALL be pi's pending record, then how it settled; any failure but its own timeout SHALL be the call's.

#### Scenario: A second hand-off of the same work
- **WHEN** the runner hands the same `work_id` over twice
- **THEN** pi answers the second `accepted: false`, the row is handed, and the model runs once

### Requirement: The model is offered exactly read, write, edit, bash and ta
Every conversation the object creates, the root, a thread, a fork and a subagent's included, SHALL offer the model exactly `read`, `write`, `edit`, `bash` and `ta`, written in pi's `modelTools` in every creating commit over whatever pi copied, and nowhere else. Every other tool SHALL be registered callable by tools and reached through `ta`; a tool the model is not offered SHALL be unavailable to the model's own call. No source SHALL build a tool result `control`, which could add a model tool.

#### Scenario: A package registers a tool the model could call
- **WHEN** a tool registered callable by the model, and a deferred one, are installed, and the model calls each directly
- **THEN** no request offers either, each call settles "not available", and a fork's and a subagent's conversation still offer the five, whatever their parent's tools drifted to

### Requirement: The five are held where the model is reached
The model provider SHALL refuse, in each call's reservation unit and with the fixed code `tool_surface`, a request whose own transcript offers the model any tools but exactly the five, a request offering none excepted; and a model call of any conversation, the owner's included, whose stored model tools are not exactly the five SHALL be refused the same. Nothing refused SHALL be sent.

#### Scenario: A hook's result widens the model's tools
- **WHEN** a hook replaces `ta`'s result with one whose `control` adds a tool, and pi adds it to the conversation's model tools
- **THEN** the run's next request is refused `tool_surface` and never sent, and so is every later call of that conversation

### Requirement: What the model is offered has one source
The provider SHALL send the gateway exactly the messages whose offered tools it checked, no other context field and no caller's payload hook, and the gateway SHALL refuse a context carrying `tools` or `systemPrompt`.

#### Scenario: A caller's payload hook adds a tool
- **WHEN** a model call's context carries `tools` and a system prompt beside its messages, and its options a payload hook that adds a tool
- **THEN** the gateway receives a context of the messages alone, and a context sent with either field is refused `invalid_request`

### Requirement: A package never shadows the object's tools or names
A package that provides or wraps a tool named as a model tool, a platform capability or a `ta:` name, or provides a tool not callable by tools, SHALL be refused at install and kept as an owner notice, the frozen copy it is checked as being the one installed; `ta`'s catalog SHALL fail loudly on a tool that takes a platform capability's name.

#### Scenario: A package provides a tool named read
- **WHEN** a package provides a tool `read`, another wraps `bash`, and another provides a tool only the model may call
- **THEN** each is refused with a notice naming why, and the object's own tools are unchanged

### Requirement: ta's catalog lists what the conversation may call
`ta` SHALL take one object, `{op: "catalog", query?, limit?, offset?}` or `{op: "call", name, arguments}`, refusing any other field. The catalog SHALL list the platform capabilities the conversation's authority may call, then the tools registered callable by tools, deferred ones marked, each with its JSON Schema, ranked by `query` as pi's `tool_search` ranks, and paged.

#### Scenario: A scoped client reads the catalog
- **WHEN** a client granted `read_files` only asks `ta` for its catalog
- **THEN** it lists `wake:list`, `files:usage` and `model`, and no policy capability and no tool

### Requirement: ta's call runs a platform capability or reaches a tool
A call SHALL run a platform capability in the object, or reach a tool registered callable by tools through pi's `executeTool`, returning its structured output, diagnostics and error flag; any other name, the file tools, `ta` itself and the `ta:` names SHALL be refused loudly.

#### Scenario: The model calls a file tool through ta
- **WHEN** the model calls `ta` with `{op: "call", name: "bash"}`, or names `ta:catalog`
- **THEN** the call fails "unknown capability: bash", or "the ta: names are the ops' own", and nothing runs

### Requirement: A ta capability is a read only when classified one
Each platform capability SHALL be classified a read or an effect, and one not classified a read SHALL be an effect. A read SHALL write nothing; an effect SHALL re-check the conversation's authority at its effect: in its own unit of the write gate, or for a nested model call in its reservation and dispatch.

#### Scenario: A revoke lands before a proposal's unit
- **WHEN** a client granted `propose_policy` calls `policy:propose` and its grant is revoked after `ta`'s check but before the proposal's unit runs
- **THEN** the unit refuses with "outside client authority no longer stands" and no proposal is held

### Requirement: The v1 platform capabilities hold to their doors' needs
The platform capabilities SHALL be `policy:show` (a read, the owner's alone), `policy:propose` (an effect needing `propose_policy`, held for the owner's card under the proposer's own share), `wake:list` (a read of only the calling authority's own scheduled work), `files:usage` (a read needing `read_files`), `model` (an effect) and `agent:run` (an effect needing `run_graph`), each registered in `ta.authority`'s map with that need.

#### Scenario: Outside clients ask for the owner's policy
- **WHEN** a full client and a scoped client call `policy:show`, and a client without `propose_policy` calls `policy:propose`
- **THEN** each is blocked with its fixed reason and a receipt, while a client granted `propose_policy` holds a proposal for the owner's card

### Requirement: ta re-checks its authority inside its own execute
`ta` SHALL re-derive its capability's need from the arguments it actually received and re-check the conversation's authority, the stamp's standing included, before it acts, keeping a refusal receipt; `ta.authority`'s `beforeTool` SHALL be only the early gate. A nested call SHALL pass `ta.authority` with its parent named.

#### Scenario: A later hook rewrites an approved call
- **WHEN** a scoped client's `ta` call of `files:usage` passes the early gate and a later hook rewrites it to `policy:show`
- **THEN** `ta` refuses it with "only the owner may use this capability", and the policy is not read

### Requirement: A nested model call spends as its run's own calls do
`ta`'s `model` capability SHALL call the object's model source on the conversation's provider session and its call's telemetry context, so it is reserved, dispatched and settled against the owner's model policy and charged to the turn at the origin of its causal chain; a refusal SHALL be the call's error.

#### Scenario: A run that asks a model through ta
- **WHEN** a run's model calls `ta`'s `model`, then answers
- **THEN** the run's three gateway requests are reserved and settled on one turn, counted against that turn's caps, and a later input's request is another turn
