## Why

The founder asked his agent for a village with "proper graphics: real textures
and lighting, maybe 3d" (live test, 2026-10-02). The agent reported three platform
limits on the custom UIs it builds, and all three are real:

1. **A 49 KB screen.** One `tinyassets.app-ui.v1` component carries markup, style
   and script as strings, bounded to 32 KiB / 16 KiB / 32 KiB and 49,152 bytes
   whole. A real game engine alone is 0.7-1.2 MB.
2. **No assets or libraries.** The frame's policy allows `data:` images only, so
   there is no texture, sprite sheet, sound, font, model or library file.
3. **No eyes.** The agent cannot see what it built or whether it runs.

Users design their own app experience, any UI they imagine; the cross-user floor
is the only invariant. None of the three limits is that floor.

## What Changes

- **Assets in a UI.** A component may carry `assets`: a map of bundle path to a
  content-addressed blob (images, audio, fonts, glTF, JS/CSS modules, data). Bytes
  live in a per-owner blob store charged to the owner's storage quota, so the cap
  is the account's storage, plus sane per-file (16 MiB), per-UI (128 MiB, 500
  files) and per-component (1 MiB of text) ceilings.
- **Writing assets by talking.** `write_graph target="app_ui"` gains `put_asset`
  (from text, base64, or a file the agents already wrote under `/u`) and
  `remove_asset`, each naming one UI and one path, under the existing
  no-revision, re-apply-on-race rule.
- **A shared library set.** A component may name `libraries` from a vendored,
  integrity-pinned allowlist served from our own origin (three.js with
  OrbitControls/GLTFLoader, PixiJS, Phaser, Howler), and `script_type: "module"`
  so its script can `import` them.
- **Delivery without opening the frame.** The authenticated app fetches the
  bytes and posts them into the sandboxed frame, which turns them into `blob:`
  URLs. The frame's policy adds `blob:` (and `data:`) sources only: still no
  `'self'`, no host, no `connect-src` to anything that leaves the browser, no
  `allow-same-origin`, `form-action 'none'`.
- **Spec only, built next:** a visual self-check tool (headless render, screenshot,
  frame rate, console errors) placed in the harness `browse` capability, and art
  generation through the owner's own model credentials. See design.md.

## Capabilities

### Modified Capabilities

- `app-ui-library`: components carry assets, libraries and a module script; two
  asset operations; the frame's source policy is widened to local `blob:`/`data:`.

## Impact

Public surface: two `write_graph target="app_ui"` operations, three optional
component fields, one owner-door route. Storage: one table. Owner: the
ui-capabilities lane. Coordinates with cc-package (assets in packages and
publish), notify-prompt (app.html chat), lean-pipeline (app.html CSP split).
