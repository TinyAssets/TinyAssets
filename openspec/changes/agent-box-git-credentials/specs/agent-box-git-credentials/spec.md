## ADDED Requirements

### Requirement: Git uses only the bound connection grant

The system SHALL authenticate smart-HTTP git only for the authenticated agent's
current command-center grant, its declared host, exact repository and git scope.

#### Scenario: Read grant cannot push

- **WHEN** a read-only grant requests receive-pack
- **THEN** the broker refuses before sending credentialed traffic

#### Scenario: Revoked or foreign grant

- **WHEN** the grant is revoked or belongs to another owner or command center
- **THEN** no new upstream request is sent and active streams close

### Requirement: Credentials remain outside the box

The system SHALL inject authentication only in the broker, preserve binary git
payloads, and refuse credential-bearing responses before returning them.

#### Scenario: Synthetic private git workflow

- **WHEN** a valid grant clones, fetches and pushes to a synthetic smart-HTTP server
- **THEN** the server verifies authentication, transferred objects match, and the
  real credential is absent from box files, environment, argv and outputs

#### Scenario: Redirect or destination substitution

- **WHEN** a request redirects or names a different host, port or repository
- **THEN** the broker refuses without forwarding authentication
