# Tasks — Served agent can build + run its own automations

> **Progress (2026-08-23).** Sliced for safety after a Codex exact-diff review
> (`adapt`, 6 findings). **RUN parity: DONE + LIVE** — `run_graph` is in
> `SERVED_ENGINE_MCP_TOOLS` and activated for `u-tiny` via the run allowlist.
> **BUILD parity: create-only, SHIPPED (un-darked 2026-08-23)** — `write_graph` is
> a CREATE-only, `target=branch` handler that calls the author-gated, effect-free
> `build_branch` directly after `_sanitize_served_branch_spec` (node-level strip of
> approval/author/fork; rejects `node_ref` + nested `graph` blob; forces
> visibility=private; size/node/type guards). Hardened across two Codex rounds
> (6→2→0 open findings); every known path to a persisted APPROVED source_code node
> is closed, and run_graph's fail-closed `_validate_source_code` is the runtime
> backstop. Un-darked per founder MVP call (3 rounds is enough for an MVP); gated to
> the `u-tiny` run allowlist. **RESIDUAL (pre-second-user harden gate):** branches
> are author-scoped not universe-scoped, and build_branch's approval surface is
> broad — the robust multi-tenant fix is a force-unapproved build MODE (clear
> approval after any inherit/deref, before persist) + a branch↔universe binding.
> **`write_graph` PATCH/edit DONE (2026-08-24):** `operation="patch"` edits an OWN
> branch in place — `_sanitize_served_patch_changes` allowlists safe self-edit ops
> (edges/state/entry_point/remove_node, name/description/tags/goal, skills), REFUSES
> `set_published`/`set_visibility`/`set_fork_from`, runs an `add_node` op through the
> SAME create per-node sanitizer, and blocks `update_node` sub-branch-invoke fields;
> routes to the author-gated transactional `patch_branch`. Same allowlist + rate
> limit + author-scope residual as create. **STILL DEFERRED:** `remix_shape`
> (cross-author commons remix); the channel/consent verbs are DONE (§2.2). The
> parallel-allowlist drift class (§2.3) is closed structurally by the single-source
> `tinyassets/served_tools.py` tuple both providers import.

## 1. Design + authority

- [ ] 1.1 Confirm the acting identity for served build/run is the VERIFIED request
      principal in `ctx.universe_id` (reuse the #2498 `BranchExecutionContext` +
      the engine-MCP principal bind); no actor_id-param or env fallback.
- [ ] 1.2 Define the own-universe scope for `write_graph target=branch`: author +
      storage resolve to the acting universe; a cross-universe target is refused
      with the uniform not-found envelope (no existence oracle).
- [ ] 1.3 Confirm `run_graph` execution rides the sanitized invoke_branch path
      (#2498) for every sub-branch, and stays behind the immutable
      approved-source-hash + `TINYASSETS_ENGINE_RUN_GRAPH_UNIVERSES` gate.

## 2. Allowlist changes (dark until BOTH server + provider list them)

- [ ] 2.1 Add `write_graph`, `run_graph`, `remix_shape` to
      `_ENGINE_MCP_ENABLED_TOOLS` (codex_provider.py) and the matching
      `_ENGINE_MCP_TOOLS` (universe_intelligence.py) — keep the two lists in sync.
- [~] 2.2 Add the channel/consent verbs so channels are USER-built via the one
      channel-agnostic node, not hard-coded effectors.
      **CONSENT half DONE (2026-08-25, PR pending):** served `source_channel`
      (action=approve, SINK-ONLY) exposed in `engine_mcp_server.py` + the
      `SERVED_ENGINE_MCP_TOOLS` tuple — mirrors `connect_compute` (allowlist gate,
      graph-pin, least-privilege write, owner-ACL via impl, secret-free).
      `channel_type=="source_code"` REFUSED (preserves the create-only write_graph
      RCE closure); 7 adversarial tests. **BUILD half DONE (2026-08-25):**
      `_sanitize_served_branch_spec` now allows ONLY the `authenticated_external_call`
      effect sink (allowlist — every other sink incl. `wiki_write_back` refused; the
      typed `handoffs` path refused). The `_SERVED_MAX_EFFECT_NODES=5` per-build cap
      described here was REMOVED (founder 2026-08-30, `no-graph-size-caps`): there is no
      effect-node ceiling on any served surface, and create/add_node/update_node now
      share one declaration validator (archived `archive/2026-09-21-edit-served-node-effects/`). What bounds a graph
      is usage, never its size. The
      6 run-time gates (packet, server-owned universe, grant-bound-to-universe, soul
      authority, per-destination consent, outbound flag + SSRF) fire per dispatch
      regardless of the declaration. `connect_http` (raw secret) stays on the browser
      form. **OWED:** a proper per-root-run effect-dispatch cap for ALL surfaces (there
      is no interim per-build bound any more — see above); Codex cross-family review of
      the slice before any flip-on.
- [ ] 2.3 Extend the grounding-file/allowlist drift guard so the two served
      allowlists cannot diverge silently (the parallel-list drift class).

## 3. Enforcement + limits

- [ ] 3.1 `write_graph` refuses any non-own-universe target (fail closed).
- [ ] 3.2 `run_graph` refuses a universe not on the allowlist and any shape whose
      approved-source-hash does not match (immutable version).
- [ ] 3.3 Effect execution stays consent-gated + rate-limited; budget
      boot-reconcile + rolling ceiling holds under a served turn.
- [ ] 3.4 Sub-branch invocation re-verifies the #2498 delegated-authz +
      confidentiality gates (regression, not assumption).

## 4. Tests (adversarial + differential + §14)

- [ ] 4.1 Served `write_graph` to a FOREIGN universe → refused, nothing written.
- [ ] 4.2 Non-founder served turn → build/run verbs absent / refused.
- [ ] 4.3 Served `run_graph` on an unlisted universe → refused; on the allowlisted
      own universe with a matching approved-source-hash → runs.
- [ ] 4.4 Served run invoking a sub-branch cannot reach a foreign private branch
      or spoof the actor (rides #2498).
- [ ] 4.5 Effect-spam + budget-ceiling caps hold under a served run.
- [ ] 4.6 §14 concurrency/load proof for the served run path (Forever Rule).
- [ ] 4.7 Differential: existing connector `write_graph`/`run_graph` behavior
      unchanged for the chatbot surface.

## 5. Gate (before merge; before any served exposure)

- [ ] 5.1 Codex SHAPE review of proposal → approve/adapt (authority-sensitive).
- [ ] 5.2 Full run/branch/graph-compiler/provider suite: zero new failures.
- [ ] 5.3 Codex exact-diff review → approve.
- [ ] 5.4 Rebuild plugin mirror + parity; `mcp_public_canary --assert-handles`
      unchanged (served allowlist change must not alter the PUBLIC connector set).
- [ ] 5.5 Final chatbot-surface `ui-test` through the live app: universe builds a
      small workflow, runs it, returns a result; plus a cross-universe-refusal
      probe. Log to `output/user_sim_session.md`.
