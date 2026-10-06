## Context

The supplied audit B4/L11–L13 identifies `command_center_packages.model_need()` as an install-only settings reader, `/u/extensions/<pkg>/extension.json` as tool-only, and the bridge in `ui_frame.py` as a limited method list. D7 names settings without a runtime schema. D8/D9 already specify package installation, and composable-ui-experiences already binds the renderer to its viewing user. These are extension points, not reasons to create competing stores or control planes.

## Goals / Non-Goals

Let the owner customize or replace the starter, main agent, tool set, model and loop with few tools plus one extension mechanism, as in the pi-style direction. Provide the same permitted execution capabilities through ta and custom UI. Cross-user isolation is the sole immutable platform policy. Credential custody and authentic owner provenance implement that isolation and the owner's chosen permissions; they do not authorize a platform veto over the owner's behavior. No platform LLM, provider-specific code or separate UI-only capability catalog is introduced.

Do not reimplement D7 memory/history, D8/D9 packages/main selection, starter extraction, protected decisions or renderer isolation. The actual UI layout is implementation work in its owning lane.

## Decisions

### Runtime settings schema v1

Read center-root `settings.yaml` for the main agent and `agents/<slug>/settings.yaml` for that roster agent. Resolve center and agent from the authenticated launch binding, never from YAML owner/path claims. A roster document is independent; missing fields use documented runtime defaults, not another agent's settings. Only an explicit per-run model choice overrides the settings model, followed by the existing owner model preference when the field is absent. Every candidate uses current owner-connected authority. No fallback to a platform or maintainer model.

| Field | Version 1 meaning |
|---|---|
| `schema_version: 1` | Required for a newly authored document; unknown versions/keys and duplicate YAML keys are errors. Safe parsing only, no tags or executable YAML. |
| `model: {connection, id, effort?}` | Local connected source ID and model ID; effort uses executor-advertised values. Missing binding asks inline and holds this launch without silently substituting another model. |
| `tools: {allow: [...]}` | Owner-selected exposed names/patterns from ta plus base tools. Absent means current available set; empty means no model-callable tools. Restricts a launch, never grants another owner's access. |
| `skills: {enabled: [...]}` | Relative skill identifiers; omitted retains current discovery, empty disables all. |
| `extensions: {enabled: [...]}` | Ordered relative extension IDs; omitted retains the currently activated set, empty disables all. Package presence alone is not activation. |
| `starter: {hooks: true|false}` | Enables/disables editable starter/hooks.md; absent retains the starter default. Never reconstitutes deleted instructions. |
| `loop: {compaction: {...}, retry: {...}}` | Owner-selected context reserve/trigger and retry attempts/backoff; typed finite nonnegative values validated against the selected executor's capabilities. No retry of uncertain side effects. |

Capture validated settings bytes/hash, resolved model and ordered extension manifest/code revisions at each turn boundary; record the snapshot in existing turn/run history. File writes use existing harness history and expected-revision conflict handling. An edit affects the next turn, never a half-completed hook/tool sequence. Revocation, account change and Stop take effect immediately and are rechecked before dispatch; a snapshot is not a durable authority grant. Background and foreground turns use the same resolver.

Missing document preserves pre-change behavior; blank/malformed settings produce a visible diagnostic and do not silently run old settings. Legacy packaged `model: <string>` becomes a visible candidate requiring resolution to a local binding before activation, without rewriting the source or guessing a provider. An explicit owner adoption writes v1 with history/Undo. D9 still owns install quarantine and local binding. Packages export logical model needs, never credentials or source-account grants.

### One extension protocol

Effective tool/effect classifications are written only by the owner through the protected owner-authenticated rule-write surface. Packages, templates, saved connectors, authors and agents (including ta callers) may propose inert suggestions; only owner approval of the exact classification revision makes one effective. Bearer-only writes, package activation and automatic code-update grants cannot approve or replace classifications.

