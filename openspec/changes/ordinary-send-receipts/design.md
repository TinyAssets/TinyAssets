# Ordinary receipts: security/concurrency design gate

Reviewed source baselines: main `8a8ec275`; held #4308 `cedc4f6d` (read by Git
object only). These are proposed interfaces, not existing runtime capabilities.

## Admission and state

At `universe_server.converse`, parse optional `ordinary_request` containing exactly
version 1 and canonical UUIDv4 request_key. Refuse simultaneous ordinary and
consumer envelopes before either route can dispatch. Resolve owner, current home,
ACL and addressed agent using existing authenticated doors. Select the ordinary
route before reserving. Consumer negotiation must never dispatch an ordinary
request or reinterpret its key as a consumer key.

Reserve before `_with_carryover`, `interactive_turn`, `_open_steering`, subscription
refresh, provider work or tools. The canonical input binds unmodified message,
input method, normalized model choice and resolved owner/home/agent. Live context,
current history, generated prompts and consumed carryover are separate snapshots,
not recomputed parts of the caller's digest. Use existing canonical digest helper.

Add receipt tables/methods to `storage.agent_turn_journal.AgentTurnJournal` in the
EXISTING authority database. Do not add a second DB or a new unfenced SQL writer.
Primary scoped uniqueness is (owner, universe, agent, key_hash). A server receipt
ID identifies the row; store digest version, intent digest, original input, phase,
exact terminal envelope, owner generation and exact associated journal IDs.
Existing key+digest returns observation, not permission. Changed digest rejects.
Only the insertion winner in the current live request continues. Nothing obtains
a dispatch right by reopening a row, reading status, expiry or process takeover.

Use #4308 `_transaction(universe)` and `check_fence` INSIDE its BEGIN IMMEDIATE,
plus existing current-home, deletion/reset and owner checks. Admission must fail
before any work when reservation is unavailable. Do not do provider calls or
cross-database callbacks inside the author writer. No optional-import fence
fallback, no generation-zero fresh receipts, no clock/TTL ownership takeover.

Phases: accepted, running, completed, failed, unknown/held. Only committed terminal
payload is complete/failed; journal failure is not proof of zero effects. A missing
row, missing schema, unreadable store, lost generation or crash gap is unknown to
the reader. Existing acceptance without a terminal can remain held forever; no
retry may convert that into a new start. Successful identical-key POST is also
observation-only. A genuinely never-admitted first POST can reserve normally.

## Internal journal link

`universe_intelligence.converse` carries an internal non-authorizing receipt ref
in the authenticated `UniverseContext`; `ServedChatAgentAdapter.create_turn`
passes it to `AgentTurnJournal.create`. The journal insert and receipt link commit
atomically in the same fenced author transaction, checking exact owner/home/agent.
Preserve journal turn IDs, generations, native/HTTP semantics and all existing
provider checks. The reference is neither a bearer capability nor model input.

`_call_writer` has a non-coordinator `call_provider` route too. Its root receipt
still gates dispatch; do not fabricate a journal ID or use newest-row matching.
When no exact child terminal proves the full root outcome, preserve unknown.
No edits to foreground_run_provider, agent_review, provider dispatch authority or
effect review are planned. The root terminal covers the entire handler, including
its existing learning and steering settlement, not just a model's last token.

## Cross-database input invariant — required dependency

Today `agent_steering.take_carryover` deletes rows before execution; `claim` deletes
before the browser POST; `settle` deletes delivered rows before conversation save.
Reservation alone does NOT fix the gap. Author DB, steering DB and conversation
DB are separate; attaching WAL databases does not establish crash atomicity.

Proposed integration-lead queue change: retain immutable input custody in the
existing steering DB, bound to exact server receipt/session and original steer
IDs. A steering transaction moves/copies claimed rows into receipt-bound custody
before deleting/reclassifying live rows. Commit custody first; then attach its
immutable digest/reference to the root receipt. No provider starts until that
attachment is committed. A crash anywhere leaves recoverable custody and held
request, never requeues unknown-delivery inputs. Do not automatically take custody
from an older request. Explicit settlement retains delivered/undelivered identity
until terminal publication confirms which was handled.

