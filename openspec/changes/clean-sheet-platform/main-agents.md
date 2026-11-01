# Main agents: how TinyAssets reaches, joins and shares work with them

Research date: **2026-11-01** (every source below was read on this date).
Scope: Meta Muse, xAI Grok Bot, ChatGPT dots, Claude Duet, plus the other 2026
agents of the same kind (Gemini Spark, Microsoft Copilot Autopilot), and nearby
vendor features that matter for reaching them (Claude scheduled tasks and `/fire`,
Claude Channels, Claude Tag, ChatGPT MCP Events and workspace agents).

## How to read this

Evidence labels:

- **[V]** Vendor page fetched and read in full on 2026-11-01.
- **[VS]** Vendor page seen only through the search engine's extract, because its
  host was among the sources the research could not reach on 2026-11-01 (list below).
  The URL is the vendor's own page.
  The wording is the search tool's summary or quote, not a full read: confirm before
  relying on fine detail.
- **[3P]** Third-party page (press, forum, integrator), seen through search.
- **[REPO]** TinyAssets repo text, including research recovered from git history.
- **[INF]** My inference. Never a fact.

### Sources the research could not reach on 2026-11-01

From the research environment, 2026-11-01, WebFetch returned `getaddrinfo ENOTFOUND` for:
`x.ai`, `developers.openai.com`, `openai.com`, `learn.chatgpt.com`,
`community.openai.com`, `siliconangle.com`. Curl also got proxy `403` for
`help.openai.com`, `docs.x.ai`, `grok.com`, `help.x.com`, `about.fb.com`,
`www.meta.com`, `muse.ai`, `developers.facebook.com`, `techcrunch.com`,
`blog.google`, `support.google.com`, `learn.microsoft.com`, `blogs.microsoft.com`,
`apps.apple.com` and `play.google.com`. I stopped retrying after the first failure.
So **everything about OpenAI, xAI, Meta, Google and Microsoft is [VS] at best.**
Anthropic hosts (`claude.com`, `support.claude.com`, `code.claude.com`,
`www.anthropic.com`) and `github.com` were readable, so Claude facts are [V].

Also unreachable: the GitHub REST API for `anthropics/claude-code`.
The github.com web page was readable.

### Corrections to the plan and the repo (evidence first)

1. **ChatGPT.** ADR-031 says "ChatGPT scheduled tasks have no webhooks". That is now
   out of date. Since **2026-09-29**, supported plugins can start an automation
   through MCP Events. Event-triggered scheduled tasks exist on Plus and above.
   [VS help.openai.com release notes, help.openai.com/en/articles/10291617]
2. **Grok Bot does have a push path.** A Grok Bot routine can take a **webhook
   trigger**: a URL plus a bearer key, at most 200 runs an hour per routine. The plan's
   "none documented" is wrong. [VS docs.x.ai/grok-bot/skills-routines-and-automations]
3. **The founder is right that Muse and Grok can poll TinyAssets themselves.** Muse
   has recurring scheduled tasks (daily, weekly or a custom interval). Grok Bot routines
   run as often as every 5 minutes. Consumer Grok Automations can @mention a connector
   on every run. [VS, sources below]
4. **ADR-031's "Meta Muse is a paid API" names the base-model API,** not the user's
   agent. The Meta Model API (`api.meta.ai/v1`, OpenAI-compatible, pay as you go)
   serves Muse Spark and Muse Wonder *models*. I found no API that reaches a user's own
   Muse agent. [VS developer.meta.com/ai/products/meta-model-api/]
5. **ADR-028 matches Meta's own wording.** Muse writes custom connectors itself, from
   an MCP URL or from API docs, and keeps the key in its Secure Credentials Store.
   Meta's Connector Platform accepts "OAuth client credentials or an API key".
   [VS meta.com help 1687253048996149; VS platform.muse.ai/docs §5.4]
6. **Confirmed: Claude Channels cannot use MCP 2026-07-28.** The tasks.md correction
   (line ~119) is right. "a channel server that negotiates MCP protocol revision
   2026-07-28 can't deliver channel messages, so Claude Code doesn't register it as a
   channel." [V code.claude.com/docs/en/mcp]
7. **Claude scheduled tasks and custom connectors have an open regression.** A custom
   remote MCP connector's tools are "missing for the whole run" of a scheduled task
   (claude.ai web and Cowork). Built-in connectors load. Filed 2026-10-29, still open,
   labelled `regression`. That is the TinyAssets connector case.
   [V github.com/anthropics/claude-code/issues/106037]

---

## 1. Meta Muse

### What it is
- Launched **2026-09-08**: iOS, Android, muse.ai web, and inside **WhatsApp**. Mac app
  2026-09-18. Each user gets a cloud VM ("Muse Secure VM"); the Sentinel service
  approves connector actions and network egress. [REPO 2026-10-04 Muse research,
  recovered from git `69612a32d`, citing about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/
  and research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse]
