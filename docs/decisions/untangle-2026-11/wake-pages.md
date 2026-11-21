# Vendors' wake pages, re-read, as built (2026-11-21)

`tasks.md` step 2 (d3 §C8, list3 P16): `main-agents.md` could not read OpenAI, xAI, Meta or
Google on 2026-11-01, so the ChatGPT, Grok and Muse records in `platform/contract/wake-kinds.json`
and `apps.json`'s Gemini steps rested on search extracts. The question for each vendor: how a
user's main agent is woken by, or receives events from, a remote MCP server or an outside
trigger with no host online (Direction 8), and the limits on each. Desk research only (Fact 14);
nothing live was touched.

Pages were fetched with curl through the session's proxy (WebFetch failed with
`getaddrinfo ENOTFOUND` on `developers.openai.com` and `help.openai.com`). Where the vendor serves
a Markdown twin (`<page>.md`: OpenAI, `learn.chatgpt.com`, `docs.x.ai`, `code.claude.com`,
`platform.claude.com`) the twin was read; otherwise the HTML as text. Evidence labels are
`main-agents.md`'s: **V** read in full, **VS** seen only through a search engine's extract of the
vendor's own page, **[INF]** inference. Every page below was read **2026-11-21**.

## Could not be read, and what that leaves unknown

