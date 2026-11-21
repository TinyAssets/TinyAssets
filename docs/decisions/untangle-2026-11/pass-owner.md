# pass-owner as built (2026-11-21)

d2 §E steps 4 and 5 for the owner area (`platform/owner/`), on the model of `pass-sandbox.md`,
`pass-files.md`, `pass-doors.md` and `pass-gateway.md`. The listing it starts from is
`list2_owner.md` (§2g comments, §2h tests), taken at `fdac25ba4`, so every item was re-read at
`origin/main` `70a45fc49`. Lanes A, O, T and R had moved most of §2h since.

The area is about 175 files, so the passes are two PRs (`tasks.md`, the passes line):

1. **This PR** (`untangle/pass-owner`): the comments pass on the object's sources (`src/`) and
   its config (`wrangler.jsonc`), and the tests pass's items decided, the two spies on a
   class's prototype among them. It changes no line of code and no test.
2. **`untangle/pass-owner-2`**, owed: the rest (§4).

Behaviour is unchanged. Every non-test source of the workspace (`src/*.ts`, `memory/*`,
`guards/rules.ts` and the guards' other helpers, `vitest.config.ts`, `write-door-map.ts`), each
minified with esbuild, `wrangler.jsonc` without its comments, the JSON records, and the
Worker's entry bundled with all it imports, has the same digest on base and head, so no line
of code changed, only comments. The migrations' SQL is in that digest, so no comment moved
into a statement.

## 1. Comments

**Kept:** vendor facts (Workers', R2's, E2B's and KV's limits and answers, workerd's and V8's
measured costs), spec pointers (the ADRs, `engines-replan.md`, `store-probe.md`,
`facet-probe.md`, d3's sections, the gateway research's sections), the decision files that
hold a design (`untangle-2026-11/r5r.md`, `r5w.md`, `r7-sweep.md`, the restore's), the
founder's rules with their dates, and the reason comments: why an order, a bound, a refusal
or a wait is what it is.

**Removed or rewritten:**
- **Provenance tags,** about 300 across 46 sources: `slice P`, `M`, `W`, `L`, `S`, `R`, `E`,
  `ID`, `SIGNIN`, `BRAIN`, `STORE item 2`, `slice 3` and `slice 10`, `lane R`, `R5 slice 1`,
  `R5 P-decide`, `untangle B4`, `F1`, `F2`, `H1`, `3b`, `A2`'s `A6, salvaged`, `tasks 2` to
  `tasks 7` with their items (`G2`, `G4`, `F1`, `S3`, `3.5`, `3.6`, `6.1`), the old plan's
  ids (`S3a`, `S3b`, `S4-S5`, `S30`, `S31`, `S34`, `W3`, `N10`, `P0-3`, `D2`, `E2`, `A1`,
  `A5`), `pi audit F6` and `F8`, `ruling M5`, `ADR-037's ruling 2`, and the review ids
  (`review L1` to `L7`, `M1` to `M6`, `N1` to `N10`, `H1`, `H3`, `F3`, `L5-1`, `L5-2`,
  `R2-L1`, `R2-M1`, `R2-L3`, `R4-L2`, `M-4`, `M-5`, `M-6`, `H-1`, `H-6`, `M-a`, `round 11`,
  `Lows A and B`, `rvlim`, `rvbr`, `rvburst V1` to `X3`, `rvbackup 1`). Git holds where a line
  came from. The sentence each tag hung on stays where it says why. A migration's header is
  its number, not "after the vendor's 4".
- **The old stack,** whose code the cutover deletes: `ledger.ts`'s, `grants.ts`'s,
  `sessions.ts`'s, `wakes.ts`'s, `usage.ts`'s, `collections.ts`'s, `inbound.ts`'s and
  `admission.ts`'s "Ported from `tinyassets/...`" lines, and the "Declared differences from
  the Python" lists of `grants.ts`, `sessions.ts` and `usage.ts`. What a list said that holds
  of the object today stays, as a fact of the object (a session's proof is the cookie only,
  live sessions are bounded, a reservation shrinks its output). Each "the Python's X" gloss
  goes (`MAX_PER_OWNER`, `revoke_owner`, `require_work`, `DeliveryBreakers`,
  `check_identity`, `_apply_key_request`, the op journal). `collections.ts`'s "Python's
  `str.isidentifier()`" stays: it names the grammar the identifier follows, not the old stack.
- **Stale:**
  - `wrangler.jsonc` "Not deployed yet: no workflow deploys platform/" (the kit deploys it),
    "binds four things" (it binds four services, `CONTROL` and the `ERASURES` producer, which
    it now names), `CONTROL`'s "the same placeholder id" (the kit writes the id), and "checks
    this config and the files Worker's" (the config guard checks every Worker it binds).
  - `grants.ts` "Owed by a later slice: the doorbell rechecks the grant just before it sends".
    It does (`GrantReader.live`), as `ledger.ts`'s header says. And its "Declared differences":
    the kill switch is the control record's, read by the object (`control-gate.ts`), and the
    held key request is applied (`Policy.applyKeyRequest`).
  - `sessions.ts` "An owner's admission … is the binder's, from the owner directory". It is
    the object's own row (`admission.ts`), checked in the binding check.
  - `erase.ts` "a later `getByName` makes the owner afresh, admitted … The window stands until
    slice 10 closes it (a record outside the object, or an explicit platform admit)". Both are
    built: a made object is `pending` until the platform's explicit admit (`admission.ts`,
    which already calls the window closed), and the doors keep the erased record of step 2.
  - `erasure.ts`'s "design-decision §1.4 item 6", a document not in the repo, and its "The
    contract slice L's door answers with (tasks.md)".
  - `network.ts` "the sandbox slice (8b) sends the network document". The sandbox Worker does.
  - `owner-do.ts` "whose hold is a later slice's (tasks.md R5)" names the plan's line,
    R5's later hold-on-sync for `paths`, as `r5-decide.md` item 3 says.
  - `schema.ts`'s part 6 "Since untangle F1 nothing marks or reads it; F2 reshapes the table".
    Nothing does; F2 is no plan's.
  - Every pointer to a working file never in the repo: `backup_shape_research.md`,
    `history_keys_research.md`, `datashapes_research.md`, `history_research.md`,
    `memshape_research.md`, `ai_storage_research.md`, `autoconnect_research.md`,
    `operator_research.md`, `starter_research.md`.
  - `relay.ts`'s "Salvaged from A6 (`platform-a6`'s `sockets.ts`) … not its pi `watch()`",
    a branch's history.
