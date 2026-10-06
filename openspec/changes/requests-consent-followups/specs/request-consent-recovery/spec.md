## ADDED Requirements

### Requirement: Clearing is recoverable for every ask
The rail and on-demand guidance SHALL distinguish clear/decline from explicit mute for every ask kind, including connect and reconnect.

#### Scenario: Need returns
- **WHEN** a cleared ask is needed again
- **THEN** the agent can raise it again; only an explicit don't-ask-again suppression blocks it, and connection controls remain available.

### Requirement: Consent classification fails closed
Every validated action SHALL have an explicit consent or non-consent classification, checked by a regression test. Unknown effectful actions MUST require protected owner proof.

#### Scenario: A new action is added
- **WHEN** an action is accepted without classification
- **THEN** the classification test fails and a bearer answer is refused.

### Requirement: Outstanding consent flows are owner bound and revocable
Generic OAuth SHALL refuse replay and foreign handles. Owner revocation SHALL cancel outstanding generic, hosted and inline connection flows for that owner alone.

#### Scenario: Owner revokes while sign-in is outstanding
- **WHEN** revoke_owner completes
- **THEN** that owner's outstanding flows cannot complete and another owner's flows remain usable.

### Requirement: Install digest covers displayed safety findings
The install plan digest SHALL include safety findings.

#### Scenario: Safety review changes
- **WHEN** safety findings change with identical package bytes and placement
- **THEN** the plan digest changes.
