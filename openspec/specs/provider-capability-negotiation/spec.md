# Provider Capability Negotiation

## Purpose

Define bounded model candidate discovery without creating credential authority or changing provider routing.

## Requirements

### Requirement: A source without a list endpoint draws candidates from a reviewed list
A source kind whose sources cannot enumerate their own models SHALL draw candidate model
ids from a tracked file per source kind, holding only the source kind and a sorted,
duplicate-free list of well-formed identifiers. A malformed file SHALL be refused rather
than read as an empty list. The file SHALL be packaged into every runtime artifact and a
change to it SHALL trigger a deployment, since an absent file is indistinguishable from
an unlisted source kind.

#### Scenario: A model id is added to the list
- **WHEN** a pull request adding the id is merged and deployed
- **THEN** every universe on that source kind offers it on the next read
- **AND** no per-provider code or release is required

#### Scenario: The list file is malformed
- **WHEN** the file is invalid, unsorted, duplicated, or holds a bad identifier
- **THEN** the read raises rather than silently offering fewer models

#### Scenario: A source enumerates its own models
- **WHEN** a source can call its provider's list endpoint
- **THEN** its ids arrive through discovery and it needs no entry in any file

### Requirement: A model id a user supplied stays that user's own
A model id an owner supplied and successfully used SHALL be recorded for that owner, SHALL
remain on that owner's own candidate list, and SHALL NOT be shared, published, aggregated
or counted across owners. Two universes of one owner SHALL be one owner. The record SHALL
be that owner's data and removed with their account.

#### Scenario: An owner uses an account-bearing selector
- **WHEN** the id embeds that owner's own account or deployment
- **THEN** it stays on their list and no other user can see it

#### Scenario: The owner narrows their model access
- **WHEN** an id that previously worked is no longer granted
- **THEN** it is still offered as that owner's own history, and is not admitted

### Requirement: A candidate to grant is never an admitted candidate
A candidate contributed by a reviewed list or by an owner's own history SHALL NOT be
admitted and SHALL NOT enter the routing order until that universe's accepted model access
includes it. Each SHALL carry an availability basis distinguishing it from the source's own
verified models, and a reason stating that access is required. Only the newest model of each
class SHALL be offered from a list, derived from the identifier's own shape with no vendor
or model names in platform code.

#### Scenario: Before the grant
- **WHEN** a contributed id is outside the universe's accepted model access
- **THEN** it is visible, unadmitted, carries a needs-access reason, and is absent from the order

#### Scenario: After the grant
- **WHEN** the owner grants access to that id
- **THEN** it becomes an admitted candidate and a turn can run on it
