# Design: preserve the addressed agent through controls

Design only, 2026-10-03. No runtime or schema change is present. This narrows the
implementation of harness section 4.18; it does not replace its shared-brain,
visibility, activities or delegation requirements.

## 1. Existing carriers and missing links

Source references below are at `origin/main`
`3e1587b3e500d81919c0c299b862d31b5360757c` (re-verified 2026-10-03). They were
pinned to a combined pre-merge foundation sha that never reached `main`, so two
had already drifted and three pointed past the end of the wrong file (design
review 2026-10-03, finding 2). Each row now names the **enclosing symbol** as
well as the line, because the symbol survives the drift that made the first set
stale -- if a line has moved, trust the symbol and correct the number here.

| Existing owner | Missing integration |
|---|---|
| `addressed_agents.resolve`, owner/home admission, `custom_agents` binding revision and immutable definition fingerprint | Capture resolved agent facts once; a display name or parsed session alone is not authority |
| `universe_server.py:3135` `converse()`, `turn_interrupt.LiveTurn` | Served registration omits the resolved agent; browser Stop omits it too |
| `interactive_http_agent.py:41` `create_turn()`, `storage.agent_turn_journal.create` | Journal supports `agent_id`, but the adapter defaults it to main |
| `engine_mcp_server.py:897` `run_graph()`, authenticated run admission and persisted `runs` | No addressed agent passes from chat to a run |
| `graph_compiler.BranchExecutionContext`, `NodeEnqueueContext` | Carry owner/universe and branch provenance, not addressed-agent provenance |
| `tinyassets/runs.py:4691` `_invoke_graph()`, `:5387` `_execution_context_for_run()`, `:6593` `_invoke_graph_resume()`, `effectors.EffectChain` | Initial execution, nested dispatch and resume cannot reconstruct the acting agent |
| `authenticated_external_call.py:673` `_rule_refusal()`, `agent_review.py:175` `_responsibility()` | Rules/switches use main; review instructions use only shared `AGENTS.md` |
| `api.pending_requests.request_from_user`, storage dedupe/agent columns | Served creation omits agent; withdrawal lacks agent predicate; app answer relays omit request agent |
| `onboarding._handle_rules`, existing Rules panel | Owner reads and edits main regardless of addressed conversation |

`ExecutionSubject(kind=agent_runtime_manifest, ref, digest)`, automation activation
epochs, provider work receipts, and cloud continuation digests already exist.
They describe WHAT executes and authorize its work. Branch work must keep its
`branch_version` subject: relabeling it as an agent manifest would violate
`provider_work_authority._validate_work_lineage`. The addressed agent describes
WHOSE controls apply. Extend existing records with that attribution; do not add
another effect-grant registry, scheduler or admission bypass. The proposed
per-launch transport credential below is a narrow authentication capability: it
proves the caller belongs to one admitted launch, but grants no effect permission.
It is distinct from the non-secret snapshot and existing execution grants.

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
engine model launch, engine tool dispatch, request creation or effector admission;
it never retries as main. Agent names are escaped display metadata, never identifiers or authority.

## 3. Capture and propagation

1. At `converse`, resolve the selector after existing authenticated owner/home
   admission, capture the snapshot, and bind it to `LiveTurn` and journal creation.
   The current binding is checked again at launch. HTTP and native turns receive
   the same identity. Preserve main's existing session and conversation keys.
2. Engine tool admission resolves the reference through the proposed per-launch
   transport binding below and cross-checks owner/universe against its persisted
   turn/run. A route's `session` query, model JSON, branch input, child-selected
   environment, or claimed `agent_id` cannot select another agent's controls.
   Session parsing may locate or cross-check a record; it cannot mint a snapshot.
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

### Proposed launch transport binding (F2)

This protocol is to be implemented and proved; neither the existing shared
owner/universe bearer nor a non-secret turn reference provides it today.

- After authenticated admission, the trusted launcher generates a fresh random
  256-bit opaque launch bearer. Extend the server-owned launch lifecycle to store
  its digest bound to an immutable launch id, turn/run reference, owner, universe,
  snapshot digest, expiry and active/revoked state. Raw credential bytes never
  enter model inputs, journals, queue bodies, public projections or logs. Use TLS
  for any network hop; local delivery must be launcher-controlled and satisfy
  the isolation requirements below. No such delivery path is presumed available.
- Engine authentication accepts this credential only for the engine-launch
  audience. It resolves identity from the server binding, then cross-checks any
  supplied reference against that binding. Reference A plus launch B's credential
  refuses, even for the same owner or agent. Binding to a current admitted run
  still requires existing execution authority and the persisted snapshot; the
  token is not an effect grant or a user-controlled alternate provenance record.
  An authenticated parent may ask the trusted server to admit a nested run; that
  server records its immutable parent lineage and copies the snapshot. A new
  worker/child launch receives its own fresh binding to that admitted run, not a
  transferable parent credential or authority to choose an arbitrary run.