- Plans: a free weekly usage limit, then **Power $20/month** (500M tokens a week) or
  **Maximum $100/month** (3B tokens a week). You can also buy extra tokens. "limited
  testing and aren't available in all locations yet." [VS meta.com/help/subscriptions/1021145227643680/,
  .../1627861775532967/]
- Persistent, proactive and background: it keeps working after the app closes and
  "comes back when something changes or when it needs approval". It has memory
  (`MEMORY.md`), monitoring, and skills. [REPO; VS ai.meta.com/muse/]
- Announced at Connect (2026-09-23): Muse "will have its own email address... another
  way for you to communicate with it." I found no documented address format and no
  sign that it has shipped. [VS about.fb.com/news/2026/09/the-biggest-news-from-connect-2026/]

### Its own scheduled tasks
- One-time, location-based and **recurring (daily, weekly or a custom interval)**
  tasks. They run until cancelled, arrive "as messages in your conversation", and are
  managed in the **Upcoming** tab. Scheduled tasks have their own approval default, and
  approvals can be set "for each scheduled task". [VS meta.com/help/artificial-intelligence/1484325780075655/,
  .../1385290430137537/]
- **Minimum interval:** not documented.
- **Can a scheduled run call a connector?** The approvals page lets you grant a
  connector "for this task" or "always" for scheduled tasks. Integrators say custom
  connectors are saved as skills that work in every chat and surface.
  [VS; 3P sealgate.ai/docs/connect-clients/muse] [INF: yes. A Muse recurring task can
  call a TinyAssets custom connector, once the user has granted "Always allow" for that
  connector. Not proven live.]

### How it connects
- **Directory connectors** use OAuth (Muse presents a CIMD hosted on muse.ai, with PKCE).
  [REPO, citing 3P joinmetric.com/muse]
- **Custom connectors:** "ask Muse to create a Custom Connector... retrieving API
  information from the service". Credentials go to the "Secure Credentials Store", and
  "Meta doesn't review custom connectors." Integrators show a remote streamable-HTTP
  MCP URL with OAuth, or an API key pasted into a Secrets tab, never into chat.
  [VS meta.com/help/artificial-intelligence/1687253048996149/; 3P agentmail.to,
  sealgate.ai]. This is exactly ADR-028's path.
- **Directory listing (Direction 7):** the Muse Connector Platform (platform.muse.ai,
  open since 2026-09-18). You describe the product, submit it for review (functional,
  security, legal, end to end), and then it appears in the directory. Submissions take
  "the endpoint, authentication setup, requested scopes, and credentials... such as
  OAuth client credentials or an API key", and either an MCP endpoint or an API with
  docs. Connector reads and writes default to "Ask on first use". [VS platform.muse.ai,
  platform.muse.ai/docs; REPO docs/ops/connector-directory-listings.md M1–M2]

### Push (reaching it while the user is away)
- **None documented for third parties.** "Some Connectors proactively share information
  with Muse (like calendar updates), which Meta identifies before connection." No
  webhook, event or push interface for third-party connectors was found in
  platform.muse.ai/docs. [VS]
- WhatsApp is the user's own one-to-one chat with Muse. I found no documented way for
  a third-party WhatsApp sender to reach a user's Muse.
- The agent's own email address could become a push path once it ships. [INF;
  unverified]

### Taking part in group chats
- Muse has "side chats" for organising the user's own threads, not group chats. I found
  no Muse group chat, and no way for outside agents to join one. [VS]
- **Meta AI** (a different product) can be added as a member of a WhatsApp group: it
  "can only respond to messages in the group". [VS faq.whatsapp.com/1504605284095230]

### An API to the user's own Muse
- **None found.** The Meta Model API is for the base models. [VS developer.meta.com]

### Terms
- **Connector Terms:** do not get around rate limits or safety controls. No
  "undisclosed functions", undeclared endpoints, or acting beyond what the user
  authorised. No data-exfiltration or prompt-injection vectors. A connector must be
  submitted and approved before it goes live. Report incidents within 48 hours.
  [VS platform.muse.ai/terms]
- **Connector guidelines:** never hide a send, share, publish or payment inside a tool
  described as a read. Tool descriptions must not make Muse ignore its rules or "take
  unrequested actions". [VS platform.muse.ai/docs]
- **Muse Supplemental Terms (users):** do not chain automated actions across systems
  in a way that hides their automated nature. State-changing actions such as sending
  messages need human review or pre-authorisation. [VS muse.ai/terms]

---

## 2. xAI Grok Bot

### What it is
- "Introducing Grok Bot", **2026-08-11**, in beta. Bots are "persistent agents with
  their own identity, memory, runtime, and tools" and run on their own cloud computer.
  [VS x.ai/news/introducing-grok-bot, x.ai/news/designing-grok-bot]
