## Context

K1 consolidates the audit's authoring paths. `ta_cli.py` currently discovers
mutable v1 tools in the jail; `ta_capabilities.py` binds its socket to one launch.
`command_center_packages.py` already provides private content-addressed blobs
and package pins. UI rows, workflows and the outbound ledger remain backends.

## Goals / Non-Goals

One authored manifest, revision and lifecycle for all contributions. No new
public MCP handle, host execution, credential custody implementation or resident
prompt content. U1 owns process admission and package-scoped broker authority.

## Decisions

### Manifest and revision

Schema v2 has `schema_version`, `name`, optional `description`, `executable`,
and arrays `tools`, `hooks`, `commands`, `cards`, `connections`, `mcp_servers`.
Unknown fields, duplicate names/JSON keys, malformed schemas and unsafe relative
paths fail closed. Tools/commands declare name, description and argument schema.
Hooks additionally declare a supported lifecycle event; cards declare a relative
HTML asset. Connection requirements declare a logical slot and required verbs,
never a credential or a transferable connection ID. MCP declarations name a
remote HTTPS endpoint plus optional slot, or a relative stdio executable/args.
No raw secrets or arbitrary environment injection are accepted.

A revision is SHA-256 over canonical manifest plus the complete path/byte map,
including helper files and UI assets, not merely the entrypoint. Installation
copies bytes into the existing private package blob store. No symlinks, absolute
paths, traversal or platform-reserved destinations; files are inert data at
installation. Working-file edits cannot change an installed revision. Installed
agent directory resolution uses authenticated center/agent identity and existing
package installation evidence, never a caller-supplied absolute directory.

### Lifecycle and permissions

State is private control-plane metadata alongside the package store, keyed by
owner, center, agent, extension name and revision. It is not workspace-editable
or exportable. `installed -> active -> revoked`; reactivation is an explicit
exact-revision action. Activating a new revision atomically replaces the active
revision for that binding. Expected revision/generation prevents stale updates.
Revoke increments the generation before cleanup and fences every contribution.

The authenticated launch supplies identity. Install/activate/revoke through ta
require current serving-owner authority; research/delegated callers cannot
mutate lifecycle state. Activation only uses already granted launch capabilities
and recipient-local connection grants. It cannot approve protected requests,
create credentials, widen scopes/endpoints/classifications, grant another
owner's data, or transfer the author's authority. Any additional authority goes
through existing protected owner approval bound to the exact revision. Every
dispatch intersects activation ceiling with current launch and live backend
grants, and rechecks revocation. Package approval and connection approval remain
distinct. Code updates invalidate activation and test receipts.

### One dispatch and backend projection

`ta` exposes extension install/inspect/activate/revoke plus revision-qualified
contributions. Search/describe identify kind, revision and availability, including
unbound connection requirements and unavailable MCP transports. Hook events and
commands use the same dispatch authority as tools; UI is a projection into the
existing isolated renderer, and its calls return through ta's capability gate.
Protected approval chrome stays first-party. Backend rows carry the installation
identity/generation; stale projections cannot serve after revoke, even if cleanup
fails. Workflow-backed effects retain their existing approval/effect machinery.
Hooks cannot suppress audit, Stop, or approval enforcement. Ordered hooks receive
versioned bounded JSON; errors remain visible and do not silently approve effects.

### Execution boundary and U1 handoff

No daemon imports or executes package code. Before U1, ordinary code runs only
inside today's bash jail with its existing launch authority, and no new secret
access. An implementation must not claim a narrower package credential ceiling
while sharing the broader launch socket. Package-scoped executable credentials and persistent stdio admission remain
unavailable until U1 supplies the boundary. Remote HTTPS MCP and credential-blind
git use today's broker/egress and existing grants; neither waits for U1.

U1 admission consumes owner/center/agent, installation ID, content digest,
activation generation, capability ceiling and recipient-local slot bindings.
It verifies immutable bytes, checks current generation before starting and on
broker effects, binds package revision to peer process identity, and revokes
leases/cancels processes on Stop/revoke. K1 supplies metadata, not a competing
launcher. Raw-key stdio requires separately protected exact-configuration opt-in
and U1's separate identity/filesystem/process view plus scanned output; absent
that support it is rejected. No fake connected/success status for blocked work.

