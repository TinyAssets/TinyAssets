## ADDED Requirements

### Requirement: Public published listing entry point
The app SHALL expose a public share page for published listings, showing their description and preview picture when available, and SHALL route Run in your universe through sign-in and the normal owner install approval sheet.

#### Scenario: Visitor follows a publication share link
- **WHEN** an anonymous visitor opens a published listing link and chooses Run in your universe
- **THEN** sign-in or sign-up preserves the listing and the authenticated owner's install approval sheet opens before installation

#### Scenario: Private resource is guessed
- **WHEN** the URL names a private or nonexistent resource
- **THEN** it returns not found without private metadata
