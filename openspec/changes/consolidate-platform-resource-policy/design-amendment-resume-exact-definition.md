# Design amendment — exact admitted definition on resume

**Status:** ROOT-APPROVED SHAPE (2026-09-23), bounded slice implemented on
`codex/resume-exact-definition` (isolated worktree). Awaiting root review of the
exact diff before commit/push. Nothing committed, nothing pushed, nothing
deployed.

Scope: a bounded prerequisite under this existing change; **not** a new backlog
entry, **not** public resume advertisement, **not** an auto-resumer.

This amendment changes **admission storage semantics** (what a `runs` row means).

---

## 1. Verified source facts (re-read on this tree, 2026-09-23)

| # | Fact | Citation |
|---|---|---|
| F1 | Def-based runs persisted **no** definition identity: `_execute_branch_core` passed `branch_version_id=None` into `_prepare_run`, whose docstring said "Def-based runs leave it as None." | `tinyassets/runs.py` `_execute_branch_core` / `_prepare_run` |
| F2 | The admitted definition **is** frozen — but only in process memory: `branch = BranchDefinition.from_dict(branch.to_dict())`, closed over by the worker. A daemon restart between `_prepare_run` and worker completion loses the only copy. | `tinyassets/runs.py` `_execute_branch_core` |
| F3 | Resume re-resolved the **current mutable** definition: `_branch_lookup(branch_def_id, _version)` discards `_version` and returns `get_branch_definition(...)`. | `tinyassets/api/runs.py` `_action_resume_run._branch_lookup` |
| F4 | `resume_run` only checked the lookup returned non-`None`; it never compared the returned graph to what was admitted. | `tinyassets/runs.py` `resume_run` |
| F5 | `resume_run` already reads `run.get("branch_version_id")` — only to hand to provider-authority admission, never to load the branch. | `tinyassets/runs.py` `resume_run` |
| F6 | `branch_versions` is an existing immutable, content-addressed store; `publish_branch_version` is idempotent by `(branch_def_id, content_hash)`. | `tinyassets/branch_versions.py` `publish_branch_version` |
| F7 | `_canonical_snapshot` carries behaviour only. It **omits** `name`, `version`, `domain_id`, `_publish_metadata`, and cannot carry per-run execution choices at all; `_load_branch_version` papers over the gap with `snapshot.setdefault("name", branch_def_id)`. | `tinyassets/branch_versions.py`, `tinyassets/runs.py` `_load_branch_version` |
| F8 | `branch_version_def_id()` exists precisely to authorize **before** loading a snapshot, and returns `""` when absent. | `tinyassets/branch_versions.py` |
| F9 | Contribution attribution derives `artifact_kind = "branch_version" if branch_version_id else "branch_def"` straight off the column. | `tinyassets/runs.py` contribution-event emit |
| F10 | The branch-delete dependency gate and market/leaderboard readers all read `branch_versions`. | `tinyassets/api/branches.py`, `tinyassets/api/market.py`, `tinyassets/api/quality_leaderboard.py` |
| F11 | Resume auth already goes through `_run_write_allowed` plus `resume_run`'s `run["actor"] != actor` gate. | `tinyassets/api/runs.py`, `tinyassets/runs.py` |
| F12 | `_invoke_graph_resume` takes **no** `recursion_limit` / `concurrency_budget_override` parameter — a resumed graph's execution choices are structurally the branch-wide ones. | `tinyassets/runs.py` `_invoke_graph_resume` |
| F13 | Reproduced red: admitted `(n1,n2)` vs resumed `(n1,n2,n3_injected)` — substitution, not suspicion. | §5 R1, re-reproduced on this tree 2026-09-23 |

---

## 2. Shape (root-approved; replaces the rejected `branch_version_id` reuse)

**Rejected:** repurposing `runs.branch_version_id` as a platform-minted pin. It
changes attribution (F9), branch-delete dependency counting and reference
semantics (F10), and the canonical snapshot omits the execution choices and the
authored name/domain entirely (F7).

**Approved:** one nullable **private** run column,
`runs.admission_envelope_json`, holding a schema-versioned envelope:

