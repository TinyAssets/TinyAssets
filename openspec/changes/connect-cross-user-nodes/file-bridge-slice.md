# File bridge: receiver-owned custody copy (slice inside `connect-cross-user-nodes`)

Implementation-ready corrected design for task **2.4**. Adapted from the
read-only precedent at `wf-cross-user-acceptance` (inspected `02b1e628`) and
**re-verified line by line against `1f121719`**, this checkout, 2026-09-23.
No runtime code written, no tests run, nothing deployed. This is a design; it is
not approved until root says so.

Scope: **cross-owner delivery only.** The owned run-file reader, capture,
upload and binding primitives are reused unchanged. No new MCP primitive is
introduced — delivery still enters through the existing `deliver_output`
extension scope, so `scripts/check_primitive_exists.py action` has nothing to
collide with.

---

## 0. What changed versus the precedent, and why

The precedent's prose (§1.3, §1.5.1, §2.0) was corrected on 2026-09-22 but its
**task list was not**. Two tasks still instruct the superseded design:

| Precedent task | What it says | Why it is wrong now |
|---|---|---|
| F4 | "**no seam signature change**"; "`source_fence` re-resolves `bound_file_in_transaction`" | Contradicts its own §1.5.1. A cross-owner `source_fence` taking `.runs.db` self-deadlocks against the `.runs.db` writer opened *inside* it at `run_file_capture.py:288-291`. |
| F8 | "Bind at `delivery_runtime._work` before `_initialize_prepared_run`" | Contradicts its own §1.3. Binding at worker start reopens the lease window root's ADAPT refused. |

A builder handed the precedent's §5 builds both the deadlock and the expiry
window. Correcting that is the main reason this document exists.

Second correction: **path rot.** The precedent cites
`execution_authority/run_file_capture.py`. The module is at
`tinyassets/run_file_capture.py`; only `blob_stream.py` lives under
`tinyassets/execution_authority/`. Every citation below is re-anchored.

Third correction, new here: **`_accept_output` does not re-authorize a replay**
(§2.1 below). The precedent's revoke semantics were stated without it.

---

## 1. Verified facts (re-checked at `1f121719`)

### 1.1 Limits come from the receiver's branch, not the contract — CONFIRMED

`tinyassets/api/deliveries.py:89-92` iterates `json.loads(receiver["contract_json"])`
over `{name, type, required}` and raises `delivery_file_transfer_not_implemented`
for `file`/`file_bundle`. There is no `max_count`/`max_bytes` on that shape.

`tinyassets/authoring/io.py:60-71` — `IODeclaration` carries `min_count`,
`max_count=1`, `max_bytes=MAX_FILE_BYTES`. `tinyassets/run_file_contract.py:41-70`
(`declared_file_inputs`) already validates a declaration against `state_schema`
(`file_bundle` → `list`, `file` → `dict`) and its docstring is explicit:
*"Validate shape/limits only; caller must resolve each reference's authority."*

The receiver branch snapshot is already parsed inside the acceptance path
(`BranchDefinition.from_dict(json.loads(receiver["snapshot_json"]))`,
`api/deliveries.py:103`). **Decision: resolve limits from the receiver's own
`io_manifest` via `declared_file_inputs`; refuse a `file`/`file_bundle` contract
field whose branch carries no matching declaration. Add no contract field.**

### 1.2 No per-user quota exists, and none is proposed — CONFIRMED

`storage/run_files.py:82-84` — `capacity_limit()` docstring, verbatim:
*"Operational subsystem ceiling; never infer an entitlement from a tier."*
`reserve_in_transaction` (`:183-188`) enforces exactly two predicates:
`allocated + max_bytes > ceiling_bytes` → `file_custody_capacity_exhausted`
(**global**, no owner term in the SUM) and `free_bytes - pending - headroom <
max_bytes` → `file_physical_capacity_exhausted` (per physical root).

`owner_id`/`universe_id` are stored for attribution and erasure, never compared
to a per-owner ceiling. A per-owner retained-byte quota is **withdrawn**: it
would contradict an in-code rule and the founder's light-usage cap reference.
Receiver consent is §1.1's `max_count`/`max_bytes`. Ceiling stays as built.

