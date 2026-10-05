# Cross-family review

Reviewer: Claude via peer-agents, one round against 54c17355c7.

Server-side file path looks consistent with existing owner reads; now checking client wiring (script context, session scope at history render).

Checking the Mermaid SRI pin against the vendored bytes.

Running the one file-boundary test file.

**VERDICT: ADAPT**

There are no floor findings. The file boundary and the text renderer both hold up. There is one correctness gap with a one-line fix.

### Finding (correctness, medium)

**Expanding a long reply after a reload throws away all the rendering.**
- **Where:** `tinyassets/onboarding/app.html:5253`, inside `offerFullMessage` (`body.textContent=full;`).
- **Trigger:** the agent writes a reply over 4000 characters that has a chart, Mermaid or file fence, or a link, and the owner reloads.
  1. History draws the 4000-character preview through `ChatRender`. The cut usually falls inside or after a fence, so that fence shows as raw text.
  2. The owner clicks "Show full message". This sets `textContent` on `.msg-body`, which replaces everything `ChatRender` built with plain text.
  3. The links, visuals and the Download chip are gone for good.
- **Why it matters:** long replies are the ones most likely to carry a chart or an export. The spec requirements "Data-only inline visuals" and "Owner file delivery" apply to the reply, not just to the first 4000 characters.
- **Smallest fix:** at that line, use the same branch `appendMessage` uses:
  ```js
  if(el.classList.contains("msg--universe")&&typeof ChatRender!=="undefined") ChatRender.render(body,full,chatFileDownloader()); else body.textContent=full;
  ```
  Use whatever class or role marker the universe bubble really carries. You could also store the role on the element in `appendMessage`. Add one browser assertion that an expanded long reply still contains an `<a>` or `.chat-file`.

### What I checked and found sound

**File boundary (`owner_door/files.py`)**
- It uses the same order as `_serve` and `handle_ui_asset`: identity gate first, then a JSON content type, a bounded body and an exact `{graph_id, path}` shape.
- The read runs inside `identity_context` in the threadpool.
- `_owner_universe` requires admin and fails closed. The `isinstance(tuple)` guard is correct, and `_relative` rejects `..`, backslashes, NUL and absolute paths other than `/u/`.
- `_logical` follows the same rules as `read_file`. The read itself is the anchored `_read`, which won't follow links on POSIX and checks for links on Windows.
- `UniverseFileError` is a subclass of `OSError`, so link and component refusals fall into the `not_found` reply. They don't become a 500.
- Replies use `_BYTE_HEADERS` (octet-stream, nosniff, attachment, sandbox CSP).
- `tests/test_chat_file_download.py` on Windows: 12 passed, 1 skipped (the symlink case, which you reported passing on Linux).

**Download client (`chatFileDownloader`)**
- It checks login epoch, owner and home before the token refresh, after it, after the response and after reading the body. That covers the spec's "account changes" scenario.
- The download filename has `/`, `\` and control characters replaced.
- A file fence can only ever fetch the viewer's own home. Even a message written by someone else can't reach another user's bytes.

**Links**
- `httpURL` checks the scheme with a regex and with `new URL`, and rejects whitespace and control characters. Anything rejected is shown as text. Links get `noopener noreferrer`.
- On native, `openExternal` gets only the already-validated `href`. Widening the desktop `isSafeExternal` to `http:` doesn't open a scheme-RCE route.

**Mermaid**
- It runs with `securityLevel:"strict"`, and source with init directives, front matter, `click`, `href`, `classDef` or `style` is refused before rendering.
- Output passes through the DOM scrub and is shown only as a `data:image/svg+xml` `<img>`, which is inert.
- The SRI pin matches the vendored bytes: sha384 `o+g/BxPwhi0C3RK7oQBxQuNimeafQ3GE/ST4iT2BxVI4Wzt60SH4pq9iXVYujjaS`. The bundle sets `globalThis.mermaid`, and the MIT license file is present.
- `.gitattributes -text` covers both copies of `mermaid_vendor.js`. Both are served through the existing module route, which uses an allowlist and a content-keyed URL.

**Charts**
- The type, label and series counts are bounded, every value must be a finite number, and the SVG is built only with `createElementNS`, `setAttribute` and `textContent`.

**Mirror and collisions**
- The plugin mirror copies of `__init__.py`, `app.html`, `chat_render.js` and `mermaid_vendor.js` are byte-identical to the source files.
- The `app.html` diff is only the CSS, `chatFileDownloader`, the `appendMessage` branch and the `__TA_CHAT_RENDER__` slot. I saw no collision beyond the documented consent lane.

I reviewed the implementation only, not deployment status.


## Disposition

AGREE: Expanded retained replies must retain rich rendering. offerFullMessage now calls ChatRender for universe replies and uses the same session-bound downloader. Added test_expanded_history_reply_keeps_links_and_file_chips. No floor findings. Lead accepts the corrected implementation; the original reviewer verdict remains ADAPT and is not represented as a second approval.


## First review of the added CI trigger scope

I reviewed only `.github/workflows/real-browser-proof.yml` and found no floor or correctness issues.

- **What changed:** the diff adds 3 entries to the `on: pull_request: paths` list and nothing else. The job, permissions, commands and secrets are unchanged.
- **Paths are real:** `tinyassets/onboarding/chat_render.js` and `tinyassets/onboarding/app/mermaid_vendor.js` are both tracked in git. `tests/test_chat_renderer_browser.py` is in the diff, and it serves and routes `mermaid_vendor.js`, so the vendor file is something this proof actually depends on.
- **Placement:** the two runtime files sit in the runtime block next to `app.html`, and the test sits in the "The proofs." block. The indentation and quoting match the neighbouring entries, so the YAML is valid.
- **Effect:** these entries only add more files that start the workflow, so it runs in more cases, never fewer. That is the direction `test_every_marked_file_retriggers_the_proof` asks for.

I didn't run any tests, as the brief asked.

VERDICT: APPROVE
