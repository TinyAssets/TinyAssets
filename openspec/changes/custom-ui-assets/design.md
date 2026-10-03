## Context

A custom UI is a `tinyassets.app-ui.v1` component in the viewer's own
`universe_app_ui` row. The app mounts it in `/app/ui-frame`: a fixed, content-free
bootstrap whose response header sandboxes it to an opaque origin with no network
(`ui_frame.py`). The bundle arrives over `postMessage`; every capability is a call
on a frozen bridge allowlist in the parent (`app_ui.js`). That containment is the
cross-user floor for UIs, because a UI is somebody's code and is shared by remix.

What a game needs that this lacks: bytes (textures, audio, fonts, models), a
library, and more than 49 KB of code. What must not change: the frame owns no
storage, holds no identity, and nothing in it can send a byte off the device.

## Goals / Non-Goals

**Goals:** a UI holds as many asset bytes as its owner's storage allows; a UI can
use a real engine without vendoring it; nothing new can leave the frame.

**Non-Goals:** open internet for UIs (remote fonts, CDNs, APIs); a UI talking to
the platform other than through the bridge; workers or WebAssembly in the frame
(not needed by the allowlisted libraries; revisit with evidence).

## Decisions

### D1. Bytes reach the frame as `blob:` URLs the frame mints itself

The parent (authenticated, same-origin) fetches each asset and library and posts
the `ArrayBuffer`s with the bundle. The bootstrap turns each into a `blob:` URL in
its own opaque origin and the bundle loads from those.

*Rejected: serving assets to the frame by URL.* The frame is an opaque origin and
carries no bearer, so a URL it can load is either public or a capability URL. A
capability URL makes private bytes fetchable by anyone who sees the URL (logs,
a screenshot, a shared remix), and it would need `'self'` or a host in the frame's
policy, which reopens a same-origin request channel for exfiltration encoding.
`blob:` is local to the document: nothing to authorize, nothing to leak.

The frame policy becomes:

```
sandbox allow-scripts; default-src 'none';
script-src 'unsafe-inline' blob:; style-src 'unsafe-inline' blob:;
img-src data: blob:; font-src data: blob:; media-src data: blob:;
connect-src data: blob:; form-action 'none'; base-uri 'none';
frame-ancestors 'self'
```

`connect-src data: blob:` lets loaders that `fetch()`/XHR their asset (three's
GLTFLoader, Pixi's `Assets`, Phaser's loader, Howler's Web Audio path) read the
blob they were handed. No source names a host, `'self'` or a scheme that leaves
the browser. `worker-src` and `frame-src` still fall back to `'none'`.

### D2. Content-addressed blobs per owner, referenced by path

`universe_app_ui_asset(owner_user_id, sha256, size_bytes, content, created_at)`,
primary key `(owner_user_id, sha256)`, in the custom-agents database. A
component's `assets` maps a bundle path to `{sha256, size, media_type}`; the
media type is the reference's (its path's extension), never the shared row's, so
two references to the same bytes cannot disagree through it. Versioning comes
free: an edit is a new hash; two UIs or two universes sharing a texture store it
once.

- Write-time validation: every referenced hash must be stored for the SAVING
  owner with the same size, so a row can never name bytes its owner does not
  have (the whole-row `save` and `replace_ui` included). Every row write takes
  the write lock (`BEGIN IMMEDIATE`) before it checks, so the check and the
  write are serial with the sweep.
- Storage: the blobs are counted by the existing `ui_library` store (one
  measurement over row text plus blob bytes) and charged before the write
  (`StorageRefused` at the quota, the existing visible refusal).
- Ceilings, all payload validation rather than account limits: 16 MiB per file,
  128 MiB and 500 files per UI, 1 MiB of component text (markup, style, script
  and the manifest), paths of `[A-Za-z0-9._-]` segments up to 200 characters.
  The old 32 KiB/16 KiB/32 KiB/49,152-byte bounds are removed; text past them
  belongs in a JS/CSS asset.
- Media types come from a closed extension table (images incl. SVG, audio,
  fonts, glTF/GLB/bin, KTX2, JS/MJS, CSS, JSON, text, WASM-less). The type is the
  one the frame's `Blob` gets; the HTTP response that carries the bytes is always
  `application/octet-stream`, `nosniff`, `attachment`, so nothing renders on the
  app origin.
- Garbage: a blob no row of its owner references, older than one hour (so a
  `put_asset` never races its own reference away), is deleted on that owner's
  next asset write.

### D3. `put_asset` and `remove_asset`

