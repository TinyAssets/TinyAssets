# Tasks — channel-agnostic inbound

## Phase 1 — Floor 1: universal inbound webhook (shippable slice)
- [ ] 1.1 `tinyassets/storage/webhook_hooks.py`: per-universe hook-token store — table
      `webhook_hooks(token PK, universe_id, branch_def_id, created_at, revoked_at)`;
      `mint(universe_id, branch_def_id)` (unguessable token), `resolve(token)` (active only),
      `revoke(token)`, `list_for_universe(universe_id)`. Content-free (ids + token only).
- [ ] 1.2 Tests: mint→resolve; revoke→resolve None; unknown→None; per-universe scoping.
- [ ] 1.3 `tinyassets/webhook_inbound.py::handle_hook(token, body, headers)` — resolve or
      404-indistinct; size cap; enqueue via the gated run path (`_action_run_branch`-equivalent)
      as `actor=universe:<uid>`, author-gated; `inputs={"webhook": {...}}`; per-token rate limit;
      return `(status, payload)`. Transport-agnostic (testable without a server).
- [ ] 1.4 Mount `Route("/mcp/hooks/{token}", handle, methods=["POST"])` in `create_streamable_http_app`
      (`universe_server.py`). Bind it AFTER discovery, before the MCP catch-all.
- [ ] 1.5 Tests: valid token → 202 + run enqueued as the universe; unknown/revoked/malformed →
      404 indistinct + nothing enqueued; cross-universe token cannot trigger another branch;
      oversized body → 413/400; body passed as input verbatim.
- [ ] 1.6 Mint/revoke/list user operation (MCP-reachable via `runtime_ops`/extensions), authorized
      to the caller's OWN universe only; returns the full `https://<domain>/mcp/hooks/<token>` URL.
- [ ] 1.7 Tests: mint for own branch → URL; mint for a branch the universe does not own → refused.
- [ ] 1.8 Plugin mirror rebuild + parity; ruff; targeted pytest green.
- [ ] 1.9 Codex cross-family review (public-surface security: token unguessable + per-branch,
      no identity from request, author-gated run, 404-indistinct, rate-limited, dark-until-flag).
- [ ] 1.10 Land; the code is DARK until `TINYASSETS_INBOUND_ENABLED` is flipped; the /mcp/hooks mount rides the existing /mcp tunnel (no infra change) +
      §11 canary). Document the enable step.

### Phase 1 activation — the 3 remaining Codex findings (gate public exposure)
- [x] 1.A **Author-gate / actor at enqueue (Codex #1).** Direct enqueue routes through the shared
      `runs.enqueue_universe_branch_run` — fails closed on an empty universe, asserts the actor is
      `universe:<uid>` (never ambient/host), and ledgers the run for parity with `_dispatch_run_action`.
      The binding is revalidated (active-only `resolve`) immediately before admit/dispatch. Owner-scope:
      mint/create are gated by `universe_access_allows(uid, write=True)` at dispatch.
- [x] 1.B **Durable aggregate admission (Codex #3).** `webhook_hooks.admit` is an on-disk sliding-window
      log bounding BOTH per-token and per-universe rate; it survives restart and is shared across workers.
      Schema DDL is initialized once per process (no re-run per request).
- [x] 1.C **Revocation reachability + owner-scope (Codex #5).** `run_graph(webhook_op=…, token=…)` and
      `run_graph(source_op=…, source_id=…)` route to mint/revoke/list + create/revoke/list through the
      dispatch; `token`/`source_id` now flow through `_extensions_impl`; all six ops are owner-scoped by
      `_WEBHOOK_OWNER_ACTIONS` in `_branch_run_scope_error`. Handle SET unchanged (canary-safe).

## Phase 2 — Floor 3: activate the event bus (BUILT, dark)
- [x] 2.1 `get_or_create_scheduler(data_dir(), _inbound_event_run_fn)` starts in the server lifespan behind
      `TINYASSETS_INBOUND_EVENT_BUS` (default OFF). run_fn fires the branch as `universe:<uid>`, fail-closed.
- [x] 2.2 Admit a namespaced `source:<id>` event_type past `VALID_EVENT_TYPES` (`_is_valid_event_type`).
- [x] 2.3 A source-bound webhook `emit_event`s (subscription fan-out + at-most-once dedupe on the delivery
      id); `_dispatch_event` fires a universe-owned subscription AS that universe.
- [ ] 2.4 Codex re-review + land (worktree, review-gated).

## Phase 3 — Floor 2: source nodes (minimal-but-real BUILT)
- [x] 3.1 A Source = a hook (with `source_id`) + a `source:<id>` event-trigger. `create_source` mints both;
      `revoke_source` tears both down; `list_sources`. Owner-scoped, run_graph-reachable.
- [ ] 3.2 Rebuild Twitter as a node-shaped, vault-backed connector (replace the hard-coded
      env-var `twitter_post` effector) — the founder's "connect Twitter" test. (Sibling outbound change.)
