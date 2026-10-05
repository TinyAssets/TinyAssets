# Publication completion and sharing starter skill

## Purpose

Expose successful publication facts and a public-only preview to the owner; keep sharing behaviour in editable skills.

## Requirements

### Requirement: Owner publication completion

Successful publish responses SHALL include `completion` containing `listing_id`,
`share_url` (null if no listing link exists), `change_kind` (`new` or `update`),
`version`, `preview_image_path` and `preview_status`. Publish approval answers
SHALL persist this object in `answer.completion` before waking the agent.
The resolved answer SHALL remain owner-scoped.

`version` SHALL be the integer package version when present, otherwise the
immutable definition content fingerprint (null only if an older completed pin's
definition metadata is unavailable during recovery). `change_kind` SHALL be `update` for
package versions after the first or an explicitly linked release successor;
otherwise it SHALL be `new`. `preview_status` SHALL be `ready`, `no_screen`,
`owner_context_required`, or `unavailable`; only `ready` carries an image path.
The direct `write_graph target="agent" operation="publish"` route SHALL pass
its command-center context to the same completion primitive.

`read_graph target="app_ui_preview" query="publication:<listing_id>"` SHALL
render the publisher's immutable public screen into their writable command-center
folder, returning the usual preview report and `screenshot` path. Another
publisher's listing SHALL return `app_ui_not_found`. It SHALL use empty bridge
data and a synthetic preview identity, never the owner's live private UI.

#### Scenario: A screen is published
- **WHEN** the owner approves a real publish request
- **THEN** the agent's resolved request contains the new listing ID and version
- **AND** a successful preview supplies an attachable path in the owner's `/u`
- **AND** only the immutable public screen and empty bridge data enter the renderer

#### Scenario: Preview cannot render
- **WHEN** the browser is unavailable or busy after publication
- **THEN** completion still reports the publication and an explicit preview failure
- **AND** it does not invent an image path

#### Scenario: Approval retries after publication finished
- **WHEN** a completed publish pin is retried before request resolution succeeded
- **THEN** it SHALL reuse the existing listing without publishing again
- **AND** completion SHALL be reconstructed for older pins that lack it
- **AND** rendering SHALL start only after the publication claim is durably finished

#### Scenario: Direct immutable listing replay
- **WHEN** a direct publish with an idempotency key is replayed
- **THEN** it SHALL return the same listing and original change kind
- **AND** it SHALL NOT invent an update relationship from a name or a retry

### Requirement: Editable offer after publication

New accounts SHALL receive `skills/share-after-publish/SKILL.md` via exclusive
creation. Existing accounts SHALL be able to fetch identical handbook text.
Reads SHALL NOT restore deleted skills or overwrite edits. The skill SHALL offer
to share on suitable connected platforms with a short draft and the public preview,
require explicit owner approval before posting through normal approval/owner rules,
and mention connecting a platform when none is suitable.

#### Scenario: Scripted agent turn
- **WHEN** a scripted agent sees a successful publication and reads the skill
- **THEN** it offers the user a draft and picture
- **AND** no external post is executed
