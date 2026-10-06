# Owner unread

## Purpose

Keep account-synced unread indicators for agent replies and pending asks.

## Requirements

### Requirement: Owner unread receipts follow the account
The app SHALL show unread agent message and pending ask counts, persist viewed IDs for the authenticated account across devices, and SHALL NOT treat viewing as approval. Counts SHALL cover the current home across agent threads; hidden pages SHALL NOT acknowledge unviewed content.

#### Scenario: Viewed content on another device
- **WHEN** an owner views messages or the Needs you inbox
- **THEN** only the viewed IDs become read, other devices receive the updated count on refresh, and later arrivals remain unread
- **AND** answered asks cease to contribute to the count

#### Scenario: Different account or hidden page
- **WHEN** another account reads its state or a page remains hidden
- **THEN** the first account's unread state is not cleared
- **AND** stale responses after account or home changes do not repaint counts

#### Scenario: Read state is unavailable
- **WHEN** an unread-state request fails
- **THEN** the app displays an unknown count and retries instead of reporting an empty inbox
