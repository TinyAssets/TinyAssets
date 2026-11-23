# binding-compat as built (2026-11-23)

`process/binding-compat` takes two things. The contract step `owner-control-signins.md` owed:
`HANDED_BEFORE` goes, the stage's doors serving eab88202b (#5807) at 100% (stage routes run
45103565688). And the process fix behind it: #5805 and #5807 each changed what the doors hand
the owner and made the owner refuse what the doors' version before hands, in the same change,
and nothing caught it but a reviewer's chance read.

## Research first (Fact 14), read on main eab88202b

- **The deploy order.** `deploy/facts.ts` `WORKERS`, each Worker after the ones it binds:
  codemode, files, gateway, sandbox, owner, doors. So a callee is always new while its caller
  is the version before: from the owner's 100% until the doors' (their 0% soak and smoke, or a
  run stopped between), the doors' version before calls the new owner, and `rollback` takes the
  doors back first. Overrides reach no RPC (`platform.json` `stages`), so the 0% smoke reaches
  only the doors' new version, and nothing else sees the owner refuse the version before.
- **What refuses an unknown or missing key at a Worker that deploys before its caller.**
  - The owner, from the doors: every binding kind (`binding.ts`, 13 `keyed` lists: the kinds,
    and the origin, source, vendor, sandbox tokens, inbound route and import bundle inside
    them), every door's request (`doors.json` `args`, held by `fields` in the owner's runner),
    and a door that is gone (the stub's call fails). Both are data or near it: the door table
    is data, the binding's lists were literals in the parsers.
  - The owner, from the doors, inside loose doors' modules: `egress.ts` (`egressRequest`,
    `reserveRequest`, `settleRequest`, `spendOutcome`), `connections.ts` (four `fields` calls),
    `reports.ts` (run frames, the sign-in report), `sandbox-time.ts` (`billedEvent`).
  - The gateway, from the owner and the doors: `doorbellInput`, `ringRequest` (`gateway.ts`),
    `authorizationGrant` (`oauth.ts`), and `metering`, the owner's reserve answer it parses.
  - The other way, a callee's answer parsed by its caller, which is the version before while
    the callee is new: the owner's `started`, `networkOutcome` (sandbox), `meterRead`, the
    connection's `#keepResealed` (gateway), the doors' refusal facts (`refused.ts`).
  - Not RPC: the directory's and the control record's KV values, R2 roots, the runner's
    documents. Those are `forward`'s (a stored shape) and the runner protocol's.
- **How the repo already pins a shape across releases.**
  - The runner protocol (`sandbox/runner/protocol.json`): each version recorded whole, frozen
    by digest; the Worker speaks its runner's and the ones either side, and a change ships in
    two deploys. It is negotiated at run time: the runner reports its protocol.
  - The export's shape: a digest pinned beside the schema it is of (`migrations.test.ts`).
  - The migration locks, and `platform.json` `forward`: append-only, held against the base
    branch (`forward.test.ts`, `ci/history.ts` `baseOf`, `fileAt`), its count riding each
    version's tag so a rollback stops where it cannot follow.
  - The recorders (`ci/record.ts`) run one fixed script on base and head, each on its own
    tree: they show an answer changed, never the head owner refusing the base doors' calls.
- **What "the previous release" is.** In CI, only the base branch (`baseOf`: the PR's base,
  the merge group's, a push's previous `main`). The stage serves whatever `main` commit was last
  deployed, by hand, often many merges back. So base-against-head is not transitive: an expand
  step (A: the doors stop handing a field, the owner still takes it) and its contract step
  (B: the owner refuses it) each pass against their own base, but one deploy carrying both
  breaks. The deploy kit is the one place that knows the serving version: `rollout` reads each
  Worker's version before and its tag, the commit's first 12 digits. The plan job checks out
  one commit (no history), so reading that commit's tables needs the history fetched.

## The design

- **What the doors hand the owner is data**, at every release, so a guard reads the base's
  copy with `fileAt` and nothing runs old code: the door table already was (`doors.json`
  `args`), and the binding's shapes become `contract/bindings.json`, which the parsers read
  (`fields(value, kind, shape)`); no list is spelled in the parser. Types stay as they were:
  a JSON module's strings widen, so `Pick<Binding, ...>` cannot come from it; the binding test
  holds each shape to exactly the fields its parser takes from what the binder builds.
- **What the doors stop handing is dropped first**, one rule at three levels: a binding
  shape's `dropped` fields and a request's `dropped` fields, taken and left out by the owner
  (`fields`, `requestFields`), listed by no tool and no request type; and a door marked
  `dropped: true`, which the owner still takes and the doors' `OwnerDoors` lacks, so `tsc`
  refuses a doors call of it, while its `DoorTypes` entry stays for the owner's handler (the
  generated `Dropped`). Each goes once the doors the stage serves hand it no more.
  `HANDED_BEFORE` was the first instance, as a special case in code.
