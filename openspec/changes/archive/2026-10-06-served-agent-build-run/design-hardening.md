# Hardening design — force-unapproved build mode + branch↔universe binding

> Codex hardening review 2026-08-23. The pre-second-user gate for served write_graph.
> The create-only handler already BLOCKS the known vectors (node_ref rejected, fork_from
> stripped, submitted approval stripped) — these are the robust belt-and-suspenders so the
> invariant holds regardless of build_branch's input shape or future changes.

## 1. Ranked approval-injection paths
| Rank | General builder path | Reachable here? | Minimal hardening |
|---|---|---|---|
| 1 | Clean `node_ref` copy of an approved standalone/branch node | No | Force-clear after resolution |
| 2 | Same-author `fork_from` inheritance | No | Force-clear after inheritance |
| 3 | Idempotent replay of an existing approved branch | Cannot succeed as approved today | Include mode and universe in replay checks |
1. `node_ref` approval inheritance — latent highest risk.
   `_lookup_node_body` returns all five approval fields from standalone and branch nodes; `_resolve_node_spec` deliberately preserves them when the effective source hash still matches. `_apply_node_spec` then constructs an approved `NodeDefinition`. See [branches.py:1588](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/api/branches.py:1588), [branches.py:1679](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/api/branches.py:1679), and [branches.py:1840](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/api/branches.py:1840).
   From this handler: unreachable because every per-node `node_ref` key is rejected at [engine_mcp_server.py:455](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/engine_mcp_server.py:455). The top-level `node_ref` rejection is defense-in-depth; `build_branch` does not consume a top-level node reference.
   Minimal fix: the force-unapproved final transform. Keep the sanitizer rejection too.
2. Same-author fork inheritance — latent.
   `_staged_branch_from_spec` copies a fork version’s `NodeDefinition` objects wholesale. Cross-author forks are demoted, but same-author forks retain every valid approval at [branches.py:2285](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/api/branches.py:2285) and [branches.py:2324](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/api/branches.py:2324).
   From this handler: unreachable because `fork_from` is stripped at [engine_mcp_server.py:448](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/engine_mcp_server.py:448). `fork_from_version` is also stripped but is not presently consumed by `build_branch`.
   Minimal fix: force-clear after `_staged_branch_from_spec`, independent of whether a later served surface permits forks.
3. Idempotent replay — not currently an approval injection, but must be covered by the root fix.
   The request-derived branch ID is presently only `(actor, request_id)` at [branches.py:2463](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/api/branches.py:2463). When an existing branch is found, `node_defs` participates in the immutable comparison at [branches.py:2516](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/api/branches.py:2516). Therefore, an existing approved source node differs from the sanitized unapproved candidate and returns `branch_idempotency_conflict`; it is not accepted as a replay.
   The cross-universe collision remains real: the same founder and request key in two universes derive the same branch ID. An identical unapproved branch can be returned across universes.
   Minimal fix: include `universe_id` in both the deterministic identity and immutable comparison.
