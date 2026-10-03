# The command-center cutover: code, storage and ids in one freeze window

## Why

The founder decided on 2026-10-01 that "universe" is gone everywhere, including
in code identifiers, storage and the `u-` id prefix ("rename them too",
"change it also"). The cut is clean, with no aliases: "we dont have old users
we just have current testers that need to cleanly move to the new system". C0
(copy) and C1 (the public MCP names) have shipped. C4a (the data layout guard,
#4234) ships first, alone. What remains is everything stored and everything in
code.

`openspec/changes/rename-universe-to-command-center/design.md` holds the
decisions: D6 (code), D7 (storage safety contract), D10 (one cutover), D11
(`u-` becomes `cc-`). This change is how they run.

## What Changes

One image, one freeze window, and one locked, resumable migration at container
start. Storage moves **once**, straight into the target on-disk layout agreed
with `target-architecture` (#4263; design E6):

- `cc-<ulid>/` holds user content and becomes the future box volume.
- `.platform/cc-<ulid>/` holds platform state, daemon-only.
- `.platform/accounts/` is new and holds per-account state.

Phases run in this order:

1. **Names.** Tables, columns, values, `CHECK` literals, marker files,
   serialized keys, checkpoints, LanceDB and JSON. Also the stored
   branch-definition fields (`delivery_sender_universe_id`, workspace
   `storage: "universe"`), the bridge keys inside stored custom-UI bundles,
   and bindings re-pointed from the retired default agent definition.
2. **Ids.** `u-<ulid>` becomes `cc-<ulid>` in every encoding, every folder is
   renamed, and derived identities are recomputed.
3. **Local verification.** The inventory finds zero operational old names and
   ids, records round-trip through their own readers, digests resolve, and
   deletion and export are proven.
4. **External records.** Stripe metadata and entitlement claims are rewritten
   after checkouts are drained, with a recorded inverse.
5. **Reopen.** The layout marker flips to 2 and the server binds its port.

The code half is a deterministic codemod (identifiers, modules, env vars, the
plugin id) in the same image. C1's edge translation is deleted. Retired-name
refusals stay.

## Capabilities

### New

- `command-center-storage-layout`: the layout marker, the exclusion lock, and
  the migration contract every storage-shape change after this one follows.

## Impact

- Every module and test is touched by the codemod, so the other lanes are
  frozen for the window and rebased with the same codemod after it.
- Production data and the Stripe metadata are rewritten. The rollback is
  restore, plus the previous image, plus the recorded Stripe inverse.
- Testers holding old ids outside our reach (cached chat-client graph ids,
  delivered links) get a clear "unknown id" and reconnect.
- Production on 2026-10-02 has no stored agent guidance (instructions, skills,
  soul) that names a retired MCP name. The only hits are verbatim copies of
  platform source in the founder's notes, which are content and exempt.
