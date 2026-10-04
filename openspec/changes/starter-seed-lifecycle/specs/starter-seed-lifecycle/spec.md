## ADDED Requirements

### Requirement: One seed lifecycle serves new and existing centers
The system SHALL use the same immutable manifest and owner/center-scoped per-path receipt for new provisioning and existing-center upgrades, including version, previous state/hash, installed hash or never-installed status, outcomes and conditional Undo references. It SHALL persist schema version 1 in `<data>/.universe-sidecars/<canonical-center-key>/starter-seeds.sqlite3`, outside agent-writable content, using the design's seed_binding, seed_receipts, seed_paths, seed_transactions, seed_transaction_paths, seed_blobs and seed_notices tables. Primary/foreign keys and all reads/writes SHALL include authenticated owner_id and center_id. Receipt keys SHALL be `(owner_id, center_id, bundle_id)` and path/tombstone keys SHALL append the normalized center-relative path. Journals and exact Undo/candidate byte blobs SHALL stay in the same database with scoped references and no cross-owner deduplication. Binding mismatch, unsupported schema or unsafe linked storage SHALL fail visibly before data access. Startup and conversation turns SHALL NOT reseed removed files or substitute default text.

#### Scenario: Another center's receipt or Undo reference is supplied
- **WHEN** an operation supplies a transaction, blob or path reference from another owner or another center of the same owner, even with matching bundle and content hashes
- **THEN** binding validation and composite-key scoping refuse access or mutation across that boundary
- **AND** candidates, prior bytes, notices, history and tombstones remain isolated; recovery and Undo enforce the same scope as installation

#### Scenario: A newer receipt schema or mismatched sidecar is encountered
- **WHEN** the database binding differs from the authenticated owner/center or its schema version is unsupported
- **THEN** the operation fails visibly without resetting, adopting or migrating that data implicitly
- **AND** no workspace mutation or turn based on a partial seed transaction proceeds

#### Scenario: Provisioning records enough to respect later deletion
- **WHEN** D10 provisions a center and the owner later deletes a seeded file
- **THEN** provisioning has already committed that file's installed hash with its bytes
- **AND** subsequent turns, retries and new bundle versions preserve the deletion, record a tombstone and only offer explicit reinstall

#### Scenario: Legacy absence is classified conservatively
- **WHEN** a center predates receipts and lacks a historically seeded `AGENTS.md` but has never received a newly introduced skill
- **THEN** it preserves missing `AGENTS.md` as an owner deletion and may exclusively create the new skill
- **AND** no missing receipt alone authorizes restoration of historically seeded content

### Requirement: Safe seed updates activate autonomously
The system SHALL automatically install proven never-installed paths and upgrade regular files whose bytes equal the recorded installed hash or a published predecessor seed hash. Receipt-recorded owner choices and deletion tombstones SHALL take precedence over stock-hash matching until the owner explicitly adopts an offered version. It SHALL preserve customized, empty, unknown, deleted, linked and unreadable content and visibly offer the new version without blocking activation. Hash-bound owner choice SHALL be needed only to replace customized content or reinstall an owner-deleted file; no owner SHALL remain on the old plumbing path. This change SHALL supply the transaction API; consumer/renderer integration and all-center/dormant-center proof SHALL belong solely to starter-agent-out-of-plumbing task 2.3.

#### Scenario: A dormant owner never responds
- **WHEN** the release cuts over and an owner has not reviewed any notice
- **THEN** their center uses the new renderer, stock `AGENTS.md` and stock skills upgrade automatically, and never-installed skills are added
- **AND** a dormant center runs or resumes that transaction before its first new-renderer turn, without an old-renderer fallback or owner-acceptance gate

#### Scenario: Customized tiny has collisions
- **WHEN** tiny has custom instructions and a colliding custom skill
- **THEN** those files remain byte-for-byte unchanged while never-installed starter/hooks.md and noncolliding starter skills are added independently, and the consumer can index them alongside owner skills
- **AND** the visible note explains where former runtime guidance now loads, how to edit/delete/Undo it, and which collided hooks/skills remain unavailable with offered candidates
- **AND** safe updates to other paths and runtime activation complete without waiting for acceptance or manual collision mapping

#### Scenario: A path cannot be safely read
- **WHEN** a seed path is empty, unreadable, linked or otherwise unverifiable
- **THEN** it is preserved without link traversal or default-text substitution and the note identifies the path
- **AND** its optional replacement does not block runtime activation

#### Scenario: Empty legacy instructions previously ran defaults
- **WHEN** a legacy center has empty AGENTS.md at migration
- **THEN** the notice explicitly says "Your instructions file is empty; you were previously running defaults", states the file was kept unchanged and that runtime default substitution has ended
- **AND** it names the added starter/hooks.md and skills supplying guidance, their edit/delete/Undo controls, and any unavailable paths instead of claiming those paths installed successfully

#### Scenario: Linked or unreadable legacy instructions previously ran defaults
- **WHEN** legacy AGENTS.md is linked or unreadable
- **THEN** the notice names the specific observed condition and explains that the previous runtime supplied defaults because the file could not safely be read
- **AND** it preserves the path without traversal, explains actual hook/skill additions or failures and the absence of runtime fallback, and persists the explanation for a dormant owner's next visit even if no file changed

### Requirement: Transactions and Undo preserve owner changes
The system SHALL apply safe file changes, receipts and index visibility at a turn boundary using owner/center-scoped locking, durable recovery and no-link conditional writes. It SHALL durably stage journal intent and exact prior/target bytes before filesystem writes, then reconcile per-path outcomes before atomically committing receipt/path state and the notice outbox in SQLite. Filesystem and database writes SHALL form one recoverable transaction, not an assumed cross-store atomic commit; no turn or new index SHALL observe its intermediate state. Recovery SHALL reuse the transaction idempotently, a concurrent edit SHALL convert the affected path to preserved-customized, and a version-keyed notice SHALL offer one-click file Undo. Tombstones and owner choices SHALL persist across versions and Undo until explicit scoped adoption. Undo SHALL reverse only writes whose current hash matches the installed hash and SHALL never restore the legacy runtime or restart extraction.

#### Scenario: An owner edits during installation or after it
- **WHEN** a concurrent edit invalidates a prepared hash, or the owner changes an installed file before Undo
- **THEN** installation or Undo preserves that edit and reports the skipped path
- **AND** other safe paths proceed, with no activation wait for the owner

#### Scenario: Crash recovery is idempotent
- **WHEN** the process crashes between staged writes, receipt commit and notice delivery
- **THEN** the next attempt resumes the same transaction before serving a turn and does not repeat completed writes or duplicate the notice

#### Scenario: Undo preserves the clean cutover
- **WHEN** the owner selects Undo for an automatic stock-file upgrade
- **THEN** matching files recover their prior bytes, unchanged new files may be removed, and the receipt records that owner choice
- **AND** the center stays on the new renderer and later versions do not silently reinstall the reverted content

#### Scenario: Undo restores a known older seed
- **WHEN** a later upgrade sees that an undone file matches a published predecessor seed hash
- **THEN** the recorded Undo choice wins over the hash match, so the file remains unchanged with a visible offer
- **AND** automatic stock upgrades resume for that path only after the owner explicitly adopts an offered version
