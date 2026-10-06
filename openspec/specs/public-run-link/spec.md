# Public run link

## Purpose

Let visitors discover published designs and copy them through the normal owner-approved install path.

## Requirements

### Requirement: Public published listing entry point
The app SHALL expose `/app/run/<definition>` for published listings, showing their description and a public-component preview picture when available, and SHALL route Run in your universe through sign-in and the normal owner install approval sheet. Listings without screens SHALL show a workflow illustration. Private resources SHALL NOT gain public links.

#### Scenario: Visitor follows a publication share link
- **WHEN** an anonymous visitor opens a published listing link and chooses Run in your universe
- **THEN** sign-in or sign-up preserves the listing and the authenticated owner's install approval sheet opens before installation, without requiring a connected model
- **AND** publication completion supplies this URL to the existing share-after-publish nudge

#### Scenario: An older release is outside the discovery shortlist
- **WHEN** an owner follows its immutable public link
- **THEN** normal install validation and approval apply to that exact release independently of catalogue pagination

#### Scenario: Private resource is guessed
- **WHEN** the URL names a private or nonexistent resource
- **THEN** it returns not found without private metadata, even if a cached picture exists
