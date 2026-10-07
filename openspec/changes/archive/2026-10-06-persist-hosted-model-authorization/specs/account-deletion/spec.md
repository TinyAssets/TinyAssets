## ADDED Requirements

### Requirement: Pending hosted authorizations follow personal erasure
The existing satellite deletion sweep SHALL remove pending hosted authorization
metadata by owner, including former-home bindings, while preserving other owners.

#### Scenario: The account is deleted before callback
- **WHEN** an owner with pending hosted flows deletes their account
- **THEN** their pending rows disappear and cannot be used to authorize later
