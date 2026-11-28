# ADR-040: Where the Agent Runs When

## Status

Accepted (founder, 2026-11-28, through the coordinator: the founder's answers and rules of
that day, and the coordinator's calls on the research's recommendations;
`docs/decisions/untangle-2026-11/placement.md`). Amends ADR-033 decisions 1 and 3, ADR-034
decisions 1.1, 4.1 and 8, and ADR-039 decisions 2.14, 2.20 and 6. Per ADR-039 decision 1,
those ADRs' bodies are not edited: only their Status lines point here.

## Date

2026-11-28

## Context

- The founder, 2026-11-28: "Where the agent runs when is the most impaortant entire point of
  the cut over." And: "The number of simaltanious sandboxes that they need is what costs us
  and thus is how free vrs paid limits work. That and total long term storage size."
- The three places are settled direction: Direction 2, ADR-033's Consequences, and ADR-039
  decision 2.14. They are the user's own main agents over MCP, the user's device, and our
  hosted sandbox. The founder: "This i thought was already the direction so it should be
  familier."
- The build had gaps between that direction and the code:
  - the device runner sat after the cutover (S7 in Step 20);
  - the tier counted runs, not sandboxes;
  - nothing chose a place per run;
  - an outside agent had no way to take *this* agent's work.
- No market leader places by availability. Each makes the user choose cloud or local, and
  none moves a live turn: work moves only between turns, carrying the session. A sleeping
  device either pauses its session (Claude Remote Control, Claude projects) or loses the turn
  in flight.
- ADR-039 decision 2.20 said that a subscription sign-in feature waits for that vendor's
  written approval. The founder: "Hu? I dont understand rule 39, seems a cop out from just
  doing the research then doing what is best for our users and allowed."
- The founder, 2026-11-28, on sources: "We should always assume that a user will want to user
  there subscriptions prefurred to using api or other and we want to fasilitate that so they
  dont have to pay api costs at all to there provider, even users wanting extra will likely
  just get credits or a limit reset and to us it would still appear as if they are using
  there subscription".
- The founder, 2026-11-28, on the sandbox: "Our sandbox is also the last option for where an
  agent runs cause that costs us and thus the user so we want to help fasilitate them
  leveraging as much out of all there agents from all there providers with as little cost to
  us and thus them as possible to do what they want to do even sterring them to do simular
  without needing sandboxes or needing less. Only users whos needs costs us momey cause we
  cant enginiener a better have any need for a sandbox".
- The founder, 2026-11-28, on vendors' cloud agents: "The agents that can run through mcp
  through providers are more reliably on then the users computer and more convienent for the
  user and requires less of the user, if what they are trying to do works with there
  subscription that way in the cloud, which like what you are doing now does so this is an
  example where even if a user building like we are now the agent could run in the cloud on
  the users subscription and its better then my computer even if my computer was one cause
  then i would have to leave it on and it wears and tears on the users computer and if the
  subscription includes it anyways then its better for the user to utilize that."

## Decision

1. **Three places run a run, in this order of preference.**
   1. **`main:<agent>`**: a vendor's own agent on the user's subscription, reaching TinyAssets
      over MCP. This covers chat assistants (ChatGPT, Claude, Grok Bot, Muse) and each
      vendor's hosted coding agent (Claude Code cloud sessions, the Copilot cloud agent).
      - It runs the same agent definition that the MCP door serves as resources and the
        `agent` prompt (ADR-039 decision 3).
      - It is the most reliably on, needs nothing of the user, wears nothing of theirs, and
        costs neither them nor us anything beyond their subscription.
      - One with a push door (`main_push`) starts in about a minute. One without
        (`main_pull`) starts at its own next scheduled pass.
   2. **`device:<id>`**: the owner's own paired computer. It costs no sandbox, and it is live
      while it is on. It is the place for what only it gives: local files and apps, a
      subscription with no cloud agent, the owner's choice, and work no main agent fits.
   3. **`sandbox`**: the owner's hosted E2B sandbox. It is always on, it costs us, and it
      counts against the tier. It is the last place in every order (decision 5).

   A run is placed once, at admission, and never moved while it runs. Only the next input is
   placed again, and it resumes from the synced session (ADR-034 decision 9). For a main
   agent, the vendor's own conversation is the session, and TinyAssets keeps the mail thread
   and the board. In code the word is `place`, never "placement", which ADR-035's body rule
   already uses.

