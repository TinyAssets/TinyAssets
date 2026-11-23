# answer-readers (2026-11-23): the compiler finds what each reader reads of the owner's answers

`untangle/answer-readers`, owed by `door-answers.md` (#5843, its "Owed" and its two reviews).
#5843 made `contract/doors.json` declare each door's `answer` where the doors' version reads
it, and the deploy plan hold it (`deploy/handed.ts` `answerHold`). The declared set was found
once, by hand: a `tsc` rerun over `doors/src` with every `answer` taken out, one `in` check
read by eye (`egress.ts` `"hook" in answer`), and `app.js`/`api.js` read call by call. Owed:

1. nothing at a commit holds the app to its rows: a read of an undeclared field, or a row the
   app no longer reads, passes;
2. `tsc` does not see a read through `"field" in answer`, so the next one slips past the rerun;
3. the runner's reads (`DecideAnswer`, the sync doors' answers) across its own pairing;
4. `doors/test/answers.ts` `TestAnswers` copies the owner's answer types, and nothing checks it.

## Research (Fact 14), from the code and git, on main 1f502d423

### What each reader reads through, and why the rerun could not hold it

- **The doors' code** (`doors/src`) reads through `DoorAnswer<D>`, the row's fields
  (`contract/src/requests.ts` `DoorAnswers`) narrowed by `DoorTypes` `answer`. `tsc` refuses a
  read of a field the type lacks, so the one direction it cannot see is surplus: a declared
  field nothing reads compiles. And an `in` check compiles on any object, so a read of a field
  the row lacks slips too. The rerun's "a read" is "a place that fails to compile without the
  field": a property read, a destructuring, and an answer handed to a type of the code's own
  that names the field (`erase.ts` returns `{ erasing: true }`, `file-bytes.ts` hands
  `fileUploadTarget`'s answer to `putChunk`, `definition.ts` takes `definitionRead`'s as
  `ServedDefinition`, `revoke.ts` spreads `grantRevoke`'s into `ClientRevoked`).
- **The app** (`doors/app`, static assets the doors serve, `src/pages.ts` `STATIC`) is untyped
  JavaScript: `api.js` `call(path)` answers `any`, so no compiler sees a read. Its routes'
  answers are code in `src/app.ts` `routes()`: a door (`{ door }`), a tool's door
  (`tools/<name>`), or a sequence that answered a `Response` (`answer(200, value)`), which
  erases the value's type. `connected.js`, a classic script, reads `connections/oauth/finish`
  by a bare `fetch`.
- **The runner** reads the answers the doors relay whole (`sync.ts` `SyncClient.post` answers
  `Record<string, unknown>`, each field checked at run time; `decide.ts` reads `DecideAnswer`'s
  keys as `unknown`). Its bytes are the sandbox Worker's at its sandbox's create or adoption; a
  sandbox adopts the Worker's runner whenever nothing lives on it, so an older runner is met
  only under a live job, and a job on a runner speaking a protocol the Worker does not
  (`SPEAKS`) is ended at its window's end (`control.ts` `#speaking`, `runner-protocol-contract.md`).
  So the runners an owner of a tree can face are those speaking its sandbox Worker's `SPEAKS`,
  however many deploys old: a pairing by protocol, not by the doors' version before.
- **The doors' tests** read past `DoorAnswer` by `TestAnswers`, cast once (`asTested`). No
  doors test may import `owner/src` (`ci/kit/layers.ts`), so `tsc` holds nothing between them.
  Deriving it needs the owner's whole answer types in the contract, which is the nested and
  literal depth `untangle/answer-depth` owes.

### The tools a guard has, measured on this host

- `typescript`'s checker is the platform's (`ci/kit/*` already parse with it). One program over
  `doors/src` and the app's scripts (`allowJs`, `checkJs` off: a script's types are its JSDoc's
  and what is inferred, and nothing in it is reported) builds in 0.7 s and is walked in 2 s.
