## ADDED Requirements

### Requirement: Editable Muse starter content
The starter publisher SHALL expose proactivity settings, goals, feed, ideas, monitor/reminder records, workflow recipes, an app_ui component and on-demand skills without enlarging resident hooks or AGENTS.

#### Scenario: Package publication
- **WHEN** starter_agent_files is called
- **THEN** it returns the complete editable content without writing to an account.

### Requirement: Meaningful notifications
The package check-in runner SHALL read current files, suppress unchanged checks and unsolicited updates when proactivity is off, and use the existing owner notify primitive.

#### Scenario: Requested work with the dial off
- **WHEN** a requested reminder becomes due or a requested monitor newly matches
- **THEN** it notifies once on successful receipt even with unsolicited proactivity off.

#### Scenario: Goals check-in
- **WHEN** goals are unchanged, or this is the first observation
- **THEN** no notification is emitted.

### Requirement: Editable command center
The package SHALL provide chat, feed, ideas, goals and files views using app_ui bridge calls, displaying owner file content as text.

#### Scenario: Owner changes data
- **WHEN** the owner edits a goals or feed file and refreshes the view
- **THEN** the view reads and displays the changed file.

### Requirement: Connection recipes
Image and inbox skills SHALL discover capabilities via ta, require appropriate owner-approved connections, and report missing access instead of pretending success.

#### Scenario: No suitable connection
- **WHEN** image or inbox discovery finds no suitable capability
- **THEN** the skill tells the user what capability to connect and follows the existing connect and approval paths.
