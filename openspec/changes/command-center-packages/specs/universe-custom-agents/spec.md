# universe-custom-agents (delta)

## ADDED Requirements

### Requirement: A package is listed by its one public definition
A published command-center package SHALL be listed by exactly one public agent definition tagged `tinyassets.command-center-package.v1`, whose `package` component carries the format version, a per-author-and-name version number, the content's sha256 and size, the file count, the agents, and what it needs (model and connection names).

`browse_commons kind="packages"` SHALL list package definitions and their summaries. A republish by the same author under the same name SHALL be a new immutable definition with the next version number.

#### Scenario: a second user finds a package
- **WHEN** another account browses packages
- **THEN** it sees the package's name, description, author, version, size and needs, and nothing of the publisher's private data
