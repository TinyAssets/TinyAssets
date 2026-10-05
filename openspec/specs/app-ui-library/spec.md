# app-ui-library Specification

## Purpose
The private row of user-authored UIs a person keeps in a universe, and which one
they are using, read and changed through `read_graph` / `write_graph
target="app_ui"` by the person's app and by their universe talking for them.

## Requirements
### Requirement: A person's UI row is changed one UI at a time without a revision
`write_graph target="app_ui"` SHALL accept the operations `activate`, `use_default`, `add_ui`, `replace_ui`, `edit_ui` and `remove_ui`, each naming one UI (or only the choice) in `payload_json` and none needing the row revision. Each SHALL apply to the row as stored at write time, keyed by the authenticated caller and universe, and SHALL NOT overwrite a change another writer saved concurrently. The row revision SHALL advance so a whole-row `save` sees the change.

#### Scenario: A universe switches a large library to one UI
- **WHEN** a universe calls `activate` with `{"ui_id": "gtm-village"}` for a library larger than one tool result
- **THEN** the choice becomes that UI and no UI in the library changes

#### Scenario: One component is edited and the rest are untouched
- **WHEN** `edit_ui` names one `ui_id` with `set` fields or `edits` whose `old` text occurs exactly once
- **THEN** only that UI changes; an `old` text occurring zero or several times is refused and nothing changes

#### Scenario: A concurrent save is not lost
- **WHEN** another writer saves the row after a targeted change read it and before it wrote
- **THEN** the targeted change is re-applied to the new row and both changes are stored

#### Scenario: A stale etag is refused
- **WHEN** `replace_ui`, `edit_ui` or `remove_ui` carries an `expected_etag` that no longer matches that UI
- **THEN** the change is refused as a conflict and nothing changes

#### Scenario: Another user cannot reach the row
- **WHEN** a user without access to the universe names it, or a collaborator names a UI only the owner has
- **THEN** the operation is refused or reports the UI not found, and the owner's row is unchanged

### Requirement: A model reads the UI row compactly
`read_graph target="app_ui"` SHALL return an index of the caller's UIs (id, name, etag, field sizes, no bodies) with the choice and revision for `query="index"`, one UI for `query=<ui_id>`, and one chunk of one field with `next_offset` when `field_name` is given. The universe's engine surface SHALL default to the index and SHALL NOT return the whole library.

#### Scenario: The engine index fits a bounded result
- **WHEN** a universe calls `read_graph target="app_ui"` for a library larger than one tool result
- **THEN** the result is the index, not truncated, and names every UI

### Requirement: The app shows a choice made by talking
After each delivered turn the app SHALL read the index and, only when the row revision moved, re-read and apply the row, so an activation made during the turn shows without a reload and an unchanged row never remounts the UI in use.

#### Scenario: Activation shows after the reply
- **WHEN** the universe activates a UI during a turn
- **THEN** the app mounts it after rendering the reply

### Requirement: A UI carries assets bounded by its owner's storage
A `tinyassets.app-ui.v1` component SHALL accept an optional `assets` map from bundle path to `{sha256, size, media_type}`, and every referenced blob SHALL be stored for the saving owner with that size, checked under the same write lock as the row write, or the write is refused and nothing changes. Blob bytes SHALL count toward the owner's storage quota, and the only other bounds SHALL be 16 MiB per file, 128 MiB and 500 files per UI, and 1 MiB of component text.

#### Scenario: A UI larger than the old 49 KB bound is saved
- **WHEN** a universe adds a UI whose script is 200 KB and whose assets total 20 MB
- **THEN** it is saved and its bytes are charged to the owner's storage

#### Scenario: A row cannot name bytes its owner does not hold
- **WHEN** a save or `replace_ui` names an asset hash not stored for the saving owner
- **THEN** it is refused naming the path, and nothing changes

#### Scenario: Storage quota refuses an asset
- **WHEN** a `put_asset` would take the owner past their storage quota
- **THEN** the visible storage refusal is returned and nothing is stored