### 1.3 The three databases, and the canonical lock order — CONFIRMED

| Store | Opened by |
|---|---|
| Platform / ACL | `storage._connect` — `api/receiver_links._owner_authority:84-85` (`BEGIN IMMEDIATE`), `run_file_capture._authority:58-71` (`BEGIN IMMEDIATE` when `write=True`) |
| Authoring | `AuthoringStore._connect` (fresh connection per call) |
| Runs | `runs._connect` — `receiver_links.transaction`, all of `storage/run_files.py`, `storage/deliveries.transaction:97-105` |

Canonical order is **platform → runs**, stated in code at
`delivery_runtime.py:97-99`: *"Preserve the canonical lock order: author store,
then runs store. No provider call or user code executes while either is held."*
`api/deliveries._accept_output:114-117` holds the same pair.

### 1.4 Therefore the copy cannot run inside the acceptance fences — CONFIRMED

`_capture_files` itself takes the platform writer (`authority(write=True)`,
`run_file_capture.py:191,220,287`) and `.runs.db BEGIN IMMEDIATE`
(`:193,222,290`). `_accept_output` holds **both** from `api/deliveries.py:114`
onward. Calling the copy under them is the same thread opening a second
connection against a held `BEGIN IMMEDIATE` on both files — 30 s `busy_timeout`,
then `OperationalError`. The copy therefore runs in the caller **above**
`_accept_output`'s fences, and receiver file ids are passed *into* acceptance.
This is forced by lock topology, not preference.

### 1.5 Binding belongs in the acceptance transaction — CONFIRMED (root's ADAPT)

- The receiver run already exists inside acceptance: `storage/deliveries.py:219-229`
  calls `runs._insert_run_in_transaction(...)` on the acceptance `conn`, between
  the `graph_deliveries` INSERT and the `graph_delivery_attempts` INSERT.
- `bind_in_transaction` (`storage/run_files.py:325-356`) is SQL-only on that same
  database: `_owned_run` SELECT, object SELECT, `INSERT INTO run_file_bindings`.
  No byte IO, no second connection.

So binding is a few statements on a transaction that is already open on the right
file. **Root's ADAPT is correct and is adopted.**

It is also load-bearing, not cosmetic. `bind_in_transaction:333-336` admits an
object whose `unbound_expires_at` has lapsed **only if a binding already exists**,
and `run_file_retention._expire_operation` collects only objects with no binding.
Binding at acceptance makes an accepted copy permanently immune to the unbound
lease **by construction**. Binding at worker start leaves a real expiry window
between "the receiver was told yes" and the bind — which is exactly what root
refused.

### 1.6 Custody cannot be deferred past acceptance — CONFIRMED

`run_file_release.release_owned_file:55-65` refuses a release only while a binding
joins a run whose `status IN ('queued','running','resumed')`. Once the sender's
source run is `succeeded`, the sender may release. An accept-now / copy-later
design therefore has a window where acceptance commits, the sender releases, and
the copy finds nothing — a delivery the receiver was told was accepted can never
be honoured. Cross-owner binding cannot close it either: `bind_in_transaction:326`
calls `_owned_run(conn, run_id, owner_id, universe_id)` and the object predicate
is `o.owner_id=? AND o.universe_id=?` (`:334-338`) — the same chokepoint we must
preserve.

### 1.7 The seam: `open_source` fits, `source_fence` does NOT — CONFIRMED

`open_source` is used at `run_file_capture.py:270` as a context manager yielding
**chunks** into `blobs.stage_stream`, and the copy loop at `:268-285` runs with
**no transaction open** (the reservation block at `:220-247` has closed). The
cross-owner adapter yields a generator over `HeldBlobStream.read_range`
(`tinyassets/execution_authority/blob_stream.py`) — the same mechanism the served
reader already uses. `:277-278` verifies `(staged.size, staged.sha256)` against
declared metadata, so the copy is digest-verified by the existing path. **No
change to `open_source`.**

`source_fence` cannot express the cross-owner fence. The publication block is:

