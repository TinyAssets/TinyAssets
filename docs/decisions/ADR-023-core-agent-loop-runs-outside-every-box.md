# ADR-023: The Core Agent Loop Runs Outside Every Box

## Status

Proposed. Would amend ADR-019 (where the loop runs) and ADR-022 (its P3 row:
"loop in the box"). Deferred (founder, 2026-10-26): moving serving out means
relocating about 113 owner stores and about 66,000 lines of serving, and
one shared trusted server would make owner isolation an app check again
(Direction 5). Revisit with metering receipts (N12) at real user counts.

## Date

2026-10-26

## Context

- **Today (corrected 2026-10-26):** the loop already runs inside
  each owner's gVisor box. Since the C6 cutover (#5171, #5182, #5227) the
  box starts `tinyassets.owner_daemon`, which builds the whole app server;
  the converse and workflow handlers run there, `ExecutionOwner` is a
  thread in that process, and the four tools run locally
  (`agent_loop/owner_tools.py`). Model calls leave through the box relay to
  the broker. ADR-019 put the loop in the box so owner-steered code would
  stop running in the shared daemon.
- **The cost of that placement:** a turn spends most of its time waiting on
  the model, so a loop inside the box holds the box awake for the whole
  turn. In
  the 10,000-user model, heavy free users (OpenRouter credit plus free
  sources, about 150 turns a day) need about 27 GiB of awake box memory at
  peak with the loop in the box, and about 2.4 GiB with it outside.
- **Industry practice:** Anthropic Managed Agents, the OpenAI Agents SDK,
  Cursor, Devin and OpenHands all keep the loop outside the tool sandbox.

## Decision

- **Where the loop runs:** a fixed core loop runs in a dedicated loop-worker
  pool, the execution owner of target-architecture D11. That pool is
  separate from the shared daemon and from every box.
- **What a worker can reach:** it holds no credentials; the broker makes
  every model call, verifying the worker's per-turn owner and generation.
  It reaches owner files only through `BoxProvider`, and the network only
  through the broker.
- **How it reaches the box:** a tool door served by the box for the four
  tools and declared extension points, with an operation id and handle
  generation on each call. This would reverse C6's "no execution
  forwarding" stance.
- **Where it runs:** its own deploy unit (`loop-worker`) under its own OS
  user, unprivileged. The turn journal, leases and fences move from the
  owner mount to a trusted per-owner area, because a lock inside gVisor
  cannot fence a writer outside it.
- **Turn state:** the generation-fenced turn journal is the session log. A
  worker that dies leaves its turn held, and a fresh worker resumes it
  without replay: the next model round starts from the journal, and a tool
  call whose outcome was not recorded stays unknown rather than re-run.
- **User customization:** skills, tools, hooks, extensions and packages run
  inside the owner's box, called at declared extension points. That is how
  users customize the harness (Direction 1); the loop's own control flow is
  not user-editable.
- **Subscription CLIs** bring their own loop. On TinyAssets boxes they run
  for the paid tier only (founder decision, 2026-10-26); otherwise they run
  on the owner's paired device (ADR-024) or the owner's own host.

## Consequences

- If accepted, ADR-022's P3 loop move, which C6 carried out, is reversed:
  the loop leaves the box for the loop-worker pool, never for the shared
  daemon. The loop worker would be built after owner serving leaves the box
  (ADR-030), since while the converse handler runs in the box the box stays
  awake through every model call wherever the loop runs. P3's other duties
  land elsewhere:
  - **Owner-session minting in broker custody** (ADR-019's P1 residual, the
    daemon-writable owner-session table) stays owed as its own step, unchanged
    in scope, under S8.
  - **The broker in-box endpoint for CLI packages** is not built: ADR-031 has
    hosted CLIs sign in through the vendor's own flow, so no token is leased.
  - **The cross-user floor completes, and S8 reads converged,** when the
    owner-session move lands and the loop runs in the loop-worker pool.
- A box wakes only for tool and customization calls.
- The box harness shrinks to tools, user code and owner files (ADR-030).
- A new registry row (proposed S35), added with its guard in the loop-worker
  slice, will hold the placement: no loop code imported by the box harness
  or the shared daemon.
- Build order: `openspec/changes/device-first-build/`.
