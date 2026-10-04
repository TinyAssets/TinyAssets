## ADDED Requirements

### Requirement: A universe agent works in a persisted session per thread or agent node
The platform SHALL keep one durable, append-only session per conversation thread and one per agent node (keyed by branch definition and node), recording every message, tool call, tool result, compaction and event, and SHALL build each model request from that session rather than from a re-rendered text summary of recent messages. The session log SHALL be held in platform-owned storage, exposed to the owning universe's agent only through a read-only view, and writable only by the platform.

#### Scenario: a follow-up sees the agent's own earlier tool work
- **WHEN** the founder sends a message after a turn in the same thread that called tools
- **THEN** the next model request contains those tool calls and their results (or a compaction summary that covers them)
- **AND** it does not depend on a fixed message-count or character cap on history

#### Scenario: one thread across surfaces
- **WHEN** the founder continues the same thread from the app, the phone and the connector
- **THEN** each message appends to the same session

#### Scenario: the agent cannot forge its history
- **WHEN** the agent writes to its session log path from a tool
- **THEN** the write is refused and the log is unchanged

#### Scenario: replacing the history renderer preserves provenance
- **WHEN** session entries are rendered into model context by a replacement for conversation_memory.format_history
- **THEN** nonce-delimited untrusted / NOT consent framing and sanitized roles/names remain, and quoted approvals cannot mint current authority
- **AND** input-method metadata remains client-reported, informational and never authority or consent even after its advice helper is removed

### Requirement: Sessions compact automatically with a pre-compaction flush
When a session's projected context exceeds the model window minus a reserve, the platform SHALL first give the agent one round to write durable notes to its files, then, using the universe's own model and credentials with no platform fallback, replace older entries in model context with a structured summary that keeps tool call and result pairs together, while the original entries remain in the log. Thresholds SHALL be read from the universe's own settings file with defaults of a 16,000-token reserve and 20,000 recent tokens kept verbatim.

#### Scenario: a long session keeps working past the window
- **WHEN** a session grows beyond the model window minus the reserve
- **THEN** a flush round runs, a compaction entry is appended, and the next request carries the summary plus recent entries
- **AND** the full history is still in the log

#### Scenario: the owner tunes compaction
- **WHEN** the universe's settings file sets a different reserve
- **THEN** compaction uses that reserve

### Requirement: Native adapters continue sessions through a declared resume capability
An adapter SHALL declare whether it can resume a native session from an opaque handle and whether it compacts itself; the platform SHALL store the handle on the session and resume it when declared, and SHALL seed a new native session from the latest summary and recent entries when the adapter cannot resume or the model changed. No platform policy SHALL name a vendor.

#### Scenario: a resumable adapter continues instead of starting fresh
- **WHEN** a session served by an adapter that declares resume receives its next message
- **THEN** the adapter resumes the stored native handle rather than launching an ephemeral session

#### Scenario: model switch reseeds
- **WHEN** the next turn of a session runs on a different model or an adapter without resume
- **THEN** the platform seeds it from the session's summary and recent entries

### Requirement: Owner messages and owner-relevant events reach the live session
An owner message, or an event concerning the owner's session (a run that session started completing, a request it raised being answered), that arrives while a turn of that session is running SHALL be appended to the result of that turn's next tool call together with the current unread count; when the session is idle it SHALL open the session's next turn as one mechanical line computed by the platform. The platform SHALL NOT impose a wake policy on agents the universe builds.

#### Scenario: a message steers the agent mid-turn
- **WHEN** the owner sends a message while the agent's turn is running tools
- **THEN** the message text and unread count reach the model with the result of the next tool call, in the same turn

#### Scenario: a finished run is reported without being asked
- **WHEN** a run the session started completes while the session is idle
- **THEN** the session's next turn opens with a single line naming the run and its outcome

### Requirement: Every agent has exactly four resident tools over a workspace it fully owns, with breadth behind one searchable command
Every universe agent SHALL have exactly `read`, `write`, `edit` and `bash` as its resident tools, over a workspace the agent fully owns. That workspace SHALL include its own wiki and brain files, which the agent SHALL be able to write. `bash` SHALL have public network egress that refuses loopback, private, link-local and metadata addresses. The workspace SHALL provide a shell toolchain including a language runtime and git.