```
with authority(write=True):            # platform BEGIN IMMEDIATE      :287
    with source_fence(metadata):       #                                :288
        with runs._connect(base) as conn:                             # :290
            conn.execute("BEGIN IMMEDIATE")   # .runs.db                :291
            guard.require_held(conn)                                  # :292
            store.commit_objects_in_transaction(...)                  # :293
```

The authoring adapter works only because its source lives in a *third* file. The
cross-owner source has no third file: sender custody, the sender's binding and
release all serialize on `.runs.db` — `run_file_release.py:43-53` opens
`runs._connect` + `BEGIN IMMEDIATE` and its own comment says *"Bindings and
ready-state change serialize on this same runs writer."* So a cross-owner
`source_fence` has two options and both fail:

- `BEGIN IMMEDIATE` on `.runs.db` → deadlocks against the `.runs.db` writer
  opened *inside it* at `:291`. Same thread, second connection. Guaranteed.
- Deferred read → no deadlock, but **orders nothing**. Ordering a later revoke is
  the only reason the fence exists.

The same argument disqualifies a `source_fence` recheck of receiver/link
generation: `receiver_links.resolve_link_in_transaction` also runs on `.runs.db`.

#### 1.7.1 Resolution — one conn-receiving hook (this is root's "fence publication")

The recheck must run **on the publication connection itself**, inside the
`BEGIN IMMEDIATE` at `:291`, where source binding, receiver/link generation and
object publication become one atomic act on one file. That is *stronger* than the
authoring adapter's two-file fence, which can only order across files.

`_capture_files` already has this pattern: `replay_result(conn, operation, result)`
is a caller-supplied callback invoked **with the publication connection** at
`:302-303`, alongside `require_current_home` / `ready_to_copy`. Add one more in
the same style:

```
publication_check=None   # called as publication_check(conn) at :292,
                         # after guard.require_held(conn),
                         # before commit_objects_in_transaction
```

The cross-owner adapter passes a **null `source_fence`** and does its work in
`publication_check`: re-resolve `bound_file_in_transaction` under the sender's
run/owner, and re-resolve receiver/link generation. Raising there rolls the
publication back — `runs._connect` commits only on clean exit. No existing caller
changes; `publication_check` defaults to `None`. Lock order platform → runs is
preserved exactly, with no nested writer on either file.

**This is a small, additive, single-hook seam change — not "unchanged."**

---

## 2. The corrected ordering: copy completes *before* acceptance

```
Phase A  validate (read-only)    ACL / link generation / contract / manifest limits
                                 / preflight; resolve each sender envelope via
                                 store.bound_file_in_transaction(run_id=source.run_id,
                                 owner_id=source.owner_user_id, ...); plus the
                                 occurrence pre-check of §2.2 — all before any
                                 allocation.
Phase B  copy (no DB write lock) copy_owned_custody_file -> receiver-owned,
                                 digest-verified, COMMITTED operation; runs in the
                                 caller ABOVE _accept_output's fences (§1.4).
Phase C  accept (ONE acceptance TX on .runs.db)
                                 existing digest/dedupe/atomic acceptance
                                 + runs._insert_run_in_transaction (already there)
                                 + bind_in_transaction on the RECEIVER file ids
                                 + graph_delivery_files provenance rows
Phase D  worker                  rewrite inputs to receiver references before
                                 _initialize_prepared_run. NO binding here.
```

Why this ordering:

- The sender's object is needed only inside Phase B, which holds it open through
  the existing held-handle reader. Sender release after Phase C is harmless **by
  construction** — the receiver already owns its bytes.
- The inverted failure (copy commits, acceptance never does) leaves a
  receiver-owned **unbound** operation, which the existing `UNBOUND_LIFETIME_SECONDS`
  lease plus `cleanup_file_operation` already collect. A collectable orphan is
  strictly safer than an unfulfillable accepted delivery.
- Capacity refusals surface to the sender *before* acceptance: a receiver at the
  ceiling produces a clean rejected send, not a dead accepted one.

No byte IO runs under any DB write lock: `_capture_files:220-247` sequences
maintenance barrier → operation guard → short reservation → IO, and Phase B runs
entirely outside the acceptance transaction.

### 2.1 NEW: a replay is not re-authorized — state revoke semantics accordingly