Extend `extension.json` with `schema_version: 2`, existing executable/tools declarations, ordered hook registrations, commands and card contributions. Version 1 tool-only manifests continue working with no implicit hooks. Entry points are relative no-link jail paths. A launch pins manifest and executable hashes; edits become effective next turn after normal activation. Run hooks in the same owner/center execution environment and no stronger launch grant than the active agent; no daemon Python imports or host execution. Raw-key stdio servers are excluded from this shared environment. A raw-key stdio server runs in its own sandbox with a separate process, user identity and filesystem view. The agent shell, hooks and other extensions reach it only over its mediated stdio pipe and have no access to its /proc entries, environment, arguments or files. Reuse the egress broker's incremental secret scanner on both stdout and stderr, including secret bytes split across chunks, before any output reaches model context, logs or transcript; withhold matching bytes and report a credential-free typed failure. Broker-injected credentials remain the default. The exception requires explicit owner opt-in for the exact server configuration revision and named own secret. The protected view names the exact server code and its author and warns that only that server's code and its author can read/use the injected key. Revocation or configuration changes invalidate the opt-in; packages never export it.

Hooks receive versioned JSON with opaque session/turn/invocation IDs, event kind, owner-visible payload and settings revision; they never receive session cookies, credential bytes or protected approval tokens. Output is a validated JSON result, not executable daemon code.

| Event | Allowed result |
|---|---|
| `input` | Pass/transform owner input for this turn or return an owner-visible response. |
| `turn_start` | Add/replace agent instructions and select configured tools/context for the turn. |
| `context` | Transform context and select compaction behavior before a model request. |
| `before_tool` | Continue, replace arguments/tool implementation, or cancel this call. |
| `after_tool` | Transform the model-visible result after the actual effect and receipt exist. |
| `turn_end` | Record owner-visible state and contribute a final card/response. |

The enabled extension order in settings is the execution order; later hooks see validated output from earlier hooks. Stop and authentic owner/center bindings are not transformable payload. A transformed tool call is dispatched through ordinary ta authority and current owner rules, including when a hook replaces a built-in. A fabricated after_tool result cannot rewrite effect receipts or claim an interactive owner approval. An extension may implement the owner's replacement model loop through ordinary ta calls; every model call still requires the owner's connection. There is no immutable four-tool behavioral policy.

Hook invocations carry IDs and cancellation/deadline signals. Timeouts, malformed output and crashes terminate that turn with a visible hook error unless the owner explicitly configured a supported recovery behavior; never silently run the unmodified tool. Retrying a hook cannot blindly replay an external side effect with uncertain outcome. Hook-initiated ta calls do not recursively re-enter the same hook dispatch; extensions needing orchestration perform it explicitly. Owner-chosen limits and progress/cancellation remain visible.

Slash commands and inline cards use this same manifest/runtime. Cards carry owner-content provenance, not forged platform/approval chrome. If owner policy asks for a decision, the extension references an existing protected request ID and opens the inline-connect-and-approve surface. Owners can change their policy to preauthorize operations; there is no mandatory extra review gate invented by extensions.

### Bridge parity through ta dispatch

Extend the existing versioned message bridge with `capabilities.search`, `capabilities.describe` and `capabilities.call` carrying ta's stable capability identifiers, structured input, correlation ID and expected revision for mutable resources. Both surfaces use one dispatcher and result/error schema. Offer connections inventory/use/connect/disconnect, memory read/edit/delete/Undo, harness read/write/history/Undo, rules read and permitted owner-control operations, model selection and extension management through that registry. A denied or unavailable capability returns the same reason on both surfaces, never a hard-coded UI-specific ban.