2. **A subscription comes before any metered source.**
   - The owner's connected sources form two groups:
     - first, subscription sign-ins (a main agent is one, by what it is);
     - second, API keys, routers and custom endpoints.
   - Direction 4's strongest connected source ranks within each group
     (`model-ranking.json`).
   - **A metered source runs only by the owner's choice.** The choice is owner policy, in
     the owner policy document's `places` table (decision 3). Only the owner writes it, in
     the owner's own session, and an agent can only propose a change (`policy_propose`;
     ADR-039 decision 2.13). It is one of these, and nothing else:
     - **"Move to…" one run:** that run, once.
     - **A fallback:** `metered.fallback` for every agent, or `metered.fallback_agents` for
       one. A key is then acceptable for that agent only when no subscription source can take
       the run at any place, now. Whenever one can, the subscription runs. "Can take the
       run" is judged against the work's real `needs` (decision 4), never against an engine
       or model the agent's definition names.
     - **A pin:** `sources.<name>`, the owner choosing one source for that agent on purpose.
       A pinned agent runs on that source, a key included, whatever else is connected.

     Connecting a key is not a choice to run on it: the app offers a key as the secondary
     option, beside subscriptions.
   - **Otherwise the run waits** for a subscription source, even when none can take it now.
     Its message offers the choice: "Waiting for your computer. Use your OpenAI key for this
     run, or whenever no plan can?" "This run" is a "Move to…". The other answer stores a
     fallback for that agent, or for all agents if the owner picks that. The app shows a pin
     apart, as a different act: "Always use this key for this agent" against "Use my key
     when no plan can".
   - **A key-only owner** meets that offer on an agent's first run. Or the connect flow asks
     once, when the owner connects a key and holds no subscription that decision 9 allows:
     "Use this key when no plan can?" A yes is stored as `metered.fallback` on.
   - **What the owner chose stays chosen.** Each admission reads only the stored policy,
     never what the owner happens to hold. So a vendor withdrawing a path, or the kill
     switch failing a source closed, moves no agent onto a key unless its fallback covers it:
     its runs wait with the offer. A fallback or pin for agent A never runs agent B.
   - **A pin and a fallback are the owner's choice for a name**, whatever definition that
     name's folder holds. A run that overwrites the folder's files, or deletes and recreates
     them within one sync, leaves the pin and runs on it: the owner pinned the name, so that
     is the owner's choice, and it is bounded by it. What a run cannot do is make a grant
     (decision 3).
   - **A newly connected subscription** takes every agent with no pin whose work's `needs`
     it meets, at that agent's next input, a fallback agent included. The move is announced once
     per agent: "Your research agent now runs on your Claude plan." A pinned agent stays on
     its pin.
   - So a model that only an API key reaches runs only when the owner chose it. Otherwise
     the strongest subscription runs.
   - A top-up, credits or a limit reset stays on the same subscription sign-in. TinyAssets
     needs no separate path for them.
   - **A subscription at its limit.** The run waits, or it goes to another subscription
     source the owner has. It never goes to a metered source by default. The owner sees the
     vendor's own limit message (ADR-034 decision 7).
   - **Connecting.** The app's primary connect flow is the vendor's own: connecting each
     main agent, and signing each subscription in at each place (device and sandbox). An API
     key is the secondary option.