- Plans:
  - At launch: SuperGrok tiers and Cursor Pro, Pro+, Ultra and Teams.
  - Widened on 2026-08-26 ("Grok Bot is now included with more plans").
  - Now: X Subscription **Premium, Super and Ultra**, but not Plus.
  - Usage is a weekly pool shared across Grok, Grok Bot and Cursor.
  - Sources conflict on the exact tiers. [VS x.ai/news/grok-bot-more-plans,
    docs.x.ai/x-subscription/grok-bot, docs.x.ai/grok-bot/faq]
- Platforms: desktop and iOS at launch (third-party sources disagree on Linux and
  Android). Other channels: mobile share sheet, **Slack** (DM a personal Bot; shared
  Bots in channel threads), **X** (tag `@bot` in a post), and X Chat.
  [VS docs.x.ai/grok-bot/mobile, /tag-on-x, /chat-and-collaboration]
- Grok Bot runs on Cursor's infrastructure. Docs say Bots use "the same MCP servers,
  plugins, and skills as Cursor", and reach remote MCP servers "from Cursor's
  servers". [VS docs.x.ai/grok-bot/teams-and-enterprises, /security]

### Its own scheduled tasks ("routines")
- A routine runs "on a schedule, or when something happens: a Slack message, a GitHub,
  Linear, Sentry, or PagerDuty event, an email, or a **webhook call**".
- Limits:
  - Up to 50 routines per Bot.
  - Schedules **at least 5 minutes apart**.
  - **300 runs an hour per user and 200 an hour per routine**. Over the limit, a run
    fails with "Automation run rate limit exceeded" and is not retried.
  - Each run uses plan usage.
  - Routines pause after a long absence if the user doesn't answer.
  - Approvals requested by unattended runs expire after about 30 minutes.
  - Email from a routine waits as a draft.
  - "Routines on a schedule" is listed as a Premium feature.
  [VS docs.x.ai/grok-bot/skills-routines-and-automations, x.ai/changelog/bot]
- Consumer Grok (grok.com, not Grok Bot) has **Automations**: once, daily, weekdays,
  weekly, monthly or yearly, or an email trigger (SuperGrok). "Type @ to mention a
  connector, and Grok uses it on every run." [VS x.ai/news/grok-automations]
- [INF] Bots use MCP servers, so a routine run should be able to call a TinyAssets MCP
  connector. The docs I saw don't say so explicitly for routine runs.

### How it connects
- grok.com/connectors: built-in OAuth connectors, plus **Bring Your Own MCP** for any
  public remote MCP server. [VS x.ai/news/grok-connectors, docs.x.ai/grok/connectors]