At `api/deliveries.py:132-152`, when `prior is not None` the code takes the
`else` branch: it reuses `prior["snapshot_json"]` and compares `mapped` to
`prior["inputs_json"]`. It does **not** call `resolve_link_in_transaction` and
does **not** call `management._owned_branch` — those run only on the
`prior is None` path (`:141-146`). The comment is deliberate: *"Replay uses the
admitted contract/snapshot, not a later revision."*

Consequence for this slice, which the precedent did not state: **revoke stops new
occurrences; it does not retroactively refuse an identical replay of an
already-accepted occurrence.** That is correct behaviour (a replay is the same
delivery, not a new one) but it must be written into the delta spec, and Phase A's
advisory pre-read must mirror the same branch structure or it will diverge from
the authoritative check it is advising.

### 2.2 Occurrence conflict decided before any allocation (root's third condition)

The authoritative `OccurrenceConflict` at `api/deliveries.py:150-152` sits
**inside** the acceptance transaction — i.e. after the copy, under Phase B → C. A
changed replay would allocate and copy bytes and only then be refused, repeatedly.
Two mechanisms move the decision ahead of the allocation:

1. **Advisory pre-read (Phase A).** A read-only `graph_deliveries` lookup on
   `(sender_id, sender_universe_id, link_id, occurrence_id)` before Phase B.
   `prior` exists and outputs differ → refuse now, no reservation, no copy.
   `prior` exists and they match → accepted replay: resolve receiver file ids from
   `graph_delivery_files`, skip Phase B entirely. `prior is None` → proceed. Being
   racy is fine: it never *grants*, and `:150-152` is untouched.

2. **Deterministic operation id keyed WITHOUT the sender file ids.** Key the
   `_capture_files` operation id on `(receiver_owner, receiver_universe, link_id,
   occurrence_id)` only, and put the ordered sender file references in the
   `request` digest. A changed replay then re-enters `_replay` at the same
   operation id with a different digest and raises `file_operation_conflict`
   (`storage/run_files.py:170`), which runs at `run_file_capture.py:195-205` —
   **before** `capacity_limit()`, `metadata_provider()` and
   `reserve_in_transaction` (`:206-247`). Nothing is allocated.

   Including the sender file ids in the operation id (the precedent's earlier
   formula) yields a *different* operation for a changed replay, hence a fresh
   allocation — the behaviour to avoid. Map the capture-layer
   `file_operation_conflict` to the same user-visible refusal as
   `OccurrenceConflict`.

An identical replay returns identical receiver `file_id`s via `_replay`, no second
copy. **The replay envelope stays immutable**: `inputs_json` remains the
sender-side envelope and the `:150-152` comparison stays byte-identical.

### 2.3 Copy, not share

`run_file_objects` has one `(owner_id, universe_id)` per row, `storage_key TEXT
NOT NULL UNIQUE`, no refcount, no grant table. Sharing a row would require
loosening the owner predicate used identically by the reader,
`bind_in_transaction:334-338` and `bound_file_in_transaction:360-371`, or adding a
grant table consulted by all three — and either way receiver readability would
depend on sender retention (§1.6) and sender erasure would break receiver records.
Copy keeps ownership = deletion authority = attribution = erasure completeness,
and the receiver reads through the **unchanged** served reader with zero authority
delta on the read path. Cross-owner physical dedupe is blocked by `storage_key
UNIQUE` and is explicitly out of scope.

### 2.4 The three refusal sites become typed validators, not deletions

- `api/deliveries.py:90-92` — contract-type refusal.
- `api/deliveries.py:112` → `delivery_runtime.reject_file_references:29-43`, which
  refuses any dict carrying `handle_id`/`artifact_id`/`file_id` or
  `type in ("file","file_bundle")`.
- `delivery_runtime._execution_subject:57` — re-runs `reject_file_references` on
  `inputs_json` at **every** dispatch, so a stored reference is refused at replay.

The replacement discriminator is **ownership resolution at the acting principal**,
not declaration. A reference passes only if it resolves under the acting owner's
custody. At execution time the acting owner is the receiver, so a *sender*
reference that survived into `inputs_json` still refuses. Fail-closed preserved.
Note `reject_file_references(outputs)` at `:112` runs **before** any authority
fence — the replacement must keep a fail-closed default there.

