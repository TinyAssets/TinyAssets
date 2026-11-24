# door-types-owner (2026-11-23): the owner's fields step holds each field to its type

`untangle/door-types-owner`, owed by `handed-types.md` (#5830, "Review" and "Owed"). The door
table (`contract/doors.json`) declared each request field's JSON type, and the deploy plan's
hold read those types across serving versions, but the owner did not enforce them: its fields
step (`requestFields`) held a request's names to the table, and each module checked types again
in its own code, so a type the table said was true only while someone kept that code in step by
hand (#5822's owner took integer cursors in code alone while the table said string), and 149
fields were `{}`, typed on neither side (148 at #5830; 150 at 1ae659350, two doors since; 149
once #5835 dropped `keyMint`'s `proposal`).

## Research (Fact 14), from the code and git

### The fields step, and what each module checks

- `contract/src/doors.ts` `requestFields` (the owner's one fields step, `owner-do.ts` `#fields`)
  holds the names: exactly the required fields, any of the optional ones, none `undefined`
  unless `undefinedAllowed`, the dropped ones taken and left out. It reads no type.
- Every handler reads `r: Record<string, unknown>` (`owner/src/door.ts` `Checked`), so each
  module checked each value's type again before use, in its own words: `printable`, `name`,
  `storedPath`, `agentId`, `whole`, `count`, `bodyOffset`, `modeOf`, `expectedEtag`, `flag`,
  `follows`, `scopes`, `outsideClient`, the import's `invalid(...)` and many inline `typeof`
  checks. Most fold the type into a check that goes beyond it (a format, a range, a length), and
  answer `invalid` with their own words.
- **Where a module checked only a type**: `files.ts` `flag` (`recursive`, `replace`),
  `runs.ts` `agentRun` (`at` a finite number, `cron` a string), `actions.ts` `runDecide`
  (`tool_name` a string), `ledger.ts` `wakeSubscribe` (`name` a string), `setWakeLimits`,
  `disable` and `remove` (`agent_id` a string), `signins.ts` `signinStart` (`engine` a string).
- **Where a module folded a type into a lookup**, so a mistyped value was answered as an absent
  one, by another code: the card doors (`policyApply`, `policyDecline`, `actionApprove`,
  `actionRefuse`, `registerAgent` as the owner, `registerDecline`: `refused`, "... is not the
  pending card shown"), `policyProposal` (dropped door: `refused`), `signinCode` and
  `signinCancel` `id` (`refused`, no such sign-in), `socketTicket` (`ticket_refused`),
  `agentStatus` and `agentCancel` (`no_run`, `no_schedule`), the owner's own
  `fileSyncCommit` `lease_id` and `epoch` (`sync_fenced`), and the collection cursors (`cursor_invalid`, which #5829 named for
  "a cursor no answer gave", a string or not).
- **The 149 untyped fields' types**, each from its module's check (and the doors' TypeScript
  where they build it): 88 `string`, 30 `integer`, 17 `array`, 4 `object`, 2 `boolean`, and
  `["string", "null"]` (`ownerExport` `cursor`, `fileUploadCommit` `expected_etag`, the outside
  `registerAgent` `recipe_id`), `["integer", "null"]` (`max_wakes_per_hour`,
  `fileUploadCommit` `mode`), `["number", "null"]` (`wakeSubscribe` `expires_at`), every JSON
  type (`runDecide` `tool_input`: what the CLI's hook handed), and `inboundAccept` `body`, a
  `Uint8Array` the doors hand over RPC, which no JSON type names.

### Who calls the doors, and what they send

- **The doors' own requests** were typed by hand beside the table: `contract/src/requests.ts`
  (written from the table) gave each field `unknown`, and `contract/src/owner.ts` `DoorTypes`
  `values` restated most fields' types, a second copy the hold never read. Generated with the
  table's types instead, `tsc` over the unchanged doors code (1ae659350's, the stage's after the
  current deploy) refused two requests only: the app's `files/write` handing `fileWrite` an
  `expected_etag` of `string | null` and its text as bytes (the table: `string`, `string`), and
  `mintKey` handing `keyMint` the app body's `scopes` unchecked.
- **The owner as itself writes with no etag, and writes bytes.** `files.ts` `heldToRead` and
  `UNREAD_WRITE` held an outside client or a run to a string etag on `fileWrite`, `fileRemove`,
  `fileRename` and `fileRestore`, and let the owner pass null: a rule the table could not say,
  since the tool's schema is what outside callers see. 90 of the owner tests' 125 `fileWrite`
  calls write as the owner with a null etag, 23 with bytes; the app's `files/write` sends
  bytes.
- **Bodies the doors hand on as they came** (`handed`): the app (`doors/app/app.js`, served
  with the doors) sends each field as the table types it (cards and digests from the answers
  it read, `expected_generation` a number, `max_wakes_per_hour` a number or null, `scopes: []`,
  `before` a number); the sandbox runner (`sandbox/runner/sync.ts`, `decide.ts`, frames and
  sign-in reports) sends numbers for epochs, cursors, sizes and counts, strings for ids and
  paths, lists for chunks, ops, frames and reports, `{ exit_code }` for an end, `true` for a
  check, and the CLI's `tool_name` and `tool_input` as it handed them; the operator's restore and
  erasure hand on the deploy kit's arguments cast (`doors/src/operator.ts`), which the kit
  builds of those types (`deploy/deploy.ts`), and the cutover's import builds typed requests
  (`tsc` held them, above).
- **Callers held to no type before**: `/api`, the app's `tools/<name>` route and code mode hand
  tool arguments as they came (`cursor-numbers.md`); `/mcp` holds them to the tool's schema (the
  MCP SDK, `doors/src/mcp.ts` `fromJsonSchema`), so an MCP caller already got invalid params
  for a mistyped value and sees no change here.
- **Uncalled doors**: `disableAgent`, `deleteAgent`, `mailChain`, `grantForgetRevoked`,
  `fileRedact` and `fileHistoryPurge` have no caller in the doors, the app or the operator
  (tests alone); `policyProposal` is a dropped door.

### How a refused value is answered

A refusal's code gives its status and, on `/mcp`, its "call again" line (`refusal.ts`
`callAgain`: `invalid` is "malformed, refused again until it is corrected"); the JSON surfaces
show its status and message, the egress door and the inbound hooks a named code. `REFUSALS`
says what `invalid` is for: "a malformed one (a wrong field, type or size)". The fields step's
name refusal is `invalid` with the row's words (`args.refused`).

## Design, from the research (decided before building)

1. **The fields step refuses a value of a type its schema does not name** (`requestFields`):
   each field's value is of one of its schema's types or its `droppedTypes` (`integer` under
   `number`, `takesType`, now the one rule the hold and the step share), else `invalid` with
   the row's words and the types it names (`invalid file request: expected_etag is a string`).
   An `undefined` field where the row takes one stays its module's. One place, from the table.
2. **Every field names its type.** The table refuses a field schema with none, so the 149 `{}`
   fields are typed (above). The vocabulary gains `bytes` (a `Uint8Array`, which only RPC
   carries), and the table refuses it on a tool's served field, since `/mcp` serves JSON
   Schema.
3. **A door whose owner takes more than its tool says takes its fields by who asks**: the
   owner itself (no outside client, no run) or anyone else, as `registerAgent` already did.
   `fileWrite`, `fileRemove`, `fileRename` and `fileRestore`: the owner's `expected_etag` is a
   string or null, its `fileWrite` content a string or bytes; the outside fields, the tool's
   schema, unchanged. The owner's runner reads the owner's fields only for the owner itself
   (`account.origin === null && account.run === null`), so `heldToRead` and `UNREAD_WRITE`,
   the same rule in code, go; `registerAgent` takes a run's call as outside too (a run holds
   no owner session, which its door asks first). Rejected: widening the tools' schemas to null (outside callers
   told null is taken, then refused) and rewriting 113 test writes to strings with etags.
4. **The doors' requests are typed from the table** (`contract/requests.ts` writes each field
   its schema's TypeScript type), so `tsc` holds every request the tree's doors build to the
   types the owner enforces. `DoorTypes` `values` keep only what is narrower than a JSON type
   (a list's items, an object's keys, `0 | 1`); the restating ones, and the restore and
   erasure request interfaces, are the table's now. The handlers whose own request types no
   longer fit take the table's (`grantSet`, `registerAgent`, `runDecide`; `PolicyWriteRequest`'s
   `policy` is the object it is handed, which `validate` makes a document).
