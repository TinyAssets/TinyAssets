## ADDED Requirements

### Requirement: Installed extensions run in a remote box
A remote box bash launch SHALL receive the bound owner's active, enabled
extension revisions and SHALL run their tools, commands and hooks through the
same engine dispatcher, grant and fences as local ta.

#### Scenario: Installed tool in a remote box
- **WHEN** an owner activates an extension and remote bash calls its tool
- **THEN** the pinned revision runs in the box and its result is returned

#### Scenario: Parity with local ta
- **WHEN** the same owner runs `ta search` and `ta describe` locally and remotely
- **THEN** both report the same capabilities and availability

### Requirement: Content-addressed, credential-free delivery
Delivery SHALL carry only installed package bytes whose SHA-256 the trusted host
verified against the revision. It SHALL NOT carry host paths, bindings, grants or
credentials. The box SHALL stage them read-only for one launch.

#### Scenario: Box inspects delivered files
- **WHEN** bash lists the staged extension tree and tries to modify it
- **THEN** only package files at mode 0555 are present, writes fail and no host
  secret or path appears

### Requirement: Owner-bound mounts and revocation
Only the trusted host SHALL claim delivered revisions. The engine SHALL re-check
every claimed revision against the bound owner's current active state. Revoked or
uninstalled revisions SHALL NOT be delivered or dispatched.

#### Scenario: Cross-owner or forged revision
- **WHEN** another owner's active revision is called or claimed as mounted
- **THEN** it is absent from search, refused at dispatch and never delivered

#### Scenario: Revoke
- **WHEN** an owner revokes an extension
- **THEN** the remote call is refused and later launches do not receive its files

### Requirement: Fail closed on delivery failure
A delivery that is unreachable, malformed or fails its digest check SHALL refuse
the bash call with `remote_extension_delivery_failed` before any command runs.

#### Scenario: Tampered or lost delivery
- **WHEN** the delivered blob does not match its revision or the engine is unreachable
- **THEN** the call is refused with outcome `not_sent` and no command runs

### Requirement: Turn hooks follow the turn's route
Turn lifecycle hooks SHALL run on the turn's own tool route. On the thin loop
that is the turn's bound box.

#### Scenario: Thin-loop turn start
- **WHEN** a thin-loop turn with a box fires `turn_start`
- **THEN** the hook runs in that box and not in the platform's local jail