### 2.5 Provenance table

`inputs_json` must stay the sender-side envelope to keep the `:150-152` comparison
byte-identical, so the sender→receiver mapping has nowhere durable to live. One
additive table:

```
graph_delivery_files(delivery_id, field_name, ordinal,
                     sender_file_id, receiver_file_id, sha256, size_bytes)
```

It **must** be added to `tinyassets/scoped_reset.py:234-238` (confirmed: exactly
five `run_file_*` tables today) in the same change, or a per-user delete leaks
rows.

### 2.6 Unsourced RPC path stays refused

`deliver_output` without a `NodeDeliverySource` keeps refusing file envelopes in
this slice: a caller-supplied `file_id` with no trusted source run has no
run-bound authority to resolve against.

---

## 3. Semantics this slice fixes explicitly

- **Receiver revoke** stops new occurrences only. Accepted deliveries keep their
  receiver-owned copies; `revoke_receiver` must not delete past copies — they are
  the receiver's record under the receiver's own deletion authority. An identical
  replay of an already-accepted occurrence still completes (§2.1).
- **Sender release / sender erasure** never touch a receiver copy.
- **Both-owner deletion**: receiver erasure removes objects, bindings,
  `graph_delivery_files` rows and blobs; sender erasure leaves the receiver copy
  readable with no dangling FK.
- **Exact occurrence dedupe** unchanged —
  `UNIQUE(sender_id, sender_universe_id, link_id, occurrence_id)`.
- **Source access isolation**: the receiver never learns a sender `file_id`, path,
  storage key or handle. Only the receiver's own ids are readable.

## 4. Corrections to sibling documents

- `design.md:435-439` — under-specified. The receiver gets a **new receiver-owned
  `file_id` produced by a custody copy**; the sender's `file_id` never becomes
  receiver-readable.
- `design.md:187-189` — stale on intake. As-built intake is authoring handles
  (`run_file_capture.capture_authoring_files:121`) plus app upload; workspace
  capture is not an intake.
- `next-slice-proposal.md:23,133-134` — stale on the read half. `read_bound_file`
  plus `blob_stream.read_range` give bounded chunked reads today.
- `next-slice-proposal.md:148-149` — the gate stands, but the remaining path is
  three items (source adapter, provenance table, ordering correction), **not**
  four: per-owner retained-byte accounting is withdrawn per §1.2.
- `api/run_files.py:59-61` (`supported_intake`, `file_delivery_available: False`)
  is the cutover surface and flips in this change. A probe must bridge its own
  cutover — it must accept both the pre- and post-flip shape.

---

## 5. Ownership and file boundaries (what the builder may touch)

| File | Change |
|---|---|
| `tinyassets/run_file_capture.py` | **+1 kwarg** `publication_check=None`, called at `:292`. Nothing else. |
| `tinyassets/run_file_sources.py` (or a new `run_file_crossowner.py`) | New `copy_owned_custody_file` adapter: `open_source` generator, null `source_fence`, `publication_check`. |
| `tinyassets/api/deliveries.py` | Phase A validation + advisory pre-read; Phase B call above the fences; typed validator replacing `:90-92`. |
| `tinyassets/storage/deliveries.py` | `graph_delivery_files` schema + `bind_in_transaction` call inside acceptance. |
| `tinyassets/delivery_runtime.py` | `reject_file_references` → ownership-resolving validator; Phase D input rewrite. |
| `tinyassets/scoped_reset.py` | `+graph_delivery_files` at `:234-238`. |
| `tinyassets/api/run_files.py` | Cutover flip at `:59-61`, last. |
| `tinyassets/storage/run_files.py` | **No change.** The owner predicate is the chokepoint; it stays. |
| `tinyassets/run_file_reader.py`, `run_file_release.py`, `run_file_retention.py` | **No change.** |

Out of bounds: `openspec/changes/connect-cross-user-nodes/` RPC documents, goal 7
(JSON UI sharing) and goal 10 (intake retirement) — independent, not gated by
this bridge and not to be coupled to it.

## 6. Tasks (12, inside task 2.4 of this change)

