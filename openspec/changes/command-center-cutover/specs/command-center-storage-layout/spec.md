## ADDED Requirements

### Requirement: Data carries a layout marker every process checks under a shared lock

The data root SHALL hold `.layout.json` with a `layout` number and a `state`. Every independent process that opens the data SHALL take a shared lock on `.layout.lock` and then read the marker, and SHALL refuse to open anything unless it knows the layout and the state is `stable`. A storage-shape migration SHALL take the lock exclusively, SHALL write `state: migrating` durably before its first change, and SHALL write the new layout with `state: stable` only after verification passes. A deploy SHALL NOT start an image on data whose marker it cannot prove that image understands.

#### Scenario: A crash mid-migration is refused
- **WHEN** a migration crashes after its first change
- **THEN** the marker says `migrating`, and every process and image refuses to start until the backup is restored or the migration finishes

#### Scenario: A reader cannot slip past a migration
- **WHEN** a process starts while a migration holds the exclusive lock
- **THEN** it waits, then reads the marker the migration left, and refuses unless that marker is stable and known

### Requirement: After the cutover no operational store names the retired word or id prefix

After the command-center cutover, no operational store SHALL contain the retired name `universe` in a table, column, key, value or file name, or an id in the form `u-<ulid>`, in any encoding. A person's verbatim content (uploads, run outputs, conversation text, agent-written brain files) SHALL be exempt and unchanged. Account deletion and export SHALL be proven on the migrated shape by direct row counts.

#### Scenario: The post-migration scan finds nothing
- **WHEN** the inventory runs against a migrated data root
- **THEN** it reports zero operational occurrences of `universe` names and `u-<ulid>` ids, and lists the exempt verbatim stores it skipped

### Requirement: A command center id is never shown to a person

The `cc-<ulid>` id SHALL be a machine value only. No user-facing surface (the app, the website, notification text) SHALL render it. Each SHALL show the command center's name, or "Your command center" when it has none. Served agent guidance SHALL NOT tell the agent to refer to a command center by its id.

#### Scenario: An unnamed command center
- **WHEN** a person opens the app for a command center with no name
- **THEN** the header reads "Your command center" and no visible text contains its `cc-` id
