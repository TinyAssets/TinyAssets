# placement-adr as built (2026-11-28)

`untangle/placement-adr`: docs and records only, no runtime code. The research of lane
`untangle/placement` (Fact 14), trimmed to what a reader needs, and the decisions it led to.
ADR-040 holds the decisions and the vendor quotes; this file holds the evidence behind them,
the hidden links found, and what is owed.

## 1. What the founder said, 2026-11-28

- "Where the agent runs when is the most impaortant entire point of the cut over."
- Cost and the tier: "The number of simaltanious sandboxes that they need is what costs us
  and thus is how free vrs paid limits work. That and total long term storage size."
- The three places are settled direction (Direction 2, ADR-033's Consequences, ADR-039 2.14):
  "This i thought was already the direction so it should be familier."
- The founder's answers:
  - the tier stays at 5 and 10, counted as simultaneous sandboxes; storage stays 25 GB and
    250 GB; a run past the limit is placed elsewhere or waits;
  - rules on the user's computer are allowed, and the app says that they are editable there;
  - the desktop app comes before the cutover;
  - vendor terms are decided by research, not by a blanket wait for written approval.
- The founder's rules, given while this PR was built, in the order given:
  1. Subscriptions before API keys: users should not pay API costs.
  2. The sandbox is the last place, and the platform steers work off it.
  3. The vendor's own cloud agent on the user's subscription comes first, before the device:
     it is more reliably on, needs nothing of the user, and wears nothing of theirs.
- Taken by the coordinator on the research's recommendations:
  - the strongest source wins, and cost picks only among the places that can run it (refined
    by the rules above: ADR-040 decision 3 says how they combine);
  - a sleeping computer pauses the run;
  - one place rule as data.

## 2. The market (read 2026-11-28)

No market leader places work by availability. Each makes the user choose cloud or local,
per task or per schedule. None moves a live turn: work moves between turns, carrying the
transcript and the branch.

| Product | Where a turn runs | When the device sleeps or goes offline | Device link |
|---|---|---|---|
| Claude Code and Remote Control [1] | The user's machine | The session pauses and reconnects on wake. Server mode exits after about 10 min offline, and resumes within about 4 h | Outbound HTTPS: it registers, polls for work and streams, with short-lived scoped credentials and revocable Trusted Devices |
| Claude projects [2] | Cloud by default; "Work locally" puts a thread on the computer | The thread "pauses while that computer is asleep, and stops if the desktop app or `claude remote-control` quits" | Remote Control, with the folder granted per thread |
| Claude self-hosted runners [5] | The org's hosts | The lease lapses about 60 s after polls stop, and the session requeues | "Each poll refreshes the lease and doubles as the heartbeat" |
| Claude scheduled tasks [4] | Cloud, or Desktop locally | Local: "If your computer sleeps through a scheduled time, the run is skipped", with one catch-up within 7 days | n/a |
| Codex [8–10] | Local, an SSH host, or OpenAI's cloud | Cloud "can keep working while your computer is asleep"; a local host stops | A secure relay, QR pairing |
| Cursor [15–17] | Local, a cloud VM, or My Machines (the loop in the cloud, the tools on the machine) | A worker "must stay online while sessions run" | The worker opens an outbound connection |
| Devin [22] | Local, cloud, or a cloud loop with Local Hands | Reconnects after short losses; a long one needs re-pairing | A single-use 10-minute code, revocable |
| Manus [23] | Local terminal commands, each approved | Needs "the machine is on and Manus Desktop is running" | Per-folder authorization |
| Morphy [25] | The user's computer, their own CLIs and plans | Not stated | The phone "over your own Wi-Fi" |

So the invariant ADR-040 copies: a run is placed once, at admission. Only the next input is
placed again, and it resumes from the synced session.

## 3. How a device connects, and what it costs

- Every leader connects outbound only: no inbound port, no tunnel. A sign-in never moves
  between machines.
- The fit here is a hibernating WebSocket from the device to the owner's object. Cloudflare
  bills no duration while idle sockets stay open, and ping and pong never wake the object
  [26]. Polling would wake the object on every poll, so it is rejected.
- Cost per place:
  - **Sandbox:** about $0.117 per running hour for 2 vCPU and 1 GiB [27], billed for the
    whole wall time, model waits included. It is always available while E2B is up.
  - **Device:** $0 in sandbox time. It is intermittent.
  - **A vendor's own agent:** $0 to us, on the user's plan. Its uptime, latency and limits
    are the vendor's (`contract/wake-kinds.json`).
- Direction 8 holds in every mix. With no device, main agents and the sandbox serve every
  surface. With E2B down, main agents and devices still run.

## 4. Today in `platform/` (untangle step 2 on the placement area)

- **One sandbox per authority.** The owner's own work has one sandbox. Each outside client
  whose runs are live has its own (`runs.ts` header, `files.ts:63-66`,
  `SandboxStart.authority`). Sign-ins live in that sandbox and are never copied
  (`contract/src/sandbox.ts` `signin_required`). So one sandbox per run is not open.
- **What the tier limits.** It limits simultaneous runs, `TierNumbers.runs` (free 5, paid
  10, `platform/deploy/control.json`). Past them, a run is refused `tier_runs` (`runs.ts:734-743`),
  against ADR-039 decision 6. The E2B line (`sandbox/src/slots.ts`) counts sandboxes against
  `sandbox_concurrency_ceiling`. So the tier's real cost, simultaneous sandboxes, is not what
  the code limits.
- **Storage** is one figure (`storage-full.ts`): the files plane (the workspace, the agent
  files, the engines' `session_paths` copies), R2 bodies and the object's rows. The sandbox
  disk is not counted.
- **The paths that need no sandbox** (ADR-040 decision 5):
  - the door's tools (`contract/doors.json`);
  - code mode in the codemode Worker (`codemode/src/codemode.ts`), off until H2;
  - the agent definition at the door (`doors/src/definition.ts`, #5748);
  - the wakes (`contract/wake-kinds.json`).

The hidden links and special cases, each under the fix that removes it and the lane that
builds the fix:

- **Place is a field of the run, apart from the credential** (PL1).
  - `CredentialKind` `vendor_login_in_sandbox` names a place inside a credential kind, and
    run rows and records store it.
  - `unattended[credential]` holds a verdict for hosted use only, so a verdict that differs
    by place has no field.
  - `engine_signins` assumes one place where sign-ins live.
  - `Runs.#place()` already calls the source label "place", which collides with the new term.
- **The start splits into a run-neutral part and a place part** (PL3). `SandboxStart` mixes
  run-neutral fields with E2B-only ones (network, route, hosts, tokens).
- **The tier counts sandboxes; runs inside one are a VM ceiling** (PL1). `TierNumbers.runs`,
  `tier_runs` and Fact 15 counted runs. Fact 15 is fixed here.
- **The runner splits into a core and a sandbox shell, with a device shell beside it** (PL3,
  PL5). `sandbox/runner/runner.ts` assumes root: run users from the template's pool,
  `lockDown`, `hidepid`, `/var/lib/ta`, and envd. The reusable core is `engine.ts`,
  `project.ts`, `sync.ts`, `frames.ts` and the session store.
- **The doors take a place credential: E2B's transform, or a device key** (PL4). The egress
  doors admit by the E2B-injected header (`contract/src/egress.ts:80`). A device has no
  transform.
- **The owner's rules on a device are only as strong as the user's machine** (PL5). R5's
  decide hook is root-owned (`decide.ts`). A user's device has no root the agent cannot
  reach. The founder allows it, and the app says so.
- **Instructions per place, served from data** (PL2). The starter's `AGENTS.md` "Where you
  run: A sandbox computer that sleeps between runs" and `$TA_SCRATCH=/scratch` assume the
  sandbox.
- **An `away` state, and "has not happened" read from the run's record** (PL5). `RunState`
  has no state for "its place is asleep", and every lost runner settles `unknown`.
- **The ranking ranks a top-tier key over a lower-tier plan** (PL2). `model-ranking.json`'s
  `about`: "Within a tier, a subscription's plan … comes before any metered key". The
  founder's rule puts every subscription before any metered source.
- **A name clash.** `contract/src/limits.ts:226` "the placement rule" is ADR-035's body rule,
  and `segments.ts` `Placement` is a segment offset. The new concept is `place` in code; the
  rule's file is `placement.json`, as the coordinator named it.

## 5. Mechanics the lanes build (ADR-040's detail)

- **What a run carries.** `RunStart` is place-neutral: today's `SandboxStart` minus the E2B
  fields, plus `place`. `PlaceSpec` holds each place's own part:
  - for the sandbox, network, route, hosts and tokens;
  - for a device, the device id, the granted folders and its key;
  - for a main agent, the wake record and the mail delivery.
- **Continuity.** It is the engine's own session (ADR-034 decision 9). `session_paths` sync
  to the owner's files at each run's end, from either the device or the sandbox. Whether a
  session written under one cwd and home resumes under another is probed per engine in PL5:
  Claude Code keys projects by cwd. The record gains per-place home and cwd fields, as data,
  if needed.
- **A device appears.** It pairs or reconnects and reports its signed-in engines (status
  checks only, never secrets). Waiting runs it can take are rung.
- **Exclusivity** comes from what exists:
  - one input is one admission and one run row;
  - mail is held until exactly one taker acks it (a run's start, or the main agent's
    `mailbox_ack`);
  - board tasks and acts are claimed by compare-and-swap on the row's etag (the starter's
    `shared-brain` rule 3).
- **Re-offering a main agent's delivery.** Only a delivery never *read* is offered on, read
  from the mailbox cursor. A delivery read but not acked is `unknown`, and the owner decides.
  Nothing is replayed.

## 6. Vendor terms and vendors' cloud agents (ADR-040 decisions 4 and 9)

Read as primary sources on 2026-11-28. ADR-040 holds each quote and URL. What changed from
ADR-033's 2026-11-01 reading:

- **Claude Code:** unchanged. Allowed on the device, in the sandbox, and as Claude.
  Claude Code cloud sessions take work through a scheduled task's API trigger, which
  Anthropic documents for "anywhere you can make an authenticated HTTP request". That is
  built (`claude_routine_fire`).
- **Codex** (read again after #5865's first review, which found the first reading quoting
  an API-key sentence as if it were about ChatGPT sign-in). OpenAI's pages speak of an app
  that gets a user's plan through Sign in with ChatGPT, and of a user's own automation.
  TinyAssets launches the user's own signed-in CLI and holds no token: a third kind.
  - On the device it is the user's own CLI "for local work", automated as their own account
    on infrastructure that is theirs ("trusted private infrastructure"). Allowed.
  - In our sandbox, OpenAI documents ChatGPT sign-in "in a remote or headless environment"
    and automation on "trusted private infrastructure". It also sends "a paid or remotely
    hosted app" to its interest form. It does not say which a platform's per-user sandbox
    is. That is genuinely ambiguous, and the risk is the user's account, so it is the
    founder's question (`tasks.md` question 7). `codex.json`'s hosted verdict stays
    `unclear`, with exact quotes, and its refusal cites the CI/CD page.
  - Codex cloud tasks have no documented third-party trigger and no documented MCP, so
    ChatGPT's own place covers OpenAI.
- **Gemini (found here, Fact 17):**
  - Since 2026-06-18, Gemini CLI serves no free individual, Google AI Pro or Ultra account.
    They moved to Antigravity CLI.
  - Antigravity's FAQ says wrapping `agy` inside third-party applications "is not supported
    under consumer plans".
  - Jules reaches only Google's partner list of MCP servers.
  - So no Google consumer plan runs TinyAssets work today. The founder's question about
    Gemini on users' own computers is answered by Google's own pages: no, and a Google
    user's Gemini runs on their own API key, as Google recommends.
  - `gemini.json`'s hosted verdict is now `forbidden`, citing Google's own route: the
    Antigravity FAQ recommends "a Gemini Enterprise or Google AI Studio API key" for a
    third-party coding agent, and Gemini CLI's terms call reaching its services through
    third-party software a violation. That covers the one sign-in Gemini CLI still serves,
    Code Assist Standard or Enterprise. Its unattended refusal says the same. The record
    stays, for the API key, and for that sign-in should a Google page permit it.
- **Grok Build:** xAI documents headless mode for "integration into other apps" and a device
  code for "containers, headless hosts". x.ai's consumer terms answered 403 to every fetch.
  There is no Grok Build record. ADR-040 says the PR that adds one reads those terms first.
- **Copilot:** GitHub documents tokens for "CI/CD pipelines, containers, or headless
  servers", and the agent tasks API for "custom automations" on Copilot Pro, Pro+ and Max.
  The cloud agent takes a remote MCP server with headers from `COPILOT_MCP_` secrets. So it
  can be a push main place (PL8).
- **OpenRouter and API keys:** allowed everywhere, through the egress door.

One vendor reading was both genuinely ambiguous and risky for users: Codex in our sandbox.
The founder answered it on 2026-11-28: off, and TinyAssets submits OpenAI's interest form.
The founder answered the E2B plan, a spending question, the same day: Hobby, with an alert
before the ceiling (`tasks.md` questions 6 and 7).

## 7. The re-cut (`tasks.md`)

- **New:** the placement block in Step 5, ahead of "The data shapes already known to be
  wrong": this PR, then PL1 to PL9. The desktop app (PL6) is in it, and so is the desktop
  shell's sign-in twin from Step 19.
- **Merged:** `untangle/runs-wait-message` into PL1 and `untangle/strongest-ranking` into
  PL2. The renames of `untangle/old-names` and `untangle/ledger-shape` that touch run rows
  share PL1's migration window.
- **Vanished:** Step 20's S7. N8 now points at PL4 to PL6.
- **Extended:** `untangle/sandbox-down-test` (runs still placed on a main agent or a device),
  `untangle/commitments-table` (the placement commitments), `untangle/vendor-limit-stop`
  (the vendor's own message; wait, or another subscription), and Step 8's live stage (item
  7b).
- **Answered:** founder question 1 (Codex and Gemini sign-ins).
- **Unchanged:** #5857 and its protocol land first. The canary keeps the sandbox: zero hosts
  means it pairs no device, and the device is proven live on the stage.

## 8. Owed

The retro-check of Fact 15's change and of ADR-039 2.20's replacement: the built, planned and
open work that says "simultaneous runs", or assumes the sandbox or a key first.

- [ ] `contract/src/control.ts` (`Tiers`), `owner/src/admission.ts`,
  `owner/src/runs.ts`, `doors/src/app.ts` and `doors/src/api-keys.ts` say "simultaneous runs";
  `limits-audit.md`'s tier row and the owner-object and doors specs say a run past them is
  refused `tier_runs`; the site's plans
  copy (`WebSite/site-react/app/page.tsx`'s plans table, `fine-print/page.tsx`,
  `start/page.tsx`), the boundary test that pins those words
  (`WebSite/site-react/scripts/public-boundary.test.mjs`), its spec
  (`openspec/specs/public-website-surface/spec.md`), `engines-replan.md` and the owner-object
  spec's agents-posting requirement say tiers differ by simultaneous runs. Each becomes
  sandboxes with the shape (`untangle/place-shapes`).
- [x] `platform/deploy/control.json` `sandbox_concurrency_ceiling: 20` against E2B's Hobby 10,
  once the founder names the plan (`untangle/place-shapes`). Done here: the founder kept
  Hobby (2026-11-28), so the record, `checklist.md`, `contract/src/control.ts` and
  `limits-audit.md`'s row say 10, and `e2b-resume-measurement.md` gains a dated
  correction.
- [ ] A control record already written with 20 keeps it, since a deploy writes the record only
  when none is held: mode `control` sets `sandbox_concurrency_ceiling` to 10 wherever one is
  (`live:` the founder's operator run).
- [ ] The founder is paged before and at the E2B team's ceiling, and at once when E2B refuses
  a create for concurrency (`untangle/sandbox-ceiling-alert`).
- [ ] TinyAssets submits OpenAI's Sign in with ChatGPT interest form
  (https://openai.com/form/sign-in-with-chatgpt-interest/), asking for hosted use of the
  unmodified Codex CLI on a user's own ChatGPT sign-in (`live:` the founder and the
  coordinator, a host act).
- [ ] `contract/src/limits.ts:226` calls ADR-035's body rule "the placement rule"; it becomes
  "the body rule", so `placement.json` names one thing (`untangle/place-shapes`).
- [ ] `sources/src/model-ranking.json`'s `about` and `ranking.ts` put a top-tier key over a
  lower-tier plan; every subscription goes first (`untangle/place-rule`).
- [ ] The starter's `AGENTS.md` "Where you run" assumes the sandbox; it says each place, and
  the steer (`untangle/place-rule`).
- [ ] Whether a Claude scheduled task loads the owner's custom TinyAssets connector (ADR-034's
  owed #106037 probe), before the rule offers it own-agent work (`untangle/place-rule`).
- [ ] `agent.json`'s `credential` and `connection` (`contract/src/agent-definition.ts`,
  honoured at `owner/src/runs.ts` `choose`, written today by the owner app's form,
  `doors/app/app.js`, and its end-to-end test) pin a source or a key in a file any
  full-grant run writes. They are retired with the form's fields, and the owner policy
  document gains the `places` table: keyed by agent name, its check keeping `sandbox` last,
  restrictions kept with the name, and grants cleared when the agent's `agent.json` is gone,
  a rename away included, with a notice (`untangle/place-shapes`).
- [ ] `choose` reads an engine or model that `agent.json` names as a preference among the
  sources the policy allows, and judges "no subscription can take the run" by the work's
  `needs`, with the tests `tasks.md`'s PL2 lists (`untangle/place-rule`).
- [ ] `cli.unattended`'s `read_note` has no record using it now that Codex's page was read
  directly; the rewrite by place keeps or deletes it (`untangle/place-shapes`).
- [ ] The owner app's sources screen offers each vendor's own connect first and an API key
  second (`untangle/place-app`).

## Sources

The vendor terms and cloud-agent pages are ADR-040's Sources 1 to 26. The market pages:

| # | Source | Page date |
|---|---|---|
| 1 | https://code.claude.com/docs/en/remote-control | undated |
| 2 | https://code.claude.com/docs/en/claude-projects | undated (beta) |
| 3 | https://code.claude.com/docs/en/claude-code-on-the-web | undated |
| 4 | https://code.claude.com/docs/en/desktop-scheduled-tasks | undated |
| 5 | https://code.claude.com/docs/en/self-hosted-environments | undated (beta) |
| 8 | https://learn.chatgpt.com/docs/cloud | undated |
| 9 | https://learn.chatgpt.com/docs/remote-connections | undated |
| 10 | https://openai.com/index/work-with-codex-from-anywhere/ | 2026-05-14 |
| 15 | https://cursor.com/docs/cloud-agents | undated |
| 16 | https://cursor.com/docs/cloud-agent/self-hosted/my-machines | undated |
| 17 | https://cursor.com/docs/cloud-agent/self-hosted/choose-runtime | undated |
| 22 | https://docs.devin.ai/cli/handoff ; https://docs.devin.ai/work-with-devin/local-hands | undated |
| 23 | https://www.manus.im/blog/manus-my-computer-desktop | 2026-03-16 |
| 25 | https://get.appmorphy.com/ ; https://www.appmorphy.com/features | undated |
| 26 | https://developers.cloudflare.com/durable-objects/best-practices/websockets/ | 2026-11-18 |
| 27 | https://e2b.dev/pricing | undated |

All were read 2026-11-28. Gaps: none of the Codex, Cursor, Copilot, Devin or Manus pages
says whether the device side polls or holds a socket; Morphy's pages do not say how its
phone link works beyond "your own Wi-Fi".
