# automation-triggers (delta)

## ADDED Requirements

### Requirement: A cron schedule runs in a named timezone

A cron automation SHALL carry an IANA timezone name, resolved once when it is
created and stored on the row. Resolution order SHALL be: the `timezone` the
creator passed; else the owner's stored account timezone; else `UTC`. An
unparseable or unknown name SHALL be refused at create rather than silently
falling back, because a silent fallback is how an owner is told 7am and given
midnight.

Evaluation SHALL read the zone from the automation's own row. Changing the
account timezone SHALL NOT move an already-created schedule.

#### Scenario: A schedule created by an owner whose zone is known

- **GIVEN** an owner whose stored account timezone is `America/Los_Angeles`
- **WHEN** they create a cron automation `0 7 * * *` passing no `timezone`
- **THEN** the automation's stored timezone SHALL be `America/Los_Angeles`
- **AND** it SHALL become due at `14:00Z` in winter and `14:00Z` in summer —
  that is, at 07:00 in the owner's clock, not the server's.

#### Scenario: A schedule created when no zone is known

- **GIVEN** an owner with no stored account timezone
- **WHEN** they create a cron automation passing no `timezone`
- **THEN** the automation's stored timezone SHALL be `UTC`.

#### Scenario: An owner overrides the zone

- **GIVEN** an owner whose stored account timezone is `America/Los_Angeles`
- **WHEN** they create a cron automation passing `timezone: "Europe/Berlin"`
- **THEN** the stored timezone SHALL be `Europe/Berlin`.

#### Scenario: An unknown zone name

- **WHEN** a create passes a `timezone` that `zoneinfo` cannot resolve
- **THEN** the create SHALL be refused, and the refusal SHALL name the field and
  state that an IANA name such as `America/Los_Angeles` is required.

### Requirement: Daylight-saving transitions fire exactly once

The schedule SHALL fire exactly once for a local wall-clock slot that a
transition makes non-existent or ambiguous:

- a slot that **does not exist** (spring-forward gap) SHALL fire at the first
  valid instant at or after it;
- a slot that is **ambiguous** (fall-back, two occurrences) SHALL fire at the
  **first** occurrence.

Whether a slot has already fired SHALL be decided by its **local date and slot**,
not by its UTC instant. A UTC-keyed decision fires an ambiguous slot twice,
because its two occurrences are different UTC minutes.

#### Scenario: The spring-forward gap

- **GIVEN** a cron `0 2 * * *` in `America/Los_Angeles`
- **WHEN** the local date is 2027-03-14, on which 02:00 local does not exist
- **THEN** the automation SHALL become due once, at `2027-03-14T10:00Z`, which
  is 03:00 local — the first valid instant after the gap.

#### Scenario: The fall-back repeat

- **GIVEN** a cron `0 1 * * *` in `America/Los_Angeles`
- **WHEN** the local date is 2027-11-07, on which 01:00 local occurs twice
- **THEN** the automation SHALL become due once, at `2027-11-07T08:00Z`, the
  first occurrence
- **AND** SHALL NOT become due again at `2027-11-07T09:00Z`, the second.

#### Scenario: An unaffected slot on a transition day

- **GIVEN** a cron `0 7 * * *` in `America/Los_Angeles`
- **WHEN** the local date is either 2027-03-14 or 2027-11-07
- **THEN** the automation SHALL become due once, at 07:00 local on that date.

### Requirement: A schedule is never shown without its clock

Every surface that displays or returns a cron automation's schedule SHALL state
the timezone alongside the time, in the form `7:00 AM America/Los_Angeles`. A
returned automation SHALL carry its `timezone` as a field, and `next_due_at`
SHALL remain an absolute instant so a caller can render it in any zone.

#### Scenario: Reading an automation back

- **WHEN** a caller reads a cron automation through the served automations
  surface
- **THEN** the payload SHALL include its `timezone`
- **AND** a human-readable schedule string naming both the local time and the
  zone.

### Requirement: The owner's timezone is captured from their client

The app SHALL report the browser's IANA zone
(`Intl.DateTimeFormat().resolvedOptions().timeZone`) on sign-in/load, and the
platform SHALL store it on the account. The value SHALL be validated as a known
IANA name before it is stored, and a client that reports nothing or an
unresolvable name SHALL leave the stored zone unchanged rather than clearing it.

#### Scenario: A signed-in user loads the app

- **GIVEN** a signed-in owner whose browser resolves `America/Los_Angeles`
- **WHEN** the app loads
- **THEN** the account's stored timezone SHALL become `America/Los_Angeles`
- **AND** a cron automation created afterwards with no explicit zone SHALL use
  it.

#### Scenario: A client reports a name the platform cannot resolve

- **GIVEN** an account whose stored timezone is `America/Los_Angeles`
- **WHEN** a client reports `Mars/Olympus_Mons`
- **THEN** the stored timezone SHALL remain `America/Los_Angeles`, and the
  request SHALL be refused rather than recorded.
