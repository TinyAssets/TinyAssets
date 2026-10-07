## ADDED Requirements

### Requirement: One immutable extension revision
The system SHALL validate one v2 manifest and bind all declared contributions
to the digest of its complete package bytes.

#### Scenario: Editing an installed package
- **WHEN** working files change after installation
- **THEN** the installed revision remains unchanged and the update requires a new activation

### Requirement: Activation never grants authority
The system MUST bind lifecycle state to authenticated owner, center and agent,
and MUST intersect activation permissions with current authority on dispatch.

#### Scenario: Foreign or revoked binding
- **WHEN** another owner references an installation or its grant is revoked
- **THEN** no new daemon-mediated contribution dispatch or private metadata access is permitted

### Requirement: One revocation fences every contribution
The system SHALL fence tools, hooks, commands, UI and MCP with one activation generation.

#### Scenario: Cleanup fails
- **WHEN** revoke commits but a backend projection cannot be removed
- **THEN** the stale projection cannot initiate new daemon-mediated effects and cleanup failure is visible; pre-U1 code already running in a bash jail is not claimed terminated

### Requirement: Recipients bring local authority
Shared command-center packages MUST carry code and logical connection needs only.

#### Scenario: Recipient installs shared extension
- **WHEN** a recipient imports a package
- **THEN** it is inert until locally activated and cannot use the author's grants

### Requirement: Execution remains isolated
The system MUST keep untrusted execution in the current jail and MUST refuse
package-scoped credential or stdio admission until U1 supplies that boundary.

#### Scenario: Stdio declared before admission exists
- **WHEN** a valid package declares a stdio MCP server
- **THEN** discovery reports it unavailable and the daemon does not execute it


### Requirement: Main-compatible connection contributions
Remote MCP and git contributions SHALL use existing credential-blind broker and
egress authority with private revision-bound local bindings, without waiting for U1.

#### Scenario: Revoked or replaced local connection
- **WHEN** a grant is revoked or its connection incarnation changes
- **THEN** the extension refuses further authenticated dispatch without exposing credentials

### Requirement: Outside clients retain independent authority
Verified outside client identity SHALL carry exact owner/client/family/generation
scopes through signed launches, queued run recovery and scheduled work. Protected
owner sessions SHALL exclusively edit grants. A durable switch SHALL default deny.

#### Scenario: Revoke while an effect is in flight
- **WHEN** an owner revokes a client during an already admitted network effect
- **THEN** revocation commits without waiting for network work and later admissions refuse; the earlier outcome is not replayed or falsely claimed undone

### Requirement: Observational hooks do not replay completed effects
Automatic hooks SHALL run in the current jail under the triggering turn's grants.
Hook failures and missing-grant skips SHALL be visible as private lifecycle evidence.

#### Scenario: A turn_end hook fails
- **WHEN** the turn has completed its effects but its turn_end hook fails
- **THEN** the completed result remains completed and the hook diagnostic is separately discoverable
