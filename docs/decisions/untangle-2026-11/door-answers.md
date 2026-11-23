# door-answers (2026-11-23): the deploy plan's hold reads the owner answers the doors read

`untangle/door-answers`, owed by `handed-types.md` (#5830, "Review" and "Owed"). The plan's hold
(`deploy/handed.ts`, `deploy.ts` `servedHold`) holds what the doors' version before hands the
owner (names and types, `dropped`, `droppedTypes`), but not what it reads back: the doors read
some owner answers field by field (`contract/src/owner.ts` `DoorTypes` `answer`), typed for
`tsc` alone, so no hold read them at a serving commit, and a deploy or a rollback could pair a
doors version that reads an answer field with an owner that no longer answers it.

## Research (Fact 14), from the code and git

### Who reads an owner answer, and how it is typed

- **Only the doors bind the owner** (`doors/wrangler.jsonc` `OWNER`; `owner/wrangler.jsonc`
  binds files, gateway, codemode and sandbox, none of which binds `OwnerDO`). Codemode's
  scripts reach the doors through the owner itself; the gateway reads the owner's `metering`
  (held already, the shape `metering` of `handed.json`), which the doors relay from
  `egressReserve`'s answer. So every answer pairing across Workers is the doors' (and what
  they relay on).
- **The doors' own code reads 29 doors' answers field by field**, each typed by `DoorTypes`
  `answer`, which `OwnerDO implements OwnerDoors` holds the owner's handlers to at one commit:
  `admissionSet` and `ownerState` `state` (`authorize.ts`), `sessionOpen` `session`,
  `tierRead` `tier` and `fileRead` `entry`, `inline` (`app.ts`), `erasureOutside` `keys`,
  `routes` or null (`erase.ts`), `toolProfile` `profile`, `tools`, `withheld` (`mcp.ts`),
  `definitionRead` (`definition.ts`), `wakeEvents` `events` (`events.ts`), `keyMint` `name`,
  `created_at`, `scopes`, `grantRevoke`, `grantForget` `forgotten` (`api-keys.ts`),
  `keyList` `keys` (`revoke.ts`), `inboundAccept`, `inboundCreate`, `inboundSet`,
  `inboundRevoke` (`inbound.ts`), `connectionPrepare` and `flowFinish` (`connect.ts`),
  `fileUploadTarget` (`file-bytes.ts`), `ownerExport` (`app.ts`), `importFileBegin`,
  `importCheck` `more`, `importDiscard` `namespace` (`import/index.ts`), `egressOpen`,
  `egressReserve`, `runDecide` `hook`, `fileReadChunks` `chunks`, and `fileRead` `inline` for
  the runner (`egress.ts`), `restoreStep` `done`, `keys` (`operator.ts`).
- **The other 26 `DoorTypes` answers are passed on whole** and read field by field only by the
  doors' tests: the operator's (`tierSet`, `dataKeyRewrap`, `gatewayReseal`, the restore
  doors but `restoreStep`, `erasureBegin`), the app routes' (`policyRead`,
  `policyProposals`, `grantList`, `registerProposals`, `noticeList`, `signinList`,
  `registerAgent`, `ownerErase`, `sessionClose`), and `sandboxBilled`, `outsideAgents`,
  `collectionGet`, `fileStat`, `sandboxDays`, `starterReceipts`, `egressReceipts`.
- **Read past the doors by no type at the doors' commit**: the owner app (`doors/app/app.js`,
  served with the doors, untyped JavaScript) reads most passed-on answers; the sandbox runner
  (`sandbox/runner/sync.ts`) reads `fileChanges`, `fileList`, `fileRead`, `fileReadChunks`,
  `fileSyncUploadBegin` and `fileSyncCommit` by casts, and `decide.ts` `runDecide` by
  `DoorAnswer`'s keys; the deploy kit reads the operator's answers by casts (`deploy.ts`
  `as RestoreSource`, ...); outside callers read the tools' answers (the tool contract).
- **The vocabulary.** The answers are objects (one may be null: `erasureOutside`), some a
  union of arms (`RestoreAnswer`, `EgressOpened | EgressRefusal`, `importFileBegin`,
  `runDecide`, `InboundAnswer`), with literal values (`TierName`, `ok: true`) and nested
  shapes (`entry`, `cards[]`, `ServedDefinition`). Two field values are no JSON type:
  `fileRead` `inline` (bytes, as a request's `bytes`) and `egressOpen` `ended`, a
  `ReadableStream`, which only RPC carries.

### What changes, from git

- 31 commits touched `owner.ts` this month; nine changed a `DoorTypes` answer. Every one but
  two added a field or a door (`toolProfile` `withheld`, `policyRead` `notes`, `hooks`,
  `codemode`, `runDecide`, `definitionRead`, `restoreHold`). The two that took one away
  replaced the door (`policyProposal` by `policyProposals`, #5832; `registerProposal` by
  `registerProposals`, #5821), each kept a deploy as a `dropped` row: the step a request
  field has, which no answer field had.

### Which deploy pairs which answers

- The owner deploys before the doors (`facts.ts` `WORKERS`), and a rollback takes the doors
  back first, so the one pairing is the doors' version before reading this tree's owner's
  answers: the same pairing the requests' hold holds. New doors never read an owner before
  them, so a field newly answered, or newly read, is never a pairing to hold.

## What "compatible" means, per answer field

- **The owner answers ⊇ what the doors' version before reads.** Every field the version
  before's row reads (its answer's fields, but those it marks `unread`) the tree's row still
  lists (answered); one it reads as always answered (`required`) the tree's answers always;
  each JSON type the tree's owner answers it as is one the version before reads (`integer`
  under `number`, `takesType`); and the tree's answers null only where the version before
  read null too (`nullable`). Widening a type the caller reads is a narrowing for the
  caller: refused.