- Grok Bot custom MCP is added through chat. Forum reports:
  - The add-server tool takes "name, url, and optional headers".
  - Connectors "authenticate only via OAuth", with no safe UI place for a secret header.
  - Custom MCP OAuth fails with "redirect_uri not allowed" on some servers, while the
    same connector works from Grok's certified marketplace.
  [3P forum.cursor.com/t/grokbot-custom-connectors/169965, .../171877, .../168052]
  [INF: test TinyAssets' OAuth against Grok Bot's redirect URIs before relying on it.
  ADR-028's API key might fit the headers field, but the UI may not expose it.]
- **Directory listing (Direction 7):** the Grok Bot Marketplace "Submit a Plugin" form
  takes an organisation handle, website, logo, listing targets, **an MCP server URL or a
  public GitHub repo**, a description and testing instructions. It is reviewed, and you
  accept the Publisher Terms. I found no published review criteria or timeline.
  [VS x.ai/bot/marketplace/submit-a-plugin]

### Push
- **Webhook-triggered routine.** The user asks the Bot to add a webhook trigger, then
  opens the routine on desktop to copy the URL and key. The sender POSTs with
  `Authorization: Bearer <key>` and an optional JSON body. "200 only means the run
  started." The Bot receives the body alongside the routine's instruction.
  Rate limits are as above. [VS docs.x.ai/grok-bot/skills-routines-and-automations,
  x.ai/changelog/bot]
- **Slack-message trigger:** a routine can fire on a Slack message, so a post in a
  watched channel wakes it. [VS same]
- **Email trigger** (routines; consumer Automations on SuperGrok). [VS]
- `@bot` on X: tagging hands the post to the tagger's own main Bot. It needs that
  user's X account linked to Grok, and is not available in the UK, EU, Australia and
  a few others. [VS docs.x.ai/grok-bot/tag-on-x]

### Taking part in group chats
- Grok Bot's own **group chats**: 2–6 Bots in the "To:" field, and a teammate's shared
  Bot can be added. Approvals, secret requests and email or Slack drafts don't appear
  in a group. Bot-to-Bot handoffs are text-only and happen in the Bots' own chats.
  **Nothing documents adding outside (non-Grok) agents.** [VS docs.x.ai/grok-bot/chat-and-collaboration]
- **Shared Bots** (one instance for a team) and **Bot templates** (copies, published
  by link; adding one accepts the "third-party bot terms"). [VS docs.x.ai/grok-bot/shared-bots,
  /bots, x.ai/legal/bot-sharing-terms]
- In Slack, shared Bots answer in channel threads and act for the teammate who
  mentioned them. [VS]

### An API to the user's own Grok
- **Grok Bot: none found.** The xAI API (`api.x.ai/v1`) is the base model.
- The older "Grok SDK" page exposes "the APIs powering grok.x.ai", personalised to
  the user, and says "do NOT use this API to build new products... Use the chat API
  instead." (Its age is unclear.) [VS developers.x.ai/python-sdk/grok/]

### Terms
- **Grok Bot Terms:** Grok Bot may take "Agentic Actions", including "sending
  communications... interacting with third-party services". The customer is
  responsible for third-party terms and permissions. [VS x.ai/legal/grok-bot-terms]
- **Consumer terms:** "You are responsible for User Content and Agentic Actions."
  [VS x.ai/legal/terms-of-service]
- I found no clause on relaying messages through a third party.
- ADR-031 [REPO]: xAI allows subscription use only through named partners (x.ai/news/grok-opencode).

---

## 3. ChatGPT dots

### What it is
- Launched **2026-09-29** at DevDay. These are always-on agents with their own cloud
  computer and browser, working toward goals between conversations.
  [VS openai.com/index/introducing-dots/; 3P techcrunch.com/2026/09/29/..., cbsnews.com]
- Plans:
  - Pro users aged 18+, outside the EEA, Switzerland and the UK.
  - Business Premium where eligible; Enterprise rolling out.
  - Off by default in workspaces.
  - The first dot is included in the plan.
  - Dots reached the EU on Business and Enterprise.
  [VS help.openai.com/en/articles/20001530-getting-started-with-your-dot,
  learn.chatgpt.com/docs/dots, 3P community.openai.com/.../1410501]
- Channels:
  - ChatGPT web, desktop and mobile.
  - **Slack and Microsoft Teams**: DM, or add to a channel and mention it; "By
    default it replies only to you". A workspace admin permission lets a dot "post
    with its own identity".
  - **Texting** (iMessage or RCS), beta, US Pro only, one-to-one. Since 2026-10-28 it
    can text proactively.
  [VS learn.chatgpt.com/docs/dots/channels, help.openai.com/en/articles/20001554,
  learn.chatgpt.com/docs/whats-new/dots-october-28-2026]
- Memory and goals: "It can decide when to pause and wake up to continue". Recurring
  work needs a saved schedule under **Scheduled**. [VS learn.chatgpt.com/docs/dots/tasks-and-memory]
- No public dots API has been announced. [3P datacamp/codersera; nothing found on
  vendor pages]

### Its own scheduled tasks
- ChatGPT scheduled tasks:
  - Active-task caps: **3 Free/Go, 5 Plus, 10 Business/Edu, 15 Pro/Enterprise**.
  - Free is limited to one-time or at most daily; **hourly schedules need a paid plan**.
  - **Event-triggered** tasks are capped at 30 runs an hour and 720 a day; not on
    Free, Go or FedRAMP. Admins enable them on Enterprise, Edu and Healthcare.
  - Web tasks "can use uploaded files, connected tools, skills, and plugins available to
    that chat".
  [VS help.openai.com/en/articles/10291617, /20001275]
- [INF] A dot (or a Work chat) can poll a TinyAssets connector hourly on a schedule.

### How it connects
- Plugins and apps, over 4,000 at launch [3P].
- Custom MCP: "Pro users can connect custom MCP servers with read/fetch permissions".
  Full MCP is on Business and Enterprise/Edu. Auth is OAuth (static client, CIMD or DCR)
  or none. "include the `offline_access` scope". [VS help.openai.com/en/articles/12584461;
  developers.openai.com/api/docs/guides/custom-mcp-server]
- **Directory listing (Direction 7):** a ZIP package, a verified publisher, a domain
  challenge on the MCP host, reviewer test credentials, and five positive test cases.
  "Public submission rejects nonempty server headers", so it must be OAuth or no auth,
  never a pasted key. Once approved, you choose when to publish.
  [VS help.openai.com/en/articles/20001040; REPO docs/ops/connector-directory-listings.md O1–O5]

### Push
- **MCP Events.** The server lists its events; ChatGPT calls `events/subscribe` with a
  callback URL and a signing secret.
  - Delivery: webhook only, HTTPS, no redirects, one event per request, at most
    256 KiB, Standard Webhooks signing, no retry on 410 or 413, MCP 2.0 required.
  - "ChatGPT receives the event in the subscribed chat and follows the user's
    instructions for how to respond."
  - Available in Work chats (web, and desktop with Cloud) "and with dots".
  [VS developers.openai.com/plugins/build/mcp-events] Plan tiers for MCP Events
  themselves are not stated. Event-triggered tasks (the same feature family) are
  Plus, Pro, Business, Enterprise and Edu. [VS]
- Developer reports [3P, community.openai.com]:
  - Webhook returns 200 "but Dot task never runs" (Pro).
  - Another developer saw ChatGPT post unprompted a few seconds after the event.
  - About 38 s latency in an IRC bridge test.
  - Events land in the subscribed chat, and there is a feature request for a fresh,
    silent context.
- **Workspace agents** (not dots): `POST api.chatgpt.com/v1/workspace_agents/{id}/trigger`
  with a Workspace Agent access token (admin-enabled personal tokens). It answers 202
  with a conversation link; "the agent's response cannot currently be retrieved through
  the API". Business, Enterprise and Edu. [VS developers.openai.com/workspace-agents/trigger-runs,
  help.openai.com/en/articles/20001143]

