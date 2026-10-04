# Tasks — Sanitize the invoke_branch closure

## 1. Execution context

- [ ] 1.1 Add a frozen `BranchExecutionContext` (actor, universe_id, caller_provenance,
      capabilities, depth) constructed ONCE at the authenticated top-level run entry
      (`runs.execute_branch` / `execute_branch_version` / the served `run_graph` path).
- [ ] 1.2 Thread it through `_invoke_graph` → node builders → the invoke closures →
      the child `execute_branch*` call → the child's builders (recursive). Never
      derive it from the run record or a node spec.
- [ ] 1.3 Persist it with async/queued child jobs and re-materialize on pickup with
      the same integrity as the existing carrier; never re-derive from state.

## 2. Delegated child authorization

- [ ] 2.1 Add a delegated-authorization resolver: given `ctx` + the AUTHORING def's
      provenance, authorize a child `branch_def_id` (own → own-readable; public-foreign
      → public-only or authoring-time-pinned `allowed_child_refs`); else uniform
      not-found. No raw `get_branch_definition` before authorization.
- [ ] 2.2 Compute + freeze `allowed_child_refs` / provenance into a definition at
      publish/remix time (authoring-time pinning); default empty for foreign.
- [ ] 2.3 Apply in `_build_invoke_branch_node` (def path) before load/execute.
- [ ] 2.4 Apply in `_build_invoke_branch_version_node`: authorize via the version's
      `branch_def_id` BEFORE snapshot load; verify the version belongs to the
      authorized definition.
- [ ] 2.5 Ensure transitivity — a public child invoked from a foreign parent keeps
      foreign provenance for its own sub-edges.

## 3. Remove actor spoofing + scope to parent

- [ ] 3.1 Delete `child_actor` from the `invoke_branch_spec` contract + `_resolve_actor`;
      the child always runs as `ctx.actor` in `ctx.universe_id`.
- [ ] 3.2 Fail closed when `ctx.actor` is empty (remove the `"anonymous"` fallback).
- [ ] 3.3 Confirm child storage, effect grants, and provider authority resolve against
      `ctx.universe_id` regardless of where the child definition originated.

## 4. Mapping + await confidentiality

- [ ] 4.1 For foreign-provenance edges, restrict `inputs_mapping` to declared delegable
      fields (reject secret/credential/internal/auth-state keys via the existing
      redaction key-classes); restrict `output_mapping` to declared writable fields.
- [ ] 4.2 Bind `await_branch_run` / `poll_child_run_status` results to a run whose
      parent run + actor + universe match `ctx`; refuse a foreign run id.

## 5. Tests (adversarial + differential)

- [ ] 5.1 Foreign parent → runner's PRIVATE child `branch_def_id` → refused (not loaded).
- [ ] 5.2 Foreign parent → public child → authorized, runs as parent actor/universe.
- [ ] 5.3 Own-universe parent → own private child → authorized (unchanged path).
- [ ] 5.4 `child_actor` in spec has no effect; empty ctx.actor fails closed.
- [ ] 5.5 Nested (grandchild) invoke stays scoped; capability ⊆ parent at each depth.
- [ ] 5.6 Version path: authorize before snapshot load; version-not-of-authorized-def
      refused; async wait_mode path covered.
- [ ] 5.7 inputs_mapping secret-key exfiltration refused; output_mapping restricted.
- [ ] 5.8 await/poll with a foreign run id refused.
- [ ] 5.9 Differential: same-author own-universe invoke (blocking + async, def + version)
      is byte-for-byte unchanged vs. current behavior.

## 6. Gate (before merge; before any served run/remix exposure)

- [ ] 6.1 Full run / branch / graph-compiler suite: zero new failures.
- [ ] 6.2 Codex exact-diff review → approve.
- [ ] 6.3 Rebuild plugin mirror (canonical `tinyassets/*` edits) + parity check.
- [ ] 6.4 Note: exposing `run_graph`/`remix_shape` on the served enabled-tools
      allowlists is a SEPARATE follow-up change that consumes this one — NOT in scope.