```json
{
  "envelope_schema": 1,
  "branch": { ...full frozen BranchDefinition.to_dict()... },
  "branch_def_id": "denormalized for the resume-time binding check",
  "execution": {
    "recursion_limit": 7,
    "concurrency_budget_override": 3,
    "effective_concurrency_budget": 3
  },
  "run_name": "caller supplied name",
  "branch_version_id": null
}
```

```
execute_branch_async / execute_branch_version_async
  └─ _execute_branch_core
       branch = from_dict(to_dict(branch))          # existing in-memory freeze
       effective_limit = override or DEFAULT        # resolved ONCE, before the row
       envelope = encode_admission_envelope(...)    # NEW, from that same freeze
       run_id = _prepare_run(..., admission_envelope=envelope)
                  └─ create_run → _initialize_prepared_run
                       BEGIN IMMEDIATE; claim thread_id;
                       capture_admission_envelope(conn)   # guarded on IS NULL
                       commit                              # ← durable boundary
       on AdmissionEnvelopeError → terminalize the reserved run, NO dispatch
       executor.submit(worker over the SAME in-memory branch)

resume_run(run_id)                        # after the existing ownership gates
       admitted = resolve_admitted_execution(base, run)    # private loader
         ├─ envelope present      → strict decode (authoritative), then
         │                          verify its branch_def_id / branch_version_id
         │                          bindings against the persisted run row
         └─ anything else         → admission_not_reconstructable
              (no envelope — with OR without a branch_version_id — corrupt,
               unknown-schema, malformed, or mismatched bindings. There is
               no legacy snapshot path; C.2 removed it. §3.4.)
       branch = from_dict(admitted.branch.to_dict())
       # branch_lookup is NOT consulted
```

**Non-negotiables, each mapping to a verified defect:**

- `runs.branch_version_id` keeps meaning "the user selected this published
  version". Attribution, delete dependencies and market canonicality are
  untouched (F9/F10) — asserted, not asserted-by-absence.
- The envelope is captured **before executable submission**, in the same
  transaction that claims `thread_id`. A capture failure **prevents dispatch**;
  there is no fail-open resumability promise.
- A second preparation cannot replace the original envelope. The write is
  guarded on `IS NULL`; re-supplying the identical bytes is idempotent, a
  different payload raises `AdmissionEnvelopeConflict`.
- `resume_run` loads the definition itself. The injected `branch_lookup` is
  retained for signature compatibility and **not consulted** (F3/F4).
- Legacy runs are never guessed at and never backfilled. Missing/corrupt/
  unknown-schema → typed `admission_not_reconstructable`, refused **before**
  provider admission and before any effect can fire.
- No new table, no new public publication surface, no new grant, no shared
  definition/market mutation, no change to advertised `run_graph` operations.
- No widening of `_row_to_run` / `get_run` / any API projection. The envelope is
  read only by `resolve_admitted_execution`, after the caller's ownership gate.
- Run inputs are already on the run row and in the checkpoint; the envelope does
  not duplicate them.

Implementation lives in a small helper module,
`tinyassets/run_admission_envelope.py` (codec + guarded storage + private
loader), not a generic framework. `tinyassets/runs.py` gains three small seams.

---

## 3. Analysis

### 3.1 Authorization
Capture happens inside `_execute_branch_core`, strictly **after** the caller was
authorized to run the branch — the envelope adds no authority and takes none.
Resume authorization is unchanged (F11). The envelope is a definition *source*,
never an authorization input: a run that has one is not thereby resumable.

### 3.2 Dispatch crash seam
Before: the admitted graph existed only in the worker closure (F2), so a crash
after `_prepare_run` left an INTERRUPTED run whose only recoverable definition
was the mutable current one. After: the envelope commits before executable
submission, so a crash after the durable boundary leaves a resolvable record,
and a crash before it leaves a run that refuses honestly. A capture failure
fails the run closed rather than dispatching an unresumable one.