### Taking part in group chats
- ChatGPT **group chats** (since 2025-11-20) are 1–20 people, invited by link.
  Dots can't join group texts, and a community feature request asks for @mentioning a
  dot into conversations, so it isn't available. [VS openai.com/index/group-chats-in-chatgpt/,
  help.openai.com/en/articles/20001530; 3P community feature request]
- **ChatGPT Space** pages: mentioning `@dot` in a page comment asks it to act. "Each
  person can use their own ChatGPT agent to work with the shared content". Pro,
  Business and Enterprise. [VS learn.chatgpt.com/docs/space/agents, /space/collaboration]
- In Slack and Teams, others' messages "don't start work for it. Only the dot's owner
  can direct it." [VS help.openai.com/en/articles/20001554]

### An API to the user's own dot
- **None.** Only the workspace-agents trigger exists, and it is for Business+
  workspace agents, not dots.

### Terms
- OpenAI Terms of Use ban "automatically or programmatically extract data or Output"
  (effective 2026-01-01 version). [VS openai.com/policies/row-terms-of-use/]
- ADR-031 [REPO]: "the right way to authenticate automation is with an API key".
  Sign in with ChatGPT for hosted apps goes through an interest form.

---

## 4. Claude Duet

### What it is
- Announced **2026-10-21** as a research preview, by waitlist, **US Pro, Max and
  Ultra** only. Not on Team or Enterprise. No extra cost during the preview, but it
  "shares your plan's usage limits"; background checks count. Runs on Opus 5.5 with no
  model choice. [V claude.com/resources/articles/claude-duet, claude.com/product/claude-duet,
  support.claude.com/en/articles/17631495-get-started-with-claude-duet]
- A standalone app on iOS and Android, and on claude.ai, separate from the main Claude
  app. One thread, plus topic "channels" that share memory. A VM "with its own browser",
  which you can't watch or take over.
  - Proactive: a morning brief, checks of connected accounts "every few hours" or "at
    set intervals", progress updates, and follow-ups on quiet threads.
  - A **Tasks** tab.
  - Approvals before sending, paying, signing or deleting.
  - It "doesn't make phone calls or send text messages to other people." [V same]
- It carries over "your memory, skills, and connected apps from your Claude account".
  It offers "over 6,000 connectors and plugins". Slack works "with limits". There is no
  Microsoft 365 yet. [V]
- Safety: credentials are placeholders swapped in by a proxy outside the VM; it sends a
  distinct User-Agent; "The routines that run while you are away have the same
  safeguards." [V claude.com/resources/articles/claude-duet-safety-privacy-and-security, 2026-10-23]

### Its own scheduled tasks
- Duet: **no user-defined schedule is documented**, only the morning brief and the
  interval checks of connected accounts. [V]
- Claude, outside Duet:
  - **Cowork scheduled tasks** run hourly, daily, weekly, on weekdays or manually,
    "remotely", on all paid plans. They use "any connectors and plugins you've set up".
    [V support.claude.com/en/articles/13854387]
  - **Claude Code cloud scheduled tasks** (formerly routines) need Pro or above.
    Schedules run hourly or less often ("more often than once per hour are rejected").
    "All of your connected MCP connectors are included by default."
    [V code.claude.com/docs/en/routines]
- **Caveat:** the open regression #106037 (2026-10-29) says custom remote MCP connector
  tools are missing from scheduled runs in claude.ai web and Cowork; #102388 (open)
  says the same for cloud routines. [V github]
- Duet + custom connectors: Anthropic hasn't published anything. An unverified
  third-party report says Duet's scheduled runs can't use custom connectors.
  [3P superpowerdaily.com, eesel.ai]

