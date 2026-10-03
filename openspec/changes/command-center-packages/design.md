# Design: share a whole command center as one package

Builds on `universe-agent-harness` §4.15 (manifest, quarantine, activation),
§4.16 (platform state out of agent reach) and §4.17 (one manifest, profiles,
scrub, ingestion boundary), and on the `publish` ask from
`in-platform-agent-systems`. Only the delta is written here.

## D1. One manifest, three profiles

`command-center.json` gains a third `profile` value: `publish`. It is the same
manifest the D11 export writes (`format_version`, `profile`, `agents`, `files`,
`workflows`, `ui`, `automations`, `needs`), so the manifest is built once
(`tinyassets/command_center_packages.py`) and the export reuses it.

| Profile | What | Where it goes | Size limit |
|---|---|---|---|
| `share` | harness subset | the public definition | 256 KiB |
| `export` | the whole folder | private local output | none |
| `publish` | the whole folder minus the private items | a public package blob | the publisher's storage quota |

## D2. What a `publish` package contains

The command-center folder is walked without following links, through the
universe-file readers.

**Never included, even when named:**
- every entry whose name starts with `.` at any depth: platform and runtime
  state, credentials, session files, `.env`;
- `workspaces/`, the managed repository checkouts;
- platform-written runtime files such as `activity.log`, `status.json`,
  `ledger.json` and any SQLite database;
- the brain files that describe the owner or hold the control plane:
  `founder.md`, `soul.md`, `soul.edit.md`, `soul_versions/` and `log.md`;
- the wiki's `drafts/`, `raw/` and `daemon-wiki/`;
- any file holding a CERTAIN credential. The platform's parser
  (`tinyassets.credential_shape`) finds it as one of two things:
  - a credential by structure: a private-key block, an auth header, a JWT, URL
    userinfo, or a URL parameter named as a secret;
  - an opaque value of mixed character classes assigned to a secret's name
    (`api_key = …`, `"token": "…"`, `my key is …`).

  Any other opaque run (ids, hashes, minified identifiers) is a SUSPECT: the
  file stays in, and the tab lists it under "Worth a look". See the dry-run
  evidence in the review log.
- any file carrying contact details (an email address or a phone number). The
  `export` profile alone may include those item by item;
- any file that is not UTF-8 text. A binary cannot be inspected, so the public
  profile never carries one;
- any file whose PATH carries a credential or contact details.

**Included by default**, because the founder asked for the whole command
center: harness files (`AGENTS.md`, `identity.md`, `settings.yaml`, `skills/`,
`extensions/`, `agents/<id>/`), workspace files and `wiki/pages/`.
- The owner can leave out any path or folder (`exclude`).
- Every `MEMORY.md` (the root one and each `agents/<id>/MEMORY.md`) is left out
  unless the owner names items as `<path>#<id>` (`memory_items`; a bare id means
  the root file). That file then carries only those bullets.

**One final-output check.** After assembly, the whole public output (every
path, the manifest, and the definition with its name, description and
components) is scanned once more with the credential parser and the contact
detector. A hit anywhere refuses the publish and names where.

**Connections** become named references. `needs.connections` in the manifest
lists the connection names that the published workflows refer to. The package
carries no `.env.example`, because the ingestion boundary admits no dot file.
The D11 export writes one locally from `needs`. `needs.model` is the `model`
from `settings.yaml`, when one is set.

The tab says plainly that detection cannot prove a file is free of personal
data. It lists every included file, and every excluded file with its reason.

## D3. Storage: a blob, an index, one listing

- **Blob.** A canonical JSON document `{format_version, manifest, files: {path:
  base64}}`, stored at `.command-center-packages/blobs/<sha256>.json` in the
  data root, outside every universe folder (§4.16). It is written once,
  atomically, and never modified.
- **Index.** `.command-center-packages/packages.db` has two tables:
  - `blobs`: one row per (author, sha256), recorded before the blob is written.
    The `packages` store measures this table, so a blob that a failed publish
    left unlisted is still charged to its author. Identical content is charged
    once per author.
  - `package_versions`: one row per listed version, with its definition id.
- **Listing.** The `publish` ask's one definition gains the tag
  `tinyassets.command-center-package.v1` and a `package` component (kind
  `tinyassets.package.v1`). The component carries `format_version`, `version`,
  `blob_sha256`, `size_bytes`, `file_count`, `agents` and `needs`.
  - Definitions are already immutable. A republish under the same name by the
    same author is the next `version`.
  - `browse_commons kind="packages"` filters definitions by the tag.
- **Quota.** The blob's bytes are admitted against the publisher's account
  through `storage_accounting.admitted(... store="packages")` before it is
  written. Over the quota, nothing is published, and the refusal names the
  package's size and the quota's numbers.