- **The guard** (`deploy/guards/handed.test.ts`, beside `forward.test.ts`): against the base
  branch, the head owner takes every door the base's table has (one the base marks dropped may
  go); every field a base shape or request lists, required or optional; no request where the
  base's door takes none; an omitted request and an `undefined` field where the base's took
  them; and requires no field the base's doors do not always hand. A request split by who asks
  is held side by side. It states its premise: the owner deploys before the doors (`WORKERS`).
  A loose door's request is its module's, unheld here (owed below). A base before
  `bindings.json` holds no shapes, so this PR's base holds the door table alone; a base whose
  parsers import a shapes file elsewhere fails it.
- **The cutover's exception, the one** (review): the doors and shapes the base's manifest names
  in its `cutover` phase's edits of `doors.json` and `bindings.json` (the eight import rows,
  `import` and `import_bundle`) may go at once. Its deletion lands in the window's last step,
  after the importer, their one caller, has closed (tasks.md §9 step 15), and in two or three
  PRs, so no "head without a manifest" test would do. Nothing else they name loosens; the
  manifest's hold phase takes the exception out.
- **Why not a recorder replaying the base's calls.** The recorders are CI's, heavy, and run
  each tree on its own; a replay needs the base's binder run with credentials. The base's data,
  read by `fileAt`, is the base's sent shape exactly, since the binder builds every binding
  through its parser.

## The problems, with their fixes

- **The binding's fields are data the guard reads at any release** (`bindings.json`,
  `bindingShapes`, `fields(value, kind, name)`).
  - 13 field lists spelled as literals inside `binding.ts`'s parsers, readable at an earlier
    commit only by running that commit's code.
  - `HANDED_BEFORE`: the expand step as a constant in code, spread into two parsers, its
    removal a hand-tracked `live:` wait.
- **One rule for anything the doors stop handing** (`dropped`, in both tables).
  - A door's request field or a door could not be stopped and still taken: the doors and the
    owner read the same row, so a removal was one step, refused mid-deploy.
- **The owner's fields step is the contract's** (`requestFields`): the omitted request, the
  undefined fields and the dropped ones in one place, beside `fields`.
- **The guard holds the change to the base's doors**, which nothing did: #5805 and #5807 each
  passed every check.
- **The contract step:** `HANDED_BEFORE` goes; the account and inbound parsers refuse `tiers`
  and `signins` `invalid` again (`binding.test.ts` malformed cases).
- **The cutover's manifest** names `bindings.json`'s import shapes and the binding test's
  `"import"`, which go with the import kind.

## Behaviour changed on purpose

- An account or inbound binding carrying `tiers` or `signins` is refused `invalid`, as before
  #5807's review. No doors the stage serves hand either.

## Left, each with its reason

- **The guard is base against head; the stage is the serving commit.** A contract step (a
  `dropped` entry taken out) passes against any base, so its wait for the stage stays the
  change's own, as this PR's was: its decision log names the serving commit. The kit's plan
  holding the run against the serving versions is owed below.
- **A whole binding shape gone** has no dropped step: a kind is handed by its doors, and the
  shape goes when they do. The one planned is the cutover's import kind, with its rows, which
  the cutover's exception above lets go.
- **A field's schema** (its type, its grammar) is not held: a door's handler and a binding's
  grammar check values, and the guard holds names. `bindingKind`'s markers are not held either.
- **A request field an MCP client cached from an older `tools/list`** may come long after a
  deploy: that is the outside clients' (the tool table's), not the deploy order's.

## Owed

- [x] The kit's plan holds the run against the serving versions: for each Worker, the serving
  version of each Worker that binds it (`rollout`'s `before` tag) read from git (the plan and
  apply jobs fetching full history), and the run's owner held to that commit's `bindings.json`
  and `doors.json` with `handedProblems` moved where both read it; then a contract step's wait
  is the kit's, not a log's (`process/handed-deploy`; done, `handed-deploy.md`).
- [x] Every other strict cross-Worker parser in data under the same rule: the loose doors'
  modules (`egress.ts`, `connections.ts`, `reports.ts`, `sandbox-time.ts`), the gateway's
  inputs (`doorbellInput`, `ringRequest`, `authorizationGrant`, `metering`), and the answers a
  caller parses (`started`, `networkOutcome`, `meterRead`, `#keepResealed`, the doors' refusal
  facts), whose rule runs the other way (`process/handed-shapes`; done in `handed-deploy.md`,
  where `reports.ts` is the runner protocol's).