All other named shapes are non-carriers:
- Inline `approved=True` cannot survive. The served sanitizer strips it, and the general inline resolver independently removes `approved` at [branches.py:1648](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/api/branches.py:1648). Remaining provenance fields are blanked because `approved` is false.
- `intent="copy"` is reachable but does not dereference anything without `node_ref`; it merely bypasses standalone-ID collision refusal. This is a documentation/behavior mismatch, not an approval path.
- Standalone registration/approval is unreachable because `_extensions_impl` receives the fixed `action="build_branch"` at [engine_mcp_server.py:595](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/engine_mcp_server.py:595).
- `invoke_branch_spec`, `invoke_branch_version_spec`, and `await_run_spec` are stored as nested execution descriptors; they do not instantiate inherited `NodeDefinition`s or approval fields. They remain a separate run-authorization boundary.
- Per-node defaults are `approved=False` with blank provenance at [branches.py:428](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/branches.py:428).
- Skill snapshots are explicitly data, not executable plugins, and normalization only retains skill fields at [branches.py:81](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/branches.py:81).
- Nested `graph.node_defs` is not currently an ingestion path. `_staged_branch_from_spec` uses nested `graph` only for edges, conditional edges, and entry point; nodes come from top-level `node_defs`/`nodes` at [branches.py:2261](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/api/branches.py:2261) and [branches.py:2344](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/api/branches.py:2344). The handler’s comment claiming nested nodes are read is stale, although rejecting `graph` remains useful future-proofing.
Adjacent, non-approval concern: a bodyless node whose `(domain_id, node_id)` resolves a host-controlled opaque callable executes without source approval at [graph_compiler.py:2819](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/graph_compiler.py:2819). That cannot persist approved source code, but the pre-second-user review should explicitly decide whether served builds may select arbitrary registered opaque callables.
## 2. Force-unapproved build mode
Use an internal `force_unapproved: bool = False` argument. Do not expose it as a user-selectable MCP field.
Threading:
- [engine_mcp_server.py:595](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/engine_mcp_server.py:595): pass `force_unapproved=True`.
- [extensions.py:342](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/api/extensions.py:342): add the internal parameter.
- [extensions.py:501](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/api/extensions.py:501): add it to `branch_kwargs`.
- `_dispatch_branch_action` needs no signature change; its existing `kwargs` dictionary carries the flag unchanged to `_ext_branch_build`.
Enforce in `_ext_branch_build`, after successful staging/validation and immediately before constructing the single persistence candidate—currently around [branches.py:2505](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/api/branches.py:2505):
```python
force_unapproved = kwargs.get("force_unapproved") is True
if force_unapproved:
    for node in branch.node_defs:
        _clear_source_code_approval(node)
candidate = branch.to_dict()
```
Then use `candidate` exclusively for:
- `create_branch_definition_once`;
- the idempotency comparison;
- `save_branch_definition`.
This should replace the three independent `branch.to_dict()` calls at lines 2512, 2539, and 2562. That makes the serialized pre-persistence object the choke point, so future dereference/inheritance changes cannot bypass it accidentally.
The mode must clear every node—not just nodes currently containing source:
```text
approved = False
approved_by = ""
approved_at = ""
approved_source_hash = ""
approval_reason = ""
```
Recommended tests:
- Parameterized builder test: matching-hash `node_ref`, same-author fork, and a monkeypatched staging result containing `.mark_approved()` all persist fully blank approval under `force_unapproved=True`.
- Compatibility test: default `force_unapproved=False` still preserves clean `node_ref` and same-author fork approval for the ordinary connector.
- Served boundary test: capture `_extensions_impl` arguments and assert both `force_unapproved is True` and `universe_id == _GRAPH_ID`.
- Idempotency test: an already-approved row is never accepted as a force-unapproved replay.
## 3. Branch ↔ universe binding
The binding must be stored on the branch, selected by trusted request context, and enforced at both authorization and persistence.
Concrete design:
1. Add `universe_id` to `BranchDefinition` near `author` at [branches.py:875](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/branches.py:875), plus `to_dict`/`from_dict`.
2. Add `branch_definitions.universe_id TEXT NOT NULL DEFAULT ''`, an index on `(universe_id, author)`, and serialization/readback at [daemon_server.py:309](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/daemon_server.py:309), [daemon_server.py:2339](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/daemon_server.py:2339), and [daemon_server.py:2369](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/daemon_server.py:2369).
3. The served handler passes `universe_id=_GRAPH_ID` alongside `force_unapproved=True`. Reject or ignore any `universe_id` inside `spec_json`; the stored value must come only from the pinned server argument.
4. `_extensions_impl` already accepts `universe_id`, but currently omits it from `branch_kwargs`. Add it there.
5. At `_dispatch_branch_action` [branches.py:297](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/api/branches.py:297), resolve the requested universe once and require `permissions.universe_access_allows(uid, write=True)` for writes. `_request_universe` is only a resolver—an explicit ID wins without authorization at [helpers.py:89](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/api/helpers.py:89)—so the ACL check is mandatory.
6. Pass the trusted `uid` explicitly through:
```text
_ext_branch_build
  → _staged_branch_from_spec
  → _apply_node_spec
  → _resolve_node_spec / _lookup_node_body
```
New branches set `BranchDefinition.universe_id=uid`; never copy it from the submitted spec or fork parent.
7. Mutation authorization becomes:
```python
actor == branch.author and uid == branch.universe_id
```
A mismatch returns the same not-found envelope as a missing branch. Apply this to patch, update, approval, delete, fork source lookup, and node-ref source lookup.
8. Include `universe_id` in:
- the request-derived ID: `branch-create-v2\0{actor}\0{uid}\0{request_id}`;
- `immutable_fields`;
- branch-version canonical snapshots at [branch_versions.py:209](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/branch_versions.py:209).
9. Replace or constrain `INSERT OR REPLACE` at [daemon_server.py:2436](/C:/Users/Jonathan/Projects/wf-served-wg/tinyassets/daemon_server.py:2436). Persistence must refuse updating an existing ID whose stored `universe_id` differs, even if an API authorization regression occurs.
10. Legacy rows should remain `universe_id=""` and be unmodifiable from the served surface until explicitly bound through a reviewed migration/admin flow. Do not infer origin from `author` or current founder home; one founder may own several universes.
Core binding tests:
- Same actor, admin of `u-a` and `u-b`: a served `u-b` patch/read of a private `u-a` branch is not-found and byte-for-byte unchanged.
- Same actor/request key in `u-a` and `u-b` creates distinct IDs.
- A direct persistence attempt cannot replace a branch across universe IDs.
- Public cross-universe commons reads remain allowed; private reads and all mutations require exact universe binding.
Verification: Windows checkout, HEAD `d74373d8`, 2026-08-23; focused sanitizer plus general-builder inheritance suite passed: `9 passed in 5.11s`. No repository files were edited; the pre-existing untracked `uv.lock` remains untouched.