3. **One place rule, as data.**
   - **The platform's default** is `platform/contract/placement.json`:

     ```json
     {
       "attended":       ["main_push", "device", "sandbox"],
       "unattended":     ["main_push", "main_pull", "device", "sandbox"],
       "unattended_due": ["main_push", "device", "main_pull", "sandbox"],
       "warm_first":     true
     }
     ```

   - **The owner's own settings are owner policy**, in the owner policy document's `places`
     table (`contract/src/policy-document.ts`, `owner/src/policy.ts`). It is written only by
     the owner, in the owner's own session, and enforced outside the sandbox (ADR-039
     decision 2.13). Each part either spends the user's money or overrides the platform's
     orders, so none of it is in a file a run or an outside client may write:
     - `order`: the owner's own orders and `warm_first`, replacing the platform's. The
       table's check refuses an order in which `sandbox` is anywhere but last, so the sandbox
       stays the last place in every order (decision 5). The owner may leave the sandbox out
       of an order, or reorder the others.
     - `agents.<name>.places`: the places the agent of that name may use, under the same
       check;
     - `default_places`: the places an agent name with no `places` of its own may use,
       under the same check; left out, the platform's orders;
     - `sources.<name>`: a pin (decision 2), naming the source, and for a key its connection;
     - `metered`: `fallback`, and `fallback_agents`, a list of agent names (decision 2), both
       off by default (Fact 15).

     The default document leaves the table out, so every default above holds. An agent asks
     for a change with `policy_propose`, which the owner answers as a card.
   - **Keyed by the agent's name.** Every per-agent entry is keyed by the agent's name,
     the one identity the ledger, mail and the `actions` table already use.
     - **Restrictions stay with the name** until the owner removes them: an agent's
       `places`, and a pin to a source our sandbox cannot run. Deleting, renaming or
       recreating the agent's folder never clears them. So a run that renames a device-only
       agent away and recreates its name, or deletes and recreates it, still gets a
       device-only agent.
     - **Grants are cleared when the agent is deleted**: a pin to a key, and its place in
       `fallback_agents`. A pin to a subscription the sandbox can run is neither a restriction
       nor a grant, and is cleared the same way.
     - **"Deleted"** means the agent's `agent.json` is gone, however it went: a door's
       delete, a rename away, or a sync that removes it. A rename away clears the grants as a
       delete does.
     - **The owner is told** what went and what stays: "Your research agent was deleted: its
       pin to your OpenAI key went with it. Its device-only limit stays on the name." A
       recreated agent of that name has no grant until the owner gives one.
     - `default_places` applies to a name that has no `places` of its own, whatever else it
       has.
   - **What an agent's files hold, and what they cannot do.**
     - `agent.json` keeps `needs` (decision 4), and the `engine` and `model` its definition
       is written for.
     - An engine or model it names is a preference. It ranks only among the sources the
       owner's policy already makes acceptable. It never makes a metered source acceptable,
       and it never makes "no subscription can take the run" true. So a run that writes
       `"engine": "pi"`, which runs only on a key, gets the strongest subscription that meets
       the work's `needs`, or waits.
     - `agent.json`'s `credential` and `connection` are retired, in `untangle/place-shapes`.
       Their job, a pin to one source or one key, is the owner's `sources.<name>`.
     - `needs` can send work to the sandbox only as the platform's own order does: last, and
       within the tier's sandboxes. It names no place, and it spends no money of the user's.
   - **What no file holds.**
     - The group order of decision 2 is the founder's rule, not a setting.
     - A place that can take nothing waits, and is never refused (ADR-039 decision 6).
     - What each place kind `can` do is a platform fact, not an owner preference (decision
       4).

   - **Attended** means a person waits in the app. A push main agent comes first, then the
     device, then the sandbox. A pull-only main agent is not in this order, because it cannot
     start a waiting person's run now. The owner can still hand the run to one with
     "Move to…".
   - **Unattended** means schedules and mail.
     - A work item with no `due` goes to a push main agent, then a pull main agent, then the
       device, then the sandbox. A pull agent's hours of delay cost nothing when nothing is
       waiting on the result.
     - A work item with a `due` (a field of the schedule or the mail, data) takes
       `unattended_due`. The device comes before a pull agent, because the pull agent may not
       pass before the deadline.
     - A delivery still unread at its `due` is offered to the next place. "Unread" is read
       from the mailbox cursor, never from an elapsed window.
   - **Warm first.** Within one kind of place, a conversation's next input prefers the one
     holding its warm session. Across kinds, the order wins. The next input resumes from the
     synced session wherever it lands.
   - **The admission.** The owner object's admission (`choose`) picks `(source, place)`
     together. A place can take a run when all six of these hold:
     1. The place is online: the main agent is connected, the device's socket is present, or
        E2B is not refusing.
     2. The work fits the place (decision 4).
     3. The vendor's verdict allows that credential at that place, attended or unattended
        (decision 9).
     4. The engine is signed in there, or the key runs there through the egress door.
     5. The run's authority is allowed there. A device and a main agent take only the
        owner's own authority. A client's run with a narrower grant stays in its own
        sandbox.
     6. There is room: the main agent's vendor limits as its record holds them, the device's
        own capacity setting, or the tier's sandboxes (decision 6).
   - **How the source order and the place order combine.** The rule is: *the first place in
     the order that can run an acceptable source wins, and the strongest acceptable source
     runs there.*
     - An acceptable source is one from the subscription group that meets the work's
       `needs` at that place, at a place the agent's own `places` include. A metered source
       is acceptable only by the owner's choice (decision 2). An owner who wants one source
       for an agent pins it.
     - The places are tried in the rule's order. At each place, the strongest acceptable
       source that place can take runs.
     - Why: the order puts the places that cost the user and us nothing first, and the most
       reliable of them first. The strongest model is still chosen among what that place
       can run. An owner for whom only the strongest will do pins it, and the app suggests
       connecting that vendor's agent or signing its subscription in on their computer
       (decision 5). That brings the strongest to a free place.
     - One trade is left open: a metered source on a free place, against a subscription in
       the sandbox. The sandbox runs the subscription; a fallback takes the key only when no
       subscription can run at any place. Why:
       metered use is money the user pays their provider directly, which the founder ruled
       out by default. The sandbox spends a ceiling the tier already gives them.
   - **None free: the run waits.** When no place can take the run now, it waits with a clear
     message: "Waiting for your computer", or "Waiting for a free sandbox (5 of 5 in use)".
     It is never refused for room (ADR-039 decision 6). It never spends metered money in
     silence. A device arriving, a sandbox freeing, or a limit resetting rings it.
   - **The metered fallback** is the owner's own policy, off by default (Fact 15).
   - **Exclusivity** comes from what exists, never a second check:
     - one input is one admission;
     - mail is held until exactly one taker acks it;
     - board tasks are claimed by compare-and-swap.