- A read's door is found from the declaration of the field the checker resolves: a field
  declared at the top of `DoorAnswers`/`UnreadAnswers`' entry, or of `DoorTypes`' `answer`
  (an arm by name, `EgressRefusal`, is each door's whose answer names it). Cloudflare's `Rpc`
  mapping of a stub's answer keeps each field's declaration, so `await stub.x()` resolves.
- A "handed" read is the expression's contextual type: an argument's parameter, a return's
  type, a spread's literal. Handed to an answer type itself, it reads nothing yet.

## What "held" means, per reader

**The declared set == what the paired readers read**, at every commit, both ways: every read
field declared, every declared field read. A read the guard cannot type is no read it can hold,
so such reads are refused outright: an `in` check on a bare `object` (an answer whose row
declares none), a cast of an answer to a type of the code's own (`as`, `as unknown as`,
`as any`, a JSDoc cast), an answer handed to `any` (a script's untyped parameter), a read of
an answer by a key no type names, and a read from `unknown` or `never` (a route whose value
is untyped, a script's call of a path it does not name).

## Design, from the research (decided before building)

One mechanism for every reader: **its compiler finds the reads, and one guard holds them to
the table.** No reader is read by hand, and none is a special case of the guard.

1. **`ci/kit/answer-reads.ts`**: the readers compiled together (`readerProgram`) and their
   reads of each answer's top-level fields (`answerReads`): by name, by `in`, by a
   destructuring, and handed to a type of the code's own that names the field; each read of a
   field no answer arm has (`undeclared`), and each read no type holds (`unheld`). And
   `unanswered`: each entry of a reader's own answer types the owner's object does not answer.
2. **The app is typed by the routes it calls.** A route's sequence answers its value (or
   `Headed`, a value with headers, or a `Response` of its own), and the surface answers the
   value as JSON (`src/app.ts`); `AppAnswer<P>` is a route's door's answer, a tool's door's
   (`ToolDoors`, generated from the table), or its sequence's value. `api.js` `call` is typed
   by it in JSDoc, so the app's reads are its compiler's. The app's pages may import that type
   (`ci/kit/layers.ts`), and the layer scanner reads a script's JSDoc imports, which the
   pre-processor does not (`ci/kit/imports.ts`).
3. **`doors/guards/answer-reads.test.ts`**: the doors' code and the app's served scripts hold
   exactly the table's set; `TestAnswers` is what `OwnerDO` answers; a probe shows each kind of
   read found, and each kind refused.
4. **The runner** is the same mechanism over `sandbox/runner`, but its hold is by protocol:
   owed below, its own lane.

Checked against the intent: the declared set is what production readers read (the doors'
code and every served script), derived by their compiler at each commit; the deploy hold reads
the same set; a reader with no type is refused rather than passed.

## Problems found (untangle step 2), with the fix above each

- **The readers' compiler finds the read set, and the guard holds it to the table.**
  - The declared set was found once by hand and held by nothing at a commit.
  - An `in` check on an answer compiled whatever field it named.
  - A cast of an answer, or an `in` on an undeclared one, read what no type held.
  - Review: an answer handed to `any` (an untyped helper in a script), cast to `any`, read by a
    computed key, or a read from `unknown` (`sourceList` answered `unknown`, a path no call
    names) read what no type held, and passed.
- **The app is typed by the routes it calls.**
  - `call` answered `any`, so the app's reads were a list kept by reading.
  - A route's value was erased into a `Response` (`answer(200, ...)` in every sequence, and
    `valued`, a wrapper that only did that).
  - `KINDS`' answers, the talk's run and the sign-in view were untyped state.
  - `connected.js` read its answer by a bare `fetch`.
  - A script's JSDoc import of a type was an import no layer check saw.
- **The table says what the app reads.** `agentSubmit` and `agentCancel` `result` were
  declared, and the app reads neither (only `agentStatus`'s, when a run ends): both `unread`,
  the step #5843 gave a field taken away.
- **`TestAnswers` is held to the owner's answers.**
  - A copy of the owner's types that nothing checked.

## Left, with reasons

- **The runner's reads.** Owed below: its reads typed by the table's answers (the same guard
  over `sandbox/runner`), recorded per protocol (`protocol.json`, frozen by digest), and the
  tree's owner held to every protocol its sandbox Worker speaks. The rows' readers then need a
  dimension of their own (which reader reads which field), since the deploy hold pairs the
  doors' version before alone. A design of its own.
- **`TestAnswers` is held, not derived**: deriving it needs the owner's whole answer types in
  the contract (`untangle/answer-depth`, owed by `door-answers.md`).
- **A raw `fetch` of the owner surface** outside `api.js` and `connected.js` would be untyped:
  nothing in the app does one, and every served script is in the guard's program.
- **What the compiler cannot see, and what holds it.** `tsc` holds the doors' code to the
  types, so only the app's scripts (no `checkJs`) can read what no type has; every such read
  goes through `any`, `unknown`, a cast or a computed key, each refused. Left seen by no one:
  enumeration and reflection (`Object.keys`, `for in`, `Reflect.get`, `Object.hasOwn`), and a
  generic helper's read (`get(answer, "field")`), which reads no field by its type. A declared
  field read only so is reported as not read, which fails loudly.
- **`fetch` as a tool's name**: the banned names are the runtime's, and a type's own
  declaration runs nothing, so the scanner (`ci/kit/imports.ts` `isolationProblems`) reads no
  banned name in an interface or a type alias; `ToolDoors` names the tool `fetch` as data, in
  every closure that reaches `requests.ts` (the contract's, the importer's), with no home.

## As built

- `ci/kit/answer-reads.ts`: `readerProgram`, `answerReads`, `unanswered`.
- `doors/guards/answer-reads.test.ts`: the three tests above, the readers and the probe
  compiled once.
- `contract/requests.ts`: `ToolDoors`; `contract/doors.json`: `agentSubmit`, `agentCancel`
  `unread: ["result"]`; `ci/kit/imports.ts`: no banned name in a type's declaration.
- `doors/src/app.ts`: `Route` answers a value, `Headed` or a `Response`; `valued` is gone;
  `AppPath`, `AppAnswer`; `doors/src/connect.ts`: the connect routes answer values;
  `doors/src/kit.ts`: `Headed`; `doors/src/pages.ts`: `STATIC`, `STATIC_PREFIX` exported.
- `doors/app/api.js` `call`, `app.js` (`answering`, `Run`, `Talk`, the sign-in view),
  `connected.js`: typed in JSDoc.
- `ci/kit/imports.ts`: a script's JSDoc import types; `ci/kit/layers.ts`: the app's pages may
  import `doors/src/app.ts`.

## Owed

- [ ] untangle/runner-answers: the runner's reads of the answers the doors relay (`sync.ts`,
  `decide.ts`) typed by the table's answers and found by `ci/kit/answer-reads.ts`, recorded
  per protocol (`sandbox/runner/protocol.json`), and the tree's owner held to every protocol
  its sandbox Worker speaks (`SPEAKS`); research first (Fact 14).
