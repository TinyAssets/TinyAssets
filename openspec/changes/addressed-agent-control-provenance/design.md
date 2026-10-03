# Design: preserve the addressed agent through controls

Design only, 2026-10-03. No runtime or schema change is present. This narrows the
implementation of harness section 4.18; it does not replace its shared-brain,
visibility, activities or delegation requirements.

## 1. Existing carriers and missing links

Source references below are at combined foundation `6684a082d923d7db6288b4639a507719b94ab742`.

| Existing owner | Missing integration |
|---|---|
| `addressed_agents.resolve`, owner/home admission, `custom_agents` binding revision and immutable definition fingerprint | Capture resolved agent facts once; a display name or parsed session alone is not authority |
| `universe_server.py:3135`, `turn_interrupt.LiveTurn` | Served registration omits the resolved agent; browser Stop omits it too |
| `interactive_http_agent.py:40`, `storage.agent_turn_journal.create` | Journal supports `agent_id`, but the adapter defaults it to main |
| `engine_mcp_server.py:897`, authenticated run admission and persisted `runs` | No addressed agent passes from chat to a run |
| `graph_compiler.BranchExecutionContext`, `NodeEnqueueContext` | Carry owner/universe and branch provenance, not addressed-agent provenance |
| `runs.py:4691,5387,6593`, `effectors.EffectChain` | Initial execution, nested dispatch and resume cannot reconstruct the acting agent |
| `authenticated_external_call.py:672`, `agent_review.py:171` | Rules/switches use main; review instructions use only shared `AGENTS.md` |
| `api.pending_requests.request_from_user`, storage dedupe/agent columns | Served creation omits agent; withdrawal lacks agent predicate; app answer relays omit request agent |
| `onboarding._handle_rules`, existing Rules panel | Owner reads and edits main regardless of addressed conversation |

`ExecutionSubject(kind=agent_runtime_manifest, ref, digest)`, automation activation
epochs, provider work receipts, and cloud continuation digests already exist.
They describe WHAT executes and authorize its work. Branch work must keep its
`branch_version` subject: relabeling it as an agent manifest would violate
`provider_work_authority._validate_work_lineage`. The addressed agent describes
WHOSE controls apply. Extend existing records with that attribution; do not add
another grant registry, capability token, scheduler or admission bypass.

## 2. One validated snapshot, existing authority still required

Use one versioned immutable value, called `AddressedAgentSnapshot` here. Store it
as a canonical document on the authoritative turn/run record; consumers use the
same parser and validator, not independent defaulting dictionaries.

| Field | Meaning and source |
|---|---|
| `version` | Exact supported document version; unknown versions refuse |
| `owner_user_id`, `universe_id` | Existing authenticated owner/current-home admission; never action input |
| `agent_id` | `main` explicitly selected at authenticated ingress, or the resolved owned binding id |
| `binding_revision`, `definition_id`, `definition_fingerprint` | Required for custom agents, captured from existing binding/definition records; absent for explicit main |

Main remains the existing seeded identity and serving authority; no synthetic
main binding is created. A serving-binding alias is normalized to main only at
fresh authenticated resolution. A captured custom identity can never become main
because its binding later becomes serving. Custom binding ownership means the
existing `created_by`/universe/conversable checks, not a new prohibition on
owner-installed definitions from other authors. Existing definition-access and
authored-branch execution checks remain independently enforced.

The snapshot is attribution and a freshness pin, not sufficient permission.
Owner, universe, binding revision and fingerprint must agree with authoritative
records. Duplicate owner/universe fields in enclosing records must match exactly.
Missing, malformed, mismatched or unresolvable custom identity refuses before
model launch, new tool dispatch, request creation or effect; it never retries as
main. Agent names are escaped display metadata, never identifiers or authority.

## 3. Capture and propagation

1. At `converse`, resolve the selector after existing authenticated owner/home
   admission, capture the snapshot, and bind it to `LiveTurn` and journal creation.
   The current binding is checked again at launch. HTTP and native turns receive
   the same identity. Preserve main's existing session and conversation keys.
2. Engine tool admission resolves its server-issued turn/run reference to this
   record and cross-checks the authenticated engine owner/universe. A route's
   `session` query, model JSON, branch input, environment chosen by a child, or a
   tool's claimed `agent_id` cannot select another agent's controls. Existing
   session parsing may locate or cross-check a record; it cannot mint the snapshot.
   Native launch metadata and HTTP dispatch must both bind the reference through
   existing trusted launcher/admission code. Scope the existing engine transport
   authentication/session to the exact admitted launch, rather than accepting
   any same-owner turn reference from a shared owner/universe bearer. An opaque
   reference is not a secret or proof of origin. Replaying another launch's
   reference, including main's from researcher, must refuse. This narrows the
   existing engine authentication boundary; it creates no independent effect grant.
   Unbound engine calls fail closed;
   they are not classified as direct owner calls because metadata is absent.
