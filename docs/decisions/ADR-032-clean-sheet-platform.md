# ADR-032: The Clean-Sheet Platform

## Status

Accepted (founder, 2026-10-27). Supersedes ADR-022. Amends ADR-019, ADR-023
and ADR-030. Supersedes the single-droplet topology, the box storage model,
broker-held SQLite as the trusted store technology (ADR-025's), and the router
and broker serving design, as listed below. Build order: `openspec/changes/clean-sheet-platform/`.

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
   schedule on the Agents SDK Lifecycle job queue, which owns the object's
   single alarm and PiHarness's wake-up job, and hibernating app WebSockets.
   The runtime keeps each object's storage private. The object's code has no
   way to another owner's object: its Worker binds nothing but service bindings
   to Workers an allow-list names with the binding kinds each may hold, none
   reaching another Worker or a shared store beyond its purpose (today the
   files Worker, with one bucket; the gateway with its slice), checked against
   the bound Worker's own
   config; the `disable_ctx_exports` flag keeps the runtime from handing it its
   own namespaces as `ctx.exports`; and its source never imports `env`. No
   module keeps mutable state at top level or writes through the global object,
   since objects of one class can share an isolate.
2. **Stateless edge Workers are the only binder.** Only they turn a verified
   credential (OAuth, `/api` key hash, inbound signature) into an owner, and
   reach that owner's object by `getByName(owner)`. The object serves only the
   owner it is named by (`ctx.id.name`) and refuses when it has no name. The
   caller reaches the object as a typed argument, never a request field. Cross-owner traffic goes
   through a postmaster Worker that reads the sender object's outbox.
   How the doors bind (decided from research, 2026-10-28; `platform/doors`):
   - **Our own authorization server,** issuer `https://tinyassets.io`, on Workers
     OAuth Provider 1.2.x in the doors Worker, so validating a token spends no
     service call. Client ID Metadata Documents are on; dynamic registration is
     only a flagged, deprecated fallback. WorkOS AuthKit stays the owner's
     upstream sign-in (owner ids stay its `sub`; any later issuer's subjects are
     namespaced by issuer), always with `prompt=login`, so a later vendor change
     never breaks a connector.
   - **Consent is never remembered,** and an MCP grant's props hold the original
     sign-in's time (`authenticated_at`), which no refresh moves. A revoke goes to
     the owner's object first, then revokes the provider's grants.
   - **The MCP door is SDK v2's stateless `createMcpHandler`,** serving 2026-07-28
     and, for MCP SDK 1.x clients, 2025-11-25 from one factory; one external tool
     per outside-callable owner door, every hint explicit, and never the model's own
     tools.
   - **The owner directory is one KV namespace and only a routing hint** (key ids
     and other credential hashes to owners, and the fail-closed `control` record);
     every revoke is enforced in the object, so KV's lag only routes a dead
     credential to a refusal. Owner admission is the object's own row.
   - **The session cookie is SameSite=Strict** and forwarded only on the owner
     app's same-origin JSON requests; outside credentials never reach the owner
     surface.
3. **A gateway Worker holds no owner state and opens one owner's sealed
   credential per call,** meters usage, and swaps placeholder credentials on
   sandbox egress. No credential sits in the loop or the sandbox.
4. **pi-durable is the loop, hosted in the owner's object, on its own
   mechanisms** (source-checked against pi-durable 1.4.0 and agents 0.31.0,
   2026-10-28). Where pi-durable has a native mechanism it is used and ours
   goes:
   - its hook names: `beforeRequest`, `afterResponse`, `onYield`,
     `beforeTool`, `afterTool`, `afterTools`, `beforeCompact`;
   - its transcript and turn-journal store, in the owner's object;
   - one pi `ExecutionEnv` for tool plumbing: its file methods call the file
     service and its `exec` wakes the microVM, checked with pi's conformance
     tests; `ta` is a trusted pi-durable tool;
   - `replay: "unsafe"` by default, so a call a crash cut off comes back
     interrupted and is never rerun; only pure reads are marked safe;
   - its compaction, steering, subagents and `sleepUntil` wakes;
   - the Agent Skills format for skills;
   - deferred MCP tools through `tool_search` and code mode, so the model
     still sees four tools plus `ta`;
   - `pi.agent` for per-conversation settings.

   Kept differences, each with its one reason:
   - User hooks run out of process: pi extensions run with full permissions,
     and the owner's object holds the ledger and credentials.
   - The model gateway, plugged in as a pi-ai custom provider: multi-tenant
     receipts and spend caps; Cloudflare's AI Gateway path routes only four
     API types.
   - The ledger and mailbox identity: pi has no cross-agent authorship.
   - Owner policy feeds pi's settings: only the owner may write policy.
   - Our own manifest for executable package parts: pi-durable cannot load
     pi-coding-agent extensions, which target Node and pi's in-process
     extension API.

   **User hooks run in a separately deployed Worker's Durable Object, which
   loads each owner's hook code with the Worker Loader (Dynamic Workers, open
   beta),** not in a Dynamic Worker inside the owner's object. pi measured on Cloudflare
   (October 2026) that code computing for 10 s in a Dynamic Worker loaded by
   the session object delayed every other request to that object by the full
   10 s; in a separate Worker's object the delay was about 50 ms. The owner's
   object calls a hook over RPC, with JSON in and out and a timeout. A
   `beforeTool` hook that times out or errors blocks the call. Decisions that
   must survive a crash are held by `api.memo` on the trusted side.
   Declarative package parts (skills, prompts, model routing, tool schemas,
   settings) load into the loop as data; shell-level code runs in the microVM.

   **pi is pinned to 1.4.x:** agents supports one pi minor, and pi's
   unreleased main already removes `retryTask`, which agents 0.31.0 calls.
   Pi 1.4's storage migration cannot be rolled back.