No other tool schema SHALL be sent to the model. Native adapter built-in tools SHALL be disabled. Platform capabilities, the owner's connected-app operations, user extensions, MCP servers the user attached and the agent's browser SHALL be reachable only through commands in the workspace shell. One of them SHALL provide search, describe and call over the same handlers as the public connector, and the base prompt SHALL carry one line naming it.

#### Scenario: the agent writes its own wiki page
- **WHEN** the agent writes a page into its own wiki
- **THEN** the write succeeds in place, not only in a notes folder

#### Scenario: the agent installs and runs a tool it needs
- **WHEN** the agent runs a package install and then the installed tool in bash
- **THEN** both succeed inside its workspace and the bytes count to the account's storage

#### Scenario: egress cannot reach the host or another user
- **WHEN** bash connects to a loopback, private-range or metadata address
- **THEN** the connection is refused

#### Scenario: a capability is found and called without a resident schema
- **WHEN** the agent needs to start one of its workflows
- **THEN** it finds the capability by searching from bash and calls it there
- **AND** the model request on every round carried only the four tool schemas

#### Scenario: handbook guidance stays current and can be overridden
- **WHEN** a seed skill needs handbook API guidance
- **THEN** it links to D6's platform-versioned read-only reference through ta describe or a skill reference, with examples tested against the real create path, instead of copying chapters into a seed
- **AND** owner-authored skills/instructions override workflow recommendations within existing authority; API contracts and code permission checks still apply

### Requirement: Platform-owned agent state is unreachable from every agent-controlled environment
The following records SHALL be stored outside every agent-controlled execution environment, including the tool jail, workflow provider jails, extension processes and the browser sandbox:
- rules;
- auto-review results;
- activity records and pending effects;
- proposals;
- import quarantine and activation records;
- the agent's browser profile.

Agents SHALL see these records only through read-only projections.

#### Scenario: a workflow cannot forge a rule
- **WHEN** a workflow provider jail or extension process running for the universe attempts to create or modify a rules record
- **THEN** no such path is reachable and the rules are unchanged

### Requirement: Onboarding gives a new universe's agent a name and one responsibility
On a new universe's first conversation, from any surface, the agent SHALL ask for a name and a responsibility. The responsibility SHALL cover what the agent owns, where it learns from, its quality bar, what needs approval, and how often it reports. The answers SHALL be written to the universe's own files. Each approval item SHALL be proposed to the owner as an ask-first rule, and the report cadence SHALL become a scheduled activity. When no compute is connected, onboarding SHALL say so and SHALL NOT simulate a reply.

#### Scenario: a responsibility becomes files, rules and a schedule
- **WHEN** a new user names the agent and states a responsibility including "ask before emailing clients" and "report every Monday"
- **THEN** `AGENTS.md` carries the responsibility, a proposed ask-first rule for client email awaits the owner's confirmation, and a Monday report activity is scheduled

#### Scenario: no compute
- **WHEN** a new user starts onboarding with no compute connected
- **THEN** the reply states that no model is connected and how to connect one

### Requirement: An agent works on several activities at once, without a connected client, and recovers them after a restart
An agent SHALL be able to start activities. An activity is a child session with a title, an origin (owner ask, approved proposal or schedule), a status (`in_progress`, `waiting_on_you`, `scheduled`, `paused`, `completed` or `failed`), a result summary, receipts, its parent agent and its execution context. The main agent's existing session key SHALL be preserved.

Several activities SHALL run concurrently. Each SHALL hold an agent seat while running and SHALL release it while waiting on the owner. An activity started when seats are full SHALL wait visibly rather than be refused. Activities SHALL run without any connected client, and status changes SHALL reach the agent's main session as a platform-computed line. Stopping an activity SHALL keep its partial result.

Each external effect SHALL be recorded with an idempotency key before it is attempted. After a restart, an activity SHALL resume from its last completed tool call. An effect whose outcome is unknown SHALL be reconciled before any retry, and if it cannot be determined the activity SHALL wait on the owner rather than retry.

