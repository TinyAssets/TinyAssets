## ADDED Requirements

### Requirement: Each secret lives only in the one process that consumes it; the broker is the narrow exception

A platform or cloud-account secret SHALL be present only in the environment or
memory of the single process that consumes it. That covers the hosting-provider
API token, the payment secret key, the identity-provider management key and the
tunnel token. None of them SHALL be held by the frontends, the execution owner,
engine MCP children, boxes or CLIs. The hosting-provider API token SHALL be
held only by CI. The vault decryption key SHALL be held only by the credential
broker. The broker SHALL accept only requests bound to an exact owner,
connection and grant, plus box-socket requests authenticated to their box. It
SHALL perform upstream calls and OAuth refresh itself. It SHALL NOT execute
request content.

#### Scenario: The request-serving processes carry no platform secret
- **WHEN** the environment variable names of the frontends, the execution owner and every engine child are listed
- **THEN** none names a hosting-provider token, payment secret key, identity-provider management key or tunnel token

#### Scenario: The vault key never reaches the loop
- **WHEN** the execution owner's memory and environment are inspected
- **THEN** the vault decryption key is absent, and model calls still succeed through the broker
