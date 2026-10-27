## ADDED Requirements

### Requirement: A published app lives once in its owner's box
The platform SHALL keep one app document under the app owner's box, and its multiplayer records as the `room:<app>:main` collection in the owner's per-owner conversation store in the broker (`conversations.db`, ADR-025, S34), never on a box's mount. Publishing SHALL copy only the slug, owner and display name into the shared route binding. Another account SHALL NOT receive a copy of the document or the records.

#### Scenario: Invitees add no copy
- **WHEN** a second account opens a published app
- **THEN** the document remains under the owner's root, the records in the owner's conversation store, and the shared binding has no record body

### Requirement: A signed-in player is routed as a verified caller
The router SHALL authenticate the player and forward that account into the published app owner's box for the app document. For the room (`/app/<slug>/state`) the router SHALL answer itself from the publisher's conversation store, with the player as the writer, and SHALL NOT forward to, ask or wake a box. A caller-supplied owner id or caller id SHALL NOT select the box, the store or the writer.

#### Scenario: A forged caller is dropped
- **WHEN** a signed-in player requests a published app and supplies another account as the caller
- **THEN** the box owner is the publisher and the caller is the signed-in player

### Requirement: Room writes commit by compare-and-swap
A room or player write SHALL present the etag it read, on the collection's one compare-and-swap primitive. A mismatch SHALL leave the stored record unchanged. The stored writer SHALL be the verified caller, and a player record SHALL be visible to that player alone.

#### Scenario: Two writers use the same etag
- **WHEN** two players present the same etag for one room record
- **THEN** one write commits and the other leaves the record unchanged

### Requirement: A visiting player cannot use private owner tools
A visiting player SHALL NOT read or write the owner's private pages. The published app and its room SHALL remain available to that player.

#### Scenario: A visitor calls write_page
- **WHEN** a visiting player invokes a private page tool in the app owner's box
- **THEN** the tool returns a private-surface refusal and does not write the page