- The HTTP adapter retains the credential in trusted dispatch context. A native
  adapter receives only its own credential through a launch-private transport
  configuration exposed read-only inside its sandbox. It must not receive a
  shared owner bearer, another launch's credential, or the owner's public OAuth
  credential. Main's credential must be unreadable from researcher's environment,
  mounts, scratch/config files, process inspection and inherited descriptors;
  the converse holds too. A shared OS uid or a filename convention alone proves
  none of this. Implementation must prove sandbox/process isolation, including
  proc/ptrace/descriptor access, before enabling this path. If the existing jail
  cannot provide it, native launch binding remains held for that dependency;
  do not substitute a shared token or claim this design supplies isolation.
- The trusted supervisor revokes the binding on turn termination, Stop, loss of
  current authority, or expiry. Resume/retry/restart creates a fresh credential
  only after re-admission, invalidating the previous launch's binding first.
  A restarted server must refuse orphaned active bindings until it has fenced the
  prior launcher; persisted identity does not revive its secret. Concurrent calls
  within a valid launch remain possible under existing gates, but credentials
  from exited, stopped, superseded or foreign launches cannot be replayed.
- Direct owner calls to public MCP use the separately authenticated public actor
  context and capture explicit main at fresh admission. This is an authentication
  audience/surface distinction established by server middleware, never a payload
  flag, absent header, session name or claimed human origin. An engine credential
  is invalid at the public-owner door; a shared owner bearer plus a turn id is
  invalid at the launch door. A platform-served child has no access to the public
  owner credential. Unbound engine calls refuse rather than becoming owner calls.
  Public-owner authentication preserves existing public consent semantics; it
  does not claim that every caller is a human typing directly.

Credential lifetime, launcher registration and the exact sandbox delivery path
must be enumerated against the implementation's actual adapters and lifecycle
before activation. These are implementation acceptance obligations, not present
facilities or authorization to create credentials in this documentation change.

No runtime reader derives authority from request text or scans arbitrary session
strings. The implementation must enumerate every run creator/resumer and existing
record digest serializer before changing the schema; identity becomes part of
any digest that commits the enclosing input, without weakening old verification.

## 4. Current checks, changes and revocation

At authenticated ingress, queue claim, engine-controlled model launch, each
engine-admitted tool call and effector dispatch, and resumed/nested admission,
recheck current owner/home admission, existing execution authority and the custom binding's creator, universe, conversable status,
revision and definition fingerprint. The snapshot never revives revoked ownership.
Recheck current rules and review switches for that agent at each effector admission;
capturing an older permissive rule with the turn is forbidden.

Any custom binding revision change, including a rename, holds further dispatches
through these engine/effector doors from the old snapshot. Deletion, unavailable
definition, ownership/home change, loss of existing execution authority, or
promotion to serving also holds them.
The response names stale/held work without exposing a foreign binding. Previously
completed effects remain recorded. The owner can start a fresh turn under the
new revision; the platform must not silently rebase or replay old effects.
An in-flight external call may finish under the existing journal's outcome rules;
revocation does not claim to undo it. No additional engine/effector call is
admitted afterward under the revoked snapshot.

**Native-loop limit (F1).** Provider-internal native CLI tools do not pass an
engine pre-tool hook. This design cannot promise a freshness check before each
such tool, and prompts are not enforcement. Binding revision or authority
revocation marks the launch held, revokes its engine binding, and requests
termination through the trusted native supervisor, including its child process
group. Until termination is confirmed, internal native actions may continue;
already-started actions and effects outside the effector door are not undone.
Record termination requested, confirmed exit, or failure/unknown outcome honestly
in the journal, and keep work held on failure. Implementation must specify and
prove its bounded detection/termination deadline and escalation using fake native
processes on Linux; do not equate sending a signal with stopping the launch.
The separate D2 native-yield repair remains held: this change does not supply an
internal-tool interception hook or establish full native per-tool rule/yield
coverage. Neither design approval nor engine admission proofs unblock that claim.

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

**Recurring-definition blast radius and recovery (F3).** Existing recurring
interval, cron and event definitions also lack this snapshot. Unless an existing
authoritative execution subject proves their exact identity, every future firing
must hold, including ordinary main automations; an active desired state is not
proof. Old queued firings and in-progress resumptions remain independently
held and are never relabelled by reconfirming their definition.

