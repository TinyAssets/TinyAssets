## Why

On 2026-10-27 the founder adopted the plan of record "TinyAssets — Clean-Sheet
Architecture Decision" (https://claude.ai/artifact/L28AqU1pMJCSvV6b6m1icy),
recorded as ADR-032. Judged only on what TinyAssets is for, the trusted side
belongs in one SQLite-backed Durable Object per owner, not in Python broker and
router processes on one droplet. No server of ours stays on (Direction 8), and
per-owner isolation is the runtime's, not our handlers' (Direction 5).

## What Changes

- The trusted side and the agent loop run as one Cloudflare Durable Object per
  owner (`OwnerDO`), written in TypeScript in the new `platform/` workspace. The
  Python logic is ported, never imported, and its tests become acceptance tests.
- Stateless edge Workers are the only binder from a verified credential to an
  owner. A gateway Worker holds sealed per-owner credentials. pi-durable is the
  loop. Owner files live in object storage; a Firecracker sandbox (E2B) wakes
  only for `bash`.
- The new stack is built beside production and verified live on the founder's
  account. Every owner moves in one maintenance window after a full backup, and
  the old paths are deleted in that change. No new feature lands on the
  retiring stack.
- From 2026-10-27 this change is the build order. The device-first plan's later
  slices (S4, S6 to S10) are rebuilt here on the new architecture.
- Registry row S36 ("per-owner trusted state is one Durable Object per owner")
  is added, drifted while the Python broker path is live.

## Capabilities

### New Capabilities

Each slice adds its own capability. The first is the owner object with the one
revision primitive (S34's rules, ported).

### Modified Capabilities

## Impact

New: `platform/` (TypeScript, Workers, workerd tests), `docs/decisions/ADR-032`,
the S36 registry row. At the cutover: the droplet, the gVisor boxes, the Python
router and broker, `.broker/accounts/` stores and the single-host deploy
workflows are deleted.
