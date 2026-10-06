## ADDED Requirements

### Requirement: Center admissions are a broker-owned append-only log

The broker SHALL record every runtime center admission and retirement as a
generation-numbered `admit` or `retire` row in its private owner-identities
database, using the same serialized, `synchronous=FULL` transaction discipline
as identity reservations. Rows SHALL never be updated or deleted. The broker
SHALL derive each row's machine identity from the principal's existing
reservation and SHALL NOT accept it from a caller. A center SHALL have at most
one `admit` row and at most one `retire` row, a `retire` SHALL require a prior
matching `admit`, and a retired center SHALL never be admitted again. An
identical repeated `admit` SHALL return the existing row.

#### Scenario: A new user's first center is admitted
- **WHEN** the daemon reserves a new principal's identity and then requests
  `admit` for that principal's new center
- **THEN** the broker appends one row whose machine is that reservation and
  returns its generation
- **AND** a retried identical request returns the same row without appending

#### Scenario: Conflicting or recycled admissions refuse
- **WHEN** a request names a center already admitted to another principal, a
  retired center, a principal with no reservation, or carries a numeric
  identity field
- **THEN** the broker refuses and appends nothing

### Requirement: The mapper binds runtime centers only from broker log rows

The bounded mapper SHALL extend or shrink its binding table at runtime only on
an authenticated daemon `admit` or `retire` request that names a principal, a
center and a log generation. The mapper SHALL fetch that row itself over an
inherited, read-only broker channel authenticated by the mapper's exact PID and
host 300000 credentials. Before binding, it SHALL verify that the row matches,
that its generation is above the mapper's bootstrap generation, that the center
is unbound, that the machine is not bound to another principal, and that the
root carries the canonical label. Numeric identities and paths SHALL NOT be
request fields. Retirement SHALL require the owner's deletion fence and no
running cell for that center.

#### Scenario: A center created after startup runs its first cell
- **WHEN** a center is created, labelled, published and admitted after startup
- **THEN** the mapper binds it and an owner cell for it runs under the
  principal's dedicated UID and GID without a restart
- **AND** a cell request for any center before its bind refuses without a
  legacy fallback

#### Scenario: A forged or mismatched admission refuses
- **WHEN** the daemon names a generation that is absent, a `retire` row, a row
  for another principal or center, or a root whose group is not the row's machine
- **THEN** the mapper refuses that request, leaves its table unchanged, and
  later authenticated requests still succeed

### Requirement: New center roots are labelled without retained capabilities

A runtime center root SHALL be created with exactly the canonical migrated root
label (`1001:<machine>`, mode `02750`, the canonical access ACL, and no default
ACL). It SHALL be created by a setgid hand-off: a fixed, capability-free
`center-root` owner cell creates a setgid directory inside a daemon-private
staging directory, and the daemon creates the root inside it and renames it
into place without replacement. No process SHALL gain or retain a capability,
and the daemon SHALL NOT chmod the root or write its access ACL. The cell SHALL
receive no caller path, executable, environment, relay or credential. Any
read-back difference from the canonical label SHALL refuse the admission.

#### Scenario: The hand-off reproduces the migrated label
- **WHEN** a new center's root is created by the hand-off on the production
  data filesystem
- **THEN** its owner, group, mode and ACLs equal a forward-migrated root's
  label, and all process capability sets remain zero throughout
- **AND** another owner's cell cannot read, relabel or copy anything under it

#### Scenario: A kernel that clears the setgid bit stops the class
- **WHEN** any step of the hand-off loses S_ISGID or yields a different ACL
- **THEN** the admission refuses and no root is published

### Requirement: Admission is ordered and crash-safe

Center creation on the selected path SHALL run these steps in order: reserve
the identity, label the root, publish it, append the `admit` row, then bind in
the mapper. The center SHALL become usable only after the bind. After a crash
at any step, a retry or the next startup SHALL finish or discard the admission:
staging remnants are removed, and a published root with no row is adopted only
if its label matches the durable reservation of the owner its authority
database names. Nothing SHALL be guessed.

#### Scenario: A crash between publish and log append
- **WHEN** the daemon dies after publishing a labelled root but before the
  `admit` row commits, and the container restarts
- **THEN** startup appends the row and binds the center when the label matches
  its owner's reservation, and refuses when it does not

### Requirement: Deletion retires a center's binding

A whole-center owner-tree deletion SHALL append the center's `retire` row and
drop its mapper binding after the daemon pass removes the tree, and before the
deletion fence is finished and the intent is cleared. Each step SHALL be
idempotent on resume.

#### Scenario: Account deletion followed by a restart
- **WHEN** a migrated account is deleted and the container restarts
- **THEN** the retired center is absent from the startup bindings and startup
  completes without refusing on the smaller principal set

### Requirement: Restart accepts exactly the principal-set change the log explains

The volume journal SHALL record the admission-log generation it last reconciled.
On restart with a stable forward journal, the coordinator SHALL compute the
expected center set from the journal's principals plus the log rows since that
generation, and the inventory SHALL equal it after orphan adoption. A tree
outside that set, or a changed principal for an existing center, SHALL refuse.
A center the log admits whose tree is missing with no `retire` row SHALL follow
founder decision F1, which defaults to refusing startup. With no journal or a
stable reverse journal, startup SHALL seed `admit` rows for inventoried centers
that lack one and SHALL never infer a `retire`. Interrupted journals SHALL keep
exact configuration matching. Reverse migration SHALL return runtime-admitted
roots to the legacy label under the existing unrecorded-inode rule.

#### Scenario: Signup, new center and deletion between restarts
- **WHEN** a user signs up, another user adds a center, and a third account is
  deleted between two restarts
- **THEN** the second startup reconciles all three from the log, binds the two
  new centers, omits the retired one, and records the new generation

#### Scenario: An unexplained tree or owner change refuses
- **WHEN** a stable forward volume holds a center tree with no `admit` row and
  no adoptable label, or a center's owner differs from its log row
- **THEN** startup refuses before any mutation

#### Scenario: Reverse after runtime admission
- **WHEN** a volume with runtime-admitted centers is reverse-migrated, the
  legacy image creates more centers, and forward migration runs again
- **THEN** reverse restores those roots to the legacy label, and forward seeds
  rows for the legacy-created centers and labels them like a first volume

### Requirement: Admission is inert while the bounded client is absent

The system SHALL apply every admission, retirement and reconciliation rule in
this capability only when the role-split bootstrap has installed the bounded
mapper client. Legacy center creation and legacy startup SHALL be unchanged.

#### Scenario: Legacy image creates a center
- **WHEN** the daemon runs without the bounded client and creates a center
- **THEN** no admission row, staging directory or mapper request is produced