These twelve are the implementation of task **2.4** — deliberately *not* new
checkboxes. `python scripts/openspec_flow.py check-change connect-cross-user-nodes
--provider claude` counts **all** checkboxes in the change against the 12-task
ceiling, and the change is already at 12; adding F1–F12 as checkboxes returned
`BLOCKED ... has 24 tasks`. So they live here and task 2.4 ticks when all twelve
are done.

- **F1** Resolve receiver file limits from the branch `io_manifest` via
  `run_file_contract.declared_file_inputs`; refuse a `file`/`file_bundle` contract
  field with no matching branch declaration. No new contract field, no per-user
  quota (§1.1, §1.2).
- **F2** Convert the three refusal sites (`api/deliveries.py:90-92`,
  `delivery_runtime.reject_file_references:29-43`, `_execution_subject:57`) to
  typed validators keyed on ownership resolution at the acting principal; keep the
  pre-authority call at `api/deliveries.py:112` fail-closed (§2.4).
- **F3** Phase A: resolve each sender envelope with the existing
  `bound_file_in_transaction` against the trusted `source.run_id`, read-only, plus
  the advisory occurrence pre-read mirroring the `prior is None` branch structure
  at `api/deliveries.py:132-152` (§2.1, §2.2).
- **F4** Add `publication_check(conn)` to `_capture_files`, called at
  `run_file_capture.py:292` after `guard.require_held(conn)` and before
  `commit_objects_in_transaction`; default `None`, no existing caller changes.
  **This supersedes any "no seam signature change" instruction** (§1.7.1).
- **F5** Add `copy_owned_custody_file` as the third `_capture_files` caller:
  `open_source` yields a generator over `HeldBlobStream.read_range`, **null**
  `source_fence`, re-resolution in `publication_check`, receiver
  `owner_id`/`universe_id` (§1.7).
- **F6** Key the operation id on `(receiver_owner, receiver_universe, link_id,
  occurrence_id)` **only**, sender file references in the `request` digest; map
  `file_operation_conflict` to the `OccurrenceConflict` refusal (§2.2).
- **F7** Order Phase B above the acceptance fences; prove no byte IO runs under
  the acceptance TX or the platform/runs writers (§1.4).
- **F8** Bind the **receiver** file ids with `bind_in_transaction` **inside the
  acceptance transaction**, alongside `runs._insert_run_in_transaction`
  (`storage/deliveries.py:219-229`). **Not at worker start** — binding there
  reopens the unbound-lease window (§1.5).
- **F9** Add `graph_delivery_files` (additive), write receiver ids in the
  acceptance TX, leave `inputs_json` and the replay digest byte-identical; add the
  table to `scoped_reset.py:234-238` and prove both-owner erasure leaves no
  dangling FK either way (§2.5).
- **F10** Phase D: rewrite inputs to receiver references in
  `delivery_runtime._work` before `_initialize_prepared_run`. No binding here.
  Make revoke stop new occurrences only, leave accepted copies intact, and record
  the replay-not-re-authorized semantics in the delta spec (§2.1, §3).
- **F11** Write the eleven negative tests of §7, each required **red** against the
  unfixed tree first; mutation-check the owner predicate at
  `storage/run_files.py:365`. Then `scripts/linux_oracle.py` for the
  descriptor/custody paths — a green Windows run is not evidence here.
- **F12** Flip `api/run_files.py:59-61` (`supported_intake`,
  `file_delivery_available`) with a cutover-bridging probe that accepts both
  shapes; then cross-family reviewer, protected deployed-SHA, canary, rendered
  two-owner acceptance, spec sync.

## 7. Required negative tests — each must be RED against the unfixed tree first

1. Receiver reads the **sender's** `file_id` via `read_graph target=run_file` →
   `run_file_not_found`. Mutation-check: flip the owner predicate at
   `storage/run_files.py:365` and this test must go red.
2. Sender's run + file under receiver identity → `run_file_access_denied`
   (`run_file_capture._authority:68-70`).
3. Copied object held past `UNBOUND_LIFETIME_SECONDS` before bind → loud refusal,
   never a silent bind of a collectable object.