5. **The module checks that only restate a type go** (list above), reading the typed request
   where the handler had to cast; checks beyond a type stay.

## The contract change outside callers see

Only a value of a type its field does not name is answered differently; every other refusal,
and every code of one, is as before.

- **Codes.** A mistyped value is `invalid` (400, "malformed") at every door. Where a module
  folded the type into a lookup it was another code: the six card doors' `card_id` and
  `digest` (`refused`, 403), `signinCode` and `signinCancel` `id` (`refused`), `socketTicket`
  (`ticket_refused`), `agentStatus` and `agentCancel` (`no_run`, `no_schedule`), the owner's
  own `fileSyncCommit` `lease_id` and `epoch` (`sync_fenced`), the collection cursors (`cursor_invalid`, 400 as before). Each was a lookup
  answering a malformed call as a missing thing; no caller sends one (the app and the runner
  send what the answers they read gave, `/mcp` refuses it first). Keeping each code would need a
  code per field in the table, a copy of an accident; the refusals' own contract names
  `invalid` for a wrong type, so the step follows it. `cursor_invalid` keeps its meaning for a
  string no answer gave.
- **Words.** A mistyped value is answered `<the row's words>: <field> is <its types>`, in place
  of its module's own words: for example "path is 1 to 256 printable characters", "agent_id
  is an agent id", "a path is an absolute, well-formed path...", "private is true or false",
  "recursive is true or false", "at is a time in seconds since the epoch", "cron is a cron
  expression", "unknown event", "expected_etag is the file's current etag, or \"\" to create it
  only when absent..." and "expected_etag is the entry's current etag..." for an outside
  client's or a run's null etag (now "invalid file request: expected_etag is a string"), "a
  grant's profile is one of directory, custom" for a non-string profile, and "outside scopes
  require exact universe, agent and capability" for a key's scopes that are no list, which the
  doors' mint now refuses before it mints, as it refuses a missing name: "a key's scopes are a
  list: none for a full key".
