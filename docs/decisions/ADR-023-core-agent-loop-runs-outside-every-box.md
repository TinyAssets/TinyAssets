# ADR-023: The Core Agent Loop Runs Outside Every Box

## Status

Proposed. Would amend ADR-019 (where the loop runs) and replace C6 step P3.

## Date

2026-10-26

## Context

- **Today:** `tinyassets/agent_loop/` already runs turns as coroutines
  outside the box. Model calls go through the broker, and the four tools
  reach the box over a `BoxHandle`.
- **ADR-019** planned to move the loop into the box (C6 step P3), so
  owner-steered code would stop running in the shared daemon.
- **The cost of that move:** a turn spends most of its time waiting on the
  model, so a loop inside the box holds the box awake for the whole turn. In
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
  every model call. It reaches owner files only through `BoxProvider`, and
  the network only through the broker.
- **Turn state:** the generation-fenced turn journal is the session log. A
  worker that dies leaves its turn held, and a fresh worker resumes it
  without replay.
- **User customization:** skills, tools, hooks, extensions and packages run
  inside the owner's box, called at declared extension points. The loop's
  control flow is not user-editable.
- **Subscription CLIs** bring their own loop. On TinyAssets boxes they run
  for the paid tier only (founder decision, 2026-10-26); otherwise they run
  on the owner's paired device (ADR-024) or the owner's own host.

## Consequences

- C6 step P3 is not built.
- A box wakes only for tool and customization calls.
- The box harness shrinks to tools, user code and owner files (ADR-030).
- Registry row S35 guards the placement: no loop code imported by the box
  harness or the shared daemon.
