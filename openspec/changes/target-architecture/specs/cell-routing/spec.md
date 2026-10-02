## ADDED Requirements

### Requirement: Every request is routed to its user's home cell

Every account SHALL record a `home_cell`, and all of the account's command
centers SHALL live in it. Sign-in SHALL mint a signed cell claim. The edge
SHALL route each request by that claim, falling back to the global lookup when
the claim is absent. No code path SHALL branch on the number of cells. While
only one cell exists, every claim SHALL name it.

#### Scenario: One cell today
- **WHEN** any signed-in user sends an MCP request
- **THEN** the edge routes it to the cell named by the user's claim, which is the single cell `c0`

### Requirement: Moving a user bumps an ownership generation that stale requests cannot pass

Moving an account between cells SHALL be one consistent export, then an import,
then a `home_cell` change and an ownership-generation bump. A cell SHALL refuse
a request or a signed claim carrying an older generation than the account's
current one.

#### Scenario: A stale route is refused
- **WHEN** a request carrying generation 1 reaches the old cell after the account moved and its generation became 2
- **THEN** the old cell refuses it and serves nothing

### Requirement: Externally triggered requests are deduplicated by idempotency key

Every externally triggered request SHALL carry an idempotency key, and the cell
SHALL execute each key at most once. Only requests the cell deduplicates SHALL
be retried automatically by the edge.

#### Scenario: A retried webhook runs once
- **WHEN** the same inbound webhook delivery arrives twice with one idempotency key
- **THEN** its effects happen once, and the second delivery returns the first result
