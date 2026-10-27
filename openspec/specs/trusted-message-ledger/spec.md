# Trusted message ledger

## Purpose
Hold who sent what to whom, and who read it, where no box can rewrite it (ADR-025, row S30),
and the shared rows of rooms and the board on one revision primitive (row S34).
S3a.1 builds the core for the 1:1 dm room; S3a.2 adds the doors and S3b the wakes.

## Requirements

### Requirement: The ledger lives in the broker
The agent registry, message ledger, receipts and cursors (`message_ledger.db`) and the
message bodies (`conversations.db`) SHALL be per-owner SQLite under the broker-private
`.broker/accounts/<sha256(owner)>/`, with `policy.db`'s modes and admission gate, and SHALL
open only once that account's policy is bootstrapped. Only the
broker SHALL open them, and only the router's owner channel SHALL reach `OUTSIDE_AGENTS`,
`ROOM_POST` and `MAILBOX_RECEIPT`. Account erasure SHALL remove both files.

#### Scenario: A box sends a ledger op
- **WHEN** the box channel sends any of the three ops
- **THEN** the broker drops the frame and opens no ledger file

### Requirement: One revision primitive
S34, W8, W9 Tier A. `conversations.db` SHALL hold `collection_rows`, the repo's one
compare-and-swap primitive: rows keyed by collection and row id, each with a revision, an
etag and the collection's next `seq`, written only while the presented etag is the row's
(empty creates it) and pulled after a `seq` cursor; payloads SHALL be stored and returned
verbatim, and a private row SHALL be visible to and writable by its writer alone. Rows and
payload bytes SHALL be bounded per writer other than the owner in one collection, per
collection and per owner, and a write past a bound SHALL be refused. Only the store's owner
SHALL clear a collection or one writer's rows in it; a clear SHALL never lower the
collection's `seq`. A clear leaves no tombstone: a reader pulling after its cursor does not
see cleared rows disappear, and a compare-and-swap on a cleared row gets a conflict with an
empty etag, from which the client recovers by creating the row again. A row SHALL count
against its creator's row share for as long as it exists, whoever takes it over; payload
bytes SHALL count against the current writer, so taking over a shared row charges its whole
size to the new writer. Clearing by writer SHALL remove every row that account created or
last wrote. The board
SHALL be `collection='board'`; a published app's room SHALL be `room:<app>:main`. Only the
router's owner channel SHALL reach `COLLECTION`, with the store owner and the caller chosen
by the router (a published slug's publisher from the published directory, the caller from
its verified identity), never by a request field. The router SHALL answer
`/app/<slug>/state` itself, and SHALL never forward, ask or wake a box for it; the
publisher SHALL clear it with `DELETE` from their protected owner session. Every
router-answered `/app/` path SHALL refuse a caller with an `outside_origin`, as the box does.

#### Scenario: Two writers move one board task at once
- **WHEN** two writers present the same etag for one `board` row
- **THEN** one commits and the other gets a conflict carrying the row as it now stands

### Requirement: Outside agents register from a verified identity
Registering or disabling an agent SHALL need the owner's protected-session proof, checked
against the live router row, and a refusal SHALL leave a receipt. An `api_key` agent SHALL
name the owner's own live key, checked by the router, which alone holds the key store; the
broker SHALL take that key's origin from the router and never read the store. An `mcp`
agent SHALL take its client and family from the caller's verified `outside_origin` and
SHALL be refused while that is absent. One outside identity SHALL be at most one enabled
agent. Only `pull` and `mcp_event` delivery SHALL be admitted; `routine_fire` and `webhook`
SHALL be refused until S9 (they wait for the credential-vault reshape, decided 2026-10-25) and
`a2a` until its own slice. An `api_key` agent SHALL NOT take `mcp_event`: it cannot
subscribe to MCP events, so it reads by `pull`.

#### Scenario: Another owner's key
- **WHEN** an owner registers an `api_key` agent naming a key minted by another owner
- **THEN** the registration is refused and no agent row exists

### Requirement: The sender is the verified identity
W3-R3. `ROOM_POST` SHALL take the sender from the router-verified identity's
`outside_origin` (none means the owner), never from the request. The room SHALL be
`dm:<agent_id>` for an enabled agent, and an outside sender SHALL post only in its own
room. A resend with the same client message id and body hash SHALL return the original; a
different hash SHALL be a conflict. One transaction SHALL write the message, its `queued`
recipient, the parent's `replied` state and a `reply` receipt with `reply_s` and `read_s`.
The owner's chain depth SHALL be 0; an agent's SHALL be its parent's plus 1.

#### Scenario: A client message id is reused with another body
- **WHEN** a sender posts a second body under a client message id it already used
- **THEN** the post is refused as `conflict` and the first message is unchanged