4. **The vendors' own agents: how each gets the work, and what it fits.**
   - **The fit check, as data.** Every place kind says what it `can` do, from one vocabulary:
     `tinyassets` (it reaches the door over MCP), `shell`, `repo`, `browse`, `long_runs`, and
     `local` (the user's own files and apps).
     - Each main agent's `can` is in its wake record (`contract/wake-kinds.json`), from its
       vendor's pages, each cited.
     - The device's and the sandbox's are in `contract/place-kinds.json`, platform data
       beside `wake-kinds.json` that no owner file overrides. Only the device lists `local`:

       ```json
       {
         "device":  ["tinyassets", "shell", "repo", "browse", "long_runs", "local"],
         "sandbox": ["tinyassets", "shell", "repo", "browse", "long_runs"]
       }
       ```

     - An agent's `agent.json` lists what its work `needs`. A schedule or mail item may add
       to that. The default is `["tinyassets"]`.
     - The rule matches `needs` against `can`, one rule for every place kind. It never
       decides by a vendor's name.
   - **How each vendor's agent is started**, as read on 2026-11-28 (Sources):

     | Vendor's agent | How TinyAssets starts it | How it reaches TinyAssets | May TinyAssets trigger it? | Today |
     |---|---|---|---|---|
     | Claude Code cloud sessions, through a scheduled task | The task's API trigger: `POST …/fire` with the task's own token, which the owner pastes and the gateway seals [16] | Its claude.ai connectors, TinyAssets among them [16] | Yes: "wire Claude Code into alerting systems, deploy pipelines, internal tools, or anywhere you can make an authenticated HTTP request" [16]. 30 fires an hour per task, and 100 scheduled runs an hour per account | Push, built (`claude_routine_fire`) |
     | ChatGPT (Work chats and dots) | MCP Events `tinyassets.changed`, then an event-triggered task | The TinyAssets app's MCP | Yes: OpenAI's own events mechanism | Push, built (`chatgpt_mcp_events`) |
     | Codex cloud tasks | Only the user's own CLI (`codex cloud exec`, experimental) [22]; no documented third-party trigger | Not documented for cloud tasks [23] | Not documented, so no | Not a place; ChatGPT's own place covers OpenAI |
     | Grok Bot | Its webhook routine | MCP | Yes, as its routine docs say | Push, built (`grok_webhook_routine`) |
     | Meta Muse | Its own recurring task | MCP | Nothing to trigger | Pull, built (`muse_recurring_task`) |
     | GitHub Copilot cloud agent | `POST /agents/repos/{owner}/{repo}/tasks` with the user's OAuth token (Copilot Pro, Pro+ and Max; public preview) [17][18] | The repository's MCP settings: a remote HTTP server, with headers from `COPILOT_MCP_` secrets, so an owner-minted key (ADR-028) [19] | Yes: the API "makes it easy to weave Copilot cloud agent into custom automations" [17] | Push, unbuilt (`untangle/place-rule`) |
     | Google Jules | A Jules API session, with the user's own Jules API key (alpha) [20] | Only Google's partner list of MCP servers, which TinyAssets is not on [21] | The API is for embedding Jules "into the tools you use every day" [20] | Not a place for TinyAssets work until it can reach the door; work that needs only a repo fits |

   - **What such a trigger holds.** A vendor's documented per-task token or API key (the
     `/fire` token, a Jules key, a Copilot OAuth token) is held sealed by the gateway. It is
     never a consumer login token (decision 9).

5. **The sandbox is the last place, and the platform steers work off it.**
   - **What steers.** Before a run would open a sandbox, the app and the default agent offer
     the same work without one, or with less of one:
     - "Let your Claude (or ChatGPT, or Copilot) agent take this";
     - "Run this on your computer";
     - work that needs no model, answered by the door's own tools.

     The default agent's instructions and skills say so per place. They are the starter's
     files, served from data, not one place's text.
   - **The paths that need no sandbox today** (`platform/`):
     - **The MCP door's own tools** (`contract/doors.json`): files (read, write, edit,
       search, history, restore), collections, the board, mail, rooms, and the inbox and
       board digests. A main agent, a device's engine and the owner's app do all of that
       with no sandbox.
     - **Code mode** (`code`, the codemode Worker): one script of door tool calls, run in a
       separately deployed Worker's VM, with no sandbox and no model. It is off by default
       until its isolation probe H2 passes (ADR-039 decision 4). Then it takes batch edits,
       moves, reports over files and rows, and other deterministic tool work.
     - **The agent definition at the door** (`doors/src/definition.ts`): `AGENTS.md`,
       `SOUL.md` and skills served as resources, the `agent` prompt and the Skills
       extension. So a main agent runs this agent's work by its own definition.
     - **Wakes** (`contract/wake-kinds.json`): ID-only doorbells to a main agent's own push
       door. Where none exists, its own scheduled pass reads the digests.
   - **What only the sandbox gives today, and whether a cheaper place could take it.** This
     is the engineering duty. Each row with a "yes" is a target: work moved off the sandbox
     where that is feasible.

     | Need | Why only the sandbox today | A cheaper place later? |
     |---|---|---|
     | Attended work from a phone, with no device on and no push main agent connected | No free place can start it now | Yes, mostly: a push main agent takes it once the owner connects one, which the app suggests |
     | A run for an outside client's authority | A client's run with a narrower grant needs its own isolated place, and a device takes only the owner's authority | Yes, for many: the client's own main agent can run a shared definition through the door, as a main place for that client |
     | Work that needs `shell` or `repo` when no main agent that `can` is connected and no device is on | No free place fits it | Yes: a coding agent (Claude Code cloud, the Copilot cloud agent) fits it once connected |
     | Unattended work with a `due` that no free place meets | No main agent passes in time and the device is off | Partly: push main agents first; the app suggests "keep this computer awake" |
     | A pi CLI run on an API key with no device | pi's CLI is the engine, and an engine's loop is untrusted code (ADR-034) | Yes: the device runs pi as it runs any CLI |
     | A subscription that runs only in our sandbox | None in decision 9's table | No such engine today |
     | A hosted sign-in flow | Signing in at the sandbox opens it | No: it is needed only to use the sandbox at all |

   - **The tier's sandboxes are a ceiling** for the users whose needs truly cost money,
     because no cheaper place can be engineered for them. They are not the normal path.

