# 2026-10-04 — Orchestration research and the T1-T10 capability checklist.

**Founder correction (2026-10-05): capability checklist, not build deliverables.** The platform never builds or ships T1-T10 setups or channel/team templates. Users, starting with the founder's own agent, build these as their own command-center projects, as complex as they like, to exercise the platform's general primitives.

# Agent orchestrations people build and sell: a comparative analysis and capability checklist for TinyAssets

**Date:** 2026-10-04. **Method:** WebSearch (mostly extended mode) and WebFetch, run 2026-10-04. No repo files were edited. **Environment:** Claude Code research subagent. Some pages were read through a summarising fetcher, and the YouTube and npm pages did not render. Where a number came only from a search-result snippet, the citation says so.

**Evidence labels used throughout:**
- **[F] documented fact.** Primary source: a repo README, official docs, or the author's own post.
- **[M] marketing or third-party claim.** Comes from vendor pages, SEO or aggregator blogs, or influencer statements. Not verified.
- **[I] my inference.**

**The founder's goal, restated as a test:** *any setup a user has seen online can be rebuilt in TinyAssets, run better there, and shared. The creator of the original then publishes on TinyAssets.*

---

## PART 1: pi (pi.dev). The harness and what people build on it

### 1.1 The core, as designed
- **[F]** Mario Zechner's design post is "What I learned building an opinionated and minimal coding agent", dated 2025-11-30 (https://mariozechner.at/posts/2025-11-30-pi-coding-agent/). Its main points:
  - **Tools and prompt.** Pi has four tools: `read`, `write`, `edit` and `bash`, plus optional read-only variants. The system prompt and tool definitions together come to under 1,000 tokens. Only `AGENTS.md` is appended to the prompt.
  - **No MCP:** "MCP servers dump their entire tool descriptions into your context on every session". Pi uses CLI tools plus READMEs instead, which gives progressive disclosure.
  - **No sub-agents:** "If you need pi to spawn itself, just ask it to run itself via bash."
  - **No plan mode.** Use a `PLAN.md` instead.
  - **No background bash.** Use tmux, which keeps long-running work observable.
  - **YOLO security.** There are no permission prompts. The container is the real boundary.
  - **Sessions and modes.** Sessions branch and resume. Context can be handed across providers, with thinking traces converted. Pi has an RPC mode (headless JSON streaming), cost tracking, and OAuth for Claude Pro/Max.