Scheduled activities SHALL be user-owned automations with an explicit activity target, retaining the authenticated owner, current serving assignment, foreground budget and firing fence.

#### Scenario: two projects continue after the chat closes
- **WHEN** the owner asks for two tasks and closes the app
- **THEN** both activities run to completion and the owner's next visit shows both under Completed with receipts

#### Scenario: a deploy does not duplicate an effect
- **WHEN** the daemon restarts after an activity attempted to send a message but before its outcome was recorded
- **THEN** the activity reconciles that send before continuing, and never sends it twice

#### Scenario: waiting releases the seat
- **WHEN** an activity is waiting on an owner approval while another activity is queued for a seat
- **THEN** the queued activity takes the released seat

### Requirement: The agent's activities and schedules are read completely
The profile's Scheduled view, and the agent's own reads of its activities, schedules and automations, SHALL return every item, paged by an explicit cursor. They SHALL NOT be cut at a size cap.

#### Scenario: a long schedule list
- **WHEN** an agent has more schedules than fit one page
- **THEN** it can page through all of them and recover any schedule's id

### Requirement: Idle proactive research is read-only by capability, and its output is proposals only
When an agent is not paused, has no in-progress activity, has not been messaged by its owner for its idle period, is inside its active hours, has no research turn running, and its cadence window has elapsed, the platform SHALL run a research turn. An event from a connected read source MAY bring the turn forward but SHALL obey the same conditions; events SHALL coalesce, and there SHALL be at most one research turn per cadence window. The defaults SHALL be 30 minutes idle, a 4-hour cadence, and 08:00–22:00 in the owner's clock, on for a new user. The research schedule SHALL appear in the Scheduled view, where the owner can edit it or set it to off. With no compute connected, that row SHALL say so.

A research turn SHALL carry a research flag in its platform-minted execution context. Only audited, side-effect-free read operations SHALL answer a context with that flag; every other operation SHALL refuse it, and the flag SHALL propagate into nested handlers, MCP calls and effectors. Within the turn:
- `write` and `edit` SHALL be refused;
- `bash` SHALL run with the workspace read-only and no egress;
- the browser SHALL be unavailable;
- extensions, hooks and the pre-compaction flush SHALL NOT run.

Platform bookkeeping (session journal, output spills and usage accounting) MAY still be written. The turn's only outward output SHALL be structured proposals, which the platform validates, bounds and stores through a dedicated proposal path. Approving a proposal SHALL start an activity in which that exact action counts as pre-approved.

#### Scenario: research cannot change anything
- **WHEN** a research turn attempts a file write, a network connection from bash, a mutating platform call, an automation, a request action or a connected-app write
- **THEN** each attempt is refused and nothing changes apart from platform bookkeeping

#### Scenario: a read that mutates is not available to research
- **WHEN** a platform read operation would retire or create a request as a side effect
- **THEN** it refuses a research context until it is side-effect free

#### Scenario: a proposal is approved
- **WHEN** a research turn proposes sending an unsent invoice and the owner approves it
- **THEN** an activity starts in which sending that invoice is pre-approved and still passes auto-review

#### Scenario: events do not multiply research
- **WHEN** a connected source reports ten new items within one cadence window
- **THEN** at most one research turn runs in that window

### Requirement: Blocked work becomes something the owner can grant
When a turn or research finds work it cannot do, the agent SHALL turn each block into one of three things:
- a proposal naming the exact rule change, connection or capability that would unblock it;
- a patch request when the gap is in the platform;
- a scheduled recheck stating its condition, when the block is waiting on a person or on time.

The agent SHALL NOT leave a static list of blocked items.

#### Scenario: a missing grant becomes a proposal
- **WHEN** the agent cannot open a pull request because its rules do not allow writes to that repository
- **THEN** the owner receives one proposal naming that rule, and approving it resumes the work

### Requirement: Every action is decided by the owner's Custom Rules for the initiating agent
The platform SHALL mint an execution context for every execution and SHALL NOT accept one from an agent. The context names the universe, the initiating agent, the delegated authority, the research flag and the approval id if any. It SHALL propagate through the platform command, workflows and automations an agent creates, activities it starts, extensions, MCP calls and effectors.

