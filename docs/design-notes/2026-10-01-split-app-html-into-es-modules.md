# Split app.html into ES modules served as static files (design, 2026-10-01)

**Status:** proposed, for lead review. Not built.
**Problem.** `tinyassets/onboarding/app.html` is the whole web app. It is 7,785
lines: one inline `<style>` (lines 8-275), the markup, and one inline
`"use strict"` `<script>` of about 7,150 lines (629-7783) with 235 top-level
functions. `app_ui.js` (966 lines) is already a separate file, but
`render_app_html()` pastes it into the script at the `__TA_APP_UI__`
placeholder.

**Costs, measured 2026-10-01:**
- 76 commits since 2026-09-01 edited app.html, so concurrent feature PRs
  conflict in one file.
- 40 test files read app.html. Eight of them (`test_app_file_upload_ui`,
  `test_app_account_transition`, `test_app_request_rail_executes`,
  `test_app_signout_clears_typed_credentials`, `test_app_serving_heal_executes`,
  `test_app_reads_use_owner_door`, `test_android_app_identity`,
  `test_onboarding_app`) carry their own `_function_source`-style regex that cuts
  a JS function out by name so node can run it. A brace inside a string, or a
  rename, breaks a test for reasons unrelated to behaviour.
- The whole-file brand receipt hash (fixed separately in #4205) made every edit
  re-run the brand exporter.
- A universe can ship only a file it can reproduce byte-for-byte through the
  Contents API (`request_theme.json`'s `_why`). App.html is far too big for
  that; a 200-line module is not.

## Constraints that shape the design

- **CSP stays strict.** It is `script-src 'nonce-…'` and `style-src 'nonce-…'`
  (`tinyassets/onboarding/__init__.py:_csp`), with a fresh nonce per request.
  There are no inline handlers today (0 `on*=` attributes), so nothing relies on
  `'unsafe-inline'`.
- **Native shells load the live page.** The Android Capacitor config uses
  `server.url` `https://tinyassets.io/app`, and the desktop app is Electron
  around the live SPA. No shell bundles a copy, so serving modules from the
  origin works everywhere.
- **No build step.** Every client is a current Chromium WebView or browser, so
  native ES modules need no bundler. This matches the repo's lack of a JS
  toolchain for the app.
- **Strict mode is already on** (`"use strict"` at line 630). Moving the script
  into a module, which is always strict, changes no semantics on that count.

## Design

1. **Module files** live in `tinyassets/onboarding/app/`:
   - `main.js` is the entry. It holds `boot()` and the DOM wiring.
   - Feature modules follow the existing section banners: `pkce.js`,
     `native_shell.js`, `session.js`, `mcp_client.js` (SSE framing, liveness),
     `owner_door.js`, `bubbles.js`, `stop.js`, `voice.js`,
     `bounded_message.js`, `plan.js`, `attachments.js`, `account.js`,
     `rules.js`, `connections.js`, `paste.js`, `notifications.js`,
     `request_rail.js` and `app_ui.js` (moved).
   - Every module **exports** its functions and touches the DOM only inside
     functions, so node can `import` it with no document present.
2. **Serving.** A new route, `GET /app/m/<build>/<name>.js`:
   - serves only basenames listed in `tinyassets/onboarding/app/`, never a path;
   - uses `Content-Type: text/javascript`;
   - sends `Cache-Control: immutable` keyed by `<build>`, the existing
     `CFG.build` / `X-TinyAssets-Build` value, so a deploy never mixes old and
     new modules.
3. **Page.** `app.html` keeps the head, the markup and the config placeholder,
   then loads `<script type="module" nonce="__TA_NONCE__" src="/app/m/<build>/main.js">`.
   The CSP adds `'strict-dynamic'` to `script-src`, so the nonce'd entry may
   import its siblings without widening the policy to a host allowlist.
4. **Tests import, not extract.** Each of the eight helpers becomes
   `node --input-type=module -e "import {fn} from '<abs path>/app/x.js'; …"`
   through the existing `_run_node` pattern. The regex extractors are deleted.
   Pure state machines, such as the upload states, bounded-message reassembly and
   SSE framing, become directly testable.
5. **CSS** can follow later as `app.css` through `<link rel="stylesheet" nonce>`.
   It is optional and not part of this plan.

## Slice plan (each slice is a small PR that keeps behaviour identical)

- **S0 Infra.**
  - The module route (allowlist, content type, cache, a refusal for traversal
    or unknown names), `'strict-dynamic'`, and a `main.js` that only logs a
    marker.
  - Tests for the route and the CSP.
  - Live proof that the module loads under the CSP on `/app` and in the Android
    shell.
- **S1 Move the script verbatim.**
  - The inline script body becomes `app/main.js`, unchanged except that
    `__TA_APP_UI__` turns into `import "./app_ui.js"`; app.html drops by about
    7,150 lines.
  - The 40 test files that read app.html for JS read `main.js` instead, through
    one shared `tests/app_source.py` helper. That is a mechanical change.
  - Proof: `ui-test` live, signed in, one turn, upload, request rail.
- **S2-S6 Carve modules out of main.js,** two or three banners per PR, in
  order of test pain:
  - S2: attachments and upload (`test_app_file_upload_ui`);
  - S3: session and account transition, plus sign-out;
  - S4: request rail;
  - S5: mcp_client, bounded message and SSE;
  - S6: owner door, serving heal and the rest.
  - Each slice converts its tests to `import` and deletes that file's
    extractor.
- **S7 Done check.**
  - No `_function_source` remains in `tests/`.
  - app.html is under 400 lines.
  - `main.js` is wiring only.

## Central registries are the conflict source (lead, 2026-10-01)

Approved PRs keep conflicting with main within minutes on shared files:
app.html, `storage_accounting` `ROOT_ENTRIES` and the route lists. The split
must not recreate that inside `main.js`. Every feature module registers
itself:
- it exports a `wire(app)` (or `register(app)`) that attaches its own DOM
  handlers and rail or menu entries;
- `main.js` imports the module list from one line per module, sorted, and
  calls each one.

A new feature then adds one file and one import line, not a hunk in a shared
function. The same rule applies on the server side
(`2026-10-01-cut-runs-py-import-coupling.md` §3).

## Risks

- **Load order.** A module is deferred and runs after parsing, while the inline
  script sat at the end of `<body>`. The effective timing is the same; S0
  proves it live.
- **Stale module cache after a deploy.** The `<build>` path segment, plus the
  existing build-mismatch reload (`X-TinyAssets-Build` HEAD check), means a page
  never imports another build's module.
- **Plugin mirror.** The `app/` directory is under `tinyassets/`, so it mirrors
  automatically. It disappears entirely if the mirror design
  (`2026-10-01-stop-committing-the-plugin-mirror.md`) lands first.
- **Universe-shippable files.** A small module is shippable through the
  Contents API in a way app.html never was. That is a gain, not a risk.
