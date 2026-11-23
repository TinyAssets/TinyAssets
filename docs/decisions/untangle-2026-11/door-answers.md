# door-answers (2026-11-23): the deploy plan's hold reads the owner answers the doors read

`untangle/door-answers`, owed by `handed-types.md` (#5830, "Review" and "Owed"). The plan's hold
(`deploy/handed.ts`, `deploy.ts` `servedHold`) holds what the doors' version before hands the
owner (names and types, `dropped`, `droppedTypes`), but not what it reads back: the doors read
some owner answers field by field (`contract/src/owner.ts` `DoorTypes` `answer`), typed for
`tsc` alone, so no hold read them at a serving commit, and a deploy or a rollback could pair a
doors version that reads an answer field with an owner that no longer answers it.

## Research (Fact 14), from the code and git

### Which readers a deploy pairs with the owner

- **Only the doors bind the owner** (`doors/wrangler.jsonc` `OWNER`; `owner/wrangler.jsonc`
  binds files, gateway, codemode and sandbox, none of which binds `OwnerDO`). Codemode's
  scripts reach the doors through the owner itself; the gateway reads the owner's `metering`
  (held already, the shape `metering` of `handed.json`), which the doors relay from
  `egressReserve`'s answer.
- The owner deploys before the doors (`facts.ts` `WORKERS`), and a rollback takes the doors
  back first, so the one pairing is the doors' version before reading this tree's owner's
  answers. The readers of that version are its code (`doors/src`) and what it serves with it:
  the owner's app (`doors/app/*.js`, the doors' static assets). New doors never read an owner
  before them, so a field newly answered, or newly read, is never a pairing to hold.
- **Not that pairing:**
  - **The sandbox runner** reads the answers the doors relay to it whole (`fileChanges`,
    `fileList`, `fileRead`, `fileReadChunks`, `fileSyncUploadBegin`, `fileSyncCommit`,
    `runDecide`). Its bytes are the sandbox Worker's (`sandbox/src/runner-bytes.ts`),
    installed at a sandbox's create and kept by digest across deploys
    (`sandbox/src/control.ts`), so a live runner may be older than any serving Worker: a
    pairing of its own, by its protocol (`sandbox/runner/protocol.ts`), recorded below.
  - **The deploy kit** reads the operator doors' answers, which the doors pass on whole
    (`doors/src/operator.ts`), by casts at the kit's own tree (`deploy.ts` `as RestoreSource`).
  - **The doors' tests** read the owner at their own commit; no deploy pairs them.
  - **Outside callers** read the tools' answers: the tool contract.

### What the paired readers read, derived from the code