### Requirement: Assets are written one path at a time
`write_graph target="app_ui"` SHALL accept `put_asset` (`ui_id`, `path`, and exactly one of `text`, `base64` or `from_file` naming a file under the command center's `/u`) and `remove_asset` (`ui_id`, `path`), under the same no-revision, re-apply-on-race and `expected_etag` rules as the other targeted operations.

#### Scenario: An image the agent rendered becomes an asset
- **WHEN** a universe calls `put_asset` with `{"ui_id": "village", "path": "img/grass.png", "from_file": "art/grass.png"}`
- **THEN** the file's bytes are stored and the UI's `assets` names them at that path

### Requirement: A UI may use pinned shared libraries
A component SHALL accept an optional `libraries` list of names from the platform's vendored allowlist and an optional `script_type` of `classic` or `module`. The app SHALL verify each library's pinned SHA-384 before the frame receives it, and an unknown name SHALL be refused by name.

#### Scenario: A module script imports three.js
- **WHEN** a UI lists `"three"` and has `script_type` `module` with `import * as THREE from "three"`
- **THEN** the frame renders it with no network request leaving the browser

### Requirement: The frame stays sealed while loading bytes
The app SHALL fetch assets and libraries itself and post them into the sandboxed frame, which SHALL load them only as `blob:` URLs it creates. The frame policy SHALL name no host, no `'self'` and no network scheme in any source, SHALL keep `connect-src` to `data:` and `blob:`, `form-action 'none'`, and SHALL NOT grant `allow-same-origin`.

#### Scenario: A bundle tries to send data out
- **WHEN** a bundle calls `fetch` to any URL, sets a remote image source, or posts a form
- **THEN** the browser blocks the request and nothing leaves the device

### Requirement: The engine can preview its owner's UI without granting effects
`read_graph target="app_ui_preview" query=<ui_id>` on the universe engine SHALL render one UI owned by the bound caller in that universe. The preview SHALL require write authority before creating the screenshot; caller-supplied identifiers SHALL NOT choose another owner. It SHALL return rendering measurements and diagnostics as untrusted UI-produced data, together with the screenshot path rather than inline PNG bytes.

#### Scenario: A stored UI renders with its assets and libraries
- **WHEN** the bound owner previews an existing UI
- **THEN** the shipped app frame, stored component, assets, and pinned libraries render in headless Chromium with its sandbox enabled
- **AND** the report includes frame rate, uncaught errors, console diagnostics, bridge calls, blocked requests, missing assets, and the screenshot path

#### Scenario: A caller cannot preview another owner's UI
- **WHEN** a preview names an unknown or foreign UI, or the caller lacks write authority
- **THEN** it is refused before writing a screenshot for that request

#### Scenario: Preview bridge calls do not perform live effects
- **WHEN** the previewed component uses its bridge or attempts an external request
- **THEN** the stand-in parent supplies the preview identity and empty read results, refuses action calls as preview-only, and external requests are blocked; requests reaching the interception handler are recorded up to the report limit

### Requirement: Preview execution and screenshot writes are bounded
A preview SHALL enforce the renderer's wall-clock limit. Linux production hosts SHALL additionally enforce the render tree's memory and process limits, supervise it in a PID namespace, and reap it. Non-Linux POSIX hosts SHALL refuse previews; Windows development hosts use the wall-clock bound and best-effort process-tree cleanup. The process slot and, on POSIX, shared host slot SHALL refuse concurrent work as `ui_preview_busy`. A missing browser or failure of required Linux containment SHALL produce an explicit refusal, not a successful blank image. A containment failure that cannot be cleaned up SHALL prevent further previews in that process.

#### Scenario: A concurrent render is refused
- **WHEN** another request holds the applicable preview slot
- **THEN** the new request returns `ui_preview_busy` without waiting in a render queue

#### Scenario: A screenshot is written into the universe
- **WHEN** a preview succeeds with an accepted UI identifier
- **THEN** its PNG is written to `/u/previews/<ui_id>.png` through the shared universe writer's no-follow path handling beneath the resolved universe root and atomic replacement
- **AND** a planted hard link at the output name is replaced rather than written through

#### Scenario: Runtime containment cannot be established
- **WHEN** the browser is unavailable, a non-Linux POSIX host attempts a preview, or a Linux production host cannot establish its required containment
- **THEN** preview returns an explicit unavailable or containment failure and does not report a successful render