Same rule as the other targeted operations: name one UI, no revision, re-applied
on a lost race, optional `expected_etag`. `put_asset {ui_id, path, text |
base64 | from_file, media_type?}` stores the blob and points the path at it in
one call. `from_file` reads a file the command center's agents already wrote
under `/u` through the owner read (`universe_file_reads`), so an image the agent
rendered with code in its box becomes an asset without passing through the
model.

### D4. Vendored, pinned libraries

`tinyassets/onboarding/ui_libraries/manifest.json` names each library, its
upstream version, format (`module` or `global`), file and SHA-384. The owner-door
route serves the file; the parent verifies the SHA-384 with `crypto.subtle`
before posting it, so a tampered or stale cached copy is refused, not run.

| name | version | format |
|---|---|---|
| `three` | 0.170.0 | module |
| `three/addons/controls/OrbitControls.js` | 0.170.0 | module |
| `three/addons/loaders/GLTFLoader.js` | 0.170.0 | module (its relative import of BufferGeometryUtils rewritten to the bare name) |
| `three/addons/utils/BufferGeometryUtils.js` | 0.170.0 | module |
| `pixi.js` | 8.22.0 | global `PIXI` |
| `phaser` | 3.90.0 | global `Phaser` |
| `howler` | 2.2.4 | global `Howl`/`Howler` |

All MIT. GSAP is not included: its 2025 "no charge" license restricts use in
tools competing with Webflow's builder, which a UI builder may be. A UI can still
vendor anything itself as a JS asset. `ui_libraries` is excluded from the plugin
mirror (the local plugin runtime serves no app); a host without the directory
answers `library_unavailable`.

Module wiring: the bootstrap adds an import map before any module runs. Each
module library maps by its name; each JS asset maps as `@ui/<path>` and
`./<path>`. A `blob:` URL is not hierarchical, so an asset module importing a
sibling uses `@ui/<path>`, not a relative path. Global libraries load as
`<script src="blob:...">` in order before the bundle script. `ta-asset:<path>` in
markup and style is replaced by that asset's `blob:` URL at render time (stored
text is never rewritten); scripts call `tinyassets.asset(path)`.

### D5. Publish and packages

`export_ui_component` carries `libraries` and `script_type`, and refuses a UI
with `assets` by name until the published definition can carry its blobs (today
it would silently drop them and publish a broken UI). The blob store exposes
`read_app_ui_asset` / `store_app_ui_asset` for the cc-package lane to copy bytes
into and out of packages.

### D6. Visual self-check

Shipped as two pieces (lead decision, 2026-10-02):

- **Eyes for every image.** The harness `read` shows an image file to the model
  (pi's read behaviour), bounded by one shared rule (`tinyassets/tool_images.py`,
  PR #4306) that the thin agent loop's box `read` uses too.
- **The render.** `read_graph target="app_ui_preview" query=<ui_id>` renders one
  of the caller's own UIs in a short-lived headless Chromium subprocess
  (`tinyassets/ui_preview.py`). The shipped `/app/ui-frame` runs under its shipped
  headers and gets the stored component, assets and pinned libraries exactly as
  the app delivers them. Every other request is refused and reported. A stand-in
  parent answers the bridge read-only: `whoami` returns the command center's id,
  reads come back empty, and actions are refused as "preview". The PNG goes to
  `/u/previews/<ui_id>.png` through an exclusive temp file plus `os.replace`, so a
  planted link is replaced, never written through. The report gives fps, uncaught
  errors, console errors and warnings, bridge calls, blocked requests and missing
  assets.
- **Capacity.** One render per process. A second concurrent request gets
  `ui_preview_busy`, never a queue. Measured cost: about 150 MB unique memory and
  20 s cold per render. A host without Playwright's Chromium answers
  `ui_preview_unavailable`. The daemon image gains chromium-headless-shell in a
  separate infra PR.
- **Interim placement.** The renderer belongs inside the command center's box in
  the target architecture (#4263), as part of the sealed-box image rather than
  the shared daemon image. Until then it runs in the daemon as a subprocess,
  under the same request allowlist.

### D7 (spec only). Art generation

The agent generates art through the owner's own model credentials (an image model
the owner connected, via the vendor-neutral provider path; never platform-paid
inference), or with code in its box (procedural textures, SVG). Output lands as a
file under `/u` and becomes an asset with `put_asset from_file`. No new primitive
is needed for the code path; an image-generation call is a provider capability
and is specified with the provider work, not here.

## Risks / Trade-offs

- **Memory in the viewer's tab.** Every asset is held in the frame. 128 MiB per
  UI bounds it; the viewer runs only their own UIs.
- **A malicious remix ships a heavy UI.** It runs in the viewer's own tab and
  storage, which is their choice to install; it cannot reach anyone else.
- **Import-map ordering.** An import map must precede the first module; the
  bootstrap is classic script and inserts it first, so a bundle cannot race it.
