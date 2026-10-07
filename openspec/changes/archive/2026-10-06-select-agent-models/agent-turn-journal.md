# Durable HTTP agent progress: storage review boundary

September10,2026. Proposed implementation within tasks2.3/2.4; no runtime table,
dispatch or resume authority is implemented yet. This is the progress journal
required by the existing model-selection design, not a new workflow system.

## Verified seam and purpose

At feature d58c3010, ordinary universe_server.converse reaches served-request
provider assignment and served_provider_budget_reservations. Custom agent roots
and provider_invocation_reservations are a separate execution path; do not invent
a custom invocation to journal an ordinary conversation. The shared store's
connection uses storage.db_path, SQLite WAL and explicit BEGIN IMMEDIATE.

ProviderRequestCapability is live, nonserializable message authority. A transport
request id, saved policy or journal row cannot revive it. Recovered progress must
go through new, genuine owner-authorized execution and fresh assignment/grant
checks. The journal supplies no provider/tool authority, capability, credential,
endpoint, header or arbitrary URL. The existing engine_tool_client owns transport
and fresh route validation; the current admission path owns every inference.

conversation_store is best-effort final text memory, not effect ownership. Do not
put tool intents there. The new journal is the immutable turn-input/round/result
source for HTTP continuation, rather than a second mutable conversation buffer.

## Proposed data shape

Use a private storage/agent_turn_journal.py and immutable domain records, in the
same db_path database through SQLiteProviderWorkAuthorityStore.connection. Schema
creation happens before a caller transaction; each mutating operation requires
an active transaction and never commits implicitly. A thin store wrapper opens
BEGIN IMMEDIATE/commit and exposes these operations for the future executor.
No changes to existing authority event chains or settled-budget schemas.

Three versioned tables, all explicitly owner_user_id/universe_id scoped:

- agent_turns: server-generated opaque turn_id; immutable initial prompt/system
  snapshot preserving exact text; captured policy generation (nullable legacy);
  creation time; monotonic generation; frontier state and current round ordinal.
  This is not a client-selected idempotency key. Owner/universe is trusted caller
  namespace, not proof of authentication. Every read/write requires both values;
  a turn id alone cannot select another owner's data.
- agent_turn_rounds: turn_id plus positive round ordinal; source_ref/model id;
  exact advertised tool-schema snapshot; verified served reservation id plus
  binding generation/digest copied as provenance; request digest; state and exact
  decoded AgentReply snapshot. Usage fields are nullable actual observations.
  Reservation provenance is not authority or evidence of current entitlement.
- agent_turn_tools: turn_id/round ordinal/call ordinal primary identity; exact
  provider call id/name/argument string; state; canonical MCP result JSON and
  fixed outcome diagnosis. Provider call ids are unique within a response batch,
  not a global durable id across rounds. A future codec integration will address
  currently rejected cross-round reused ids using real fixtures; no invention
  or rewriting of returned wire ids in this storage slice.

Root and round/tool foreign keys use owner/universe and their composite parent
identity. No FK to budget rows: normal settled-budget pruning must not erase
tool-effect evidence. The provenance snapshot remains after those rows age out.
No credentials, capability nonces, bearer, route secrets or auth-home paths are
stored. Payloads are private owner data; errors/log/repr never contain them.

All snapshots use strict JSON with duplicate/nonfinite rejection and explicit
schema version; reads validate typed fields and exact duplicated identity/state
columns. Corruption is held, never treated as a missing turn. Use existing codec
records for validated text/tool replies rather than a parallel provider parser.
Stored tool results retain canonical MCP content/structuredContent/isError,
including standard image/audio/resource blocks; exclude transport metadata and
unrecognized envelope extras. A known non-text result must remain preserved
even though the current text codec cannot feed it to a model. Mark that turn
unsupported rather than discarding the result or pretending the effect is unknown.

## Proposed transition contract