6. **The tier counts simultaneous sandboxes, and storage.** This amends ADR-034 decision 8
   and ADR-039 decision 6, and Fact 15 says so.
   - Free gets 5 simultaneous sandboxes and 25 GB; paid gets 10 and 250 GB. These are the
     founder's numbers, unchanged, now counted as sandboxes. They stay in the control record.
   - There is one sandbox per authority: one for the owner's own work, and one for each
     outside client whose runs are live. The owner's live sandboxes are the authorities with
     live leases.
   - A run that joins an open sandbox counts nothing new. Runs inside one sandbox are bounded
     only by a recorded technical ceiling: the VM's measured memory per CLI.
   - Main-agent and device runs count zero sandboxes. A free user whose agents run on their
     vendors' clouds, or whose computer stays on, gets the paid experience.
   - So most owners hold at most one sandbox: their own work's. The tier binds only an owner
     whose outside clients run hosted agents at the same time. Free allows the owner's own
     sandbox plus four clients'; paid, plus nine. Past that, a client's run waits, or runs on
     the client's own main agent once that is built (decision 5).
   - A run past the tier is placed on another place by the rule, or it waits. It is never
     refused (`tier_runs` goes).
   - Storage counts what it counts today: the files plane, R2 bodies and the object's rows.
     A device's own disk and its granted folders never count. What a device run syncs into
     the files plane counts, as a sandbox run's does.
   - Sandbox time stays measured only, never enforced (registry row N12).

7. **A device that sleeps pauses its run.** This amends ADR-039 decision 2.14 and, through
   it, ADR-024.
   - When the socket drops, the run becomes `away`. It is not settled, and its conversation
     stays busy. The device's outbox keeps its frames.
   - When the device reconnects under the same lease epoch, the run goes on and the frames
     flush. The app shows "Your computer is asleep: this run is paused", with Stop.
   - The run becomes `unknown`, and is never replayed, only when one of these happens:
     - the device comes back without it;
     - the device is revoked;
     - the owner presses Stop.
   - An owner's bound on `away` is their own setting, off by default (Fact 15).
   - The rest of 2.14 stands. A device runs only its owner's work, connects outbound only,
     and keeps vendor sign-ins on itself. Every hosted surface works with no device online.

