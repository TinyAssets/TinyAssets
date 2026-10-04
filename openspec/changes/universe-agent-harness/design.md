# Design: every universe is an always-on dot

Status: **revised 2026-10-01 to the founder's dots direction. Needs founder
approval again.** The first version (approved 2026-10-01, sessions, tone, tools
and self-improvement) is kept wherever this revision does not replace it. §5
lists what is kept and what changed. This document is the design and contains
no code. Production measurements in §3 were taken read-only on 2026-10-01,
through `ssh workflow-droplet` → `docker exec tinyassets-daemon`, against
`/data/.tinyassets.db` and `/data/u-01kxm1vszd8hwp7em418asq8h9/`.

## 1. Founder direction and the product it names

### 1.1 Direction (verbatim, 2026-10-01)

> "a new users universe should act like an always on dot agent from chatgpt.
> but with the customization of openclaw and the clean powerfull tool list of
> pi.dev and user design shareabliaty for harness and on ui interface command
> center designs for agent management"

> "it should work how ever dots work for chatgpt. dont get past background self
> work that was a user project anyways and i dont want you matching or thinking
> about that shape, i want you building what ever it is that a chatgpt dot agent
> expereance is like. pi's 4 tools, users should have full customization of the
> harness layer for any configeration of agents"

The second message rules out a source of design. The always-on behaviour here
copies what dots do. It is **not** derived from, and is not measured by, the
background agent tiny built for itself or any user-built heartbeat graph. Those
remain user projects built from graph primitives, and this design neither
reuses nor changes their shape.

### 1.2 ChatGPT dots as shipped (OpenAI DevDay, 2026-09-29)

`openai.com` and `help.openai.com` answer 403 to automated fetches, so OpenAI's
wording below comes from search excerpts of its pages and from press that quotes
them. The full research brief, with every citation, is
`ta-scratch-lead/dots-research.md`. It is outside the repo.

