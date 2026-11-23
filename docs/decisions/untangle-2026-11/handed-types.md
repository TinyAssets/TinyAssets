# handed-types (2026-11-23): the deploy kit's hold reads each door field's type

`untangle/handed-types`, owed by `cursor-numbers.md` (#5829, "Owed"). The plan's hold
(`deploy/handed.ts`, `deploy.ts` `servedHold`) held each door field's name and part (required,
optional, dropped) between the doors' version before and this tree's owner, not its type. So a
change of a field's type passed the plan even where a deploy or a rollback would pair doors
that hand the old type with an owner that refuses it: #5822 turned the collection cursors from
integers to strings, and a plan of #5829 over b65adb647's doors would have passed.

## Research (Fact 14), from the code and git

### Who declares a field's type, and where

- **The door table is the one source.** Each field of a door's request in `contract/doors.json`
  is a JSON Schema. `contract/src/doors.ts` `argsSchema` serves them as each tool's input on
  `/mcp`, where the MCP SDK holds every outside call to them, and as `POST /api/tools/<name>`;
  the owner's door modules check the same types in code. `contract/src/requests.ts` (written
  by `npm run generate`) carries names only, each value `unknown`. No zod or other schema
  copies them.
- **The vocabulary is `type` alone.** Every field schema on main, and at d315aaa61 (the first
  commit with `handed.json`), b65adb647, 9bb0b1b32 and b8424315e, holds only `type` (a JSON type
  name, or a list of them) and `description`: 99 typed fields on main (65 `string`, 22
  `integer`, 6 `boolean`, 4 `["string", "null"]`, 1 `number`, 1 `object`) and 148 `{}`, every
  one on a door with no tool (the doors' own calls, whose modules check their values in code).
  Nothing held a schema to that vocabulary, so an `enum` or a `minimum` added later would have
  narrowed a field and passed any hold that reads `type`.
- **The shapes (`contract/handed.json`) declare no types.** Each of the 38 parsers reads its
  names from the file (`taken`) and checks its values in code (`typeof value["record"] !==
  "string"`, `whole(event["seq"], 0, next)`). A type list there would be a second copy of those
  checks that nothing ties to them. The shapes stay held by name (left, below).

### What each version hands and takes

- The table is one file both Workers read, so at any commit the doors hand a field as its
  schema says (the `/mcp` schema, and their own calls) and the owner takes it as the same schema
  says. In a pairing the doors at T hand T's type and the owner at N must take it.
- The deploy order (`deploy/facts.ts` `WORKERS`: codemode, files, gateway, sandbox, owner,
  doors) deploys the owner first, and a rollback takes the doors back first (`rollbackAsk`,
  the record's reverse order), so the only pairing across a deploy or a rollback is the
  doors' version before beside this tree's owner: what the plan holds (`servedHold`), against
  the commit each serving version's tag names. A rollback lands on the record's versions
  before, which the run's own plan held its owner against, so it needs no hold of its own.
  New doors never talk to an owner before them, so widening a field is never a pairing to hold.
- The table answers nothing: a door's answer is the owner module's, with no schema. So the
  door table is held one way, the doors' requests; the shapes' answers stay held by name. The
  doors do read some door answers field by field (`contract/src/owner.ts` `DoorTypes` `answer`:
  `tierRead`'s `tier`, `sessionOpen`'s `session`, the restore doors' answers), typed for `tsc`
  alone, which no hold reads at a serving commit (owed below, from the review).

### How #5810, #5821, #5825 and #5826 took a name in steps

- #5821 kept `registerProposal` one deploy as a `dropped` door (the owner takes it, the doors
  call it no more); #5825 took it out once the stage served no doors that called it. #5826 did
  the same for `actionRequest`. A field takes the same step through `Args.dropped` (taken and
  left out by `requestFields`), and a shape's through `dropped` and `always`.
- #5810 made a serving commit without `handed.json` stop the plan, and a rollback to one stop
  the rollback, rather than guess what it hands.
- A type narrowing has no such step today. With one schema per field, the release that narrows
  the owner narrows the doors too, so every release after one that took the wider type would
  fail a hold of types against it: a type could never narrow through the kit. #5822 shows the
  step it needed: its owner still took a number for one deploy (`cursorsOf().from`) while the
  table said `string`, which no data said.

## What "compatible" means, per field

- **The doors hand ⊆ the owner takes.** For every field the doors' version before lists as
  required or optional (it hands none it lists as dropped), every JSON type its schema names
  must be one the tree's owner takes: the field's schema types, or one of its `droppedTypes`,
  where `integer` is taken by `number` as JSON Schema has it. A field the tree's owner lists
  as dropped takes any value (`requestFields` leaves it out); one it no longer takes at all is
  the names' problem, said once.
- **The owner answers ⊆ the doors read:** no door answer is data, so nothing holds it here,
  names or types; the answers the doors read are owed (below).
- **Required and optional** are held as before: no field newly required that the doors' version
  before does not always hand, none taken no more.
- **A field with no declared type (`{}`).** At the owner it takes any value: what JSON Schema and
  the `/mcp` schema say. At the doors' version before, with a type at the owner, what was handed
  is unknown: the pairing is held by name alone for that field, and the plan says so by name
  (`held by name alone, the doors' version before declaring no type: <door> <field>`), never
  passing it as typed. Refusing it instead would make a type, once declared on any of the 148
  untyped fields, never deployable. Every serving commit's table carries the same vocabulary
  (above), so no older file is unreadable; a schema outside it throws, read from git or not.
- **A widened type passes** (`integer` to `number`, `string` to `["string", "null"]`). **A
  narrowed type fails by name** unless the owner still takes the narrowed-out types:
  `"droppedTypes": { "<field>": ["<type>", ...] }` in the door's `args`, the types a release
  before handed that the doors hand no more and the owner still takes, as `dropped` is for a
  name. It goes once the stage serves no doors version that hands them.

## Problems found (untangle step 2), with the fix above each

- **The hold reads each door field's types from the table's own schema, beside its name.**
  - `deploy/handed.ts` `argsFields` reduced each schema to its key, so a type change passed.
  - The plan line said "none refuses the other" over pairings it had not held for type.
- **A type narrows in steps, as a name goes (`droppedTypes`).**
  - No data could say that the owner takes a type the doors hand no more; #5822's step lived in
    the owner's code alone, and a hold of types would have refused every narrowing for good.
- **A field's schema is held to what the hold reads (`fieldTypes`, `contract/src/doors.ts`).**
  - `argsProblems` took any object as a field's schema: a keyword the hold does not read would
    narrow a field unseen. One reader, `fieldTypes`, is both the table's rule and the hold's.
- **The hold says what it held by name alone.**
  - A doors version before with no type on a field the owner types would have passed silently.
- **Comments.** `deploy/handed.ts`'s header and `contract/src/doors.ts`'s `Args` named fields only.

## Left, with reasons

- **The shapes' types** (`contract/handed.json`): their parsers check them in code, and a list in
  the file would be a copy nothing ties to them. Holding them means each parser reading its
  field types from the file, as it reads its names (`taken`): a change across all 38 parsers,
  owed below, research first.
- **`/api`, the app's `tools/<name>` and code mode check no type** (cursor-numbers' research):
  outside callers there may hand any value whatever the table says; the owner's modules refuse
  it. That is the callers' contract with the owner, not a pairing across the deploy.

## As built

- `contract/src/doors.ts`: `fieldTypes` reads a field schema's JSON types (null for none) and
  throws on any key but `type` and `description`; `Args.droppedTypes`; the table refuses a
  field schema `fieldTypes` cannot read, and `droppedTypes` that are not a typed field's types
  it does not already take.
- `deploy/handed.ts`: `handedHold` (was `handedProblems`) answers the problems and the fields
  held by name alone; each door field the doors' version before types is held to the owner's
  types and its `droppedTypes`.
- `deploy/deploy.ts` `servedHold`: the plan line names the fields held by name alone.
- Tests: `deploy/guards/handed.test.ts` (narrowed fails by name, passes through `droppedTypes`,
  then goes; widened passes; `integer` under `number`; an untyped field held by name and named;
  an unreadable schema throws), `deploy/guards/deploy.test.ts` (the plan stops on b65adb647's
  integer cursors, and names an untyped field), `contract/guards/doors.test.ts` (the table's
  new rules).

## Review (2026-11-23)

- Run on the real commits, the hold refuses #5822 over b65adb647's doors, naming four fields
  (`inboxDigest`, `boardDigest` and `collectionPull` `after`, `collectionHistory` `before`, as
  integer), and passes the stage's b8424315e and the rollback target 9bb0b1b32 against this
  tree, none held by name alone: the fallback is not live today.
- **The table's types are a declaration the owner's code is not held to.** `requestFields`
  holds a request's names to the table (so `dropped` is what the owner takes), but each
  field's type is checked again in its module's code, and `droppedTypes` says the owner takes a
  type that only a hand edit of that code makes true, as #5822's owner took integer cursors in
  code alone (`collections.ts` `cursorsOf().from`, until #5829) where the table said `string`;
  and the 148 `{}` fields are held by no type, silently. One fix behind both: the owner's fields step
  refuses a value its schema's types and `droppedTypes` do not name, and each `{}` field
  declares its type. It changes outside callers' refusal words across the doors, so it is
  owed, not built here.

## Owed

- [x] untangle/door-types-owner: the owner's fields step (`requestFields`) holds each field's
  value to its schema's types and `droppedTypes`, as it holds names, and the 148 `{}` fields
  declare their types; then the module type checks it repeats go. Research first (Fact 14).
- [ ] untangle/door-answers: the door answers the doors read (`DoorTypes` `answer`) become data
  the hold reads at a serving commit (`handed.json` shapes, owner to doors), held as answers.

- [ ] untangle/handed-shape-types: each shape parser reads its fields' types from
  `contract/handed.json` as it reads their names (`taken`), so the hold holds a shape's types as
  it now holds a door field's. Research first (Fact 14): which of the 38 parsers' checks are a
  JSON type and which are more (ranges, sequences, nested shapes).
