# 2026-10-04 — Preserve supplied Muse connection research for connection and approval design.

**Founder correction (2026-10-05): capability checklist, not build deliverables.** The platform never builds or ships T1-T10 setups or channel/team templates. Users, starting with the founder's own agent, build these as their own command-center projects, as complex as they like, to exercise the platform's general primitives.

# Meta Muse: every way it connects to other software, and parity for TinyAssets

Researched 2026-10-04 (web search + fetch, sources inline). Evidence labels:

- **[DOC]** documented by Meta (newsroom, help center, Meta research blog) or by a launch partner's own newsroom (Stripe, Tailscale)
- **[PRESS]** reputable press reporting (TechCrunch, Bloomberg/SiliconANGLE, 404 Media, Reuters via others)
- **[3P]** third-party developer docs or blog posts describing their own integration with Muse (SealGate, AgentMail, Metric, Stacktree). Generally reliable for the flow they tested; not Meta-authored
- **[USER]** forum or social posts and SEO aggregators. Unverified
- **[INF]** my inference

I could not fetch The Verge, Wired, Ars or CNN (blocked or HTTP 451). Claims attributed to them come through secondary summaries and are labelled that way.

## 2026-10-05 correction: social connections and founder direction

Re-research supersedes any inference below that directory membership means social
publishing. Muse has **no directory connector for LinkedIn, X, TikTok, YouTube or
Reddit**. Its Facebook, Instagram and Threads connectors **read** those Meta
surfaces; they are not a social publishing integration. [3P]
[Postiz](https://postiz.com/blog/meta-muse-ai-connectors-schedule-social-media-posts),
[PostFast](https://postfa.st/blog/meta-muse-connectors-list).

Social publishing reaches Muse through a custom MCP connector (paste its MCP
link in chat, then approve the sign-in link) or a connector the agent writes
itself. Directory entries, announced partnerships and community recipes must
not be conflated. The cited vendors describe their own integrations; their
aggregator offerings are evidence for the generic flow, not a TinyAssets dependency.

**Founder direction:** copy the generic ladder: directory as data, then a custom
MCP link with OAuth sign-in, then agent-written connectors, then browser fallback.
No per-platform app registration and no third-party aggregators. The owner's
agent builds its social workflow from those primitives; the platform does not
ship a separate integration for each social network.

## 0. Timeline

| Date | Event | Source |
|---|---|---|
| 2026-04 | Muse Spark model announced | [DOC] https://about.fb.com/news/2026/04/introducing-muse-spark-meta-superintelligence-labs/ |
| 2026-07-24 | Meta AI "Muse Spark agents" connect to Google Calendar and Gmail (precursor) | [PRESS] https://www.axios.com/2026/07/24/meta-muse-spark-agents |
| 2026-09-08 | Muse launches: iOS, Android, muse.ai, WhatsApp; Secure VM + Sentinel; Link by Stripe | [DOC] https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/ ; security blog https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse ; [DOC] https://stripe.com/newsroom/news/stripe-helps-meta-muse-shop-with-link |
| 2026-09-16..22 | Outbound phone calls in dogfood; human call-center layer revealed, then rolled back | [PRESS] https://www.404media.co/meta-tests-muse-ai-agent-calls-that-are-actually-made-by-humans-in-a-call-center/ |
| 2026-09-18 | Mac app (files, mail, calendar, notes, messages); Connector Platform opens to developers (muse.ai/platform) | [PRESS] https://techcrunch.com/2026/09/18/metas-muse-hits-mac-letting-the-ai-take-actions-on-your-computer/ ; [DOC] https://muse.ai/platform |
| 2026-09-21 | Amazon blocks Muse's browser agent | [PRESS] https://siliconangle.com/2026/09/21/amazon-blocks-metas-muse-agent-from-shopping-on-users-behalf/ |
| 2026-09-22 | Mac dictation-config zero-day (Patrick Wardle) | [PRESS] https://www.malwarebytes.com/blog/bugs/2026/09/metas-muse-ai-assistant-has-a-zero-day-that-can-turn-it-into-a-mac-backdoor |
| 2026-09-23 | Connect 2026: full Mac computer use; retail/travel/grocery connectors; Notion, Granola, GitHub, Box; Shop Pay + PayPal; Muse email address; glasses + custom wake name; Realtime Avatar; "Muse Charm" device | [DOC] https://about.fb.com/news/2026/09/the-biggest-news-from-connect-2026/ ; [PRESS] https://techcrunch.com/2026/09/23/everything-new-coming-to-metas-ai-agent-muse/ |
| 2026-09-27 | Browser manual takeover reported in testing on the web app | [USER/PRESS-secondary] https://www.progressiverobot.com/2026/09/27/muse-web-browser-manual-control-muse-agent-users/ (cites TestingCatalog) |
| 2026-09-29 | Muse for Small Business (Asana, Box, Canva, Dropbox, Figma, HighLevel, QuickBooks, Klaviyo, Lovable, Notion, Shopify, Slack, Stripe, Zoom, FB/IG business, ad accounts) | [DOC] https://about.fb.com/news/2026/09/introducing-muse-small-business/ |
| 2026-09-30 | Tailscale connector (Muse joins your tailnet as a node) | [DOC] https://tailscale.com/blog/meta-muse-ai-agent-tailscale |

Primary help center index: https://www.meta.com/help/artificial-intelligence/1303670544995562/

## 0.1 Architecture behind every connection method [DOC]

Source: https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse (2026-09-08)

- **Per-user cloud VM ("Muse Secure VM").** The agent runs in a `systemd-nspawn` runtime cell. Root inside the cell is mapped to an unprivileged host user. There is no CAP_SYS_PTRACE and no CAP_NET_ADMIN.
- **Host-side services, outside the cell:**
  - `hatch-safety`: classifiers that inspect what goes into and comes out of model inference
  - `privsep` workers: run connector business logic with scoped privileges
  - `hatch-authd`: credential store, mints surrogate tokens
  - **Sentinel**: "sole permission authority for connector actions and network egress"
  - Postgres
- **Credentials never reach the agent.** Code in the cell only ever sees a "surrogate" token. After Sentinel authorizes a concrete request, it swaps the surrogate for the real credential at egress. Each worker has a credential allowlist: "A calendar worker cannot ask authd for an email credential simply by changing a request parameter."
- **Connector CLIs are thin.** Built-in connector CLIs in the cell parse arguments and pass typed args plus file descriptors over a Unix socket (SO_PEERCRED and peer ACLs) to a sandboxed worker.
- **Every network request is governed at egress.** Sentinel sees L4/L7 data (host, IP, port, method, path) and blocks SSRF to private IPs after DNS resolution. **Taint tracking:** a tool process becomes "tainted" once it reads user data. Tainted processes lose auto-allow and go through approval.
- **Approvals are out-of-band capabilities.** "Sentinel creates a pending approval and execution stops… A dialog is presented to the user directly within the client UI — not via their conversation with Muse." Approvals are "strict capabilities, not conversational suggestions… bound to the particular connector/destination and use case." Sentinel generates a "user-visible purpose" and decides which grant types to offer: "one-time, session-scoped, task-scoped, time-bounded, or perpetual."
- **Read/write split finer than OAuth scopes.** "fine-grained control over exactly which actions the agent can take… beyond the coarse groups that are usually exposed as OAuth scopes."
- **Email connector filtering.** It strips OTPs, password-reset links and magic links, using deterministic filters plus a classifier.
- **Prompt-injection defenses.** All external data is labelled untrusted. A classifier ensemble checks DOM, images, downloaded files, off-task personal-data egress and high-risk form submission.
- **Third-party OAuth tokens are stored "in your VM, not in centralized Meta infrastructure."** A Confidential VM, encrypted with a user-held key, is in limited testing.

---

## 1. Method catalogue

### 1.1 First-party Meta surfaces and account linking

**What it is.** Facebook, Instagram, Threads (and Messenger, per third-party lists) connect automatically if they are in the same Meta Accounts Center as the Muse login. [DOC] https://www.meta.com/help/artificial-intelligence/1687253048996149/

- **Sign-in:** a Meta account, which can be created from FB/IG, email or phone. [DOC] https://www.meta.com/help/artificial-intelligence/1331373868832401/
- **Flow:**
  1. Sign in with Meta.
  2. FB and IG are already connected, with no consent step at connect time beyond the Accounts Center link.
  3. Onboarding asks your name and lets you name the agent.
  4. Muse reads FB/IG to build a profile, then asks to connect Gmail and calendar for "deeper day-to-day help."
  - Source: [3P/USER aggregators, consistent with DOC] https://www.aiagentslibrary.com/blog/how-to-use-meta-muse/ . A hands-on (2026-09-20, via NeoTeo) reports repeated prompts to connect bank accounts, inbox, documents and ID-expiry dates. [PRESS-secondary] https://www.neoteo.com/en/a-meta-muse-hands-on-review-found-task-help-and-data-prompts
- **What it can do:**
  - "turn a recipe reel the person saved on Instagram into a grocery list" [DOC launch post]
  - Small Business: Instagram professional analytics, Facebook Pages, Meta ad accounts, customer messaging [DOC] https://about.fb.com/news/2026/09/introducing-muse-small-business/
- **WhatsApp as a channel.** You can chat with Muse inside WhatsApp, with no app install [DOC launch post]. One user reports a WhatsApp Muse chat shows up read-only inside the Muse app as one of the chats [USER] https://x.com/altryne/status/2097452780395057412 . Third-party comparisons say file support and long-task management are lighter in WhatsApp than in the app [USER] https://aiextracash.com/tools/muse/muse-whatsapp/ . I found no evidence Muse reads your other WhatsApp chats.
- **Glasses ("coming months"):** wake by the custom name you gave the agent, and "act on what you're looking at" (vision). [DOC] https://about.fb.com/news/2026/09/the-biggest-news-from-connect-2026/ ; [PRESS] https://mixed-news.com/en/meta-muse-agent-ai-glasses-connect-2026-email-address/
- **Voice:** voice mode, where "Muse gets work done in the background while you're still talking." Also a "Muse Charm" pocket voice device ("more later this year") and a Realtime Avatar for video chat. [DOC Connect post]
- **Quest:** nothing found.
- **Revoke:** Settings > Connectors > disconnect. Data already pulled may remain in memory until you invoke the "forget" skill. [DOC] https://www.meta.com/help/artificial-intelligence/2225571704857152/

### 1.2 Directory / built-in connectors (OAuth)

**Which services.**
- Launch, per Meta's AI chief and the help center via an aggregator [3P] https://appsformuse.com/ (checked 2026-10-01):
  - Gmail, Outlook, Google Calendar, Google Docs/Drive
  - Plaid (banking), Link by Stripe, OpenTable, Spotify
  - Function Health, Withings, Peloton, Apple Health
  - Tailscale, Messenger/IG/FB/Threads
  - Ticketmaster and Shopify Catalog (reported)
- 2026-09-23 [DOC/PRESS]:
  - Shopping: Walmart, Best Buy, American Eagle, DICK'S, Fanatics, Gap, Michael Kors, Sephora, Ulta, Wayfair
  - Instacart, Expedia
  - Notion, Granola, GitHub, Box
  - Shop Pay, PayPal
- 2026-09-29: the Small Business set (see 1.1).
- [3P] lists Google Contacts, Tasks, Sheets, Slides and Forms too https://www.sprites.ai/muse/connectors ([USER]-grade).

**User flow** [DOC] https://www.meta.com/help/artificial-intelligence/1687253048996149/ :
- **Two entry points.** (a) Say "Connect my Gmail" in chat, or (b) Settings > Connectors > Connect > Continue, then follow the provider sign-in. Aggregators say the in-chat route surfaces the same Connect step inline [3P] https://www.getmusehelp.com/ ([INF]: the provider OAuth page opens in a browser sheet, and the chat continues on return).
- **Access level per connector.** Read-only versus full action, where the service supports it. Email example: "people choose… whether it reads their mail or can also send on their behalf" [DOC launch post].
- **Global permission defaults** [DOC] https://www.meta.com/help/artificial-intelligence/1385290430137537/ : Settings > Permissions has two values.
  - "Ask for some actions" asks before writes and "important read operations."
  - "Always ask" asks before everything.
  - Defaults can be set separately for connectors, scheduled tasks, artifacts, and web access (with a site allowlist).
- **Push-style connectors.** "Some Connectors proactively share information with Muse (like calendar updates), which Meta identifies before connection." [DOC] https://www.meta.com/help/artificial-intelligence/1047255454427887/
- **Limits.**
  - One account per connector. No second Gmail or Outlook. [3P] https://www.usecarly.com/blog/can-meta-muse-connect-to-multiple-gmail-accounts/
  - Email strips OTP and reset links, so the agent cannot log in elsewhere as you through your inbox [DOC].
- **Revoke:** disconnect in Settings. Memory and history may persist [DOC].
- **Connector-side OAuth details** for partners, from a partner's docs [3P] https://www.joinmetric.com/muse :
  - OAuth authorization code with PKCE S256
  - Discovery via `/.well-known/oauth-authorization-server` and `/.well-known/oauth-protected-resource`
  - Muse presents a client ID metadata document (CIMD) hosted on `https://muse.ai`, with static client-ID fallback
  - Redirect URIs `https://agent.meta.ai/api/hatch/oauth/callback` and `https://muse.ai/connect/oauth-callback`
  - A provider may add its own step-up, e.g. Metric asks you to tap "Approve Muse" on your phone

### 1.3 Connector Platform: third-party developers publish to the directory

- **What it is.** Opened 2026-09-18. Form at muse.ai/platform with three steps: describe, submit for review, appear in the directory. Meta does functional, security and legal review plus live end-to-end testing. Stripe Link is available for connector payments. [DOC] https://muse.ai/platform ; [DOC] Stripe newsroom
- **Form fields** [3P] https://stacktr.ee/blog/muse-connector-platform :
  - Overview: name, developer, site, example prompts, 512px icon, "accepts payments?", contacts, privacy policy, ToS
  - Technical: **either a hosted MCP endpoint plus docs, or a raw API endpoint with optional OpenAPI URL plus docs**
  - Auth: API keys / OAuth with PKCE / Other; plus "access requirements"
  - No SDK and no manifest
- **Demand.** "more than 1,500 applications in less than [a] week" [PRESS TechCrunch 09-23]
- **User side.** Approved connectors appear in the directory, some featured. Users invoke them by naming the service in chat.

### 1.4 Custom connectors: user-supplied MCP URL or API, *written by the agent*

**What it is** [DOC]:
- "Muse can also write its own custom connectors for other services you care about if they have their own APIs or CLIs." (security blog)
- Help center: users "request Muse create custom connectors, which involves retrieving API credentials stored in a 'Secure Credentials Store'… Meta doesn't review custom connectors."

**Flow as documented by integrators** [3P, consistent across several]:
1. **Paste an MCP link in chat, then approve its sign-in link.** You ask in chat. SealGate's paste-in prompt: "Create a Custom Connector for a new remote MCP server, then connect to it: Name: sealgate, Transport: remote streamable HTTP, URL: https://mcp.sealgate.ai/mcp, Auth: OAuth". https://sealgate.ai/docs/connect-clients/muse
2. **Muse builds the client.** It writes an MCP client (or an API client from the docs you point it at) on its VM and tests it. https://www.blotato.com/meta-muse
3. **Auth.**
   - OAuth: Muse presents a sign-in link, you authenticate, and the token is bound to you.
   - API key: **"Muse opens a Secrets tab for the key. Paste it there. Don't paste API keys into the chat."** https://www.agentmail.to/blog/give-muse-email-address . SealGate calls it the "secure credential prompt (not in chat)."
4. **Egress approval.** "Muse's request to share information with AgentMail appears in the approval section on the right of the app." You choose one-time or standing. (AgentMail)
5. **Saved as a reusable skill.** It works in new chats and on every surface (app, web, Mac, WhatsApp). (SealGate)

**Other details.**
- **Reach and limits.** Public internet only, same as ChatGPT/Grok connectors. Local MCP is reachable only through Tailscale (1.11). Custom connectors count against the usage meter.
- **Failure handling** [INF from docs]: Muse tests before saving. SealGate documents an API-key-URL fallback when OAuth fails.
- **Manage.** Skills appear in the Activity log [DOC skills article https://www.meta.com/help/artificial-intelligence/2797651547267109/ , which only discusses built-in skills]. The help center does not describe deleting a user-made skill [gap].

### 1.5 Cloud browser agent (with login handoff and takeover)

What it is [DOC] https://www.meta.com/help/artificial-intelligence/2124746764949121/ and the security blog:
- **The browser.** "a web browser inside your chat with Muse, which both you and Muse can control." It is real Chromium behind a virtualization layer, with a broker owning CDP.
- **Browser sub-agent constraints.** It sees an **accessibility-tree snapshot, not raw DOM**. It cannot run JS in the page and devtools are disabled.
- **Uses:** booking, purchases, forms, "keeping an eye on things, like looking for price drops."

**User flow.**
- **Watch, take over, stop.** Tap **"Open browser"** in chat to watch. **"Take control of the browser"** pauses the agent ("the agent is paused and can't act at all"). **"Stop the task"** ends it.
- **Logins.** "we present a custom UI on the client to capture your username and password, route them directly to authd… injected into the browser window at the point of need." The agent never sees the password.
- **Password managers.** 1Password support is "coming" so Muse can use your existing logins [DOC launch post].

**Limits and gaps.**
- **CAPTCHA / 2FA:** not documented. Takeover is the assumed path. OTPs are filtered from email, so the user types codes [3P inference] https://www.progressiverobot.com/2026/09/27/muse-web-browser-manual-control-muse-agent-users/
- **Approvals.** Web access defaults are "Ask for some actions" (data sharing or unfamiliar sites) or "Always ask" (any site). There is a per-site allowlist and the "Allow for this site" grant [DOC approvals article].
- **Site blocking.** Amazon blocked Muse on 2026-09-21: the agent didn't identify itself as an AI and could reach order history. Users see Amazon ToS pop-ups. [PRESS] SiliconANGLE/Bloomberg. Lesson: browser automation against logged-in sites is fragile politically and technically.
- **Web-app takeover.** Reported "coming soon" to muse.ai on 2026-09-27 [PRESS-secondary]. Mobile takeover is documented in the help center.

### 1.6 Desktop app (Mac): files, local apps, full computer use

- **What it is.**
  - 2026-09-18: Mac app with files, Messages, Notes, Reminders, Mail, Calendar "within their native applications" [PRESS TechCrunch]
  - 2026-09-23: "Computer use is now available with Muse for Mac… drive any app on your Mac," and "walk away from your computer and it keeps working" [PRESS]
- **Flow** [DOC] https://www.meta.com/help/artificial-intelligence/1126304576638594/ :
  1. Install the app.
  2. Grant three macOS permissions: **Full Disk Access, Automation, Notifications**. Computer use presumably also needs Accessibility and Screen Recording [INF].
  3. During setup, set each local app to **Off / Read only / Read and interact**.
  - Manage later in Muse Settings > File System Access, or in macOS Privacy & Security.
- **Safety.**
  - Asks before important actions such as email or purchases.
  - Deleted files go to the Trash.
  - "Monitor its actions carefully."
  - Security incident: a local process could rewrite an undocumented dictation-server setting and capture the auth token (2026-09-22) [PRESS Malwarebytes].
- **Windows / Linux desktop:** none found.

### 1.7 Phone / OS-level integrations

- **Android SMS and Apple Health connectors.** Reported as named in the help center, set up in Settings > Connectors, with access managed in device settings. [3P] https://opentools.ai/muse-connectors/android-sms , https://opentools.ai/muse-connectors/apple-health . *I did not see them on the fetched connectors help page, so treat as unverified.*
- **Phone calendar, contacts, smart-home connectors:** mentioned by Tailscale's blog [DOC-partner, no detail].
- **Location-based reminders.** "Get a message… when near a specific location" [DOC] https://www.meta.com/help/artificial-intelligence/1484325780075655/ , which implies the OS location permission.
- **iOS privacy label** lists contacts, precise location, health, financial, photos and email as linked data [DOC-ish] https://apps.apple.com/us/app/muse-from-meta/id6760173601 . iPhone only, iOS 18+. No Siri, Shortcuts or widgets mentioned.
- **Outbound phone calls to businesses (beta, dogfood).** "calls a business on your behalf, handles the conversation, completes your request, and reports back with a transcript and a summary." Some calls were secretly placed by human contractors; the feature was rolled back pending disclosures. [PRESS] 404 Media / Reuters. **Not a shipped capability.**
- **Notifications.** Continues after the app closes and notifies on completion or when approval is needed [DOC launch + App Store].

### 1.8 Files, uploads, artifacts, cloud drives

- **Uploads and generated files ("artifacts")** [DOC] https://www.meta.com/help/artificial-intelligence/2074655449783957/ :
  - Types: PDF, Word, XLSX/CSV, images, code, interactive web apps
  - Saved to the workspace (the VM). You can "inspect, edit and download these files freely, including Muse's memory" (`MEMORY.md`)
  - Artifacts are not shared externally unless you turn sharing on or approve the request
- **Cloud drives:** Google Drive/Docs (launch); Box (09-23); Dropbox (Small Business).

### 1.9 Payments and purchases

**Methods** [DOC] https://www.meta.com/help/artificial-intelligence/1436362127544482/ ; Stripe newsroom 2026-09-08:
- **Link by Stripe** (recommended). At Link merchants it is instant checkout with the saved method. Elsewhere, Link issues a **single-use virtual card scoped to merchant, amount and a short validity window**.
- **Saved cards on a site,** via credentials in the Secure Credential Store. Muse detects the checkout page and prompts "with the exact details of the purchase every time."
- **Added 09-23:** Shop Pay and PayPal.

**Confirmation.** "For every purchase, consumers are asked to approve the transaction total directly in the chat interface, and Muse never sees their underlying payment details." The help center: Muse "will always ask for your approval prior to completing a purchase." You must verify the merchant's terms, and you are "responsible for all transactions."

**Hands-on reports.**
- Bakery checkout "paused for final approval" (NeoTeo hands-on).
- A Verge test reportedly bought items but removed other cart items first; bulk email deletion erased thousands of messages after it got permission [PRESS-secondary, via aggregator search summaries; not verified directly].

### 1.10 Agent's own identity: email address

- **Announced 09-23.** "Muse will have its own email address that it can use to get things done, or even just as another way for you to communicate with it." CC it on threads or forward mail to it. [DOC Connect post; PRESS TechCrunch]. Format and timing not disclosed.
- **Today's workaround** [3P]: AgentMail custom connector (API key in the Secrets tab, then an egress approval, then Muse creates its own inbox). https://www.agentmail.to/blog/give-muse-email-address

### 1.11 Private networks / self-hosted services (Tailscale)

[DOC-partner] https://tailscale.com/blog/meta-muse-ai-agent-tailscale (2026-09-30):
- **Connect.** Log in with your identity provider and Muse joins your tailnet as **its own node**. It makes outbound connections only.
- **Approval.** "Requires explicit confirmation the first time it connects to any tailnet device." Grants are one-time or standing.
- **Demo.** Listed a directory, checked podman containers, ran updates on a Raspberry Pi.
- **Isolation.** Tailscale ACLs apply on top of Muse's own controls. Revocable at any time.
- **Why it matters.** This is Muse's general escape hatch for *local* MCP servers, home servers, NAS devices and dev boxes, with no per-service code.

### 1.12 Agent-to-agent / business agents

- **Not a documented Muse feature.** It is an emergent pattern: Muse sends messages and fills forms, while >1M businesses run Meta Business Agent on WhatsApp/Messenger, so "agent talking to agent, with the humans stepping in only to approve." [3P/USER commentary] https://chatmaxima.com/blog/meta-muse-ai-agent-businesses-whatsapp-messenger/
- **No A2A protocol announced.** [INF]

### 1.13 Proactive / background work

[DOC launch post, reminders article, approvals article]:
- **Long tasks keep running after the app closes.** Muse "comes back when something changes or when it needs approval."
- **Scheduled work.** One-time, recurring (daily, weekly or custom) and location-based reminders. Delivered "as messages in your conversation." Managed in Assistant icon > **Upcoming** tab, or by chat. Recurring tasks run until cancelled.
- **Monitoring.** Watches the web for things like price drops, and gets connector pushes such as calendar updates.
- **Unprompted suggestions** from memory.
- **Separate approval default** for scheduled tasks.

### 1.14 Permissions, oversight, audit

[DOC approvals article https://www.meta.com/help/artificial-intelligence/1385290430137537/ + security blog]:
- **Grants:** **Allow once / Allow for this task / Allow for this site / Always allow (connector) / Deny**. Sentinel also supports session-scoped and time-bounded grants.
- **Presentation.** Grants appear in a dedicated approval UI outside the chat transcript: "approval section on the right of the app" on desktop/web (AgentMail). Each comes with a Sentinel-written purpose and **Task details** to read before granting.
- **Activity surfaces:**
  - **Activity log** (chronological, includes skills used)
  - **Upcoming**
  - **System Files**
  - The avatar shows live status
- **Undo / stop** some actions in chat. Disconnect connectors. Reset Muse (irreversible). Forget skill. Training opt-out.

---

## 2. Smoothness patterns (what makes it feel seamless)

1. **Zero-step first connection.** Identity-provider accounts (FB/IG) are pre-connected. Onboarding *uses* them immediately to build a profile, then asks for the next connection in context. The first value arrives before the user configures anything.
2. **One verb, two doors.** "Connect my Gmail" in chat and Settings > Connectors run the same flow. Users never need to know which door exists.
3. **No settings form for custom integrations.** The user gives a name plus an MCP URL or API docs. The *agent* writes, tests and saves the client as a reusable skill. Custom connectors are conversation-native, not admin-native.
4. **Secrets never enter the transcript.** API keys go in a "Secrets tab" or "secure credential prompt," and website passwords go in a native credential capture UI. Only surrogates exist inside the agent's sandbox, and the real value is injected at egress. The user is told explicitly "don't paste keys in chat."
5. **Approvals are out-of-band, typed and scoped.** They sit in a separate pane or dialog, not in chat text, so a scam page can't fake them. Each has a machine-written purpose, details on demand and a *menu of durations* (once / task / site / always / deny). Execution suspends on a pending approval and resumes automatically on grant.
6. **Ask only when it matters.** Reads, previously allowed actions and low-risk clean (untainted) egress pass silently. Global defaults are two knobs ("Ask for some" / "Always ask") per surface type.
7. **Read vs write per connector, finer than OAuth scopes.**
8. **Work continues off-screen.** Close the app and you get a notification when something changes or approval is needed. Voice mode runs work *while you talk*.
9. **Watchable, interruptible browser in the chat.** Open browser, Take control (the agent is frozen), Stop. Credentials are injected and never visible.
10. **Money is approved by exact total.** Single-use cards are scoped to merchant, amount and time; the agent never sees card numbers.
11. **Every surface shares one agent and one skill set.** App, web, Mac, WhatsApp and glasses all have the same connectors after one setup.
12. **Network-level escape hatch.** One Tailscale connector reaches every private service, with first-contact approval per device.
13. **Recoverability.** Deleted files go to the Trash. Undo and stop are available in chat. Memory is a plain editable `MEMORY.md`.

Rough edges (for honesty):
- One account per connector.
- No documented CAPTCHA/2FA story.
- Web takeover lagged mobile.
- Amazon blocked browser shopping.
- Bulk destructive actions are possible after a single broad grant (Verge, secondary).
- Mac auth-token exposure.
- Phone calls paused over undisclosed human callers.
- Aggressive prompts to connect sensitive data (NeoTeo).

---

## 3. Parity matrix

TinyAssets today, per the 2026-10-04 audit supplied by the caller (not re-verified by me against code):
- inline single-action approval cards
- inline first-run model-connect card
- OAuth providers as data (Google only)
- API-key form plus generic HTTP connections
- `ta` CLI to call connections mid-turn, and the agent can write extensions
- jailed bash with fetch and egress
- no MCP attach, no browser/computer use, no phone/OS integration
- four separate connect forms, and a requests side panel being removed

Constraints: no per-platform code; BYO model; cross-user isolation is the only invariant.

| # | Muse capability | TinyAssets today | General-shape way to close it (no per-platform code) |
|---|---|---|---|
| 1 | Connect from chat ("connect my X") **and** from settings, same flow | **Partial.** Inline model-connect card exists, but 4 separate forms | **One `connect` card type** rendered inline by the agent (via `ta connect <target>`) and from settings. It dispatches on *auth shape*, not platform: oauth / api_key / mcp / basic / none. Delete the 4 forms. |
| 2 | Directory connectors with OAuth | **Partial.** OAuth providers as data, Google only | Keep providers as data rows (auth URL, token URL, scopes, read/write action map). Add rows, not code. Add **auto-discovery**: if a service publishes OAuth AS metadata (RFC 8414) or MCP PRM, no row is needed. |
| 3 | Remote MCP custom connector (URL + OAuth DCR/CIMD/PKCE, or bearer) | **Missing** | **MCP client attach** as a connection type. Streamable HTTP; OAuth 2.1 + PKCE; DCR **and** a CIMD document hosted on our domain; static-client fallback; bearer/API-key header. Tools surface to the agent via `ta`, so any BYO model can call them. Highest-leverage single item, since every Muse directory partner already ships an MCP server. |
| 4 | Agent writes its own connector from API docs/OpenAPI, tests, saves as a reusable skill | **Partial.** Agent can write extensions and call HTTP connections | Formalise the "extension = saved connector": agent writes it, runs a self-test, persists it per-user, and it is listed and revocable in the same connections list. Accept an OpenAPI URL as input. Credential slots are declared by the extension, never inlined. |
| 5 | Secrets never in transcript; surrogate tokens injected at egress | **Partial/unknown.** API-key form exists; jailed bash has egress | **Secret-entry card** (out-of-band, not a chat message) plus an **egress broker** that substitutes surrogate tokens for real credentials on matching host/path. The sandbox never holds raw secrets. Per-connection credential allowlist (connection A's code can't fetch B's secret). |
| 6 | Out-of-band approvals with durations (once / task / site / always / deny), purpose text, suspend-resume | **Partial.** Single-action inline cards; side panel being removed | Keep cards bound to one action, but add a **grant-duration menu** and a **policy record** (connection × action-class × destination × scope × expiry) that later checks consult. Execution must suspend on pending and resume on grant with no user re-prompt. Render approvals in a surface the model cannot write into (not plain chat text). Don't lose an "approvals inbox" for background runs when the side panel goes. |
| 7 | Read vs write per connection; global "ask for some / always ask" defaults per surface type | **Missing** (as far as the audit says) | Tag each action (from the provider row, MCP tool annotations `readOnlyHint`/`destructiveHint`, or HTTP method) as read/write/destructive/spend. Two global knobs per surface (connections, web, scheduled, sharing). |
| 8 | Egress governance with taint (auto-allow clean reads; approval once user data is involved) | **Partial.** Jailed egress exists | Extend the egress proxy with per-destination allowlist, SSRF guard, and a "tainted" flag on processes that read user data. That one shape covers web fetch, connections and extensions. |
| 9 | Cloud browser in chat: watch / take control / stop; credential capture and injection | **Missing** | **Per-user headless Chromium** in the jail, exposed as a tool (a11y-tree snapshot plus actions) with a **live view stream and takeover** in the client. Login uses the secret-entry card, injected by the broker. This is the universal fallback for any service with no API: one shape, no per-site code. Expect site blocks (Amazon precedent): send an honest agent UA, and prefer APIs/MCP when available. |
| 10 | CAPTCHA / 2FA handoff | **Missing** (Muse is weak here too) | Takeover covers it. Add an "agent needs you" notification that deep-links to the live browser. |
| 11 | Desktop app control (files, local apps, full computer use) | **Missing** | **Companion device node.** A small user-installed agent (desktop first) that registers as a user-owned *device connection* and exposes local capabilities as a **local MCP server** (files, run command, screenshot/click). It is reached through the same MCP-attach path over an outbound tunnel. Per-capability Off / Read / Read+interact. One shape for Mac, Windows and Linux. |
| 12 | Phone/OS (SMS, contacts, health, location reminders, notifications) | **Missing** | The same device-node shape on Android/iOS: the mobile app exposes OS capabilities as MCP tools, gated by OS permissions and the same Off/Read/Interact toggles. Push notifications for approvals and completion are table stakes even before that. |
| 13 | Private network reach (Tailscale node) | **Missing** | Generic **"network attach"**: let the user's sandbox join *their* overlay network (WireGuard/Tailscale auth key as a connection secret), outbound only, first-contact approval per host. Same isolation invariant: the per-user jail joins only that user's network. Also the answer for "local MCP server." |
| 14 | Agent's own email address / inbound channel (CC/forward to agent) | **Missing** (unknown) | Generic **inbound address per universe** (email plus webhook URL) that lands as an event and can trigger a turn. Outbound sending reuses connections. No vendor code; any inbound-mail provider sits behind it. |
| 15 | Messaging-app front door (WhatsApp) | **Missing** (we have web/app; MCP connector into Claude/ChatGPT) | "Channels are user-built": the inbound-event plus outbound-send shape lets a user wire any messaging bridge as a connection. We already have the chatbot-connector front door, which is our analogue. |
| 16 | Payments with exact-total approval and scoped single-use cards | **Missing** | **Spend action class**: any action tagged "spend" *always* raises an approval card showing merchant + exact total + currency, never grantable as "always." Card issuance is a provider-as-data row (any agentic-card issuer) whose credential the broker injects. Budget caps per user. |
| 17 | Background long-running tasks that pause for approval and notify | **Partial** (turns run until finished; requests panel being removed) | Pending approvals must be durable and reachable outside the open chat: an approvals list plus push/email notification. The run resumes on grant. |
| 18 | Scheduled / recurring / location reminders; monitoring (price drops, connector pushes) | **Partial/unknown** | Generic **trigger** shape: cron, interval, webhook/inbound event, connection push (MCP notifications / provider webhooks), poll-and-diff. All feed a turn. Show them in an "Upcoming" list. |
| 19 | Activity log, task details, undo/stop, disconnect, forget | **Partial/unknown** | One per-user activity ledger of every connection call, egress request and approval (with grant scope). Disconnect means revoke the token plus delete grants. "Forget" means a memory-scrub skill. Soft-delete (trash) for agent-deleted files. |
| 20 | Same connectors on every surface after one setup | **Have** (connections are per-universe, CLI-reachable) [INF] | Keep connections bound to the user/universe, never to a client surface. |
| 21 | Multi-account per service | Muse **lacks** this | Allow N connections per provider, labelled. Cheap differentiation. |
| 22 | Developer-submitted directory | **Missing** | A directory is just shared provider/MCP rows anyone can publish, with user consent at install. That fits "democratized commons," and needs no review queue to start. |
| 23 | Vision / glasses / voice-while-working | **Missing** | Out of scope for connectivity. A voice channel is already planned. |
| 24 | Outbound phone calls | Muse **paused** this | Not a parity requirement today. Later it is a voice-provider connection (data row) plus disclosure. |

---

## 4. Recommended build order for parity

Ordered by leverage per unit of work, and by what unblocks later rows.

1. **Unified connect card + one connections list (rows 1, 2, 20, 21).** Collapse the four forms into one card that dispatches on auth shape (oauth / api_key / mcp / basic). The agent can raise it mid-turn (`ta connect`) and the same card appears in settings. The original request resumes automatically after connect. Allow multiple accounts per provider. *Smoothness win with the least new machinery.*
2. **MCP attach (row 3).** Remote streamable-HTTP MCP client with OAuth 2.1/PKCE, DCR plus a CIMD doc on our domain, static and bearer fallback, and tools exposed through `ta`. That instantly reaches every service that built for Muse, Claude or ChatGPT, with zero per-platform code.
3. **Secret-entry card + egress broker with surrogate injection (rows 5, 8).** This is the security foundation that makes 2, 4 and 6 safe under BYO models (Muse's Sentinel/authd split, enforced outside the model). Do it alongside 2, not after.
4. **Scoped grants + read/write/spend action classes + durable approvals inbox with push (rows 6, 7, 16-shell, 17).** Add the duration menu (once / task / site / always / deny), suspend-resume, and two global knobs. Do this before the requests panel is fully removed, so background runs still have somewhere to ask.
5. **Agent-authored connectors as first-class saved, self-tested, revocable extensions (row 4)** from an OpenAPI URL or docs. We are already close; formalise the persistence and the listing.
6. **Triggers + activity ledger (rows 18, 19).** Cron, inbound webhook/email address (row 14), connection push, poll-diff. Add an "Upcoming" view and a per-user ledger of calls, egress and grants.
7. **Cloud browser with live view, takeover and credential injection (rows 9, 10).** The universal fallback for API-less services. It is heavier (per-user Chromium in the jail, streaming), so it comes after the cheaper API/MCP paths cover the bulk.
8. **Network attach (row 13).** User's overlay-network key as a connection; reaches local MCP servers and home services with no device app.
9. **Device node (rows 11, 12).** A desktop companion exposing local capabilities as a local MCP server over an outbound tunnel; then the same on mobile (SMS, contacts, location, notifications). Reuses MCP attach (2) and grants (4) entirely.
10. **Spend rail (row 16 full).** Card-issuer provider rows plus the always-ask spend card with exact total and budget caps. **Shared commons directory (row 22).**

Not pursuing for parity: phone calls (Muse paused them), glasses/avatar, Amazon-style logged-in scraping as a primary path.

Caveats:
- The TinyAssets state is the caller's 2026-10-04 audit, not re-verified against code.
- The Android SMS and Apple Health connectors, The Verge hands-on details, and the WhatsApp read-only mirror are unverified.
- The help center's skills article does not cover user-made skills; custom-connector-as-skill rests on [3P] integrator docs plus Meta's "Muse can write its own custom connectors."