Work an agent creates or delegates SHALL run with at most that agent's authority. An activity one agent starts on another SHALL run with the lesser of the two agents' authority unless the owner grants more. Editing another agent's harness SHALL be its own action class.

Before executing, every enforcement point SHALL call one decision function with the action's class and the context. The enforcement points are the tool layer, the platform command, the credential-blind effectors, channels, the egress proxy and the browser broker. The function returns the behaviour of the most specific matching rule for the initiating agent; when two rules are equally specific, it returns the stricter one. There are four behaviours:
- **do:** proceed, after auto-review when the action is consequential.
- **do if pre-approved:** proceed only when an authenticated owner message in the session or an approved proposal names exactly this action. Otherwise the action is treated as ask first.
- **ask first:** raise one app request and set the activity to waiting on you.
- **hand off:** raise a request for the owner to perform the action. The agent SHALL NOT execute it.

Semantic classes (messaging, payments, security changes, access grants, reads) SHALL come only from trusted declarations by connections, channels and integrations. An undeclared operation on a generic effector SHALL be treated as a consequential write. Browser interactions SHALL be an interaction class only, not inferred payments.

Rules SHALL be owner-edited. The agent SHALL be able to read them and propose changes, and its writes to rules SHALL be refused.

Seed rules SHALL allow every workspace action, the agent's own harness edits, shell egress and connected-app reads, and writes to destinations under an existing standing grant. They SHALL set ask first for new destinations, undeclared operations, messaging people, publishing, sharing, spending over budget, consequential browser actions and editing other agents' harnesses. Standing destination grants and their call-time checks SHALL be preserved, and an approval marked "always allow" SHALL write a do rule.

#### Scenario: approve once, then reuse
- **WHEN** the agent first emails a new client under an ask-first rule, the owner approves with "always allow", and a later activity emails the same client
- **THEN** the later email proceeds without a new request

#### Scenario: a created workflow cannot exceed its creator
- **WHEN** an agent whose rules set ask first for a connection creates a workflow that later calls that connection with the owner signed out
- **THEN** the call is decided by the creating agent's rule and becomes a request

#### Scenario: the agent cannot loosen its own rules
- **WHEN** the agent writes to its rules from any tool or environment
- **THEN** the write is refused and the agent may instead raise a rule-change request

### Requirement: Consequential actions pass an auto-review that can only tighten
By default, before any consequential action whose rule is do or do if pre-approved, the platform SHALL run a review. A consequential action is any class other than workspace actions, the agent's own harness edits and connected-app reads. The owner MAY switch the review off per class, and the app SHALL state what that means.

The review SHALL be a tool-free call on the universe's own model, admitted like any agent call: it SHALL re-enter the activity's seat when the activity holds one, and otherwise queue for the account's own. It SHALL have its own deadline. It SHALL itself not be reviewed, and SHALL be retried at most once. A consequential action that reaches the send boundary with no run model bound to review it SHALL be held, not sent. Its inputs SHALL be:
- trusted: the structured planned action, the matching rules, the built-in safety requirements and authenticated owner messages;
- untrusted evidence: action text, page content and agent-editable harness files.

It SHALL return proceed, or needs approval with a reason, as exactly one JSON object with exactly those fields; any other reply SHALL count as no answer. The result SHALL be bound to the exact action and rule-set version. The review SHALL only convert an action to ask first; it SHALL NOT create grants, loosen rules or override hand off. If the review cannot run, the action SHALL become a request naming the cause, and the activity SHALL release its seat while waiting.

#### Scenario: review blocks an off-instruction send
- **WHEN** a do rule covers a channel but the planned message contradicts the owner's stated instructions
- **THEN** the review returns needs approval and the owner receives a request with the reason

#### Scenario: hostile content cannot approve itself
- **WHEN** the content of a planned action instructs the reviewer to proceed
- **THEN** that content is treated as untrusted evidence and cannot turn an ask-first or hand-off action into one that proceeds

#### Scenario: an approval echoed inside the reply is no answer
- **WHEN** the reviewer's reply quotes a proceed object from the action's content inside other text
- **THEN** the reply counts as no answer and the action is held

