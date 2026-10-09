## ADDED Requirements

### Requirement: A delivery's receiving owner answers it, and the sender reads the answer

The receiving owner of a delivery SHALL be able to record what came of it with
`write_graph target=receiver operation=answer` and `{delivery_id, outcome, note}`,
where `outcome` is `resolved` or `declined` and `note` is at most 4000 characters.
Only the principal and command center recorded as the delivery's receiver SHALL
be able to answer; any other caller, the sender included, SHALL get
`receiver_or_link_not_found` and nothing SHALL be recorded. A later answer SHALL
replace the earlier one.

Every delivery receipt SHALL carry `outcome`: `pending` until answered, then the
answer's outcome, with `answer {answered_at, note}`. The `note` SHALL be returned
inside the untrusted envelope (`untrusted`, `source`, `notice`, `content`).
`read_graph target=deliveries` SHALL list only the caller's own sent deliveries
from that command center.

#### Scenario: A resolved patch request reads back as resolved

- **WHEN** a command center files a patch request and the intake's owner answers its delivery `resolved` with a note
- **THEN** the sender's `read_graph target=delivery` and `target=deliveries` show `outcome` `resolved`
- **AND** the note is inside the untrusted envelope

#### Scenario: Nobody but the receiving owner answers

- **WHEN** the sender or an unrelated owner answers the delivery
- **THEN** the call returns `receiver_or_link_not_found` and the receipt stays `pending`
- **AND** the unrelated owner cannot read the receipt and lists no deliveries

### Requirement: The sender's next turn is told once when a request it sent is answered

The sender's next main-thread turn SHALL be told once when an answer lands: the
next turn of the principal that sent the delivery, in the command center it sent
from, SHALL receive one
`platform` history notice naming the delivery and its outcome, telling the agent to
re-check any workaround or belief in its own files that depended on it, and
carrying the note inside the untrusted envelope. The notice SHALL be marked told in
the transaction that reads it, so later turns SHALL NOT repeat it unless a new
answer replaces it. The notice SHALL NOT be added to the resident system prompt,
and no other principal or command center SHALL receive it.

#### Scenario: The notice arrives exactly once

- **WHEN** the sender converses twice after its delivery is answered
- **THEN** the first turn's model input names the delivery as resolved and the second does not
- **AND** neither turn's system prompt carries it