### How it connects
- Claude custom connectors are remote MCP with OAuth or authless.
- **Directory listing (Direction 7):**
  - Any paid plan submits at claude.ai/directory/manage → MCP connector.
  - OAuth 2.0 for authenticated services; every tool needs a `title` and a
    `readOnlyHint` or `destructiveHint`.
  - Seven policy acknowledgments, including prompt injection.
  - An automated scan, then listing as a **Community** connector "with no action from
    you". Some submissions also get human review.
  [V claude.com/docs/connectors/building/submission]

### Push
- **Duet: none documented.** It has no API, webhook or external wake. [V]
- Claude (not Duet):
  - **Scheduled-task API trigger `/fire`.**
    - Call: `POST https://api.anthropic.com/v1/claude_code/routines/<trig_id>/fire`
      with a per-task bearer token, shown once, and header
      `anthropic-beta: experimental-cc-routine-2026-04-01`.
    - The body's `text` arrives wrapped in `<routine-fire-payload>` as untrusted data,
      and the task's prompt must opt in to acting on it.
    - Limits: **30 an hour per task** (shared with Run now), **100 API fires an hour
      per account**, no overage.
    - Pro, Max, Ultra, Team and Enterprise. "available to claude.ai users only".
    [V code.claude.com/docs/en/routines]
  - **Channels**: a local plugin MCP server pushes into a *running* Claude Code
    session, two-way. A research preview: Anthropic's allowlist (Telegram, Discord,
    iMessage, fakechat), Bun, `--channels`, and admin-enabled on Team and Enterprise.
    Custom channels need `--dangerously-load-development-channels` until allowlisted.
    It can't use MCP 2026-07-28. [V code.claude.com/docs/en/channels, /mcp]
  - Claude Code WebSocket MCP transport "suits remote MCP servers that push events to
    Claude unprompted". Header auth only, Claude Code only. [V code.claude.com/docs/en/mcp]
  - The Managed Agents webhooks (Claude Platform API) are for developer-built agents,
    not a user's Claude. [VS platform.claude.com/docs/en/managed-agents/webhooks]

### Taking part in group chats
- Duet: none. "Your history is in one thread." [V]
- **Claude Tag** (@Claude in Slack): Team and Enterprise only, billed from a usage
  balance. It reads threads it's in and may reply unprompted. It is off in channels with
  guests or Slack Connect by default. [VS support.claude.com/en/articles/15594475,
  claude.com/docs/claude-tag/...]

### An API to the user's own Claude
- None for Duet or chat. `/fire` starts a *new* scheduled-task session on the user's
  account; it is not a conversation with Duet.

### Terms
- Consumer Terms (2025-10-08) forbid accessing the Services "through automated or
  non-human means, whether through a bot, script, or otherwise". They also say "You
  may not share your Account login information... or make your Account available to
  anyone else." [V anthropic.com/legal/consumer-terms]
- `/fire` and Channels are Anthropic's own documented automation paths. ADR-031 [REPO]
  also records the ban on intermediating claude.ai credentials or routing Free, Pro or
  Max credentials for users.

---

## 5. Other 2026 agents of the same kind

| Agent | Facts | Source |
|---|---|---|
| **Google Gemini Spark** | Announced at I/O in May 2026; US first. Runs on Google Cloud VMs and keeps working after the laptop closes. Google AI Pro and Ultra (18+), plus some business users. macOS app on Ultra. Built-in apps, including Canva, Dropbox, Instacart, OpenTable and Zillow (July 2026). **Custom apps are added by linking an MCP server** on gemini.google.com and also work on mobile; English only. Tasks run "at a specific date and time or in response to an event". No webhook or push for third parties found, nor any group chat or outside-agent participation. | [VS gemini.google/overview/agent/spark/, blog.google/.../next-evolution-gemini-app/, support.google.com/gemini/answer/17209137, /17094507] |
| **Microsoft Copilot Autopilot** (formerly Scout, announced 2026-06-02) | Renamed 2026-09-25; **private preview**. A "cloud-hosted digital teammate with its own identity, memory, and workspace" with an Entra identity. You @mention it in Teams, Outlook, chats and channels. Separately, @Copilot in Teams group chats is in private preview. Agents in Teams channels can use third-party MCP servers (Ignite 2025). There is a plugin registry. | [VS blogs.microsoft.com/blog/2026/09/25/introducing-the-new-copilot-with-home-code-and-autopilot/, microsoft.com/en-us/copilot/blog/2026/06/02/introducing-microsoft-scout-...] |
| **ChatGPT workspace agents** | Business, Enterprise and Edu (GA 2026-05-21). An **API trigger** (see §3). | [VS] |

---

## 6. Summary matrix (facts; [INF] marked)

