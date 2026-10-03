# Exact ordinary receipts: revised design gate

Baseline: main `8a8ec275`; proposed protocol, not current runtime behavior.
The four first-review findings are accepted and addressed below. Runtime work
remains gated on independent review and deletion/privacy coordination.

## 1. Separate preparation from execution

`POST /app/turn/prepare` is authenticated owner-only storage work. Validate current
home, addressed agent, ACL, deletion/reset barrier and caller payload. Reject
mixed consumer/ordinary envelopes. A fresh preparation may select the existing
consumer negotiation path without creating an ordinary receipt or running work.
An ordinary preparation allocates a NEW server ID, retaining original message,
input method, normalized model choice, owner/home/agent, explicit queued IDs,
digest version and immutable payload digest. The caller cannot choose/reissue ID.
The ID is a non-secret identifier, never a bearer capability. Use the existing
canonical digest helper; never hash refreshed history/prompts as caller intent.

Preparation commits three bounded steps: (1) author row PREPARING; (2) exact input
custody in steering; (3) immutable custody snapshot/digest attached in author row,
then PREPARED. No step consumes input for inference, runs tools, or dispatches.
Concurrent claims cannot steal each other's rows. Missing IDs/conflicts hold the
preparation; no partial claim or text substitution. A crash after either early
commit preserves inputs and leaves the receipt non-executable. No implicit repair
of PREPARING to executable state. Lost prepare responses may leave held drafts;
owner-scoped pending records may expose their exact receipt binding for recovery.
That is observation only; browser must not silently prepare/send a replacement.

The app persists returned receipt ID AND pinned payload/destination before sending
`converse(ordinary_request={version:1, receipt_id:...}, ...)`. Storage failure means
no converse. Any supplied ordinary envelope is looked up in exact owner/home/agent
scope BEFORE dynamic consumer negotiation, carryover, interactive_turn, open_turn,
subscription refresh or provider/effect work. Missing/malformed/conflicting keys
fail closed; they never become unkeyed sends or consumer_request_required. An
existing ordinary preparation stays ordinary even if a consumer was selected
later, or is held if policy no longer permits it. It is never converted/rekeyed.

Under existing author BEGIN IMMEDIATE and scope/deletion guards, verify immutable
payload, attached custody and current issuing BOOT, then CAS PREPARED -> STARTED.
Only that live invocation receives one dispatch permission. All other phases
return observation/held. The first insertion does NOT dispatch; this explicitly
replaces the earlier insertion-winner design. Commit STARTED before provider input
exposure. Crash immediately after it means unknown, never a second winner. The
original message is retained in the receipt; initial carryover must additionally
commit its attempt transition before being included in the first provider input.

Manual resend of the identical ID cannot repeat a STARTED operation. Repeated text
with a new server ID represents a separate explicit user intent. No automatic
POST replay or automatic new preparation for an uncertain send. Legacy unkeyed
clients keep their existing behavior but cannot claim receipt-owned input rows.

## 2. Durable input custody throughout existing steering paths

Extend the EXISTING steering rows with immutable original ID/text and exact
owner/home/agent/receipt binding plus a custody state. Do not create another
execution-authority database. States: queued -> claimed -> attempted;
closure freezes them as closed_claimed / closed_attempted. Attempted means exposed
or possibly exposed to inference, never proof of completed delivery or zero effects.

| Existing operation | Keyed behavior required before exposing/deleting anything |
|---|---|
| claim / take_carryover | preparation transaction retains exact rows as claimed; returns stable identities; no deletion/text matching |
| open_turn | root must be STARTED; link existing live_id separately; stale keyed roots freeze/hold custody, never delete/requeue |
| enqueue | while root/open input frontier is current and open, commit immutable bound row before acknowledging it |
| take | validate STARTED/current BOOT/open frontier; commit claimed -> attempted before returning exact input to model |
| settle | atomically freeze frontier, retain all claimed/attempted rows and exact states |
| stale cleanup / legacy APIs | exclude receipt-owned rows from destructive legacy paths; freeze without granting new execution |