One generation-CAS frontier per turn prevents two workers advancing it together.
Every operation carries expected_generation. A mismatch returns a fixed conflict
without mutation. Equivalent finalization is idempotent only for byte-identical
identity/payload; conflicting duplicate results refuse, never overwrite.

1. Create root at generation1, ready for inference, from trusted exact input and
   owner namespace. No provider or tool launch occurs. Actual caller integration
   must create it only after genuine conversation authority is checked.
2. Begin round records the exact candidate/tool inventory and admitted reservation
   provenance, and advances ready -> inference_started before network dispatch.
   Only one active round can exist. Future executor must use fresh admission for
   this one inference; this operation does not reserve, spend or refresh authority.
3. Finish inference preserves one complete validated reply and actual nullable
   usage; atomic whole-batch insertion creates planned tool rows in returned
   order. Completed text advances to completed; tool requests to tools_pending;
   refusal/truncated/filter/unknown to a named held state, never completed.
   An inference transport failure can be recorded separately as held; storage
   does not decide whether capacity permits retry or another model.
4. Before each tools/call, atomically move the next planned row to started and
   commit. Earlier calls must have completed results; no skip or reorder. Only
   the winning CAS can dispatch. Caller must stop if that commit fails.
5. After a real MCP result, store it exactly and move started -> completed.
   isError is a known returned tool result, not permission to replay. When the
   whole batch has completed results, frontier becomes ready for next inference.
   Non-text projection holds with its known result intact.
6. Post-dispatch exception/cancellation/invalid result becomes unknown and holds
   the turn. If the process dies between intent and result, started is already
   ambiguous on recovery: never turn it back to planned, including after a timer.
   A missing durable result after a successful effect is still unsafe to replay.
   Pre-dispatch rejection may record not_sent, but only from explicit transport
   evidence; absence of a result is never that evidence. No implicit retries.

Persisted inference_started, started/unknown tool, or corrupted state cannot
automatically resume. A terminal result record may be recovered only as data,
not as authorization. Pure loading is read-only and never claims work or retries.
The later executor must revalidate owner/home/deletion/assignment/grants before
resuming any planned action, and a stale worker's later result must fail its CAS.
Never reuse a cancelled live capability or refill a spent invocation carrier.

No transparent exactly-once claim across remote effects: commit-before-send
can leave a harmless but indeterminate intent when a process dies before send.
Failing closed there is required; do not add a lease that silently replays it.

## Retention, deletion and rollout

The new tables own private owner data, not published or settlement history.
Retain unresolved progress without automatic pruning. Initial implementation has
no independent cleanup timer or silent payload truncation; completed history
retention can later reuse explicit owner data-retention policy. Account/universe
deletion must remove roots and all rounds/tools, including former-home rows;
add owner-key entries to account_deletion's existing exception map as needed.
Test deletion against real dependency ordering/foreign keys, not just a map.

Additive create-if-absent schema with version validation; no old rows rewritten.
Rollback to the older runtime leaves journal tables inert, not replayable. Do not
activate the HTTP agent or picker in this storage-only commit. Cost-constrained
agent request transport, finite trusted launch plan, per-inference accounting,
cross-model transcript conversion and resumed-owner execution are the next
integration, not something a journal alone proves. No public MCP/API changes.

## Required review and tests

Review exact table/transaction seam and record ownership before code, especially
whether three tables are necessary or a smaller equivalent preserves identity,
generation-CAS, whole-batch insertion and known-result retention without new
authority. Resolve post-dispatch cancellation and recovery states explicitly.
No source of rank, model choice, cost authorization or background-self ownership
changes. Bound runtime by existing resource/authority policy, not a new arbitrary
workflow-size limit hidden in storage.

Prove real SQLite concurrent start/finalize races, rollback-on-fault before send,
duplicate exact/conflicting results, no partial batch insert, scope isolation,
malformed/corrupt records, crash after committed intent, known isError and
unsupported-content result retention, reused wire id in another round, budget
pruning independence and real account deletion. Use synthetic data and actual
Linux as well as Windows. No live workflow, provider or permission edits.