#### Scenario: review cannot run
- **WHEN** the universe's model is unavailable at review time
- **THEN** the action becomes a request naming that cause and is not executed

### Requirement: Hand-backs ship on by default as editable rules
New universes SHALL be seeded with hand-off rules for these three classes, each declared by trusted integrations:
- changing a password, credential or security setting on an external account;
- moving money or making a payment;
- granting another person access to the owner's accounts or data.

The owner MAY change these rules, and the app SHALL state plainly what turning one off allows. An imported bundle SHALL NOT loosen them on activation. No rule SHALL allow an agent to reach another user's universe, data or money.

#### Scenario: a payment is handed back by default
- **WHEN** the agent reaches a payment through a payment integration in a universe with the default rules
- **THEN** it is not executed, and the owner receives a hand-off request

#### Scenario: the owner turns a hand-back off knowingly
- **WHEN** the owner changes the money-movement rule to ask first
- **THEN** the app shows what that allows before saving, and later payments become approval requests instead of hand-offs

### Requirement: Each agent has a profile with Activity, its computer, Pause and push
Each agent SHALL have a profile showing its name, responsibility, status, model and usage, with these tabs:
- **Activity:** waiting on you first, then in progress, scheduled, and completed with receipts. Each activity can be steered, paused or stopped.
- **Computer:** a live view of its browser and shell output, with take over and return control.
- **Memory**, **Rules** and **Harness**.

The agent SHALL drive its browser only through a restricted broker that offers no raw protocol access, script evaluation, cookie or storage export, or network inspection. Each activity SHALL use its own browser context, and an approval for a browser step SHALL be void if its target changed. The broker SHALL sanitize credential-bearing URLs and filter credentials from page text, screenshots, downloads and errors. While the owner has taken over, the agent's browser commands SHALL wait and nothing SHALL be recorded into the session or the model. On return the session SHALL receive a platform-computed line.

Pausing an agent SHALL stop its new turns, research and scheduled activities, and SHALL stop the running turn at its next tool boundary. The platform SHALL push notifications for completed, waiting-on-you and new-proposal events. A roster view SHALL show every agent with status, waiting count, current activity and next scheduled run, with inline approve and pause. Both views SHALL be built on the user-editable custom-UI layer.

#### Scenario: the owner logs in for the agent
- **WHEN** the agent hits a login page, the owner takes over, signs in and returns control
- **THEN** the agent continues with the logged-in session, and the password appears in no session log, screenshot or model request

#### Scenario: pause holds everything
- **WHEN** the owner pauses an agent with a running activity and a scheduled one
- **THEN** the running turn stops at its next tool boundary, the scheduled activity does not start, and no research turn runs until resume

### Requirement: Memory is editable item by item
Agent memory SHALL be held as items with stable ids. The owner SHALL be able to view, edit and delete any single item. Each change SHALL be recorded in the file history with undo, and deleting an item SHALL NOT require deleting the agent.

#### Scenario: one memory is removed
- **WHEN** the owner deletes one memory item from the profile
- **THEN** the next turn's memory lacks that item and every other item is unchanged

### Requirement: Learning retirement preserves unresolved sources
D7 alone SHALL retire extract_learning, _learn_from_turn and its converse invocation, _UNRECORDED_LESSON and extraction-only helpers, after memory IDs, the history store and Undo are deployed and verified. Before removal, D7 SHALL preserve unresolved cursor spans verbatim with conversation/turn source IDs in an owner-only review artifact, retain original history and existing memory, and SHALL NOT mark unwritten facts learned. Migration SHALL be idempotent across crashes and SHALL NOT re-extract settled history. The editable memory/review workflow SHALL deduplicate against current notes and mark a source handled only after verified persistence or an explicit no-fact decision; failed work SHALL remain visibly pending without breaking reply delivery. Replacement agents SHALL have the same authorized access to the backlog. At D7's release, extraction SHALL be removed globally without an owner-response gate, hidden background replacement or per-center compatibility path; seed adoption and file Undo SHALL NOT control extraction.

