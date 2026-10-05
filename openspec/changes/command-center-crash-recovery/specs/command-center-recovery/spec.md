## ADDED Requirements

### Requirement: Recovery survives application failure
The shell SHALL expose refresh, browse and open-chat controls independently of frame rendering, retry a failed frame before the page with bounded backoff, and visibly offer Reload when recovery fails.

#### Scenario: Deploy interrupts boot
- **WHEN** a frame disappears, boot fails, or a stale module fails to load
- **THEN** controls remain reachable and recovery retries without waiting for an in-flight turn

### Requirement: Recovery preserves private drafts
Recovery SHALL preserve unsent composer text and restore it only to the same verified owner, home and addressed agent.

#### Scenario: Account changes across reload
- **WHEN** another account signs in after recovery
- **THEN** the former account's draft is not displayed

### Requirement: Stale module URLs resolve
The module route SHALL redirect stale valid build hashes for allowlisted names to the current content URL using a non-cacheable redirect.

#### Scenario: Open page outlives deploy
- **WHEN** an old page requests an existing module using its former hash
- **THEN** the response redirects to the current immutable URL and never caches new bytes under the old key