**The hold MUST NOT activate before audience separation is live (task 4).**
This is a hard ordering constraint, not a preference. The hold's only exit is an
owner reconfirmation through the public owner automation door, and §3 states that
the design cannot yet tell that door apart from an engine call -- the automation
write door is also on the engine surface: `engine_mcp_server.py:3012`
`write_graph()` routes `target="automation"` into
`:2661 _write_served_automation()`. (The review cited `:2821` for this; that
line is inside `write_graph()` but is docstring prose about output links. The
claim holds at the lines above, re-verified on `origin/main` 2026-10-03.)
Shipping the hold first would therefore stop every recurring
workflow, including every ordinary main automation, with no reachable restart:
a self-inflicted outage on a surface the Forever Rule says must work with no
host online. Either land the hold together with task 4, or ship it inert behind
the same switch and enable it only once audience separation is proven. Any slice
that enables the hold must cite the passing audience-separation proof from task 4
(`launch replay / audience rejection`) in its own evidence (design review
2026-10-03, finding 3 -- raised as blocking before implementation).

**Grandfathering is a founder decision, not settled here** (design review
2026-10-03, finding 4). The draft proposed no grandfathering at all. The reviewer
observed that custom-agent `converse` first existed with #4287, merged
2026-10-03 01:41Z, so any definition authored before that moment can only have
come from the owner or `main` -- which makes stamping those explicit-`main` and
holding only later ones a clean cutover that does not halt live automations. That
is sound, and it is also exactly the kind of "trust a timestamp" inference the
paragraph above forbids, so the two cannot both stand unexamined: the question is
whether the #4287 merge time is authoritative enough to act as lineage. It needs
the founder's call, with the smallest ask recorded in `docs/host-actions.md`.
Until that is answered, assume no grandfathering, which is why the ordering
constraint above is what keeps the Forever Rule intact in the meantime.