- **[F]** Packaging comes from the pi-mono monorepo: `pi-ai` (unified LLM API), `pi-agent-core`, `pi-coding-agent`, `pi-tui` and `pi-web-ui` (https://pyshine.com/Pi-Mono-Full-Stack-AI-Agent-Toolkit/, https://www.npmjs.com/package/@mariozechner/pi-coding-agent).
  - The repo now also appears under `earendil-works/pi`. That copy adds `pi-durable` (a conversation/task runtime), `pi-telemetry` and a `Chord` app-composition runtime, and shows about 112.5k stars (https://github.com/earendil-works/pi, fetched 2026-10-04).
  - Pi runs four ways: interactive, print/JSON, RPC, or the TypeScript SDK.
- **[F] The extension API** (docs/extensions.md in pi-mono, fetched 2026-10-04). This is the one mechanism everything else hangs off.
  - **Lifecycle events:** `session_start`/`session_shutdown`, `before_agent_start`, `agent_end`, `agent_settled`, `context`, `context_with_system`, `turn_end`, `message_end`.
  - **Tool events:** `tool_call`, `tool_result` and `tool_execution_*`. A `tool_call` handler can mutate the input or block the call with `{block:true, reason}`.
  - **Other events:** `provider_stream_event`, `user_bash`, `mcp_servers_change`, and `pi.events` for messages between extensions.
  - **Registration:** `registerTool` (with a schema and an optional output schema), `registerCommand`, `registerShortcut`, `registerFlag`, `registerProvider`, `registerVirtualModel` (routing), `registerToolRenderer`, and `registerMcpServer`.
  - **UI:** `ctx.ui.confirm/notify/custom/status`, footer widgets, overlays, and a replaceable editor. The RPC mode supports dialogs.
  - **State:** `appendEntry` (durable state hidden from the model), `sendMessage`/`sendUserMessage` (injected into context), `setActiveTools`, and `modelRegistry.streamSimple` for nested model calls.
  - **[I]** `registerMcpServer` suggests that MCP has since become an extension-level primitive rather than a core feature. The summariser may have conflated it with an adapter, so re-check it before quoting.

### 1.2 How customisations are built, shared and installed
- **[F]** There are four unit types: Extensions (TypeScript), Skills, Prompt templates, and Themes. They are bundled as "pi packages" and installed with `pi install npm:<pkg>` or `pi install git:github.com/<owner>/<repo>`. `pi list` shows them and `pi update` upgrades them. Packages install globally by default, or per project with `-l` (https://pi.dev/packages, https://gist.github.com/schpet/85531b6a05a5d8119e859bdec6b0e0b8).
- **[F]** The pi.dev/packages catalogue listed **5,357 packages** on 2026-10-04. It filters by type (extension, skill, theme, prompt), author and downloads.
  - The monthly download figures it displays: pi-mcp-adapter 1.5M, pi-subagents 593K, billion-context 552K, pi-web-access 546K.
  - **[I]** These figures are high enough that they probably include CI and transitive installs. Use them for ranking only.
- **[F]** The idiom is "Ask Pi to build what you want, or install a package that does it your way." The agent writes its own extensions, so a user's customisation is usually agent-authored code.

### 1.3 Catalogue of orchestrations built on pi

All entries are [F] repo or package descriptions unless marked. Sources: awesome-pi.site/extensions, taoofmac.com/space/ai/agentic/pi, pi.dev/packages, and the repos linked.

| Category | Examples | How built |
|---|---|---|
| **Sub-agents / delegation** | `pi-subagents` (single delegation plus scripted multi-agent workflows); `@tintinweb/pi-subagents` (Claude-Code-style, parallel); `pi-herdsman` (async, nested, background); `@quintinshaw/pi-dynamic-workflows` (fan-out to hundreds of subagents with model routing, token accounting and worktree isolation) | Extension registers a `delegate` tool that spawns child pi processes over RPC/SDK |
| **Multi-agent teams** | `pi-agentteam` (leader plus researcher, planner and implementer in tmux panes, 2026-06-04); `Pi-Agents-Team` (background RPC workers; the orchestrator sees only summaries and one `final_answer` per worker); `pi-agent-teams` (Claude agent-teams clone with a shared task list); `pi-coordinator` (orchestrator locked to a single `dispatch_agent` tool, three specialists); `agent-pi` (orchestration suite with browser review tools and guardrails); `pi-agent-harness` (baryonlabs: one domain sentence becomes a team of `.pi/agents`, `.pi/skills` and `.pi/prompts`, with six team patterns) | Extensions plus tmux, worktrees and RPC. Teams are defined as files |
| **Visible orchestration** | `pi-tmux-orchestrator` (supervised tmux grids); `pi-tmux-agents` (persistent tmux sub-agents, watchdogs, worktrees, TUI); `pi-side-agents` (short-lived agents with statusline tracking) | tmux is the process supervisor |
| **Hooks / guardrails** | `pi-permission-system`, `pi-warden` (judges writes against policy docs), `pi-auto-review` (model-backed approval broker with hard-deny), `pi-verdict` (auto-mode-style gate), `pi-sentinel` (runs checks on every edit and checkpoints) | `tool_call` block/modify hooks |
| **Always-on / remote / channels** | **rho** (always-on operator: about 30-minute check-ins, `brain.jsonl` memory plus a markdown vault, web UI at `/brain`, Telegram, an email inbox at `name@rhobot.dev`, runs on macOS, Linux, Android/Termux and iOS via SSH; https://github.com/mikeyobrien/rho); **Mercury** (WhatsApp, Slack and Discord gateway; pi as a sandboxed subprocess using bubblewrap or sandbox-exec; RBAC at the bash level; cron and one-shot jobs; extensions compiled into a single Docker image; https://github.com/Michaelliv/mercury); `pi-queue` (webhook tasks, human approval, isolated worktrees); `pi-tramp` (SSH/Docker remote execution); `remote-pi` (PWA remote control via relay) | pi wrapped by a daemon. The extension provides channels, cron and memory |
| **Model routing** | `pi-router` (failover, circuit breaker), `pi-bifrost` (tiers and strategies), `pi-multi-account` and `pi-multi-pass` (rotate subscription OAuth accounts), `registerVirtualModel` | Provider extensions |
| **Memory** | `pi-hermes-memory` (session search, secret scanning), `pi-qdrant-memory`, `pi-memory-mem0`, `pi-blackhole` (compaction plus observational memory), `gentle-engram` (one brain shared across MCP agents), `billion-context` (compression) | `context` hook plus storage |
| **MCP** | `pi-mcp-adapter` (top package), `pi-mcp-extension`, `@pi-unipi/mcp` | Extension proxies MCP tools lazily |
| **UI customisation** | `pi-generative-ui` (streams HTML/SVG widgets into macOS windows), Glimpse (WKWebView micro-UI), `pi-draw`, `pi-open-tui`, `pi-leader-key`, themes | `ctx.ui.custom`, overlays, renderers |
| **Alternative front ends** | `pi-web-ui` cockpit, `pi-webview` (Chrome side panel), pi-gui (Electron), pithagoras (hosted web UI), pi-companion (web, mobile and desktop), Graphone (Tauri) | SDK/RPC clients |
| **Voice / computer use** | `rpiv-voice` (local Whisper `/voice`), `pi-sano-tts`, `doompi-voice`; `pi-computer-use` | Tools plus UI hooks |
| **Observability** | Langfuse, Braintrust, `pi-otel`, Raindrop plugins; `pi-token-burden` | Event subscribers |
| **Skills packs** | e.g. "bigpowers" (73 engineering skills) | SKILL.md bundles |
| **Ports / forks** | `pi_agent_rust`, `pz` (Zig), `oh-my-pi` (with an IDE wired in) | Re-implementations of the same shape |

**[I] What pi proves.** A minimal core plus *one* extension mechanism produced roughly 5k packages covering every orchestration pattern listed in Part 2. That works because the mechanism has four properties:
1. **Hooks into the loop** (`tool_call`, `context`, `before_agent_start`).
2. **Tool and command registration.**
3. **UI hooks.**
4. **Package install and update with a single command.**

TinyAssets has none of the four yet in the form a command-center designer can use.

---

## PART 2: "Jarvis" / command-center creators. 27 concrete setups

Column key: Agents/roles · Tools/memory · Schedule · Channels/UI/voice · Approvals · How sold · How run.

| # | Setup (creator) | What it does | How sold | How buyers run it | Evidence |
|---|---|---|---|---|---|
| 1 | **OpenClaw core** (ex-Clawdbot/Moltbot) | Self-hosted personal agent with full system access (browser, terminal, filesystem); memory across sessions; skills as `SKILL.md`; **heartbeat** every ~30 min that speaks only when something needs attention; gateway to Telegram, WhatsApp, Slack, Discord, Signal, iMessage | Free OSS; ecosystem below | Mac mini, Linux box or WSL2, plus your own API keys | [F] https://docs.openclaw.ai/start/openclaw, https://milvus.io/blog/openclaw-formerly-clawdbot-moltbot-explained-a-complete-guide-to-the-autonomous-ai-agent.md |
| 2 | **ClawHub registry** | Community skills (Gmail, GitHub, Spotify, Hue, Obsidian, crypto trading). The registry grew from 2,857 to 10,700+ skills. **ClawHavoc:** 341, then 824, then 1,400+ malicious skills (AMOS stealer, key theft). VirusTotal scanning was added | Free; some paid listings ("15% fee" per a seller blog [M]) | One-command CLI install | [F] https://thehackernews.com/2026/02/researchers-find-341-malicious-clawhub.html; [M] https://crewclaw.com/blog/sell-openclaw-agents-marketplace (2026-03-02) |
| 3 | **Alex Finn: "Henry" plus Mission Control** | Chief-of-staff agent Henry (Opus 4.6) over Ralph (eng manager, ChatGPT OAuth), Charlie (dev, local Qwen 3.5), Scout (research) and Quill (writer). Agent-built **Mission Control** dashboard: task board, calendar, projects, memory, docs, team screens, plus a "2D pixel-art factory" view. A "mission statement" at the top drives reverse prompting when idle | YouTube/X audience; community/course monetisation not confirmed in the sources read | 4 Mac Studios (~1.5 TB unified memory) running local models plus $250/mo ChatGPT Pro | [F roles/hardware per article] https://metatrends.substack.com/p/clawpilled-meet-your-ai-chief-of (2026-03-04); https://x.com/AlexFinn/status/2028791230713528753; [M] the "rebuilt a multi-week feature in 5 min" and "agent phoned him" claims |
| 4 | **Mission Control (OSS dashboard for OpenClaw)** | Seven-column Kanban (Planning through Done), live event feed, AI planner that asks clarifying questions before dispatch, bootstraps Builder/Tester/Reviewer/Learner agents | Free OSS; agencies resell setup | Container beside the OpenClaw gateway | [F] https://pub.towardsai.net/mission-control-an-orchestration-dashboard-for-openclaw-c3454f959b15, https://www.dan-malone.com/blog/mission-control-ai-agent-squads |
| 5 | **Dan Malone: Telegram-forum agent team** | Four agents (generalist Claudette, Home Assistant "Homey", productivity "Goaly", finance "Fin") with one Telegram forum topic each; MCPorter bridges MCP servers (Notion, Granola, HA, Xero); `sessions_send` for agent-to-agent messaging with permissions; heartbeats, crons (inbox triage, weekly planning, 15-min git sync) and webhooks; confirmation for locks and alarms; SOUL.md boundaries | Not sold; implementation offered as a service | Proxmox container | [F] https://www.dan-malone.com/blog/building-a-multi-agent-ai-team-in-a-telegram-forum |
| 6 | **Riley Brown: narrow-agent team** | After "200 hours testing": many skills on one agent fails; build focused narrow agents (e.g. a YouTube agent) and compose them into a team | YouTube/X content funnel | OpenClaw | [F/M] https://x.com/rileybrown/status/2028233214398206096 |
| 7 | **Greg Isenberg / Brian Casel: "24/7 digital employees"** | Main agent plus sub-agents across 5–10 machines; pick one boring workflow in one industry and sell it. Casel: $600 Mac mini with four agents in Slack (dev, marketing, admin) | Podcast; builders paid to deploy OpenClaw for executives | Mac mini / VM / Orgo | [M] https://x.com/gregisenberg/status/2024247983999521123, https://www.theneuron.ai/explainer-articles/openclaw-personal-ai-agent-setup-guide-an-use-cases-february-2026/ |
| 8 | **Hermes Agent (Nous Research)** | Self-improving: writes skills from experience and refines them; SOUL.md plus MEMORY.md plus session search; built-in **cron** with delivery to any platform; **zero-token cron** (`--script --no-agent`); single gateway (Telegram, Discord, Slack, WhatsApp, Signal, WeChat, iMessage); Kanban where the parent posts cards and sub-agents work in parallel; `/steer` mid-run and `/rollback`; brain on a VPS, arm on a laptop over outbound WSS | Free OSS; agentskills.io skills; a French firm charges €2,700/mo for adapted workflows (user story) | $5 VPS, Raspberry Pi 5, Android/Termux, Mac Studio; models via OpenRouter or local | [F] https://github.com/nousresearch/hermes-agent, https://hermes-agent.nousresearch.com/docs/user-stories |
| 9 | **J.A.R.V.I.S HUD on Hermes** (eadmin2) | Iron-Man voice assistant: local faster-whisper STT, ElevenLabs Flash streaming TTS, browser HUD with live activity, transcription, **tool-call approval cards**, embedded Kanban and session browser, agent-summoned "holographic media panels" via a `hud_display` plugin; allowlist proxy keeps the agent key out of the browser | Free OSS (~180 stars) | Mac mini on the LAN, Hermes, ElevenLabs key | [F] https://github.com/eadmin2/jarvis_ai |
| 10 | **n8n "Jarvis" template #8500** | Telegram text or voice to an OpenAI agent; tasks, calendar, Gmail, contacts, expenses via MCPs; optional ElevenLabs | Free in the n8n gallery; Creator Hub earns affiliate commission (30% for the first year per n8n), not template sales | Import JSON, connect credentials, add your own API keys | [F] https://n8n.io/workflows/8500-jarvis-productivity-ai-agent-for-tasks-calendar-email-and-expense-using-mcps/, https://n8n.io/affiliates/ |
| 11 | **Nate Herk: "Ultimate team of AI agents" (n8n)** | Central assistant routes to four sub-agents (email, calendar, contacts, content) | Free template used as a funnel into AI Automation Society: free tier 452k+ members; **AIS+ $129/mo** (3,700+ members, 100+ templates, 6 courses) | n8n cloud or self-host plus keys | [F] Skool post (2025-02); [M] pricing per https://aifunnelinsider.com/ai-automation-society-plus-skool-review/ (checked 2026-09-08) |
| 12 | **Gumroad SOUL.md persona packs** (aiagenttools) | 20 personas $5.99; 100 for $9.90; a 10-item bundle $29 | Gumroad one-time; 10% fee | Download, copy into the OpenClaw workspace | [F listing] https://aiagenttools.gumroad.com/l/jryauv |
| 13 | **awesome-openclaw-agents** (mergisi) | 162–177 SOUL.md agent templates across 19 categories | Free; feeds the seller's hosting (CrewClaw) | Copy the files | [F] https://github.com/mergisi/awesome-openclaw-agents |
| 14 | **"Claude Code: 34 AI agents" playbook** (growthwithalex) | 34 agents across 8 departments, each a build brief you paste into Claude Code | Gumroad | The buyer pastes it and Claude Code builds it | [F listing] https://growthwithalex.gumroad.com/l/claudeagents |
| 15 | **claude-code-templates** (davila7) | 400+ components (agents, commands, hooks, MCPs, settings) with a CLI installer and monitoring | Free OSS | `npx` CLI | [F] https://github.com/davila7/claude-code-templates |
| 16 | **IndyDevDan: Tactical Agentic Coding** | Orchestrator agent that runs CRUD over a fleet of agents with real-time observability; OSS `claude-code-hooks-multi-agent-observability` | **$599 course**; free repos as the funnel | Claude Code plus hooks plus a local dashboard | [F] https://agenticengineer.com/tactical-agentic-coding, https://github.com/disler |
| 17 | **Paperclip: "zero-human company"** | Org chart (CEO agent turns the mission into goals and delegates), **heartbeats** via a DB queue, per-agent budgets with auto-pause, tickets with approval gates, governance; adapters for Claude Code, Codex, OpenClaw, Cursor, Gemini CLI and HTTP bots ("if it can receive a heartbeat, it's hired"); multiple isolated companies per deployment | Free OSS (~97k stars, MIT) | `npx paperclipai@latest onboard` | [F] https://github.com/paperclipai/paperclip |
| 18 | **Gas Town** (Steve Yegge) | 20–30 parallel Claude Code instances; roles Mayor, Polecats (ephemeral workers), Refinery (merge queue), Witness, Deacon; Beads issue memory; written in Go, released 2026-01-01 | Free OSS | Local plus many subscriptions | [F] https://yegge.ai/gastown, https://leosimons.com/2026/01/02/understanding-yegges-gas-town/ |
| 19 | **Claude Flow / Ruflo** (ruvnet) | Swarm and hive-mind modes, up to 64 agents, SQLite shared memory, MCP tool suite | Free OSS | npm CLI on top of Claude Code auth | [F] https://gist.github.com/ruvnet/9b066e77dd2980bfdcc5adf3bc082281 |
| 20 | **Conductor / Vibe Kanban / Claude Squad** | Parallel Claude Code and Codex agents, one git worktree each; Kanban cards; diff-first review | Conductor is a Mac app; others OSS | Local | [F/M] https://nimbalyst.com/blog/best-agent-management-tools-2026/ |
| 21 | **agent-teams-ai** (777genius) | "Build your AI company": agents message each other and review each other's work while you watch a Kanban; 300+ models including free ones | Free OSS | Local | [F] https://github.com/777genius/agent-teams-ai |
| 22 | **TradingAgents** (TauricResearch) | Analysts (fundamental, sentiment, news, technical) run in parallel, then a bull/bear researcher debate, then trader, then risk team; v0.6.0 runs a provider per model tier | Free OSS (~110k stars) | Python plus keys | [F] https://github.com/tauricresearch/tradingagents |
| 23 | **Lindy / Relevance AI: "AI employees"** | Role agents (email triage, SDR, support) built in chat; template marketplace | SaaS: Lindy $29.99–$199.99 per user/mo (2026-10-01); Relevance enterprise-only | Hosted | [M] https://www.eesel.ai/blog/lindy-vs-relevance-ai |
| 24 | **Cole Medin: Second Brain starter / Dynamous** | A Claude Code skill generates a personal PRD for a proactive, persistent assistant: a 24/7 content engine, PPTX/video generation, SOPs, an Obsidian vault; Archon workflow engine | Free repo; paid Dynamous community and workshops | Claude Code locally | [F] https://github.com/coleam00/second-brain-starter, https://x.com/cole_medin/status/2015590510241743038 |
| 25 | **Obsidian plus Claude Code second brain** (Karpathy LLM-wiki pattern; e.g. Noah's free vault) | The vault is just files; a CLAUDE.md explains the structure; the agent reads and writes notes | Free templates (countering $500 courses) | Local | [F] https://noahvnct.substack.com/p/steal-my-ai-second-brain-setup-with |
| 26 | **Home Assistant agents** | HA's built-in MCP server; `mcp-assist`; `ha-realtime-voice-agent` (OpenAI Realtime, speaker routing); fully local Whisper, Piper and llama.cpp voice | Free OSS | HA box | [F] https://github.com/mike-nott/mcp-assist, https://github.com/Underzenith85/ha-realtime-voice-agent |
| 27 | **Hosted OpenClaw** (DigitalOcean 1-Click $12/mo, Cloudflare Moltworker $5/mo plus keys, Hostinger ~$5.99/mo, Agent37 $0.99/mo) | The same agent, without the Mac mini | Hosting fee | One click plus your own key | [F] https://www.digitalocean.com/blog/moltbot-on-digitalocean; [M] prices per https://hostingstep.com/best-openclaw-hosting/ |

Also seen: OpenClaw-focused Skool communities. OpenClaw Lab is $29/mo with tiered early-bird pricing; AI Builders and Claude Club are $97/mo ([M] https://florian-darroman.medium.com/7-best-openclaw-community-and-courses-to-actually-get-help-0b9ca7f0d80b). On reported seller earnings, a CrewClaw blog claims top sellers make $500–$1,500/mo from one-time $5–$49 agent files ([M], self-interested source).

**[I] What the market looks like:**
- **The software is free and the money is in attention.** Courses ($599), communities ($29–$129/mo), done-for-you services (up to €2,700/mo) and hosting are what sell. Template files sell for $5–$49 and earn little. The thing being sold is *setup labour plus trust*, because buyers cannot get the setup running themselves.
- **Every setup is an assembly of the same ~20 capabilities** (Part 3). They differ only in roles, prompts, schedule and UI.
- **The pains buyers pay to escape:**
  - always-on hardware (a Mac mini, Mac Studios, a VPS);
  - API-key wrangling;
  - install friction;
  - the security of skills (ClawHavoc);
  - no updates once a template has been copied;
  - no visibility (which is why Mission Control exists).

---

## PART 3: Decomposition into general capabilities

| ID | General capability | Concrete forms seen |
|---|---|---|
| C1 | **Persistent always-on agent / gateway process** | OpenClaw gateway, Hermes gateway, rho, Mercury, Paperclip server |
| C2 | **Schedules and heartbeats** (cron, proactive check-in that speaks only when needed, zero-token scripted cron) | OpenClaw heartbeat, Hermes cron, rho 30-min, Paperclip heartbeats, Malone crons |
| C3 | **Inbound events / webhooks / triggers** | Malone hooks, pi-queue webhooks, HA state changes, market data |
| C4 | **Sub-agent spawn and delegation** (single, parallel, chain, nested, background) | pi-subagents, Hermes Kanban, Claude Flow, Gas Town polecats |
| C5 | **Multi-agent team structure:** roles, org chart, shared task board, agent-to-agent messaging, debate | Henry's team, Paperclip, Mission Control Kanban, Malone `sessions_send`, TradingAgents debate |
| C6 | **Persistent memory / second brain / self-improvement** (SOUL.md, MEMORY.md, vault, session search, learned skills) | Hermes, rho brain/vault, Obsidian vaults, Cole Medin |
| C7 | **Skills and prompt packages** | SKILL.md, ClawHub, pi skills, Claude plugins |
| C8 | **Extension hooks into the agent loop** (block/modify tool calls, inject context, register tools, commands and renderers) | pi extension API, Claude Code hooks, IndyDevDan observability |
| C9 | **Tool / API / MCP integrations** | MCPorter, pi-mcp-adapter, n8n nodes, HA MCP |
| C10 | **Multi-channel messaging** (Telegram, WhatsApp, Slack, Discord, Signal, iMessage, email), with per-topic agent routing | OpenClaw, Hermes, Mercury, Malone forum topics, n8n Jarvis |
| C11 | **Voice in/out** (STT/TTS, realtime, phone calls) | JARVIS HUD, HA realtime, n8n Telegram voice, Finn's "agent called me" |
| C12 | **Custom dashboards / UI**, often agent-built (Mission Control screens, HUD, generative widgets, Kanban, pixel-art office) | Finn MC, OSS Mission Control, JARVIS HUD, pi-generative-ui, Paperclip UI |
| C13 | **Browser / computer use** | OpenClaw browser, Hermes noVNC, pi-computer-use |
| C14 | **Approvals / guardrails / steer / rollback** | JARVIS approval cards, Paperclip gates, pi-warden, Hermes `/steer` and `/rollback`, Malone lock confirmations |
| C15 | **Model choice, routing, budgets, cost tracking** (per-agent model, local models, failover, budget caps) | Finn (per-agent mixed models), Paperclip budgets, pi-router, TradingAgents per-tier provider |
| C16 | **Attachable compute / remote execution** (VPS, GPU, Mac Studio, laptop "arm", SSH) | Finn's Mac Studios, Hermes brain/arm, pi-tramp, OpenClaw nodes |
| C17 | **Observability / audit / tracing** | Live feed, Langfuse/OTel plugins, Hermes SQLite and Grafana |
| C18 | **Session branching / resume / handoff** | pi session tree, cross-provider handoff |
| C19 | **Distribution: templates, marketplace, one-click install, updates** | ClawHub, pi packages, n8n gallery, Gumroad, Claude plugin marketplaces |
| C20 | **Creator monetisation** (sale, subscription, affiliate, usage rev-share) | Gumroad 10%, ClawHub 15% [M], n8n affiliate 30%, Apify 80% usage share (https://docs.apify.com/academy/actor-marketing-playbook/store-basics/how-actor-monetization-works) |
| C21 | **Multi-user / shared agent / RBAC** (family agent, team Slack, roles) | Hermes family WhatsApp, Mercury RBAC, Paperclip multi-company |
| C22 | **Isolation, sandbox and supply-chain safety** | Mercury bubblewrap, VirusTotal scanning after ClawHavoc, pi "container is the boundary" |
| C23 | **Parallel workspace isolation and merge** (worktrees, merge queue); coding-specific | Gas Town Refinery, Conductor, Vibe Kanban |
| C24 | **Domain data / device connections** (markets, home devices, inboxes) | TradingAgents feeds, HA, Gmail |

---

## PART 4: TinyAssets fit

"TinyAssets today" comes from the platform shape in the brief and the 2026-10-04 pi gap review (on main at 14f5e658). Rows marked **(unverified)** go beyond what the brief states and need a code check before anyone relies on them.

### 4.1 Capability matrix

| Cap | Used by (Part 2 #) | TinyAssets today | General-shape close (no per-setup code) | How TinyAssets does it **better** |
|---|---|---|---|---|
| C1 Always-on process | 1, 3, 5, 8, 17, rho, Mercury | **Missing.** Jail calls die (600 s). Background workflows exist but are not resident agents | A **persistent box** primitive: a user-declared long-lived process in the jail with restart policy and health, owned by the universe (the sealed-box lanes #4319/S7 #4292 already designed this) | **Zero-host 24/7:** no Mac mini, VPS or Docker. Survives the founder's PC being off. This is the #1 pain buyers pay $5–$12/mo and $600 hardware to solve |
| C2 Schedules / heartbeats | 1, 3, 5, 8, 17 | **Partial.** Background workflows and automations exist; whether an *agent heartbeat that decides whether to speak* exists is unverified | Heartbeat = a scheduled workflow whose node is "wake agent with context X; deliver only if it chooses". Add a zero-LLM scripted variant | Budget-aware heartbeats on BYO or free models; nothing to keep alive |
| C3 Inbound events / webhooks | 5, 26, pi-queue | **Partial (unverified)**: HTTP connections exist; a per-universe inbound webhook URL is unverified | Generic receiver URL, then a trigger on a workflow or agent | The same receiver can accept *other users'* events (cross-user composition) |
| C4 Sub-agent spawn | 4, 8, 17–21, pi | **Partial (unverified)** | `ta spawn` (child agent with its own harness files, tools and budget; parallel, chain or background), returning summaries only (the Pi-Agents-Team pattern) | Each child can use a different model or provider; isolation is per child; children run on platform compute, not on your tabs |
| C5 Team structure / board / A2A | 3, 4, 5, 17, 21, 22 | **Partial.** Command centers hold multiple agents and harness files; no shared task board or A2A messaging primitive confirmed | Make the task board **data** (a page or collection) plus `ta message <agent>`. Org charts and roles are just harness files and screens | Teams that span users (your researcher plus my writer) with isolation enforced; a whole team copied with one click |
| C6 Memory / second brain | 8, 24, 25, rho | **Have/partial.** The universe writes its own brain; pages and storage exist | Expose memory as files plus search (`ta search memory`); self-written skills already fit the files model | Memory travels with the command center; a template ships *empty memory slots*, so buyers never inherit the creator's data |
| C7 Skills | 1, 2, 8, 12, 13 | **Have** (skills aligned with pi) | n/a | Skills run in the jail, which removes ClawHavoc-class host compromise |
| C8 Loop extension hooks | pi, 16, guardrail packs | **Missing.** `settings.yaml` is not read at runtime; no `tool_call`/`context` hooks; no user tools | **The pi extension contract:** agent-written extensions register tools and commands and hook `before_agent_start` / `context` / `tool_call` (block/modify) / `tool_result` / `agent_end`; settings read at runtime; extensions ship inside the command-center package | Extensions are sandboxed per universe and can be published and copied; owner-side policy is enforced daemon-side, so a template's hooks cannot escape the jail |
| C9 Tools / API / MCP | 5, 9, 10, 26 | **Partial.** OAuth directory as data, API keys and HTTP connections exist; **MCP attach missing**; connection calls only work as workflow nodes, not mid-turn | `ta <connection op>` mid-turn (D6a in progress) plus **MCP attach as a connection kind**, lazily described (pi's progressive-disclosure argument) | One-click OAuth from the directory rather than editing JSON or env files; credentials stay in the vault and are never copied with a template |
| C10 Multi-channel messaging | 1, 5, 8, 10, Mercury | **Partial.** The bubble and voice exist in-app; Telegram, WhatsApp, Slack and Discord are reachable only via user-built HTTP connections (unverified whether any is packaged) | A channel = connection (bot token or OAuth) plus inbound receiver plus an outbound op; routing (topic to agent) is data in the command center. *Not* hard-coded effectors (per founder rule) | Channel bundles become publishable shapes; one universe can answer on every channel; per-topic routing copies with the template |
| C11 Voice | 3, 9, 10, 26 | **Partial/have.** Voice exists in command centers | Voice as a UI-bridge capability (STT/TTS providers BYO); outbound phone call as a connection op | Voice in *every* command center by default; the HUD effect needs only screens plus a parity bridge |
| C12 Custom dashboards / UI | 3, 4, 9, 17, 20, 21 | **Partial.** Screens are first-class, but the **UI bridge is a fixed, poll-only action list with no `ta` parity** | Bridge = the same capability surface as `ta` (owner-permitted) plus an **event stream** (activity, approvals, tool calls) | Mission Control *is the product*: every command center already has screens, so no separate Next.js app or port forwarding. Agent-built screens are publishable |
| C13 Browser / computer use | 1, 8, pi | **Missing** (browser fallback planned; D5 in flight) | Browser as a jailed capability (headless plus screenshot) and later attachable desktops | Hosted browser with no local Chrome profile at risk |
| C14 Approvals / guardrails | 5, 8, 9, 17, pi | **Partial.** Consent is enforced daemon-side; "Waiting on you" request cards exist | Hook-based gates (C8) plus approval cards delivered over the bridge events and any channel | Approvals reach the bubble, phone or Telegram, wherever the user is; the cross-user floor cannot be weakened by a template |
| C15 Models / routing / budgets | 3, 8, 17, 22 | **Have/partial.** BYO model and OpenRouter free; per-agent model choice exists (D8 per-agent controls in flight); budget caps unverified | Per-agent model in harness files; routing and failover as an extension; budget = the real provider budget only (per memory rule) | **BYO-model means a template runs free for the buyer on day one** (OpenRouter free); no platform LLM markup |
| C16 Attachable compute | 3, 8 (brain/arm), pi-tramp | **Missing.** `connect_compute` registers models only | Compute as a connection: SSH/agent endpoint (VPS, GPU, home Mac) the jail can dispatch to, the analogue of pi's `user_bash` hook and Hermes backends | "Bring your Mac Studio *if you want*": optional, never required, unlike every OpenClaw setup |
| C17 Observability | 4, 16, pi plugins | **Partial (unverified)**: activity feed, D2 activities in flight | Activity events as a stream (same as the C12 bridge events) | Built in, no Langfuse wiring |
| C18 Session branching | pi | **Unverified** | Low priority | — |
| C19 Distribution / install / updates | 2, 10, 12–15, pi | **Partial.** Publish workflow+screen bundles; copy into your own universe on your own compute | Publish the **whole command-center package** (screens, workflows, agents, harness files, extensions, connection *requirements*); install = copy plus a connection checklist; **versioned updates to installed copies** (diff/merge, opt-in) | One-click copy that *runs immediately* on zero-host compute, compared with "download a zip, buy a Mac mini, paste keys". Isolation reviewed by construction, which is the trust problem ClawHub failed |
| C20 Creator revenue | 11, 12, 16, Apify | **Missing** (design-only, TINY share by run-time) | Attribution ledger, then aggregate creator metrics, then payout (founder-gated) | Usage-based recurring revenue (like Apify's 80%) instead of $9 one-off sales; creators are paid when copies *run*, not just once at download |
| C21 Multi-user / shared | 8 (family), Mercury, 17 | **Partial.** Cross-user receivers are **one-way only** | Reply/RPC across users, owner-published readable outputs, and a rule for who pays for another user's run | Cross-user composition: my agent calls your published agent and gets an answer, with isolation intact |
| C22 Isolation / supply chain | 2, Mercury | **Have.** Cross-user isolation is the one invariant, plus the jail | Keep it; add static scanning of published packages | A template can't steal your keys, which is the headline differentiator after ClawHavoc |
| C23 Worktree / merge orchestration | 18, 20 | **Partial**: jailed bash and git in a box | Falls out of C1 + C4 + C16 | — |
| C24 Domain data / devices | 22, 26 | **Partial** via connections | Generic HTTP/OAuth/MCP connections | — |

### 4.2 Capability gaps that block "rebuild anything", in blocking order

1. **C8 extension hooks plus runtime `settings.yaml`.** Without these a designer cannot reproduce pi-style orchestrations: subagent tools, guardrails, routers, memory hooks.
2. **C1 persistent processes.** Without them there are no gateways, resident agents or long-running bots. OpenClaw, Hermes, rho and Mercury are all resident.
3. **C12 UI bridge parity plus events.** Mission Control, the JARVIS HUD and live Kanban views need live events and a full verb surface, not polling a fixed list.
4. **C9 mid-turn `ta` connection calls plus MCP attach.** Almost every setup relies on MCP servers (Malone, n8n Jarvis, Home Assistant).
5. **C10 packaged channel shapes.** Telegram, WhatsApp, Slack and Discord are the front door of most Jarvis setups. They are buildable today only by hand.
6. **C4/C5 spawn and A2A messaging primitives** (`ta spawn`, `ta message`).
7. **C13 browser.**
8. **C16 attachable compute.** This is needed for local-model and GPU setups like Finn's, but it is optional.
9. **C21 two-way cross-user** and **C19 updates to installed copies.**
10. **C20 revenue.** This gates creator acquisition, not rebuilding.

### 4.3 (a) T1-T10 capability checklist for user projects

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

Users choose how complex their projects become. Home Assistant and family-agent examples likewise check optional MCP/local-network and cross-user/member primitives; they are not platform deliverables.

### 4.4 (b) The creator-acquisition angle

**What creators get today:**
- **Gumroad:** 10% fee, a one-time $5–$49 sale, then support burden.
- **Skool:** $29–$129/mo community memberships.
- **n8n:** affiliate commission only.
- **ClawHub:** roughly 15% [M], with the platform's trust damaged by ClawHavoc.
- **Courses:** $599.

**Their buyers' pain** is that the template does not run without hardware, keys and setup skill, so creators sell *setup labour*.

**What would make them publish on TinyAssets instead:**

1. **One-click install that runs immediately for their buyers.** Copy the command center, then a connection checklist (OAuth clicks), then it is live 24/7 on zero-host compute with a free model. This deletes the "it doesn't work on my machine" support load that limits creators today. *(Needs C1, C19, OAuth directory.)*
2. **Recurring revenue tied to usage, not a single sale.** The design is a run-time share of subscription revenue. The usage-share model is proven by Apify (80% to creators).
   - Make it visible on the publish page from day one, even if phase 1 is attribution plus a ledger with no payout.
   - Offer a fiat path as well as TINY. Most of these creators price in USD on Gumroad and Skool. *(C20; legal questions stay with the founder.)*
3. **Updates flow to installed copies.** Creators currently cannot fix or improve a sold zip. Versioned packages with opt-in merge into copies (user edits preserved) turn a template into a product with a changelog. *(C19.)*
4. **Attribution that survives remixing.** A lineage graph (fork of a fork) with credit and revenue split up the chain. This matches the founder's "build it better and share it" loop. Creators stop fearing copies because remixes pay them. *(C19/C20.)*
5. **Creator analytics.** Aggregated, private-by-default metrics: installs, weekly active copies, run-time, retention, remix tree, and connection-checklist drop-off. These are the startup metrics the founder named. *(C17/C20.)*
6. **Trust by construction.** "Templates cannot touch your keys or other users" is a marketing line creators can repeat after ClawHavoc. Add package scanning plus a verified-creator badge. *(C22.)*
7. **Embeddable demos and deep links.** A "Run this in your universe" button for YouTube descriptions and X posts that lands on a live, read-only preview. Their content funnel then converts on TinyAssets, not on Gumroad. *(C19.)*
8. **Keep their community, add the product.** Let creators gate premium command centers to their Skool or Whop members (a membership-check connection, or invite codes), so TinyAssets complements their $129/mo community instead of competing with it. *(General shape: access rules as data.)*
9. **Cross-user composition as a moat.** A creator can publish an *agent service*, for example a trading-signal agent, that other users' agents call with a two-way receiver. Creators earn per call, which is impossible on Gumroad. *(C21.)*
10. **BYO model removes the creator's cost risk.** Buyers' runs never bill the creator, unlike hosting a SaaS version.

**First targets [I]:**
- **OpenClaw and Hermes YouTubers**, who demo the most visual setups and fight the most install pain: Finn, Riley Brown, Malone-style builders.
- **n8n template creators**, who already have productised JSON workflows with Telegram front ends.
- **pi extension authors**, a technical audience that will port their extensions once the C8 contract exists.

### 4.5 (c) Prioritised build list

Ordered by how much of "rebuild anything" each item unblocks, then by shared dependency.

| P | Item | Unblocks | Notes |
|---|---|---|---|
| **P0** | **D6 `ta` mid-turn verbs plus the extension contract.** Agent-written extensions with `registerTool`/`registerCommand` and `before_agent_start`/`context`/`tool_call`(block/modify)/`tool_result`/`agent_end` hooks; `settings.yaml` read at runtime; extensions packaged in the command center | C8, C9, C4, C14; T2, T4, T6, T10 | Already the keystone in the pi gap review. Public API surface and authority, so it needs an OpenSpec change |
| **P0** | **Persistent boxes** (long-lived user processes, restart policy, health, owned by the universe) | C1, C10 gateways, C2 resident heartbeats; T1, T3, T10 | Revive the #4319/S7 #4292 branches. This is the zero-host 24/7 headline |
| **P1** | **UI bridge parity with `ta` plus an event stream** (activity, tool calls, approvals) | C12, C14, C11, C17; T2, T5, T6 | Mission Control and the JARVIS HUD become plain screens |
| **P1** | **MCP attach as a connection kind**, lazily described | C9; T3, T7, T26 | Matches pi's progressive-disclosure argument |
| **P1** | **Channel shapes as publishable bundles** (Telegram, Discord, Slack, WhatsApp, email) built from connection plus receiver plus outbound op; routing as data | C10; T1, T3, T7 | Users build and optionally publish their own channels from these primitives; the platform ships no channel templates |
| **P1** | **`ta spawn` / `ta message`** plus a task board as a data collection | C4, C5; T2, T6, T8, T10 | |
| **P2** | **Publish-the-whole-package plus connection checklist plus versioned updates to installed copies plus lineage** | C19; creator angle 1, 3, 4 | Storage shape, so it needs an OpenSpec change |
| **P2** | **Creator attribution ledger plus aggregate analytics** (no payout) | C20 phase 1; creator angle 2, 5 | Already the safe first phase in the design |
| **P2** | **Browser capability** (D5) | C13 | |
| **P3** | **Two-way cross-user** (reply/RPC, readable outputs, who-pays rule) | C21; T8 variant, creator angle 9 | `connect-cross-user-nodes` change |
| **P3** | **Attachable compute** (SSH/agent endpoint as a connection) | C16; T10 local, HA reserve test | Optional by design |
| **P3** | **Payout rail** (fiat plus TINY), gated by founder and legal | C20 phase 2 | Money, so proposal plus design first |
| **P3** | **Package scanning, verified-creator badge, embeddable "Run in your universe" links** | C22, creator angle 6, 7 | |

**[I] Capability coverage.** T1-T10 checks whether users have the primitives to build their own projects. Publication, second-account copying, host-off execution and attribution exercise general capabilities when users choose them; they do not require the platform to build or ship any setup.

---

## Sources (accessed 2026-10-04)

**pi:**
- https://mariozechner.at/posts/2025-11-30-pi-coding-agent/
- https://pi.dev/packages
- https://raw.githubusercontent.com/badlogic/pi-mono/main/packages/coding-agent/README.md
- https://raw.githubusercontent.com/badlogic/pi-mono/main/packages/coding-agent/docs/extensions.md
- https://github.com/earendil-works/pi
- https://taoofmac.com/space/ai/agentic/pi
- https://awesome-pi.site/extensions/
- https://github.com/mikeyobrien/rho
- https://github.com/Michaelliv/mercury
- https://github.com/KristjanPikhof/Pi-Agents-Team
- https://github.com/skidvis/pi-coordinator
- https://github.com/tmustier/pi-agent-teams
- https://github.com/baryonlabs/pi-agent-harness
- https://github.com/noickare/pi-tmux-agents
- https://pyshine.com/Pi-Mono-Full-Stack-AI-Agent-Toolkit/

**OpenClaw:**
- https://docs.openclaw.ai/start/openclaw
- https://milvus.io/blog/openclaw-formerly-clawdbot-moltbot-explained-a-complete-guide-to-the-autonomous-ai-agent.md
- https://thehackernews.com/2026/02/researchers-find-341-malicious-clawhub.html
- https://crewclaw.com/blog/sell-openclaw-agents-marketplace
- https://metatrends.substack.com/p/clawpilled-meet-your-ai-chief-of
- https://x.com/AlexFinn/status/2028791230713528753
- https://pub.towardsai.net/mission-control-an-orchestration-dashboard-for-openclaw-c3454f959b15
- https://www.dan-malone.com/blog/building-a-multi-agent-ai-team-in-a-telegram-forum
- https://x.com/rileybrown/status/2028233214398206096
- https://x.com/gregisenberg/status/2024247983999521123
- https://aiagenttools.gumroad.com/l/jryauv
- https://github.com/mergisi/awesome-openclaw-agents
- https://www.digitalocean.com/blog/moltbot-on-digitalocean
- https://hostingstep.com/best-openclaw-hosting/
- https://florian-darroman.medium.com/7-best-openclaw-community-and-courses-to-actually-get-help-0b9ca7f0d80b

**Hermes:**
- https://github.com/nousresearch/hermes-agent
- https://hermes-agent.nousresearch.com/docs/user-stories
- https://github.com/eadmin2/jarvis_ai

**n8n and automation platforms:**
- https://n8n.io/workflows/8500-jarvis-productivity-ai-agent-for-tasks-calendar-email-and-expense-using-mcps/
- https://n8n.io/affiliates/
- https://www.skool.com/ai-automation-society/new-video-i-built-the-ultimate-team-of-ai-agents-in-n8n-with-no-code-free-template
- https://aifunnelinsider.com/ai-automation-society-plus-skool-review/

**Claude Code ecosystem:**
- https://growthwithalex.gumroad.com/l/claudeagents
- https://github.com/davila7/claude-code-templates
- https://agenticengineer.com/tactical-agentic-coding
- https://github.com/disler
- https://www.morphllm.com/claude-code-marketplace

**Multi-agent orchestrators:**
- https://github.com/paperclipai/paperclip
- https://yegge.ai/gastown
- https://leosimons.com/2026/01/02/understanding-yegges-gas-town/
- https://gist.github.com/ruvnet/9b066e77dd2980bfdcc5adf3bc082281
- https://nimbalyst.com/blog/best-agent-management-tools-2026/
- https://github.com/777genius/agent-teams-ai
- https://github.com/tauricresearch/tradingagents
- https://www.eesel.ai/blog/lindy-vs-relevance-ai

**Second brain and home automation:**
- https://github.com/coleam00/second-brain-starter
- https://x.com/cole_medin/status/2015590510241743038
- https://noahvnct.substack.com/p/steal-my-ai-second-brain-setup-with
- https://github.com/mike-nott/mcp-assist
- https://github.com/Underzenith85/ha-realtime-voice-agent

**Creator monetisation comparison:**
- https://docs.apify.com/academy/actor-marketing-playbook/store-basics/how-actor-monetization-works
