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

Extend `extension.json` with `schema_version: 2`, existing executable/tools declarations, ordered hook registrations, commands and card contributions. Version 1 tool-only manifests continue working with no implicit hooks. Entry points are relative no-link jail paths. A launch pins manifest and executable hashes; edits become effective next turn after normal activation. Run hooks in the same owner/center execution environment and no stronger launch grant than the active agent; no daemon Python imports or host execution.

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

## Live events and orchestration ownership (2026-10-04)

Extend the same owner-bound bridge with subscribe/unsubscribe and a versioned event envelope: opaque event ID/cursor, kind, owner-bound center/agent/run references, resource revision and sanitized payload. Kinds cover activity, tool-call lifecycle, approvals, task-board changes and process health. A subscription applies the same installation capability ceiling and current authority as ta reads. Approval events carry protected request references/status, never decision tokens; clicking opens the first-party approval sheet or Needs you flow, not custom-UI approval chrome.

Use an authoritative snapshot plus cursor and replay to avoid gaps; dedupe by event ID. Bound retention/backpressure and emit a resync-required result when the cursor is unavailable; obtain a new snapshot instead of silently dropping events. Account/center switch, frame destruction, grant revocation and bundle replacement invalidate streams and pending responses. Reconnect cannot carry the prior account's payloads. The transport extends the existing UI bridge; the inline approval lane need not replace its current polling to consume protected decisions.

Blocking order: hooks/settings here; resident-agent-processes for long-lived gateways/heartbeats; bridge events here; connect-anything-ladder for MCP; agent-team-channel-templates for channel templates and spawn/message/task board; D5 browser; optional attachable compute via existing workspace-node/connect-cross-user-nodes. No founder desktop as infrastructure. Use existing channel, scheduler and agent-system stores. Follow-up package publishing, installed-copy versioned merge and remix credit stay with command-center-packages, command-center-recipient-updates, creator-revenue-share and attribution. Incoming packages never bring author credentials/grants. T1–T10 are integrated acceptance, not a claim that this one implementation supplies every dependency.

## Acceptance criteria — rebuild it here, better (T1–T10)

The following acceptance bar is preserved from the supplied analysis; it is future verification, not a result. In T5, approval cards mean entry points to the founder-mandated protected approval sheet. No side requests panel is permitted.

Each test is passed only if three things hold:
- it is built **only from general primitives**;
- it is **published** and **copied by a second account**;
- the copy **runs 24/7 with no host online**.

The final proof is a rendered conversation through the live surface.

| # | Original | Rebuild spec (pass criteria) | Needs | Better-than-original bar |
|---|---|---|---|---|
| T1 | **OpenClaw personal agent with heartbeat** (#1) | One agent with SOUL/MEMORY files; 30-min heartbeat that messages *only* when warranted; Telegram in/out; Gmail and Calendar skills | C1/C2, C10, C9, C6 | No Mac mini; copy-and-run in under 5 min; skills cannot exfiltrate keys |
| T2 | **Alex Finn's Henry plus Mission Control** (#3) | Chief of staff plus four specialists on *different* models; screens for task board, calendar, projects, memory, docs and team; mission statement drives idle reverse-prompting; the agent builds a new screen on request | C4, C5, C12 (bridge parity plus events), C15, C2 | Zero hardware instead of four Mac Studios; BYO and free models; the whole team copied in one click |
| T3 | **Dan Malone's Telegram-forum team** (#5) | Four agents, one forum topic each, mention routing in General, A2A messaging with permission, crons plus webhooks, confirmation on sensitive ops | C10 routing, C5 A2A, C3, C14 | Routing is data in the template, so another user installs it against *their own* forum |
| T4 | **Hermes self-improving agent plus cron** (#8) | Agent writes and refines its own skills; nightly 3 AM digest of the day's conversations; `/steer` and `/rollback`; zero-LLM scripted cron | C6, C2, C8 (steer via hooks), C14 | Learned skills are publishable; rollback is backed by storage versioning |
| T5 | **JARVIS voice HUD** (#9) | Voice in/out in a command center; live activity feed; approval cards; agent-summoned media panels | C11, C12 events, C14 | No LAN box; works from phone and desktop; same HUD on any command center |
| T6 | **Paperclip zero-human company** (#17) | CEO agent turns a mission into goals and delegates down an org chart; ticket board; per-agent budgets with auto-pause; approval gates | C4, C5, C15 budgets, C14, C2 | Each agent's provider is BYO; the company is a template others fork and remix |
| T7 | **n8n Jarvis / Nate Herk agent team** (#10/11) | Telegram voice to an orchestrator over email, calendar, contacts and content sub-agents; expenses logged to a sheet | C10, C11, C9 (OAuth directory), C4 | No n8n hosting and no credential JSON; OAuth is one click; the creator earns on run-time rather than an affiliate link |
| T8 | **TradingAgents debate swarm** (#22) | Parallel analyst agents, bull/bear debate, trader, risk; scheduled daily per ticker; HTML report screen; per-tier models | C4 parallel, C2, C12, C15, C24 | Scheduled 24/7 without a laptop; the report is a shareable page; other users subscribe cross-user to *your* published signal (two-way C21) |
| T9 | **Second brain (Obsidian plus Claude Code / Cole Medin)** (#24/25) | Vault-as-files memory, CLAUDE.md conventions, 24/7 content engine that produces drafts and SOPs on a schedule | C6, C2, C9 (Obsidian/Drive sync) | Brain lives in the universe and is reachable from any channel; a template ships with empty slots, never the creator's notes |
| T10 | **pi-style multi-agent coding team** (Pi-Agents-Team / Gas Town lite, #18/pi) | An orchestrator extension registers `dispatch_agent`; three workers in parallel boxes return summaries only; a guardrail hook blocks writes outside the repo; PRs opened via a GitHub connection | **C8**, C1, C4, C16, C9 | Runs while the user sleeps with no tmux; hooks are sandboxed; the extension itself is publishable |

Reserve tests: the Home Assistant voice agent (#26), which needs MCP attach plus a local-network compute attachment (C16); and a family shared agent on WhatsApp (Hermes story), which needs C21 and multi-member access.

### Acceptance ownership

| Tests | Implementing dependencies alongside this change |
|---|---|
| T1, T3, T10 | resident-agent-processes; agent-team-channel-templates; connect-anything-ladder |
| T2, T6, T8 | agent-team-channel-templates; existing model/budget, scheduler and cross-user recipient lanes |
| T4, T9 | Existing D7 memory/history, scheduler and connection lanes; hooks/settings here |
| T5, T7 | Existing voice/channel/renderer lanes; bridge events here; inline-connect-and-approve and connection ladder; creator-revenue-share for T7 |
| Every T1–T10 | command-center-packages and command-center-recipient-updates plus attribution: publish, second-account copy with local bindings, no leaked source state; live rendered proof on cloud-only compute |

Record source and copy identities, package revisions, deployment SHA, rendered proof and host-off run evidence for every test. No box is marked accepted by a mocked demo or merely by publishing a package.
