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
  refuses one past it (`CODE_ANSWER_TOO_LARGE`). Part 2, with the spec's other owner names.
- The owner's swallowed errors and retries, each to be re-read and decided as the other areas'
  were (`bodies.ts`'s `PLACE_TRIES` wait on `chunk_deleting` among them). Part 2.

## 4. Owed: `untangle/pass-owner-2`

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
