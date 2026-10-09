---
severity: P2
title: Thin-loop box tools fire no before_tool/after_tool extension hooks
filed: '2026-10-06'
summary: 'On the thin loop, read/write/edit/bash are served by the box, not the engine, so the engine middleware that fires before_tool/after_tool never sees them; engine-routed tools in the same turn still fire those hooks, but in the platform jail rather than the box'
---

# Tool-level extension hooks split across two places on the thin loop

Found while building `remote-box-extension-runtime`. Before that change, remote
boxes ran no extensions at all. They now run tools, commands and turn lifecycle
hooks (`input`, `turn_start`, `context`, `turn_end`) in the box.

The gap that remains is in tool-level events:

- `ExtensionHookEvents` (`tinyassets/engine_mcp_server.py`) fires
  `before_tool`/`after_tool` only around engine MCP tool calls, and runs the hook
  through the engine's local jail `bash`.
- `LoopToolSession.call` (`tinyassets/agent_loop/tool_session.py`) serves box
  tools and owner reads without calling it. No tool-level hook fires for those
  tools.

Resolution needs one owner of the event per call. One option is for the loop
session to fire both events for every call in the box and for the engine route to
mark loop sessions so its middleware skips them. That marker has to be signed into
the launch route, because the route is authority-bearing. It needs its own spec.

## Turn hooks run with the box's integrity (review 2026-10-06)

On the thin loop, turn lifecycle hooks (`input`, `turn_start`, `context`,
`turn_end`) now run inside the owner's box, where the agent's own `bash` also
runs. They lose no enforcement they had before, since remote boxes ran no
extensions at all, but they are **best-effort and model-influenceable**: box
code can skip a hook, fake one, or alter its output. An owner who wants a hook
as a tamper-proof audit log of their agent does not get one on the thin loop.
Tamper-proof hooks would need a platform-side route (the same signed-marker
work as the tool-hook gap above).

Delivery size: each remote bash call carries the active extension bundle
(at most 4 MiB of blobs, about 6 MB on stdin). A real box provider with a
smaller stdin limit would make every bash call in that owner's box fail while
an extension is active. Check real providers' limits before calling this
shipped; caching the bundle by digest in the box would remove the repeat
transfer.
