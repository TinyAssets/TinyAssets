# ADR-012: Sealed Command-Center Boxes; Target Architecture Now, Capacity Later

## Status

Accepted

## Date

2026-10-02 (sealed box approved 2026-10-01)

## Context

The founder: *"i would like to move towards the architecture and dependencies
we want later sooner rather than later. i want to do things correct the first
time"*. Per-owner isolation is the foundation the rest of the work stands on.

## Decision

Build the final interfaces and data placement at small capacity:

- Each command center runs in exactly one sealed box with its own kernel
  boundary (Firecracker with snapshot and restore; gVisor behind the same
  `BoxProvider` interface where KVM is unusable), its own fixed-size disk from
  the account's storage quota, and no network interface. The platform reaches
  box contents only through `BoxProvider` and treats them as untrusted.
- The control plane is the only always-on layer. The thin agent loop, the
  scheduler, triggers, inbox and notifications live there; a box keeps no
  timers and suspends after at most 60 seconds idle.
- Platform state (vault, run, consent, usage, conversation and session stores)
  lives outside every box. Credentials stay in the broker, never in the loop or
  in a box.
- Postgres holds the cross-user transactional domains (catalog, ledger, inbox,
  market) behind an outbox; the vendor is chosen when it is stood up.
- Growth adds cells and box hosts behind fixed seams (`home_cell`, ownership
  generation, outbox). No code path checks the stage or the tier.

## Consequences

- Work in flight: `openspec/changes/target-architecture/` and its slice
  changes. Code seams: `tinyassets/boxes/`, `tinyassets/control_plane/`,
  `tinyassets/agent_loop/`, `tinyassets/broker/`.
- Foundational patches are brought forward, so pre-migration band-aids never
  delay this move.
