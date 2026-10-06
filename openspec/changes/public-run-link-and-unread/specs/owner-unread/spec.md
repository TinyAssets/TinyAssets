## ADDED Requirements

### Requirement: Owner unread receipts follow the account
The app SHALL show unread agent message and pending ask counts, persist viewed IDs for the authenticated account across devices, and SHALL NOT treat viewing as approval.

#### Scenario: Viewed content on another device
- **WHEN** an owner views messages or the Needs you inbox
- **THEN** only the viewed IDs become read, all devices receive the updated count, and later arrivals remain unread

#### Scenario: Different account or hidden page
- **WHEN** another account reads its state or a page remains hidden
- **THEN** the first account's unread state is not cleared
