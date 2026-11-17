# T2d as built (2026-11-16)

T2d is lane T's fourth doors PR (`t2c.md`, "`ownDoor<T>`: owed"): the table doors' answers the doors' tests read, typed from the contract instead of by hand, now that o2b (#5657) writes `OwnerDoors` from the door table with each door's answer `DoorAnswer<"door">`.

## The plan (research, before building)

**What is there.** A doors test reads an owner door's answer through `ownDoor<T>(owner, door, request)` (`doors/test/support.ts`), `T` written at the call, and the browser test's entry (`e2e-entry.ts`) through a local `door<T>` of its own. Both call `callDoor` (`doors/src/tools.ts`), which answers `unknown`, and cast it to `T`. 40 calls name 16 doors. Thirteen of those doors have answers a test reads. `DoorTypes` (`contract/src/owner.ts`) types twelve of them not at all, so their `DoorAnswer` is `object`, and types `fileRead`'s `entry` only as `object`. Five of the hand types are named copies of an owner shape: `GrantRow` (`support.ts`, the owner's `Client`), `Card` (`binder.test.ts`), `Day` (`e2b-hooks.test.ts`), `Receipt` (`egress.test.ts`, the owner's `EgressReceipt`) and `Read` (`import.test.ts`, `fileRead`'s). The rest are written inline at each call, sometimes differently for the same door (`noticeList`'s `detail` is `string` in one file and `unknown` in two others; `policyRead`'s `policy` is `unknown` in one and `Record<string, unknown>` in three). One more cast is outside `ownDoor`: `registerAgent`'s answer in `support.ts` `registerAgent`, cast to `{ agent_id: string }`.

**The hidden link.** Each hand type copies the owner's answer, and nothing holds the copy to it. If the owner renames a field, `tsc` still passes, and a test either fails at run time or keeps passing on `undefined`. `OwnerDO implements OwnerDoors` (`owner/src/owner-do.ts`) already holds the owner's answer to `DoorAnswer<D>`. So once a test's answer is a `DoorTypes` `answer`, a test's read and the owner's answer are one fact that `tsc` checks.

**The shape chosen.**

1. **`callDoor<D extends string>` answers `DoorAnswer<D>`.** That is the contract's answer for the door named: the typed answer for a door the code names, and `object` for a name only the table knows (a tool's or a route's door, as the product passes it). Product callers infer `D` and pass the answer on, as before. The one cast sits beside the dispatch that makes it true (`stub[row.door]`, a method whose `OwnerDoors` type answers `DoorAnswer<D>`). This is better than a cast in each test helper: both the doors' test suite and the browser test's entry need the typed call, and the entry cannot import `support.ts` (it imports `cloudflare:test`).
2. **`ownDoor<D extends DoorName>(owner, door: D, request?)` answers `DoorAnswer<D>`,** so a test names no type, and a name that is no door fails `tsc`. `doorAs`, its one-caller wrapper, is folded into it. `e2e-entry.ts`'s `door` takes `D extends DoorName` the same way.
3. **The answers the doors' tests read are `DoorTypes` entries,** 13 new ones, each with the fields the tests' hand types named, not the owner's whole answer, as the doc says (`answer`: what the doors read): `outsideAgents`, `collectionGet`, `fileStat`, `sandboxDays`, `policyRead` (its `policy` the contract's `PolicyDocument`), `policyProposal`, `grantList`, `registerProposal`, `noticeList`, `starterReceipts`, `signinList`, `egressReceipts` and `registerAgent`. `fileRead`'s `entry: object` becomes the fields the import's read-back and the upload test read, plus its `chunks`. The five named copies and every inline hand type go. The `DoorTypes` doc now says the answer is what the doors *or their tests* read, which `OwnerDO implements OwnerDoors` holds to the owner's own.
4. **#5657's two (`sandboxRung`, `restoreRelease`) go.** Its review found that they typed answers the doors don't read. Checked against every reader: the ring consumer (`ring.ts`) ignores `sandboxRung`'s `waiting`, and no doors test reads it. The operator channel passes `restoreRelease`'s answer on as JSON, and the kit's restore mode (`deploy.ts`) checks only that the op succeeded. So both are entries no reader needs, and their doors answer `object`, as an unread answer does. The owner's own tests read both from the owner's class, unchanged. Every other `answer` entry has a reader: the doors' code, a doors test, or the kit, which reads the operator channel's answers by the contract's named types (`RestoreSource`, `RestoreHold`, `RestoreAnswer`, `RestoreNames`), or field by field in the rotation sweep (`RewrapAnswer`, `ResealAnswer`: `left`, `kid`).

**Not in scope, and why it is no problem:** the doors' tests' casts of an MCP or app answer (`answerOf(...) as { ... }`) read the wire. That is JSON the doors write (`answerJson`), not the object's RPC answer, so `DoorAnswer` is not its type. The contract's doc names that case already ("an object they pass on as it came, as JSON").

**Overlap with `untangle/o2c`:** none. It splits the door table (`contract/doors.json`, `owner/doors.json`) and rewrites the generator's input. This PR changes neither, and no generated file: `npm run generate`'s contract step writes them unchanged.

**Size.** About 250 changed lines in 16 files.

## As built

As planned. One call changed beyond its type: `egress.test.ts` `receipts` reverses with `toReversed()`, since the answer's array is now `readonly`. `reverse()` changed the answer in place, and nothing read it after.

**Mutations**, each refused by `tsc` on this head:

- **The owner's `starterReceipts` answering `ver` for `version`** (`owner/src/starter.ts`): `OwnerDO incorrectly implements interface 'OwnerDoors'` (`owner-do.ts`). Before, the doors' tests' `{ version: number }` cast compiled.
- **The owner's `Notice.detail` a number:** the same `implements` error, at `noticeList`.
- **A doors test naming a door that is not there** (`ownDoor(who, "policyReed")`): refused, `not assignable to parameter of type 'DoorName'`. Before, it was a string, and only the run refused it.