| Page | What happened | Unknown as a result |
|---|---|---|
| [Muse reminders and scheduled tasks](https://www.meta.com/help/superintelligence/1484325780075655/) (the old `/help/artificial-intelligence/` URL redirects here) | HTTP 200, but the article body is rendered by script; only its title and `dateModified` 2026-09-08 are in the HTML. Facts are from search extracts (VS). | Muse's minimum recurring interval; whether a scheduled run calls a custom connector without a fresh approval. |
| [Muse connectors](https://www.meta.com/help/superintelligence/1687253048996149/), [Muse email addresses](https://www.meta.com/help/superintelligence/1149305437517250/) | The same: script-rendered, VS only. | Whether mail to a Muse's own `@muse.ai` address starts Muse work by itself. |
| [Grok Bot changelog](https://x.ai/changelog/bot) | HTTP 403. | Any Grok Bot change after the docs below. Dropped from the record's citations: the docs page carries everything it was cited for. |
| [Grok routines help](https://grok.com/help/grok-bot/routines), [plans](https://grok.com/help/grok-bot/plans) | Redirect to the grok.com app shell, no article. | The docs page links here "for more on webhooks", so the webhook URL's host may be described there. |
| [claude-code #106037](https://github.com/anthropics/claude-code/issues/106037), [#102388](https://github.com/anthropics/claude-code/issues/102388) | HTTP 403 (GitHub access for that repository is not enabled in this session). | Whether custom connector tools still go missing in scheduled runs. Its citation keeps its 2026-11-01 read. |
| `modelcontextprotocol/experimental-ext-triggers-events` history | GitHub API refused. | Whether the MCP Events design sketch changed after 2026-11-03. Its citation keeps that read. |

## OpenAI: ChatGPT MCP Events, scheduled tasks, dots

Sources (V): [MCP Events](https://developers.openai.com/plugins/build/mcp-events);
[Scheduled tasks in ChatGPT](https://help.openai.com/en/articles/10291617) ("Updated: 20 days
ago"); [Scheduled tasks (learn)](https://learn.chatgpt.com/docs/automations),
[Meet dots](https://learn.chatgpt.com/docs/dots),
[Tasks and memory](https://learn.chatgpt.com/docs/dots/tasks-and-memory);
[Add custom MCP server](https://developers.openai.com/api/docs/guides/custom-mcp-server),
[Connecting custom MCP servers](https://help.openai.com/en/articles/12584461) ("Updated: 24 days
ago"), [learn index](https://learn.chatgpt.com/llms.txt).

- **Where MCP Events works:** "in Work chats on ChatGPT web, in Work chats in the desktop app with
  Cloud selected, and with dots. Workspace controls for plugins and event-triggered tasks apply."
- **Transports:** Streamable HTTP with `initialize`, or MCP 2.0 HTTP (`2026-07-28`) with
  `server/discover`. The 2026-11-01 note "MCP 2.0 required" no longer holds; the doors advertise
  on both.
- **Delivery** is webhook only: "Polling, streaming, and the draft's `gap` and `terminated` control
  notifications are not supported." HTTPS, no redirects, private addresses blocked, one event per
  request, at most 256 KiB, Standard Webhooks headers plus `X-MCP-Subscription-Id`; transient
  failures are retried, `410` and `413` are not. All match the record.
- **Subscription and verification:** `events/subscribe` with a callback URL and a `whsec_` secret
  whose base64 decodes to 24 to 64 bytes (`wake-records.ts` `WHSEC`); a signed single-use
  challenge echoed back, `-32015` `CallbackEndpointError` with `data.reason` on failure, a 10 s
  example timeout; `ttlMs` omitted is the server's default; a refresh's new secret is dual-signed
  for "a short rotation window". All match.
- **New since 2026-11-03:** "ChatGPT can group separately delivered events into one task run
  according to the task's batching settings." And: "Cache successful verification by
  authenticated principal and callback URL for a bounded period so repeated subscription requests
  do not trigger repeated challenges." TinyAssets verifies each subscribe again by choice
  (`tasks.md`), which costs one extra challenge per refresh (about a day by default) and breaks
  nothing the page states: no change.
- **Plan tiers** (the open question): OpenAI still states none for MCP Events itself. Work is on
  every plan, Free, Go, Plus, Pro, Business, Edu and Enterprise. Event-triggered scheduled tasks
  need Work and "an eligible Plus, Pro, Business, Enterprise, or Edu account, or an eligible
  ChatGPT for Healthcare workspace", not Free, Go or FedRAMP; Enterprise, Edu and Healthcare
  admins must turn on "Allow event-triggered scheduled tasks". The help center names only Gmail,
  Slack and GitHub as event-triggered sources; a third-party plugin's MCP Events land "in the
  subscribed chat". The workspace article says "Custom MCP server connections and full MCP support
  are available for ChatGPT Business and Enterprise/Edu"; the developer guide carries no plan
  gate; the earlier "Pro: read/fetch only" wording is on neither page. Dots: Pro 100/200/500 (18+,
  outside the EEA, UK and Switzerland), Business Premium, Enterprise, Edu and Healthcare; on by
  default in workspaces from 2026-11-18 unless they opt out.
- **Scheduled tasks:** active caps 3 Free/Go, 5 Plus, 10 Business/Edu, 15 Pro/Enterprise. Free runs
  at most daily; "Eligible paid plans support recurring tasks up to once per hour."
  Event-triggered: "up to 30 times per hour and 720 times per day across all of your
  event-triggered tasks. Multiple events may be grouped together." Dots "can decide when to pause
  and wake up to continue work".
- **Webhooks in:** only MCP Events, plus the workspace agents trigger (Business, Enterprise, Edu;
  not dots). **Push to the user:** task notifications by push, email or both; dots reach their
  owner in ChatGPT, Slack, calls and texting (a Pro US beta).

## Anthropic: Claude scheduled tasks and `/fire`

Sources (V): [Cloud scheduled tasks](https://code.claude.com/docs/en/routines);
[Trigger a routine through the API](https://platform.claude.com/docs/en/api/claude-code/routines-fire).

- Plans: Pro, Max, Ultra, Team and Enterprise; `/fire` also needs "Claude Code on the web enabled"
  and is "available to claude.ai users only".
- Limits, unchanged: 30 fires an hour per task (shared with Run now and one-shot re-arms), 100 API
  fires an hour per account; schedules hourly or slower.
- **Over a limit (new):** "the endpoint returns `429 rate_limit_error` with a `Retry-After`
  header." `owner/src/wakes.ts` already honours `429` and `Retry-After` for every record, so this
  answers the live test's owed question.
- **Body (new):** `text` holds at most 65,536 characters; more, a missing or unsupported
  `anthropic-version`, or a paused routine answer `400 invalid_request_error`; unknown fields are
  ignored.
- **Errors (new):** `401` no token or one that does not match the routine; `403` no access; **`404`
  the routine does not exist**; `500` and `503` retry. "Generating a new token revokes the previous
  one." "There is no idempotency key": each success is a new session.
- Beta header `experimental-cc-routine-2026-04-01`; "The two most recent previous header versions
  continue to work." Matches the record.

## xAI: Grok Bot routines

Sources (V; the first was VS): [Skills and routines](https://docs.x.ai/grok-bot/skills-routines-and-automations),
[Use the computer and apps](https://docs.x.ai/grok-bot/computer-and-apps),
[When your Bot takes initiative](https://docs.x.ai/grok-bot/initiative),
[Settings and notifications](https://docs.x.ai/grok-bot/settings-and-notifications),
[Grok Bot on The X Subscription](https://docs.x.ai/x-subscription/grok-bot),
[FAQ](https://docs.x.ai/grok-bot/faq).

- **Webhook in, confirmed as recorded:** the routine's Webhook section shows "POST to" (the URL), a
  "key" sent as `Authorization: Bearer <key>`; the body is optional JSON, and "The Bot receives the
  body with the routine instruction." "A `200` … means Grok Bot accepted the call and started a
  run." "Any other response means a run didn't start, so confirm that the routine isn't paused and
  that the request uses the current key." **The page names neither the URL's host nor the status a
  stale key gets**, so `url.hosts` stays `null` and `token_dead` stays `[]`.
- **Triggers:** a schedule, Slack, GitHub, Linear, Sentry or PagerDuty, an email to the Bot's own
  address, or a webhook; only new activity counts.
- **Limits:** 50 routines per Bot, 20 run records kept per routine, schedules at least 5 minutes
  apart; 300 runs an hour per user and 200 per routine, past either "Automation run rate limit
  exceeded". **New:** "Some runs can also fail with this error because of a limit all Grok Bot
  routines share, even when your own routines run less than that. This is a known issue … isn't
  retried automatically, so … for a webhook, send the call again." Each run spends usage; routines
  may pause after a long absence.
- **Plans:** Premium, Super and Ultra on The X Subscription (not Plus), plus the legacy Cursor and
  SuperGrok paths. The 2026-11-01 "a Premium feature" note for schedules is not on the page.
- **Email:** the owner's Bots share one address; "New mail doesn't start a chat or wake a Bot on its
  own." A routine can watch for mail from given senders or a domain, authenticated mail only by
  default. [INF] A second push path if TinyAssets ever sends mail out; not needed while the webhook
  covers it.
- **Push to the user:** per-Bot notifications on finish or wait.
- **MCP:** "A Bot can also add an MCP server that isn't in the catalog, by URL"; on desktop, Add a
  custom connector takes a Server URL, optional HTTP headers and an OAuth Client ID (and secret).
  The 2026-11-01 forum report of "no UI place for a header" is out of date. OAuth tokens are "held
  on Cursor's connector backend", consistent with `apps.json`.

## Meta: Muse

Sources: [Muse Connector guidelines](https://platform.muse.ai/docs) (**V**, was VS);
[Reminders and scheduled tasks](https://www.meta.com/help/superintelligence/1484325780075655/),
[Connectors](https://www.meta.com/help/superintelligence/1687253048996149/),
[Muse email addresses](https://www.meta.com/help/superintelligence/1149305437517250/) (VS);
[Design the Muse App (Spaces)](https://about.fb.com/news/2026/11/introducing-spaces-customize-muse-app/),
2026-11-06 (V, no wake content).

- **No wake API, webhook or event channel for third-party connectors.** The connector guidelines
  cover tool permissions (reads and writes "Ask on first use"; "Always allow" for tools that are not
  sensitive; sensitive writes ask every time), submission ("OAuth client credentials or an API
  key") and review, and say nothing of push, events or webhooks. The record's `push: null` stands,
  now on a full read.
- **Scheduled tasks (VS):** "daily, weekly, or on a custom interval"; "Recurring tasks continue until
  you cancel them"; managed in Upcoming. The minimum interval is not documented.
- **Own address (VS):** a Muse can have its own `@muse.ai` address; whether mail to it starts work is
  not in the extracts.

## Google: Gemini (no wake record; `apps.json` only)

Sources (V): [Connect & manage custom apps](https://support.google.com/gemini/answer/17209137),
[Manage your tasks & workflows](https://support.google.com/gemini/answer/17094507),
[Create & manage schedules for tasks](https://support.google.com/gemini/answer/17094710).

- **Custom apps:** "Be 18 or over and in the US", a personal Google Account, and "**Have History
  on**. Custom apps are unavailable when this setting is off." `apps.json` said "Keep Activity":
  corrected. Settings > Connected Apps > Add a custom app; "If the server doesn't support Dynamic
  Client Registration … enter your credentials", so DCR is the default, consistent with
  `registration: dcr`.
- **Triggers:** time-based schedules, Gmail monitors and topic monitors only. No third-party, MCP or
  webhook trigger.
- **Limits:** 50 active schedules with AI Pro or Ultra, 10 with Plus or none; 15 tasks at once, and a
  schedule does not run while 15 are running; run times approximate; tasks "keep working even if
  your device is turned off", yet "Schedules will not run as long as task mode is off".

## What changes

1. **The shape stands.** Push exists where the records say: MCP Events for ChatGPT, `/fire` for
   Claude, a webhook routine for Grok. Muse and Gemini have none; their agents pull on their own
   schedules. All four records rest on full reads, except Muse's help-center lines (VS).
2. **Claude:** `max_body_bytes` `null` → `65536` (65,536 characters of `text`; UTF-8 bytes are never
   fewer than characters, so the bound never admits a body the vendor refuses). `token_dead` `[401]`
   → `[401, 404]`: a deleted routine's token never works again either, and the owner's remedy is the
   same, a new task's token. Both fields were already in the record's type, checked by
   `wake-records.ts` (an integer of at least 1024; statuses 400 to 599) and read where the wake is
   built (`wake-kinds.ts` `doorbellRequest`) and answered (`owner/src/wakes.ts`). The
   over-limit `429` needs nothing: the doorbell honours it for every record.
3. **ChatGPT:** the limits line now says what is stated (event-triggered task caps and plans; Work
   chats and dots under workspace controls) and that no MCP Events plan tier is. The verification
   cache the page asks for stays unbuilt by choice.
4. **Grok:** confirmed. The shared rate limit can fail a webhook call under our own limits; that fits
   the doorbell's no-retry rule (the wake settles failed, the next change rings). Host and
   stale-key status stay unpinned.
5. **Muse:** confirmed pull only.

Also corrected where they copied these facts: the Claude limits line on a deleted token,
`limits-audit.md`'s `max_body_bytes` row, `tasks.md` step 2 (ticked) and §10 step 7 (the `429`
question dropped), and one pointer line in `main-agents.md`'s unreachable-sources section.

Out of scope: a pull-only `gemini` record beside Muse's (its agent already registers as `pull`
with no record).

## Owed live (`tasks.md` §10 step 7)

- live: whether a personal paid ChatGPT account (Plus or Pro) can add the custom MCP plugin and
  subscribe to MCP Events, against a Business account (`main-agents.md` §9 item 2); and whether a
  refresh's re-verification is ever refused.
- live: the Grok routine webhook URL's host (pinned in `url.hosts` once seen) and the status a stale
  key gets (into `token_dead`).
- live: Muse's minimum recurring interval, and whether mail to a Muse's own address starts work.