Initial carryover is part of the first take/attempt transition, not merely read
from the preparation snapshot. Account/home/agent changes cannot retarget it.
No insertion is permitted after frontier freeze. Snapshot all receipt-bound inputs
after freeze, including late enqueues, with exact IDs/text/state. Unknown roots
retain their attempted and unattempted inputs; neither becomes automatic carryover.
Only after exact terminal + durable history acknowledgement may cleanup delete
closed_attempted rows or release demonstrably untouched closed_claimed rows.
Cleanup uses terminal snapshot IDs and matching receipt/state, never current text.
Immutable snapshots survive cleanup; original user data follows ordinary retention.

## 3. Transactions, journal link, and terminal

Retain current main's single-writer/no-handover assumptions. Lock order is existing
maintenance barrier -> author BEGIN IMMEDIATE -> ONE steering OR history writer;
never acquire author from inside a subordinate write or hold both sub-writers.
Hold the author guard across each small subordinate mutation so deletion/current
home changes serialize against it; release all SQL writers before providers.
Cross-store commits are intentionally separate; each gap holds input or allows
only idempotent projection. Do not claim atomicity from attached WAL databases.
Audit/refactor every affected legacy entrypoint to respect this lock order; use
transaction-aware internal helpers, not recursive writer connections.

Add receipt methods/tables to the existing authority DB and link receipt to exact
child journal IDs in the same author transaction as AgentTurnJournal.create.
Pass an internal non-authorizing reference through UniverseContext and the served
adapter; preserve existing IDs/Stop identity/checks. Do not expose it to the model.
The raw call_provider fallback is gated by the same root admission; it must not
invent a child ID or infer completion from a newest journal row. No changes to
foreground_run_provider, agent_review, provider dispatch authority or effect review.

After all existing work settles and the input frontier freezes, commit a full
root terminal envelope with immutable input snapshot and success/failure certainty.
Use current BOOT and scope checks. Child completion alone is not root completion.
Failure to save the terminal keeps recovery unknown even if a direct response
contains earned output. Failure/effects certainty uses existing normalized fields;
no invented notSent. Terminal publication cannot re-enter provider work.

Project completed/failure founder/interjection/reply rows in one conversation
transaction, with UNIQUE receipt marker + terminal digest + exact row IDs. Same
projection is no-op, changed digest refuses. Then acknowledge projected in author
state. If history commits and acknowledgement crashes, repeat only projection.
Cleanup follows that acknowledgement. Terminal reads NEVER repair history or state.
Terminal snapshot remains the recovery source when optional projection is delayed.

## 4. Missing history, initialization, reset and privacy

Only explicit startup/migration establishes the receipt schema in the existing
author DB; prepare/converse/read do not call creating journal connection helpers.
Validate migration/version readiness before prepare/start; missing/partial schema
fails closed. Strict reads use validated existing DB_FILENAME paths and mode=ro,
not db_path() (which migrates legacy names) or connection() (which creates schema).
A caller-supplied ID is LOOKUP ONLY in every schema state; converse never inserts it.
Thus deleting/resetting the entire receipt table cannot turn an old ID into a fresh
admission even after legitimate reinitialization. New preparation always issues a
fresh ID and cannot resume an old record. No permanent content tombstone is needed.

Bind PREPARED to existing boot-scoped BOOT.boot_id. A restored PREPARED snapshot
from a previous process cannot dispatch. BOOT is an additional refusal check, not
authority, leader election, or provider-work permission. No TTL takeover. Restoring
any database snapshot/reset that could roll back STARTED to PREPARED requires
quiescing the writer and retiring that process/boot before serving again. In-place
rollback while the same process serves is unsupported and must fail the maintenance
gate. STARTED old-boot work remains unknown; only verified terminal projection can
be repaired without executing. Existing no-handover restriction stays intact.

