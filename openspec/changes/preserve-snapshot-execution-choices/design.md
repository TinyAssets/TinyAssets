## Context

**All source citations in this section are pinned to `ff1320d5`** — the sha the diagnostic pass read — and describe the tree *before* this change. They are historical findings, not a map of the current working tree, where the same lines have moved. Read them with `git show ff1320d5:<path>`.

`BranchDefinition` declares `default_llm_policy` and `concurrency_budget` (`tinyassets/branches.py:1004-1035`) and `publish_branch_version` now preserves each non-null value (`tinyassets/branch_versions.py:225-241`, landed PR #3933). Nothing upstream can produce a value:

- **Storage.** `branch_definitions` (`tinyassets/daemon_server.py:312-330`) has no column for either field; no later `ALTER TABLE` adds one (`daemon_server.py:496-581`); `_BRANCH_DEFINITION_INSERT_SQL` names 20 columns (`daemon_server.py:2545-2554`) and `_branch_def_from_row` (`daemon_server.py:2444-2478`) cannot return what was never written.
- **Build.** `_staged_branch_from_spec` (`tinyassets/api/branches.py:2595-2735`) never reads either key, top-level or via the nested-`graph` path; the receipt returns `built`/`ok` with no mention of them, and there is no unknown-spec-key refusal, so a correct field name and a typo are equally invisible.
- **Patch.** `_apply_patch_op` (`api/branches.py:2953-3191`) has no setter; plausible names hit `unknown op`. Attaching the fields to an accepted op (`set_name`) returns `patched` for a change that did not happen.
- **Read / fork.** `_ext_branch_get` (`api/branches.py:596-645`) applies no allowlist — the keys are absent, not stripped. The fork inheritance block (`api/branches.py:2646-2705`) enumerates topology and skips both fields.

Evidence: `tests/test_branch_execution_choice_authoring.py` (22 diagnostic assertions, all green against the *unfixed* tree) and `output/execution-choice-authoring-result.md`. `PLAN.md` mentions neither field (`docview search`, no matches); no `PLAN.md` edit is proposed and nothing here conflicts with a PLAN principle. Two principles are load-bearing *for* this shape: *Foundation builds to the end state* (storage schema is foundation — no compat-shim bandage) and *Tools are the agent-computer interface* (extend the existing op table, do not mint a tool).

## Goals / Non-Goals

**Goals:** an ordinary author sets or clears a workflow-wide model policy and concurrency budget through the already-authorized branch surface; the value is stored, read back, inherited by a fork, and retained with distinct identity when frozen.

**Non-Goals:** public resume, mid-node effect replay, new permissions or MCP tools, provider/catalog changes, a per-user or platform concurrency *ceiling* (usage limits live at admission, not in a field validator), repo-wide unknown-spec-key refusal, historical version rewriting, private user workflow edits.

## Decisions

### Storage: two additive nullable columns, one definition of each fact

`ALTER TABLE branch_definitions ADD COLUMN default_llm_policy_json TEXT` and `... ADD COLUMN concurrency_budget INTEGER`, both nullable, guarded by the existing `PRAGMA table_info` probe pattern (`daemon_server.py:496-508`, the `goal_id` precedent). No index — neither field is a list/filter key. `_BRANCH_DEFINITION_INSERT_SQL` grows 20 → 22 columns; the policy is `json.dumps`'d or NULL; `_branch_def_from_row` reads both through the existing `row.keys()` guard so a row predating the migration reads as unset.

Rejected: carrying both inside the existing `graph_json` blob the way `io_manifest` is. It needs no migration, which is its only advantage. It hides an execution choice inside the topology blob, makes it invisible to SQL, and repeats the `entry_point` double-write that the schema itself already annotates as awkward (`daemon_server.py:314`). Storage shape is foundation, so it builds to the end state rather than to the cheapest patch. **Explicitly: the fields are written in exactly one place — the new columns. They are not also written into `graph_json`.** Two definitions of one fact is the defect class this repo keeps paying for.

`NULL` is the only unset representation and it means *inherit / unbounded*. There is no third "explicitly cleared" state: clearing writes NULL, so a cleared branch and a never-set branch produce an identical row, an identical read, and an identical absent-key snapshot — which is exactly the form the landed snapshot fix preserves. A distinct cleared marker would change identity for previously-unset branches and is rejected for that reason. Node-level `llm_policy is None` keeps meaning "inherit the branch default" (`branches.py:365`).

### Backward compatibility, both directions

Old rows: columns read NULL → unset → behaviour and snapshot hash form unchanged, no backfill, no republish. Old code can read the new table, but its `INSERT OR REPLACE` writer omits the new columns and WOULD erase their values when replacing a row. Additive columns alone do not guarantee rollback data preservation. Prefer forward repair. If an older image must be restored, preserve a cloud database backup and prevent definition writes until a compatible writer is restored; never claim old writes preserve the choices and never revert the schema. Add a regression that demonstrates this compatibility boundary.

### Validation matches the compiler, not a guess

`ConcurrencyTracker.__init__` (`tinyassets/graph_compiler.py:285-287`) does `threading.Semaphore(budget) if budget else None`. Therefore, on today's unvalidated field: `0` silently means **unbounded** while reporting a budget of 0; `True` silently means `Semaphore(1)`; `-1` raises `ValueError` inside compile; `"4"` raises `TypeError` inside compile. Every value that validates clean today is either a silent lie or a late crash.

Authoring validation therefore adopts the run-level override contract verbatim — `type(value) is int and value > 0` (`tinyassets/run_input_runtime.py:159-163`). `type(...) is int`, not `isinstance`, is the point: it refuses `bool` instead of coercing it. Errors surface through `BranchDefinition.validate()` (`branches.py:1312-1324`, which today has no `concurrency_budget` branch at all) so build, patch and publish all inherit one check. **No ceiling in the validator** — `no-structural-caps-on-graph-size` limits usage, not shape, and the provider in-flight ceiling owned by `raise-served-concurrency-budget` is a different quantity from this per-branch field.

`default_llm_policy` reuses `_validate_llm_policy_shape` (`branches.py:845-879`) unchanged: unknown keys stay tolerated for forward-compat, `preferred_provider` stays the one named footgun. Adding an allowlist here would contradict this change's own snapshot decision and recreate a lossy path. That leaves a real residual gap, stated rather than papered over: **a typo inside the policy dict is still accepted and stored.** The mitigation is narrower than "closed": **the build and patch receipts echo the effective stored execution choices**, which is a *report*, not a detector. It makes the FIELD-level miss visible — a misspelled top-level spec key (`default_llm_polcy`) leaves the choice `null` in the receipt while the call still reports `built`/`patched`, so the author can see nothing was applied. It does **not** detect an unknown key *within* a policy dict: that key is echoed back because it was stored verbatim. Detecting it would require the allowlist this change declines to add. The echo does not change refusal semantics for any existing caller.

### Surface boundary

| Boundary | Route | Behaviour |
|---|---|---|
| create / build | `_staged_branch_from_spec` (`api/branches.py:2595`) | read both by resolving key **presence** — a dedicated `_choice_present`/`_choice_value` pair, deliberately **not** `_spec_get` (`:2632`), which falls through on an explicit null — so top-level **and** nested-`graph` forms work and an explicit null clears; validate; echo in the receipt |
| patch | `_apply_patch_op` (`api/branches.py:2953`) | `set_default_llm_policy`, `set_concurrency_budget`, mirroring `set_io_manifest` (`:3113-3122`): field required or explicit error, `null` clears, everything else still `unknown op` |
| read | `_ext_branch_get` (`api/branches.py:596`) | both keys returned from the row; no allowlist change needed |
| describe | `_build_branch_text` (`api/branches.py:2736`) | one line when either is set, so an author can discover controls that are currently invisible |
| fork | inheritance block (`api/branches.py:2646-2705`) | inherit both next to `state_schema`/`io_manifest`; the fork's own spec may override |
| save / load | `daemon_server.py:2486-2554` / `:2444-2478` | 22-column insert; guarded row read |
| snapshot | `branch_versions.py:225-241` | unchanged; the landed conditional non-null inclusion simply becomes reachable |

Root pre-build correction: the existing `_spec_get` falls through on explicit
null, so do not reuse it blindly for these choices. Resolve presence separately:
top-level key wins even when null; else nested graph key wins even when null;
else inherit from the fork parent. Explicit null in either form clears rather
than re-inheriting. Add top-null/nested-value and nested-null/fork-parent tests.
Do not change unrelated topology-key semantics.

SQLite INTEGER accepts only signed64-bit values. For larger positive budgets,
refuse with an actionable storage-representation error before binding rather
than silently coercing to float or crashing. This is a representability bound,
not a provider/admission ceiling; normal positive values are not capped by any
platform concurrency constant. Pin the boundary and overflow refusal in tests.

Idempotency: both fields already sit in the build `immutable_fields` tuple (`api/branches.py:2888-2907`), so a same-`request_id` replay differing only in an execution choice returns `branch_idempotency_conflict`. That comparison is vacuous today and becomes live here; it is the intended semantics, not a regression.

MCP surface is unchanged: no new top-level tool, no new field name, canonical handle set per Hard Rule 11 untouched. `check_primitive_exists action set_default_llm_policy` and `... set_concurrency_budget` both report CLEAN on `origin/main`.

## Risks / Trade-offs

- Open policy dictionaries can hold arbitrary user material: preserve the existing authoring/visibility boundary. This field grants no provider credential and does not make a private branch public. Redesigning all policy validation is separate work, not a reason to drop user choices.
- A migration on a live table is the hard-to-reverse part. It is additive and nullable, so the failure mode is a column no code reads; the mitigation is the both-directions compatibility above plus the legacy-row assertions.
- **The original 22 diagnostic tests are not capability proof.** Each asserted the then-current drop/refuse behaviour, so they go red the moment the gap closes — by design. The implementation lane rewrote them into set/read/fork assertions and kept the honestly-still-true ones (unknown-op refusal, legacy-row absence, snapshot absent-key form, unknown-spec-key tolerance). Measured result of that pass: **29 of 45 cases RED against `ff1320d5`, 16 GREEN there** — the 16 are the deliberate compatibility pins, not red-first proof, and the module says so. `TestLegacyDatabaseMigration` (6 further cases, added in the verification pass) was not executed against `ff1320d5` and makes no red-first claim.
- Serializer/unit evidence alone does not prove routing: keep a compile/provider-call assertion on the supported preferred-policy shape and concurrency reconstruction evidence. Do not claim live resume proof.

## Verification and rollout

Design first: this proposal carries the storage-shape and validation decisions that need root review *before* code. **That pre-build review is DONE** — independent Codex root review, artifact `2026-09-24-execution-choice-authoring-shape.md (archived in git)` (2026-09-24 UTC): AGREE on additive nullable columns, existing authorized operations, explicit clearing, immutable versions, no new provider authority, PLAN unchanged; one `DISAGREE_EVIDENCE` on the rollback claim (`save_branch_definition` uses `INSERT OR REPLACE`, so an old writer erases the new columns), which is folded into the compatibility section above and pinned by `TestLegacyDatabaseMigration::test_an_old_writer_erases_the_choices_it_does_not_know_about`. A post-build independent review is separate and is root's. Then: exact focused tests (red-first where the assertion can be), existing publish/version and concurrency suites, `ruff check`, `python packaging/claude-plugin/build_plugin.py` for `mirror-parity` (the same four modules exist in the plugin mirror), Linux oracle not required (no sandbox/fs/limits surface). Normal required CI/image/deploy, `python scripts/deployed_sha.py --assert-contains <sha>`, and `python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp` (no `--assert-handles` change expected; run it anyway to prove the handle set did not move). Ask the app agent through the ordinary UI to exercise its own saved choices — a rendered conversation in which an uncoached author sets a budget, reads it back on a later turn, and reads it back off a pinned version; no operator workflow edits, no coaching, not the node-level `llm_policy` path. Sync the delta when code lands, archive only after acceptance. Deployment or successful tests alone are not closure.
