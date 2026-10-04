# universe-agent-harness (delta)

## ADDED Requirements

### Requirement: A whole command center publishes as one immutable package, scrubbed of private items
An owner SHALL be able to publish their whole command center as one package through the owner-confirmed `publish` ask, using the shared bundle manifest with profile `publish`.

The package SHALL contain the harness files, the roster agents, the wiki's curated pages and the app, alongside the workflows, UI and automation triggers the ask already publishes, plus the owner's own folders. The owner MAY leave out any path, and MAY name memory items, per file, to include.

**At the command center's top folder the carried files SHALL be a closed set**, and a file there that is not in it SHALL stay home and SHALL be listed with that reason. Most platform state is written at the top folder, so an enumeration of private names there can only ever be as complete as the last person to extend it: `orgchart.md` and `requests.json` travelled because nothing named them, and a subsequent grep of the root-level filenames platform code writes found twenty-one more, including the branch-task queue. A file of the same name inside the owner's own folder is the owner's content and SHALL still travel, because every private-name rule is scoped to the top folder only.

Top-level FOLDERS SHALL NOT be a closed set -- the owner may create any, and their content is most of what sharing a command center means -- so they remain a refusal list. **The platform's own folders SHALL be named in that list from the constants their writers use, not spelled out**, and each SHALL carry its own reason. The owner's uploads SHALL be on that list: an upload may be anything personal, and it is authoritative content the platform never reshapes (Hard Rule 9), so it is not the platform's to publish on the owner's behalf. Hand-listing is not sufficient and was shown not to be: the top-folder rule alone still published `artifacts/reviews`, `artifacts/executions` and `artifacts/discarded_targets`, where the discard archive preserves a whole work target including its request text, and the first hand-written guard for this requirement would have stayed green when that folder was added.

The owner-facing sentence SHALL describe what the package carries rather than what was removed, for the same reason: a removal list cannot be more complete than itself. It SHALL say that private brain files stay home while naming `identity.md`'s exception implicitly (it travels as the published roster agent's own identity), and SHALL state that memory travels only where the owner named entries.

The package SHALL never contain:
- dot-prefixed entries or managed repository checkouts;
- platform runtime files or databases;
- the owner-describing brain files;
- the wiki's drafts or raw material;
- unselected memory;
- any file holding a certain credential (by structure, or a mixed-class opaque value assigned to a secret's name) or contact details. Owner approval SHALL NOT override this. Any other opaque run SHALL keep its file in and be listed on the tab for review.

Connections SHALL appear only as named references in the manifest. A binary file SHALL never be included. A final check SHALL scan every public path, the manifest and the definition, and SHALL refuse the publish on any detection.

The package content SHALL be stored once, immutably and content-addressed, in platform storage outside every command-center folder. Its bytes SHALL be admitted against the publisher's storage quota before anything is published, and a refusal SHALL name the package's size.

The tab SHALL list every included file, and every excluded file with its reason, and SHALL state that detection cannot prove a file free of personal data. The consent record (the action, its digest covering the package content, and the tab text) SHALL be pinned in platform-owned storage keyed by the request. The owner's surfaces SHALL render the tab from that record, and the answer SHALL execute only that record.

#### Scenario: private items never reach a published package
- **GIVEN** a command center whose folder holds `MEMORY.md`, `founder.md`, a `.credentials` file, a workspace file containing an API key and one containing an email address
- **WHEN** the owner confirms a package publish without naming memory items
- **THEN** none of those files is in the package, and the tab named each excluded file with its reason

#### Scenario: an over-quota package is refused with its size
- **WHEN** a package's content would take the publisher over their storage quota
- **THEN** nothing becomes public and the refusal names the package's size

#### Scenario: a file nobody enumerated stays home
- **GIVEN** a command center whose top folder holds a file no rule names -- a future platform state file, or anything a later change writes there
- **WHEN** the owner confirms a package publish
- **THEN** that file is not in the package, and the tab lists it with the reason that it is not one of the files a package carries from the top folder

#### Scenario: the owner's uploads stay home
- **GIVEN** a command center holding files the owner uploaded into its canon
- **WHEN** the owner confirms a package publish
- **THEN** no uploaded file is in the package, and the tab says the files the owner uploaded stay theirs

#### Scenario: the command center's own work records stay home
- **GIVEN** a command center whose discard archive holds a work target carrying the text of a request the owner made
- **WHEN** the owner confirms a package publish
- **THEN** no file under the platform's artifacts folder is in the package, and the tab names it as the command center's own work records

#### Scenario: the owner's own content still travels
- **GIVEN** the same command center with a note inside the owner's own folder, including one whose filename matches a private platform name
- **WHEN** the owner confirms a package publish
- **THEN** those notes are in the package, because the top-folder rules are scoped to the top folder and the scrub is what protects their contents

### Requirement: Installing a package is quarantined until the owner activates it, and materialises as the installer's own copy
A served agent SHALL be able to raise an `install` ask naming a package's definition. The platform SHALL verify the package content against its pinned digest, and SHALL run the ingestion boundary, which:
- bounds bytes, file count, depth and path length;
- rejects absolute, traversal, dot-prefixed and colliding paths.

The platform SHALL then record a quarantine record in platform-owned storage, keyed by command center, agent and request, outside every agent-reachable location. It SHALL write the tab itself, previewing what will land where, what the package needs, and which existing paths stay the installer's own.

Only the owner's answer from their own surface SHALL activate the install, and it SHALL be idempotent on the request. The answer SHALL:
- create private workflow copies authored by the installer;
- add the UI to the installer's library;
- create the automations paused, owned by the installer;
- write the files into the installer's command center, within their storage quota.

Harness files SHALL land under `agents/<slug>/`, never over the installer's own main agent. An existing path SHALL NOT be overwritten. The installer SHALL never receive the publisher's private data, credentials or any write path to the publisher's command center, and installed work SHALL run only under the installer's own authority and connections.

#### Scenario: a second user installs and runs a published command center
- **GIVEN** user A published their command center as a package
- **WHEN** user B's agent raises an install ask and B confirms it
- **THEN** B owns private copies of A's workflows, the UI and paused automations, and A's files appear in B's command center
- **AND** B can run an installed workflow under B's own authority
- **AND** none of A's excluded items is present

#### Scenario: nothing materialises before activation
- **WHEN** an install ask is raised but not yet confirmed
- **THEN** no file, workflow, UI or automation exists in the installer's command center

#### Scenario: a path that escapes is refused
- **WHEN** a package's content names a path with `..`, an absolute path or two paths that collide after case folding
- **THEN** the install ask is refused before any quarantine record is written