- **Restating the code, 42 comments:** the 21 handler modules' three-line paragraph under
  their `THE DOORS` rule ("Each door's body, which the object's runner hands …"), a copy of
  `owner-do.ts`'s header, and the same modules' `/** The \`x\` doors' handlers, by door. */`
  over their `xDoors`. A module's own paragraph after it (the files doors' two-chunk rule, the
  egress doors' tokens) stays.
- **Run-on lines** (a paragraph patched past 100 characters, or broken mid-sentence),
  reflowed in about 40 paragraphs, among them `owner-do.ts`'s header (three), `gate.ts`'s,
  `schedule.ts`'s (whose "a a chunk delete's" is now "or a chunk delete's"), `control-gate.ts`'s,
  `egress.ts`'s, `sandbox-time.ts`'s, `ledger.ts`'s bounds and `changelog.ts`'s header.

**Left as they are, each with its reason:**
- `owner-do.ts`'s 110-line header and the field docs of `OwnerDO`. The header is the one
  place that tells the binding, the runner, the lanes, the erasure and the admission end to
  end, and the modules point to it; each field doc says which module a part is and what it
  holds, which its type's name does not.
- `schema.ts`'s part and migration comments. Each says why a table, a column or a trigger is
  what it is, which the frozen SQL cannot.
- `ledger.ts`'s `ROOM_POST` and `MAILBOX_RECEIPT` headings, the ledger's own names for its
  two rules.
- `src/env.ts`, which is generated (`npm run generate`).
- `src/import.ts`, which `cutover.json` deletes, as `pass-doors.md` skips the import window.
- The four sources R5's hooks change (`actions.ts`, `door-map.ts`, `policy.ts`, `runs.ts`,
  `untangle/r5-hooks`), so the two merge cleanly: part 2, once it lands.
- The owner side of `platform/contract/` that list2 §2g counts beside the owner's
  (`limits.ts`, `control.ts`, `run-events.ts`, `policy-document.ts`, `apps.ts` and the rest).
  They are the contract area's pass, as `pass-gateway.md` left the contract's own.

## 2. Tests