### Sharing and consolidation

Existing command-center packages carry extension files and logical needs only.
Recipient installation is inert and uses local bindings; no activation state,
credentials, connection IDs, grants or private self-test responses travel.
`command-center-harness-control` (#4503/#4508) extension/hook/card tasks,
`saved-agent-connectors` (#4511), `connect-anything-ladder` (#4496) attachment
lifecycle, and `command-center-agent-templates` (#4515) directory resolution are
superseded by K1. Their settings, generic transport and unrelated copy work
remain intact. No second connector registry or UI authoring protocol is added.

## Risks / Trade-offs

- Shared-jail authority cannot prove package-scoped custody: keep that admission
  unavailable until U1, and report availability separately from installation.
- Backend projections can partially fail: generation fencing is authoritative;
  rebuild projections idempotently and expose failures.
- Legacy v1 remains compatible for now; it cannot claim v2 contributions or
  activation by changing only a version field. Migration is explicit installation.

## Migration Plan

Add schema/state first, then wire ta and backend adapters under the same lifecycle.
No destructive conversion of existing UI, workflows, connections or v1 files.
Rollback disables v2 dispatch while retaining installed blobs, history and revoke.
No deploy claim until deployed SHA and a real-user app pass; draft PR may precede
U1-dependent execution. Decisions and remaining tasks are recorded in the PR.

## Proposal review dispositions (Claude, 2026-10-06)

Verdict ADAPT; AGREE with findings 1-8. Concrete refinements:

1. Exact executable bytes require a read-only per-launch snapshot mount through
   existing jail construction. That mount plumbing is permitted; privileged
   launcher policy stays with U1. Until implemented, executable contributions
   are discoverable but explicitly unavailable, never run from mutable files.
2. Daemon-side dispatch checks the installation generation. Pre-U1 revoke
   fences new dispatch/effects, but cannot unmount or terminate code already
   running in a bash invocation. Strong process cancellation remains U1 work.
3. Automatic hooks need a separate existing-jail invocation with the triggering
   turn's grant, and no authority when there is no turn. They remain unavailable
   until that integration exists; metadata activation alone is not hook execution.
4. Browser UI dispatch must bind the authenticated owner session and active
   installation generation at the existing UI backend, not reuse an absent bash
   socket. Until integrated, cards expose metadata only and cannot claim rendering.
5. Workspace settings only narrow the active set, never activate/reactivate.
   `starter.hooks` is separately dependent on the starter loader; K1 does not
   implement that lane's editable starter behavior.
6. The first install API accepts a complete byte map, not arbitrary blob digests
   or host paths. The jailed client owns workspace reading; the daemon validates
   supplied bytes. Shared imports retain existing package consent/screening.
   No lookup API accepts another owner's revision without a bound installation.
7. Extension blobs use the existing command-center package format with files
   rooted at `extensions/<name>/`; existing build/check functions own encoding.
   The extension revision is the whole deterministic package blob digest. Charge
   the recipient's packages storage before writing, using existing accounting.
   A shared enclosing package has its own digest; extraction/reinstallation
   reconstructs this extension envelope from bytes, without inherited authority.
8. Lifecycle mutation requires `delegated_authority == "serving-owner"`, no
   `approval_id`, and current binding authority. Addressed agents retain their
   own authenticated identity; an addressed agent is not inherently delegated.

Executable updates require explicit activation of the new revision in K1.
Existing presentation-only recipient update policy is unchanged; no automatic
executable update policy is introduced. Superseded proposal notices are recorded
in all four source changes. Their unchecked tasks remain historical pending
work, not falsely marked implemented; K1 is the delivery owner for folded scope.

## Implementation review dispositions

Claude returned ADAPT, reporting no floor break and three runtime correctness
issues plus a mirror synchronization observation. AGREE with containment and
recovery fixes: absent extension authority skips mounting, malformed settings or
unreadable revisions produce visible catalog diagnostics without hiding lifecycle
commands, and revoke checks the installed record without requiring readable code.
The research-specific premise was broader than the existing behavior: ordinary
bash already refuses research in `universe_tools.bash`; that refusal is retained.
No new research execution is introduced.

AGREE that mounts must be launch-local. Mount identity now lives in the bridge's
copied context and propagates with each daemon dispatch, rather than mutable shared
backend state. Tests cover two independent launch contexts and real jailed calls.
AGREE on mirror parity; regenerated after the on-demand handbook addition.
No second review round is claimed. Full review is in review-implementation.md.

## Continued fold-in decisions (2026-10-06)

Remote MCP ports the #4496 protocol client, using the existing authenticated
connection effector for each HTTP exchange. Its current consent, rules, review,
OAuth refresh, endpoint validation and response scanning remain authoritative.
There is no independent MCP activation table. A server contribution exposes
explicit discover/call operations; calls pin the discovered catalog hash and
never replay an uncertain tool outcome. Only bounded HTTP responses are admitted
by today's effector; persistent stdio still waits for U1.

Local connection bindings are private activation metadata, never package bytes.
Activation can optionally bind declared slots to exact current connection/grant
IDs and connection incarnations. Every use rechecks those pins, required verbs,
current launch authority and the activation ceiling. Rebinding requires a new
activation generation; absent bindings remain visible requirements. This plumbing
is implemented with remote MCP because the remote contribution needs it.

The remaining fold-ins are credential-blind git through today's egress, and
outside-client scoped authority with durable revocation/kill switch. Outside
clients must not gain lifecycle-owner authority by authoring extension code.
Live AuthKit identity evidence remains founder-owned; stdio/package-cell
admission is the only runtime item deferred to U1.


Git fold-in ports #4513 binary smart HTTP and scanning onto today's broker.
Only active extension connection slots declaring exact git_read:owner/repo or
git_write:owner/repo scopes create ephemeral egress rewrite routes. Routes never
carry credentials, are proxy/launch bound, recheck current extension authority,
and expire on launch exit. The broker independently verifies host, exact repo,
verb, grant and incarnation; it injects credentials and refuses redirects.
No per-owner git_bridge or U1 filesystem/process changes are included.


Outside-client continuation uses the #4501 verified-claims probe and the named
platform-root .outside-client-authority.sqlite3 store. Exact owner/client scopes
name universe, agent and capability; authoring code never changes them. The
protected owner-session endpoint is the only remote grant editor. Revocation
retires every observed family; reconnect needs a newly verified family and a
new explicit owner grant. A durable switch defaults denied and a process override
can only deny. There is no positive authorization cache. Origin/generation rides
inside the signed engine launch grant, not mutable request headers, and ta and
engine calls recheck it. Production client/family claim configuration remains
unset until the founder's live AuthKit evidence check; no brand inference or
silent migration of existing connectors is introduced. Existing primary clients
must be explicitly grandfathered or owner-reconsented before that cutover.


## Lifecycle continuation review dispositions (Claude)

Verdict ADAPT. AGREE with required findings 1?5; no second approval is claimed.
The complete review is retained in review-lifecycle.md.

1. Effect admission now commits a durable in-flight lease under a short
   transaction. Network work holds no global SQLite lock. Revoke/kill-switch
   commits fence subsequent admissions immediately. Already admitted network
   work may finish; failure records an unknown outcome and never authorizes
   replay. An acknowledged revoke is not a claim to undo an earlier send.
   Authority-store errors fail closed, including verified-claims observation.
2. Outside launches require both a signed tool list and a nonempty grant key;
   unsigned route fallback is refused before spawning an engine session.
3. Runs capture outside owner/client/family/generation atomically in their run
   row; normal execution, queued recovery and resume restore it. Schedules and
   event subscriptions store the same private origin; event wakes inherit it.
   Dispatch and current-node authority checks revalidate it. Outside bearers
   cannot mint independent webhook/source credentials through run_graph.
4. Hooks are observational, never an authorization gate. Failure and no-bash
   skips are persisted in the existing package database and discoverable via
   ta extension:events. A failed turn_end does not change completed tool effects
   into a retryable failure. Stop never starts another hook.
5. Elevated engine identity is bound only around each jailed hook invocation,
   restored before the original handler. A real-jail regression makes a hook
   invoke ta read_graph and checks that it does not recursively trigger itself.

AGREE on indirect resource scope: outside calls with indirect IDs must state
an explicit universe; command_center_id is also a supported selector. Existing
resolved-universe ACL checks additionally reject objects outside the admitted
universe. Named-agent operations use exact agent bindings; main is the scope for
command-center-wide handles. Granting a center-wide handle intentionally covers
its existing center-level behavior; it is not an agent-row filter. Engine/ta
calls retain independent exact capability checks. Running an owner-authored
workflow delegates its existing effect permissions, but retains outside origin
and the live client revocation fence. Connection-backed extension use additionally
requires exact connection capability scopes. No authored code creates a grant.

Owner grant editing accepts only current home/admin authority through the
protected interactive session. It is not available to an OAuth bearer, including
a first-party bearer. Scope changes require the current generation. Existing
client grants are never copied from imported packages. Production classification
remains gated solely on founder-verified AuthKit claim evidence; the probe has
no tokens in its output. Default outside admission is deny.

UI cards project immutable HTML or app_ui JSON assets through the existing UI
library on activation, with owner/agent/revision/generation-specific IDs. The
read fence hides revoked, superseded, disabled or edited projections even when
cleanup fails. The sandboxed frame owns no bearer; its parent verifies the live
projection before each bridge action, retaining the existing owner/session/frame
checks. Activation validates renderability before committing. A failed projection
write is reported; reactivation with the read-back generation repairs projection.

## C2 folded obligations

The following unfinished source tasks retain their acceptance detail; the one target change owns them.

### command-center-harness-control

Implementation backlog for general primitives only. T1-T10 is a capability checklist for user-built command-center projects, never a platform setup delivery requirement. D7 owns editor/history, D8/D9 own package/main selection, and starter-agent-out-of-plumbing owns renderer replacement; extend those paths rather than duplicating them.

## 1. Runtime settings

- [x] 1.1 Implement v1 settings validation, sparse/default and legacy-model behavior with revision snapshots; verify malformed/duplicate/unknown fields fail visibly and package bindings remain local.
- [ ] 1.2 Wire one resolver into foreground/background turns, tool/skill selection, starter hooks and model/loop controls; verify next-turn changes, current revocation and no platform LLM fallback.

## 2. Extension mechanism

- [ ] 2.1 Extend existing manifests to v2 hooks with v1 tool-only support, pinned code revisions and jail execution; verify deterministic order, owner/center confinement, exact-revision third-party activation and manual/automatic recipient updates for hooks, extensions, agents and channel-template code with author/ceiling grants. Prove silent unchanged-scope updates cannot read recipient data and activated hooks cannot inspect a raw-key stdio server's isolated process/environment/files; verify mediated stdout/stderr uses broker scanning before any output sink.
- [ ] 2.2 Wire all six lifecycle events, tool replacement and owner-supplied loop calls through ta; verify failure/cancellation, no recursive hook dispatch and no uncertain-effect retry.
- [ ] 2.3 Add extension commands/cards with owner-content provenance and existing protected request references; verify no counterfeit decisions or receipts. Prove package/template/agent classifications remain inert until exact-revision owner approval through the protected owner-authenticated rule-write surface, including send_payment mislabeled read under a standing read grant; reject bearer-only writes and stale approval reuse.

## 3. UI parity and primitive verification

- [ ] 3.0 Add permission-bound subscribe/unsubscribe, snapshot/cursor replay and live activity/tool/approval/task/process events; verify dedupe, resync after retention/backpressure, account switching and revocation.

- [ ] 3.1 Expose shared ta search/describe/call through the existing bridge and installation permission binding; verify connections, memory, harness/history, rules and model/extension capabilities.
- [ ] 3.2 Implement revision-checked edits and exact-revision permission/frame invalidation, including recipient updates and explicit author/ceiling auto-update grants; prove cross-user mutations, unchanged-scope hostile updates, guessed IDs, link escapes, copied bundles, account switching, stale responses and revocation cannot cross bindings.
- [ ] 3.3 Coordinate D7/D8/D9 and starter acceptance: prove owner-controlled main replacement, hook disablement and model-independent recovery without duplicate installation or editor paths; verify broker-only credential isolation with synthetic hostile-skill probes and recipient-local bindings at the primitive level.
- [ ] 3.4 Keep test names; run affected/heavy tests on Windows and Linux 3.11 oracle, touched-Python ruff, plugin mirror for tinyassets edits and hygiene gate with 0 removed / 0 tampering.
- [ ] 3.5 Assert deployed implementation SHA, public canary and a real-user custom-UI settings/memory/tool/main-agent pass; sync only this capability and reconcile the parent delegation links.
- [ ] 3.6 Expose general ta spawn, ta message and revisioned task-board operations through existing launch/mailbox/collection records; verify scoped model/delegation grants, child Stop/revocation, message identity/delivery, concurrent claims and existing budget pause. No templates or specific setups.


### saved-agent-connectors

Implementation backlog only; dependent work starts after the prerequisites in design.md.

## 1. Delivery

- [ ] 1.1 Consume MCP-lane credential slots, egress and connection lifecycle without implementing another card.
- [ ] 1.2 Save exact extension revision, declared slots, local bindings and test receipts using existing extension storage.
- [ ] 1.3 Run safe self-tests or owner-authorized effectful tests; keep failed and changed revisions visibly untested.
- [ ] 1.4 Expose source/version, saved status and revoke/disconnect in existing connections controls.
- [ ] 1.5 Apply exact-revision activation and author/ceiling auto-updates; fence revocation and require retesting updates.
- [ ] 1.6 Publish credential-free connector requirements and prove second-account local binding with no source private data.
- [ ] 1.7 Run affected/heavy tests, Linux oracle where sandbox work is touched, ruff and hygiene (0 removed / 0 tampering).
- [ ] 1.8 Assert deployed SHA and real-user author/test/save/reuse/revoke proof; sync the capability.


### connect-anything-ladder

Implementation backlog only. Card ownership belongs to inline-connect-and-approve; browser and saved extensions have separate changes.

## 1. MCP attach, secret entry and OAuth

- [ ] 1.1 Verify generic OAuth, streaming broker and existing request/card prerequisites; add typed MCP metadata preserving HTTP rows and rollback cleanup.
- [x] 1.2 Add MCP OAuth metadata discovery, PKCE/resource binding and DCR/CIMD/static registration without per-platform code; retain the optional provider directory.
- [x] 1.3 Extend the remote adapter to MCP 2026-07-28 and legacy negotiation, JSON/SSE, scanned auth challenges, HTTP cancellation and host-bound URL MRTR continuation; prove local standards-server traffic through broker IPC and uncertain-call non-replay. Live host callback/durable wake wiring remains 1.6 (see docs/concerns/mcp-current-protocol-host-wiring.md).
- [ ] 1.4 Implement exact-revision jailed stdio and scoped proxy auth; support warned named-own-key opt-in in a separate process/user/filesystem sandbox, with grant/revision isolation and no host fallback. Verify agent/other-author code cannot read server /proc, environment, arguments or files; reuse broker scanning on stdout/stderr and prove split-chunk key bytes never reach model context, logs or transcript. Verify the warning names only that server's code and author as key readers.
- [ ] 1.5 Implement protected secret entry and allowlisted egress slots with owner/incarnation checks and credential-free output/export.
- [ ] 1.6 Wire ta catalog/dispatch and existing inline coordinator; verify stale catalogs, Stop, account switching, callback replay and crash recovery.
- [ ] 1.7 Fence revocation before durable cleanup; preserve independent backing HTTP connections and prevent old-incarnation reuse.
- [ ] 1.8 Connect an unknown MCP server through the existing card with multiple accounts and model-independent controls; prove owner-classified and unknown-effect editable defaults.
- [ ] 1.9 Run affected/heavy tests, Linux oracle, ruff and hygiene (0 removed / 0 tampering).
- [ ] 1.10 Assert deployed SHA, public canary and real-user MCP connect/cancel/revoke pass; sync capability and delegations.


### command-center-agent-templates

## 1. Public copy contract
- [x] 1.1 Add selected public instruction-template export and source validation.
- [x] 1.2 Add private idempotent recipient binding installation and explicit UI reference remapping.
- [x] 1.3 Refuse unsupported nested workflow dependencies before copy and describe absent templates.
## 2. Verification and delivery
- [x] 2.1 Prove isolation, unsupported-source refusal, no-provider execution and crash replay with meaningful tests.
- [x] 2.2 Integrate frontend agent aliases, independent exact-head floor review and hosted browser proof.
- [ ] 2.3 Complete protected merge/deployment, real-user functional acceptance and spec sync.

Delivery evidence (2026-10-05, draft PR #4515): frontend `whoami().agent_refs`
and `openChat` integration was already on main. Commit `73b3ddc9e4` adds the
authenticated installed-directory handoff and extends the real Chromium proof
to file-package copies as well as screen-only copies. Linux oracle: 246 passed,
including static prompt budgets; ruff and plugin rebuild/import probe passed;
hygiene: 0 removed, 0 tampering. Claude reviewed that exact implementation head:
APPROVE, no floor/correctness findings; lead AGREE.

Hosted browser cases and the no-skip assertion passed in
[run 37406639881](https://github.com/TinyAssets/TinyAssets/actions/runs/37406639881).
The PR records the review receipt and final evidence. Directory provenance does
not authorize hooks: harness control still owns activation and safe file reads.
Task 2.3 remains deliberately unchecked; this is not a deployment claim.

CI repair (2026-10-05, PR #4515): merged `origin/main` before repair and
rechecked before push. Job `112088492334` shows the request-continuations thread
calling the test's process-wide fake `time.sleep(5)` and raising `StopLoop`.
The extracted maintenance loop now receives a private import scope for its
clock and update-maintenance dependency. All prior assertions remain; update
failure isolation, update call counts and unchanged process sleep are asserted.
No runtime or static prompt changes; Playwright imports remain test/fixture-local.
Linux oracle: 118 passed, zero skips (delivery account deletion/reservations,
update maintenance, static prompt budgets, agent templates and system Chromium
browser). Ruff and plugin rebuild/import probe passed. Claude cross-family
review: APPROVE, no floor/correctness findings; lead AGREE. Hygiene: 0 removed,
0 tampering. Reuse the existing draft PR; task 2.3 remains unchecked.


### agent-box-git-credentials

## 1. Diagnosis and non-authority tools

- [x] 1.1 Inspect served/box paths and reproduce public clone, small pytest,
  large exact edit, branch/merge/conflict/rebase, and persistence in Linux.
- [x] 1.2 Install pytest on the production jail's Python, separately from the
  daemon venv; preserve existing isolation and resource limits.
- [x] 1.3 Complete synthetic authentication failure proof, Windows/Linux checks,
  ruff, hygiene and draft review; record evidence in the PR body.

## 2. Proposed credential route (not authorized for implementation in this lane)

- [ ] 2.1 Reconcile UID-split broker interfaces and specify binary streaming IPC
  with exact owner/agent/grant/host/repository/method bindings.
- [ ] 2.2 Implement the route and surrogate lifecycle with current-grant checks,
  revocation, pinned HTTPS, bounded secret scanning and unknown-push handling.
- [ ] 2.3 Prove synthetic authenticated clone/fetch/push, wrong-owner/host/repo,
  revoked grants, redirects, token non-exposure and binary pack integrity.
- [ ] 2.4 Integrate with an isolating `/cc` provider, pass a real-user flow,
  assert deployment SHA and sync the as-built spec before claiming completion.


### outside-agent-grants

Docs-only round 2; all boxes describe future implementation and acceptance. No code, deployment or live-proof completion is claimed. Tasks are split by independently verifiable surface; each section stays at or below the owner's requested 12 tasks.

## 1. Identity and authority foundation

- [ ] 1.1 Reconcile adjacent changes and prove AuthKit verified client claims/lookup, metadata and reconnect candidates (`auth_time` preserved on refresh; `sid` across fresh auth); specify and test protected re-consent/credential-family fallback so recovery never clears old fences.
- [ ] 1.2 Bind verified client classification on every bearer request; configure the exact first-party issuer/client-id allowlist for web/phone/desktop; test spoofing, missing identity, CIMD/DCR, same-owner independent clients and stolen-bearer attribution semantics.
- [ ] 1.3 Implement OutsideClientAuthority at the named platform-root SQLite path with a single transactional authority service shared by all workers, owner defaults, per-universe grants, owner/client fences and secret-free audit; test concurrency, store outage, no replica fallback and cross-universe visibility; register platform storage/backup ownership.
- [ ] 1.4 Implement owner-editable starter agents/levels/message mode, atomic grant revisions/expiry, first-converse bootstrap and no-home status; test concurrent snapshots and preservation of existing/revoked grants.

## 2. Admission and owner controls

- [ ] 2.1 Enforce read grants on get_status/read_graph/read_page, including aliases, mixed collections and shared pages; verify filtering before serialization and uniform cross-user refusals.
- [ ] 2.2 Enforce message/control/costly on converse/write_graph/run_graph/write_page and batch/alias operations; verify all affected agents/resources and missing-grant errors without effects.
- [ ] 2.3 Apply and test every route/method disposition in bearer-surfaces.md, including all app HTTP routes, pulse and private bearer services; unknown bearer routes fail closed; first-party authorized calls still work.
- [ ] 2.4 Propagate outside origin through existing turn/run/nested/scheduled carriers; test both owner-selected message modes, direct-call limits, mode narrowing on resume, and continuing trusted owner/universe automations and queued runs.
- [ ] 2.5 Complete the protected owner-session integration with inline-connect-and-approve and Connected apps inventory, widening/narrowing, starter/message settings, revoke/recovery and last-used; extend scoped access readback and audit; prove bearer self-elevation cannot work. Working owner controls are a hard prerequisite to enforcement.
- [ ] 2.6 Implement transactional local revoke and effect-admission fencing across universes/workers, open sessions, refresh and queued/pending work; test race boundaries and honest receipts for already committed effects, independently of provider revocation success.
- [ ] 2.7 Implement fresh-generation reconnect and provider revocation attempts; test protected owner re-consent when AuthKit generation evidence is insufficient, keeping pre-revoke credentials invalid without permanent owner lockout.
- [ ] 2.8 Integrate client provenance and current message-mode/grant revalidation with protected sensitive-action sheets and standing decisions; prove bearer approval/retry refusal and that effect approval alone cannot widen grants.
- [ ] 2.9 Implement the NEW durable outside-only kill switch and deny-only process override; test next-admission refusal on every worker, outage fail-closed behavior, and exemptions for verified first-party and trusted owner/universe work before rollback use.

## 3. Verification and live acceptance

- [ ] 3.1 Rehearse cutover only after working owner controls: grandfather existing primary connectors or obtain visible owner choices, reconcile unknown legacy identity with notice before cutover, and hold only unreconciled outside-origin work. Rehearse kill-switch activation and retained/backported admission on rollback; first-party read/write/run and owner controls continue.
- [ ] 3.2 Run focused and affected heavy tests plus ruff, cross-user mutation cases and one cross-family floor/correctness implementation review with AGREE/DISAGREE_EVIDENCE dispositions; strictly validate OpenSpec, assert deployed implementation SHA and run `python scripts/mcp_public_canary.py --assert-handles` with no added handles.
- [ ] 3.3 Mandatory rendered live pass with two clients on one owner account (claude.ai + ChatGPT, or OpenClaw): independently edit grants, prove a missing-grant refusal, revoke one and prove refusal on its next call while the other works, then prove first-party app read/write/run and protected grant widening after cutover. Record sanitized receipts; no Muse blocker can waive this task.
- [ ] 3.4 Additional rendered Muse proof at `https://tinyassets.io/mcp`: verify CIMD/DCR callback acceptance for `https://agent.meta.ai/api/hatch/oauth/callback`, starter grant, owner widening/message mode, sensitive sheet, audit and revoke; record sanitized receipts or the exact callback/configuration blocker, without representing a blocker as a live pass.
- [ ] 3.5 After implementation and mandatory live proof, sync canonical specs and archive with validation/review/deployment/live receipts, explicitly recording any additional Muse blocker; keep live/outbox, A2A and outbound attach in their later changes.
