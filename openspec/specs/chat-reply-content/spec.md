# Chat reply content

## Purpose
Safe links, inline visuals and owner-session file delivery in app chat.

## Requirements

### Requirement: Safe reply links
The app SHALL render markdown and bare HTTP(S) URLs as new-tab links with noopener noreferrer, leaving executable URLs inert.
#### Scenario: Unsafe link
- **WHEN** a reply contains a javascript URL
- **THEN** no executable anchor is created

### Requirement: Data-only inline visuals
The app SHALL render mermaid and bar/line/stacked chart fences without executing agent scripts and preserve code when validation or rendering fails.
#### Scenario: Invalid visual
- **WHEN** a chart is invalid or Mermaid cannot render
- **THEN** the original code remains visible

### Requirement: Owner file delivery
The app SHALL render file fences as download chips and deliver folder bytes only through an owner-authenticated session, refusing traversal, links and other users, including public-universe viewers.
#### Scenario: Owner downloads an export
- **WHEN** the owner clicks a file chip for exports/report.xlsx
- **THEN** the authenticated endpoint returns the exact bytes as an opaque attachment
#### Scenario: Other user requests an export
- **WHEN** a non-owner requests that file
- **THEN** the endpoint returns not_found without revealing its existence
#### Scenario: Account changes during download
- **WHEN** the login or home changes before the body completes
- **THEN** no file is saved under the new session

### Requirement: Discoverable reply formats
Starter editable guidance and served tool guidance SHALL describe visuals and the file fence, and require checking files before offering them.
#### Scenario: Agent delivers an export
- **WHEN** the agent reads its guidance
- **THEN** it knows the file fence produces an owner-only download chip