8. **The device.** These are the founder's answers of 2026-11-28.
   - **The desktop app comes before the cutover.** It is the existing Electron
     `desktop-app`, made Morphy-like:
     - it hosts the device runner;
     - it signs in through the platform and pairs the computer;
     - it keeps the computer awake by the owner's setting;
     - it grants local folders one at a time.

     A command-line runner alone is not the product.
   - **The link.** It is a hibernating WebSocket from the device to the owner's object,
     through the doors.
     - Commands go down the socket.
     - Events, MCP, sync and egress go up over HTTPS to the egress-host doors, under a device
       key (ADR-028's keys) plus the run's own secret.
     - An idle device costs nothing and keeps no sandbox awake.
   - **The owner's rules on the device.** Rules are allowed there.
     - R5's root-owned decide hook has no root on a user's computer. So the agent, running as
       the user, can edit the local rule files.
     - The app says so plainly: on your computer, your rule files are editable there.
     - The door's grants still hold, because TinyAssets enforces them at its own doors: the
       run's MCP grant, and the egress door for API keys.
     - A device run's other network traffic is the user's own machine's.
   - **What stays on the device, and what never reaches it.**
     - Vendor sign-ins stay where the vendor's own CLI wrote them.
     - API keys never reach the device: the CLI's base URL is the egress door, and the
       gateway injects the key.
     - The TinyAssets workspace syncs by the same protocol and fencing as the sandbox's.
     - Other local folders are off until granted, and are never synced or counted.

9. **Vendor terms are decided by research, not by a blanket wait.** This replaces ADR-039
   decision 2.20's "A subscription sign-in feature waits for that vendor's written approval".
   - **The principle.** Each vendor's published terms are read as primary sources, with the
     URL, the date read and a quote. Those terms decide each engine, credential and place.
     Then TinyAssets does what is best for users and allowed.
   - **Published routes.** A vendor's own published route is one of its terms, and it is
     followed. An example is an interest form for remotely hosted apps.
   - **The founder decides** only where the terms are genuinely ambiguous *and* the choice
     is risky for users.
   - **Where the verdict lives.** It is data: the engine record's terms fields
     (`cli.login.hosted_use` and `cli.unattended` today, keyed by place from
     `untangle/place-shapes`), and a main agent's wake record. A vendor with no record yet
     keeps its verdict here, until its record cites them.
   - **When it is read again.** It is read again whenever its record changes. A vendor that
     withdraws a path fails that source closed through the per-source kill switch (ADR-034).
   - **The rest of 2.20 stands.**
     - TinyAssets never holds a consumer login token. A vendor's documented per-task token
       or API key is held sealed (decision 4).
     - Wakes go through each vendor's own door.
     - A device runner launches only the user's own signed-in, unmodified CLI.
     - Vendor names are never a product's name.
   - **The verdicts**, as read on 2026-11-28. "Own agent" is the vendor's own agent on the
     user's subscription (decision 4).

     | Engine and credential | Device | Hosted sandbox | Own agent |
     |---|---|---|---|
     | Claude Code, Claude plan | allowed [1] | allowed [1] | allowed: Claude over MCP, and Claude Code cloud sessions [16] |
     | Codex, ChatGPT plan | allowed [2][24][25] | unclear, so off; the founder asks OpenAI through its interest form (below) [2][3][24][25] | allowed: ChatGPT over MCP [26] |
     | Gemini CLI, Google AI Pro or Ultra | not served since 2026-06-18 [5] | not served [5] | none reaches the door today (Jules, [21]) |
     | Gemini CLI, Code Assist Standard or Enterprise sign-in | not allowed: Google's route for a third-party agent is an API key [8], and its terms ban third-party software reaching Gemini CLI's services [6] | not allowed [6][8] | none |
     | Antigravity CLI, Google AI Pro or Ultra | not allowed: "not supported under consumer plans" [7][8] | not allowed [7][8] | none |
     | Grok Build, SuperGrok or X Premium+ | allowed [9][10] | allowed by xAI's docs [10]; the PR that adds a Grok Build record reads its consumer terms first (they answered 403) | allowed: Grok Bot routines |
     | Copilot CLI, Copilot plan | allowed [11] | allowed [11][12] | allowed: the Copilot cloud agent [17][19] |
     | OpenRouter, the user's own key | allowed, through the egress door [13][14] | allowed [13][14] | none |
     | Any API key | allowed, through the egress door; the key never reaches the device | allowed | none |

   - **Codex, read closely.** OpenAI's pages speak of an app that gets a user's plan, and of
     a user's own automation. TinyAssets is a third kind: it launches the user's own
     signed-in CLI, and it never holds a token.
     - **Its own agent.** ChatGPT reaching TinyAssets over MCP is OpenAI's own platform and
       its own events mechanism [26]. Allowed.
     - **The device.** OpenAI documents the user's own CLI signed in with ChatGPT "for local
       work" [2]. It documents automation as the user's own Codex account on "trusted private
       infrastructure" [24], and the user's own computer is theirs. The device runner holds no
       token: the user's CLI signs itself in, so the account is not made available to anyone
       else [25]. The interest form [3] covers an app that gets "ChatGPT plan usage" through
       Sign in with ChatGPT tokens, which the runner never does. Allowed, on that reading.
     - **Our sandbox.** OpenAI documents ChatGPT sign-in "in a remote or headless
       environment" by device code [2], and automation on "trusted private infrastructure"
       [24]. It sends "a paid or remotely hosted app" that uses a user's plan to its
       interest form [3]. It does not say which a per-user sandbox that a platform runs is:
       - if it is the user's own private infrastructure, signed in by them, hosting is
         allowed;
       - if it is a remotely hosted app's infrastructure, it needs OpenAI's partner route
         [3][4], and TinyAssets running the VM may read as making the account available to
         another [25].

       That is ambiguous, and the risk is the user's ChatGPT account. The founder decided
       (2026-11-28): it stays off, and TinyAssets submits OpenAI's Sign in with ChatGPT
       interest form. So the record's verdict stays `unclear`, and the sign-in is not offered
       in the sandbox. Once OpenAI answers yes, two steps turn it on:
       - the control record's sign-in switch (`control.signins`) offers the sign-in, and with
         it attended sandbox runs, with no deploy;
       - unattended sandbox runs wait for the record's own verdict
         (`unattended.vendor_login_in_sandbox`), which is a record change and so a deploy.
   - **Google's subscribers.** Neither of Google's CLIs runs a consumer Google plan for an
     app like TinyAssets, and Jules does not yet reach the door. Such a user's Gemini runs on
     a Google API key they connect, as Google's own FAQ recommends [8]. The app says why.
     That key runs only once the owner chooses it, by decision 2's owner policy: a fallback,
     a pin or one "Move to…", offered at the first run or in the connect flow.

## What changes in the other ADRs, exactly

- **ADR-033 decision 1:** a subscription is the default source, and an API key is the
  secondary one (decision 2 here).
- **ADR-033 decision 3:** decision 9's table replaces the 2026-11-01 reading of OpenAI's and
  Google's terms. Codex on a ChatGPT plan runs on the device and as ChatGPT. Google's
  consumer plans run neither CLI.
- **ADR-034 decision 1.1:** a provider's CLI runs in the user's sandbox *or on their
  device*.
- **ADR-034 decision 4.1:** admission chooses `(source, place)` by decisions 2 to 5, against
  the tier's simultaneous sandboxes.
- **ADR-034 decision 8:** tiers differ only by simultaneous sandboxes and storage (decision
  6).
- **ADR-039 decision 2.14:** the device's in-flight work is paused while it sleeps
  (decision 7).
- **ADR-039 decision 2.20:** decision 9 replaces its written-approval line.
- **ADR-039 decision 6:** the promise is the tier's simultaneous sandboxes. A run past them
  is placed elsewhere or waits.
- The Status lines of each of these ADRs, and of ADR-024, name this ADR.

## Consequences

- **Build order.** The placement lanes and the desktop app go into `tasks.md` Step 5, ahead
  of the cutover, because their first PR changes stored shapes (Direction 5). S7 leaves
  Step 20.
- **Direction 8 holds in every mix.** With no device, main agents and the sandbox serve
  every surface. With E2B down, main agents and devices still run.
- **The E2B ceiling is now a real constraint.**
  - Most owners hold one sandbox at a time, their own work's. A free owner holds five only
    when four outside clients run its hosted agents at once (decision 6). Even so, the E2B
    team's account-wide ceiling binds early: Hobby's 10 is ten owners running at once.
  - Hobby allows 10 running sandboxes and Pro 100 [15]. The control record held 20.
  - The founder keeps Hobby for now (2026-11-28): "we will need a way of knowing when we hit
    this limit so we can upgrade then". So the control record's ceiling is 10, and the
    founder is paged at 80% and 100% of it, and at once when E2B refuses a create for
    concurrency (`tasks.md` Step 5, `untangle/sandbox-ceiling-alert`). Raising the plan is
    then the founder's spending call.
- **The decision changes if** a vendor changes a page a verdict cites, or E2B's ceilings
  change.

## Sources

All read 2026-11-28. "Undated" means the page shows no date.

1. https://code.claude.com/docs/en/legal-and-compliance (undated).
   - "Nor does it prevent an end user from signing in to the unmodified Claude Code binary
     with their own Claude subscription, including where a platform hosts Claude Code".
   - Hosting needs the Commercial Terms and an unmodified binary, and no one may "pay for,
     resell, or intermediate Claude usage on their end users' behalf".
   - "Advertised usage limits for Pro, Max, and Ultra plans assume ordinary, individual usage
     of Claude Code and the Agent SDK."
2. https://learn.chatgpt.com/docs/auth (redirected from developers.openai.com/codex/auth,
   undated). Each quote is under the section named.
   - "OpenAI authentication": "The ChatGPT desktop app, Codex CLI, and IDE extension
     support both sign-in methods for local work."
   - "Login on headless devices", for signing in to ChatGPT with the Codex CLI when
     "You're running the CLI in a remote or headless environment": "prefer device code
     authentication (beta)".
   - "Fallback: Authenticate locally and copy your auth cache": "API keys are still the
     recommended default for automation."
   - "Sign in with an API key", about API-key sign-in only: "Use API key authentication for
     programmatic Codex CLI workflows, such as CI/CD jobs. Don't expose Codex execution in
     untrusted or public environments."