- **Narrowed.** A present `null` for an optional field typed otherwise (`max_bytes`, `after`,
  `conversation_id`, `at`, `request_id`, `etag`, ...) was read as absent by `?? null`; it is now
  refused, as `/mcp` refused it already. A `runDecide` `check` other than true or false, a
  `mailChain` `body_message_id` that is no string at a zero offset, and other ignored values of
  the wrong type are refused too.

## Expand, then contract, across the deploy order

- **The plan's hold**, run on this tree against the doors at 1ae659350 (the stage after the
  current deploy), f3bf49268 and a5dc841df (main): no problem, and the 149 newly typed fields "held by name
  alone, the doors' version before declaring no type", as `handed-types.md` designed for the
  first typing of an untyped field. The four by-who file doors pair the old single list with
  both new lists, each taking what it handed.
- **What the stage's doors hand those 149 fields**, read from their code (it is this tree's
  doors but the two changes below): the types above, every one (`tsc` over that code with the
  typed requests, plus the handed bodies' callers). The two the typed requests refused are
  taken: the app's `fileWrite` with a null etag and bytes is the owner's own call, which its
  owner fields take, and `mintKey`'s scopes are the app's `[]`.
- **A rollback** of this change takes the owner back to a fields step that holds no type,
  beside doors that hand the declared types: nothing narrows. It adds no migration.
- The hold's by-name fallback is live for this deploy; once the stage and its rollback target
  serve this tree's doors no field is untyped at either side, and it can go (owed).

## Problems found (untangle step 2), with the fix above each

- **The fields step holds each value to its schema's types and `droppedTypes`, one rule for
  the hold and the step (`takesType`).**
  - `requestFields` held names only; each module re-checked types, so `droppedTypes` was true
    only by a hand edit (#5822).
  - `deploy/handed.ts` had its own copy of the `integer` under `number` rule.
- **Every field names a type; `bytes` is a type no tool serves.**
  - 149 fields typed on neither side; `inboundAccept`'s bytes fit no JSON type.
- **The owner's own wider fields are data (by who asks), not code.**
  - `heldToRead`, `UNREAD_CHANGE` and `UNREAD_WRITE`: a per-caller etag rule in `files.ts`.
  - The table said `string` for an etag and content the owner's own calls hand as null and
    bytes.
- **The doors' requests carry the table's types (`requests.ts`), so `values` are only what is
  narrower.**
  - `DoorTypes` `values` and the restore and erasure request interfaces restated the table's
    types by hand; `tsc` held the doors to them, not to the table.
  - `PolicyWriteRequest` said the owner is handed a policy document; it is handed an object.
- **Module checks that restate a type go.**
  - `flag`, `agentRun`'s `at` and `cron`, `runDecide`'s `tool_name`, `wakeSubscribe`'s `name`,
    `signinStart`'s `engine`, the ledger's `agent_id`.
- **Comments.** `REFUSALS`' `cursor_invalid` ("not a string"), `requests.ts`'s header ("a
  field's value is `unknown`... `fileWrite`'s content as bytes"), `owner.ts`'s `DoorTypes`,
  `doors.ts`'s `Args` and `DoorArgs`, `fileWrite`'s doc.

## Left, with reasons

- **Type halves of checks that go beyond a type** (`typeof x !== "string" || !RE.test(x)`,
  `archive.ts`'s `limit` fallback): the handlers read `r` as `Record<string, unknown>`, so
  `tsc` needs them; and the modules' own request interfaces (`FileWriteRequest`,
  `ActionAnswerRequest`, ...) are hand copies of the table's types. One fix behind both: each
  handler's request is its row's (`DoorRequests`) and `Call.r` is typed by it. Owed.
- **`fileWrite`'s bytes path for an outside caller** stays in `files.ts`: the owner's fields
  take bytes, so the module takes them for the owner.
- **The uncalled doors** (above): their fields are typed; whether the app owes them a route or
  they go is their own question. Owed.

## As built

- `contract/doors.json`: the 149 fields typed; `fileWrite`, `fileRemove`, `fileRename`,
  `fileRestore` by who asks.
- `contract/src/doors.ts`: `FieldType` (was `JsonType`, with `bytes`), `takesType`, `typeOf`,
  `requestFields` holding types, the table refusing an untyped field and a tool's bytes field,
  `argsSchema` serving a by-who tool its outside fields.
- `contract/requests.ts`, `contract/src/requests.ts`: each field its type.
- `contract/src/owner.ts`: `DoorTypes` `values` only where narrower; the restore and erasure
  requests the table's; `contract/src/refusal.ts` `cursor_invalid`'s comment.
- `deploy/handed.ts`: `takesType` from the contract.
- `owner/src/owner-do.ts` `#fields`: the owner's fields for the owner itself alone.
- `owner/src/files.ts`, `runs.ts`, `actions.ts`, `ledger.ts`, `signins.ts`, `grants.ts`,
  `policy.ts`: the checks and types above. `doors/src/api-keys.ts`: a key's scopes a
  list before the mint. `cutover.json`: the import doors' types named in `owner.ts`.
- Tests: `contract/guards/doors.test.ts` (each type taken and refused, `integer` under
  `number`, a dropped type, bytes, an undefined field; a field with no type and a tool's bytes
  field refused; a by-who tool served its outside fields), the owner tests whose module words
  for a mistyped value became the step's, and whose outside writes sent bytes or a null etag.

## Review (2026-11-23)

- The sync agent's four sync doors fence its lease in the bind step (`owner-do.ts` `#syncer`),
  before the fields step, so a mistyped `lease_id` or `epoch` there is still `sync_fenced`
  (checked on a probe); only the owner's own lease commit, whose module fences it after the
  fields, answers `invalid`. The research and contract lines above are corrected to that.
- The type check reads the request and the table alone, after the bind, account, session,
  guard and scope steps (before the guard and scope only on `fieldsFirst` rows), so a
  malformed value's `invalid` against a missing thing's `refused` tells a caller only what
  it sent: no lookup's answer is reachable through it.

## Owed

- [ ] untangle/door-types-handlers: each handler's request is its row's (`DoorRequests`) and
  `Call.r` is typed by it, so the type halves of the modules' checks and their hand-copied
  request interfaces go; `registerAgent`'s handler then tells the owner itself as `#fields`
  does (`origin` and `run` null), not by `origin` alone, a second copy of that rule (review).
- [ ] live: the stage and its rollback target serve doors from this tree; then
  `deploy/handed.ts`'s by-name fallback (`untyped`) and the plan line naming it go
  (untangle/door-types-fallback).
- [x] untangle/uncalled-doors: `disableAgent`, `deleteAgent`, `mailChain`,
  `grantForgetRevoked`, `fileRedact` and `fileHistoryPurge` have no caller but tests: research
  whether the app owes each a route, or it goes.