- **The doors' code**, by `tsc`: with every row's `answer` taken out, every place `doors/src`
  reads an answer's field fails to compile, so the errors are the reads. Rerun from
  `platform/` on a clean tree:
  `node -e 'const f="contract/doors.json",fs=require("fs"),t=JSON.parse(fs.readFileSync(f));for(const r of t.doors)delete r.answer;fs.writeFileSync(f,JSON.stringify(t))' && node contract/requests.ts && npx tsc --noEmit -p . | grep '^doors/src'; git checkout -- .`
  At this branch it names 96 errors in 14 files, 29 doors: `admissionSet` and `ownerState`
  `state`, `sessionOpen` `session` (`authorize.ts`); `tierRead` `tier`, `fileRead` `entry`
  and `inline`, `ownerExport` (`app.ts`); `ownerErase` `erasing`, `erasureOutside` `keys`,
  `routes` or null (`erase.ts`); `toolProfile` (`mcp.ts`); `definitionRead`
  (`definition.ts`); `wakeEvents` `events` (`events.ts`); `keyMint`, `grantRevoke`,
  `grantForget` (`api-keys.ts`); `keyList` and `grantRevoke` (`revoke.ts`); `inboundAccept`
  `state`, `error`, `inboundCreate`, `inboundSet`, `inboundRevoke` (`inbound.ts`);
  `connectionPrepare`, `flowFinish` (`connect.ts`); `fileUploadTarget` (`file-bytes.ts`);
  `importFileBegin`, `importCheck` `more`, `importDiscard` `namespace` (`import/index.ts`);
  `egressOpen`, `egressReserve`, `fileReadChunks` `chunks` (`egress.ts`); `restoreStep`
  `done`, `keys` (`operator.ts`). A read by an `in` check compiles on `object`, so the rerun
  misses it: the one such read, `runDecide` `hook` (`egress.ts`, `"hook" in answer`), is the
  30th, found by reading each `in` check of `doors/src` (#5843's second review).
- **The app**, by reading it (untyped JavaScript, so no compiler finds its reads): each
  `call("<route>")` in `doors/app/app.js` and `api.js` (`grep -n 'call("' doors/app/*.js`),
  each route's door from `doors/src/app.ts`'s routes (`door:`, the `tools/<name>` route, and
  the routes whose doors code hands an answer on whole), and each field the app reads of
  that answer. 24 doors more: `fileUsage` (through `account`) `entries`, `chunk_bytes`,
  `inline_bytes`; `noticeList`, `registerProposals`, `actionRequests`, `policyProposals`
  (Home's lists); `registerAgent` `display_name`, `actionApprove` `run` (a card's yes);
  `grantList`, `outsideAgents` `agents`, `owner_agents`, `apps`; `policyRead` `generation`,
  `policy`, `codemode`; `fileStat` `entry`, `fileWrite` `entry`, `fileList` `entries`;
  `agentSubmit`, `agentStatus`, `agentCancel` (the run's fields the Talk view shows);
  `inboxDigest` `mail`, `rooms`, `boardDigest` `rows`; `signinStart`, `signinList`;
  `runList` `runs`, `next`; `socketTicket` `ticket`; `connectionList` `connections`,
  `connectionCommit` `display_name`.
- **Read before by tests alone**, and no longer declared: `sandboxBilled`, `tierSet`,
  `dataKeyRewrap`, `gatewayReseal`, the restore doors but `restoreStep`, `erasureBegin`,
  `sessionClose`, `collectionGet`, `sandboxDays`, `starterReceipts`, `egressReceipts`, and the
  fields of declared answers no paired reader reads (`ownerState` but `state`, `policyRead`
  `notes`, `hooks`, `fileRead` `chunks`, `egressOpen` `connection`, `generation`, `runDecide`
  but `hook`, `registerAgent` `agent_id`).

### The vocabulary, and what changes, from git

- The answers are objects (one may be null: `erasureOutside`), some a union of arms
  (`EgressOpened | EgressRefusal`, `importFileBegin`, `restoreStep`, `inboundAccept`,
  `signinStart`, `agentStatus`), with literal values and nested shapes. Two field values are
  no JSON type: `fileRead` `inline` (bytes) and `egressOpen` `ended`, a `ReadableStream`.
- A list of `unknown` makes a stub's whole answer `never` (Cloudflare's `Rpc` types), which
  reads as anything: an answer's list is typed as values RPC carries.
- 31 commits touched `owner.ts` this month; nine changed a `DoorTypes` answer. Every one but
  two added a field or a door. The two that took one away replaced the door (#5821, #5832),
  each kept a deploy as a `dropped` row: the step a request field has, which no answer field
  had.

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

1. **The door table declares each answer the doors' version reads** (`doors.json` row
   `answer`): `required` and `optional` fields, each schema its JSON type alone
   (`fieldTypes`, the requests' vocabulary, now with `stream`, which no tool's field names,
   as `bytes`), `nullable` where the owner may answer null, and `unread`. Not `handed.json`,
   as the owed line had it: a door's answer is its row's, beside its request, and a shape
   there would be a second file naming the same door.
2. **`tsc` holds both sides at one commit from it.** `npm run generate` writes `DoorAnswers`
   (what the doors read: the fields, but `unread`) and `UnreadAnswers` (what the owner still
   answers); `DoorAnswer` is the row's, narrowed by `DoorTypes` `answer` only where the doors'
   code reads narrower than a JSON type (a literal, a union's arms, a list's items, an
   object's fields), and `tsc` refuses a narrowing of a field the row does not read, or one
   wider than its row's; `OwnerDO implements OwnerDoors` holds each handler to it, and
   `owner-do.ts` `UnreadAnswered` to its `unread` fields too.
3. **The hold reads it** (`deploy/handed.ts` `answerHold`): the rules above, and the doors
   whose version before declares no answer (`Hold.undeclared`), which the plan line names.
4. **Readers that are not that pairing read the owner's own types**: the owner's whole
   answers stay in the contract where a reader past the doors needs them (`EgressOpened`,
   `EgressReserved`, `EgressRefusal`, `OwnerStateAnswer`, the rotations' and the restore's,
   and `DecideAnswer`, the runner's), and the doors' tests read theirs by `TestAnswers`
   (`doors/test/answers.ts`), since no doors test may import `owner/src`.

## Review (2026-11-23), and what was decided

- **(a) The declared set was the old `DoorTypes` sets, which tests read too** (#5843's
  reviewer, BLOCK): the hold would have refused an owner that stopped answering a field only
  a test reads (`sandboxDays`, `starterReceipts`, `egressReceipts`, `sandboxBilled`
  `counted`, the operator doors), and said "which the doors' version before reads",
  falsely. Decided (the coordinator, option one): the hold models deploy pairings, and
  tests are not callers. The table declares only what the doors' code and their app read
  (derived above, rerunnable), the runner's and the kit's reads are their own pairings, and
  the tests read the owner's answer by their own types. The hold's words are then true.
- **`DoorTypes` restated JSON-typed fields beside their narrower parts** (`OwnerStateAnswer`,
  `RestoreSource`, `RestoreAnswer`, `EgressOpened`, `EgressReserved`, `EgressRefusal`):
  `DoorTypes` now holds only what the doors' code needs narrower, each derived the same way
  (the `tsc` errors with no narrowing); those interfaces are the owner's whole answers, for
  the readers past the doors, and none is in `DoorTypes`. `egressOpen`'s and
  `egressReserve`'s arms still name `reservation`, `metering` and the like: each is required
  in its arm, which the table's optional field is not.

## Problems found (untangle step 2), with the fix above each

- **Each answer the doors' version reads is data the hold reads (`answer`), one declaration
  for both Workers.**
  - `DoorTypes` `answer` typed the doors' reads for `tsc` alone; no serving commit's answer
    could be read, so a field the owner stopped answering passed the plan.
  - A doors version before with no declaration would have passed silently.
- **An answer field goes in steps (`unread`).**
  - No data could say that the doors read a field no more while the owner still answers it.
- **Each reader reads by the types of its own pairing.**
  - The doors' tests and the runner read the owner through the doors' types, so what only a
    test or the runner read would have been held as the doors' read.
- **Comments.** `owner.ts`'s header and `DoorTypes`, `doors.ts`'s header, `handed.ts`'s header
  and `requests.ts`'s named answers as types alone.

## Left, with reasons

- **The nested and literal parts of an answer** (`TierName`, `ok: true`, `entry`'s fields, a
  list's items) stay `DoorTypes`'s, held by `tsc` at a commit and by no plan: the requests'
  hold has the same depth (a field's JSON type), and a deeper vocabulary is a design of its
  own. Owed below, research first.
- **The app's reads are declared from reading it**: nothing at a commit holds `app.js` to the
  table, so a read added to the app without its row's field passes `tsc`. Owed below.
- **The runner's reads** are a pairing of their own, by its protocol across deploys. Owed below.
- **The kit's reads** are of the owner it runs against, at the tree it is.

## As built

- `contract/doors.json`: 54 rows' `answer` (30 for the doors' code, 24 for the app), each
  field at its top level, typed as each handler answers it.
- `contract/src/doors.ts`: `Answer`; the table refuses an answer that is not its fields (each
  typed, listed once, at least one), a `nullable` but `true`, and `unread` fields it answers
  not; `stream` joins the vocabulary, and no tool's field names it or `bytes` (`RPC_TYPES`).
- `contract/requests.ts`, `contract/src/requests.ts`: `DoorAnswers` and `UnreadAnswers` (a
  list of values RPC carries; an answer of optional fields alone also an `object`);
  `contract/src/owner-doors.ts`: `Parts` takes `answer` only where the row declares one, and
  `Stray` refuses a narrowing of a field the row does not read, or one wider than its row's.
- `contract/src/owner.ts`: `DoorAnswer` is the row's, each narrowed field `DoorTypes`'s
  (`Narrowed`); `OwnerAnswer` adds the `unread` fields; `DoorTypes` keeps only what the doors'
  code reads narrower; `InboundRoute`, which nothing reads once the table declares the inbound
  answers, is gone (#5843's second review); `DecideAnswer`, the runner's
  (`owner/src/actions.ts` answers it, `sandbox/runner/decide.ts` reads its keys).
  `cutover.json`: the `owner.ts` edit names `importDiscard` no more.
- `doors/src/inbound.ts` `delivered` reads the doors' answer type; `gateway/memory`'s owner
  stand-in answers `EgressOpened`; `doors/test/answers.ts` `TestAnswers`, which `ownDoor`
  answers by.
- `owner/src/owner-do.ts`: `UnreadAnswered`, each handler answering its `unread` fields.
- `deploy/handed.ts` `answerHold` and `Hold.undeclared`; `deploy.ts` `servedHold`'s line names
  the undeclared doors.
- Tests: `contract/guards/doors.test.ts` (the answer rules), `deploy/guards/handed.test.ts` (a
  field gone, then through `unread`; made optional; widened; `integer` under `number`; null;
  an undeclared door named; a dropped door), `deploy/guards/deploy.test.ts` (the plan stops on
  a field the doors serving read, and names an undeclared answer).

## Owed

- [ ] untangle/answer-readers: the app's reads held to its rows at a commit (typed `app.js`,
  or a guard that reads its calls), and the runner's reads of the answers the doors relay held
  across its own pairing (its protocol, `sandbox/runner/protocol.ts`); research first (Fact
  14).
- [ ] untangle/answer-depth: an answer field's nested fields and literal values held as data,
  or named as left for good; research first (Fact 14), with the shapes' types
  (untangle/handed-shape-types).