Receipt/custody/projection content is ordinary owner data with existing retention:
conversation history is retained until existing explicit session/account deletion.
Do not extend retention to solve replay. Session deletion/reset must remove matching
intent, terminal and custody copies along with conversation content, or refuse via
existing active-work reset rules pending coordinated handling. Do not introduce an
indefinite account-deletion blocker for uncertain receipts. Deleting receipts is
safe for replay protection: their IDs will remain lookup-only and unknown forever.

Account deletion must first use existing tombstone/guard ordering to prevent writes,
then erase ALL SQL-visible owner content/receipt metadata, including former-home
copies. Inventory new tables in existing root satellite scanning and exact owner
classification. Inventory private .agent-sessions steering databases for exact
owner rows across validated homes; never delete another owner's shared directory.
Delete child projection/custody/open-receipt markers as well as payloads. Preserve
existing staged/quarantined path handling and deletion retry behavior. No new
content tombstones, retention exemption, privileges or credentials. Tests must
show concurrent writers refused after tombstone and another owner's data unchanged.
The model tests SQL-visible erasure, not forensic erasure, backup policy, or actual
account-deletion code. These lifecycle edits REQUIRE independent security/privacy
review and explicit parent coordination before any runtime implementation.

## 5. Owner-only recovery and app behavior

Add narrow prepare and receipt handlers/registrations without editing placement
handler bodies. Existing app authentication/same-origin conventions apply. Receipt
read requires current owner/home/addressed-agent; reject supplied owner. Do not
reuse mutating agent-binding resolution on the read path. Expose only exact scoped
receipt/envelope; missing/inaccessible stays unknown. No list-all endpoint, repair,
claim, dispatch, migration, event emission, or consumer selection side effect.

App stores per-ID entries so independent tabs/sends cannot overwrite each other.
Foreground/online/reload reads coalesce and fence late responses by login epoch,
owner, home, agent AND receipt. Settle only exact terminal identity. Auth/offline
failures preserve unknown; legacy records cannot be settled by history text.
Queued/steered IDs and existing live_id remain separate, linked identities.
Watchdogs remain active. No actual Android background acceptance is claimed.

## 6. Evidence and bounded implementation surface

`proofs/protocol.py` is a stdlib unittest model using three separate SQLite files,
real concurrent writers and committed crash gaps. It exercises the revised
protocol, not production routing, auth, migrations, providers, devices or erasure.
Model acceptance is necessary design evidence, never runtime integration proof.

After gate approval, proposed production surface is: app.html; universe_server
ordinary admission/route ordering; universe_intelligence context propagation;
served adapter plus agent_turn_journal receipt/link methods; agent_steering custody
APIs; conversation_store exact projection; narrow onboarding prepare/receipt routes;
existing schema/maintenance/reset inventory; separately coordinated account_deletion
owner inventory and private-store erasure. Add focused synthetic/browser tests and
mutation tables for data-loss/cross-owner guards. No #4308 dependency or new writer
outside the existing authority/steering/conversation stores.

Required production proof includes each admission/custody/projection crash gap,
initial carryover before exposure, legacy/new consumer coexistence, missing schema,
restored snapshots, scope changes, tombstone races, duplicate provider/effect count,
truncated/silent stream, genuine failure, offline/auth recovery, same-page/reload
and multiple surfaces. Exact-head implementation review and protected CI follow;
parent owns integration/deploy, then real Android acceptance remains required.

## 7. Independent review outcome: implementation blocked

Review of `e55431ce` closed the four original P1s and accepted the privacy direction,
but found a cross-process P1. The current-BOOT test in section 2/model assumes the
steering caller is the serving process. Actual engine_steering._take runs in a
separate engine process with its own BOOT identity. The implementation must NOT
copy this equality literally or trust a supplied boot ID as current authority.
The engine delivery/serving-incarnation contract and its bounded file scope need
parent coordination and a distinct-process retirement proof before this gate can
pass. See review.md for exact finding; no runtime implementation is approved.