## D4. Publish: the existing ask, one more block

`publish` accepts an optional `package` block:
`{exclude: [path], include: [path], memory_items: [id], agent: <agent_id>}`.
- `build_snapshot` builds the package as well. The digest covers the branch
  rows, the definition (including its version number, allocated at ask time)
  and the blob's sha256.
- **The consent record is platform-owned.** The ask writes a pin to
  `packages.db`, keyed by (universe, request). The pin holds the action, the
  digest, the agent, and the tab's kind, title and body. This applies to every
  `publish` ask, not only to packages.
  - The rail renders a publish or install row's title and body from the pin,
    so an agent that rewrites the row in its own folder changes nothing the
    owner sees.
  - The answer executes the pin's action, not the row's.
  - A row with no pin cannot be confirmed.
- `execute_action` records and writes the blob, quota-gated, before flipping
  anything public. A retry of a confirmed request finds the pin `activated`
  and returns its receipt.

## D5. Install: quarantine, preview, activation

A new pending-request action, `install`:
`{agent_definition_id, agent: <agent_id>}`.

**Ask (served agent).**
1. The platform reads the definition. It must carry the package tag.
2. It loads the blob from platform storage and verifies its sha256.
3. It runs the ingestion boundary (§4.17):
   - bounds on bytes, file count, path depth and path length;
   - absolute paths, traversal, backslashes, NUL, empty and dot components,
     and dot-prefixed names are rejected;
   - case and Unicode (NFC, casefolded) collisions are rejected;
   - only regular-file payloads are accepted (base64 text).
4. It writes a **quarantine record** in `.command-center-packages/quarantine/`,
   keyed by (universe, agent, request). The record holds the digest and the
   planned destination of every file. That includes which destinations already
   exist in the installer's command center: those are kept as they are and
   named.
5. The tab is the platform's words: name, author, version, size, what it
   needs, what lands where, and what stays the installer's. It states that
   everything runs as the installer, on their own connections, and that the
   automations arrive paused.

**Answer (person surface only).** The platform:
1. reads the quarantine record for this request and this universe;
2. re-verifies the blob;
3. recomputes the destination plan. If it differs from the pinned plan,
   nothing is materialised and the owner is asked to ask again;
4. claims the record atomically (`pinned` to `activating`);
5. reserves the installer's `universe_files` bytes before any effect;
6. materialises the package, recording each component's new id in the record
   as it goes:
   - remix each branch-ref into a private branch owned by the installer, under
     its published name. The snapshot's skills are passed explicitly, so
     nothing falls back to the source branch's live skills. The model policy
     is cleared, so the copy uses the installer's own model;
   - add the UI to the installer's library, under a fresh `ui_id` if theirs is
     taken;
   - create each automation against the copy its spec names. It is created
     paused in the same insert, so it is never runnable before the owner
     resumes it;
   - write the files;
7. marks the record `activated`, with the receipt.

A retry of an `activated` record returns the receipt. A retry of an
`activating` record resumes, skipping the components already recorded. A
failure releases the claim and leaves the ask pending.

**Where files land.** Harness files at the package root (`AGENTS.md`,
`identity.md`, `MEMORY.md`, `settings.yaml`, `skills/`, `extensions/`,
`prompts/`) go to `agents/<slug>/`. The package's `agents/<id>/` go to
`agents/<slug>-<id>/`. This is the §4.14 roster layout, so the installer's own
main agent is never overwritten. Workspace files and `wiki/pages/` land at
their own relative paths, because the UI and the workflows address them by
path. A path that already exists is kept as the installer's own.
- The destination map is checked after relocation for case and Unicode
  collisions, and for a file that would sit where a folder must.
- Every file is created `O_EXCL | O_NOFOLLOW`, beneath directories opened one
  component at a time without following links. A link that the installer's own
  agent plants between preview and confirmation refuses the write; it never
  redirects it.

**References stay by name.** A UI finds its workflows and automations by name
(served `interfaces` chapter), so the copies keep their published names. An
automation's `event_filter.branch_def_id`, which names a workflow key, maps to
the installer's copy. The tab lists each needed connection and whether the
installer already has one by that name.

**Floor.**
- Nothing in the package carries the publisher's credentials, private data or
  any write path back to them.
- Workflow copies are private remixes authored by the installer.
- Automations are owned by the installer and run as them.
- The quarantine and activation records live outside every agent-reachable
  location.

The served agent may read the preview: the ask's own return value, wrapped as
untrusted content. It cannot answer the ask.

**What this MVP installs.** It installs command centers built the way the
served guidance builds them: workflows with agent nodes, automations, a UI and
shared files. That is the GTM Village's shape. Harness files land as files;
the runtime for a roster agent is D8.

