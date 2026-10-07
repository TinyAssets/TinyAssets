# Tasks

Ordered so the first live proof arrives before anything is deleted. Nothing
here removes `write_graph`; that stays a founder call once the script path
works.

## Decide first — this one gates the rest

- [ ] **Founder approval of the four-primitive floor** (`design.md`). It is a
      `PLAN.md`-level statement: isolated execution, authenticated call,
      durable state, identity and arbitration are the platform; workspace,
      graph, pooling, retry and resume become libraries. Everything below
      assumes it.

## Slice 1 — one file, no workspace, proves the shape

- [ ] Expose `http.request` on a held connection through the existing RPC alias
      map (`_NODE_MCP_ACTION_ALIASES`, `graph_compiler.py:1517`), routed to the
      existing `authenticated_external_call` effector so consent, budget and
      evidence are unchanged.
- [ ] Consent checked **at the call**, against connections the universe already
      holds. No "declare what you will use" list — declarations are what failed
      four times.
- [ ] `run_script(source)` on the served surface. No other arguments.
- [ ] Live proof: the founder's universe posts to an API it holds a connection
      for, as one `.py`, uncoached.

## Slice 2 — workspaces as a library

- [ ] `exec_isolated(argv, fs)` as a first-class RPC capability: spawn a jail
      per call with one bind, return the result. This is primitive 1 made
      reachable from user code.
- [ ] `workspace` reimplemented **as library code** on top of it plus a git
      clone over `http.request` — not as a platform verb.
- [ ] A script can hold several workspaces at once and create them at runtime.
      Cover it with the case that motivated it: two checkouts open together.
- [ ] Live proof: README fix end to end — checkout, real compile check, edit,
      push, PR — as one `.py`, uncoached, gate count zero.

## Slice 3 — retire the policy that was never ours, in the ONLY safe order

Sequencing corrected 2026-08-31 after measuring what cancel actually does; see
`docs/concerns/2026-08-31-cancel-is-advisory-and-the-timeout-is-doing-its-job.md`.
An earlier draft of this list had step 2 before step 1 and would have removed
the only working stop.

- [ ] **Make cancellation real.** Today it is advisory and checked only between
      nodes (`runs.py:2795`), and `node_sandbox.py` never hears about it at
      all. Reach the running child, kill the whole jail (reuse the timeout
      path's process-group kill), and still release lease, ledger and lock.
      **Prerequisite for everything below** — with the vault absolute,
      cancellation is what bounds a borrowed workflow.
- [ ] Per-tenant arbitration replaces the host-wide workspace slot. Build this
      before removing the slot, or the shared-resource invariant is unenforced.
- [ ] **Only then** delete `MAX_WORKSPACE_TIMEOUT_SECONDS`. It is currently the
      sole mechanism that terminates a runaway node, so it can only go once the
      user has a real stop and the platform has real arbitration. Quota bounds
      a tenant's share; it does not stop one stuck run.
- [ ] Delete `ALLOWED_IMPORTS` and `FORBIDDEN_PATTERNS` — theatre, walked past
      by `'sub' + 'process'`, protecting nobody but the author from themselves.
- [ ] Delete `MAX_RPC_CALLS` and `MAX_WORKSPACE_COMMANDS`; they bound DSL node
      shape, which no longer exists.
- [ ] Branch naming and "never the default branch" move into library code the
      user can fork.
- [ ] Keep and re-verify: `/data` never bound, `RLIMIT_AS` + aggregate-RSS
      watchdog, protocol bounds, and **credential blindness — the vault is
      absolute, first-party code included**.

## Not in this change

- Where libraries live, how a script imports one, versioning and trust in a
  shared library. That is the commons question and it is now the load-bearing
  one — it deserves its own change rather than a paragraph here.
- Deprecating `write_graph`.