3. https://developers.openai.com/siwc/token-sharing-open-source (undated), about an app that
   gets a user's plan through Sign in with ChatGPT: "These docs explain ChatGPT plan usage for
   open-source and locally hosted apps. If you're interested in offering it in a paid or
   remotely hosted app, complete the interest form."
4. https://help.openai.com/en/articles/20001542 ("Updated: 15 days ago").
   - Plan use in "participating commercial apps and sites is available with Go, Plus, Pro,
     and Business".
   - "We've launched with a limited set of partners."
5. Gemini CLI's end for consumer plans.
   - https://github.com/google-gemini/gemini-cli/blob/main/README.md: "Free individual access
     and **Google AI Pro / Ultra** (Google One) moved to Antigravity CLI on June 18, 2026".
   - https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/
     (2026-05-19): "On June 18, 2026, Gemini CLI and Gemini Code Assist IDE extensions will
     stop serving requests for Google AI Pro and Ultra".
6. https://github.com/google-gemini/gemini-cli/blob/main/docs/resources/tos-privacy.md
   (undated): "Directly accessing the services powering Gemini CLI (for example, the Gemini
   Code Assist service) using third-party software, tools, or services (for example, using
   OpenClaw with Gemini CLI OAuth) is a violation of applicable terms and policies."
7. https://antigravity.google/terms (undated), section 6: "Using third party software, tools,
   or services to access the Service (e.g. using OpenClaw with Antigravity OAuth) is a breach
   of this Agreement."