5. **Owner files live in versioned per-owner object storage** behind a
   trusted file API. A Firecracker microVM sandbox (E2B first, self-hostable
   later) is woken only for `bash`; its disk is a warm cache. How they are
   stored (decided from research, 2026-10-28):
   - **Bytes:** one R2 bucket of immutable 4 MiB chunks named by SHA-256,
     under `o/<ns>/c/<sha256>`, where `ns` is a random 128-bit value made on
     first use and held only in the owner's object. A path never becomes a key:
     R2 NFC-normalizes keys, and uploads are verbatim (Fact 9). The bucket is
     shared by all owners; its bytes are content, reachable only through an
     owner's manifest and namespace.
   - **Manifest:** in the owner's object: paths, versions, history, chunk
     references, quotas, the change feed and the sync lease, with the board's
     compare-and-swap and never-repeating seq (a second store of the same rules,
     declared in the revision-primitive guard). Files of 4 KiB or less are
     inline.
   - **The files Worker** holds the only bucket binding, takes hex and bytes
     only, never sees a path or chooses an owner, and verifies every put with
     R2's `put` SHA-256 check (not S3 checksum headers, which R2 lists as
     composite only). A call moves at most two chunks' bytes, and a request may
     make at most 32 service-binding calls. The binder, the trust root, may
     stream chunks to it with a namespace read from the owner's object.
   - **Versions and GC:** each live path's last 10 versions, and its versions
     of the last 30 days up to 20 in all, as owner policy; every entry and kept
     history row holds its content, in every root; chunks are
     reference-counted, recorded before any byte is sent, counted stored only
     once the files Worker accepted them, and deleted after a 24 h grace on the
     object's one schedule, so GC never lists the bucket. Quotas, also owner
     policy, refuse loudly, but never a change that only frees; the policy keeps
     `max_entries` times the larger of `keep_versions` and `keep_recent` within
     `max_history`, and the owner may purge a path's history.
   - **Sync:** a lease of a random id (the claim token) and a rising epoch
     (fencing). API writes never take it; when both sides changed a file the
     API's version stays and the sandbox's is saved as a reported conflict copy.
   - **pi's `ExecutionEnv`** maps onto the manifest and the files Worker and
     passes pi-durable 1.4.0's own conformance suite but for `exec`.
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
- E2B resume and terms, and Durable Object regional availability, are
  verified before each dependent slice commits. Durable Object pricing and
  limits, Dynamic Workers and Facets were verified from Cloudflare's pages on
  2026-10-28.

## Sources

- https://claude.ai/artifact/L28AqU1pMJCSvV6b6m1icy (plan of record)
- https://www.anthropic.com/engineering/managed-agents
- https://platform.claude.com/docs/en/managed-agents/vaults
- https://raw.githubusercontent.com/cloudflare/agents/main/README.md
- pi 1.4.0 and pi-durable 1.4.0 (npm `@earendil-works/pi-coding-agent`,
  `@earendil-works/pi-durable`), and Cloudflare agents 0.31.0
  (`agents/harness/pi`), source-checked 2026-10-28
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
