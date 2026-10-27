---
severity: P1
title: No build slice moves owner serving out of the box (ADR-030)
filed: '2026-10-27'
summary: ADR-030 serves owner MCP, app and /api requests on the trusted side, but no slice of the device-first plan moves serving out of the box, and a shared trusted server would turn owner isolation back into an app check.
---

# No build slice moves owner serving out of the box

**Filed:** 2026-10-27
**Verified:** 2026-10-27, on main da3e21f04
**Severity:** P1
**Requirement IDs:** A8, A9, ADR-023, ADR-030, Direction 5.
**Status:** founder decision at S2a.

## Facts (checked on main, 2026-10-27)

- Each box runs the whole app server. `tinyassets/owner_daemon.py` `create_app`
  builds `universe_server.create_streamable_http_app()`. The plan measured this
  in gVisor at 95 to 145 MiB awake (ADR-030) and a 3.7 s cold start.
- The served code is large: once the app is built, the box has loaded 212
  `tinyassets` modules, about 117,000 lines (84,000 at import). Those modules
  hold 65 SQLite path literals, about 62 file names, some of them broker-side
  stores. #5324 cited about 66,000 lines and 113 stores; its method is not
  recorded.
- Only boxhostd can see an owner volume. `boxes/host_service.py` unshares a
  private mount namespace before serving, so only that namespace and its
  `runsc` children see attachments.
- SQLite locking across the gVisor boundary is unsafe: a lock taken inside
  gVisor cannot fence a writer outside it (ADR-023 moves the turn journal off
  the owner mount for this reason).
- One trusted server shared across owners would make owner isolation an app
  check again. Direction 5 says the OS now enforces it.

## The gap in the plan

The plan of record is "TinyAssets — Agents Everywhere, Device-First:
Architecture Plan". Its build slice S2a (loop worker) deletes "the loop's home
in the shared daemon". But since the C6 cutover the loop runs in the box, not
the shared daemon. And no slice moves serving out of the box, which ADR-030
(plan decision A9: owner MCP, app and `/api` requests served on the trusted
side) requires.

## Options

1. **A per-owner trusted serving process outside the box.** It runs under its
   own unprivileged uid, reaches only that owner's broker-held stores, and
   reaches that owner's files only through `BoxProvider`. Owner isolation stays
   an OS fact, the box stays slim, and the process stops on idle like a box.
2. **One shared trusted server with app-level owner checks.** This conflicts
   with Direction 5.
3. **Keep serving in the box.** This conflicts with A9 (ADR-030).

## Recommendation

Option 1, in two steps:

1. Move platform state off the owner volume into per-owner trusted stores
   (broker-held SQLite, as ADR-025 records).
2. Serve owner files through a remote `BoxProvider` driver, then delete
   `owner_daemon`'s app-server path.

Resolve by deleting this file once the founder decides and the plan carries a
slice for it.