Reuse the existing public owner automation door: `read_graph` targets
`automations`/`automation` and `write_graph` target `automation`, operation
`resume`, with `automation_id` and `expected_revision`. Today
`api.automations._control` only changes desired state; it does not reconfirm
provenance. Extend this existing operation with explicit
`payload_json={"confirm_agent_provenance": true, "agent_id": "main"}` (or the
owner's explicitly selected custom binding). An ordinary resume without that
confirmation must return the held reason and make no provenance change. The
owner must see the schedule, timezone, inputs and selected agent before issuing
this confirmation; do not synthesize owner consent from the existing active bit.

Only the stored definition owner, authenticated through the public owner surface
in their current home, may reconfirm. An engine launch cannot do so via forged
payload or an internal wrapper. Existing owner-or-admin pause/delete authority
remains; admin control of someone else's automation must not mint that owner's
snapshot. Re-resolve the chosen binding, its current revision/definition and
existing authored-branch/execution eligibility exactly as at fresh creation.
In one authoritative revision-guarded update, persist the fresh snapshot, advance
the existing activation/claim fence and activate only future work. Preserve the
schedule, timezone, inputs and overlap policy; do not replay missed firings or
historical effects. A concurrent edit/deletion/revocation wins or produces an
explicit conflict, never a partially reconfirmed definition. Retired definitions
stay retired. One-shot work is reissued via the existing create door, not silently
replayed by recurring reconfirmation.

The existing list/get projection must expose a durable effective `held` state,
`held_reason=agent_provenance_unverified`, the current definition revision and
`reconfirmation_required` for affected rows, even when `desired_state=active`.
The reason must not disappear when a transient refusal-ledger entry expires.
Explain that future firings are blocked, identify the owner reconfirm action and
show the selected identity after success. Do not present an executable next-run
promise for a held row. Before rollout, preview/count affected definitions via
this authorized owner surface and include the hold/reconfirm behaviour in owner
acceptance. This design performs no inventory or live migration.

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
| Launch B credential plus launch A reference (main/researcher and same-agent launches); expired/stopped/superseded binding; missing credential | Refuse before row/effect; no reclassification as direct owner, no shared-token fallback |
| Fake native researcher attempts to read main credential through env/files/mounts/proc/fds; launch credential at public MCP and public bearer at engine door | Isolation and audience refusal proved on Linux; absent isolation blocks native activation |
| Native turn revoked/revised mid-loop | No subsequent engine/effector admission; supervisor termination deadline/outcome recorded; internal-tool residual stated, D2 stays held |
| Foreign/co-admin/other-home binding and forged tool/session/queue payload | Uniform refusal before row/effect; no foreign data, no default-to-main fallback |
| Binding revision, deletion/promotion, owner/home revocation and rule update races | Acknowledged revocation wins before next admission; in-flight outcomes recorded, no replay |
| Request dedupe/mute/withdraw and answers while another agent is selected | No cross-agent settlement/withdrawal; original agent receives answer or explicit held status |
| Owner Rules panel and Stop, two tabs/two agents | Current-home/agent/revision gate, stale-response fence, addressed stop plus explicit stop-all |
| Existing main recurring definition missing snapshot; owner reconfirms through public resume; admin/engine/ordinary resume attempts; concurrent edit | Visible durable held state before confirmation; only owner CAS creates fresh provenance for future firings; schedule preserved, old queued work held, no missed-fire replay |
| Legacy main, ambiguous old work, corrupt snapshot, mixed executor versions | New main remains compatible; ambiguous/stale work holds; no migration grants authority |

Mutation checks must kill removal of the agent predicate, current-home check,
revision comparison, persisted identity on resume, main-fallback refusal, and
launch-binding replay/audience rejection.
Run affected heavy tests, Ruff and mirror/import checks for the implementation;
Linux is required for any process/fencing proof. A fresh cross-family implementation
review and grouped owner live pass precede claiming complete per-agent controls.

Grouped live acceptance must distinguish addressed chat Stop from background
work: Stop targets the selected live turn and reports its observed outcome;
its queued/background workflows remain
subject to their own explicit automation controls. Do not label the whole agent
or every workflow stopped because its chat ended.

## 8. First Claude ADAPT disposition

The review of `f302de7cef40c674473862b80ff1109530e375f8` is ADAPT, not an
implementation or activation receipt. F1 is addressed by section 4's engine-only
admission scope, native termination/residual contract and native proof row. F2 is
addressed by the proposed launch credential protocol, isolation/audience boundary
and replay proofs. F3 chooses explicit owner reconfirmation through the existing
automation control surface, with visible holds and the full recurring blast
radius. [Actual Claude design APPROVE](https://github.com/TinyAssets/TinyAssets/pull/4343#issuecomment-5965426542)
at `6bf7923597983ec9af61968b99001745a981f2a7` closes task 2 only. Cross-worker ordering,
actual launch isolation/lifecycle, termination deadlines and reconfirmation
transactions still require implementation proof; none is asserted available.

## 9. Second Claude ADAPT disposition (2026-10-03, lead's reviewer)

The review of `be52aa8191e97cd265beb58de5a67b36111fe89a` is ADAPT. Its item 1
(premises match `origin/main`) and item 7 (branch_version subject kept, native
limit stated honestly, one ordering tested in both race orders, proof matrix) are
AGREE and need no change. The rest is folded here:

- **Finding 2, citations pinned to a sha that is not on `main`.** Fixed in §1.
  Re-pinned to `origin/main` `3e1587b3e500d81919c0c299b862d31b5360757c` and
  re-verified line by line, which found more drift than the review reported:
  `authenticated_external_call.py` `:672 → :673` and `agent_review.py`
  `:171 → :175` as it said, and additionally that the three bare `runs.py`
  citations resolve only in `tinyassets/runs.py` — they point past the end of
  `tinyassets/api/runs.py`, so the bare filename was ambiguous between two real
  files. Every row now carries its enclosing symbol, so the next drift is
  self-correcting. `universe_server.py:3135` and `engine_mcp_server.py:897` were
  already correct.
- **Finding 3, the hold breaks the Forever Rule (blocking).** Accepted in §6 as a
  hard ordering constraint: the hold must not activate before task 4's audience
  separation is live, either landing together or shipping inert behind the same
  switch, and any slice enabling it must cite task 4's passing audience-rejection
  proof. While verifying this, the review's own citation for the engine-side
  automation door (`:2821`) proved to be docstring prose; the claim is true at
  `engine_mcp_server.py:3012 write_graph()` routing into
  `:2661 _write_served_automation()`, and §6 now cites those.
- **Finding 4, grandfathering.** Recorded in §6 as an open founder decision
  rather than resolved here, with the review's pre-#4287 proposal stated as the
  cheaper option and its one tension named: it trusts a merge timestamp as
  lineage, which this design forbids elsewhere. Smallest ask filed in
  `docs/host-actions.md`. Until answered, no grandfathering is assumed, which is
  what makes the ordering constraint above load-bearing.
- **Finding 5, the "no new permission/policy/setting" overclaim.** Reworded in
  `proposal.md`, which now lists the transport credential and its digest, the
  launch-binding table and snapshot columns, the new durable `held` state, and
  the public `write_graph` payload and projection fields — while keeping the
  accurate half: no existing permission is widened, because each control keeps
  its authority and only changes which agent it selects.
- **Finding 6, founder approval before implementation.** Filed as one
  `docs/host-actions.md` row with two asks: the grandfathering judgement, and a
  single go on the hard-to-reverse shape (public MCP surface delta needing a
  canary `--assert-handles`, the per-launch credential, the storage additions,
  the held behaviour). Money: none.

This disposition closes the design gate only. No implementation, activation or
deployment is granted, and the §6 held items (NativeD2 yield, cross-worker
ordering) stay held unless a slice needs them.
