## ADDED Requirements

### Requirement: An owner's UI preferences are private to them and follow them across devices
The platform SHALL keep a UI-preference record per authenticated owner, per
agent id (`main` only, for now) and per viewport class (`phone` or `wide`), for
a fixed set of preference keys (`chat_cloud`). Only that owner SHALL read or write
it, through the app's identity-gated routes. The owner SHALL be taken from the
authenticated identity alone, and no request field SHALL name an owner.

#### Scenario: The owner signs in on a second device
- **WHEN** an owner placed the chat cloud on one browser and signs in on another with the same viewport class
- **THEN** the second browser lays the chat cloud out from the owner's record

#### Scenario: Another owner asks
- **WHEN** a signed-in owner reads or writes UI preferences
- **THEN** only rows keyed by their own authenticated identity are read or changed, whatever the request body or query contains

#### Scenario: No identity
- **WHEN** a request to the UI-preference routes carries no authenticated identity
- **THEN** it is refused with `authentication_required` and nothing is read or written

### Requirement: A UI preference is a bounded, validated value, not storage
The platform SHALL accept only known preference keys, the agent id `main` and
the two viewport classes. It SHALL refuse a value larger than 2 KiB, or one that
does not match that key's shape, with a reason and without writing anything.

#### Scenario: An unknown key or an oversized value
- **WHEN** an owner posts a key that is not `chat_cloud`, an agent id other than `main`, or a value over 2 KiB
- **THEN** the request is refused with a 400 and a reason, and no row changes

#### Scenario: A malformed chat-cloud value
- **WHEN** a `chat_cloud` value lacks `v: 1`, has a mode other than open or bubble, or has a non-finite coordinate
- **THEN** it is refused and the stored value stays as it was

### Requirement: UI preferences go with the account
Deleting an account SHALL remove every UI-preference row keyed by that owner,
and SHALL leave every other owner's rows unchanged.

#### Scenario: An account is deleted
- **WHEN** an owner deletes their account
- **THEN** none of their UI-preference rows remain, and another owner's rows are unchanged

### Requirement: The app works without the server record
The app SHALL lay the chat cloud out from the device's own copy when the server
record cannot be read, and SHALL keep that copy current on every placement. It
SHALL NOT move the cloud on a late server answer once the owner has started
moving it.

#### Scenario: Offline
- **WHEN** the UI-preference read fails
- **THEN** the chat cloud uses this device's saved placement, or the default when there is none

#### Scenario: First load after deploy
- **WHEN** the read succeeds with no record and this device has a saved placement
- **THEN** the chat cloud keeps that placement and the app writes it to the owner's record

#### Scenario: A late answer
- **WHEN** the server record arrives after the owner began dragging or resizing the cloud
- **THEN** the cloud stays where the owner is putting it
