# Agent box git credentials

## Purpose

Credential-blind smart HTTP for an owner's connected repository through the
broker and the served `/u` jail. Verified with a synthetic Linux git server;
production `/cc` deployment and real-user acceptance remain pending.

## Requirements

### Requirement: Git uses only the bound connection grant

The system SHALL authenticate smart-HTTP git only for the authenticated agent's
current command-center grant, its declared host, exact repository and git scope.

#### Scenario: Read grant cannot push

- **WHEN** a read-only grant requests receive-pack
- **THEN** the broker refuses before sending credentialed traffic

#### Scenario: Revoked or foreign grant

- **WHEN** the grant is revoked or belongs to another owner or command center
- **THEN** no new upstream request is sent and active streams close

#### Scenario: Authority changes during backpressure

- **WHEN** a grant is revoked, its scope changes or the broker generation advances
  while an upload or download is waiting for credit
- **THEN** the stream stops, rather than waiting for the peer to grant more credit

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

#### Scenario: Expired or foreign surrogate

- **WHEN** a route is used after its launch ends or on a different center's proxy
- **THEN** it refuses without opening an upstream request

### Requirement: Authenticated git setup does not disable local commands

The system SHALL refuse unavailable authenticated git authority without preventing
otherwise admitted local bash commands from running.

#### Scenario: Missing or ambiguous git authority

- **WHEN** identity, owner admission, broker, proxy or a unique repository grant
  is unavailable at launch
- **THEN** the launch installs no authenticated routes, reports a fixed diagnostic
  and lets local commands run without an ambient credential fallback