## Applied independent ADAPT352s, September10 04:23UTC

Full shape review at8480ec10 is committed in
docs/reviews/2026-09-10-agent-turn-journal-shape-review.md. The following exact
refinements supersede ambiguous prose above before any storage code:

1. finish_tool receives raw validated MCP CallToolResult, not codec.ToolOutcome.
   Journal projection retains the standard content-block union, excluding meta
   and unknown envelope extras, plus structuredContent/isError. Derive
   content_kind from stored JSON; never accept it as caller truth. Only the
   read-side inference projection uses the text codec. Non-text completion
   becomes held_unsupported_result with its exact known result retained.
2. Finalization outcomes are applied/already_applied/conflict. Test a terminal
   target for byte-identical identity/payload/error/outcome BEFORE comparing the
   generation. already_applied never mutates or advances generation. Every
   differing replay is conflict. start_tool never returns a reusable dispatch
   right from an already-started row.
3. Both composite child foreign keys use ON DELETE CASCADE. All three tables
   carry owner/universe and enter the existing person-keyed deletion exception
   map. Exercise real deletion of current and former-home rows, with another
   owner's rows preserved and receipt counts including cascaded children.
4. Classify all three tables for scoped reset in the same implementation. This
   journal is effect evidence, so preserve completed records and block matching
   active/ambiguous progress; do not add unreviewed operator deletion authority.
   Use preserve_or_block plus explicit matching-turn inspection, not a label
   alone. Scope by exact owner/home, leaving unrelated resets unblocked. Existing
   unrelated unclassified tables are baseline, not excuse to omit these three.
   No resettable-column map is needed for preserved, non-deleted tables.
5. ensure_schema refuses an active transaction and executes individual CREATE
   statements, never executescript. It cannot commit caller work. Mutating
   operations require an active transaction; wrapper commits before dispatch.
6. Tool states are planned/started/completed/not_sent/unknown. not_sent/unknown
   are terminal held outcomes; a thrown cancellation maps to unknown after
   start. Turn states are ready/inference_started/tools_pending/completed,
   held_refusal/held_truncated/held_filter/held_unknown_stop/held_transport,
   held_tool_not_sent/held_tool_unknown/held_unsupported_result. Corruption raises
   a fixed corrupt-read error, never synthesizes an executable state. Round
   provenance includes binding_id, reservation id, binding generation and digest;
   actual usage remains nullable, without reservation-estimate backfill.

Use a partial unique index for an active inference round, versioned AgentReply
snapshot/load validation without changing the approved codec, and the shared
ledger timestamp helper. Loading and finalizing never mint authority or replay.

## Initial implementation, September10 04:44UTC

Private agent_turn_records.py and agent_turn_journal.py now implement the three
tables and reviewed transitions. Input/provenance/reply/result snapshots carry
version1; round states are inference_started/received/failed. Binding identity,
generation and digests live in the validated candidate snapshot, not duplicated
SQL columns. Actual token observations stay in the validated reply; cost is
nullable integer microusd and is never supplied from a reservation estimate here.
The existing codec is unchanged. There is no live caller or execution authority.

For scoped reset, ready/inference_started/tools_pending/held_tool_unknown block
the exact owner/home. Semantic and known terminal holds cannot be resumed by
this store, so they preserve evidence without blocking reset. Corrupt matching
data blocks; unrelated owners/homes do not. This is inspection, not new deletion
authority. Account deletion uses its existing counted sweep plus cascading child
foreign keys and owner-key overrides to remove former-home history as well.

Initial Windows run:87 passes,0 skips across new journal and account-deletion
tests. Linux, exact independent implementation review and runtime integration
remain pending. No full-agent readiness or end-to-end fallback claim follows.
