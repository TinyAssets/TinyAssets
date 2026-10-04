## ADDED Requirements

### Requirement: Platform state that decides authority is not writable by the party it decides about

Platform state the daemon reads to decide what a command center is permitted to do SHALL NOT live inside that command center's folder, because the command center's own processes can write there. It SHALL live under the daemon-owned per-command-center sidecar directory, which no jail binds. A database found at a former in-folder path SHALL NOT be adopted as authoritative.

#### Scenario: A command center cannot forge its own consent
- **GIVEN** a command center whose own processes create an effector-consent database in its folder, holding a row granting an effect its owner never approved
- **WHEN** an effect is dispatched and the consent gate is consulted
- **THEN** the gate reads the daemon-owned sidecar database, the planted file is never adopted, and the effect is refused for want of consent

#### Scenario: A planted link at the old name changes nothing
- **WHEN** a link is planted at the former in-folder database name, pointing at another command center's database
- **THEN** the consent answer is unaffected, because the name is no longer read

### Requirement: The enumeration of what may stay is enforced, not written down

A test SHALL assert that no platform path helper resolves inside a command-center folder, with an explicit allowlist for state a command center's own code must read through its jail. A store added later SHALL fail that test rather than silently join the problem, and the account-deletion sweep SHALL be derived from the same enumeration so a relocated store cannot be missed.

#### Scenario: A new store placed inside a command center fails
- **WHEN** a path helper is added or changed so that it resolves inside a command-center folder, and it is not on the allowlist
- **THEN** the enumeration test fails and names the helper

#### Scenario: Deleting an account leaves no sidecar
- **WHEN** an account is deleted
- **THEN** no file belonging to any of its command centers remains under the sidecar directory

### Requirement: The move is one-way, resumable, and refuses an unaccountable state

The migration SHALL run under the exclusive data-layout lock before any role opens the data, and the layout marker SHALL refuse an image that predates the move. For each command center it SHALL be idempotent. Where both the in-folder and the sidecar database exist it SHALL refuse that command center loudly and change nothing, because that state is either an interrupted run or a planted file and guessing is how a forged file gets blessed. A migrated in-folder file SHALL be renamed aside rather than deleted, so the prior state is recoverable.

#### Scenario: Running the migration twice changes nothing
- **WHEN** the migration runs on a volume it has already migrated
- **THEN** it makes no change and the roles start

#### Scenario: An interrupted migration does not half-apply
- **WHEN** the migration is killed part-way through
- **THEN** the layout marker records that the move is in progress, and the next start resumes it before any role opens the data

#### Scenario: Both copies present is a refusal, not a merge
- **WHEN** a command center has both an in-folder and a sidecar database
- **THEN** the migration refuses that command center by name and leaves both files untouched

### Requirement: No consent survives the move, and the re-grant is one click at first use

The migration SHALL carry no existing consent row forward. Because no provenance record exists for rows written before this change, a carried row would be indistinguishable from the forgery this change exists to prevent, so the sidecar database SHALL be created empty and the prior file SHALL be renamed aside rather than read.

The resulting re-grant SHALL NOT be a migration step, a batch, or a pre-emptive prompt. The **first use** of each effect SHALL raise the ordinary consent ask, as a single owner-confirmable item, carrying the same sink and destination it always did. Its wording SHALL say that this is a one-time re-confirmation following a security move, so that an owner reading it understands why it appeared and does not read it as a malfunction or a new request.

#### Scenario: A consent granted before the move does not survive it
- **GIVEN** a command center whose in-folder consent database granted an effect before the migration
- **WHEN** the migration has run and that effect is dispatched
- **THEN** no consent is found, the effect does not proceed unasked, and the prior file remains on disk renamed aside

#### Scenario: The re-grant is the ordinary ask, with a reason
- **WHEN** the first post-migration use of an effect finds no consent
- **THEN** the owner receives the ordinary consent ask for that exact sink and destination, confirmable in one action, worded as a one-time re-confirmation after a security move

#### Scenario: An unattended effect waits rather than proceeding
- **WHEN** an automation's first post-migration run needs a consent and no owner is present to confirm it
- **THEN** the effect waits on that ask and does not proceed, and the wait is visible rather than silent