3. Run admission copies the validated snapshot into the existing run row in the
   same transaction that records owner/universe and run admission. Queue messages
   carry the run reference; consumers reload the persisted snapshot. Existing
   execution/claim fences remain mandatory. No queue body supplies a replacement.
4. Extend `BranchExecutionContext` and `EffectChain` with the validated snapshot.
   Normal compilation, immutable-version execution, retry and resume reconstruct
   it from that run record. Resume never uses the then-current browser selection.
5. Invoke edges copy the parent's snapshot while preserving/narrowing existing
   branch provenance. A child cannot name a different agent. A queued wake created
   inside a run copies the snapshot into its existing durable automation/work
   definition and thence its admitted run. Standalone human-created work captures
   explicit main at its authenticated creation boundary. Delegation to another
   agent is outside this change, not inferred from branch author or name.
6. For activity/agent-manifest work, resolve the agent through the already bound
   immutable execution subject and activation/receipt lineage. Persist that same
   validated projection on the turn/run; mismatch with its subject refuses. Do
   not independently select a binding or overwrite the existing subject/digests.
   If the subject resolver cannot establish the addressed binding, hold the work.

No runtime reader derives authority from request text or scans arbitrary session
strings. The implementation must enumerate every run creator/resumer and existing
record digest serializer before changing the schema; identity becomes part of
any digest that commits the enclosing input, without weakening old verification.

## 4. Current checks, changes and revocation

At authenticated ingress, queue claim, model launch, each new tool/effect dispatch,
and resumed/nested admission, recheck current owner/home admission, existing
execution authority and the custom binding's creator, universe, conversable status,
revision and definition fingerprint. The snapshot never revives revoked ownership.
Recheck current rules and review switches for that agent before each effect;
capturing an older permissive rule with the turn is forbidden.

Any custom binding revision change, including a rename, holds further dispatches
from the old snapshot. Deletion, unavailable definition, ownership/home change,
loss of existing execution authority, or promotion to serving also holds them.
The response names stale/held work without exposing a foreign binding. Previously
completed effects remain recorded. The owner can start a fresh turn under the
new revision; the platform must not silently rebase or replay old effects.
An in-flight external call may finish under the existing journal's outcome rules;
revocation does not claim to undo it. No additional call is admitted afterward.

Freshness checking and marking a dispatch admitted require a common authoritative
ordering with binding/owner revocation; a free-standing check followed by a later
send is insufficient. Reuse existing authority transactions/claim fences. For
rules stored separately, implementation must serialize rule-change acknowledgement
with dispatch admission (including other workers), document the linearization
point, and test both race orders. A change acknowledged before admission must
win; a dispatch admitted first is in flight and its outcome is recorded. This is
an implementation acceptance condition, not a claim that a process-local lock
or two SQLite reads already supply that guarantee.

## 5. Existing controls select this identity

**Rules and auto-review.** The effector receives the validated snapshot from its
chain and supplies `agent_id` to both `decide` and `review_refusal`. Existing
standing connection grants, operation classification and tighten-only review
semantics stay intact. Shared operation-kind declarations remain shared and are
labelled as such in the existing panel. Review evidence includes the pinned custom
definition's responsibility/instructions plus shared harness instructions, all
inside the existing untrusted envelope. No provider call uses host credentials.
Missing provenance is a hold even when main's review switch is off.

**Owner Rules door.** GET/POST resolves the addressed selector within the caller's
current home, reusing existing owner checks. The panel captures its agent identity
and revision at load; submissions include that expected revision and refuse a
stale selection. List, set, delete and review-switch calls select that agent.
Rule deletion checks its stored agent as well as row id. Existing consequence
confirmations remain required. Late responses never repaint another agent's panel.
The current selector suffices; no new role manager or UI layout is proposed.

**Pending requests.** Add server-only snapshot input to the existing API; the
model's payload cannot supply it. Reuse `pending_requests.agent` and scoped dedupe
keys; carry the custom revision pin with the request. Creation, dedupe, suppression
and read projection use the asking agent. An identity-bound served withdrawal
requires the same agent in the guarded UPDATE, in addition to pending/origin and
existing owner gates. Human owner access to all requests is preserved. Changing
agent must never lift a mute or reuse another agent's standing decision. A custom
revision change invalidates automatic reuse of old action approvals; retain their
history and require a new current-revision ask, without rewriting old keys.

