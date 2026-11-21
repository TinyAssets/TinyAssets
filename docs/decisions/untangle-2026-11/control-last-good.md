# control-last-good as built (2026-11-21)

`untangle/control-last-good` builds slices C1 and C2 of the deploy-safety research (section C):
when the control record can't be read, each reader serves the last copy it read well formed and
refuses only the acts that need a fresh one (the founder, 2026-11-21, call 13). Before it, an
outage and a bad write were one case, and both closed every gate.

## The problems found

- `contract/src/control.ts` `readControl` read KV as `type: "json"`: a value that is not JSON
  threw into the same `catch` as an outage, so a typo in a kill flip was `unreadable`. Serving a
  copy on `unreadable` would have kept the platform on after a typo'd flip off.
- Three last-good copies, each its own: the sandbox's `known` and `known_retention` (used for
  any read but a good one, malformed included), the owner's `platform_tiers` and
  `platform_signins`. No reader had a copy of the gates themselves, so an outage closed them all.
- `doors/src/import/index.ts` `tiers` handed `tiersOf(unreadable)`, null, which the owner reads as
  "the record holds none" and deletes its kept tiers (`admission.ts` `keep`): an outage during
  the import wiped the tiers of every owner it touched. `signinsRead` did the same to the kept
  sign-in switches (`[]`), at the doors and in the owner's own lane.
- The kit's `control` mode and the readers parsed by two routes (`parseControl` of a JSON value,
  and of KV's own JSON parse), with no bound on the record's size.

## The fix

1. **A read tells a bad write from an outage** (C1). `readControl(store, keeper)` reads the
   text; `controlOf(text)` parses it (at most `CONTROL_TEXT_MAX` bytes, JSON, `parseControl`).
   A text it refuses is `malformed`, which closes every gate and never uses a copy. Only a throw
   from KV is an outage. The kit's `control` mode checks its live and edited record with
   `controlOf` too, so it never writes a record a reader would read as malformed.
2. **One keeper, handed in by each reader** (C2). `ControlKeeper` (`kept`, `keep`) is the
   contract's. `readControl` keeps each well-formed read's text, written only when it changes;
   a missing or malformed read drops it (review, 2026-11-21: else an outage after a typo'd kill
   flip served the copy from before it, reopening what the typo closed). It reads the copy only
   on an outage, as `{ kind: "kept", control, read_at }`; with no copy it
   is `unreadable`, as before. A keeper that fails is logged and is no copy. The gates read
   `gating(read)`, the record as read or kept.
   - **Doors:** the isolate's memory, then the location's Cache API under one key no route
     serves (`doors/src/control.ts` `KEEPER`, a module-state exemption in `doors/guards/rules.ts`:
     the platform's one record, no owner's). Every doors reader goes through `controlRead`.
   - **Owner:** `SqlKeeper` over the object's own SQLite (`control_kept`, migration 20, not
     exported), its writes quiet units of the write gate (`control-gate.ts` `objectKeeper`).
   - **Sandbox:** the same `SqlKeeper` (migration 4), which replaces `known_retention` (dropped).
3. **The fresh-only acts are one table** (`FRESH_ONLY`), each refused with `staleRefusal`, a
   `503` to retry in a minute, by `requireFresh(read, act)` where it happens:
   - an outside client's new authorization and a new owner's first sign-in (`authorize.ts`);
   - a new API key (`app.ts` `keys`);
   - a vendor sign-in (`owner/src/signins.ts` `signinStart`, mode `in`; a sign-out stays open).
     The owner tells by the binding: a kept read hands no switches.
   - Writing the record's numbers into kept state: `tiersOf` and `signinsRead` hand none
     (`undefined`) on a kept or unreadable read, so the owner keeps its own `platform_tiers` and
     `platform_signins`, and the sandbox writes `known` only from a good read. This closes the
     import's wipe.
   Everything else runs on the copy, of any age: tool calls, `/api`, `/app`, hooks, runs,
   starts. A kill flipped before the outage holds through it.
4. **`CONTROL_TEXT_MAX`, 64 KiB:** a recorded technical ceiling, what each kept copy is costed
   for (the owner's database bound). The owner-bound guard costs `control_kept`; the limits
   audit's worst case is restated (369.8 MB under half, after the rooms).

## Behaviour changed on purpose

- The sandbox starts nothing on a record that is missing or malformed (`sandbox_unconfigured`);
  before, it started on the numbers it last kept. Renewals still run on `known`'s numbers, as
  before.
- During an outage the sandbox starts on the copy last read by any read (a start, a sign-in or a
  look); before, a start's retention was the one the last start handed.
- The owner app's account page shows the tiers of the record its gate read, the kept copy
  included; its sources page shows every sign-in off while the copy is served.

## Left, each with its reason

- The owner keeps `control_kept` beside `platform_tiers` and `platform_signins`. Folding the two
  into the one row needs the binding to carry the record, not its tiers and switches: every
  account binding, its parsers, the recorders and the 170 test bindings that hand `tiers`. Its own
  PR, after this one shows the row in place.
- The sandbox's `known` stays: it holds the numbers last read for renewals through a good record
  that holds none (a different requirement from an outage), and the looks' failure count.
- A location that never read the record, or whose cache evicted it, has no copy and refuses as
  before. During a long outage the copy reaches only the locations that read before it began.
- A version split: a version without the keeper fails closed on its share; this ships at 100%
  before a gradual deploy relies on it (research §3.2).

## Owed

- [ ] The owner's `platform_tiers` and `platform_signins` folded into its `control_kept` row,
  the binding carrying the record read fresh instead of its tiers and switches
  (`untangle/owner-control-row`).
- [ ] The founder's checklist line: a kill flipped while KV is down doesn't reach a kept copy,
  so the kill that needs no KV is the edge switch (slice B1, `untangle/edge-kill`).
