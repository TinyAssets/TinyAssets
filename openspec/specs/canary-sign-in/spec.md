# canary-sign-in Specification

## Purpose
The canary signs in like any user. No account has its own sign-in path,
credential type, admission rule or host role, so a canary failure is a real
user failure and its fix repairs every account.

## Requirements
### Requirement: The canary is an ordinary account
The canary SHALL sign in once through the same system-browser flow the phone
and desktop apps use (`POST /app/native-sign-in`, then `/app/token`
`authorization_code` with `native_ref`, `code_verifier` and `return_secret`),
and SHALL renew through `/app/token` `refresh_token` with its rotating session
handle, exactly like a signed-in app. The platform SHALL hold no route, secret,
pinned user id or admission exception for the canary.

#### Scenario: no canary-only door
- **WHEN** a client POSTs to `/app/canary-sign-in`
- **THEN** the router answers as for any unknown app route, and no environment
  value names a canary account

#### Scenario: the canary renews like an app
- **WHEN** a deploy or the uptime canary needs a session
- **THEN** `scripts/canary_session.py` renews the stored handle through
  `/app/token`, stores the rotated handle before using the access token, and
  uses that token as an ordinary bearer on `/app` and `/mcp`

### Requirement: Each workflow keeps its own sealed chain
CI SHALL keep the session handle sealed with AES-GCM under
`TINYASSETS_CANARY_SESSION_KEY`, one chain per workflow (`deploy`, `uptime`),
restored from and saved to the Actions cache on `main` only. A run on any
other ref SHALL NOT renew a chain. With no saved chain, the chain's sealed seed
secret starts it. Saved chains SHALL be keyed by their seed's digest, so a new
sign-in starts a fresh chain and a dead chain's saves are never restored over it.

#### Scenario: an ended session
- **WHEN** renewal answers 401
- **THEN** the probe fails with `canary_signed_out`, and one ordinary sign-in
  (`scripts/canary_session.py begin` then `finish --set-secret <seed>`) restarts that chain

#### Scenario: a transient renewal failure
- **WHEN** renewal fails with any other status
- **THEN** nothing is stored, and the existing handle is still used on the
  next attempt

#### Scenario: a save that does not land
- **WHEN** the Actions cache does not hold a renewed session after saving it
- **THEN** it is saved once more under a fresh key, and if that does not land
  either the step fails with `canary_session_save_lost` naming the chain