8. https://antigravity.google/docs/faq (undated).
   - "Antigravity CLI headless mode (agy -p) is intended for direct scripting and CI
     workflows. Wrapping agy inside third-party coding agents or applications, or automating
     account rotation, is not supported under consumer plans."
   - "To use a third-party coding agent with Gemini, we recommend using a Gemini Enterprise
     or Google AI Studio API key."
9. https://x.ai/news/grok-build-cli (2026-05-25).
   - "Available now to all SuperGrok and X Premium Plus subscribers."
   - "Headless mode (`-p`) allows easily running agents inside scripts and automations."
10. https://docs.x.ai/build/overview.md and https://docs.x.ai/build/enterprise.md (undated).
    - "Headless mode is ideal for scripts, automations, or integration into other apps".
    - The device code sign-in is for "SSH sessions, containers, headless hosts".
    - x.ai's consumer terms (https://x.ai/legal/terms-of-service) answered 403 to every fetch.
11. https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli
    (undated): a token "is ideal for CI/CD pipelines, containers, or headless servers".
12. https://docs.github.com/en/site-policy/github-terms/github-terms-of-service (effective
    2026-04-27).
    - "Your login may only be used by one person — i.e., a single login may not be shared by
      multiple people."
    - No clause read forbids hosting the CLI for the user's own sign-in.
13. https://openrouter.ai/docs/guides/overview/auth/oauth (undated).
    - "Use OAuth to let your users bring their own OpenRouter API key to your app."
    - "Their requests are billed to their own OpenRouter credits".
14. https://openrouter.ai/terms (2026-08-31), section 7: no access "for purposes of reselling
    API access to Models".
15. https://e2b.dev/pricing (undated): Hobby "Up to 10 concurrently running sandboxes", Pro
    "Up to 100 concurrently running sandboxes".
16. https://code.claude.com/docs/en/routines (undated, research preview).
    - A scheduled task runs "on a schedule, on API calls, or on GitHub events", each run a
      full Claude Code cloud session, with "your connected MCP connectors".
    - The API trigger: "Use this to wire Claude Code into alerting systems, deploy
      pipelines, internal tools, or anywhere you can make an authenticated HTTP request."
    - "Scheduled tasks draw down subscription usage the same way interactive sessions do."
    - Scheduled runs: 100 an hour per account. Run now and API fires: 30 an hour per task.
17. https://github.blog/changelog/2026-05-13-agent-tasks-rest-api-now-available-for-copilot-pro-pro-and-max/
    (2026-06-04): the agent tasks REST API is available to Copilot Pro, Pro+ and Max users, in
    public preview, with personal access tokens and OAuth tokens; it "makes it easy to weave
    Copilot cloud agent into custom automations".
18. https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/start-copilot-sessions
    (undated): sessions start from the REST API, the GitHub CLI, the GitHub MCP Server, and
    automations "on a schedule or in response to events".
19. https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/extend-cloud-agent-with-mcp
    (undated): "Copilot cloud agent accepts `"local"`, `"stdio"`, `"http"`, or `"sse"`";
    header values reference secrets whose names "must start with `COPILOT_MCP_`"; remote
    servers that use OAuth are not supported from repository settings.
20. https://developers.google.com/jules/api (undated, alpha): "The Jules API lets you
    programmatically access Jules's capabilities"; it can "embed Jules's intelligence
    directly into the tools you use every day"; the key goes in `X-Goog-Api-Key`.
21. https://jules.google/docs/changelog/2026-02-02 (2026-02-02): MCP support "starting with:
    Linear, Stitch, Neon, Tinybird, Context7, and Supabase"; "It might seem odd for us to
    limit which MCP servers Jules can connect to."
22. https://learn.chatgpt.com/docs/cli/reference (undated): `codex cloud` is marked
    Experimental, "Browse or execute Codex cloud chats from the terminal"; `codex cloud
    exec` takes `--env`, and authentication "follows the same credentials as the main CLI".
23. https://learn.chatgpt.com/docs/extend/mcp.md (undated): "ChatGPT web uses remote MCP
    tools through plugins"; "Local MCP servers and transports may not be available in the
    cloud". No page read documents a remote MCP server in a Codex cloud task.
24. https://learn.chatgpt.com/docs/auth/ci-cd-auth (undated), on Sign in with ChatGPT in
    automation: "This guide shows how to keep ChatGPT-managed Codex auth working on a trusted
    CI/CD runner"; "This is an advanced workflow for enterprise and other trusted private
    automation"; use it only when "the runner is trusted private infrastructure"; "Do not
    use this workflow for public or open-source repositories."
25. https://openai.com/policies/terms-of-use/ (effective 2026-01-01): "You may not share your
    account credentials or make your account available to anyone else".
26. https://developers.openai.com/plugins/build/mcp-events (undated): "MCP Events lets
    ChatGPT subscribe to updates from your MCP server, such as new messages, content
    updates, or status changes."
- `docs/decisions/untangle-2026-11/placement.md` (the research and the market map)
- ADR-024, ADR-033, ADR-034, ADR-039