D7 acceptance SHALL include N>=10 paired natural fact-teaching trials per supported model family with extraction off against an extraction-on baseline using matched fixtures/settings. Verified immediate durable-write rate and later cross-surface recall rate SHALL each be no worse than baseline in every family, with numerators, denominators and traces reported separately. Negative controls SHALL cover jokes, hypotheticals, credentials and failed writes. Starter renderer savings SHALL NOT count this separate removal.

#### Scenario: an unsettled conversation survives retirement and retry
- **WHEN** D7 migrates a conversation with unresolved source spans and the process restarts before completing the migration
- **THEN** retry preserves each span's original text and source IDs exactly once, retains the original history and memory, and leaves unprocessed sources pending
- **AND** an existing or replacement agent can review them without a resurrected extractor or false learned marker

#### Scenario: a failed write does not settle a source
- **WHEN** the memory/review workflow cannot persist a fact from a pending source
- **THEN** the source remains visibly pending, the failure is reported and reply delivery continues
- **AND** only a later verified write or explicit no-fact decision settles that source

#### Scenario: extraction removal fails the memory comparison
- **WHEN** a supported model family has a lower immediate write rate or later recall rate than its extraction-on baseline
- **THEN** D7 retirement acceptance fails and the regression is corrected before global removal
- **AND** no pooled score, owner acceptance or seed-file adoption bypasses that evidence

### Requirement: Every per-agent record is keyed by agent, and the brain is shared
Every per-agent record (sessions and conversation memory, steering and stop, the tool journals and status lines, rules and auto-review switches, activities, pending requests with their deduplication and answer routing, and notifications) SHALL be keyed by agent id, with the seeded main agent only a default; an agent id SHALL never replace the owner and universe binding. The universe's brain and memory files SHALL be shared by all of its agents. Each agent's harness configuration SHALL carry a visibility scope the owner may change, applied to what the platform places in the agent's context and returns from its reads: the seeded main agent SHALL default to every agent's conversations with the owner and every agent's activity within the universe, and other agents SHALL default to their own threads and activities plus the shared brain. The scope is not isolation between one owner's agents, which share the universe's files. No scope SHALL reach another user's universe. Concurrent brain writes through the platform's tools SHALL NOT silently overwrite each other: owner feedback SHALL be captured as one exclusively created entry file per item naming the agent, tool writes and edits to a brain file SHALL apply atomically only to the whole-file version the agent read and otherwise be refused with the current content, and one reconciler agent (the main agent by default) running single-flight SHALL fold entries into topic files, recording absorbed entry ids before acknowledging them so a replay after a crash loses nothing.

#### Scenario: feedback to a specialist reaches the shared brain
- **WHEN** the owner tells a specialist agent a preference and later asks the main agent about it
- **THEN** the preference is in the shared brain's log, the main agent reconciles it into the topic file, and the main agent knows it

#### Scenario: two agents edit the same brain file
- **WHEN** two agents write the same brain file from the same starting version
- **THEN** the second write is refused with the current content and neither change is lost

#### Scenario: a specialist's context holds its own threads by default
- **WHEN** a specialist agent with default visibility reads conversations through its tools
- **THEN** it is served its own threads only, while the main agent with default visibility is served all of them

