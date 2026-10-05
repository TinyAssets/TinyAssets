> 2026-10-04 audit at main `9e96ff9595`; the lanes below are the work plan.

# Muse-style bubble + pi.dev harness: gap audit and build plan

**Audited:** `origin/main` at `9e96ff9595`, 2026-10-04. I worked read-only, from a detached worktree. Line numbers refer to that sha.
**Requirements audited (founder, 2026-10-04):**
1. Remove the requests panel; requests are handled in chat.
2. Connect anything smoothly in chat lines.
3. Make soul.md and memory accessible and editable, starting clean.
4. Give command-center designers pi.dev-like control of the harness.

---

## A. Meta Muse: what is documented

Muse launched in the US on 2026-09-08, on web, iOS, Android and WhatsApp, with a Mac app from 09-18. Each user gets a dedicated cloud Linux VM with its own Chromium. A separate "Sentinel" policy agent approves every network call and every connector action.

Sources: [S1](https://betanews.com/article/meta-muse-ai-agent-launch/), [S2](https://axios.com/2026/09/08/meta-debuts-muse-personal-ai-agent), [S3](https://about.fb.com/news/2026/09/introducing-muse), [S4](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse).

Labels used below: **FACT** means Meta's own pages or several outlets agree. **REPORTED** means one secondary source. **INFERENCE** is my own reading.

### Connections
- **FACT: you connect by saying it in chat** ("Connect my Gmail") or through Settings > Connectors. [S5](https://www.meta.com/help/artificial-intelligence/1687253048996149/), [S6](https://saascrmreview.com/how-to-use-meta-muse/)
- **FACT: prebuilt connectors** cover Google Workspace, Outlook, Spotify, OpenTable, Plaid and Stripe Link, among others. Notion, GitHub and Instacart were added after launch.
- **FACT: scope is chosen per connector**, for example read-only versus read+send. Connectors that share information proactively are flagged before you connect.
- **FACT: no prebuilt connector?** You ask Muse to "create a Custom Connector" for any service with an API or CLI. Muse fetches the docs, and keys go into a Secure Credentials Store. Meta warns that it "doesn't review custom connectors". [S5], [S4]
- **REPORTED: MCP is not a user-facing setting.** Muse writes its own MCP client on its VM, tests it, and saves it as a reusable *skill*. [S8](https://parallel.ai/articles/meta-muse-custom-integrations.md)
- **REPORTED: keys never go through chat.** A "secure entry flow" sends them to the VM's credential daemon, and Sentinel swaps them into requests at the network boundary.
- **FACT: browser fallback.** For sites with no API, a client UI captures the username and password straight into `authd`. The agent only ever sees surrogate tokens. [S4]
- **INFERENCE: no "Connect" card is documented.** I found no source describing a rich Connect card inside the chat line. The chat route is a text request that hands off to OAuth or the secure-entry UI.

### Approvals
- **FACT: approval dialogs are not chat messages.** "A dialog is presented to the user directly within the client UI — not via their conversation with Muse." Sentinel sends it, and it describes the exact action. [S4]
- **FACT: approvals are bound to one action.** A purchase shows the item and total with accept/reject. [S10](https://xenospectrum.com/en/meta-muse-address-approval-scope/)
- **FACT: scopes** are one-time, session, task, time-bounded or perpetual. Defaults are conservative. Read-only and already-authorized actions run without interrupting you.
- **REPORTED: there is no pending-approval inbox.** There is an Activity feed / audit log of what Muse did and plans to do. [S9](https://www.bitdoze.com/md/muse-ai-meta-agent.md)
- **Implication for us:** the founder wants approvals "in chat like Muse". What Muse actually does is put a *protected, policy-layer* dialog next to the chat. The agent does not author it. Our #4449 InlineApprovals card, which renders from the server envelope, already matches that spirit. The remaining gap is placement and coverage, not the security model.

### Soul and memory
- **FACT: three plain-markdown files.** Assistant icon > **Identity** shows `Memory.md` ("information you share"), `Soul.md` ("core truths, boundaries and personality") and `Identity.md` ("name, creature, vibe"). Users "can inspect, edit and download these files freely". [S6], [S4]
- **REPORTED: web layout.** A right rail shows the avatar plus two "access with care" cards (Soul, Memory), each with an **Edit** button. You can also edit by chat ("what have you saved?", "forget X"). [S9], [S13](https://youmind.com/ko-KR/landing/x-viral-articles/muse-ai-beginner-tutorial)
- **REPORTED: OpenClaw lineage.** Underneath sit SOUL.md, MEMORY.md, USER.md, IDENTITY.md, AGENTS.md and BOOTSTRAP.md, but only three are shown to users. [S14](https://wallstreetcn.com/articles/3782468), [S15](https://explainx.ai/blog/what-is-soul-md-meta-muse-persona-file-2026)
- **Why it reads as clean:** it shows few files, in plain words, with friendly fields, and hides the plumbing.
- **INFERENCE: initial contents.** A new user probably starts with a short stock soul and empty memory, and BOOTSTRAP runs once. No source prints the default contents.

### First run and extensibility
- **FACT:** a card is required even on the free tier. You name the agent and pick an avatar and style. Tabs are Chats, Goals and Library. [S7](https://openclawdatabase.com/meta-muse/setup/), [S16](https://www.theneuron.ai/explainer-articles/how-to-get-started-with-meta-muse/)
- **FACT:** each connector ships with a SKILL. Muse writes new skills itself. Developers can submit connectors for review at muse.ai/platform. [S12](https://cryptobriefing.com/meta-muse-developer-connector-api-access/), [S18](https://runtimewire.com/article/meta-opens-muse-connectors-developers)
- **Caveat:** several UI details come from secondary write-ups without screenshots.

---

## B. Audit of main

### B1. Requests panel

**What exists**
- **The rail is still a DOM aside.** `tinyassets/onboarding/app.html:469-641` holds `<aside id="request-rail">` with:
  - the `rail-head` "Request history" heading;
  - `rail-error`;
  - `#rail-items`;
  - the "Add a key yourself" button and `#rail-add-panel` (paste-anything plus the manual HTTP form, :476-532);
  - `#connect-panel`, the whole model-connect screen, parked here (:537-640).
- **Cards are in the thread, but only as one block.** `renderRail` (:8876-8990) moves `#rail-items` into `#thread` once and appends the `#request-entry-points` block (:8880-8886). It is not interleaved with the turns: `appendMessage` (:1965-2003) keeps appending after it. Every pending request therefore lives in one block, pinned wherever it was first inserted, not at the turn that raised it.
- **History stays in the aside.** `InlineApprovals.history` (:8860-8867) writes `recently_answered` into `#request-history` inside the aside. On phone, `paintRailChip` / `toggleRailChip` (:7148-7178, CSS :376-380) make that aside a tappable chip.
- **The connect panel is parked on the rail.** `#connect-panel` lives on the aside and is re-hosted into a card body (`connectBody`, :9007-9033; parking at :8949-8950). `openConnectRequest` (:6088-6097) is the single entry point and sets `railOpen`.
- **Only one request kind uses the protected path.** `approve_action` renders through `InlineApprovals.build` (:8763-8858). The server side is `tinyassets/onboarding/inline_requests.py` plus `tinyassets/bound_requests.py`, with protected preview/edit/decide and an owner session.
- **Every other kind uses the legacy generic card.** That includes `answer`, `connect_http`, `connect`, `extend_http`, `rotate_http`, `remove_http`, `install` (publish/install), `grant_workspace_consent`, patch intake, `start_activity` proposals and `bind_model_access`; the action types are in `tinyassets/api/pending_requests.py:224-709, 1116-1141, 2659-2706`. The card is `railBody` (:9395-9470): free fields, a "Reply or note" box, "Don't ask me this again", and the verbs Accept / Deny / Clear / Send reply. `answerRail` (:9532+) answers by **relaying a synthetic chat turn** (`sendTurn(replyLine(...))`, "Approved: …" lines). That is the pre-#4449 model.
- **Other paths still open "the rail".**
  - `?request=` deep links (`railLink`, :8665, :8872).
  - Push notifications, which deep-link there.
  - `openInstallRequest` (:9617-9630), called from `app_ui.js:1596` for a Browse/install ask. It adds `rail--open` to the aside.
  - The Account screen's "Waiting on you" profile status (:7319) and its "Request notifications" section (:801-806).
  - Copy that still says "under Waiting on you" (:8043, :9257) and "The request is waiting in this list" (:9319).
- **The rail is pinned by tests and specs.** 11 test files reference the rail DOM, including `test_inline_approvals_real_browser.py`, `test_request_card_layout_and_links.py` and `test_notification_is_the_setup.py`. `openspec/specs/onboarding-web-app` and `openspec/specs/inline-connect-and-approve` mention it 7 times.

**What has to move so the aside can be deleted**
1. **Anchor cards to their turns.** Each pending request should be a thread item at the point it was raised, using `created_at` or the originating turn. A resolved request collapses into a one-line receipt in place, which replaces `#request-history`.
2. **Re-home the connect UI.** `#connect-panel` and `#rail-add-panel` become a thread card (see B2), not aside residents.
3. **Point the openers at the thread.** Deep links, push, `openInstallRequest` and the profile "Waiting on you" link should scroll to the thread card.
4. **Retire the legacy verbs.** The remaining kinds move onto action-bound cards: preview, then Approve/Deny/Edit, executed server-side. The generic Accept/Deny/Clear/Send-reply-as-turn goes away. "Don't ask again" becomes a Rules scope (always/task), as `inline-connect-and-approve` task 1.4 already specifies.
5. **Delete the chrome.** Remove the aside, `paintRailChip`, the phone chip CSS and the "Waiting on you" copy.

### B2. Connecting anything in chat

**What exists**
- **Connect surfaces on main.** All of the buttons converge on `openConnectRequest`. There are still four distinct form bodies:
  1. **`InlineConnection` first-run card** (#4452), app.html:6110-6145. It is a real chat line (`appendMessage('platform','',box)`), with "Connect <primary>" / "Other AI".
  2. **The `#connect-panel` model-setup screen** (:537-640): guided sign-in, paste-a-key source cards, subscriptions, and "Other ways to connect" (sign-in, endpoint URL/key/model/wire). It is reached from the first-run card's "Other AI", from the model menu (:3205, :3229, :3464), from the model dialog's "Connect a source" (:3624, :3885), from the Account screen's "Connect free AI" (:7704) and from voice (:2601).
  3. **"Add a key yourself" paste-anything** plus the manual HTTP form (:476-532). It is now dumped into the thread as `request-entry-points`.
  4. **The agent-raised `connect` / `connect_http` card** through generic `railBody`. It renders `grant_sentence`, fields and a sign-in offer when OAuth is discovered.
  - In addition, the Account "connections" list (:756-761) and the model dialog's "Model access for connected sources" (:707-711) are management surfaces.

**Traces**

**(a) Registered OAuth provider (Google).**
1. The agent calls `write_graph target=pending_request operation=ask` with a `connect` action naming the hosts. The tool docs are at `engine_mcp_server.py:172, 1494, 3298`.
2. `_with_sign_in_offer` (`pending_requests.py:521`) resolves the provider from the daemon directory, `connection_oauth/providers.json` (Google only; the client id/secret come from env, #4441/#4443).
3. The card renders sign-in as the primary action. It still comes through the generic card, in the block.
4. Sign-in uses a popup or system browser. Server-side PKCE custody is `inline-connect-and-approve` task 2.3 and is not finished.
5. Result: mostly inline. Only one provider is registered, and per #4441 Google is inactive until it is configured.

**(b) A service with an MCP server: not supported.**
- `connection_type` is `"http"` only (`storage/outbound_connections.py:4791-4824`; the `ta` catalog filters to http, `ta_capabilities.py:58-60`).
- The attached MCP design (`mcp:<attachment>:<tool>`, http or stdio) is explicitly **deferred** (`openspec/changes/universe-agent-harness/design.md:1265-1279`). The open question there is storage and owner-door activation.
- Today the agent's only option is to hand-roll JSON-RPC over an `http` connection through `ta connection:<id>:POST`. That works only for single-request, non-streaming servers.

**(c) An API-key service.**
- The agent asks with `connect_http` / `connect`, giving the endpoints, method and auth scheme. The owner pastes into the card's secret field, and the secret goes straight to the vault.
- The owner can also paste anything into "Add a key yourself", where `connection_inference` derives the connection.
- Inline-ish, but the form is heavy: manual host, path and method fields, and an OAuth 1.0a four-box form.

**(d) No connector at all.**
- The agent can research the API on the open internet through bash (S3a egress proxy). It can then compose a `connect` ask, and after the deposit call `ta connection:<id>:<verb>` mid-turn (#4439) or write an extension under `/u/extensions`.
- So the end-to-end path exists in primitives. What is missing:
  - No starter skill teaches it, so it depends on the model improvising.
  - No browser fallback: D5 is still "building"; there is no browser broker on main beyond UI preview and image read.
  - No credential capture for sites that only have a login.
  - The answer is a relayed chat turn, not a server continuation, except for `approve_action`.

**Summary of the gap**
- One smooth card instead of four bodies.
- More platform-registered OAuth entries (data plus host app registrations).
- Attached MCP.
- A browser-login fallback.
- An editable "connect-anything" skill so the agent drives the ladder itself.

### B3. soul.md and memory

**Where the files live.** Under the command-center home `cc-<ulid>/` (user content, per `command_center_layout.py:25-43`).
- **The new-account seed is 13 OKF files.** `tinyassets/universe_bundle.py:12-15, 47-61`, called from `api/universe.py:5668`. The files are `index.md`, `log.md`, `soul.md`, `soul.edit.md`, `identity.md`, `founder.md`, `orgchart.md`, `projects.md`, `goals.md`, `body.md`, `origin.md`, `soul_versions/index.md` and `soul_versions/0001.md`.
- **`AGENTS.md` is reseeded per turn.** On the first turn `read_operating_instructions` (`universe_intelligence.py:483-511`) writes `AGENTS.md` from `DEFAULT_OPERATING_INSTRUCTIONS` (:455-478).
- **`MEMORY.md` holds memory**, one bullet per item with `m_xxxx` ids (`tinyassets/memory_items.py`).

**What a new account looks like: cluttered, not clean.**
- The seeded `soul.md` (`universe_bundle.py:89-134`) is not a persona. It is boilerplate about "an OKF concept document that tracks the latest OKF spec on GitHub", with links to soul.edit, OKF URLs and frontmatter (`okf_source`, `okf_tracking`, `edit_authority`).
- `identity.md` / `founder.md` say "Status: not learned yet … oath-confirmed founder".
- That is about 14 files of jargon, against Muse's three plain files.

**How a user sees and edits them today**
- **Memory: yes, but buried.** It is reachable only via the bubble ••• menu > **Account**, then scrolling past connections to "What your agent remembers" (`app.html:766-773`, logic :7321-7391). Each item is an input with Save/Delete, plus an "Undo latest change" button (`harness_history`). The backend is `/app/memory` (`onboarding/__init__.py:1250-1300`, route :2580).
- **Soul: no.** There is no view or edit of `soul.md`, `identity.md` or `AGENTS.md` anywhere in the app. The "Your agent" block shows only name, responsibility and status from `/app/profile` (:7307-7320).
- **The agent can read but not write `soul.md`.** `soul.md` is read-only inside the jail (`universe_tools.py:132-150, 1213-1224`); the agent changes it through `soul_edit` / `write_brain`.
- **Planned but not started.** The designed Memory tab and Harness tab ("the agent's files, the history, and Undo"; `universe-agent-harness/design.md:624-639`, D7 steps 1-5) are "not started" per the tracker. Memory ids and history have landed; `settings.yaml` and the Harness tab have not.

**Side finding (needs a concern):** `MEMORY.md` is missing from `command_center_layout.USER_NAMES`.
- It is listed in `universe_tools.AGENT_BRAIN_FILES` and `command_center_packages.HARNESS_ROOT_FILES`, but not in `USER_NAMES` (`command_center_layout.py:25-43`).
- `classify()` returns `None` for unknown names (:130-138), and the module docstring says the migration refuses to run until every entry is classified.
- Every home that has a `MEMORY.md` will therefore block or flag the `command-center-cutover` migration.
- I have not run the migration. This is from reading the code, so verify it before filing.

### B4. Harness control for command-center designers

**What a package/design controls today**
- **Harness files that travel** (`command_center_packages.py:100-127`): `AGENTS.md`, `identity.md`, `MEMORY.md` (per item), `settings.yaml`, `skills/`, `extensions/`, `prompts/`. An install remaps them under `agents/<slug>/`.
  - Brain files (`soul.md`, `founder.md`, `soul.edit.md`, `log.md`) and the governed set never travel.
- **`settings.yaml` is not read at runtime.** Its only reader is `model_need()` (`command_center_packages.py:819-830`), which reports the `model:` string as an install-time requirement. D7 step 4 ("Add `settings.yaml`") is not started.
- **Extensions are tools only.** `ta` loads `/u/extensions/<pkg>/extension.json` with `{executable, tools[]}` and runs it in the jail (`ta_cli.py:30-80`, `ta_capabilities.py:90-93`).
- **What `ta` can call:** platform MCP tools in the signed launch grant, and owner http connections (`ta_capabilities.py:200-246`).
- **Skills:** `skills/<name>/SKILL.md` is read each turn (`universe_tools.py:1229-1231`).
- **UI bridge for custom UIs** (`ui_frame.py:288-310`): whoami, listAgents, sendMessage, openChat, readConversation, listAutomations, listRuns, readLive, readRun, readRunOutput, listFiles, readFile, emit, conversationDesign, setConversationDesign. Plus package install (`app_ui.js:841-849`).

**What the platform fixes that pi.dev would let a user change**

| pi.dev lets you change | TinyAssets main today |
|---|---|
| System prompt / instructions entirely | `_HARNESS_HEAD` (`universe_tools.py:1213`) and the prompt sections in `universe_intelligence.py` are fixed. `DEFAULT_OPERATING_INSTRUCTIONS` substitutes for an empty `AGENTS.md`. Moving this out is the `starter-agent-out-of-plumbing` proposal, which is unbuilt; it reports 13-15K resident tokens per round. |
| Extension lifecycle hooks (before/after tool call, turn start, context/compaction, input transform) | None. Extensions only add callable tools. |
| Custom tools replacing built-ins, tool allow-list | Exactly four tools plus served MCP handles are fixed by the platform. The grant narrows only per node/automation, not per owner setting. |
| Model/provider per agent, thinking level | Per-agent model selection exists (`select-agent-models`, `agent-model-selection` spec), but a package cannot set it. `settings.yaml model:` is advisory. |
| Slash commands / custom UI from extensions | Extensions cannot render a card or a command. A custom UI cannot list connections, read or edit harness files, read memory or rules, call `ta` capabilities, or pick a model. "UI bridge = ta parity" is an open item in `pi-gap-review-2026-10-04`. |
| Session/loop behaviour (compaction, retries, the loop itself) | Fixed; S7 thin loop is in review (#4282/#4292). |
| Replace the main agent | A package installs as a roster agent under `agents/<slug>/`. "Replacement main" is acceptance item 3.3 of `starter-agent-out-of-plumbing` and is not built. |
| MCP servers as tool sources | Deferred (B2b). |

The correct fixed floor stays: cross-user isolation, secret custody, protected approval provenance and owner-only rule writes.

---

## C. Build plan

**Legend**
- **[PROP]** needs an openspec proposal first: public MCP/API surface, storage shape, authority, migration or money.
- **[BUILD]** build, prove live, then spec.
- **[EXISTS]** a proposal already exists; implement against it.

Lanes are ordered by user impact. Each lane is one PR with at most 12 tasks.

### Requirement 1: requests panel gone

**L1. Requests are chat lines** — [BUILD] (UI only; storage and API unchanged). **Depends on #4458 landing first.**
1. Render each pending request as its own thread item, ordered by `created_at` / originating turn, instead of one `#rail-items` block.
2. Collapse resolved requests into one-line in-place receipts that replace `#request-history`.
3. Re-host `#connect-panel` as a thread card, not an aside resident.
4. Point `?request=` deep links, push and `openInstallRequest` at the thread card.
5. Point the profile status "Waiting on you" at the thread card.
6. Delete the aside, the rail head, `paintRailChip` and the phone chip CSS.
7. Rewrite the "Waiting on you" / "in this list" copy.
8. Update the 11 rail-pinned test files without renaming tests; keep names per the hygiene-gate memory.
9. Sync the `onboarding-web-app` and `inline-connect-and-approve` specs.
10. Live proof through the app, on phone and desktop.

- **Conflict with #4458:** it edits app.html at the same regions (renderRail and history around :8857, answerRail/refreshRail around :9442-9641). It also edits `test_app_request_rail_executes.py`, `test_request_rail_honest_asks.py` and `test_onboarding_app.py`. Start L1 from post-#4458 main and prove overlap with `git merge-tree`.

**L2. Every request kind is action-bound** — [EXISTS] `inline-connect-and-approve` tasks 1.2/1.4 (authority; the proposal is approved).
- Move `answer`, `install`/publish, `grant_workspace_consent`, patch intake, `start_activity` and `extend`/`rotate`/`remove_http` onto the protected envelope with server-side execution and continuation.
- Retire `answerRail`'s relayed "Approved:" turns.
- Replace "Don't ask again" with a Rules scope.
- Can run in parallel with L1 server-side; the UI merges after L1.

### Requirement 2: connect anything

**L3. One connect card** — [BUILD].
- Merge the four bodies (InlineConnection, `#connect-panel`, paste-anything/manual HTTP, generic `connect` card) into one inline "Connect <service>" component.
- Ladder: registered OAuth, then discovered OAuth, then secure paste straight to the vault (never chat), with manual fields folded.
- The model dialog, Account and model menu buttons open this card in the thread.
- Kept as a separate lane from L1 because L1 is placement and L3 is the component itself.
- Depends on L1.

**L4. The agent drives connect-anything** — [BUILD].
- Add an editable starter skill `skills/connect/SKILL.md`: find the docs over egress, prefer OAuth, compose a `connect` ask with exact endpoints, verify with a `ta connection:` call, then save a per-service skill (the Muse pattern).
- Add `ta search` hits for the connect verbs.
- Live proof: connect to a service that has no directory entry and no prior code.
- Ships with starter content, so coordinate with the `starter-agent-out-of-plumbing` hooks/skills layout.

**L5. More registered OAuth providers as data** — [BUILD] plus a host-action row.
- Add GitHub, Slack, Notion and Microsoft entries in `connection_oauth/providers.json`.
- Activate Google.
- Client registrations are founder-only: file a `docs/host-actions.md` row.

**L6. Attached MCP** — [PROP] (new storage for attachments, an owner activation door, transport semantics).
- Implement the deferred `design.md:1265` design.
- `http` attachments ride an existing connection, credential-blind. `stdio` attachments run in the jail.
- Tools appear as `mcp:<attachment>:<tool>` in `ta`.
- The card in L3 offers "Paste an MCP URL".

**L7. Browser-login fallback** — [PROP] (secret custody for site credentials). It extends D5, which is "building" (#4306/#4314).
- Out-of-band credential capture into the vault, with surrogate tokens to the agent, as in Muse's `authd`.

### Requirement 3: soul and memory accessible and clean

**L8. A "Your agent" panel in the bubble** — [EXISTS] D7 steps 2 and 5 in `universe-agent-harness`.
- Reach it from the bubble ••• menu (not Account): an avatar and name header with Soul / Memory / Identity cards marked "access with care", each opening plain markdown with Edit, Save and Undo through `harness_history`.
- Extend `/app/memory` into an allow-listed owner file door (`soul.md`, `identity.md`, `MEMORY.md`, `AGENTS.md`).
- Memory edit is per item or whole file; the agent can also edit by chat.
- Floor review is required because this is an owner write door.

**L9. Clean start** — [PROP] (seed storage shape; fold into `starter-seed-lifecycle` / D10 as a delta).
- New accounts get a short plain `soul.md` (persona, boundaries, voice), `identity.md` (name/vibe, empty), an empty `MEMORY.md` and `AGENTS.md`.
- `orgchart`, `projects`, `goals`, `body`, `origin`, `founder`, `index`, `log` and `soul_versions` are created only when first learned.
- Strip the OKF/jargon frontmatter from what users see.
- Must preserve:
  - `first_contact.py:27` (checks for `soul.md`);
  - `branches.py:950-970`;
  - the Loop-branch parsing in `api/universe.py:360-395`;
  - the `soul.edit` governance.
- Existing homes upgrade via D10 receipts.

**L0 (one-line fix, before L9).** Add `MEMORY.md` to `command_center_layout.USER_NAMES` and file the concern (B3 side finding).

### Requirement 4: pi.dev-like harness control

**L10. Starter out of plumbing** — [EXISTS] `starter-agent-out-of-plumbing` (approved design, unbuilt).
- Move resident policy into `starter/hooks.md` plus five skills, and stop reseeding `AGENTS.md`.
- Depends on `starter-seed-lifecycle`.
- This is the precondition for "the user can change the main agent".

**L11. `settings.yaml` is live** — [PROP] (storage shape plus authority narrowing; D7 step 4 is named but the shape is unspecified).
- Per-agent `model`, tool allow-list (narrow only), skill/extension enablement and starter hooks on/off.
- Packages carry it.
- Installs prompt to bind the model when it is not connected.

**L12. Extension hooks** — [PROP] (authority: code that intercepts the loop).
- Extend `extension.json` with lifecycle hooks (`before_tool` / `after_tool` / `turn_start` / `context`) run in the jail with the launch's grant.
- Add extension-contributed commands and inline cards rendered through the protected card shell.

**L13. UI bridge parity with `ta`** — [PROP] (a custom UI acting as the owner; overlaps `composable-ui-experiences`).
- Bridge methods: list connections, `ta` capability calls (never approvals), read/write harness files (owner-confirmed), memory, rules read, model pick.
- Approvals stay on the protected first-party surface.

**L14. Replaceable main agent from a package** — [EXISTS] D8/D9 plus `command-center-agent-templates`.
- Installing a package may offer "make this my main agent", with acceptance under starter 3.3.
- Depends on L10 and L11.

### Recommended order and dependencies
1. **First:** L0, then L1 (after #4458) and L2 in parallel.
2. **Then:** L3 (after L1) and L8.
3. **Then:** L9 and L4 (with L10).
4. **Then:** L5, L11, L6, L7, L12, L13, L14.

**Constraints**
- **app.html serialisation:** #4458, L1, L3 and L8 all touch `app.html`. Merge them serially and check each with `git merge-tree`.
- **Starter content chain:** L9, L10 and L4 all touch starter content and seed receipts, and must go through the single `starter-seed-lifecycle` mechanism.
- **L6 / L7 / L12 / L13 need cross-family review of the proposal** before any code.
