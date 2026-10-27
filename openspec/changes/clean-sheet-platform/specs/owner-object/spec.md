## ADDED Requirements

### Requirement: One object holds each owner's trusted state
Each owner's trusted state SHALL live in exactly one SQLite-backed Durable Object of the `OwnerDO` class, in that object's private storage. No trusted per-owner state SHALL live in a store shared between owners, and nothing in one owner's object SHALL be readable from another owner's.

#### Scenario: Two owners write the same row key
- **WHEN** Alice and Bob each write row `t` in collection `board`, each through their own object
- **THEN** each object holds only its own owner's row, and a pull in Alice's object never returns Bob's

#### Scenario: A full owner leaves another owner untouched
- **WHEN** Alice's object refuses a write because her collections are full
- **THEN** a write to Bob's object under the same bounds succeeds

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
The owner Worker's config SHALL bind nothing, at top level and in every environment, its keys drawn from an allow-list with no binding key, and its flags SHALL include `disable_ctx_exports`. It SHALL be the Worker's only wrangler config. Its source SHALL never name `exports`, SHALL import from `cloudflare:workers` only the classes it extends, and SHALL keep no module-level mutable state nor write through the global object.

#### Scenario: The object looks for its own namespace
- **WHEN** code inside the object reads `ctx.exports`
- **THEN** it is undefined, so no stub to another owner's object can be made

#### Scenario: A binding is added to the config
- **WHEN** a Durable Object, service, var or any other binding key is added at top level or inside an environment, or a second wrangler config file appears
- **THEN** the config guard fails CI

#### Scenario: Source reaches for exports or env
- **WHEN** a source reads `ctx.exports`, imports `exports` or `env` from `cloudflare:workers`, or writes through `globalThis`, `self` or `global`
- **THEN** a source guard fails CI, for every script extension under `src`

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