### Requirement: The owner talks to any of their agents directly
A conversation turn SHALL carry the agent it addresses, `main` by default or one of the owner's own agents bound in that universe, and SHALL run as that agent: its own instructions from its definition, its own thread (conversation memory `agent:<agent_id>:principal:<owner>`, native session and steering key `thread:` plus that), on the universe's serving engine and seats, reading and writing the universe's shared brain. A lesson taught to a non-main agent SHALL reach the shared brain except a name or identity file, which would rename the main agent. The main agent's turn SHALL see the owner's recent turns with the other agents as untrusted context naming each agent. An addressed id that is not the caller's own conversable agent in that universe (unknown, another account's, bound in another universe, or a conversation-design installation) SHALL be refused by name before any model call or stored turn, never answered by the main agent instead. A steer and a conversation read SHALL name the addressed agent the same way. A custom UI SHALL be able to open the app's chat addressed to one of the viewer's agents and send a turn to a named agent, and SHALL be refused for an agent that is not the viewer's.

#### Scenario: clicking a villager talks to that agent
- **WHEN** the owner addresses a custom agent and sends a message
- **THEN** the reply comes from that agent's instructions on its own thread, the main thread is unchanged, and the main agent's next turn sees that exchange

#### Scenario: a foreign agent id is refused
- **WHEN** a turn, steer or conversation read names an agent that belongs to another account or another universe
- **THEN** it is refused as not one of the caller's agents, no model is called and nothing is recorded

### Requirement: The harness layer is user-configurable for any roster of agents
A universe SHALL support any number of agents. Each agent SHALL have its own instructions, identity, memory, skills, extensions, settings (model, research cadence, idle period, active hours, compaction, channels), rules, sessions, activities and profile. A per-agent skill or extension SHALL override a shared one of the same name. An agent SHALL be able to start an activity on another agent in the same universe under the delegation rule. New universes SHALL be seeded from an explicitly published starter template. D10 SHALL use starter-seed-lifecycle as its sole provisioning and upgrade mechanism, including per-path installed-hash receipts at creation, autonomous stock upgrades, preservation of owner customizations/deletions, visible version offers and conditional file Undo. The starter renderer cutover SHALL include every center without waiting for owner review; D9 activation of imported third-party bundles SHALL remain separate.

#### Scenario: a lead hands work to a specialist
- **WHEN** the owner's main agent starts an activity on a specialist agent with narrower rules
- **THEN** the activity runs at the lesser authority and appears in the specialist's profile

### Requirement: Harness bundles and command-center layouts are shareable through an owner-activated quarantine
An owner SHALL be able to export one agent's or a roster's harness, optionally with profile and roster layouts, as a versioned bundle carried by a public agent definition. The bundle SHALL have relative, normalised paths, and SHALL be refused, with its size named, when it exceeds the carrier's limit. It SHALL be published only after the owner confirms a scrubbed preview. It SHALL exclude memory unless the owner opts in item by item, and SHALL always exclude session logs, credentials and browser state.

An import SHALL be held in platform-owned storage outside every agent-writable location. Only an owner-controlled activation record SHALL admit it: until then no rules apply, no extensions load, no schedules register, no channels connect, and its content enters no index or trusted prompt. Imported rule suggestions SHALL activate only as written or stricter, unless the owner edits them.

#### Scenario: an imported bundle does nothing until activated
- **WHEN** a user imports another user's bundle that contains a schedule, a skill and an extension
- **THEN** nothing is scheduled, indexed or executed, and the agent cannot copy it into an active location, until the user activates it

#### Scenario: export never carries secrets
- **WHEN** an owner exports an agent
- **THEN** the bundle contains no session log, credential, browser state or unselected memory, and the owner's contact details are scrubbed from its text

### Requirement: A command center exports to a runnable, publish-ready folder
An owner SHALL be able to export a command center in one action to a folder (also offered as a zip) using the same bundle manifest and path rules as sharing and import. The folder SHALL contain each agent's harness (instructions, persona, skills, extensions, prompts, rules, model and schedule configuration as references), the roster, the agent's workspace files, the wiki as an OKF bundle and the brain files, workflows and automations as data, the memory items the owner selected, the command-center layouts, a local runner, a README, a LICENSE placeholder the owner chooses, a `.gitignore` excluding secrets, `.env` and runtime state, and a `.env.example`. The manifest SHALL declare a profile: `share` (the harness subset through the public carrier, size-limited, contact details always scrubbed) or `export` (the whole folder, private local output). Credentials SHALL never be exported: structured fields SHALL be serialized schema-aware with connections as named references and `.env.example` lines, and any file in which a credential is detected SHALL stay excluded regardless of owner approval. Arbitrary content (workspace files, wiki and brain prose, binaries) SHALL start excluded and be included only by the owner after a content preview that states its detection limits. Session logs and browser state SHALL never be exported; contact details MAY be included item by item only in the `export` profile. Publishing SHALL be a separate owner-approved action that re-runs the preview without personal-data inclusions.

The local runner SHALL run the exported command center with no platform account or network access to the platform: the four tools over the folder, the same `AGENTS.md` and skill format, the exported rules as prompts, any OpenAI-compatible model endpoint configured locally, and the exported schedules while the machine runs once separately enabled. By default it SHALL confirm each bash command, block when no approval is given, confine file tools to the folder, and keep its own policy files read-only to the agent's tools; confirming each bash command SHALL be a default the owner may loosen for their own command center, and SHALL stay enforced for an imported or shared command center until its owner activates and configures it; its README SHALL disclose that it runs with host privileges. The README SHALL state what works without the platform and what does not. Publishing SHALL remain user-built: the platform SHALL provide no publishing effector, and the agent publishes with its own tools on the owner's own connection under the owner's rules. Import SHALL accept an exported folder, zip or cloned repository of that layout into quarantine through an ingestion boundary that bounds bytes, file count, depth and time, rejects absolute or traversal paths, links, special files and path collisions, copies only validated regular files, excludes `.git`, and executes nothing supplied. The OKF wiki export SHALL be the export's only OKF writer.

#### Scenario: run locally with no account
- **WHEN** the owner exports their command center, sets `MODEL_BASE_URL` to a local Ollama endpoint and starts the runner
- **THEN** they can chat with their agent, which reads and edits its exported workspace with its four tools, with no TinyAssets account or network

#### Scenario: a published export carries no secrets
- **WHEN** the owner exports and their agent pushes the folder to the owner's GitHub
- **THEN** the repository contains no credential, `.env`, session log, browser state or unselected memory, and `.env.example` names the keys to add

#### Scenario: a credential in a workspace file stays out
- **WHEN** a workspace file contains an API key and the owner selects it in the preview
- **THEN** it remains excluded and the preview names why

#### Scenario: a cloned command center imports into quarantine
- **WHEN** a second user imports a clone of that repository
- **THEN** it lands in quarantine and nothing loads, schedules or connects until they activate it

### Requirement: The harness is files the agent edits, versioned with rollback
Turn assembly SHALL be mechanical: a base prompt, then the universe's `AGENTS.md`, then a bounded `MEMORY.md`, then the skill index, then the session. The agent SHALL be able to edit `AGENTS.md`, `MEMORY.md`, its skills, prompts, extensions and settings file (but not its rules, which are owner-only), and the platform SHALL record every turn's changes to tracked universe files in a platform-owned history store the agent cannot write, with any version-control process run inside a credential-free jail rather than on the host, and the owner SHALL be able to roll back from the app.

#### Scenario: a told preference changes the next turn
- **WHEN** the founder tells the agent how to work and the agent edits `AGENTS.md`
- **THEN** the next turn's request contains the edited text

#### Scenario: a bad self-edit is undone
- **WHEN** a turn empties or more than halves a loaded harness or brain file
- **THEN** the change is committed, the app offers an Undo for it, and the agent's next event line names it
- **AND** Undo restores the previous content

#### Scenario: a saved skill is used in a new session
- **WHEN** the agent writes `skills/<name>/SKILL.md` and a later session's task matches its description
- **THEN** the skill appears in that session's index and its body is readable on demand

### Requirement: Tool results are truthful, fast and never silently truncated
Every adapter's tool calls SHALL be journaled into the session log. A tool failure SHALL return its actual cause, and a result larger than the model budget SHALL be written to a file in the session's output area and returned as a head plus that path.

#### Scenario: an oversized read is still complete
- **WHEN** a platform read returns more than the model budget
- **THEN** the agent receives the beginning and a path from which it reads the full result

#### Scenario: native tool calls are visible
- **WHEN** a native adapter turn calls tools
- **THEN** each call and its result appear in the session log and the app's activity view

### Requirement: The default voice is concise and result-first
The seed `AGENTS.md` given to a new universe SHALL instruct reporting results first in a few lines, verifying effects before reporting them, and stating unverified items only when they change what the owner should do; the platform's base prompt SHALL NOT add persona, warmth, curiosity or ask-to-clarify instructions of its own.

#### Scenario: the base prompt carries no persona policy
- **WHEN** a turn is assembled for a universe whose `AGENTS.md` is empty
- **THEN** the request contains the base prompt's tool, folder and untrusted-envelope lines and no platform-authored persona or tone instructions
