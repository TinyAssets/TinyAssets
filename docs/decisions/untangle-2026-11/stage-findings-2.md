# stage-findings-2 as built (2026-11-23)

The live connector test on the stage (claude.ai custom connector, `stage.tinyassets.io`, the
founder's `+stage` owner, 2026-11-23, batch 1 about 08:05Z and batch 2 about 08:26 to
08:35Z). Each finding was researched in the spec first (Fact 14), then fixed where the spec
settles it, or set out here where it does not.

## 1. `inbox_digest` refused a full-grant client

**What the spec says.** A mailbox is an agent's. An outside identity's agent is the card its
MCP client files on its own verified call (`agent_register`, `registerPropose`) and the owner
applies in its session (`owner-object` "An MCP agent registers through a card its client
files"; `registrations.ts`). The ledger reads an outside caller's mailbox as its one enabled
agent's (`Ledger.#identity`), and a grant never makes one: "a registration is an optional
mailbox identity most main agents never file" (`registered-agent.md`). The founder's test
names "a wake-up pasted for a really registered main agent" (`tasks.md` §8). So the spec
settles option (b): register first. Option (a) would give every connection a mailbox no
registration names, and nothing in the spec makes one.

**What landed.** The refusal's words name the way out. They were "this outside identity is
not one enabled agent", spelled twice (`Ledger.#self`, `OwnerDO.#universe`). Now they are one
constant, `contract/src/refusal.ts` `NOT_AN_AGENT` (the contract's, since the doors' tests
read it too, as #5817's key test does): "this connection has no registered agent, which a
mailbox, its rooms and a narrowed grant's work need: file one with agent_register, for the
owner to approve". `inbox_digest` and `mailbox_read` say a connection has mail once it is a
registered agent. `agent_register` says what approval gives. `agents_list` says a `room_id`
is null until then.

**What the spec does not settle: the owner's half.** The owner cannot apply a card in the app.
`registerAgent`'s card form (`card_id`, `digest`) and `registerProposal` have no route in
`doors/src/app.ts`. The app's `outside-agents` route takes only an API key's registration, and
no screen shows a pending card. Filing one tells the owner nothing (`Registrations.file`
raises a notice only when it evicts). A client that registers today waits for an apply that
cannot happen. So `/workspace/index.md` and the connector's instructions do not send clients
to `agent_register` yet; that would lead them into the same wait. The options, for the
coordinator:

- (i) Filing raises one owner notice naming the client and the card, and an app view
  (`#registration/<card_id>`) shows the card (`registerProposal`) and applies it by its id and
  digest (`registerAgent`).
- (ii) `agent_register` answers an `approve_url`, the app view of (i), which the client
  hands its user, as `agents_list` hands a `connect_url`.
- (iii) Both. A notice reaches an owner whose client says nothing; the link reaches one who
  is in the chat.

Either way it is app work beside `untangle/app-key-grant`'s Connected apps screen, so it is
owed, not built here.

## 2 and 6. A file fault answered "internal error"

**Cause.** Neither a tombstone nor a history row. Every file fault (`FileFault`: `not_found`,
`is_directory`, `not_directory`, `exists`, `not_empty`) was an `Error` of its own and no
refusal. Only the sync agent's door read it (`doors/src/egress.ts`). The MCP door, `/api`, the
app's `tools/` route and code mode each read a non-refusal as a bug and answered "internal
error". So `file_read` of any missing path failed this way, not just a removed one. So did
`file_remove` of a folder without `recursive` (6).

**Fix.** A file fault is a refusal of its own code (`contract/src/refusal.ts`: `FileFault`
extends `Refusal`; `is_directory`, `not_directory`, `exists` and `not_empty` are 409, and
`not_found` is the existing 404). Every door now answers it as it answers any refusal, the code
first in its words. The sync agent's door still answers it in its own protocol (`faultOf`, now
read through `refusalOf`). The two `instanceof Refusal || instanceof FileFault` checks
(`files.ts` sync ops, `starter.ts`) are one test now.

**Consistent across read, stat and remove (6).** `file_read` and `fetch` refuse a missing path
`not_found`. `file_remove` of a path removed since its etag was read answers `{conflict:
null}`, as every etag-held write does; only `expected_etag: ""` is refused `not_found`
(review). `file_stat` answers `{entry: null}`: a stat says what stands, and
the app and the agents read it to learn whether to create (`file_write` with
`expected_etag: ""`). Its description now says so. `file_remove` of a folder without
`recursive` is refused by design (`Files.#remove`: `is_directory` when empty, `not_empty`
with its contents). Its description says so too.

## 3. `board_digest` named `collection_pull`

`collection_pull` was the door's old tool name. The door is `collectionPull`, and its tool is
`collection_list`. The description now names `collection_list` of the board collection from
`after` 0. A guard (`contract/guards/tools.test.ts` `unservedNames`) holds every name in a
tool's description and its arguments' to a tool that each profile serving that tool also
serves. A name counts when it is snake_case, begins with a tool's family (`collection_`,
`mailbox_`) and is no argument's name. So a run's tool cannot name a mailbox tool either.

## 4. The seeded map linked to paths a fresh owner lacks

