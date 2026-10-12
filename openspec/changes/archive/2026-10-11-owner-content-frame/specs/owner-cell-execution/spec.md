## ADDED Requirements

### Requirement: Cell protocol failures retain bounded diagnostics
The launcher and daemon SHALL report the cell kind, authenticated exit status and bounded stderr tail when a cell exits early or returns an invalid frame.

#### Scenario: Failed or malformed cell
- **WHEN** an owner-content, owner-measure, tool or decoder cell exits before its reply or returns a malformed frame
- **THEN** the raised error and daemon log include that cell's kind, exit status and bounded stderr tail, without copying stdout or request bytes.

### Requirement: Production acceptance exercises owner publication
Production-copy acceptance SHALL exercise owner-content publication with bytes and directories only, and a tool call with a pending owner-owned brain file.

#### Scenario: Pending brain on restored center
- **WHEN** acceptance restores the production center and places a pending brain in its workspace
- **THEN** real tool execution promotes the exact bytes under the owner UID and retains the canonical center root label.

### Requirement: Wiki creation preserves owner custody
Wiki directories SHALL be created by the admitted owner's content cell; tool preparation SHALL recover only empty daemon-owned immediate categories under pinned owner wiki/pages and wiki/drafts parents.

#### Scenario: New wiki category followed by a tool
- **WHEN** a wiki write creates a category after migration
- **THEN** its directories and files belong to the owner and subsequent storage observation and tool execution succeed.

#### Scenario: Existing empty category debris
- **WHEN** tool preparation finds an empty daemon-owned category from the old writer
- **THEN** it replaces it with an owner-created directory without changing root labels or capabilities.

#### Scenario: Recovery races and user bytes
- **WHEN** a category is nonempty, a symlink, or concurrently replaced by its owner
- **THEN** recovery never follows the symlink, never removes nonempty content, and never overwrites the owner's replacement.
