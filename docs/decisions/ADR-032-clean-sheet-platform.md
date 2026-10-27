# ADR-032: The Clean-Sheet Platform

## Status

Accepted (founder, 2026-10-27). Supersedes ADR-022. Supersedes the
single-droplet topology, the box storage model, broker-held SQLite as the
trusted store technology, and the router and broker serving design, as listed
below. Build order: `openspec/changes/clean-sheet-platform/`.

## Date

2026-10-27

## Context

On 2026-10-27 the founder ruled that each choice is judged only on what
TinyAssets is for, never on today's stack. The plan of record is the Claude
doc "TinyAssets — Clean-Sheet Architecture Decision"
(https://claude.ai/artifact/L28AqU1pMJCSvV6b6m1icy), adopted that day.

Its admissible reasons are isolation strength, resume and exec latency,
durability, credential and egress control, cost per owner at scale, 24/7
uptime with no host machine (Direction 8), per-owner isolation (Direction 5),
user customization (Directions 1-3), interop with the leading agents,
operating effort for one founder, and lock-in and exit. It struck "today's
kernel", "what DigitalOcean supports", "the one-droplet layout", "existing
code" and "it's what we run now" as reasons. Its audit of every decision in
force found five that rested on those: the one-droplet topology, gVisor as
the box runtime, owner files on host-local block storage, broker-held SQLite
with no replication, and the single-host deploy model.

Evidence: four research tracks (sandbox, trusted side, agent, reasoning
audit), an 18-product survey of agent platforms, the NVIDIA OpenShell study,
and the box-runtime study with this repo's own Firecracker and gVisor
measurements (`openspec/changes/target-architecture/evidence.md`: Firecracker
exec 7.7 ms against 48 ms p95, workspace builds 6.5 s against 19-32 s).

## Decision

1. **The trusted side and the agent loop run as one SQLite-backed Cloudflare
   Durable Object per owner (`OwnerDO`).** It holds the owner's agent
   registry, ledger, receipts and bodies, board and room collections on the one
   revision primitive with history, policy, grants, sessions, connection
   metadata and sealed credentials, subscriptions, the session event log, one
   schedule driven by one alarm, and hibernating app WebSockets. The runtime
   keeps each object's storage private. The class has no binding to its own
   namespace, and no module keeps mutable state at top level, since objects of
   one class can share an isolate.
2. **Stateless edge Workers are the only binder.** Only they turn a verified
   credential (OAuth, `/api` key hash, inbound signature) into an owner, and
   reach that owner's object by `getByName(owner)`. The caller reaches the
   object as a typed argument, never a request field. Cross-owner traffic goes
   through a postmaster Worker that reads the sender object's outbox.
3. **A gateway Worker holds no owner state and opens one owner's sealed
   credential per call,** meters usage, and swaps placeholder credentials on
   sandbox egress. No credential sits in the loop or the sandbox.
4. **pi-durable is the loop,** hosted in the owner's object. User hooks run in
   a sandboxed isolate beside it; shell-level code runs in the microVM.
5. **Owner files live in versioned per-owner object storage** behind a
   trusted file API. A Firecracker microVM sandbox (E2B first, self-hostable
   later) is woken only for `bash`; its disk is a warm cache.
6. **The trusted side is TypeScript on Workers.** The Python logic is ported,
   never imported, and its tests become acceptance tests. The new stack is
   built beside production and moved to in one cutover window after a full
   backup (Direction 5); the old paths are deleted in that change.

## What it supersedes

- **ADR-022**, gVisor boxes on the droplet: replaced by the Firecracker
  microVM sandbox.
- **The single-droplet topology**: `openspec/changes/target-architecture`
  D10 (one cell on one host) and its deferred warm standby. There is no host
  of ours.
- **The box storage model** in `openspec/changes/owner-daemon-in-box` (one
  XFS image per owner on the host's block storage): replaced by object storage
  with the sandbox disk as a cache.
- **Broker-held per-owner SQLite** as the store technology. ADR-025's
  placement principle (the ledger and conversations live outside the box)
  stands; their home is the owner's object.
- **The router and broker serving design**: the Python router, broker
  process, owner channel and forwarded box serving become edge Workers, owner
  objects and the gateway.
- **ADR-030's owner-bound channels** were proposed only on an unmerged branch.
  They are not adopted.

## What stands

- ADR-023's principle: the core loop runs outside every box.
- ADR-024, ADR-025 (placement), ADR-026, ADR-027, ADR-028, ADR-029 and
  ADR-031, and ADR-030's split rule (owner code in the sandbox, trusted code
  outside).
- The model sees four tools plus `ta`.
- Wakes are ID-only doorbells. Unknown work is never replayed. Status comes
  only from receipts.

## Consequences

- No new feature lands on the retiring stack. Its deploy stays safe until the
  cutover window.
- Registry row S36 reads drifted until the cutover deletes the Python broker
  path.
- The first platform slice lands the `platform/` workspace and `OwnerDO` with
  the ported revision primitive. Nothing is deployed and nothing is replaced
  yet.
- The decision changes if measured Durable Object availability is worse than a
  two-region design we could run, the trusted side costs more than about $0.25
  per owner a month, an owner needs more than 10 GB of trusted state or 1,000
  requests a second, compliance requires customer-held keys, or E2B's p95
  resume exceeds 1.5 s.
- Facts read secondhand (E2B resume and terms, Durable Object pricing and
  limits, regional availability, Dynamic Workers and Facets) are verified
  before each dependent slice commits.

## Sources

- https://claude.ai/artifact/L28AqU1pMJCSvV6b6m1icy (plan of record)
- https://www.anthropic.com/engineering/managed-agents
- https://platform.claude.com/docs/en/managed-agents/vaults
- https://raw.githubusercontent.com/cloudflare/agents/main/README.md
- pi 1.4.0 and pi-durable 1.4.0 (npm `@earendil-works/pi-coding-agent`,
  `@earendil-works/pi-durable`)
- Cloudflare docs repo (`cloudflare/cloudflare-docs`, 2026-10-27): Durable
  Objects limits, pricing, lifecycle, in-memory state, alarms, data location;
  Workers OAuth Provider v1; Agents harness for pi
- https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/docs/specification/2026-07-28/changelog.mdx
- https://github.com/e2b-dev/infra
- https://e2b.dev/blog/how-manus-uses-e2b-to-provide-agents-with-virtual-computers
- https://github.com/NVIDIA/OpenShell
- Firecracker `SPECIFICATION.md`, `docs/jailer.md`; gVisor
  `g3doc/architecture_guide/security.md`
- `openspec/changes/target-architecture/evidence.md`