### 3.3 Execution choices, name, domain, metadata — resolved
The rejected snapshot-based shape lost the authored `name` (F7,
`setdefault("name", branch_def_id)`), `domain_id`, `version`, and both
`recursion_limit_override` / `concurrency_budget_override`. The envelope carries
the full `to_dict()` plus the **effective** execution values resolved once,
before the run row exists, from the same freeze the worker receives — so the
envelope cannot drift from what actually ran. Covered by G3/G4.

### 3.4 Legacy runs are NOT reconstructable — fail closed (CORRECTED, round 2)
**The earlier reasoning in this section was invalid and is withdrawn.** It
claimed a legacy version-pinned run could be reconstructed because
`_invoke_graph_resume` takes no `recursion_limit` /
`concurrency_budget_override` parameter, tested by a runtime signature
inspection (`resume_applies_per_run_execution_overrides()`).

That inference is backwards. The absence of those parameters proves only that
**the old resume path dropped the admitted overrides** — it is the bug, not
evidence about the admission. A run admitted with
`recursion_limit_override=500` and resumed by the buggy path ran at 100; that
made the old resume wrong, and reproducing it does not make it *admission
truth*. Worse, the inference was self-referential: correction §1 below gives
`_invoke_graph_resume` exactly those parameters, so the gate it depended on
inverts the moment the contract is fixed.

A legacy run therefore has **no complete durable evidence** of what it was
admitted with. `branch_version_id` records the user-selected version, never the
per-run execution choices, and the canonical `branch_versions` snapshot cannot
reconstruct `name` / `version` / overrides at all. The corrected contract:

> **Every run without a durable admission envelope refuses
> `admission_not_reconstructable`, regardless of `branch_version_id`.** No
> guessed defaults, no reproduction of the old dropping behaviour, no
> reconstruction from the canonical snapshot.

`resume_applies_per_run_execution_overrides()` and the whole legacy
reconstruction path are **removed**, not patched. Nothing in the codebase now
infers admission truth from a function signature.

### 3.6 Coverage is by admission seam, not by async/sync (round 2)
The envelope must be captured at **every** seam that admits a new run, because
"all admitted new runs preserve their original execution" is the contract. The
four `_prepare_run` admission call sites:

| Seam | Definition source | Envelope |
|---|---|---|
| `_execute_branch_core` (async def + version) | caller's freeze | captured |
| `execute_branch` (sync) | caller's freeze | captured (round 2) |
| `execute_branch_version` (sync) | `_load_branch_version` | captured (round 2) |
| `_initialize_prepared_run` (durable-intent re-prepare) | pre-existing run | see below |

`_initialize_prepared_run` is the shared tail of `_prepare_run` **and** a
separate seam for durable intents that re-initialize an already-reserved run.
Its `admission_envelope` parameter is optional and guarded `IS NULL`, so:
a durable intent that *admits* a run passes the envelope its own freeze
produced; one that merely re-prepares an already-admitted run passes nothing
and the original envelope stands. A durable-intent caller that reserves a run
with **no** envelope leaves that run non-resumable — it refuses honestly rather
than executing an unprovable definition. That is the documented excluded seam,
and it is excluded by evidence, not by claim: §5 lists the exact call sites.

### 3.5 Blast radius
Def admission publishes nothing, so `branch_versions` is untouched: the
`versions_invoking` delete gate, `_action_list_branch_versions`, market
canonicality and `quality_leaderboard` all see exactly what they saw before
(G6/G7). This is the whole reason the approved shape does not reuse the
published-version store.

---

## 4. Tasks (11)