The starter's design gives a directory its index line when an agent makes it. The knowledge
skill says: "a directory you make gets an `index.md` and a line in its parent's index, up to
`/workspace/index.md`". It also starts the base when there is first something to keep. The
seeded map promised `/workspace/knowledge/index.md` and `/workspace/projects/`, which nothing
seeds. `/workspace/projects/` was also a second home for project notes. The skills put a
project's pages in the knowledge base (`rooms-and-board`'s `links:
/workspace/knowledge/projects/x.md`; the knowledge skill's page per concept, a project among
them). The map's `# Work` section is gone (starter version 10). An owner already seeded keeps
their map: it is a personal file. A guard (`owner/guards/starter.test.ts` `unseeded`) holds
every index line of a seeded `index.md` to a seeded file or a folder holding one.

## 5. Writer attribution differs between files and collections: intended

The two stores record different things, each for a reason of its own:

- **Files** are the owner's tree alone. Their `writer` is a files scope, the owner's (`owner`)
  or a client's (`client:<id>`). It is the attribution the history, the sync notices ("naming
  who changed it ... or the outside client by name") and the client's share all read
  (`owner-object` spec, sandbox lease: "written as that client").
- **Collections** have more than one principal: a Tier A room's visiting owner writes as
  itself. Their `writer` is the binding's caller (`owner-object`: "the binding's caller stays
  the only caller"), the principal. A private row is that principal's own, and the owner
  clears one writer's rows by it. The client's scope is kept beside it as the row's `filer`
  (`collections.ts` `filerOf`), which its share is held to. So an outside client of the owner
  writes a row as the owner's principal, and `filer` names the client.

Making a collection's `writer` the client would change who sees a private row. The owner's
own app would stop seeing its client's private rows, against "deletes and clears the owner's
alone". So nothing changed. Whether a digest row should also show its `filer`, so an agent
can tell which of the owner's clients wrote a room row, is a separate question. The spec
answers it today with the row's body (`owner:` in the board's rows).

## 7. `agent_register` needs a `command_center_id` no tool returns

The field is where a narrowed grant acts. `OwnerDO.#universe` reads the registered agent's
`command_center_id` as the universe a scoped grant's triples name. The owner types that name
in the app as the key's "Space". For a full grant any printable name registers. Its argument
description now says so. Whether "command center" is a platform namespace at all is
`untangle/old-names`' (`tasks.md`: "command center" dropped from registration, grants, tables
and wake text unless research finds it a real namespace). The rest of 7 is finding 1: `main`
has no room for this connection, and the mailbox stays refused, until it is a registered
agent.

## 8. Two rows both at revision 3: intended

`revision` is per row. A new row starts past `collection_space.revision_floor`, the newest
revision any delete or clear gave owner-wide, so a key made again never repeats one
(`owner-object`: "a key made again SHALL go on past the newest revision any delete or clear
gave"). Two new rows after deletes both start at that floor plus one. `etag` (from the
collection's `seq` and the body) tells them apart.

## 9. `board_digest`'s cursor moved with no board rows: as the spec says, with a leak

The cursor is the owner-wide collection clock (`owner-object`: "the next cursor the page's last
change when it is full, else the owner's latest"), so one cursor covers the board and every
room, and a reader never scans again what it passed. It does tell a caller how many collection
changes happened, and when, including other writers' private rows. A visiting owner can call
`board_digest` and `collection_list` (`owner/doors.json` caller `visitors`). The
per-collection `seq` that `collection_list` pages by tells the same of one collection.
Fixed in `untangle/board-cursor` (`board-cursor.md`): a cursor is the reader's own last
item, and a spurious reset after a prune is the accepted cost.

## 10. `file_history` shows an append as `write`: intended

An append writes the file's whole next version at the etag it read (doors spec: `file_append`
"writes at the etag it read and reads again on a conflict"). The history's ops are what a
version is (`schema.ts`: `write`, `mkdir`, `delete`, `rename_from`, `rename_to`, `restore`,
`conflict`, `starter`). The spec names no `append` op, so nothing changed.

## 11. "This call has no result", seen once

Two calls in flight (`agents_list`, `file_usage`) answered "This call has no result" and ran
fine again. Nothing on the doors cuts a tool call without an answer. A 2026-07-28 call's wait
is the client's own (its stream), a 2025-11-25 call's is `LEGACY_WAIT_MS` (10 minutes), and a
refusal or a bug is a result with text. What could end a call with no result from this side
is the runtime dropping the request: an isolate past its limits, or a deploy replacing the
Worker mid-request. Neither left a trace the test could see. Recorded as seen, most likely
the client's.

## Owed

- [x] `untangle/register-apply`: the owner's apply of a registration card in the app, (i),
  (ii) or (iii) above; then `/workspace/index.md` or the connector's instructions lead a
  client to `agent_register`. Done as (iii) (`register-apply.md`); the refusal and the
  mailbox tools' descriptions lead a client there at its need, so the map and the
  instructions stay.
- [ ] live: after the next deploy of the doors and the owner, `file_read` of a removed file and
  `file_remove` of a folder without `recursive` answer their codes on the stage.
