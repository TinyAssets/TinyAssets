## ADDED Requirements

### Requirement: Hosted model authorization survives daemon replacement
Hosted PKCE bindings SHALL survive process restart until their original expiry,
without persisting authorization codes, verifiers, keys or raw flow handles.
One valid owner/home/preset/verifier-matching callback SHALL atomically consume
the binding before exchange; no uncertain exchange SHALL automatically replay.

#### Scenario: Deploy while the user authorizes
- **WHEN** the daemon restarts after begin and before an unexpired callback
- **THEN** the same authorized owner/home/verifier can complete exactly once

#### Scenario: Multiple workers receive the same callback
- **WHEN** two processes attempt to consume one valid binding concurrently
- **THEN** exactly one can proceed to provider exchange

#### Scenario: A mismatched callback arrives
- **WHEN** owner, home, verifier or preset does not match the binding
- **THEN** the attempt is refused without consuming another valid owner's binding
