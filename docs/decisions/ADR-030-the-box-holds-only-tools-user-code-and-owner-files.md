# ADR-030: The Box Holds Only Tools, User Code and Owner Files

## Status

Accepted (founder, 2026-10-26). The deferral in #5324 is withdrawn: on
2026-10-27T01:23Z the founder said to "review the doc and build only the
correct architecture", and the plan records this decision as A9. Amends ADR-019
and ADR-022 on what a box runs.

How owner serving is isolated: decided by the founder on 2026-10-27 (the
hybrid below), after the research recorded under Evidence.

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
as a `principal` parameter on the broker's owner channel
(`broker/client.py`, `broker/process.py` roles), so any handler bug can read
another owner's stores. The box channel already binds one owner to a
connection: boxhostd's per-uid relay opens `BOX_CHANNEL` and the broker fixes
the connection to that owner.

## Decision

- **The box runs a slim harness:** the four tools, user customizations at
  their extension points, vendor CLIs (paid tier) and owner files. It never
  builds the app server.
- **Owner requests are served on the trusted side.** The router and loop
  worker answer owner MCP, app and `/api` requests, and touch owner files
  only through `BoxProvider` calls.
- **The split rule:** code that must run as the owner (user code, shells,
  CLIs) stays in the box. Trusted code that only reads or writes owner data
  runs outside and calls in.
- **Owners are kept apart by owner-bound broker channels.**
  - The shared router authenticates the caller and routes. It holds no
    handler code.
  - For each request it opens a broker connection bound to that one owner,
    as the box channel already is, and passes it to a shared worker by file
    descriptor.
  - The worker that serves the request runs under a uid with no broker role
    of its own, so it can reach only the owner whose connection it holds.
    The owner is never a request field below the router.
  - The router's choice of owner is the one application-level trust root. It
    is kept small (authentication, identity, routing).
  - Later step: the broker verifies the end-user credential itself (owner
    sessions and API keys move into broker custody), so the owner a
    connection is bound to comes from the credential. ADR-022's residual
    (the daemon-writable owner-session table) already owes this move.
- **Per-owner trusted state stays one store per owner** in the broker
  (ADR-025's `.broker/accounts/<sha256(owner)>/`). Platform stores on the
  owner volume move there; owner files are reached only through
  `BoxProvider`.
- **Not chosen:**
  - A trusted server process per owner. No market leader runs one. On the
    one droplet only about 20 to 30 could be awake at once, and the router's
    choice of owner would still be an application check.
  - One shared server that names the owner as a parameter. That is today's
    shape, and the bug class behind the cross-tenant leaks listed under
    Evidence.

## Consequences

- Cold start and awake memory drop to the harness's size. ADR-026's class
  reservations are measured again (registry row N12, metering).
- `owner_daemon`'s app-server path is deleted, and with it the box-ingress
  websocket pass-through.
- Requests that don't touch owner files (status, messaging, rooms) never
  wake a box.
- A handler bug or a compromised worker reaches only the owners whose
  requests it holds, never every owner's stores.
- The trusted-front doors (`mailbox_door`, `room_door`, the board) move from
  a `principal` parameter to the owner-bound channel. Their logic is
  unchanged.
- The loop worker (ADR-023, S2a) uses the same owner-bound channels.
- Build order: `openspec/changes/device-first-build/` (slice "Owner-bound
  serving").

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
- **One isolated store per tenant:**
  - Cloudflare Durable Objects:
    https://developers.cloudflare.com/durable-objects/platform/limits and
    https://blog.cloudflare.com/sqlite-in-durable-objects (2024-09-26)
  - Fly.io: https://fly.io/docs/machines/guides-examples/one-app-per-user-why/
  - Turso: https://turso.tech/blog/multi-tenancy-at-scale
- **Credentials scoped at a gateway or proxy:**
  - Anthropic, as above.
  - AWS token vending:
    https://aws.amazon.com/blogs/security/how-to-implement-saas-tenant-isolation-with-abac-and-aws-iam
- **No surveyed leader documents a trusted server process per user.**
- **Cross-tenant leaks from a missing owner check in a shared service:**
  - Asana MCP server:
    https://www.bleepingcomputer.com/news/security/asana-warns-mcp-ai-feature-exposed-customer-data-to-other-orgs/
  - Langflow CVE-2026-55255: https://blog.securelayer7.net/cve-2026-55255-langflow-idor/
  - Twenty CVE-2026-55583: https://www.rapid7.com/db/vulnerabilities/cve-2026-55583/
  - OpenAI, 2023-03-20: https://openai.com/index/march-20-chatgpt-outage/
- **The TinyAssets side**, read on main 8b415f569:
  - the owner channel names the owner as a parameter: `broker/client.py`
    (the request's `principal`), `broker/process.py` (roles by uid);
  - the box channel binds the owner: `boxes/relay.py`, `broker/server.py`
    `BOX_CHANNEL`;
  - capacity: one `s-4vcpu-8gb` droplet, about 3 GiB for awake per-owner
    memory.
