## ADDED Requirements

### Requirement: Platform state lives outside every agent environment, in the agreed layout

Every per-command-center and per-account platform record SHALL live under the
cell's `.platform/` root, resolved only through the platform path resolver.
The shared root databases keep their location.

Per-command-center state SHALL live under `.platform/cc-<ulid>/`. It covers:
the trusted policy files `soul.edit.md` and `dispatcher_config.yaml`; the
credential vault and CLI credentials; the run, consent, usage, attention,
conversation and session stores; rules; auto-review results; activity records;
pending effects; proposals; import quarantine; the browser profile; the id
marker; lease, seat, slot, lock and stamp state; and upload custody records.

Per-account platform state SHALL live under `.platform/accounts/<account_id>/`.
Account-owned rows in the shared root databases MAY stay there, served
through account-scoped views.

User content, including the agent-editable `soul.md`, `soul_versions/` and
`config.yaml`, SHALL live under `cc-<ulid>/`, and the daemon SHALL read it as
untrusted. No box, jail, extension process or browser sandbox SHALL mount
or reach `.platform/`. The platform SHALL refuse to open a platform store found
inside user content.

#### Scenario: A pre-created consent database is refused
- **WHEN** an agent creates a file named like the consent database inside its user content, carrying an active consent row
- **THEN** the platform never opens it, and the consent gate reads only the `.platform/` store

#### Scenario: No agent environment sees the vault
- **WHEN** any process in any box or jail lists or opens every path it can reach
- **THEN** no `.platform/` file is among them

### Requirement: Cross-user transactional domains live in Postgres, fed by outboxes beside their causes

The catalog, ledger, inbox and market SHALL be stored in Postgres behind
`TransactionalStore`. An effect on them SHALL be written to an outbox table in
the same SQLite database, and the same transaction, as its cause. The effect
SHALL be delivered at least once. Postgres SHALL apply it idempotently,
deduplicated by its origin store and outbox id. State owned by one account
SHALL be reached through `store_for(account)`, which MAY be an account-scoped
view over a shared store or a per-account store.

#### Scenario: A redelivered effect applies once
- **WHEN** an outbox row is delivered, its acknowledgement is lost, and the pump delivers it again
- **THEN** Postgres applies it once

### Requirement: SQLite stores meet a version floor and replicate continuously off-region

Every runtime image SHALL link SQLite 3.51.3 or later. It SHALL assert this at
startup, and SHALL refuse to start below the floor. Every SQLite store SHALL
replicate continuously to off-region object storage with point-in-time
restore, and exactly one writer SHALL hold each replica path.

#### Scenario: An old SQLite refuses to start
- **WHEN** an image linking SQLite 3.46.1 starts
- **THEN** startup fails loudly, naming the floor

#### Scenario: A regional loss is recoverable
- **WHEN** the primary region is lost
- **THEN** every SQLite store restores from the off-region replica to within seconds of the loss
