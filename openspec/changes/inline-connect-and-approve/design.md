## Context and scope

At `95508ecdc0`, `app.html` polls the rail, duplicates connect surfaces, redirects OAuth and relays answers via `sendTurn`. `authenticated_external_call._rule_refusal` returns dry_run; storage emits answer events/requeues activities without durable originating-chat resumption. Extend these seams. The pi.dev-like bubble is permanent emergency/control plumbing; Muse models its interaction. Any platform uses generic connection data, never provider code. Directory/app registration, D6 and D5 remain follow-ups.

## Decisions

**One interaction.** Place requests beside their originating turn (background requests in the initiating agent's thread). Connect shows plain-word scope and Connect / Not now. Approval shows summary, editable draft, Approve / Edit / Deny and once / this task / always, default once. Edit returns a fresh preview without execution. Other connect entry points focus this card; the rail becomes read-only history. Secret deposit controls never write transcript messages. Failure preserves the request/draft with Try again / available alternative / skip. Not now defers; denial/skip records an explicit outcome.

**Exact execution.** For ask_first, the trusted dispatcher stores `{version:1, executor, arguments, subject, connection_revision, consent_digest, policy_digest}`. Executor is an existing generic operation; arguments are validated non-secret inputs; subject pins authenticated owner/home, initiating agent/binding revision, task, conversation and turn. Hash RFC 8785 canonical JSON with SHA-256; pin referenced file/body versions and digests without changing transmitted bytes. Display prose, mutable paths and caller identities are not authority.

Approve claims the displayed revision/hash and executes that envelope server-side through ordinary enforcement: current isolation, custody, grants, consent, initiating-agent rules and owner-configured review. Edits or changed authority/policy require a fresh validated preview. No agent retry turn. Only owner rules choose ask_first; do, hand_off and do_if_preapproved retain their meanings (missing preapproval is not a new ask-first policy). Defaults stay editable starter rules.

**Scopes.** Every approval writes a visible, revocable owner rule. Once matches one digest/execution reservation. Task matches the server-issued task ID plus the displayed agent/connection/operation/destination predicate until task termination. Always persists that predicate. Wider scopes permit changing draft content within that predicate and existing consent only. Subsequent owner edits/revocation win; never substitute the selected agent or main. Deny writes no allow rule. Existing deposit consent and don't-ask-again semantics remain.

**Continuation.** Every surface's common answer path durably records a sanitized outcome; approval wakes on the execution result, ordinary/item answers on their answer, and OAuth on deposit success. Route to the saved agent/task/conversation through existing turn coordination, serializing with live work and deduplicating event IDs at admission. Remove the page relay. Connection success gets one-line confirmation followed by the original task, unprompted. Missing context/power stays visibly pending.

## Storage contract

Add tables to server-owned `.agent-sessions/<universe>/rules.db`, outside agent-writable storage, sharing owner-rule transactions. JSON/text fields are TEXT, counters INTEGER, timestamps UTC epoch REAL:

| Table | Columns and keys |
|---|---|
| `request_controls` | `request_id` PK; required `revision, context_json, phase, updated_at`; nullable `action_json, action_sha256, policy_digest, decision_json, result_json, execution_key`; execution_key UNIQUE |
| `request_events` | `event_id` INTEGER autoincrement PK; required `dedupe_key` UNIQUE, `agent_id, type, payload_json, wake_required, created_at`; nullable `request_id, revision, admitted_turn_id` |
| `scoped_approval_rules` | `decision_id` PK; required `request_id, agent_id, scope, matcher_json, policy_digest, state, created_at, updated_at`; nullable `task_id, action_sha256, execution_key` |

Context is server-derived provenance. Decision records actor, choice, scope, revision/hash and predicate. Action contains credential references only; results/events are sanitized. Phase: pending/deferred/executing/succeeded/denied/skipped/failed/uncertain; deferred/failed/uncertain remain unresolved. Rule scope: once/task/always; state: active/reserved/consumed/revoked/expired. Policy digest fingerprints applicable owner rules/review settings, excluding the newly issued scoped grant itself.

Approval claim, rule write and event append are atomic. Controls govern lifecycle; existing `.pending_requests.db` retains prose/fields/items and status vocabulary with idempotently reconciled answer/status projections. No vault schema change. Reserve `request_id:approved_revision` before execution and reuse it for receipts/provider idempotency. Duplicate answers return current state. A possibly completed external effect stays uncertain: retry requires proven non-execution or supported idempotency. Persist result and wake together; retain undelivered wakes and unresolved execution records. Delivered events may expire only with snapshot recovery. Account deletion removes these records with existing control stores.

## Public contracts

- Existing `write_graph target=pending_request operation=ask` accepts `action:{type:"approve_action",pending_action:{executor,arguments}}`. Reads add `revision, action_sha256, draft, phase, result` and safe provenance. Protected bindings are not agent-writable.
- Existing answer adds `{request_id,expected_revision,action_sha256,decision,scope?,draft?}`. Decisions: approve/edit/deny/retry/skip/defer/alternative; scope: once/task/always, required for approve. Edit accepts only operation-declared editable fields, returning a new preview. Alternative resumes planning, never executes. Revision/hash are required for bound approvals; existing values/item/dismiss answers remain supported. Stale decisions return `request_conflict` with the current safe card. Runtime agents cannot self-approve.
- Schema errors: `{error:"request_invalid",errors:[{path,expected,message}],example}`, naming exact fields/types with a minimal non-secret example. Validate discriminators; never guess provider/manifest formats or echo secrets.
- New bearer-authenticated SSE `POST /app/api/events`, body `{universe_id,after?}`, returns `{id,type,request_id?,revision?,agent_id,data}`: request.upsert/request.result/turn.status/snapshot. Use the owner-door gate and complete safe projections, no bearer in URLs. Subscribe-before-snapshot/replay closes load races; reconnect replays the cursor or supplies a complete snapshot. Stop delivery on access/account changes; show stale status with Retry. No fixed request/status poll, no new MCP handles; public MCP stays `https://tinyassets.io/mcp`.
- Reuse `/app/model-connect/{operation}` and `/app/model-callback/{flow}` in popup/system in-app sign-in. The child/native flow owns existing client PKCE state and finishes authenticated exchange independently of the parent; server finish/deposit records the result/wake. Preserve state/issuer/consent checks. Completion messaging targets the exact app origin with only an opaque flow reference. No secrets/codes in thread/events/agent context, new provider routes or full-page redirects. Blocked/closed/failed sign-in stays pending; replay/account switches cannot bind another request.

## Risks and migration

External APIs cannot promise exactly-once effects: reconcile uncertainty before retry. OAuth still requires discovery/public PKCE client support; offer existing private key deposit where available without promising Google/LinkedIn coverage. Reuse `addressed-agent-control-provenance`; missing provenance holds rather than borrowing main's rules.

Create tables idempotently before new writers. Preserve legacy requests/items/suppressions; backfill only verified provenance and visibly hold unlinked continuation. Free-text approvals require newly bound previews. Cut over UI after server support; old clients get an update-required response for bound cards. Rollback pauses bound execution/resumption and preserves records, never reinstates an answer handler that bypasses binding.

## Founder questions (non-blocking)

Should always offer a broader owner-edited predicate at launch, or retain the proposed narrow scope? Which providers should receive TinyAssets OAuth registrations first in the separate directory follow-up? Defaults here are narrow scope and no new registrations.
