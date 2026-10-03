## ADDED Requirements

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