- [x] A.1 Add `tinyassets/run_admission_envelope.py`: schema-versioned codec, `capture_admission_envelope` (guarded `IS NULL` write + read-back verify), private `resolve_admitted_execution` loader, typed `AdmissionEnvelopeError` / `AdmissionEnvelopeConflict` / `AdmissionNotReconstructable`.
- [x] A.2 Add the nullable private `runs.admission_envelope_json` column to the existing `_migrate_runs_table_columns` ALTER loop — additive, idempotent, no index, no backfill.
- [x] A.3 Thread `admission_envelope` through `_prepare_run` and the durable-intent seam `_initialize_prepared_run`; capture inside that helper's `BEGIN IMMEDIATE`, guarded so a second preparation cannot replace the original.
- [x] A.4 In `_execute_branch_core`, resolve `effective_limit` before the run row, encode the envelope from the existing freeze, and pass it to `_prepare_run`. Covers def **and** version-based async admission.
- [x] A.5 On `AdmissionEnvelopeError`, terminalize the reserved run through the existing `_managed_execution_scope` / `terminalize_unstarted_run` path and return FAILED — never dispatch.
- [x] A.6 In `resume_run`, take the definition from `resolve_admitted_execution` and stop consulting `branch_lookup`; document the parameter as retained-but-unconsulted.
- [x] ~~A.7 Validate a legacy version pin with `branch_version_def_id()`...~~ **WITHDRAWN round 2** — legacy reconstruction is removed entirely (§3.4); there is no legacy pin path left to validate.
- [x] A.8 Register `admission_not_reconstructable` in the `ResumeError` docstring and record that `branch_version_mismatch` is **retired** — the definition no longer comes from a version lookup that could miss, so there is no mismatch left to report. (`snapshot_schema_drift` is a pre-existing `branch_versions` decode failure_class, unrelated to `ResumeError`; the round-1 wording that grouped them was wrong.)
- [x] A.9 Land R1/R2 red (§5) and prove them red on the unfixed tree for the stated reason.
- [x] A.10 Land the green contract G1–G9 (§5), including a genuine new-def-run admit → draft-edit → resume **success** on the admitted snapshot.
- [ ] A.11 Root reviews the exact diff; then `python packaging/claude-plugin/build_plugin.py`, `ruff check`, and the deployed-SHA + public-canary + rendered acceptance gates of §7 before any claim of shipped.

### Round-2 corrections (root, 2026-09-23) — the contract the code must meet

- [x] **C.1 Actually enforce the admitted execution choices.** `_invoke_graph_resume` takes `recursion_limit` and `concurrency_budget_override`; it passes the override into `compile_branch(...)` and `recursion_limit` into `app.invoke(config={"configurable": ...})` — the same two seams the non-resume `_invoke_graph` uses. `resume_run` supplies `admitted.recursion_limit` / `admitted.concurrency_budget_override`. Persisting them without threading them was a contract the code did not keep.
- [x] **C.2 Delete the signature-reflection gate and fail closed for legacy.** Remove `resume_applies_per_run_execution_overrides()` and `_reconstruct_legacy`. No envelope → typed `admission_not_reconstructable`. (§3.4.)
- [x] **C.3 Cover the synchronous admission seams.** `execute_branch` and `execute_branch_version` encode the envelope from the *same* frozen branch object they hand to `_invoke_graph`, with the same effective `recursion_limit` / `concurrency_budget_override` the invoke receives, and terminalize-on-capture-failure exactly as the async path does. `_initialize_prepared_run`'s durable-intent seam is documented as excluded in §3.6.
- [x] **C.4 Strict decode — a malformed envelope refuses, never defaults.** `_decode` type-checks every field (`recursion_limit` a positive `int` within limits, `concurrency_budget_override` `None`-or-positive-`int`, `effective_concurrency_budget` consistent with the override, `run_name` a `str`, `branch_version_id` `None`-or-`str`), rejects unknown top-level keys, and requires every key to be present. `_opt_int` no longer swallows a bad value into `None`. The envelope's `run_id` / `branch_def_id` / `branch_version_id` are validated against the persisted run row, so an envelope belonging to another run or branch refuses instead of executing. `read_raw_envelope` treats **only** a genuine missing-column `OperationalError` as "unknown"; every other DB fault propagates.
- [x] **C.5 Capture failure is proven against a real storage fault.** The test forces an actual SQLite write failure at the capture seam (not a synthetic `AdmissionEnvelopeError`), and asserts the reserved run ends terminal-FAILED, is never submitted to the executor, and keeps its `actor` / `owner_user_id` attribution. An encoding fault **before** reservation propagates with no run row at all, which is correct — there is nothing to terminalize.

