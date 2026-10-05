I've confirmed the main blockers in the launcher. Now I'm checking whether this branch already has a stdio MCP path or proxy-handle code that I missed.

**AGREE.** Task 1.4 can't be marked complete on this base. I found no foundation path you missed. I also found one gap your handoff doesn't cover yet.

Your findings, with evidence:

1. **The launcher only spawns the image decoder.** `deploy/role_launcher.py:291-292` sends every SPAWN to `_decoder`. Lines 310-314 refuse any `kind` other than `image-decoder`. Lines 357-360 run a fixed `execve` of `ta-decoder.py`. So there's no way to launch an exact-revision package, argv or cwd. The docstring at lines 4-6 says the same: "no arbitrary exec… Engine kinds remain refused until their complete owner-cell implementations are installed."
2. **The launcher isn't running in production.** `role_launcher.py:434-435` exits with "role launcher startup is not admitted: migration and engine-cell integration pending". `Dockerfile:396` still runs `python -m tinyassets.serve` directly, so not even the decoder cell is live. `tinyassets/role_decoder.py:19-20` refuses unless `broker_selected()`, which is just an env switch (`broker/supervisor.py:33-34`).
3. **The provider jail isn't the separate-user boundary the spec asks for.**
   - `tinyassets/providers/provider_jail.py:606-610` is bubblewrap with `--unshare-all` at the daemon's uid. Nothing sets a different uid.
   - Lines 46-48 say outright that "an MCP stdio server, a shell tool -- is a descendant inside the same namespaces." Agent code in that jail can therefore read the server's `/proc` entries, environment and argv, which the design forbids (`connect-anything-ladder/design.md:41`).
   - The jail also has no owner-scoped stdio activation path to reuse.
4. **The scoped proxy credential channel isn't built.** `per-role-uid-split/tasks.md:156` (2.6) is still open. It covers launcher-mediated broker start, routing engine egress "through its cell proxy only", and refusing the legacy per-grant proxy at uid 1001. Task 2.5 at line 133 is also open: it moves the "engine-mcp thin proxy" into the launcher client at uid 1003 and pins proxy scope at the receiver.
5. **I found no stdio MCP client to reuse.** The only `stdio` hits in `tinyassets/` (`universe_server.py:5180,5197`, `auth/middleware.py:920`) are about TinyAssets serving its own MCP over stdio, not attaching an external stdio server.

The gap to add to the blocker record: even the planned foundation won't satisfy the named-own-key requirement. `per-role-uid-split/design.md:82` gives all engine and provider children one shared uid, `1003:1003`. Its rows 556-560 move the engine MCP child, provider CLI children and the agent's own tool jail (`universe_tools.py:777`) onto that same 1003. A raw-key server in a 1003 cell would share a user identity with the agent shell it must be hidden from. That breaks `design.md:41` ("separate process, user identity and filesystem view"). The uid split needs its own design decision here, such as a separate uid per raw-key server or a user-namespace mapping. This lane shouldn't make that call.

Handoff for the blocker record, without widening this lane:
- **Depends on:** per-role-uid-split tasks 2.5, 2.6 and 2.7 (lines 133, 156, 174). These cover the generic owner-cell launch kind, the cell egress proxy with receiver-pinned scope, and enabling the launcher in production. 2.8 also has to land: the oracle proofs on the production image.
- **New foundation item:** a raw-key server identity separate from the shared 1003 cell uid.
- **Keep 1.4 unchecked** and record the blocker as such. Task 1.10 stays out of scope, as you said.

This was a read-only review. I ran no tests and made no edits.

VERDICT: APPROVE