| Item | At this head | Decision |
|---|---|---|
| The surface's three checks (`owner.test.ts`, `door-methods`, `write-gate`) | `door-methods.test.ts` gone; `owner.test.ts` reads the prototype against the table, its absent-wake list gone (O2) | Done. |
| `write-gate.test.ts` pins the hub's helper text | Present: `#door`'s and `#quiet`'s bodies exact, `#handled`, `#call`, `#synced`, `#filing`, `placing`, `reading`, `rereading` by substring, the parts by a regex | **Owed,** part 2: a reformat breaks it with no change in behaviour. |
| `policy-boundary.test.ts`'s member-name lists and the `o` rule | `WIDENS` and `OTHERS` gone: the widenings are a type fact with a minted proof (O1b) | Done. Its import and table lists are kept: a security statement, as `pass-doors.md` keeps `rules.ts`. |
| `admission-caller.test.ts` tied to file names | `READER`, `SENDER`, `CHANNEL`; `ADMIN` gone | **Kept.** Which files may name the reserved caller is the guard's subject. |
| `relay-memory.ts` reads constants by regex | Present | **Kept.** `relay.ts` imports `agents/websockets`, so a node guard cannot load it; a constant written another way fails the read loudly ("holds no numeric"). |
| `ledger-boundary.test.ts` file allow-lists | Present | **Kept,** a security statement: who may import the ledger. |
| `memory/probe.ts` builds the modules by hand | It runs the real `OwnerDO` (`LegOwner`) | Done. |
| `test/admitted.ts`, `test/worker.ts`'s subclasses | Gone (T1c, #5564) | Done. |
| Constants pinned to their own literal | About 20 (`PLACE_TTL_S`, `APPEND_TRIES`, `CODEMODE_TIMEOUT_MS`, `NOTICES_KEPT`, `LANE_HUNG_S`, …) | **Owed,** part 2: each retired, or kept where it holds what the figure is for, as `pass-gateway.md` decided `G-T3`. |
| Sleeps and timeout literals | Each a poll that ends on what it reads | **Held** by the waits guard (`ci/guards/timeouts.test.ts` `timerWaits`, over `platform/*/test/*`). |
| JSON read back out of refusal messages | None left | Done. |

**The two spies on a class's prototype** (`tasks.md`'s passes line). Each passes its call
through unchanged and makes no call refuse. Both are kept, each with its reason:
- **`bodies.test.ts` `Files.prototype.begin`** ("a placed write past a chunk the sweep is
  deleting begins again once it is gone"). The test must delete the `deleting` rows only once
  the write was refused and waits, or it never tests the retry. Nothing else shows that: the
  refused unit rolls back, so it leaves no record; the refusal is the object's own, before any
  `FILES` call, so the FILES gate never sees it; and two calls' order at the object is a
  convention, not an event. The spy reads the refusal as an event the test caused, and the
  test waits on it (`until`). The other way is a hook in `bodies.ts` for the test.
- **`proofs.test.ts` `Sessions.prototype.extend`** ("hands its holder no other"). The test's
  subject is a store that keeps the proof its door handed it, and a real minted proof reaches
  only a handler's stores: no test may import the mint (`proof-mint.ts`, policy-boundary's
  `forged`), and RPC would clone it. The spy is that store, the threat the test is about,
  kept past its call. The other way is a door that hands a proof out.

## 3. Found and left

- `specs/owner-object/spec.md`'s code mode requirement names `CODE_ANSWER_MAX` (31 MiB as V8
  serializes it), which no code defines: `codemode.ts` fits an answer to `PAGE_BYTES` and
  refuses one past it (`CODE_ANSWER_TOO_LARGE`). Part 2, with the spec's other owner names
  (done, §6.3).
