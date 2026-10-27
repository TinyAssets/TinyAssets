# ADR-030: The Box Holds Only Tools, User Code and Owner Files

## Status

Accepted (founder, 2026-10-26). The deferral in #5324 is withdrawn: on
2026-10-27T01:23Z the founder said to "review the doc and build only the
correct architecture", and the plan records this decision as A9.

How owner serving is isolated was decided by the founder on 2026-10-27 (the
owner-bound channels below), after the research recorded under Evidence.

Amends ADR-019 and ADR-022 on what a box runs, ADR-023 on how the loop worker
reaches the broker, and ADR-025 on who records messages.

## Date

2026-10-26 (isolation decided 2026-10-27)

## Context

Today each box starts `tinyassets.owner_daemon`, which builds the whole app
server (`universe_server.create_streamable_http_app()`). Measured in gVisor
on 2026-10-26, that costs about a 3.7 s cold start and 95 to 145 MiB per
awake box. ADR-023 moves the loop out and ADR-025 moves messaging out. What
is left in the box is serving the owner's MCP and app requests, and most of
that is reading and writing owner files.

Once serving leaves the box, something on the trusted side must keep owners
apart. Today the router runs as one uid for every owner and names the owner
as a `principal` field on the broker's owner channel (`broker/client.py`),
and the broker accepts any principal from that uid (`broker/process.py`
roles, `broker/server.py`). So any handler bug can reach another owner's
stores. The box channel is already bound to one owner: boxhostd's per-uid
relay opens `BOX_CHANNEL` (`boxes/relay.py`) and the broker fixes the
connection to that owner, which it can't rebind.

## Decision

- **The box runs a slim harness:** the four tools, user customizations at
  their extension points, vendor CLIs (paid tier) and owner files. It never
  builds the app server.
- **Owner requests are served on the trusted side.** The router
  authenticates and routes. Owner workers and the loop worker do the
  serving, and touch owner files only through `BoxProvider` calls.
- **The split rule:** code that must run as the owner (user code, shells,
  CLIs) stays in the box. Trusted code that only reads or writes owner data
  runs outside and calls in.
- **Owners are kept apart by owner-bound broker channels and one-owner
  workers.**
  - A *binder* opens a broker connection bound to one owner and one verified
    caller. The broker fixes both and never lets the connection rebind, as
    it does for the box channel. The binder passes the connection by file
    descriptor to a worker.
  - **A worker serves one owner for its whole life.** It is forked from a
    warm, pre-imported parent and exits on idle rather than serve a second
    owner. Each worker slot runs under its own uid, from a range that is
    absent from the broker's role map, as boxes have their own range. So a
    worker can't open a broker connection of its own, and can't read a
    sibling's memory, attach to it, signal it or copy its descriptors,
    because those need the same uid. Workers also set no-new-privileges and
    non-dumpable, and seccomp denies ptrace, `process_vm_readv`/`writev` and
    `pidfd_getfd`. The parent runs no handler code and never reads a bound
    connection; it only forks, drops to the slot's uid and hands the
    descriptor over. Proof runs under `scripts/linux_oracle.py`.
  - Below the binder, no op names the owner or the caller as a field.
  - Within one owner's worker, separating callers (for example two visitors
    in a published room, each with private rows) stays an application
    check: the broker scopes each request to the caller its bound channel
    carries.
- **Who binds:**
  - The router binds for owner MCP, app and `/api` requests.
  - boxhostd binds box channels, as today.
  - The broker binds for loop-worker turns it dispatches (wakes, scheduled
    turns, resumed held turns), from its own ledger. It hands one end of a
    bound socket pair to the loop-worker parent over a role-mapped control
    socket. So the broker dispatches turns, which amends ADR-023's "the
    broker verifies the worker's per-turn owner".
  - Ops that stay on the router's all-owner channel, because they precede
    binding or span owners: `FENCE`, `INBOUND_VERIFY`, `ADMIT_OWNER`,
    `OWNER_ADMISSION`, `OWNER_IDENTITY`, `DELETION_JOB`, `BILLING_PLAN` and
    `OWNER_BOX_WAKE`.
  - Every op that names an owner moves to the bound channel: `OPEN` (model
    and connector streams), `STATUS`, `LEDGER_QUERY`, `MODEL_SOURCE`,
    `USAGE`, `HTTP_CONNECT`, `CAPABILITY`, `CONNECTION_CATALOG`,
    `DISCONNECT`, `BROWSER_VAULT`, `OWNER_VOLUME`, `OWNER_POLICY`,
    `MCP_EVENTS`, `OUTSIDE_AGENTS`, `ROOM_POST`, `MAILBOX_RECEIPT` and
    `COLLECTION`. The three owner-channel clients today (`BrokerClient`,
    `owner_identities._owner_request`, `mcp_events.request`) become one.
  - Per-owner state still held in shared stores moves to the owner's broker
    store with this slice: MCP event subscriptions (`mcp-events.db` is
    broker-wide today). The router's own stores, owner sessions and API
    keys, move into broker custody at the later step below.
- **Cross-user requests.** A bound channel carries both the store owner and
  the verified caller: a visitor in a published app's room is bound to the
  publisher's store with the visitor as caller, and an outside agent is
  bound to its owner's store with its `outside_origin` as caller. Delivery
  between two owners is a broker op whose two owners come from the bound
  channel and the published directory, never from a request field; its
  slice specifies it.