The trusted host authenticates the viewing owner and selected center/agent and binds the isolated frame instance, current bundle revision and permission revision to each request. It checks message source/nonce and current binding, not frame-supplied identity. Permission records reuse the existing installation/owner authority infrastructure outside editable package files; record owner, center, installation, exact bundle revision/content hash, allowed capabilities, permission revision and revocation. An owner can grant the full set for their center or a smaller set and revoke it later. Every third-party bundle revision requires renewed owner permission even when its requested capabilities are unchanged, unless the owner explicitly enabled automatic updates from that authenticated author within a recorded capability ceiling. That author identity, ceiling and update preference live in the owner permission record, not in the incoming package. An expanded scope still requires a new owner choice. The recipient-update path must check these records before activating any new revision and invalidate old frame handles. No model or platform policy agent participates in this permission check.

Owner-permitted parity includes editable main-agent files. Files resolve under the bound harness roots without symlink/reparse escape; whole-memory edits preserve D7 IDs/history via its existing write operation. Concurrent writes require an expected revision and return a conflict without discarding later edits. Reading a connection returns redacted metadata and opaque usable handles, never vault credentials. Interactive approval tokens and platform identity records are not ta execution capabilities and cannot be forged by calling the bridge. If a chosen operation requires an owner interaction, return the protected inline request handle; broad standing owner permission can eliminate repeated asks according to existing rules.

Account switch, center switch, navigation, permission revocation or frame destruction invalidates handles and pending responses. In-flight effects already dispatched retain their true receipts; late results cannot render in another owner's UI. An authorized cross-user interaction uses the existing recipient grant; no guessed ID, copied bundle or YAML field grants access.

### Ownership reconciliation

This contract fills D7 step 4 and D6 extension/ta gaps. `composable-ui-experiences` remains sole owner of the sandboxed renderer and installation binding; this adds its generic dispatcher methods rather than another renderer. D8/D9 main-agent selection consumes these settings after install, and starter task 3.3 remains the replacement-main proof owner. Record these delegation links in parent designs so implementers do not separately implement the old placeholders.

### Third-party executable revision activation

Hook and extension code from another author uses the same activation contract as UI bundles: recipient permission binds owner, center, installation, exact code revision/content hash and permission revision. An unchanged-scope author update stays inactive until the recipient approves that exact revision, unless a previously recorded owner auto-update grant binds the authenticated author and capability ceiling. Incoming code cannot create that grant, expand its ceiling or reuse old execution handles. This is a future supported non-UI update plan owned by this change, extending the existing recipient updater; its current presentation-updates-v1 executor remains presentation-only. This applies to agents and channel-template executable parts too; recipients choose manual or automatic updates per installed copy, automatic off by default. Recheck revocation and current authority before every execution, and invalidate prior revision handles.

## Harness coordination primitives

Use existing roster and agent-system launch records for ta spawn: bind owner/center, parent run, child role/model binding, task ID, allowed capabilities, budget and cancellation relationship. Support single, parallel, chain, nested and background delegation using one primitive with explicit dependency records. Authority is an intersection of current parent delegation and child-local grants, never a copied YAML assertion. Children select their own owner-connected model and return bounded summaries or owner-scoped artifact references; uncertain effects remain receipts, not success prose.

Ta message targets a real agent/run mailbox with authenticated sender and recipient bindings, idempotent message ID, reply correlation and delivery/ack state. Per-owner agent messaging needs the current declared grant; cross-user routing requires the existing recipient permission path, never an assumed same-owner shortcut. A message is task content, not approval or permission. Unknown/stopped recipients remain visibly undelivered. Define Stop propagation at spawn: attached children stop with parent; explicitly detached background work has its own visible owner lifecycle. Revocation always wins.

Task board is an ordinary owner-scoped data collection: stable task ID, assignee, parent/dependencies, state, expected revision, result refs and budget status. Claim/update uses optimistic concurrency so two workers cannot silently overwrite each other; history and event stream expose changes. Reuse budgets for per-agent pause at caps. Do not make the board a second execution authority.

Only agent spawn, agent-to-agent messaging and the task board move into this harness contract. Reuse existing roster/agent-system, budget and collection records; no channel/team templates or specific setups are supplied.

## Migration Plan

