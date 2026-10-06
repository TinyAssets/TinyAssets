## Why
Clearing a reconnect request was described as a permanent refusal. Follow-up review also identified missing regression coverage and owner flow revocation.

## What Changes
- Teach recovery for every cleared ask, preserving explicit mutes.
- Require explicit action consent classification and protect unknown actions.
- Cancel outstanding connect flows when an owner's sessions are revoked.
- Pin generic OAuth replay/isolation and install safety digest behavior with tests.

## Capabilities
### New Capabilities
- `request-consent-recovery`: recoverable asks and fail-closed consent lifecycle.
### Modified Capabilities
None.

## Impact
Pending request API/handbook, owner sessions, OAuth tests, package tests and plugin mirror. No new credential grants or storage columns.