Dropped from the pre-review task list: the `branch_versions` admission-pin
helper, the `_canonical_snapshot` name interaction, the def-vs-version
attribution classifier, and the `versions_invoking` blast-radius measurement.
All four existed only to service the rejected column-reuse shape; the envelope
removes them rather than solving them.

---

## 5. Red / green test contract

Two files, both run on this tree with a fresh temp root outside the repo.

### Red on the unfixed tree — verified 2026-09-23
Reverse-applied the `tinyassets/runs.py` diff, re-ran both files, re-applied:
**11 failed, 4 passed**. The 4 passers are the invariance guards (G6, G7, G8,
G9) which must hold before *and* after — they are not red contracts.

- **R1** `tests/test_resume_admission_identity.py::test_resume_never_runs_an_edited_definition`
  → `AssertionError: resume ran a definition the run was never admitted with;
  assert ('n1','n2','n3_injected') == ('n1','n2')`. The substitution itself, not
  a refusal.
- **R2** `tests/test_def_run_admission_pin.py::test_def_based_run_durably_binds_its_admitted_definition`
  → `AdmissionNotReconstructable: Run '…' predates admission-envelope capture
  and records no immutable definition.` **Adapted per root review** from the
  original "`branch_version_id` must be non-NULL" assertion to the durable-
  envelope assertion; the original red evidence and its reasoning are preserved
  verbatim in the module docstring.

### Green — 42 passed, `ruff check` clean
Re-measured 2026-09-23 on the four focused files (the two originals plus
`test_resume_execution_choices.py` and `test_resume_run.py`), fresh external
basetemp: **42 passed, 0 failed, 0 skipped, 7.55s**. The round-2 corrections
C.1/C.3/C.5 added the files and cases below G12; the old "15 passed" predates them.
| id | Assertion | Test |
|---|---|---|
| G1 | New def-run resolves an `envelope`-sourced record whose node set is exactly the admitted one. | R2 (now green) |
| G2 | Admit → edit the draft → resume runs `ADMITTED_NODES` and **succeeds**; the poisoned `branch_lookup` is never called. | R1 (now green) |
| G3 | Authored `name` and `domain_id` and the caller's `run_name` survive. | `test_envelope_preserves_name_domain_and_caller_metadata` |
| G4 | `recursion_limit=7` (≠ default) and `concurrency_budget_override=3` round-trip as the effective values. | `test_caller_execution_overrides_roundtrip` |
| G5 | Envelope capture failure → run FAILED, `_invoke_prepared_branch` never called. | `test_envelope_capture_failure_prevents_dispatch` |
| G6 | `runs.branch_version_id` still NULL for a def run; the `artifact_kind` derivation still yields `branch_def`. | `test_def_run_attribution_columns_unchanged` |
| G7 | `list_branch_versions` is empty after def admission — delete/market/leaderboard readers unaffected. | `test_branch_versions_store_untouched_by_def_admission` |
| G8 | No `get_run` / `list_runs` projection exposes the envelope (key scan + serialized-blob scan). | `test_envelope_is_not_in_the_run_projection` |
| G9 | No admission truth is inferred from a function signature — C.2 deleted the reflection gate, so a legacy run refuses regardless of what `_invoke_graph_resume` accepts. (Inverts the round-1 "resume has no per-run execution parameters" pin, which C.1 deliberately falsified by adding them.) | `test_no_admission_truth_is_inferred_from_a_function_signature` |
| G10 | A second preparation cannot replace the envelope; identical bytes are idempotent. | `test_duplicate_preparation_cannot_replace_the_original_envelope` |
| G11 | Legacy def run with no envelope refuses `admission_not_reconstructable` **before** provider admission (provider spy never called, no graph dispatched). | `test_legacy_def_run_without_envelope_refuses_before_provider` |
| G12 | Corrupt envelope refuses and never falls back to the current definition. | `test_corrupt_envelope_refuses_and_never_falls_back` |
| G13 | A legacy **version**-based run refuses too, despite holding an immutable `branch_version_id` pin — the pin records the user's version selection, never the per-run execution choices. (Round-1 asserted the opposite; C.2 removed that path.) | `test_legacy_version_run_refuses_despite_its_immutable_pin` |
| G14 | Resume hands the worker the admitted `recursion_limit` / `concurrency_budget_override`, and those choices survive a draft edit between admit and resume — C.1's enforcement, at `compile_branch` and `app.invoke`. | `test_resume_hands_the_worker_the_admitted_execution_choices`, `test_resume_compiles_and_invokes_with_the_admitted_execution_choices`, `test_admitted_choices_survive_a_draft_edit_between_admit_and_resume` |
| G15 | Column is additive on a pre-existing table, migration is idempotent, and an existing row stays NULL (unknown), never guessed. | `test_migration_is_additive_and_idempotent` |