1. Add resolver and protocol negotiation with visible validation before activation. Preserve missing-settings behavior and v1 tool-only extensions.
2. Wire foreground/background turns and ta once, then expose the same dispatcher through the existing owner-bound bridge. Activate packages only through D9's current path.
3. Demonstrate an owner changing settings, disabling starter hooks, replacing a built-in/main agent and editing memory from a custom UI; prove account switch/revocation isolation. Undo content via history or disable extensions with ordinary owner controls, without resetting data or silently invoking a stock agent.

## Risks / Trade-offs

- Owner code can break its own agent: expose diagnostics and recovery through the existing model-independent owner surface; do not force a platform starter.
- Hooks and frames can act as confused deputies: resolve authority at the shared dispatcher on every call and invalidate stale frame bindings.
- Live files can change during a turn: pin revisions, recheck authority at dispatch and report next-turn activation explicitly.

## Open Questions

None affecting authority or storage. Exact UI affordances remain with the existing UI lanes; protocol names and v1 field semantics above are the proposed contract.

## Implementation reconciliation (2026-10-05, L5)

Baseline `b945fb3b3a` still has install-only settings. Task 1.1 adds
`harness_settings.py`: immutable exact-byte/SHA-256 snapshots, strict bounded YAML,
independent roster-root reads, ordered selections and an adapter to the existing
`ModelPreferences` schema. Missing files preserve defaults; malformed files do
not. This parser is not runtime activation or a permission grant.

Concrete v1 loop keys are `compaction.reserve_tokens`,
`compaction.trigger_tokens`, `retry.attempts` and `retry.backoff_seconds`.
Token/attempt values are nonnegative integers; backoff is finite and nonnegative.
Executor-specific support and limits must be checked by task 1.2 before dispatch.

A portable v1 model contains `id` and optional `effort`, with `connection` absent
until the recipient binds it. Publication removes source connection handles;
import rejects a package that still supplies one. This makes the approved
recipient-local binding requirement concrete without rewriting imported bytes,
adding authority, or inventing a provider. Legacy unversioned model-only files
remain readable as unresolved logical needs. An explicit run choice takes
precedence; otherwise unresolved needs raise `LocalModelBindingRequired` for the
future ingress adapter to present inline. Snapshot history recording belongs to
the task 1.2 turn integration, not this parser.

Task 1.2 has integration dependencies absent at this baseline:

- `addressed_agents.resolve()` returns a custom binding ID and definition
  instructions; it carries no authenticated installed-directory/roster slug.
  `command_center_packages.plan_install()` independently creates that slug.
  D8/D9 must supply the binding between these records before roster turns can
  select their settings. A guessed `agents/<binding-id>` path would silently
  read defaults instead of the installed agent's settings.
- `starter-agent-out-of-plumbing` task 1.3 owns the editable `starter/hooks.md`
  loader and is unchecked; current `converse()` still appends resident guidance.
  The settings toggle cannot disable a loader that has not landed. That lane
  retains extraction, seeding and main replacement ownership.

Keep task 1.2 and subsequent ordered tasks unchecked until those integrations
are available and proven. No foreground/background control, executable activation,
UI parity, live acceptance or completed capability is claimed by task 1.1.

Task 1.1 verification: Linux oracle Python 3.11.16, settings/package/prompt-cost
tests: **175 passed, zero skips**. Windows settings: **43 passed**; prior
settings/package run: **163 passed, one existing skip**. Windows settings plus
prompt-cost: **52 passed, one failure** at the existing tool-description ratchet
(32,353 versus 30,100); see `docs/concerns/windows-tool-description-budget.md`.
The Linux ratchet passes, and no prompt budget or existing test was changed.
Changed-Python Ruff, plugin rebuild/import probe and strict OpenSpec validation
pass. Task 1.2 onward remains unverified and unimplemented by this slice.

