# ADR-019: One Box Per Owner, With an Owner-Only Policy Layer

## Status

Accepted (founder decisions 2026-10-11). Amends ADR-012.

## Date

2026-10-11

## Context

ADR-012 fixed a sealed box per command center, a thin loop in the control
plane, and a box that suspends after at most 60 seconds idle. The isolation
cutover instead shipped 33 per-path `role_*` owner cells, and `BoxProvider`
never gained a production caller (Shape registry S8). The frontier baseline
(#4624, G1/G4/G5) needs an always-on computer per owner that keeps working
with every device off, a browser the owner can take over, and guardrails a
prompt injection cannot edit. The convergence plan left one question open:
is the box the shipped owner cell or a separate kernel boundary.

Founder, 2026-10-11: build the correct shape now with no band-aids; start
C6 in parallel with C3; add an owner-only policy layer; send email through
the owner's connected account.

## Decision

- **One box per owner account**, not per command center. Command centers are
  directories inside it. The floor is cross-user.
- **The box runs the work:** the agent loop, its tools, child sessions,
  resident processes, the browser, and native CLIs as external-agent
  packages. A box stays awake while any of these runs and suspends only when
  none does.
- **Outside the box:** the control plane (sessions, timers, wakes, routing,
  turn journal), the broker (credentials, model calls, egress, spend, owner
  policy, receipts) and the box host (lifecycle, quotas, snapshots).
- **Production driver: gVisor (`runsc`)** behind `BoxProvider`, one sandbox
  per owner, run by a box host service outside the daemon container. It
  needs no KVM and runs on today's server. Firecracker stays an allowed
  driver behind the same contract, built only on a measured need. The
  per-path owner cells are deleted, not wrapped.
- **Everything inside a box is untrusted, the loop included.** Every
  guarantee is enforced where a request leaves the box.
- **Owner-only policy layer.** Approval rules, the egress gate and spending
  caps are written only by the owner through the protected owner session,
  stored outside the box, enforced by the broker at the box boundary, and
  readable but never writable from inside the box.
- **Known P1 residual:** the broker validates the owner cookie against
  `.runtime/owner-sessions.db`, which the daemon can write. A compromised
  daemon can mint an owner session and write policy. P2/P3 close this by
  moving session minting and validation to broker-owned custody.
- **No platform mail domain.** The agent sends email as a connector call on
  the owner's connected account.

## Consequences

- Spec: `openspec/changes/one-box-per-owner/`. It deletes
  `openspec/changes/target-architecture/` and `box-provider-foundation/`
  when it closes.
- ADR-012 still holds for platform state outside boxes, the control plane as
  the only always-on layer for timers, Postgres domains and cells. Its
  per-command-center box, loop placement and 60-second rule are replaced
  here.