The resume tests run the worker on the calling thread via `_InlineExecutor`: a
pooled worker's exception lands in a `Future` nobody reads, which would let a
resume that never ran read as a pass.

Gates run (2026-09-23, finalization lane): `ruff check` on the six touched
files — clean; the four focused files — **42 passed**;
`python packaging/claude-plugin/build_plugin.py` — 501 files staged,
`Import probe: probe-ok`, mirror byte-identical for `runs.py` and
`run_admission_envelope.py`. `scripts/linux_oracle.py` not required — no
sandbox/fs/process-limit surface. Full suite and the deploy gates remain A.11.

---

## 6. Open items (no longer blocking)

- **Per-run overrides on a legacy run** stay unrecoverable by construction —
  they were never stored. §3.4 makes that explicit and pins it (G9) rather than
  guessing. New runs carry them in the envelope (G4).
- **Envelope growth.** The envelope is one JSON blob per run, roughly the size
  of the branch definition. Unbounded per-run definition size is founder policy
  (no structural caps on graph size), so this is a storage-usage question for
  the resource-policy change this amendment sits under, not a shape defect.
  Flagging, not solving, in this slice.
- **Queue recovery, family epochs, public resume advertisement** are explicitly
  out of scope and unchanged here.

---

## 7. Rollback boundary — honest version

**Reverting does not restore safety.** The old binary still resolves the current
mutable definition on resume (F3/F4), which is the defect. So the rollback story
is forward-repair or keep resume unadvertised — not "revert and we are fine."

- **Data is preserved on revert.** `admission_envelope_json` is nullable and
  additive; an old binary ignores the column. No reset, no backfill, no
  guessing at existing rows.
- **Runs admitted under the new binary** keep their envelope. If the new binary
  returns, they resume correctly; under the old binary they are exposed to the
  original substitution bug like everything else.
- **Legacy NULL rows** are never filled in. They refuse honestly (G11).
- **Existing public resume stays unadvertised.** Nothing here claims effect
  replay is now universally safe — resuming a graph whose effects already fired
  is a separate, unsolved question.
- **Deployment gates (correcting the earlier "no canary obligation").** `resume_run`
  is reachable from the live MCP surface, so before any shipped claim:
  `python scripts/deployed_sha.py --assert-contains <sha>`,
  `python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp`, and a
  rendered `ui-test` conversation. Merged is not deployed.
- Out of scope, explicitly: public resume advertisement, any auto-resumer, any
  new grant or table, and any edit to PR #3940's files
  (`branches.py` / `daemon_server.py` / `api/branches.py`).

---

## 8. Round-3 corrections (root, 2026-09-23) — actual paths, no invented policy

Round 2 passed 30 integration and 42 focused tests; root found execution paths
those passes did not reach. Completeness is not inferred from them.

- [x] **D.1 Remove the invented ceiling; mirror the real execution contract.**
  `_SANITY_CEILING = 1_000_000` matched nothing in the codebase. Verified
  contract: `compile_branch` (`graph_compiler.py:3945-3952`) computes
  `override if override is not None else branch.concurrency_budget` and builds a
  `ConcurrencyTracker` **iff that is not None**; `ConcurrencyTracker.__init__`
  (`graph_compiler.py:283`) is `Semaphore(budget) if budget else None`, so `0`
  means *tracked but unbounded* and is NOT equivalent to `None`.
  `BranchDefinition.validate()` never constrains `concurrency_budget`, and
  `recursion_limit_override` is range-checked 10..1000 only at the MCP surface
  (`api/runs.py:1088`), never internally. So: strict typing (`bool` is not an
  `int`), `recursion_limit >= 1`, `concurrency_budget_override >= 0`, **no upper
  bound**, and the branch-wide budget recorded verbatim. A malformed branch
  budget raises `AdmissionEnvelopeError` at encode instead of degrading to
  `None` — degrading would resume under unbounded concurrency while claiming it
  was admitted.