Final parser edge-case verification: oversized YAML integers and invalid YAML
timestamps also become `SettingsError`. **45 settings tests pass on both Windows
and the Linux 3.11 oracle, zero skips**; the package and prompt-cost regression
run above remains applicable. Plugin rebuild/import probe and Ruff pass. Hygiene
at the first slice commit reports **0 removed / 0 tampering**. Claude's required
cross-family review returned **APPROVE** with no floor/correctness findings and
independently confirmed the task 1.2 dependencies; see [review.md](review.md).
Lead disposition: **AGREE**. Draft PR #4503 remains an incomplete capability;
there is no deployment, live pass or as-built spec sync claim.

## Continuation reconciliation (2026-10-05, L5b)

Rechecked against `ae7790a388` after slice 1 merged as #4503. Its merge
completed only 1.1, not runtime task 1.2. The roster/settings and starter-loader
dependencies documented above remain absent. Do not infer activation from a
merged parser or mark 1.2 complete.

The first requested continuation item, 2.1, also depends on
`connect-anything-ladder` task 1.4 for the separate raw-key stdio server sandbox
and scanned stdout/stderr. That task is unchecked and no such runtime exists.
The approved design explicitly delegates that sandbox; this lane must consume
it rather than introduce a competing MCP runtime. The broker scanner alone is
not evidence of process/user/filesystem isolation.

The existing recipient update policy is `presentation-updates-v1`
(`command_center_update_policy.py`). Its `presentation_decisions()` refuses
changed retained components and executable UI/capability changes. It is not an
authenticated-author/capability-ceiling grant for executable updates. Extending
that existing mechanism for executable activation remains this lane's 2.1 work;
the presentation policy must not be silently widened or treated as that grant.

One independently useful 2.1 boundary fix is implemented: `ta_cli.extensions()`
now refuses unsupported versions before cataloguing any tool. Previously it
ignored `schema_version`, so a v2 manifest was executable as a legacy tool
without its promised activation protocol. Missing version and integer version
1 retain tool-only behavior; booleans, floats and other version types are
rejected. `hooks`, `commands` and `cards` cannot claim legacy behavior by omitting
the version or declaring v1. This is a fail-closed compatibility boundary, not
v2 activation, code pinning or a substitute runtime. Invalid packages remain
visible on stderr and do not remove unrelated platform capabilities.

Task 2.1 stays unchecked. Tasks 2.2, 2.3 and 3.0-3.6 are not advanced past this
ordered integration gap. The dependency handoff is recorded in
`docs/concerns/harness-control-runtime-dependencies.md`; task 3.5 also requires
deployment and real-user acceptance beyond creation of a draft PR. No new
public handle, permission grant, prompt guidance or static prompt budget is
introduced. The parent D7 delegation remains valid and unchanged.

Boundary verification: Linux oracle Python 3.11.16 with real bubblewrap,
`test_extension_activation_boundary.py`, `test_ta_capabilities.py`,
`test_ta_capabilities_jail.py` and `test_converse_turn_cost.py`: **65 passed,
zero skips**. The first oracle attempt stopped during its source copy because
the plugin rebuild changed the tree; the rerun used stable product files.
Windows boundary/ta tests: **48 passed**. Windows prompt-cost: **9 passed,
1 existing failure** (32,353 vs 30,100 tool-description characters), already
tracked in `docs/concerns/windows-tool-description-budget.md`. No ratchet or
existing test was weakened. Touched-Python Ruff, plugin rebuild/import probe,
strict OpenSpec validation, concern metadata and diff checks pass. No affected
test file is on the heavy-test exclusion list. These results verify this
boundary patch, not task 3.4's verification of the unimplemented capability.

## Live events and orchestration ownership (2026-10-04)

Extend the same owner-bound bridge with subscribe/unsubscribe and a versioned event envelope: opaque event ID/cursor, kind, owner-bound center/agent/run references, resource revision and sanitized payload. Kinds cover activity, tool-call lifecycle, approvals, task-board changes and process health. A subscription applies the same installation capability ceiling and current authority as ta reads. Approval events carry protected request references/status, never decision tokens; clicking opens the first-party approval sheet or Needs you flow, not custom-UI approval chrome.

