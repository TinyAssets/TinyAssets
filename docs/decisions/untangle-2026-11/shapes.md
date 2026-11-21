# The data shapes before real data: the remaining PRs, in order (2026-11-21)

`tasks.md` step 5's data-shapes line (d3 §P, list3 P48) and step 4's lane O remainder, read at
`origin/main` `88e74e1cd`. The designs are `untangle-2026-11-remaining-build.md` §P and the two
researches it adopted (`datashapes_research.md`, `history_keys_research.md`, not kept in the
repo). This file orders what is left, names the files each PR touches, and records the rooms
PR's decisions (the first one built, `untangle/shapes-rooms`).

## What has merged

- T1b4 (#5536), T1c (#5564 to #5582), R1's files cipher (#5530), R5's P-shape (#5531).
- §P item 1, receipts on their rows (#5554, owner migration 9).
- §P item 3's H1, the history keys (#5538, owner migration 7).
- §P item 5's F1, retention off and one index ceiling (#5548, owner migration 8), but for its
  owed census refusal.
- §P item 6, the chosen retentions (#5682).
- d3.md item 6 (`sessions.ts` copying `SESSION_TTL_S`): done, it reads the constant.

The owner has 18 migrations, all pinned.

## What is left, in dependency order

| # | PR | After | Touches |
|---|---|---|---|
| 1 | **Rooms**: a `rooms` table and room members, opaque ids (§P item 2) | T1c, T1b4, receipts: merged. **Unblocked.** | `owner/src/{ledger,segments,archive,inbound,launch,runs,schema,export,restore}.ts`, the generated `owner/src/door-map.ts`, `contract/doors.json` (`roomPost`'s description) and the generated `contract/src/owner-doors.ts`, `doors/app/app.js`, the starter's three texts (`rooms-and-board`, `schedule`, `agents/main/AGENTS.md`) and their generated copies (`starter.json`, `manifest.json`, `plugins/*/skills/*`), `owner/memory/probe.ts`, the owner tests that name `dm:` rooms, `owner/guards/{owner-bound,frozen-bounds,migrations}.test.ts` and `autoindexes.ts`, `limits-audit.md`, the owner spec |
| 2 | **F1's census refusal** at the index's ceiling (lane I; #5548's Owed) | F1: merged. **Unblocked**, but its design is open (below). | `import/{census,survey}.ts`, `import/guards/census.test.ts`; option (b) also `owner/src/{manifest,files,schema}.ts` |
| 3 | **Chain-depth stop**: an off-by-default owner setting that stops agent-to-agent mail past a chain depth (§Q item 2, lane O) | #5707 (`owner/src/policy.ts`) and #5714 (`contract/src/policy-document.ts`) merge; rooms (both post in `ledger.ts`) | `contract/src/policy-document.ts`, `owner/src/{policy,ledger}.ts`, the default document's digest, the policy tests |
| 4 | **S1**, the stream extraction into `owner/src/stream.ts`, no behaviour change, a migration widening the segment tables' stream checks | rooms (its mail adapter carries the room-id tags) | new `owner/src/stream.ts`; `segments.ts`, `archive.ts`, `ledger.ts`, `owner-do.ts`, `export.ts`, `holds.ts`; a grep guard |
| 5 | **H2**, the erasures stream and the feed | S1 | `collections.ts`, `owner-do.ts`, `pages.ts`, `export.ts`; one starter skill line |
| 6 | **H3**, the history stream with erased-chain drops, owing §3.4's check (bound the deleter's name, or take the factor into the guard's term) | H2 | `collections.ts`, `files.ts`, `owner-do.ts`, `export.ts`, the spec, `limits-audit.md` |
| 7 | **Conversations**: ids carry their order key, idle ones move to a `conversations` stream (§P item 4, with datashapes 1b) | H3 (its straggler merge and key-drop queue; `history_keys_research.md` §6) | `owner/src/{runs,launch}.ts`, `contract/src/{ids,limits}.ts`, the stream adapter |
| 8 | **F2**, file versions to segments with mark-and-sweep | H3 and R1 (merged) | `owner/src/files.ts`, the stream adapter, `export.ts` |
| — | F3, the live index's R2 level | the founder's open question; the recommendation is after the cutover | — |

Step 4's "lane O (O1 to O5) is left": every O item is ticked but the chain-depth stop (row 3)
and B24's dry run (#5709, its own lane). The rooms PR is lane O's too.

**In parallel now, with disjoint files:** rooms (1) and the census refusal (2). Rows 4 to 8 are
a chain, each on the one before; 3 waits on two open PRs and on rooms.

**The census refusal needs its design chosen before it is built** (Fact 14). #5548 named two
ways without a copy of the object's accounting: (a) replay the built tree through the object's
own write path (`Files` over the census's `node:sqlite`, with the owner's `MIGRATIONS`), or (b)
one TS form of each row's charge and of a write's row set, with the SQL generated from it.
(a) touches only `import/`; (b) touches `owner/src/schema.ts`, which rooms changes, so (b) would
run after rooms.

## Open PRs, checked for shared files

- #5707 (R5 hooks): `contract/doors.json`, `contract/src/owner-doors.ts`, `door-map.ts`,
  `runs.ts`. Rooms edits other hunks of the same files (`roomPost`'s description, a comment in
  `runs.ts`): no shared hunk, and the generated files are regenerated at the merge.
- #5714 (the contract pass): `contract/src/policy-document.ts`, `wake-kinds.ts`. Rooms touches
  neither: `ROOM_PREFIX`'s copy in `wake-kinds.ts` is left to after #5714 (below).
- `untangle/pass-owner-2` (local, not pushed): comments in nearly every owner test, and
  `owner-bound.test.ts`. Rooms changes the room ids in some of the same tests. Whichever lands
  second takes the other's side and re-applies its own lines.
- #5692 (the OKF watcher): `starter.json`, `manifest.json`, the plugins, for `okf-spec.md`
  only. Both raise `manifest.json`'s `version`: whichever merges second takes the next one and
  regenerates.
- #5709 (B24's dry run), #5710 to #5716, #5696, #5343: no shared file.

## The rooms PR's decisions

The research's design (`datashapes_research.md` §1.4), with one gap it left closed here:

1. **A pair's room id is a digest of its member set** (`pairRoom`: `rm_` and 32 hex), as an
   own agent's id is a digest of its name (`nativeId`). The research had `agents_list` return
   each agent's room with the caller and also made a pair's room only at its first post. Both
   hold only if a pair's id exists before its row: with a random id, `agents_list` would have
   to write a room to list it. With the digest, `agents_list` stays a read, and opening a pair
   twice is the same room. It is not keyed: the object's id key is made with the first message,
   and a read can't make it; nothing needs the id unguessable, only unread.
2. **One resolver.** A post's room is its row. An id with no row yet is a pair the sender may
   open: the post finds the partner whose pair id with the sender matches, among the
   identities the sender may post to, and makes the room and its two members in the post's own
   unit. `roomOf` (inbound, launch) finds or makes a pair's room from its members.
3. **The triggers key on the recipient.** An agent's mail to the owner counts in its
   `unacked_to_owner` by its `owner` recipient row, not by `room_id = 'dm:' || sender_id`.
4. **The archive keys on rooms.** A fence's bloom holds its records' room ids; a deleted agent's
   rooms go to `archive_purges` by room in the deletion's unit, and its room rows go with it.
   Migration 19 sets every older fence's bloom to all ones, so an older segment counts as
   holding every room: a purge reads it and skips it, never misses it. `roomMembers` and both
   parsers are deleted.
5. **One bound for rooms and purges.** A room is a row until its archived mail is gone, so
   `rooms` and `archive_purges` share one ceiling, `maxRooms` (5,000: 27.2 MB of the owner's
   half, which the room the change log's bound was derived from holds; the owner's rooms with
   each agent are at most 2,000 of it). A deletion moves rows from one to the other and is never
   refused for the archive's pace, so its `storage_busy` and `maxAgentRows`' term on the purges
   go. A new pair's first post past it is refused `ledger_full`.
6. **The dark rows keep their strings.** Migration 19 backfills `rooms` and `room_members`
   from the hot messages' distinct `room_id`, each old string kept as that room's id; the `dm:`
   parse exists only in that frozen SQL. `FORWARD` 19 refuses a generation from before it,
   since the backfill reads other tables' rows (as migration 9's did).
7. **`agents_list`'s `room_id`** is each agent's room with the caller, or with the owner on
   the caller's own entry (an outside agent's or a run's own); null for an outside identity
   that is not one enabled agent, which posts nowhere. The skills and `app.js` use it; nothing
   tells an agent how an id is made.
8. **Left:** `ROOM_PREFIX`'s copy in `contract/src/wake-kinds.ts` (the app rooms' collection
   prefix, not a mail room) waits for #5714, which edits that file; S4's group rooms re-derive
   the one-recipient-a-message bound.