- [x] **D.2 Decoder checks effective concurrency against the frozen policy.**
  With `override is None`, `effective_concurrency_budget` must equal the
  `concurrency_budget` of the envelope's **own frozen definition** (decoded
  first, then compared). No current-definition lookup, no legacy fallback. A
  boolean `envelope_schema` is now rejected explicitly: `True == 1`, so the bare
  `!=` version comparison accepted it.
- [x] **D.3 The claim transaction fails closed.** `_initialize_prepared_run`'s
  `BEGIN IMMEDIATE`, thread_id `UPDATE` and `commit()` raised bare
  `sqlite3.Error`, escaping every seam's `except AdmissionEnvelopeError` and
  leaving a reserved QUEUED row nothing would dispatch. They are now wrapped as
  `AdmissionEnvelopeError` carrying the reserved `run_id`, so the existing
  settle path terminalizes it. `_settle_failed_admission_envelope`'s guardless
  branch pins `_expected_statuses={queued}` and reports the status that actually
  holds, so a run cancelled between reservation and capture is never rewritten.
  Evidence is a real read-only database and a real commit-stage fault, not an
  encoding mock.
- [x] **D.4 The two executing seams admit what they dispatch.**
  `delivery_runtime._work` and `run_input_runtime._work_under_maintenance_barrier`
  both call `_invoke_prepared_branch`, so §3.6's "non-executing durable intent"
  exclusion never covered them: their runs executed and then refused to resume.
  Both now encode the envelope immediately before `_initialize_prepared_run`,
  from the exact objects they dispatch with — for the delivery seam the receiver
  snapshot and `DEFAULT_RECURSION_LIMIT`; for the run-input seam the branch
  materialized from the **reloaded authoritative** admission envelope (not the
  preparer's return value, which chooses execution bindings only) plus
  `prepared.recursion_limit` / `prepared.concurrency_budget_override`. Both read
  `run_name` / `branch_version_id` from the reserved run row via the private
  `runs._reserved_run_admission_identity`, because the decoder's binding check
  compares against that row. No new public API; capture stays guarded `IS NULL`,
  so a re-prepared attempt re-supplies an identical envelope and nothing is
  replayed.
- [x] **D.5 Honest comments.** The `resume_run` gate comment claimed the
  authority was "the admission envelope (or, for a pre-envelope run, its
  explicit immutable version)" — that path was deleted in C.2. It now states
  that the envelope is the only authority and that `branch_version_id` records
  the user's selection, never the admitted execution choices.

Round-3 coverage: `tests/test_admission_envelope_corrections.py` (22 tests).
Root owns independent cross-family review, Linux-oracle verification and
release; nothing here is a deployment claim.

---

## 9. Round-4 (2026-09-24) — independent review folded in; oracle red

Independent cross-family review (Claude66315, source read only, no tests run):
**APPROVE** with four non-blocking notes. Full report:
`output/resume-envelope-final-review-peer.md` (worktree `0a7f`). Evidence record:
`2026-09-24-resume-exact-admission-evidence.md (archived in git)`.

- [x] **E.1 Correct the stale codec comment.** `_require_int`'s docstring claimed
  `BranchDefinition.validate()` "does not constrain `concurrency_budget` at
  all". True at base `3f62ed53`, false since #3940, which adds
  `validate_concurrency_budget` (`type(...) is int`, `> 0`, under
  `SQLITE_MAX_INT64`) and calls it from `validate()`. The docstring now says
  that is an *authoring-time* field validator rather than an admission ceiling,
  and that the decoder stays deliberately wider than it — `0` accepted verbatim
  (a branch authored before that validator can carry it, and `0` means *tracked
  but unbounded*), still no upper bound. §D.1 above is left as the dated
  round-3 record; **codec compatibility logic is unchanged.**
