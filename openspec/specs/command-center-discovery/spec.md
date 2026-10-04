# Command-center discovery

## Purpose
Keep command-center creation, discovery and switching accessible while copying public designs only into the consenting owner's isolated command center.

## Requirements

### Requirement: Build and browse remain reachable
The blank command center and trusted switcher SHALL retain Build your own, Try someone else's and own-screen switching across empty catalogues, selection changes and reloads. The blank offer SHALL NOT have a dismiss action that removes those options. Notification opt-out SHALL remain unchanged.

#### Scenario: No shared file packages exist
- **WHEN** an owner opens a blank command center with an empty catalogue
- **THEN** build and browse remain visible, browsing explains the empty state, and no message or install is sent automatically

#### Scenario: A custom screen is active
- **WHEN** the owner opens trusted command-center navigation
- **THEN** their installed screens, blank screen and shared-design browsing remain available independently of the custom frame
- **AND** a stale result from another account or home cannot render or mutate the current selection

### Requirement: Public systems copy as components after explicit consent
A supported public system SHALL be distinguished from a file package and copied through a pinned owner-confirmed component-only plan. Copies SHALL use private recipient workflows, additive UI storage, remapped declared workflow references and paused recipient automations. They SHALL NOT import private files, invent a package blob, execute publisher resources or overwrite installed designs.

#### Scenario: An existing public system has a UI and two workflows
- **WHEN** another owner previews and confirms its component-only copy
- **THEN** only the declared public components are copied, UI references resolve to recipient workflows, automations arrive paused and publisher state remains unchanged
- **AND** cancelling before confirmation creates no components and retrying confirmation does not duplicate completed effects

#### Scenario: A legacy reference cannot be safely copied
- **WHEN** a required workflow version is not public or a UI embeds an identifiable source workflow ID instead of a supported declared reference
- **THEN** the system is reported unsupported before new effects rather than rewriting script text or borrowing publisher authority