- **The trust root.** Binders choose owners in application code. Keep them
  small: authentication, identity, routing and dispatch. Later, the broker
  verifies the end-user credential itself (owner sessions and API keys move
  into broker custody), so the router's binding comes from the credential.
  ADR-022's residual (the daemon-writable owner-session table) already owes
  this move.
- **Per-owner trusted state is one store per owner** in the broker
  (ADR-025's `.broker/accounts/<sha256(owner)>/`). Platform stores on the
  owner volume move there, each backup-first with its migration in the same
  PR. Owner files are reached only through `BoxProvider`.
- **Not chosen:**
  - A long-lived trusted server per owner, started on its own and held
    awake like a box. Each would import the whole server (about 100 MiB
    measured outside gVisor). On one `s-4vcpu-8gb` droplet (ADR-022 G3 and
    the memory limits in `deploy/compose.yml`) that caps how many owners
    can be served at once. The chosen workers are short-lived forks from one
    warm parent that share its pages copy-on-write (`gc.freeze()` before
    forking keeps them shared), so each owner costs only its own writes.
    The slice measures that per-worker figure before sizing the pool.
  - One shared server that names the owner as a field. That is today's
    shape, and it is the bug class behind the cross-tenant leaks listed
    under Evidence.

## Consequences

- Cold start and awake memory drop to the harness's size. ADR-026's class
  reservations are measured again (registry row N12, metering), and
  metering adds per-owner worker time and memory.
- `owner_daemon`'s app-server path is deleted, and with it the box-ingress
  websocket pass-through.
- Requests that don't touch owner files (status, messaging, rooms) never
  wake a box.
- A handler bug or a compromised worker reaches only the one owner that
  worker was bound to.
- The trusted-front doors (`mailbox_door`, `room_door`, the board) move from
  a `principal` field to the bound channel. Their logic is unchanged.
- Registry rows S30 and S34 ("reached only by the router's owner-channel
  ops") read "reached only through a bound channel" once that lands.
- New registry row S36 holds the shape and its guard.
- Build order: `openspec/changes/device-first-build/` (the "Owner-bound
  serving" slice, before S2a).

## Evidence

Market survey, read 2026-10-27; unless dated, these are vendor docs as read
that day.

- **Shared control plane, per-session sandbox, loop and credentials outside:**
  - Anthropic Managed Agents: https://www.anthropic.com/engineering/managed-agents
    (2026-04-08), and https://platform.claude.com/docs/en/managed-agents/overview
  - Claude Code on the web: https://code.claude.com/docs/en/claude-code-on-the-web
  - Cursor cloud agents: https://cursor.com/docs/cloud-agent/security
  - Devin: https://docs.devin.ai/enterprise/vpc/overview
  - Codex cloud: https://developers.openai.com/codex/cloud/environments
  - Manus: https://manus.im/blog/manus-sandbox
- **Per-tenant compute holding its own state:**
  - Cloudflare Durable Objects (an actor and private SQLite per object):
    https://developers.cloudflare.com/durable-objects/platform/limits and
    https://blog.cloudflare.com/sqlite-in-durable-objects (2024-09-26)
  - Fly.io, one app per customer:
    https://fly.io/docs/machines/guides-examples/one-app-per-user-why/
  - Turso, one database per tenant: https://turso.tech/blog/multi-tenancy-at-scale
- **Credentials scoped at a gateway or proxy:**
  - Anthropic, as above.
  - AWS token vending:
    https://aws.amazon.com/blogs/security/how-to-implement-saas-tenant-isolation-with-abac-and-aws-iam
- **NVIDIA OpenShell** (https://github.com/NVIDIA/OpenShell, Apache-2.0,
  v0.1.6 on 2026-10-27):
  - Not adopted, for these reasons:
    - it runs the agent loop inside the sandbox (against ADR-023);
    - it runs a trusted supervisor per sandbox;
    - it keeps tenants apart by role checks in a shared gateway, control
      plane only (its RFC 0011);
    - its shared-kernel Docker path is weaker than gVisor and needs Linux
      6.2's Landlock, where production runs 6.1;
    - on a KVM host its libkrun microVM driver has no snapshot and starts
      5 to 30 times slower than Firecracker (ADR-022's allowed second
      driver);
    - its interfaces still move weekly after a breaking 0.1 rewrite.
  - It is not declined for lacking gVisor: its Kubernetes path can use a
    gVisor runtime class. Its Landlock baseline is unavailable under runsc.
  - Its placeholder credentials swapped at the egress proxy, and its
    revisioned network policy, are recorded in `ideas/PIPELINE.md`.
  - Read from a clone of the repo; the version and RFC references are the
    repo's own `docs/` and `rfc/0011-multi-player-design/` as of that date.
- **Cross-tenant leaks from a missing owner check in a shared service:**
  - Asana MCP server (2025):
    https://www.bleepingcomputer.com/news/security/asana-warns-mcp-ai-feature-exposed-customer-data-to-other-orgs/
  - OpenAI, 2023-03-20 (a shared connection served to the wrong user):
    https://openai.com/index/march-20-chatgpt-outage/
  - Langflow and Twenty IDOR advisories (2026), from secondary sources.
- **The TinyAssets side**, read on main 8b415f569:
  - the owner channel names the owner as a field: `broker/client.py`
    (the request's `principal`), `broker/process.py` (roles by uid);
  - the box channel binds the owner: `boxes/relay.py`, `broker/server.py`
    `BOX_CHANNEL`;
  - capacity: one `s-4vcpu-8gb` droplet (ADR-022).