---

## write_brain persistent prompt-injection (Codex cross-tool review 2026-08-24)

**PRE-EXISTING** (write_brain + read_commons_shape were live before the write_graph
create slice; surfaced by the cross-tool pass, not introduced by #2509).

**The vector (lethal-trifecta on the brain):**
1. An attacker publishes a public branch/agent whose text contains instructions.
2. The served agent reads it via `read_commons_shape` (or WebFetch) — returned with
   NO untrusted-content envelope (`engine_mcp_server.read_commons_shape` →
   `branches.py:539`).
3. The prompt-injectable agent is induced to call `write_brain` with that content.
4. `commit_learning` persists the body and labels its source
   `founder conversation (<actor>)` — losing the true provenance
   (`universe_intelligence.py:633-635`).
5. Next turn the body is concatenated VERBATIM into the system prompt under
   `# What I know so far` (`universe_intelligence.py:347,435`) — so the injected
   instructions persistently steer an agent holding founder-bound `write_graph`,
   `run_graph`, `connect_compute`, and brain-write authority.

Graph pinning, filesystem governance, and section whitelisting do NOT stop this —
it is semantic authority laundering.

**Do NOT** simply remove `write_brain` from the served allowlist — that regresses
the founder's shipped proactive-brain-persistence feature (#2482).

**Durable fix (design decision for the founder — his feature + a safety tradeoff):**
- Brain writes require **server-verifiable founder provenance** — e.g. a one-use
  learning grant minted from the authenticated founder *utterance* this turn, or a
  trusted post-turn writer that sees only that utterance (not tool/commons output).
- Tool/commons-derived observations are stored as **untrusted data** and are NEVER
  inserted verbatim into system-role grounding — render them in a clearly delimited
  untrusted section the model is instructed not to treat as instructions.
- Related but distinct from the 2026-08-06 `commit_learning` visibility concern
  (that is about confidential founder input leaking OUT; this is about untrusted
  input steering IN).
