# ADR-024: A Paired Device Runs Only Its Owner's Work

## Status

Accepted (founder, 2026-10-26). Amends ADR-008.

## Date

2026-10-26

## Context

ADR-008 makes the platform cloud-only and names the founder's PC as never
infrastructure. The device runner lets an owner's own computer run their
vendor CLIs under their own login, host their game matches and expose local
tools. That is the cheapest placement for everyone, and the strongest
position under vendor terms: the owner's own device, the owner's own login,
the owner's own work.

## Decision

A paired device is a placement for its owner's own work, never platform
infrastructure.

- It runs only work admitted for its owner, never another user's, and never
  as a fallback for platform paths.
- It connects outbound only. Polls park at the broker, so an idle device
  never keeps a box awake.
- Vendor CLI logins stay on the device; the broker holds only the pairing
  verifier and revocation state.
- Local tools are off until the owner grants each one, and the device
  enforces the grant again.
- Every hosted surface keeps working with no device online (Direction 8).
  Offline means an explicit "unavailable", and in-flight work becomes
  "unknown", never replayed.

## Consequences

- **For ADR-008:** the cloud-only rule still governs platform services. The
  founder's PC may pair like any user's device, for the founder's own work
  only.
- **Code:** `serves: owner_only` is enforced for every device source.
- Build order: `openspec/changes/device-first-build/`.