Use an authoritative snapshot plus cursor and replay to avoid gaps; dedupe by event ID. Bound retention/backpressure and emit a resync-required result when the cursor is unavailable; obtain a new snapshot instead of silently dropping events. Account/center switch, frame destruction, grant revocation and bundle replacement invalidate streams and pending responses. Reconnect cannot carry the prior account's payloads. The transport extends the existing UI bridge; the inline approval lane need not replace its current polling to consume protected decisions.

Blocking order: hooks/settings here; resident-agent-processes for long-lived gateways/heartbeats; bridge events here; connect-anything-ladder for MCP; spawn/message/task-board primitives here; D5 browser; optional attachable compute via existing workspace-node/connect-cross-user-nodes. No founder desktop as infrastructure. Use existing channel, scheduler and agent-system stores. Follow-up package publishing, installed-copy versioned merge and remix credit stay with command-center-packages, command-center-recipient-updates, creator-revenue-share and attribution. Incoming packages never bring author credentials/grants. T1-T10 are a capability checklist for user-built projects, not platform deliverables.

## Capability checklist (T1-T10)

**Founder correction (2026-10-05): capability checklist, not build deliverables.** The platform never builds or ships T1-T10 setups or channel/team templates. Users, starting with the founder's own agent, build these as their own command-center projects, as complex as they like, to exercise the platform's general primitives.

| # | User project reference | General primitives the user needs |
|---|---|---|
| T1 | OpenClaw heartbeat | Persistent execution; editable memory/skills; schedules and scripted checks; inbound receivers and outbound connections; owner notification rules; brokered credential custody. |
| T2 | Henry / Mission Control | Agent spawn; per-agent models; A2A messaging; revisioned task board; schedules; editable memory/files; custom screens, ta parity and live events. |
| T3 | Telegram-forum team | Agent spawn; permitted A2A messaging; inbound receivers/outbound calls; user-authored topic/mention routing; schedules/webhooks; protected approvals. |
| T4 | Hermes skills / cron | Editable skills/memory; versioned history and rollback; scripted schedules; lifecycle hooks and steer commands; owner approval rules. |
| T5 | JARVIS voice HUD | Voice input/output; custom screens/media; replayable activity events; protected approval request references. |
| T6 | Paperclip company | Agent spawn/delegation and A2A messaging; task board with dependencies; per-agent models/budgets and pause; schedules; protected approvals. |
| T7 | n8n Jarvis / Nate Herk | Voice input/output; inbound/outbound channel primitives; OAuth connections and data writes; spawn/delegation and A2A messaging; attribution/usage for user-published work. |
| T8 | TradingAgents debate swarm | Parallel spawn and A2A messaging; schedules; per-agent models/budgets; report screens; data connections; authorized cross-user publication/subscriptions. |
| T9 | Second brain | Owner-scoped files/memory; editable conventions; versioned history; schedules; sync connections; inbound/outbound channel primitives. |
| T10 | pi-style coding team | Lifecycle hooks and extension tools; parallel spawn and A2A messaging; task board; persistent scoped processes; bounded summaries/artifact references; connection calls; optional attached compute. |

This maps capability coverage across existing lanes, not setup acceptance or required publication/copying. Users choose complexity and whether to publish. Existing resident-process, connection, voice, memory/history, package, recipient-update, attribution and cross-user lanes retain their ownership. Approval cards open the protected approval sheet.

### Primitive credential verification

For broker-only credential custody, use synthetic canary keys and a hostile skill probing environment/files, foreign connection slots, response echo (including split streaming chunks) and outbound upload to a test collector. Verify no key bytes reach model/tool context, jail files/environment, logs, transcripts, exported packages or the collector; foreign slots are denied before lookup while permitted operations succeed. Repeat with recipient-local bindings and verify source-account handles are unusable. Record attempted paths and scanned artifacts. Explicit owner raw-key stdio opt-in is a different custody choice and does not satisfy this broker-only criterion. This verifies primitives, not a T1 setup deliverable.