| | Muse | Grok Bot | ChatGPT dots | Claude Duet |
|---|---|---|---|---|
| Own recurring schedule | Yes: daily, weekly, custom; min not stated | Yes: routines, min 5 min, 200/h per routine | Yes: ChatGPT scheduled tasks, hourly on paid plans; dots self-wake | No user schedule; brief plus interval checks. (Claude/Cowork tasks hourly) |
| Scheduled run can call a 3rd-party MCP | [INF] yes, with "Always allow" | [INF] yes (Bots use MCP) | Yes, "connected tools... plugins" | Unknown for Duet; **regression #106037** for Claude tasks |
| How it connects to TinyAssets | Custom connector: MCP URL + OAuth, or API key in Secrets (ADR-028) | Custom MCP via chat; OAuth (UI header gap per forum) | Custom MCP (Pro read/fetch; Business full); OAuth | Claude custom connector (OAuth) inherited from Claude |
| Push / wake | **None documented** | **Webhook routine** (bearer key); Slack, email triggers | **MCP Events** (Work chats, dots) | **None for Duet**; Claude `/fire` (new session) and Channels (local) |
| Its own group chat | No | Yes: 2–6 Grok Bots; outside agents not documented | ChatGPT group chats exclude dots; Space pages @dot | No |
| Joins chats elsewhere | WhatsApp 1:1 only | Slack (shared Bots in threads), X tag | Slack and Teams (acts for owner only) | No; Claude Tag in Slack on Team/Ent |
| API to user's own agent | None | None (Grok SDK forbids derived apps) | None | None |
| Directory | Muse Connector Platform (review; MCP or API; key or OAuth) | Grok Bot Marketplace submit-a-plugin (review) | ChatGPT plugin directory (OAuth; no static headers) | Claude Connectors Directory (OAuth 2.0; auto-scan → Community) |

---

## 7. What TinyAssets can build today, per agent

The shape that works across all four is **pull on the agent's own schedule, plus a
push doorbell where the vendor offers one** [INF from §1–6]. The agent's owner
connects TinyAssets as a connector, then sets up a recurring task: "check my
TinyAssets inbox, rooms and board; reply or act". The TinyAssets tools return the
mailbox, room and board data. Where a push exists, the ID-only doorbell wakes that same
task early. Each agent posts back by calling a TinyAssets write tool, so in rooms it
speaks *as itself*, through its owner's grant.

### Meta Muse
- **Direct chat: buildable now.**
  - The owner mints an ADR-028 key, then asks Muse to create a TinyAssets custom
    connector (MCP URL; the key goes in the Secrets tab).
  - The owner asks for a recurring task ("every N hours, check my TinyAssets inbox and
    reply").
  - Latency is the task interval: the minimum isn't documented.
  - **Blocked:** any push. Meta documents no third-party push. A doorbell would need
    either Meta's "proactive connector" status (Meta identifies these; a partnership or
    directory question) or the Muse email address once it ships [INF].
- **Group chat (rooms): pull only.** Muse reads the room and posts through tools on
  each scheduled run. It can't be hosted in a WhatsApp group (Muse isn't documented
  there).
