# ADR-030: The Box Holds Only Tools, User Code and Owner Files

## Status

Accepted (founder, 2026-10-26; the deferral in #5324 withdrawn by the
founder's 2026-10-27 instruction to build the plan of record). Amends ADR-019 and ADR-022 on what a box
runs.

## Date

2026-10-26

## Context

Today each box starts `tinyassets.owner_daemon`, which builds the whole app
server (`universe_server.create_streamable_http_app()`). Measured in gVisor
on 2026-10-26, that costs about a 3.7 s cold start and 95 to 145 MiB per
awake box. ADR-023 moves the loop out and ADR-025 moves messaging out. What
is left in the box is serving the owner's MCP and app requests, and most of
that is reading and writing owner files.

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

## Consequences

- Cold start and awake memory drop to the harness's size. ADR-026's class
  reservations are measured again (registry row N12, metering).
- `owner_daemon`'s app-server path is deleted, and with it the box-ingress
  websocket pass-through.
- Requests that don't touch owner files (status, messaging, rooms) never
  wake a box.
- How owner isolation holds once serving is on the trusted side is
  unresolved; `docs/concerns/2026-10-27-no-slice-moves-serving-out-of-the-box.md`
  tracks it.
- Build order: `openspec/changes/device-first-build/`.