## Out of scope for this slice

- Rules suggestions (`rules.json`). They activate "as written or stricter" and
  need the D1 rules store's import path.
- The roster runtime for `agents/<slug>/`. Until D8 these are files the
  installer's agents can read and adopt.
- Converting the wiki to OKF. The package carries `wiki/pages/` verbatim so the
  copy round-trips. The OKF conversion belongs to the export (D11).
- Unpublishing and sweeping orphan blobs.
- Binaries in a public package.

## Review log

- **gpt-6-astra design refute, round 1 (2026-10-01): ADAPT.** Ten findings.
  Adopted:
  - 2: one final-output check, paths included; memory scoped per file; no
    binaries.
  - 3: the rail renders publish and install rows from the platform pin.
  - 4: the destination map is validated after relocation; the writer refuses
    links; the plan is re-checked at commit.
  - 5: the remix passes the snapshot's skills and clears the model policy. The
    cross-author live-skills fallback is also closed in `branches.py`.
  - 6: a claimed, resumable activation; automations are created paused.
  - 7: names are preserved, the event-filter workflow key is mapped, and
    connection needs are previewed.
  - 8: blob ownership is recorded before the write; the version is pinned at
    ask time.
  - 9: no dot file in the package.
  - 10: the MVP is scoped to the shape the guidance builds.

  Kept, with the reason:
  - 1: workspace files and wiki pages are included by default. The founder's
    direction (2026-10-02, relayed by the lead) defines the `publish` profile
    as "the export layout minus the private items". The tab lists every
    included file, the owner confirms that list, and the tab states the
    detection limit.

- **Lead decision (2026-10-01): keep default-include.** Publishing is itself
  the explicit opt-in: the owner confirms a preview, and the founder asked for
  the whole command center. Three conditions come with it, all built:
  - **The preview lists everything.** Every file that goes public, and every
    file left out with its reason.
  - **An exclude switch before confirming.** The publish tab carries one
    Include / Leave out switch per top-level folder or file (the 15 largest),
    plus a box for any other path.
    - The answer rebuilds the package with those left out, and the result must
      be a subset of what was shown, so a switch can only narrow it.
    - A path that the tab did not list is refused, never guessed.
  - **Likely-sensitive content is flagged, not silently included.** A file that
    mentions an often-private word ("confidential", "salary", "password", …)
    is listed under "Worth a look before you confirm", with the word. Detected
    credentials and contact details stay excluded, as before.

- **gpt-6-astra code refute, round 1 (2026-10-01): ADAPT.** Ten findings,
  all adopted:
  - 1: the rail and the answer look the pin up by request id BEFORE reading
    any row field, so a row disguised as an ordinary question still renders
    and executes as its pin.
  - 2: the platform mints the request id in the pin first. The row is created
    under that id and never deduplicated onto a row in the writable store. The
    same ask raised again reuses its still-pending pinned row.
  - 3: an automation's idempotency key is namespaced by command center and
    owner, and a replay must return the installer's own row. Pin ids are
    unique per request.
  - 4: every workflow string goes through the shared detectors one by one.
    Only id- and digest-shaped values under id or digest keys are skipped.
    - The shared credential parser reads ISO-8601 timestamps as opaque runs,
      so they are stripped before the scan. Without that, every board and
      every workflow row would trip it.
  - 5: the package writer moves to the shared `write_data_path(mode="exclusive")`
    from #4254, which owns each platform's containment. That PR is not merged
    yet, so this slice stacks on it. Until then, the POSIX path is the anchored
    writer, and the Windows fallback (single-tenant tray only) keeps the named
    parent-junction residual.
  - 6: protected names, memory files and harness relocation compare
    case- and Unicode-insensitively.
  - 7: an install reserves only the bytes still to land. On failure it commits
    what landed and gives back the rest.
  - 8: publish claims its pin, finishes it with the receipt, and pins its
    version number at ask time, so a retry names the same version.
  - 9: the UI's id is recorded before it is added, and a resume adds nothing
    if the id is already there.
  - 10: a publish or install ask needs no agent-written kind or title.