Browser-side `claim` cannot delete inputs before it has a durable root request
binding. It must carry the pinned request key and exact IDs, atomically record
custody locally in the steering transaction, and return the stable claim. Identical
claim/key is observational; different keys cannot claim the same held IDs.
The queued-send body must be digest-bound to its claimed IDs, not matched by text.
Fresh root admission with mismatched or missing custody holds/refuses before work.

This ownership/protocol expansion is NOT yet granted to this lane. It is a design
P1 until the integration lead assigns the queue change and the reviewer accepts
the complete protocol. Do not implement a receipts-only shortcut and claim all
pending/steered inputs are crash-safe. Existing process-local live_id stays intact;
receipt identity is linked separately, not substituted for Stop/steering identity.

## Exact terminal and history

After all existing work settles, write the full response envelope to the root
receipt under its original generation before reporting durable terminal success.
Never synthesize a fresh provider response after a terminal-write failure. Direct
response may still convey earned output, but recovery remains unknown if the
terminal could not commit. Genuine failures carry existing normalized effects
certainty, not an invented notSent classification.

`conversation_store.record_exchange_turns` / `_record_pair` gain an exact projection
identity. One conversation transaction inserts the complete founder/interjection/
reply pair and a unique projection marker binding receipt ID + terminal digest to
exact row IDs. Repeating the same projection is a no-op; changed digest refuses.
Then mark the receipt projected in a separate fenced author transaction. Crash
between these commits can repeat only this idempotent projection, never execution.
Run projection from terminal-writer/reconciler paths, not the read endpoint. Held
terminals retain their envelope even if optional history storage is unavailable.
`record_failure` follows the same exact projection mechanism. Text/time are never
used to locate, delete or settle a request.

## Read endpoint and client

Add `_handle_turn_receipt` plus one route in onboarding/__init__.py. Require the
existing owner app identity, same-origin JSON, current-home and addressed-agent
resolution. Arguments: universe_id, agent_id, request_key. Ignore/reject supplied
owner. Use non-creating read-only connections; no migrations, claim, execution,
projection repair or event emission. Return only this exact scope/key's receipt
and committed terminal envelope; absence and inaccessible scope disclose no data.
A receipt is never authority for another operation. No list-all receipt endpoint.

App persists UUID and pinned payload/destination before send; storage failure
means no send. Store per-key entries so multiple sends/tabs cannot erase each other.
Same-page foreground, online and reload reads reconcile exact IDs. Coalesce reads,
fence late results on login epoch/owner/home/agent, and settle only that key. Legacy
unkeyed records remain explicitly unconfirmed; remove text-matching completion in
restoreInflight/finishActiveTurn. No new automatic POST retries. Manual same-key
resend cannot rerun acceptance, while changed payload requires a new explicit intent.

## Retention, reset and deletion

Raw intent/terminal is owner data; preserve upload bytes and ordinary deletion
scope. Existing account_deletion and scoped_reset must inventory the new tables,
block resets of active/unknown receipt/custody links and delete dependent rows in
verified owner scope. Terminal detail compaction must retain a scoped tombstone
that prevents an old key becoming a fresh execution. No arbitrary TTL eviction.
This integration-lead scope must be assigned before implementation can be complete.

## Required proof and unresolved gates

- Racing identical POSTs: one reservation, provider/effect execution once; same
  key changed payload rejects, repeated text/new keys remain separate intents.
- Crash injection before/after reserve, custody, child journal link, inference,
  external effect, steering settlement, terminal commit and history projection.
  Every gap is either a proven terminal or held; no uncertain work is reissued.
- Author/steering/conversation transactions never create cyclic lock order;
  no provider runs under a SQL writer; double projection creates exactly one pair.
- Owner/home/agent/key substitution, deletion/reset race, stale generation and
  late UI answers disclose no other thread and cannot mutate its receipts.
- Before-admission loss, lost completed reply, genuine provider failure,
  truncated/silent stream, auth renewal, offline/online, same-page/reload,
  multiple pending sends and surfaces, old/new client and consumer interoperability.
- Repeat fenced mutation proofs against exact coordinated #4308 baseline; no
  modified held branch and no new unlisted writer. Native fallback is covered.

Gate remains OPEN: independent design review; fenced baseline/dependency choice;
integration-lead custody/reset ownership and a fully specified custody transition
contract. No implementation is authorized by a conditional design approval while
one of these correctness dependencies remains unresolved.
