# canary-sign-in Specification

## Purpose
The deploy canary's own sign-in, for one pinned synthetic account, when the
identity provider's hosted page refuses headless browsers. Never a second way
for a real user or an attacker to reach an owner session.

## Requirements
### Requirement: The door exists only for the one configured account
The deployment SHALL enable the door only when the host configures BOTH a
shared door key of at least 32 bytes and the canary's provider user id, SHALL
take the account from that configuration and never from the request, and SHALL
answer the same 404 as an absent route whenever either is missing.

#### Scenario: unconfigured deployment
- **WHEN** the door key or the pinned user id is unset, short, or malformed
- **THEN** the route answers 404 and no account is asked about upstream

### Requirement: Two proofs, and the pin decides the account
A caller SHALL present the shared key, compared in constant time, AND the
account's real password, which the identity provider authenticates
server-side. The server SHALL refuse unless the authenticated user id equals
the pinned one, and SHALL disclose no refusal detail before the key verifies.

#### Scenario: wrong key, wrong password, or another account
- **WHEN** the key does not match, the provider refuses the password, or the
  authenticated user is not the pinned canary
- **THEN** sign-in is refused, nothing upstream is asked before the key
  verifies, and no session is issued

### Requirement: Only a session the connector already accepts
The door SHALL return a session the identity provider issued, SHALL mint no
credential of its own, and SHALL return nothing that this server's own bearer
validation does not accept for the pinned account.

#### Scenario: upstream returns an unusable or foreign session
- **WHEN** the provider's token fails this server's validation or resolves to
  another account
- **THEN** the request fails and no token reaches the caller

### Requirement: No new cookie path and no approval authority
The door SHALL NOT issue the protected owner cookie or any other browser
session, so approval-answering authority stays with interactive sign-in.

#### Scenario: canary exercises capabilities
- **WHEN** the canary signs in through the door
- **THEN** it holds the ordinary bearer only, and its approval capability is
  reported as unexercised rather than passed

### Requirement: Bounded attempts and secret-free records
Attempts SHALL be rate-limited per caller with bounded limiter state, and
every outcome SHALL be logged as a code without key, password, address or
token material.

#### Scenario: repeated attempts
- **WHEN** a caller exceeds the attempt budget inside the window
- **THEN** further attempts are refused with a retry hint and reach no upstream