- [x] **E.2 The cap is per resumed segment.** The admitted `recursion_limit` is
  re-applied as the recursion cap **for the resumed segment**, not as a
  remaining-step budget carried over from the interrupted segment — the same
  per-invocation ceiling semantics `_invoke_graph` applies, and strictly better
  than the prior path, which silently took LangGraph's stock default. Now stated
  in the spec. `_invoke_graph_resume` still emits no `recursion_limit_applied`
  event, so `get_run` does not surface the cap for a resumed segment: cosmetic,
  recorded, not fixed here.
- [x] **E.3 A failed capture may remain queued — but never dispatches.**
  `_settle_failed_admission_envelope` → `_managed_execution_scope` can itself
  raise `RunExecutionAuthorityLost` from inside the `except
  AdmissionEnvelopeError` handler (workspace-managed sub-run whose lock is
  concurrently held), leaving the reserved row `queued`. The same holds if the
  storage the settle needs is concurrently unavailable. In every such case the
  run is **still never submitted to the executor**, so the failure stays closed
  rather than executing an unprovable definition. Now stated in the spec as an
  as-built limitation.
- [ ] **E.4 Recorded, not acted on.** A malformed branch budget refuses at encode
  time, before any run row exists, where the old path reserved a run and failed
  visibly at compile (`api/runs.py:1127-1129` classifies it, so no crash).
  Unreachable once #3940's validator fronts it.

### Spec sync (this round)
`openspec/specs/graph-execution-substrate/spec.md` gains the as-built resume
requirement only (`+68 / -2`), renamed to `…owner, status, checkpoint, and
admitted-definition guards`. Previously synced text is preserved — the
`recover_in_flight_runs` sweep paragraph and all four pre-existing scenarios
stand alongside the five new ones. The unrelated legacy delta in this change
directory (workspace storage observation) was **not** copied wholesale. The
umbrella change is **not** archived.

### Cloud Linux oracle — GREEN on the exact head
Run `35960544895`, 2026-09-24 05:35:25 UTC, base
`3f62ed53c1b3fc3d53cdb875269245888ba23906` + patch
`83c44c1fd63cb1afc9fccd3ee83812c65f89da185326c6fefa36765c5bc31db4`
(121126 bytes), nine targeted files: **SUCCESS — 122 passed, 0 failed,
0 errored, 0 skipped.**

The prior run `35959680983` (patch `0ef21f0b…50eca459f3b1a27212c0cc1f`,
118729 bytes) was RED — 119 passed, 3 failed, all in
`tests/test_resume_admission_identity.py`. Cause: the `admitted_run` fixture
stubbed `_invoke_prepared_branch` but not the dispatch, so the real thread pool
(`tinyassets/runs.py:5375-5395`) resolved the module global after the `with`
block exited, and the entry-point-less fixture branch wrote `failed` over the
status the fixture set. **Fixed in the test file only — dispatch forced onto the
calling thread via the file's existing `_InlineExecutor`, plus two
initial-state diagnostics (`queued` before interrupt, `get_future` None-or-done).
No runtime change, no weakened assertion.**

**What that file proves, stated exactly.** It is a dispatch-seam test: the
fixture admits a real run with a real envelope, then **manually** writes
`interrupted`, and each resume case stubs `_has_checkpoint` to `True` and
replaces `_invoke_graph_resume` with a recorder. It therefore proves definition
identity, recursion limit, concurrency budget and lookup non-consultation at the
dispatch seam — **not** a real checkpoint, compile or invoke, and **not an actual
full live resume.** The real interrupt → checkpoint → compile → invoke path is
covered by `tests/test_resume_lifecycle_integration.py` and
`tests/test_resume_execution_choices.py`, neither of which stubs `resume_run`,
`_invoke_graph_resume` or `_has_checkpoint`. Evidence record:
`2026-09-24-resume-exact-admission-evidence.md (archived in git)`. Committed on the
branch only; root owns push, PR and the final exact-head review.