4. **Sender releases the source immediately after acceptance** → receiver copy
   still readable. Holds by construction under §2; this is the regression that
   catches any drift back to copy-after-accept.
5. Replay of the same `(sender, universe, link, occurrence)` → one operation,
   identical receiver `file_id`s, unchanged provenance; a replay citing a
   *different* sender file → occurrence conflict **with no allocation** (assert on
   `run_file_operations`/`run_file_allocations`, not just the exception).
6. Revoke after delivery → past copies readable, new occurrences refuse, and an
   identical replay of the accepted occurrence still completes (§2.1).
7. Receiver erasure clears five `run_file_*` tables + `graph_delivery_files` +
   blobs; sender erasure leaves the receiver copy readable, no dangling FK.
8. Crash between copy-commit and acceptance → orphan collected by the unbound
   lease, no accepted delivery. Crash after the durable start marker → the
   existing never-auto-replay rule (`delivery_runtime.py:109-111`) holds.
9. Copy exceeding `capacity_limit()` → refusal attributed to the receiver, before
   acceptance, no partial bind, sender-visible failure.
10. Unsourced `deliver_output` with a file envelope → still refused (§2.6).
11. **Deadlock regression:** a cross-owner capture whose `publication_check` is
    replaced by a `.runs.db BEGIN IMMEDIATE` must fail loudly in test, pinning
    §1.7's argument as executable rather than prose.

Existing positive delivery/run-file regressions stay green.

### 7.1 Measured baseline — two of the three refusal sites have no guard test

Characterization run, this checkout at `1f121719`, 2026-09-23:
`python -m pytest tests/test_delivery_runtime.py -q` → **10 passed** (fresh
`--basetemp`; a reused temp dir first produced 6 `WinError 5` errors, the known
Windows temp-ACL trap, not a code failure).

`grep -rn "delivery_file_transfer_not_implemented" tests/` returns **exactly one
hit**: `tests/test_delivery_runtime.py:141`, which calls
`runtime.reject_file_references(value)` directly. So of the three refusal sites F2
converts:

| Site | Guard test today |
|---|---|
| `delivery_runtime.reject_file_references:29-43` | yes — `:141`, called directly |
| `api/deliveries.py:90-92` (contract-type) | **none** |
| `delivery_runtime._execution_subject:57` (re-refuses at every dispatch) | **none** |

This matters for F2 and F11: two of the three sites can be converted — or
silently weakened — with the whole suite still green, and the one test that does
exist asserts the helper in isolation rather than through a real caller. F11 must
therefore drive each of the three sites **through its actual call path** and
mutation-check it, not assert the helper. A guard whose test cannot go red when
the guard is removed is decor.

## 8. Open items

- Receiver-directed **retry** (task 2.5) is untouched here.
- September24: root independently approved this shape before implementation
  (2026-09-23-file-bridge-shape.md (archived in git)). Claude independently approved
  runtime01b8f2e2 after the source-authority and declared-position corrections.
  Root measured70passed/1skip across six focused modules; independent Opus
  measured133passed/0skips across nine related modules. These Windows runs do not
  establish Linux, deployment or ordinary two-owner acceptance.
- Scoped operator reset currently refuses unknown root custody rather than
  deleting it. Account erasure is separately tested; successful operator reset
  is not claimed. See the indexed scoped-reset concern.
- Non-blocking post-MVP follow-ups from the final review: a replay whose prior
  delivery is erased mid-call raises raw KeyError rather than a typed refusal;
  concurrent duplicate first acceptance may safely conflict rather than return
  the original receipt; remove a dead receiver-row assignment. None is a false
  acceptance or authority bypass. Preserve these for real-user follow-through,
  not another generic pre-live hardening round.

Rollback: redeploy the prior cloud image using the existing release workflow if
new acceptance/custody failures appear. The provenance table is additive: retain
it and all copied bytes, do not drop data as rollback cleanup. Older code does
not provide the new transfer capability, so hold file-delivery/account-erasure
operations involving new provenance until a forward fix if reverting runtime.
Prefer a forward fix for erasure-specific failures; do not pretend the previous
image understands the new child table.

Acceptance remains two independently authenticated app owners in ordinary
rendered conversation, with no operator-built workflow.
