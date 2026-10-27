# Trusted message ledger

## Purpose
Hold who sent what to whom, and who read it, where no box can rewrite it (ADR-025, row S30).
S3a.1 builds the core for the 1:1 dm room; S3a.2 adds the doors and S3b the wakes.

## Requirements

### Requirement: The ledger lives in the broker
The agent registry, message ledger, receipts and cursors (`message_ledger.db`) and the
message bodies (`conversations.db`) SHALL be per-owner SQLite under the broker-private
`.broker/accounts/<sha256(owner)>/`, with `policy.db`'s modes and admission gate. Only the
broker SHALL open them, and only the router's owner channel SHALL reach `OUTSIDE_AGENTS`,
`ROOM_POST` and `MAILBOX_RECEIPT`. Account erasure SHALL remove both files.

#### Scenario: A box sends a ledger op
- **WHEN** the box channel sends any of the three ops
- **THEN** the broker drops the frame and opens no ledger file

### Requirement: Outside agents register from a verified identity
Registering or disabling an agent SHALL need the owner's protected-session proof, checked
against the live router row. An `api_key` agent SHALL name the owner's own live key; an
`mcp` agent SHALL take its client and family from the caller's verified `outside_origin` and
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
