# runner-answers (2026-11-23): the owner answers every runner protocol still spoken

`untangle/runner-answers`, owed by `answer-readers.md` (#5847, "Owed"): the runner's reads of
the answers the doors relay (`sync.ts`, `decide.ts`) typed by the table's answers and found by
`ci/kit/answer-reads.ts`, recorded per protocol (`sandbox/runner/protocol.json`), and the
tree's owner held to every protocol its sandbox Worker speaks (`SPEAKS`).

## Research (Fact 14), from the code and git, on main acb256306

### How the runner pairs with the owner

- **The runner reads the owner through the doors' relay, not through the doors' types.** The
  sync agent's doors (`doors/src/egress.ts` `serveSync`) hand each owner answer on whole, as
  JSON: `fileChanges`, `fileList`, `fileSyncCommit` and the three upload doors as they came;
  `fileRead` spread with its `inline` bytes as base64; `fileReadChunks` as `{ chunks }`, each
  base64. The decide door (`serveDecide`) hands `runDecide`'s answer whole, but its `hook` arm
  (an `http` hook's own words, which the runner never asks for). Nothing else the runner posts
  is read past its status (`frames.ts`, `postSignins`).
- **What it reads** (each a top-level field, as `sync.ts` and `decide.ts` read them):
  `fileChanges` `changes`, `reset`, `cursor`; `fileList` `entries`, `next`; `fileRead`
  `entry`, `inline`, `chunks`; `fileReadChunks` `chunks`; `fileSyncCommit` `outcomes`;
  `fileSyncUploadBegin` `intent_id`, `missing`; `runDecide` `tools`, `generation`, `allow`.
  Each read as `Record<string, unknown>` (`SyncClient.post`) or `unknown`
  (`decide.ts` `Answer`, keyed by `DecideAnswer`), each cast where it is used, so no type
  holds the owner to any of it, and the table declares only `fileList` `entries`, `fileRead`
  `entry` and `inline`, `fileReadChunks` `chunks` and `runDecide` `hook`: what the doors' code
  and app read.
- **Every runner of protocol 5 reads that set.** Protocol 5 came with #5794 (99d42771a);
  since, `sync.ts` and `decide.ts` changed in #5795, #5798, #5801, #5803 and #5843, and none
  changed a top-level read (`git log -p 99d42771a^..acb256306 -- platform/sandbox/runner/
  {sync,decide}.ts`, the reads by `grep -oE '(read|got|page|begun|answer)\["[a-z_]+"\]'`).
  Nested fields (an entry's `version`, an outcome's `current`) are `untangle/answer-depth`'s.

### What a live sandbox's runner can be at a deploy

- A runner's bytes are the sandbox Worker's at its sandbox's create or adoption, kept by
  digest across deploys (`sandbox/src/runner-bytes.ts`, `control.ts`); the Worker adopts its
  own whenever nothing lives on the sandbox's, and keeps one a run lives on only if it speaks
  that runner's protocol (`#speaking`, `runner-protocol-contract.md`). A runner's code changes
  with no protocol bump (every edit of `sync.ts`), so a protocol is a family of digests, and
  only what every one of them reads is what "protocol 5's runner reads".
- So at a deploy the runners that talk to the tree's owner speak a protocol the serving
  sandbox Worker speaks (the live runs it launched), or the tree's (the ones it will). The
  deploy order (`facts.ts` `WORKERS`: the owner binds `SANDBOX`, so the sandbox deploys first)
  does not change that: a runner lives in its VM whatever serves. On a rollback the serving
  sandbox is the newer one, whose protocol the older tree may not record at all.
- One a contract step stops speaking (`SPEAKS` `[4, 5]` to `[5]`) may still finish a run that
  outlived two deploys, to its window's end (`foreign-runner-reap.md`): the serving Worker's
  `SPEAKS` names it, so holding the serving sandbox's protocols holds it too.

### What the deploy hold and the kit already give

- `deploy/handed.ts` `answerHold` holds the tree's rows to what a reader's rows read (each
  field still answered, always where read so, as types it reads, null only where read null);
  `handedHold(then, now)` reads each Worker's serving commit by `sideAt` (`then("sandbox")`
  among them), and the CI guard runs it against the base branch.
- `ci/kit/answer-reads.ts` finds a reader's reads where the answer's type is the table's
  (`DoorAnswers`, `UnreadAnswers`, a homomorphic mapping of them keeping each field's
  declaration), and refuses a read no type holds.
- `SPEAKS` is a constant in `protocol.ts`: no serving commit's can be read as data. The
  records (`protocol.json`) are frozen by digest (`state-format.test.ts` `FROZEN`), and name
  no read of the owner's.
- The table's `unread`: fields the owner answers that the doors' code and app read not, which
  `UnreadAnswers` types, `owner-do.ts` `UnreadAnswered` holds each handler to answer, the
  doors' guard leaves out of the doors' set, and the hold leaves to the version before.

## Design, from the research (decided before building)

One mechanism: **each reader's reads are a row-shaped record, and the hold holds the tree's
rows to every record a reader still reads by** (the doors' version before's rows, each
spoken protocol's record).

1. **The table declares every answer field a paired reader reads.** A field only the runner
   reads is in its row, marked `unread` (the doors read it not; the owner answers it): so the
   owner is typed and held to it (`UnreadAnswers`, `UnreadAnswered`), the doors' set and their
   hold are unchanged, and no second vocabulary is made. `runDecide`'s `tools`, `generation`
   and `allow` are its row's, so `DecideAnswer` is the owner's own type (`owner/src/actions.ts`).
2. **The relay is one map and one type.** `contract/src/egress.ts` `SYNC_DOORS` maps each sync
   door to the owner door it relays, which `serveSync` calls by it; `RelayedAnswer<D>` is the
   owner's answer as JSON carries it (bytes as base64), which the runner's `SyncClient.post`
   and `decide.ts` read by. The kit finds those reads, as it finds the doors'.
3. **Each protocol's record has its reads** (`protocol.json` `relayed`: by door, the fields
   its runner reads, each as its row typed it when recorded, required where read so). The
   guard (`sandbox/guards/answer-reads.test.ts`) holds the runner's reads, found by the kit,
   to its protocol's record: each read field recorded, as the row types it. The record may
   name more (what an earlier digest of the protocol read): it is frozen, so a new read is a
   new protocol. Protocol 5's record gains `relayed` once, here, its pin with it: the research
   above shows every runner of protocol 5 read exactly that set, so the record says what its
   bytes always did, and the Worker↔runner wire is unchanged.
4. **`SPEAKS` is data** (`sandbox/runner/speaks.json`), so the hold reads it at a serving
   commit as it reads the doors' table.
5. **The hold holds the tree's rows to every spoken protocol's record**: each protocol the
   serving sandbox speaks (its commit's records) and each the tree's sandbox speaks, by
   `answerHold`, its words naming the reader. A release that records none of what it speaks
   (no `speaks.json`, or a spoken protocol with no `relayed`: every commit before this one)
   is held by nothing, and the plan names it, as an undeclared door is.

Checked against the intent: the runner's reads are found by the kit from its source at each
commit, recorded once per protocol as a frozen fact of its bytes, and the owner at a deploy
answers every runner that can still be talking to it, the newer ones on a rollback too.

## Problems found (untangle step 2), with the fix above each

- **The table declares every field a paired reader reads; the doors' set is the fields they
  read (`unread` for the rest).**
  - `SyncClient.post` answered `Record<string, unknown>` and `decide.ts` read `unknown`, each
    field cast where used: no type held the owner to `fileList` `next`, `fileRead` `chunks`,
    `fileChanges`, `fileSyncCommit`, `fileSyncUploadBegin` or `runDecide`'s yes, so an owner
    that stopped answering one passed `tsc` and every hold.
  - `DecideAnswer` in the contract was a second list of `runDecide`'s fields, apart from its
    row, which the runner keyed its reads by.
- **The relay is one map and one type.**
  - `serveSync`'s switch named each sync door's owner door, and the runner assumed the same
    pairing with nothing to show it.
  - `SyncClient.post` turned a `404` of `read` alone into `{ missing: true }`: an `if` for one
    door, and an answer no owner gives in every door's answer. `fetchInto` reads the
    refusal's status instead, as any caller of a `SyncRefused` does.
- **A protocol's record says what its runner reads; `SPEAKS` is data; the hold holds both
  sides' spoken protocols.**
  - Nothing recorded what a runner of a protocol reads of the owner's, so no deploy could hold
    the owner to a runner older (or, on a rollback, newer) than its tree.
  - `SPEAKS` was a constant, so no serving commit's could be read.
  - The hold's words named the doors' version before alone (`UNREAD_STEP`).
- **Comments.** `handed.ts`'s header ("the runner's ... reads are not this pairing, and no row
  holds them"), `doors.ts`'s header and `Answer`, `owner.ts`'s header (`DecideAnswer`),
  `OwnerAnswer` and `owner-do.ts` `Unanswered` named `unread` as the doors' alone.

## Left, with reasons

- **Nested fields** (an entry's `version`, an outcome's `current`, the runner's casts of them):
  the table's depth is a field's JSON type, for every reader; `untangle/answer-depth` owes it
  (`door-answers.md`).
- **A record names what a runner of its protocol read, and may name more than today's
  runner reads**: a frozen record cannot shrink when a later runner of its protocol reads
  less, and the owner must still answer the earlier ones. A new read is a new protocol.
- **`fileReadChunks`'s relay builds `{ chunks }`** rather than spreading the answer: it hands
  every field the row declares, so the runner reads it by `RelayedAnswer` alike.
- **Protocol 5's pin moved once** (`state-format.test.ts` `FROZEN`), to found `relayed`: the
  research shows every runner of protocol 5 read exactly the recorded set, and nothing the
  Worker or a runner says to the other changed. No record before 5 has `relayed`: none is
  spoken.
- **The first plan after this names the sandbox's version before as recorded by none** (its
  commit has no `speaks.json`); main's sandbox speaks 5, whose record this tree holds.
- **Recorders**: none moves. No answer, door, method or route changed: the rows' new fields are
  types and hold data, and the doors relay the same calls.

## As built

- `contract/doors.json`: `fileChanges` (`changes`, `cursor`, `reset`), `fileList` `next`,
  `fileRead` `chunks`, `fileSyncCommit` `outcomes`, `fileSyncUploadBegin` (`intent_id`,
  `missing`) and `runDecide` (`tools`, `generation`, `allow`), each `unread`; `npm run
  generate` writes them into `UnreadAnswers`.
- `contract/src/egress.ts`: `SYNC_DOORS`, each sync door's owner door; `contract/src/owner.ts`:
  `RelayedAnswer`; `DecideAnswer` moved to `owner/src/actions.ts`, the one module that
  answers it.
- `doors/src/egress.ts` `serveSync`: each door's owner door by `SYNC_DOORS`.
- `sandbox/runner/sync.ts` (`SyncAnswer`, `SyncClient.post`, `fetchInto`'s `404`) and
  `decide.ts`: read by `RelayedAnswer`.
- `sandbox/runner/protocol.json` 5 `relayed`, `state-format.test.ts` its pin, `test/state-shape.ts`
  `Protocol.relayed`; `sandbox/runner/speaks.json`, which `protocol.ts` `SPEAKS` reads.
- `sandbox/guards/answer-reads.test.ts`: the runner's reads, found by the kit, each in its
  protocol's record as the table types it.
- `deploy/handed.ts`: `Side.runners`, read by `sideAt`; `runnerProblems` over the serving
  sandbox's protocols and the tree's, by `answerHold` with its `Reader`; `Hold.unrecorded`;
  `deploy.ts` `servedHold`'s line names them. `ci/kit/layers.ts`: the deploy kit reads the
  runner's two files.
- Tests: `deploy/guards/handed.test.ts` (a field gone, made optional, widened; a door gone; a
  serving protocol the tree records nowhere; one both speak held once; the unrecorded named;
  `sideAt`), `deploy/guards/deploy.test.ts` (the plan names an unrecorded sandbox, and stops on
  a serving protocol's read).