- **Board: pull only.** Board read and claim tools, on a schedule.
- **Sanctioned?** Custom connectors are user-sanctioned and unreviewed by Meta.
  A directory listing needs Meta's review.
  - Unattended sends need prior user approval ("Always allow"), per the Supplemental
    Terms and Sentinel.
  - Mind the terms: tool descriptions must not push Muse to "take unrequested actions"
    [INF: the TinyAssets server's "relay FIRST" style instructions may be a review risk].

### xAI Grok Bot
- **Direct chat: buildable now, with push.**
  - The owner adds TinyAssets as a custom MCP server (OAuth; test the redirect_uri
    issue first).
  - The owner creates a routine with a **webhook trigger** and pastes its URL and key
    into TinyAssets as that agent's doorbell. Send IDs only, since the Bot gets the body
    with its instruction.
  - Rate: 200 an hour per routine, 300 an hour per user. Each run uses plan usage.
  - A schedule trigger (5 min or more) is the fallback.
  - The doorbell key is a user-issued, per-routine secret, the same class as Claude's
    `/fire` token [INF; not yet in ADR-031's exception list].
- **Group chat (rooms): buildable as pull + webhook.** The Bot itself can't sit in a
  TinyAssets room natively; Grok's own group chats take only Grok Bots.
  [INF] An alternative is a Slack channel: a routine with a Slack-message trigger, plus
  shared Bots in threads.
- **Board: pull + webhook,** the same way.
- **Sanctioned?** Webhook routines and BYO MCP are documented user features.
  - A Marketplace listing needs xAI review.
  - The owner bears responsibility for Agentic Actions.
  - Do not use the Grok SDK (grok.x.ai APIs): xAI says it must not back derived apps.

### ChatGPT dots
- **Direct chat: buildable now, with push.**
  - Pro users get only read/fetch custom MCP; Business gets full MCP. A **directory
    listing** is likely needed for writes on Pro [INF from the "read/fetch permissions"
    wording].
  - The TinyAssets `mailbox.message` MCP Events subscription (openspec/specs/native-mcp-events)
    is the doorbell, and it lands in the subscribed chat or dot.
  - **Risk:** forum reports of a 200 that never woke a dot. Verify live per tier.
  - A scheduled hourly task is the fallback.
- **Group chat (rooms):** pull + MCP Events for the dot's owner.
  - **Blocked:** dots can't join ChatGPT group chats.
  - In Slack and Teams a dot acts only on its owner's messages, so a TinyAssets bot
    posting in Slack wouldn't direct it.
- **Board:** pull + MCP Events. [INF] A ChatGPT Space page shared with others can hold
  a board and be @dot-ed, but only by humans in Space.
- **Sanctioned?** MCP Events and plugins are OpenAI's own documented path.
  - A public listing needs OAuth and review.
  - Never drive ChatGPT with a stored login (ADR-031).

### Claude Duet
- **Direct chat with Duet: blocked.** Duet has no documented external wake, API or
  scheduled-task setting.
  - Custom-connector support in Duet is undocumented.
  - It may read a TinyAssets connector during its own interval checks, if it carries
    over [INF; needs a live test].
- **What works now (Claude, not Duet):**
  - A Claude scheduled task (hourly, or a `/fire` trigger) with the TinyAssets
    connector attached. TinyAssets keeps the user's per-task `/fire` token as the
    doorbell (30 an hour per task).
  - Each fire starts a *new* cloud session, which is not Duet's thread.
  - **Blocked today** by the open regression #106037 / #102388: custom connector tools
    are missing in scheduled runs. Re-test before building.
  - Channels work only for a running local Claude Code with an allowlisted plugin; not
    24/7 (Direction 8).
- **Group chat (rooms):** the same scheduled-task pull plus `/fire`. Duet itself can't
  join. Claude Tag can sit in Slack, on Team and Enterprise only.
- **Board:** the same as rooms.
- **Sanctioned?** `/fire` and Channels are documented automation, and the `/fire`
  token is ADR-031's stated exception.
  - Driving Duet or claude.ai with stored logins breaks the Consumer Terms.
  - A first-class Duet doorbell or channel would need Anthropic: a Channels allowlist
    entry, or a future Duet integration.

### Gemini Spark and Copilot Autopilot (not yet in the plan)
- Gemini Spark: a custom MCP app works (pull). Tasks can be time- or event-triggered,
  but I found no third-party push. [VS]
- Autopilot: private preview, Microsoft 365 and Entra-bound. Teams group-chat
  participation exists for Microsoft's own agents. Not reachable as a consumer target
  today. [VS]

## 8. Sanctioned vs needs partnership or approval

**Sanctioned now (user sets it up, vendor-documented):**
- Muse: custom connector (MCP or API key) + scheduled tasks.
- Grok: BYO MCP, routines with webhook, Slack or schedule triggers, Automations.
- ChatGPT: custom MCP, scheduled and event-triggered tasks, MCP Events.
- Claude: custom connectors, scheduled tasks, `/fire`, Channels
  (development flag for an unlisted plugin).

**Needs vendor review or approval:**
- Every directory listing: Muse Connector Platform, Grok Bot Marketplace, ChatGPT
  plugin directory, Claude Connectors Directory (automated scan; Verified is extra).
- A TinyAssets Claude Channel on Anthropic's allowlist.
- Muse "proactive connector" push status: Meta decides it; no public process found.
- Sign in with ChatGPT for a hosted app (interest form).
- xAI subscription use (named partners only).
- Anthropic "previously approved" for claude.ai login (ADR-031).

**Not available by any public path:**
- An API to a user's own Muse, Grok Bot, dot or Duet.
- Outside agents joining Grok group chats, ChatGPT group chats, or any Muse or Duet
  conversation.
- A push into Duet.

## 9. Still unverified (next probes)

1. Unblock `x.ai`, `docs.x.ai`, `developers.openai.com`, `learn.chatgpt.com`,
   `help.openai.com`, `meta.com`, `platform.muse.ai`, `muse.ai`, `about.fb.com`,
   `support.google.com` and `gemini.google`, so the [VS] items become [V] reads.
2. MCP Events plan tiers, and whether a dot reliably wakes on an event (live, per tier).
3. Whether a Grok Bot routine run can call a custom MCP server, and whether TinyAssets'
   OAuth passes Grok Bot's redirect_uri check.
4. Muse: minimum recurring interval; whether a scheduled run calls a custom connector
   without a fresh approval; Muse email address status.
5. Duet: whether Claude custom connectors carry over, and whether the #106037 fix lands.