- **The step a field takes away: `unread`.** The doors stop reading a field first (the row
  lists it `unread`: the owner still answers it as its schema says, the doors' types hold it
  no more), and it goes once the stage serves no doors version that reads it, as `dropped`
  is for a request's field. A type or an optional step (as `droppedTypes` and shapes'
  `always`) has no case today; a change of a read field's type goes under a new name, the
  old one marked `unread`.
- **A doors version before whose row declares no answer** (every serving commit's table
  today): what it reads is unknown, so nothing holds its answer, and the plan says so, door
  by door (`answers held by nothing, the doors' version before declaring none: <door>, ...`),
  never passing it as held, as #5830 named an untyped field. This change answers every field
  as before, so the first deploy pairs nothing that refuses.

## Design, from the research (decided before building)

1. **The door table declares each answer the doors read** (`doors.json` row `answer`):
   `required` and `optional` fields, each schema its JSON type alone (`fieldTypes`, the
   requests' vocabulary, now with `stream`, which no tool's field names, as `bytes`),
   `nullable` where the owner may answer null, and `unread`. One place: the 55 `DoorTypes`
   answers move in, each at its top level.
2. **`tsc` holds both sides at one commit from it.** `npm run generate` writes `DoorAnswers`
   (what the doors read: the fields, but `unread`) and `UnreadAnswers` (what the owner still
   answers); `DoorAnswer` is the row's, narrowed by `DoorTypes` `answer` only where a JSON
   type is wider (a literal, a union's arms, a nested shape), and `tsc` refuses a narrowing
   of a field the row does not read; `OwnerDO implements OwnerDoors` holds each handler to
   it, and `owner-do.ts` `UnreadHeld` to its `unread` fields too.
3. **The hold reads it** (`deploy/handed.ts` `answerHold`): the rules above, and the doors
   whose version before declares no answer (`Hold.undeclared`), which the plan line names.
4. **Left as they were**: answers passed on whole (no row declares them); the nested and
   literal parts (`DoorTypes`, below).

## Problems found (untangle step 2), with the fix above each

- **Each answer the doors read is data the hold reads (`answer`), one declaration for both
  Workers.**
  - `DoorTypes` `answer` typed the doors' reads for `tsc` alone; no serving commit's answer
    could be read, so a field the owner stopped answering passed the plan.
  - A doors version before with no declaration would have passed silently.
- **An answer field goes in steps (`unread`).**
  - No data could say that the doors read a field no more while the owner still answers it.
- **Comments.** `owner.ts`'s header and `DoorTypes`, `doors.ts`'s header, `handed.ts`'s header
  and `requests.ts`'s named answers as types alone.

## Left, with reasons

- **The nested and literal parts of an answer** (`TierName`, `ok: true`, `entry`'s fields, a
  list's items) stay `DoorTypes`'s, held by `tsc` at a commit and by no plan: the requests'
  hold has the same depth (a field's JSON type), and a deeper vocabulary is a design of its
  own. Owed below, research first.
- **The readers past the doors** (the app, the runner, the kit) read by no type tied to the
  table: a declaration of their reads would be a copy nothing holds. The runner lives across
  deploys by its own protocol (`sandbox/runner/protocol.ts`), and the kit reads the owner it
  runs against at the tree it is. Owed below, research first.
- **Outside callers' reads** are the tools' contract, not a pairing across the deploy.

## As built

- `contract/doors.json`: 55 rows' `answer`, every field the `DoorTypes` answers named, at its
  top level, typed as each handler answers it.
- `contract/src/doors.ts`: `Answer`; the table refuses an answer that is not its fields (each
  typed, listed once, at least one), a `nullable` but `true`, and `unread` fields it answers
  not; `stream` joins the vocabulary, and no tool's field names it or `bytes` (`RPC_TYPES`).
- `contract/requests.ts`, `contract/src/requests.ts`: `DoorAnswers` and `UnreadAnswers`;
  `contract/src/owner-doors.ts`: `Parts` takes `answer` only where the row declares one, and
  `Stray` refuses a narrowing of a field the row does not read, or one wider than its row's.
- `contract/src/owner.ts`: `DoorAnswer` is the row's, each narrowed field `DoorTypes`'s
  (`Narrowed`); `OwnerAnswer` adds the `unread` fields; `DoorTypes` keeps the narrower parts
  alone; the wholly JSON-typed `RewrapAnswer`, `ResealAnswer`, `RestoreHold`, `RestoreHeld`
  and `InboundRoute` are the table's. `cutover.json`: the `owner.ts` edit names
  `importDiscard` no more (its entry went; the `doors.json` edit still names it).
- `owner/src/owner-do.ts`: `UnreadAnswered`, each handler answering its `unread` fields.
- `deploy/handed.ts` `answerHold` and `Hold.undeclared`; `deploy.ts` `servedHold`'s line names
  the undeclared doors.
- Tests: `contract/guards/doors.test.ts` (the answer rules), `deploy/guards/handed.test.ts` (a
  field gone, then through `unread`; made optional; widened; `integer` under `number`; null;
  an undeclared door named; a dropped door), `deploy/guards/deploy.test.ts` (the plan stops on
  a field the doors serving read, and names an undeclared answer).

## Owed

- [ ] untangle/answer-readers: the runner's sync and decide reads, the app's reads and the
  kit's operator reads typed by `DoorAnswer` (or its successor), so the table declares them
  and the hold holds them; research first (Fact 14): the runner's protocol across deploys,
  and how `app.js` is held to a type.
- [ ] untangle/answer-depth: an answer field's nested fields and literal values held as data,
  or named as left for good; research first (Fact 14), with the shapes' types
  (untangle/handed-shape-types).
