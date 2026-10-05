# connect-cross-user-nodes (delta)

## ADDED Requirements

### Requirement: The platform offers one configured patch intake, and only by consent

The platform SHALL name the patch intake it offers through configuration
(`TINYASSETS_PATCH_INTAKE_RECEIVER_ID`), never a universe id in code, because
the intake is owned by an ordinary user. With no intake configured, no request
is seeded, the rail read carries no `patch_intake` block, and no delivery
behaviour changes. A configured-but-invalid value SHALL be reported loudly, seed
nothing, and refuse every delivery to the intake rather than permitting one
unchecked.

Sending to the configured intake SHALL require an active `patch_intake`
effector consent in the sending universe whose destination is exactly that
intake's `receiver_id`. The refusal SHALL name itself
(`patch_intake_consent_required`) and SHALL apply before any cross-owner file
copy as well as at acceptance, so a refused sender moves no bytes. Receivers
the platform does not offer SHALL be unaffected: the receiving owner's own
exposure remains their whole authority.

#### Scenario: No intake is configured
- **WHEN** a universe's rail is read with `TINYASSETS_PATCH_INTAKE_RECEIVER_ID` unset
- **THEN** no consent request is seeded and the reply carries no `patch_intake` block

#### Scenario: Delivery without the grant is refused
- **WHEN** a universe with a connected output link delivers to the configured intake and holds no active `patch_intake` consent for it
- **THEN** the delivery is refused naming `patch_intake_consent_required`, no delivery row is written, and no file bytes are copied

#### Scenario: A revoked grant stops the next send
- **WHEN** the consent is revoked after a successful delivery
- **THEN** a further send on the same link is refused, including a retry of an already-accepted occurrence

#### Scenario: Another user's receiver is unaffected
- **WHEN** a universe delivers to a receiver that is not the configured intake
- **THEN** no `patch_intake` consent is required and the receiving owner's exposure alone decides

### Requirement: The consent request is seeded at first sign-in and never re-asked

The rail read SHALL seed one platform-origin pending request offering the
configured intake, so it is present at a new user's first sign-in and at an
existing user's next one with no migration. The ask SHALL carry no fields and
require no secret. Seeding SHALL be idempotent, and SHALL NOT happen when the
grant is already held, when an ask for that intake is already pending, or when
the user has already answered, denied or cleared an ask for that same intake.
Whether the user already decided SHALL be keyed on the intake's `receiver_id`,
not on the request's rendered text.

Approving the request SHALL record exactly one consent — sink `patch_intake`,
destination that `receiver_id` — and nothing else. Before writing it the server
SHALL re-check that the stored row still names the configured intake and that
the intake accepts this sender; on either miss the request SHALL stay pending
with the reason and no consent SHALL be written.

#### Scenario: A new universe's first rail read
- **WHEN** an owner reads the rail of a universe with no prior answer and no grant
- **THEN** one pending platform-origin request offering the intake is present, with no fields

#### Scenario: Repeated reads do not duplicate it
- **WHEN** the rail is read again while that request is pending
- **THEN** exactly one such request exists

#### Scenario: A declined or cleared ask is not re-seeded
- **WHEN** the owner denies, clears, or clears-with-"don't ask me this again", and the rail is read again
- **THEN** no new request for that intake is seeded

#### Scenario: Approval creates exactly the one grant
- **WHEN** the owner approves the request
- **THEN** the universe holds one active `patch_intake` consent whose destination is the configured `receiver_id`, the request is answered `allowed`, and the reply carries the intake's contract

#### Scenario: The intake changed since the ask was raised
- **WHEN** the configured intake no longer matches the stored row, or the intake does not accept this sender
- **THEN** no consent is written, the request stays pending, and the reply says why

### Requirement: The patch-intake consent is the owner's tap, never self-granted

Every answer to `grant_patch_intake`, including Deny and Clear, SHALL require
the protected interactive owner session. The ordinary bearer answer route
(including chatbot, MCP and CLI callers) SHALL refuse with
`interactive_approval_required`, leaving the request and consent unchanged.
The rail SHALL use the protected owner-session route and offer owner sign-in
when that session is absent. A bearer payload cannot supply owner proof.

`patch_intake` SHALL be a person-only consent sink: the agent's own channel-approval
verb SHALL refuse to write it, checked in the function that performs the grant so
the refusal does not depend on one entry point, and under every spelling that verb
accepts. The requirement to hold the consent SHALL follow the ask a universe was
actually offered, recorded per universe, so changing or removing the configured
intake cannot release an intake a universe already holds a link to. Approving
SHALL resolve the pending request before recording the consent, so a losing or
failed resolution never leaves an active consent behind a request nobody answered.

#### Scenario: Recovery after Clear or Deny still requires the owner
- **WHEN** an agent raises a replacement ask after the owner cleared or declined the previous ask
- **THEN** a bearer answer is refused without granting consent or resolving the replacement, and the owner can approve it through the protected session

#### Scenario: The agent approves the sink for itself
- **WHEN** it calls the channel-approval verb with the `patch_intake` sink, under either the `sink` or `channel_type` key
- **THEN** it is refused, no consent exists, and the owner's request is still pending

#### Scenario: The configured intake is unset or retargeted
- **WHEN** a universe was offered an intake, connected an output to it, and the configuration is then removed or pointed elsewhere
- **THEN** delivery to that intake is still refused without the grant

#### Scenario: Two answers race
- **WHEN** one caller approves while another dismisses the same request
- **THEN** exactly one wins, and a consent exists only if the approval was the winner

### Requirement: Served guidance points at the seeded request, never at a credential

The rail read SHALL carry, when an intake is configured, the intake's
`receiver_id`, its display label, whether the grant is held, and how to proceed.
Served guidance SHALL state that filing a patch request involves no token, URL
or credential, SHALL describe filing one when the grant is held, and SHALL
direct a universe without the grant to the already-seeded request rather than to
a connection or credential ask of its own.

#### Scenario: A universe without the grant is asked to report a bug
- **WHEN** it reads `read_graph target="pending_requests"`
- **THEN** `patch_intake.granted` is false and the guidance points at the seeded request instead of a credential ask

#### Scenario: A universe with the grant files one mid-turn
- **WHEN** it reads the rail
- **THEN** `patch_intake.granted` is true and it carries the `receiver_id` needed to read the contract, connect an output and deliver
