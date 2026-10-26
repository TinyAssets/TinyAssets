# The owner's daemon runs in the owner's box (C6, built correctly)

## Why

The founder ruled on 2026-10-16: C6 does not go live until it is built in the
clean shape. The research report "Clean per user instance shape" (twelve
production systems plus AWS, Microsoft and WorkOS tenancy guidance) finds that
every system with a per-user instance works the same way. A thin shared door
authenticates the caller, maps the identity to an instance, forwards the
request, and holds the secrets and the wake clock. It never reads tenant data.
Everything that touches a tenant's data runs inside that tenant's instance.

Main does not have that shape. C6 made the box a tool sandbox, while the agent
loop, history, schedules, pause and every app/MCP handler stayed in one shared
daemon. That daemon reads every owner's data through per-center host
directories (main shape audit, 2026-10-16). Every cross-owner finding in four
review rounds came from that gap. C6's own design already puts the loop in the
box (P3), but P3 was never built.

## What changes

- **In the box: the owner's daemon.** The same TinyAssets codebase runs inside
  each owner's box as a single-owner process. Its data root is the owner's box
  tree, and it holds only that owner's centers, history, schedules, pause,
  pending requests, runs, wiki, files and databases. It serves that owner's
  app API and MCP handlers over a socket. Within a box there is no cross-owner
  code, because no other owner exists there.
- **Shared, and nothing else** (the research's six responsibilities):
  - identity (WorkOS sign-in, MCP OAuth, `/api` keys);
  - the owner → box router, which forwards to the box socket with a per-box
    secret and wakes a sleeping box;
  - billing (Stripe webhooks; the plan tier is pushed to the box);
  - the credential and egress proxy (the broker; boxes hold no raw secrets);
  - a next-wake index of `(owner_id, next_wake_at)`;
  - published commons data, copied out at publish time.
- **Lifecycle:** a box sleeps when idle. It wakes on an inbound request or at
  its next-wake time, which keeps Direction 8 with no host attention. That is
  mandatory: on one 8 GB host, with a daemon peaking near 1.4 GiB, always-on
  boxes do not fit.
- **One cutover:** the migration builds each owner's data root in their box
  from today's host data and splits the shared root databases by owner, after
  a full verified backup. After it, the shared process holds no host path to
  any owner's data.
- **Deleted:** the per-center host directories; the daemon-side loop and the
  four-tool exec bridge (`box_tools` → `start_exec`); the `file_binding`
  owner-file routing in shared code; cross-owner filters and guards in shared
  handlers; and the multi-owner scheduler loop.

## Impact

- Floor: cross-user, credential custody, storage, migration, money. This is
  one cutover and one migration (Direction 5). It supersedes #5168, #5167 and
  #5169, and it completes C6 P3, P5 and P6 in the clean shape.
- C12 rebases onto this: knowledge files live in the owner's box root.
- Nothing deploys until the cutover is rehearsed on the private production
  copy.