- **gpt-6-astra code refute, round 2 (2026-10-01): ADAPT.** Eight findings.
  Seven adopted:
  - 1: a switch narrows the already-verified blob (`narrow_package`) and never
    rebuilds from the live folder, so an edit after the check cannot ride in.
  - 2: the identifier exemption is an exact list of schema fields, not a
    suffix rule. Contact detection always runs, even under those keys.
  - 3: the local timestamp strip is gone. The shared parser now owns it,
    after URL and header parsing (separate PR `fix/credential-shape-timestamps`).
  - 4: the tab lists every path in full, with no count cap and no cut.
    `MAX_FILES` drops to 2,000, so a package is never bigger than a list an
    owner can read.
  - 6: a blob is written first and owned second. An "already paid" record
    exists only once its bytes do, and a failed write leaves nothing that
    skips the next reservation.
  - 7: claims carry a token. Progress, release and finish require it; every
    progress write renews the lease; a save runs before and after every effect.
    A superseded activation stops at its next step.
  - 8: on replay, a screen at the intended id is adopted only if its content
    matches. Otherwise the package's screen goes under a fresh id.

  Not adopted:
  - 5: DISAGREE_CONCERN. The writer IS now the shared
    `write_universe_file(mode="exclusive")` from #4254. Its non-POSIX branch is
    check-then-use, and that PR's author owns it as the single-tenant tray's
    containment. This lane does not fork a second writer for one platform: one
    definition of safe file IO is the lead's rule.

- **gpt-6-astra code refute, round 3, the last (2026-10-01): ADAPT, with one
  P1.** The identifier exemption applied to matching key names at every
  depth, so `state_schema[0].default.id` skipped the credential test. Fixed:
  the exemption is now by SCHEMA LOCATION (`_ID_PATHS`, with list indices
  dropped and a definition's component key read as `*`). A nested `id` in user
  data is scanned in full. No fourth round, per the three-round cap.

- **Dry run against the founder's live GTM Village (2026-10-01, read-only,
  production container, no publish).** The probe printed paths, reasons,
  parser labels and token shapes only, never content.
  - The round-2 scrub would have excluded 246 of 300 text files and refused
    150 of 217 workflows: ids, hex run ids, git shas, CSS classes and code
    identifiers, nearly all of them `opaque_high_entropy`.
  - The lead's rule: the default must not strip a working village. So the
    scan is tiered (D2): only a certain credential excludes or refuses; a
    suspect is listed for review and stays in.
  - Measured after tiering:
    - 322 files are included and 46 excluded. Of those 46, 8 are certain
      credentials (2 URL userinfo), 4 carry email addresses, and the rest are
      runtime files, databases, brain files, wiki drafts and the checkout.
    - 289 files are flagged for review.
    - All 8 GTM Village workflows pass. The 14 that are still refused are
      unrelated platform-development workflows whose code assigns a mixed-class
      value to a secret's name.
    - 92 `extensions/gtm-village/` files travel.
  - The tradeoff, stated: a key pasted bare into a note, not assigned to a
    secret's name, is now listed for review instead of dropped. The owner
    reads the list before confirming; the tab says detection cannot prove
    a file holds no secret.

- **Lead tightening (2026-10-01).** A list of 289 is one nobody reads, so a key
  pasted bare must not depend on it.
  - **Published key formats are certain anywhere.** These are the
    high-precision subset of the gitleaks default ruleset (MIT), matched by
    format and never by service name:
    - model-provider `sk-` keys (their open-alphabet bodies must mix a digit
      with upper and lower case);
    - `gh?_` and `<issuer>_pat_` tokens; `AKIA`/`ASIA`; `xox?-`; `AIza`;
      `glpat-`; `sk_live_`/`rk_live_`; `hf_`; `npm_`; `SG.`; bot tokens;
      private-key blocks.
  - **Never flagged:** lowercase hex of an id's length (16, 32, 40 or 64:
    run ids, uuid4 hex, git shas, sha256), and one-class runs (kebab-case
    identifiers, constants).
  - **The review list is grouped.** One line per kind (often-private words,
    random-looking strings), with a count and the first five files.
  - **Dry run after these changes, same village:** 322 files included and 46
    excluded, 8 of them certain credentials. 269 files are flagged: 217 for
    random-looking strings and 52 for words. On the tab that is two grouped
    lines. All GTM workflows still pass.

- **Suspect tier narrowed to key-like runs (lead, 2026-10-01).** A run is
  flagged only if it passes every one of these tests:
  - at least 24 characters, after a short type prefix such as `user_` is taken
    off;
  - Shannon entropy of at least 4.0 bits per character (after detect-secrets'
    HighEntropyString, Apache-2.0; its base64 default is 4.5 and gitleaks'
    generic rule is 3.5);
  - all three of lowercase, uppercase and digits;
  - not id-length hex;
  - not identifier-shaped (camel, snake or kebab case of word-like segments,
    with digit groups up to a date long).

  URLs and paths are judged segment by segment, and timestamps are taken out
  first. Review words are now phrases: "private" and "diagnosis" were 50 of
  the 52 word flags.

  On the village dry run, flags went from 269 to 29: 20 random-looking strings
  (11 distinct, mostly copies of the same run across archived notes, and
  capability-URL paths, which are true positives) and 9 "password" mentions.
  338 files are included. All GTM workflows pass with no flags.

