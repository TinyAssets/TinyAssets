# Owner cell execution

## Purpose

Keep background and interactive execution bound to the same admitted owner and filtered network.

## Requirements

### Requirement: Cell execution resolves the admitted owner
Execution cells SHALL resolve the exact command center's owner from the broker admission log and admit only that owner or that center's internally bound run actor.

#### Scenario: Background and delegated execution
- **WHEN** a background graph, automation or sub-agent executes as its center actor
- **THEN** its code, HTTP and tool cells execute with the same admitted owner identity as interactive execution.

#### Scenario: Cross-owner refusal
- **WHEN** a foreign owner, foreign center actor, unbound actor or retired center requests a cell
- **THEN** admission fails before execution, without allocating an identity or changing ownership.

#### Scenario: HTTP effect under a center actor
- **WHEN** a center actor emits an authenticated HTTP effect
- **THEN** the same resolver supplies its admitted owner to the existing broker grant check, and foreign grants remain refused.

### Requirement: Tool networking retains filtered egress
Tool bash SHALL reach permitted HTTP and HTTPS endpoints through the pinned checking proxy while retaining network isolation.

#### Scenario: Production-copy acceptance
- **WHEN** acceptance restores and migrates the cutover snapshot and starts the real server
- **THEN** background and automation code/HTTP graphs, a sub-agent, Patch Request Intake through file_issue with stub GitHub, and tool bash curl to a local fixture succeed; cross-owner and private-destination requests remain refused.

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
