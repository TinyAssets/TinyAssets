# Sanitize the invoke_branch closure so foreign/remixed branches run safely

## Why

Running a FOREIGN branch (a public branch by another author, or a remix of one)
can execute arbitrary code as an arbitrary actor in the runner's context. The
`invoke_branch` node closures (`_build_invoke_branch_node` +
`_build_invoke_branch_version_node`, `graph_compiler.py`) spawn a CHILD branch run
using author-supplied data with no authorization against the RUNNING principal:
the child `branch_def_id`/`branch_version_id` is loaded raw (`get_branch_definition`
loads any branch by id — including the runner's private ones), and the child runs
as a spec-supplied `child_actor` (identity spoofing). This is the documented reason
`run_graph`/`remix_shape` are excluded from the served universe agent's surface.
Sanitizing this closure is the keystone that lets every surface RUN and BUILD
workflows (the "users add a Slack channel node and run it" goal).

## What Changes

Carry an **immutable execution context** through every invoke edge and authorize
the (author-controlled) child reference against **delegated** authority — never the
runner's ambient authority. Both variants, both wait modes, every depth.

1. **Child authorization = delegated, not ambient (the real IDOR fix).** The child
   `branch_def_id` / `branch_version_id` is chosen by the branch AUTHOR, so it must
   be authorized against what the author was allowed to reference — NOT the runner's
   readability. Using the run-actor's readability is the trap: the run actor is the
   victim, whose own check would authorize a foreign spec's reference to the victim's
   PRIVATE branch. So: a foreign-authored branch may reference only **public**
   targets or child capabilities **explicitly pinned/imported at authoring time**;
   a same-author (own-universe) branch may reference its own. Fail closed (uniform
   not-found), never a raw load.
2. **Immutable execution context; no actor spoofing.** Thread a frozen context —
   authenticated actor, execution universe, caller provenance/trust, capabilities —
   from the parent run into the closure and the child. The child executes as the
   parent's authenticated actor in the parent's universe; a public definition may
   ORIGINATE elsewhere but execution authority + storage + effects stay scoped to
   the parent universe. **Remove `child_actor`** (string equality is not a boundary).
   Never reconstruct authority from the mutable run record; fail closed — no
   `"anonymous"` fallback. Async jobs securely persist the same context.
3. **Mappings are confidentiality boundaries.** A foreign spec's `inputs_mapping`
   may map only declared, delegable parent fields — never secrets, credentials,
   internal metadata, or authorization/control state; `output_mapping` is restricted
   to declared writable data fields.
4. **Transitive + low-level.** Enforce (1)–(3) at every invocation depth and at the
   low-level execution entry (not only the top closure). For the version path,
   authorize BEFORE snapshot load and verify the version belongs to the authorized
   definition. `await_branch_run` / polling must bind a state-supplied run id to the
   parent run + actor + universe before returning status/output. Keep the existing
   depth cap; inherit depth/resource budgets across the edge.

Scope note: "arbitrary BRANCH execution" is the confirmed vector; it becomes code
execution only where the selected branch reaches approved `source_code` / effectful
/ provider-call nodes — which is why the gate must also bound those capabilities
across the edge.

## Boundaries (defer, do not duplicate)

- **Cross-author `source_code` approval** is ALREADY stripped on remix
  (`api/branches.py` `_clear_source_code_approval` under `fork_from` + cross-author);
  this change is the RUN-PATH complement, not a re-do.
- **Exposing `run_graph`/`remix_shape` on the served enabled-tools allowlists** is a
  FOLLOW-UP that consumes this fix — it is not in this change. This change makes the
  closure safe; the served-surface exposure is a separate reviewed slice.
- **OS-isolation of the engine turn** (`engine-os-sandbox`) is the separate,
  deeper defense and is not superseded here.

## Security invariants (must hold)

- No invoke edge widens authority. An author-controlled child reference is
  authorized against the AUTHOR's delegated authority (public or pinned), never the
  runner's ambient readability; the child runs as the parent's authenticated actor,
  in the parent's universe, with the parent's (or narrower) capabilities.
- A foreign branch cannot read, invoke, or exfiltrate the runner's private branches
  or secrets via `branch_def_id`/`branch_version_id`, `inputs_mapping`, or a
  state-supplied `await` run id.
- Execution context is immutable and threaded end-to-end (blocking, async, def,
  version, and every nested depth); authority is never rebuilt from a mutable run
  record and never falls back to `"anonymous"`.
- Fail closed: an unauthorized/absent child, actor/universe/capability mismatch, or
  a version that does not belong to its authorized definition refuses the invoke
  rather than degrading to a raw load.
- The subscription-CLI and existing same-author own-universe invoke paths are
  behaviorally unchanged (differential + full suite).

## Gate

Security/authority-critical → Codex SHAPE review of this design before build
(dispatched), then exact-diff review before merge; full run/branch/graph-compiler
suite zero-new-failures + new adversarial regression tests (foreign-private child
refused, actor-spoof refused, transitive depth). This change does NOT itself expose
run/remix on the served surface — that is the follow-up it unblocks.

## Codex shape review — 2026-08-23: VERDICT adapt (folded above)

Caught a critical flaw in the first draft and refined the model:

1. **[critical]** Gating the child via the RUN-ACTOR's readability does NOT stop the
   IDOR — the run actor is the victim, whose own check authorizes a foreign spec's
   reference to the victim's private branch. Use DELEGATED authority (public or
   pinned), not the victim's ambient authority. (Folded → change #1.)
2. Precision: confirmed vector is attacker-controlled child selection + actor
   injection = arbitrary BRANCH execution; code execution only where the branch
   reaches approved source_code / effectful nodes. (Folded → scope note.)
3. Actor-only authz is insufficient — carry an immutable execution context (actor +
   universe + provenance/trust + capabilities); a public definition may originate
   elsewhere but execution authority + storage/effects stay in the parent universe.
   (Folded → change #2.)
4. Remove `child_actor`; never rebuild authority from the mutable run record; fail
   closed, no `"anonymous"`; async must persist the context. (Folded → change #2.)
5. Transitive at every depth + low-level entry; version path authorizes BEFORE
   snapshot load + verifies the version belongs to the authorized definition.
   (Folded → change #4.)
6. `inputs_mapping` / `output_mapping` are confidentiality boundaries. (Folded → #3.)
7. Audit `await_branch_run` / polling — bind a state-supplied run id to parent run +
   actor + universe. (Folded → change #4.)
8. Before enabling served RUN/REMIX, test nested invokes, private-target denial,
   version-id bypass, async polling, mapping exfiltration, effectful/provider-call
   capabilities, inherited depth/resource budgets. (→ tasks.md gate.)