- The owner's swallowed errors and retries, each to be re-read and decided as the other areas'
  were (`bodies.ts`'s `PLACE_TRIES` wait on `chunk_deleting` among them). Part 2 (done, §6.4).

## 4. Owed: `untangle/pass-owner-2` (built, §6; what R5's hooks hold back is part 3's, §6.5)

- The comments pass on `test/`, `guards/` and `memory/` (about 350 provenance tags, the test
  titles that are a tag, `L5:` or `A6:`, and the Python pointers of
  `policy-boundary.test.ts` and `ledger-boundary.test.ts`).
- The comments of `actions.ts`, `door-map.ts`, `policy.ts` (its 14 Python pointers) and
  `runs.ts`, once R5's hooks land.
- The tests pass's changes: the constant pins, `write-gate.test.ts`'s pinned helper text, and
  a search for checks that could never fail.
- §3's spec name and the swallowed errors and retries.

## 5. Proof

- Every non-test source's minified esbuild digest, `wrangler.jsonc` without its comments,
  `doors.json`, `migrations.json`, `model-ranking.json`, and the entry's bundle, the same on
  base `70a45fc49` and head.
- The recorders: `rpc` and `rv` (the owner's) and `wire` and `tools` are the same.
- No mutation: the PR changes no code and no test.

## 6. Part 2 as built (`untangle/pass-owner-2`, 2026-11-21)

The comments of `test/` and of the two guards whose checks it changes (`owner-bound`,
`write-gate`), the tests pass's changes, the spec's name and the swallowed errors and retries,
on `origin/main` `88e74e1cd`. Behaviour is unchanged: no line of product code changed (§6.6).
The comments of the other guards and of `memory/` were done here too, but with them the diff
passed 1,500 changed lines, so they move to part 3 (§6.5).

### 6.1 Comments

**Removed or rewritten**, across 64 files:
- **Provenance tags,** about 270: the slice names (`slice P`, `M`, `L`, `R`, `S`, `W`, `ID`,
  `SIGNIN`, `BRAIN`, `STORE item 2, phase 1`, `slice 3`, `slice 5`, `slice D`), `lane R`,
  `untangle A2`, `B4`, `F1`, `H1`, `R5 P-decide`, `P-resume`, the old plan's ids (`tasks 2` to
  `tasks 7`, `S30`, `S34`, `S3b`, `W3`, `N10`, `P0-3`, `G2`, `G4`, `E1`, `E2`, `D2`, `B23`,
  `B25`, `P4`, `P6`, `P11`, `H0`, `decisions A1`, `A7`), `pi audit F8`, and the review ids
  (`review H1` to `N10`, `R2-L3`, `R4-1` to `R4-L2`, `L5-1`, `L5-2`, `L6-2`, `M-1` to `M-6`,
  `rvlim`, `rvburst`, `rvbackup 1`, `rvsi`, `rvmem4`, `rvsh2_pack`, `rvsh7_starve`, `round 2`
  to `round 11`, `Lows A and B`). The sentence each hung on stays where it says why.
- **Test titles that were a tag:** the `A1:` to `A6:`, `L5:`, `R1:` and `F3-seed-wait:`
  prefixes, and the describes named for a review, each renamed for what it holds: `review
  round 3` (a retired path and a seed that cannot land), `starter updates, review round 2`
  (starter proposals), `the reviewer's untested cases` (cases no other test holds), `W3 limits`
  (the wake limits). A test's name is no record: `ci/budgets.json`'s slowest-test names are a
  past run's measurement and stay as measured.
- **The old stack:** the "Ported from `tests/test_*.py`" headers of `ledger.test.ts`,
  `collections.test.ts`, `sessions.test.ts`, `wakes.test.ts` and `grants.test.ts`,
  `usage.test.ts`'s `tinyassets/request_budget.py` and `broker/policy.py` (its transition
  rules stay, as the object's), `ledger-bounds.test.ts`'s "the Python's owed L5", and "As in
  the Python test" and "as the Python door refused None". What each said that holds of the
  object stays, as a fact of the object (the object is the account, so no test erases one).
- **Working files never in the repo:** `history_research.md` and `operator_research.md`.
- **Stale:** `owner-bound.test.ts`'s header kept a changelog of which slice added which table
  (S, W, ID, M, H1); the guard computes every term, and git holds the history.
  `codemode.test.ts`'s "is slice M's" (the `code` door is `code-door.test.ts`'s) and "owed to
  H2's live probe" (no such probe is owed; workerd shows neither CPU limits nor isolation).
  `erasure.test.ts`'s and `cron.test.ts`'s "carried over from the deleted ... tests".
- **Run-on and short lines** a removed tag left: about 35 paragraphs reflowed to 100 columns.

**Kept:** the ADR, spec, d3 and plan pointers (`d3 §N6`, `plan §6`), measured figures with the
run they came from, `network.test.ts`'s
"the old stack's `ask` mode", which names the policy shape the validator refuses, and
`rv.record.ts`'s "the reviewer's RPC recorder", the tool's name. `test/import.test.ts` goes at
the cutover, as `src/import.ts` does, and is skipped.

### 6.2 Tests

**The constant pins** (`pass-gateway.md` `G-T3`'s rule: a pin that restates its source is
retired; a check of what the figure is for stays):

| Pin | Decision |
|---|---|
| `network` `DNS_SERVERS`, `KEY_MIN_BYTES` | **Retired.** The open plan's expected document names `1.1.1.1`; the key test mints with 32 bytes and refuses 1, 16 and 31. |
| `network` `ALL_TRAFFIC` | **Rewritten:** the open plan expects `denyOut: ["0.0.0.0/0"]`, what "denies all else" means, so the document is held to it, not the constant. |
| `erasure` `RECOVERY_WINDOW_S` | **Retired.** The answer's `complete_at` is checked at 30 days, as a literal. |
| `bodies` `PLACE_TTL_S`, `LIMITS.maxRowBytes`; `owner` `maxRowBytes` | **Retired.** The sweep test runs past the TTL; the row of `maxRowBytes` is placed in two chunks and one byte more is refused. |
| `files-quota` and `owner-bound` `INDEX_BYTES`, `owner-bound` and `files-share` `WORST_FILL` | **Retired.** `owner-bound` checks the manifest term is `INDEX_BYTES` times `WORST_FILL`, and `files-share`'s large test measures the fill against `WORST_FILL`. `files-share`'s `PAGE_ROOM` stays: it derives the source's literal 3,066 from the page's usable bytes. |
| `brain` `APPEND_TRIES`, `collections` `maxRowsCharge`, `codemode` `CODEMODE_TIMEOUT_MS` and `CODEMODE_DEFAULTS`, `notices` `NOTICES_KEPT`, `doorbell` `SUBSCRIPTIONS_PER_AGENT`, `file-readers` `SEARCH_SCAN`, `changelog` `LOG_WIDE` | **Retired.** Each restated its source; the test beside it shows the figure at work (`LOG_WIDE` against the schema's frozen trigger text). |
| `schedule` `LANE_HUNG_S` | **Rewritten:** the job's `hung_timeout_seconds` is read against `LANE_HUNG_S`, the lane's own, not 60. |
| `usage` `MODEL_DEFAULTS` | **Rewritten:** the default settings are checked as the literal they are (every cap `null`, no sources), the title's "every cap off". |
| `owner-bound` `EGRESS_PATH_MAX` | **Rewritten:** the schema's `CHECK` must hold `EGRESS_PATH_MAX` itself, so the constant and the frozen SQL are tied, not each pinned to 2048. |
| `admit` `MAX_KEYS` equal to `GRANT_LIMITS.maxOwnerClients` | **Kept:** two figures that must move together, the link `limits.ts` names ("which a test holds equal"). |
| `files-share` `SHARE_BYTES`, `owner-bound` `IMAGE_MAX`, `limits-audit`'s five | **Kept:** each is derived from what it bounds (a record's layout, the widest row, the move's pieces), not restated. |
| `file-readers` `SEARCH_ANSWER_BYTES` | **Kept:** it holds the private 5,462 of `SEARCH_ENVELOPE_BYTES` to `MAX_PATH_BYTES`' base64, as `file-search.ts` says. |
| `agent-doors` `OWNER_TREES`, `config` `OWNER_CONTROL` | **Kept,** security statements: the trees only the owner's full grant writes, and the reads of `CONTROL`. A test over the constant could not see a tree dropped. |
| `memory-bound` `MEMORY` | **Kept:** the regex reader's one check against the sources (`relay-memory.ts`, kept in §2). |

**A check that could never fail, made to:** `egress.test.ts` "receipts every refusal, keeping
the newest refusalsKept" made nine refusals and pinned `refusalsKept` to 500, so no bound was
ever reached. It now fills a full bucket of older refusals, and the nine new ones let the
oldest nine go.

**`write-gate.test.ts`'s pinned helper text:** the helpers' bodies are compared as code, not
text (`canonical`: no space beside a bracket, a dot, a comma or a semicolon, no trailing
comma), and the parts' members by their name and arrow at any indent. A reformat of `#door`
(its argument on a line of its own, a trailing comma) failed the old check and passes this
one; `#door` running its act outside the gate fails both. What it holds is unchanged: which
gate each helper reaches, the source-shape guard `pass-owner` §2 keeps.

### 6.3 The spec's `CODE_ANSWER_MAX`

`owner-object/spec.md`'s code mode requirement now says what the code does: a script's answer
fits one answer, as every read does (the spec's paging requirement names `RESULT_CHARS`); output
past it is cut at a character and said so (`output_cut`), and a value that alone passes it is
refused `answer_too_large`, its calls and what landed kept. Its scenario is two:
`code-door.test.ts` "cuts output past one answer, saying so, and refuses only a value past it"
holds both. No other name in the owner spec is missing from the code.

### 6.4 The owner's swallowed errors and retries

Each `catch` of `owner/src` (about 90) and each retry and wait was re-read. All are kept, and
behaviour is unchanged.

**Not swallowed: a refusal translated, or a cleanup before the rethrow.** Parse-or-refuse
readers (`agent-file.ts`, `launch.ts`'s agent file, `connections.ts`' model, scope and redirect
URI, `relay.ts`'s binding header, `export.ts`'s cursors, `restore.ts`'s first part), each a
fixed refusal; a grant gone read as `authority_changed` or `sync_fenced` (`authority.ts`,
`owner-do.ts`, `runs.ts`); a conflict turned into its answer (`collections.ts` `conflicted`,
`files.ts` `fileConflicted`); `codemode.ts`'s tool call, its refusal's text or `internal error`
past the VM; and the cleanups that rethrow (`bodies.ts`'s release, `files.ts`'s abort,
`connections.ts`' refresh settle, `owner-do.ts`'s runner end, `runs.ts`'s post leave,
`gate.ts`'s unit, which commits what it wrote and rethrows).

**Swallowed, kept, each with its reason beside it:**
- **Logged, then tried again by its own lane:** `backup.ts`'s manifest sweep
  (`MANIFEST_RETRY_S`), `files.ts`'s chunk delete (`GC_RETRY_S`; no hash in the log),
  `sandbox-time.ts`'s meter read, `connections.ts`' provider revoke, `wakes.ts`'s recovery and
  send (each deferred `WAKE_LEASE_S`; no id, URL or body in the log), `launch.ts`'s network
  re-apply (`NETWORK_RETRY_S`), `erase.ts`'s erasure, resumed from its recorded step, and
  `owner-do.ts`'s schedule sync after a first change, which the next door or pass that syncs
  makes again.
- **Logged, the call's end settled anyway:** a run's start with no answer settles `unknown`
  (`launch.ts`); a sign-in's start with no answer ends it `start_lost`, its lease freed
  (`signins.ts`); a run's cancel, a lease's release (its idle reap pauses it), and a refused
  sign-in's stop.
- **Read as an answer:** the kill switch unreadable is closed (`wakes.ts` `open`); a ring that
  crossed as a refusal is `failed`, anything else `unknown` (a byte may have left); a ring whose
  request is refused as invalid is `failed`, `not_sent`; a malformed reseal from the gateway is logged and the call
  settles (`egress.ts`); a refresh refused leaves the credential to the open's own check; an
  upload intent gone is no owner (`files.ts`); an unreadable `agent.json` held no scope
  (`starter.ts`), and a lost swap is the owner's change, left to them; a starter pass that fails
  is noticed and retried in its row; a socket that could not attach is closed `lost`, a bad
  frame closes it `frame`, and a send or close to a peer gone is nothing to do (`relay.ts`).
- **Not text:** `edit.ts`, `file-search.ts` and `files.ts`' UTF-8 decodes answer `not_utf8` or
  `null`, never a fallback.
- **A refusal receipt that cannot be written** (`owner-do.ts`, `files.ts`): logged, and the
  refusal stands regardless.
- **The archive** (`archive.ts`): a read whose mark moved is `null`, read again; a parent not
  the object's is no parent; `archive_changed` mid-pass is the next pass's; `archive_full` and
  `storage_full` hold the lane and tell the owner once.

**Retries and waits, kept:**
- `bodies.ts` `PLACE_TRIES`: a placed write that meets a chunk the sweep is deleting begins
  again, waiting 100 ms more each try, five tries, then `chunk_deleting` stands ("begin
  again"). A placed write is one door call, so the object, not its caller, is the one that can
  begin it again; the sweep's delete is one R2 call between two units. **Found and left:** the
  wait is a sleep, not the event: the sweep's delete could hand the object a promise that ends
  when its unit lands, and the write would wait on that, bounded by `LANE_CALL_MS`. That changes
  when a write answers, so it is not this pass: a sub-line of `tasks.md`'s re-check loop line
  (`untangle/place-wait`).
- `bodies.ts` `READ_TRIES` and `ledger.ts`'s `archive_changed` retry: a read raced by mail
  between its units is read again a few times, then `storage_busy` with its `Retry-After`.
- `files.ts` `APPEND_TRIES`: an append that loses its swap is tried again, at most as many
  times as uploads may be open, then `file_busy`.
- The lanes' back-offs (`ARCHIVE_RETRY_S`, `ARCHIVE_BUSY_RETRY_S`, `MANIFEST_RETRY_S`,
  `MANIFEST_BUSY_RETRY_S`, `GC_RETRY_S`, `NETWORK_RETRY_S`, `LANE_RETRY_S`): each a row's own
  time, never a loop in one pass; the busy ones are the pool's wait (`SLOT_WAIT_MS`).
- `launch.ts` `settledWithin`: a run door's wait ends at the run's settle (`#wake`), bounded by
  the caller's `waitMs`. `gate.ts` `unitsNow`: a lane takes the pool's units now or is `busy`.

**Found and left:** `changelog.ts` `LOG_WIDE` has no reader in product code; its one reader is
`changelog.test.ts`, which holds it to the frozen trigger text. A product export only a test
reads is a code change: with `untangle/place-wait`.

### 6.5 Owed: `untangle/pass-owner-3`, once #5707 (`untangle/r5-hooks`) merges

The files R5's hooks change, so the two merge cleanly, untouched here:
- `src/actions.ts`: `R5 slice 2`, `R5 P-resume` and `r5_research.md` (a working file never in
  the repo) in its header.
- `src/policy.ts`: 16 pointers to the old stack, and `slice M` twice.
- `src/runs.ts`: `slice ID`, `slice S`, `R5 P-resume`, `review H1` twice and `review M1`; its two
  catches are an `authority_changed` translation and a post's leave before the rethrow (kept,
  §6.4).
- `src/door-map.ts` and `doors.json`: no tag; nothing found.
- `test/actions.test.ts`: `R5 P-decide`, `R5 P-resume`. `test/policy.test.ts`: `N4`, its pins
  to be read with §6.2's rule.
- `guards/doors.test.ts`: ten door reasons, strings the guard prints, carry `slice L`, `slice
  W`, `tasks 6` and "slice SIGNIN, owed: an MCP tool for the past runs".

And the comments of the other 13 guards and of `memory/`, moved here from part 2 for its size
(about 300 changed lines; none of them in a file #5707 changes): their tags (`slice R`, `L`,
`S`, `M`, `untangle A2`, `B4`, `review L5-2`, `L6-2`, `M3`, `M4`, `M-a`, `rvburst`, `rvbackup 1`,
`round 5`, `H0`, `decisions A7`, `tasks 2`, `3.6`), the "Ported from" headers of
`policy-boundary.test.ts`, `ledger-boundary.test.ts` and `revision-primitive.test.ts`,
`rpcretain_research.md` (a working file never in the repo, four places),
`admission-caller.test.ts`'s "nothing is deployed, so no tombstone exists yet", `alarm.test.ts`'s
"salvaged from A6", and the paragraphs they leave short.

### 6.6 Proof

- Every source of the workspace (`src/`, `guards/`, `test/`, `memory/`, `wrangler.jsonc`
  without its comments, the JSON records), minified with esbuild after blanking each test's
  titles, and the Worker's entry bundled with all it imports: the same digest on base
  `88e74e1cd` and head, but for the 18 test files of §6.2.
- The recorders: `wire`, `tools`, `rpc` and `rv` are the same.
- Mutations, each killed by the changed test: `ALL_TRAFFIC` as `0.0.0.0/1`; the lane's job given
  `LANE_PASS_S` as its hung timeout; the refused receipts kept one past `refusalsKept`;
  `MODEL_DEFAULTS.turn_cost` set to 1; `EGRESS_PATH_MAX` as 4096; `#door` running its act
  outside the gate. A reformat of `#door` across lines with a trailing comma passes the
  write-gate guard and fails its old check.