| Dots behaviour | Source |
|---|---|
| An always-on agent with **its own cloud computer and browser** (Linux plus Chrome). It keeps working after the chat closes and can "keep several projects moving simultaneously, accept new work without forcing users into separate conversational threads." | [VentureBeat 09-29](https://venturebeat.com/technology/openai-launches-dots-always-on-ai-agent-coworkers-and-chatgpt-space-where-they-can-collaborate-with-human-teams), [Anima 09-30](https://animaapp.com/blog/agentic/openai-dots/) |
| **Onboarding:** give it a name and a clear responsibility, meaning what it owns, where it learns, its quality bar, what needs approval, and how often it reports. Connect apps and set rules. | Anima; [Codersera 09-30](https://codersera.com/blog/openai-chatgpt-dots-guide-2026/) |
| **Proactive research.** When you are not working with it, it "looks for ways to help in the background … using read-only tools restricted in code, so they cannot send messages, change content in connected apps, or control a browser or computer, and any follow-up action must pass the usual rules and checks." | help.openai.com excerpt; [Unite.AI 09-29](https://www.unite.ai/openai-rolls-out-dots-agents-powered-by-gpt-6-astra-in-chatgpt/) |
| **Custom Rules:** each action gets one of four behaviours: *Take action without asking*, *Take action if pre-approved* (only the exact action the user asked for), *Ask before taking action*, or *Hand off to you*. | help.openai.com excerpt; Codersera |
| **Auto-review** "checks potentially consequential actions against the user's instructions, Custom Rules and OpenAI's built-in safety requirements before determining whether the work can proceed autonomously or requires approval." It cannot be turned off. | VentureBeat; Codersera |
| **Reserved actions.** Changing a password or moving money "must be handed back to the user". Permanently deleting data or installing unrecognised software "requires confirmation each time". | Unite.AI |
| **Profile and Activity:** *In progress / Scheduled / Completed*, the live computer with **Take over / Return control**, and **Pause** from the ••• menu. "Activity View lets users inspect background work and intervene." | VentureBeat; Anima; help.openai.com excerpt |
| **Notification:** a phone push when a task is done. Reachable in ChatGPT on web, desktop and phone, in Slack and Teams, and by voice. | help.openai.com excerpt; Unite.AI |
| **Memory:** it learns preferences from feedback. Individual memories "cannot currently be viewed or edited". The only way to clear them is to delete the dot. | Unite.AI |
| **Limits:** one dot per plan, OpenAI's model, and setup only on desktop. | Codersera; [MediaNama 10-01](https://www.medianama.com/2026/10/223-openai-launches-dots-devday-2026/) |

**What we copy as-is:** everything in the table except the last two rows.
**What we change:** memory is viewable and editable item by item, the number of
agents is unlimited and configured by the user, and every model call runs on
the user's own compute because the platform never supplies an LLM. **What we
add from the founder's other references:** OpenClaw-level customization of the
harness, pi's four tools, and harness and command-center designs that users
can share.

### 1.3 pi's tool list (the "clean powerful tool list")

pi's defaults are `read`, `write`, `edit` and `bash`. Its prompt plus tool
definitions come to under 1k tokens
([pi.dev](https://pi.dev)). pi 0.99 (v0.99.2 of 2026-09-30) added MCP without
adding resident schemas: servers are reached through one programmable layer
(`codemode`, with `searchTools()` / `describeNamespace()`) and are not listed
in the tool description
([release](https://github.com/earendil-works/pi/releases/tag/v0.99.2)). This
design keeps exactly the four tools. Bash is the programmable layer, and `ta
search` is the discovery call (§4.7).

## 2. Research: the four reference harnesses (sources fetched 2026-10-01)

| | pi (pi.dev, earendil-works/pi, v0.99.2 of 2026-09-30) | OpenClaw (openclaw/openclaw, v2026.9.7 of 2026-09-30) | Claude Code (code.claude.com/docs, auto mode default from v2.1.283) | Hermes Agent (NousResearch/hermes-agent, v2026.9.24 of 2026-09-24) |
|---|---|---|---|---|
| **Loop / session length** | A run continues while tool results or queued messages need another model call, then ends. Sessions are persistent JSONL trees with resume, fork and branch. | Serialized per-session runs. The default runtime budget is **48 h** (`timeoutSeconds` 172800, `0` = unlimited). Progress does not reset it, and a separate idle watchdog (120 s cloud) catches a hung model. | Gather, act, verify, repeat until done. Sessions persist as JSONL and support `--continue`, `--resume` and fork. | Persistent sessions. `/goal` adds a judge model after every turn that feeds "continue" back into the same session until the goal is met (the Ralph loop). |
| **Context** | Auto-compaction when `tokens > window − reserve` (reserve 16k, keep the latest 20k). It writes a structured iterative summary as a tree entry, and the originals stay on disk. | Auto-compaction keeps tool call/result pairs intact. Before compacting it runs a silent **memory flush** turn so the agent saves durable notes. A safeguard mode audits summary quality. | Auto-compaction plus `/compact`. CLAUDE.md is always loaded. **Auto memory** loads `MEMORY.md` (first 200 lines or 25 KB) every session. | `MEMORY.md` (2,200 chars) and `USER.md` (1,375 chars) are a frozen snapshot in the system prompt. A full store is an error the agent must consolidate. `/compress`. FTS5 search over past sessions. |
| **Tools** | Defaults are `read`, `bash`, `edit`, `write`, with optional `grep`, `find`, `ls`. MCP servers default to **codemode** (scripts call tools found by `searchTools()`) or **deferred** (`tool_search`), so their schemas are never resident. | `exec`, `process`, `read`/`write`/`edit`/`apply_patch`, web search and fetch, `browser`, `message`, `sessions_*`/subagents, `cron`, `tool_search`/`tool_call` for large catalogs, plugins. | File ops, search, shell, web search and fetch, subagents, MCP with deferred tool loading, Claude in Chrome. | 40+ tools: terminal, files, browser, `web_search`, `execute_code` (scripts call tools over RPC), `delegate_task`, `memory`, `session_search`, `cronjob`, MCP. Seven terminal backends (local, Docker, SSH, Modal, Daytona …). |
| **Extensions / skills** | Agent Skills spec (agentskills.io). Only name, description and path sit in the prompt, and the body loads on demand. TypeScript extensions add tools, hooks and providers. Packages ship via npm or git. "Ask Pi to create the … skills, extensions … you need." | Skills plus **Skill Workshop**. Plugins and ClawHub. | Skills (Agent Skills standard; commands merged into skills), hooks, plugins, subagents. | Skills (agentskills.io) in `~/.hermes/skills/`. The agent can create, modify or delete any skill through `skill_manage`. Skills Hub. |
| **Authority** | **No permission system.** It runs with the process's OS permissions. Containment (container or micro-VM) is the boundary. | `tools.exec.mode`, with `auto` recommended. Main-session tools run on the host unless sandboxed, and inbound senders are untrusted until paired. | Permission modes. **Auto mode** (a classifier approves or denies) is the default starting mode. `bypassPermissions`, plus a sandbox for defense in depth. | `approvals.mode: smart`: an auxiliary model auto-approves low risk, auto-denies dangerous commands and prompts only when uncertain. `off` = yolo. Unattended cron defaults to deny for dangerous commands. |
| **Self-improvement** | The agent writes its own skills, prompt templates and extensions. | **Self-learning, default `auto`**. Immediate repair of a skill the turn used, plus a detached experience review after substantial work. Proposals are hash-bound, security-scanned and **captured for rollback**. "Dreaming" consolidates memory in the background. | Auto memory learns from corrections. The user or agent edits CLAUDE.md and skills. | Agent-curated memory with periodic **nudges**. **Autonomous skill creation** after complex tasks. Skills self-improve during use. A **curator** moves unused skills from active to stale to archived. |
| **Background** | None built in. pi-chat bridges chat channels into sandboxed sessions. | Automations (cron), a heartbeat monitor, hooks, webhooks, standing orders. | `/loop` (session-scoped), Desktop scheduled tasks, cloud **Routines**. | Built-in cron delivering to any platform, `/goal`, kanban worker lanes. |
| **Steering mid-run** | `Enter` steers after the current tool batch and `Alt+Enter` queues a follow-up. Esc aborts and returns queued messages. | A **steering queue** checks at every tool launch. Unstarted tools get a synthetic "Skipped to process an incoming message" result and the message enters before the next model call. | Interrupt, then redirect. | A new message interrupts. `/stop`. |
| **Best at** | Minimal resident surface (<1k tokens), self-extension, containment over prompts. | Long budgets, a steering queue at tool boundaries, memory flush before compaction, rollback-captured self-learning. | Verify-as-you-go loop, terse result-first tone, auto memory, auto mode. | A closed learning loop (skills plus memory nudges plus curator), goal continuation, cron. |

Sources:
- [pi docs](https://github.com/earendil-works/pi/tree/main/packages/coding-agent/docs): `how-pi-works.md`, `compaction.md`, `skills.md`, `mcp.md`, `settings.md`, `security.md`, `usage.md`
- [pi-chat](https://github.com/earendil-works/pi-chat)
- [OpenClaw docs](https://github.com/openclaw/openclaw/tree/main/docs): `concepts/agent-loop.md`, `concepts/compaction.md`, `concepts/queue-steering.md`, `concepts/agent-workspace.md`, `concepts/soul.md`, `tools/index.md`, `tools/self-learning.md`, `tools/permission-modes.md`, `automation/index.md`
- [Claude Code docs](https://code.claude.com/docs/en/how-claude-code-works): `memory`, `skills`, `permission-modes`, `scheduled-tasks`
- [Hermes Agent README](https://github.com/NousResearch/hermes-agent) and docs: `user-guide/features/{memory,skills,curator,goals,tools}`, `user-guide/security`

Current pi defaults to the four tools, and it now also ships `grep`/`find`/`ls`
and built-in MCP behind codemode and deferred exposure. This design keeps the
four and puts breadth behind `ta` in bash (§4.7).

**Synthesis.**
- Every reference keeps the **session** as the unit of continuity and compacts
  it, rather than rebuilding context per message.
- All four use **Agent Skills** files the agent can write.
- None puts per-action consent inside the agent's own workspace. Containment or
  a classifier carries safety.
- The three that learn (OpenClaw, Hermes, Claude Code) let the agent edit its
  own instructions, and the stronger two version or rollback-capture what it
  writes.

## 3. Current-state audit: tiny's harness

### 3.1 Code paths (origin/main 2ffb29ae)

- **Persona prompt.** `universe_intelligence._build_persona_system_prompt`
  (`:433-672`) opens "You are a personified intelligence the founder is
  raising, and right now you are getting to know the founder". It then says
  "Speak warmly", "stay genuinely curious and ask about them", and "If
  something is ambiguous … I ask to clarify". About 2.4k characters of "How I
  ask for what I need" grant-shape instructions follow (`:613-650`).
  `_CROSS_SURFACE_CONTINUITY` (`:1202`) says "Greet them warmly", and
  `_turn_input_method_context` (`:1218`) and `_UNRECORDED_LESSON` (`:1186`) add
  more. Measured median system prompt: 7,963 characters.
- **Turn loop.** `converse` (`:1241`) is one stateless turn. Memory is
  `conversation_memory.format_history`: `DEFAULT_LIMIT = 20` messages and
  `DEFAULT_CHAR_CAP = 7000` (`conversation_memory.py:34-41`), text only, so the
  turn never sees its own earlier tool calls or results. That block ends with
  "a costly action still needs consent recorded THIS turn" (`:158`). Codex
  launches with `--ephemeral` (`providers/codex_provider.py:871`), so there is
  no native session to resume either. For converse, Codex gets an empty scratch
  `/workspace` "so it answers as a chat model rather than acting as a code
  agent" (`:876-880`), which is the opposite of the directive. After the reply,
  a separate learning-extraction model call still runs whenever the turn did not
  call `write_brain` (`:1484-1494`).
- **Served tools.** `served_tools.SERVED_ENGINE_MCP_TOOLS` lists 14 handles:
  `read_graph`, `get_status`, `run_graph`, `write_graph`, `browse_commons`,
  `read_commons_shape`, `read_brain`, `write_brain`, `connect_compute`,
  `source_channel`, `read`, `write`, `edit`, `bash`. Their descriptions are
  resident on every round, ratcheted at 30,000 characters (about 28.5k actual,
  roughly 7k tokens) in `tests/test_converse_turn_cost.py:79`. pi's whole
  prompt plus tools fits under 1k tokens.
- **Jail.** `universe_tools.py` runs `read`/`write`/`edit`/`bash` inside
  bubblewrap. It has **no network** (`:1069-1082`: "a shell in /u with no
  network"), a read-only root, and writes limited to ten brain files and six
  harness dirs (`AGENT_BRAIN_FILES`, `AGENT_HARNESS_DIRS`, `:129-135`). The
  `.runtime/` move for platform state is still pending.
- **Skills.** `skill_index` and `harness_prompt` exist (`:1036-1108`). The
  founder's `skills/` directory is **empty**.
- **Agents it builds.** Agent nodes in the universe's own graphs
  (`shared_self.py`) run the same converse harness through
  `background_served_provider.py:815-852`, so the agents it builds inherit
  every harness limit above. They are measured as the universe's own creations,
  not as the harness.

### 3.2 Production numbers (agent_turns, 2026-09-15 → 2026-10-01: tiny's chat turns)

The harness is the universe's own agent talking with its owner, so it is
measured on chat turns only. Turns were classified by the actual message after
the memory block. Of 1,644 tiny turns, 251 are chat with the founder. The other
1,393 are wakes of a background agent tiny built for itself, which is a
user-built graph and not part of the harness. They are excluded here (founder,
2026-10-01: "dont conflate the two things").

| Measure (251 chat turns) | Value |
|---|---|
| Median input tokens per turn | 61,862 (p90 510,368) |
| Total input tokens | 53.7M |
| Median reply length | 637 chars |
| Replies containing an ask | 26% |
| Replies with blocked / can't / unavailable language | 54% |
| Replies with "I haven't verified / unverified / can't confirm" disclaimers | 29% (66 of 225) |
| Replies claiming any effect (merged, posted, PR, sent …) | 33 (15%) |
| … of which not negated in the same reply | **7** |
| Input tokens per effect-claiming reply | 1.6M |
| Turns ending indeterminate (`held_native_unknown`) or abandoned | 26 (10.4%) |

**Corrections.** 46 of 262 founder messages (18%) are corrections ("your still
not understanding the general shape", "it could have easily by now made the
village fully functional"), and 8 of the last 40 are. 13 universe replies open
with "You're right" or "Sorry".

**Asks.** 85 app requests were raised between 2026-08-28 and 2026-10-01. The
founder dismissed 15 and muted 8 more request kinds.

**What tiny itself says blocks it** (`notes/continuation.md`, 2026-10-01 05:43Z):
"Local FastMCP/pytest/Ruff/browser capabilities remain absent", and a
`read_graph` result "truncates before its result can be compacted". In chat it
said that a background turn "has a 15-minute maximum". That was its own
900 s interval trigger, which it believed was a limit.

**Workspace.** The universe root has 422 entries, 362 of them platform
`.worker_supervisor.*.json` files. tiny's real work lives in `notes/` (about
880 KB of scripts, JSON probes and plans), including a 4.4 KB
`prompts/background-operating.md` and an 18 KB `notes/goals-and-outcomes.md`
that it re-reads every turn, because no session carries what it already read.

**Tool observability.** `agent_turn_tools` holds 109 rows, all from the
HTTP-loop universe and **none for tiny's native turns**. Nobody, including
the founder and tiny, can see after the fact which tools a turn called.

### 3.3 Top drag findings (chat turns)

1. **No session.** Every chat message rebuilds context from scratch and forgets
   the agent's own earlier tool calls and results. The median turn is 61,862
   input tokens, and 7 of 225 replies report an un-negated effect.
2. **Guidance teaches asking and hedging.** 26% of chat replies ask, and 29%
   carry verification disclaimers. The persona prompt instructs warmth,
   curiosity, asking to clarify, and per-turn consent.
3. **Tools are walled where the work is.** There is no network in bash, no
   browser, and no toolchain, and `read_graph` results truncate. Those are the
   blockers tiny lists itself, and they became 85 requests.
4. **Heavy per-round context.** About 8k characters of persona, 7k of history
   text and about 28.5k of tool descriptions are re-sent each round.
5. **Invisible, unreliable turns.** Native turns journal no tool calls, so
   neither the founder nor tiny can see what a turn did, and 10.4% of chat turns
   end indeterminate or abandoned.

## 4. Target design: the dot experience, element by element

### 4.1 Principle

A new user's universe **is** a dot. It is one always-on agent with its own
computer and a responsibility. It does its own work, watches for things to do
while idle, and acts under rules the user sets. It runs on the user's own
compute.

- **Platform supplies:** a persisted session, a workspace the agent owns, rule
  enforcement and review, activity tracking, and a profile page.
- **User owns:** everything else, including the instructions, memory, skills,
  rules, model and the number of agents.
- **One locked floor: cross-user isolation**
  (`the-floor-is-cross-user-only`). An agent can never touch another user's
  universe, data or money.
- **Everything else is a default the user can edit.** That includes the dots
  protections: Custom Rules, the hand-backs and auto-review.
- **Defaults reproduce dots exactly.** A new user gets the dots experience
  without configuring anything.

| Element | Section |
|---|---|
| Name plus one responsibility at onboarding | 4.2 |
| Its own computer: workspace, network, browser with take-over | 4.3 |
| Always on, several projects at once (Activity) | 4.4 |
| Read-only proactive research while idle, proposals only | 4.5 |
| Blocked work becomes something to grant | 4.6 |
| Exactly pi's four tools | 4.7 |
| Custom Rules and the execution context | 4.8 |
| Auto-review | 4.9 |
| Hand-backs (default rules) | 4.10 |
| Profile page, Scheduled view, Pause, push | 4.11 |
| Reachable from the app, `converse` and chat apps | 4.12 |
| Memory editable item by item | 4.13 |
| Harness layer and roster, any configuration | 4.14 |
| Shareable harness bundles and command-center layouts | 4.15 |
| Platform state is out of reach of every agent environment | 4.16 |

### 4.2 Onboarding: a name and one responsibility

On a new universe's first conversation, from any surface, the agent asks for
two things: a name, and a responsibility. The responsibility has the five parts
dots asks for:
- what it owns;
- where it learns from (connections and sources);
- the quality bar;
- what needs approval;
- how often it reports.

The questions come from the seed template (§4.14). They are not code paths. The
answers are recorded as follows:
- the name goes to `identity.md`, as the personification spec already requires;
- the responsibility goes to a `## Responsibility` section of `AGENTS.md`;
- each "needs approval" item is proposed to the owner as an *Ask before taking
  action* rule (§4.8). The agent cannot write rules itself.
- the report cadence becomes a scheduled activity (§4.4).

If no compute is connected, onboarding says so plainly and links to connecting
compute. Nothing pretends to run (Hard Rule 8).

### 4.3 Its own computer

**Workspace.** The agent has **its own workspace that it fully owns**,
including its own wiki and brain files.

- Live evidence, 2026-10-01: the founder's agent wrote a 6 KB page, then said
  "my wiki is read-only through the available tools" and had to save it under
  `notes/`.
- How the workspace is made is left open. S3c's move of platform state
  (#4175) is paused after a REJECT review. The inverse shape, where the agent's
  own workspace is its jail root and platform state stays where it is, is being
  costed. This design depends only on the property, not on either mechanism.

**Shell network.** `bash` reaches the public internet through the checking
proxy (S3a, #4174).

**Browser.** A Chromium dedicated to the agent, in its own sandbox outside every
agent-controlled environment, driven through a **restricted broker**: the
`browse` command over a per-universe socket.
- **What the broker offers:** navigation, reading page text, an accessibility
  snapshot, a screenshot, click, type, select, scroll, and download into the
  workspace.
- **What it never offers:** raw CDP, arbitrary script evaluation, cookie or
  storage export, or network inspection.
- **The profile** (cookies, saved logins) lives in platform-owned storage that
  no agent environment mounts (§4.16).
- **Filtering.** Login forms are protected fields that the agent cannot read
  back. URLs returned to the agent have credential-bearing query parameters and
  fragments stripped. Page text, screenshots, downloads, errors and logs pass a
  credential filter.
- **The honest promise.** The agent never holds stored credentials, saved
  passwords or session cookies. A page that *displays* a secret (an API-key
  settings page, for example) is filtered on a best-effort basis, so the owner
  should not point the agent at such pages. That is the boundary, and the
  profile says so.
- **One context per activity.** Each activity gets its own browser context
  sharing the logged-in profile. An approval for a browser step binds to the
  target it was asked for (origin, path and form fingerprint) and is void if
  that target has changed by execution time.

**Live view and take-over.** The profile's Computer tab streams the active
browser context through the owner door, together with the running `bash` tail.
- *Take over* hands input to the owner, and the agent's `browse` calls wait with
  `owner has control`.
- While the owner has control, **recording stops**: nothing typed or shown
  enters the session log, screenshots or the model.
- *Return control* gives the session one platform-computed line, `owner returned
  control at <sanitized url>`.

### 4.4 Always on, several projects at once: Activity

**Identity.**
- The main agent keeps the S1 key `thread:principal:<owner>` unchanged.
- Each further roster agent has the main key `agent:<agent_id>:thread`.
- An activity is a child session keyed `activity:<id>`, recording its parent
  agent and its parent activity if it has one.

**An activity record holds:**
- title;
- origin: an owner ask, an approved proposal, or a schedule;
- status: `in_progress`, `waiting_on_you`, `scheduled`, `paused`, `completed` or
  `failed`;
- a result summary;
- receipts: effects, approvals used, and the tool journal;
- the execution context of §4.8.

**Running.**
- The agent starts activities with `ta activity start`, and several run at once.
- Each holds one agent seat (#4154) while running. When seats are full a new
  activity waits visibly and is never refused.
- An activity in `waiting_on_you` **releases its seat**, so a pending approval
  cannot starve the account.
- Status reaches the agent's main session as the S2 event line.
- Activities need no connected client and run until done.

**Surviving a deploy.** The session log alone is not enough to recover.
- The activity record holds durable runnable state: status, lease, the last
  completed tool call, and a pending-effect record with an idempotency key for
  every external effect before it is attempted.
- On restart, an activity resumes from its last completed tool call.
- An effect whose outcome is unknown is reconciled through its receipt or the
  effector's idempotency key before anything retries it.
- If it still cannot be determined, the activity goes to `waiting_on_you` with
  "this may already have happened: <effect>". It never retries blindly.

**Scheduled activities.** Automations target branches today (`branch_def_id`
in `automations.py:217`, and the lease identifies a branch). D2 adds an explicit
`activity` target kind (agent id plus activity template) to the automation
contract. It keeps everything `user-owned-automations` requires: the
authenticated owner, the current serving assignment, the foreground budget and
the firing fence. The timer is platform machinery. The execution belongs to the
user's universe and runs on the user's compute.

**Reads never truncate.** The Scheduled view, and the agent's own reads of its
activities and schedules, are complete. They are paged by cursor, never cut at a
size cap.
- Live evidence: "the automation catalog truncates before I can recover the
  existing schedule ID".
- Lesson: `data-size-must-not-change-what-a-user-sees`.

**Storage.** Activity records are a new storage shape. D2 opens its own storage
proposal before code, and records are placed per §4.16.

### 4.5 Proactive research while idle (read-only, enforced in code)

As in dots, the dot itself looks for ways to help while the owner is not working
with it. This is harness behaviour. It is not a graph the user must build, and
it does not reuse the shape of any user-built wake loop.

**When it runs.** A research turn starts only when all of these hold:
- the agent is not paused;
- it has no `in_progress` activity;
- the owner has not messaged for the idle period;
- the time is inside active hours;
- no other research turn for the agent is running (single flight);
- the cadence window has elapsed.

An event from a connected read source may bring the next run forward, but it
obeys the same conditions. There is never more than one research turn per
cadence window, and events arriving while a turn is pending coalesce.

**Defaults** (decided 2026-10-01): idle 30 min, cadence 4 h, active hours
08:00–22:00 in the owner's clock, on for a new user. The research schedule
appears in the profile's **Scheduled** view, where the owner can edit it or
switch it off. With no compute connected, that row says so plainly instead of
silently not running.

**Read-only by capability, not by mount alone.** The daemon handlers behind
`ta` can write even when the jail is read-only, so the boundary is a capability
that every layer checks:
- A research turn carries an unforgeable `research` flag in its execution
  context (§4.8).
- **Positive allowlist.** Only audited, side-effect-free read operations answer
  a context with that flag. Everything else refuses, including automation
  creation and execution, request actions, and connected-app operations not
  declared as reads.
- **Known violator.** `read_graph target=pending_requests` reaches
  `list_requests` → `rail_entry`, which can retire offers and create a consent
  request (`api/graph_reads.py:237`, `api/pending_requests.py:2024`,
  `patch_intake.py:334`). It stays off the allowlist until it is side-effect
  free.
- **Inside the jail:** `write` and `edit` are refused. `bash` runs with the
  workspace read-only and no egress socket bound. `browse` is absent.
- The flag propagates into nested handlers, MCP calls and effectors.
  Extensions and hooks do not run during research.
- Research turns do not run the pre-compaction flush. They are short-lived and
  start from the agent's files.
- **Permitted bookkeeping.** The platform still writes its own bookkeeping:
  the session journal, output spills to the session's output area, and usage
  accounting. These are platform writes, not agent effects.

**Output is proposals only**, through one narrow platform path. It is not
general request creation. The turn returns structured proposals, each naming
one planned action, why, and the evidence.
- The platform validates them, bounds their size, and stores them as
  `proposal` requests.
- Approving one starts an activity in which that exact action counts as
  pre-approved. It still passes auto-review.
- A turn that finds nothing produces nothing.

### 4.6 Blocked work becomes something to grant

Live evidence: the founder's agent marked every item on its page "blocked on
environment". A dot does not leave a static blocked list. When any turn, or
research, finds work it cannot do, it turns each block into one of two things:

- **A grant proposal.** The proposal names the exact rule change, connection or
  capability that would unblock the work, for example "allow `app.write:github`
  for opening PRs on repo X" or "connect Vercel read access". Approval applies
  it and resumes the work.
- **A patch request**, when the gap is in the platform itself, following
  `capability-gaps-close-mid-turn-via-patch-requests`.

A block that is neither (it is waiting on a person or on time) becomes a
scheduled recheck activity, with its condition stated.

### 4.7 Tools: exactly pi's four

- **Resident tools.** Every agent gets `read`, `write`, `edit` and `bash` over
  its workspace, and no other resident tool. The tool definitions sent each
  round are those four schemas plus one base-prompt line naming `ta`.
- **Native built-ins off.** Adapters that bring native tools run with them
  disabled. Today each adapter restricts its native tools in its own way
  (`claude_provider.py`, `codex_provider.py`), and D6 makes that uniform.
- **Breadth goes behind one searchable command in bash.** This is pi 0.99's
  codemode idea, with bash as the interpreter:
  - `ta search <words>` lists matching capabilities across platform verbs
    (graphs, runs, automations, activities, requests, commons), the user's
    connected-app operations, user extensions, and attached MCP servers.
  - `ta describe <name>` prints one capability's arguments.
  - `ta <name> --json '<args>'` calls it, and a script can chain many calls in
    one `bash` command.
  - `browse` (§4.3) is a command too, not a tool.
- **Same handlers as the connector.** `ta` uses the same handlers as the public
  connector. The deferred-MCP route the first version planned for the agent is
  dropped. The public connector's six verbs for chatbots are unchanged.

### 4.8 Custom Rules and the execution context (authority)

**Shape.** A rule has two parts:
- **an action matcher:** a plain-language description plus a structured match
  on an *action class* and optional fields;
- **a behaviour:** `do` (take action without asking), `do_if_preapproved`,
  `ask_first`, or `hand_off`.

The most specific matching rule wins. When two rules are equally specific, the
stricter wins.

**Execution context.** Rules mean nothing if a workflow the agent created
later runs as "the owner". Today the effector authenticates as the connection
grant's stored owner and carries no originating-agent context
(`effectors/authenticated_external_call.py:829`). So every execution carries an
unforgeable **execution context**, minted by the platform and never accepted
from the agent. It holds:
- universe;
- initiating agent;
- delegated authority;
- research flag;
- approval id, if any.

The context propagates through `ta`, workflows and automations the agent
creates, activities it starts, extensions, MCP calls and effectors. Every
enforcement point evaluates rules **for the initiating agent in the context**.

**Delegation never increases authority.**
- A workflow, automation or activity an agent creates runs with at most that
  agent's authority. When one agent starts an activity on another, the activity
  runs with the **lesser** of the two.
- Widening either requires an owner grant.
- Editing another agent's harness files is its own class,
  `harness.edit:<agent>`, seeded `ask_first`. Editing its own files is `do`.
- Editing universe-level shared skills and extensions counts as editing every
  agent that loads them. It is `do` when the universe has one agent, and
  `ask_first` when other agents with different rules load them.

**Action classes, and how each is known:**

| Class | Enforcement point | How the class is established |
|---|---|---|
| `workspace.files`, `workspace.shell`, `workspace.workflows` | tool layer, `ta` | by construction |
| `harness.edit:<agent>` | tool layer, `ta` | the path or target agent |
| `shell.egress` | egress proxy | one coarse class. Explicitly **not** assumed anonymous, because an uploaded token or bearer URL can ride on it |
| `app.read:<conn>[:<op>]`, `app.write:<conn>[:<op>]` | effector | the operation kinds the connection definition declares (trusted classification). On the generic HTTP effector, an undeclared operation is `app.write` (consequential), whatever its method |
| `people.message:<channel>` | channel or effector | declared by the channel or connection |
| `commons.publish`, `share` | commons and visibility handlers | by construction |
| `spend` | effector | declared cost |
| `money.move`, `security.change`, `access.grant` | effector | declared by trusted integrations (payment, identity and admin connections) |
| `browser.action` | `browse` broker | an interaction class only. Clicks, submits and typing are not inferred to be payments. The owner may set `hand_off` for named sites |

The platform makes **no claim of universal payment or message detection** from
raw HTTP or browser gestures. Semantic classes come from declared integrations.
Unknown consequential operations fall to the agent's `app.write` or
`browser.action` rule.

**Who edits rules.** Rules live in platform-owned storage outside every agent
environment (§4.16).
- The owner edits them in the profile's Rules tab, or through the owner door.
- The agent reads them through a read-only view, and may propose a change as a
  request.
- Reason: an agent must never loosen its own authority.

**Seed rules** reproduce dots, and the first version's "act inside, ask
outside", as visible, editable rules:

| Class | Seed behaviour |
|---|---|
| `workspace.*`, own `harness.edit`, `shell.egress`, `app.read:*` | `do` |
| `app.write` to a destination with an existing standing grant | `do` (existing grants are honoured) |
| `app.write` to a new destination or an undeclared operation, `people.message`, `commons.publish`, `share`, `spend` over budget, `browser.action` on submit or purchase-looking controls, other agents' `harness.edit` | `ask_first` |
| `money.move`, `security.change`, `access.grant` (the hand-backs, §4.10) | `hand_off` |
| Each "needs approval" item from onboarding | `ask_first` |

**One decision point.** Every enforcement point calls
`rules.decide(action, context)` before executing:
- `do`: proceed. A consequential action goes through auto-review first (§4.9).
- `do_if_preapproved`: proceed only when an authenticated owner message in the
  session, or an approved proposal, names exactly this action. Otherwise the
  call is treated as `ask_first`.
- `ask_first`: raise one app request. The activity goes to `waiting_on_you`,
  and the agent continues other work. An approval may tick "always allow this",
  which writes a `do` rule.
- `hand_off`: raise a request for the owner to perform the action, offering
  take-over for a browser step. The agent never executes it.

**Existing grants are kept.** The standing destination grants
(`effector_consents`, the `_check_consent` path) and their call-time checks are
unchanged. They become the data the `app.write` rules consult.

### 4.9 Auto-review

- **What triggers it.** It is on by default for every **consequential** action
  whose rule is `do` or `do_if_preapproved`. Consequential means every class
  except `workspace.*`, own `harness.edit` and `app.read`.
- **Off switch.** The owner may switch auto-review off **per class** in the
  Rules tab (decided 2026-10-01). The tab says what that means: actions of that
  kind then proceed on the rule alone.
- **Invocation.** A separate, **tool-free** call on the universe's own model.
  - It is admitted like any agent call, through the same seat-aware executor:
    it re-enters the activity's seat when the activity holds one (an
    automation), and otherwise queues for the account's own. The node's seat is
    already released when its effects fire, so the review never waits behind
    the work it reviews. It has its own deadline.
  - A consequential action with no run model bound to review it is held at the
    send boundary, never sent unchecked.
  - Its answer must be exactly one JSON object with exactly the two fields; an
    object echoed inside prose is no answer.
  - It is not itself reviewed.
  - It gets one bounded retry. After that the action becomes a request naming
    the cause, and the activity releases its seat while waiting.
- **Inputs, with provenance.**
  - Trusted: the planned action in structured form, the matching rules, the
    built-in safety requirements, and authenticated owner messages.
  - Untrusted evidence, in a marked envelope: action text, page content, and
    agent-editable harness files (including `## Responsibility`).
- **Output.** `proceed`, or `needs_approval: <reason>`. The result binds to a
  hash of the exact action and the rule-set version, and is void if either
  changes.
- **Review can only tighten.** It can turn `do` into `ask_first`. It can never
  create a grant, loosen a rule, or override `hand_off`.
- **Fails closed and loud.** No compute, an error or a timeout makes the action
  a request naming the cause.
- **Never inside the workspace.** Workspace actions are undoable through file
  history, and reviewing them would recreate the drag measured in §3.

### 4.10 Hand-backs (default rules)

Decided 2026-10-01. These ship **on by default**, exactly as in dots:
- `security.change`: password, credential or security settings on an external
  account;
- `money.move`: moving money or making a payment;
- `access.grant`: granting another person access to the owner's accounts or
  data.

They are seed `hand_off` rules in the user's harness, **not** fixed platform
policy, because the only platform invariant is cross-user isolation.
- The owner may change them. The Rules tab says plainly what turning one off
  means, for example "your agent will be able to move money from connected
  accounts without handing it to you".
- An imported bundle cannot loosen them on activation (§4.15).

Two more dots confirmations map like this:
- **Permanent deletion** in a connected app is `app.write` with a declared
  `delete` operation, seeded `ask_first`.
- **Installing software inside the agent's own workspace** is not a hand-back.
  The workspace is the containment.

What stays locked is the cross-user floor: no rule lets an agent reach another
user's universe, data or money.

### 4.11 The profile page, the Scheduled view and the command center

**One agent's profile:**
- **Header:** name, responsibility, status (working / idle / paused / waiting
  on you), model, usage this period, including research's share.
- **Activity tab:** *Waiting on you* first, then *In progress*, *Scheduled* and
  *Completed*. Completed items carry receipts. Each item can be steered, paused
  or stopped, and a stopped activity keeps its partial result.
  - *Scheduled* lists every scheduled activity and the research schedule, each
    with its next run, editable. It is complete and paged, never truncated.
- **Computer tab:** live browser view, Take over / Return control, and the
  current bash tail.
- **Memory tab:** §4.13.
- **Rules tab:** the four-behaviour editor, the auto-review switch per class,
  and proposed rule changes.
- **Harness tab:** the agent's files, the history, and Undo.

**Pause** (••• menu) stops the agent's new turns, research and scheduled
activities. The running turn stops at its next tool boundary (#4152). Resume
continues.

**Push** is sent on *completed*, *waiting on you* and *new proposal*, through
the existing notifications (#4140, #4138), with a toggle per kind.

**Roster view (the command center).** It has one row per agent, showing:
- status;
- the waiting-on-you count;
- the current activity and the next scheduled run;
- usage.

From it the owner can approve or reply inline, pause, and open a profile.

**Both pages are built on the custom-UI layer** (`app-ui-library`, #4160 and
#4165), so users redesign them and share the design (§4.15).

### 4.11a One concept: the command center (founder, 2026-10-01)

> "your harness and ui are all your command center set up, universe becomes
> command center, switch ui becomes switch command center and the opening
> greating a new user gets becomes welcome commander"

**One concept.** The user's harness, the roster, profile and Activity pages
above, and the custom-UI work are **one concept: the user's command-center
setup**. It is shareable as a single design (§4.15). The custom-UI work
includes the GTM Village, custom UIs, and "change the harness and UI just by
talking" (#4160, #4165, #4168).

**User-facing copy:**
- the app's "Switch UI" control becomes **"Switch command center"**;
- a new user's opening greeting opens with **"Welcome, commander"**;
- where the app presents the universe's own space to its owner, it is called
  the **command center**.

**What does not change.** The rename is user-facing copy and concept only.
These stay as they are, because renaming them is a public-surface change with
no user benefit:
- internal identifiers and storage;
- the MCP handles (`read_graph` … `converse`) and the connector's tool
  descriptions;
- PLAN.md's term "universe".

Where the line is unclear, the C0 slice flags it in its PR.

### 4.12 Reachable everywhere

- **App and connector.** The app (web, desktop, phone) and `converse` already
  reach the main agent's one session (S1).
- **Chat apps** (Slack, Telegram, Teams and others) connect through **channel
  extensions** that relay into the same session.
  - Channels stay user-built (`channels-must-be-user-built-not-hardcoded-
    effectors`).
  - The published starter template ships ready-made channel extensions, so a
    new user gets dots' "reach it in Slack" in one connect step.
- **One session.** A message from any of these steers the same session (S2).

### 4.13 Memory, item by item

- **One item per bullet.** `MEMORY.md` holds one item per bullet, each with a
  short stable id (`- [m_7f3a] Prefers invoices on the 1st`). The agent adds
  and edits items as it learns from feedback.
- **Memory tab.** The owner can view, edit or delete any single item.
- **History and Undo.** Every change is recorded in the file history, with
  Undo.
- **Deleting an item never requires deleting the agent.** That is the dots
  limitation we drop.

### 4.14 The harness layer: fully user-configurable, any roster

**Per-agent harness files.** The main agent's files stay where S1 put them, at
the workspace root. Each further agent has the same layout under
`agents/<id>/`:
- `AGENTS.md`: instructions and `## Responsibility`;
- `identity.md`;
- `MEMORY.md`;
- `skills/` and `extensions/`: a per-agent one overrides a shared one of the
  same name;
- `settings.yaml`: model, research cadence, idle period, active hours,
  compaction thresholds, channels and seat priority.

Rules are owner-edited and stored per §4.16.

**Any configuration.** The user can configure:
- one dot or many;
- specialists with narrow rules;
- a lead that hands activities to others (`ta activity start --agent <id>`),
  under the delegation rule of §4.8.

Graph agent nodes stay workflow primitives, and a graph can start an activity on
a roster agent, again with at most its creator's authority.

**The seed template.** A new universe is seeded from an explicitly published,
reviewed starter template, never the founder's live private files. The
template holds the base `AGENTS.md`, the onboarding questions, the seed rules,
starter skills and channel extensions.

### 4.15 Sharing harness bundles and command-center layouts

- **Format.** A **versioned bundle manifest**: one agent's or a roster's
  harness (instructions, skills, extensions, settings, a rules *suggestion*),
  optionally with layouts. Paths are relative, normalised, and refused if they
  escape the bundle.
- **Carrier.** The existing `universe-custom-agents` public definition. Its
  components accept arbitrary JSON objects with a `kind` (`custom_agents.py:310`),
  so skills, extensions and layouts fit without a new public table.
  - A bundle over the 256 KiB envelope is refused, with its size named, until a
    larger carrier is designed.
  - D9 reconciles the spec's 64-component limit with the implementation, which
    removed it.
- **Export is explicit, and private by default.**
  - Never included: memory (unless opted in item by item), session logs,
    credentials and browser state.
  - Contact details and connection identifiers are scrubbed.
  - The owner confirms a preview before publishing.
- **Import is quarantined outside the agent's reach.** Imported bundles sit in
  platform-owned storage (§4.16), not in the agent's writable workspace, so
  the agent cannot copy quarantined code into an active location.
  - The agent may read a preview as untrusted content.
  - An owner-controlled **activation record**, also outside agent-writable
    state, is the only gate.
  - Only activated content enters extension loading, the skill index or
    trusted prompt assembly. Indexes never include quarantined content.
  - Imported rule suggestions activate only as written or stricter. Loosening
    one, including a hand-back, needs an explicit owner edit.

### 4.16 Platform state is out of reach of every agent environment

S1 already learned this: authoritative session records live in the data root's
`.agent-sessions/<universe>/`, outside every universe folder, because workflow
provider jails bind the universe read-write and their hidden-directory masking
exempts `.runtime/` (`agent_sessions.py:19-29`, `provider_jail.py:224`, `:259`).

The new platform-owned records follow the same rule. They live outside **every**
agent-controlled execution environment: the four-tool jail, workflow provider
jails, extension processes and the browser sandbox. The records are:
- rules;
- auto-review results;
- activity records and pending effects;
- proposals;
- import quarantine and activation records;
- the browser profile.

Agents see them only through read-only owner-door projections. Registering a
path with a resolver is not enough.

### 4.17 Export: a runnable, publish-ready folder (founder, 2026-10-01)

> "the exportability module likely needs refactoring to present but the
> principle that users exportability is supported remains. that becomes more
> relevant now that users can build and share entire command centers, they
> should also be able to export them easily to a folder on their computer so if
> they wanted to plug a local model into it they would be good to go able to run
> their command center locally without our platform at all"
>
> "also export relates to if someone wanted to publish their command center to
> github or another platform"

**One action, one folder.** The owner exports a command center in one action,
from the profile ••• menu or by asking the agent, which calls the same door.
The result is a folder, also offered as a zip download. It contains:

| Path | What |
|---|---|
| `command-center.json` | The bundle manifest (§4.15): format version, agents, the roster, and per-agent model and schedule config as references |
| `agents/<agent>/` | Each agent's harness: `AGENTS.md`, persona, `skills/`, `extensions/`, prompts, and rules as an editable `rules.json` (the D1 table, the hand-backs included) |
| `workspace/` | The agent's own workspace: the files it built (§4.3, W) |
| `wiki/` and `brain/` | The wiki as an OKF bundle, and the brain files |
| `workflows/` and `automations.json` | Branch definitions, and schedules as data (the Activities branch included) |
| `memory/` | Memory items, only the ones the owner selected (§4.13) |
| `ui/` | The command-center and profile layouts (custom-UI layer) |
| `run/` | The local runner (below) |
| `README.md`, `LICENSE`, `.gitignore`, `.env.example` | What makes it publish-ready |

**One manifest, two profiles.** Export, share (§4.15, D9) and import use the
same manifest and path rules. The manifest declares a profile:
- **`share`** is the harness subset, published through the public
  agent-definition carrier. The 256 KiB limit applies to this profile only.
  Contact details are always scrubbed.
- **`export`** is the whole folder, kept as private local output with no
  carrier.
- **Import** accepts either profile: a shared bundle, an exported folder or
  zip, or a cloned repository with that layout. It lands in quarantine (§4.15)
  through the ingestion boundary below.

**No secrets, and arbitrary content is opt-in.** A scrub cannot sanitize
arbitrary files, so the export does not claim to.
- **Structured fields are serialized schema-aware.** Connections become named
  references with `.env.example` lines (`GITHUB_TOKEN=`, …). Workflow effect
  configs, automation inputs and layouts are written field by field, and
  credential-shaped and identifier fields are replaced with references.
- **Arbitrary content starts excluded:** workspace files, wiki and brain prose,
  binaries. The owner includes it after a content preview. Every file is
  scanned with the same credential parser the platform redacts with. A file
  with a detected credential stays excluded, and owner approval cannot override
  a detected credential. An uninspectable file (binary, archive) is excluded
  until the owner includes it by name.
- **Every output is covered.** These exclusions apply to all output, including
  the generated README and manifest.
- **Limits are stated honestly.** The preview says plainly that detection
  cannot prove a file is free of personal data.
- **Never exported:** session logs and browser state.
- **Contact details** are scrubbed by default. In the private `export` profile
  only, the owner may include them item by item.

**Publish-ready as a repository.**
- `README.md` is generated from the responsibility and the roster. It covers:
  - what the command center does;
  - how to run it locally with a local model, as three commands;
  - which keys it needs;
  - what works without the platform and what does not.
- `LICENSE` is a placeholder the owner picks; the export names the choice and
  does not decide it.
- `.gitignore` excludes `.env`, keys, runtime state and the runner's local
  session files.
- The layout above is stable and versioned (`command-center.json`
  `format_version`), so anyone who clones it can run it.
- **Publish-ready does not mean safe to publish.** The export is private local
  output. Publishing is a separate act with its own owner approval: it is a
  consequential action under the owner's rules, and it re-runs the preview
  without any personal-data inclusions.

**Publishing stays user-built.** There is no platform GitHub effector. The agent
publishes with its own tools: `bash` and `git` over the egress proxy, on the
owner's own GitHub or other connection, under the owner's rules (`app.write`
and `commons.publish`). Alternatively, the owner adds a shareable publish skill
or workflow. The platform supplies only the format and the folder.

**Runnable standalone: the local runner.** `run/` holds a small,
dependency-free runner shaped like pi. It runs with the user's own host
privileges, with no platform jail, so its defaults are conservative:
- `bash` asks for confirmation before each command by default. Unattended
  operation is an explicit opt-in, and a missing approval blocks rather than
  proceeds.
  This is a default, not a lock (stamp note, 2026-10-01): the harness is the
  owner's, and pi itself runs without asking, so the owner may loosen it for
  their own command center. It stays enforced for an imported or shared
  command center until its owner activates and configures it.
- File tools are confined to the folder. A path that resolves outside it is
  refused.
- The runner's policy (`rules.json`, the runner config) is read-only to the
  agent's tools, so the agent cannot loosen its own rules locally.
- Schedules run only after a separate `run/schedule --enable`.
- The README discloses that the runner has host privileges, and states which
  platform guarantees (jail, egress checks, auto-review) are absent locally.
- A cloned command center's `run/` code is third-party code. Review it before
  running; its own prompts cannot establish that it is trustworthy.

The runner's features:
- **Tools:** `read`, `write`, `edit` and `bash` over the folder.
- **Context:** the same `AGENTS.md` and `skills/<name>/SKILL.md` format.
- **Rules:** `rules.json` is honoured for ask-first and hand-off as terminal
  prompts.
- **Model:** any OpenAI-compatible endpoint, set in `.env` (`MODEL_BASE_URL`,
  `MODEL_NAME`), for example Ollama at `http://localhost:11434/v1`.
- **Platform:** no account and no network to TinyAssets.
- **Schedules:** `automations.json` runs from a `run/schedule` loop while the
  machine is on.

| Works fully without the platform | Degrades or is absent |
|---|---|
| Chat with each agent, its four tools, skills, `AGENTS.md`, rules prompts, workspace, wiki and brain files, workflows the runner can execute, schedules while the machine is on | Delivery between users and the commons; hosted channels (Slack and Telegram webhooks need a public endpoint); phone push; the hosted browser broker; always-on while the machine sleeps; auto-review unless a model is configured for it; quota and usage accounting |

**The import ingestion boundary.** Quarantine blocks activation, but not the
damage extraction can do, so import runs a boundary first:
- **Bounds:** compressed and expanded bytes, file count, depth and processing
  time.
- **Rejected:** absolute and traversal paths, links and reparse points, special
  files, and case or Unicode path collisions.
- **Copy:** only validated regular files, into a fresh quarantine directory.
  `.git` is excluded.
- **Never executed during import:** hooks, filters, installers, or the
  supplied `run/` code.
- **No unlimited import.** An export with no size limit does not imply import
  without resource limits.

**`okf_export` is refactored, not kept beside it.** `tinyassets/wiki/okf_export.py`
becomes the `wiki/` writer inside the export. It is the only OKF writer, so
there are not two definitions of one fact.
- Its curated source set and privacy exclusions (`soul.md`, `drafts/`, `raw/`,
  `daemon-wiki`) are preserved.
- Wiki content outside `pages/` is exported only if the owner includes it in
  the content preview.
- Brain and workspace copying cannot route around those exclusions.
- A delta to `knowledge-retrieval-and-memory` permits orchestration through the
  export door. That door is the owner door, not a new MCP action. `docs/concerns/2026-10-01-okf-export-is-unwired.md`
closes when the export ships.

**Order.** D11 comes right after D9 (it reuses D9's manifest, path rules, scrub
and quarantine) and before D10. It could not usefully come earlier, because
until D9 there is no manifest to share. The local runner is independent of D9
and can be built in parallel with it.

### 4.18 Many agents, one universe: the multi-agent invariant (founder, 2026-10-01)

> "depending on command center build the user might talk to more agents than
> just the main dot one we are designing. users can design any kind of agent and
> configure it in any command center orchestrations"
>
> "by default the dot like agent that comes with your universe is aware of all
> the activity happening in that universe … and in general the universe brain is
> usually shared so all saved feedback from the user gets all reconciled in the
> same brain memory files no matter which agent you talk to"

**The invariant.** Every per-agent record is keyed by `agent_id` from day one.
`main` is only the seeded default, never a special case in code. The roster UI
is D8. Nothing before D8 may assume there is one agent.

**D8 integration design (2026-10-03, not implemented):**
[addressed-agent-control-provenance](../addressed-agent-control-provenance/design.md)
specifies the missing authenticated turn/run carrier and control-door wiring
after #4287 and #4228. Their accepted foundation residual remains open: keyed
storage alone does not make custom-agent rules, Stop or request routing work.
That design owns current-binding/revocation checks and safe legacy handling;
the shared-brain and visibility requirements below remain here. The first Claude
ADAPT is folded there, with [design approval at 6bf7923](https://github.com/TinyAssets/TinyAssets/pull/4343#issuecomment-5965426542): native internal tools have no claimed
pre-tool interception (D2 remains held), launch identity requires a proposed
isolated per-launch transport credential, and legacy recurring definitions need
visible holds plus owner reconfirmation. None of these facilities is implemented
by the documentation change.

**What is per agent, and what is shared**

| Per agent (keyed by `agent_id`) | Shared by the whole universe |
|---|---|
| Sessions and conversation memory: one per (agent, thread) | The brain and memory files: one information layer about the owner, their projects and their goals |
| Steering: a steer goes to the agent the owner is talking to in that thread | The workspace files (§4.3) |
| The tool journal and status lines, which name the acting agent | Workflows and automations, attributed to the agent that made them |
| Custom Rules and auto-review switches | |
| Activities (effect intents inherit the agent through their activity) | Seats: account capacity stays shared, with each seat attributed to its agent |
| Stop: a stop targets the addressed agent's turn, beside a separate stop-all | |
| Profile, Activity and Rules pages; pending requests and push, which carry the agent's name | |

**Visibility is a harness capability, not a code path.** Each agent's harness
config has a `visibility` scope, editable by the owner:
- `universe`: the default for the seeded main agent. It can read every agent's
  conversations with the owner, and every agent's activities, status lines and
  effects, within this universe only.
- `own`: the default for other agents. It reads its own threads and activities,
  plus the shared brain.

The platform applies the scope when it builds an agent's context and when it
serves `read_graph` reads. There is no `if main` anywhere. The cross-user floor
is unchanged: "all activity" never leaves the universe. An agent selector never
stands in for ownership: every read and write still binds the authenticated
owner and the pinned universe first.

**What `own` is, and is not.** It is a context and serving policy: what the
platform puts in front of an agent and returns from its reads. It is **not**
isolation between one owner's agents. Agents of a universe share its files, so
an agent with `bash` can reach the stores those files live in. Those are
conversation memory (`.conversation_memory.db`) and native session files. The
shared brain also carries what was learned from every conversation by design.
Raw-transcript isolation between agents would need per-agent stores behind the
jail. That is a separate decision, not claimed here.

**One brain, many writers: the reconcile rule.** Several agents can save
feedback into the same brain at once. Silent last-writer-wins would lose it, so
the rule is:
1. **Capture is one immutable file per entry.** Saving a piece of owner
   feedback creates `brain/inbox/<time>-<agent>-<id>.md`, named by a fresh id
   and created exclusively, so concurrent captures never touch each other.
   `log.md` stays the generated human-readable history.
2. **Tool writes to brain files are compare-and-swap on the whole file.** The
   platform's `write` and `edit` read the file's digest when the agent last
   read it, and apply the change atomically only if the file still has that
   digest. Exact-text matching alone would miss changes elsewhere in the file.
   On a conflict the write is refused and the current content is returned. This
   is guaranteed for writers that use the tools. A `bash` write to a brain file
   bypasses it and is visible in file history; the guarantee is not claimed for
   it.
3. **One fenced reconciler.** The reconciler is the main agent by default
   (owner-changeable). It runs single-flight per universe under the D3
   scheduler's lease and folds inbox entries into topic files.
   - Each topic file records the entry ids it absorbed in its frontmatter.
   - Only after the topic write commits does the reconciler acknowledge the
     entry, by moving it to `brain/inbox/done/`.
   - On a crash between the two, the replay finds the id already absorbed and
     only acknowledges it. Replay is idempotent, and no capture is lost.
   - File history (§4.15) keeps every version, so any merge can be undone.

**Slices this changes**

| Slice | State | Change |
|---|---|---|
| S1 sessions and conversation memory | Merged | The main thread keeps `thread:principal:<owner>` and conversation key `principal:<owner>`. Other agents use conversation key `agent:<agent_id>:principal:<owner>` and session and steering key `thread:agent:<agent_id>:principal:<owner>`: the `thread:` prefix is what S2 steers. No migration |
| Stop (`turn_interrupt`) | Shipped | Today a stop ends every live turn for (owner, universe). It becomes per agent and thread, with a separate stop-all |
| S2 steering (#4188) | Merging | Keyed by session key, so it follows S1. The app sends the addressed agent with the steer (D8 UI) |
| S4 journal (#4190) | Foundation in #4228; integration pending | `agent_turn_journal` has an agent column on the combined foundation, but served creation still defaults to main. The D8 provenance change wires the captured identity; status names the acting agent |
| D1a rules | Merged | Already per agent (`rules.agent`, `MAIN_AGENT` is the seed) |
| D1d review (#4200/#4228) | Per-agent foundation reviewed; integration pending | #4228 adds `(agent, action_class)` and migrates the old global switches to main. The effector still selects main until the D8 provenance change |
| D2 activities | In build | `agent_id` on each activity. Effect intents inherit it through `activity_id`. Status lines go to the owning agent's main session, and to any agent whose visibility covers it |
| Pending requests and push | Shipped | Add the asking agent's id and name ("Your agent asks" becomes "<name> asks"). Deduplication, mute and answer routing are scoped per agent |
| converse and the app | Built | `converse`, the steer route and the owner's conversation read take an addressed `agent_id` (`tinyassets/addressed_agents.py`); a custom UI opens the chat addressed to an agent. The command center decides which agents are exposed (D8) |
| Brain writes | Shipped | Inbox capture files, digest compare-and-swap in the `write` and `edit` tools for brain files, and the fenced reconciler (D3 or D7) |
| Seats | Shipped | Capacity stays per account; each seat carries the agent for attribution |

## 5. What already shipped, mapped onto the dot

| Shipped or in flight | Kept | Changed |
|---|---|---|
| **S1, #4173 (merged):** native session per thread or agent node, persona warmth and per-turn-consent text removed, seeded `AGENTS.md` | Session keying. The main agent keeps `thread:principal:<owner>`, activities add `activity:<id>`, roster agents add `agent:<id>:thread`. The tone removal. Session records outside the universe, the pattern §4.16 generalises. | Only Codex declares `native_resume` today (`codex_provider.py:755`). Claude resume stays pending (S1b). The seeded `AGENTS.md` authority text becomes a **description** of the user's seed rules, and enforcement moves to `rules.decide` (D1). |
| **S3a, #4174 (in review):** bash egress through the checking proxy socket | All of it. | Egress becomes the `shell.egress` class, which is not assumed anonymous. No socket is bound for research turns (D3). |
| **S3c, #4175 (paused after a REJECT review):** platform state leaves the universe root | Nothing depends on it. | The design needs only "the agent has its own workspace it fully owns" (§4.3). The inverse shape is being costed. |
| **First version S2:** owner steering | All of it. | It also carries activity status lines. |
| **First version S4:** journal, session log, spill | All of it. | It also feeds receipts and the live view. |
| **First version S5:** `ta` plus deferred MCP | `ta` over the socket, and the resident block cut. | **Exactly four tools**: deferred MCP for the agent is dropped, and `ta search`/`describe` is the discovery layer (D6). |
| **First version S6:** self-improvement and versioning | All of it. | Memory items get stable ids and the Memory tab. Rules are excluded from what the agent can write. |
| **First version S7:** outward actions through standing grants | The effectors and the standing-grant checks. | **Replaced** by Custom Rules, the execution context, auto-review and hand-back defaults (D1). |
| **First version S8:** every universe | The starter template and deleting the old surface. | Onboarding and the profile join it. |
| Spec requirement "inside acts without asking; outside asks once" | Its default effect, reproduced by the seed rules. | **Replaced** by the rules, context, review and hand-back requirements. |

## 6. Slices

Each slice is its own PR, proven live in the founder's app, with at most 12
tasks. A slice adding a storage shape opens its own storage proposal first. D1
is authority, and its proposal, design and spec deltas are this change.
- **Proof scope.** Each slice's live proof uses only what has landed by then.
- **Re-checks.** Where a later slice adds a surface (activity status in D2,
  take-over in D5), that slice re-checks the earlier behaviour.

**C0: Command-center naming** (copy only, shipped early, §4.11a)
1. Rename "Switch UI" to "Switch command center" in the app and in its
   served guidance.
2. Make the new-user opening greeting open with "Welcome, commander".
3. Use "command center" where the app names the universe's own space.
4. Update tests that pin the old copy.
5. Flag unclear lines in the PR.
6. Live proof: the app header and a fresh account's greeting.

**W: Own workspace** (mechanism being costed in place of S3c)
1. Choose the mechanism: own jail root, or the S3c move.
2. Make the wiki and brain writable by the agent.
3. Add python, node, git and ripgrep to the image.
4. Write a jail-proof test per refused address class.
5. Live proof: write a wiki page, pip install, pytest, git clone.

**S2: Owner steering** (unchanged)
1. Queue messages per session.
2. Append them to the next tool result with the unread count.
3. Write the idle opening line.
4. Live proof: a mid-turn steer.

**S4: Journal and spill** (unchanged)
1. Journal native tool events.
2. Add the HTTP-loop session log and compaction.
3. Spill oversized output.
4. Report one-line causes.
5. Show live tool activity.
6. Live proof: an oversized `read_graph` read in full.

**D1: Custom Rules, execution context, auto-review, hand-backs**
1. Define the action classes and the trusted classification from connection
   declarations.
2. Add the platform-minted execution context through `ta`, workflows,
   automations, extensions, MCP calls and effectors.
3. Apply delegation at the lesser of the two authorities.
4. Add the rules store outside every agent environment.
5. Add `rules.decide` at every enforcement point.
6. Seed rules, including the hand-back defaults and honoured standing grants.
7. Turn `ask_first` and `hand_off` into requests, with "always allow" writing a
   `do` rule.
8. Write the tool-free auto-review under the existing admission: bound to the
   action hash, one retry, fail closed, tighten-only.
9. Add the per-class auto-review switch with plain consequence text.
10. Make agent rule writes refused and proposal-only.
11. Build the Rules tab and change the S1 `AGENTS.md` authority text.
12. Live proof: approve once, then reuse. A hand-back becomes a request. A
    workflow created by a narrowed agent is refused what that agent is refused.

*As built (2026-10-01, lead decision).* D1 ships as D1a, D1b and D1d:
- **D1a:** the rules store, `decide`, the seed and the owner door.
- **D1b:** declared operation kinds and the Rules editor.
- **D1d:** the auto-review on the run's own model and the per-class switch.

The execution context (tasks 2–3) is **folded into D8**. While "main" is the
only agent, every action is already decided under main's rules, so it adds
nothing earlier. Its two pieces that matter sooner land where they are first
needed:
- the research flag with D3;
- the approval id with D2/D3. Until then, *if pre-approved* asks first, which
  fails closed.

Enforcement so far is at the credential-blind effector. The other points follow
the surfaces that need them (`ta` in D6, `browse` in D5).

**D2: Activities** (storage proposal first)
1. Write the storage proposal: records, pending effects, idempotency keys,
   leases.
2. Add the `agent:<id>:thread` and `activity:<id>` keys with parentage.
3. Add `ta activity start/list/stop`.
4. Hold a seat while running, release it while waiting.
5. Resume after a deploy, reconciling unknown effects.
6. Add the automation `activity` target kind with the full user-owned contract.
7. Report status lines into the main thread.
8. Keep partial results on stop.
9. Build the Activity tab with a complete, paged Scheduled view.
10. Add Pause and Resume.
11. Live proof: two activities in parallel after the chat is closed, and one
    surviving a deploy.

**D3: Proactive research and grantable blocks**
1. Write the idle, cadence, single-flight and coalescing scheduler, with the
   defaults in the Scheduled view.
2. Carry the `research` flag in the context.
3. Build the positive allowlist of audited reads, and make `pending_requests`
   reads side-effect free.
4. Inside the jail: refuse write and edit, mount bash read-only, bind no
   egress, leave `browse` absent.
5. Run no extensions, hooks or flush in research.
6. Add the narrow proposal path.
7. Make an approved proposal a pre-approved activity.
8. Turn blocked work into grant proposals or patch requests.
9. Show the no-compute notice on the research row.
10. Write a jail and handler test that every write path is refused in research.
11. Live proof: a real proposal, and a blocked item turned into a grant.

**D4: Onboarding, profile and push**
1. Write the template questions.
2. Write name and responsibility into the files.
3. Propose the approval items as rules.
4. Schedule the report cadence.
5. Show the no-compute notice.
6. Build the profile shell.
7. Add the push kinds.
8. Live proof: a fresh account onboarded.

**D5: The computer**
1. Run the browser sandbox outside agent environments.
2. Add the restricted broker command set: no CDP, eval or export.
3. Keep the profile store outside agent reach.
4. Protect login fields and sanitize URLs.
5. Add the credential filter on text, screenshots, downloads and errors.
6. Give each activity its own context, with approvals bound to the target.
7. Add the live view.
8. Add Take over / Return control with recording suspended.
9. Live proof: the owner logs in during take-over, the agent continues, and no
   secret is in the log.

**D6: Exactly four tools**
1. Add `ta search` and `ta describe`.
2. Cut the resident block to four plus the `ta` line.
3. Disable native built-ins everywhere.
4. Drop deferred MCP for the agent.
5. Move handbook chapters to skills.
6. Lower the ratchet.
7. Live proof: tokens per round before and after.
8. Implement owner-attached MCP servers using the D6a follow-up interface below,
   after resolving attachment authority/storage and transport lifecycle.

**D6a implementation (2026-10-04; additive, not the four-tool cutover).**
`ta` is a dependency-free executable mounted read-only in each bash jail.
A private, invocation-scoped Unix socket reaches the existing engine handlers
and connection effector; it is removed when bash returns. No bearer or vault
reference enters the jail. The engine captures the owner, universe, initiating
agent and research flag from its platform launch, and rechecks serving-owner
authority on every socket request. Client JSON cannot set that context.
Connection calls re-read active owner/universe grants and use the existing
effector's soul authority, per-agent rules, auto-review, consent and credential
broker, including endpoint/method scope and response secret filtering.
Approval ids are absent in this slice: `do_if_preapproved` still asks first.
The generic HTTP connection definition works for any platform without a patch.

Discovery uses `ta search <words>` and `ta describe <name>`; calls use
`ta <name> --json '<object>'`. Platform names are the existing served engine
handles (which call the connector implementations under the existing pins).
Connected operations are `connection:<connection-id>:<scope>` and accept
`{"request":{"path":"/v1/items","body":{}}}`. The scope supplies the HTTP
verb, and describe includes the connection's allowed endpoints. Refusals print
JSON and exit nonzero. No new public MCP handle or storage table is added.

An extension unit is a folder beneath `extensions/<package>/` or
`agents/<initiating-agent>/extensions/<package>/`, containing an executable
and `extension.json`, for example:

```json
{"executable":"run","tools":[{"name":"hello","description":"Greet a person",
 "arguments":{"type":"object","properties":{"name":{"type":"string"}}}}]}
```

The CLI reads these files inside the jail on each invocation. Tools are named
`ext:shared:<package>:<tool>` or `ext:agent:<package>:<tool>`; executable argv is
`[executable, tool-name, arguments-json]`, stdout is one JSON value, and stderr
is diagnostic output. Programs inherit the same jail and socket, can compose
`ta` calls, and gain no authority. Nothing executes on the daemon from a
manifest. No registration or deploy is needed. Existing read-only root mounts
still apply; this slice does not widen the roster's harness-edit permissions.

**`ta` never exceeds the launch's tool grant (2026-10-04, PR #4439 review).**
An agent node's `tools_allowed` narrows its served tools, and before `ta` that
narrowing held only in the provider's own tool list. `ta` now carries it to the
engine. Each engine server holds a second random key beside its bearer, published
on the same private route record and never given to a provider launch. Every
launch's route carries its served tools signed with that key over the launch's
session and turn. `ta` serves the platform capabilities in that signed grant and
nothing else; with no signed grant, or one without `bash`, bash runs with no
`ta`. Connections are callable through `ta` only when the grant holds both
`write_graph` and `run_graph`, the tools that already reached a connection by
building and running an effect node. The unnarrowed default (the owner's chat,
or a node naming no tool) signs the whole served set, so it keeps every
capability. Nothing in a `ta` request, a manifest or the request route is a
grant unless the signature verifies. The key lasts for the supervised engine
server object, including subprocess crash/respawn; there is no table and no public handle. Not covered: the grant has no
expiry within one server's life, and a direct call on the engine's MCP route by
a holder of the bearer is still narrowed only by the provider's tool list, as
it was before D6a.

**Attached MCP follow-up (deferred, no partial adapter).** Reserve
`mcp:<attachment>:<tool>` for tools/list descriptions and tools/call. An owner
attachment should declare either `stdio: {argv, cwd}` (executed inside the
same jail with its cleared environment), or `http: {connection_id, endpoint}`
(a credential-blind transport using that connection's rules, grants and scope).
The interface must bind the same platform execution context for its entire
session, never accept credentials or actor/context overrides in the attachment,
and close children/sessions with the invocation. Before implementing, settle
owner-authenticated attachment storage/activation and MCP initialization,
streaming, cancellation and reconnect semantics. The existing connection
broker buffers one HTTP request/response, so treating it as a persistent MCP
transport now would only half-build the capability. This work remains in D6;
any new table or public owner door needs its own delta before code.

Resident-tool removal, native-tool changes, handbook migration and the token
ratchet remain D6 follow-ups. Durable workflow/automation context propagation
remains D1's separate work; D6a binds mid-turn connection effects. No deployment
or live-user acceptance is claimed by this branch.

**D7: Memory and harness editing**
1. Give memory items stable ids.
2. Build the Memory tab.
3. Add the jailed history store and Undo.
4. Add `settings.yaml`.
5. Build the Harness tab.
6. Add seed curator and review skills.
7. Delete `read_brain`, `write_brain`, `soul_edit` and the learning call.
8. Live proof: delete one item, and Undo an `AGENTS.md` change.

**D8: Roster and command center**
1. Add the `agents/<id>/` layout and overrides.
2. Add create, rename and remove agent.
3. Make delegation activities run at the lesser authority.
4. Classify shared-harness edits.
5. Build the roster view with inline approve and pause.
6. Build both pages on the custom-UI layer.
7. Live proof: a lead hands work to a specialist, which runs under the
   specialist's rules.

**D9: Sharing**
1. Define the bundle manifest and path rules.
2. Add the size policy and reconcile the component limit.
3. Write the scrub pass with an owner-confirmed preview.
4. Store quarantine outside agent reach.
5. Add the owner activation record.
6. Exclude quarantine from the index and loading.
7. Activate only as written or stricter.
8. Live proof: a second account imports, and nothing runs until it activates.

**D11: Export to a runnable, publish-ready folder** (after D9, before D10; §4.17)
1. The export door and the folder layout over D9's manifest (`format_version`).
2. Fold `okf_export` in as the `wiki/` writer; close the okf concern.
3. Scrub and preview shared with D9; credentials as references plus `.env.example`.
4. README, LICENSE placeholder and `.gitignore` generation.
5. The local runner: four tools, `AGENTS.md` and skills, `rules.json` prompts, any OpenAI-compatible endpoint, the schedule loop.
6. Import accepts an exported folder, a zip or a cloned repository into quarantine.
7. Live proof: export the founder's command center, run it against a local Ollama model with no account, publish it with the agent's own git, and import the clone on a second account.

**D10: Everywhere, and delete the old surface**
1. Seed every new universe from the published starter template.
2. Add starter channel extensions for Slack and Telegram.
3. Delete the replaced handles and guidance.
4. Run `ui-test` on a fresh account.
5. Run the canary with `--assert-handles`.

## 7. Decisions (2026-10-01, the lead from standing founder direction)

1. **Hand-backs:** on by default, exactly as in dots. They are seed rules the
   user may edit, and the UI explains what turning one off means. Only the
   cross-user floor is locked.
2. **Auto-review:** on by default for every consequential action, with a
   per-class off switch.
3. **Idle research:** idle 30 min, at most every 4 h, 08:00–22:00 local, on for
   new users, on the user's own compute. It is visible and editable in the
   Scheduled view, and says so plainly when no compute is connected.
4. **Brain safety (approved in the first version):** rollback through file
   history, not refusing the write.

No founder questions remain open in this revision. Approval of the whole is
task 1.2.

**Review record.**
- gpt-6-astra refute of 04983ba2 (first version): ADAPT, folded in.
- gpt-6-astra refute of b3f7c054 (this revision): ADAPT. Eight DISAGREE points
  and four extra findings are folded into §4.3–4.16, §5 and §6. Points 7 (four
  tools) and 10 (no background-self derivation) were AGREE.

## Appendix A: first-version detail that is kept

These sections are from the approved first version, renumbered. §4 overrides
them where the two differ. In particular:
- §4.8–4.10 replace the first version's authority section (3.5), which is
  omitted here.
- §4.7 replaces its tool table (3.4), which is omitted here.
- The "Platform: one extension … deferred MCP" route is dropped.

### A.2 Sessions (biggest felt change)

- **Unit.** A *session* is a durable, append-only log of messages, tool calls,
  tool results, compactions and events. It has an id and belongs to one
  universe. There is one agent and one session per conversation thread: the
  owner's main thread is one session across app, phone, desktop and connector.
  Agents the universe builds for itself (agent nodes in its graphs) are its own
  creations. The harness gives them the same primitive (an agent node may keep
  a session keyed by its node) so it can build good agents, but their behaviour
  is theirs, not the harness's.
- **Storage.** As built in S1, the session log lives in the data root's
  `.agent-sessions/<universe>/`, outside every universe folder (§4.16), and
  only the platform can write it. The agent sees it read-only, through a
  read-only mount at `/u/sessions` in the tool jail, never as a writable root
  path (Codex review finding 6). So it can read and grep its own history but
  cannot forge it. It is the
  successor to `AgentTurnJournal` rows, which become keyed by session. Its
  bytes count to the account storage pool (`account-storage-quota`).
- **Model context** is the last compaction summary plus every entry after it.
  The platform assembles it mechanically:
  1. base prompt (≤600 tokens);
  2. `AGENTS.md`;
  3. `MEMORY.md` (the first 200 lines or 25 KB);
  4. the skill index;
  5. the session.

  There is no persona Python, no 7,000-character text block, and no
  re-description of files.
- **Compaction.** The defaults follow pi:
  - The trigger is `projected_tokens > window − reserve`, with a 16k reserve
    and the latest 20k kept verbatim.
  - The summary is structured (goal, decisions, files touched, open threads,
    pending requests, exact ids) and made by the session's own model. Tool
    call/result pairs are never split, as in OpenClaw.
  - Before compacting, one silent *flush* round lets the agent write what must
    survive to its files, as in OpenClaw's memory flush.
  - Thresholds live in `settings.yaml`, which the agent and user edit.
  - The flush and summary calls are made by the universe's own model, on its
    own credentials, through the same seat and accounting as any other turn.
    There is no platform fallback model (Hard Rule 15).
- **Native adapters.** An adapter MAY declare a `resume` capability with an
  opaque handle, which the platform stores on the session (a CLI session id,
  for example). With `resume`, the adapter continues its own native session and
  `--ephemeral` goes. An adapter that `self_compacts` compacts itself, and the
  platform records the event. Without `resume`, or after a model switch, the
  platform seeds a new native session from the summary plus the tail. The
  platform log stays the truth either way. No vendor name appears in this
  contract (Hard Rule 3). The existing adapters are migration debt that
  implements it.
- **The turn runs until done.** The founder's rule already holds in
  practice, though the code expresses "no cap" as a 30-day sentinel
  (`universe_intelligence.py:272`). The coordinator enforces that sentinel
  (`agent_turn_coordinator.py:416`), and it becomes a truly absent deadline.
  Real turns stop only on idle, Stop, run-owner proof, or a budget the user
  set (`turn-runs-until-finished-not-wall-clock`). A "turn" ends when the model
  stops calling tools.
- **Steering.** A message arriving mid-turn is queued. At the next tool
  boundary the platform appends the queued messages to that tool's result as a
  delimited "new messages from your founder" block, and adds the unread counter
  from #4170. This is OpenClaw's steering queue, expressed in a way any adapter
  can carry, because every adapter returns tool results. Stop and "send all
  queued" is #4152.

### A.3 Owner messages and events reach the live session

- A message the owner sends while a turn is running is steered into that turn
  at the next tool boundary, appended to that tool's result together with the
  unread count (#4170). The owner never waits for a long turn to end before
  being heard. Stop, then send everything queued, is #4152.
- Events that concern the owner's session reach it the same way when a turn is
  live, or as the opening line of the next turn when it is idle. Those events
  are a run it started finishing, a request it raised being answered, or an
  owner message from another surface. The line is short and mechanical, for
  example "since your last turn: run 7f3… completed (failed: …), request req_…
  answered". It is computed by the platform and never authored by an LLM.
- How any agent the universe builds wakes, loops or schedules itself is that
  agent's own design. The platform supplies the event and trigger primitives
  (`automation_events`, #4171) and adds no wake policy.

### A.6 Tone and the seed `AGENTS.md`

The persona Python is replaced by a short seed `AGENTS.md`. It belongs to the
user and is edited by the agent. Its core, about 300 tokens:

> I am {name}, my founder's agent, working in my universe at /u. I work like a
> senior engineer with my own computer. I do the work, check that it worked,
> and report in a few lines: what changed, where, how I verified it, and what
> is next if anything. Result first, no preamble, no apologies, no restating the
> question. I mention what I could not verify only when it changes what my
> founder should do. Inside my universe I act without asking. I ask, through one
> app request, only for credentials, reaching other people, or spending beyond
> my budget. When blocked I try another route, then move to other work. I keep
> MEMORY.md current, save a skill when I solve something new or get corrected,
> and edit this file when my founder tells me how to work.

Identity stays in `identity.md`: the persona name still comes from the
learned self-model, as the existing personification spec requires. Only the
platform-authored tone and behaviour text moves into `AGENTS.md`. `voice.md`
keeps working until S6 folds it in. The untrusted-envelope rule stays in
the base prompt, because it is a cross-user boundary.

### A.7 Feedback loop

- **Truthful, fast results.** A tool error returns the real cause in one line
  plus the remedy where one is known. There are no generic "unavailable"
  messages (`a-blanket-except-makes-one-message-for-every-cause`). Results over
  the model budget are written to `sessions/<id>/tool-output/<call>.txt` and
  returned as a head plus the path, never as a truncation marker with nothing
  behind it.
- **Every adapter journals every tool call** into the session log. For native
  adapters this comes from their streamed tool events, which the Codex adapter
  already parses (`codex_provider.py:397`). The app shows the turn's tool
  activity live. That is the "see it work" half of Claude Code.
- **Verify is a habit, not a gate.** The seed `AGENTS.md` asks the agent to
  check each effect by reading it back (run status, file diff, PR state). The
  platform adds no checklist (founder 2026-10-01: "No gates, review checklists or
  cooldowns").

### A.8 Self-improvement with versioning and rollback

- **Harness files.** These are user-owned and agent-editable:
  - `AGENTS.md`
  - `MEMORY.md` (index, always loaded, bounded)
  - `skills/`, `prompts/`, `extensions/`
  - `settings.yaml` (model preference, compaction thresholds, reserve,
    session defaults)
  - the brain files (OKF kept)
- **Versioning.** At turn end the platform snapshots the changed tracked paths
  into a history store under `.runtime/harness-history/`. The commit message
  carries the session and turn id. The store is a bare git repository that the
  agent cannot write or delete.
  - Git never runs on the host against agent-written content. The commit runs
    as a process inside a credential-free tool jail, with `/u` read-only and
    only the history store writable, and with hooks, filters, `core.*` and
    attributes all disabled.
  - An agent-written `.git/hooks` or a filter config therefore never executes
    outside the jail (Codex review finding 5; `workspace_git.py:3` forbids
    host git on user-controlled repos for this reason).
  - The agent may keep its own git repository in `/u` for its own use, which
    is separate from the history store.
  - Package caches are not tracked. Rollback
  uses `git` from bash, `ta harness rollback <rev>`, or an **Undo** in the app.
  This is the code-level guard against a weak model blanking a file (see open
  question 2): any loss is one revert away. A turn that empties or more than
  halves a loaded file (`AGENTS.md`, `MEMORY.md`, a brain file) gets an Undo
  notice in the app and a line in its next event message.
- **Learning loop.** The agent learns the way Hermes and OpenClaw do, by
  default and through files:
  - it saves a skill after novel multi-step success or a correction;
  - it patches a skill it used that was wrong;
  - it updates `MEMORY.md` at the pre-compaction flush.

  A curator (stale, then archived) and a nightly experience review ship as
  editable seed workflows, not platform code.
- **Deleted:** `read_brain`/`write_brain`, the separate learning-extraction call
  (`extract_learning`, `commit_learning`, `_LEARNING_SYSTEM`,
  `_UNRECORDED_LESSON`), the `soul.edit` whitelist and `soul_versions/` for
  owner turns. Git replaces versioning. The founder-only write boundary is
  enforced by the jail: a visitor turn has no write view.

### A.9 Map to existing primitives (reuse, do not rebuild)

| Need | Existing primitive |
|---|---|
| Session log | `AgentTurnJournal` / `agent_turn_rounds` / `agent_turn_tools`, rekeyed by session. `conversation_store` (custody, failure history) stays as the owner-door projection. |
| Turn loop and adapters | `agent_turn_coordinator.py`, `providers/call.make_interactive_agent_turn`, native adapters |
| Stop and queued messages | `turn_interrupt.py` (#4152) |
| Unread counter | #4170 |
| Event wakes | `automation_events.py`, `owner_message` (#4171), `pending_request_answered`, `app_event`, schedules, webhooks |
| Agent nodes (agents the universe builds) | `shared_self.py`, `served_tools.node_tool_grant` |
| Jail and tools | `universe_tools.py`, `providers/provider_jail.py`, `universe_files.py` |
| Skills | `universe_tools.skill_index` |
| Asks | `pending_requests` and the request rail with phone/desktop/browser notifications (#4140, #4138) |
| Credential-blind calls | `effectors/authenticated_external_call.py`, `connect_http`/`extend_http` grants |
| Concurrency and usage | `universe_seats` (#4154), account storage quota (#4158, #4166) |
| Platform verbs | `engine_mcp_server.py` handlers, re-exposed (not rewritten) as `ta` and as deferred MCP |

### A.10 Delete list (each lands in the slice that replaces it)

- `universe_intelligence.py`:
  - persona assembly `_build_persona_system_prompt` with its brain, ask and
    clock sections (the clock becomes one base-prompt line);
  - `_GROUNDING_IS_CURRENT`, `_CROSS_SURFACE_CONTINUITY`,
    `_turn_input_method_context`, `_UNRECORDED_LESSON`;
  - the learning path (`extract_learning`, `commit_learning`,
    `_learn_from_turn`, `_LEARNING_SYSTEM`, `_parse_learning_json`,
    `_brain_recording_tools`, `_wrote_its_brain`);
  - the WebFetch denylist tuples, once every adapter runs tools only in the
    platform jail.
- `conversation_memory.format_history` as model context (it stays only as an
  owner-door transcript renderer, if one is still needed).
- Codex `--ephemeral` and the empty-`/workspace` converse mode
  (`codex_provider.py:871-880`).
- Engine handles `read_brain`, `write_brain`, `connect_compute`, `source_channel`
  as resident tools (folded into `ta`). The resident handbook guidance moves
  to skills, and the 30,000-character ratchet in `test_converse_turn_cost.py`
  drops to the new budget.
- `soul_edit` governance and `soul_versions/` for owner turns, and the
  `voice.md` special case.
- The prompt text that teaches per-turn consent: the `conversation_memory.py:158`
  footer, and any similar handbook text. The effectors' standing-grant checks
  stay.
- The 362 `.worker_supervisor.*.json` files at the universe root move to
  `.runtime/`. Leftover epoch-1 supervisor files with no live owner are
  deleted.

### A.11 Risks

1. **Weak free models with full power.** Mistakes stay inside the universe and
   git reverts them. The founder's 09-26 rule (a code guard, not obedience)
   is answered by git rather than refusal. Measure per model family in each
   slice's live test.
2. **Egress safety and abuse** (Codex review finding 7).
   - The filter is enforced at packet level, inside the jail's own network
     namespace. It covers every protocol and both address families, and checks
     each translated destination (NAT64/DNS64, as `storage/outbound_connections.py:1649`
     already documents), not only the address the agent named.
   - The host's own services and other universes' engine ports are never
     routable.
   - Per-tenant connection-rate and bandwidth limits protect the shared IP and
     the box. These are host-protection floors, not usage quotas.
   - S3 cannot land without a jail-proof test for each refused class.
3. **Session logs hold sensitive tool output.** They are owner-only and never
   published. A commons publish refuses `sessions/` (old design §4).
4. **Two authorities.** `conversation_store` and the session log must not both
   be model context. The session log is the model's, and the store is the
   owner door's projection of it. Delete the old path in the same slice.
5. **Writable root before trusted readers move** (Codex review finding 6). If
   the agent could create a legacy `.effector_consents.db` at the root, the
   daemon would read forged grants from it.
   - S3 moves every trusted reader to `.runtime/` and removes each legacy
     root-path fallback before the root becomes writable, in one change.
   - Provider-launch and tool-jail views stay separate, and hidden-settings
     masking stays (`provider_jail.py:251`).
6. **Deploys kill live turns** (`deploy-kills-in-flight-turns`). A session
   survives, and the next event resumes it from the log, so a deploy loses at
   most one round, not the thread.