### Requirement: One open wake per ringing recipient
S3b, W3. In the same transaction as the message, `ROOM_POST` SHALL open one `ledger_wakes`
row per recipient whose delivery method rings (`mcp_event`), or leave that recipient's open
(`pending` or `rate_limited`) wake to cover it; the unique index `one_open_wake` holds at
most one per agent. A `pull` agent SHALL never be woken, and no wake SHALL open for the
message's own sender. Disabling an agent, or moving it off `mcp_event`, SHALL suppress its
open wake.

#### Scenario: The owner sends two messages before the wake goes out
- **WHEN** the owner posts twice to an `mcp_event` agent's dm room
- **THEN** one wake is pending and, once accepted, both messages read `woken`

### Requirement: AGENT_WAKE sends once, through the op journal
S3b, W3. `control_plane/outside_wakes.run_due` SHALL claim due wakes on the broker's shared
tick (the MCP event delivery pass), never in a box and with no timer of its own, taking at
most one due wake per owner per pass and starting each pass after the last owner the previous
one looked at, so no owner's agents crowd out another's; only an accepted wake SHALL make the
pass run again. Nothing SHALL be claimed while the broker's `TINYASSETS_OUTSIDE_DENY` is set;
wakes stay pending until it clears. Each due wake goes to `AGENT_WAKE`, which SHALL write may-have-sent to the broker's op journal (`broker/ops.py`)
before the first byte leaves through the owner's egress. A 2xx SHALL settle `sent` with a
`wake_accepted` receipt; a 429 SHALL settle `rate_limited` with `due` pushed by its
Retry-After and a `rate_limited` receipt, the messages staying queued; another answer or a
callback refused before any connection (owner egress, the public-address check, or
resolution) SHALL settle `failed`. A claim that finds the agent disabled or off `mcp_event`
SHALL suppress the wake and revoke the agent's callbacks. A send with no answer, or one abandoned past its lease
whose journal shows a byte may have left, SHALL settle `unknown` with a `wake_sent` receipt
and SHALL never be replayed; one the journal proves unsent SHALL be due again.

Owed, not built here: revoking an outside client's grant SHALL end its wakes and erase its
callbacks as disabling the agent does (M3, waits on #5343's broker-held grants, and blocks S31
reading converged once that lands); bounding `ledger_wakes` growth and the per-pass scan of
every owner's ledger belong to S4's caps (L5).

#### Scenario: The callback answers 429
- **WHEN** a wake is answered 429 with Retry-After 120 and another message arrives
- **THEN** no wake is sent for 120 seconds, and then one wake covers both messages

### Requirement: Wakes are doorbells
S31. A wake and its event SHALL carry identifiers only: the command center, the agent and the
mailbox cursor, never a message body, in the payload, the receipts or the wake row.

#### Scenario: A wake goes out for a message
- **WHEN** any wake for a message is sent, rate limited, failed or unknown
- **THEN** that message's body appears in no sent payload and no ledger or event store

### Requirement: A reader sees only its own mail
W3-R3, W3-R5. `MAILBOX_RECEIPT read` SHALL return only the reader's messages past its
cursor, moving them from `queued` to `read` with a `read` receipt; with `max_bytes` it SHALL
return whole messages under that budget, at least one, and SHALL mark and receipt only the
messages it returns. `ack` SHALL advance the cursor and never move it back, and never past
the reader's own newest message. A body whose hash no longer matches the ledger SHALL be
withheld and flagged `tampered`.

#### Scenario: A retried wake starts a second session
- **WHEN** a second session reads the mailbox after the first acknowledged through its read
- **THEN** it finds nothing new

### Requirement: The doors answer on the trusted side
S3a.2, ADR-025. The router SHALL answer `read_graph target=mailbox`, `write_graph
target=room_message` and `target=mailbox_ack` from `owner_router.TRUSTED`, `GET /api/mailbox`,
`POST /api/mailbox/ack` and `POST /api/mailbox/replies` for API keys, and the owner's
`/app/api/outside-agents` and `/app/api/agent-messages` behind the protected owner session,
all through `ledger_request`, and SHALL never forward, ask or wake a box for them. The
reader and sender SHALL be the verified `outside_origin`; until P0-3 an MCP caller without
one SHALL be refused, never read as the owner. The outside kill switches SHALL close the
agent doors before any ledger call. Every body an outside agent reads, and every outside
agent's body the owner reads, SHALL be in the untrusted envelope; a read's `cursor` SHALL
acknowledge through it first; a read SHALL return whole messages under the `read_graph`
ceiling, at least one, with `next_cursor` at the cut; a reply SHALL show its recorded
`reply_s`. The box SHALL answer these targets `served_on_trusted_side`.

#### Scenario: An API-key agent replies claiming to be the owner
- **WHEN** an agent's key posts a reply whose body says `"sender": "owner"`
- **THEN** the owner reads it from that agent, with its time to reply
