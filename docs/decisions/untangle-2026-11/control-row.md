# control-row as built (2026-11-22)

`untangle/control-row` takes the two items `control-last-good.md` owed: the owner's kept tier
numbers folded into its one kept copy of the control record, the binding carrying the record
instead of its tiers (`untangle/owner-control-row`), and the founder's checklist line for a
kill flipped while KV is down (`untangle/edge-kill`). The sign-in switches' half of the fold
waits for `untangle/start-faults`, which holds the owner's `signins.ts` and `launch.ts`.

## Research first (Fact 14), read on main 8fbf509a5

- **What the doors hand.** Every outside and owner call reads the record once
  (`doors/src/control.ts`, the doors' keeper), and `withAccount` (`binder.ts`) put two values
  derived from it on the account and inbound bindings: `tiers` (`tiersOf`) and `signins`
  (`signinsRead`). The import's binding carried `tiers` too (`bindImport`, the import's own
  `tiers` helper). Each was `undefined` on a kept or unreadable read.
- **What the owner kept, and where.** Three one-row tables, none exported:
  `platform_tiers` (baseline part 12: the tiers' JSON, written at `#bind` by
  `OwnerAdmission.keep`), `platform_signins` (the switched-on ids, written at `#account` and
  at the runs lane's pass by `signins.ts` `switchedOn`), and `control_kept` (migration 20: the
  record's text, written by the object's own reads through `objectKeeper`: a code-mode
  script's tool calls, the runs lane's pass, the doorbell's gate). Three partial copies of one
  record, each written by a different path.
- **Who reads the tiers.** `OwnerAdmission.tiers()` and `numbers()`: the files byte quota and
  the storage-full refusal (`ownerStores`), the starter's readiness, every run's admission and
  the lane's runs (`launch.ts`, through `admission`). Nothing reads `platform_tiers` but
  `admission.ts`.
- **Restore and forward.** `FORWARD` takes `null` for a migration that names no exported
  table, nor one a trigger of it writes; `platform_tiers` is neither, and the restore's load
  table never names it. The export's shape (`exported()`) does not change; its pin moves only
  its schema number.
- **The edge switch.** No WAF rule, rulesets client or kill-rule step exists: the deploy kit
  (`deploy/cloudflare.ts`, `deploy.ts`), `contract/identity.json` and the checklist name none.
  The research's slice B1 (`deploy_safety_research.md` §2.2) is unbuilt, carried as
  `tasks.md`'s "Cloudflare-side kill switch" line (`untangle/kill-switch-cloudflare`). Mode
  `handback` reads its route record from the control namespace, so it is no kill that skips
  KV either.

## What landed

1. **The binding carries the record** (`Binding.control`): its text as the binder read it
   fresh, null when it found none or a malformed one, and absent when it was not read fresh.
   `freshRecord` replaces `tiersOf`, and `ControlRead`'s `read` kind carries the `text` it
   parsed, so the doors hand the bytes every keeper compares (a stringified `Control` is not a
   record's text). The account, inbound and import parsers refuse a text `controlOf` refuses
   (Fact 8: a binder bug is loud). The import binding's record is optional, as the account's.
2. **The owner keeps one copy.** `#bind` keeps a handed record in `control_kept` through the
   contract's `SqlKeeper`, the same row the object's own reads keep, and
   `OwnerAdmission.tiers()` reads the tier numbers from it. `OwnerAdmission.keep` goes. The
   seed's readiness check now runs only when a binding hands a record.
3. **Migration 27 drops `platform_tiers`.** `FORWARD` null (not exported); pinned
   (`node ci/kit/locks.ts pin owner HEAD`); the export's shape pinned again at schema 27, its
   digest unchanged. The owner-bound guard and the policy-boundary guard lose its row, and the
   limits audit's worst case is restated (4,602,017,219.25 bytes; 398.0 MB under half).
4. **`SqlKeeper.keep` reads first.** A keep that changes nothing writes nothing: an owner unit
   that runs any write is marked changed for the nightly manifest (`gate.ts`), and the bind's
   keep runs in the door's unit, as `OwnerAdmission.keep` did with its own read first.
5. **The founder's checklist line** (`deploy/checklist.md`, mode `control`): a kill flipped
   while KV is down reaches no kept copy; the kill that needs no KV is an edge switch, none
   exists yet, and until it lands a hand-made WAF custom rule is the one that reads no KV.
6. **The owner tests run on a record with the founder's tiers.** Their setup
   (`test/gates.ts`) writes `OPEN` with outside access off before each test (the doorbell
   closed, as an empty namespace had it): the object's own reads now keep the copy
   its tiers come from, so an empty namespace dropped the numbers a test's binding handed at
   the lane's first pass. The records the code-door, doorbell, sign-in and mail tests write
   carry the founder's tiers, and the two tier-room tests write their numbers to the record
   the lane reads. A binding in a test hands `OPEN_RECORD` or `openRecord(tiers)`
   (`ci/fixtures.ts`).

## Behaviour changed on purpose

- The object's own reads of the record (the runs lane's pass, a code-mode script's tool
  calls, the doorbell's gate) now move its tier numbers too: the lane's runs take the numbers
  the lane read, not the last door's, and an own read that finds the record missing or
  malformed drops them, as a binder's read did.
- An object that keeps no copy yet admits no run, and adds no file byte, until its next read
  of the record. Every object that has run a lane pass or a code-mode call since migration 20
  keeps one.

## Left, each with its reason

- The binding still carries `signins` beside the record, and the owner still keeps
  `platform_signins`: both are derived from the record and go with the switches' fold, which
  changes `signins.ts` (`switchedOn`, the sign-in doors) and `launch.ts` (`#laneSignins`),
  both on `untangle/start-faults` now. Its one decision: a missing record leaves no copy, so
  the switches a missing record reads as on (each record's own verdict, `signinsRead`) need a
  rule of their own or go; the sandbox starts nothing on a missing record either way.
- The edge switch itself, which needs the founder's token scope (Zone WAF Edit) or two rules
  the founder makes once by hand, and a read of the zone's free rule slots.
- The kept copy is the last write, not the newest record: a door's read at a location KV has
  not reached yet can overwrite the object's newer own read (the record carries no version).
  KV's 60 s bounds it, as it bounds every reader's fresh read; the next fresh read restores it.
- Migration 27 makes the owner forward-only: a rollback stops at it (`forwardOnly`), as at
  migrations 25 and 26.

## Review (2026-11-22)

- A missing or malformed record fails closed: no run is admitted (`numbers()`, `tier_unread`),
  no waiting run starts (`#capacity`), and no file byte is added (`BOUNDS`, `tier_unread`).
- The owner tests' start record (`test/gates.ts`) has outside access off, as the empty
  namespace main's tests ran on: with it on, the object's own `wakes` alarm rang the wakes the
  wakes, grants and schedule tests drive by hand (CI, owner 1/2 and 2/2). Code-door's tests
  write `OPEN` again. No production path changed: the doorbell's gate is the same read.

## Owed

- [x] The sign-in switches folded into the one kept copy: the binding's `signins` and the
  owner's `platform_signins` go, the switches read from `control_kept`
  (`untangle/owner-control-signins`, after `untangle/start-faults`).
- [x] Slice B1, the edge switch: two WAF custom rules on the zone, `TinyAssets kill: outside`
  (the doors' outside paths) and `TinyAssets kill: all` (every doors path and the egress
  host), their expressions rendered from `contract/identity.json` with a guard holding them
  equal; `dark` and `routes` make them disabled when absent and stop on one enabled or
  drifted; the checklist's toggle line (`untangle/kill-switch-cloudflare`). Done
  (`kill-switch-cloudflare.md`: a drifted rule is set back to the tree's, not stopped on).

The first was taken by `untangle/owner-control-signins` (`owner-control-signins.md`): the
switches read from the one kept record, and with no record kept, no sign-in on.
