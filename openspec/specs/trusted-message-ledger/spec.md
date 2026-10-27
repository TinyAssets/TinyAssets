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
verbatim, and a private row SHALL be visible to and writable by its writer alone. One
owner's collections SHALL be bounded in rows and payload bytes, and a write past the bound
SHALL be refused. The board
SHALL be `collection='board'`; a published app's room SHALL be `room:<app>:main`. Only the
router's owner channel SHALL reach `COLLECTION`, with the store owner and the caller chosen
by the router (a published slug's publisher from the published directory, the caller from
its verified identity), never by a request field. The router SHALL answer
`/app/<slug>/state` itself, and SHALL never forward, ask or wake a box for it.

#### Scenario: Two writers move one board task at once
- **WHEN** two writers present the same etag for one `board` row
- **THEN** one commits and the other gets a conflict carrying the row as it now stands

### Requirement: Outside agents register from a verified identity
Registering or disabling an agent SHALL need the owner's protected-session proof, checked
against the live router row, and a refusal SHALL leave a receipt. An `api_key` agent SHALL
name the owner's own live key, checked by the router, which alone holds the key store; the
broker SHALL take that key's origin from the router and never read the store. An `mcp`
agent SHALL take its client and family from the caller's verified `outside_origin` and
SHALL be refused while that is absent. Only `pull` delivery SHALL be admitted until S3b.

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

### Requirement: A reader sees only its own mail
W3-R3, W3-R5. `MAILBOX_RECEIPT read` SHALL return only the reader's messages past its
cursor, moving them from `queued` to `read` with a `read` receipt; `ack` SHALL advance the
cursor and never move it back. A body whose hash no longer matches the ledger SHALL be
withheld and flagged `tampered`.

#### Scenario: A retried wake starts a second session
- **WHEN** a second session reads the mailbox after the first acknowledged through its read
- **THEN** it finds nothing new
