# Design — Sanitize the invoke_branch closure

## Context

`graph_compiler._build_invoke_branch_node` and `_build_invoke_branch_version_node`
compile a node that, at run time, spawns a CHILD branch run
(`runs.execute_branch` / `execute_branch_async` / `execute_branch_version_async`).
Today the closure captures author-supplied `branch_def_id` / `branch_version_id` and
`child_actor` from `node.invoke_branch_spec`, loads the child with
`get_branch_definition` (no authz), and runs it as `_resolve_actor()` — which returns
the spec's `child_actor`, else reads `actor` off the **mutable** parent run record,
else `"anonymous"`. Nothing binds the child to the parent's authority or universe.

Threat (Codex-confirmed): a victim V running a FOREIGN branch (public, or a remix of
one) executes the foreign author's chosen child branch, as a chosen actor, in V's
context — arbitrary branch execution (→ code execution where the child reaches an
approved `source_code`/effectful/provider-call node), and identity spoofing. This is
why `run_graph`/`remix_shape` are off the served surface.

## Goals / non-goals

- GOAL: no invoke edge widens authority; a foreign branch cannot reach V's private
  branches/secrets; execution stays in the parent actor+universe with parent (or
  narrower) capabilities; enforced transitively + on both variants + both wait modes.
- NON-GOAL: exposing run/remix on the served surface (follow-up that consumes this);
  OS-isolation of the engine turn (`engine-os-sandbox`); re-doing the already-shipped
  cross-author `source_code` approval strip on remix.

## Key decision 1 — an immutable ExecutionContext threaded end-to-end

Introduce a frozen `BranchExecutionContext` (dataclass, `frozen=True`), created ONCE
at the top-level run entry from the AUTHENTICATED request, and threaded into
`_invoke_graph` → the compiled node builders → the invoke closure → the child
`execute_branch*` call → the child's compiled builders (recursively):

```
BranchExecutionContext(
    actor: str,                # the authenticated principal; NEVER from a spec/run row
    universe_id: str,          # execution universe; storage + effects scoped here
    caller_provenance: str,    # "own" | "public-foreign"  (trust of the RUNNING def)
    capabilities: frozenset,   # parent capability ceiling; child inherits ⊆ this
    depth: int,                # existing recursion cap
)
```

Rationale (Codex #3/#4): authority must not be reconstructed from the mutable run
record or a spec field. The context is the single source of truth for who/where/what
the child may do, and it is immutable so a nested edge cannot widen it.

Async: the context is persisted with the queued child job (same integrity as the
existing carrier), re-materialized on pickup — never re-derived from state.

## Key decision 2 — delegated authorization of the child reference

The child reference is AUTHOR-controlled, so authorize it against the AUTHORING
branch's delegated authority, NOT `ctx.actor`'s readability (the trap: V's own
readability authorizes a foreign spec pointing at V's private branch).

Resolution rule for an invoke edge whose PARENT def has provenance P:
- `P == "own"` (parent authored by `ctx.actor` in `ctx.universe_id`): the child may
  be any branch that same author/universe may read (own private OR public).
- `P == "public-foreign"`: the child may ONLY be **public**, or a capability the
  parent def **pinned at authoring time** (an explicit `allowed_child_refs` set frozen
  into the def when it was published/remixed — a foreign author cannot reference the
  runner's private branches because they were never pinnable by that author).
- Any other case → fail closed (uniform `not_found`; never a raw
  `get_branch_definition`).

The child's OWN provenance for its sub-edges is computed the same way from `ctx`
(a public child invoked from a foreign parent stays `"public-foreign"`), so the gate
is transitive and cannot be laundered by one hop through a public branch.

## Key decision 3 — remove child_actor; scope execution to the parent

- Delete `child_actor` from the spec contract + `_resolve_actor`. The child ALWAYS
  runs as `ctx.actor` in `ctx.universe_id`. Fail closed if `ctx.actor` is empty
  (no `"anonymous"` fallback).
- `execute_branch*` calls pass `actor=ctx.actor` and the pinned universe; storage,
  effect grants, provider authority resolve against `ctx.universe_id` regardless of
  where the child DEFINITION originated.

## Key decision 4 — mappings + await are confidentiality boundaries

- `inputs_mapping`: when `ctx.caller_provenance != "own"`, restrict mapped parent
  keys to a declared, delegable allowlist (public/data fields) — reject mapping of
  secret/credential/internal/auth-state keys (reuse the existing served-status
  redaction key-classes as the deny basis).
- `output_mapping`: restrict to declared writable data fields.
- `await_branch_run` / `poll_child_run_status`: a state-supplied `run_id` must be
  verified to belong to `ctx` (parent run + actor + universe) before returning
  status/output — an internally minted child handle is fine; a caller-influenced one
  must be bound.

## Version path ordering (Codex #5)

`_build_invoke_branch_version_node`: authorize the `branch_version_id` (via its
`branch_def_id`, delegated rule) BEFORE `execute_branch_version_async` loads the
snapshot, and verify the resolved version belongs to the authorized definition
(no version-id → foreign-def bypass). Same immutable-context threading.

## Alternatives considered

- **Gate by run-actor readability** (first draft): REJECTED — authorizes the victim's
  own private branches for a foreign spec (Codex critical).
- **Block all cross-author invoke**: too blunt — public composition is the platform's
  point; delegated authority preserves it safely.

## Rollout

Behind the existing recursion cap; differential-tested against the current
same-author path (must be byte-for-byte unchanged) + new adversarial regressions.
This change is dark to the served surface until the SEPARATE run/remix-exposure
slice consumes it.