**Answers and notifications.** Route a successful answer/reply from the stored
request's agent, not the currently open chat. Notification metadata and answer
events retain that agent; no new event subscription authority is created. Recheck
current scope before action-bearing acceptance or automatic continuation. If a
request's agent was deleted/revised, keep the answer/history visible and report
continuation held; an owner may still dismiss it. Do not redirect to main. A
credential already deposited successfully is not reported as failed because a
subsequent continuation is held, and its bytes never enter the relay/journal.

**Stop and journals.** Register the captured agent on the live turn and journal.
Stop targets `(verified owner, universe, addressed agent)` from the page's captured
in-flight selection. Separate explicit stop-all retains owner-scoped cancellation;
absence of agent no longer accidentally means stop-all. A deleted agent's running
turn remains stoppable using its server-owned live identity after owner/home
validation; stop narrows authority and must not require a still-live binding.
Cross-owner requests stop nothing. Stop retains current between-tool/native/HTTP
cancellation semantics; it does not silently cancel background workflows.

## 6. Legacy and rollout contract

New direct owner requests omitting the selector explicitly capture main at
authenticated ingress; ordinary main behaviour, keys, rules and review defaults
remain unchanged. Agent-aware engine requests missing their snapshot refuse.

Existing `main` defaults on migrated journal/request rows are not proof that a
custom-agent turn did not create them. Completed historical rows keep their
existing display, with provenance marked unverified when read as control evidence.
They cannot authorize a future effect. Old queued/resumable work without a verified
snapshot is held for owner reissue unless authoritative immutable lineage proves
its identity; no inference from timestamp, run name, current selection or default
column is permitted. Old stored asks remain readable/dismissible; ambiguous
action-bearing acceptance/automatic continuation requires a fresh pinned ask.
Known historical main review-switch migration remains valid because it records
the old global setting, not provenance of a custom run.

Use nullable/versioned storage additions, with schema-shape tests and transactional
writes; do not backfill ambiguous work as main. Old binaries must not execute new
snapshot-bearing work while ignoring its controls. Rollout must drain/fence old
workers before enabling the new schema/consumer contract; rollback must hold new
work or drain it before restoring an old executor. No deployment is part of this
design. Implementation review requires a concrete mixed-version refusal proof.

## 7. Worked example and proof plan

One owner has main and researcher in one home. Researcher has `app.write=hand_off`;
main allows that class and its review is off. Today `_rule_refusal(u, 'demo',
'POST', '/')` uses main and returns `None` even though
`decide(u, 'app.write', agent='researcher').proceeds` is false. This characterization
only calls the rule door, not transport; standing connection gates are still real.

After implementation, researcher's authenticated turn creates snapshot R. A graph
run, queued child and resumed run all carry R. At the real effector door the
researcher rule returns `rule_hand_off`; outbound dispatch count stays zero.
Main's separately admitted run selects main and can pass its rules only if every
other existing consent/authority gate also passes. Neither can select the other's
snapshot through tool arguments. Researcher's request is distinct from main's
identical ask; answering it while viewing main continues researcher or reports a
stale hold. Stop on researcher leaves main's live turn untouched.

| Required proof, using fake transport/provider only | Acceptance |
|---|---|
| Served main/researcher turns through actual run admission and effector door | Distinct rules/switches, journal attribution and request ids; researcher hand-off sends zero calls |
| Nested graph, delayed queue, process-restart resume, manifest activity | Same validated identity survives; subject/owner/universe mismatches refuse |
| Foreign/co-admin/other-home binding and forged tool/session/queue payload | Uniform refusal before row/effect; no foreign data, no default-to-main fallback |
| Binding revision, deletion/promotion, owner/home revocation and rule update races | Acknowledged revocation wins before next admission; in-flight outcomes recorded, no replay |
| Request dedupe/mute/withdraw and answers while another agent is selected | No cross-agent settlement/withdrawal; original agent receives answer or explicit held status |
| Owner Rules panel and Stop, two tabs/two agents | Current-home/agent/revision gate, stale-response fence, addressed stop plus explicit stop-all |
| Legacy main, ambiguous old work, corrupt snapshot, mixed executor versions | New main remains compatible; ambiguous/stale work holds; no migration grants authority |

Mutation checks must kill removal of the agent predicate, current-home check,
revision comparison, persisted identity on resume, and main-fallback refusal.
Run affected heavy tests, Ruff and mirror/import checks for the implementation;
Linux is required for any process/fencing proof. A fresh cross-family implementation
review and grouped owner live pass precede claiming complete per-agent controls.
