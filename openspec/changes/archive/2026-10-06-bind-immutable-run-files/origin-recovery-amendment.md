# Explicit admitted origin for safe initial dispatch recovery

2026-09-19 proposal; 2026-09-20 lead disposition accepted the bounded Fable
96232 ADAPT corrections in
[`admitted-origin-shape-review`](../../../docs/reviews/2026-09-20-admitted-origin-shape-review.md).
That complete shape review gates the implementation recorded below; it is not
an exact-code approval or release claim.
This is a dependency of durable initial dispatch, not another workflow queue or
effect retry feature. Existing first-file release and remaining origin scope stay
unchanged. Consumer builder agrees to the exact `canonical_consumer`, version 1
intake stamp. Direct origin is generic, not derived from the presence of files.

## Evidence and missing fact

`storage/run_input_admissions.py::_SCHEMA` freezes owner/universe, target pin or
snapshot, digest, start timestamp/token. It has no origin identity or dispatch
options. `run_input_runtime.dispatch_admitted_run` receives a trusted in-memory
preparer and optional settlement observer. `PreparedRunExecution` supplies
recursion/concurrency overrides which are not stored on the run row. A restart
therefore cannot infer the correct authority preparer from an available lock,
file bindings or a guessed callback, nor replace original options with defaults.
The consumer's canonical request row already correlates its exact reserved run
and owns selection/context; that is not duplicated here.

## Minimal existing-row amendment

Add only these fields to `run_input_admissions`, with additive migration:

- `origin_kind TEXT NOT NULL DEFAULT ''`
- `origin_version INTEGER NOT NULL DEFAULT 0`
- `origin_options_json TEXT NOT NULL DEFAULT '{}'`

The reviewed intake adapter supplies constants, never forwarded payload origin
selectors. It stamps them in the same transaction as existing run/envelope/input
bindings or consumer correlation. Same-run replay compares all three immutable
values along with existing target/owner identity; no relabeling/backfill from
files, branch authorship or elapsed time. Legacy `('', 0, '{}')` remains held for
recovery until an explicitly reviewed migration can prove its source. Unknown
kind/version or invalid options refuses without executing or terminalizing.

Initial static registry entries:

- `('direct', 1)`: ordinary direct invocation, including an existing immutable
  version pin. Options are exactly `recursion_limit` (positive integer, bool
  refused) and `concurrency_budget_override` (positive integer or null), within
  existing runtime bounds (strict positive integers; the existing public intake
  separately enforces its 10..1000 recursion override range). Preserve already
  accepted runtime values, including a limit below the public override range.
  Encode effective defaults at intake, not at
  restart. No arbitrary kwargs, callables, paths, credentials, provider grants,
  duplicated inputs or model preferences. Maximum encoded options is 1 KiB.
- `('canonical_consumer', 1)`: options exactly `{}`. Resolve the exact existing
  canonical request-to-run correlation; use that row's current/source authority
  and captured selection/context. Missing/mismatched/deleted correlation refuses.

Kinds/versions select code from a fixed internal allowlist, never an import name,
registry entry writable by workflows, or executable serialized closure.
`on_node_status` and `on_settled` are observation-only callbacks: rebuild from
static adapter code when available or omit honestly after restart. Never serialize
them or make notification the sole durable projection mechanism.

## Recovery stays on the existing start path

Reuse the existing boot/five-minute maintenance loop with a separate bounded
rotating admissions-table scan, independent of file-operation presence/cursor.
No additional durable queue, lease, pool, delivery claim or retry status. The scan
may nominate rows, but only the existing worker can decide start authority under
the same nonexpiring run lock, author fence and runs transaction.

Under the held guard, reload immutable origin and validate the selected adapter
matches; require genuinely unstarted queued state, both marker/token null, no
cancel intent, fresh owner/tombstone/universe/source permissions and applicable
provider admission. A live saturated pool job may own queued work before taking
the guard: competing submissions converge on the same start CAS; neither may
retire the other solely because the lock was free. Zero-row CAS never invokes.
Started or ambiguous queued/running rows are held with action-may-have-occurred
semantics, never replayed or guessed dead. Managed retirement keeps its separate
exact worker/kernel evidence requirements and Stop stays lifetime-guard-independent.

Direct source read permission remains the existing public-or-original-author
rule, but readability is NOT provider execution authority. Preserve current
foreground provider branch-author fence. A foreign reusable definition that
needs an owned remix uses existing attributed branch remix; do not silently
launder authorship or call execution refusal a sign-in failure. The frozen
admitted behavior is not replaced with a newer source body during recovery.

## Accepted implementation corrections

- Both initial/same-key and restart nomination use the static
  `run_input_origins.dispatch_initial_run` entry. It selects direct or canonical
  consumer v1 code only; the worker revalidates that selection/correlation under
  the same execution guard and existing start CAS. Custom caller closures are
  not an alternate public-origin path. The lower-level worker remains an
  internal integration/test seam.
- Prepare executes in the existing empty Context and derives owner/universe/
  actor from persisted envelope/run/correlation, never current request globals.
  Only bind_provider constructs the explicit-principal provider session, after
  writers unwind. No serialized provider grant or live callback is retained.
- Direct v1 is depth zero with no nested parent. A legitimate self-root family
  is not a child-pool run: `_get_executor` selects by depth, while authenticated
  root reservation may set family root equal to its own run ID. Accept no family
  or valid self-root; reject foreign/malformed roots and closing families. The
  cloud lane's independent generic family activation/recovery correction remains
  a release dependency. Run lineage's historical comparison parent is NOT child
  execution provenance and is not used as such.
- Direct refuses an existing consumer correlation; consumer refuses an absent
  correlation and its own preparer checks exact saved source/selection/context.
  Unknown, legacy, mismatched origin or corrupt prior start markers stay held;
  generic exceptions cannot rewrite prior-start ambiguity to failed.
- Direct public intake must reserve run/envelope/bindings atomically, not call
  `_prepare_run` or write initial events before the common start CAS. That public
  adapter is not yet connected at this checkpoint.
- Consumer v1 options remain `{}`. Its adapter's explicit 100/None execution
  defaults must be proven identical to existing effective behavior and shared
  by initial/recovery, with ambient-default-change regression. These are versioned
  execution semantics, not model/provider pins; the consumer lane owns proof.

## Red-first proof and review questions

1. Atomic origin/options/correlation stamp, immutable replay, legacy defaults,
   unknown version/kind/options refusal, and no file-shape origin inference.
2. Crash before dispatch recovers the same run and original direct options or
   exact consumer correlation; lost ephemeral callbacks never replay effects.
3. Saturated live pool versus restart worker starts exactly once through existing
   CAS; pre-start cancel/revoke/owner deletion refuses; started queued never runs.
4. Actual owned/remixed direct/version and canonical-consumer graph paths retain
   their source/provider fences; persisted snapshots/hashes/inputs stay exact.

Review only whether this missing provenance/option fact belongs in the existing
envelope and whether allowlisted recovery preserves authority/CAS. Do not reopen
file custody architecture or treat this as approval of final public runtime.
